"""Unit tests for PDF processor modules.

Covers:
- AzureOCRStrategy (azure_document_intelligence_processor.py)
- PyMuPDFOCRStrategy (pymupdf_ocrmypdf_processor.py)
- PyMuPDFOpenCVProcessor (pymupdf_opencv_processor.py)
- OpenCVLayoutAnalyzer (opencv_layout_analyzer.py)
- VLMOCRStrategy (vlm_ocr_strategy.py)
"""

import asyncio
import base64
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_logger():
    return MagicMock()


def _mock_config():
    return AsyncMock()


# ============================================================================
# OpenCV layout analyzer helpers (no external deps needed)
# ============================================================================

class TestRectArea:
    def test_positive_area(self):
        from app.modules.parsers.pdf.opencv_layout_analyzer import _rect_area
        assert _rect_area((0, 0, 10, 20)) == 200

    def test_zero_area_collapsed(self):
        from app.modules.parsers.pdf.opencv_layout_analyzer import _rect_area
        assert _rect_area((5, 5, 5, 10)) == 0

    def test_negative_coordinates_clamped(self):
        from app.modules.parsers.pdf.opencv_layout_analyzer import _rect_area
        # x1 < x0 => max(0, ...) = 0
        assert _rect_area((10, 0, 5, 5)) == 0

    def test_zero_width(self):
        from app.modules.parsers.pdf.opencv_layout_analyzer import _rect_area
        assert _rect_area((0, 0, 0, 10)) == 0

    def test_zero_height(self):
        from app.modules.parsers.pdf.opencv_layout_analyzer import _rect_area
        assert _rect_area((0, 0, 10, 0)) == 0


class TestOverlapRatio:
    def test_full_overlap(self):
        from app.modules.parsers.pdf.opencv_layout_analyzer import _overlap_ratio
        a = (0, 0, 10, 10)
        b = (0, 0, 10, 10)
        assert _overlap_ratio(a, b) == 1.0

    def test_no_overlap(self):
        from app.modules.parsers.pdf.opencv_layout_analyzer import _overlap_ratio
        a = (0, 0, 5, 5)
        b = (10, 10, 20, 20)
        assert _overlap_ratio(a, b) == 0.0

    def test_partial_overlap(self):
        from app.modules.parsers.pdf.opencv_layout_analyzer import _overlap_ratio
        a = (0, 0, 10, 10)
        b = (5, 5, 15, 15)
        ratio = _overlap_ratio(a, b)
        # Intersection = 5*5 = 25, area_a = 100
        assert abs(ratio - 0.25) < 0.001

    def test_zero_area_a(self):
        from app.modules.parsers.pdf.opencv_layout_analyzer import _overlap_ratio
        assert _overlap_ratio((0, 0, 0, 0), (0, 0, 10, 10)) == 0.0

    def test_contained(self):
        from app.modules.parsers.pdf.opencv_layout_analyzer import _overlap_ratio
        a = (2, 2, 8, 8)
        b = (0, 0, 10, 10)
        # Intersection = 6*6 = 36, area_a = 36
        assert _overlap_ratio(a, b) == 1.0


class TestPixelToPdf:
    def test_conversion(self):
        from app.modules.parsers.pdf.opencv_layout_analyzer import _pixel_to_pdf
        result = _pixel_to_pdf(150, 150)
        assert abs(result - 72.0) < 0.01

    def test_zero(self):
        from app.modules.parsers.pdf.opencv_layout_analyzer import _pixel_to_pdf
        assert _pixel_to_pdf(0, 150) == 0.0


