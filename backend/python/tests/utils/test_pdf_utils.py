import pytest
from app.services.parsing.providers.smart_pdf_parser import _page_needs_ocr
from app.modules.parsers.pdf.ocr_handler import OCRStrategy

class MockPage:
    def __init__(self, width=1000.0, height=1000.0, images=None, text="A lot of text here to bypass minimal text", words=None):
        self.width = width
        self.height = height
        self.images = images or []
        self._text = text
        
        if words is None:
            # Fake dense words to bypass low_density
            self._words = [{"x0": 0, "top": 0, "x1": width, "bottom": height}]
        else:
            self._words = words

    def extract_text(self):
        return self._text
        
    def extract_words(self):
        return self._words

@pytest.mark.parametrize("images, expected_routing", [
    # 49.1% coverage (exactly below 50%). Should NOT need OCR.
    ([{"x0": 10.0, "top": 0.0, "x1": 501.0, "bottom": 1000.0}], False),
    # 51% coverage (above 50%). Should need OCR.
    ([{"x0": 0.0, "top": 0.0, "x1": 510.0, "bottom": 1000.0}], True),
    # Overlapping images whose sum is 60% but exact union is 45%. Should NOT need OCR.
    ([{"x0": 0.0, "top": 0.0, "x1": 600.0, "bottom": 500.0},
      {"x0": 300.0, "top": 0.0, "x1": 900.0, "bottom": 500.0}], False),
    # Negative origin: derived x1 is 500, clipped width is 500. Area 50%. Should NOT need OCR.
    ([{"x0": -500.0, "top": 0.0, "width": 1000.0, "height": 1000.0}], False),
    # Clipping outside page: 500x500 outside page bounds mapped to inside. Area 25%.
    ([{"x0": 500.0, "top": 500.0, "x1": 1500.0, "bottom": 1500.0}], False),
])
def test_page_needs_ocr_routing(images, expected_routing):
    """
    Test the OCR routing boundary based on exact image coverage.
    Validates _page_needs_ocr handles exact union boundaries, overlap, and clipping accurately.
    """
    page = MockPage(width=1000.0, height=1000.0, images=images)
    assert _page_needs_ocr(page) == expected_routing

import unittest.mock as mock

def test_ocr_strategy_needs_ocr_routing():
    """
    Validates OCRStrategy.needs_ocr matches the exact same routing boundaries as _page_needs_ocr.
    """
    # 49.1% coverage
    page = MockPage(
        width=1000.0, 
        height=1000.0, 
        images=[{"x0": 10.0, "top": 0.0, "x1": 501.0, "bottom": 1000.0}]
    )
    assert OCRStrategy.needs_ocr(page, mock.Mock()) == False
    
    # 51% coverage
    page_heavy = MockPage(
        width=1000.0, 
        height=1000.0, 
        images=[{"x0": 0.0, "top": 0.0, "x1": 510.0, "bottom": 1000.0}]
    )
    assert OCRStrategy.needs_ocr(page_heavy, mock.Mock()) == True
