import asyncio
import base64
import contextlib
import logging
import os
import re
import tempfile
import threading
from io import BytesIO
from typing import TYPE_CHECKING, Any, Dict, Optional, cast

import pdfplumber
from langchain.chat_models.base import BaseChatModel
from langchain_core.messages import HumanMessage
from PIL import Image

from app.core.org_context import spawn_in_request
from app.exceptions.indexing_exceptions import (
    AISettingsUnavailableError,
    DocumentProcessingError,
    MultimodalModelNotConfiguredError,
)
from app.exceptions.non_retryable import NonRetryableError
from app.modules.parsers.pdf.ocr_handler import OCRStrategy
from app.modules.parsers.pdf.pdf_rasterizer import render_batch_from_path_sync
from app.services.llm_gateway.gateway import get_llm_gateway, provider_key
from app.utils.aimodels import (
    LLMProvider,
    coerce_message_content_to_text,
    get_generator_model,
    is_multimodal_llm,
)
from app.utils.env_config import env_int
from app.utils.llm import LLMUnavailableError, get_llm_for_role, load_ai_models
from app.utils.opik import get_llm_invoke_config

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

# Every page is one model call, so a longer scan is refused rather than read at any cost.
MAX_PAGES_ENV = "VLM_OCR_MAX_PAGES"
DEFAULT_MAX_PAGES = 500


def vlm_ocr_max_pages() -> int:
    return max(1, env_int(MAX_PAGES_ENV, DEFAULT_MAX_PAGES))



def _without_frames(error: BaseException) -> BaseException:
    """Drop the tracebacks of *error* and of every exception chained to it.

    A traceback keeps the frames of the failed call alive, and those frames hold
    the page image; ``with_traceback(None)`` alone leaves ``__cause__`` and
    ``__context__`` holding theirs.
    """
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        current.__traceback__ = None
        pending.extend(e for e in (current.__cause__, current.__context__) if e is not None)
    return error


