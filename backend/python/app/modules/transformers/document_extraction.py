import base64
import io
import json
import logging
from typing import List, Optional

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app.config.constants.arangodb import DepartmentNames
from app.models.blocks import Block, SemanticMetadata
from app.modules.extraction.prompt_template import (
    prompt_for_document_extraction,
)
from app.modules.transformers.transformer import TransformContext, Transformer
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.aimodels import coerce_message_content_to_text
from app.utils.llm import get_llm_for_role
from app.utils.streaming import invoke_with_structured_output_and_reflection

DEFAULT_CONTEXT_LENGTH = 128000
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
    departments: List[str] = Field(
        description="The list of departments this document belongs to", max_items=3
    )
    category: str = Field(description="Main category this document belongs to")
    subcategories: SubCategories = Field(
        description="Nested subcategories for the document"
    )
    languages: List[str] = Field(
        description="List of languages detected in the document"
    )
    topics: List[str] = Field(
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

class DocumentExtraction(Transformer):
    def __init__(self, logger, graph_provider: IGraphDBProvider, config_service) -> None:
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
        record.semantic_metadata = SemanticMetadata(
            departments=document_classification.departments,
            languages=document_classification.languages,
            topics=document_classification.topics,
            summary=document_classification.summary,
            categories=[document_classification.category],
            sub_category_level_1=document_classification.subcategories.level1,
            sub_category_level_2=document_classification.subcategories.level2,
            sub_category_level_3=document_classification.subcategories.level3,
        )
        self.logger.debug("🎯 Document extraction completed successfully")


    def _prepare_content(self, blocks: List[Block], is_multimodal_llm: bool, context_length: int) -> List[dict]:
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
    def _fill_prompt(department_list: str, record_name: str, record_type: str) -> str:
        """Substitute template placeholders via `.replace()`, not `.format()`.

        A record name containing braces (e.g. "report_{final}.pdf") would raise
        inside `.format()`, and record names come from connector data.
        """
        return (
            prompt_for_document_extraction
            .replace("{department_list}", department_list)
            .replace("{record_name}", record_name or "(unknown)")
            .replace("{record_type}", record_type or "(unknown)")
        )

    async def classify(
        self,
        blocks: List[Block],
        org_id: str,
        departments: Optional[List[str]] = None,
        record_name: str = "",
        record_type: str = "",
    ) -> Optional[DocumentClassification]:
        """Extract metadata using pre-fetched *departments*.

        This variant is intended for use by the standalone Extraction Service
        where injecting a graph provider is undesirable.  When *departments* is
        ``None`` or empty the method falls back to the DepartmentNames defaults
        rather than making a graph call.
        """
        self.logger.debug("🎯 Extracting domain metadata (pre-fetched departments)")
        self.llm, config = await get_llm_for_role(self.config_service, "indexing", reasoning_effort="low")
        is_multimodal_llm = config.get("isMultimodal")
        context_length = config.get("contextLength") or DEFAULT_CONTEXT_LENGTH
        self.logger.debug(f"Context length: {context_length}")

        try:
            resolved_departments: List[str] = departments or [dept.value for dept in DepartmentNames]
            department_list = "\n".join(f'     - "{dept}"' for dept in resolved_departments)
            filled_prompt = self._fill_prompt(department_list, record_name, record_type)
            content = self._prepare_content(blocks, is_multimodal_llm, context_length)
            if len(content) == 0:
                self.logger.info("No content to process in document extraction")
                return None
            message_content = [
                {"type": "text", "text": filled_prompt},
                {"type": "text", "text": "Document Content: "},
            ]
            message_content.extend(content)
            messages = [HumanMessage(content=message_content)]
            parsed_response = await invoke_with_structured_output_and_reflection(
                self.llm, messages, DocumentClassification
            )
            if parsed_response is not None:
                self.logger.debug("✅ Document classification parsed successfully")
                return parsed_response
            self.logger.warning(
                "⚠️ Structured extraction failed after all attempts. Falling back to summary."
            )
            return await self._fallback_summary(message_content)
        except Exception as e:
            self.logger.error(f"❌ Error during classify: {str(e)}")
            raise

    async def extract_metadata(
        self,
        blocks: List[Block],
        org_id: str,
        record_name: str = "",
        record_type: str = "",
    ) -> Optional[DocumentClassification]:
        """
        Extract metadata from document content.
        """
        self.logger.debug("🎯 Extracting domain metadata")
        self.llm, config = await get_llm_for_role(self.config_service, "indexing", reasoning_effort="low")
        is_multimodal_llm = config.get("isMultimodal")
        context_length = config.get("contextLength") or DEFAULT_CONTEXT_LENGTH

        self.logger.debug(f"Context length: {context_length}")

        try:
            self.logger.debug(f"🎯 Extracting departments for org_id: {org_id}")
            departments = await self.graph_provider.get_departments(org_id)
            if not departments:
                departments = [dept.value for dept in DepartmentNames]

            department_list = "\n".join(f'     - "{dept}"' for dept in departments)
            filled_prompt = self._fill_prompt(department_list, record_name, record_type)

            # Prepare multimodal content
            content = self._prepare_content(blocks, is_multimodal_llm, context_length)

            if len(content) == 0:
                self.logger.info("No content to process in document extraction")
                return None
            # Create the multimodal message
            message_content = [
                {
                    "type": "text",
                    "text": filled_prompt
                },
                {
                    "type": "text",
                    "text": "Document Content: "
                }
            ]
            # Add the multimodal content
            message_content.extend(content)

            # Create the message for VLM
            messages = [HumanMessage(content=message_content)]

            # Use centralized utility with reflection
            parsed_response = await invoke_with_structured_output_and_reflection(
                self.llm, messages, DocumentClassification
            )

            if parsed_response is not None:
                self.logger.debug("✅ Document classification parsed successfully")
                return parsed_response

            self.logger.warning(
                "⚠️ Structured extraction failed after all attempts. "
                "Falling back to plain LLM summary."
            )
            return await self._fallback_summary(message_content)

        except Exception as e:
            self.logger.error(f"❌ Error during metadata extraction: {str(e)}")
            raise

    async def _fallback_summary(
        self, message_content: List[dict]
    ) -> Optional[DocumentClassification]:
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
                [HumanMessage(content=fallback_prompt)]
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
        except Exception as e:
            self.logger.error(f"❌ Fallback summary call failed: {e}")
            return None

    async def process_document(
        self,
        blocks: List[Block],
        org_id: str,
        record_name: str = "",
        record_type: str = "",
    ) -> DocumentClassification:
        self.logger.info("🖼️ Processing blocks for semantic metadata extraction")
        return await self.extract_metadata(blocks, org_id, record_name=record_name, record_type=record_type)