class TestCountDistinctLines:
    def test_empty(self):
        import numpy as np
        from app.modules.parsers.pdf.opencv_layout_analyzer import _count_distinct_lines
        assert _count_distinct_lines(np.array([], dtype=bool)) == 0

    def test_single_run(self):
        import numpy as np
        from app.modules.parsers.pdf.opencv_layout_analyzer import _count_distinct_lines
        arr = np.array([False, True, True, True, False])
        assert _count_distinct_lines(arr) == 1

    def test_multiple_runs(self):
        import numpy as np
        from app.modules.parsers.pdf.opencv_layout_analyzer import _count_distinct_lines
        arr = np.array([True, True, False, True, False, True])
        assert _count_distinct_lines(arr) == 3

    def test_starts_with_true(self):
        import numpy as np
        from app.modules.parsers.pdf.opencv_layout_analyzer import _count_distinct_lines
        arr = np.array([True, False, True])
        assert _count_distinct_lines(arr) == 2

    def test_all_true(self):
        import numpy as np
        from app.modules.parsers.pdf.opencv_layout_analyzer import _count_distinct_lines
        arr = np.array([True, True, True])
        assert _count_distinct_lines(arr) == 1

    def test_all_false(self):
        import numpy as np
        from app.modules.parsers.pdf.opencv_layout_analyzer import _count_distinct_lines
        arr = np.array([False, False, False])
        assert _count_distinct_lines(arr) == 0


class TestReadingOrderKey:
    def test_sort_order(self):
        from app.modules.parsers.pdf.opencv_layout_analyzer import (
            LayoutRegion,
            LayoutRegionType,
            _reading_order_key,
        )
        r1 = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(0, 10, 50, 20))
        r2 = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(0, 5, 50, 15))
        regions = [r1, r2]
        regions.sort(key=_reading_order_key)
        # r2 has y0=5, should come first
        assert regions[0] is r2

    def test_same_y_sort_by_x(self):
        from app.modules.parsers.pdf.opencv_layout_analyzer import (
            LayoutRegion,
            LayoutRegionType,
            _reading_order_key,
        )
        r1 = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(20, 5, 50, 15))
        r2 = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(5, 5, 50, 15))
        regions = [r1, r2]
        regions.sort(key=_reading_order_key)
        assert regions[0] is r2


# ============================================================================
# OpenCVLayoutAnalyzer
# ============================================================================

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
# PyMuPDFOpenCVProcessor
# ============================================================================

