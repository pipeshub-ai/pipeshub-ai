import asyncio
import base64
import logging
from pathlib import Path
import re
from http import HTTPStatus
from typing import Any
from urllib.parse import unquote, urlparse

from app.exceptions.indexing_exceptions import DocumentProcessingError
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
VIEWBOX_NUM_COMPONENTS = 4
# Image URLs come from document bodies (uploads, synced mail, crawled pages), so a
# document's author chooses them: they must never reach internal addresses.
# A larger image is dropped rather than buffered whole: a document's image URL can
# serve any amount. downscale_to_limits fits accepted images to each model.
_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_IMAGE_FETCH_LIMITS = PublicFetchLimits(max_bytes=_MAX_IMAGE_BYTES, timeout_s=10.0)
_CSS_VAR_OPEN = re.compile(r'var\(\s*--[\w-]+\s*(,)?')
_MAX_PARALLEL_IMAGE_FETCHES = 4
# An SVG's author is whoever wrote the document, and cairosvg has no time limit
# of its own. So it is rasterised in a worker process with a deadline and a
# memory cap (``svg_worker``), and this budget is a cheap filter in front of it:
# the decoded size, the element count and depth, what references expand to, and
# the size of the PNG it will draw.
_MAX_SVG_BYTES = 5 * 1024 * 1024
_MAX_SVG_ELEMENTS = 50_000
_MAX_SVG_DEPTH = 100
_MAX_SVG_EXPANDED_ELEMENTS = 200_000
_MAX_SVG_REFERENCES = 20_000
_MAX_SVG_OUTPUT_PIXELS = 32_000_000
_logger = logging.getLogger(__name__)


