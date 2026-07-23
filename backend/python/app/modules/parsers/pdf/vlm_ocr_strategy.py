import asyncio
import base64
import os
import tempfile
from io import BytesIO
from typing import Any, Dict, Optional

import pdfplumber
from langchain.chat_models.base import BaseChatModel
from langchain_core.messages import HumanMessage
from PIL import Image

from app.config.constants.service import config_node_constants
from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.modules.parsers.pdf.pdf_rasterizer import render_batch_from_path_sync
from app.modules.parsers.pdf.ocr_handler import OCRStrategy
from app.utils.aimodels import coerce_message_content_to_text, get_generator_model, is_multimodal_llm
from app.utils.llm import get_llm_for_role


class VLMOCRStrategy(OCRStrategy):
    """OCR strategy that uses Vision Language Models to convert PDF pages to markdown"""

    # Concurrency limit for processing pages
    CONCURRENCY_LIMIT = int(os.getenv('CONCURRENCY_LIMIT', '10'))

    # Number of retry attempts for page processing (excluding the initial attempt)
    MAX_RETRY_ATTEMPTS = 2

    # Default DPI for rendering pages (configurable via RENDER_DPI env var)
    RENDER_DPI = int(os.getenv('RENDER_DPI', '200'))

    # At 200 DPI each page is roughly 11 MB before PNG/base64 expansion.
    PAGE_RENDER_BATCH_SIZE = int(os.getenv('PAGE_RENDER_BATCH_SIZE', '5'))
    # Default prompt template
    DEFAULT_PROMPT = """# Role
You are a precise document OCR specialist. Convert the provided document image to clean, accurate markdown.

# Core Instructions
1. **Extract all visible text** exactly as written—preserve spelling, punctuation, capitalization, and numbers verbatim
2. **Maintain reading order**: top-to-bottom, left-to-right (or appropriate order for multi-column layouts)
3. **Preserve document hierarchy** using markdown: `#` for titles, `##` for sections, `###` for subsections

# Formatting Rules

## Text Styling
- **Bold** for text that appears bold/emphasized
- *Italic* for italicized text
- `code` for monospaced/typed text

## Lists
- Use `-` for unordered lists
- Use `1.` for numbered lists
- Preserve nested indentation

## Tables
- Convert all tables to markdown table format
- Use `|` separators and `---` header dividers
- Align columns appropriately

## Images & Visual Elements
- Include ALL images, photos, diagrams, charts, logos, and illustrations
- Use format: `{{IMAGE: description}}`
- Place image placeholders in their correct reading order position within the document
- Descriptions should be informative and detailed:
  - For charts/graphs: include type, axis labels, legend info, and key data points
  - For diagrams: describe structure, flow direction, and labeled components
  - For logos: include company/brand name if identifiable
  - For photos: describe subject, setting, and relevant details
  - For decorative images: brief description is sufficient
- If the entire page is a single image with no text: `{{IMAGE: comprehensive description}}`

### Image Examples
- Logo: `{{IMAGE: Acme Corp logo - red triangle with company name below}}`
- Chart: `{{IMAGE: Bar chart showing Q1-Q4 revenue on x-axis, dollars in millions on y-axis, values ranging from $10M to $45M}}`
- Photo: `{{IMAGE: Team of five people gathered around conference table in modern office}}`
- Diagram: `{{IMAGE: Flowchart with 5 boxes connected by arrows showing approval process: Submit → Review → Approve → Implement → Complete}}`

## Special Elements
- Checkboxes: `[ ]` (unchecked) or `[x]` (checked)
- Preserve line breaks where semantically meaningful
- Represent horizontal rules as `---`

# Output
Return ONLY the extracted markdown. No preamble, no explanations, no commentary."""

    def __init__(self, logger, config) -> None:
        """
        Initialize VLM OCR strategy

        Args:
            logger: Logger instance
            config: ConfigurationService instance
        """
        super().__init__(logger)
        self.config = config
        self.doc = None
        self.llm = None
        self.llm_config = None
        self.document_analysis_result = None
        self._pdf_path = None
        self._page_images: Dict[int, str] = {}

    def _create_llm_from_config(self, config: Dict[str, Any]) -> BaseChatModel:
        """Helper to create an LLM instance from a configuration dictionary."""
        self.llm_config = config
        provider = config["provider"]
        model_string = config.get("configuration", {}).get("model")
        if model_string:
            model_names = [name.strip() for name in model_string.split(",") if name.strip()]
            model_name = model_names[0] if model_names else None
        else:
            model_name = None
        return get_generator_model(provider, config, model_name)

    async def _get_multimodal_llm(self) -> BaseChatModel:
        """
        Get a multimodal LLM for VLM OCR.

        Selection priority:
        1. Indexing role assignment, if the assigned model is multimodal.
        2. Default LLM, if it is multimodal.
        3. First available multimodal LLM.

        Returns:
            BaseChatModel: Multimodal LLM instance

        Raises:
            ValueError: If no multimodal LLM is found in configuration
        """
        self.logger.info("🔍 Getting multimodal LLM for VLM OCR")

        try:
            # 1. Check indexing role assignment first — use it if multimodal
            try:
                _, role_config = await get_llm_for_role(self.config, "indexing")
                if is_multimodal_llm(role_config):
                    self.logger.info(
                        f"✅ Using indexing-role LLM for VLM OCR: {role_config.get('provider')}"
                    )
                    return self._create_llm_from_config(role_config)
            except Exception:
                pass

            # 2. Scan all LLM configs for the best multimodal candidate
            ai_models = await self.config.get_config(
                config_node_constants.AI_MODELS.value,
                use_cache=False
            )
            llm_configs = ai_models.get("llm", [])

            if not llm_configs:
                raise DocumentProcessingError(
                    "No LLM configurations found",
                    details={"required": "multimodal LLM"},
                )

            default_config = None
            first_multimodal_config = None

            for config in llm_configs:
                is_default = config.get("isDefault", False)
                is_multimodal = is_multimodal_llm(config)

                if is_default and is_multimodal:
                    self.logger.info(f"✅ Using default multimodal LLM: {config.get('provider')}")
                    return self._create_llm_from_config(config)

                if is_default:
                    default_config = config

                if is_multimodal and first_multimodal_config is None:
                    first_multimodal_config = config

            if default_config:
                provider = default_config.get("provider", "unknown")
                model_string = default_config.get("configuration", {}).get("model", "unknown")
                self.logger.warning(
                    f"⚠️ Default LLM does not support multimodal capabilities. "
                    f"Provider: {provider}, Model: {model_string}. "
                    f"Using first available multimodal LLM..."
                )

            if first_multimodal_config:
                return self._create_llm_from_config(first_multimodal_config)

            error_msg = (
                "❌ No multimodal LLM found in configuration. "
                "VLM OCR requires a multimodal LLM. Please configure at least one multimodal LLM."
            )
            self.logger.error(error_msg)
            raise DocumentProcessingError(
                error_msg,
                details={"required": "multimodal LLM"},
            )

        except DocumentProcessingError:
            raise
        except Exception as e:
            self.logger.error(f"❌ Error getting multimodal LLM: {str(e)}")
            raise DocumentProcessingError(
                f"Failed to get multimodal LLM: {str(e)}",
                details={"error": str(e)},
            ) from e

    def _render_page_batch_to_base64(self, page_numbers: list[int]) -> Dict[int, str]:
        """Render a batch of pages via pdfplumber in an isolated worker process.

        Only the requested *page_numbers* are rasterised in one pool submission,
        keeping per-worker memory proportional to the batch size rather than the
        full document.
        """
        if not self._pdf_path:
            raise RuntimeError(
                "PDF source path not initialized; load_document must run first"
            )
        rendered_pages = render_batch_from_path_sync(
            self._pdf_path,
            page_numbers,
            self.RENDER_DPI,
        )
        page_images: Dict[int, str] = {}
        for page_number, (image_array, _) in rendered_pages.items():
            buf = BytesIO()
            Image.fromarray(image_array).save(buf, format="PNG")
            img_base64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            page_images[page_number] = f"data:image/png;base64,{img_base64}"
        return page_images

    async def _preload_page_images(self) -> int:
        """Rasterise pages in batches to avoid OOM in the process-pool worker.

        Returns the number of pages successfully rasterised.  A batch failure
        is logged and skipped rather than aborting the entire document — the
        successfully rendered pages can still be processed by the LLM, and
        ``process_page`` will emit an empty result for missing pages instead of
        raising ``KeyError``.
        """
        total = len(self.doc.pages)
        all_page_numbers = list(range(1, total + 1))
        batch_size = self.PAGE_RENDER_BATCH_SIZE

        for start in range(0, len(all_page_numbers), batch_size):
            batch = all_page_numbers[start : start + batch_size]
            self.logger.debug(
                "Rendering page batch %d–%d of %d", batch[0], batch[-1], total
            )
            try:
                batch_images = await asyncio.to_thread(
                    self._render_page_batch_to_base64, batch
                )
                self._page_images.update(batch_images)
            except Exception:
                self.logger.exception(
                    "Failed to rasterise page batch %d–%d; those pages will "
                    "be skipped during OCR processing",
                    batch[0], batch[-1],
                )

        return len(self._page_images)

    _coerce_content_to_text = staticmethod(coerce_message_content_to_text)

    async def _call_llm_for_markdown(self, image_base64: str, page_number: int) -> str:
        """
        Call LLM with page image to get markdown output

        Args:
            image_base64: Base64-encoded image with data URI prefix
            page_number: Page number for the prompt

        Returns:
            str: Markdown content from LLM
        """
        try:
            # Format prompt with page number
            prompt = self.DEFAULT_PROMPT

            # Create multimodal message
            message = HumanMessage(
                content=[
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_base64}},
                ]
            )

            # Call LLM
            self.logger.debug(f"📤 Calling LLM for page {page_number}")
            response = await self.llm.ainvoke([message])

            # Extract content. LangChain message content may be a plain string or
            # a list of content blocks (e.g. Gemini returns the latter), so coerce
            # to text before any string handling.
            raw_content = getattr(response, "content", response)
            markdown_content = self._coerce_content_to_text(raw_content)

            # Clean up: Remove markdown code block wrapper if present
            markdown_content = markdown_content.strip()
            if markdown_content.startswith("```markdown"):
                markdown_content = markdown_content[len("```markdown"):].strip()
                if markdown_content.endswith("```"):
                    markdown_content = markdown_content[:-3].strip()
            elif markdown_content.startswith("```"):
                # Handle generic code block wrapper
                markdown_content = markdown_content[3:].strip()
                if markdown_content.endswith("```"):
                    markdown_content = markdown_content[:-3].strip()

            self.logger.debug(f"✅ Received markdown for page {page_number} ({len(markdown_content)} chars)")
            return markdown_content

        except Exception as e:
            self.logger.error(f"❌ Error calling LLM for page {page_number}: {str(e)}")
            raise

    async def process_page(self, page, page_number: Optional[int] = None) -> Dict[str, Any]:
        """Process a single PDF page with VLM OCR.

        Returns an empty-markdown result instead of raising when the
        pre-rendered image is unavailable (e.g. because the rasterisation
        batch for this page failed due to OOM).
        """
        if page_number is None:
            page_number = page.page_number
        self.logger.info(f"📄 Processing page {page_number} with VLM OCR")

        image_base64 = self._page_images.get(page_number)
        if image_base64 is None:
            self.logger.warning(
                "Skipping page %d: no pre-rendered image available", page_number
            )
            return {
                "page_number": page_number,
                "markdown": "",
                "width": page.width,
                "height": page.height,
            }

        try:
            markdown = await self._call_llm_for_markdown(image_base64, page_number)
            return {
                "page_number": page_number,
                "markdown": markdown,
                "width": page.width,
                "height": page.height,
            }
        except Exception as e:
            self.logger.error(f"❌ Error processing page {page_number}: {str(e)}")
            raise

    async def _preprocess_document(self) -> Dict[str, Any]:
        """
        Process all pages concurrently with semaphore limiting

        Returns:
            Dict containing pages with markdown and metadata
        """
        pages = self.doc.pages
        self.logger.info(f"🚀 Processing {len(pages)} pages with VLM OCR (concurrency: {self.CONCURRENCY_LIMIT})")

        rendered_count = await self._preload_page_images()
        if rendered_count == 0:
            raise DocumentProcessingError(
                "Failed to rasterise any pages for VLM OCR",
                details={"total_pages": len(pages)},
            )

        semaphore = asyncio.Semaphore(self.CONCURRENCY_LIMIT)

        async def process_page_with_retry(page, page_number: int) -> Dict[str, Any]:
            """Process page with retry logic (3 total attempts)"""
            async with semaphore:
                last_error = None
                for attempt in range(self.MAX_RETRY_ATTEMPTS + 1):
                    try:
                        return await self.process_page(page, page_number)
                    except Exception as e:
                        last_error = e
                        if attempt < self.MAX_RETRY_ATTEMPTS:
                            self.logger.warning(
                                f"⚠️ Retry {attempt + 1}/2 for page {page_number}: {str(e)}"
                            )
                        else:
                            self.logger.error(
                                f"❌ All retries failed for page {page_number}"
                            )
                            raise last_error

        tasks = [
            asyncio.create_task(process_page_with_retry(page, page_num + 1))
            for page_num, page in enumerate(pages)
        ]

        try:
            pages_results = await asyncio.gather(*tasks)
        except Exception:
            self.logger.error("❌ Cancelling all remaining tasks due to failure")
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        doc_markdown = "\n\n---\n\n".join([page["markdown"] for page in pages_results])
        result = {
            "pages": pages_results,
            "markdown": doc_markdown,
            "total_pages": len(pages),
        }

        self.logger.info(f"✅ Completed processing {len(pages)} pages")
        return result

    async def load_document(self, content: bytes) -> None:
        """
        Load PDF document and initialize LLM

        Args:
            content: PDF document content as bytes
        """
        self.logger.info("📥 Loading document for VLM OCR processing")

        try:
            self.logger.debug("📄 Loading PDF with pdfplumber")
            tmp = tempfile.NamedTemporaryFile(
                delete=False, suffix=".pdf", prefix="pipeshub_vlm_"
            )
            try:
                tmp.write(content)
                tmp.flush()
            finally:
                tmp.close()
            self._pdf_path = tmp.name
            self.doc = pdfplumber.open(self._pdf_path)
            self.logger.info(f"📚 Loaded PDF with {len(self.doc.pages)} pages")

            self.logger.debug("🤖 Getting multimodal LLM")
            self.llm = await self._get_multimodal_llm()

            self.logger.debug("⚙️ Processing document pages")
            self.document_analysis_result = await self._preprocess_document()

            self.logger.info("✅ Document loaded and processed successfully")
        except Exception as e:
            self.logger.error(f"❌ Error loading document: {str(e)}")
            raise
        finally:
            if self.doc:
                self.doc.close()
                self.doc = None
            if self._pdf_path:
                try:
                    os.unlink(self._pdf_path)
                except OSError:
                    pass
                self._pdf_path = None
            self._page_images = {}

