"""Writes a prose description onto every image block worth describing.

An image block reaches a model in one of two forms: its pixels, or words about
it. Words are the only form that always works -- a text-only LLM never sees
pixels, a request that hit its model's image cap sends text for the images
that lost a slot (`app/utils/image_admission.py`), and lexical search can only
match text. Until this step existed, those words came from
`image_metadata.captions`, which the PDF parsers never populate, so for most
documents there were no words at all.

Why here, and not where images are embedded: `SinkOrchestrator.index()` writes
the record to blob storage *before* the vector store runs, so a description
generated during embedding would never reach the stored record -- and the
stored record is exactly what `fetch_record` reads at query time. Describing
before the blob write puts the text in both places from one generation.

Cost is the reason this is careful rather than exhaustive:

* Page furniture is skipped outright (`is_decorative_image`) -- on a typical
  report that is most of the images.
* Blocks that already carry a description are left alone, so the second blob
  write during enrichment is free.
* Descriptions are inherited from the previous version of the record when the
  image bytes are unchanged, which is the common case on connector re-syncs.
* A per-record ceiling bounds the worst case, and one failed description never
  fails the record.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING, Any

from app.models.blocks import BlockType, ImageMetadata
from app.utils.env_utils import env_int
from app.utils.image_utils import is_decorative_image, read_image_dimensions

if TYPE_CHECKING:
    import logging

    from langchain_core.language_models.chat_models import BaseChatModel

    from app.config.configuration_service import ConfigurationService
    from app.models.blocks import Block, BlocksContainer

# How many images of one record may be described. A 500-page scan would
# otherwise turn a single sync into hundreds of vision calls; past a few dozen
# figures the marginal one adds little to what search can already find.
DEFAULT_MAX_IMAGES_PER_RECORD = 60
MAX_IMAGES_ENV_VAR = "PIPESHUB_MAX_DESCRIBED_IMAGES_PER_RECORD"

# Matches the concurrency the embedding path already uses for vision calls.
_CONCURRENCY = 10

# The prompt asks for a full transcription of any text in the image, so a
# dense figure legitimately produces a lot; this only guards against a model
# that runs away, and is well above what a real figure yields.
_MAX_DESCRIPTION_CHARS = 8_000


def _image_uri(block: "Block") -> str | None:
    data = getattr(block, "data", None)
    if isinstance(data, dict):
        uri = data.get("uri")
        return uri if isinstance(uri, str) and uri else None
    return None


def _content_key(uri: str) -> str:
    return hashlib.sha256(uri.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _existing_description(block: "Block") -> str | None:
    metadata = getattr(block, "image_metadata", None)
    description = getattr(metadata, "description", None) if metadata else None
    return description if isinstance(description, str) and description.strip() else None


def harvest_descriptions(record: dict[str, Any] | None) -> dict[str, str]:
    """Map image content -> description from a previously stored record.

    Connector re-syncs re-parse a document that has not changed, producing
    fresh blocks with no descriptions. Re-describing images we have already
    paid for is the single largest avoidable cost in this path, so the
    previous version's work is carried forward by content hash.
    """
    if not isinstance(record, dict):
        return {}
    containers = record.get("block_containers")
    blocks = containers.get("blocks", []) if isinstance(containers, dict) else []
    harvested: dict[str, str] = {}
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != BlockType.IMAGE.value:
            continue
        data = block.get("data")
        uri = data.get("uri") if isinstance(data, dict) else None
        metadata = block.get("image_metadata") or {}
        description = metadata.get("description") if isinstance(metadata, dict) else None
        if isinstance(uri, str) and uri and isinstance(description, str) and description.strip():
            harvested[_content_key(uri)] = description.strip()
    return harvested


class ImageDescriber:
    """Annotates a record's image blocks with `image_metadata.description`."""

    def __init__(self, logger: "logging.Logger", config_service: "ConfigurationService") -> None:
        self.logger = logger
        self.config_service = config_service

    @property
    def max_images_per_record(self) -> int:
        return env_int(
            MAX_IMAGES_ENV_VAR, DEFAULT_MAX_IMAGES_PER_RECORD, lo=0, hi=1_000,
        ) or 0

    async def annotate(
        self,
        block_containers: "BlocksContainer | None",
        *,
        inherited: dict[str, str] | None = None,
    ) -> int:
        """Describe the image blocks that need it. Returns how many were written.

        Never raises: a record that cannot be described is still a record worth
        indexing, and the query side degrades to whatever text it does have.
        """
        try:
            return await self._annotate(block_containers, inherited or {})
        except Exception:
            self.logger.warning(
                "Image description failed for this record; indexing continues without it",
                exc_info=True,
            )
            return 0

    async def _annotate(
        self, block_containers: "BlocksContainer | None", inherited: dict[str, str],
    ) -> int:
        blocks = getattr(block_containers, "blocks", None) or []
        candidates = [b for b in blocks if b.type == BlockType.IMAGE and _image_uri(b)]
        if not candidates:
            return 0

        pending: list[tuple[Block, str]] = []
        reused = 0
        for block in candidates:
            if _existing_description(block):
                continue
            uri = _image_uri(block)
            if uri is None:
                continue
            carried = inherited.get(_content_key(uri))
            if carried:
                self._write(block, carried)
                reused += 1
                continue
            if self._is_decorative(block, uri):
                continue
            pending.append((block, uri))

        if not pending:
            if reused:
                self.logger.debug("Reused %d image description(s) from the previous version", reused)
            return reused

        vlm = await self._vision_model()
        if vlm is None:
            self.logger.debug(
                "No multimodal indexing model configured; %d image(s) keep whatever "
                "captions the parser found", len(pending),
            )
            return reused

        cap = self.max_images_per_record
        if cap <= 0:
            return reused
        if len(pending) > cap:
            self.logger.info(
                "Describing the first %d of %d images in this record (%s caps it)",
                cap, len(pending), MAX_IMAGES_ENV_VAR,
            )
            pending = pending[:cap]

        written = await self._describe_all(pending, vlm)
        self.logger.info(
            "Described %d image(s), reused %d from the previous version", written, reused,
        )
        return written + reused

    def _is_decorative(self, block: "Block", uri: str) -> bool:
        """Skip page furniture before paying for a vision call."""
        width = height = None
        metadata = getattr(block, "image_metadata", None)
        size = getattr(metadata, "image_size", None) if metadata else None
        if isinstance(size, dict):
            width, height = size.get("width"), size.get("height")
        if not (width and height):
            measured = read_image_dimensions(uri)
            if measured:
                width, height = measured
        return is_decorative_image(width, height)

    async def _describe_all(
        self, pending: list[tuple["Block", str]], vlm: "BaseChatModel",
    ) -> int:
        semaphore = asyncio.Semaphore(_CONCURRENCY)

        async def describe(block: "Block", uri: str) -> bool:
            async with semaphore:
                try:
                    description = await self._describe_one(uri, vlm)
                except Exception as exc:
                    # One unreadable image must not cost the record its other
                    # descriptions, so this is logged and skipped, not raised.
                    self.logger.debug("Could not describe image block %s: %s", block.index, exc)
                    return False
            if not description:
                return False
            self._write(block, description)
            return True

        results = await asyncio.gather(
            *(describe(block, uri) for block, uri in pending), return_exceptions=False,
        )
        return sum(1 for ok in results if ok)

    async def _describe_one(self, uri: str, vlm: "BaseChatModel") -> str:
        from langchain_core.messages import HumanMessage

        from app.modules.extraction.prompt_template import prompt_for_image_description
        from app.utils.aimodels import coerce_message_content_to_text

        response = await vlm.ainvoke([
            HumanMessage(content=[
                {"type": "text", "text": prompt_for_image_description},
                {"type": "image_url", "image_url": {"url": uri}},
            ]),
        ])
        return coerce_message_content_to_text(response.content).strip()[:_MAX_DESCRIPTION_CHARS]

    @staticmethod
    def _write(block: "Block", description: str) -> None:
        if block.image_metadata is None:
            block.image_metadata = ImageMetadata()
        block.image_metadata.description = description

    async def _vision_model(self) -> "BaseChatModel | None":
        """The indexing-role model, or None when it cannot see images.

        Same role and resolution the rest of the indexing pipeline uses
        (`document_extraction.py`, `vectorstore.py`), so a deployment
        configures one model for indexing and everything follows it.
        """
        from app.utils.llm import get_llm_for_role

        try:
            llm, config = await get_llm_for_role(
                self.config_service, "indexing", reasoning_effort="low",
            )
        except Exception:
            self.logger.warning("Could not resolve the indexing model", exc_info=True)
            return None
        return llm if config.get("isMultimodal") else None


__all__ = [
    "DEFAULT_MAX_IMAGES_PER_RECORD",
    "MAX_IMAGES_ENV_VAR",
    "ImageDescriber",
    "harvest_descriptions",
]
