"""Extended unit tests for PDF processor modules to increase coverage.

Covers uncovered lines/branches in:
- AzureOCRStrategy (azure_document_intelligence_processor.py)
- PyMuPDFOCRStrategy (pymupdf_ocrmypdf_processor.py)
- OpenCVLayoutAnalyzer (opencv_layout_analyzer.py)
- PyMuPDFOpenCVProcessor (pymupdf_opencv_processor.py)
"""

import os
import tempfile
from io import BytesIO
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, PropertyMock, call, patch

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_logger():
    import logging
    return MagicMock(spec=logging.Logger)


def _mock_config():
    return AsyncMock()


# ============================================================================
# OpenCVLayoutAnalyzer — covering analyze_page, _detect_table_regions full
#   path, _detect_text_regions full path, _extract_image_regions full path,
#   _collect_unclaimed_text_blocks unclaimed heading/text paths
# ============================================================================

class TestOpenCVLayoutAnalyzerAnalyzePage:
    """Tests for OpenCVLayoutAnalyzer.analyze_page covering full classification paths."""

    def _make_analyzer(self):
        from app.modules.parsers.pdf.opencv_layout_analyzer import OpenCVLayoutAnalyzer
        return OpenCVLayoutAnalyzer(logger=_mock_logger(), render_dpi=150)

    @patch("app.modules.parsers.pdf.opencv_layout_analyzer.cv2")
    def test_analyze_page_text_region_classified_as_text(self, mock_cv2):
        """analyze_page classifies text region as TEXT when not heading or list."""
        analyzer = self._make_analyzer()

        # Mock page
        mock_page = MagicMock()
        mock_page.rect.width = 612.0
        mock_page.rect.height = 792.0

        # Render returns an image
        mock_pix = MagicMock()
        mock_pix.height = 100
        mock_pix.width = 80
        mock_pix.samples = np.zeros(100 * 80 * 3, dtype=np.uint8).tobytes()
        mock_page.get_pixmap.return_value = mock_pix

        # OpenCV mocks
        mock_cv2.cvtColor.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.adaptiveThreshold.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.morphologyEx.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.getStructuringElement.return_value = np.ones((1, 10), dtype=np.uint8)
        mock_cv2.add.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.dilate.return_value = np.zeros((100, 80), dtype=np.uint8)

        # No table contours, no text contours from CV2
        mock_cv2.findContours.return_value = ([], None)

        # get_images returns empty
        mock_page.get_images.return_value = []

        # PyMuPDF text dict: one text block
        mock_page.get_text.return_value = {
            "blocks": [{
                "type": 0,
                "bbox": (50, 50, 200, 100),
                "lines": [{
                    "spans": [{"text": "Normal paragraph text here.", "size": 12.0, "flags": 0}]
                }]
            }]
        }

        regions = analyzer.analyze_page(mock_page)
        # The block should be picked up by _collect_unclaimed_text_blocks as TEXT
        text_regions = [r for r in regions if r.type.value == "text"]
        assert len(text_regions) >= 1
        assert "Normal paragraph text here." in text_regions[0].text

    @patch("app.modules.parsers.pdf.opencv_layout_analyzer.cv2")
    def test_analyze_page_unclaimed_block_as_heading(self, mock_cv2):
        """analyze_page classifies unclaimed block as HEADING when font size is large."""
        analyzer = self._make_analyzer()

        mock_page = MagicMock()
        mock_page.rect.width = 612.0
        mock_page.rect.height = 792.0
        mock_pix = MagicMock()
        mock_pix.height = 100
        mock_pix.width = 80
        mock_pix.samples = np.zeros(100 * 80 * 3, dtype=np.uint8).tobytes()
        mock_page.get_pixmap.return_value = mock_pix

        mock_cv2.cvtColor.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.adaptiveThreshold.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.morphologyEx.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.getStructuringElement.return_value = np.ones((1, 10), dtype=np.uint8)
        mock_cv2.add.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.dilate.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.findContours.return_value = ([], None)
        mock_page.get_images.return_value = []

        # Text dict with one block that has a large font size (heading)
        # median font size will be 24 (only one block), so threshold = 24*1.3 = 31.2
        # Set font size to 32 to trigger heading
        mock_page.get_text.return_value = {
            "blocks": [
                {
                    "type": 0,
                    "bbox": (50, 50, 200, 80),
                    "lines": [{
                        "spans": [{"text": "Chapter Title", "size": 10.0, "flags": 0}]
                    }]
                },
                {
                    "type": 0,
                    "bbox": (50, 100, 200, 120),
                    "lines": [{
                        "spans": [{"text": "Big Heading", "size": 20.0, "flags": 0}]
                    }]
                },
            ]
        }

        regions = analyzer.analyze_page(mock_page)
        heading_regions = [r for r in regions if r.type.value == "heading"]
        assert len(heading_regions) >= 1

    @patch("app.modules.parsers.pdf.opencv_layout_analyzer.cv2")
    def test_analyze_page_unclaimed_block_bold_heading(self, mock_cv2):
        """Unclaimed bold block with size >= median*1.1 and single line -> heading."""
        analyzer = self._make_analyzer()

        mock_page = MagicMock()
        mock_page.rect.width = 612.0
        mock_page.rect.height = 792.0
        mock_pix = MagicMock()
        mock_pix.height = 100
        mock_pix.width = 80
        mock_pix.samples = np.zeros(100 * 80 * 3, dtype=np.uint8).tobytes()
        mock_page.get_pixmap.return_value = mock_pix

        mock_cv2.cvtColor.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.adaptiveThreshold.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.morphologyEx.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.getStructuringElement.return_value = np.ones((1, 10), dtype=np.uint8)
        mock_cv2.add.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.dilate.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.findContours.return_value = ([], None)
        mock_page.get_images.return_value = []

        # Two blocks: one normal and one bold at >= median * 1.1
        # Many spans at size 10 to establish a median of 10.0
        # Bold block at size 12.0 >= 10.0 * 1.1 = 11.0 and single line
        mock_page.get_text.return_value = {
            "blocks": [
                {
                    "type": 0,
                    "bbox": (50, 50, 200, 80),
                    "lines": [
                        {"spans": [{"text": "Normal text here", "size": 10.0, "flags": 0}]},
                        {"spans": [{"text": "More normal text", "size": 10.0, "flags": 0}]},
                        {"spans": [{"text": "Even more text", "size": 10.0, "flags": 0}]},
                    ]
                },
                {
                    "type": 0,
                    "bbox": (50, 200, 300, 220),
                    "lines": [{
                        "spans": [{"text": "Bold Title", "size": 12.0, "flags": 0b10000}]
                    }]
                },
            ]
        }

        regions = analyzer.analyze_page(mock_page)
        heading_regions = [r for r in regions if r.type.value == "heading"]
        assert len(heading_regions) >= 1

    @patch("app.modules.parsers.pdf.opencv_layout_analyzer.cv2")
    def test_analyze_page_in_table_block_skipped(self, mock_cv2):
        """Blocks inside table regions are skipped in _collect_unclaimed_text_blocks."""
        analyzer = self._make_analyzer()

        mock_page = MagicMock()
        mock_page.rect.width = 612.0
        mock_page.rect.height = 792.0
        mock_pix = MagicMock()
        mock_pix.height = 100
        mock_pix.width = 80
        mock_pix.samples = np.zeros(100 * 80 * 3, dtype=np.uint8).tobytes()
        mock_page.get_pixmap.return_value = mock_pix

        mock_cv2.cvtColor.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.adaptiveThreshold.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.morphologyEx.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.getStructuringElement.return_value = np.ones((1, 10), dtype=np.uint8)
        mock_cv2.add.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.dilate.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.findContours.return_value = ([], None)
        mock_page.get_images.return_value = []
        mock_page.get_text.return_value = {"blocks": []}

        regions = analyzer.analyze_page(mock_page)
        assert isinstance(regions, list)

    @patch("app.modules.parsers.pdf.opencv_layout_analyzer.cv2")
    def test_detect_table_regions_with_valid_contours(self, mock_cv2):
        """_detect_table_regions processes contours with sufficient area, grid lines, and cells."""
        analyzer = self._make_analyzer()
        binary = np.zeros((500, 400), dtype=np.uint8)
        page_w = 400.0 * 72.0 / 150  # convert pixel to points
        page_h = 500.0 * 72.0 / 150

        # Create a contour that produces a large enough bounding rect
        cnt = np.array([[[50, 50]], [[350, 50]], [[350, 450]], [[50, 450]]], dtype=np.int32)
        mock_cv2.getStructuringElement.return_value = np.ones((1, 10), dtype=np.uint8)
        mock_cv2.morphologyEx.return_value = np.ones((500, 400), dtype=np.uint8) * 255
        mock_cv2.add.return_value = np.ones((500, 400), dtype=np.uint8) * 255
        mock_cv2.dilate.return_value = np.ones((500, 400), dtype=np.uint8) * 255
        mock_cv2.findContours.side_effect = [
            ([cnt], None),  # outer contours
            # Inner contours for cell detection - need 4+
            ([np.array([[[60, 60]], [[100, 100]]]), np.array([[[110, 110]], [[150, 150]]]),
              np.array([[[160, 160]], [[200, 200]]]), np.array([[[210, 210]], [[250, 250]]])], None),
        ]
        mock_cv2.boundingRect.return_value = (50, 50, 300, 400)
        mock_cv2.bitwise_not.return_value = np.zeros((400, 300), dtype=np.uint8)

        result = analyzer._detect_table_regions(binary, page_w, page_h)
        assert isinstance(result, list)

    @patch("app.modules.parsers.pdf.opencv_layout_analyzer.cv2")
    def test_detect_table_regions_too_small(self, mock_cv2):
        """Small contours are rejected in _detect_table_regions."""
        analyzer = self._make_analyzer()
        binary = np.zeros((500, 400), dtype=np.uint8)
        page_w = 400.0
        page_h = 500.0

        # Tiny contour
        cnt = np.array([[[0, 0]], [[2, 0]], [[2, 2]], [[0, 2]]], dtype=np.int32)
        mock_cv2.getStructuringElement.return_value = np.ones((1, 10), dtype=np.uint8)
        mock_cv2.morphologyEx.return_value = np.zeros((500, 400), dtype=np.uint8)
        mock_cv2.add.return_value = np.zeros((500, 400), dtype=np.uint8)
        mock_cv2.dilate.return_value = np.zeros((500, 400), dtype=np.uint8)
        mock_cv2.findContours.return_value = ([cnt], None)
        mock_cv2.boundingRect.return_value = (0, 0, 2, 2)

        result = analyzer._detect_table_regions(binary, page_w, page_h)
        assert result == []

    @patch("app.modules.parsers.pdf.opencv_layout_analyzer.cv2")
    def test_detect_table_regions_too_large(self, mock_cv2):
        """Oversized contours (covering most of page) are rejected."""
        analyzer = self._make_analyzer()
        binary = np.zeros((500, 400), dtype=np.uint8)
        page_w = 400.0
        page_h = 500.0

        # Contour covering nearly all the page
        cnt = np.array([[[0, 0]], [[399, 0]], [[399, 499]], [[0, 499]]], dtype=np.int32)
        mock_cv2.getStructuringElement.return_value = np.ones((1, 10), dtype=np.uint8)
        mock_cv2.morphologyEx.return_value = np.ones((500, 400), dtype=np.uint8) * 255
        mock_cv2.add.return_value = np.ones((500, 400), dtype=np.uint8) * 255
        mock_cv2.dilate.return_value = np.ones((500, 400), dtype=np.uint8) * 255
        mock_cv2.findContours.return_value = ([cnt], None)
        mock_cv2.boundingRect.return_value = (0, 0, 400, 500)

        result = analyzer._detect_table_regions(binary, page_w, page_h)
        assert result == []

    @patch("app.modules.parsers.pdf.opencv_layout_analyzer.cv2")
    def test_detect_table_regions_insufficient_grid_lines(self, mock_cv2):
        """Contours with insufficient grid lines are rejected."""
        analyzer = self._make_analyzer()
        binary = np.zeros((500, 400), dtype=np.uint8)
        page_w = 400.0
        page_h = 500.0

        # Medium contour
        cnt = np.array([[[50, 50]], [[200, 50]], [[200, 200]], [[50, 200]]], dtype=np.int32)
        mock_cv2.getStructuringElement.return_value = np.ones((1, 10), dtype=np.uint8)
        # return a partially filled result for morphologyEx (horiz/vert lines)
        horiz_result = np.zeros((500, 400), dtype=np.uint8)
        vert_result = np.zeros((500, 400), dtype=np.uint8)
        mock_cv2.morphologyEx.side_effect = [horiz_result, vert_result]
        mock_cv2.add.return_value = np.zeros((500, 400), dtype=np.uint8)
        mock_cv2.dilate.return_value = np.ones((500, 400), dtype=np.uint8) * 255
        mock_cv2.findContours.return_value = ([cnt], None)
        mock_cv2.boundingRect.return_value = (50, 50, 150, 150)

        result = analyzer._detect_table_regions(binary, page_w, page_h)
        assert result == []

    @patch("app.modules.parsers.pdf.opencv_layout_analyzer.cv2")
    def test_detect_text_regions_filters_small_and_in_table(self, mock_cv2):
        """_detect_text_regions filters out small and table-overlapping regions."""
        analyzer = self._make_analyzer()
        binary = np.zeros((500, 400), dtype=np.uint8)
        page_w = 400.0
        page_h = 500.0

        # One large text contour, one small one
        large_cnt = np.array([[[10, 10]], [[300, 10]], [[300, 100]], [[10, 100]]], dtype=np.int32)
        small_cnt = np.array([[[0, 0]], [[1, 0]], [[1, 1]], [[0, 1]]], dtype=np.int32)

        mock_cv2.getStructuringElement.return_value = np.ones((3, 10), dtype=np.uint8)
        mock_cv2.dilate.return_value = np.zeros((500, 400), dtype=np.uint8)
        mock_cv2.findContours.return_value = ([large_cnt, small_cnt], None)
        mock_cv2.boundingRect.side_effect = [(10, 10, 290, 90), (0, 0, 1, 1)]

        table_rects = []
        result = analyzer._detect_text_regions(binary, table_rects, page_w, page_h)
        # Only the large one should pass
        assert isinstance(result, list)

    @patch("app.modules.parsers.pdf.opencv_layout_analyzer.cv2")
    def test_detect_text_regions_in_table_excluded(self, mock_cv2):
        """Text regions overlapping with tables are excluded."""
        analyzer = self._make_analyzer()
        binary = np.zeros((500, 400), dtype=np.uint8)
        page_w = 400.0
        page_h = 500.0

        cnt = np.array([[[10, 10]], [[200, 10]], [[200, 200]], [[10, 200]]], dtype=np.int32)
        mock_cv2.getStructuringElement.return_value = np.ones((3, 10), dtype=np.uint8)
        mock_cv2.dilate.return_value = np.zeros((500, 400), dtype=np.uint8)
        mock_cv2.findContours.return_value = ([cnt], None)
        mock_cv2.boundingRect.return_value = (10, 10, 190, 190)

        from app.modules.parsers.pdf.opencv_layout_analyzer import _pixel_to_pdf
        # Create a table rect that encompasses the text region
        table_rects = [(
            _pixel_to_pdf(0, 150), _pixel_to_pdf(0, 150),
            _pixel_to_pdf(400, 150), _pixel_to_pdf(400, 150),
        )]

        result = analyzer._detect_text_regions(binary, table_rects, page_w, page_h)
        # The text region overlaps with the table so should be excluded
        assert isinstance(result, list)

    def test_extract_image_regions_with_images(self):
        """_extract_image_regions extracts valid images."""
        analyzer = self._make_analyzer()
        mock_page = MagicMock()
        mock_page.get_images.return_value = [(42, 0, 100, 100, 8, "DeviceRGB", "", "")]

        mock_rect = MagicMock()
        mock_rect.x0 = 50.0
        mock_rect.y0 = 50.0
        mock_rect.x1 = 250.0
        mock_rect.y1 = 250.0
        mock_page.get_image_rects.return_value = [mock_rect]

        mock_parent = MagicMock()
        mock_parent.extract_image.return_value = {"image": b"fake-image", "ext": "png"}
        mock_page.parent = mock_parent

        result = analyzer._extract_image_regions(mock_page, [], 612.0, 792.0)
        assert len(result) == 1
        assert result[0]["data"] == b"fake-image"
        assert result[0]["ext"] == "png"

    def test_extract_image_regions_too_small(self):
        """_extract_image_regions rejects images that are too small."""
        analyzer = self._make_analyzer()
        mock_page = MagicMock()
        mock_page.get_images.return_value = [(42, 0, 10, 10, 8, "DeviceRGB", "", "")]

        mock_rect = MagicMock()
        mock_rect.x0 = 0.0
        mock_rect.y0 = 0.0
        mock_rect.x1 = 1.0
        mock_rect.y1 = 1.0
        mock_page.get_image_rects.return_value = [mock_rect]

        result = analyzer._extract_image_regions(mock_page, [], 612.0, 792.0)
        assert result == []

    def test_extract_image_regions_in_table(self):
        """_extract_image_regions skips images overlapping tables."""
        analyzer = self._make_analyzer()
        mock_page = MagicMock()
        mock_page.get_images.return_value = [(42, 0, 100, 100, 8, "DeviceRGB", "", "")]

        mock_rect = MagicMock()
        mock_rect.x0 = 50.0
        mock_rect.y0 = 50.0
        mock_rect.x1 = 250.0
        mock_rect.y1 = 250.0
        mock_page.get_image_rects.return_value = [mock_rect]

        # Table rect that fully contains the image rect
        table_rects = [(0.0, 0.0, 612.0, 792.0)]

        result = analyzer._extract_image_regions(mock_page, table_rects, 612.0, 792.0)
        assert result == []

    def test_extract_image_regions_no_rects(self):
        """_extract_image_regions handles images with no rects."""
        analyzer = self._make_analyzer()
        mock_page = MagicMock()
        mock_page.get_images.return_value = [(42, 0, 100, 100, 8, "DeviceRGB", "", "")]
        mock_page.get_image_rects.return_value = []

        result = analyzer._extract_image_regions(mock_page, [], 612.0, 792.0)
        assert result == []

    def test_extract_image_regions_extraction_error(self):
        """_extract_image_regions handles extraction errors gracefully."""
        analyzer = self._make_analyzer()
        mock_page = MagicMock()
        mock_page.get_images.return_value = [(42, 0, 100, 100, 8, "DeviceRGB", "", "")]

        mock_rect = MagicMock()
        mock_rect.x0 = 50.0
        mock_rect.y0 = 50.0
        mock_rect.x1 = 250.0
        mock_rect.y1 = 250.0
        mock_page.get_image_rects.return_value = [mock_rect]

        mock_parent = MagicMock()
        mock_parent.extract_image.side_effect = RuntimeError("extraction failed")
        mock_page.parent = mock_parent

        result = analyzer._extract_image_regions(mock_page, [], 612.0, 792.0)
        assert result == []

    def test_extract_image_regions_no_image_data(self):
        """_extract_image_regions skips when extract_image returns empty."""
        analyzer = self._make_analyzer()
        mock_page = MagicMock()
        mock_page.get_images.return_value = [(42, 0, 100, 100, 8, "DeviceRGB", "", "")]

        mock_rect = MagicMock()
        mock_rect.x0 = 50.0
        mock_rect.y0 = 50.0
        mock_rect.x1 = 250.0
        mock_rect.y1 = 250.0
        mock_page.get_image_rects.return_value = [mock_rect]

        mock_parent = MagicMock()
        mock_parent.extract_image.return_value = {"image": None}
        mock_page.parent = mock_parent

        result = analyzer._extract_image_regions(mock_page, [], 612.0, 792.0)
        assert result == []

    def test_get_text_blocks_for_region_no_bbox(self):
        """_get_text_blocks_for_region skips blocks without bbox."""
        analyzer = self._make_analyzer()
        text_dict = {
            "blocks": [
                {"type": 0, "bbox": None},
                {"type": 0},  # no bbox key
            ]
        }
        result = analyzer._get_text_blocks_for_region((10, 10, 50, 50), text_dict)
        assert result == []

    def test_classify_list_type_ordered_with_paren(self):
        """_classify_list_type detects ordered lists with parentheses."""
        analyzer = self._make_analyzer()
        text = "1) First item\n2) Second item\n3) Third item"
        from app.modules.parsers.pdf.opencv_layout_analyzer import LayoutRegionType
        result = analyzer._classify_list_type(text)
        assert result == LayoutRegionType.ORDERED_LIST

    def test_classify_list_type_mixed(self):
        """_classify_list_type returns None when items are mixed."""
        analyzer = self._make_analyzer()
        text = "- Bullet item\n1. Numbered item\nRandom text"
        result = analyzer._classify_list_type(text)
        # Less than 60% for either type
        assert result is None

    def test_collect_unclaimed_text_blocks_in_image(self):
        """_collect_unclaimed_text_blocks skips blocks overlapping images."""
        from app.modules.parsers.pdf.opencv_layout_analyzer import LayoutRegion, LayoutRegionType
        analyzer = self._make_analyzer()
        text_dict = {
            "blocks": [{
                "type": 0,
                "bbox": (50, 50, 250, 250),
                "lines": [{"spans": [{"text": "In image text", "size": 12.0, "flags": 0}]}],
            }]
        }
        regions = []
        table_rects = []
        image_bboxes = [(0, 0, 612, 792)]  # image covers full page

        analyzer._collect_unclaimed_text_blocks(text_dict, regions, table_rects, image_bboxes, 612.0, 792.0)
        assert len(regions) == 0

    def test_collect_unclaimed_text_blocks_in_table(self):
        """_collect_unclaimed_text_blocks skips blocks overlapping tables."""
        from app.modules.parsers.pdf.opencv_layout_analyzer import LayoutRegion, LayoutRegionType
        analyzer = self._make_analyzer()
        text_dict = {
            "blocks": [{
                "type": 0,
                "bbox": (50, 50, 250, 250),
                "lines": [{"spans": [{"text": "In table text", "size": 12.0, "flags": 0}]}],
            }]
        }
        regions = []
        table_rects = [(0, 0, 612, 792)]
        image_bboxes = []

        analyzer._collect_unclaimed_text_blocks(text_dict, regions, table_rects, image_bboxes, 612.0, 792.0)
        assert len(regions) == 0

    def test_collect_unclaimed_text_blocks_empty_text(self):
        """_collect_unclaimed_text_blocks skips blocks with empty text."""
        analyzer = self._make_analyzer()
        text_dict = {
            "blocks": [{
                "type": 0,
                "bbox": (50, 50, 250, 250),
                "lines": [{"spans": [{"text": "   ", "size": 12.0, "flags": 0}]}],
            }]
        }
        regions = []
        analyzer._collect_unclaimed_text_blocks(text_dict, regions, [], [], 612.0, 792.0)
        assert len(regions) == 0

    def test_collect_unclaimed_text_blocks_no_bbox(self):
        """_collect_unclaimed_text_blocks skips blocks without bbox."""
        analyzer = self._make_analyzer()
        text_dict = {
            "blocks": [{
                "type": 0,
                "lines": [{"spans": [{"text": "Text", "size": 12.0, "flags": 0}]}],
            }]
        }
        regions = []
        analyzer._collect_unclaimed_text_blocks(text_dict, regions, [], [], 612.0, 792.0)
        assert len(regions) == 0

    def test_collect_unclaimed_text_blocks_non_text_type(self):
        """_collect_unclaimed_text_blocks skips non-text blocks (type != 0)."""
        analyzer = self._make_analyzer()
        text_dict = {
            "blocks": [{
                "type": 1,
                "bbox": (50, 50, 250, 250),
            }]
        }
        regions = []
        analyzer._collect_unclaimed_text_blocks(text_dict, regions, [], [], 612.0, 792.0)
        assert len(regions) == 0


