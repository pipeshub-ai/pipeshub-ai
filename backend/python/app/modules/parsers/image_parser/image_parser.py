import base64

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



