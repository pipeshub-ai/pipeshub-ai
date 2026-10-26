"""The table model's page render (opencv_layout_analyzer._detect_model_boxes)."""
import logging
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

import app.modules.parsers.pdf.opencv_layout_analyzer as ola
from app.modules.parsers.pdf import table_detector

BOXES = {"table": [((0.1, 0.2, 0.9, 0.5), 0.9)], "picture": [], "caption": []}


def _page() -> MagicMock:
    page = MagicMock()
    page.to_image.side_effect = AssertionError("pdfium must not render in the parse thread")
    return page


def test_renders_through_the_raster_pool_at_72_dpi() -> None:
    img = np.zeros((792, 612, 3), np.uint8)
    with patch.object(ola, "_rasterize_page", return_value=(img, 1.0)) as raster, \
            patch.object(table_detector, "detect_layout_boxes", return_value=BOXES) as detect:
        assert ola._detect_model_boxes(_page(), "/tmp/doc.pdf") == BOXES
    assert raster.call_args.args[1] == 72
    assert raster.call_args.kwargs == {"pdf_path": "/tmp/doc.pdf", "raster_cache": None}
    assert detect.call_args.args[0] is img


def test_uses_the_documents_model_raster_cache() -> None:
    """Rendering page by page reopens the whole PDF each time: quadratic in its length."""
    img = np.zeros((792, 612, 3), np.uint8)
    cache = MagicMock()
    with patch.object(ola, "_rasterize_page", return_value=(img, 1.0)) as raster, \
            patch.object(table_detector, "detect_layout_boxes", return_value=BOXES):
        ola._detect_model_boxes(_page(), "/tmp/doc.pdf", cache)
    assert raster.call_args.kwargs["raster_cache"] is cache


def test_a_failed_render_falls_back_to_rule_tables_and_says_so(caplog: pytest.LogCaptureFixture) -> None:
    error = RuntimeError("Failed to load document (PDFium: Data format error).")
    with patch.object(ola, "_rasterize_page", side_effect=error), \
            patch.object(table_detector, "detect_layout_boxes") as detect, \
            caplog.at_level(logging.WARNING, logger=ola.__name__):
        assert ola._detect_model_boxes(_page(), "/tmp/doc.pdf") is None
    detect.assert_not_called()
    assert "Data format error" in caplog.text
