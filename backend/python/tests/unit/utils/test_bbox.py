"""Unit tests for app.utils.transformation.bbox."""

import pytest

from app.utils.transformation.bbox import (
    denormalize_corner_coordinates,
    normalize_corner_coordinates,
    transform_bbox_to_corners,
)


# ---------------------------------------------------------------------------
# transform_bbox_to_corners
# ---------------------------------------------------------------------------
class TestTransformBboxToCorners:
    def test_basic_rectangle(self):
        bbox = {"l": 10, "t": 20, "r": 100, "b": 80}
        result = transform_bbox_to_corners(bbox)
        assert result == [
            [10, 20],   # top-left
            [100, 20],  # top-right
            [100, 80],  # bottom-right
            [10, 80],   # bottom-left
        ]

    def test_zero_origin(self):
        bbox = {"l": 0, "t": 0, "r": 50, "b": 50}
        result = transform_bbox_to_corners(bbox)
        assert result == [
            [0, 0],
            [50, 0],
            [50, 50],
            [0, 50],
        ]

    def test_float_values(self):
        bbox = {"l": 1.5, "t": 2.5, "r": 10.5, "b": 20.5}
        result = transform_bbox_to_corners(bbox)
        assert result == [
            [1.5, 2.5],
            [10.5, 2.5],
            [10.5, 20.5],
            [1.5, 20.5],
        ]

    def test_returns_four_points(self):
        bbox = {"l": 0, "t": 0, "r": 1, "b": 1}
        result = transform_bbox_to_corners(bbox)
        assert len(result) == 4
        for point in result:
            assert len(point) == 2

    def test_missing_key_raises_value_error(self):
        with pytest.raises(ValueError, match="bbox missing required key"):
            transform_bbox_to_corners({"l": 0, "t": 0, "r": 0})

    def test_missing_l_raises(self):
        with pytest.raises(ValueError, match="bbox missing required key: l"):
            transform_bbox_to_corners({"t": 0, "r": 0, "b": 0})

    def test_extra_keys_ignored(self):
        bbox = {"l": 0, "t": 0, "r": 10, "b": 10, "coord_origin": "TOPLEFT"}
        result = transform_bbox_to_corners(bbox)
        assert len(result) == 4

    def test_degenerate_point_bbox(self):
        bbox = {"l": 5, "t": 5, "r": 5, "b": 5}
        result = transform_bbox_to_corners(bbox)
        assert result == [[5, 5], [5, 5], [5, 5], [5, 5]]


# ---------------------------------------------------------------------------
# normalize_corner_coordinates
# ---------------------------------------------------------------------------
class TestNormalizeCornerCoordinates:
    def test_divides_by_page_dimensions(self):
        corners = [[100, 200], [300, 200], [300, 400], [100, 400]]
        page_width = 1000.0
        page_height = 800.0
        result = normalize_corner_coordinates(corners, page_width, page_height)
        # x_norm = x / page_width
        # y_norm = (page_height - y) / page_height
        assert result[0] == pytest.approx([100 / 1000, (800 - 200) / 800])
        assert result[1] == pytest.approx([300 / 1000, (800 - 200) / 800])
        assert result[2] == pytest.approx([300 / 1000, (800 - 400) / 800])
        assert result[3] == pytest.approx([100 / 1000, (800 - 400) / 800])

    def test_origin_corner_normalizes_to_expected(self):
        corners = [[0, 0], [100, 0], [100, 50], [0, 50]]
        result = normalize_corner_coordinates(corners, 100.0, 100.0)
        assert result[0] == pytest.approx([0.0, 1.0])  # (0/100, (100-0)/100)
        assert result[1] == pytest.approx([1.0, 1.0])
        assert result[2] == pytest.approx([1.0, 0.5])
        assert result[3] == pytest.approx([0.0, 0.5])

    def test_zero_page_width_raises(self):
        with pytest.raises(ValueError, match="must be positive"):
            normalize_corner_coordinates([[0, 0]], 0, 100)

    def test_negative_page_height_raises(self):
        with pytest.raises(ValueError, match="must be positive"):
            normalize_corner_coordinates([[0, 0]], 100, -5)

    def test_invalid_corner_format_raises(self):
        with pytest.raises(ValueError, match="corner .* must be a list"):
            normalize_corner_coordinates([[0]], 100, 100)

    def test_full_page_bbox(self):
        corners = [[0, 0], [612, 0], [612, 792], [0, 792]]
        result = normalize_corner_coordinates(corners, 612.0, 792.0)
        assert result[0] == pytest.approx([0.0, 1.0])
        assert result[1] == pytest.approx([1.0, 1.0])
        assert result[2] == pytest.approx([1.0, 0.0])
        assert result[3] == pytest.approx([0.0, 0.0])


# ---------------------------------------------------------------------------
# denormalize_corner_coordinates
# ---------------------------------------------------------------------------
class TestDenormalizeCornerCoordinates:
    def test_multiplies_by_page_dimensions(self):
        normalized = [[0.1, 0.25], [0.3, 0.25], [0.3, 0.5], [0.1, 0.5]]
        page_width = 1000.0
        page_height = 800.0
        result = denormalize_corner_coordinates(normalized, page_width, page_height)
        assert result[0] == pytest.approx([100.0, 200.0])
        assert result[1] == pytest.approx([300.0, 200.0])
        assert result[2] == pytest.approx([300.0, 400.0])
        assert result[3] == pytest.approx([100.0, 400.0])

    def test_zero_normalized_returns_zeros(self):
        normalized = [[0, 0], [0, 0], [0, 0], [0, 0]]
        result = denormalize_corner_coordinates(normalized, 500.0, 500.0)
        for corner in result:
            assert corner == [0.0, 0.0]

    def test_full_normalized_returns_page_dimensions(self):
        normalized = [[1.0, 1.0]]
        result = denormalize_corner_coordinates(normalized, 800.0, 600.0)
        assert result[0] == pytest.approx([800.0, 600.0])

    def test_empty_corners(self):
        result = denormalize_corner_coordinates([], 100, 100)
        assert result == []

    def test_single_point(self):
        normalized = [[0.5, 0.5]]
        result = denormalize_corner_coordinates(normalized, 200.0, 300.0)
        assert result[0] == pytest.approx([100.0, 150.0])

    def test_returns_four_points_for_four_inputs(self):
        normalized = [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6], [0.7, 0.8]]
        result = denormalize_corner_coordinates(normalized, 100.0, 100.0)
        assert len(result) == 4
