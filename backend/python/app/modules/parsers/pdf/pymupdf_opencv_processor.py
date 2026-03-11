from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import cv2
import fitz
import numpy as np

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
from app.utils.indexing_helpers import get_rows_text, get_table_summary_n_headers

DEFAULT_RENDER_DPI = 150
DPI_SCALE = 72.0
MIN_TABLE_AREA_RATIO = 0.002
MAX_TABLE_AREA_RATIO = 0.7
MIN_IMAGE_AREA_RATIO = 0.003
MIN_TEXT_AREA_RATIO = 0.0005
HEADING_FONT_SIZE_RATIO = 1.3
BOLD_FLAG = 0b10000
OVERLAP_THRESHOLD = 0.5
TABLE_CELL_COUNT_THRESHOLD = 4
MIN_TABLE_GRID_LINES = 3
HORIZONTAL_KERNEL_SCALE = 40
VERTICAL_KERNEL_SCALE = 40
TEXT_DILATE_KERNEL_WIDTH_FRAC = 1 / 40
TEXT_DILATE_KERNEL_HEIGHT_FRAC = 1 / 150
BLOCK_MATCH_OVERLAP_THRESHOLD = 0.3
MIN_LIST_LINES = 2
LIST_BULLET_PATTERNS = ("•", "●", "○", "■", "□", "▪", "▫", "–", "—", "-")
ORDERED_LIST_PATTERN_CHARS = frozenset("0123456789.)")


class LayoutRegionType(str, Enum):
    TEXT = "text"
    HEADING = "heading"
    TABLE = "table"
    IMAGE = "image"
    LIST = "list"
    ORDERED_LIST = "ordered_list"


@dataclass
class LayoutRegion:
    type: LayoutRegionType
    bbox: Tuple[float, float, float, float]  # (x0, y0, x1, y1) in PDF points
    text: str = ""
    font_size: float = 0.0
    is_bold: bool = False
    image_data: Optional[bytes] = None
    image_ext: str = "png"
    table_grid: Optional[List[List[str]]] = None
    table_bbox_cells: Optional[List[Tuple[float, float, float, float]]] = None
    list_items: List[str] = field(default_factory=list)
    sub_regions: List[LayoutRegion] = field(default_factory=list)


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


def _rect_area(bbox: Tuple[float, float, float, float]) -> float:
    return max(0, bbox[2] - bbox[0]) * max(0, bbox[3] - bbox[1])


def _overlap_ratio(
    a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]
) -> float:
    ix0 = max(a[0], b[0])
    iy0 = max(a[1], b[1])
    ix1 = min(a[2], b[2])
    iy1 = min(a[3], b[3])
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    area_a = _rect_area(a)
    if area_a == 0:
        return 0.0
    return inter / area_a


def _pixel_to_pdf(val: float, dpi: int) -> float:
    return val * DPI_SCALE / dpi


def _count_distinct_lines(projection: np.ndarray) -> int:
    """Count distinct contiguous runs of True values in a 1-D boolean array."""
    if projection.size == 0:
        return 0
    transitions = np.diff(projection.astype(np.int8))
    rising_edges = int(np.sum(transitions == 1))
    return rising_edges + (1 if projection[0] else 0)


def _reading_order_key(region: LayoutRegion) -> Tuple[float, float]:
    return (region.bbox[1], region.bbox[0])


