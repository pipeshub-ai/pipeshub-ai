import asyncio
import base64
import logging
from http import HTTPStatus
from typing import Any
from urllib.parse import unquote, urlparse

from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.modules.parsers.image_parser import svg_core
from app.utils.image_utils import (
    INDEX_RECORD_IMAGE_BYTES,
    fit_image_for_index,
    get_extension_from_mimetype,
)
from app.utils.public_http import (
    PublicFetchError,
    PublicFetchLimits,
    PublicUrlFetcher,
    UnsafeUrlError,
)
from app.utils.url_redaction import redact_url
from app.services.parsing.interface import ParseResult

try:
    from cairosvg import svg2png
except Exception:
    svg2png = None

from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat

VALID_IMAGE_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.webp','.svg']
# Image URLs come from document bodies (uploads, synced mail, crawled pages), so a
# document's author chooses them: they must never reach internal addresses.
# A larger image is dropped rather than buffered whole: a document's image URL can
# serve any amount. downscale_to_limits fits accepted images to each model.
_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_IMAGE_FETCH_LIMITS = PublicFetchLimits(max_bytes=_MAX_IMAGE_BYTES, timeout_s=10.0)
_MAX_PARALLEL_IMAGE_FETCHES = 4
_logger = logging.getLogger(__name__)


