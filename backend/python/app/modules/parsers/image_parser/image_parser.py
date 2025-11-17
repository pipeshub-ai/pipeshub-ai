import asyncio
import base64
import re
from typing import Optional
from urllib.parse import unquote, urlparse

try:
    from cairosvg import svg2png
except Exception:
    svg2png = None
import aiohttp

from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat

VALID_IMAGE_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.webp','.svg']
VIEWBOX_NUM_COMPONENTS = 4
class ImageParser:
    def __init__(self, logger) -> None:
        self.logger = logger

    def parse_image(self, image_content: bytes, extension: str) -> BlocksContainer:
        base64_encoded_content = base64.b64encode(image_content).decode("utf-8")
        base64_image = f"data:image/{extension};base64,{base64_encoded_content}"
        self.logger.debug(f"Base64 image: {base64_image[:100]}")

        image_block = Block(
            index=0,
            type=BlockType.IMAGE.value,
            format=DataFormat.BASE64.value,
            data={"uri": base64_image},
        )
        return BlocksContainer(blocks=[image_block], block_groups=[])

    def _is_valid_image_url(self, url: str) -> bool:
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

    def _is_valid_image_content_type(self, content_type: str) -> tuple[bool, str]:
        """
        Validate content type and return (is_valid, extension).
        Returns (False, '') for invalid or unsupported image types.
        """
        if not content_type:
            return False, ''

        content_type = content_type.lower().split(';')[0].strip()

        # Must be an image content type
        if not content_type.startswith('image/'):
            return False, ''

        # Extract and validate extension
        extension = content_type.split('/')[-1]

        if f".{extension}" not in VALID_IMAGE_EXTENSIONS:
            return False, ''

        return True, extension

    async def _fetch_single_url(self, session: aiohttp.ClientSession, url: str) -> str | None:
        try:
            # Check if already a base64 data URL
            self.logger.debug(f"_fetch_single_url start for URL: {url[:200]}...")
            if url.startswith('data:image/'):
                # Skip SVG images - check the MIME type in the data URL
                if url.startswith('data:image/svg+xml'):
                    self.logger.debug("Data URL is SVG; converting SVG base64 to PNG base64")
                    return f"data:image/png;base64,{self.svg_base64_to_png_base64(url)}"

                self.logger.debug("URL is already base64 encoded")
                return url

            # Validate URL format before attempting to fetch
            if not self._is_valid_image_url(url):
                self.logger.warning(f"URL does not appear to be an image URL: {url[:100]}...")
                return None

            self.logger.debug(f"HEAD request for URL: {url[:200]}...")
            async with session.head(url, timeout=aiohttp.ClientTimeout(total=10), allow_redirects=True) as head_response:
                head_response.raise_for_status()
                content_type = head_response.headers.get('content-type', '').lower()
                self.logger.debug(f"HEAD content-type for URL {url[:200]}... => {content_type}")

                # Validate content type before fetching full image
                is_valid, extension = self._is_valid_image_content_type(content_type)
                if not is_valid:
                    self.logger.debug(f"Skipping non-image or unsupported image type: {content_type} from URL: {url[:100]}...")
                    return None

                # If extension couldn't be determined, try to get from URL or default
                if not extension:
                    parsed = urlparse(url)
                    path = parsed.path.lower()
                    for ext in VALID_IMAGE_EXTENSIONS:
                        if path.endswith(ext):
                            extension = ext.lstrip('.')
                            break
                    if not extension:
                        extension = 'png'  # fallback

            # Now fetch the actual image content
            self.logger.debug(f"GET request for URL: {url[:200]}...")
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10), allow_redirects=True) as response:
                response.raise_for_status()

                # Re-verify content-type from GET response (in case it differs)
                get_content_type = response.headers.get('content-type', '').lower()
                is_valid, fetched_extension = self._is_valid_image_content_type(get_content_type)
                self.logger.debug(f"GET content-type for URL {url[:200]}... => {get_content_type}")

                if not is_valid:
                    self.logger.warning(f"Content-type changed or invalid during GET: {get_content_type} from URL: {url[:100]}...")
                    return None

                # Use extension from GET response if available, otherwise use from HEAD
                if fetched_extension:
                    extension = fetched_extension

                # Read content and encode to base64
                content = await response.read()

                # Basic validation - ensure we got some content
                if not content:
                    self.logger.warning(f"Empty content received from URL: {url}")
                    return None

                base64_encoded = base64.b64encode(content).decode('utf-8')
                base64_image = f"data:image/{extension};base64,{base64_encoded}"
                if 'svg' in extension:
                    self.logger.debug("Detected SVG extension from GET; converting SVG base64 to PNG base64")
                    base64_image = f"data:image/png;base64,{self.svg_base64_to_png_base64(base64_encoded)}"
                self.logger.debug(f"Converted URL to base64 for {extension}: {url}")
                return base64_image

        except Exception as e:
            self.logger.error(f"Failed to convert URL to base64: {url}, error: {str(e)}")
            return None

    async def urls_to_base64(self, urls: list[str]) -> list[str|None]:
        """
        Convert a list of image URLs to base64 encoded strings asynchronously.
        If a URL is already a base64 data URL, it's returned as-is.
        SVG images are skipped and None is appended instead.

        Args:
            urls: List of image URLs or base64 data URLs

        Returns:
            List of base64 encoded image strings (None for SVG images or failed conversions)
        """
        async with aiohttp.ClientSession() as session:
            # Process all URLs concurrently
            tasks = [self._fetch_single_url(session, url) for url in urls]
            base64_images = await asyncio.gather(*tasks)
            return list(base64_images)

    @staticmethod
    def _extract_svg_dimensions(svg_str: str) -> tuple[Optional[int], Optional[int]]:
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
    def _sanitize_svg_content(svg_str: str) -> bytes:
        """
        Sanitize SVG content to fix common XML parsing issues, including unbound namespace prefixes.
        This function ensures that common SVG namespace declarations are present in the root <svg> element.

        Args:
            svg_str: SVG content as a string

        Returns:
            Sanitized SVG content as bytes
        """
        try:
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
        output_width: Optional[int] = None,
        output_height: Optional[int] = None,
        scale: float = 1.0,
        background_color: Optional[str] = None
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
                raise ValueError("Decoded content does not appear to be valid SVG")

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

            # Sanitize SVG content to fix unbound namespace prefixes and other XML issues
            sanitized_svg_data = ImageParser._sanitize_svg_content(svg_str)

            # Convert SVG to PNG using cairosvg
            # cairosvg requires explicit dimensions when SVG doesn't have them
            if svg2png is None:
                raise Exception("import from cairosvg failed")
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
            raise ValueError(f"Invalid base64 input: {e}")
        except UnicodeDecodeError as e:
            raise ValueError(f"Cannot decode SVG content: {e}")
        except Exception as e:
            raise Exception(f"SVG to PNG conversion failed: {e}") from e


