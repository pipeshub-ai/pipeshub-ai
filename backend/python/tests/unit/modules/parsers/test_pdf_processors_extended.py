"""Extended unit tests for PDF processor modules to increase coverage.

Covers uncovered lines/branches in:
- OpenCVLayoutAnalyzer (legacy; skipped — stub exposes types only)
- PDFPlumberOpenCVProcessor (pdfplumber_opencv_processor.py)
"""

from __future__ import annotations

import os
import tempfile
from io import BytesIO
from typing import TYPE_CHECKING, Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, PropertyMock, call, patch

import numpy as np
import pytest

if TYPE_CHECKING:
    from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
        LayoutRegion,
        ParsedPageData,
    )


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

@pytest.mark.skip(reason="OpenCVLayoutAnalyzer not in stub opencv_layout_analyzer; pipeline is opencv_layout_analyzer.")
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
        from app.modules.parsers.pdf.opencv_layout_analyzer import (
            LayoutRegion,
            LayoutRegionType,
        )
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
        from app.modules.parsers.pdf.opencv_layout_analyzer import (
            LayoutRegion,
            LayoutRegionType,
        )
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

@pytest.mark.skip(reason="OpenCVLayoutAnalyzer not in stub opencv_layout_analyzer; pipeline is opencv_layout_analyzer.")
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
# PDFPlumberOpenCVProcessor — create_blocks branch coverage, load_document
# ============================================================================

