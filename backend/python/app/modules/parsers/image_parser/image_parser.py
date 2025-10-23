import asyncio
import base64

import aiohttp

from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat


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

    async def _fetch_single_url(self, session: aiohttp.ClientSession, url: str) -> str | None:
        """
        Fetch a single URL and convert it to base64.

        Args:
            session: aiohttp ClientSession
            url: Image URL or base64 data URL

        Returns:
            Base64 encoded image string or None if failed/skipped
        """
        # Check if already a base64 data URL
        if url.startswith('data:image/'):
            # Skip SVG images
            if 'svg' in url.lower():
                self.logger.debug("Skipping SVG image (already base64)")
                return None
            self.logger.debug("URL is already base64 encoded")
            return url

        try:
            # Fetch the image from URL
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                response.raise_for_status()

                # Get the content type to determine image format
                content_type = response.headers.get('content-type', '')

                # Extract extension from content type (e.g., 'image/png' -> 'png')
                extension = 'png'  # default
                if 'image/' in content_type:
                    extension = content_type.split('/')[-1].split(';')[0]

                # Skip SVG images
                if 'svg' in extension.lower():
                    self.logger.debug(f"Skipping SVG image from URL: {url[:100]}...")
                    return None

                # Read content and encode to base64
                content = await response.read()
                base64_encoded = base64.b64encode(content).decode('utf-8')
                base64_image = f"data:image/{extension};base64,{base64_encoded}"

                self.logger.debug(f"Converted URL to base64: {url[:100]}...")
                return base64_image

        except Exception as e:
            self.logger.error(f"Failed to convert URL to base64: {url}, error: {str(e)}")
            return None

    async def urls_to_base64(self, urls: list[str]) -> list[str]:
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