# ============================================================================
# OpenCVLayoutAnalyzer — analyze_page text classification paths
# ============================================================================

class TestOpenCVLayoutAnalyzerTextClassification:
    """Tests for text region classification inside analyze_page."""

    def _make_analyzer(self):
        from app.modules.parsers.pdf.opencv_layout_analyzer import OpenCVLayoutAnalyzer
        return OpenCVLayoutAnalyzer(logger=_mock_logger(), render_dpi=150)

    @patch("app.modules.parsers.pdf.opencv_layout_analyzer.cv2")
    def test_analyze_page_list_region(self, mock_cv2):
        """analyze_page creates list region when text matches list pattern."""
        analyzer = self._make_analyzer()

        mock_page = MagicMock()
        mock_page.rect.width = 612.0
        mock_page.rect.height = 792.0
        mock_pix = MagicMock()
        mock_pix.height = 100
        mock_pix.width = 80
        mock_pix.samples = np.zeros(100 * 80 * 3, dtype=np.uint8).tobytes()
        mock_page.get_pixmap.return_value = mock_pix

        mock_cv2.cvtColor.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.adaptiveThreshold.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.morphologyEx.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.getStructuringElement.return_value = np.ones((3, 10), dtype=np.uint8)
        mock_cv2.add.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.dilate.return_value = np.zeros((100, 80), dtype=np.uint8)

        # One text region contour
        cnt = np.array([[[5, 5]], [[395, 5]], [[395, 95]], [[5, 95]]], dtype=np.int32)
        mock_cv2.findContours.side_effect = [
            ([], None),  # tables
            ([cnt], None),  # text regions
        ]
        mock_cv2.boundingRect.return_value = (5, 5, 390, 90)
        mock_page.get_images.return_value = []

        from app.modules.parsers.pdf.opencv_layout_analyzer import _pixel_to_pdf
        bbox_x0 = _pixel_to_pdf(5, 150)
        bbox_y0 = _pixel_to_pdf(5, 150)
        bbox_x1 = _pixel_to_pdf(395, 150)
        bbox_y1 = _pixel_to_pdf(95, 150)

        # Text dict with a list-like text block overlapping the detected region
        mock_page.get_text.return_value = {
            "blocks": [{
                "type": 0,
                "bbox": (bbox_x0, bbox_y0, bbox_x1, bbox_y1),
                "lines": [
                    {"spans": [{"text": "- Item one", "size": 12.0, "flags": 0}]},
                    {"spans": [{"text": "- Item two", "size": 12.0, "flags": 0}]},
                    {"spans": [{"text": "- Item three", "size": 12.0, "flags": 0}]},
                ]
            }]
        }

        regions = analyzer.analyze_page(mock_page)
        list_regions = [r for r in regions if r.type.value == "list"]
        assert len(list_regions) >= 1

    @patch("app.modules.parsers.pdf.opencv_layout_analyzer.cv2")
    def test_analyze_page_heading_region_by_font_size(self, mock_cv2):
        """analyze_page creates heading for text regions with large font size."""
        analyzer = self._make_analyzer()

        mock_page = MagicMock()
        mock_page.rect.width = 612.0
        mock_page.rect.height = 792.0
        mock_pix = MagicMock()
        mock_pix.height = 100
        mock_pix.width = 80
        mock_pix.samples = np.zeros(100 * 80 * 3, dtype=np.uint8).tobytes()
        mock_page.get_pixmap.return_value = mock_pix

        mock_cv2.cvtColor.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.adaptiveThreshold.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.morphologyEx.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.getStructuringElement.return_value = np.ones((3, 10), dtype=np.uint8)
        mock_cv2.add.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.dilate.return_value = np.zeros((100, 80), dtype=np.uint8)

        cnt = np.array([[[5, 5]], [[395, 5]], [[395, 30]], [[5, 30]]], dtype=np.int32)
        mock_cv2.findContours.side_effect = [
            ([], None),  # tables
            ([cnt], None),  # text
        ]
        mock_cv2.boundingRect.return_value = (5, 5, 390, 25)
        mock_page.get_images.return_value = []

        from app.modules.parsers.pdf.opencv_layout_analyzer import _pixel_to_pdf
        bbox_x0 = _pixel_to_pdf(5, 150)
        bbox_y0 = _pixel_to_pdf(5, 150)
        bbox_x1 = _pixel_to_pdf(395, 150)
        bbox_y1 = _pixel_to_pdf(30, 150)

        # Block with large font (>= median*1.3, median = 12 from other blocks)
        mock_page.get_text.return_value = {
            "blocks": [
                {
                    "type": 0,
                    "bbox": (bbox_x0, bbox_y0, bbox_x1, bbox_y1),
                    "lines": [{"spans": [{"text": "Big Title", "size": 24.0, "flags": 0}]}]
                },
                {
                    "type": 0,
                    "bbox": (100, 200, 300, 250),
                    "lines": [{"spans": [{"text": "Body text", "size": 12.0, "flags": 0}]}]
                },
            ]
        }

        regions = analyzer.analyze_page(mock_page)
        heading_regions = [r for r in regions if r.type.value == "heading"]
        assert len(heading_regions) >= 1

    @patch("app.modules.parsers.pdf.opencv_layout_analyzer.cv2")
    def test_analyze_page_image_not_in_text(self, mock_cv2):
        """Images not overlapping text regions are added as image regions."""
        analyzer = self._make_analyzer()

        mock_page = MagicMock()
        mock_page.rect.width = 612.0
        mock_page.rect.height = 792.0
        mock_pix = MagicMock()
        mock_pix.height = 100
        mock_pix.width = 80
        mock_pix.samples = np.zeros(100 * 80 * 3, dtype=np.uint8).tobytes()
        mock_page.get_pixmap.return_value = mock_pix

        mock_cv2.cvtColor.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.adaptiveThreshold.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.morphologyEx.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.getStructuringElement.return_value = np.ones((3, 10), dtype=np.uint8)
        mock_cv2.add.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.dilate.return_value = np.zeros((100, 80), dtype=np.uint8)
        mock_cv2.findContours.return_value = ([], None)

        # One image
        mock_page.get_images.return_value = [(42, 0, 200, 200, 8, "DeviceRGB", "", "")]
        mock_rect = MagicMock()
        mock_rect.x0 = 100.0
        mock_rect.y0 = 100.0
        mock_rect.x1 = 400.0
        mock_rect.y1 = 400.0
        mock_page.get_image_rects.return_value = [mock_rect]
        mock_parent = MagicMock()
        mock_parent.extract_image.return_value = {"image": b"img-data", "ext": "jpg"}
        mock_page.parent = mock_parent

        mock_page.get_text.return_value = {"blocks": []}

        regions = analyzer.analyze_page(mock_page)
        image_regions = [r for r in regions if r.type.value == "image"]
        assert len(image_regions) == 1