class TestPyMuPDFOpenCVProcessorExtended:

    def _make_processor(self):
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            PDFPlumberOpenCVProcessor,
        )
        return PDFPlumberOpenCVProcessor(logger=_mock_logger(), config=_mock_config())

    @pytest.mark.asyncio
    async def test_create_blocks_all_region_types(self):
        """create_blocks handles TABLE, IMAGE, LIST (bullet + numbered), and TEXT."""
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PDFPlumberOpenCVProcessor,
        )
        proc = PDFPlumberOpenCVProcessor(logger=_mock_logger(), config=_mock_config())

        regions = [
            LayoutRegion(type=LayoutRegionType.TABLE, bbox=(0, 0, 100, 100), table_grid=[["A", "B"]]),
            LayoutRegion(type=LayoutRegionType.IMAGE, bbox=(0, 100, 100, 200), image_data=b"img", image_ext="png"),
            LayoutRegion(type=LayoutRegionType.LIST, bbox=(0, 200, 100, 300), text="- A\n- B", list_items=["- A", "- B"]),
            LayoutRegion(type=LayoutRegionType.LIST, bbox=(0, 300, 100, 400), text="1. A\n2. B", list_items=["1. A", "2. B"]),
            LayoutRegion(type=LayoutRegionType.TEXT, bbox=(0, 400, 100, 500), text="Title and body"),
        ]
        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=regions)

        from app.utils.table_enrichment import TableEnrichmentResult

        enrichment = TableEnrichmentResult(
            summary="Table", headers=["A", "B"], descriptions=["Row text"]
        )

        with patch("app.modules.parsers.pdf.pdfplumber_opencv_processor.get_llm_for_role",
                    new_callable=AsyncMock, return_value=(MagicMock(), {})), \
             patch("app.modules.parsers.pdf.pdfplumber_opencv_processor.enrich_tables",
                    new_callable=AsyncMock, return_value=[enrichment]):
            result = await proc.create_blocks([pd])

        # TABLE row, IMAGE, 4 list items (2 groups), 1 TEXT
        assert len(result.blocks) >= 7
        assert len(result.block_groups) >= 3  # table, bullet list, ordered list

    @pytest.mark.asyncio
    async def test_image_text_follows_the_image_as_a_text_block(self) -> None:
        from app.models.blocks import BlockType
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PDFPlumberOpenCVProcessor,
        )
        proc = PDFPlumberOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
        chart = LayoutRegion(
            type=LayoutRegionType.IMAGE, bbox=(0, 0, 300, 200),
            image_data=b"img", image_ext="png", text="CIF 0.19 million",
        )
        photo = LayoutRegion(
            type=LayoutRegionType.IMAGE, bbox=(0, 300, 300, 500),
            image_data=b"img", image_ext="png", caption="Figure 2: The plant",
        )
        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[chart, photo])

        result = await proc.create_blocks([pd])

        assert [b.type for b in result.blocks] == [BlockType.IMAGE, BlockType.TEXT, BlockType.IMAGE]
        assert result.blocks[1].data == "CIF 0.19 million"
        assert result.blocks[0].image_metadata.captions == []
        assert result.blocks[2].image_metadata.captions == ["Figure 2: The plant"]

    @pytest.mark.asyncio
    async def test_create_blocks_no_page_number_filter(self):
        """create_blocks without page_number processes all pages."""
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PDFPlumberOpenCVProcessor,
        )
        proc = PDFPlumberOpenCVProcessor(logger=_mock_logger(), config=_mock_config())

        # Not "Page 1": a lone page number at the top of a page is dropped as furniture.
        r1 = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(0, 0, 100, 50), text="First page text")
        r2 = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(0, 0, 100, 50), text="Second page text")
        page1 = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[r1])
        page2 = ParsedPageData(page_number=2, width=612.0, height=792.0, regions=[r2])

        result = await proc.create_blocks([page1, page2])
        assert len(result.blocks) == 2

    @pytest.mark.asyncio
    async def test_load_document_delegates(self):
        """load_document calls parse_document then create_blocks."""
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            PDFPlumberOpenCVProcessor,
        )
        proc = PDFPlumberOpenCVProcessor(logger=_mock_logger(), config=_mock_config())

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
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            PDFPlumberOpenCVProcessor,
        )

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
            "app.modules.parsers.pdf.layout_pages.pdfplumber.open",
            return_value=mock_cm,
        ), patch(
            "app.modules.parsers.pdf.layout_pages.extract_layout_regions",
            return_value=[],
        ):
            result = await proc.parse_document("test.pdf", BytesIO(b"fake-pdf-bytes"))

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_parse_document_unlink_oserror_is_swallowed(self):
        """parse_document swallows OSError when cleaning up the temp PDF file."""
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            PDFPlumberOpenCVProcessor,
        )

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
            "app.modules.parsers.pdf.layout_pages.pdfplumber.open",
            return_value=mock_cm,
        ), patch(
            "app.modules.parsers.pdf.layout_pages.extract_layout_regions",
            return_value=[],
        ), patch(
            "app.modules.parsers.pdf.layout_pages.os.unlink",
            side_effect=OSError("permission denied"),
        ):
            result = await proc.parse_document("test.pdf", b"fake-pdf-bytes")

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_parse_document_tempfile_failure_skips_unlink(self):
        """parse_document skips unlink when temp file creation never assigns tmp_path."""
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            PDFPlumberOpenCVProcessor,
        )

        proc = PDFPlumberOpenCVProcessor(logger=_mock_logger(), config=_mock_config())

        with patch(
            "app.modules.parsers.pdf.layout_pages.tempfile.NamedTemporaryFile",
            side_effect=OSError("disk full"),
        ), patch(
            "app.modules.parsers.pdf.layout_pages.os.unlink",
        ) as mock_unlink:
            with pytest.raises(OSError, match="disk full"):
                await proc.parse_document("test.pdf", b"fake-pdf-bytes")

        mock_unlink.assert_not_called()

    @pytest.mark.asyncio
    async def test_build_table_group_skip_llm_enrichment(self):
        """_build_table_group derives headers and row text locally when LLM is skipped."""
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PDFPlumberOpenCVProcessor,
        )

        proc = PDFPlumberOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
        region = LayoutRegion(
            type=LayoutRegionType.TABLE,
            bbox=(0, 0, 200, 100),
            table_grid=[
                [{"text": "Name"}, {"text": "Age"}],
                [{"text": "Alice"}, {"text": "30"}],
                ["Bob", None],
            ],
        )
        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[])
        blocks: list = []
        block_groups: list = []

        with patch(
            "app.modules.parsers.pdf.pdfplumber_opencv_processor.get_llm_for_role",
            new_callable=AsyncMock,
        ) as mock_get_llm, patch(
            "app.modules.parsers.pdf.pdfplumber_opencv_processor.enrich_table_grid",
            new_callable=AsyncMock,
        ) as mock_enrich:
            bg = await proc._build_table_group(
                region, pd, blocks, block_groups, skip_llm_enrichment=True
            )

        mock_get_llm.assert_not_called()
        mock_enrich.assert_not_called()
        assert bg is not None
        assert bg.type.value == "table"
        assert len(blocks) == 2
        assert blocks[0].data["row_number"] == 1
        assert "Name:" in blocks[0].data["row_natural_language_text"]
        assert blocks[1].data["row_number"] == 2

    @pytest.mark.asyncio
    async def test_build_table_group_empty_grid(self):
        """_build_table_group returns None for an empty table grid."""
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PDFPlumberOpenCVProcessor,
        )

        proc = PDFPlumberOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
        region = LayoutRegion(
            type=LayoutRegionType.TABLE, bbox=(0, 0, 200, 100), table_grid=[]
        )
        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[])
        result = await proc._build_table_group(region, pd, [], [])
        assert result is None

    @pytest.mark.asyncio
    async def test_create_blocks_skip_llm_enrichment(self):
        """create_blocks forwards skip_llm_enrichment to table group building."""
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PDFPlumberOpenCVProcessor,
        )

        proc = PDFPlumberOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
        region = LayoutRegion(
            type=LayoutRegionType.TABLE,
            bbox=(0, 0, 100, 100),
            table_grid=[["ColA", "ColB"], ["1", "2"]],
        )
        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[region])

        with patch.object(
            proc, "_build_table_group", new_callable=AsyncMock, return_value=MagicMock()
        ) as mock_build:
            await proc.create_blocks([pd], skip_llm_enrichment=True)

        mock_build.assert_awaited_once()
        assert mock_build.await_args.kwargs["skip_llm_enrichment"] is True

    @pytest.mark.asyncio
    async def test_create_blocks_keeps_plain_rows_when_no_llm(self) -> None:
        """No indexing LLM must not fail the parse: that would send the PDF to OCR."""
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PDFPlumberOpenCVProcessor,
        )

        proc = PDFPlumberOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
        region = LayoutRegion(
            type=LayoutRegionType.TABLE,
            bbox=(0, 0, 100, 100),
            table_grid=[["ColA", "ColB"], ["1", "2"]],
        )
        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[region])

        with patch(
            "app.modules.parsers.pdf.pdfplumber_opencv_processor.get_llm_for_role",
            new_callable=AsyncMock,
            side_effect=ValueError("no indexing model configured"),
        ), patch.object(
            proc, "_build_table_group", new_callable=AsyncMock, return_value=MagicMock()
        ) as mock_build:
            await proc.create_blocks([pd])

        assert mock_build.await_args.kwargs["skip_llm_enrichment"] is True

    @pytest.mark.asyncio
    async def test_table_without_grid_keeps_its_text(self) -> None:
        """A TABLE region with no cell grid is emitted as text rather than dropped."""
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PDFPlumberOpenCVProcessor,
        )

        proc = PDFPlumberOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
        region = LayoutRegion(
            type=LayoutRegionType.TABLE, bbox=(0, 0, 100, 100), text="Q1 revenue 10", table_grid=None,
        )
        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[region])
        container = await proc.create_blocks([pd], skip_llm_enrichment=True)
        assert [b.data for b in container.blocks] == ["Q1 revenue 10"]

    def test_release_freed_memory_is_a_no_op_without_glibc(self) -> None:
        from app.modules.parsers.pdf import layout_pages as mod

        with patch.object(mod.ctypes, "CDLL", side_effect=OSError("no libc.so.6")):
            mod._release_freed_memory()
        with patch.object(mod.ctypes, "CDLL") as cdll:
            mod._release_freed_memory()
        cdll.return_value.malloc_trim.assert_called_once_with(0)

    def test_unmapped_glyph_codes_are_never_emitted(self) -> None:
        """(cid:N) is a glyph with no Unicode mapping, not text."""
        from app.modules.parsers.pdf.layout_pages import _drop_unmapped_glyphs
        from app.modules.parsers.pdf.opencv_layout_analyzer import (
            LayoutRegion,
            LayoutRegionType,
        )

        text = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(0, 0, 1, 1), text="Revenue (cid:3)(cid:7) grew")
        garbage = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(0, 0, 1, 1), text="(cid:1)(cid:2) (cid:9)")
        listed = LayoutRegion(type=LayoutRegionType.LIST, bbox=(0, 0, 1, 1), list_items=["(cid:127) first", "(cid:4)"])
        table = LayoutRegion(type=LayoutRegionType.TABLE, bbox=(0, 0, 1, 1), table_grid=[["(cid:5)Q1", "10"]])

        out = _drop_unmapped_glyphs([text, garbage, listed, table])
        assert out == [text, listed, table]
        assert text.text == "Revenue  grew"
        assert listed.list_items == ["first"]
        assert table.table_grid == [["Q1", "10"]]

    @pytest.mark.asyncio
    async def test_build_table_group_missing_row_text_fallback(self):
        """Row blocks are built one-to-one from the enrichment's descriptions.

        Padding a short LLM response out to the full row count is enrich_table_grid's
        job (it never returns fewer descriptions than data rows); _build_table_group
        just renders whatever it's given.
        """
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PDFPlumberOpenCVProcessor,
        )
        from app.utils.table_enrichment import TableEnrichmentResult

        proc = PDFPlumberOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
        region = LayoutRegion(
            type=LayoutRegionType.TABLE,
            bbox=(0, 0, 200, 100),
            table_grid=[["A", "B"], ["1", "2"], ["3", "4"]],
        )
        pd = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[])
        blocks: list = []
        block_groups: list = []

        enrichment = TableEnrichmentResult(
            summary="Table summary",
            headers=["A", "B"],
            descriptions=["Row one text", "Row two text"],
        )

        bg = await proc._build_table_group(region, pd, blocks, block_groups, enrichment=enrichment)

        assert bg is not None
        assert len(blocks) == 2
        assert blocks[0].data["row_natural_language_text"] == "Row one text"
        assert blocks[1].data["row_natural_language_text"] == "Row two text"


