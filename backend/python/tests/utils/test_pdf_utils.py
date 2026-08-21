import pytest
from app.utils.pdf_utils import calculate_exact_image_union_area

def test_calculate_exact_image_union_area_regression_49_1_percent():
    """
    Regression test for CodeRabbit feedback.
    A full-height image from 1% to 50.1% of page width covers exactly 49.1% of the page.
    With the old 50x50 grid, this would mark 26/50 columns (52%) and incorrectly pass the 50% threshold.
    """
    page_width = 1000.0
    page_height = 1000.0
    
    # Image spans x from 10 to 501 (width 491, which is 49.1% of 1000)
    # y from 0 to 1000 (height 1000, which is 100% of 1000)
    images = [
        {"x0": 10.0, "top": 0.0, "x1": 501.0, "bottom": 1000.0}
    ]
    
    area = calculate_exact_image_union_area(images, page_width, page_height)
    page_area = page_width * page_height
    ratio = area / page_area
    
    # Exact ratio should be 0.491
    assert abs(ratio - 0.491) < 1e-5
    assert ratio <= 0.5  # Should NOT trigger image-heavy logic

def test_calculate_exact_image_union_area_overlaps():
    """Test that overlapping images do not double-count area."""
    page_width = 100.0
    page_height = 100.0
    
    images = [
        {"x0": 0.0, "top": 0.0, "x1": 50.0, "bottom": 50.0},  # Area 2500
        {"x0": 25.0, "top": 25.0, "x1": 75.0, "bottom": 75.0}, # Area 2500, overlaps by 625
    ]
    
    # Total area should be 2500 + 2500 - 625 = 4375
    area = calculate_exact_image_union_area(images, page_width, page_height)
    assert area == 4375.0

def test_calculate_exact_image_union_area_clipping():
    """Test that images outside page bounds are correctly clipped."""
    page_width = 100.0
    page_height = 100.0
    
    images = [
        {"x0": 50.0, "top": 50.0, "x1": 150.0, "bottom": 150.0}
    ]
    
    # Should clip to 50x50 to 100x100 -> area 2500
    area = calculate_exact_image_union_area(images, page_width, page_height)
    assert area == 2500.0
