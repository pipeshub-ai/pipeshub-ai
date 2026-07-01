"""Unit tests for PDF processor modules.

Covers:
- PDFPlumberOpenCVProcessor (pdfplumber_opencv_processor.py)
- Layout helper math (types from opencv_layout_analyzer; DPI constant local)
- VLMOCRStrategy (vlm_ocr_strategy.py)
"""

import asyncio
import base64
from io import BytesIO
from typing import Tuple
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import numpy as np
import pytest

from app.exceptions.indexing_exceptions import DocumentProcessingError

from app.modules.parsers.pdf.opencv_layout_analyzer import (
    LayoutRegion,
    LayoutRegionType,
    _append_hyperlink_url_to_text,
    _inject_hyperlinks_into_text,
    extract_layout_regions,
)

# PDF typographic baseline: points per inch (pixel → PDF pt: px * DPI_SCALE / raster_dpi).
DPI_SCALE = 72

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_logger():
    return MagicMock()


def _mock_config():
    return AsyncMock()


# Legacy raster-analyzer helpers (production layout lives in opencv_layout_analyzer).
def _rect_area(bbox: Tuple[float, float, float, float]) -> float:
    return max(0, bbox[2] - bbox[0]) * max(0, bbox[3] - bbox[1])


def _overlap_ratio(
    a: Tuple[float, float, float, float],
    b: Tuple[float, float, float, float],
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
    if projection.size == 0:
        return 0
    transitions = np.diff(projection.astype(np.int8))
    rising_edges = int(np.sum(transitions == 1))
    return rising_edges + (1 if projection[0] else 0)


def _reading_order_key(region: LayoutRegion) -> Tuple[float, float]:
    return (region.bbox[1], region.bbox[0])


# ============================================================================
# OpenCV layout analyzer helpers (no external deps needed)
# ============================================================================

class TestRectArea:
    def test_positive_area(self):
        assert _rect_area((0, 0, 10, 20)) == 200

    def test_zero_area_collapsed(self):
        assert _rect_area((5, 5, 5, 10)) == 0

    def test_negative_coordinates_clamped(self):
        # x1 < x0 => max(0, ...) = 0
        assert _rect_area((10, 0, 5, 5)) == 0

    def test_zero_width(self):
        assert _rect_area((0, 0, 0, 10)) == 0

    def test_zero_height(self):
        assert _rect_area((0, 0, 10, 0)) == 0


class TestOverlapRatio:
    def test_full_overlap(self):
        a = (0, 0, 10, 10)
        b = (0, 0, 10, 10)
        assert _overlap_ratio(a, b) == 1.0

    def test_no_overlap(self):
        a = (0, 0, 5, 5)
        b = (10, 10, 20, 20)
        assert _overlap_ratio(a, b) == 0.0

    def test_partial_overlap(self):
        a = (0, 0, 10, 10)
        b = (5, 5, 15, 15)
        ratio = _overlap_ratio(a, b)
        # Intersection = 5*5 = 25, area_a = 100
        assert abs(ratio - 0.25) < 0.001

    def test_zero_area_a(self):
        assert _overlap_ratio((0, 0, 0, 0), (0, 0, 10, 10)) == 0.0

    def test_contained(self):
        a = (2, 2, 8, 8)
        b = (0, 0, 10, 10)
        # Intersection = 6*6 = 36, area_a = 36
        assert _overlap_ratio(a, b) == 1.0


class TestHyperlinkTextInjection:
    def test_append_hyperlink_url_to_text(self):
        text = "You can visit myLinked for more info."
        out = _append_hyperlink_url_to_text(
            text,
            "myLinked",
            "https://www.linkedin.com/in/example",
        )
        assert out == (
            "You can visit myLinked (https://www.linkedin.com/in/example) "
            "for more info."
        )

    def test_append_hyperlink_url_is_idempotent(self):
        text = "myLinked (https://example.com)"
        assert _append_hyperlink_url_to_text(text, "myLinked", "https://example.com") == text

    def test_inject_hyperlinks_into_text_from_word_boxes(self):
        region_words = [
            {"text": "You", "x0": 100, "x1": 120, "top": 80, "bottom": 95},
            {"text": "can", "x0": 125, "x1": 145, "top": 80, "bottom": 95},
            {"text": "visit", "x0": 150, "x1": 175, "top": 80, "bottom": 95},
            {"text": "myLinked", "x0": 180, "x1": 250, "top": 80, "bottom": 95},
        ]
        hyperlinks = [{
            "x0": 180,
            "top": 77,
            "x1": 250,
            "bottom": 97,
            "uri": "https://www.linkedin.com/in/example",
        }]
        text = "You can visit myLinked"
        out = _inject_hyperlinks_into_text(text, hyperlinks, region_words)
        assert out == "You can visit myLinked (https://www.linkedin.com/in/example)"

    def test_extract_layout_regions_includes_hyperlink_url(self):
        pytest.importorskip("reportlab")
        from io import BytesIO

        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        import pdfplumber

        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.drawString(100, 700, "You can visit ")
        c.linkURL(
            "https://www.linkedin.com/in/example",
            (180, 695, 250, 715),
            relative=0,
        )
        c.drawString(180, 700, "myLinked")
        c.save()

        with pdfplumber.open(BytesIO(buf.getvalue())) as pdf:
            regions = extract_layout_regions(pdf.pages[0])

        assert len(regions) == 1
        assert "myLinked (https://www.linkedin.com/in/example)" in regions[0].text


class TestPixelToPdf:
    def test_conversion(self):
        result = _pixel_to_pdf(150, 150)
        assert abs(result - 72.0) < 0.01

    def test_zero(self):
        assert _pixel_to_pdf(0, 150) == 0.0


class TestCountDistinctLines:
    def test_empty(self):
        assert _count_distinct_lines(np.array([], dtype=bool)) == 0

    def test_single_run(self):
        arr = np.array([False, True, True, True, False])
        assert _count_distinct_lines(arr) == 1

    def test_multiple_runs(self):
        arr = np.array([True, True, False, True, False, True])
        assert _count_distinct_lines(arr) == 3

    def test_starts_with_true(self):
        arr = np.array([True, False, True])
        assert _count_distinct_lines(arr) == 2

    def test_all_true(self):
        arr = np.array([True, True, True])
        assert _count_distinct_lines(arr) == 1

    def test_all_false(self):
        arr = np.array([False, False, False])
        assert _count_distinct_lines(arr) == 0


class TestReadingOrderKey:
    def test_sort_order(self):
        r1 = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(0, 10, 50, 20))
        r2 = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(0, 5, 50, 15))
        regions = [r1, r2]
        regions.sort(key=_reading_order_key)
        # r2 has y0=5, should come first
        assert regions[0] is r2

    def test_same_y_sort_by_x(self):
        r1 = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(20, 5, 50, 15))
        r2 = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(5, 5, 50, 15))
        regions = [r1, r2]
        regions.sort(key=_reading_order_key)
        assert regions[0] is r2


