import asyncio
import base64
import io
import logging
from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field, field_validator
from pydantic_core import to_jsonable_python

from app.agents.agent_loop.prompt_cache import (
    family_cache_strategy,
    forget_cache_params,
)
from app.config.constants.arangodb import DepartmentNames
from app.exceptions.non_retryable import NonRetryableError
from app.models.blocks import Block, SemanticMetadata
from app.modules.entity_resolution.languages import canonical_language
from app.modules.extraction.prompt_template import (
    document_extraction_record_header,
    prompt_for_document_extraction,
)
from app.modules.transformers.transformer import TransformContext, Transformer
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.aimodels import coerce_message_content_to_text
from app.utils.llm import get_llm_for_role
from app.utils.opik import get_llm_invoke_config
from app.utils.streaming import invoke_with_structured_output_and_reflection

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

DEFAULT_CONTEXT_LENGTH = 128000
PROMPT_FAMILY = "document-extraction"
CONTENT_TOKEN_RATIO = 0.85
MAX_IMAGE_DIMENSION = 2000

SUPPORTED_LLM_IMAGE_PREFIXES = (
    "data:image/png",
    "data:image/jpeg",
    "data:image/jpg",
    "data:image/gif",
    "data:image/webp",
)

_MIME_TO_PIL_FORMAT = {
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/jpg": "JPEG",
    "image/gif": "GIF",
    "image/webp": "WEBP",
}

logger = logging.getLogger(__name__)

# The block types _prepare_content reads; it skips every other type.
CLASSIFIED_BLOCK_TYPES = frozenset({"text", "image", "table_row", "code"})

# The longest cl100k_base token is 128 bytes and a character is at least one
# UTF-8 byte, so n characters are never fewer than ceil(n / 128) tokens. The
# len // 4 fallback in _prepare_content never counts fewer either.
_MAX_CHARS_PER_TOKEN = 128


