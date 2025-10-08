import base64
from app.models.blocks import Block, BlockType, BlocksContainer, DataFormat

class ImageParser:
    def __init__(self) -> None:
        pass
    

    def parse_image(self, image_content: str) -> BlocksContainer:
        base64_image = base64.b64encode(image_content).decode("utf-8")
        return self.parse_base64(base64_image)

    def parse_base64(self, image_base64: str) -> None:
        
        image_block = Block(
            index=0,
            type=BlockType.IMAGE.value,
            format=DataFormat.BASE64.value,
            data = {
                    "url": image_base64   
            }
        )

        blocks_container = BlocksContainer(
            blocks=[image_block],
            block_groups=[]
        )

        return blocks_container



