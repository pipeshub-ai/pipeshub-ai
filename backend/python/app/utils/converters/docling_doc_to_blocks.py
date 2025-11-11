import json
import uuid
from typing import Optional

from docling.datamodel.document import DoclingDocument

from app.models.blocks import (
    Block,
    BlockContainerIndex,
    BlockGroup,
    BlocksContainer,
    BlockType,
    CitationMetadata,
    DataFormat,
    GroupType,
    ImageMetadata,
    Point,
    TableMetadata,
)
from app.utils.indexing_helpers import (
    get_rows_text,
    get_table_summary_n_headers,
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
    def __init__(self, logger, config) -> None:
        self.logger = logger
        self.config = config

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
    async def _process_content_in_order(self, doc: DoclingDocument) -> BlocksContainer|bool:
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

        def _enrich_metadata(block: Block|BlockGroup, item: dict, doc_dict: dict) -> None:
            page_metadata = doc_dict.get("pages", {})
            # self.logger.debug(f"Page metadata: {json.dumps(page_metadata, indent=4)}")
            # self.logger.debug(f"Item: {json.dumps(item, indent=4)}")
            if "prov" in item:
                prov = item["prov"]
                if isinstance(prov, list) and len(prov) > 0:
                    # Take the first page number from the prov list
                    page_no = prov[0].get("page_no")
                    if page_no:
                        block.citation_metadata = CitationMetadata(page_number=page_no)
                        page_size = page_metadata[str(page_no)].get("size", {})
                        page_width = page_size.get("width", 0)
                        page_height = page_size.get("height", 0)
                        page_bbox = prov[0].get("bbox", {})

                        if page_bbox and page_width > 0 and page_height > 0:
                            try:
                                page_corners = transform_bbox_to_corners(page_bbox)
                                normalized_corners = normalize_corner_coordinates(page_corners, page_width, page_height)
                                # Convert normalized corners to Point objects
                                bounding_boxes = [Point(x=corner[0], y=corner[1]) for corner in normalized_corners]
                                block.citation_metadata.bounding_boxes = bounding_boxes
                            except Exception as e:
                                self.logger.warning(f"Failed to process bounding boxes: {e}")
                                # Don't set bounding_boxes if processing fails


                elif isinstance(prov, dict) and DOCLING_REF_NODE in prov:
                    # Handle legacy reference format if needed
                    page_path = prov[DOCLING_REF_NODE]
                    page_index = int(page_path.split("/")[-1])
                    pages = doc_dict.get("pages", [])
                    if page_index < len(pages):
                        page_no = pages[page_index].get("page_no")
                        if page_no:
                                block.citation_metadata = CitationMetadata(page_number=page_no)


        async def _handle_text_block(item: dict, doc_dict: dict, parent_index: int, ref_path: str,level: int,doc: DoclingDocument) -> Optional[Block]:
            block = None
            if item.get("text") != "":
                block = Block(
                        id=str(uuid.uuid4()),
                        index=len(blocks),
                        type=BlockType.TEXT,
                        format=DataFormat.TXT,
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
            children = item.get("children", [])
            for child in children:
                await _process_item(child, doc, level + 1)

            return block

        async def _handle_group_block(item: dict, doc_dict: dict, parent_index: int, level: int,doc: DoclingDocument) -> BlockGroup:
            # For groups, process children and return their blocks
            label = item.get("label", "")
            block_group = None
            if label in ["form_area","inline","key_value_area","list","ordered_list"]:
                block_group = BlockGroup(
                    index=len(block_groups),
                    type=GroupType(label),
                    parent_index=parent_index,
                )
                block_groups.append(block_group)

            children = item.get("children", [])
            childBlocks = []

            for child in children:
                result = await _process_item(child, doc, level + 1, block_group.index if block_group else None)
                if result:
                    if isinstance(result, Block):
                        childBlocks.append(BlockContainerIndex(block_index=result.index))
                    elif isinstance(result, BlockGroup):
                        childBlocks.append(BlockContainerIndex(block_group_index=result.index))

            if block_group:
                block_group.children = childBlocks

            return block_group

        def _get_ref_text(ref_path: str, doc_dict: dict) -> str:
            """Get text content from a reference path."""
            if not ref_path.startswith("#/"):
                return ""
            path_parts = ref_path[2:].split("/")
            item_type = "texts"
            items = doc_dict.get(item_type, [])
            item_index = int(path_parts[1])
            if item_index < len(items):
                item = items[item_index]
            else:
                item = None
            if item and isinstance(item, dict):
                return item.get("text", "")
            return ""

        def _resolve_ref_list(refs: list) -> list[str]:
                return [
                    _get_ref_text(ref.get(DOCLING_REF_NODE, ""), doc_dict) if isinstance(ref, dict) else str(ref)
                    for ref in refs
                ]

        async def _handle_image_block(item: dict, doc_dict: dict, parent_index: int, ref_path: str,level: int,doc: DoclingDocument) -> Block:
            _captions = item.get("captions", [])
            _captions = _resolve_ref_list(_captions)
            _footnotes = item.get("footnotes", [])
            _footnotes = _resolve_ref_list(_footnotes)
            item.get("prov", {})
            block = Block(
                    id=str(uuid.uuid4()),
                    index=len(blocks),
                    type=BlockType.IMAGE,
                    format=DataFormat.BASE64,
                    data=item.get("image",None ),
                    comments=[],
                    source_creation_date=None,
                    source_update_date=None,
                    source_id=ref_path,
                    source_name=None,
                    source_type=None,
                    parent_index=parent_index,
                    image_metadata=ImageMetadata(
                        captions=_captions,
                        footnotes=_footnotes,
                        # annotations=item.get("annotations", []),
                    ),
                )
            _enrich_metadata(block, item, doc_dict)
            blocks.append(block)
            children = item.get("children", [])
            for child in children:
                await _process_item(child, doc, level + 1)

            return block

        async def _handle_table_block(item: dict, doc_dict: dict,parent_index: int, ref_path: str,table_markdown: str,level: int,doc: DoclingDocument) -> BlockGroup|None:
            table_data = item.get("data", {})
            cell_data = table_data.get("table_cells", [])
            if len(cell_data) == 0:
                self.logger.warning(f"No table cells found in the table data: {table_data}")
                return None
            response = await get_table_summary_n_headers(self.config, table_markdown)
            table_summary = response.summary
            column_headers = response.headers

            table_rows_text,table_rows = await get_rows_text(self.config, table_data, table_summary, column_headers)

            # Convert caption and footnote references to text strings
            _captions = item.get("captions", [])
            _captions = _resolve_ref_list(_captions)
            _footnotes = item.get("footnotes", [])
            _footnotes = _resolve_ref_list(_footnotes)
            block_group = BlockGroup(
                index=len(block_groups),
                name=item.get("name", ""),
                type=GroupType.TABLE,
                parent_index=parent_index,
                description=None,
                source_group_id=item.get("self_ref", ""),
                table_metadata=TableMetadata(
                    num_of_rows=table_data.get("num_rows", 0),
                    num_of_cols=table_data.get("num_cols", 0),
                    captions=_captions,
                    footnotes=_footnotes,
                ),
                data={
                    "table_summary": table_summary,
                    "column_headers": column_headers,
                    "table_markdown": table_markdown,
                },
                format=DataFormat.JSON,
            )
            _enrich_metadata(block_group, item, doc_dict)

            childBlocks = []
            for i,row in enumerate(table_rows):
                index = len(blocks)
                block = Block(
                    id=str(uuid.uuid4()),
                    index=index,
                    type=BlockType.TABLE_ROW,
                    format=DataFormat.JSON,
                    comments=[],
                    source_creation_date=None,
                    source_update_date=None,
                    source_id=ref_path,
                    source_name=None,
                    source_type=None,
                    parent_index=block_group.index,
                    data={
                        "row_natural_language_text": table_rows_text[i] if i<len(table_rows_text) else "",
                        "row_number": i+1,
                        "row":json.dumps(row)
                    },
                    citation_metadata=block_group.citation_metadata
                )
                # _enrich_metadata(block, row, doc_dict)
                blocks.append(block)
                childBlocks.append(BlockContainerIndex(block_index=index))

            block_group.children = childBlocks
            block_groups.append(block_group)
            children = item.get("children", [])
            for child in children:
                await _process_item(child, doc, level + 1)

            return block_group

        async def _process_item(ref, doc: DoclingDocument, level=0, parent_index=None) -> None:
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

            self.logger.debug(f"Processing item: {item_type} {ref_path}")

            # Create block
            result = None
            if item_type == DOCLING_TEXT_BLOCK_TYPE:
                result = await _handle_text_block(item, doc_dict, parent_index, ref_path,level,doc)

            elif item_type == DOCLING_GROUP_BLOCK_TYPE:
                result = await _handle_group_block(item, doc_dict, parent_index, level,doc)


            elif item_type == DOCLING_IMAGE_BLOCK_TYPE:
                result = await _handle_image_block(item, doc_dict, parent_index, ref_path,level,doc)

            elif item_type == DOCLING_TABLE_BLOCK_TYPE:
                tables = doc.tables
                table = tables[item_index]
                table_markdown = table.export_to_markdown(doc=doc)
                result = await _handle_table_block(item, doc_dict, parent_index, ref_path,table_markdown,level,doc)
            else:
                self.logger.error(f"❌ Unknown item type: {item_type} {item}")
                return None

            return result

        # Start processing from body
        doc_dict = doc.export_to_dict()
        body = doc_dict.get("body", {})
        texts = doc_dict.get("texts", [])
        if texts == []:
            self.logger.info("No text blocks found in the document")
            return False
        for child in body.get("children", []):
            await _process_item(child,doc)

        self.logger.debug(f"Processed {len(blocks)} items in order")
        return BlocksContainer(blocks=blocks, block_groups=block_groups)

    async def convert(self, doc: DoclingDocument) -> BlocksContainer|bool:
        block_containers = await self._process_content_in_order(doc)
        return block_containers








