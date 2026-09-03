import base64
import io
import logging
from functools import lru_cache
from typing import List, Literal, Optional, Union

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app.config.constants.arangodb import DepartmentNames
from app.models.blocks import Block, BlockGroup, SemanticMetadata
from app.modules.extraction.prompt_template import (
    prompt_for_code_extraction,
    prompt_for_document_extraction,
)
from app.modules.parsers.code_parser.models import FILLER_KINDS
from app.modules.transformers.transformer import TransformContext, Transformer
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.aimodels import coerce_message_content_to_text
from app.utils.llm import get_llm_for_role
from app.utils.streaming import invoke_with_structured_output_and_reflection

DEFAULT_CONTEXT_LENGTH = 128000
CONTENT_TOKEN_RATIO = 0.85
MAX_IMAGE_DIMENSION = 2000
SentimentType = Literal["Positive", "Neutral", "Negative"]

# Filler spans worth showing the LLM as raw source. `imports` is the single best
# evidence for external dependencies and `statements` carries module-level
# wiring (router registration, env reads); the rest is comments and whitespace.
CODE_RAW_TEXT_KINDS = frozenset({"imports", "statements"})
MAX_RAW_TEXT_CHARS_PER_BLOCK = 1500
MAX_DOCSTRING_CHARS = 400
MAX_EDGE_TARGETS_PER_RELATION = 25

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


@lru_cache(maxsize=1)
def _token_encoder() -> Optional[object]:
    """Return a cached tiktoken encoder, or ``None`` when unavailable."""
    try:
        import tiktoken
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