class ImageParser:
    def __init__(self, logger) -> None:
        self.logger = logger

    async def parse(
        self,
        content: bytes,
        record_name: str,
        config: dict[str, Any] | None = None,
    ) -> ParseResult:
        extension = config.get("extension")
        # SVG->PNG conversion (cairosvg) is synchronous and can be slow for
        # complex vector graphics; keep it off the event loop.
        block_container = await asyncio.to_thread(self.parse_image, content, extension)
        return ParseResult(
            block_container=block_container,
            metadata={"record_name": record_name},
        )

    def parse_image(self, image_content: bytes, extension: str) -> BlocksContainer:
        base64_encoded_content = base64.b64encode(image_content).decode("utf-8")

        # Handle SVG images by converting to PNG
        if extension.lower() == 'svg':
            self.logger.debug("Detected SVG image; converting to PNG")
            try:
                png_base64 = self.svg_base64_to_png_base64(base64_encoded_content)
                base64_image = f"data:image/png;base64,{png_base64}"
            except Exception as e:
                self.logger.warning(f"Failed to convert SVG to PNG: {e}")
                raise DocumentProcessingError(
                    f"Failed to convert SVG to PNG: {e}",
                    details={"error": str(e)},
                ) from e
        else:
            base64_image = f"data:image/{extension};base64,{base64_encoded_content}"

        self.logger.debug(f"Base64 image: {base64_image[:100]}")

        image_block = Block(
            index=0,
            type=BlockType.IMAGE.value,
            format=DataFormat.BASE64.value,
            data={"uri": base64_image},
        )
        return BlocksContainer(blocks=[image_block], block_groups=[])

    @staticmethod
    def _is_valid_image_url(url: str) -> bool:
        """
        Validate if URL appears to be an image URL by checking:
        1. File extension in the URL path
        2. URL format (http/https)
        """
        if not url or not isinstance(url, str):
            return False

        # Must be HTTP or HTTPS URL
        if not url.startswith('http://') and not url.startswith('https://'):
            return False

        try:
            parsed = urlparse(url)
            path = unquote(parsed.path.lower())

            # Valid image extensions


            # Check if URL path ends with a valid image extension
            if any(path.endswith(ext) for ext in VALID_IMAGE_EXTENSIONS):
                return True

        except Exception:
            pass

        # If we can't determine from URL, allow it but will check content-type later
        return True

    @staticmethod
    def _is_valid_image_content_type(content_type: str) -> bool:
        """
        Validate content type and return True for valid image types.
        """
        if not content_type:
            return False

        content_type = content_type.lower().split(';')[0].strip()

        # Must be an image content type
        if not content_type.startswith('image/'):
            return False

        return True

    @staticmethod
    def _log_http_error(log: logging.Logger, url: str, status: int) -> None:
        if status == HTTPStatus.FORBIDDEN:
            if 'X-Amz-Expires' in url:
                log.warning(
                    f"⚠️ Access denied (403) for signed URL - likely expired or invalid signature: {redact_url(url)}"
                )
            else:
                log.warning(f"⚠️ Access denied (403) for URL - insufficient permissions: {redact_url(url)}")
        elif status == HTTPStatus.NOT_FOUND:
            log.warning(f"⚠️ Image not found (404) at URL: {redact_url(url)}")
        elif status >= HTTPStatus.INTERNAL_SERVER_ERROR:
            log.warning(f"⚠️ Server error ({status}) when fetching URL: {redact_url(url)}")
        else:
            log.warning(f"⚠️ HTTP error ({status}) when fetching URL: {redact_url(url)}")

    @staticmethod
    async def _fetch_single_url(
        fetcher: PublicUrlFetcher,
        url: str,
        logger: logging.Logger | None = None,
    ) -> str | None:
        log = logger or _logger
        try:

            # Check if already a base64 data URL
            if url.startswith('data:image/'):
                # Skip SVG images - check the MIME type in the data URL
                if url.startswith('data:image/svg+xml'):
                    log.debug("Data URL is SVG; converting SVG base64 to PNG base64")
                    return "data:image/png;base64," + await ImageParser.svg_base64_to_png_base64_async(url)

                log.debug("URL is already base64 encoded")
                return url

            # Validate URL format before attempting to fetch
            if not ImageParser._is_valid_image_url(url):
                log.warning(f"⚠️ URL does not appear to be an image URL: {redact_url(url)}")
                return None

            try:
                response = await fetcher.get(url, _IMAGE_FETCH_LIMITS)
            except UnsafeUrlError as e:
                log.warning(f"⚠️ Skipping image on a non-public address: {redact_url(url)} ({e})")
                return None
            except PublicFetchError as e:
                log.warning(f"⚠️ Network error when fetching URL: {redact_url(url)} (Error: {e})")
                return None

            if not HTTPStatus.OK <= response.status_code < HTTPStatus.MULTIPLE_CHOICES:
                ImageParser._log_http_error(log, url, response.status_code)
                return None

            get_content_type_header = response.headers.get('content-type', '').lower()
            get_content_type = get_content_type_header.split(';')[0].strip()
            is_valid = ImageParser._is_valid_image_content_type(get_content_type)
            log.debug(f"GET content-type for URL {redact_url(url)} => {get_content_type}")

            if not is_valid:
                log.info(f"⚠️ Content-type invalid during GET: {get_content_type} from URL: {redact_url(url)}")
                return None

            extension = get_extension_from_mimetype(get_content_type)
            if not extension:
                log.info(f"⚠️ Extension couldn't be determined for URL: {redact_url(url)}; skipping image")
                return None

            if f".{extension}" not in VALID_IMAGE_EXTENSIONS:
                log.info(f"⚠️ Extension {extension} not in valid image extensions, from URL: {redact_url(url)}; skipping image")
                return None

            content = response.content
            if not content:
                log.info(f"⚠️ Empty content received from URL: {redact_url(url)}")
                return None

            base64_encoded = base64.b64encode(content).decode('utf-8')
            if 'svg' in extension:
                log.debug("Detected SVG extension from GET; converting SVG base64 to PNG base64")
                return "data:image/png;base64," + await ImageParser.svg_base64_to_png_base64_async(base64_encoded)

            log.debug(f"Converted URL to base64 for {extension}: {redact_url(url)}")
            return f"data:image/{extension};base64,{base64_encoded}"

        except Exception as e:
            log.error(f"⚠️ Failed to convert URL to base64: {redact_url(url)}, error: {str(e)}")
            return None

    @staticmethod
    async def urls_to_base64(
        urls: list[str],
        logger: logging.Logger | None = None,
    ) -> list[str | None]:
        """
        Convert a list of image URLs to base64 encoded strings asynchronously.
        A base64 data URL is not fetched. Every image is shrunk to what
        indexing keeps (``fit_image_for_index``), and once this call's images
        reach ``INDEX_RECORD_IMAGE_BYTES`` the rest are not fetched (None).
        SVG images are skipped and None is appended instead.

        Args:
            urls: List of image URLs or base64 data URLs
            logger: Optional logger; defaults to module logger when omitted

        Returns:
            List of base64 encoded image strings (None for SVG images or failed conversions)
        """
        fetcher = PublicUrlFetcher()
        # Each image is held whole, and again as base64, until it is shrunk:
        # fetched all at once, a page of photos was held at full size together.
        slots = asyncio.Semaphore(_MAX_PARALLEL_IMAGE_FETCHES)
        # Downloads overlap; decoding does not: each holds a photo's pixels.
        fitting = asyncio.Lock()
        kept = skipped = 0

        async def fetch(url: str) -> str | None:
            nonlocal kept, skipped
            async with slots:
                if kept >= INDEX_RECORD_IMAGE_BYTES:
                    skipped += 1
                    return None
                uri = await ImageParser._fetch_single_url(fetcher, url, logger=logger)
                if uri is None:
                    return None
                async with fitting:
                    uri = await asyncio.to_thread(fit_image_for_index, uri)
                kept += len(uri)
                return uri

        base64_images = list(await asyncio.gather(*(fetch(url) for url in urls)))
        if skipped:
            (logger or _logger).info(
                "Image budget of %d MB reached; %d image(s) not fetched",
                INDEX_RECORD_IMAGE_BYTES // (1024 * 1024), skipped,
            )
        return base64_images

    @staticmethod
    def _extract_svg_dimensions(svg_str: str) -> tuple[int | None, int | None]:
        """Width and height of an SVG from its attributes or viewBox."""
        return svg_core.extract_svg_dimensions(svg_str)

    @staticmethod
    def _resolve_css_var_fallbacks(svg_str: str) -> str:
        """Replace each CSS ``var(--name, fallback)`` with its fallback."""
        return svg_core.resolve_css_var_fallbacks(svg_str)

    @staticmethod
    def _sanitize_svg_content(svg_str: str) -> bytes:
        """Repair namespace declarations and ``var()`` values cairosvg cannot read."""
        return svg_core.sanitize_svg_content(svg_str)

    @staticmethod
    def _svg_to_png_in_process(
        svg_base64: str,
        output_width: int | None = None,
        output_height: int | None = None,
        scale: float = 1.0,
        background_color: str | None = None
    ) -> str:
        """SVG base64 to PNG base64 in this process (what the SVG worker runs)."""
        return svg_core.convert(
            svg_base64, output_width, output_height, scale, background_color, svg2png=svg2png
        )

    @staticmethod
    def svg_base64_to_png_base64(
        svg_base64: str,
        output_width: int | None = None,
        output_height: int | None = None,
        scale: float = 1.0,
        background_color: str | None = None
    ) -> str:
        """Convert an SVG (base64, with or without data URI prefix) to PNG base64.

        Runs in a child process with a wall-clock deadline and a memory cap
        (``svg_worker.run_bounded``), since the SVG comes from a document.
        Raises ``DocumentProcessingError`` when it is refused, overruns or fails.
        """
        from app.modules.parsers.image_parser import svg_worker

        return svg_worker.run_bounded(
            svg_base64, output_width, output_height, scale, background_color
        )

    @staticmethod
    async def svg_base64_to_png_base64_async(svg_base64: str) -> str:
        """``svg_base64_to_png_base64`` for async callers: a parse-pool worker when
        the service has one, otherwise a child process of its own."""
        from app.modules.parsers.image_parser import svg_worker

        return await svg_worker.render_async(svg_base64)