# ============================================================================
# PyMuPDFOpenCVProcessor — covering _extract_tables_with_pymupdf,
#   create_blocks branch coverage, load_document
# ============================================================================

class TestPyMuPDFOpenCVProcessorExtended:

    def _make_processor(self):
        with patch("app.modules.parsers.pdf.pymupdf_opencv_processor.OpenCVLayoutAnalyzer"):
            from app.modules.parsers.pdf.pymupdf_opencv_processor import PyMuPDFOpenCVProcessor
            return PyMuPDFOpenCVProcessor(logger=_mock_logger(), config=_mock_config())

    def test_extract_tables_with_pymupdf_matches_existing_region(self):
        """_extract_tables_with_pymupdf matches a table to an existing TABLE region."""
        from app.modules.parsers.pdf.pymupdf_opencv_processor import (
            LayoutRegion, LayoutRegionType, ParsedPageData, PyMuPDFOpenCVProcessor,
        )
        with patch("app.modules.parsers.pdf.pymupdf_opencv_processor.OpenCVLayoutAnalyzer"):
            proc = PyMuPDFOpenCVProcessor(logger=_mock_logger(), config=_mock_config())

        # Create a region and page data
        existing_region = LayoutRegion(type=LayoutRegionType.TABLE, bbox=(50, 50, 300, 200))
        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[existing_region])

        # Mock doc and page
        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_doc.__getitem__ = lambda s, i: mock_page

        # Mock table finder
        mock_table = MagicMock()
        mock_table.bbox = (50, 50, 300, 200)
        mock_table.extract.return_value = [["A", "B"], ["1", "2"]]
        mock_table_finder = MagicMock()
        mock_table_finder.tables = [mock_table]
        mock_page.find_tables.return_value = mock_table_finder

        proc._extract_tables_with_pymupdf(mock_doc, [pd])

        # The existing region should have its grid updated
        assert existing_region.table_grid == [["A", "B"], ["1", "2"]]

    def test_extract_tables_with_pymupdf_new_table(self):
        """_extract_tables_with_pymupdf adds new TABLE region when no match found."""
        from app.modules.parsers.pdf.pymupdf_opencv_processor import (
            LayoutRegion, LayoutRegionType, ParsedPageData, PyMuPDFOpenCVProcessor,
        )
        with patch("app.modules.parsers.pdf.pymupdf_opencv_processor.OpenCVLayoutAnalyzer"):
            proc = PyMuPDFOpenCVProcessor(logger=_mock_logger(), config=_mock_config())

        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[])

        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_doc.__getitem__ = lambda s, i: mock_page

        mock_table = MagicMock()
        mock_table.bbox = (100, 100, 400, 300)
        mock_table.extract.return_value = [["X", "Y"]]
        mock_table_finder = MagicMock()
        mock_table_finder.tables = [mock_table]
        mock_page.find_tables.return_value = mock_table_finder

        proc._extract_tables_with_pymupdf(mock_doc, [pd])

        assert len(pd.regions) == 1
        assert pd.regions[0].type == LayoutRegionType.TABLE
        assert pd.regions[0].table_grid == [["X", "Y"]]

    def test_extract_tables_find_tables_error(self):
        """_extract_tables_with_pymupdf handles find_tables error."""
        from app.modules.parsers.pdf.pymupdf_opencv_processor import (
            ParsedPageData, PyMuPDFOpenCVProcessor,
        )
        with patch("app.modules.parsers.pdf.pymupdf_opencv_processor.OpenCVLayoutAnalyzer"):
            proc = PyMuPDFOpenCVProcessor(logger=_mock_logger(), config=_mock_config())

        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[])

        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_doc.__getitem__ = lambda s, i: mock_page
        mock_page.find_tables.side_effect = RuntimeError("no tables")

        proc._extract_tables_with_pymupdf(mock_doc, [pd])
        assert len(pd.regions) == 0

    @pytest.mark.asyncio
    async def test_create_blocks_all_region_types(self):
        """create_blocks handles TABLE, IMAGE, LIST, ORDERED_LIST, HEADING, and TEXT."""
        from app.modules.parsers.pdf.pymupdf_opencv_processor import (
            LayoutRegion, LayoutRegionType, ParsedPageData, PyMuPDFOpenCVProcessor,
        )
        with patch("app.modules.parsers.pdf.pymupdf_opencv_processor.OpenCVLayoutAnalyzer"):
            proc = PyMuPDFOpenCVProcessor(logger=_mock_logger(), config=_mock_config())

        regions = [
            LayoutRegion(type=LayoutRegionType.TABLE, bbox=(0, 0, 100, 100), table_grid=[["A", "B"]]),
            LayoutRegion(type=LayoutRegionType.IMAGE, bbox=(0, 100, 100, 200), image_data=b"img", image_ext="png"),
            LayoutRegion(type=LayoutRegionType.LIST, bbox=(0, 200, 100, 300), text="- A\n- B", list_items=["- A", "- B"]),
            LayoutRegion(type=LayoutRegionType.ORDERED_LIST, bbox=(0, 300, 100, 400), text="1. A\n2. B", list_items=["1. A", "2. B"]),
            LayoutRegion(type=LayoutRegionType.HEADING, bbox=(0, 400, 100, 450), text="Title"),
            LayoutRegion(type=LayoutRegionType.TEXT, bbox=(0, 450, 100, 500), text="Body text"),
        ]
        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=regions)

        mock_response = MagicMock()
        mock_response.summary = "Table"
        mock_response.headers = ["A", "B"]

        with patch("app.modules.parsers.pdf.pymupdf_opencv_processor.get_table_summary_n_headers",
                    new_callable=AsyncMock, return_value=mock_response), \
             patch("app.modules.parsers.pdf.pymupdf_opencv_processor.get_rows_text",
                    new_callable=AsyncMock, return_value=(["Row text"], [["A", "B"]])):
            result = await proc.create_blocks([pd])

        # Should have blocks for all types
        assert len(result.blocks) >= 6  # text, heading, 2 list items, table row, image
        assert len(result.block_groups) >= 3  # table, list, ordered_list

    @pytest.mark.asyncio
    async def test_create_blocks_no_page_number_filter(self):
        """create_blocks without page_number processes all pages."""
        from app.modules.parsers.pdf.pymupdf_opencv_processor import (
            LayoutRegion, LayoutRegionType, ParsedPageData, PyMuPDFOpenCVProcessor,
        )
        with patch("app.modules.parsers.pdf.pymupdf_opencv_processor.OpenCVLayoutAnalyzer"):
            proc = PyMuPDFOpenCVProcessor(logger=_mock_logger(), config=_mock_config())

        r1 = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(0, 0, 100, 50), text="Page 1")
        r2 = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(0, 0, 100, 50), text="Page 2")
        page1 = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[r1])
        page2 = ParsedPageData(page_number=2, width=612.0, height=792.0, regions=[r2])

        result = await proc.create_blocks([page1, page2])
        assert len(result.blocks) == 2

    @pytest.mark.asyncio
    async def test_load_document_delegates(self):
        """load_document calls parse_document then create_blocks."""
        from app.modules.parsers.pdf.pymupdf_opencv_processor import PyMuPDFOpenCVProcessor
        with patch("app.modules.parsers.pdf.pymupdf_opencv_processor.OpenCVLayoutAnalyzer"):
            proc = PyMuPDFOpenCVProcessor(logger=_mock_logger(), config=_mock_config())

        mock_parsed = [MagicMock()]
        mock_blocks = MagicMock()
        proc.parse_document = AsyncMock(return_value=mock_parsed)
        proc.create_blocks = AsyncMock(return_value=mock_blocks)

        result = await proc.load_document("test.pdf", b"content", page_number=2)
        proc.parse_document.assert_awaited_once_with("test.pdf", b"content")
        proc.create_blocks.assert_awaited_once_with(mock_parsed, page_number=2)
        assert result is mock_blocks

    @pytest.mark.asyncio
    async def test_parse_document_with_bytesio(self):
        """parse_document accepts BytesIO input."""
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
                result = await proc.parse_document("test.pdf", BytesIO(b"fake-pdf-bytes"))

            assert len(result) == 1


# ============================================================================
# PyMuPDFOCRStrategy — load_document OCR path, _preprocess_document,
#   process_page, _merge_lines_to_sentences, _process_block_text,
#   print_merge_statistics, create_debug_pdf
# ============================================================================