# ============================================================================
# OpenCVLayoutAnalyzer (PyMuPDF raster path removed from opencv_layout_analyzer stub)
# ============================================================================

@pytest.mark.skip(reason="OpenCVLayoutAnalyzer not in stub; layout uses opencv_layout_analyzer.")
class TestOpenCVLayoutAnalyzer:
    def _make_analyzer(self):
        from app.modules.parsers.pdf.opencv_layout_analyzer import OpenCVLayoutAnalyzer
        return OpenCVLayoutAnalyzer(logger=_mock_logger(), render_dpi=150)

    def test_init(self):
        analyzer = self._make_analyzer()
        assert analyzer.render_dpi == 150

    @patch("app.modules.parsers.pdf.opencv_layout_analyzer.cv2")
    @patch("app.modules.parsers.pdf.opencv_layout_analyzer.fitz")
    def test_render_page_to_image(self, mock_fitz, mock_cv2):
        import numpy as np
        analyzer = self._make_analyzer()
        mock_page = MagicMock()
        mock_pix = MagicMock()
        mock_pix.height = 100
        mock_pix.width = 80
        mock_pix.samples = np.zeros(100 * 80 * 3, dtype=np.uint8).tobytes()
        mock_page.get_pixmap.return_value = mock_pix
        result = analyzer._render_page_to_image(mock_page)
        assert result.shape == (100, 80, 3)

    @patch("app.modules.parsers.pdf.opencv_layout_analyzer.cv2")
    def test_preprocess(self, mock_cv2):
        import numpy as np
        analyzer = self._make_analyzer()
        img = np.zeros((100, 80, 3), dtype=np.uint8)
        mock_cv2.cvtColor.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.adaptiveThreshold.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.morphologyEx.return_value = np.zeros((100, 80), dtype=np.uint8)
        result = analyzer._preprocess(img)
        mock_cv2.cvtColor.assert_called_once()
        mock_cv2.adaptiveThreshold.assert_called_once()

    def test_compute_median_font_size_empty(self):
        analyzer = self._make_analyzer()
        assert analyzer._compute_median_font_size({}) == 12.0

    def test_compute_median_font_size_with_data(self):
        analyzer = self._make_analyzer()
        text_dict = {
            "blocks": [{
                "type": 0,
                "lines": [
                    {"spans": [{"size": 10}, {"size": 14}]},
                    {"spans": [{"size": 12}]},
                ],
            }]
        }
        result = analyzer._compute_median_font_size(text_dict)
        assert result == 12.0

    def test_compute_median_font_size_even_count(self):
        analyzer = self._make_analyzer()
        text_dict = {
            "blocks": [{
                "type": 0,
                "lines": [
                    {"spans": [{"size": 10}, {"size": 20}]},
                ],
            }]
        }
        result = analyzer._compute_median_font_size(text_dict)
        assert result == 15.0

    def test_extract_text_and_metadata(self):
        analyzer = self._make_analyzer()
        blocks = [{
            "lines": [{
                "spans": [
                    {"text": "Hello world", "size": 12, "flags": 0},
                ]
            }]
        }]
        text, avg_size, is_bold = analyzer._extract_text_and_metadata(blocks)
        assert text == "Hello world"
        assert avg_size == 12.0
        assert is_bold is False

    def test_extract_text_bold_flag(self):
        analyzer = self._make_analyzer()
        blocks = [{
            "lines": [{
                "spans": [{"text": "Bold", "size": 14, "flags": 0b10000}]
            }]
        }]
        _, _, is_bold = analyzer._extract_text_and_metadata(blocks)
        assert is_bold is True

    def test_extract_text_empty_blocks(self):
        analyzer = self._make_analyzer()
        text, avg, bold = analyzer._extract_text_and_metadata([])
        assert text == ""
        assert avg == 0
        assert bold is False

    def test_get_text_blocks_for_region(self):
        analyzer = self._make_analyzer()
        text_dict = {
            "blocks": [
                {"type": 0, "bbox": (10, 10, 50, 50)},
                {"type": 0, "bbox": (200, 200, 300, 300)},
                {"type": 1, "bbox": (10, 10, 50, 50)},  # image block, skipped
            ]
        }
        matched = analyzer._get_text_blocks_for_region((10, 10, 50, 50), text_dict)
        assert len(matched) == 1

    def test_classify_list_type_bullet(self):
        from app.modules.parsers.pdf.opencv_layout_analyzer import LayoutRegionType
        analyzer = self._make_analyzer()
        text = "- Item one\n- Item two\n- Item three"
        result = analyzer._classify_list_type(text)
        assert result == LayoutRegionType.LIST

    def test_classify_list_type_ordered(self):
        from app.modules.parsers.pdf.opencv_layout_analyzer import LayoutRegionType
        analyzer = self._make_analyzer()
        text = "1. First\n2. Second\n3. Third"
        result = analyzer._classify_list_type(text)
        assert result == LayoutRegionType.ORDERED_LIST

    def test_classify_list_type_not_list(self):
        analyzer = self._make_analyzer()
        text = "Just a normal paragraph of text without list markers."
        result = analyzer._classify_list_type(text)
        assert result is None

    def test_classify_list_type_too_few_lines(self):
        analyzer = self._make_analyzer()
        text = "- Only one item"
        result = analyzer._classify_list_type(text)
        assert result is None

    @patch("app.modules.parsers.pdf.opencv_layout_analyzer.cv2")
    def test_detect_table_regions_returns_list(self, mock_cv2):
        import numpy as np
        analyzer = self._make_analyzer()
        binary = np.zeros((500, 400), dtype=np.uint8)
        mock_cv2.getStructuringElement.return_value = np.ones((1, 10), dtype=np.uint8)
        mock_cv2.morphologyEx.return_value = np.zeros((500, 400), dtype=np.uint8)
        mock_cv2.add.return_value = np.zeros((500, 400), dtype=np.uint8)
        mock_cv2.dilate.return_value = np.zeros((500, 400), dtype=np.uint8)
        mock_cv2.findContours.return_value = ([], None)
        result = analyzer._detect_table_regions(binary, 400.0, 500.0)
        assert isinstance(result, list)

    @patch("app.modules.parsers.pdf.opencv_layout_analyzer.cv2")
    def test_detect_text_regions_returns_list(self, mock_cv2):
        import numpy as np
        analyzer = self._make_analyzer()
        binary = np.zeros((500, 400), dtype=np.uint8)
        mock_cv2.getStructuringElement.return_value = np.ones((3, 10), dtype=np.uint8)
        mock_cv2.dilate.return_value = np.zeros((500, 400), dtype=np.uint8)
        mock_cv2.findContours.return_value = ([], None)
        result = analyzer._detect_text_regions(binary, [], 400.0, 500.0)
        assert isinstance(result, list)

    def test_extract_image_regions(self):
        analyzer = self._make_analyzer()
        mock_page = MagicMock()
        mock_page.get_images.return_value = []
        result = analyzer._extract_image_regions(mock_page, [], 400.0, 500.0)
        assert result == []

    def test_collect_unclaimed_text_blocks_empty(self):
        analyzer = self._make_analyzer()
        text_dict = {"blocks": []}
        regions = []
        analyzer._collect_unclaimed_text_blocks(text_dict, regions, [], [], 400.0, 500.0)
        assert regions == []

    def test_collect_unclaimed_text_blocks_claimed(self):
        from app.modules.parsers.pdf.opencv_layout_analyzer import LayoutRegion, LayoutRegionType
        analyzer = self._make_analyzer()
        text_dict = {
            "blocks": [{
                "type": 0,
                "bbox": (10, 10, 50, 50),
                "lines": [{"spans": [{"text": "Hello", "size": 12, "flags": 0}]}],
            }]
        }
        # Region that overlaps the block
        existing = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(10, 10, 50, 50))
        regions = [existing]
        analyzer._collect_unclaimed_text_blocks(text_dict, regions, [], [], 400.0, 500.0)
        # Block is claimed, no new region added
        assert len(regions) == 1