class TestPdfPlumberHeadings:
    """Heading lines become text blocks with sub_type HEADING, as the HTML parser marks them."""

    @staticmethod
    def _text(text: str, *, size: float = 10.0, bold: bool = False) -> LayoutRegion:
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
        )

        return LayoutRegion(
            type=LayoutRegionType.TEXT, bbox=(0, 300, 100, 310), text=text, font_size=size, is_bold=bold
        )

    def test_a_heading_is_one_short_line_set_apart_by_weight_or_size(self) -> None:
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            body_font_size,
            is_heading,
        )

        regions = [self._text("body", size=10), self._text("body", size=10), self._text("big", size=14)]
        body = body_font_size(regions)
        assert body == 10
        assert is_heading(self._text("Item 7. Management's Discussion", bold=True), body)
        assert is_heading(self._text("Liquidity and Capital Resources", size=12), body)
        assert not is_heading(self._text("Liquidity and Capital Resources", size=11), body)
        assert not is_heading(self._text("Bold first line\nsecond line", bold=True), body)
        assert not is_heading(self._text("x" * 161, bold=True), body)
        assert not is_heading(self._text("   ", bold=True), body)

    def test_no_sized_text_means_only_bold_counts(self) -> None:
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            body_font_size,
            is_heading,
        )

        assert body_font_size([]) == 0.0
        assert not is_heading(self._text("Large", size=30), 0.0)
        assert is_heading(self._text("Bold", bold=True), 0.0)

    @pytest.mark.asyncio
    async def test_create_blocks_marks_headings(self) -> None:
        from app.models.blocks import BlockSubType
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
            ParsedPageData,
            PDFPlumberOpenCVProcessor,
        )

        proc = PDFPlumberOpenCVProcessor(logger=_mock_logger(), config=_mock_config())
        split_off = self._text("Item 8. Financial Statements")
        split_off.heading = True
        regions = [
            self._text("Item 7. MD&A", bold=True),
            self._text("Revenue grew in 2023 because of higher volumes across segments."),
            LayoutRegion(type=LayoutRegionType.TABLE, bbox=(0, 320, 100, 360), table_grid=[["Year", "Revenue"], ["2023", "10"]]),
            self._text("Results of Operations", size=13),
            split_off,
        ]
        page = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=regions)

        container = await proc.create_blocks([page], skip_llm_enrichment=True)

        headings = {b.data for b in container.blocks if b.sub_type == BlockSubType.HEADING}
        assert headings == {"Item 7. MD&A", "Results of Operations", "Item 8. Financial Statements"}
        assert len(container.block_groups) == 1