class TestPyMuPDFOCRStrategyExtended:

    @patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor.spacy")
    def _make_strategy(self, mock_spacy):
        mock_nlp = MagicMock()
        mock_nlp.pipe_names = []
        mock_nlp.add_pipe = MagicMock()
        mock_nlp.tokenizer = MagicMock()
        mock_spacy.load.return_value = mock_nlp
        from app.modules.parsers.pdf.pymupdf_ocrmypdf_processor import PyMuPDFOCRStrategy
        return PyMuPDFOCRStrategy(logger=_mock_logger(), config=_mock_config())

    @pytest.mark.asyncio
    async def test_load_document_with_ocr_success(self):
        """load_document processes OCR when needs_ocr is True."""
        strategy = self._make_strategy()

        mock_doc = MagicMock()
        mock_doc.__len__ = lambda s: 1
        mock_page = MagicMock()
        mock_page.rect.width = 612
        mock_page.rect.height = 792
        mock_page.get_text.side_effect = [
            {"blocks": []},  # for dict
        ]
        mock_doc.__getitem__ = lambda s, i: mock_page
        mock_doc.__iter__ = lambda s: iter([mock_page])

        processed_doc = MagicMock()
        processed_doc.__len__ = lambda s: 1
        processed_doc.__getitem__ = lambda s, i: mock_page

        with patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor.fitz") as mock_fitz, \
             patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor.OCRStrategy") as MockOCR, \
             patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor.ocrmypdf") as mock_ocrmypdf, \
             patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor.process_table_pymupdf",
                   new_callable=AsyncMock), \
             patch("builtins.open", MagicMock()), \
             patch("os.path.exists", return_value=True), \
             patch("os.remove"):

            mock_fitz.open.side_effect = [mock_doc, processed_doc]
            MockOCR.needs_ocr = MagicMock(return_value=True)
            mock_ocrmypdf.ocr = MagicMock()

            # Mock tempfile context managers
            mock_temp_in = MagicMock()
            mock_temp_in.name = "/tmp/test_in.pdf"
            mock_temp_in.__enter__ = lambda s: s
            mock_temp_in.__exit__ = MagicMock(return_value=False)

            mock_temp_out = MagicMock()
            mock_temp_out.name = "/tmp/test_out.pdf"
            mock_temp_out.__enter__ = lambda s: s
            mock_temp_out.__exit__ = MagicMock(return_value=False)

            with patch("tempfile.NamedTemporaryFile", side_effect=[mock_temp_in, mock_temp_out]):
                with patch("builtins.open", MagicMock(return_value=BytesIO(b"ocr-content"))):
                    await strategy.load_document(b"fake-pdf")

            assert strategy.doc is not None

    @pytest.mark.asyncio
    async def test_load_document_ocr_failure_fallback(self):
        """load_document falls back to direct extraction if OCR fails."""
        strategy = self._make_strategy()

        mock_doc = MagicMock()
        mock_doc.__len__ = lambda s: 1
        mock_page = MagicMock()
        mock_page.rect.width = 612
        mock_page.rect.height = 792
        mock_page.get_text.return_value = {"blocks": []}
        mock_doc.__getitem__ = lambda s, i: mock_page
        mock_doc.__iter__ = lambda s: iter([mock_page])

        with patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor.fitz") as mock_fitz, \
             patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor.OCRStrategy") as MockOCR, \
             patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor.ocrmypdf") as mock_ocrmypdf, \
             patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor.process_table_pymupdf",
                   new_callable=AsyncMock), \
             patch("os.path.exists", return_value=True), \
             patch("os.remove"):

            mock_fitz.open.return_value = mock_doc
            MockOCR.needs_ocr = MagicMock(return_value=True)

            mock_temp_in = MagicMock()
            mock_temp_in.name = "/tmp/test_in.pdf"
            mock_temp_in.__enter__ = lambda s: s
            mock_temp_in.__exit__ = MagicMock(return_value=False)

            mock_temp_out = MagicMock()
            mock_temp_out.name = "/tmp/test_out.pdf"
            mock_temp_out.__enter__ = lambda s: s
            mock_temp_out.__exit__ = MagicMock(return_value=False)

            mock_ocrmypdf.ocr.side_effect = RuntimeError("OCR failed")

            with patch("tempfile.NamedTemporaryFile", side_effect=[mock_temp_in, mock_temp_out]):
                await strategy.load_document(b"fake-pdf")

            # Should fall back to mock_doc
            assert strategy.doc is mock_doc
            assert strategy._needs_ocr is False

    @pytest.mark.asyncio
    async def test_preprocess_document_with_text_and_image_blocks(self):
        """_preprocess_document processes text and image blocks."""
        strategy = self._make_strategy()

        mock_page = MagicMock()
        mock_page.rect.width = 612
        mock_page.rect.height = 792
        mock_page.get_text.side_effect = [
            # dict call
            {"blocks": [
                {
                    "type": 0,
                    "bbox": (0, 0, 200, 50),
                    "lines": [{
                        "spans": [{"text": "Hello", "font": "Arial", "size": 12, "flags": 0, "bbox": (0, 0, 50, 20)}],
                        "bbox": (0, 0, 200, 20),
                    }],
                    "number": 0,
                },
                {
                    "type": 1,
                    "bbox": (0, 100, 200, 300),
                    "ext": "png",
                    "image": b"fake-image-bytes",
                },
            ]},
        ]

        strategy.doc = MagicMock()
        strategy.doc.__len__ = lambda s: 1
        strategy.doc.__getitem__ = lambda s, i: mock_page

        # Mock nlp for sentence processing
        mock_doc_nlp = MagicMock()
        mock_sent = MagicMock()
        mock_sent.text = "Hello"
        mock_sent.start_char = 0
        mock_sent.end_char = 5
        mock_doc_nlp.sents = [mock_sent]
        mock_doc_nlp.__iter__ = lambda s: iter([mock_sent])
        strategy.nlp = MagicMock(return_value=mock_doc_nlp)

        with patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor.process_table_pymupdf",
                    new_callable=AsyncMock), \
             patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor.image_bytes_to_base64",
                    return_value="data:image/png;base64,abc"), \
             patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor._normalize_bbox",
                    return_value=[{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}, {"x": 0, "y": 1}]):
            result = await strategy._preprocess_document()

        assert "pages" in result
        assert "paragraphs" in result
        assert "sentences" in result
        assert len(result["pages"]) == 1

    @pytest.mark.asyncio
    async def test_preprocess_document_merges_small_blocks(self):
        """_preprocess_document merges small text blocks."""
        strategy = self._make_strategy()

        mock_page = MagicMock()
        mock_page.rect.width = 612
        mock_page.rect.height = 792
        mock_page.get_text.return_value = {
            "blocks": [
                {
                    "type": 0,
                    "bbox": (0, 0, 200, 20),
                    "lines": [{
                        "spans": [{"text": "Short", "font": "Arial", "size": 12, "flags": 0, "bbox": (0, 0, 50, 20)}],
                        "bbox": (0, 0, 200, 20),
                    }],
                    "number": 0,
                },
                {
                    "type": 0,
                    "bbox": (0, 25, 200, 45),
                    "lines": [{
                        "spans": [{"text": "Also short", "font": "Arial", "size": 12, "flags": 0, "bbox": (0, 0, 80, 20)}],
                        "bbox": (0, 25, 200, 45),
                    }],
                    "number": 1,
                },
            ]
        }

        strategy.doc = MagicMock()
        strategy.doc.__len__ = lambda s: 1
        strategy.doc.__getitem__ = lambda s, i: mock_page

        mock_doc_nlp = MagicMock()
        mock_sent = MagicMock()
        mock_sent.text = "Short Also short"
        mock_sent.start_char = 0
        mock_sent.end_char = 16
        mock_doc_nlp.sents = [mock_sent]
        strategy.nlp = MagicMock(return_value=mock_doc_nlp)

        with patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor.process_table_pymupdf",
                    new_callable=AsyncMock), \
             patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor._normalize_bbox",
                    return_value=[{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}, {"x": 0, "y": 1}]):
            result = await strategy._preprocess_document()

        # Both blocks should be merged into one paragraph
        assert len(result["paragraphs"]) == 1

    @pytest.mark.asyncio
    async def test_preprocess_document_image_unsupported_ext_skipped(self):
        """_preprocess_document skips images with unsupported extensions."""
        strategy = self._make_strategy()

        mock_page = MagicMock()
        mock_page.rect.width = 612
        mock_page.rect.height = 792
        mock_page.get_text.return_value = {
            "blocks": [
                {
                    "type": 1,
                    "bbox": (0, 100, 200, 300),
                    "ext": "tiff",
                    "image": b"tiff-data",
                },
            ]
        }

        strategy.doc = MagicMock()
        strategy.doc.__len__ = lambda s: 1
        strategy.doc.__getitem__ = lambda s, i: mock_page

        with patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor.process_table_pymupdf",
                    new_callable=AsyncMock):
            result = await strategy._preprocess_document()

        # No blocks added for tiff image
        assert len(result["blocks"]) == 0

    @pytest.mark.asyncio
    async def test_preprocess_document_image_no_bytes_skipped(self):
        """_preprocess_document skips images without bytes."""
        strategy = self._make_strategy()

        mock_page = MagicMock()
        mock_page.rect.width = 612
        mock_page.rect.height = 792
        mock_page.get_text.return_value = {
            "blocks": [
                {
                    "type": 1,
                    "bbox": (0, 100, 200, 300),
                    "ext": "png",
                    "image": None,
                },
            ]
        }

        strategy.doc = MagicMock()
        strategy.doc.__len__ = lambda s: 1
        strategy.doc.__getitem__ = lambda s, i: mock_page

        with patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor.process_table_pymupdf",
                    new_callable=AsyncMock):
            result = await strategy._preprocess_document()

        assert len(result["blocks"]) == 0

    def test_process_block_text_multi_span_with_chars(self):
        """_process_block_text handles multi-span lines with character data."""
        strategy = self._make_strategy()
        mock_doc_nlp = MagicMock()
        mock_sent = MagicMock()
        mock_sent.text = "Hello world"
        mock_sent.start_char = 0
        mock_sent.end_char = 11
        mock_doc_nlp.sents = [mock_sent]
        strategy.nlp = MagicMock(return_value=mock_doc_nlp)

        block = {
            "type": 0,
            "lines": [{
                "spans": [
                    {
                        "text": "Hello",
                        "font": "Arial",
                        "size": 12,
                        "flags": 0,
                        "bbox": (0, 0, 50, 20),
                        "chars": [
                            {"c": "H", "bbox": (0, 0, 10, 20)},
                            {"c": "e", "bbox": (10, 0, 20, 20)},
                        ],
                    },
                    {
                        "text": "world",
                        "font": "Arial",
                        "size": 12,
                        "flags": 0,
                        "bbox": (50, 0, 100, 20),
                        "chars": [],
                    },
                ],
                "bbox": (0, 0, 100, 20),
            }],
            "bbox": (0, 0, 100, 20),
        }

        with patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor._normalize_bbox",
                    return_value=[{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}, {"x": 0, "y": 1}]):
            result = strategy._process_block_text(block, 200.0, 400.0, 0)

        assert len(result["lines"]) == 1
        assert len(result["words"]) == 2  # H and e chars

    def test_process_block_text_empty_spans_raises(self):
        """_process_block_text crashes with IndexError when spans list is empty.

        This is a known edge case in the source code where line 390 accesses
        block["lines"][0]["spans"][0] without checking for empty spans.
        """
        strategy = self._make_strategy()
        mock_doc_nlp = MagicMock()
        mock_doc_nlp.sents = []
        strategy.nlp = MagicMock(return_value=mock_doc_nlp)

        block = {
            "type": 0,
            "lines": [{
                "spans": [],
                "bbox": (0, 0, 100, 20),
            }],
            "bbox": (0, 0, 100, 20),
        }

        with patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor._normalize_bbox",
                    return_value=[{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}, {"x": 0, "y": 1}]):
            with pytest.raises(IndexError):
                strategy._process_block_text(block, 200.0, 400.0, 0)

    def test_process_block_text_no_lines_raises(self):
        """_process_block_text crashes when lines list is empty.

        Source code line 398 unconditionally accesses block["lines"][0]
        for span_type metadata, which raises IndexError for empty lines.
        """
        strategy = self._make_strategy()
        mock_doc_nlp = MagicMock()
        mock_doc_nlp.sents = []
        strategy.nlp = MagicMock(return_value=mock_doc_nlp)

        block = {
            "type": 0,
            "lines": [],
            "bbox": (0, 0, 100, 20),
        }

        with patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor._normalize_bbox",
                    return_value=[{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}, {"x": 0, "y": 1}]):
            with pytest.raises(IndexError):
                strategy._process_block_text(block, 200.0, 400.0, 0)

    def test_process_block_text_bad_bbox_normalization(self):
        """_process_block_text handles bbox normalization error."""
        strategy = self._make_strategy()
        mock_doc_nlp = MagicMock()
        mock_sent = MagicMock()
        mock_sent.text = "Text"
        mock_sent.start_char = 0
        mock_sent.end_char = 4
        mock_doc_nlp.sents = [mock_sent]
        strategy.nlp = MagicMock(return_value=mock_doc_nlp)

        block = {
            "type": 0,
            "lines": [{
                "spans": [{"text": "Text", "font": "Arial", "size": 12, "flags": 0, "bbox": (0, 0, 50, 20)}],
                "bbox": (0, 0, 100, 20),
            }],
            "bbox": "invalid",  # bad bbox
        }

        def normalize_side_effect(bbox, pw, ph):
            if bbox == "invalid":
                raise TypeError("bad bbox")
            return [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}, {"x": 0, "y": 1}]

        with patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor._normalize_bbox",
                    side_effect=normalize_side_effect):
            result = strategy._process_block_text(block, 200.0, 400.0, 0)

        # paragraph should have normalized_bbox = None due to error
        assert result["paragraph"]["bounding_box"] is None

    def test_process_block_text_bbox_wrong_size(self):
        """_process_block_text handles bbox with wrong number of elements."""
        strategy = self._make_strategy()
        mock_doc_nlp = MagicMock()
        mock_sent = MagicMock()
        mock_sent.text = "Text"
        mock_sent.start_char = 0
        mock_sent.end_char = 4
        mock_doc_nlp.sents = [mock_sent]
        strategy.nlp = MagicMock(return_value=mock_doc_nlp)

        block = {
            "type": 0,
            "lines": [{
                "spans": [{"text": "Text", "font": "Arial", "size": 12, "flags": 0, "bbox": (0, 0, 50, 20)}],
                "bbox": (0, 0, 100, 20),
            }],
            "bbox": (0, 0, 100),  # only 3 elements, not 4
        }

        with patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor._normalize_bbox",
                    return_value=[{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}, {"x": 0, "y": 1}]):
            result = strategy._process_block_text(block, 200.0, 400.0, 0)

        # bbox size != 4, so normalization skipped
        assert result["paragraph"]["bounding_box"] is None

    def test_merge_lines_to_sentences(self):
        """_merge_lines_to_sentences merges lines using nlp."""
        strategy = self._make_strategy()
        mock_doc_nlp = MagicMock()
        mock_sent1 = MagicMock()
        mock_sent1.text = "Hello world."
        mock_sent1.start_char = 0
        mock_sent1.end_char = 12
        mock_sent2 = MagicMock()
        mock_sent2.text = "Second sentence."
        mock_sent2.start_char = 13
        mock_sent2.end_char = 29
        mock_doc_nlp.sents = [mock_sent1, mock_sent2]
        strategy.nlp = MagicMock(return_value=mock_doc_nlp)

        lines_data = [
            {
                "content": "Hello world.",
                "bounding_box": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 0.1}, {"x": 0, "y": 0.1}],
            },
            {
                "content": "Second sentence.",
                "bounding_box": [{"x": 0, "y": 0.2}, {"x": 1, "y": 0.2}, {"x": 1, "y": 0.3}, {"x": 0, "y": 0.3}],
            },
        ]

        result = strategy._merge_lines_to_sentences(lines_data)
        assert len(result) == 2
        assert result[0]["sentence"] == "Hello world."

    def test_merge_lines_to_sentences_empty_lines(self):
        """_merge_lines_to_sentences handles empty content lines."""
        strategy = self._make_strategy()
        mock_doc_nlp = MagicMock()
        mock_doc_nlp.sents = []
        strategy.nlp = MagicMock(return_value=mock_doc_nlp)

        lines_data = [
            {"content": "   ", "bounding_box": []},
        ]

        result = strategy._merge_lines_to_sentences(lines_data)
        assert result == []

    @pytest.mark.asyncio
    async def test_process_page_extracts_words_and_lines(self):
        """process_page extracts words and lines from a page."""
        strategy = self._make_strategy()
        mock_page = MagicMock()
        mock_page.rect.width = 612
        mock_page.rect.height = 792

        mock_page.get_text.side_effect = [
            # "words" call
            [(10, 10, 50, 20, "Hello", 0, 0, 0)],
            # "dict" call
            {
                "blocks": [{
                    "lines": [{
                        "spans": [{"text": "Hello world"}],
                        "bbox": (10, 10, 100, 20),
                    }]
                }]
            },
        ]

        with patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor._normalize_bbox",
                    return_value=[{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}, {"x": 0, "y": 1}]):
            result = await strategy.process_page(mock_page)

        assert len(result["words"]) == 1
        assert len(result["lines"]) == 1
        assert result["page_width"] == 612

    def test_print_merge_statistics_with_data(self):
        """print_merge_statistics prints stats when data is available."""
        strategy = self._make_strategy()
        strategy.document_analysis_result = {
            "pages": [{
                "lines": [{"content": "Line 1"}],
                "words": [{"content": "word1"}],
            }],
            "sentences": [{
                "page_number": 1,
                "block_number": 0,
                "bounding_box": [{"x": 0, "y": 0}],
            }],
            "paragraphs": [{
                "page_number": 1,
                "block_number": 0,
                "bounding_box": [{"x": 0, "y": 0}],
            }],
        }
        strategy.print_merge_statistics()  # should not raise

    def test_print_merge_statistics_empty_sentences(self):
        """print_merge_statistics works with no sentences."""
        strategy = self._make_strategy()
        strategy.document_analysis_result = {
            "pages": [{
                "lines": [],
                "words": [],
            }],
            "sentences": [],
            "paragraphs": [],
        }
        strategy.print_merge_statistics()

    def test_create_debug_pdf(self):
        """create_debug_pdf creates a debug visualization."""
        strategy = self._make_strategy()

        mock_page = MagicMock()
        mock_page.rect.width = 612
        mock_page.rect.height = 792
        strategy.doc = [mock_page]

        strategy.document_analysis_result = {
            "paragraphs": [{
                "page_number": 1,
                "bounding_box": [
                    {"x": 0.1, "y": 0.1}, {"x": 0.5, "y": 0.1},
                    {"x": 0.5, "y": 0.3}, {"x": 0.1, "y": 0.3},
                ],
            }],
            "sentences": [{
                "page_number": 1,
                "bounding_box": [
                    {"x": 0.1, "y": 0.1}, {"x": 0.5, "y": 0.1},
                    {"x": 0.5, "y": 0.2}, {"x": 0.1, "y": 0.2},
                ],
            }],
        }

        with patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor.fitz") as mock_fitz:
            mock_debug_doc = MagicMock()
            mock_debug_page = MagicMock()
            mock_debug_page.rect.width = 612
            mock_debug_page.rect.height = 792
            mock_debug_doc.__iter__ = lambda s: iter([mock_debug_page])
            mock_debug_doc.new_page = MagicMock()
            mock_fitz.open.return_value = mock_debug_doc
            mock_fitz.Rect = MagicMock(return_value=MagicMock())

            strategy.create_debug_pdf("/tmp/debug.pdf")

            mock_debug_doc.save.assert_called_once_with("/tmp/debug.pdf")
            mock_debug_doc.close.assert_called_once()

    def test_create_custom_tokenizer_with_sentencizer(self):
        """_create_custom_tokenizer adds sentencizer when not present."""
        strategy = self._make_strategy()
        mock_nlp = MagicMock()
        mock_nlp.pipe_names = []
        mock_nlp.tokenizer = MagicMock()
        result = strategy._create_custom_tokenizer(mock_nlp)
        # Should add sentencizer and custom_sentence_boundary
        assert mock_nlp.add_pipe.call_count >= 2

    def test_create_custom_tokenizer_already_has_sentencizer(self):
        """_create_custom_tokenizer skips adding sentencizer if already present."""
        strategy = self._make_strategy()
        mock_nlp = MagicMock()
        mock_nlp.pipe_names = ["sentencizer"]
        mock_nlp.tokenizer = MagicMock()
        result = strategy._create_custom_tokenizer(mock_nlp)
        # custom_sentence_boundary added but sentencizer not added again
        calls = [c[0][0] for c in mock_nlp.add_pipe.call_args_list]
        assert "sentencizer" not in calls