# ============================================================================
# PDFPlumberOpenCVProcessor
# ============================================================================

class TestPyMuPDFOpenCVProcessor:
    def _make_processor(self):
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import PDFPlumberOpenCVProcessor
        return PDFPlumberOpenCVProcessor(logger=_mock_logger(), config=_mock_config())

    def test_normalize_bbox_to_points(self):
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import _normalize_bbox_to_points
        points = _normalize_bbox_to_points((0, 0, 100, 200), 200.0, 400.0)
        assert len(points) == 4
        assert points[0].x == 0.0
        assert points[0].y == 0.0
        assert points[2].x == 0.5
        assert points[2].y == 0.5

    @pytest.mark.asyncio
    async def test_parse_document(self):
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import PDFPlumberOpenCVProcessor

        proc = PDFPlumberOpenCVProcessor(logger=_mock_logger(), config=_mock_config())

        mock_pdf = MagicMock()
        mock_page = MagicMock()
        mock_page.width = 612
        mock_page.height = 792
        mock_pdf.pages = [mock_page]
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_pdf
        mock_cm.__exit__.return_value = None

        with patch(
            "app.modules.parsers.pdf.pdfplumber_opencv_processor.pdfplumber.open",
            return_value=mock_cm,
        ), patch(
            "app.modules.parsers.pdf.pdfplumber_opencv_processor.extract_layout_regions",
            return_value=[],
        ):
            result = await proc.parse_document("test.pdf", b"fake-pdf-bytes")

        assert len(result) == 1
        assert result[0].page_number == 1
        assert result[0].width == 612
        assert result[0].height == 792

    def test_make_citation(self):
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import ParsedPageData, PDFPlumberOpenCVProcessor
        proc = PDFPlumberOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[])
        citation = proc._make_citation((0, 0, 306, 396), pd)
        assert citation.page_number == 1
        assert len(citation.bounding_boxes) == 4

    def test_build_text_block(self):
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PDFPlumberOpenCVProcessor,
        )
        proc = PDFPlumberOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
        region = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(0, 0, 100, 50), text="Hello world")
        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[])
        blocks = []
        block = proc._build_text_block(region, pd, blocks)
        assert block.type.value == "text"
        assert block.data == "Hello world"
        assert len(blocks) == 1

    def test_build_text_block_explicit_heading_subtype(self):
        """_build_text_block still accepts an explicit heading subtype for callers."""
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PDFPlumberOpenCVProcessor,
        )
        from app.models.blocks import BlockSubType
        proc = PDFPlumberOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
        region = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(0, 0, 100, 50), text="Title")
        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[])
        blocks = []
        block = proc._build_text_block(region, pd, blocks, sub_type=BlockSubType.HEADING)
        assert block.sub_type == BlockSubType.HEADING

    def test_build_image_block_no_data(self):
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PDFPlumberOpenCVProcessor,
        )
        proc = PDFPlumberOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
        region = LayoutRegion(type=LayoutRegionType.IMAGE, bbox=(0, 0, 100, 100), image_data=None)
        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[])
        blocks = []
        result = proc._build_image_block(region, pd, blocks)
        assert result is None
        assert len(blocks) == 0

    def test_build_image_block_with_data(self):
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PDFPlumberOpenCVProcessor,
        )
        proc = PDFPlumberOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
        region = LayoutRegion(
            type=LayoutRegionType.IMAGE, bbox=(0, 0, 100, 100),
            image_data=b"fake-image-data", image_ext="png",
        )
        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[])
        blocks = []
        block = proc._build_image_block(region, pd, blocks)
        assert block is not None
        assert block.type.value == "image"
        assert "base64" in block.data["uri"]

    def test_build_list_group_unordered(self):
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PDFPlumberOpenCVProcessor,
        )
        proc = PDFPlumberOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
        region = LayoutRegion(
            type=LayoutRegionType.LIST, bbox=(0, 0, 200, 100),
            text="- Item A\n- Item B", list_items=["- Item A", "- Item B"],
        )
        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[])
        blocks = []
        block_groups = []
        bg = proc._build_list_group(region, pd, blocks, block_groups)
        assert bg.type.value == "list"
        assert len(blocks) == 2
        assert len(block_groups) == 1

    def test_build_list_group_ordered_items_use_list_group(self):
        """Numbered list lines are emitted as ``GroupType.LIST`` (no separate ordered type)."""
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PDFPlumberOpenCVProcessor,
        )
        proc = PDFPlumberOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
        region = LayoutRegion(
            type=LayoutRegionType.LIST, bbox=(0, 0, 200, 100),
            text="1. First\n2. Second", list_items=["1. First", "2. Second"],
        )
        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[])
        blocks = []
        block_groups = []
        bg = proc._build_list_group(region, pd, blocks, block_groups)
        assert bg.type.value == "list"

    @pytest.mark.asyncio
    async def test_build_table_group(self):
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PDFPlumberOpenCVProcessor,
        )
        proc = PDFPlumberOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
        region = LayoutRegion(
            type=LayoutRegionType.TABLE, bbox=(0, 0, 200, 100),
            table_grid=[["A", "B"], ["1", "2"]],
        )
        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[])
        blocks = []
        block_groups = []

        mock_response = MagicMock()
        mock_response.summary = "Table summary"
        mock_response.headers = ["A", "B"]

        with patch("app.modules.parsers.pdf.pdfplumber_opencv_processor.get_table_summary_n_headers",
                    new_callable=AsyncMock, return_value=mock_response), \
             patch("app.modules.parsers.pdf.pdfplumber_opencv_processor.get_rows_text",
                    new_callable=AsyncMock, return_value=(["Row 1 text"], [["1", "2"]])):
            bg = await proc._build_table_group(region, pd, blocks, block_groups)

        assert bg is not None
        assert bg.type.value == "table"
        assert len(blocks) == 1  # one row

    @pytest.mark.asyncio
    async def test_build_table_group_no_grid(self):
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PDFPlumberOpenCVProcessor,
        )
        proc = PDFPlumberOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
        region = LayoutRegion(type=LayoutRegionType.TABLE, bbox=(0, 0, 200, 100), table_grid=None)
        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[])
        result = await proc._build_table_group(region, pd, [], [])
        assert result is None

    @pytest.mark.asyncio
    async def test_create_blocks_filters_by_page(self):
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PDFPlumberOpenCVProcessor,
        )
        proc = PDFPlumberOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
        r1 = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(0, 0, 100, 50), text="Page 1 text")
        r2 = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(0, 0, 100, 50), text="Page 2 text")
        page1 = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[r1])
        page2 = ParsedPageData(page_number=2, width=612.0, height=792.0, regions=[r2])
        result = await proc.create_blocks([page1, page2], page_number=1)
        assert len(result.blocks) == 1
        assert result.blocks[0].data == "Page 1 text"

