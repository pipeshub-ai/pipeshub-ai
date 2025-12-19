import asyncio
import base64
from typing import Any, Dict

import fitz
from langchain.chat_models.base import BaseChatModel
from langchain_core.messages import HumanMessage

from app.config.constants.service import config_node_constants
from app.modules.parsers.pdf.ocr_handler import OCRStrategy
from app.utils.aimodels import get_generator_model


class VLMOCRStrategy(OCRStrategy):
    """OCR strategy that uses Vision Language Models to convert PDF pages to markdown"""

    # Concurrency limit for processing pages
    CONCURRENCY_LIMIT = 3

    # Number of retry attempts for page processing (excluding the initial attempt)
    MAX_RETRY_ATTEMPTS = 2

    # Default DPI for rendering pages
    RENDER_DPI = 200
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


    def _is_multimodal(self, config: Dict[str, Any]) -> bool:
        """
        Check if an LLM configuration supports multimodal capabilities

        Args:
            config: LLM configuration dictionary

        Returns:
            bool: True if the LLM supports multimodal
        """
        return (
            config.get("isMultimodal", False) or
            config.get("configuration", {}).get("isMultimodal", False)
        )
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
        Get a multimodal LLM, preferring the default LLM if it's multimodal,
        otherwise falling back to the first available multimodal LLM

        Returns:
            BaseChatModel: Multimodal LLM instance

        Raises:
            ValueError: If no multimodal LLM is found in configuration
        """
        self.logger.info("🔍 Getting multimodal LLM for VLM OCR")

        try:
            # Get AI models configuration
            ai_models = await self.config.get_config(
                config_node_constants.AI_MODELS.value,
                use_cache=False
            )
            llm_configs = ai_models.get("llm", [])

            if not llm_configs:
                raise ValueError("No LLM configurations found")

            # Find the best multimodal LLM in a single pass
            default_config = None
            first_multimodal_config = None

            for config in llm_configs:
                is_default = config.get("isDefault", False)
                is_multimodal = self._is_multimodal(config)

                # If we find a default multimodal LLM, use it immediately
                if is_default and is_multimodal:
                    self.logger.info(f"✅ Using default multimodal LLM: {config.get('provider')}")
                    llm = self._create_llm_from_config(config)
                    return llm

                # Track default LLM (even if not multimodal) for warning
                if is_default:
                    default_config = config

                # Track first multimodal LLM as fallback
                if is_multimodal and first_multimodal_config is None:
                    first_multimodal_config = config

            # If default exists but isn't multimodal, log warning
            if default_config:
                provider = default_config.get("provider", "unknown")
                model_string = default_config.get("configuration", {}).get("model", "unknown")
                self.logger.warning(
                    f"⚠️ Default LLM does not support multimodal capabilities. "
                    f"Provider: {provider}, Model: {model_string}. "
                    f"Using first available multimodal LLM..."
                )

            # Use first available multimodal LLM
            if first_multimodal_config:
                llm = self._create_llm_from_config(first_multimodal_config)
                return llm

            # No multimodal LLM found
            error_msg = (
                "❌ No multimodal LLM found in configuration. "
                "VLM OCR requires a multimodal LLM. Please configure at least one multimodal LLM."
            )
            self.logger.error(error_msg)
            raise ValueError(error_msg)

        except Exception as e:
            self.logger.error(f"❌ Error getting multimodal LLM: {str(e)}")
            raise ValueError(f"Failed to get multimodal LLM: {str(e)}")

    def _render_page_to_base64(self, page) -> str:
        """
        Render a PDF page as a PNG image and convert to base64

        Args:
            page: PyMuPDF page object

        Returns:
            str: Base64-encoded PNG image with data URI prefix
        """
        try:
            # Render page to pixmap at specified DPI
            mat = fitz.Matrix(self.RENDER_DPI / 72, self.RENDER_DPI / 72)
            pix = page.get_pixmap(matrix=mat)

            # Convert to PNG bytes
            img_bytes = pix.tobytes("png")

            # Encode to base64
            img_base64 = base64.b64encode(img_bytes).decode('utf-8')

            # Return with data URI prefix
            return f"data:image/png;base64,{img_base64}"

        except Exception as e:
            self.logger.error(f"❌ Error rendering page to base64: {str(e)}")
            raise

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

            # Extract content
            markdown_content = response.content if hasattr(response, 'content') else str(response)

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

    async def process_page(self, page) -> Dict[str, Any]:
        """
        Process a single PDF page with VLM OCR

        Args:
            page: PyMuPDF page object

        Returns:
            Dict containing page markdown and metadata
        """
        page_number = page.number + 1
        self.logger.info(f"📄 Processing page {page_number} with VLM OCR")

        try:
            # Render page to base64 image
            self.logger.debug(f"🖼️ Rendering page {page_number} to image")
            image_base64 = self._render_page_to_base64(page)
            # Call LLM to get markdown
            markdown = await self._call_llm_for_markdown(image_base64, page_number)

            return {
                "page_number": page_number,
                "markdown": markdown,
                "width": page.rect.width,
                "height": page.rect.height,
            }

        except Exception as e:
            self.logger.error(f"❌ Error processing page {page_number}: {str(e)}")
            # Re-raise the error instead of returning empty markdown
            raise

    async def _preprocess_document(self) -> Dict[str, Any]:
        """
        Process all pages concurrently with semaphore limiting

        Returns:
            Dict containing pages with markdown and metadata
        """
        self.logger.info(f"🚀 Processing {len(self.doc)} pages with VLM OCR (concurrency: {self.CONCURRENCY_LIMIT})")

        # Create semaphore for concurrency control
        semaphore = asyncio.Semaphore(self.CONCURRENCY_LIMIT)

        async def process_page_with_retry(page) -> Dict[str, Any]:
            """Process page with retry logic (3 total attempts)"""
            async with semaphore:
                last_error = None
                # Loop for initial attempt + MAX_RETRY_ATTEMPTS
                for attempt in range(self.MAX_RETRY_ATTEMPTS + 1):
                    try:
                        return await self.process_page(page)
                    except Exception as e:
                        last_error = e
                        if attempt < self.MAX_RETRY_ATTEMPTS:  # Not the last attempt
                            self.logger.warning(
                                f"⚠️ Retry {attempt + 1}/2 for page {page.number + 1}: {str(e)}"
                            )
                        else:  # Last attempt failed
                            self.logger.error(
                                f"❌ All retries failed for page {page.number + 1}"
                            )
                            raise last_error

        # Create tasks
        tasks = [asyncio.create_task(process_page_with_retry(page)) for page in self.doc]

        try:
            # Process all pages concurrently
            pages_results = await asyncio.gather(*tasks)
        except Exception:
            # Cancel all remaining tasks
            self.logger.error("❌ Cancelling all remaining tasks due to failure")
            for task in tasks:
                if not task.done():
                    task.cancel()
            # Wait for all tasks to complete cancellation
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        doc_markdown = "\n\n---\n\n".join([page["markdown"] for page in pages_results])
        # Build result structure
        result = {
            "pages": pages_results,
            "markdown": doc_markdown,
            "total_pages": len(self.doc),
        }

        self.logger.info(f"✅ Completed processing {len(self.doc)} pages")
        return result

    async def load_document(self, content: bytes) -> None:
        """
        Load PDF document and initialize LLM

        Args:
            content: PDF document content as bytes
        """
        self.logger.info("📥 Loading document for VLM OCR processing")

        try:
            # Load PDF with PyMuPDF
            self.logger.debug("📄 Loading PDF with PyMuPDF")
            self.doc = fitz.open(stream=content, filetype="pdf")
            self.logger.info(f"📚 Loaded PDF with {len(self.doc)} pages")

            # Get multimodal LLM (prefers default, falls back to first available)
            self.logger.debug("🤖 Getting multimodal LLM")
            self.llm = await self._get_multimodal_llm()

            # Process document
            self.logger.debug("⚙️ Processing document pages")
            self.document_analysis_result = await self._preprocess_document()

            self.logger.info("✅ Document loaded and processed successfully")

        except Exception as e:
            self.logger.error(f"❌ Error loading document: {str(e)}")
            raise

