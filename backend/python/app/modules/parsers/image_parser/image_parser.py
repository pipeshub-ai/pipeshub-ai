import base64
import requests

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

    def urls_to_base64(self, urls: list[str]) -> list[str]:
        """
        Convert a list of image URLs to base64 encoded strings.
        If a URL is already a base64 data URL, it's returned as-is.
        SVG images are skipped and None is appended instead.
        
        Args:
            urls: List of image URLs or base64 data URLs
            
        Returns:
            List of base64 encoded image strings (None for SVG images or failed conversions)
        """
        base64_images = []
        
        for url in urls:
            # Check if already a base64 data URL
            if url.startswith('data:image/'):
                # Skip SVG images
                if 'svg' in url.lower():
                    base64_images.append(None)
                    self.logger.debug(f"Skipping SVG image (already base64)")
                    continue
                base64_images.append(url)
                self.logger.debug(f"URL is already base64 encoded")
                continue
                
            try:
                # Fetch the image from URL
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                
                # Get the content type to determine image format
                content_type = response.headers.get('content-type', '')
                
                # Extract extension from content type (e.g., 'image/png' -> 'png')
                extension = 'png'  # default
                if 'image/' in content_type:
                    extension = content_type.split('/')[-1].split(';')[0]
                
                # Skip SVG images
                if 'svg' in extension.lower():
                    base64_images.append(None)
                    self.logger.debug(f"Skipping SVG image from URL: {url[:100]}...")
                    continue
                
                # Encode to base64
                base64_encoded = base64.b64encode(response.content).decode('utf-8')
                base64_image = f"data:image/{extension};base64,{base64_encoded}"
                
                base64_images.append(base64_image)
                self.logger.debug(f"Converted URL to base64: {url[:100]}...")
                
            except Exception as e:
                self.logger.error(f"Failed to convert URL to base64: {url}, error: {str(e)}")
                base64_images.append(None)
        
        return base64_images
