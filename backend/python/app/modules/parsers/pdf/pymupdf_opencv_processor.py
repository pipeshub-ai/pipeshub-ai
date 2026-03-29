from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from dataclasses import dataclass
from io import BytesIO
from typing import List, Optional, Tuple

import fitz

from app.config.configuration_service import ConfigurationService
from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockSubType,
    BlockType,
    CitationMetadata,
    DataFormat,
    GroupType,
    ImageMetadata,
    ListMetadata,
    Point,
    TableMetadata,
)
from app.modules.parsers.pdf.opencv_layout_analyzer import (
    BLOCK_MATCH_OVERLAP_THRESHOLD,
    DEFAULT_RENDER_DPI,
    LayoutRegion,
    LayoutRegionType,
    OpenCVLayoutAnalyzer,
    _overlap_ratio,
    _reading_order_key,
)
from app.utils.indexing_helpers import get_rows_text, get_table_summary_n_headers


def _normalize_bbox_to_points(
    bbox: Tuple[float, float, float, float],
    page_width: float,
    page_height: float,
) -> List[Point]:
    x0, y0, x1, y1 = bbox
    return [
        Point(x=x0 / page_width, y=y0 / page_height),
        Point(x=x1 / page_width, y=y0 / page_height),
        Point(x=x1 / page_width, y=y1 / page_height),
        Point(x=x0 / page_width, y=y1 / page_height),
    ]


@dataclass
class ParsedPageData:
    page_number: int
    width: float
    height: float
    regions: List[LayoutRegion]


