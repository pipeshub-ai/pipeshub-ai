import json
from typing import List
import uuid
import io
import base64
from app.utils.llm import get_llm
from docling.datamodel.document import DoclingDocument


from app.models.blocks import (
    Block,
    BlockGroup,
    BlocksContainer,
    BlockType,
    CitationMetadata,
    GroupType,
    ImageMetadata,
    Point,
    TableMetadata,
    DataFormat,
)
from app.utils.transformation.bbox import (
    normalize_corner_coordinates,
    transform_bbox_to_corners,
)
from app.modules.parsers.excel.prompt_template import row_text_prompt, table_summary_prompt

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
        self.llm = None

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
    async def _process_content_in_order(self, doc: DoclingDocument) -> BlocksContainer:
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
            # print(f"Page metadata: {json.dumps(page_metadata, indent=4)}")
            # print(f"Item: {json.dumps(item, indent=4)}")
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
        

        def _handle_text_block(item: dict, doc_dict: dict, parent_index: int, ref_path: str) -> Block:
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
                    format=DataFormat.MARKDOWN,
                )
            block_groups.append(block_group)
            _enrich_metadata(block_group, item, doc_dict)
            for child in children:
                _process_item(child, level + 1, block_group.index)

        def _get_ref_text(ref_path: str, doc_dict: dict) -> str:
            """Get text content from a reference path."""
            if not ref_path.startswith("#/"):
                return ""
            path_parts = ref_path[2:].split("/")
            item_type = "texts"
            items = doc_dict.get(item_type, [])
            item_index = int(path_parts[1])
            item = items[item_index] if item_index < len(items) else None
            if item and isinstance(item, dict):
                return item.get("text", "")
            return ""

        def _handle_image_block(item: dict, doc_dict: dict, parent_index: int, ref_path: str) -> Block:
            pictureItem = doc.pictures[int(item.get("self_ref", "").split("/")[-1])]
            _captions = item.get("captions", [])
            _captions = [_get_ref_text(ref_path,doc_dict) for caption in _captions]
            _footnotes = item.get("footnotes", []) 
            _footnotes = [_get_ref_text(ref_path,doc_dict) for footnote in _footnotes]
            prov = item.get("prov", {})
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

        async def _handle_table_block(item: dict, doc_dict: dict,parent_index: int, ref_path: str,table_markdown: str) -> BlockGroup:
            table_data = item.get("data", {})
            table_summary, column_headers = await self.get_table_summary(table_data)
            table_rows_text,table_rows = await self.get_rows_text(table_data, table_summary, column_headers)
            
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
                ),
                data={
                    "table_summary": table_summary,
                    "column_headers": column_headers,
                    "table_markdown": table_markdown,
                },
                format=DataFormat.JSON,
            )
            _enrich_metadata(block_group, item, doc_dict)

            children = []
            for i,row in enumerate(table_rows):
                print(f"Processing table row: {json.dumps(row, indent=4)}")
                index = len(blocks)
                block = Block(
                    id=str(uuid.uuid4()),
                    index=index,
                    type=BlockType.TABLE_ROW,
                    format=DataFormat.TXT,
                    comments=[],
                    source_creation_date=None,
                    source_update_date=None,
                    source_id=ref_path,
                    source_name=None,
                    source_type=None,
                    parent_index=block_group.index,
                    data={
                        "row_natural_language_text": table_rows_text[i],
                        "row_number": i+1,
                        "row":json.dumps(row)
                    },
                    citation_metadata=block_group.citation_metadata
                )
                # _enrich_metadata(block, row, doc_dict)
                blocks.append(block)
                children.append(index)

            block_group.children = children
            block_groups.append(block_group)

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

            print(f"Processing item: {item_type} {ref_path}")

            # Create block
            if item_type == DOCLING_TEXT_BLOCK_TYPE:
                _handle_text_block(item, doc_dict, parent_index, ref_path)

            elif item_type == DOCLING_GROUP_BLOCK_TYPE:
                return
                _handle_group_block(item, doc_dict, parent_index, level)


            elif item_type == DOCLING_IMAGE_BLOCK_TYPE:
                _handle_image_block(item, doc_dict, parent_index, ref_path)

            elif item_type == DOCLING_TABLE_BLOCK_TYPE:
                tables = doc.tables
                table = tables[item_index]
                table_markdown = table.export_to_markdown()
                await _handle_table_block(item, doc_dict, parent_index, ref_path,table_markdown)
            else:
                self.logger.error(f"❌ Unknown item type: {item_type} {item}")
                return None

        # Start processing from body
        doc_dict = doc.export_to_dict()
        self.pages = doc_dict.get("pages")
        body = doc_dict.get("body", {})
        for child in body.get("children", []):
            await _process_item(child,doc)

        self.logger.debug(f"Processed {len(blocks)} items in order")
        return BlocksContainer(blocks=blocks, block_groups=block_groups)

    async def convert(self, doc: DoclingDocument) -> BlocksContainer:
        block_containers = await self._process_content_in_order(doc)
        return block_containers

 
    async def _call_llm(self, messages):
        return await self.llm.ainvoke(messages)

    async def get_rows_text(
        self, table_data: dict, table_summary: str, column_headers: list[str]
    ) -> List[str]:
        """Convert multiple rows into natural language text using context from summaries in a single prompt"""
        try:
            # Prepare rows data
            if column_headers[0].startswith("Column_"):
                table_rows = table_data.get("grid", [])
            else:
                table_rows = table_data.get("grid", [])[1:]

            rows_data = [
                {
                    column_headers[i]: (
                        cell.get("text", "")
                    )
                    for i, cell in enumerate(row)
                }
                for row in table_rows
            ]

            # Get natural language text from LLM with retry
            messages = row_text_prompt.format_messages(
                table_summary=table_summary, rows_data=json.dumps(rows_data, indent=2)
            )

            response = await self._call_llm(messages)
            if '</think>' in response.content:
                response.content = response.content.split('</think>')[-1]
            # Try to extract JSON array from response
            try:
                # First try direct JSON parsing
                return json.loads(response.content),table_rows
            except json.JSONDecodeError:
                # If that fails, try to find and parse a JSON array in the response
                content = response.content
                # Look for array between [ and ]
                start = content.find("[")
                end = content.rfind("]")
                if start != -1 and end != -1:
                    try:
                        return json.loads(content[start : end + 1]),table_rows
                    except json.JSONDecodeError:
                        # If still can't parse, return response as single-item array
                        return [content],table_rows
                else:
                    # If no array found, return response as single-item array
                    return [content],table_rows

        except Exception:
            raise

    async def get_table_summary(self, table_data: dict) -> str:
        """
        Use LLM to generate a concise summary of a Docling TableData object,
        mirroring the approach in Excel's get_table_summary.
        """
        try:
            def _cell_to_value(cell):
                if isinstance(cell, dict):
                    if "text" in cell and cell["text"] is not None:
                        return cell["text"]
                    return ""
                return cell

            grid = table_data.get("grid", []) or []

            column_headers: list[str] = []
            # row_headers: list[str] = []
            if grid:
                first_row = grid[0]
                if isinstance(first_row, list):
                    column_headers = [str(_cell_to_value(c)) if c is not None and c.get("column_header") else f"Column_{i+1}" for i, c in enumerate(first_row)]
                # first_column = list(map(lambda row: row[0], grid))
                # if isinstance(first_column, list):
                #     row_headers = [str(_cell_to_value(c)) if c is not None and c.get("row_header") else f"Row_{i+1}" for i, c in enumerate(first_column)]
                
                sample_rows = [row for row in grid if all(cell.get("column_header") == False for cell in row)][:3]

            sample_data = []
            for row in sample_rows:
                sample_data.append({column_headers[i]: _cell_to_value(cell) for i, cell in enumerate(row)})

            # LLM prompt (reuse Excel's)
            messages = table_summary_prompt.format_messages(
                headers=column_headers, sample_data=json.dumps(sample_data, indent=2)
            )
            response = await self._call_llm(messages)
            if '</think>' in response.content:
                    response.content = response.content.split('</think>')[-1]
            return response.content,column_headers
        except Exception as e:
            self.logger.error(f"Error getting table summary from Docling: {e}")
            raise
        
    async def _call_llm(self, messages):
        if self.llm is None:
            self.llm,_ = await get_llm(self.config)
        return await self.llm.ainvoke(messages)
