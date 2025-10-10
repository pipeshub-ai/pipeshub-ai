import base64
from app.models.blocks import Block, BlockType, BlocksContainer, DataFormat

class ImageParser:
    def __init__(self, logger) -> None:
        self.logger = logger
        
    def parse_image(self, image_content: bytes,extension: str) -> BlocksContainer:
        base64_image = "data:image/" + extension + ";base64," + base64.b64encode(image_content).decode("utf-8")
        self.logger.debug(f"Base64 image: {base64_image[:100]}")
        return self.parse_base64(base64_image)
    
    def parse_base64(self, image_base64: str) -> BlocksContainer:
        self.logger.debug(f"Parsing image base64")
        image_block = Block(
            index=0,
            type=BlockType.IMAGE.value,
            format=DataFormat.BASE64.value,
            data = {
                    "uri": image_base64   
            }
        )

        blocks_container = BlocksContainer(
            blocks=[image_block],
            block_groups=[]
        )

        return blocks_container