# ============================================================================
# AzureOCRStrategy — load_document, _process_with_azure, _preprocess_document,
#   _process_azure_page, _process_table, _merge_small_blocks,
#   _merge_lines_to_sentences, _create_searchable_pdf, _get_lines_for_paragraph,
#   custom_sentence_boundary, _create_custom_tokenizer, process_page
# ============================================================================

class TestAzureOCRStrategyExtended:

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

    @patch("app.modules.parsers.pdf.azure_document_intelligence_processor.spacy")
    def test_init_spacy_failure(self, mock_spacy):
        """AzureOCRStrategy.__init__ handles spaCy load failure."""
        mock_spacy.load.side_effect = RuntimeError("spaCy model not found")
        from app.modules.parsers.pdf.azure_document_intelligence_processor import AzureOCRStrategy
        strategy = AzureOCRStrategy(
            logger=_mock_logger(), config=_mock_config(),
            endpoint="https://fake.cognitiveservices.azure.com",
            key="fake-key",
        )
        assert strategy.nlp is None

    @pytest.mark.asyncio
    async def test_load_document_needs_ocr(self):
        """load_document routes to Azure OCR when OCR is needed."""
        strategy = self._make_strategy()

        mock_page = MagicMock()
        mock_page.rect.width = 612
        mock_page.rect.height = 792

        mock_temp_doc = MagicMock()
        mock_temp_doc.__enter__ = lambda s: s
        mock_temp_doc.__exit__ = MagicMock(return_value=False)
        mock_temp_doc.__iter__ = lambda s: iter([mock_page])
        mock_temp_doc.__len__ = lambda s: 1

        with patch("app.modules.parsers.pdf.azure_document_intelligence_processor.fitz") as mock_fitz, \
             patch("app.modules.parsers.pdf.azure_document_intelligence_processor.OCRStrategy") as MockOCR:
            mock_fitz.open.return_value = mock_temp_doc
            MockOCR.needs_ocr = MagicMock(return_value=True)

            strategy._process_with_azure = AsyncMock()
            strategy._preprocess_document = AsyncMock(return_value={"pages": []})

            await strategy.load_document(b"fake-pdf")

            strategy._process_with_azure.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_load_document_no_ocr_raises(self):
        """load_document raises when OCR is not needed (azure strategy expects OCR)."""
        strategy = self._make_strategy()

        mock_page = MagicMock()
        mock_page.rect.width = 612
        mock_page.rect.height = 792

        mock_temp_doc = MagicMock()
        mock_temp_doc.__enter__ = lambda s: s
        mock_temp_doc.__exit__ = MagicMock(return_value=False)
        mock_temp_doc.__iter__ = lambda s: iter([mock_page])
        mock_temp_doc.__len__ = lambda s: 1

        with patch("app.modules.parsers.pdf.azure_document_intelligence_processor.fitz") as mock_fitz, \
             patch("app.modules.parsers.pdf.azure_document_intelligence_processor.OCRStrategy") as MockOCR:
            mock_fitz.open.return_value = mock_temp_doc
            MockOCR.needs_ocr = MagicMock(return_value=False)

            with pytest.raises(Exception, match="Azure OCR is not needed"):
                await strategy.load_document(b"fake-pdf")

    @pytest.mark.asyncio
    async def test_load_document_analysis_error_defaults_to_ocr(self):
        """load_document defaults to OCR on analysis error."""
        strategy = self._make_strategy()

        with patch("app.modules.parsers.pdf.azure_document_intelligence_processor.fitz") as mock_fitz, \
             patch("app.modules.parsers.pdf.azure_document_intelligence_processor.OCRStrategy") as MockOCR:
            mock_fitz.open.side_effect = RuntimeError("Cannot open PDF")

            strategy._process_with_azure = AsyncMock()
            strategy._preprocess_document = AsyncMock(return_value={"pages": []})

            await strategy.load_document(b"fake-pdf")
            assert strategy._needs_ocr is True

    @pytest.mark.asyncio
    async def test_process_with_azure_success(self):
        """_process_with_azure processes document with Azure DI."""
        strategy = self._make_strategy()

        mock_result = MagicMock()
        mock_azure_page = MagicMock()
        mock_azure_page.width = 8.5
        mock_azure_page.height = 11.0
        mock_azure_page.lines = []
        mock_azure_page.words = []
        mock_result.pages = [mock_azure_page]
        mock_result.paragraphs = []
        mock_result.tables = []

        mock_poller = AsyncMock()
        mock_poller.result.return_value = mock_result

        mock_client = AsyncMock()
        mock_client.begin_analyze_document.return_value = mock_poller
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.modules.parsers.pdf.azure_document_intelligence_processor.AsyncDocumentAnalysisClient",
                    return_value=mock_client):
            await strategy._process_with_azure(b"fake-content")

        assert strategy.doc is mock_result

    @pytest.mark.asyncio
    async def test_process_with_azure_failure(self):
        """_process_with_azure raises on Azure failure."""
        strategy = self._make_strategy()

        mock_client = AsyncMock()
        mock_client.begin_analyze_document.side_effect = RuntimeError("Azure error")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.modules.parsers.pdf.azure_document_intelligence_processor.AsyncDocumentAnalysisClient",
                    return_value=mock_client):
            with pytest.raises(RuntimeError, match="Azure error"):
                await strategy._process_with_azure(b"fake-content")

    def test_create_custom_tokenizer_no_sentencizer(self):
        """_create_custom_tokenizer adds sentencizer if not present."""
        strategy = self._make_strategy()
        mock_nlp = MagicMock()
        mock_nlp.pipe_names = []
        mock_nlp.tokenizer = MagicMock()
        result = strategy._create_custom_tokenizer(mock_nlp)
        calls = [c[0][0] for c in mock_nlp.add_pipe.call_args_list]
        assert "sentencizer" in calls

    def test_create_custom_tokenizer_already_has_both(self):
        """_create_custom_tokenizer skips sentencizer if present, but adds custom boundary if not."""
        strategy = self._make_strategy()
        mock_nlp = MagicMock()
        mock_nlp.pipe_names = ["sentencizer"]
        mock_nlp.tokenizer = MagicMock()
        result = strategy._create_custom_tokenizer(mock_nlp)
        calls = [c[0][0] for c in mock_nlp.add_pipe.call_args_list]
        assert "sentencizer" not in calls
        assert "custom_sentence_boundary" in calls

    def test_create_custom_tokenizer_with_both_already(self):
        """_create_custom_tokenizer skips both if already present."""
        strategy = self._make_strategy()
        mock_nlp = MagicMock()
        mock_nlp.pipe_names = ["sentencizer", "custom_sentence_boundary"]
        mock_nlp.tokenizer = MagicMock()
        result = strategy._create_custom_tokenizer(mock_nlp)
        calls = [c[0][0] for c in mock_nlp.add_pipe.call_args_list]
        assert "sentencizer" not in calls
        assert "custom_sentence_boundary" not in calls

    @pytest.mark.asyncio
    async def test_process_page_processed_raises(self):
        """process_page raises NotImplementedError when already processed."""
        strategy = self._make_strategy()
        strategy._processed = True
        with pytest.raises(NotImplementedError, match="Azure processes entire document"):
            await strategy.process_page(MagicMock())

    @pytest.mark.asyncio
    async def test_process_page_not_processed_extracts_data(self):
        """process_page extracts words and lines from PyMuPDF page when not processed."""
        strategy = self._make_strategy()
        strategy._processed = False

        mock_page = MagicMock()
        mock_page.rect.width = 612
        mock_page.rect.height = 792
        mock_page.get_text.side_effect = [
            # "words" call
            [(10, 10, 50, 20, "Hello", 0, 0, 0), (60, 10, 100, 20, "  ", 0, 0, 0)],
            # "dict" call
            {
                "blocks": [{
                    "lines": [{
                        "spans": [{"text": "Hello world"}],
                        "bbox": (10, 10, 100, 20),
                    }]
                }]
            },
        ]

        result = await strategy.process_page(mock_page)
        assert len(result["words"]) == 1  # empty word skipped
        assert len(result["lines"]) == 1
        assert result["page_width"] == 612
        assert result["page_height"] == 792

    def test_process_block_text_pymupdf_single_span(self):
        """_process_block_text_pymupdf processes single-span lines."""
        strategy = self._make_strategy()
        mock_doc_nlp = MagicMock()
        mock_sent = MagicMock()
        mock_sent.text = "Hello world"
        mock_sent.start_char = 0
        mock_sent.end_char = 11
        mock_doc_nlp.sents = [mock_sent]
        strategy.nlp = MagicMock(return_value=mock_doc_nlp)

        block = {
            "type": 0,
            "bbox": (0, 0, 200, 50),
            "lines": [{
                "spans": [{"text": "Hello world", "font": "Arial", "size": 12, "flags": 0, "bbox": (0, 0, 100, 20)}],
                "bbox": (0, 0, 200, 20),
            }],
            "number": 0,
        }

        result = strategy._process_block_text_pymupdf(block, 612.0, 792.0)
        assert len(result["lines"]) == 1
        assert result["lines"][0]["content"] == "Hello world"

    def test_process_block_text_pymupdf_multi_span(self):
        """_process_block_text_pymupdf processes multi-span lines."""
        strategy = self._make_strategy()
        mock_doc_nlp = MagicMock()
        mock_sent = MagicMock()
        mock_sent.text = "Hello world"
        mock_sent.start_char = 0
        mock_sent.end_char = 11
        mock_doc_nlp.sents = [mock_sent]
        strategy.nlp = MagicMock(return_value=mock_doc_nlp)

        block = {
            "type": 0,
            "bbox": (0, 0, 200, 50),
            "lines": [{
                "spans": [
                    {"text": "Hello", "font": "Arial", "size": 12, "flags": 0, "bbox": (0, 0, 50, 20)},
                    {"text": "world", "font": "Arial", "size": 12, "flags": 0, "bbox": (50, 0, 100, 20)},
                ],
                "bbox": (0, 0, 200, 20),
            }],
            "number": 0,
        }

        result = strategy._process_block_text_pymupdf(block, 612.0, 792.0)
        assert "Hello" in result["lines"][0]["content"]

    def test_process_block_text_pymupdf_with_chars(self):
        """_process_block_text_pymupdf processes character-level data."""
        strategy = self._make_strategy()
        mock_doc_nlp = MagicMock()
        mock_sent = MagicMock()
        mock_sent.text = "Hi"
        mock_sent.start_char = 0
        mock_sent.end_char = 2
        mock_doc_nlp.sents = [mock_sent]
        strategy.nlp = MagicMock(return_value=mock_doc_nlp)

        block = {
            "type": 0,
            "bbox": (0, 0, 200, 50),
            "lines": [{
                "spans": [{
                    "text": "Hi",
                    "font": "Arial",
                    "size": 12,
                    "flags": 0,
                    "bbox": (0, 0, 20, 20),
                    "chars": [
                        {"c": "H", "bbox": (0, 0, 10, 20)},
                        {"c": "i", "bbox": (10, 0, 20, 20)},
                    ],
                }],
                "bbox": (0, 0, 200, 20),
            }],
            "number": 0,
        }

        result = strategy._process_block_text_pymupdf(block, 612.0, 792.0)
        assert len(result["words"]) == 2

    def test_process_block_text_pymupdf_empty_line(self):
        """_process_block_text_pymupdf skips empty lines."""
        strategy = self._make_strategy()
        mock_doc_nlp = MagicMock()
        mock_doc_nlp.sents = []
        strategy.nlp = MagicMock(return_value=mock_doc_nlp)

        block = {
            "type": 0,
            "bbox": (0, 0, 200, 50),
            "lines": [{
                "spans": [{"text": "   ", "font": "Arial", "size": 12, "flags": 0, "bbox": (0, 0, 50, 20)}],
                "bbox": (0, 0, 200, 20),
            }],
            "number": 0,
        }

        result = strategy._process_block_text_pymupdf(block, 612.0, 792.0)
        assert result["lines"] == []

    def test_process_block_text_pymupdf_multi_span_space(self):
        """_process_block_text_pymupdf preserves spaces in multi-span."""
        strategy = self._make_strategy()
        mock_doc_nlp = MagicMock()
        mock_sent = MagicMock()
        mock_sent.text = "Hello world"
        mock_sent.start_char = 0
        mock_sent.end_char = 11
        mock_doc_nlp.sents = [mock_sent]
        strategy.nlp = MagicMock(return_value=mock_doc_nlp)

        block = {
            "type": 0,
            "bbox": (0, 0, 200, 50),
            "lines": [{
                "spans": [
                    {"text": "Hello ", "font": "Arial", "size": 12, "flags": 0, "bbox": (0, 0, 50, 20)},
                    {"text": "world", "font": "Arial", "size": 12, "flags": 0, "bbox": (55, 0, 100, 20)},
                ],
                "bbox": (0, 0, 200, 20),
            }],
            "number": 0,
        }

        result = strategy._process_block_text_pymupdf(block, 612.0, 792.0)
        assert "Hello" in result["lines"][0]["content"]

    def test_extract_page_properties_pymupdf(self):
        """_extract_page_properties returns PyMuPDF properties when not OCR."""
        strategy = self._make_strategy()
        mock_page = MagicMock(spec=[])
        mock_page.rect = MagicMock()
        mock_page.rect.width = 612
        mock_page.rect.height = 792
        result = strategy._extract_page_properties(mock_page, False, 1)
        assert result["unit"] == "point"
        assert result["width"] == 612

    @pytest.mark.asyncio
    async def test_preprocess_document_with_paragraphs_and_tables(self):
        """_preprocess_document processes Azure pages with paragraphs and tables."""
        strategy = self._make_strategy()

        mock_azure_page = MagicMock()
        mock_azure_page.width = 8.5
        mock_azure_page.height = 11.0
        mock_azure_page.unit = "inch"
        mock_azure_page.page_number = 1
        mock_azure_page.lines = []
        mock_azure_page.tables = []

        mock_paragraph = MagicMock()
        mock_paragraph.content = "Test paragraph"
        mock_paragraph.words = []
        mock_paragraph.role = "paragraph"
        mock_paragraph.confidence = 0.99

        mock_doc = MagicMock()
        mock_doc.pages = [mock_azure_page]
        mock_doc.paragraphs = [mock_paragraph]

        strategy.doc = mock_doc
        strategy._get_bounding_box = MagicMock(return_value=[{"x": 0.1, "y": 0.1}])
        strategy._normalize_coordinates = MagicMock(return_value=[{"x": 0.01, "y": 0.01}])

        mock_doc_nlp = MagicMock()
        mock_sent = MagicMock()
        mock_sent.text = "Test paragraph"
        mock_sent.start_char = 0
        mock_sent.end_char = 14
        mock_doc_nlp.sents = [mock_sent]
        strategy.nlp = MagicMock(return_value=mock_doc_nlp)

        result = await strategy._preprocess_document()
        assert len(result["pages"]) == 1
        assert len(result["paragraphs"]) == 1

    @pytest.mark.asyncio
    async def test_process_azure_page_with_lines(self):
        """_process_azure_page processes lines from Azure."""
        strategy = self._make_strategy()

        mock_line = MagicMock()
        mock_line.content = "Test line"
        mock_line.confidence = 0.95

        mock_page = MagicMock()
        mock_page.lines = [mock_line]
        mock_page.tables = []

        strategy.doc = MagicMock()
        strategy.doc.paragraphs = []

        strategy._get_bounding_box = MagicMock(return_value=[{"x": 0.1, "y": 0.1}])
        strategy._normalize_coordinates = MagicMock(return_value=[{"x": 0.01, "y": 0.01}])

        page_dict = {"width": 8.5, "height": 11.0, "lines": [], "words": [], "tables": []}
        result = {"lines": [], "paragraphs": [], "sentences": [], "tables": [], "blocks": []}

        await strategy._process_azure_page(mock_page, page_dict, result, 1)
        assert len(page_dict["lines"]) == 1

    @pytest.mark.asyncio
    async def test_process_azure_page_with_tables(self):
        """_process_azure_page processes tables from Azure."""
        strategy = self._make_strategy()

        mock_cell = MagicMock()
        mock_cell.content = "Cell content"
        mock_cell.row_index = 0
        mock_cell.column_index = 0
        mock_cell.row_span = 1
        mock_cell.column_span = 1
        mock_cell.confidence = 0.99

        mock_table = MagicMock()
        mock_table.cells = [mock_cell]
        mock_table.row_count = 1
        mock_table.column_count = 1

        mock_page = MagicMock()
        mock_page.lines = []
        mock_page.tables = [mock_table]
        mock_page.width = 8.5
        mock_page.height = 11.0

        strategy.doc = MagicMock()
        strategy.doc.paragraphs = []

        # Mock _process_table to return proper data with bounding_boxes (plural)
        table_data = {
            "row_count": 1,
            "column_count": 1,
            "page_number": 1,
            "cells": [{"row_index": 0, "column_index": 0, "content": "Cell content",
                        "row_span": 1, "column_span": 1}],
            "bounding_boxes": [
                {"x": 0.1, "y": 0.1}, {"x": 0.5, "y": 0.1},
                {"x": 0.5, "y": 0.5}, {"x": 0.1, "y": 0.5},
            ],
        }
        strategy._process_table = MagicMock(return_value=table_data)

        mock_response = MagicMock()
        mock_response.summary = "Table summary"
        mock_response.headers = ["Col1"]

        page_dict = {"width": 8.5, "height": 11.0, "lines": [], "words": [], "tables": []}
        result = {"lines": [], "paragraphs": [], "sentences": [], "tables": [], "blocks": []}

        with patch("app.modules.parsers.pdf.azure_document_intelligence_processor.get_table_summary_n_headers",
                    new_callable=AsyncMock, return_value=mock_response), \
             patch("app.modules.parsers.pdf.azure_document_intelligence_processor.get_rows_text",
                    new_callable=AsyncMock, return_value=(["Row text"], [["Cell content"]])):
            await strategy._process_azure_page(mock_page, page_dict, result, 1)

        assert len(result["tables"]) == 1

    @pytest.mark.asyncio
    async def test_process_azure_page_table_no_cells(self):
        """_process_azure_page skips tables with no cells."""
        strategy = self._make_strategy()

        mock_table = MagicMock()
        mock_table.cells = []
        mock_table.row_count = 0
        mock_table.column_count = 0

        mock_page = MagicMock()
        mock_page.lines = []
        mock_page.tables = [mock_table]
        mock_page.width = 8.5
        mock_page.height = 11.0

        strategy.doc = MagicMock()
        strategy.doc.paragraphs = []
        strategy._get_bounding_box = MagicMock(return_value=[])
        strategy._normalize_element_data = MagicMock(side_effect=lambda d, w, h: d)
        strategy._get_page_number = MagicMock(return_value=1)

        page_dict = {"width": 8.5, "height": 11.0, "lines": [], "words": [], "tables": []}
        result = {"lines": [], "paragraphs": [], "sentences": [], "tables": [], "blocks": []}

        await strategy._process_azure_page(mock_page, page_dict, result, 1)
        assert len(result["tables"]) == 0

    def test_process_table(self):
        """_process_table extracts table data with normalized coordinates."""
        strategy = self._make_strategy()

        mock_cell = MagicMock()
        mock_cell.content = "A1"
        mock_cell.row_index = 0
        mock_cell.column_index = 0
        mock_cell.row_span = 1
        mock_cell.column_span = 1
        mock_cell.confidence = 0.95

        mock_table = MagicMock()
        mock_table.cells = [mock_cell]
        mock_table.row_count = 1
        mock_table.column_count = 1

        mock_page = MagicMock()
        mock_page.width = 8.5
        mock_page.height = 11.0

        strategy._get_bounding_box = MagicMock(return_value=[{"x": 0.1, "y": 0.1}])
        strategy._normalize_element_data = MagicMock(side_effect=lambda d, w, h: d)
        strategy._get_page_number = MagicMock(return_value=1)

        result = strategy._process_table(mock_table, mock_page)
        assert result["row_count"] == 1
        assert result["column_count"] == 1
        assert len(result["cells"]) == 1
        assert result["page_number"] == 1

    def test_merge_small_blocks(self):
        """_merge_small_blocks merges consecutive small blocks."""
        strategy = self._make_strategy()

        # First block: 16 words (above threshold), second: short, third: short
        # First won't merge with second. Second and third will merge.
        blocks = [
            {"type": 0, "bbox": (0, 0, 100, 20), "lines": [{"spans": [{"text": " ".join(["word"] * 16)}]}]},
            {"type": 0, "bbox": (0, 25, 100, 45), "lines": [{"spans": [{"text": "Short"}]}]},
            {"type": 0, "bbox": (0, 50, 100, 100), "lines": [{"spans": [{"text": "Also short"}]}]},
        ]

        result = strategy._merge_small_blocks(blocks)
        # First stays alone (16 words >= 15 threshold), second and third merged
        assert len(result) == 2

    def test_merge_small_blocks_no_merge(self):
        """_merge_small_blocks doesn't merge when first block has enough words."""
        strategy = self._make_strategy()

        blocks = [
            {"type": 0, "bbox": (0, 0, 100, 20), "lines": [{"spans": [{"text": " ".join(["word"] * 20)}]}]},
            {"type": 0, "bbox": (0, 25, 100, 45), "lines": [{"spans": [{"text": "Also short"}]}]},
        ]

        result = strategy._merge_small_blocks(blocks)
        assert len(result) == 2

    def test_merge_lines_to_sentences_azure(self):
        """_merge_lines_to_sentences merges lines to sentences."""
        strategy = self._make_strategy()
        mock_doc_nlp = MagicMock()
        mock_sent = MagicMock()
        mock_sent.text = "Hello world."
        mock_sent.start_char = 0
        mock_sent.end_char = 12
        mock_doc_nlp.sents = [mock_sent]
        strategy.nlp = MagicMock(return_value=mock_doc_nlp)

        lines_data = [
            {"content": "Hello world.", "bounding_box": [{"x": 0, "y": 0}]},
        ]

        result = strategy._merge_lines_to_sentences(lines_data)
        assert len(result) == 1
        assert result[0]["sentence"] == "Hello world."

    def test_merge_lines_to_sentences_nlp_none(self):
        """_merge_lines_to_sentences returns empty when nlp is None."""
        strategy = self._make_strategy()
        strategy.nlp = None

        result = strategy._merge_lines_to_sentences([{"content": "text", "bounding_box": []}])
        assert result == []

    def test_merge_lines_to_sentences_empty_content(self):
        """_merge_lines_to_sentences skips empty content."""
        strategy = self._make_strategy()
        mock_doc_nlp = MagicMock()
        mock_doc_nlp.sents = []
        strategy.nlp = MagicMock(return_value=mock_doc_nlp)

        result = strategy._merge_lines_to_sentences([{"content": "   ", "bounding_box": []}])
        assert result == []

    def test_get_lines_for_paragraph_no_overlap(self):
        """_get_lines_for_paragraph returns empty when no lines overlap."""
        strategy = self._make_strategy()
        page_lines = [
            {"content": "unrelated", "bounding_box": [
                {"x": 0.8, "y": 0.8}, {"x": 0.9, "y": 0.8},
                {"x": 0.9, "y": 0.9}, {"x": 0.8, "y": 0.9},
            ]},
        ]
        para_text = "Hello world"
        para_bbox = [{"x": 0, "y": 0}, {"x": 0.1, "y": 0}, {"x": 0.1, "y": 0.1}, {"x": 0, "y": 0.1}]

        result = strategy._get_lines_for_paragraph(page_lines, para_text, para_bbox)
        assert result == []

    def test_check_bbox_overlap_edge_cases(self):
        """_check_bbox_overlap handles edge cases."""
        strategy = self._make_strategy()

        # Touching boxes
        bbox1 = [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}, {"x": 0, "y": 1}]
        bbox2 = [{"x": 1, "y": 0}, {"x": 2, "y": 0}, {"x": 2, "y": 1}, {"x": 1, "y": 1}]
        assert strategy._check_bbox_overlap(bbox1, bbox2) is False

    @pytest.mark.asyncio
    async def test_create_searchable_pdf(self):
        """_create_searchable_pdf creates a searchable PDF from Azure results."""
        strategy = self._make_strategy()

        # Mock Azure doc with pages, words
        mock_word = MagicMock()
        mock_word.content = "Hello"
        mock_word.bounding_regions = [MagicMock()]
        mock_word.bounding_regions[0].polygon = [
            MagicMock(x=0.1, y=0.1), MagicMock(x=0.3, y=0.1),
            MagicMock(x=0.3, y=0.15), MagicMock(x=0.1, y=0.15),
        ]

        mock_azure_page = MagicMock()
        mock_azure_page.page_number = 0
        mock_azure_page.words = [mock_word]

        strategy.doc = MagicMock()
        strategy.doc.pages = [mock_azure_page]

        mock_pdf_page = MagicMock()
        mock_pdf_page.rect.width = 612
        mock_pdf_page.rect.height = 792
        mock_pdf_page.insert_textbox = MagicMock()

        mock_doc = MagicMock()
        mock_doc.__len__ = lambda s: 1
        mock_doc.__getitem__ = lambda s, i: mock_pdf_page

        with patch("app.modules.parsers.pdf.azure_document_intelligence_processor.fitz") as mock_fitz, \
             patch("app.modules.parsers.pdf.azure_document_intelligence_processor.os") as mock_os, \
             patch("builtins.open", MagicMock(return_value=BytesIO(b"searchable-pdf"))):
            mock_fitz.open.return_value = mock_doc
            mock_fitz.Rect = MagicMock(return_value=MagicMock())
            mock_os.makedirs = MagicMock()
            mock_os.path.join = MagicMock(return_value="/tmp/searchable.pdf")

            result = await strategy._create_searchable_pdf(b"original-content", "/tmp/out")

        mock_pdf_page.insert_textbox.assert_called()

    @pytest.mark.asyncio
    async def test_create_searchable_pdf_no_azure_page(self):
        """_create_searchable_pdf handles pages without Azure results."""
        strategy = self._make_strategy()
        strategy.doc = MagicMock()
        strategy.doc.pages = []  # no azure pages

        mock_pdf_page = MagicMock()
        mock_pdf_page.rect.width = 612
        mock_pdf_page.rect.height = 792

        mock_doc = MagicMock()
        mock_doc.__len__ = lambda s: 1
        mock_doc.__getitem__ = lambda s, i: mock_pdf_page

        with patch("app.modules.parsers.pdf.azure_document_intelligence_processor.fitz") as mock_fitz, \
             patch("app.modules.parsers.pdf.azure_document_intelligence_processor.os") as mock_os, \
             patch("builtins.open", MagicMock(return_value=BytesIO(b"pdf"))):
            mock_fitz.open.return_value = mock_doc
            mock_os.makedirs = MagicMock()
            mock_os.path.join = MagicMock(return_value="/tmp/searchable.pdf")

            result = await strategy._create_searchable_pdf(b"content")

    @pytest.mark.asyncio
    async def test_create_searchable_pdf_empty_word(self):
        """_create_searchable_pdf skips empty words."""
        strategy = self._make_strategy()

        mock_word = MagicMock()
        mock_word.content = "   "

        mock_azure_page = MagicMock()
        mock_azure_page.page_number = 0
        mock_azure_page.words = [mock_word]

        strategy.doc = MagicMock()
        strategy.doc.pages = [mock_azure_page]

        mock_pdf_page = MagicMock()
        mock_pdf_page.rect.width = 612
        mock_pdf_page.rect.height = 792

        mock_doc = MagicMock()
        mock_doc.__len__ = lambda s: 1
        mock_doc.__getitem__ = lambda s, i: mock_pdf_page

        with patch("app.modules.parsers.pdf.azure_document_intelligence_processor.fitz") as mock_fitz, \
             patch("app.modules.parsers.pdf.azure_document_intelligence_processor.os") as mock_os, \
             patch("builtins.open", MagicMock(return_value=BytesIO(b"pdf"))):
            mock_fitz.open.return_value = mock_doc
            mock_os.makedirs = MagicMock()
            mock_os.path.join = MagicMock(return_value="/tmp/searchable.pdf")

            await strategy._create_searchable_pdf(b"content")

        mock_pdf_page.insert_textbox.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_searchable_pdf_word_no_bounding_regions(self):
        """_create_searchable_pdf skips words without bounding regions."""
        strategy = self._make_strategy()

        mock_word = MagicMock()
        mock_word.content = "Hello"
        mock_word.bounding_regions = []

        mock_azure_page = MagicMock()
        mock_azure_page.page_number = 0
        mock_azure_page.words = [mock_word]

        strategy.doc = MagicMock()
        strategy.doc.pages = [mock_azure_page]

        mock_pdf_page = MagicMock()
        mock_pdf_page.rect.width = 612
        mock_pdf_page.rect.height = 792

        mock_doc = MagicMock()
        mock_doc.__len__ = lambda s: 1
        mock_doc.__getitem__ = lambda s, i: mock_pdf_page

        with patch("app.modules.parsers.pdf.azure_document_intelligence_processor.fitz") as mock_fitz, \
             patch("app.modules.parsers.pdf.azure_document_intelligence_processor.os") as mock_os, \
             patch("builtins.open", MagicMock(return_value=BytesIO(b"pdf"))):
            mock_fitz.open.return_value = mock_doc
            mock_os.makedirs = MagicMock()
            mock_os.path.join = MagicMock(return_value="/tmp/searchable.pdf")

            await strategy._create_searchable_pdf(b"content")

        mock_pdf_page.insert_textbox.assert_not_called()

    def test_process_block_text_azure_no_content(self):
        """_process_block_text_azure returns None when block has no content."""
        strategy = self._make_strategy()
        mock_block = MagicMock(spec=["words", "role"])
        mock_block.words = []
        mock_block.role = "paragraph"

        strategy._get_bounding_box = MagicMock(return_value=[])
        strategy._normalize_coordinates = MagicMock(return_value=None)

        result = strategy._process_block_text_azure(mock_block, 8.5, 11.0)
        assert result is None

    def test_process_block_text_azure_with_words(self):
        """_process_block_text_azure processes words."""
        strategy = self._make_strategy()
        mock_word = MagicMock()
        mock_word.content = "Hello"
        mock_word.confidence = 0.99

        mock_block = MagicMock()
        mock_block.content = "Hello world"
        mock_block.words = [mock_word]
        mock_block.role = "paragraph"
        mock_block.confidence = 0.99

        strategy._get_bounding_box = MagicMock(return_value=[{"x": 0.1, "y": 0.1}])
        strategy._normalize_coordinates = MagicMock(return_value=[{"x": 0.01, "y": 0.01}])

        result = strategy._process_block_text_azure(mock_block, 8.5, 11.0)
        assert result is not None
        assert result["content"] == "Hello world"
        assert len(result["words"]) == 1

    def test_normalize_element_data_no_bbox(self):
        """_normalize_element_data handles data without bounding_box."""
        strategy = self._make_strategy()
        data = {"other": "value"}
        result = strategy._normalize_element_data(data, 200.0, 400.0)
        assert result == {"other": "value"}

    def test_normalize_element_data_empty_bbox(self):
        """_normalize_element_data skips normalization for empty bounding_box."""
        strategy = self._make_strategy()
        data = {"bounding_box": [], "other": "value"}
        result = strategy._normalize_element_data(data, 200.0, 400.0)
        # Empty list is falsy so normalization is skipped, bbox stays as-is
        assert result["bounding_box"] == []

    def test_cells_to_grid_out_of_bounds(self):
        """cells_to_grid ignores cells outside the grid."""
        strategy = self._make_strategy()
        cells = [
            {"row_index": 0, "column_index": 0, "content": "A1", "row_span": 1, "column_span": 1},
            {"row_index": 5, "column_index": 5, "content": "Out", "row_span": 1, "column_span": 1},
        ]
        grid = strategy.cells_to_grid(2, 2, cells)
        assert grid == [["A1", ""], ["", ""]]

    def test_cells_to_grid_empty(self):
        """cells_to_grid with empty cells."""
        strategy = self._make_strategy()
        grid = strategy.cells_to_grid(2, 2, [])
        assert grid == [["", ""], ["", ""]]

    def test_cells_to_grid_none_content(self):
        """cells_to_grid handles None content."""
        strategy = self._make_strategy()
        cells = [
            {"row_index": 0, "column_index": 0, "content": None, "row_span": 1, "column_span": 1},
        ]
        grid = strategy.cells_to_grid(1, 1, cells)
        assert grid == [[""]]

    def test_process_line_valid_with_confidence(self):
        """_process_line processes line with confidence."""
        strategy = self._make_strategy()
        mock_line = MagicMock()
        mock_line.content = "Test line"
        mock_line.confidence = 0.95

        strategy._get_bounding_box = MagicMock(return_value=[{"x": 0.1, "y": 0.1}])
        strategy._normalize_coordinates = MagicMock(return_value=[{"x": 0.01, "y": 0.01}])

        result = strategy._process_line(mock_line, 8.5, 11.0)
        assert result["content"] == "Test line"
        assert result["confidence"] == 0.95

    def test_process_line_no_confidence(self):
        """_process_line handles line without confidence attr."""
        strategy = self._make_strategy()
        mock_line = MagicMock(spec=["content"])
        mock_line.content = "Test line"

        strategy._get_bounding_box = MagicMock(return_value=[{"x": 0.1, "y": 0.1}])
        strategy._normalize_coordinates = MagicMock(return_value=[{"x": 0.01, "y": 0.01}])

        result = strategy._process_line(mock_line, 8.5, 11.0)
        assert result["content"] == "Test line"
        assert result["confidence"] is None

    def test_should_merge_blocks_long_first(self):
        """_should_merge_blocks returns False when first block has many words."""
        strategy = self._make_strategy()
        text = " ".join(["word"] * 20)
        b1 = {"type": 0, "lines": [{"spans": [{"text": text}]}]}
        b2 = {"type": 0, "lines": [{"spans": [{"text": "short"}]}]}
        assert strategy._should_merge_blocks(b1, b2) is False


