import asyncio
import base64
from urllib.parse import unquote, urlparse
from typing import Optional
from cairosvg import svg2png
import aiohttp

from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat

VALID_IMAGE_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.webp','.svg']
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

        if extension not in VALID_IMAGE_EXTENSIONS:
            return False, ''

        return True, extension

    async def _fetch_single_url(self, session: aiohttp.ClientSession, url: str) -> str | None:
        # Check if already a base64 data URL
        if url.startswith('data:image/'):
            # Skip SVG images - check the MIME type in the data URL
            if url.startswith('data:image/svg+xml'):
                return self.svg_base64_to_png_base64(url)

            self.logger.debug("URL is already base64 encoded")
            return url

        # Validate URL format before attempting to fetch
        if not self._is_valid_image_url(url):
            self.logger.warning(f"URL does not appear to be an image URL: {url[:100]}...")
            return None

        try:
            # First do a HEAD request to check content-type without downloading
            async with session.head(url, timeout=aiohttp.ClientTimeout(total=10), allow_redirects=True) as head_response:
                head_response.raise_for_status()
                content_type = head_response.headers.get('content-type', '').lower()

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
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10), allow_redirects=True) as response:
                response.raise_for_status()

                # Re-verify content-type from GET response (in case it differs)
                get_content_type = response.headers.get('content-type', '').lower()
                is_valid, fetched_extension = self._is_valid_image_content_type(get_content_type)

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
                if extension == 'svg':
                    base64_image = self.svg_base64_to_png_base64(base64_encoded)
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
            
            # Convert SVG to PNG using cairosvg
            png_data = svg2png(
                bytestring=svg_data,
                output_width=output_width,
                output_height=output_height,
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
            raise Exception(f"SVG to PNG conversion failed: {e}")


