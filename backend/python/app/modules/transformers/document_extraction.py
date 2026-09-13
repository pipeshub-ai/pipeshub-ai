import base64
import io
import logging
from typing import TYPE_CHECKING, Any, Literal, Optional

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app.config.constants.arangodb import DepartmentNames
from app.models.blocks import Block, SemanticMetadata
from app.modules.extraction.prompt_template import (
    prompt_for_document_extraction,
)
from app.modules.transformers.transformer import TransformContext, Transformer
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.llm_gateway.gateway import (
    get_llm_gateway,
    llm_call_site,
    provider_key,
)
from app.utils.aimodels import coerce_message_content_to_text
from app.utils.llm import (
    LLMNotConfiguredError,
    LLMUnavailableError,
    get_llm_for_role,
)
from app.utils.streaming import invoke_with_structured_output_and_reflection

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

DEFAULT_CONTEXT_LENGTH = 128000
CONTENT_TOKEN_RATIO = 0.85
MAX_IMAGE_DIMENSION = 2000
SentimentType = Literal["Positive", "Neutral", "Negative"]

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
        description="List of languages detected in the document"
    )
    sentiment: SentimentType = Field(description="Overall sentiment of the document")
    confidence_score: float = Field(
        description="Confidence score of the classification", ge=0, le=1
    )
    topics: list[str] = Field(
        description="List of key topics/themes extracted from the document"
    )
    summary: str = Field(description="Summary of the document")


class ExtractionLLMError(Exception):
    """The model produced neither a classification nor a fallback summary."""

    code = "LLM_FAILED"