class TestPyMuPDFOpenCVProcessor:
    def _make_processor(self):
        with patch("app.modules.parsers.pdf.pymupdf_opencv_processor.OpenCVLayoutAnalyzer"):
            from app.modules.parsers.pdf.pymupdf_opencv_processor import PyMuPDFOpenCVProcessor
            return PyMuPDFOpenCVProcessor(logger=_mock_logger(), config=_mock_config())

    def test_normalize_bbox_to_points(self):
        from app.modules.parsers.pdf.pymupdf_opencv_processor import _normalize_bbox_to_points
        points = _normalize_bbox_to_points((0, 0, 100, 200), 200.0, 400.0)
        assert len(points) == 4
        assert points[0].x == 0.0
        assert points[0].y == 0.0
        assert points[2].x == 0.5
        assert points[2].y == 0.5

    @pytest.mark.asyncio
    async def test_parse_document(self):
        from app.modules.parsers.pdf.pymupdf_opencv_processor import PyMuPDFOpenCVProcessor
        with patch("app.modules.parsers.pdf.pymupdf_opencv_processor.OpenCVLayoutAnalyzer") as MockAnalyzer:
            mock_analyzer_inst = MagicMock()
            mock_analyzer_inst.analyze_page.return_value = []
            MockAnalyzer.return_value = mock_analyzer_inst

            proc = PyMuPDFOpenCVProcessor(logger=_mock_logger(), config=_mock_config())

            mock_doc = MagicMock()
            mock_doc.__len__ = lambda s: 1
            mock_page = MagicMock()
            mock_page.rect.width = 612
            mock_page.rect.height = 792
            mock_doc.__getitem__ = lambda s, i: mock_page
            mock_doc.close = MagicMock()

            with patch("app.modules.parsers.pdf.pymupdf_opencv_processor.fitz") as mock_fitz:
                mock_fitz.open.return_value = mock_doc
                result = await proc.parse_document("test.pdf", b"fake-pdf-bytes")

            assert len(result) == 1
            assert result[0].page_number == 1

    def test_make_citation(self):
        from app.modules.parsers.pdf.pymupdf_opencv_processor import ParsedPageData, PyMuPDFOpenCVProcessor
        with patch("app.modules.parsers.pdf.pymupdf_opencv_processor.OpenCVLayoutAnalyzer"):
            proc = PyMuPDFOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[])
        citation = proc._make_citation((0, 0, 306, 396), pd)
        assert citation.page_number == 1
        assert len(citation.bounding_boxes) == 4

    def test_build_text_block(self):
        from app.modules.parsers.pdf.pymupdf_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PyMuPDFOpenCVProcessor,
        )
        with patch("app.modules.parsers.pdf.pymupdf_opencv_processor.OpenCVLayoutAnalyzer"):
            proc = PyMuPDFOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
        region = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(0, 0, 100, 50), text="Hello world")
        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[])
        blocks = []
        block = proc._build_text_block(region, pd, blocks)
        assert block.type.value == "text"
        assert block.data == "Hello world"
        assert len(blocks) == 1

    def test_build_text_block_heading(self):
        from app.modules.parsers.pdf.pymupdf_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PyMuPDFOpenCVProcessor,
        )
        from app.models.blocks import BlockSubType
        with patch("app.modules.parsers.pdf.pymupdf_opencv_processor.OpenCVLayoutAnalyzer"):
            proc = PyMuPDFOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
        region = LayoutRegion(type=LayoutRegionType.HEADING, bbox=(0, 0, 100, 50), text="Title")
        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[])
        blocks = []
        block = proc._build_text_block(region, pd, blocks, sub_type=BlockSubType.HEADING)
        assert block.sub_type == BlockSubType.HEADING

    def test_build_image_block_no_data(self):
        from app.modules.parsers.pdf.pymupdf_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PyMuPDFOpenCVProcessor,
        )
        with patch("app.modules.parsers.pdf.pymupdf_opencv_processor.OpenCVLayoutAnalyzer"):
            proc = PyMuPDFOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
        region = LayoutRegion(type=LayoutRegionType.IMAGE, bbox=(0, 0, 100, 100), image_data=None)
        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[])
        blocks = []
        result = proc._build_image_block(region, pd, blocks)
        assert result is None
        assert len(blocks) == 0

    def test_build_image_block_with_data(self):
        from app.modules.parsers.pdf.pymupdf_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PyMuPDFOpenCVProcessor,
        )
        with patch("app.modules.parsers.pdf.pymupdf_opencv_processor.OpenCVLayoutAnalyzer"):
            proc = PyMuPDFOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
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
        from app.modules.parsers.pdf.pymupdf_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PyMuPDFOpenCVProcessor,
        )
        with patch("app.modules.parsers.pdf.pymupdf_opencv_processor.OpenCVLayoutAnalyzer"):
            proc = PyMuPDFOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
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

    def test_build_list_group_ordered(self):
        from app.modules.parsers.pdf.pymupdf_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PyMuPDFOpenCVProcessor,
        )
        with patch("app.modules.parsers.pdf.pymupdf_opencv_processor.OpenCVLayoutAnalyzer"):
            proc = PyMuPDFOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
        region = LayoutRegion(
            type=LayoutRegionType.ORDERED_LIST, bbox=(0, 0, 200, 100),
            text="1. First\n2. Second", list_items=["1. First", "2. Second"],
        )
        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[])
        blocks = []
        block_groups = []
        bg = proc._build_list_group(region, pd, blocks, block_groups)
        assert bg.type.value == "ordered_list"

    @pytest.mark.asyncio
    async def test_build_table_group(self):
        from app.modules.parsers.pdf.pymupdf_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PyMuPDFOpenCVProcessor,
        )
        with patch("app.modules.parsers.pdf.pymupdf_opencv_processor.OpenCVLayoutAnalyzer"):
            proc = PyMuPDFOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
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

        with patch("app.modules.parsers.pdf.pymupdf_opencv_processor.get_table_summary_n_headers",
                    new_callable=AsyncMock, return_value=mock_response), \
             patch("app.modules.parsers.pdf.pymupdf_opencv_processor.get_rows_text",
                    new_callable=AsyncMock, return_value=(["Row 1 text"], [["1", "2"]])):
            bg = await proc._build_table_group(region, pd, blocks, block_groups)

        assert bg is not None
        assert bg.type.value == "table"
        assert len(blocks) == 1  # one row

    @pytest.mark.asyncio
    async def test_build_table_group_no_grid(self):
        from app.modules.parsers.pdf.pymupdf_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PyMuPDFOpenCVProcessor,
        )
        with patch("app.modules.parsers.pdf.pymupdf_opencv_processor.OpenCVLayoutAnalyzer"):
            proc = PyMuPDFOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
        region = LayoutRegion(type=LayoutRegionType.TABLE, bbox=(0, 0, 200, 100), table_grid=None)
        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[])
        result = await proc._build_table_group(region, pd, [], [])
        assert result is None

    @pytest.mark.asyncio
    async def test_create_blocks_filters_by_page(self):
        from app.modules.parsers.pdf.pymupdf_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PyMuPDFOpenCVProcessor,
        )
        with patch("app.modules.parsers.pdf.pymupdf_opencv_processor.OpenCVLayoutAnalyzer"):
            proc = PyMuPDFOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
        r1 = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(0, 0, 100, 50), text="Page 1 text")
        r2 = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(0, 0, 100, 50), text="Page 2 text")
        page1 = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[r1])
        page2 = ParsedPageData(page_number=2, width=612.0, height=792.0, regions=[r2])
        result = await proc.create_blocks([page1, page2], page_number=1)
        assert len(result.blocks) == 1
        assert result.blocks[0].data == "Page 1 text"