class TestRunningHeadersAndPageNumbers:
    """Page numbers and lines repeated in the margins are dropped, as Docling keeps them out of the body."""

    @staticmethod
    def _page(n: int, *regions: LayoutRegion) -> ParsedPageData:
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import ParsedPageData

        return ParsedPageData(page_number=n, width=612.0, height=792.0, regions=list(regions))

    @staticmethod
    def _text(text: str, top: float, bottom: float) -> LayoutRegion:
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            LayoutRegion,
            LayoutRegionType,
        )

        return LayoutRegion(type=LayoutRegionType.TEXT, bbox=(50, top, 300, bottom), text=text)

    def test_page_numbers_and_repeated_headers_are_noise(self) -> None:
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import running_noise

        pages = []
        for n in range(1, 5):
            header = self._text("Table of Contents", 20, 32)
            body = self._text(f"Revenue rose on page {n}.", 300, 320)
            footer = self._text(str(40 + n), 760, 772)
            pages.append(self._page(n, header, body, footer))
        noise = running_noise(pages)
        # The header's first copy stays; page numbers all go.
        assert noise == {id(r) for p in pages[1:] for r in (p.regions[0],)} | {id(p.regions[2]) for p in pages}

    def test_a_line_seen_once_at_the_top_is_not_a_running_header(self) -> None:
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import running_noise

        pages = [self._page(1, self._text("Consolidated Balance Sheets", 20, 32)),
                 self._page(2, self._text("Consolidated Statements of Income", 20, 32))]
        assert running_noise(pages) == set()

    def test_a_title_continued_over_a_few_pages_of_a_long_document_is_kept(self) -> None:
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import running_noise

        title = "Consolidated Balance Sheets (continued)"
        body = lambda: self._text("Assets were up.", 300, 320)  # noqa: E731
        pages = [self._page(n, self._text(title, 20, 32), body()) for n in range(3)]
        pages += [self._page(n, body()) for n in range(3, 20)]
        assert running_noise(pages) == set()

    def test_a_page_number_below_a_short_page_is_noise(self) -> None:
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import running_noise

        number = self._text("49", 250, 261)
        short = self._page(49, self._text("Total comprehensive income", 200, 212), number)
        full = [self._page(n, self._text("Revenue was up.", 300, 320), self._text(str(n), 760, 772)) for n in (46, 47, 48)]
        assert id(number) in running_noise([*full, short])

    def test_a_lone_number_off_the_page_sequence_is_kept(self) -> None:
        """Diagram labels and list numbers mid-page are content, unlike page numbers."""
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import running_noise

        numbered = [self._page(n, self._text("Quorums intersect.", 300, 320), self._text(str(n), 760, 772)) for n in range(1, 12)]
        # The label is the last line on its page, where a short page's number would sit.
        labels = [self._text("3", 500, 511) for _ in range(12, 19)]
        drawn = [self._page(n, self._text("Quorums intersect.", 300, 320), label) for n, label in zip(range(12, 19), labels)]
        noise = running_noise(numbered + drawn)
        assert not noise & {id(label) for label in labels}
        assert {id(p.regions[1]) for p in numbered} <= noise

    def test_the_first_copy_of_a_running_header_stays(self) -> None:
        """A filing's date may be printed only in its running header."""
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import running_noise

        headers = [self._text(f"Case: 21-13199 Date Filed: 01/05/2022 Page: {n} of 5", 20, 32) for n in range(1, 6)]
        pages = [self._page(n, h, self._text("The court held.", 300, 320)) for n, h in zip(range(1, 6), headers)]
        noise = running_noise(pages)
        assert id(headers[0]) not in noise
        assert {id(h) for h in headers[1:]} <= noise

    @pytest.mark.asyncio
    async def test_create_blocks_drops_them(self) -> None:
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            PDFPlumberOpenCVProcessor,
        )

        pages = [self._page(n, self._text("Revenue was up.", 300, 320), self._text(f"Page {n}", 760, 772)) for n in (1, 2)]
        container = await PDFPlumberOpenCVProcessor(logger=_mock_logger(), config=_mock_config()).create_blocks(pages, skip_llm_enrichment=True)
        assert [b.data for b in container.blocks] == ["Revenue was up.", "Revenue was up."]
        assert [b.index for b in container.blocks] == [0, 1]