class _RenderSink:
    """Where a pool thread hands over rendered pages.

    The thread keeps its job and arguments until it next runs, which can be after
    the awaiting coroutine has moved on or been cancelled; a render can even finish
    after that. Images passed back as a result or kept in a plain dict would stay
    alive there. Once closed, the sink holds nothing and takes nothing.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pages: dict[int, str] = {}
        self._closed = False

    def put(self, pages: dict[int, str]) -> None:
        with self._lock:
            if not self._closed:
                self._pages.update(pages)

    def close(self) -> dict[int, str]:
        with self._lock:
            self._closed = True
            pages, self._pages = self._pages, {}
        return pages

class VLMOCRStrategy(OCRStrategy):
    """OCR strategy that uses Vision Language Models to convert PDF pages to markdown"""

    # Concurrency limit for processing pages
    CONCURRENCY_LIMIT = int(os.getenv('CONCURRENCY_LIMIT', '10'))

    # Number of retry attempts for page processing (excluding the initial attempt)
    MAX_RETRY_ATTEMPTS = 2

    # Default DPI for rendering pages (configurable via RENDER_DPI env var)
    RENDER_DPI = int(os.getenv('RENDER_DPI', '200'))

    # Pages rendered per process-pool submission to cap worker memory usage.
    # At 200 DPI each page is ~11 MB as a numpy array; 20 pages ≈ 220 MB peak.
    PAGE_RENDER_BATCH_SIZE = int(os.getenv('PAGE_RENDER_BATCH_SIZE', '20'))
    MAX_OUTPUT_TOKENS = 4096
    _MULTIPLICATION_ASTERISK = re.compile(
        r"(?<![0-9A-Za-z])(?P<left>\d[\dA-Za-z]*)\s*\*\s*(?=\d)"
    )
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

    def __init__(self, logger: logging.Logger, config: "ConfigurationService") -> None:
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
            MultimodalModelNotConfiguredError: If no multimodal LLM is configured
            DocumentProcessingError: If the configuration could not be read
        """
        self.logger.info("🔍 Getting multimodal LLM for VLM OCR")

        try:
            # 1. Check indexing role assignment first — use it if multimodal
            try:
                _, role_config = await get_llm_for_role(self.config, "indexing", reasoning_effort="low")
                if is_multimodal_llm(role_config):
                    self.logger.info(
                        f"✅ Using indexing-role LLM for VLM OCR: {role_config.get('provider')}"
                    )
                    return self._create_llm_from_config(role_config)
            except Exception:
                pass

            # 2. Scan all LLM configs for the best multimodal candidate
            # `{}` until an admin saves AI settings. A store that cannot be read
            # raises instead: an outage, retried, never "no model is set up".
            try:
                ai_models = await load_ai_models(self.config, raise_on_error=True)
            except Exception as e:
                raise AISettingsUnavailableError(
                    "Could not read the workspace's AI settings",
                    details={"error": str(e)},
                ) from e
            raw_configs = ai_models.get("llm")
            llm_configs: list[dict[str, Any]] = (
                [cast("dict[str, Any]", c) for c in cast("list[object]", raw_configs) if isinstance(c, dict)]
                if isinstance(raw_configs, list)
                else []
            )

            if not llm_configs:
                # The settings were read and hold no model at all (a new org):
                # the same fact as holding no multimodal one.
                raise MultimodalModelNotConfiguredError(
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
            raise MultimodalModelNotConfiguredError(
                error_msg,
                details={"required": "multimodal LLM"},
            )

        except (DocumentProcessingError, AISettingsUnavailableError):
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

    async def _preload_page_images(self, page_numbers: Optional[list[int]] = None) -> None:
        """Rasterise pages in batches to avoid OOM in the process-pool worker.

        With *page_numbers* only those pages are rendered and added to
        ``_page_images``; without it every page is rendered and kept.
        """
        total = len(self.doc.pages)
        wanted = page_numbers if page_numbers is not None else list(range(1, total + 1))
        batch_size = max(1, self.PAGE_RENDER_BATCH_SIZE)

        for start in range(0, len(wanted), batch_size):
            batch = wanted[start : start + batch_size]
            self.logger.debug(
                "Rendering page batch %d–%d of %d", batch[0], batch[-1], total
            )
            sink = _RenderSink()
            try:
                await asyncio.to_thread(self._render_into, batch, sink)
                self._page_images.update(sink.close())
            finally:
                sink.close()

    def _render_into(self, page_numbers: list[int], sink: "_RenderSink") -> None:
        sink.put(self._render_page_batch_to_base64(page_numbers))

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
            invoke_kwargs: dict[str, Any] = {}
            if (
                self.llm_config
                and self.llm_config.get("provider") == LLMProvider.OPENAI_COMPATIBLE.value
            ):
                invoke_kwargs = {
                    "max_tokens": self.MAX_OUTPUT_TOKENS,
                    "temperature": 0,
                }
                model_name = str(
                    self.llm_config.get("configuration", {}).get("model", "")
                ).lower()
                if "qwen" in model_name:
                    invoke_kwargs["extra_body"] = {
                        "chat_template_kwargs": {"enable_thinking": False},
                    }

            llm = self.llm
            if llm is None:
                raise DocumentProcessingError("VLM OCR has no model loaded")
            response = await get_llm_gateway().invoke(
                llm,
                [message],
                provider=provider_key(llm),
                call_site="vlm_ocr",
                config=get_llm_invoke_config(llm),
                **invoke_kwargs,
            )

            response_metadata = getattr(response, "response_metadata", {}) or {}
            if response_metadata.get("finish_reason") == "length":
                raise DocumentProcessingError(
                    f"VLM OCR output was truncated for page {page_number}"
                )

            # Extract content. LangChain message content may be a plain string or
            # a list of content blocks (e.g. Gemini returns the latter), so coerce
            # to text before any string handling.
            raw_content = getattr(response, "content", response)
            markdown_content = self._coerce_content_to_text(raw_content)
            markdown_content = self._MULTIPLICATION_ASTERISK.sub(
                r"\g<left>×", markdown_content
            )

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
        """
        Process a single PDF page with VLM OCR

        Args:
            page: pdfplumber page object
            page_number: 1-based page number (derived from page if not provided)

        Returns:
            Dict containing page markdown and metadata
        """
        if page_number is None:
            page_number = page.page_number
        self.logger.info(f"📄 Processing page {page_number} with VLM OCR")

        try:
            image_base64 = self._page_images.get(page_number)
            if image_base64 is None:
                raise KeyError(f"No pre-rendered image for page {page_number}")
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

        semaphore = asyncio.Semaphore(self.CONCURRENCY_LIMIT)
        # Pages that failed after every retry were indexed empty. Only a count and the
        # last error, without its frames, are kept, so failed pages keep no images.
        unreadable_count = 0
        last_unreadable: Exception | None = None

        async def process_page_with_retry(page: object, page_number: int) -> Dict[str, Any]:
            """Process page with retry logic (3 total attempts)"""
            nonlocal unreadable_count, last_unreadable
            async with semaphore:
                last_error = None
                for attempt in range(self.MAX_RETRY_ATTEMPTS + 1):
                    try:
                        result = await self.process_page(page, page_number)
                        # The page is done: free its image now, not when the batch ends.
                        self._page_images.pop(page_number, None)
                        return result
                    except (NonRetryableError, LLMUnavailableError):
                        # A provider that is down, or a call that must not be retried: blank pages
                        # would index an empty document, so the record fails as a whole.
                        raise
                    except Exception as e:
                        last_error = e
                        if attempt < self.MAX_RETRY_ATTEMPTS:
                            self.logger.warning(
                                f"⚠️ Retry {attempt + 1}/2 for page {page_number}: {str(e)}"
                            )
                        else:
                            # One page the model cannot read must not cost the document.
                            self.logger.warning(
                                "Page %s could not be read after %d attempts (%s); indexing it empty",
                                page_number, attempt + 1, last_error,
                            )
                            unreadable_count += 1
                            _without_frames(e)
                            last_unreadable = e
                            return {
                                "page_number": page_number,
                                "markdown": "",
                                "width": getattr(page, "width", None),
                                "height": getattr(page, "height", None),
                            }
                raise AssertionError("unreachable: the last attempt returns")

        # Rolling window: at most `window` rendered page images are alive at once
        # (PAGE_RENDER_BATCH_SIZE). A producer renders pages in order as window slots
        # free up; CONCURRENCY_LIMIT workers take pages as soon as they are rendered, so
        # a slow page never holds up the others. A slot is freed when the page's image is
        # dropped (its call succeeded or it was given up).
        window = max(1, self.PAGE_RENDER_BATCH_SIZE)
        render_chunk = max(1, window // 4)
        slots = asyncio.Semaphore(window)
        ready: asyncio.Queue[Optional[int]] = asyncio.Queue()
        n_workers = max(1, min(self.CONCURRENCY_LIMIT, len(pages)))
        results: dict[int, dict[str, Any]] = {}

        async def produce() -> None:
            next_page = 1
            total = len(pages)
            while next_page <= total:
                want = min(render_chunk, total - next_page + 1)
                for _ in range(want):
                    await slots.acquire()
                numbers = list(range(next_page, next_page + want))
                next_page += want
                await self._preload_page_images(numbers)
                for number in numbers:
                    ready.put_nowait(number)
            for _ in range(n_workers):
                ready.put_nowait(None)

        async def work() -> None:
            while (number := await ready.get()) is not None:
                try:
                    results[number] = await process_page_with_retry(pages[number - 1], number)
                finally:
                    self._page_images.pop(number, None)
                    slots.release()

        tasks = [spawn_in_request(produce())]
        tasks += [spawn_in_request(work()) for _ in range(n_workers)]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            self.logger.error("❌ Cancelling all remaining tasks due to failure")
            for task in tasks:
                if not task.done():
                    task.cancel()
            # Each stopped worker keeps its error, and that error's frames hold the
            # image of the page it was reading.
            for outcome in await asyncio.gather(*tasks, return_exceptions=True):
                if isinstance(outcome, BaseException):
                    _without_frames(outcome)
            raise
        finally:
            del tasks
            self._page_images = {}
        pages_results = [results[n] for n in range(1, len(pages) + 1)]

        if pages and last_unreadable is not None and unreadable_count == len(pages):
            # Every page failing alike (no rendered images, output always truncated) is
            # systemic: an empty document would be indexed as a success. Fail the record.
            self.logger.error("❌ VLM OCR could not read any of the %d pages", len(pages))
            raise last_unreadable

        doc_markdown = "\n\n---\n\n".join([page["markdown"] for page in pages_results])
        result = {
            "pages": pages_results,
            "markdown": doc_markdown,
            "total_pages": len(pages),
        }

        self.logger.info(f"✅ Completed processing {len(pages)} pages")
        return result

    @staticmethod
    def _open_pdf_sync(content: bytes) -> "tuple[str, Any, int]":
        """Write *content* to a temp file and open it. Returns (path, doc, page count).

        Cleans up after itself when it fails, so a caller only owns what it gets back.
        """
        tmp = tempfile.NamedTemporaryFile(
            delete=False, suffix=".pdf", prefix="pipeshub_vlm_"
        )
        doc = None
        try:
            try:
                tmp.write(content)
                tmp.flush()
            finally:
                tmp.close()
            doc = pdfplumber.open(tmp.name)
            return tmp.name, doc, len(doc.pages)
        except BaseException:
            if doc is not None:
                doc.close()
            with contextlib.suppress(OSError):
                os.unlink(tmp.name)
            raise

    @staticmethod
    def _release_pdf_sync(path: Optional[str], doc: object) -> None:
        if doc is not None:
            with contextlib.suppress(Exception):
                doc.close()
        if path:
            with contextlib.suppress(OSError):
                os.unlink(path)

    async def _open_pdf_off_loop(self, content: bytes) -> "tuple[str, Any, int]":
        """Run ``_open_pdf_sync`` in a thread without leaking its file on cancellation.

        The side that finishes second releases the file: the thread when the caller has
        already given up, the caller when the thread handed it over first. Neither relies
        on a loop callback, so the file is released even if the loop closes mid-open.
        """
        lock = threading.Lock()
        abandoned = False
        handed_over: "Optional[tuple[str, Any, int]]" = None

        def open_and_hand_over() -> "Optional[tuple[str, Any, int]]":
            nonlocal handed_over
            opened = self._open_pdf_sync(content)
            with lock:
                if not abandoned:
                    handed_over = opened
                    return opened
            self._release_pdf_sync(opened[0], opened[1])
            return None

        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(None, open_and_hand_over)
        try:
            opened = await asyncio.shield(fut)
        except asyncio.CancelledError:
            with lock:
                abandoned = True
                orphan, handed_over = handed_over, None
            if orphan is not None:
                # Closing and unlinking can be slow; keep them off the loop.
                try:
                    loop.run_in_executor(None, self._release_pdf_sync, orphan[0], orphan[1])
                except RuntimeError:  # the executor is already shut down
                    self._release_pdf_sync(orphan[0], orphan[1])
            raise
        return cast("tuple[str, Any, int]", opened)

    async def load_document(self, content: bytes) -> None:
        """
        Load PDF document and initialize LLM

        Args:
            content: PDF document content as bytes
        """
        self.logger.info("📥 Loading document for VLM OCR processing")

        try:
            self.logger.debug("📄 Loading PDF with pdfplumber")
            # Writing the file and parsing its page tree is blocking work that grows
            # with the PDF; scanned and damaged files make it the slowest part.
            self._pdf_path, self.doc, page_count = await self._open_pdf_off_loop(content)
            self.logger.info(f"📚 Loaded PDF with {page_count} pages")
            max_pages = vlm_ocr_max_pages()
            if page_count > max_pages:
                raise DocumentProcessingError(
                    f"The file is too large for OCR: {page_count} pages, more than the "
                    f"{max_pages} read with OCR ({MAX_PAGES_ENV})"
                )

            self.logger.debug("🤖 Getting multimodal LLM")
            self.llm = await self._get_multimodal_llm()

            self.logger.debug("⚙️ Processing document pages")
            self.document_analysis_result = await self._preprocess_document()

            self.logger.info("✅ Document loaded and processed successfully")
        except Exception as e:
            self.logger.error(f"❌ Error loading document: {str(e)}")
            raise
        finally:
            doc, path = self.doc, self._pdf_path
            self.doc = None
            self._pdf_path = None
            if doc is not None or path:
                await asyncio.shield(
                    asyncio.get_running_loop().run_in_executor(
                        None, self._release_pdf_sync, path, doc
                    )
                )