class TestVLMOCRStrategy:
    def _make_strategy(self):
        try:
            from app.modules.parsers.pdf.vlm_ocr_strategy import VLMOCRStrategy
        except (ImportError, AttributeError):
            pytest.skip("VLMOCRStrategy not importable in this environment")

        with patch.object(VLMOCRStrategy, "__init__", return_value=None):
            strategy = VLMOCRStrategy.__new__(VLMOCRStrategy)
            strategy.logger = _mock_logger()
            strategy.config = _mock_config()
            strategy.doc = None
            strategy.llm = None
            strategy.llm_config = None
            strategy.document_analysis_result = None
            strategy.RENDER_DPI = 200
            strategy.DEFAULT_PROMPT = "Convert to markdown."
            strategy.CONCURRENCY_LIMIT = 10
            strategy.MAX_RETRY_ATTEMPTS = 2
            return strategy

    def test_render_all_pages_to_base64(self):
        strategy = self._make_strategy()
        strategy._pdf_path = "/tmp/test.pdf"
        mock_img = MagicMock()
        mock_img.save.side_effect = lambda buf, format=None: buf.write(b"fake-png-bytes")

        with patch(
            "app.modules.parsers.pdf.vlm_ocr_strategy.render_all_pages_from_path_sync",
            return_value={1: (np.zeros((10, 10, 3), dtype=np.uint8), 200 / 72.0)},
        ), patch(
            "app.modules.parsers.pdf.vlm_ocr_strategy.Image.fromarray",
            return_value=mock_img,
        ):
            result = strategy._render_all_pages_to_base64()

        assert result[1].startswith("data:image/png;base64,")

    @pytest.mark.asyncio
    async def test_call_llm_for_markdown(self):
        strategy = self._make_strategy()
        mock_response = MagicMock()
        mock_response.content = "# Heading\n\nSome text"
        strategy.llm = AsyncMock()
        strategy.llm.ainvoke = AsyncMock(return_value=mock_response)

        result = await strategy._call_llm_for_markdown("data:image/png;base64,abc", 1)
        assert result == "# Heading\n\nSome text"

    @pytest.mark.asyncio
    async def test_call_llm_strips_markdown_fence(self):
        strategy = self._make_strategy()
        mock_response = MagicMock()
        mock_response.content = "```markdown\n# Title\n```"
        strategy.llm = AsyncMock()
        strategy.llm.ainvoke = AsyncMock(return_value=mock_response)

        result = await strategy._call_llm_for_markdown("data:image/png;base64,abc", 1)
        assert result == "# Title"

    @pytest.mark.asyncio
    async def test_call_llm_strips_generic_fence(self):
        strategy = self._make_strategy()
        mock_response = MagicMock()
        mock_response.content = "```\n# Title\n```"
        strategy.llm = AsyncMock()
        strategy.llm.ainvoke = AsyncMock(return_value=mock_response)

        result = await strategy._call_llm_for_markdown("data:image/png;base64,abc", 1)
        assert result == "# Title"

    @pytest.mark.asyncio
    async def test_process_page(self):
        strategy = self._make_strategy()
        strategy._page_images = {1: "data:image/png;base64,abc"}
        strategy._call_llm_for_markdown = AsyncMock(return_value="# Page 1")

        mock_page = MagicMock()
        mock_page.page_number = 1
        mock_page.width = 612
        mock_page.height = 792

        result = await strategy.process_page(mock_page)
        assert result["page_number"] == 1
        assert result["markdown"] == "# Page 1"

    @pytest.mark.asyncio
    async def test_process_page_error(self):
        strategy = self._make_strategy()
        strategy._page_images = {}

        mock_page = MagicMock()
        mock_page.page_number = 1

        with pytest.raises(KeyError):
            await strategy.process_page(mock_page)

    def test_create_llm_from_config(self):
        strategy = self._make_strategy()
        config = {
            "provider": "openai",
            "configuration": {"model": "gpt-4o"},
        }
        with patch("app.modules.parsers.pdf.vlm_ocr_strategy.get_generator_model") as mock_get:
            mock_get.return_value = MagicMock()
            result = strategy._create_llm_from_config(config)
            mock_get.assert_called_once_with("openai", config, "gpt-4o")
            assert strategy.llm_config == config

    def test_create_llm_from_config_no_model(self):
        strategy = self._make_strategy()
        config = {"provider": "openai", "configuration": {}}
        with patch("app.modules.parsers.pdf.vlm_ocr_strategy.get_generator_model") as mock_get:
            mock_get.return_value = MagicMock()
            strategy._create_llm_from_config(config)
            mock_get.assert_called_once_with("openai", config, None)

    @pytest.mark.asyncio
    async def test_get_multimodal_llm_default(self):
        strategy = self._make_strategy()
        strategy.config.get_config = AsyncMock(return_value={
            "llm": [{"provider": "openai", "isDefault": True, "configuration": {"model": "gpt-4o"}}]
        })
        with patch("app.modules.parsers.pdf.vlm_ocr_strategy.is_multimodal_llm", return_value=True), \
             patch("app.modules.parsers.pdf.vlm_ocr_strategy.get_generator_model") as mock_get:
            mock_get.return_value = MagicMock()
            result = await strategy._get_multimodal_llm()
            assert result is not None

    @pytest.mark.asyncio
    async def test_get_multimodal_llm_no_configs(self):
        strategy = self._make_strategy()
        strategy.config.get_config = AsyncMock(return_value={"llm": []})
        with pytest.raises(DocumentProcessingError, match="No LLM configurations"):
            await strategy._get_multimodal_llm()

    @pytest.mark.asyncio
    async def test_get_multimodal_llm_no_multimodal(self):
        strategy = self._make_strategy()
        strategy.config.get_config = AsyncMock(return_value={
            "llm": [{"provider": "openai", "isDefault": True, "configuration": {"model": "gpt-3.5"}}]
        })
        with patch("app.modules.parsers.pdf.vlm_ocr_strategy.is_multimodal_llm", return_value=False):
            with pytest.raises(DocumentProcessingError, match="No multimodal LLM found"):
                await strategy._get_multimodal_llm()

    @pytest.mark.asyncio
    async def test_get_multimodal_llm_fallback_to_first(self):
        strategy = self._make_strategy()
        strategy.config.get_config = AsyncMock(return_value={
            "llm": [
                {"provider": "openai", "isDefault": True, "configuration": {"model": "gpt-3.5"}},
                {"provider": "anthropic", "isDefault": False, "configuration": {"model": "claude-3"}},
            ]
        })
        call_count = [0]

        def mock_is_multimodal(config):
            call_count[0] += 1
            return config["provider"] == "anthropic"

        with patch("app.modules.parsers.pdf.vlm_ocr_strategy.is_multimodal_llm", side_effect=mock_is_multimodal), \
             patch("app.modules.parsers.pdf.vlm_ocr_strategy.get_generator_model") as mock_get:
            mock_get.return_value = MagicMock()
            result = await strategy._get_multimodal_llm()
            assert result is not None

    @pytest.mark.asyncio
    async def test_preprocess_document(self):
        strategy = self._make_strategy()
        mock_page1 = MagicMock()

        strategy.doc = MagicMock()
        strategy.doc.pages = [mock_page1]

        strategy.process_page = AsyncMock(return_value={
            "page_number": 1,
            "markdown": "# Page 1",
            "width": 612,
            "height": 792,
        })
        strategy._preload_page_images = AsyncMock()

        result = await strategy._preprocess_document()
        assert result["total_pages"] == 1
        assert "# Page 1" in result["markdown"]

    @pytest.mark.asyncio
    async def test_load_document(self):
        strategy = self._make_strategy()
        strategy._get_multimodal_llm = AsyncMock(return_value=MagicMock())
        strategy._preprocess_document = AsyncMock(return_value={"pages": [], "markdown": "", "total_pages": 1})

        mock_doc = MagicMock()
        mock_doc.pages = [MagicMock()]

        with patch(
            "app.modules.parsers.pdf.vlm_ocr_strategy.pdfplumber.open",
            return_value=mock_doc,
        ):
            await strategy.load_document(b"fake-pdf")

        mock_doc.close.assert_called_once()
        assert strategy.document_analysis_result is not None