# ============================================================================
# PyMuPDFOCRStrategy
# ============================================================================

class TestPyMuPDFOCRStrategy:
    @patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor.spacy")
    def _make_strategy(self, mock_spacy):
        mock_nlp = MagicMock()
        mock_nlp.pipe_names = []
        mock_nlp.add_pipe = MagicMock()
        mock_nlp.tokenizer = MagicMock()
        mock_spacy.load.return_value = mock_nlp
        from app.modules.parsers.pdf.pymupdf_ocrmypdf_processor import PyMuPDFOCRStrategy
        return PyMuPDFOCRStrategy(logger=_mock_logger(), config=_mock_config())

    def test_merge_bounding_boxes(self):
        strategy = self._make_strategy()
        bboxes = [
            [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}, {"x": 0, "y": 10}],
            [{"x": 5, "y": 5}, {"x": 20, "y": 5}, {"x": 20, "y": 20}, {"x": 5, "y": 20}],
        ]
        result = strategy._merge_bounding_boxes(bboxes)
        assert result[0] == {"x": 0, "y": 0}
        assert result[2] == {"x": 20, "y": 20}

    def test_should_merge_blocks_both_text(self):
        strategy = self._make_strategy()
        b1 = {"type": 0, "lines": [{"spans": [{"text": "short"}]}]}
        b2 = {"type": 0, "lines": [{"spans": [{"text": "also short"}]}]}
        assert strategy._should_merge_blocks(b1, b2) is True

    def test_should_merge_blocks_long_text(self):
        strategy = self._make_strategy()
        text = " ".join(["word"] * 20)
        b1 = {"type": 0, "lines": [{"spans": [{"text": text}]}]}
        b2 = {"type": 0, "lines": [{"spans": [{"text": "short"}]}]}
        assert strategy._should_merge_blocks(b1, b2) is False

    def test_should_merge_blocks_non_text(self):
        strategy = self._make_strategy()
        b1 = {"type": 1, "lines": []}
        b2 = {"type": 0, "lines": []}
        assert strategy._should_merge_blocks(b1, b2) is False

    def test_merge_block_content(self):
        strategy = self._make_strategy()
        b1 = {"lines": [{"text": "line1"}], "bbox": (0, 0, 50, 50)}
        b2 = {"lines": [{"text": "line2"}], "bbox": (10, 60, 60, 100)}
        merged = strategy._merge_block_content(b1, b2)
        assert len(merged["lines"]) == 2
        assert merged["bbox"] == (0, 0, 60, 100)

    def test_process_block_text_single_span(self):
        strategy = self._make_strategy()
        # Mock nlp to return a sentence
        mock_doc = MagicMock()
        mock_sent = MagicMock()
        mock_sent.text = "Hello world"
        mock_sent.start_char = 0
        mock_sent.end_char = 11
        mock_doc.sents = [mock_sent]
        strategy.nlp = MagicMock(return_value=mock_doc)

        block = {
            "type": 0,
            "lines": [{
                "spans": [{"text": "Hello world", "font": "Arial", "size": 12, "flags": 0, "bbox": (0, 0, 100, 20)}],
                "bbox": (0, 0, 100, 20),
            }],
            "bbox": (0, 0, 100, 20),
        }
        result = strategy._process_block_text(block, 200.0, 400.0, 0)
        assert len(result["lines"]) == 1
        assert result["lines"][0]["content"] == "Hello world"

    def test_process_block_text_multi_span(self):
        strategy = self._make_strategy()
        mock_doc = MagicMock()
        mock_sent = MagicMock()
        mock_sent.text = "Hello world"
        mock_sent.start_char = 0
        mock_sent.end_char = 11
        mock_doc.sents = [mock_sent]
        strategy.nlp = MagicMock(return_value=mock_doc)

        block = {
            "type": 0,
            "lines": [{
                "spans": [
                    {"text": "Hello", "font": "Arial", "size": 12, "flags": 0, "bbox": (0, 0, 50, 20)},
                    {"text": "world", "font": "Arial", "size": 12, "flags": 0, "bbox": (50, 0, 100, 20)},
                ],
                "bbox": (0, 0, 100, 20),
            }],
            "bbox": (0, 0, 100, 20),
        }
        result = strategy._process_block_text(block, 200.0, 400.0, 0)
        assert len(result["lines"]) == 1
        assert "Hello" in result["lines"][0]["content"]

    @pytest.mark.asyncio
    async def test_load_document_no_ocr(self):
        strategy = self._make_strategy()
        mock_doc = MagicMock()
        mock_doc.__len__ = lambda s: 1
        mock_doc.__iter__ = lambda s: iter([MagicMock()])
        mock_page = MagicMock()
        mock_page.rect.width = 612
        mock_page.rect.height = 792
        mock_page.get_text.return_value = {"blocks": []}
        mock_doc.__getitem__ = lambda s, i: mock_page

        with patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor.fitz") as mock_fitz, \
             patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor.OCRStrategy") as MockOCR, \
             patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor.process_table_pymupdf",
                   new_callable=AsyncMock):
            mock_fitz.open.return_value = mock_doc
            MockOCR.needs_ocr = MagicMock(return_value=False)
            await strategy.load_document(b"fake-pdf")
            assert strategy.doc is mock_doc
            assert strategy.ocr_pdf_content is None

    @pytest.mark.asyncio
    async def test_process_page(self):
        strategy = self._make_strategy()
        mock_page = MagicMock()
        mock_page.rect.width = 612
        mock_page.rect.height = 792
        mock_page.get_text.side_effect = [
            [(0, 0, 50, 20, "Hello", 0, 0, 0)],  # "words" mode
            {"blocks": [{"lines": [{"spans": [{"text": "Hello"}], "bbox": (0, 0, 50, 20)}]}]},  # "dict" mode
        ]
        result = await strategy.process_page(mock_page)
        assert "words" in result
        assert "lines" in result

    def test_print_merge_statistics_no_result(self):
        strategy = self._make_strategy()
        strategy.document_analysis_result = None
        strategy.print_merge_statistics()  # should not raise