def _local_name(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


_CSS_URL_REF = re.compile(r"url\(\s*['\"]?\s*#([^'\")\s]+)")


def _svg_references(node: object) -> list[str]:
    """Ids this element draws again: ``href``/``xlink:href`` and any ``url(#id)``."""
    refs: list[str] = []
    for name, value in node.attrib.items():  # type: ignore[attr-defined]
        if _local_name(name) == "href":
            target = value.strip()
            if target.startswith("#"):
                refs.append(target[1:].strip())
        elif "url(" in value:
            refs.extend(_CSS_URL_REF.findall(value))
    if _local_name(node.tag) == "style" and node.text:  # type: ignore[attr-defined]
        refs.extend(_CSS_URL_REF.findall(node.text))  # type: ignore[attr-defined]
    return refs


def check_svg_budget(svg_str: str) -> None:
    """Refuse an SVG that is too big, or draws too much, to rasterise.

    A cheap pre-filter in front of the worker process that bounds time and
    memory: entities are refused (``defusedxml``), and every reference
    (``<use>``, ``url(#id)`` in fill, mask, marker, clip-path, filter...) is
    counted as drawing its target again. Anything that is not well-formed XML
    is refused, as cairosvg could not read it either.
    """
    from defusedxml import ElementTree as safe_et
    from defusedxml.common import DefusedXmlException

    if len(svg_str) > _MAX_SVG_BYTES:
        raise DocumentProcessingError("SVG is too large to rasterise", details={"bytes": len(svg_str)})
    try:
        root = safe_et.fromstring(svg_str.encode("utf-8"))
    except DefusedXmlException as e:
        raise DocumentProcessingError("SVG uses entities or DTDs", details={"error": str(e)}) from e
    except Exception as e:
        raise DocumentProcessingError("SVG is not well-formed", details={"error": str(e)}) from e

    ids: dict[str, object] = {}
    count = 0
    references = 0
    stack = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > _MAX_SVG_ELEMENTS or depth > _MAX_SVG_DEPTH:
            raise DocumentProcessingError("SVG is too complex to rasterise", details={"elements": count})
        node_id = node.get("id")
        if node_id:
            ids.setdefault(node_id.strip(), node)
        references += len(_svg_references(node))
        if references > _MAX_SVG_REFERENCES:
            raise DocumentProcessingError("SVG has too many references", details={"references": references})
        stack.extend((child, depth + 1) for child in node)

    memo: dict[int, int] = {}

    def expanded(node: object, active: frozenset[int]) -> int:
        key = id(node)
        if key in memo:
            return memo[key]
        if key in active:
            raise DocumentProcessingError("SVG references itself")
        inner = active | {key}
        total = 1 + sum(expanded(c, inner) for c in node)  # type: ignore[attr-defined]
        for ref in _svg_references(node):
            target = ids.get(ref)
            if target is not None:
                total += expanded(target, inner)
        if total > _MAX_SVG_EXPANDED_ELEMENTS:
            raise DocumentProcessingError("SVG expands to too much to rasterise", details={"elements": total})
        memo[key] = total
        return total

    try:
        expanded(root, frozenset())
    except RecursionError as e:
        raise DocumentProcessingError("SVG is nested too deeply to rasterise") from e


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
        """
        Extract width and height from SVG content.
        Tries to get dimensions from width/height attributes, then from viewBox if not available.

        Args:
            svg_str: SVG content as a string

        Returns:
            Tuple of (width, height) in pixels, or (None, None) if not found
        """
        try:
            # Find the <svg> tag (case-insensitive)
            svg_tag_pattern = r'<svg[^>]*?>'
            match = re.search(svg_tag_pattern, svg_str, re.IGNORECASE)

            if not match:
                return None, None

            svg_tag = match.group(0)

            # Try to extract width attribute
            width_match = re.search(r'width\s*=\s*["\']?([^"\'>\s]+)', svg_tag, re.IGNORECASE)
            height_match = re.search(r'height\s*=\s*["\']?([^"\'>\s]+)', svg_tag, re.IGNORECASE)

            width = None
            height = None

            if width_match:
                width_str = width_match.group(1).strip()
                # Remove units (px, pt, etc.) and convert to int
                width_value = re.sub(r'[^\d.]', '', width_str)
                if width_value:
                    try:
                        width = int(float(width_value))
                    except ValueError:
                        pass

            if height_match:
                height_str = height_match.group(1).strip()
                # Remove units (px, pt, etc.) and convert to int
                height_value = re.sub(r'[^\d.]', '', height_str)
                if height_value:
                    try:
                        height = int(float(height_value))
                    except ValueError:
                        pass

            # If both found, return them
            if width and height:
                return width, height

            # If not found, try viewBox (format: "x y width height")
            viewbox_match = re.search(r'viewBox\s*=\s*["\']?([^"\'>]+)', svg_tag, re.IGNORECASE)
            if viewbox_match:
                viewbox_str = viewbox_match.group(1).strip()
                # Extract numbers from viewBox
                viewbox_numbers = re.findall(r'[-+]?\d*\.?\d+', viewbox_str)
                if len(viewbox_numbers) >= VIEWBOX_NUM_COMPONENTS:
                    try:
                        vb_width = int(float(viewbox_numbers[2]))
                        vb_height = int(float(viewbox_numbers[3]))
                        # Use viewBox dimensions if width/height not found
                        if not width:
                            width = vb_width
                        if not height:
                            height = vb_height
                    except (ValueError, IndexError):
                        pass

            return width, height

        except Exception:
            return None, None

    @staticmethod
    def _resolve_css_var_fallbacks(svg_str: str) -> str:
        """
        Replace each CSS ``var(--name, fallback)`` with its fallback, as a browser
        does when the custom property is not defined.

        cairosvg has no custom-property support, and its color parser finds a hex
        color anywhere in the value but slices it from index 1, so
        ``var(--fill-0, #B4B4B4)`` fails with "invalid literal for int() with
        base 16: 'ar'". A ``var()`` without a fallback, or one whose parentheses
        never close, is left as is.

        One pass with an explicit stack: the SVG comes from a document, so deep
        ``var()`` nesting must not recurse or rescan.
        """
        if 'var(' not in svg_str:
            return svg_str
        out: list[str] = []
        # One frame per open var(): [has_fallback, open parens inside it,
        # input index of "var(", output length at "var("].
        frames: list[list] = []
        i = 0
        n = len(svg_str)
        while i < n:
            char = svg_str[i]
            match = _CSS_VAR_OPEN.match(svg_str, i) if char == 'v' else None
            if match:
                has_fallback = match.group(1) is not None
                frames.append([has_fallback, 0, i, len(out)])
                i = match.end()
                if has_fallback:
                    while i < n and svg_str[i].isspace():
                        i += 1
                else:
                    out.extend(match.group(0))
                continue
            if frames and char == '(':
                frames[-1][1] += 1
            elif frames and char == ')':
                if frames[-1][1]:
                    frames[-1][1] -= 1
                else:
                    has_fallback, _, _, out_start = frames.pop()
                    if has_fallback:
                        while len(out) > out_start and out[-1].isspace():
                            out.pop()
                        i += 1
                        continue
            out.append(char)
            i += 1
        if frames:
            _, _, in_start, out_start = frames[0]
            return ''.join(out[:out_start]) + svg_str[in_start:]
        return ''.join(out)

    @staticmethod
    def _sanitize_svg_content(svg_str: str) -> bytes:
        """
        Sanitize SVG content to fix common XML parsing issues, including unbound namespace prefixes.
        This function ensures that common SVG namespace declarations are present in the root <svg> element,
        and replaces CSS var() values that cairosvg cannot read with their fallbacks.

        Args:
            svg_str: SVG content as a string

        Returns:
            Sanitized SVG content as bytes
        """
        try:
            svg_str = ImageParser._resolve_css_var_fallbacks(svg_str)

            # Find the <svg> tag (case-insensitive)
            svg_tag_pattern = r'(<svg[^>]*?)>'
            match = re.search(svg_tag_pattern, svg_str, re.IGNORECASE)

            if not match:
                # If no <svg> tag found, return as-is
                return svg_str.encode('utf-8')

            svg_tag = match.group(1)
            fixed_tag = svg_tag

            # Add xmlns if completely missing
            if 'xmlns=' not in fixed_tag.lower() and 'xmlns:' not in fixed_tag.lower():
                fixed_tag += ' xmlns="http://www.w3.org/2000/svg"'
            # Ensure default SVG namespace is present
            elif 'xmlns="' not in fixed_tag and 'xmlns=\'' not in fixed_tag:
                # Check if it has xmlns:something but not xmlns itself
                if not re.search(r'xmlns\s*=\s*["\']', fixed_tag, re.IGNORECASE):
                    fixed_tag += ' xmlns="http://www.w3.org/2000/svg"'

            # Add xlink namespace if xlink: is used anywhere but namespace not declared
            if re.search(r'\bxlink:', svg_str, re.IGNORECASE):
                if 'xmlns:xlink=' not in fixed_tag.lower():
                    fixed_tag += ' xmlns:xlink="http://www.w3.org/1999/xlink"'

            # If we made changes, replace the original tag
            if fixed_tag != svg_tag:
                svg_str = svg_str[:match.start()] + fixed_tag + '>' + svg_str[match.end():]

        except Exception:
            # If sanitization fails, return original content
            # cairosvg might still be able to handle it or will give a clearer error
            pass

        return svg_str.encode('utf-8')

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

    @staticmethod
    def _svg_to_png_in_process(
        svg_base64: str,
        output_width: int | None = None,
        output_height: int | None = None,
        scale: float = 1.0,
        background_color: str | None = None
    ) -> str:
        """
        Convert SVG base64 string to PNG base64 string.

        Args:
            svg_base64: Base64 encoded SVG string (with or without data URI prefix)
            output_width: Desired output width in pixels (optional)
            output_height: Desired output height in pixels (optional)
            scale: Scale factor for the output (default: 1.0)
            background_color: Background color for transparent areas (e.g., 'white', '#FFFFFF')

        Returns:
            Base64 encoded PNG string

        Raises:
            ValueError: If the input is not valid base64 or SVG
            Exception: If conversion fails
        """
        try:
            # Remove data URI prefix if present
            if svg_base64.startswith('data:image/svg+xml;base64,'):
                svg_base64 = svg_base64.replace('data:image/svg+xml;base64,', '')

            # Decode base64 to get SVG content
            svg_data = base64.b64decode(svg_base64)

            # Verify it's actually SVG content
            svg_str = svg_data.decode('utf-8')
            if not ('<svg' in svg_str.lower() or '<?xml' in svg_str.lower()):
                raise DocumentProcessingError(
                    "Decoded content does not appear to be valid SVG",
                    details={"content_preview": svg_str[:100]},
                )

            if len(svg_str) > _MAX_SVG_BYTES:
                raise DocumentProcessingError("SVG is too large to rasterise", details={"bytes": len(svg_str)})

            # Extract dimensions from SVG if not provided
            final_width = output_width
            final_height = output_height

            if not final_width or not final_height:
                svg_width, svg_height = ImageParser._extract_svg_dimensions(svg_str)

                # Use extracted dimensions if not provided
                if not final_width:
                    final_width = svg_width
                if not final_height:
                    final_height = svg_height

                # If still no dimensions found, use defaults
                # Default to 800x600 if no dimensions available
                if not final_width:
                    final_width = 800
                if not final_height:
                    final_height = 600

            if (final_width or 0) * (final_height or 0) * scale * scale > _MAX_SVG_OUTPUT_PIXELS:
                raise DocumentProcessingError(
                    "SVG would be rasterised too large",
                    details={"width": final_width, "height": final_height},
                )

            # Sanitize SVG content to fix unbound namespace prefixes and other XML issues
            sanitized_svg_data = ImageParser._sanitize_svg_content(svg_str)
            # The budget is applied to what cairosvg will read, the repaired text.
            check_svg_budget(sanitized_svg_data.decode("utf-8"))

            # Convert SVG to PNG using cairosvg
            # cairosvg requires explicit dimensions when SVG doesn't have them
            if svg2png is None:
                raise DocumentProcessingError(
                    "SVG conversion dependency missing: cairosvg is not installed",
                    details={"dependency": "cairosvg"},
                )
            png_data = svg2png(
                bytestring=sanitized_svg_data,
                output_width=final_width,
                output_height=final_height,
                scale=scale,
                background_color=background_color
            )

            # Encode PNG to base64
            png_base64 = base64.b64encode(png_data).decode('utf-8')

            return png_base64

        except base64.binascii.Error as e:
            raise DocumentProcessingError(
                f"Invalid base64 input: {e}",
                details={"error": str(e)},
            ) from e
        except UnicodeDecodeError as e:
            raise DocumentProcessingError(
                f"Cannot decode SVG content: {e}",
                details={"error": str(e)},
            ) from e
        except DocumentProcessingError:
            raise
        except Exception as e:
            raise DocumentProcessingError(
                f"SVG to PNG conversion failed: {e}",
                details={"error": str(e)},
            ) from e