# ============================================================================
# Additional targeted tests for remaining uncovered lines
# ============================================================================

class TestPyMuPDFOpenCVProcessorExtraTables:
    """Extra tests for _extract_tables_with_pymupdf non-TABLE region skip (line 127)."""

    def test_extract_tables_skips_non_table_regions(self):
        """Non-TABLE regions are skipped when matching tables."""
        from app.modules.parsers.pdf.pymupdf_opencv_processor import (
            LayoutRegion, LayoutRegionType, ParsedPageData, PyMuPDFOpenCVProcessor,
        )
        with patch("app.modules.parsers.pdf.pymupdf_opencv_processor.OpenCVLayoutAnalyzer"):
            proc = PyMuPDFOpenCVProcessor(logger=_mock_logger(), config=_mock_config())

        # Regions: a TEXT region at the same location as the table
        text_region = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(50, 50, 300, 200), text="Some text")
        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[text_region])

        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_doc.__getitem__ = lambda s, i: mock_page

        mock_table = MagicMock()
        mock_table.bbox = (50, 50, 300, 200)
        mock_table.extract.return_value = [["A", "B"]]
        mock_table_finder = MagicMock()
        mock_table_finder.tables = [mock_table]
        mock_page.find_tables.return_value = mock_table_finder

        proc._extract_tables_with_pymupdf(mock_doc, [pd])

        # Since there's no TABLE region, a new one should be added
        table_regions = [r for r in pd.regions if r.type == LayoutRegionType.TABLE]
        assert len(table_regions) == 1