def count_tokens(text: str) -> int:
    """Token count for *text*, falling back to ~4 chars per token."""
    if not text:
        return 0
    enc = _token_encoder()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return max(1, len(text) // 4)


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
    sentiment: SentimentType = Field(description="Overall sentiment of the document")
    confidence_score: float = Field(
        description="Confidence score of the classification", ge=0, le=1
    )
    topics: List[str] = Field(
        description="List of key topics/themes extracted from the document"
    )
    summary: str = Field(description="Summary of the document")


# A closed set, enforced by the schema rather than only by the prompt: an open
# string lets the model drift across "Service" / "Backend Service" / "Services"
# for the same file, which makes the field useless as a filter.
ArchitectureRole = Literal[
    "API Route",
    "Service",
    "Repository / Data Access",
    "Connector",
    "Pipeline Transformer",
    "Model / Schema",
    "Parser",
    "Agent Tool",
    "Middleware",
    "Event Handler",
    "Factory",
    "Utility",
    "Config",
    "Test",
    "Migration",
    "Script",
    "CLI Command",
    "UI Component",
    "State Store",
    "Hook",
    "Unknown",
]


class CodeClassification(BaseModel):
    architecture_role: ArchitectureRole = Field(
        description="The architectural layer this file belongs to"
    )
    category: str = Field(description="Broad system area this file belongs to")
    subcategories: SubCategories = Field(
        description="Nested feature/domain path for the file"
    )
    topics: List[str] = Field(
        description="3 to 6 cross-cutting technical concepts as lowercase noun phrases"
    )
    summary: str = Field(
        description="2 to 4 sentence capability statement for a developer searching the codebase"
    )
    design_patterns: List[str] = Field(
        default_factory=list,
        description="Structural design patterns genuinely present in the file (0 to 3)",
    )
    external_dependencies: List[str] = Field(
        default_factory=list,
        description="External systems, services and APIs this file talks to",
    )


def to_semantic_metadata(
    classification: Union[DocumentClassification, CodeClassification],
) -> SemanticMetadata:
    """Map either classification shape onto the shared SemanticMetadata model.

    ``departments`` and ``languages`` are lists rather than ``None`` on the code
    path because ``GraphDBTransformer.save_metadata_to_db`` iterates them
    directly. The programming language is deliberately not written into
    ``languages`` -- that collection holds natural languages, and
    ``CodeFileRecord.language`` already carries the code one.
    """
    category = (classification.category or "").strip()
    common = {
        "summary": classification.summary,
        "topics": classification.topics,
        "categories": [category] if category else [],
        "sub_category_level_1": classification.subcategories.level1,
        "sub_category_level_2": classification.subcategories.level2,
        "sub_category_level_3": classification.subcategories.level3,
    }

    if isinstance(classification, CodeClassification):
        return SemanticMetadata(
            **common,
            departments=[],
            languages=[],
            architecture_role=classification.architecture_role,
            design_patterns=classification.design_patterns,
            external_dependencies=classification.external_dependencies,
        )

    return SemanticMetadata(
        **common,
        departments=classification.departments,
        languages=classification.languages,
    )


def find_code_summary_block(blocks: List[Block]) -> Optional[Block]:
    """Return the file-summary block CodeFileParser emits, if there is one.

    This locates the code prompt's input, it does not decide the branch — that
    is the caller's ``is_code``. The block carries the repo-relative path, the
    language, and the symbol table the prompt renders instead of raw source,
    none of which reach the extraction service any other way. Absent on a file
    with no tree-sitter grammar, which parses as prose. No other parser emits
    ``BlockType.RECORD_SUMMARY``.
    """
    for block in blocks:
        if (
            block.type.value == "record_summary"
            and block.code_metadata is not None
            and block.code_metadata.kind == "file_summary"
        ):
            return block
    return None

class DocumentExtraction(Transformer):
    def __init__(self, logger, graph_provider: IGraphDBProvider, config_service) -> None:
        super().__init__()
        self.logger = logger
        self.graph_provider = graph_provider
        self.config_service = config_service

    async def apply(self, ctx: TransformContext) -> None:
        record = ctx.record
        blocks = record.block_containers.blocks

        summary_block = find_code_summary_block(blocks) if ctx.is_code else None
        if summary_block is not None:
            classification = await self.process_code_document(
                blocks, summary_block, record.block_containers.block_groups
            )
        else:
            classification = await self.process_document(blocks, record.org_id)

        if classification is None:
            record.semantic_metadata = None
            return
        record.semantic_metadata = to_semantic_metadata(classification)
        self.logger.debug("🎯 Document extraction completed successfully")

    def _prepare_content(self, blocks: List[Block], is_multimodal_llm: bool, context_length: int) -> List[dict]:
        MAX_TOKENS = int(context_length * CONTENT_TOKEN_RATIO)
        MAX_IMAGES = 50
        total_tokens = 0
        image_count = 0
        image_cap_logged = False
        content = []

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

    async def classify(
        self,
        blocks: List[Block],
        org_id: str,
        departments: Optional[List[str]] = None,
        block_groups: Optional[List[BlockGroup]] = None,
        is_code: bool = False,
    ) -> Optional[Union[DocumentClassification, CodeClassification]]:
        """Extract metadata using pre-fetched *departments*.

        This variant is intended for use by the standalone Extraction Service
        where injecting a graph provider is undesirable.  When *departments* is
        ``None`` or empty the method falls back to the DepartmentNames defaults
        rather than making a graph call.

        *is_code* is the caller's record-level decision (``events._is_code_file``),
        so the service pipeline and the inline pipeline classify a given record
        identically. The summary block is still read for the path and language the
        code prompt interpolates; a record flagged as code that produced no such
        block was parsed as prose (no tree-sitter grammar) and has nothing for
        that prompt to describe, so it falls through to the document prompt.
        """

        summary_block = find_code_summary_block(blocks) if is_code else None
        if summary_block is not None:
            return await self.process_code_document(blocks, summary_block, block_groups)
        if is_code:
            self.logger.info(
                "Record flagged as code has no summary block; using document extraction"
            )

        self.logger.info("🎯 Extracting domain metadata (pre-fetched departments)")
        self.llm, config = await get_llm_for_role(self.config_service, "indexing", reasoning_effort="low")
        is_multimodal_llm = config.get("isMultimodal")
        context_length = config.get("contextLength") or DEFAULT_CONTEXT_LENGTH
        self.logger.debug(f"Context length: {context_length}")

        try:
            resolved_departments: List[str] = departments or [dept.value for dept in DepartmentNames]
            department_list = "\n".join(f'     - "{dept}"' for dept in resolved_departments)
            sentiment_list = "\n".join(
                f'     - "{sentiment}"' for sentiment in SentimentType.__args__
            )
            filled_prompt = prompt_for_document_extraction.replace(
                "{department_list}", department_list
            ).replace("{sentiment_list}", sentiment_list)
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
        self, blocks: List[Block], org_id: str
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

            sentiment_list = "\n".join(
                f'     - "{sentiment}"' for sentiment in SentimentType.__args__
            )

            filled_prompt = prompt_for_document_extraction.replace(
                "{department_list}", department_list
            ).replace("{sentiment_list}", sentiment_list)


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
                sentiment="Neutral",
                confidence_score=0.0,
                topics=[],
                summary=summary_text,
            )
        except Exception as e:
            self.logger.error(f"❌ Fallback summary call failed: {e}")
            return None

    async def process_document(self, blocks: List[Block], org_id: str) -> DocumentClassification:
            self.logger.info("🖼️ Processing blocks for semantic metadata extraction")
            return await self.extract_metadata(blocks, org_id)

    async def process_code_document(
        self,
        blocks: List[Block],
        summary_block: Block,
        block_groups: Optional[List[BlockGroup]] = None,
    ) -> Optional[CodeClassification]:
        """Classify a source file using the code-specific prompt.

        *summary_block* is the file-summary block from
        :func:`find_code_summary_block`; it supplies the repo-relative path and
        the language, neither of which is on the ``Record`` the indexing
        pipeline builds (``convert_record_dict_to_record`` returns a base
        ``Record``, and ``filePath`` lives on the codeFiles node).
        """
        self.logger.info("💻 Extracting code metadata")
        self.llm, config = await get_llm_for_role(
            self.config_service, "indexing", reasoning_effort="low"
        )
        context_length = config.get("contextLength") or DEFAULT_CONTEXT_LENGTH

        try:
            summary_data = summary_block.data if isinstance(summary_block.data, dict) else {}
            file_path = summary_data.get("file_path") or summary_block.name or "unknown"
            language = (summary_block.code_metadata.language if summary_block.code_metadata else None) or "unknown"

            architecture_role_list = "\n".join(
                f'     - "{role}"' for role in ArchitectureRole.__args__
            )
            filled_prompt = (
                prompt_for_code_extraction
                .replace("{file_path}", file_path)
                .replace("{language}", language)
                .replace("{architecture_role_list}", architecture_role_list)
            )

            content = self._prepare_code_content(
                blocks, summary_block, context_length, block_groups
            )
            if not content:
                self.logger.info("No content to process in code extraction")
                return None

            message_content = [
                {"type": "text", "text": filled_prompt},
                {"type": "text", "text": content},
            ]
            messages = [HumanMessage(content=message_content)]

            parsed_response = await invoke_with_structured_output_and_reflection(
                self.llm, messages, CodeClassification
            )
            if parsed_response is not None:
                self.logger.info("✅ Code classification parsed successfully")
                return parsed_response

            self.logger.warning(
                "⚠️ Structured code extraction failed after all attempts. Falling back to summary."
            )
            return await self._code_fallback_summary(content)
        except Exception as e:
            self.logger.error(f"❌ Error during code metadata extraction: {str(e)}")
            raise

    def _prepare_code_content(
        self,
        blocks: List[Block],
        summary_block: Block,
        context_length: int,
        block_groups: Optional[List[BlockGroup]] = None,
    ) -> str:
        """Render the parsed file as structured text for the LLM.

        Sends the parser's own view of the file rather than its raw source: the
        symbol table costs a fraction of the tokens and leaves room for the two
        things that actually answer the prompt -- the import statements and the
        unresolved cross-file edges, which are where external dependencies are
        visible. Comment and header spans are dropped; they carry no signal the
        docstrings do not already carry.

        *block_groups* matters for class-based files: CodeFileParser emits a
        top-level container as a BlockGroup, so its docstring and its
        INHERITS/IMPLEMENTS edges are not reachable from ``blocks`` alone.
        """
        MAX_TOKENS = int(context_length * CONTENT_TOKEN_RATIO)
        total_tokens = 0
        sections: List[str] = []

        def add(section: str) -> bool:
            """Append *section* if it fits. Returns False once the budget is spent."""
            nonlocal total_tokens
            increment = count_tokens(section)
            if total_tokens + increment > MAX_TOKENS:
                return False
            sections.append(section)
            total_tokens += increment
            return True

        summary_data = summary_block.data if isinstance(summary_block.data, dict) else {}
        overview = [f"## File\n{summary_data.get('text', '')}"]
        symbols = summary_data.get("symbols")
        if symbols:
            overview.append(f"Top-level symbols: {', '.join(symbols)}")
        add("\n".join(overview))

        raw_sections: List[str] = []
        symbol_sections: List[str] = []
        edges: dict[str, list[str]] = {}

        # Groups first so a container is listed above the members it owns.
        for block in [*(block_groups or ()), *blocks]:
            cm = block.code_metadata
            if cm is None:
                continue

            for edge in cm.pending_edges or ():
                relation, target = edge.get("relation"), edge.get("toName")
                if relation and target:
                    targets = edges.setdefault(relation, [])
                    if target not in targets:
                        targets.append(target)

            if block is summary_block:
                continue

            kind = cm.kind or "symbol"
            if kind in CODE_RAW_TEXT_KINDS:
                text = block.data.get("text", "") if isinstance(block.data, dict) else ""
                if text.strip():
                    raw_sections.append(
                        f"### {kind}\n{text[:MAX_RAW_TEXT_CHARS_PER_BLOCK]}"
                    )
                continue
            if kind in FILLER_KINDS:
                continue

            lines = [f"[{kind}] {cm.qualified_name or block.name or 'anonymous'}"]
            if cm.signature:
                lines.append(f"  Signature: {cm.signature}")
            if cm.docstring:
                lines.append(f"  Doc: {cm.docstring[:MAX_DOCSTRING_CHARS]}")
            if cm.decorators:
                lines.append(f"  Decorators: {', '.join(cm.decorators)}")
            symbol_sections.append("\n".join(lines))

        if raw_sections:
            add("## Imports and module-level code\n" + "\n\n".join(raw_sections))

        if edges:
            edge_lines = [
                f"{relation}: {', '.join(targets[:MAX_EDGE_TARGETS_PER_RELATION])}"
                for relation, targets in sorted(edges.items())
            ]
            add("## References to other files\n" + "\n".join(edge_lines))

        if symbol_sections:
            header_added = add("## Symbols")
            for section in symbol_sections if header_added else ():
                if not add(section):
                    self.logger.info(
                        "✂️ Code content exceeds %d tokens. Truncating symbol list.",
                        MAX_TOKENS,
                    )
                    break

        return "\n\n".join(sections)

    async def _code_fallback_summary(self, content: str) -> Optional[CodeClassification]:
        """Plain LLM call for a summary when structured code extraction fails.

        Only the rendered file content is resent -- forwarding the code prompt
        would carry its "return the JSON object only" instruction into a call
        that asks for prose.
        """
        try:
            fallback_prompt = [
                {
                    "type": "text",
                    "text": (
                        "Summarise the following source file for a developer searching a codebase. "
                        "Cover what it is responsible for, which systems it talks to, and its main "
                        "entry points, in 2 to 4 sentences. "
                        "Return only the summary text, nothing else."
                    ),
                },
                {"type": "text", "text": content},
            ]
            response = await self.llm.ainvoke([HumanMessage(content=fallback_prompt)])

            if hasattr(response, "content"):
                raw_content = response.content
            elif isinstance(response, str):
                raw_content = response
            else:
                raw_content = None

            summary_text = coerce_message_content_to_text(raw_content).strip()
            if not summary_text:
                self.logger.error("❌ Code fallback summary returned empty response")
                return None

            self.logger.info("✅ Code fallback summary obtained successfully")
            # Everything but the summary stays empty: a guessed role here is
            # indistinguishable downstream from one the model actually chose.
            return CodeClassification(
                architecture_role="Unknown",
                category="",
                subcategories=SubCategories(level1="", level2="", level3=""),
                topics=[],
                summary=summary_text,
            )
        except Exception as e:
            self.logger.error(f"❌ Code fallback summary call failed: {e}")
            return None



