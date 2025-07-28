import json
import uuid

from app.models.blocks import (
    Block,
    BlockGroup,
    BlocksContainer,
    BlockType,
    CitationMetadata,
    GroupType,
    ImageMetadata,
    TableMetadata,
    TextFormat,
)
from app.utils.transformation.bbox import (
    normalize_corner_coordinates,
    transform_bbox_to_corners,
)

DOCLING_TEXT_BLOCK_TYPE = "texts"
DOCLING_IMAGE_BLOCK_TYPE = "pictures"
DOCLING_TABLE_BLOCK_TYPE = "tables"
DOCLING_GROUP_BLOCK_TYPE = "groups"
DOCLING_PAGE_BLOCK_TYPE = "pages"
DOCLING_REF_NODE= "$ref"

class DoclingDocToBlocksConverter():
    def __init__(self, logger):
        self.logger = logger


    # Docling document format:
    # {
    #     "body": {
    #         "children": [
    #             {
    #                 "$ref": "#/texts/0"
    #             },
    #             {
    #                 "$ref": "#/texts/1"
    #             }
    #         ]
    #     },
    #     "texts": [
    #         {
    #             "self_ref": "#/texts/0",
    #             "text": "Hello, world!",
    #             "prov": [
    #                 {
    #                     "page_no": 1,
    #                     "bbox": {"l": 0, "t": 10.1, "r": 10.1,  "b": 10.1, "coord_origin": "BOTTOMLEFT"}
    #                 }
    #             ]
    #         },
    #         {
    #             "self_ref": "#/texts/1",
    #             "text": "Hello, world!",
    #             "prov": [
    #                 {
    #                     "page_no": 1,
    #                     "bbox": {"l": 0, "t": 10.1, "r": 10.1,  "b": 10.1, "coord_origin": "BOTTOMLEFT"}
    #                 }
    #             ]
    #         }
    #     ]
    #     "pictures": [
    #         {
    #             "self_ref": "#/pictures/0",
    #             "image": "data:image/png;base64,...",
    #             "prov": [
    #                 {
    #                     "page_no": 1,
    #                     "bbox": {"l": 0, "t": 10.1, "r": 10.1,  "b": 10.1, "coord_origin": "BOTTOMLEFT"}
    #                 }
    #             ]
    #         }
    #     ]
    #     "tables": [
    # }
    #
    # Todo: Handle Bounding Boxes, PPTX, CSV, Excel, Docx, markdown, html etc.
    def _process_content_in_order(self, doc_dict: dict) -> BlocksContainer:
        """
        Process document content in proper reading order by following references.

        Args:
            doc_dict (dict): The document dictionary from Docling

        Returns:
            list: Ordered list of text items with their context
        """
        block_groups = []
        blocks = []
        processed_refs = set() # check by block_source_id

        def _enrich_metadata(block: Block|BlockGroup, item: dict, doc_dict: dict):
            page_metadata = doc_dict.get("pages", [])
            print(f"Page metadata: {json.dumps(page_metadata, indent=4)}")
            print(f"Item: {json.dumps(item, indent=4)}")
            if "prov" in item:
                prov = item["prov"]
                if isinstance(prov, list) and len(prov) > 0:
                    # Take the first page number from the prov list
                    page_no = prov[0].get("page_no")
                    if page_no:
                        page_no = str(page_no)
                        if isinstance(block, Block):
                            block.citation_metadata = CitationMetadata(page_number=page_no)
                        elif isinstance(block, BlockGroup):
                            block.citation_metadata = CitationMetadata(page_number=page_no)
                        bounding_boxes = []
                        print(f"Page no: {page_no}")
                        print(f"Page metadata: {json.dumps(page_metadata, indent=4)}")
                        print(f"Page metadata: {json.dumps(page_metadata.get(page_no), indent=4)}")
                        page_size = page_metadata[page_no].get("size", {})
                        page_width = page_size.get("width", 0)
                        page_height = page_size.get("height", 0)
                        page_bbox = prov[0].get("bbox", {})

                        if page_bbox:
                            page_corners = transform_bbox_to_corners(page_bbox)
                            bounding_boxes = normalize_corner_coordinates(page_corners, page_width, page_height)
                            print(f"bounding boxes: {bounding_boxes}")
                        block.citation_metadata.bounding_boxes = bounding_boxes


                elif isinstance(prov, dict) and DOCLING_REF_NODE in prov:
                    # Handle legacy reference format if needed
                    page_path = prov[DOCLING_REF_NODE]
                    page_index = int(page_path.split("/")[-1])
                    pages = doc_dict.get("pages", [])
                    if page_index < len(pages):
                        page_no = pages[page_index].get("page_no")
                        if page_no:
                            if isinstance(block, Block):
                                block.citation_metadata = CitationMetadata(page_number=page_no)
                            elif isinstance(block, BlockGroup):
                                block.citation_metadata = CitationMetadata(page_number=page_no)

        def _handle_text_block(item: dict, doc_dict: dict, parent_index: int, ref_path: str) -> Block:
            block = Block(
                    id=str(uuid.uuid4()),
                    index=len(blocks),
                    type=BlockType.TEXT,
                    format=TextFormat.TXT,
                    data=item.get("text", ""),
                    comments=[],
                    source_creation_date=None,
                    source_update_date=None,
                    source_id=ref_path,
                    source_name=None,
                    source_type=None,
                    parent_index=parent_index,
                )
            _enrich_metadata(block, item, doc_dict)
            blocks.append(block)

        def _handle_group_block(item: dict, doc_dict: dict, parent_index: int, level: int) -> BlockGroup:
            # For groups, process children and return their blocks
            children = item.get("children", [])
            block_group = BlockGroup(
                    id=str(uuid.uuid4()),
                    index=len(block_groups),
                    name=item.get("name", ""),
                    type=GroupType.LIST,
                    parent_index=parent_index,
                    description=None,
                    source_group_id=item.get("self_ref", ""),
                )
            block_groups.append(block_group)
            _enrich_metadata(block_group, item, doc_dict)
            for child in children:
                _process_item(child, level + 1, block_group.index)

        def _handle_image_block(item: dict, doc_dict: dict, parent_index: int, ref_path: str) -> Block:
            block = Block(
                    id=str(uuid.uuid4()),
                    index=len(blocks),
                    type=BlockType.IMAGE,
                    format=TextFormat.UTF8,
                    data=item.get("image", ""),
                    comments=[],
                    source_creation_date=None,
                    source_update_date=None,
                    source_id=ref_path,
                    source_name=None,
                    source_type=None,
                    parent_index=parent_index,
                    image_metadata=ImageMetadata(
                        captions=item.get("captions", []),
                        footnotes=item.get("footnotes", []),
                        annotations=item.get("annotations", []),
                    )
                )
            _enrich_metadata(block, item, doc_dict)
            blocks.append(block)

        def _handle_table_block(item: dict, doc_dict: dict, parent_index: int, ref_path: str) -> BlockGroup:
            print(f"Processing table: {json.dumps(item.get('data', ''), indent=4)}")
            table_data = item.get("data", {})
            table_rows = table_data.get("grid", [])
            print(f"Table rows: {json.dumps(table_rows, indent=4)}")
            block_group = BlockGroup(
                id=str(uuid.uuid4()),
                index=len(block_groups),
                name=item.get("name", ""),
                type=GroupType.TABLE,
                parent_index=parent_index,
                description=None,
                source_group_id=item.get("self_ref", ""),
                table_metadata=TableMetadata(
                    num_of_rows=table_data.get("num_rows", 0),
                    num_of_cols=table_data.get("num_cols", 0),
                    captions=item.get("captions", []),
                    footnotes=item.get("footnotes", []),
                )
            )
            table_rows = item.get("data", {}).get("grid", [])

            for row in table_rows:
                print(f"Processing table row: {json.dumps(row, indent=4)}")
                block = Block(
                    id=str(uuid.uuid4()),
                    index=len(blocks),
                    type=BlockType.TABLE_ROW,
                    format=TextFormat.TXT,
                    data=json.dumps(row),
                    comments=[],
                    source_creation_date=None,
                    source_update_date=None,
                    source_id=ref_path,
                    source_name=None,
                    source_type=None,
                    parent_index=block_group.index,
                )
                _enrich_metadata(block, row, doc_dict)
                blocks.append(block)


            _enrich_metadata(block_group, item, doc_dict)
            block_groups.append(block_group)


        def _process_item(ref, level=0, parent_index=None) -> None:
            """Recursively process items following references and return a BlockContainer"""
            # e.g. {"$ref": "#/texts/0"}

            if isinstance(ref, dict):
                ref_path = ref.get(DOCLING_REF_NODE, "")
            else:
                ref_path = ref

            if not ref_path or ref_path in processed_refs:
                return None

            processed_refs.add(ref_path)

            if not ref_path.startswith("#/"):
                return None

            path_parts = ref_path[2:].split("/")
            item_type = path_parts[0]  # 'texts', 'groups', etc.
            try:
                item_index = int(path_parts[1])
            except (IndexError, ValueError):
                return None

            items = doc_dict.get(item_type, [])
            if item_index >= len(items):
                return None

            item = items[item_index]

            if not item or not isinstance(item, dict) or item_type not in [DOCLING_TEXT_BLOCK_TYPE, DOCLING_GROUP_BLOCK_TYPE, DOCLING_IMAGE_BLOCK_TYPE, DOCLING_TABLE_BLOCK_TYPE]:
                self.logger.error(f"Invalid item type: {item_type} {item}")
                return None

            print(f"Processing item: {item_type} {ref_path}")

            # Create block
            if item_type == DOCLING_TEXT_BLOCK_TYPE:
                _handle_text_block(item, doc_dict, parent_index, ref_path)

            elif item_type == DOCLING_GROUP_BLOCK_TYPE:
                _handle_group_block(item, doc_dict, parent_index, level)


            elif item_type == DOCLING_IMAGE_BLOCK_TYPE:
                _handle_image_block(item, doc_dict, parent_index, ref_path)

            elif item_type == DOCLING_TABLE_BLOCK_TYPE:
                _handle_table_block(item, doc_dict, parent_index, ref_path)
            else:
                self.logger.error(f"❌ Unknown item type: {item_type} {item}")
                return None

        # Start processing from body
        body = doc_dict.get("body", {})
        for child in body.get("children", []):
            _process_item(child)

        self.logger.debug(f"Processed {len(blocks)} items in order")

        return BlocksContainer(blocks=blocks, block_groups=block_groups)

    def convert(self, doc: dict) -> BlocksContainer:
        block_containers = self._process_content_in_order(doc)
        # Log block information in a JSON-serializable format
        print(f"Ordered items: {json.dumps(doc, indent=4)}")
        blocks = block_containers.blocks

        # For each block, print the data and combine the data of all blocks into a single string, add a new line after each block
        # add a delimiter between blocks when page is changed
        combined_data = ""
        current_page = None
        for block in blocks:
            if block.citation_metadata and block.citation_metadata.page_number is  None:
                print(f"Block: {block.data}")
            if block.citation_metadata and block.citation_metadata.page_number is not None and block.citation_metadata.page_number != current_page:
                combined_data += "\n\n"
                current_page = block.citation_metadata.page_number
            combined_data += block.data + "\n\n"
        # for block_group in block_groups:
        #     combined_data += block_group.data
        print(f"Combined data: {combined_data}")
        # Safely serialize blocks, handling cases where model_dump might not exist
        # try:
            # serialized_blocks = [block.model_dump() if hasattr(block, 'model_dump') else str(block) for block in blocks]
            # serialized_block_groups = [block_group.model_dump() if hasattr(block_group, 'model_dump') else str(block_group) for block_group in block_groups]
            # print(f"Block groups: {json.dumps(serialized_block_groups, indent=4)}")
            # print(f"Blocks: {json.dumps(serialized_blocks, indent=4)}")

        # except Exception as e:
        #     print(f"Error serializing blocks: {e}")
        #     print(f"Blocks: {blocks}"

        # )

        return block_containers