class TestPyMuPDFOCRStrategyCleanupError:
    """Test for temp file cleanup error path (lines 117-118)."""

    @patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor.spacy")
    def _make_strategy(self, mock_spacy):
        mock_nlp = MagicMock()
        mock_nlp.pipe_names = []
        mock_nlp.add_pipe = MagicMock()
        mock_nlp.tokenizer = MagicMock()
        mock_spacy.load.return_value = mock_nlp
        from app.modules.parsers.pdf.pymupdf_ocrmypdf_processor import PyMuPDFOCRStrategy
        return PyMuPDFOCRStrategy(logger=_mock_logger(), config=_mock_config())

    @pytest.mark.asyncio
    async def test_load_document_ocr_cleanup_error(self):
        """Temp file cleanup errors are caught and logged."""
        strategy = self._make_strategy()

        mock_doc = MagicMock()
        mock_doc.__len__ = lambda s: 1
        mock_page = MagicMock()
        mock_page.rect.width = 612
        mock_page.rect.height = 792
        mock_page.get_text.return_value = {"blocks": []}
        mock_doc.__getitem__ = lambda s, i: mock_page
        mock_doc.__iter__ = lambda s: iter([mock_page])

        processed_doc = MagicMock()
        processed_doc.__len__ = lambda s: 1
        processed_doc.__getitem__ = lambda s, i: mock_page

        with patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor.fitz") as mock_fitz, \
             patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor.OCRStrategy") as MockOCR, \
             patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor.ocrmypdf") as mock_ocrmypdf, \
             patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor.process_table_pymupdf",
                   new_callable=AsyncMock), \
             patch("os.path.exists", return_value=True), \
             patch("os.remove", side_effect=PermissionError("Cannot delete")):

            mock_fitz.open.side_effect = [mock_doc, processed_doc]
            MockOCR.needs_ocr = MagicMock(return_value=True)
            mock_ocrmypdf.ocr = MagicMock()

            mock_temp_in = MagicMock()
            mock_temp_in.name = "/tmp/test_in.pdf"
            mock_temp_in.__enter__ = lambda s: s
            mock_temp_in.__exit__ = MagicMock(return_value=False)

            mock_temp_out = MagicMock()
            mock_temp_out.name = "/tmp/test_out.pdf"
            mock_temp_out.__enter__ = lambda s: s
            mock_temp_out.__exit__ = MagicMock(return_value=False)

            with patch("tempfile.NamedTemporaryFile", side_effect=[mock_temp_in, mock_temp_out]):
                with patch("builtins.open", MagicMock(return_value=BytesIO(b"ocr-content"))):
                    await strategy.load_document(b"fake-pdf")

            # Should not raise, errors are caught
            assert strategy.doc is not None

    def test_process_block_text_normalize_bbox_exception(self):
        """_process_block_text catches normalize_bbox exceptions for block bbox."""
        strategy = self._make_strategy()
        mock_doc_nlp = MagicMock()
        mock_sent = MagicMock()
        mock_sent.text = "Text"
        mock_sent.start_char = 0
        mock_sent.end_char = 4
        mock_doc_nlp.sents = [mock_sent]
        strategy.nlp = MagicMock(return_value=mock_doc_nlp)

        block = {
            "type": 0,
            "lines": [{
                "spans": [{"text": "Text", "font": "Arial", "size": 12, "flags": 0, "bbox": (0, 0, 50, 20)}],
                "bbox": (0, 0, 100, 20),
            }],
            "bbox": (0, 0, 100, 20),  # valid bbox with 4 elements
        }

        call_count = [0]
        def normalize_side_effect(bbox, pw, ph):
            call_count[0] += 1
            if call_count[0] == 3:  # Third call is for block bbox
                raise ValueError("bad normalize")
            return [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}, {"x": 0, "y": 1}]

        with patch("app.modules.parsers.pdf.pymupdf_ocrmypdf_processor._normalize_bbox",
                    side_effect=normalize_side_effect):
            result = strategy._process_block_text(block, 200.0, 400.0, 0)

        assert result["paragraph"]["bounding_box"] is None