def _token_floor(block_type: str, data: Any) -> int:  # noqa: ANN401 - Block.data is free-form
    """A lower bound on what _prepare_content counts for this block."""
    if isinstance(data, dict):
        if block_type == "table_row":
            data = data.get("row_natural_language_text")
        elif block_type == "code":
            data = data.get("text")
    if block_type == "image" or not isinstance(data, str):
        return 0
    return -(-len(data) // _MAX_CHARS_PER_TOKEN)


def classify_request_blocks(
    blocks: list[Block], context_length_ceiling: int | float | None
) -> list[dict]:
    """The blocks the classify endpoint will read, already JSON-ready.

    _prepare_content reads only ``type``, ``format`` and ``data``, skips
    other block types and empty blocks, and stops at the first text block
    that would take it past ``int(context_length * CONTENT_TOKEN_RATIO)``
    tokens. Serialising the rest of a large container was a measurable share
    of the indexing loop's CPU.

    With *context_length_ceiling* at or above the service's context length,
    a block after the one where the token floor alone passes the budget can
    never be reached, so it is left out; the service's own truncation still
    decides where the content ends. ``None`` keeps every block.
    """
    budget = (
        int(context_length_ceiling * CONTENT_TOKEN_RATIO)
        if context_length_ceiling
        else None
    )
    floor_total = 0
    request_blocks: list[dict] = []
    for block in blocks:
        block_type = getattr(block.type, "value", block.type)
        if block_type not in CLASSIFIED_BLOCK_TYPES or not block.data:
            continue
        data = block.data
        request_blocks.append({
            "type": block_type,
            "format": getattr(block.format, "value", block.format),
            "data": data if type(data) is str else to_jsonable_python(data),
        })
        # Only a text block can end the service's loop, so only one can end this one.
        if budget is not None and block_type != "image":
            floor_total += _token_floor(block_type, data)
            if floor_total > budget:
                break
    return request_blocks


def _downscale_base64_image(
    data_uri: str, max_dim: int = MAX_IMAGE_DIMENSION
) -> str | None:
    """Resize a base64 data-URI image so neither dimension exceeds *max_dim*.

    Returns the (possibly resized) data URI on success, or ``None`` when the
    image cannot be processed (PIL unavailable, corrupt data, etc.) so the
    caller can decide to skip the image rather than forward an oversized one.
    """
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow is not installed – cannot downscale images for LLM")
        return None

    try:
        header, b64_data = data_uri.split(",", 1)
        mime = header.replace("data:", "").split(";")[0].strip().lower()
        pil_fmt = _MIME_TO_PIL_FORMAT.get(mime)
        if not pil_fmt:
            logger.warning("Unsupported MIME type for downscaling: %s", mime)
            return None

        raw = base64.b64decode(b64_data)
        img = Image.open(io.BytesIO(raw))
        w, h = img.size

        if w <= max_dim and h <= max_dim:
            return data_uri

        # RGBA / palette images must be converted before saving as JPEG
        if pil_fmt == "JPEG" and img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")

        scale = min(max_dim / w, max_dim / h)
        new_w, new_h = int(w * scale), int(h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)

        buf = io.BytesIO()
        save_kwargs = {}
        if pil_fmt == "JPEG":
            save_kwargs["quality"] = 85
        img.save(buf, format=pil_fmt, **save_kwargs)
        new_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        logger.info("📐 Resized image from %dx%d to %dx%d for LLM", w, h, new_w, new_h)
        return f"data:{mime};base64,{new_b64}"
    except Exception as exc:
        logger.warning("Failed to downscale base64 image: %s", exc)
        return None

class SubCategories(BaseModel):
    level1: str = Field(description="Level 1 subcategory")
    level2: str = Field(description="Level 2 subcategory")
    level3: str = Field(description="Level 3 subcategory")

class DocumentClassification(BaseModel):
    departments: list[str] = Field(
        description="The list of departments this document belongs to", max_items=3
    )
    category: str = Field(description="Main category this document belongs to")
    subcategories: SubCategories = Field(
        description="Nested subcategories for the document"
    )
    languages: list[str] = Field(
        description="Natural languages the document is written in"
    )
    topics: list[str] = Field(
        description="List of key topics/themes extracted from the document"
    )
    summary: str = Field(
        description=(
            "Retrieval-facing summary. Sentence 1 must state document type, "
            "primary subject, principal parties or owning team, and time period "
            "or effective date, in that order — a downstream tool shows only the "
            "first ~600 characters, so front-loading is mandatory. Length is "
            "tiered to document depth: 3-5 sentences for thin/sparse documents, "
            "no more than 300 words typically, never exceeding 400 words. Prefer "
            "concrete named entities, identifiers, dates, and figures over "
            "connective prose. No opening filler, meta commentary, "
            "recommendations, or unsupported claims."
        )
    )

    @field_validator("languages", mode="after")
    @classmethod
    def _only_natural_languages(cls, values: list[str]) -> list[str]:
        names = (canonical_language(v) for v in values if isinstance(v, str))
        return list(dict.fromkeys(n for n in names if n))


def semantic_metadata_from_classification(classification: DocumentClassification) -> SemanticMetadata:
    return SemanticMetadata(
        departments=classification.departments,
        languages=classification.languages,
        topics=classification.topics,
        summary=classification.summary,
        categories=[classification.category],
        sub_category_level_1=classification.subcategories.level1,
        sub_category_level_2=classification.subcategories.level2,
        sub_category_level_3=classification.subcategories.level3,
    )


class DocumentExtraction(Transformer):
    def __init__(
        self, logger: logging.Logger, graph_provider: IGraphDBProvider, config_service: "ConfigurationService",
    ) -> None:
        super().__init__()
        self.logger = logger
        self.graph_provider = graph_provider
        self.config_service = config_service

    async def apply(self, ctx: TransformContext) -> None:
        record = ctx.record
        blocks = record.block_containers.blocks

        document_classification = await self.process_document(
            blocks,
            record.org_id,
            record_name=record.record_name,
            record_type=record.record_type.value,
        )
        if document_classification is None:
            record.semantic_metadata = None
            return
        record.semantic_metadata = semantic_metadata_from_classification(document_classification)
        self.logger.debug("🎯 Document extraction completed successfully")


    def _prepare_content(
        self, blocks: list[Block], *, is_multimodal_llm: bool, context_length: int,
    ) -> list[dict]:
        MAX_TOKENS = int(context_length * CONTENT_TOKEN_RATIO)
        MAX_IMAGES = 50
        total_tokens = 0
        image_count = 0
        image_cap_logged = False
        content = []

        # Lazy import tiktoken; fall back to a rough heuristic if unavailable
        enc = None
        try:
            import tiktoken  # type: ignore
            try:
                enc = tiktoken.get_encoding("cl100k_base")
            except Exception:
                enc = None
        except Exception:
            enc = None

        def count_tokens(text: str) -> int:
            if not text:
                return 0
            if enc is not None:
                try:
                    return len(enc.encode(text))
                except Exception:
                    pass
            # Fallback heuristic: ~4 chars per token
            return max(1, len(text) // 4)

        for block in blocks:
            if block.type.value == "text":
                if block.data:
                    candidate = {
                        "type": "text",
                        "text": block.data if block.data else ""
                    }
                    increment = count_tokens(candidate["text"])
                    if total_tokens + increment > MAX_TOKENS:
                        self.logger.info("✂️ Content exceeds %d tokens (%d). Truncating to head.", MAX_TOKENS, total_tokens + increment)
                        break
                    content.append(candidate)
                    total_tokens += increment
            elif block.type.value == "image":
                # Respect provider limits on images per request
                if image_count >= MAX_IMAGES:
                    if not image_cap_logged:
                        self.logger.info("🛑 Reached image cap of %d. Skipping additional images.", MAX_IMAGES)
                        image_cap_logged = True
                    continue
                if is_multimodal_llm:
                    if block.data and block.format.value == "base64":
                        image_data = block.data
                        image_data = image_data.get("uri")

                        if not image_data:
                            continue

                        if image_data.startswith(("http://", "https://")):
                            pass  # remote URLs are validated server-side
                        elif image_data.startswith(SUPPORTED_LLM_IMAGE_PREFIXES):
                            result = _downscale_base64_image(image_data)
                            if result is None:
                                self.logger.warning("⚠️ Skipping image that could not be downscaled")
                                continue
                            image_data = result
                        elif image_data.startswith("data:image/"):
                            self.logger.warning(
                                f"⚠️ Skipping unsupported image format for LLM: "
                                f"{image_data[:80]}..."
                            )
                            continue
                        else:
                            self.logger.warning(f"⚠️ Skipping invalid image URL format: {image_data[:100]}")
                            continue

                        candidate = {
                            "type": "image_url",
                            "image_url": {
                                "url": image_data
                            }
                        }
                        content.append(candidate)
                        image_count += 1
                    else:
                        continue
                else:
                    continue

            elif block.type.value == "table_row":
                if block.data:
                    if isinstance(block.data, dict):
                        table_row_text = block.data.get("row_natural_language_text")
                    else:
                        table_row_text = str(block.data)
                    candidate = {
                        "type": "text",
                        "text": table_row_text if table_row_text else ""
                    }
                    increment = count_tokens(candidate["text"])
                    if total_tokens + increment > MAX_TOKENS:
                        self.logger.info("✂️ Content exceeds %d tokens (%d). Truncating to head.", MAX_TOKENS, total_tokens + increment)
                        break
                    content.append(candidate)
                    total_tokens += increment

            elif block.type.value == "code" and block.data:
                code_text = block.data.get("text", "") if isinstance(block.data, dict) else str(block.data)
                if code_text:
                    candidate = {
                        "type": "text",
                        "text": code_text,
                    }
                    increment = count_tokens(code_text)
                    if total_tokens + increment > MAX_TOKENS:
                        self.logger.info("✂️ Content exceeds %d tokens (%d). Truncating to head.", MAX_TOKENS, total_tokens + increment)
                        break
                    content.append(candidate)
                    total_tokens += increment

        return content

    @staticmethod
    def _fill_prompt(department_list: str) -> str:
        return prompt_for_document_extraction.replace("{department_list}", department_list)

    @staticmethod
    def _record_header(record_name: str, record_type: str) -> str:
        """Substitute template placeholders via `.replace()`, not `.format()`.

        A record name containing braces (e.g. "report_{final}.pdf") would raise
        inside `.format()`, and record names come from connector data.
        """
        return (
            document_extraction_record_header
            .replace("{record_name}", record_name or "(unknown)")
            .replace("{record_type}", record_type or "(unknown)")
        )

    async def classify(
        self,
        blocks: list[Block],
        org_id: str,
        departments: list[str] | None = None,
        record_name: str = "",
        record_type: str = "",
    ) -> DocumentClassification | None:
        """Extract metadata using pre-fetched *departments*.

        This variant is intended for use by the standalone Extraction Service
        where injecting a graph provider is undesirable.  When *departments* is
        ``None`` or empty the method falls back to the DepartmentNames defaults
        rather than making a graph call.
        """
        self.logger.debug("🎯 Extracting domain metadata (pre-fetched departments)")
        self.llm, config = await get_llm_for_role(self.config_service, "indexing", reasoning_effort="low")
        try:
            resolved_departments: list[str] = departments or [dept.value for dept in DepartmentNames]
            return await self._classify_blocks(
                blocks, org_id, resolved_departments, config, record_name, record_type,
            )
        except Exception as e:
            self.logger.error(f"❌ Error during classify: {str(e)}")
            raise

    async def extract_metadata(
        self,
        blocks: list[Block],
        org_id: str,
        record_name: str = "",
        record_type: str = "",
    ) -> DocumentClassification | None:
        """
        Extract metadata from document content.
        """
        self.logger.debug("🎯 Extracting domain metadata")
        self.llm, config = await get_llm_for_role(self.config_service, "indexing", reasoning_effort="low")
        try:
            self.logger.debug(f"🎯 Extracting departments for org_id: {org_id}")
            departments = await self.graph_provider.get_departments(org_id)
            if not departments:
                departments = [dept.value for dept in DepartmentNames]
            return await self._classify_blocks(blocks, org_id, departments, config, record_name, record_type)
        except Exception as e:
            self.logger.error(f"❌ Error during metadata extraction: {str(e)}")
            raise

    async def _classify_blocks(
        self,
        blocks: list[Block],
        org_id: str,
        departments: list[str],
        config: dict,
        record_name: str,
        record_type: str,
    ) -> DocumentClassification | None:
        context_length = config.get("contextLength") or DEFAULT_CONTEXT_LENGTH
        self.logger.debug(f"Context length: {context_length}")
        # Off the loop: PIL resizing and tiktoken on a large deck stall it for seconds.
        content = await asyncio.to_thread(
            self._prepare_content,
            blocks, is_multimodal_llm=bool(config.get("isMultimodal")), context_length=context_length,
        )
        if len(content) == 0:
            self.logger.info("No content to process in document extraction")
            return None
        department_list = "\n".join(f'     - "{dept}"' for dept in departments)
        document = [
            {"type": "text", "text": self._record_header(record_name, record_type)},
            {"type": "text", "text": "Document Content: "},
            *content,
        ]
        # The instructions go first and alone, so every record of the org sends
        # the same prefix and the provider can serve it from cache.
        llm = self.llm
        model_name = str((config.get("configuration") or {}).get("model") or "")
        cache = family_cache_strategy(
            llm, family=PROMPT_FAMILY, org_id=org_id, shard_key=record_name, model_name=model_name,
        )
        system = cache.system(self._fill_prompt(department_list), "")
        try:
            parsed_response = await invoke_with_structured_output_and_reflection(
                llm, [system, HumanMessage(content=document)], DocumentClassification, prompt_cache=cache,
                # Recorded under the name the strategy was chosen with, so the next record skips it.
                on_cache_rejected=lambda: forget_cache_params(llm, model_name),
                raise_call_errors=True,
            )
        except NonRetryableError:
            raise
        except Exception as e:
            # The model did not answer. The fallback would resend the whole
            # document to the same model, doubling every record's cost while
            # it is down; only a reply that did not fit the schema earns one.
            self.logger.warning("⚠️ Document classification call failed, skipping it: %s", e)
            return None
        if parsed_response is not None:
            self.logger.debug("✅ Document classification parsed successfully")
            return parsed_response
        self.logger.warning(
            "⚠️ Structured extraction failed after all attempts. Falling back to plain LLM summary."
        )
        # The instructions carry the summary rules, which the fallback summary follows too.
        instructions = {"type": "text", "text": self._fill_prompt(department_list)}
        return await self._fallback_summary([instructions, *document])

    async def _fallback_summary(
        self, message_content: list[dict]
    ) -> DocumentClassification | None:
        """Plain LLM call to get a summary when structured extraction fails."""
        try:
            fallback_prompt = [
                {
                    "type": "text",
                    "text": (
                        "Provide a concise summary of the following document/record. "
                        "Return only the summary text, nothing else."
                    ),
                },
                {"type": "text", "text": "Document Content: "},
            ]
            fallback_prompt.extend(
                item for item in message_content
                if item.get("type") in ("text", "image_url")
            )

            response = await self.llm.ainvoke(
                [HumanMessage(content=fallback_prompt)],
                config=get_llm_invoke_config(self.llm),
            )

            if hasattr(response, "content"):
                raw_content = response.content
            elif isinstance(response, str):
                raw_content = response
            else:
                raw_content = None

            summary_text = coerce_message_content_to_text(raw_content).strip()
            if not summary_text:
                self.logger.error("❌ Fallback summary returned empty response")
                return None

            self.logger.info("✅ Fallback summary obtained successfully")
            return DocumentClassification(
                departments=[],
                category="",
                subcategories=SubCategories(level1="", level2="", level3=""),
                languages=[],
                topics=[],
                summary=summary_text,
            )
        except NonRetryableError:
            raise
        except Exception as e:
            self.logger.error(f"❌ Fallback summary call failed: {e}")
            return None

    async def process_document(
        self,
        blocks: list[Block],
        org_id: str,
        record_name: str = "",
        record_type: str = "",
    ) -> DocumentClassification:
        self.logger.info("🖼️ Processing blocks for semantic metadata extraction")
        return await self.extract_metadata(blocks, org_id, record_name=record_name, record_type=record_type)



