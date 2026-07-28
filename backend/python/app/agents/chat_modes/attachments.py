"""Attachment resolution for `/chat/stream`, extracted from chatbot.py's
former `_build_attachment_llm_messages()`/`_append_conversation_history()`
so it survives the migration to the agent loop instead of being dropped.

Two attachment kinds, two different fates on this path:

- Document attachments (PDF/text/markdown) resolve to the SAME `<record>`
  text-block format `record_to_message_content()` already produces for
  chatbot and for `dynamic_fetch_full_record` -- folded into the agent's
  `Goal.constraints` as plain text, so citation IDs stay on the one shared
  `CitationRefMapper` the rest of the turn uses.
- Image attachments do NOT currently reach the agent loop's first turn as
  multimodal content. This is not a regression introduced by this
  migration: `agents/agent_loop/respond.py`'s module docstring already
  documents this as an accepted, pre-existing gap for agent endpoints
  ("current-turn attachments are no longer resolved into multimodal blocks
  here... tracked as a follow-up, not part of this fix") -- `Goal` itself
  (`agent_loop_lib/core/types.py`) is a text-only pydantic model with no
  multimodal `Part` slot to attach an image to. What this module DOES do
  for images: note their presence/filenames as a text constraint (so the
  model at least knows they exist and can ask the user to describe them or
  try `dynamic_fetch_full_record` if the image was itself indexed as a
  record) and merge their `virtualRecordId`s into the retrieval scope below.

Both kinds' `virtualRecordId`s are merged into the retrieval filter scope
(`connector_ids` passed to `search_internal_knowledge` / the `filters`
prefetch uses) so a retrieval call -- prefetch or a follow-up tool call --
can surface the attachment's indexed content alongside the rest of the
knowledge base.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.utils.chat_helpers import CitationRefMapper, record_to_message_content

if TYPE_CHECKING:
    import logging

    from app.modules.transformers.blob_storage import BlobStorage

__all__ = ["ResolvedAttachments", "resolve_attachments"]

_TEXT_ATTACHMENT_MIME_TYPES = frozenset({"text/plain", "text/markdown", "text/mdx"})
_DOC_ATTACHMENT_MIME_TYPES = _TEXT_ATTACHMENT_MIME_TYPES | {"application/pdf"}


@dataclass
class ResolvedAttachments:
    """Everything the bridge needs to fold attachments into a chat-mode
    turn without re-deriving any of it."""

    context_text: str  # empty string when there is nothing to add
    virtual_record_ids: list[str] = field(default_factory=list)
    has_image_attachments: bool = False


def _is_image(mime_type: str) -> bool:
    return mime_type.lower().startswith("image/")


def _is_doc(mime_type: str) -> bool:
    return mime_type.lower() in _DOC_ATTACHMENT_MIME_TYPES


async def resolve_attachments(
    attachments: list[dict[str, Any]] | None,
    *,
    blob_store: "BlobStorage",
    org_id: str,
    ref_mapper: CitationRefMapper,
    logger: "logging.Logger",
) -> ResolvedAttachments:
    """Resolves the current turn's `query_info["attachments"]` into text
    context + retrieval-scope record IDs. `ref_mapper` is the SAME mapper
    the rest of the turn (prefetch, retrieval tool) shares, so citation IDs
    minted here don't collide with or duplicate ones minted elsewhere.
    """
    if not attachments:
        return ResolvedAttachments(context_text="")

    doc_attachments = [
        att for att in attachments
        if isinstance(att, dict) and _is_doc((att.get("mimeType") or ""))
    ]
    image_attachments = [
        att for att in attachments
        if isinstance(att, dict) and _is_image((att.get("mimeType") or ""))
    ]

    virtual_record_ids: list[str] = []
    content_blocks: list[dict[str, Any]] = []

    for att in doc_attachments:
        vrid = att.get("virtualRecordId") or ""
        if not vrid:
            continue
        virtual_record_ids.append(vrid)
        try:
            record = await blob_store.get_record_from_storage(vrid, org_id)
        except Exception as exc:  # noqa: BLE001 - one bad attachment must not fail the turn
            logger.warning("resolve_attachments: failed to resolve vrid=%s: %s", vrid, exc)
            continue
        if not record:
            continue
        record_blocks, ref_mapper = record_to_message_content(record, ref_mapper=ref_mapper)
        content_blocks.extend(record_blocks)

    for att in image_attachments:
        vrid = att.get("virtualRecordId") or ""
        if vrid:
            virtual_record_ids.append(vrid)

    parts: list[str] = []
    if content_blocks:
        parts.append("Attached documents:")
        parts.extend(
            block["text"] for block in content_blocks if block.get("type") == "text" and block.get("text")
        )
    if image_attachments:
        names = ", ".join(att.get("fileName", "unnamed") for att in image_attachments if isinstance(att, dict))
        parts.append(
            f"The user also attached {len(image_attachments)} image(s) ({names}) this turn. "
            "Image content is not visible to you directly -- if the image is relevant, ask the "
            "user to describe it, or try searching/fetching the record if it was indexed."
        )

    return ResolvedAttachments(
        context_text="\n\n".join(parts),
        virtual_record_ids=virtual_record_ids,
        has_image_attachments=bool(image_attachments),
    )
