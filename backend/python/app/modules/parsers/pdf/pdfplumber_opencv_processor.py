from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from io import BytesIO
from typing import List, Optional, Tuple

import pdfplumber

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
    LayoutRegion,
    LayoutRegionType,
    extract_layout_regions,
)
from app.utils.indexing_helpers import (
    generate_simple_row_text,
    get_rows_text,
    get_table_summary_n_headers,
)


_NUMBERED_LIST_ITEM_RE = re.compile(
    r"^\s*(?:\(\s*\d+\s*\)|\d+[.)])\s+\S",
)


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


class PDFPlumberOpenCVProcessor:
    """PDF parser combining pdfplumber with OpenCV layout analysis (opencv_layout_analyzer).

    Uses pdfplumber for rasterization and table detection plus OpenCV segmentation for
    regions. No ML models required.
    """

    def __init__(
        self,
        logger: logging.Logger,
        config: ConfigurationService,
    ) -> None:
        self.logger = logger
        self.config = config

    async def parse_document(
        self, doc_name: str, content: bytes | BytesIO
    ) -> List[ParsedPageData]:
        stream = content if isinstance(content, BytesIO) else BytesIO(content)
        stream.seek(0)
        pdf_bytes = stream.read()

        def _parse_sync() -> List[ParsedPageData]:
            out: List[ParsedPageData] = []
            tmp_path: str | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=".pdf", prefix="pipeshub_pdf_"
                ) as tmp:
                    tmp_path = tmp.name
                    tmp.write(pdf_bytes)
                    tmp.flush()
                with pdfplumber.open(tmp_path) as pdf:
                    for page_idx, page in enumerate(pdf.pages):
                        regions = extract_layout_regions(page, pdf_path=tmp_path)
                        out.append(
                            ParsedPageData(
                                page_number=page_idx + 1,
                                width=float(page.width),
                                height=float(page.height),
                                regions=regions,
                            )
                        )
            finally:
                if tmp_path is not None:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
            return out

        pages_data = await asyncio.to_thread(_parse_sync)
        for pd in pages_data:
            self.logger.debug(
                f"Page {pd.page_number}: detected {len(pd.regions)} layout regions"
            )
        return pages_data

    async def create_blocks(
        self,
        parsed_data: List[ParsedPageData],
        page_number: Optional[int] = None,
        skip_llm_enrichment: bool = False,
    ) -> BlocksContainer:
        blocks: List[Block] = []
        block_groups: List[BlockGroup] = []

        for pd in parsed_data:
            if page_number is not None and pd.page_number != page_number:
                continue

            for region in pd.regions:
                if region.type == LayoutRegionType.TABLE:
                    await self._build_table_group(
                        region,
                        pd,
                        blocks,
                        block_groups,
                        skip_llm_enrichment=skip_llm_enrichment,
                    )
                elif region.type == LayoutRegionType.IMAGE:
                    self._build_image_block(region, pd, blocks)
                elif region.type == LayoutRegionType.LIST:
                    self._build_list_group(region, pd, blocks, block_groups)
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
                image_size={
                    "width": int(region.bbox[2] - region.bbox[0]),
                    "height": int(region.bbox[3] - region.bbox[1]),
                },
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
        skip_llm_enrichment: bool = False,
    ) -> Optional[BlockGroup]:
        grid = region.table_grid
        if not grid or len(grid) == 0:
            return None

        if skip_llm_enrichment:
            table_summary = ""
            column_headers: List[str] = []
            # Derive headers from the first row if it looks like a header row
            first_row = grid[0] if grid else []
            raw_headers = [
                (cell.get("text", "") if isinstance(cell, dict) else str(cell or ""))
                for cell in first_row
            ]
            data_rows = grid[1:] if grid else []
            table_rows_text = []
            for row in data_rows:
                row_dict = {
                    (raw_headers[i] if i < len(raw_headers) else f"Column_{i + 1}"): (
                        cell.get("text", "") if isinstance(cell, dict) else str(cell or "")
                    )
                    for i, cell in enumerate(row)
                }
                table_rows_text.append(generate_simple_row_text(row_dict))
            table_rows = data_rows
        else:
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
        group_type = GroupType.LIST

        bg = BlockGroup(
            id=str(uuid.uuid4()),
            index=len(block_groups),
            type=group_type,
            citation_metadata=self._make_citation(region.bbox, page_data),
            list_metadata=ListMetadata(
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