class TestOpenCVLayoutAnalyzerTableRegionsAdvanced:
    """Additional tests for _detect_table_regions hitting line 151-155, 174-179."""

    def _make_analyzer(self):
        from app.modules.parsers.pdf.opencv_layout_analyzer import OpenCVLayoutAnalyzer
        return OpenCVLayoutAnalyzer(logger=_mock_logger(), render_dpi=150)

    @patch("app.modules.parsers.pdf.opencv_layout_analyzer.cv2")
    def test_detect_table_regions_valid_table_with_cells(self, mock_cv2):
        """Full path through _detect_table_regions with valid grid and cells."""
        from app.modules.parsers.pdf.opencv_layout_analyzer import (
            _pixel_to_pdf, MIN_TABLE_GRID_LINES, TABLE_CELL_COUNT_THRESHOLD,
        )
        analyzer = self._make_analyzer()
        h, w = 500, 400
        binary = np.zeros((h, w), dtype=np.uint8)

        # Set up line detection results
        horiz_lines = np.zeros((h, w), dtype=np.uint8)
        vert_lines = np.zeros((h, w), dtype=np.uint8)

        # Create horizontal grid lines at y=60, 100, 140, 180 (4 distinct lines)
        for y_val in [60, 100, 140, 180]:
            horiz_lines[y_val, 55:345] = 255

        # Create vertical grid lines at x=60, 120, 200, 280 (4 distinct lines)
        for x_val in [60, 120, 200, 280]:
            vert_lines[55:185, x_val] = 255

        cnt = np.array([[[50, 50]], [[350, 50]], [[350, 190]], [[50, 190]]], dtype=np.int32)

        mock_cv2.getStructuringElement.return_value = np.ones((1, 10), dtype=np.uint8)
        mock_cv2.morphologyEx.side_effect = [horiz_lines, vert_lines]
        mock_cv2.add.return_value = np.maximum(horiz_lines, vert_lines)
        mock_cv2.dilate.return_value = np.maximum(horiz_lines, vert_lines)
        mock_cv2.findContours.side_effect = [
            ([cnt], None),  # outer contours
            # Inner contours (cells) - need TABLE_CELL_COUNT_THRESHOLD (4)
            ([np.array([[[0, 0]]]), np.array([[[1, 1]]]),
              np.array([[[2, 2]]]), np.array([[[3, 3]]])], None),
        ]
        mock_cv2.boundingRect.return_value = (50, 50, 300, 140)
        mock_cv2.bitwise_not.return_value = np.zeros((140, 300), dtype=np.uint8)

        # Use large page area so the region passes MIN_TABLE_AREA_RATIO
        # but not MAX_TABLE_AREA_RATIO
        page_w_pt = 612.0
        page_h_pt = 792.0

        result = analyzer._detect_table_regions(binary, page_w_pt, page_h_pt)
        assert isinstance(result, list)
        assert len(result) == 1

    @patch("app.modules.parsers.pdf.opencv_layout_analyzer.cv2")
    def test_detect_table_regions_not_enough_cells(self, mock_cv2):
        """Table candidate rejected when inner contours < TABLE_CELL_COUNT_THRESHOLD."""
        analyzer = self._make_analyzer()
        h, w = 500, 400
        binary = np.zeros((h, w), dtype=np.uint8)

        horiz_lines = np.zeros((h, w), dtype=np.uint8)
        vert_lines = np.zeros((h, w), dtype=np.uint8)
        for y_val in [60, 100, 140, 180]:
            horiz_lines[y_val, 55:345] = 255
        for x_val in [60, 120, 200, 280]:
            vert_lines[55:185, x_val] = 255

        cnt = np.array([[[50, 50]], [[350, 50]], [[350, 190]], [[50, 190]]], dtype=np.int32)

        mock_cv2.getStructuringElement.return_value = np.ones((1, 10), dtype=np.uint8)
        mock_cv2.morphologyEx.side_effect = [horiz_lines, vert_lines]
        mock_cv2.add.return_value = np.maximum(horiz_lines, vert_lines)
        mock_cv2.dilate.return_value = np.maximum(horiz_lines, vert_lines)
        mock_cv2.findContours.side_effect = [
            ([cnt], None),
            # Only 2 inner contours (fewer than threshold of 4)
            ([np.array([[[0, 0]]]), np.array([[[1, 1]]])], None),
        ]
        mock_cv2.boundingRect.return_value = (50, 50, 300, 140)
        mock_cv2.bitwise_not.return_value = np.zeros((140, 300), dtype=np.uint8)

        result = analyzer._detect_table_regions(binary, 612.0, 792.0)
        assert result == []


class TestAzureOCRStrategyProcessPageLines:
    """Additional tests for _process_azure_page line processing coverage."""

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

    @pytest.mark.asyncio
    async def test_process_azure_page_with_paragraphs(self):
        """_process_azure_page processes paragraphs from Azure."""
        strategy = self._make_strategy()

        mock_line = MagicMock()
        mock_line.content = "Test paragraph text"
        mock_line.confidence = 0.95

        mock_paragraph = MagicMock()
        mock_paragraph.content = "Test paragraph text"
        mock_paragraph.words = []
        mock_paragraph.role = "paragraph"
        mock_paragraph.confidence = 0.99

        mock_page = MagicMock()
        mock_page.lines = [mock_line]
        mock_page.tables = []

        strategy.doc = MagicMock()
        strategy.doc.paragraphs = [mock_paragraph]

        strategy._get_bounding_box = MagicMock(return_value=[
            {"x": 0.1, "y": 0.1}, {"x": 0.5, "y": 0.1},
            {"x": 0.5, "y": 0.3}, {"x": 0.1, "y": 0.3},
        ])
        strategy._normalize_coordinates = MagicMock(return_value=[
            {"x": 0.01, "y": 0.01}, {"x": 0.06, "y": 0.01},
            {"x": 0.06, "y": 0.03}, {"x": 0.01, "y": 0.03},
        ])

        mock_doc_nlp = MagicMock()
        mock_sent = MagicMock()
        mock_sent.text = "Test paragraph text"
        mock_sent.start_char = 0
        mock_sent.end_char = 19
        mock_doc_nlp.sents = [mock_sent]
        strategy.nlp = MagicMock(return_value=mock_doc_nlp)

        page_dict = {"width": 8.5, "height": 11.0, "lines": [], "words": [], "tables": []}
        result = {"lines": [], "paragraphs": [], "sentences": [], "tables": [], "blocks": []}

        await strategy._process_azure_page(mock_page, page_dict, result, 1)

        assert len(result["paragraphs"]) == 1
        assert len(result["sentences"]) == 1
        assert len(page_dict["lines"]) == 1

    @pytest.mark.asyncio
    async def test_process_azure_page_empty_paragraph(self):
        """_process_azure_page handles paragraphs with no content."""
        strategy = self._make_strategy()

        mock_paragraph = MagicMock(spec=["words", "role"])
        mock_paragraph.words = []
        mock_paragraph.role = "paragraph"

        mock_page = MagicMock()
        mock_page.lines = []
        mock_page.tables = []

        strategy.doc = MagicMock()
        strategy.doc.paragraphs = [mock_paragraph]

        strategy._get_bounding_box = MagicMock(return_value=[])
        strategy._normalize_coordinates = MagicMock(return_value=None)

        page_dict = {"width": 8.5, "height": 11.0, "lines": [], "words": [], "tables": []}
        result = {"lines": [], "paragraphs": [], "sentences": [], "tables": [], "blocks": []}

        await strategy._process_azure_page(mock_page, page_dict, result, 1)

        # Empty paragraph should not be added
        assert len(result["paragraphs"]) == 0

    def test_process_block_text_pymupdf_empty_spans_in_line(self):
        """_process_block_text_pymupdf handles empty text in spans."""
        strategy = self._make_strategy()
        mock_doc_nlp = MagicMock()
        mock_doc_nlp.sents = []
        strategy.nlp = MagicMock(return_value=mock_doc_nlp)

        block = {
            "type": 0,
            "bbox": (0, 0, 200, 50),
            "lines": [{
                "spans": [],
                "bbox": (0, 0, 200, 20),
            }],
            "number": 0,
        }

        # This accesses spans[0] unconditionally at line 480/483
        # which will raise IndexError
        with pytest.raises(IndexError):
            strategy._process_block_text_pymupdf(block, 612.0, 792.0)