# ============================================================================
# AzureOCRStrategy
# ============================================================================

class TestAzureOCRStrategy:
    @patch("app.modules.parsers.pdf.azure_document_intelligence_processor.spacy")
    def _make_strategy(self, mock_spacy):
        mock_nlp = MagicMock()
        mock_nlp.pipe_names = ["sentencizer", "custom_sentence_boundary"]
        mock_nlp.add_pipe = MagicMock()
        mock_nlp.tokenizer = MagicMock()
        mock_spacy.load.return_value = mock_nlp

        from app.modules.parsers.pdf.azure_document_intelligence_processor import AzureOCRStrategy
        return AzureOCRStrategy(
            logger=_mock_logger(), config=_mock_config(),
            endpoint="https://fake.cognitiveservices.azure.com",
            key="fake-key",
        )

    def test_normalize_bbox(self):
        strategy = self._make_strategy()
        result = strategy._normalize_bbox((0, 0, 100, 200), 200.0, 400.0)
        assert len(result) == 4
        assert result[0] == {"x": 0.0, "y": 0.0}
        assert result[2] == {"x": 0.5, "y": 0.5}

    def test_get_bounding_box_polygon(self):
        strategy = self._make_strategy()
        element = MagicMock()
        element.polygon = [MagicMock(x=0.1, y=0.2), MagicMock(x=0.3, y=0.4)]
        result = strategy._get_bounding_box(element)
        assert len(result) == 2
        assert result[0]["x"] == 0.1

    def test_get_bounding_box_bounding_regions(self):
        strategy = self._make_strategy()
        element = MagicMock(spec=["bounding_regions"])
        region = MagicMock()
        region.polygon = [MagicMock(x=0.5, y=0.6)]
        element.bounding_regions = [region]
        result = strategy._get_bounding_box(element)
        assert result[0]["x"] == 0.5

    def test_get_bounding_box_no_polygon(self):
        strategy = self._make_strategy()
        element = MagicMock(spec=[])
        result = strategy._get_bounding_box(element)
        assert result == []

    def test_normalize_coordinates(self):
        strategy = self._make_strategy()
        coords = [{"x": 50, "y": 100}, {"x": 100, "y": 200}]
        result = strategy._normalize_coordinates(coords, 200.0, 400.0)
        assert result[0] == {"x": 0.25, "y": 0.25}

    def test_normalize_coordinates_empty(self):
        strategy = self._make_strategy()
        assert strategy._normalize_coordinates([], 200.0, 400.0) is None

    def test_normalize_coordinates_none(self):
        strategy = self._make_strategy()
        assert strategy._normalize_coordinates(None, 200.0, 400.0) is None

    def test_normalize_element_data(self):
        strategy = self._make_strategy()
        data = {"bounding_box": [{"x": 50, "y": 100}], "other": "value"}
        result = strategy._normalize_element_data(data, 200.0, 400.0)
        assert result["bounding_box"][0]["x"] == 0.25
        assert result["other"] == "value"

    def test_should_merge_blocks(self):
        strategy = self._make_strategy()
        b1 = {"type": 0, "lines": [{"spans": [{"text": "short"}]}]}
        b2 = {"type": 0, "lines": [{"spans": [{"text": "more"}]}]}
        assert strategy._should_merge_blocks(b1, b2) is True

    def test_should_merge_blocks_different_types(self):
        strategy = self._make_strategy()
        assert strategy._should_merge_blocks({"type": 1}, {"type": 0}) is False

    def test_merge_block_content(self):
        strategy = self._make_strategy()
        b1 = {"lines": [{"text": "a"}], "bbox": (0, 0, 10, 10)}
        b2 = {"lines": [{"text": "b"}], "bbox": (5, 5, 20, 20)}
        merged = strategy._merge_block_content(b1, b2)
        assert len(merged["lines"]) == 2
        assert merged["bbox"] == (0, 0, 20, 20)

    def test_check_bbox_overlap_overlapping(self):
        strategy = self._make_strategy()
        bbox1 = [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}, {"x": 0, "y": 1}]
        bbox2 = [{"x": 0.5, "y": 0.5}, {"x": 1.5, "y": 0.5}, {"x": 1.5, "y": 1.5}, {"x": 0.5, "y": 1.5}]
        assert strategy._check_bbox_overlap(bbox1, bbox2) is True

    def test_check_bbox_overlap_not_overlapping(self):
        strategy = self._make_strategy()
        bbox1 = [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}, {"x": 0, "y": 1}]
        bbox2 = [{"x": 5, "y": 5}, {"x": 6, "y": 5}, {"x": 6, "y": 6}, {"x": 5, "y": 6}]
        assert strategy._check_bbox_overlap(bbox1, bbox2) is False

    def test_cells_to_grid(self):
        strategy = self._make_strategy()
        cells = [
            {"row_index": 0, "column_index": 0, "content": "A1", "row_span": 1, "column_span": 1},
            {"row_index": 0, "column_index": 1, "content": "B1", "row_span": 1, "column_span": 1},
            {"row_index": 1, "column_index": 0, "content": "A2", "row_span": 1, "column_span": 1},
            {"row_index": 1, "column_index": 1, "content": "B2", "row_span": 1, "column_span": 1},
        ]
        grid = strategy.cells_to_grid(2, 2, cells)
        assert grid == [["A1", "B1"], ["A2", "B2"]]

    def test_cells_to_grid_with_span(self):
        strategy = self._make_strategy()
        cells = [
            {"row_index": 0, "column_index": 0, "content": "Merged", "row_span": 2, "column_span": 1},
            {"row_index": 0, "column_index": 1, "content": "B1", "row_span": 1, "column_span": 1},
        ]
        grid = strategy.cells_to_grid(2, 2, cells)
        assert grid[0][0] == "Merged"
        assert grid[0][1] == "B1"

    def test_extract_page_properties_azure(self):
        strategy = self._make_strategy()
        mock_page = MagicMock()
        mock_page.width = 8.5
        mock_page.height = 11.0
        mock_page.unit = "inch"
        mock_page.page_number = 1
        result = strategy._extract_page_properties(mock_page, True, 1)
        assert result["width"] == 8.5
        assert result["unit"] == "inch"

    def test_process_line_valid(self):
        strategy = self._make_strategy()
        mock_line = MagicMock()
        mock_line.content = "Hello world"
        mock_line.confidence = 0.95
        strategy._get_bounding_box = MagicMock(return_value=[{"x": 0, "y": 0}])
        strategy._normalize_coordinates = MagicMock(return_value=[{"x": 0, "y": 0}])
        result = strategy._process_line(mock_line, 200.0, 400.0)
        assert result["content"] == "Hello world"

    def test_process_line_empty(self):
        strategy = self._make_strategy()
        mock_line = MagicMock()
        mock_line.content = "   "
        result = strategy._process_line(mock_line, 200.0, 400.0)
        assert result is None

    def test_process_line_no_content(self):
        strategy = self._make_strategy()
        mock_line = MagicMock(spec=[])
        result = strategy._process_line(mock_line, 200.0, 400.0)
        assert result is None

    def test_merge_bounding_boxes(self):
        strategy = self._make_strategy()
        bboxes = [
            [{"x": 0, "y": 0}, {"x": 5, "y": 5}],
            [{"x": 10, "y": 10}, {"x": 15, "y": 15}],
        ]
        result = strategy._merge_bounding_boxes(bboxes)
        assert result[0] == {"x": 0, "y": 0}
        assert result[2] == {"x": 15, "y": 15}

    def test_process_block_text_azure(self):
        strategy = self._make_strategy()
        mock_block = MagicMock()
        mock_block.content = "Azure paragraph text"
        mock_block.words = []
        mock_block.role = "paragraph"
        mock_block.confidence = 0.99
        strategy._get_bounding_box = MagicMock(return_value=[{"x": 0, "y": 0}])
        strategy._normalize_coordinates = MagicMock(return_value=[{"x": 0, "y": 0}])
        result = strategy._process_block_text_azure(mock_block, 200.0, 400.0)
        assert result["content"] == "Azure paragraph text"

    def test_get_lines_for_paragraph(self):
        strategy = self._make_strategy()
        page_lines = [
            {
                "content": "hello world",
                "bounding_box": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 0.1}, {"x": 0, "y": 0.1}],
            },
            {
                "content": "unrelated text",
                "bounding_box": [{"x": 0, "y": 0.5}, {"x": 1, "y": 0.5}, {"x": 1, "y": 0.6}, {"x": 0, "y": 0.6}],
            },
        ]
        para_text = "hello world"
        para_bbox = [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 0.1}, {"x": 0, "y": 0.1}]
        result = strategy._get_lines_for_paragraph(page_lines, para_text, para_bbox)
        assert len(result) == 1
        assert result[0]["content"] == "hello world"

    async def test_process_page_not_processed(self):
        strategy = self._make_strategy()
        strategy._processed = False
        mock_page = MagicMock()
        mock_page.rect.width = 612
        mock_page.rect.height = 792
        mock_page.get_text.side_effect = [
            [(0, 0, 50, 20, "Test")],
            {"blocks": []},
        ]
        result = await strategy.process_page(mock_page)
        assert "words" in result

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

    def test_render_page_to_base64(self):
        strategy = self._make_strategy()
        mock_page = MagicMock()
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"fake-png-bytes"
        mock_page.get_pixmap.return_value = mock_pix

        with patch("app.modules.parsers.pdf.vlm_ocr_strategy.fitz") as mock_fitz:
            mock_fitz.Matrix.return_value = MagicMock()
            result = strategy._render_page_to_base64(mock_page)

        assert result.startswith("data:image/png;base64,")

    def test_render_page_to_base64_error(self):
        strategy = self._make_strategy()
        mock_page = MagicMock()
        mock_page.get_pixmap.side_effect = RuntimeError("render failed")

        with patch("app.modules.parsers.pdf.vlm_ocr_strategy.fitz"):
            with pytest.raises(RuntimeError):
                strategy._render_page_to_base64(mock_page)

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
        strategy._render_page_to_base64 = MagicMock(return_value="data:image/png;base64,abc")
        strategy._call_llm_for_markdown = AsyncMock(return_value="# Page 1")

        mock_page = MagicMock()
        mock_page.number = 0
        mock_page.rect.width = 612
        mock_page.rect.height = 792

        result = await strategy.process_page(mock_page)
        assert result["page_number"] == 1
        assert result["markdown"] == "# Page 1"

    @pytest.mark.asyncio
    async def test_process_page_error(self):
        strategy = self._make_strategy()
        strategy._render_page_to_base64 = MagicMock(side_effect=RuntimeError("render fail"))

        mock_page = MagicMock()
        mock_page.number = 0
        with pytest.raises(RuntimeError):
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
        with pytest.raises(ValueError, match="No LLM configurations"):
            await strategy._get_multimodal_llm()

    @pytest.mark.asyncio
    async def test_get_multimodal_llm_no_multimodal(self):
        strategy = self._make_strategy()
        strategy.config.get_config = AsyncMock(return_value={
            "llm": [{"provider": "openai", "isDefault": True, "configuration": {"model": "gpt-3.5"}}]
        })
        with patch("app.modules.parsers.pdf.vlm_ocr_strategy.is_multimodal_llm", return_value=False):
            with pytest.raises(ValueError, match="No multimodal LLM found"):
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
        mock_page1.number = 0
        mock_page1.rect.width = 612
        mock_page1.rect.height = 792

        strategy.doc = MagicMock()
        strategy.doc.__len__ = lambda s: 1
        strategy.doc.__iter__ = lambda s: iter([mock_page1])

        strategy.process_page = AsyncMock(return_value={
            "page_number": 1,
            "markdown": "# Page 1",
            "width": 612,
            "height": 792,
        })

        result = await strategy._preprocess_document()
        assert result["total_pages"] == 1
        assert "# Page 1" in result["markdown"]

    @pytest.mark.asyncio
    async def test_load_document(self):
        strategy = self._make_strategy()
        strategy._get_multimodal_llm = AsyncMock(return_value=MagicMock())
        strategy._preprocess_document = AsyncMock(return_value={"pages": [], "markdown": "", "total_pages": 1})

        with patch("app.modules.parsers.pdf.vlm_ocr_strategy.fitz") as mock_fitz:
            mock_doc = MagicMock()
            mock_doc.__len__ = lambda s: 1
            mock_fitz.open.return_value = mock_doc
            await strategy.load_document(b"fake-pdf")

        assert strategy.doc is not None
        assert strategy.document_analysis_result is not None