class DocumentExtraction(Transformer):
    def __init__(self, logger, graph_provider: IGraphDBProvider, config_service) -> None:
        super().__init__()
        self.logger = logger
        self.graph_provider = graph_provider
        self.config_service = config_service

    async def apply(self, ctx: TransformContext) -> None:
        record = ctx.record
        try:
            record.semantic_metadata = await self.process_document(
                record.block_containers.blocks, record.org_id
            )
        except LLMNotConfiguredError as e:
            self.logger.info("⏭️ Extraction skipped for record %s: %s", record.id, e)
            record.semantic_metadata = None
            ctx.extraction_skip_reason = str(e)
            return
        except (ExtractionLLMError, LLMUnavailableError) as e:
            # The record is already searchable: an LLM failure fails extraction only
            # (no metadata → extractionStatus FAILED), not the record.
            self.logger.error("❌ Document extraction failed for record %s: %s", record.id, e)
            record.semantic_metadata = None
            return
        self.logger.debug("🎯 Document extraction completed successfully")


    def _prepare_content(self, blocks: list[Block], is_multimodal_llm: bool, context_length: int) -> list[dict]:
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

                        if image_data.startswith("http://") or image_data.startswith("https://"):
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

            elif block.type.value == "code":
                if block.data:
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
    def render_prompt(departments: list[str]) -> str:
        department_list = "\n".join(f'     - "{dept}"' for dept in departments)
        sentiment_list = "\n".join(
            f'     - "{sentiment}"' for sentiment in SentimentType.__args__
        )
        return prompt_for_document_extraction.format(
            department_list=department_list, sentiment_list=sentiment_list
        )

    @staticmethod
    def to_semantic_metadata(classification: DocumentClassification) -> SemanticMetadata:
        """Map the LLM's schema onto the stored model. A blank name means "not known"."""

        def names(values: list[str]) -> list[str]:
            return [value.strip() for value in values if value and value.strip()]

        def optional_name(value: str) -> Optional[str]:
            return value.strip() or None

        category = optional_name(classification.category)
        return SemanticMetadata(
            departments=names(classification.departments),
            languages=names(classification.languages),
            topics=names(classification.topics),
            summary=classification.summary,
            categories=[category] if category else [],
            sub_category_level_1=optional_name(classification.subcategories.level1),
            sub_category_level_2=optional_name(classification.subcategories.level2),
            sub_category_level_3=optional_name(classification.subcategories.level3),
        )

    async def classify(
        self,
        blocks: list[Block],
        org_id: str,
        departments: Optional[list[str]] = None,
    ) -> SemanticMetadata | None:
        """Classify with pre-fetched *departments*; makes no graph call.

        For the standalone Extraction Service, which has no graph provider.
        Empty *departments* falls back to the DepartmentNames defaults.
        """
        return await self._classify_blocks(
            blocks, departments or [dept.value for dept in DepartmentNames]
        )

    async def extract_metadata(
        self, blocks: list[Block], org_id: str
    ) -> SemanticMetadata | None:
        departments = await self.graph_provider.get_departments(org_id)
        return await self._classify_blocks(
            blocks, departments or [dept.value for dept in DepartmentNames]
        )

    async def _classify_blocks(
        self, blocks: list[Block], departments: list[str]
    ) -> SemanticMetadata | None:
        """Classify *blocks*; ``None`` means there was nothing to classify.

        Raises:
            ExtractionLLMError: neither the structured call nor the fallback
                summary produced anything.
        """
        # A local, not an attribute: one instance classifies documents of different orgs at once.
        llm, config = await get_llm_for_role(self.config_service, "indexing", reasoning_effort="low")
        is_multimodal_llm = config.get("isMultimodal")
        context_length = config.get("contextLength") or DEFAULT_CONTEXT_LENGTH

        content = self._prepare_content(blocks, is_multimodal_llm, context_length)
        if not content:
            self.logger.info("No content to process in document extraction")
            return None

        message_content: list[str | dict[Any, Any]] = [
            {"type": "text", "text": self.render_prompt(departments)},
            {"type": "text", "text": "Document Content: "},
            *content,
        ]
        with llm_call_site("classify"):
            # An outage raises rather than falling back: the fallback would call the same provider.
            parsed_response = await invoke_with_structured_output_and_reflection(
                llm, [HumanMessage(content=message_content)], DocumentClassification, raise_unavailable=True
            )
        if parsed_response is not None:
            self.logger.debug("✅ Document classification parsed successfully")
            return self.to_semantic_metadata(parsed_response)

        self.logger.warning(
            "⚠️ Structured extraction failed after all attempts. "
            "Falling back to plain LLM summary."
        )
        fallback = await self._fallback_summary(llm, message_content)
        if fallback is None:
            raise ExtractionLLMError(
                "Document classification and the fallback summary both failed"
            )
        return fallback

    async def _fallback_summary(
        self, llm: "BaseChatModel", message_content: list[str | dict[Any, Any]]
    ) -> SemanticMetadata | None:
        """Plain LLM call to get a summary when structured extraction fails."""
        try:
            fallback_prompt: list[str | dict[Any, Any]] = [
                {
                    "type": "text",
                    "text": (
                        "Provide a concise summary of the following document/record. "
                        "Return only the summary text, nothing else."
                    ),
                },
                {"type": "text", "text": "Document Content: "},
            ]
            for item in message_content:
                if isinstance(item, str):
                    fallback_prompt.append({"type": "text", "text": item})
                elif item.get("type") in ("text", "image_url"):
                    fallback_prompt.append(item)

            response = await get_llm_gateway().invoke(
                llm, [HumanMessage(content=fallback_prompt)], provider=provider_key(llm), call_site="classify"
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
            # Only the summary is known. None lists and no category tell the graph
            # writer to keep the record's existing edges instead of clearing them.
            return SemanticMetadata(summary=summary_text, categories=[])
        except LLMUnavailableError:
            # The provider is down, not the document: the caller waits instead of failing it.
            raise
        except Exception as e:
            self.logger.error(f"❌ Fallback summary call failed: {e}")
            return None

    async def process_document(
        self, blocks: list[Block], org_id: str
    ) -> SemanticMetadata | None:
        self.logger.info("🖼️ Processing blocks for semantic metadata extraction")
        return await self.extract_metadata(blocks, org_id)