class OpenCVLayoutAnalyzer:
    """Lightweight ML-free layout analysis using OpenCV morphological operations."""

    def __init__(self, logger: logging.Logger, render_dpi: int = DEFAULT_RENDER_DPI) -> None:
        self.logger = logger
        self.render_dpi = render_dpi

    def _render_page_to_image(self, page: fitz.Page) -> np.ndarray:
        zoom = self.render_dpi / DPI_SCALE
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        return np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, 3
        )

    def _preprocess(self, img: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 10
        )
        kernel = np.ones((2, 2), np.uint8)
        return cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    def _detect_table_regions(
        self, binary: np.ndarray, page_width_pt: float, page_height_pt: float
    ) -> List[Tuple[float, float, float, float]]:
        h, w = binary.shape
        page_area = page_width_pt * page_height_pt

        horiz_kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT, (max(w // HORIZONTAL_KERNEL_SCALE, 1), 1)
        )
        horiz_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horiz_kernel)

        vert_kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT, (1, max(h // VERTICAL_KERNEL_SCALE, 1))
        )
        vert_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vert_kernel)

        grid_mask = cv2.add(horiz_lines, vert_lines)
        grid_mask = cv2.dilate(grid_mask, np.ones((3, 3), np.uint8), iterations=2)

        contours, _ = cv2.findContours(
            grid_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        table_rects: List[Tuple[float, float, float, float]] = []
        for cnt in contours:
            x, y, cw, ch = cv2.boundingRect(cnt)
            pdf_bbox = (
                _pixel_to_pdf(x, self.render_dpi),
                _pixel_to_pdf(y, self.render_dpi),
                _pixel_to_pdf(x + cw, self.render_dpi),
                _pixel_to_pdf(y + ch, self.render_dpi),
            )
            region_area = _rect_area(pdf_bbox)
            if region_area < page_area * MIN_TABLE_AREA_RATIO:
                continue

            # Reject regions covering most of the page (borders / frames)
            if region_area > page_area * MAX_TABLE_AREA_RATIO:
                self.logger.debug(
                    f"Skipping oversized table candidate "
                    f"({region_area / page_area:.0%} of page) — likely a border"
                )
                continue

            # Verify real grid structure: need multiple horizontal AND vertical
            # internal lines, not just a surrounding border.
            roi_h = horiz_lines[y : y + ch, x : x + cw]
            roi_v = vert_lines[y : y + ch, x : x + cw]

            h_projection = np.any(roi_h > 0, axis=1)
            v_projection = np.any(roi_v > 0, axis=0)
            num_h_lines = _count_distinct_lines(h_projection)
            num_v_lines = _count_distinct_lines(v_projection)

            if num_h_lines < MIN_TABLE_GRID_LINES or num_v_lines < MIN_TABLE_GRID_LINES:
                self.logger.debug(
                    f"Skipping table candidate with insufficient grid "
                    f"(h_lines={num_h_lines}, v_lines={num_v_lines})"
                )
                continue

            roi = grid_mask[y : y + ch, x : x + cw]
            inner_contours, _ = cv2.findContours(
                cv2.bitwise_not(roi), cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE
            )
            if len(inner_contours) >= TABLE_CELL_COUNT_THRESHOLD:
                table_rects.append(pdf_bbox)

        return table_rects

    def _detect_text_regions(
        self,
        binary: np.ndarray,
        table_rects: List[Tuple[float, float, float, float]],
        page_width_pt: float,
        page_height_pt: float,
    ) -> List[Tuple[float, float, float, float]]:
        h, w = binary.shape
        page_area = page_width_pt * page_height_pt
        dilate_w = max(int(w * TEXT_DILATE_KERNEL_WIDTH_FRAC), 5)
        dilate_h = max(int(h * TEXT_DILATE_KERNEL_HEIGHT_FRAC), 3)
        dilate_kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT, (dilate_w, dilate_h)
        )
        dilated = cv2.dilate(binary, dilate_kernel, iterations=2)

        contours, _ = cv2.findContours(
            dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        text_rects: List[Tuple[float, float, float, float]] = []
        for cnt in contours:
            x, y, cw, ch = cv2.boundingRect(cnt)
            pdf_bbox = (
                _pixel_to_pdf(x, self.render_dpi),
                _pixel_to_pdf(y, self.render_dpi),
                _pixel_to_pdf(x + cw, self.render_dpi),
                _pixel_to_pdf(y + ch, self.render_dpi),
            )
            if _rect_area(pdf_bbox) < page_area * MIN_TEXT_AREA_RATIO:
                continue
            in_table = any(
                _overlap_ratio(pdf_bbox, tr) > OVERLAP_THRESHOLD
                for tr in table_rects
            )
            if not in_table:
                text_rects.append(pdf_bbox)

        return text_rects

    def _extract_image_regions(
        self,
        page: fitz.Page,
        table_rects: List[Tuple[float, float, float, float]],
        page_width_pt: float,
        page_height_pt: float,
    ) -> List[Dict[str, Any]]:
        page_area = page_width_pt * page_height_pt
        images: List[Dict[str, Any]] = []
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            rects = page.get_image_rects(xref)
            if not rects:
                continue
            rect = rects[0]
            pdf_bbox = (rect.x0, rect.y0, rect.x1, rect.y1)
            if _rect_area(pdf_bbox) < page_area * MIN_IMAGE_AREA_RATIO:
                continue
            in_table = any(
                _overlap_ratio(pdf_bbox, tr) > OVERLAP_THRESHOLD
                for tr in table_rects
            )
            if in_table:
                continue
            try:
                base_image = page.parent.extract_image(xref)
                if base_image and base_image.get("image"):
                    images.append(
                        {
                            "bbox": pdf_bbox,
                            "data": base_image["image"],
                            "ext": base_image.get("ext", "png"),
                        }
                    )
            except Exception as e:
                self.logger.warning(f"Could not extract image xref={xref}: {e}")
        return images

    def _get_text_blocks_for_region(
        self,
        region_bbox: Tuple[float, float, float, float],
        text_dict: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        matched: List[Dict[str, Any]] = []
        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            bb = block.get("bbox")
            if not bb:
                continue
            block_bbox = (bb[0], bb[1], bb[2], bb[3])
            if _overlap_ratio(block_bbox, region_bbox) > BLOCK_MATCH_OVERLAP_THRESHOLD:
                matched.append(block)
        return matched

    def _extract_text_and_metadata(
        self, blocks: List[Dict[str, Any]]
    ) -> Tuple[str, float, bool]:
        texts: List[str] = []
        total_size = 0.0
        count = 0
        any_bold = False
        for block in blocks:
            for line in block.get("lines", []):
                line_text_parts: List[str] = []
                for span in line.get("spans", []):
                    span_text = span.get("text", "")
                    line_text_parts.append(span_text)
                    size = span.get("size", 0)
                    total_size += size
                    count += 1
                    if span.get("flags", 0) & BOLD_FLAG:
                        any_bold = True
                line_text = " ".join(line_text_parts).strip()
                if line_text:
                    texts.append(line_text)
        avg_size = total_size / count if count > 0 else 0
        return "\n".join(texts), avg_size, any_bold

    def _compute_median_font_size(self, text_dict: Dict[str, Any]) -> float:
        sizes: List[float] = []
        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    s = span.get("size", 0)
                    if s > 0:
                        sizes.append(s)
        if not sizes:
            return 12.0
        sizes.sort()
        mid = len(sizes) // 2
        if len(sizes) % 2 == 0:
            return (sizes[mid - 1] + sizes[mid]) / 2
        return sizes[mid]

    def _classify_list_type(self, text: str) -> Optional[LayoutRegionType]:
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        if len(lines) < MIN_LIST_LINES:
            return None
        bullet_count = sum(
            1 for line in lines if any(line.startswith(p) for p in LIST_BULLET_PATTERNS)
        )
        if bullet_count >= len(lines) * 0.6:
            return LayoutRegionType.LIST

        ordered_count = 0
        for line in lines:
            stripped = line.lstrip()
            if stripped and stripped[0].isdigit():
                j = 1
                while j < len(stripped) and stripped[j] in ORDERED_LIST_PATTERN_CHARS:
                    j += 1
                if j < len(stripped) and stripped[j] == " ":
                    ordered_count += 1
        if ordered_count >= len(lines) * 0.6:
            return LayoutRegionType.ORDERED_LIST
        return None

    def analyze_page(
        self, page: fitz.Page
    ) -> List[LayoutRegion]:
        page_w = page.rect.width
        page_h = page.rect.height

        img = self._render_page_to_image(page)
        binary = self._preprocess(img)

        table_rects = self._detect_table_regions(binary, page_w, page_h)
        text_rects = self._detect_text_regions(binary, table_rects, page_w, page_h)
        image_infos = self._extract_image_regions(page, table_rects, page_w, page_h)

        text_dict = page.get_text("dict")
        median_font_size = self._compute_median_font_size(text_dict)

        regions: List[LayoutRegion] = []

        regions.extend(
            LayoutRegion(type=LayoutRegionType.TABLE, bbox=tb) for tb in table_rects
        )

        for ib in image_infos:
            in_text = any(
                _overlap_ratio(ib["bbox"], tr) > OVERLAP_THRESHOLD
                for tr in text_rects
            )
            if not in_text:
                regions.append(
                    LayoutRegion(
                        type=LayoutRegionType.IMAGE,
                        bbox=ib["bbox"],
                        image_data=ib["data"],
                        image_ext=ib["ext"],
                    )
                )

        image_bboxes = [ib["bbox"] for ib in image_infos]
        for tr in text_rects:
            in_image = any(
                _overlap_ratio(tr, ib) > OVERLAP_THRESHOLD for ib in image_bboxes
            )
            if in_image:
                continue
            matched_blocks = self._get_text_blocks_for_region(tr, text_dict)
            if not matched_blocks:
                continue
            text, avg_size, is_bold = self._extract_text_and_metadata(matched_blocks)
            if not text.strip():
                continue

            list_type = self._classify_list_type(text)
            if list_type is not None:
                items = [ln.strip() for ln in text.split("\n") if ln.strip()]
                regions.append(
                    LayoutRegion(
                        type=list_type,
                        bbox=tr,
                        text=text,
                        font_size=avg_size,
                        is_bold=is_bold,
                        list_items=items,
                    )
                )
            elif avg_size >= median_font_size * HEADING_FONT_SIZE_RATIO or (
                is_bold and avg_size >= median_font_size * 1.1 and "\n" not in text.strip()
            ):
                regions.append(
                    LayoutRegion(
                        type=LayoutRegionType.HEADING,
                        bbox=tr,
                        text=text,
                        font_size=avg_size,
                        is_bold=is_bold,
                    )
                )
            else:
                regions.append(
                    LayoutRegion(
                        type=LayoutRegionType.TEXT,
                        bbox=tr,
                        text=text,
                        font_size=avg_size,
                        is_bold=is_bold,
                    )
                )

        self._collect_unclaimed_text_blocks(
            text_dict, regions, table_rects, image_bboxes, page_w, page_h
        )

        regions.sort(key=_reading_order_key)
        return regions

    def _collect_unclaimed_text_blocks(
        self,
        text_dict: Dict[str, Any],
        regions: List[LayoutRegion],
        table_rects: List[Tuple[float, float, float, float]],
        image_bboxes: List[Tuple[float, float, float, float]],
        page_w: float,
        page_h: float,
    ) -> None:
        """Pick up any PyMuPDF text blocks not covered by existing regions."""
        existing_bboxes = [r.bbox for r in regions]
        median_size = self._compute_median_font_size(text_dict)

        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            bb = block.get("bbox")
            if not bb:
                continue
            block_bbox = (bb[0], bb[1], bb[2], bb[3])

            claimed = any(
                _overlap_ratio(block_bbox, eb) > BLOCK_MATCH_OVERLAP_THRESHOLD for eb in existing_bboxes
            )
            if claimed:
                continue
            in_table = any(
                _overlap_ratio(block_bbox, tr) > OVERLAP_THRESHOLD for tr in table_rects
            )
            if in_table:
                continue
            in_image = any(
                _overlap_ratio(block_bbox, ib) > OVERLAP_THRESHOLD for ib in image_bboxes
            )
            if in_image:
                continue

            text, avg_size, is_bold = self._extract_text_and_metadata([block])
            if not text.strip():
                continue

            if avg_size >= median_size * HEADING_FONT_SIZE_RATIO or (
                is_bold and avg_size >= median_size * 1.1 and "\n" not in text.strip()
            ):
                rtype = LayoutRegionType.HEADING
            else:
                rtype = LayoutRegionType.TEXT

            regions.append(
                LayoutRegion(
                    type=rtype,
                    bbox=block_bbox,
                    text=text,
                    font_size=avg_size,
                    is_bold=is_bold,
                )
            )


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

            self._extract_tables_with_pymupdf(doc, pages_data)
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
            except Exception:
                self.logger.debug(f"find_tables failed on page {pd.page_number}")
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