class PyMuPDFOpenCVProcessor:
    """PDF parser combining PyMuPDF text extraction with OpenCV layout analysis.

    Uses OpenCV morphological operations for table detection, text region grouping,
    and heading/list heuristics. No ML models required.
    """

    def __init__(
        self,
        logger: logging.Logger,
        config: ConfigurationService,
        render_dpi: int = DEFAULT_RENDER_DPI,
    ) -> None:
        self.logger = logger
        self.config = config
        self.render_dpi = render_dpi
        self._analyzer = OpenCVLayoutAnalyzer(logger, render_dpi)

    async def parse_document(
        self, doc_name: str, content: bytes | BytesIO
    ) -> List[ParsedPageData]:
        stream = content if isinstance(content, BytesIO) else BytesIO(content)
        doc = fitz.open(stream=stream.read(), filetype="pdf")

        try:
            pages_data: List[ParsedPageData] = []
            for page_idx in range(len(doc)):
                page = doc[page_idx]
                regions = await asyncio.to_thread(self._analyzer.analyze_page, page)
                pages_data.append(
                    ParsedPageData(
                        page_number=page_idx + 1,
                        width=page.rect.width,
                        height=page.rect.height,
                        regions=regions,
                    )
                )
                self.logger.debug(
                    f"Page {page_idx + 1}: detected {len(regions)} layout regions"
                )

            await asyncio.to_thread(self._extract_tables_with_pymupdf, doc, pages_data)
            return pages_data
        finally:
            doc.close()

    def _extract_tables_with_pymupdf(
        self, doc: fitz.Document, pages_data: List[ParsedPageData]
    ) -> None:
        """Use PyMuPDF's built-in table finder to populate table grids."""
        for pd in pages_data:
            page = doc[pd.page_number - 1]
            try:
                table_finder = page.find_tables()
            except Exception as e:
                self.logger.debug(f"find_tables failed on page {pd.page_number}: {e}")
                continue

            for table in table_finder.tables:
                t_bbox = (table.bbox[0], table.bbox[1], table.bbox[2], table.bbox[3])
                best_region: Optional[LayoutRegion] = None
                best_overlap = 0.0
                for region in pd.regions:
                    if region.type != LayoutRegionType.TABLE:
                        continue
                    ov = _overlap_ratio(t_bbox, region.bbox)
                    if ov > best_overlap:
                        best_overlap = ov
                        best_region = region

                grid = table.extract()
                if best_region and best_overlap > BLOCK_MATCH_OVERLAP_THRESHOLD:
                    best_region.table_grid = grid
                    best_region.bbox = t_bbox
                else:
                    pd.regions.append(
                        LayoutRegion(
                            type=LayoutRegionType.TABLE,
                            bbox=t_bbox,
                            table_grid=grid,
                        )
                    )

            pd.regions.sort(key=_reading_order_key)

    async def create_blocks(
        self,
        parsed_data: List[ParsedPageData],
        page_number: Optional[int] = None,
    ) -> BlocksContainer:
        blocks: List[Block] = []
        block_groups: List[BlockGroup] = []

        for pd in parsed_data:
            if page_number is not None and pd.page_number != page_number:
                continue

            for region in pd.regions:
                if region.type == LayoutRegionType.TABLE:
                    await self._build_table_group(
                        region, pd, blocks, block_groups
                    )
                elif region.type == LayoutRegionType.IMAGE:
                    self._build_image_block(region, pd, blocks)
                elif region.type in (LayoutRegionType.LIST, LayoutRegionType.ORDERED_LIST):
                    self._build_list_group(region, pd, blocks, block_groups)
                elif region.type == LayoutRegionType.HEADING:
                    self._build_text_block(
                        region, pd, blocks, sub_type=BlockSubType.HEADING
                    )
                else:
                    self._build_text_block(region, pd, blocks)

        return BlocksContainer(blocks=blocks, block_groups=block_groups)

    def _make_citation(
        self, bbox: Tuple[float, float, float, float], page_data: ParsedPageData
    ) -> CitationMetadata:
        points = _normalize_bbox_to_points(bbox, page_data.width, page_data.height)
        return CitationMetadata(
            page_number=page_data.page_number,
            bounding_boxes=points,
        )

    def _build_text_block(
        self,
        region: LayoutRegion,
        page_data: ParsedPageData,
        blocks: List[Block],
        sub_type: Optional[BlockSubType] = None,
    ) -> Block:
        block = Block(
            id=str(uuid.uuid4()),
            index=len(blocks),
            type=BlockType.TEXT,
            sub_type=sub_type,
            format=DataFormat.TXT,
            data=region.text,
            comments=[],
            citation_metadata=self._make_citation(region.bbox, page_data),
        )
        blocks.append(block)
        return block

    def _build_image_block(
        self,
        region: LayoutRegion,
        page_data: ParsedPageData,
        blocks: List[Block],
    ) -> Optional[Block]:
        if not region.image_data:
            return None

        mime = f"image/{region.image_ext}"
        b64 = base64.b64encode(region.image_data).decode("utf-8")
        data_uri = f"data:{mime};base64,{b64}"

        block = Block(
            id=str(uuid.uuid4()),
            index=len(blocks),
            type=BlockType.IMAGE,
            format=DataFormat.BASE64,
            data={"uri": data_uri},
            comments=[],
            citation_metadata=self._make_citation(region.bbox, page_data),
            image_metadata=ImageMetadata(
                image_format=region.image_ext,
            ),
        )
        blocks.append(block)
        return block

    async def _build_table_group(
        self,
        region: LayoutRegion,
        page_data: ParsedPageData,
        blocks: List[Block],
        block_groups: List[BlockGroup],
    ) -> Optional[BlockGroup]:
        grid = region.table_grid
        if not grid or len(grid) == 0:
            return None

        response = await get_table_summary_n_headers(self.config, grid)
        table_summary = response.summary if response else ""
        column_headers = response.headers if response else []

        table_rows_text, table_rows = await get_rows_text(
            self.config,
            {"grid": grid},
            table_summary,
            column_headers,
        )

        num_rows = len(grid)
        num_cols = len(grid[0]) if grid else 0
        num_cells = num_rows * num_cols if num_cols else None

        citation = self._make_citation(region.bbox, page_data)

        bg = BlockGroup(
            id=str(uuid.uuid4()),
            index=len(block_groups),
            type=GroupType.TABLE,
            table_metadata=TableMetadata(
                num_of_rows=num_rows,
                num_of_cols=num_cols,
                num_of_cells=num_cells,
                has_header=bool(column_headers),
                column_names=column_headers or None,
            ),
            data={
                "table_summary": table_summary,
                "column_headers": column_headers,
            },
            format=DataFormat.JSON,
            citation_metadata=citation,
        )

        row_indices: List[int] = []
        for i, _row in enumerate(table_rows):
            idx = len(blocks)
            block = Block(
                id=str(uuid.uuid4()),
                index=idx,
                type=BlockType.TABLE_ROW,
                format=DataFormat.JSON,
                comments=[],
                parent_index=bg.index,
                data={
                    "row_natural_language_text": (
                        table_rows_text[i] if i < len(table_rows_text) else ""
                    ),
                    "row_number": i + 1,
                },
                citation_metadata=citation,
            )
            blocks.append(block)
            row_indices.append(idx)

        bg.children = BlockGroupChildren.from_indices(block_indices=row_indices)
        block_groups.append(bg)
        return bg

    def _build_list_group(
        self,
        region: LayoutRegion,
        page_data: ParsedPageData,
        blocks: List[Block],
        block_groups: List[BlockGroup],
    ) -> BlockGroup:
        group_type = (
            GroupType.ORDERED_LIST
            if region.type == LayoutRegionType.ORDERED_LIST
            else GroupType.LIST
        )

        bg = BlockGroup(
            id=str(uuid.uuid4()),
            index=len(block_groups),
            type=group_type,
            citation_metadata=self._make_citation(region.bbox, page_data),
            list_metadata=ListMetadata(
                list_style=(
                    "numbered" if group_type == GroupType.ORDERED_LIST else "bullet"
                ),
                item_count=len(region.list_items),
            ),
        )

        item_indices: List[int] = []
        for item_text in region.list_items:
            idx = len(blocks)
            block = Block(
                id=str(uuid.uuid4()),
                index=idx,
                type=BlockType.TEXT,
                sub_type=BlockSubType.LIST_ITEM,
                format=DataFormat.TXT,
                data=item_text,
                comments=[],
                parent_index=bg.index,
                citation_metadata=self._make_citation(region.bbox, page_data),
            )
            blocks.append(block)
            item_indices.append(idx)

        bg.children = BlockGroupChildren.from_indices(block_indices=item_indices)
        block_groups.append(bg)
        return bg

    async def load_document(
        self,
        doc_name: str,
        content: bytes | BytesIO,
        page_number: Optional[int] = None,
    ) -> BlocksContainer:
        parsed = await self.parse_document(doc_name, content)
        return await self.create_blocks(parsed, page_number=page_number)


