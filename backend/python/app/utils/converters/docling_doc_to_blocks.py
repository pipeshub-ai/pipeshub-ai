import uuid
from typing import Any

from docling.datamodel.document import DoclingDocument

from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockType,
    CitationMetadata,
    DataFormat,
    GroupType,
    ImageMetadata,
    Point,
    TableMetadata,
)
from app.utils.indexing_metrics import track_indexing_enrichment
from app.utils.llm import get_llm_for_role
from app.utils.table_enrichment import (
    TableEnrichmentResult,
    enrich_table_grid,
    enrich_tables,
    simple_table_enrichment,
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

_ITEM_TYPES = (
    DOCLING_TEXT_BLOCK_TYPE,
    DOCLING_GROUP_BLOCK_TYPE,
    DOCLING_IMAGE_BLOCK_TYPE,
    DOCLING_TABLE_BLOCK_TYPE,
)


def _resolve_ref(doc_dict: dict[str, Any], ref_path: str) -> dict[str, Any] | None:
    """Resolve a ``#/<type>/<index>`` reference to its item, or None."""
    if not ref_path or not ref_path.startswith("#/"):
        return None
    parts = ref_path[2:].split("/")
    item_type = parts[0]
    try:
        index = int(parts[1])
    except (IndexError, ValueError):
        return None
    items = doc_dict.get(item_type, [])
    if index >= len(items):
        return None
    item = items[index]
    if not isinstance(item, dict) or item_type not in _ITEM_TYPES:
        return None
    return item


def collect_reachable_table_refs(doc_dict: dict[str, Any]) -> list[str]:
    """Table refs reachable from the body, in document order.

    Mirrors ``_process_item``'s ref-following so the tables enriched up front are
    exactly the ones the walk will render. ``doc_dict["tables"]`` can also hold entries
    unreachable from the body, which would otherwise be enriched for nothing.
    """
    seen: set[str] = set()
    table_refs: list[str] = []

    def walk(ref: Any) -> None:  # noqa: ANN401
        ref_path = ref.get(DOCLING_REF_NODE, "") if isinstance(ref, dict) else ref
        # Dedupe over every ref type, matching _process_item's processed_refs.
        if not ref_path or ref_path in seen:
            return
        seen.add(ref_path)
        item = _resolve_ref(doc_dict, ref_path)
        if item is None:
            return
        if ref_path[2:].split("/")[0] == DOCLING_TABLE_BLOCK_TYPE:
            table_refs.append(ref_path)
        for child in item.get("children", []):
            walk(child)

    for child in doc_dict.get("body", {}).get("children", []):
        walk(child)
    return table_refs



class DoclingDocToBlocksConverter():
    def __init__(self, logger: Any, config: Any) -> None:  # noqa: ANN401
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
    async def _process_content_in_order(self, doc: DoclingDocument, page_number: int | None = None, skip_table_enrichment: bool = False) -> BlocksContainer|bool:
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

        def _enrich_metadata(block: Block|BlockGroup, item: dict[str, Any], doc_dict: dict[str, Any], default_page_number: int | None = None) -> None:
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

            # Fallback: use default_page_number if no citation_metadata was set from prov
            if default_page_number is not None:
                if block.citation_metadata is None:
                    block.citation_metadata = CitationMetadata(page_number=default_page_number)
                else:
                    block.citation_metadata.page_number = default_page_number

        async def _handle_text_block(item: dict[str, Any], doc_dict: dict[str, Any], parent_index: int | None, ref_path: str,level: int,doc: DoclingDocument) -> Block | None:
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
                _enrich_metadata(block, item, doc_dict, default_page_number=page_number)
                blocks.append(block)
            children = item.get("children", [])
            for child in children:
                await _process_item(child, doc, level + 1)

            return block

        async def _handle_group_block(item: dict[str, Any], doc_dict: dict[str, Any], parent_index: int | None, level: int,doc: DoclingDocument) -> BlockGroup:
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
            child_block_indices = []
            child_block_group_indices = []

            for child in children:
                result = await _process_item(child, doc, level + 1, block_group.index if block_group else None)
                if result:
                    if isinstance(result, Block):
                        child_block_indices.append(result.index)
                    elif isinstance(result, BlockGroup):
                        child_block_group_indices.append(result.index)

            if block_group:
                block_group.children = BlockGroupChildren.from_indices(
                    block_indices=child_block_indices,
                    block_group_indices=child_block_group_indices
                )

            return block_group

        def _get_ref_text(ref_path: str, doc_dict: dict[str, Any]) -> str:
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

        def _resolve_ref_list(refs: list[Any]) -> list[str]:
                return [
                    _get_ref_text(ref.get(DOCLING_REF_NODE, ""), doc_dict) if isinstance(ref, dict) else str(ref)
                    for ref in refs
                ]

        async def _handle_image_block(item: dict[str, Any], doc_dict: dict[str, Any], parent_index: int | None, ref_path: str,level: int,doc: DoclingDocument) -> Block:
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
                    data=item.get("image"),
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
            _enrich_metadata(block, item, doc_dict, default_page_number=page_number)
            blocks.append(block)
            children = item.get("children", [])
            for child in children:
                await _process_item(child, doc, level + 1)

            return block

        async def _handle_table_block(item: dict[str, Any], doc_dict: dict[str, Any],parent_index: int | None, ref_path: str,level: int,doc: DoclingDocument) -> BlockGroup|None:
            table_data = item.get("data", {})
            cell_data = table_data.get("table_cells", [])
            if len(cell_data) == 0:
                self.logger.warning(f"No table cells found in the table data: {table_data}")
                return None

            table_grid = table_data.get("grid", [])

            enrichment = table_enrichments.get(ref_path)
            if enrichment is None and skip_table_enrichment:
                enrichment = simple_table_enrichment(table_grid)
            elif enrichment is None:
                # The pre-pass and this walk are meant to visit the same tables; a miss
                # means they drifted apart. Enrich inline so correctness never depends
                # on the two walks staying in sync - only the latency win does.
                self.logger.warning(f"Table {ref_path} missed pre-enrichment; enriching inline")
                fallback_llm = llm
                if fallback_llm is None:
                    fallback_llm, _ = await get_llm_for_role(self.config, "indexing", reasoning_effort="low")
                enrichment = await enrich_table_grid(
                    fallback_llm,
                    table_grid,
                    logger=self.logger,
                )

            table_summary = enrichment.summary
            column_headers = enrichment.headers
            table_rows_text = enrichment.descriptions

            # Convert caption and footnote references to text strings
            _captions = item.get("captions", [])
            _captions = _resolve_ref_list(_captions)
            _footnotes = item.get("footnotes", [])
            _footnotes = _resolve_ref_list(_footnotes)

            num_of_rows = table_data.get("num_rows", None)
            num_of_cols = table_data.get("num_cols", None)
            num_of_cells = num_of_rows * num_of_cols if num_of_rows and num_of_cols else None

            block_group = BlockGroup(
                index=len(block_groups),
                name=item.get("name", ""),
                type=GroupType.TABLE,
                parent_index=parent_index,
                description=None,
                source_group_id=item.get("self_ref", ""),
                table_metadata=TableMetadata(
                    num_of_rows=num_of_rows,
                    num_of_cols=num_of_cols,
                    num_of_cells=num_of_cells,
                    captions=_captions,
                    footnotes=_footnotes,
                ),
                data={
                    "table_summary": table_summary,
                    "column_headers": column_headers,
                },
                format=DataFormat.JSON,
            )
            _enrich_metadata(block_group, item, doc_dict, default_page_number=page_number)

            table_row_block_indices = []
            for i, row_text in enumerate(table_rows_text):
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
                        "row_natural_language_text": row_text,
                        "row_number": i+1
                    },
                    citation_metadata=block_group.citation_metadata
                )
                # _enrich_metadata(block, row, doc_dict)
                blocks.append(block)
                table_row_block_indices.append(index)

            block_group.children = BlockGroupChildren.from_indices(block_indices=table_row_block_indices)
            block_groups.append(block_group)
            children = item.get("children", [])
            for child in children:
                await _process_item(child, doc, level + 1)

            return block_group

        async def _process_item(ref: Any, doc: DoclingDocument, level: int = 0, parent_index: int | None = None) -> Block | BlockGroup | None:  # noqa: ANN401
            """Recursively process items following references and return a BlockContainer"""
            # e.g. {"$ref": "#/texts/0"}

            ref_path = ref.get(DOCLING_REF_NODE, "") if isinstance(ref, dict) else ref

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
                
            # Create block
            result = None
            if item_type == DOCLING_TEXT_BLOCK_TYPE:
                result = await _handle_text_block(item, doc_dict, parent_index, ref_path,level,doc)

            elif item_type == DOCLING_GROUP_BLOCK_TYPE:
                result = await _handle_group_block(item, doc_dict, parent_index, level,doc)


            elif item_type == DOCLING_IMAGE_BLOCK_TYPE:
                result = await _handle_image_block(item, doc_dict, parent_index, ref_path,level,doc)

            elif item_type == DOCLING_TABLE_BLOCK_TYPE:
                result = await _handle_table_block(item, doc_dict, parent_index, ref_path,level,doc)
            else:
                self.logger.error(f"❌ Unknown item type: {item_type} {item}")
                return None

            return result

        # Start processing from body
        doc_dict = doc.export_to_dict()

        # Enrich every reachable table up front, concurrently, before the ordered walk
        # below touches anything. The walk itself (block/BlockGroup append order,
        # index=len(blocks) assignment) is unchanged - it just reads from this dict
        # instead of awaiting an LLM call inline per table.
        table_refs = collect_reachable_table_refs(doc_dict)
        llm = None
        table_enrichments: dict[str, TableEnrichmentResult] = {}
        if table_refs:
            grids = []
            valid_refs = []
            for ref_path in table_refs:
                table_item = _resolve_ref(doc_dict, ref_path)
                if table_item is None:
                    continue
                table_data = table_item.get("data", {})
                grid = table_data.get("grid", [])
                if not table_data.get("table_cells") or not grid:
                    continue
                grids.append(grid)
                valid_refs.append(ref_path)

            if grids:
                if skip_table_enrichment:
                    results = [simple_table_enrichment(grid) for grid in grids]
                else:
                    llm, _ = await get_llm_for_role(self.config, "indexing", reasoning_effort="low")
                    results = await enrich_tables(
                        llm,
                        grids,
                        logger=self.logger,
                    )
                table_enrichments = dict(zip(valid_refs, results))

        body = doc_dict.get("body", {})
        for child in body.get("children", []):
            await _process_item(child,doc)

        self.logger.debug(f"Processed {len(blocks)} items in order")
        return BlocksContainer(blocks=blocks, block_groups=block_groups)

    async def convert(self, doc: DoclingDocument, page_number: int | None = None, skip_table_enrichment: bool = False) -> BlocksContainer|bool:
        label = f"docling-page-{page_number}" if page_number is not None else "docling"
        with track_indexing_enrichment(self.logger, label=label):
            return await self._process_content_in_order(doc, page_number=page_number, skip_table_enrichment=skip_table_enrichment)








