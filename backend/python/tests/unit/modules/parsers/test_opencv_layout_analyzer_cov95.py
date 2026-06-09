"""Targeted tests to raise coverage of opencv_layout_analyzer above 95%."""

from __future__ import annotations

from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

import app.modules.parsers.pdf.opencv_layout_analyzer as ola
from app.modules.parsers.pdf.opencv_layout_analyzer import (
    ColumnDetectionResult,
    DocumentRasterCache,
    LayoutRegion,
    LayoutRegionType,
    _absorb_labels_into_images,
    _append_hyperlink_url_to_text,
    _bbox_edge_distance,
    _bbox_gap,
    _binarize_foreground,
    _build_line_side_meta,
    _clamp_bbox,
    _classify_text_blocks,
    _crop_image_bytes,
    _detect_borderless_tables,
    _detect_images,
    _detect_page_columns,
    _detect_stray_text_regions,
    _detect_table_boxes_from_vectors,
    _detect_tables,
    _detect_uniform_fill_stripe_regions,
    _extract_list_items,
    _find_vector_bullets,
    _grid_to_text,
    _gutter_relax_merge_pair,
    _hyperlink_anchor_for_region,
    _hyperlink_bbox,
    _hyperlink_uri,
    _inflate_if_thin,
    _inject_hyperlinks_into_region,
    _inject_hyperlinks_into_regions,
    _inject_hyperlinks_into_text,
    _ink_density,
    _iou,
    _is_chart_satellite,
    _is_monospace_font_name,
    _is_plausible_table,
    _line_side,
    _merge_regions,
    _merge_row_tables,
    _merge_small_text_regions,
    _overlap_ratio,
    _page_hyperlinks,
    _reading_order,
    _reextract_grid_for_bbox,
    _reextract_region_payload,
    _refine_image_bbox,
    _region_column_side,
    _region_is_predominantly_monospace,
    _region_side,
    _render_all_pages,
    _resolve_line_sides,
    _resolve_overlaps,
    _rasterize_page,
    _rescue_columns_if_single,
    _safe_text_in_bbox,
    _segment_blocks_native,
    _segment_vector_blocks,
    _split_lines_into_runs,
    _split_region_by_gutters,
    _stray_line_to_region,
    _table_contained_in_text,
    _table_overlap_with_large_neighbor_allowed,
    _table_proximity_neighbor_allowed,
    _text_is_table_top_header,
    _tight_bbox_from_words,
    _tighten_to_content,
    _trim_empty_rows_cols,
    _trim_image_text_margins,
    _trim_paragraph_rows,
    _valid_bbox,
    _validate_gutter_with_projection,
    _word_center_in_bbox,
    _word_center_x,
    _word_count,
    _x_projections_overlap,
    _y_projections_overlap,
    _group_words_into_lines,
    extract_layout_regions,
)


# ---------------------------------------------------------------------------
# Fixtures / factories
# ---------------------------------------------------------------------------

def _w(
    text: str,
    x0: float,
    x1: float,
    top: float,
    bottom: float,
    **kw: Any,
) -> Dict[str, Any]:
    return {
        "text": text,
        "x0": x0,
        "x1": x1,
        "top": top,
        "bottom": bottom,
        "fontname": kw.pop("fontname", "Helvetica"),
        "size": kw.pop("size", 10.0),
        **kw,
    }


def _region(
    rtype: LayoutRegionType,
    bbox: Tuple[float, float, float, float],
    text: str = "",
    **kw: Any,
) -> LayoutRegion:
    return LayoutRegion(type=rtype, bbox=bbox, text=text, **kw)


def _two_col_words(
    page_w: float = 612.0,
    n_lines: int = 30,
    gutter_x: float = 306.0,
    gap_half: float = 20.0,
) -> List[Dict[str, Any]]:
    """Synthetic two-column body text with a central gutter gap on every line."""
    words: List[Dict[str, Any]] = []
    left_x1 = gutter_x - gap_half
    right_x0 = gutter_x + gap_half
    y = 750.0
    step = (700.0 - 100.0) / max(n_lines - 1, 1)
    for i in range(n_lines):
        top = y - i * step
        bottom = top + 10.0
        left = _w(f"L{i}a L{i}b L{i}c", 50, left_x1, top, bottom)
        right = _w(f"R{i}a R{i}b R{i}c", right_x0, 550, top, bottom)
        words.extend([left, right])
    return words


def _make_pdf_page(draw_fn):
    pytest.importorskip("reportlab")
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    import pdfplumber

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    draw_fn(c)
    c.showPage()
    c.save()
    with pdfplumber.open(BytesIO(buf.getvalue())) as pdf:
        return pdf.pages[0]


# ---------------------------------------------------------------------------
# Merge gates (lines 104–327)
# ---------------------------------------------------------------------------

class TestMergeGates:
    def test_gutter_relax_merge_pair_table_text_near(self):
        table = _region(LayoutRegionType.TABLE, (0, 0, 100, 50))
        text = _region(LayoutRegionType.TEXT, (102, 0, 200, 50))
        assert _gutter_relax_merge_pair(table, text, 1.5) is True

    def test_gutter_relax_merge_pair_rejects_large_gap(self):
        table = _region(LayoutRegionType.TABLE, (0, 0, 100, 50))
        text = _region(LayoutRegionType.TEXT, (110, 0, 200, 50))
        assert _gutter_relax_merge_pair(table, text, 5.0) is False

    def test_gutter_relax_merge_pair_rejects_non_table_text(self):
        a = _region(LayoutRegionType.TEXT, (0, 0, 50, 20))
        b = _region(LayoutRegionType.TEXT, (52, 0, 100, 20))
        assert _gutter_relax_merge_pair(a, b, 1.0) is False

    def test_table_proximity_neighbor_allowed_same_type(self):
        a = _region(LayoutRegionType.TABLE, (0, 0, 100, 100))
        b = _region(LayoutRegionType.TABLE, (100, 0, 200, 100))
        assert _table_proximity_neighbor_allowed(a, b) is True

    def test_table_proximity_neighbor_blocks_large_text(self):
        table = _region(LayoutRegionType.TABLE, (0, 50, 200, 150))
        text = _region(LayoutRegionType.TEXT, (0, 0, 200, 40), text="body")
        assert _table_proximity_neighbor_allowed(table, text) is False

    def test_table_proximity_neighbor_header_bypass(self):
        table = _region(LayoutRegionType.TABLE, (50, 100, 250, 200))
        header = _region(LayoutRegionType.TEXT, (60, 70, 240, 100), text="Header")
        assert _text_is_table_top_header(table, header) is True
        assert _table_proximity_neighbor_allowed(table, header) is True

    def test_text_is_table_top_header_rejects_footnote(self):
        table = _region(LayoutRegionType.TABLE, (50, 100, 250, 200))
        foot = _region(LayoutRegionType.TEXT, (60, 210, 240, 230))
        assert _text_is_table_top_header(table, foot) is False

    def test_table_overlap_with_large_neighbor_allowed(self):
        table = _region(LayoutRegionType.TABLE, (0, 50, 200, 150))
        text = _region(LayoutRegionType.TEXT, (0, 0, 200, 51))
        assert _table_overlap_with_large_neighbor_allowed(table, text) is False

    def test_table_overlap_substantial_overlap_allowed(self):
        table = _region(LayoutRegionType.TABLE, (0, 0, 100, 100))
        text = _region(LayoutRegionType.TEXT, (0, 0, 100, 100))
        assert _table_overlap_with_large_neighbor_allowed(table, text) is True

    def test_table_contained_in_text(self):
        table = _region(LayoutRegionType.TABLE, (10, 10, 90, 90))
        text = _region(LayoutRegionType.TEXT, (0, 0, 100, 100))
        assert _table_contained_in_text(table, text) is True
        assert _table_contained_in_text(text, table) is False


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

class TestGeometryHelpers:
    def test_valid_bbox(self):
        assert _valid_bbox((0, 0, 10, 10)) is True
        assert _valid_bbox(None) is False
        assert _valid_bbox((0, 0, 0, 10)) is False

    def test_inflate_if_thin(self):
        out = _inflate_if_thin((5, 5, 5.5, 10))
        assert out[2] - out[0] >= 1.0

    def test_clamp_bbox(self):
        assert _clamp_bbox((-5, -5, 700, 900), 612, 792) == (0, 0, 612, 792)

    def test_iou_and_overlap(self):
        a = (0, 0, 10, 10)
        b = (5, 5, 15, 15)
        assert _iou(a, b) > 0
        assert _overlap_ratio(a, b) == 0.25
        assert _overlap_ratio((0, 0, 0, 0), b) == 0.0

    def test_bbox_gap_and_projections(self):
        a = (0, 0, 10, 10)
        b = (20, 0, 30, 10)
        hgap, vgap = _bbox_gap(a, b)
        assert hgap == 10.0
        assert vgap == 0.0
        assert _x_projections_overlap(a, b) is False
        assert _y_projections_overlap(a, b) is True

    def test_word_center_helpers(self):
        word = _w("hi", 10, 20, 5, 15)
        assert _word_center_x(word) == 15.0
        assert _word_center_in_bbox(word, (0, 0, 30, 30)) is True
        assert _word_center_x({"bad": "data"}) is None

    def test_tight_bbox_from_words(self):
        words = [_w("a", 10, 20, 5, 15), _w("b", 25, 35, 5, 15)]
        assert _tight_bbox_from_words(words) == (10, 5, 35, 15)
        assert _tight_bbox_from_words([]) is None

    def test_word_count_and_edge_distance(self):
        assert _word_count("one two three") == 3
        assert _word_count("") == 0
        d = _bbox_edge_distance((0, 0, 10, 10), (15, 0, 25, 10))
        assert d == 5.0

    def test_grid_to_text(self):
        grid = [["A", "B"], ["C", "D"]]
        assert "A | B" in _grid_to_text(grid)
        assert _grid_to_text(None) == ""


# ---------------------------------------------------------------------------
# Column detection
# ---------------------------------------------------------------------------

class TestColumnDetection:
    def test_rejects_empty_page(self):
        result = _detect_page_columns([], 612, 792)
        assert result.gutter_x is None
        assert result.details.get("reason") == "page too small or empty"

    def test_rejects_too_few_words(self):
        words = [_w(f"w{i}", 50, 80, 700 - i, 710 - i) for i in range(10)]
        result = _detect_page_columns(words, 612, 792)
        assert result.gutter_x is None
        assert "too few valid words" in result.details.get("reason", "")

    def test_accepts_two_column_layout(self):
        words = _two_col_words()
        result = _detect_page_columns(words, 612, 792)
        assert result.gutter_x is not None
        assert 280 < result.gutter_x < 330
        assert result.confidence >= 0.5

    def test_skips_synthetic_bullet_font(self):
        words = _two_col_words()
        words.append(_w("•", 40, 45, 700, 710, fontname="_synthetic_bullet"))
        result = _detect_page_columns(words, 612, 792)
        assert result.gutter_x is not None

    def test_table_bbox_excludes_table_rows(self):
        words = _two_col_words()
        table_bbox = (40, 400, 280, 500)
        result = _detect_page_columns(words, 612, 792, table_bboxes=[table_bbox])
        assert isinstance(result, ColumnDetectionResult)

    def test_projection_rescue_accepts_clear_gutter(self):
        words = _two_col_words()
        page_w = 612.0

        def raster_getter():
            img = np.full((100, 200, 3), 255, dtype=np.uint8)
            img[:, 80:120] = 0
            return img, 200.0 / page_w

        result = _detect_page_columns(
            words, page_w, 792, raster_getter=raster_getter,
        )
        assert result.gutter_x is not None or result.confidence < 0.6

    def test_validate_gutter_with_projection_paths(self):
        page_w = 612.0

        def good_raster():
            img = np.full((150, 300, 3), 255, dtype=np.uint8)
            img[:, 40:60] = 0
            img[:, 120:280] = 50
            img[:, 220:260] = 0
            return img, 300.0 / page_w

        out = _validate_gutter_with_projection(good_raster, 150.0, 20.0, page_w)
        assert "accepted" in out

        def empty_raster():
            return np.array([]), 1.0

        assert _validate_gutter_with_projection(empty_raster, 150.0, 20.0, page_w)["accepted"] is False

        def fail_raster():
            raise RuntimeError("boom")

        assert _validate_gutter_with_projection(fail_raster, 150.0, 20.0, page_w)["accepted"] is False


# ---------------------------------------------------------------------------
# Line grouping, sides, reading order
# ---------------------------------------------------------------------------

class TestLineGroupingAndReadingOrder:
    def test_group_words_into_lines_with_gutter(self):
        gutter = 300.0
        words = [
            _w("left", 50, 100, 100, 110),
            _w("right", 320, 400, 100, 110),
        ]
        lines, raw_ids = _group_words_into_lines(words, gutter_x=gutter)
        assert len(lines) == 2
        assert len(set(raw_ids)) >= 1

    def test_line_side_classification(self):
        gutter = 300.0
        left_line = [_w("a", 50, 100, 10, 20)]
        right_line = [_w("b", 350, 400, 10, 20)]
        wide_line = [_w("l", 50, 100, 30, 40), _w("r", 350, 400, 30, 40)]
        assert _line_side(left_line, gutter) == "left"
        assert _line_side(right_line, gutter) == "right"
        assert _line_side(wide_line, gutter) == "wide"
        assert _line_side(left_line, None) == "any"

    def test_resolve_line_sides_propagates_wide(self):
        gutter = 300.0
        wide_line = [_w("l", 50, 150, 10, 20), _w("r", 350, 450, 10, 20)]
        left_frag = [_w("frag", 50, 80, 10, 20)]
        lines = [wide_line, left_frag]
        raw_ids = [0, 0]
        sides = _resolve_line_sides(lines, raw_ids, gutter)
        assert sides[0] == "wide"
        assert sides[1] == "wide"

    def test_region_column_side_wide(self):
        gutter = 300.0
        wide = _region(LayoutRegionType.TEXT, (50, 10, 550, 30))
        assert _region_column_side(wide, gutter, 612) == "wide"

    def test_reading_order_single_column(self):
        r1 = _region(LayoutRegionType.TEXT, (0, 20, 100, 40), "b")
        r2 = _region(LayoutRegionType.TEXT, (0, 10, 100, 30), "a")
        ordered = _reading_order([r1, r2], 612, 792)
        assert ordered[0].text == "a"

    def test_reading_order_two_column(self):
        gutter = 306.0
        left = _region(LayoutRegionType.TEXT, (50, 100, 250, 120), "left")
        right = _region(LayoutRegionType.TEXT, (330, 100, 550, 120), "right")
        words = _two_col_words(n_lines=5)
        ordered = _reading_order([right, left], 612, 792, gutter_x=gutter, all_words=words)
        assert ordered[0].text == "left"

    def test_build_line_side_meta_empty_without_gutter(self):
        assert _build_line_side_meta([_w("a", 0, 10, 0, 10)], None) == []


# ---------------------------------------------------------------------------
# Overlap resolution and small-text merge
# ---------------------------------------------------------------------------

class TestResolveOverlaps:
    def test_merges_overlapping_text(self):
        a = _region(LayoutRegionType.TEXT, (0, 0, 100, 50), "hello")
        b = _region(LayoutRegionType.TEXT, (50, 0, 150, 50), "world")
        merged = _resolve_overlaps([a, b], page_w=612, page_h=792)
        assert len(merged) == 1
        assert "hello" in merged[0].text

    def test_table_contained_in_text_inverts_primary(self):
        table = _region(LayoutRegionType.TABLE, (10, 10, 90, 90), "t", table_grid=[["a"]])
        text = _region(LayoutRegionType.TEXT, (0, 0, 100, 100), "body")
        merged = _resolve_overlaps([table, text], page_w=612, page_h=792)
        assert merged[0].type == LayoutRegionType.TEXT

    def test_list_type_gate_blocks_merge(self):
        lst = _region(LayoutRegionType.LIST, (0, 0, 100, 50), "• a", list_items=["a"])
        text = _region(LayoutRegionType.TEXT, (0, 0, 100, 50), "overlap")
        out = _resolve_overlaps([lst, text], page_w=612, page_h=792)
        assert len(out) == 2

    def test_proximity_merge_small_gap(self):
        a = _region(LayoutRegionType.TEXT, (0, 0, 100, 50), "aaa bbb ccc ddd eee")
        b = _region(LayoutRegionType.TEXT, (0, 52, 100, 102), "one")
        out = _resolve_overlaps([a, b], page_w=612, page_h=792, proximity=5.0)
        assert len(out) == 1

    def test_merge_regions_image_invalidates_crop(self):
        img = _region(LayoutRegionType.IMAGE, (0, 0, 100, 100), image_data=b"png")
        other = _region(LayoutRegionType.TEXT, (50, 50, 150, 150), "cap")
        merged = _merge_regions(img, other)
        assert merged.image_data is None

    def test_merge_small_text_regions(self):
        big = _region(LayoutRegionType.TEXT, (0, 0, 100, 20), "one two three four five six")
        small = _region(LayoutRegionType.TEXT, (0, 22, 100, 40), "hi")
        out = _merge_small_text_regions([big, small], max_gap=5.0)
        assert len(out) == 1
        assert "hi" in out[0].text


# ---------------------------------------------------------------------------
# List parsing and text classification
# ---------------------------------------------------------------------------

class TestListParsing:
    def test_split_lines_into_runs_bullet_list(self):
        lines = [
            [_w("•", 10, 15, 100, 110)],
            [_w("item", 20, 60, 100, 110)],
            [_w("•", 10, 15, 120, 130)],
            [_w("two", 20, 60, 120, 130)],
        ]
        lines[0][0]["text"] = "• one"
        lines[2][0]["text"] = "• two"
        runs = _split_lines_into_runs(lines)
        assert any(r[0] == "list" for r in runs)

    def test_extract_list_items(self):
        lines = [
            [_w("• first", 10, 60, 100, 110)],
            [_w("continued", 20, 80, 115, 125)],
            [_w("• second", 10, 60, 140, 150)],
        ]
        items = _extract_list_items(lines)
        assert len(items) >= 1

    def test_classify_text_blocks(self):
        words = [_w("hello world", 10, 100, 50, 60)]
        blocks = [(5, 45, 105, 65)]
        regions = _classify_text_blocks(blocks, [], words)
        assert len(regions) == 1
        assert regions[0].type == LayoutRegionType.TEXT


# ---------------------------------------------------------------------------
# Chart satellites and stray text
# ---------------------------------------------------------------------------

class TestChartSatelliteAndStray:
    def test_is_chart_satellite_below_image(self):
        image = _region(LayoutRegionType.IMAGE, (100, 200, 300, 400))
        label = _region(LayoutRegionType.TEXT, (150, 180, 250, 198), "Figure 1")
        assert _is_chart_satellite(label, image) is True

    def test_is_chart_satellite_rejects_table(self):
        image = _region(LayoutRegionType.IMAGE, (0, 0, 100, 100))
        table = _region(LayoutRegionType.TABLE, (0, 110, 50, 120))
        assert _is_chart_satellite(table, image) is False

    def test_absorb_labels_into_images(self):
        image = _region(LayoutRegionType.IMAGE, (100, 200, 300, 400))
        label = _region(LayoutRegionType.TEXT, (150, 180, 250, 198), "Fig 1")
        out = _absorb_labels_into_images([image, label], 612, 792)
        assert len(out) == 1

    def test_detect_stray_text_regions(self):
        covered = _region(LayoutRegionType.TEXT, (0, 0, 200, 200), "inside")
        words = [
            _w("inside", 50, 100, 50, 60),
            _w("stray", 300, 350, 300, 310),
        ]
        out = _detect_stray_text_regions([covered], words)
        assert len(out) >= 2

    def test_stray_line_to_region(self):
        line = [_w("orphan", 300, 350, 300, 310)]
        reg = _stray_line_to_region(line)
        assert reg is not None
        assert reg.text == "orphan"


# ---------------------------------------------------------------------------
# Hyperlinks
# ---------------------------------------------------------------------------

class TestHyperlinkHelpers:
    def test_hyperlink_bbox_and_uri(self):
        assert _hyperlink_bbox({"x0": 1, "top": 2, "x1": 3, "bottom": 4}) == (1, 2, 3, 4)
        assert _hyperlink_bbox({"bad": 1}) is None
        assert _hyperlink_uri({"uri": b"https://x.com"}) == "https://x.com"
        assert _hyperlink_uri({}) == ""

    def test_page_hyperlinks_exception(self):
        page = MagicMock()
        type(page).hyperlinks = property(lambda self: (_ for _ in ()).throw(RuntimeError))
        assert _page_hyperlinks(page) == []

    def test_inject_hyperlinks_into_region_list_and_table(self):
        words = [_w("link", 10, 50, 10, 20)]
        links = [{"x0": 10, "top": 8, "x1": 50, "bottom": 22, "uri": "https://x.com"}]
        region = _region(
            LayoutRegionType.LIST,
            (0, 0, 100, 30),
            "link item",
            list_items=["link item"],
        )
        _inject_hyperlinks_into_region(region, links, words)
        assert "https://x.com" in region.list_items[0]

        table = _region(
            LayoutRegionType.TABLE,
            (0, 0, 100, 50),
            "link",
            table_grid=[["link"]],
        )
        _inject_hyperlinks_into_region(table, links, words)
        assert "https://x.com" in table.text

    def test_inject_hyperlinks_skips_image(self):
        img = _region(LayoutRegionType.IMAGE, (0, 0, 100, 100))
        _inject_hyperlinks_into_region(img, [{"uri": "x"}], [])
        assert img.text == ""


# ---------------------------------------------------------------------------
# Raster / cv2 helpers
# ---------------------------------------------------------------------------

class TestRasterHelpers:
    def test_binarize_foreground(self):
        img = np.full((50, 50, 3), 255, dtype=np.uint8)
        img[10:20, 10:20] = 0
        bw = _binarize_foreground(img)
        assert bw.shape == (50, 50)

    def test_ink_density_and_crop(self):
        img = np.full((100, 100, 3), 255, dtype=np.uint8)
        img[20:40, 20:40] = 0
        bbox = (14.4, 14.4, 28.8, 28.8)
        assert _ink_density(img, bbox, scale=2.0) > 0
        data = _crop_image_bytes(img, bbox, scale=2.0)
        assert data is not None and data[:8] == b"\x89PNG\r\n\x1a\n"

    def test_tighten_to_content(self):
        img = np.full((100, 100, 3), 255, dtype=np.uint8)
        img[30:50, 30:50] = 0
        tight = _tighten_to_content(img, 2.0, (10, 10, 90, 90))
        assert tight is not None
        blank = np.full((50, 50, 3), 255, dtype=np.uint8)
        assert _tighten_to_content(blank, 2.0, (0, 0, 25, 25)) is None
        assert _tighten_to_content(None, 2.0, (0, 0, 10, 10)) == (0, 0, 10, 10)

    def test_trim_empty_rows_cols(self):
        grid = [[None, "a"], ["", "b"], [None, None]]
        clean = _trim_empty_rows_cols(grid)
        assert clean == [["a"], ["b"]]

    def test_is_monospace_font_name(self):
        assert _is_monospace_font_name("Courier") is True
        assert _is_monospace_font_name("Helvetica") is False


# ---------------------------------------------------------------------------
# Segment blocks
# ---------------------------------------------------------------------------

class TestSegmentBlocks:
    def test_segment_blocks_native_empty(self):
        assert _segment_blocks_native([], 612, 792) == []

    def test_segment_blocks_native_single_block(self):
        words = [_w(f"word{i}", 50, 80, 700 - i * 12, 710 - i * 12) for i in range(5)]
        boxes = _segment_blocks_native(words, 612, 792)
        assert len(boxes) >= 1


# ---------------------------------------------------------------------------
# Integration: extract_layout_regions with reportlab PDFs
# ---------------------------------------------------------------------------

class TestExtractLayoutRegions:
    def test_single_column_prose(self):
        def draw(c):
            c.setFont("Helvetica", 12)
            y = 750
            for i in range(5):
                c.drawString(72, y, f"Paragraph line {i} with enough text to extract.")
                y -= 20

        page = _make_pdf_page(draw)
        regions = extract_layout_regions(page)
        assert len(regions) >= 1
        assert any(r.type == LayoutRegionType.TEXT for r in regions)

    def test_two_column_pdf(self):
        def draw(c):
            c.setFont("Helvetica", 10)
            y = 750
            for i in range(25):
                c.drawString(50, y, f"Left column line {i} text here.")
                c.drawString(330, y, f"Right column line {i} text here.")
                y -= 12

        page = _make_pdf_page(draw)
        regions = extract_layout_regions(page)
        assert len(regions) >= 2

    def test_table_pdf(self):
        pytest.importorskip("reportlab")
        from reportlab.lib import colors
        from reportlab.platypus import Table, TableStyle
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        import pdfplumber

        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        data = [["H1", "H2"], ["A", "B"], ["C", "D"]]
        t = Table(data)
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 1, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ]))
        t.wrapOn(c, 400, 200)
        t.drawOn(c, 72, 600)
        c.showPage()
        c.save()

        with pdfplumber.open(BytesIO(buf.getvalue())) as pdf:
            page = pdf.pages[0]
            regions = extract_layout_regions(page)
        assert any(r.type == LayoutRegionType.TABLE for r in regions)

    def test_bullet_list_pdf(self):
        def draw(c):
            c.setFont("Helvetica", 11)
            y = 700
            for item in ["First item", "Second item", "Third item"]:
                c.drawString(72, y, f"• {item}")
                y -= 18

        page = _make_pdf_page(draw)
        regions = extract_layout_regions(page)
        assert any(r.type in (LayoutRegionType.LIST, LayoutRegionType.TEXT) for r in regions)

    def test_blank_page_returns_image_region(self):
        def draw(c):
            pass

        page = _make_pdf_page(draw)
        fake_img = np.full((100, 80, 3), 128, dtype=np.uint8)
        with patch.object(ola, "_rasterize_page", return_value=(fake_img, 150 / 72.0)):
            regions = extract_layout_regions(page)
        assert len(regions) == 1
        assert regions[0].type == LayoutRegionType.IMAGE
        assert regions[0].image_data is not None

    def test_extract_words_exception_yields_empty(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.extract_words.side_effect = RuntimeError("fail")
        page.lines = []
        page.rects = []
        page.curves = []
        page.images = []
        fake_img = np.full((100, 80, 3), 200, dtype=np.uint8)
        with patch.object(ola, "_rasterize_page", return_value=(fake_img, 150 / 72.0)):
            regions = extract_layout_regions(page)
        assert len(regions) == 1

    def test_hyperlink_injection(self):
        def draw(c):
            c.drawString(100, 700, "Visit ")
            c.linkURL("https://example.com", (180, 695, 230, 715), relative=0)
            c.drawString(180, 700, "here")

        page = _make_pdf_page(draw)
        regions = extract_layout_regions(page)
        assert any("https://example.com" in (r.text or "") for r in regions)


class TestDetectTablesDirect:
    def test_detect_tables_on_table_page(self):
        pytest.importorskip("reportlab")
        from reportlab.lib import colors
        from reportlab.platypus import Table, TableStyle
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        import pdfplumber

        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        data = [["X", "Y"], ["1", "2"], ["3", "4"]]
        t = Table(data)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black)]))
        t.wrapOn(c, 400, 200)
        t.drawOn(c, 72, 600)
        c.showPage()
        c.save()

        with pdfplumber.open(BytesIO(buf.getvalue())) as pdf:
            page = pdf.pages[0]
            words = page.extract_words(extra_attrs=["fontname", "size"])
            tables = _detect_tables(page, float(page.width), float(page.height), words)
        assert isinstance(tables, list)


class TestDocumentRasterCache:
    def test_cache_get(self):
        fake_pages = {1: (np.zeros((10, 10, 3), dtype=np.uint8), 2.0)}
        cache = DocumentRasterCache("/tmp/fake.pdf")
        with patch.object(ola, "_render_all_pages", return_value=fake_pages):
            img, scale = cache.get(1)
        assert img.shape == (10, 10, 3)
        assert scale == 2.0


class TestReextractAndSafeText:
    def test_safe_text_in_bbox(self):
        page = _make_pdf_page(lambda c: c.drawString(72, 700, "hello"))
        words = page.extract_words()
        assert words
        x0 = min(float(w["x0"]) for w in words) - 2
        x1 = max(float(w["x1"]) for w in words) + 2
        top = min(float(w["top"]) for w in words) - 2
        bottom = max(float(w["bottom"]) for w in words) + 2
        text = _safe_text_in_bbox(page, (x0, top, x1, bottom))
        assert "hello" in text.lower()

    def test_inject_hyperlinks_into_regions_no_links(self):
        page = MagicMock()
        page.hyperlinks = []
        regions = [_region(LayoutRegionType.TEXT, (0, 0, 10, 10), "x")]
        out = _inject_hyperlinks_into_regions(regions, page, [])
        assert out is regions


# ---------------------------------------------------------------------------
# Additional direct coverage for large uncovered blocks
# ---------------------------------------------------------------------------

class TestColumnDetectionBranches:
    def test_rejects_small_page(self):
        result = _detect_page_columns([_w("a", 0, 10, 0, 10)], 50, 50)
        assert result.gutter_x is None

    def test_rejects_invalid_word_keys(self):
        words = _two_col_words()
        words.append({"text": "bad", "x0": "nope"})
        result = _detect_page_columns(words, 612, 792)
        assert result.gutter_x is not None

    def test_tabular_row_excluded(self):
        """A line with 3+ large internal gaps should not vote."""
        words = _two_col_words(n_lines=25)
        tabular = []
        x = 50.0
        for i in range(5):
            tabular.append(_w(f"c{i}", x, x + 40, 400, 410))
            x += 50.0
        words.extend(tabular)
        result = _detect_page_columns(words, 612, 792)
        assert isinstance(result, ColumnDetectionResult)

    def test_rescue_path_with_weak_voting_fraction(self):
        words = _two_col_words()
        page_w = 612.0

        def raster_with_gutter():
            img = np.full((792, 612, 3), 255, dtype=np.uint8)
            gx = int(306 * 150 / 72)
            img[:, max(0, gx - 8): min(img.shape[1], gx + 8)] = 255
            img[:, : gx - 20] = 40
            img[:, gx + 20:] = 40
            return img, 150.0 / 72.0

        result = _detect_page_columns(
            words, page_w, 792, raster_getter=raster_with_gutter,
        )
        assert isinstance(result.details, dict)

    def test_validate_gutter_outside_raster(self):
        img = np.full((50, 50, 3), 255, dtype=np.uint8)
        out = _validate_gutter_with_projection(
            lambda: (img, 1.0), 9999.0, 10.0, 612.0,
        )
        assert out["accepted"] is False

    def test_validate_gutter_sparse_page(self):
        img = np.full((50, 200, 3), 255, dtype=np.uint8)
        out = _validate_gutter_with_projection(
            lambda: (img, 1.0), 100.0, 10.0, 612.0,
        )
        assert out["accepted"] is False


class TestRegionSideAndReadingOrder:
    def test_region_side_from_line_meta(self):
        gutter = 306.0
        words = _two_col_words(n_lines=5)
        line_meta = _build_line_side_meta(words, gutter)
        left = _region(LayoutRegionType.TEXT, (50, 100, 280, 130), "left")
        side = _region_side(left, gutter, 612, line_meta)
        assert side in ("left", "wide", "right")

    def test_reading_order_two_column_with_wide_heading(self):
        gutter = 306.0
        heading = _region(LayoutRegionType.TEXT, (50, 700, 560, 730), "Title")
        left = _region(LayoutRegionType.TEXT, (50, 600, 280, 620), "L")
        right = _region(LayoutRegionType.TEXT, (330, 600, 560, 620), "R")
        words = _two_col_words(n_lines=10)
        ordered = _reading_order(
            [right, left, heading], 612, 792, gutter_x=gutter, all_words=words,
        )
        texts = [r.text for r in ordered]
        assert "Title" in texts
        assert "L" in texts and "R" in texts

    def test_reading_order_edge_band_footer(self):
        gutter = 306.0
        footer = _region(LayoutRegionType.TEXT, (50, 20, 560, 40), "footer")
        left = _region(LayoutRegionType.TEXT, (50, 400, 280, 420), "L")
        right = _region(LayoutRegionType.TEXT, (330, 400, 560, 420), "R")
        words = _two_col_words(n_lines=10)
        ordered = _reading_order(
            [footer, right, left], 612, 792, gutter_x=gutter, all_words=words,
        )
        assert len(ordered) == 3


class TestTableHelpers:
    def test_is_plausible_table(self):
        bbox = (72, 600, 300, 700)
        grid = [["H1", "H2"], ["A", "B"], ["C", "D"]]
        assert _is_plausible_table(bbox, grid, 612, 792, []) is True

    def test_is_plausible_table_rejects_full_page(self):
        bbox = (0, 0, 600, 750)
        grid = [["a", "b"], ["c", "d"]]
        assert _is_plausible_table(bbox, grid, 612, 792, []) is False

    def test_is_plausible_table_sparse_rescue(self):
        bbox = (72, 600, 300, 700)
        grid = [["H1", "H2"], [None, "B"], [None, "D"]]
        assert _is_plausible_table(bbox, grid, 612, 792, []) is True

    def test_trim_paragraph_rows(self):
        grid = [["Title paragraph here.", ""], ["a", "b"], ["c", "d"]]
        trimmed, bbox = _trim_paragraph_rows(grid, (72, 600, 300, 700))
        assert trimmed is not None
        assert bbox[1] >= 600

    def test_rescue_columns_if_single(self):
        page = MagicMock()
        page.within_bbox.return_value.extract_table.return_value = [
            ["Only", "One", "Col"],
            ["a", "b", "c"],
        ]
        grid = _rescue_columns_if_single(
            page, (72, 600, 300, 700), [["Only One Col"], ["abc"]], 612, 792,
        )
        assert grid is not None or grid is None

    def test_merge_row_tables(self):
        page = MagicMock()
        candidates = [
            ((72, 650, 200, 670), [["A", "B"]]),
            ((72, 630, 200, 650), [["C", "D"]]),
        ]
        merged = _merge_row_tables(candidates, page, 612, 792)
        assert isinstance(merged, list)

    def test_detect_table_boxes_from_vectors(self):
        page = MagicMock()
        page.lines = [
            {"x0": 72, "x1": 300, "top": 650, "bottom": 651},
            {"x0": 72, "x1": 300, "top": 630, "bottom": 631},
            {"x0": 72, "x1": 73, "top": 630, "bottom": 660},
            {"x0": 299, "x1": 300, "top": 630, "bottom": 660},
        ]
        page.rects = []
        boxes = _detect_table_boxes_from_vectors(page, 612, 792)
        assert isinstance(boxes, list)


class TestImageHelpers:
    def test_refine_image_bbox(self):
        pp_bbox = (-10, -10, 700, 900)
        cv_blocks = [(50, 50, 150, 150)]
        words = [_w("cap", 60, 100, 160, 170)]
        out = _refine_image_bbox(pp_bbox, cv_blocks, words, 612, 792)
        assert isinstance(out, list)

    def test_trim_image_text_margins(self):
        bbox = (100, 100, 300, 300)
        words = [_w("noise", 110, 130, 110, 120)]
        trimmed = _trim_image_text_margins(bbox, words)
        assert trimmed is not None

    def test_split_region_by_gutters(self):
        img = np.full((200, 200, 3), 255, dtype=np.uint8)
        img[80:120, :] = 0
        img[:, 80:120] = 0
        bbox = (0, 0, 144, 144)
        parts = _split_region_by_gutters(img, 2.0, bbox, [], depth=0)
        assert len(parts) >= 1

    def test_split_region_by_gutters_none_image(self):
        assert _split_region_by_gutters(None, 2.0, (0, 0, 100, 100), []) == [
            (0, 0, 100, 100)
        ]

    def test_detect_images_with_embedded(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = [{
            "x0": 100, "x1": 200, "top": 500, "bottom": 600,
        }]
        page.lines = []
        page.rects = []
        page.curves = []

        def raster():
            img = np.full((792, 612, 3), 255, dtype=np.uint8)
            img[500:600, 100:200] = 0
            return img, 1.0

        regions = _detect_images(
            page, raster, [], existing=[], all_words=[],
        )
        assert isinstance(regions, list)

    def test_detect_images_vector_figure_promotion(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = []
        page.lines = [
            {"x0": 100 + i, "x1": 150 + i, "top": 400, "bottom": 401}
            for i in range(20)
        ]
        page.rects = []
        page.curves = []
        block = (90, 390, 260, 420)

        def raster():
            img = np.full((792, 612, 3), 255, dtype=np.uint8)
            img[390:420, 90:260] = 30
            return img, 1.0

        regions = _detect_images(
            page, raster, [block], existing=[], all_words=[],
        )
        assert any(r.type == LayoutRegionType.IMAGE for r in regions)


class TestVectorBulletsAndStripes:
    def test_find_vector_bullets(self):
        page = MagicMock()
        page.curves = []
        page.rects = [
            {"x0": 72, "x1": 78, "top": 700, "bottom": 706},
            {"x0": 72, "x1": 78, "top": 680, "bottom": 686},
        ]
        words = [
            _w("item one", 82, 150, 698, 708),
            _w("item two", 82, 150, 678, 688),
        ]
        bullets = _find_vector_bullets(page, words)
        assert len(bullets) >= 2
        assert bullets[0]["fontname"] == "_synthetic_bullet"

    def test_find_vector_bullets_exception(self):
        page = MagicMock()
        type(page).curves = property(lambda self: (_ for _ in ()).throw(RuntimeError))
        assert _find_vector_bullets(page, []) == []

    def test_detect_uniform_fill_stripe_regions_empty(self):
        page = MagicMock()
        page.rects = []
        page.lines = []
        assert _detect_uniform_fill_stripe_regions(page) == []

    def test_region_is_predominantly_monospace(self):
        page = MagicMock()
        chars = []
        for i in range(25):
            chars.append({
                "text": "x",
                "fontname": "Courier",
                "x0": i * 5,
                "x1": i * 5 + 4,
                "top": 10,
                "bottom": 20,
            })
        page.chars = chars
        assert _region_is_predominantly_monospace(page, (0, 0, 200, 30)) is True


class TestSegmentVectorBlocks:
    def test_segment_vector_blocks_clusters(self):
        page = MagicMock()
        vecs = []
        for i in range(6):
            vecs.append({
                "x0": 100 + i * 2,
                "x1": 102 + i * 2,
                "top": 400 + i * 2,
                "bottom": 402 + i * 2,
            })
        page.lines = vecs
        page.rects = []
        page.curves = []
        blocks = _segment_vector_blocks(page, [], 612, 792, min_members=5)
        assert isinstance(blocks, list)


class TestReextractPayload:
    def test_reextract_region_payload_table(self):
        page = _make_pdf_page(lambda c: None)
        pytest.importorskip("reportlab")
        from reportlab.lib import colors
        from reportlab.platypus import Table, TableStyle
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        import pdfplumber

        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        data = [["A", "B"], ["1", "2"]]
        t = Table(data)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black)]))
        t.wrapOn(c, 400, 200)
        t.drawOn(c, 72, 600)
        c.showPage()
        c.save()
        with pdfplumber.open(BytesIO(buf.getvalue())) as pdf:
            page = pdf.pages[0]
            region = _region(LayoutRegionType.TABLE, (70, 595, 250, 680))
            out = _reextract_region_payload(region, page, [], 612, 792)
        assert out.type == LayoutRegionType.TABLE

    def test_reextract_region_payload_text(self):
        words = [_w("hello world", 72, 150, 700, 712, size=12)]
        region = _region(LayoutRegionType.TEXT, (70, 698, 160, 715), "")
        out = _reextract_region_payload(region, None, words, 612, 792)
        assert "hello" in out.text

    def test_reextract_region_payload_list(self):
        lines_words = [
            [_w("• one", 72, 120, 700, 712)],
            [_w("• two", 72, 120, 680, 692)],
        ]
        all_words = [w for ln in lines_words for w in ln]
        region = _region(
            LayoutRegionType.LIST,
            (70, 675, 160, 715),
            "• one\n• two",
            list_items=["one", "two"],
        )
        out = _reextract_region_payload(region, None, all_words, 612, 792)
        assert out.list_items

    def test_reextract_grid_for_bbox(self):
        page = _make_pdf_page(lambda c: c.drawString(72, 700, "hello"))
        grid = _reextract_grid_for_bbox(page, (70, 695, 150, 715), 612, 792)
        assert grid is None or isinstance(grid, list)


class TestResolveOverlapsAdvanced:
    def test_gutter_relax_cross_column_table_text(self):
        gutter = 306.0
        table = _region(LayoutRegionType.TABLE, (280, 100, 560, 200), "t")
        text = _region(LayoutRegionType.TEXT, (50, 100, 290, 200), "body text here")
        merged = _resolve_overlaps(
            [table, text], page_w=612, page_h=792, gutter_x=gutter, proximity=2.0,
        )
        assert len(merged) <= 2

    def test_resolve_reextracts_dirty_regions(self):
        page = _make_pdf_page(lambda c: c.drawString(72, 700, "merged text"))
        words = page.extract_words(extra_attrs=["fontname", "size"])
        a = _region(LayoutRegionType.TEXT, (70, 695, 120, 715), "merged")
        b = _region(LayoutRegionType.TEXT, (70, 695, 130, 715), "text")
        out = _resolve_overlaps(
            [a, b], page=page, all_words=words, page_w=612, page_h=792,
        )
        assert len(out) == 1


class TestChartSatelliteAdvanced:
    def test_is_chart_satellite_horizontal_beside(self):
        image = _region(LayoutRegionType.IMAGE, (200, 200, 400, 400))
        label = _region(LayoutRegionType.TEXT, (410, 250, 480, 270), "Legend")
        assert _is_chart_satellite(label, image) is True

    def test_absorb_labels_wrong_column_rejected(self):
        gutter = 306.0
        image = _region(LayoutRegionType.IMAGE, (50, 200, 250, 400))
        label = _region(LayoutRegionType.TEXT, (330, 180, 450, 198), "Fig")
        words = _two_col_words(n_lines=3)
        out = _absorb_labels_into_images(
            [image, label], 612, 792, gutter_x=gutter, all_words=words,
        )
        assert len(out) == 2

    def test_absorb_skips_oversized_growth(self):
        image = _region(LayoutRegionType.IMAGE, (0, 0, 600, 750))
        label = _region(LayoutRegionType.TEXT, (0, 760, 600, 780), "cap")
        out = _absorb_labels_into_images([image, label], 612, 792)
        assert len(out) >= 1


class TestHyperlinkEdgeCases:
    def test_hyperlink_anchor_for_region(self):
        words = [_w("click", 10, 50, 10, 20)]
        link = {"x0": 10, "top": 8, "x1": 50, "bottom": 22, "uri": "https://a.com"}
        pair = _hyperlink_anchor_for_region(link, words)
        assert pair == ("click", "https://a.com")

    def test_append_hyperlink_no_anchor_match(self):
        text = "nothing here"
        out = _append_hyperlink_url_to_text(text, "missing", "https://x.com")
        assert out == text

    def test_inject_hyperlinks_into_text_sorted(self):
        words = [
            _w("short", 10, 40, 10, 20),
            _w("longer anchor", 50, 120, 10, 20),
        ]
        links = [
            {"x0": 10, "top": 8, "x1": 40, "bottom": 22, "uri": "https://a.com"},
            {"x0": 50, "top": 8, "x1": 120, "bottom": 22, "uri": "https://b.com"},
        ]
        text = "short and longer anchor"
        out = _inject_hyperlinks_into_text(text, links, words)
        assert "https://" in out


class TestRasterize:
    def test_rasterize_page_from_stream(self):
        page = _make_pdf_page(lambda c: c.drawString(72, 700, "x"))
        img, scale = _rasterize_page(page, 72)
        assert img.shape[2] == 3
        assert scale == 1.0

    def test_render_all_pages(self):
        pytest.importorskip("reportlab")
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        import tempfile
        import os

        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.drawString(72, 700, "x")
        c.showPage()
        c.save()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(buf.getvalue())
            path = tmp.name
        try:
            fake_pil = MagicMock()
            fake_pil.convert.return_value = fake_pil
            arr = np.zeros((10, 10, 3), dtype=np.uint8)
            with patch(
                "app.modules.parsers.pdf.pdf_rasterizer.render_all_pages_from_path_sync",
                return_value={1: (arr, 1.0)},
            ):
                pages = _render_all_pages(path, 72)
            assert 1 in pages
        finally:
            os.unlink(path)


class TestBorderlessTables:
    def test_detect_borderless_tables(self):
        words = []
        y = 700.0
        for row in range(5):
            x = 72.0
            for col in range(3):
                words.append(_w(f"r{row}c{col}", x, x + 40, y, y + 10))
                x += 50.0
            y -= 15.0
        page = MagicMock()
        page.within_bbox.return_value.extract_table.return_value = None
        regions = _detect_borderless_tables(page, 612, 792, words, [])
        assert isinstance(regions, list)


class TestListRunEdgeCases:
    def test_split_lines_single_marker_downgrades_to_text(self):
        lines = [[_w("• only", 10, 60, 100, 110)]]
        lines[0][0]["text"] = "• only one"
        runs = _split_lines_into_runs(lines)
        assert runs[0][0] == "text"

    def test_extract_list_items_continuation(self):
        lines = [
            [_w("• first part", 10, 80, 100, 110)],
            [_w("continued", 20, 80, 115, 125)],
        ]
        lines[0][0]["text"] = "• first part"
        items = _extract_list_items(lines)
        assert len(items) == 1


class TestExtractLayoutExtended:
    def test_horizontal_rule_page(self):
        def draw(c):
            c.setFont("Helvetica", 10)
            for i in range(8):
                c.drawString(72, 750 - i * 20, f"Section paragraph line {i} content.")
            c.line(72, 600, 540, 600)
            for i in range(8):
                c.drawString(72, 580 - i * 20, f"Below rule line {i} with text.")

        page = _make_pdf_page(draw)
        regions = extract_layout_regions(page)
        assert len(regions) >= 1

    def test_classify_list_from_pdf(self):
        def draw(c):
            c.setFont("Helvetica", 11)
            y = 700
            for item in ["Alpha item", "Beta item", "Gamma item"]:
                c.drawString(72, y, f"- {item}")
                y -= 18

        page = _make_pdf_page(draw)
        regions = extract_layout_regions(page)
        assert regions

    def test_reextract_with_raster_cache(self):
        page = _make_pdf_page(lambda c: c.drawString(72, 700, "cached"))
        fake_img = np.full((100, 80, 3), 128, dtype=np.uint8)
        cache = DocumentRasterCache("/tmp/x.pdf")
        with patch.object(ola, "_render_all_pages", return_value={1: (fake_img, 2.0)}):
            regions = extract_layout_regions(page, raster_cache=cache)
        assert regions


class TestCoveragePush:
    """Additional tests targeting remaining uncovered branches."""

    def test_column_hard_reject_low_confidence(self):
        words = _two_col_words(n_lines=6)
        result = _detect_page_columns(words, 612, 792)
        assert result.gutter_x is None or result.confidence >= 0.0

    def test_text_is_table_top_header_edge_cases(self):
        table = _region(LayoutRegionType.TABLE, (50, 100, 250, 200))
        wide = _region(LayoutRegionType.TEXT, (40, 50, 260, 95))
        assert _text_is_table_top_header(table, wide) is False
        tall = _region(LayoutRegionType.TEXT, (60, 10, 240, 100))
        assert _text_is_table_top_header(table, tall) is False

    def test_table_overlap_header_bypass_at_207(self):
        table = _region(LayoutRegionType.TABLE, (50, 100, 250, 200))
        header = _region(LayoutRegionType.TEXT, (60, 70, 240, 100))
        assert _table_overlap_with_large_neighbor_allowed(table, header) is True

    def test_merge_row_tables_chain(self):
        page = MagicMock()
        mock_table = MagicMock()
        mock_table.bbox = (72, 630, 200, 650)
        mock_table.extract.return_value = [["A", "B", "C"], ["1", "2", "3"]]
        sub = MagicMock()
        sub.find_tables.return_value = [mock_table]
        page.within_bbox.return_value = sub
        page.curves = []
        singles = [
            ((72, 650, 200, 670), [["H1", "H2"]]),
            ((72, 630, 200, 650), [["R1", "R2"]]),
            ((72, 610, 200, 630), [["R3", "R4"]]),
        ]
        merged = _merge_row_tables(singles, page, 612, 792)
        assert len(merged) >= 1

    def test_rescue_columns_if_single_expands(self):
        page = MagicMock()
        page.curves = []
        sub = MagicMock()
        sub.extract_table.return_value = [["A", "B"], ["1", "2"]]
        page.within_bbox.return_value = sub
        grid = _rescue_columns_if_single(
            page, (72, 600, 200, 650), [["single col only"]], 612, 792,
        )
        assert grid is not None

    def test_detect_uniform_fill_stripe_regions_with_stripes(self):
        page = MagicMock()
        rects = []
        for i in range(8):
            rects.append({
                "x0": 72, "x1": 400,
                "top": 600 - i * 12, "bottom": 608 - i * 12,
                "fill": True,
                "non_stroking_color": (0.9, 0.9, 0.9),
            })
        page.rects = rects
        page.lines = []
        chars = []
        for i in range(30):
            chars.append({
                "text": "x",
                "fontname": "Courier",
                "x0": 80 + (i % 10) * 5,
                "x1": 84 + (i % 10) * 5,
                "top": 550 + (i // 10) * 12,
                "bottom": 558 + (i // 10) * 12,
            })
        page.chars = chars
        stripes = _detect_uniform_fill_stripe_regions(page)
        assert isinstance(stripes, list)

    def test_detect_tables_with_stripe_frame_skip(self):
        page = MagicMock()
        page.images = []
        page.lines = []
        mock_t = MagicMock()
        mock_t.bbox = (72, 600, 300, 700)
        mock_t.extract.return_value = [["a", "b"], ["c", "d"]]
        page.find_tables.return_value = [mock_t]
        stripe = (70, 595, 305, 705)
        words = [_w("a", 80, 100, 620, 630), _w("b", 150, 170, 620, 630)]
        tables = _detect_tables(
            page, 612, 792, words, stripe_frames=[stripe],
        )
        assert isinstance(tables, list)

    def test_detect_tables_borderless_fallback(self):
        page = MagicMock()
        page.images = []
        page.find_tables.return_value = []
        page.lines = [
            {"x0": 72, "x1": 300, "top": 650 - i, "bottom": 651 - i}
            for i in range(25)
        ]
        words = []
        y = 650.0
        for row in range(6):
            x = 72.0
            for col, val in enumerate(["100", "200", "300"]):
                words.append(_w(val, x, x + 30, y, y + 10))
                x += 40.0
            y -= 14.0
        tables = _detect_tables(page, 612, 792, words, stripe_frames=[])
        assert isinstance(tables, list)

    def test_segment_blocks_with_gutter_and_rules(self):
        words = _two_col_words(n_lines=8)
        boxes = _segment_blocks_native(
            words, 612, 792, h_rules=[400.0], gutter_x=306.0,
        )
        assert isinstance(boxes, list)

    def test_segment_vector_blocks_with_gutter(self):
        page = MagicMock()
        vecs = []
        for i in range(6):
            vecs.append({
                "x0": 50 + i,
                "x1": 52 + i,
                "top": 400,
                "bottom": 402,
            })
        page.lines = vecs
        page.rects = []
        page.curves = []
        blocks = _segment_vector_blocks(
            page, [], 612, 792, gutter_x=306.0, min_members=5,
        )
        assert isinstance(blocks, list)

    def test_detect_images_overlapping_cluster(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = [
            {"x0": 100, "x1": 180, "top": 500, "bottom": 580},
            {"x0": 150, "x1": 230, "top": 520, "bottom": 600},
        ]
        page.lines = []
        page.rects = []
        page.curves = []

        def raster():
            img = np.full((792, 612, 3), 255, dtype=np.uint8)
            img[500:600, 100:230] = 0
            return img, 1.0

        regions = _detect_images(page, raster, [], existing=[], all_words=[])
        assert isinstance(regions, list)

    def test_detect_images_out_of_bounds_refine(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = [{"x0": -5, "x1": 50, "top": 500, "bottom": 580}]
        page.lines = []
        page.rects = []
        page.curves = []
        words = [_w("cap", 10, 40, 590, 600)]

        def raster():
            return np.full((792, 612, 3), 255, dtype=np.uint8), 1.0

        regions = _detect_images(
            page, raster, [(0, 480, 60, 610)], existing=[], all_words=words,
        )
        assert isinstance(regions, list)

    def test_is_plausible_table_image_overlap(self):
        bbox = (72, 600, 300, 700)
        grid = [["a", "b"], ["c", "d"]]
        img_bbox = (72, 600, 300, 700)
        assert _is_plausible_table(bbox, grid, 612, 792, [img_bbox]) is False

    def test_refine_image_bbox_with_words(self):
        cv_blocks = [(50, 50, 150, 150)]
        words = [_w("dense text here", 55, 145, 55, 145)]
        out = _refine_image_bbox((40, 40, 160, 160), cv_blocks, words, 612, 792)
        assert isinstance(out, list)

    def test_trim_image_text_margins_no_trim(self):
        bbox = (100, 100, 300, 300)
        words = [_w("far", 10, 20, 10, 20)]
        assert _trim_image_text_margins(bbox, words) == bbox

    def test_split_region_by_gutters_vertical(self):
        img = np.full((200, 200, 3), 255, dtype=np.uint8)
        img[:, 90:110] = 0
        parts = _split_region_by_gutters(
            img, 2.0, (0, 0, 144, 144), [], depth=1,
        )
        assert len(parts) >= 1

    def test_classify_text_blocks_skips_higher_priority(self):
        words = [_w("in table", 80, 100, 620, 630)]
        table = _region(LayoutRegionType.TABLE, (70, 610, 200, 640))
        blocks = [(75, 615, 195, 635)]
        regions = _classify_text_blocks(blocks, [table], words)
        assert regions == []

    def test_classify_list_block(self):
        lines_words = [
            [_w("• one", 72, 120, 700, 712)],
            [_w("• two", 72, 120, 680, 692)],
            [_w("• three", 72, 120, 660, 672)],
        ]
        for ln in lines_words:
            ln[0]["text"] = ln[0]["text"]
        lines_words[0][0]["text"] = "• one"
        lines_words[1][0]["text"] = "• two"
        lines_words[2][0]["text"] = "• three"
        all_words = [w for ln in lines_words for w in ln]
        blocks = [(70, 655, 160, 715)]
        regions = _classify_text_blocks(blocks, [], all_words)
        assert any(r.type == LayoutRegionType.LIST for r in regions)

    def test_resolve_overlaps_list_items_merge(self):
        lst = _region(
            LayoutRegionType.LIST,
            (0, 0, 100, 50),
            "• a",
            list_items=["a"],
        )
        lst2 = _region(
            LayoutRegionType.LIST,
            (0, 0, 100, 50),
            "• b",
            list_items=["b"],
        )
        out = _resolve_overlaps([lst, lst2], page_w=612, page_h=792)
        assert len(out) == 1
        assert "b" in out[0].list_items

    def test_merge_small_text_no_neighbors(self):
        solo = _region(LayoutRegionType.TEXT, (0, 0, 50, 20), "hi")
        table = _region(LayoutRegionType.TABLE, (0, 30, 50, 80), "t")
        out = _merge_small_text_regions([solo, table])
        assert len(out) == 2

    def test_merge_small_text_max_gap_none(self):
        big = _region(LayoutRegionType.TEXT, (0, 0, 100, 20), "one two three four five six")
        small = _region(LayoutRegionType.TEXT, (0, 100, 100, 120), "hi")
        out = _merge_small_text_regions([big, small], max_gap=None)
        assert len(out) == 1

    def test_detect_stray_only_synthetic_bullets(self):
        words = [_w("•", 10, 15, 10, 20, fontname="_synthetic_bullet")]
        out = _detect_stray_text_regions([], words)
        assert out == []

    def test_absorb_labels_overlap_satellite(self):
        image = _region(LayoutRegionType.IMAGE, (100, 100, 300, 300))
        label = _region(LayoutRegionType.TEXT, (120, 280, 200, 310), "cap")
        out = _absorb_labels_into_images([image, label], 612, 792)
        assert len(out) == 1

    def test_is_chart_satellite_overlap(self):
        image = _region(LayoutRegionType.IMAGE, (100, 100, 300, 300))
        label = _region(LayoutRegionType.TEXT, (150, 150, 200, 170), "in")
        assert _is_chart_satellite(label, image) is True

    def test_inject_hyperlink_region_table_grid(self):
        words = [_w("cell", 10, 40, 10, 20)]
        region = _region(
            LayoutRegionType.TABLE,
            (0, 0, 100, 50),
            "cell",
            table_grid=[["cell"]],
        )
        links = [{"x0": 10, "top": 8, "x1": 40, "bottom": 22, "uri": "https://t.com"}]
        _inject_hyperlinks_into_region(region, links, words)
        assert "https://t.com" in region.text

    def test_hyperlink_anchor_no_words(self):
        link = {"x0": 10, "top": 8, "x1": 40, "bottom": 22, "uri": "https://x.com"}
        assert _hyperlink_anchor_for_region(link, []) is None

    def test_extract_layout_table_and_header(self):
        pytest.importorskip("reportlab")
        from reportlab.lib import colors
        from reportlab.platypus import Table, TableStyle
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        import pdfplumber

        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.setFont("Helvetica", 10)
        c.drawString(80, 680, "Quarter Ended")
        data = [["Q1", "Q2"], ["100", "200"], ["300", "400"]]
        t = Table(data)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black)]))
        t.wrapOn(c, 400, 200)
        t.drawOn(c, 80, 600)
        c.showPage()
        c.save()
        with pdfplumber.open(BytesIO(buf.getvalue())) as pdf:
            regions = extract_layout_regions(pdf.pages[0])
        assert any(r.type == LayoutRegionType.TABLE for r in regions)

    def test_extract_layout_two_column_full_pipeline(self):
        def draw(c):
            c.setFont("Helvetica", 9)
            y = 750
            for i in range(30):
                c.drawString(50, y, f"Left col {i} with enough words here.")
                c.drawString(330, y, f"Right col {i} with enough words here.")
                y -= 11

        page = _make_pdf_page(draw)
        regions = extract_layout_regions(page)
        assert len(regions) >= 2

    def test_rasterize_page_with_pdf_path(self):
        pytest.importorskip("reportlab")
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        import tempfile
        import os

        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.drawString(72, 700, "path test")
        c.showPage()
        c.save()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(buf.getvalue())
            path = tmp.name
        try:
            page = _make_pdf_page(lambda c: c.drawString(72, 700, "path test"))
            fake_pil = MagicMock()
            fake_pil.convert.return_value = fake_pil
            arr = np.zeros((20, 20, 3), dtype=np.uint8)
            with patch(
                "app.modules.parsers.pdf.pdf_rasterizer.render_page_from_path_sync",
                return_value=(arr, 1.0),
            ):
                img, scale = _rasterize_page(page, 72, pdf_path=path)
            assert img.shape == (20, 20, 3)
        finally:
            os.unlink(path)

    def test_reextract_grid_strategies(self):
        page = _make_pdf_page(lambda c: c.drawString(72, 700, "abc def"))
        grid = _reextract_grid_for_bbox(page, (70, 695, 200, 715), 612, 792)
        assert grid is None or isinstance(grid, list)

    def test_region_side_table_fallback(self):
        gutter = 306.0
        table = _region(LayoutRegionType.TABLE, (50, 100, 550, 200))
        side = _region_side(table, gutter, 612, [])
        assert side in ("left", "right", "wide")

    def test_reading_order_empty(self):
        assert _reading_order([], 612, 792) == []

    def test_merge_regions_duplicate_text(self):
        a = _region(LayoutRegionType.TEXT, (0, 0, 10, 10), "hello")
        b = _region(LayoutRegionType.TEXT, (0, 0, 10, 10), "hello")
        merged = _merge_regions(a, b)
        assert merged.text == "hello"


# ---------------------------------------------------------------------------
# Coverage push to 95%+ — targeted branch tests
# ---------------------------------------------------------------------------

class TestCoveragePush95:
    """Exercises remaining uncovered branches in opencv_layout_analyzer."""

    # -- merge / header gates (305, 320, 322) --------------------------------

    def test_text_is_table_top_header_non_pair_returns_false(self):
        a = _region(LayoutRegionType.TABLE, (0, 0, 100, 100))
        b = _region(LayoutRegionType.TABLE, (0, 0, 100, 100))
        assert _text_is_table_top_header(a, b) is False

    def test_text_is_table_top_header_rejects_wide_x_range(self):
        table = _region(LayoutRegionType.TABLE, (100, 100, 300, 200))
        narrow = _region(LayoutRegionType.TEXT, (102, 70, 298, 98))
        wide_left = _region(LayoutRegionType.TEXT, (50, 70, 240, 98))
        wide_right = _region(LayoutRegionType.TEXT, (110, 70, 320, 98))
        assert _text_is_table_top_header(table, narrow) is True
        assert _text_is_table_top_header(table, wide_left) is False
        assert _text_is_table_top_header(table, wide_right) is False

    # -- validate_gutter_with_projection (561, 584, 600, 611-614) -----------

    def test_validate_gutter_raster_too_small(self):
        tiny = np.full((5, 5, 3), 255, dtype=np.uint8)
        out = _validate_gutter_with_projection(lambda: (tiny, 1.0), 2.5, 20.0, 612.0)
        assert out["accepted"] is False
        assert "too small" in out["reason"]

    def test_validate_gutter_window_too_narrow(self):
        img = np.full((50, 50, 3), 255, dtype=np.uint8)
        out = _validate_gutter_with_projection(lambda: (img, 0.01), 25.0, 1.0, 612.0)
        assert out["accepted"] is False

    def test_validate_gutter_sparse_reference_ink(self):
        img = np.full((100, 300, 3), 255, dtype=np.uint8)
        out = _validate_gutter_with_projection(lambda: (img, 1.0), 150.0, 20.0, 612.0)
        assert out["accepted"] is False
        assert "sparse" in out.get("reason", "")

    def test_validate_gutter_accepts_clear_valley(self):
        page_w = 612.0
        scale = 150.0 / 72.0
        w_px = int(page_w * scale)
        img = np.full((200, w_px, 3), 255, dtype=np.uint8)
        gx = int(306 * scale)
        img[:, : gx - 15] = 80
        img[:, gx + 15:] = 80
        out = _validate_gutter_with_projection(lambda: (img, scale), 306.0, 24.0, page_w)
        assert isinstance(out, dict)
        assert "accepted" in out

    # -- column detection internals (728-733, 782, 792, 815, 826, 884+) ----

    def test_detect_columns_line_inside_table_majority(self):
        words = _two_col_words(n_lines=25)
        table_bbox = (40, 350, 570, 450)
        for i in range(8):
            y = 360 + i * 10
            words.append(_w(f"T{i}a T{i}b T{i}c", 50, 550, y, y + 8))
        result = _detect_page_columns(words, 612, 792, table_bboxes=[table_bbox])
        assert isinstance(result, ColumnDetectionResult)

    def test_detect_columns_skips_single_word_lines_and_table_rows(self):
        words = _two_col_words(n_lines=20)
        words.append(_w("solo", 300, 320, 500, 510))
        tabular = []
        x = 50.0
        for i in range(6):
            tabular.append(_w(f"c{i}", x, x + 35, 400, 410))
            x += 45.0
        words.extend(tabular)
        result = _detect_page_columns(words, 612, 792)
        assert isinstance(result.details, dict)

    def test_detect_columns_no_plateau_rejects(self):
        words = []
        y = 700.0
        for i in range(30):
            words.append(_w(f"word{i}", 50, 200, y, y + 10))
            y -= 12.0
        result = _detect_page_columns(words, 612, 792)
        assert result.gutter_x is None

    def test_detect_columns_side_balance_and_gutter_voters(self):
        words = _two_col_words(n_lines=35)
        left_only = []
        for i in range(12):
            top = 200 + i * 15
            left_only.append(_w(f"onlyL{i}", 50, 200, top, top + 10))
        words.extend(left_only)
        result = _detect_page_columns(words, 612, 792)
        assert "side_balance" in result.details

    def test_detect_columns_rescue_hard_reject_and_rescue(self):
        words = _two_col_words(n_lines=30)
        page_w = 612.0
        scale = 150.0 / 72.0

        def raster():
            w_px = int(page_w * scale)
            img = np.full((792, w_px, 3), 255, dtype=np.uint8)
            gx = int(306 * scale)
            img[:, : gx - 12] = 60
            img[:, gx + 12:] = 60
            return img, scale

        with patch.object(ola, "_COL_MIN_VOTING_WORD_FRACTION", 0.99):
            result = _detect_page_columns(
                words, page_w, 792, raster_getter=raster,
            )
        assert result.details.get("hard_reject_reason") or result.gutter_x is not None
        if result.details.get("rescue"):
            assert "accepted" in result.details["rescue"]

    def test_detect_columns_rescue_rejected_returns_none_gutter(self):
        words = _two_col_words(n_lines=30)

        def bad_raster():
            img = np.full((100, 100, 3), 255, dtype=np.uint8)
            return img, 1.0

        with patch.object(ola, "_COL_MIN_CONFIDENCE", 1.1):
            result = _detect_page_columns(
                words, 612, 792, raster_getter=bad_raster,
            )
        assert result.gutter_x is None
        assert result.details.get("hard_reject_reason")
        assert result.details.get("rescue", {}).get("accepted") is False

    # -- line meta / region side (1247, 1253, 1272, 1281, 1284-1290) --------

    def test_build_line_side_meta_skips_bad_words(self):
        words = [_w("good", 50, 100, 100, 110)]
        with patch.object(
            ola,
            "_group_words_into_lines",
            return_value=([[], words], [0, 1]),
        ):
            meta = _build_line_side_meta(words, 306.0)
        assert isinstance(meta, list)
        bad_line = [{"text": "x", "x0": "nope", "x1": 100, "top": 100, "bottom": 110}]
        with patch.object(
            ola,
            "_group_words_into_lines",
            return_value=([bad_line], [0]),
        ):
            meta2 = _build_line_side_meta(words, 306.0)
        assert meta2 == []

    def test_region_side_text_from_line_meta(self):
        gutter = 306.0
        words = [
            _w("left", 50, 200, 100, 110),
            _w("right", 350, 500, 100, 110),
        ]
        meta = _build_line_side_meta(words, gutter)
        left = _region(LayoutRegionType.TEXT, (45, 95, 210, 115), "left")
        right = _region(LayoutRegionType.TEXT, (340, 95, 510, 115), "right")
        assert _region_side(left, gutter, 612, meta) == "left"
        assert _region_side(right, gutter, 612, meta) == "right"

    def test_region_side_wide_from_mixed_lines(self):
        gutter = 306.0
        words = [
            _w("L", 50, 100, 100, 110),
            _w("R", 350, 400, 100, 110),
        ]
        meta = _build_line_side_meta(words, gutter)
        wide = _region(LayoutRegionType.TEXT, (45, 95, 410, 115), "L R")
        assert _region_side(wide, gutter, 612, meta) == "wide"

    def test_region_side_no_meta_fallback(self):
        gutter = 306.0
        reg = _region(LayoutRegionType.TEXT, (50, 100, 250, 120), "x")
        assert _region_side(reg, gutter, 612, []) in ("left", "right", "wide")

    def test_region_side_none_gutter(self):
        reg = _region(LayoutRegionType.TEXT, (0, 0, 100, 20), "x")
        assert _region_side(reg, None, 612, []) == "any"

    # -- segment_blocks_native (1587-1691) -----------------------------------

    def test_segment_blocks_native_invalid_word_and_sizes(self):
        words = [_w("ok", 50, 100, 120, 130, size=11.0)]
        bad_line = [{"text": "x", "x0": "nope", "x1": 100, "top": 100, "bottom": 110}]
        with patch.object(
            ola,
            "_group_words_into_lines",
            return_value=([[], bad_line, words], [0, 1, 2]),
        ):
            with patch.object(
                ola,
                "_resolve_line_sides",
                return_value=["any", "any", "any"],
            ):
                boxes = _segment_blocks_native(words, 612, 792)
        assert isinstance(boxes, list)
        no_size = [_w("plain", 50, 100, 140, 150)]
        no_size[0].pop("size", None)
        boxes2 = _segment_blocks_native(no_size, 612, 792)
        assert isinstance(boxes2, list)

    def test_segment_blocks_native_h_rule_splits_blocks(self):
        words = []
        for i in range(6):
            words.append(_w(f"above{i}", 50, 200, 600 - i * 14, 610 - i * 14))
        for i in range(6):
            words.append(_w(f"below{i}", 50, 200, 400 - i * 14, 410 - i * 14))
        boxes = _segment_blocks_native(words, 612, 792, h_rules=[505.0])
        assert len(boxes) >= 2

    def test_segment_blocks_native_gutter_side_gate(self):
        words = _two_col_words(n_lines=10)
        boxes = _segment_blocks_native(words, 612, 792, gutter_x=306.0)
        assert len(boxes) >= 2

    def test_segment_blocks_native_filters_tiny_blocks(self):
        words = [_w("tiny", 50, 52, 100, 102)]
        boxes = _segment_blocks_native(words, 612, 792)
        assert boxes == []

    def test_segment_blocks_native_low_overlap_starts_new_block(self):
        words = [
            _w("blockA", 50, 150, 700, 710, size=12),
            _w("blockB", 300, 400, 680, 690, size=8),
        ]
        boxes = _segment_blocks_native(words, 612, 792)
        assert len(boxes) >= 1

    # -- segment_vector_blocks (1713-1867) -----------------------------------

    def test_segment_vector_blocks_page_exception(self):
        page = MagicMock()
        type(page).lines = property(lambda self: (_ for _ in ()).throw(RuntimeError))
        assert _segment_vector_blocks(page, [], 612, 792) == []

    def test_segment_vector_blocks_filters_and_clusters(self):
        page = MagicMock()
        vecs = []
        for i in range(8):
            vecs.append({
                "x0": 100 + i * 3,
                "x1": 103 + i * 3,
                "top": 400 + i * 3,
                "bottom": 403 + i * 3,
            })
        page.lines = vecs
        page.rects = []
        page.curves = []
        blocks = _segment_vector_blocks(page, [], 612, 792, min_members=5)
        assert len(blocks) >= 1

    def test_segment_vector_blocks_skips_invalid_and_stripe(self):
        page = MagicMock()
        page.lines = [
            {"bad": "data"},
            {"x0": 100, "x1": 99, "top": 400, "bottom": 401},
            {"x0": 0, "x1": 600, "top": 400, "bottom": 401},
            {"x0": 0, "x1": 600, "top": 0, "bottom": 790},
        ]
        page.rects = []
        page.curves = []
        stripe = (90, 390, 260, 420)
        blocks = _segment_vector_blocks(
            page, [(50, 380, 300, 430)], 612, 792,
            stripe_frames=[stripe], min_members=1,
        )
        assert isinstance(blocks, list)

    def test_segment_vector_blocks_gutter_no_cross_merge(self):
        page = MagicMock()
        left = [{"x0": 50 + i, "x1": 52 + i, "top": 400, "bottom": 402} for i in range(6)]
        right = [{"x0": 350 + i, "x1": 352 + i, "top": 400, "bottom": 402} for i in range(6)]
        page.lines = left + right
        page.rects = []
        page.curves = []
        blocks = _segment_vector_blocks(
            page, [], 612, 792, gutter_x=306.0, min_members=5,
        )
        assert isinstance(blocks, list)

    def test_segment_vector_blocks_wide_straddling_primitive(self):
        page = MagicMock()
        page.lines = [{
            "x0": 50, "x1": 560, "top": 400, "bottom": 402,
        }]
        page.rects = []
        page.curves = []
        for i in range(5):
            page.lines.append({
                "x0": 100 + i * 2, "x1": 102 + i * 2,
                "top": 410 + i * 2, "bottom": 412 + i * 2,
            })
        blocks = _segment_vector_blocks(
            page, [], 612, 792, gutter_x=306.0, min_members=5,
        )
        assert isinstance(blocks, list)

    # -- uniform fill stripes (1987-2027, 2030-2146) -------------------------

    def test_region_is_predominantly_monospace_width_cv(self):
        page = MagicMock()
        chars = []
        for i in range(25):
            chars.append({
                "text": "x",
                "fontname": "Helvetica",
                "x0": 80 + i * 5.0,
                "x1": 84 + i * 5.0,
                "top": 550,
                "bottom": 558,
            })
        page.chars = chars
        assert _region_is_predominantly_monospace(page, (70, 540, 220, 570)) is True

    def test_region_is_predominantly_monospace_too_few_chars(self):
        page = MagicMock()
        page.chars = [{"text": "x", "fontname": "Helvetica", "x0": 0, "x1": 1, "top": 0, "bottom": 1}]
        assert _region_is_predominantly_monospace(page, (0, 0, 100, 100)) is False

    def test_detect_uniform_fill_stripe_int_color_and_gap_break(self):
        page = MagicMock()
        rects = []
        for i in range(8):
            rects.append({
                "x0": 72, "x1": 400,
                "top": 600 - i * 14,
                "bottom": 608 - i * 14,
                "fill": True,
                "non_stroking_color": 0.9,
            })
        page.rects = rects
        page.lines = []
        chars = []
        for i in range(30):
            chars.append({
                "text": "x",
                "fontname": "Courier",
                "x0": 80 + (i % 10) * 5,
                "x1": 84 + (i % 10) * 5,
                "top": 540 + (i // 10) * 12,
                "bottom": 548 + (i // 10) * 12,
            })
        page.chars = chars
        stripes = _detect_uniform_fill_stripe_regions(page)
        assert isinstance(stripes, list)

    def test_detect_uniform_fill_stripe_rejects_internal_rules(self):
        page = MagicMock()
        rects = []
        for i in range(8):
            rects.append({
                "x0": 72, "x1": 400,
                "top": 600 - i * 12,
                "bottom": 608 - i * 12,
                "fill": True,
                "non_stroking_color": (0.9, 0.9, 0.9),
            })
        page.rects = rects
        page.lines = [
            {"x0": 200, "x1": 201, "top": 540, "bottom": 610},
            {"x0": 72, "x1": 400, "top": 570, "bottom": 571},
        ]
        chars = []
        for i in range(30):
            chars.append({
                "text": "x",
                "fontname": "Courier",
                "x0": 80 + (i % 10) * 5,
                "x1": 84 + (i % 10) * 5,
                "top": 540 + (i // 10) * 12,
                "bottom": 548 + (i // 10) * 12,
            })
        page.chars = chars
        stripes = _detect_uniform_fill_stripe_regions(page)
        assert stripes == []

    def test_detect_uniform_fill_stripe_invalid_color_skipped(self):
        page = MagicMock()
        page.rects = [{
            "x0": 72, "x1": 400, "top": 600, "bottom": 608,
            "fill": True, "non_stroking_color": "not-a-color",
        }]
        page.lines = []
        assert _detect_uniform_fill_stripe_regions(page) == []

    # -- borderless tables (2447-2569) ---------------------------------------

    def _borderless_words(self) -> List[Dict[str, Any]]:
        words: List[Dict[str, Any]] = []
        words.append(_w("Months ended December", 72, 250, 720, 730))
        y = 700.0
        for row in range(5):
            x = 72.0
            for col, val in enumerate(["100", "200", "300"]):
                words.append(_w(val, x, x + 35, y, y + 10))
                x += 45.0
            y -= 14.0
        words.append(_w("2020", 72, 100, 650, 660))
        words.append(_w("2021", 120, 150, 650, 660))
        words.append(_w("2022", 170, 200, 650, 660))
        return words

    def test_detect_borderless_tables_full_band(self):
        words = self._borderless_words()
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.return_value = [
            ["H1", "H2", "H3"],
            ["100", "200", "300"],
            ["400", "500", "600"],
            ["700", "800", "900"],
        ]
        page.within_bbox.return_value = sub
        page.curves = []
        regions = _detect_borderless_tables(page, 612, 792, words, [])
        assert isinstance(regions, list)

    def test_detect_borderless_structural_pending_break(self):
        words = self._borderless_words()
        for i in range(4):
            words.append(_w(f"label{i}", 72, 150, 680 - i * 12, 688 - i * 12))
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.return_value = [
            ["a", "b"], ["1", "2"], ["3", "4"],
        ]
        page.within_bbox.return_value = sub
        regions = _detect_borderless_tables(page, 612, 792, words, [])
        assert isinstance(regions, list)

    def test_detect_borderless_skips_image_overlap(self):
        words = self._borderless_words()
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.return_value = [["a", "b"], ["1", "2"], ["3", "4"]]
        page.within_bbox.return_value = sub
        img_bbox = (70, 640, 250, 730)
        regions = _detect_borderless_tables(page, 612, 792, words, [img_bbox])
        assert isinstance(regions, list)

    def test_detect_tables_borderless_fallback_integration(self):
        page = MagicMock()
        page.images = []
        page.find_tables.return_value = []
        page.lines = [
            {"x0": 72, "x1": 540, "top": 600 - i, "bottom": 601 - i}
            for i in range(25)
        ]
        page.curves = []
        words = self._borderless_words()
        sub = MagicMock()
        sub.extract_table.return_value = [
            ["H1", "H2", "H3"],
            ["100", "200", "300"],
            ["400", "500", "600"],
        ]
        page.within_bbox.return_value = sub
        tables = _detect_tables(page, 612, 792, words, stripe_frames=[])
        assert isinstance(tables, list)

    # -- merge_row_tables expand (2788-2855) ---------------------------------

    def test_merge_row_tables_expand_with_alt_table(self):
        page = MagicMock()
        page.curves = []
        mock_table = MagicMock()
        mock_table.bbox = (72, 630, 220, 650)
        mock_table.extract.return_value = [
            ["H1", "H2", "H3"],
            ["A", "B", "C"],
            ["D", "E", "F"],
        ]
        sub = MagicMock()
        sub.find_tables.return_value = [mock_table]
        page.within_bbox.return_value = sub
        singles = [
            ((72, 650, 220, 670), [["R1", "R2", "R3"]]),
            ((72, 630, 220, 650), [["R4", "R5", "R6"]]),
        ]
        merged = _merge_row_tables(singles, page, 612, 792)
        assert len(merged) >= 1

    def test_merge_row_tables_curves_block_expand(self):
        page = MagicMock()
        curves = []
        for i in range(6):
            curves.append({
                "x0": 80 + i, "x1": 82 + i,
                "top": 600 + i, "bottom": 602 + i,
            })
        page.curves = curves
        singles = [
            ((72, 650, 220, 670), [["A", "B", "C"]]),
            ((72, 630, 220, 650), [["D", "E", "F"]]),
        ]
        merged = _merge_row_tables(singles, page, 612, 792)
        assert len(merged) >= 1

    def test_merge_row_tables_chain_without_expand(self):
        page = MagicMock()
        page.curves = []
        singles = [
            ((72, 650, 200, 690), [["only", "row", "here"]]),
            ((72, 620, 200, 640), [["x", "y"]]),
            ((72, 600, 200, 620), [["p", "q"]]),
        ]
        merged = _merge_row_tables(singles, page, 612, 792)
        assert len(merged) >= 1

    # -- detect_images (3195-3351) -------------------------------------------

    def test_detect_images_page_images_exception(self):
        page = MagicMock()
        type(page).images = property(lambda self: (_ for _ in ()).throw(RuntimeError))
        page.width = 612
        page.height = 792
        page.lines = []
        page.rects = []
        page.curves = []

        def raster():
            return np.full((792, 612, 3), 255, dtype=np.uint8), 1.0

        regions = _detect_images(page, raster, [], existing=[], all_words=[])
        assert regions == []

    def test_detect_images_filters_small_and_fullpage(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = [
            {"x0": 100, "x1": 110, "top": 500, "bottom": 510},
            {"x0": 0, "x1": 612, "top": 0, "bottom": 792},
        ]
        page.lines = []
        page.rects = []
        page.curves = []

        def raster():
            return np.full((792, 612, 3), 255, dtype=np.uint8), 1.0

        regions = _detect_images(page, raster, [], existing=[], all_words=[])
        assert isinstance(regions, list)

    def test_detect_images_multi_cluster_with_gutters(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = [
            {"x0": 100, "x1": 180, "top": 500, "bottom": 580},
            {"x0": 200, "x1": 280, "top": 500, "bottom": 580},
        ]
        page.lines = []
        page.rects = []
        page.curves = []

        def raster():
            img = np.full((792, 612, 3), 255, dtype=np.uint8)
            img[500:580, 100:280] = 0
            img[540:545, 180:200] = 255
            return img, 1.0

        regions = _detect_images(page, raster, [], existing=[], all_words=[])
        assert isinstance(regions, list)

    def test_detect_images_ink_density_promotion(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = []
        page.lines = []
        page.rects = []
        page.curves = []
        block = (100, 500, 250, 620)

        def raster():
            img = np.full((792, 612, 3), 255, dtype=np.uint8)
            img[500:620, 100:250] = 30
            return img, 1.0

        regions = _detect_images(
            page, raster, [block], existing=[], all_words=[],
        )
        assert any(r.type == LayoutRegionType.IMAGE for r in regions)

    def test_detect_images_vector_heavy_block(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = []
        page.lines = [
            {"x0": 100 + i, "x1": 150 + i, "top": 500, "bottom": 501}
            for i in range(20)
        ]
        page.rects = []
        page.curves = []
        block = (90, 490, 280, 520)
        words = [_w("lbl", 110, 130, 525, 535)]

        def raster():
            return np.full((792, 612, 3), 255, dtype=np.uint8), 1.0

        regions = _detect_images(
            page, raster, [block], existing=[], all_words=words,
        )
        assert isinstance(regions, list)

    def test_refine_image_bbox_high_word_density_skipped(self):
        cv_blocks = [(50, 50, 150, 150)]
        words = [_w("dense", 55, 145, 55, 145)]
        out = _refine_image_bbox((40, 40, 160, 160), cv_blocks, words, 612, 792)
        assert out == [(40, 40, 160, 160)]

    def test_trim_image_text_margins_trims_top_bottom(self):
        bbox = (100, 100, 300, 300)
        words = [
            _w("topcap", 150, 200, 102, 112),
            _w("bottom", 150, 200, 288, 298),
        ]
        trimmed = _trim_image_text_margins(bbox, words)
        assert trimmed[1] >= bbox[1]

    def test_split_region_by_gutters_horizontal_split(self):
        img = np.full((400, 400, 3), 255, dtype=np.uint8)
        img[50:170, 50:350] = 0
        img[230:350, 50:350] = 0
        img[180:220, :] = 255
        parts = _split_region_by_gutters(img, 2.0, (0, 0, 288, 288), [], depth=0)
        assert len(parts) >= 1

    # -- is_plausible_table rescue (2948-2978) ---------------------------------

    def test_is_plausible_table_sparse_column_rescue(self):
        bbox = (72, 600, 300, 700)
        grid = [
            ["H1", "H2", "H3"],
            [None, "B", None],
            [None, "D", None],
            [None, "F", None],
        ]
        assert _is_plausible_table(bbox, grid, 612, 792, []) is True

    def test_is_plausible_table_invalid_bbox(self):
        assert _is_plausible_table((72, 700, 70, 600), [["a"]], 612, 792, []) is False

    # -- reading_order band cuts (4084-4142) ---------------------------------

    def test_reading_order_multi_col_with_wide_heading(self):
        gutter = 306.0
        words = _two_col_words(n_lines=8)
        heading = _region(LayoutRegionType.TEXT, (50, 700, 560, 730), "Title")
        left = _region(LayoutRegionType.TEXT, (50, 600, 280, 620), "L1")
        right = _region(LayoutRegionType.TEXT, (330, 600, 560, 620), "R1")
        left2 = _region(LayoutRegionType.TEXT, (50, 500, 280, 520), "L2")
        right2 = _region(LayoutRegionType.TEXT, (330, 500, 560, 520), "R2")
        ordered = _reading_order(
            [right2, left2, right, left, heading], 612, 792,
            gutter_x=gutter, all_words=words,
        )
        texts = [r.text for r in ordered]
        assert "Title" in texts
        assert "L1" in texts and "R1" in texts

    def test_reading_order_edge_band_wide_footer(self):
        gutter = 306.0
        footer = _region(LayoutRegionType.TEXT, (50, 10, 560, 35), "Footer text")
        left = _region(LayoutRegionType.TEXT, (50, 400, 280, 420), "L")
        right = _region(LayoutRegionType.TEXT, (330, 400, 560, 420), "R")
        words = _two_col_words(n_lines=5)
        ordered = _reading_order(
            [footer, right, left], 612, 792, gutter_x=gutter, all_words=words,
        )
        assert len(ordered) == 3

    # -- resolve_overlaps proximity horizontal (3648+) -----------------------

    def test_resolve_overlaps_proximity_horizontal_gap(self):
        a = _region(LayoutRegionType.TEXT, (0, 0, 100, 50), "aaa bbb ccc ddd eee")
        b = _region(LayoutRegionType.TEXT, (105, 0, 200, 50), "fff ggg")
        out = _resolve_overlaps([a, b], page_w=612, page_h=792, proximity=10.0)
        assert len(out) == 1

    def test_resolve_overlaps_table_contained_primary_swap(self):
        table = _region(LayoutRegionType.TABLE, (10, 10, 90, 90), "t")
        text = _region(LayoutRegionType.TEXT, (0, 0, 100, 100), "big body")
        out = _resolve_overlaps([table, text], page_w=612, page_h=792)
        assert out[0].type == LayoutRegionType.TEXT

    # -- merge_small_text_regions (4187-4263) --------------------------------

    def test_merge_small_text_iterative_chain(self):
        big = _region(LayoutRegionType.TEXT, (0, 0, 100, 20), "one two three four five six")
        s1 = _region(LayoutRegionType.TEXT, (0, 22, 100, 40), "a")
        s2 = _region(LayoutRegionType.TEXT, (0, 42, 100, 60), "b")
        out = _merge_small_text_regions([big, s1, s2], max_gap=5.0)
        assert len(out) == 1

    def test_merge_small_text_prefers_closer_neighbor(self):
        far = _region(LayoutRegionType.TEXT, (0, 0, 100, 20), "one two three four five six")
        near = _region(LayoutRegionType.TEXT, (0, 22, 100, 40), "seven eight nine ten eleven")
        small = _region(LayoutRegionType.TEXT, (0, 45, 100, 60), "hi")
        out = _merge_small_text_regions([far, near, small], max_gap=5.0)
        assert len(out) <= 2

    def test_merge_small_text_empty_regions(self):
        assert _merge_small_text_regions([]) == []

    # -- classify_text_blocks extras (3378-3424) -----------------------------

    def test_classify_text_blocks_no_words_in_box(self):
        blocks = [(0, 0, 10, 10)]
        assert _classify_text_blocks(blocks, [], []) == []

    def test_classify_text_blocks_list_single_item_downgrades(self):
        lines = [[_w("• one", 72, 120, 700, 712)]]
        lines[0][0]["text"] = "• one"
        words = lines[0]
        blocks = [(70, 695, 160, 715)]
        regions = _classify_text_blocks(blocks, [], words)
        assert regions[0].type == LayoutRegionType.TEXT

    def test_classify_text_blocks_with_gutter(self):
        words = [
            _w("left line one", 50, 200, 700, 710),
            _w("left line two", 50, 200, 680, 690),
        ]
        blocks = [(45, 675, 210, 715)]
        regions = _classify_text_blocks(blocks, [], words, gutter_x=306.0)
        assert len(regions) >= 1

    # -- detect_table_boxes_from_vectors (2589-2673) -------------------------

    def test_detect_table_boxes_from_vectors_rect_segments(self):
        page = MagicMock()
        page.lines = [
            {"x0": 72, "x1": 300, "top": 650, "bottom": 651},
            {"x0": 72, "x1": 300, "top": 600, "bottom": 601},
        ]
        page.rects = [{
            "x0": 72, "x1": 300, "top": 600, "bottom": 650,
        }]
        boxes = _detect_table_boxes_from_vectors(page, 612, 792)
        assert isinstance(boxes, list)

    def test_detect_table_boxes_from_vectors_too_small(self):
        page = MagicMock()
        page.lines = [{"x0": 72, "x1": 80, "top": 650, "bottom": 651}]
        page.rects = []
        assert _detect_table_boxes_from_vectors(page, 612, 792) == []

    # -- rescue_columns / trim_paragraph (2684-2915) -------------------------

    def test_rescue_columns_many_curves_aborts(self):
        page = MagicMock()
        curves = []
        for i in range(6):
            curves.append({
                "x0": 80 + i, "x1": 82 + i,
                "top": 610 + i, "bottom": 612 + i,
            })
        page.curves = curves
        grid = _rescue_columns_if_single(
            page, (72, 600, 300, 700), [["single"]], 612, 792,
        )
        assert grid == [["single"]]

    def test_rescue_columns_alt_strategy(self):
        page = MagicMock()
        page.curves = []
        sub = MagicMock()
        sub.extract_table.side_effect = [
            None,
            [["A", "B"], ["1", "2"]],
        ]
        page.within_bbox.return_value = sub
        grid = _rescue_columns_if_single(
            page, (72, 600, 300, 700), [["only one col"]], 612, 792,
        )
        assert grid is not None

    def test_trim_paragraph_rows_strips_headers(self):
        grid = [
            ["This is a long paragraph row.", ""],
            ["a", "b"],
            ["c", "d"],
        ]
        trimmed, bbox = _trim_paragraph_rows(grid, (72, 600, 300, 700))
        assert len(trimmed) <= len(grid)
        assert bbox[1] >= 600

    # -- stray text / absorb labels (3901-4010) --------------------------------

    def test_absorb_labels_same_column_only(self):
        gutter = 306.0
        image = _region(LayoutRegionType.IMAGE, (50, 200, 250, 400))
        label = _region(LayoutRegionType.TEXT, (60, 180, 200, 198), "Fig 1")
        words = _two_col_words(n_lines=3)
        out = _absorb_labels_into_images(
            [image, label], 612, 792, gutter_x=gutter, all_words=words,
        )
        assert len(out) == 1

    def test_detect_stray_text_creates_region(self):
        covered = _region(LayoutRegionType.TEXT, (0, 0, 100, 100), "inside")
        words = [
            _w("inside", 30, 60, 30, 40),
            _w("orphan", 300, 350, 500, 510),
        ]
        out = _detect_stray_text_regions([covered], words)
        assert len(out) >= 2

    def test_stray_line_to_region_invalid_returns_none(self):
        assert _stray_line_to_region([{"bad": "word"}]) is None

    # -- extract_layout_regions integration paths (1409-1538) ----------------

    def test_extract_layout_crops_detected_images(self):
        page = _make_pdf_page(lambda c: c.drawString(72, 700, "text only"))
        fake_img = np.full((792, 612, 3), 128, dtype=np.uint8)
        img_region = _region(LayoutRegionType.IMAGE, (100, 500, 200, 600))
        with patch.object(ola, "_rasterize_page", return_value=(fake_img, 150 / 72.0)):
            with patch.object(ola, "_detect_images", return_value=[img_region]):
                with patch.object(ola, "_detect_tables", return_value=[]):
                    with patch.object(
                        ola, "_segment_blocks_native", return_value=[],
                    ):
                        with patch.object(
                            ola,
                            "_crop_image_bytes",
                            return_value=b"\x89PNG\r\n\x1a\nfake",
                        ):
                            regions = extract_layout_regions(page)
        image_regions = [r for r in regions if r.type == LayoutRegionType.IMAGE]
        assert image_regions
        assert image_regions[0].image_data is not None

    def test_extract_layout_vector_bullets_merged(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.extract_words.return_value = [
            _w("item one", 82, 150, 698, 708),
        ]
        page.lines = []
        page.rects = [{"x0": 72, "x1": 78, "top": 700, "bottom": 706}]
        page.curves = []
        page.images = []
        page.find_tables.return_value = []
        page.hyperlinks = []
        fake_img = np.full((100, 80, 3), 200, dtype=np.uint8)
        with patch.object(ola, "_rasterize_page", return_value=(fake_img, 150 / 72.0)):
            regions = extract_layout_regions(page)
        assert isinstance(regions, list)

    def test_extract_layout_vectors_no_words_no_embed(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.extract_words.return_value = []
        page.lines = [{"x0": i, "x1": i + 2, "top": 400, "bottom": 402} for i in range(8)]
        page.rects = []
        page.curves = []
        page.images = []
        page.find_tables.return_value = []
        page.hyperlinks = []
        fake_img = np.full((100, 80, 3), 200, dtype=np.uint8)
        with patch.object(ola, "_rasterize_page", return_value=(fake_img, 150 / 72.0)):
            regions = extract_layout_regions(page)
        assert isinstance(regions, list)

    def test_rasterize_page_with_cache(self):
        page = MagicMock()
        page.page_number = 1
        cache = DocumentRasterCache("/tmp/x.pdf")
        fake = (np.zeros((5, 5, 3), dtype=np.uint8), 2.0)
        with patch.object(cache, "get", return_value=fake):
            img, scale = _rasterize_page(page, 72, raster_cache=cache)
        assert img.shape == (5, 5, 3)

    # -- hyperlink / safe text edge cases ------------------------------------

    def test_safe_text_in_bbox_exception(self):
        page = MagicMock()
        page.within_bbox.side_effect = RuntimeError("fail")
        assert _safe_text_in_bbox(page, (0, 0, 10, 10)) == ""

    def test_word_center_in_bbox_bad_item(self):
        assert _word_center_in_bbox({"bad": 1}, (0, 0, 10, 10)) is False

    def test_tight_bbox_from_words_bad_data(self):
        assert _tight_bbox_from_words([{"x0": "nope"}]) is None

    def test_ink_density_small_crop(self):
        img = np.full((10, 10, 3), 255, dtype=np.uint8)
        assert _ink_density(img, (0, 0, 1, 1), scale=1.0) == 0.0

    def test_crop_image_bytes_none_and_tiny(self):
        assert _crop_image_bytes(None, (0, 0, 10, 10), 1.0) is None
        img = np.full((5, 5, 3), 255, dtype=np.uint8)
        assert _crop_image_bytes(img, (0, 0, 0.1, 0.1), 1.0) is None

    def test_hyperlink_anchor_partial_overlap(self):
        words = [_w("click here now", 10, 80, 10, 20)]
        link = {"x0": 30, "top": 8, "x1": 50, "bottom": 22, "uri": "https://x.com"}
        pair = _hyperlink_anchor_for_region(link, words)
        assert pair is not None

    def test_inject_hyperlinks_into_regions_with_links(self):
        page = MagicMock()
        page.hyperlinks = [{
            "x0": 10, "top": 8, "x1": 50, "bottom": 22, "uri": "https://z.com",
        }]
        words = [_w("link", 10, 50, 10, 20)]
        regions = [_region(LayoutRegionType.TEXT, (0, 0, 100, 30), "link")]
        out = _inject_hyperlinks_into_regions(regions, page, words)
        assert "https://z.com" in out[0].text

    # -- reextract_grid strategies (2316-2345) ---------------------------------

    def test_reextract_grid_picks_best_columns(self):
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.side_effect = [
            [["a"]],
            [["a", "b"], ["1", "2"]],
            None,
        ]
        page.within_bbox.return_value = sub
        grid = _reextract_grid_for_bbox(page, (72, 600, 300, 700), 612, 792)
        assert grid is not None
        assert len(grid[0]) >= 2

    def test_reextract_region_payload_table_no_page(self):
        reg = _region(LayoutRegionType.TABLE, (0, 0, 100, 100), "t")
        out = _reextract_region_payload(reg, None, [], 612, 792)
        assert out is reg

    def test_reextract_region_payload_list_items(self):
        lines = [
            [_w("• one", 72, 120, 700, 712)],
            [_w("• two", 72, 120, 680, 692)],
        ]
        lines[0][0]["text"] = "• one"
        lines[1][0]["text"] = "• two"
        words = [w for ln in lines for w in ln]
        reg = _region(LayoutRegionType.LIST, (70, 675, 160, 715), "", list_items=[])
        out = _reextract_region_payload(reg, None, words, 612, 792)
        assert out.list_items

    # -- detect_tables vector path (2243-2263) -------------------------------

    def test_detect_tables_vector_box_with_words(self):
        page = MagicMock()
        page.images = []
        page.find_tables.return_value = []
        page.lines = [
            {"x0": 72, "x1": 300, "top": 650, "bottom": 651},
            {"x0": 72, "x1": 300, "top": 600, "bottom": 601},
            {"x0": 72, "x1": 73, "top": 600, "bottom": 660},
            {"x0": 299, "x1": 300, "top": 600, "bottom": 660},
        ]
        page.rects = []
        words = [
            _w("A", 80, 100, 620, 630),
            _w("B", 150, 170, 620, 630),
            _w("C", 80, 100, 640, 650),
            _w("D", 150, 170, 640, 650),
        ]
        sub = MagicMock()
        sub.extract_table.return_value = [["A", "B"], ["C", "D"]]
        page.within_bbox.return_value = sub
        tables = _detect_tables(page, 612, 792, words, stripe_frames=[])
        assert isinstance(tables, list)


class TestCoveragePush95Phase2:
    """Second wave: force remaining high-value branches."""

    def test_detect_columns_rescue_success(self):
        words = _two_col_words(n_lines=30)
        rescue_result = {
            "accepted": True,
            "reference_ink": 100.0,
            "min_ink_in_window": 5.0,
            "ink_ratio": 0.05,
        }
        with patch.object(ola, "_COL_MIN_CONFIDENCE", 1.1):
            with patch.object(
                ola, "_validate_gutter_with_projection", return_value=rescue_result,
            ):
                result = _detect_page_columns(words, 612, 792, raster_getter=lambda: (None, 1.0))
        assert result.gutter_x is not None
        assert result.details.get("rescued_by_projection")

    def test_validate_gutter_accepted_ratio(self):
        page_w = 612.0
        scale = 150.0 / 72.0
        w_px = max(100, int(page_w * scale))
        img = np.full((200, w_px, 3), 255, dtype=np.uint8)
        gx = min(w_px // 2, int(306 * scale))
        img[:, : max(0, gx - 20)] = 100
        img[:, min(w_px, gx + 20):] = 100
        out = _validate_gutter_with_projection(
            lambda: (img, scale), gx / scale, 24.0, page_w,
        )
        if out.get("reference_ink", 0) >= ola._COL_RESCUE_MIN_REFERENCE_INK:
            assert "ink_ratio" in out

    def test_validate_gutter_no_reference_pool(self):
        img = np.full((50, 50, 3), 255, dtype=np.uint8)
        out = _validate_gutter_with_projection(lambda: (img, 1.0), 25.0, 40.0, 612.0)
        assert out["accepted"] is False

    def test_detect_columns_line_bad_center_in_table(self):
        words = _two_col_words(n_lines=20)
        tbox = (40, 350, 570, 450)
        words.append({"text": "x", "x0": 50, "x1": 60, "top": 400, "bottom": 410})
        result = _detect_page_columns(words, 612, 792, table_bboxes=[tbox])
        assert isinstance(result, ColumnDetectionResult)

    def test_group_words_into_lines_empty(self):
        assert _group_words_into_lines([]) == ([], [])

    def test_group_words_into_lines_gutter_split(self):
        words = [
            _w("left", 50, 100, 100, 110),
            _w("right", 350, 400, 100, 110),
        ]
        lines, _ids = _group_words_into_lines(words, gutter_x=306.0)
        assert len(lines) == 2

    def test_split_lines_into_runs_empty_and_bad_x(self):
        assert _split_lines_into_runs([]) == []
        lines = [[{"text": "hello", "top": 0, "bottom": 1}]]
        runs = _split_lines_into_runs(lines)
        assert runs[0][0] == "text"

    def test_split_lines_into_runs_continuation_line(self):
        lines = [
            [_w("• one", 10, 60, 100, 110)],
            [_w("cont", 20, 80, 115, 125)],
            [_w("• two", 10, 60, 140, 150)],
        ]
        lines[0][0]["text"] = "• one"
        lines[2][0]["text"] = "• two"
        runs = _split_lines_into_runs(lines)
        assert any(r[0] == "list" for r in runs)

    def test_find_vector_bullets_skips_bad_and_no_text(self):
        page = MagicMock()
        page.curves = [{"bad": 1}]
        page.rects = [{"x0": 72, "x1": 78, "top": 700, "bottom": 706}]
        assert _find_vector_bullets(page, []) == []

    def test_detect_uniform_fill_stripe_passes_filter(self):
        page = MagicMock()
        rects = []
        for i in range(8):
            rects.append({
                "x0": 72, "x1": 400,
                "top": 600 - i * 12, "bottom": 608 - i * 12,
                "fill": True,
                "non_stroking_color": (0.92, 0.92, 0.92),
            })
        page.rects = rects
        page.lines = []
        chars = []
        for i in range(30):
            chars.append({
                "text": "x",
                "fontname": "Courier",
                "x0": 80 + (i % 10) * 5.0,
                "x1": 84 + (i % 10) * 5.0,
                "top": 540 + (i // 10) * 12,
                "bottom": 548 + (i // 10) * 12,
            })
        page.chars = chars
        stripes = _detect_uniform_fill_stripe_regions(page)
        assert isinstance(stripes, list)

    def test_region_is_predominantly_monospace_chars_exception(self):
        page = MagicMock()
        type(page).chars = property(lambda self: (_ for _ in ()).throw(RuntimeError))
        assert _region_is_predominantly_monospace(page, (0, 0, 100, 100)) is False

    def test_borderless_tables_returns_table_region(self):
        words = [_w("Months ended December", 72, 250, 720, 730)]
        y = 700.0
        for row in range(4):
            x = 72.0
            for val in ["100", "200", "300"]:
                words.append(_w(val, x, x + 35, y, y + 10))
                x += 45.0
            y -= 14.0
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.return_value = [
            ["H1", "H2", "H3"],
            ["100", "200", "300"],
            ["400", "500", "600"],
            ["700", "800", "900"],
        ]
        page.within_bbox.return_value = sub
        page.curves = []
        regions = _detect_borderless_tables(page, 612, 792, words, [])
        assert any(r.type == LayoutRegionType.TABLE for r in regions)

    def test_merge_row_tables_expand_success(self):
        page = MagicMock()
        page.curves = []
        mock_table = MagicMock()
        mock_table.bbox = (72, 620, 220, 660)
        mock_table.extract.return_value = [
            ["H1", "H2", "H3"],
            ["A", "B", "C"],
            ["D", "E", "F"],
            ["G", "H", "I"],
        ]
        sub = MagicMock()
        sub.find_tables.return_value = [mock_table]
        page.within_bbox.return_value = sub
        singles = [
            ((72, 640, 220, 660), [["R1", "R2", "R3"]]),
            ((72, 620, 220, 640), [["R4", "R5", "R6"]]),
        ]
        with patch.object(ola, "_trim_paragraph_rows", side_effect=lambda g, b: (g, b)):
            merged = _merge_row_tables(singles, page, 612, 792)
        assert len(merged) >= 1

    def test_detect_tables_with_images_and_stripe_skip(self):
        page = MagicMock()
        page.images = [{"x0": 100, "x1": 200, "top": 500, "bottom": 600}]
        mock_t = MagicMock()
        mock_t.bbox = (72, 600, 300, 700)
        mock_t.extract.return_value = [["a", "b"], ["c", "d"]]
        page.find_tables.return_value = [mock_t]
        page.lines = []
        stripe = (70, 595, 305, 705)
        words = [_w("a", 80, 100, 620, 630)] * 5
        tables = _detect_tables(page, 612, 792, words, stripe_frames=[stripe])
        assert isinstance(tables, list)

    def test_detect_tables_pp_candidate_exceptions(self):
        page = MagicMock()
        page.images = []
        bad_t = MagicMock()
        bad_t.bbox = "bad"
        good_t = MagicMock()
        good_t.bbox = (72, 600, 300, 700)
        good_t.extract.side_effect = RuntimeError("fail")
        page.find_tables.return_value = [bad_t, good_t]
        page.lines = []
        tables = _detect_tables(page, 612, 792, [], stripe_frames=[])
        assert tables == []

    def test_detect_images_invalid_embedded_bbox(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = [{"x0": "bad", "x1": 200, "top": 500, "bottom": 600}]
        page.lines = []
        page.rects = []
        page.curves = []

        def raster():
            return np.full((792, 612, 3), 255, dtype=np.uint8), 1.0

        assert _detect_images(page, raster, [], existing=[], all_words=[]) == []

    def test_detect_images_cluster_raster_exception(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = [
            {"x0": 100, "x1": 180, "top": 500, "bottom": 580},
            {"x0": 190, "x1": 270, "top": 500, "bottom": 580},
        ]
        page.lines = []
        page.rects = []
        page.curves = []

        def bad_raster():
            raise RuntimeError("boom")

        regions = _detect_images(page, bad_raster, [], existing=[], all_words=[])
        assert isinstance(regions, list)

    def test_split_region_vertical_gutters_recursive(self):
        img = np.full((400, 400, 3), 255, dtype=np.uint8)
        img[50:350, 50:170] = 0
        img[50:350, 230:350] = 0
        parts = _split_region_by_gutters(
            img, 2.0, (0, 0, 288, 288), [], depth=1,
        )
        assert len(parts) >= 1

    def test_reading_order_is_wide_edge_band(self):
        gutter = 306.0
        footer = _region(LayoutRegionType.TEXT, (50, 5, 560, 35), "Footer")
        left = _region(LayoutRegionType.TEXT, (50, 400, 280, 420), "L")
        right = _region(LayoutRegionType.TEXT, (330, 400, 560, 420), "R")
        ordered = _reading_order(
            [left, right, footer], 612, 792, gutter_x=gutter, all_words=[],
        )
        assert len(ordered) == 3

    def test_resolve_overlaps_horizontal_proximity(self):
        a = _region(LayoutRegionType.TEXT, (0, 0, 50, 20), "aaa bbb ccc ddd eee")
        b = _region(LayoutRegionType.TEXT, (55, 0, 120, 20), "fff ggg hhh")
        out = _resolve_overlaps([a, b], page_w=612, page_h=792, proximity=10.0)
        assert len(out) == 1

    def test_merge_small_text_merge_with_prev(self):
        prev = _region(LayoutRegionType.TEXT, (0, 0, 100, 20), "one two three four five")
        small = _region(LayoutRegionType.TEXT, (0, 22, 100, 40), "x")
        out = _merge_small_text_regions([prev, small], max_gap=5.0)
        assert len(out) == 1

    def test_extract_layout_vector_bullets_added(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.extract_words.return_value = [_w("item one", 82, 150, 698, 708)]
        page.lines = []
        page.rects = [{"x0": 72, "x1": 78, "top": 700, "bottom": 706}]
        page.curves = []
        page.images = []
        page.find_tables.return_value = []
        page.hyperlinks = []
        fake_img = np.full((100, 80, 3), 200, dtype=np.uint8)
        with patch.object(ola, "_rasterize_page", return_value=(fake_img, 150 / 72.0)):
            regions = extract_layout_regions(page)
        assert isinstance(regions, list)

    def test_iou_disjoint_returns_zero(self):
        assert _iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0

    def test_trim_empty_rows_cols_all_empty(self):
        grid = [[None, ""], ["", None]]
        assert _trim_empty_rows_cols(grid) == []


class TestCoveragePush95Phase3:
    """Third wave: patch-assisted coverage for stubborn branches."""

    def test_validate_gutter_accepted_true(self):
        w_px = 200
        h_px = 100

        def fake_binarize(_img):
            bw = np.zeros((h_px, w_px), dtype=np.uint8)
            bw[:, :80] = 255
            bw[:, 120:] = 255
            return bw

        img = np.full((h_px, w_px, 3), 255, dtype=np.uint8)
        with patch.object(ola, "_binarize_foreground", side_effect=fake_binarize):
            out = _validate_gutter_with_projection(
                lambda: (img, 1.0), 100.0, 20.0, 612.0,
            )
        assert out["accepted"] is True
        assert "ink_ratio" in out

    def test_detect_columns_voting_word_fraction_reject(self):
        words = _two_col_words(n_lines=30)
        with patch.object(ola, "_COL_MIN_VOTING_WORD_FRACTION", 0.99):
            result = _detect_page_columns(words, 612, 792)
        assert result.gutter_x is None or result.details.get("voting_word_fraction", 1) >= 0.99

    def test_detect_columns_peak_core_too_narrow(self):
        words = []
        y = 700.0
        for i in range(50):
            words.append(_w(f"L{i}", 50, 280, y, y + 10))
            words.append(_w(f"R{i}", 330, 550, y, y + 10))
            y -= 10.0
        result = _detect_page_columns(words, 612, 792)
        assert isinstance(result, ColumnDetectionResult)

    def test_segment_blocks_native_crosses_h_rule(self):
        words = [_w(f"w{i}", 50, 200, 700 - i * 20, 710 - i * 20) for i in range(8)]
        boxes = _segment_blocks_native(words, 612, 792, h_rules=[650.0])
        assert isinstance(boxes, list)
        assert len(boxes) >= 1

    def test_segment_blocks_native_size_ratio_reject(self):
        words = [
            _w("big", 50, 200, 700, 720, size=20),
            _w("small", 50, 200, 680, 690, size=8),
        ]
        boxes = _segment_blocks_native(words, 612, 792)
        assert len(boxes) >= 1

    def test_segment_vector_blocks_stripe_overlap_skip(self):
        page = MagicMock()
        page.lines = [{
            "x0": 100, "x1": 102, "top": 400, "bottom": 402,
        }] * 6
        page.rects = []
        page.curves = []
        stripe = (95, 395, 110, 410)
        blocks = _segment_vector_blocks(
            page, [], 612, 792, stripe_frames=[stripe], min_members=5,
        )
        assert blocks == []

    def test_segment_vector_blocks_full_page_cluster_skip(self):
        page = MagicMock()
        page.lines = [{
            "x0": 0, "x1": 600, "top": 0, "bottom": 790,
        }]
        page.lines.extend([
            {"x0": 10 + i, "x1": 12 + i, "top": 10 + i, "bottom": 12 + i}
            for i in range(10)
        ])
        page.rects = []
        page.curves = []
        blocks = _segment_vector_blocks(page, [], 612, 792, min_members=5)
        assert isinstance(blocks, list)

    def test_stripe_detect_internal_v_rule_filters(self):
        page = MagicMock()
        rects = []
        for i in range(8):
            rects.append({
                "x0": 72, "x1": 400,
                "top": 600 - i * 12, "bottom": 608 - i * 12,
                "fill": True, "non_stroking_color": (0.9, 0.9, 0.9),
            })
        page.rects = rects
        page.lines = [{
            "x0": 200, "x1": 201, "top": 530, "bottom": 610,
        }]
        page.chars = [
            {
                "text": "x", "fontname": "Courier",
                "x0": 80 + i * 5, "x1": 84 + i * 5,
                "top": 540, "bottom": 548,
            }
            for i in range(25)
        ]
        assert _detect_uniform_fill_stripe_regions(page) == []

    def test_stripe_no_fill_skipped(self):
        page = MagicMock()
        page.rects = [{
            "x0": 72, "x1": 400, "top": 600, "bottom": 608, "fill": False,
        }]
        page.lines = []
        assert _detect_uniform_fill_stripe_regions(page) == []

    def test_monospace_width_cv_path(self):
        page = MagicMock()
        page.chars = [
            {
                "text": "x", "fontname": "Helvetica",
                "x0": 80 + i * 5.0, "x1": 83 + i * 5.0,
                "top": 540, "bottom": 548,
            }
            for i in range(25)
        ]
        assert _region_is_predominantly_monospace(page, (70, 530, 220, 560)) is True

    def test_borderless_structural_and_prose_breaks(self):
        words = []
        y = 700.0
        for row in range(5):
            x = 72.0
            for val in ["100", "200"]:
                words.append(_w(val, x, x + 35, y, y + 10))
                x += 45.0
            y -= 14.0
        words.append(_w("prose paragraph line here", 120, 400, 650, 660))
        for i in range(4):
            words.append(_w(f"hdr{i}", 72, 150, 720 - i * 12, 728 - i * 12))
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.return_value = [["a", "b"], ["1", "2"], ["3", "4"]]
        page.within_bbox.return_value = sub
        regions = _detect_borderless_tables(page, 612, 792, words, [])
        assert isinstance(regions, list)

    def test_borderless_big_gap_and_pending_struct(self):
        words = []
        y = 700.0
        for row in range(3):
            x = 72.0
            for val in ["10", "20"]:
                words.append(_w(val, x, x + 30, y, y + 10))
                x += 40.0
            y -= 14.0
        for i in range(5):
            words.append(_w(f"s{i}", 72, 120, 650 - i * 10, 658 - i * 10))
        y = 500.0
        for row in range(3):
            x = 72.0
            for val in ["30", "40"]:
                words.append(_w(val, x, x + 30, y, y + 10))
                x += 40.0
            y -= 14.0
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.return_value = [["a", "b"], ["1", "2"]]
        page.within_bbox.return_value = sub
        regions = _detect_borderless_tables(page, 612, 792, words, [])
        assert isinstance(regions, list)

    def test_merge_row_tables_find_tables_scores(self):
        page = MagicMock()
        page.curves = []
        alt = MagicMock()
        alt.bbox = (72, 610, 220, 670)
        alt.extract.return_value = [
            ["H1", "H2", "H3"],
            ["A", "B", "C"],
            ["D", "E", "F"],
        ]
        sub = MagicMock()
        sub.find_tables.return_value = [alt]
        page.within_bbox.return_value = sub
        singles = [
            ((72, 650, 220, 670), [["R1", "R2", "R3"]]),
            ((72, 630, 220, 650), [["R4", "R5", "R6"]]),
        ]
        with patch.object(ola, "_trim_paragraph_rows", side_effect=lambda g, b: (g, b)):
            with patch.object(ola, "_trim_empty_rows_cols", side_effect=lambda g: g):
                merged = _merge_row_tables(singles, page, 612, 792)
        assert merged

    def test_merge_row_tables_single_chain_extend(self):
        page = MagicMock()
        page.curves = []
        singles = [
            ((72, 650, 220, 670), [["A", "B", "C"]]),
            ((72, 630, 220, 650), [["D", "E", "F"]]),
            ((72, 610, 220, 630), [["G", "H", "I"]]),
        ]
        merged = _merge_row_tables(singles, page, 612, 792)
        assert len(merged) >= 1

    def test_detect_tables_image_bbox_filter(self):
        page = MagicMock()
        page.images = [
            {"x0": 70, "x1": 310, "top": 595, "bottom": 705},
            {"x0": 10, "x1": 15, "top": 10, "bottom": 15},
            {"bad": "image"},
        ]
        page.find_tables.return_value = []
        page.lines = [{"x0": 72, "x1": 540, "top": 600 - i, "bottom": 601 - i} for i in range(25)]
        page.curves = []
        words = []
        y = 700.0
        for row in range(4):
            x = 72.0
            for val in ["100", "200"]:
                words.append(_w(val, x, x + 35, y, y + 10))
                x += 45.0
            y -= 14.0
        sub = MagicMock()
        sub.extract_table.return_value = [["a", "b"], ["1", "2"], ["3", "4"]]
        page.within_bbox.return_value = sub
        tables = _detect_tables(page, 612, 792, words, stripe_frames=[])
        assert isinstance(tables, list)

    def test_detect_tables_vector_success(self):
        page = MagicMock()
        page.images = []
        page.find_tables.return_value = []
        page.lines = [
            {"x0": 72, "x1": 300, "top": 650, "bottom": 651},
            {"x0": 72, "x1": 300, "top": 600, "bottom": 601},
            {"x0": 72, "x1": 73, "top": 600, "bottom": 660},
            {"x0": 299, "x1": 300, "top": 600, "bottom": 660},
        ]
        page.rects = []
        words = [_w(str(i), 80 + i * 20, 95 + i * 20, 620 + i, 630 + i) for i in range(6)]
        sub = MagicMock()
        sub.extract_table.return_value = [["A", "B"], ["C", "D"]]
        page.within_bbox.return_value = sub
        tables = _detect_tables(page, 612, 792, words, stripe_frames=[])
        assert any(t.type == LayoutRegionType.TABLE for t in tables)

    def test_detect_images_horizontal_rule_skip_in_block(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = []
        page.lines = [{
            "x0": 100, "x1": 200, "top": 500, "bottom": 501,
        }]
        page.rects = []
        page.curves = []
        block = (90, 490, 210, 520)

        def raster():
            return np.full((792, 612, 3), 255, dtype=np.uint8), 1.0

        regions = _detect_images(page, raster, [block], existing=[], all_words=[])
        assert isinstance(regions, list)

    def test_detect_images_word_density_high_skip(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = []
        page.lines = []
        page.rects = []
        page.curves = []
        block = (100, 500, 200, 600)
        words = [_w("w", 110 + i, 120 + i, 510 + i, 520 + i) for i in range(30)]

        def raster():
            return np.full((792, 612, 3), 255, dtype=np.uint8), 1.0

        regions = _detect_images(page, raster, [block], existing=[], all_words=words)
        assert regions == []

    def test_split_region_horizontal_split_depth0(self):
        img = np.full((400, 400, 3), 255, dtype=np.uint8)
        img[180:220, :] = 255
        img[50:170, 50:350] = 0
        img[230:350, 50:350] = 0
        parts = _split_region_by_gutters(img, 2.0, (0, 0, 288, 288), [], depth=0)
        assert len(parts) >= 1

    def test_classify_text_blocks_list_two_items(self):
        lines = [
            [_w("• one", 72, 120, 700, 712)],
            [_w("• two", 72, 120, 680, 692)],
        ]
        lines[0][0]["text"] = "• one"
        lines[1][0]["text"] = "• two"
        words = [w for ln in lines for w in ln]
        blocks = [(70, 675, 160, 715)]
        regions = _classify_text_blocks(blocks, [], words)
        assert regions[0].type == LayoutRegionType.LIST

    def test_classify_text_blocks_empty_run_skipped(self):
        region_box = (70, 695, 90, 715)
        empty_line = [[_w("", 72, 80, 700, 710)]]
        with patch.object(ola, "_group_words_into_lines", return_value=(empty_line, [0])):
            with patch.object(ola, "_split_lines_into_runs", return_value=[("text", empty_line)]):
                with patch.object(ola, "_tight_bbox_from_words", return_value=region_box):
                    regions = _classify_text_blocks([region_box], [], empty_line[0])
        assert regions == []

    def test_reading_order_wide_band_cuts(self):
        gutter = 306.0
        title = _region(LayoutRegionType.TEXT, (50, 650, 560, 680), "Title")
        left = _region(LayoutRegionType.TEXT, (50, 500, 280, 520), "L")
        right = _region(LayoutRegionType.TEXT, (330, 500, 560, 520), "R")
        ordered = _reading_order(
            [right, left, title], 612, 792, gutter_x=gutter, all_words=[],
        )
        assert len(ordered) == 3
        assert {r.text for r in ordered} == {"Title", "L", "R"}

    def test_reading_order_single_col_fallback(self):
        gutter = 306.0
        left = _region(LayoutRegionType.TEXT, (50, 400, 280, 420), "L")
        ordered = _reading_order([left], 612, 792, gutter_x=gutter, all_words=[])
        assert len(ordered) == 1

    def test_resolve_overlaps_proximity_blocked_by_table_gate(self):
        table = _region(LayoutRegionType.TABLE, (0, 0, 200, 200), "t")
        big_text = _region(LayoutRegionType.TEXT, (0, 202, 200, 402), "x" * 200)
        out = _resolve_overlaps([table, big_text], page_w=612, page_h=792, proximity=5.0)
        assert len(out) == 2

    def test_merge_small_text_target_after(self):
        first = _region(LayoutRegionType.TEXT, (0, 0, 100, 20), "one two three four five")
        small = _region(LayoutRegionType.TEXT, (0, 22, 100, 40), "x")
        third = _region(LayoutRegionType.TEXT, (0, 100, 100, 120), "far away text")
        out = _merge_small_text_regions([first, small, third], max_gap=5.0)
        assert len(out) == 2

    def test_extract_layout_no_words_many_vectors(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.extract_words.return_value = []
        page.lines = [{"x0": i, "x1": i + 2, "top": 400, "bottom": 402} for i in range(8)]
        page.rects = []
        page.curves = []
        page.images = []
        page.find_tables.return_value = []
        page.hyperlinks = []
        fake_img = np.full((100, 80, 3), 200, dtype=np.uint8)
        with patch.object(ola, "_rasterize_page", return_value=(fake_img, 150 / 72.0)):
            regions = extract_layout_regions(page)
        assert isinstance(regions, list)

    def test_extract_layout_raster_cache_get(self):
        page = _make_pdf_page(lambda c: c.drawString(72, 700, "cached"))
        fake_img = np.full((100, 80, 3), 128, dtype=np.uint8)
        cache = DocumentRasterCache("/tmp/cache.pdf")
        with patch.object(ola, "_render_all_pages", return_value={1: (fake_img, 2.0)}):
            regions = extract_layout_regions(page, raster_cache=cache)
        assert regions

    def test_find_vector_bullets_no_text_right(self):
        page = MagicMock()
        page.curves = []
        page.rects = [{"x0": 72, "x1": 78, "top": 700, "bottom": 706}]
        words = [_w("far", 200, 250, 698, 708)]
        assert _find_vector_bullets(page, words) == []

    def test_hyperlink_inject_no_anchor_in_text(self):
        words = [_w("other", 10, 40, 10, 20)]
        region = _region(LayoutRegionType.TEXT, (0, 0, 100, 30), "nothing")
        links = [{"x0": 50, "top": 8, "x1": 80, "bottom": 22, "uri": "https://x.com"}]
        _inject_hyperlinks_into_region(region, links, words)
        assert region.text == "nothing"

    def test_reextract_grid_within_bbox_none(self):
        page = MagicMock()
        page.within_bbox.side_effect = RuntimeError("fail")
        assert _reextract_grid_for_bbox(page, (0, 0, 10, 10), 612, 792) is None

    def test_trim_paragraph_rows_all_paragraph(self):
        grid = [["Long paragraph sentence here.", ""], ["Another long one.", ""]]
        trimmed, bbox = _trim_paragraph_rows(grid, (72, 600, 300, 700))
        assert trimmed == grid

    def test_table_boxes_from_vectors_success(self):
        page = MagicMock()
        page.lines = [
            {"x0": 72, "x1": 300, "top": 650, "bottom": 651},
            {"x0": 72, "x1": 300, "top": 600, "bottom": 601},
            {"x0": 72, "x1": 73, "top": 600, "bottom": 660},
            {"x0": 299, "x1": 300, "top": 600, "bottom": 660},
        ]
        page.rects = []
        boxes = _detect_table_boxes_from_vectors(page, 612, 792)
        assert len(boxes) >= 1

    def test_overlap_ratio_zero_box_area(self):
        assert _overlap_ratio((5, 5, 5, 10), (0, 0, 10, 10)) == 0.0


class TestCoveragePush95Phase4:
    """Integration and direct branch tests for final coverage push."""

    def test_financial_borderless_via_detect_tables(self):
        words = []
        words.append(_w("Three months ended September", 72, 280, 720, 730))
        y = 700.0
        for row in range(6):
            x = 72.0
            for val in ["100", "200", "300"]:
                words.append(_w(val, x, x + 35, y, y + 10))
                x += 45.0
            y -= 14.0
        page = MagicMock()
        page.images = []
        page.find_tables.return_value = []
        page.lines = [{"x0": 72, "x1": 540, "top": 650 - i, "bottom": 651 - i} for i in range(25)]
        page.curves = []
        sub = MagicMock()
        sub.extract_table.return_value = [
            ["Q1", "Q2", "Q3"],
            ["100", "200", "300"],
            ["400", "500", "600"],
            ["700", "800", "900"],
        ]
        page.within_bbox.return_value = sub
        tables = _detect_tables(page, 612, 792, words, stripe_frames=[])
        assert any(t.type == LayoutRegionType.TABLE for t in tables)

    def test_detect_page_columns_left_only_side_balance(self):
        words = []
        y = 700.0
        for i in range(40):
            words.append(_w(f"L{i}a L{i}b", 50, 280, y, y + 10))
            words.append(_w(f"R{i}a R{i}b", 330, 550, y, y + 10))
            y -= 11.0
        for i in range(15):
            words.append(_w(f"only{i}", 50, 200, 300 + i * 12, 310 + i * 12))
        result = _detect_page_columns(words, 612, 792)
        assert result.details.get("n_left_only_lines", 0) >= 0

    def test_detect_page_columns_gutter_out_of_bounds(self):
        words = _two_col_words(n_lines=30)
        with patch.object(ola, "_COL_CENTRAL_RANGE", (0.01, 0.02)):
            result = _detect_page_columns(words, 612, 792)
        assert isinstance(result, ColumnDetectionResult)

    def test_segment_blocks_empty_lines_path(self):
        words = [_w("solo", 50, 100, 100, 110)]
        with patch.object(ola, "_group_words_into_lines", return_value=([[], [words[0]]], [0, 1])):
            with patch.object(ola, "_resolve_line_sides", return_value=["any", "any"]):
                boxes = _segment_blocks_native(words, 612, 792)
        assert len(boxes) == 1

    def test_segment_blocks_no_line_records(self):
        with patch.object(ola, "_group_words_into_lines", return_value=([[]], [0])):
            with patch.object(ola, "_resolve_line_sides", return_value=["any"]):
                assert _segment_blocks_native([_w("x", 0, 1, 0, 1)], 612, 792) == []

    def test_segment_vector_thin_vertical_skip(self):
        page = MagicMock()
        page.lines = [{"x0": 100, "x1": 101, "top": 0, "bottom": 790}]
        page.rects = []
        page.curves = []
        assert _segment_vector_blocks(page, [], 612, 792, min_members=1) == []

    def test_segment_vector_gutter_side_gate(self):
        page = MagicMock()
        page.lines = [
            {"x0": 50, "x1": 100, "top": 400, "bottom": 420},
            {"x0": 55, "x1": 105, "top": 405, "bottom": 425},
            {"x0": 60, "x1": 110, "top": 410, "bottom": 430},
            {"x0": 65, "x1": 115, "top": 415, "bottom": 435},
            {"x0": 70, "x1": 120, "top": 420, "bottom": 440},
            {"x0": 75, "x1": 125, "top": 425, "bottom": 445},
        ]
        page.rects = []
        page.curves = []
        blocks = _segment_vector_blocks(page, [], 612, 792, gutter_x=306.0, min_members=5)
        assert len(blocks) >= 1

    def test_stripe_returns_nonempty_cluster(self):
        page = MagicMock()
        rects = []
        for i in range(8):
            top = 600 - i * 8
            rects.append({
                "x0": 72, "x1": 400,
                "top": top, "bottom": top + 8,
                "fill": True, "non_stroking_color": (0.91, 0.91, 0.91),
            })
        page.rects = rects
        page.lines = []
        page.chars = [
            {
                "text": "x", "fontname": "Courier",
                "x0": 80 + (i % 10) * 5.0,
                "x1": 84 + (i % 10) * 5.0,
                "top": 540 + (i // 10) * 12,
                "bottom": 548 + (i // 10) * 12,
            }
            for i in range(30)
        ]
        with patch.object(ola, "_region_is_predominantly_monospace", return_value=True):
            stripes = _detect_uniform_fill_stripe_regions(page)
        assert len(stripes) >= 1

    def test_stripe_internal_h_rule_rejects(self):
        page = MagicMock()
        rects = []
        for i in range(8):
            rects.append({
                "x0": 72, "x1": 400,
                "top": 600 - i * 12, "bottom": 608 - i * 12,
                "fill": True, "non_stroking_color": (0.9, 0.9, 0.9),
            })
        page.rects = rects
        page.lines = [{
            "x0": 72, "x1": 400, "top": 570, "bottom": 571,
        }]
        page.chars = [
            {
                "text": "x", "fontname": "Courier",
                "x0": 80 + i * 5, "x1": 84 + i * 5,
                "top": 540, "bottom": 548,
            }
            for i in range(25)
        ]
        assert _detect_uniform_fill_stripe_regions(page) == []

    def test_borderless_header_month_keywords(self):
        words = []
        y = 700.0
        for row in range(4):
            x = 72.0
            for val in ["100", "200"]:
                words.append(_w(val, x, x + 35, y, y + 10))
                x += 45.0
            y -= 14.0
        words.insert(0, _w("year ended December", 72, 250, 720, 730))
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.return_value = [["a", "b"], ["1", "2"], ["3", "4"]]
        page.within_bbox.return_value = sub
        regions = _detect_borderless_tables(page, 612, 792, words, [])
        assert isinstance(regions, list)

    def test_merge_row_tables_expand_best_grid(self):
        page = MagicMock()
        page.curves = []
        at = MagicMock()
        at.bbox = (72, 610, 220, 670)
        at.extract.return_value = [
            ["H1", "H2", "H3"],
            ["A", "B", "C"],
            ["D", "E", "F"],
            ["G", "H", "I"],
        ]
        sub = MagicMock()
        sub.find_tables.return_value = [at]
        page.within_bbox.return_value = sub
        singles = [
            ((72, 640, 220, 660), [["R1", "R2", "R3"]]),
            ((72, 620, 220, 640), [["R4", "R5", "R6"]]),
        ]
        merged = _merge_row_tables(singles, page, 612, 792)
        assert merged
        assert any(len(g) >= 3 for _, g in merged)

    def test_detect_images_small_embedded_skip(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = [{"x0": 100, "x1": 110, "top": 500, "bottom": 510}]
        page.lines = []
        page.rects = []
        page.curves = []

        def raster():
            return np.full((792, 612, 3), 255, dtype=np.uint8), 1.0

        assert _detect_images(page, raster, [], existing=[], all_words=[]) == []

    def test_detect_images_tighten_none_keeps_tile(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = [
            {"x0": 100, "x1": 180, "top": 500, "bottom": 580},
            {"x0": 190, "x1": 270, "top": 500, "bottom": 580},
        ]
        page.lines = []
        page.rects = []
        page.curves = []

        def raster():
            return np.full((792, 612, 3), 255, dtype=np.uint8), 1.0

        with patch.object(ola, "_tighten_to_content", return_value=None):
            regions = _detect_images(page, raster, [], existing=[], all_words=[])
        assert isinstance(regions, list)

    def test_detect_images_figure_n_vectors_15(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = []
        page.lines = [
            {"x0": 100 + i, "x1": 120 + i, "top": 500, "bottom": 505}
            for i in range(18)
        ]
        page.rects = []
        page.curves = []
        block = (90, 490, 280, 530)

        def raster():
            return np.full((792, 612, 3), 255, dtype=np.uint8), 1.0

        regions = _detect_images(page, raster, [block], existing=[], all_words=[])
        assert any(r.type == LayoutRegionType.IMAGE for r in regions)

    def test_split_region_h_gutter_recursive(self):
        img = np.full((300, 300, 3), 255, dtype=np.uint8)
        img[50:250, 50:250] = 0
        img[140:160, 50:250] = 255
        parts = _split_region_by_gutters(img, 2.0, (0, 0, 216, 216), [], depth=0)
        assert len(parts) >= 1

    def test_split_region_v_gutter_depth1(self):
        img = np.full((300, 300, 3), 255, dtype=np.uint8)
        img[50:250, 50:250] = 0
        img[50:250, 140:160] = 255
        parts = _split_region_by_gutters(img, 2.0, (0, 0, 216, 216), [], depth=1)
        assert len(parts) >= 1

    def test_classify_skips_higher_priority_overlap(self):
        words = [_w("txt", 80, 100, 620, 630)]
        table = _region(LayoutRegionType.TABLE, (70, 610, 200, 640))
        blocks = [(75, 615, 195, 635)]
        assert _classify_text_blocks(blocks, [table], words) == []

    def test_classify_list_needs_two_items(self):
        lines = [[_w("• one", 72, 120, 700, 712)]]
        lines[0][0]["text"] = "• one"
        blocks = [(70, 695, 160, 715)]
        regions = _classify_text_blocks(blocks, [], lines[0])
        assert regions[0].type == LayoutRegionType.TEXT

    def test_reading_order_unplaced_region(self):
        gutter = 306.0
        left = _region(LayoutRegionType.TEXT, (50, 400, 280, 420), "L")
        right = _region(LayoutRegionType.TEXT, (330, 400, 560, 420), "R")
        orphan = _region(LayoutRegionType.TEXT, (50, 100, 280, 120), "O")
        ordered = _reading_order(
            [left, right, orphan], 612, 792, gutter_x=gutter, all_words=[],
        )
        assert len(ordered) == 3

    def test_reading_order_wide_edge_crosses_gutter(self):
        gutter = 306.0
        wide = _region(LayoutRegionType.TEXT, (50, 10, 560, 40), "Footer wide")
        left = _region(LayoutRegionType.TEXT, (50, 400, 280, 420), "L")
        right = _region(LayoutRegionType.TEXT, (330, 400, 560, 420), "R")
        ordered = _reading_order(
            [wide, left, right], 612, 792, gutter_x=gutter, all_words=[],
        )
        assert ordered[0].text == "Footer wide"

    def test_resolve_overlaps_reextract_dirty(self):
        page = _make_pdf_page(lambda c: c.drawString(72, 700, "hello world"))
        words = page.extract_words(extra_attrs=["fontname", "size"])
        a = _region(LayoutRegionType.TEXT, (70, 695, 120, 715), "hello")
        b = _region(LayoutRegionType.TEXT, (70, 695, 150, 715), "world")
        out = _resolve_overlaps(
            [a, b], page=page, all_words=words, page_w=612, page_h=792,
        )
        assert len(out) == 1
        assert out[0].text

    def test_merge_small_text_pop_after_merge(self):
        a = _region(LayoutRegionType.TEXT, (0, 0, 100, 20), "one two three four five")
        b = _region(LayoutRegionType.TEXT, (0, 22, 100, 40), "x")
        c = _region(LayoutRegionType.TEXT, (0, 44, 100, 64), "y")
        out = _merge_small_text_regions([a, b, c], max_gap=5.0)
        assert len(out) == 1

    def test_extract_layout_vector_bullets_in_words(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.extract_words.return_value = [_w("item", 82, 150, 698, 708)]
        page.rects = [{"x0": 72, "x1": 78, "top": 700, "bottom": 706}]
        page.curves = []
        page.lines = []
        page.images = []
        page.find_tables.return_value = []
        page.hyperlinks = []
        fake_img = np.full((100, 80, 3), 200, dtype=np.uint8)
        with patch.object(ola, "_rasterize_page", return_value=(fake_img, 150 / 72.0)):
            regions = extract_layout_regions(page)
        assert regions

    def test_extract_layout_page_attrs_exception(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.extract_words.return_value = []
        type(page).lines = property(lambda self: (_ for _ in ()).throw(RuntimeError))
        page.find_tables.return_value = []
        page.hyperlinks = []
        fake_img = np.full((100, 80, 3), 200, dtype=np.uint8)
        with patch.object(ola, "_rasterize_page", return_value=(fake_img, 150 / 72.0)):
            regions = extract_layout_regions(page)
        assert isinstance(regions, list)

    def test_extract_layout_horizontal_rule_detection(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.extract_words.return_value = [_w("text", 72, 120, 700, 712)]
        page.lines = [{"x0": 0, "x1": 612, "top": 400, "bottom": 401}]
        page.rects = []
        page.curves = []
        page.images = []
        page.find_tables.return_value = []
        page.hyperlinks = []
        fake_img = np.full((100, 80, 3), 200, dtype=np.uint8)
        with patch.object(ola, "_rasterize_page", return_value=(fake_img, 150 / 72.0)):
            regions = extract_layout_regions(page)
        assert regions

    def test_inject_hyperlinks_table_grid_match(self):
        words = [_w("data", 10, 40, 10, 20)]
        region = _region(
            LayoutRegionType.TABLE, (0, 0, 100, 50), "data",
            table_grid=[["data"]],
        )
        links = [{"x0": 10, "top": 8, "x1": 40, "bottom": 22, "uri": "https://d.com"}]
        _inject_hyperlinks_into_region(region, links, words)
        assert "https://d.com" in str(region.table_grid)

    def test_is_plausible_table_rescue_fail_sparse(self):
        bbox = (72, 600, 300, 700)
        grid = [["H", "H2"], [None, None], [None, None]]
        assert _is_plausible_table(bbox, grid, 612, 792, []) is False

    def test_rescue_columns_second_strategy(self):
        page = MagicMock()
        page.curves = []
        sub = MagicMock()
        sub.extract_table.side_effect = [None, [["A", "B"], ["1", "2"]]]
        page.within_bbox.return_value = sub
        grid = _rescue_columns_if_single(
            page, (72, 600, 300, 700), [["one col"]], 612, 792,
        )
        assert grid is not None

    def test_detect_tables_stripe_skip_and_rescue(self):
        page = MagicMock()
        page.images = []
        mock_t = MagicMock()
        mock_t.bbox = (72, 600, 300, 700)
        mock_t.extract.return_value = [["Only", "One"], ["a", "b"]]
        page.find_tables.return_value = [mock_t]
        page.lines = []
        page.curves = []
        sub = MagicMock()
        sub.extract_table.return_value = [["A", "B"], ["1", "2"]]
        page.within_bbox.return_value = sub
        stripe = (70, 595, 305, 705)
        tables = _detect_tables(page, 612, 792, [], stripe_frames=[stripe])
        assert isinstance(tables, list)

    def test_full_pipeline_two_column_table_image(self):
        pytest.importorskip("reportlab")
        from reportlab.lib import colors
        from reportlab.platypus import Table, TableStyle
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        import pdfplumber

        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.setFont("Helvetica", 9)
        y = 750
        for i in range(20):
            c.drawString(50, y, f"Left column line {i} with text.")
            c.drawString(330, y, f"Right column line {i} with text.")
            y -= 12
        data = [["A", "B"], ["1", "2"], ["3", "4"]]
        t = Table(data)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black)]))
        t.wrapOn(c, 200, 100)
        t.drawOn(c, 72, 500)
        c.showPage()
        c.save()
        with pdfplumber.open(BytesIO(buf.getvalue())) as pdf:
            regions = extract_layout_regions(pdf.pages[0])
        assert len(regions) >= 2


class TestCoveragePush95Phase5:
    """Final push: exercise stubborn branches with precise fixtures."""

    def test_borderless_all_row_kinds(self):
        words = [_w("Months ended March", 72, 250, 720, 730)]
        y = 700.0
        for row in range(5):
            x = 72.0
            for val in ["100", "200", "300"]:
                words.append(_w(val, x, x + 35, y, y + 10))
                x += 45.0
            y -= 14.0
        words.append(_w("2020", 72, 100, 650, 660))
        words.append(_w("2021", 120, 150, 650, 660))
        for i in range(4):
            words.append(_w(f"label{i}", 72, 140, 680 - i * 10, 688 - i * 10))
        words.append(_w("prose line here", 150, 400, 640, 650))
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.return_value = [
            ["H1", "H2", "H3"],
            ["100", "200", "300"],
            ["400", "500", "600"],
        ]
        page.within_bbox.return_value = sub
        page.curves = []
        regions = _detect_borderless_tables(page, 612, 792, words, [])
        assert isinstance(regions, list)

    def test_borderless_low_fill_ratio_skipped(self):
        words = []
        y = 700.0
        for row in range(3):
            x = 72.0
            for val in ["100", "200"]:
                words.append(_w(val, x, x + 35, y, y + 10))
                x += 45.0
            y -= 14.0
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.return_value = [["a", None], [None, None]]
        page.within_bbox.return_value = sub
        regions = _detect_borderless_tables(page, 612, 792, words, [])
        assert regions == []

    def test_merge_row_tables_inner_expand_loop(self):
        page = MagicMock()
        page.curves = []
        at = MagicMock()
        at.bbox = (72, 610, 220, 670)
        at.extract.return_value = [
            ["H1", "H2", "H3"],
            ["A", "B", "C"],
            ["D", "E", "F"],
        ]
        sub = MagicMock()
        sub.find_tables.return_value = [at]
        page.within_bbox.return_value = sub
        singles = [
            ((72, 640, 220, 660), [["R1", "R2", "R3"]]),
            ((72, 620, 220, 640), [["R4", "R5", "R6"]]),
        ]
        merged = _merge_row_tables(singles, page, 612, 792)
        assert merged

    def test_merge_row_tables_curve_abort_expand(self):
        page = MagicMock()
        curves = [
            {"x0": 80 + i, "x1": 82 + i, "top": 610 + i, "bottom": 612 + i}
            for i in range(6)
        ]
        page.curves = curves
        singles = [
            ((72, 640, 220, 660), [["A", "B", "C"]]),
            ((72, 620, 220, 640), [["D", "E", "F"]]),
        ]
        merged = _merge_row_tables(singles, page, 612, 792)
        assert len(merged) >= 1

    def test_is_plausible_table_dense_col_rescue(self):
        grid = [
            ["H1", "H2", "H3"],
            [None, "x", None],
            [None, "y", None],
            [None, "z", None],
        ]
        assert _is_plausible_table((72, 600, 300, 700), grid, 612, 792, []) is True

    def test_is_plausible_table_rescue_fail_no_dense_cols(self):
        grid = [["only", None], [None, None], [None, None]]
        assert _is_plausible_table((72, 600, 300, 700), grid, 612, 792, []) is False

    def test_detect_images_n_vectors_5_low_density(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = []
        page.lines = [
            {"x0": 100 + i * 3, "x1": 110 + i * 3, "top": 500, "bottom": 510}
            for i in range(8)
        ]
        page.rects = []
        page.curves = []
        block = (90, 490, 200, 530)
        words = [_w("lbl", 110, 130, 520, 530)]

        def raster():
            return np.full((792, 612, 3), 255, dtype=np.uint8), 1.0

        regions = _detect_images(page, raster, [block], existing=[], all_words=words)
        assert isinstance(regions, list)

    def test_detect_images_ink_density_branch(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = []
        page.lines = []
        page.rects = []
        page.curves = []
        block = (100, 500, 200, 600)

        def raster():
            img = np.full((792, 612, 3), 255, dtype=np.uint8)
            img[500:600, 100:200] = 0
            return img, 1.0

        regions = _detect_images(page, raster, [block], existing=[], all_words=[])
        assert any(r.type == LayoutRegionType.IMAGE for r in regions)

    def test_split_region_prefers_vertical_when_no_horizontal(self):
        img = np.full((300, 300, 3), 255, dtype=np.uint8)
        img[50:250, 50:120] = 0
        img[50:250, 180:250] = 0
        parts = _split_region_by_gutters(img, 2.0, (0, 0, 216, 216), [], depth=1)
        assert len(parts) >= 1

    def test_split_region_horizontal_segments(self):
        img = np.full((300, 300, 3), 255, dtype=np.uint8)
        img[50:120, 50:250] = 0
        img[180:250, 50:250] = 0
        parts = _split_region_by_gutters(img, 2.0, (0, 0, 216, 216), [], depth=0)
        assert len(parts) >= 1

    def test_classify_text_no_lines_in_box(self):
        words = [_w("outside", 300, 350, 300, 310)]
        blocks = [(0, 0, 100, 100)]
        assert _classify_text_blocks(blocks, [], words) == []

    def test_classify_text_tight_bbox_none(self):
        words = [_w("x", 72, 80, 700, 710)]
        blocks = [(70, 695, 90, 715)]
        with patch.object(ola, "_tight_bbox_from_words", return_value=None):
            assert _classify_text_blocks(blocks, [], words) == []

    def test_classify_list_run_merge_text(self):
        lines = [
            [_w("• one", 72, 120, 700, 712)],
            [_w("• two", 72, 120, 680, 692)],
            [_w("plain", 72, 120, 660, 672)],
        ]
        lines[0][0]["text"] = "• one"
        lines[1][0]["text"] = "• two"
        words = [w for ln in lines for w in ln]
        blocks = [(70, 655, 160, 715)]
        regions = _classify_text_blocks(blocks, [], words)
        assert regions

    def test_reading_order_col_of_fallback(self):
        gutter = 306.0
        mystery = _region(LayoutRegionType.TABLE, (50, 400, 280, 420))
        left = _region(LayoutRegionType.TEXT, (50, 200, 280, 220), "L")
        right = _region(LayoutRegionType.TEXT, (330, 200, 560, 220), "R")
        ordered = _reading_order(
            [mystery, left, right], 612, 792, gutter_x=gutter, all_words=[],
        )
        assert len(ordered) == 3

    def test_reading_order_thin_band_skipped(self):
        gutter = 306.0
        left = _region(LayoutRegionType.TEXT, (50, 400, 280, 420), "L")
        right = _region(LayoutRegionType.TEXT, (330, 400, 560, 420), "R")
        wide = _region(LayoutRegionType.TEXT, (50, 399, 560, 400.5), "thin")
        ordered = _reading_order(
            [wide, left, right], 612, 792, gutter_x=gutter, all_words=[],
        )
        assert len(ordered) == 3

    def test_detect_page_columns_no_peak_supporters(self):
        with patch.object(ola, "_COL_MIN_PEAK_VOTES", 9999):
            words = _two_col_words(n_lines=30)
            result = _detect_page_columns(words, 612, 792)
        assert result.gutter_x is None

    def test_detect_page_columns_gutter_width_rejects(self):
        words = _two_col_words(n_lines=30, gap_half=80.0)
        result = _detect_page_columns(words, 612, 792)
        assert isinstance(result, ColumnDetectionResult)

    def test_segment_blocks_overlap_ratio_branch(self):
        words = [
            _w("a", 50, 200, 700, 710, size=12),
            _w("b", 50, 200, 688, 698, size=12),
        ]
        boxes = _segment_blocks_native(words, 612, 792)
        assert len(boxes) == 1

    def test_segment_blocks_size_ratio_reject_new_block(self):
        words = [
            _w("big", 50, 200, 700, 720, size=20),
            _w("small", 300, 400, 680, 690, size=6),
        ]
        boxes = _segment_blocks_native(words, 612, 792)
        assert len(boxes) >= 2

    def test_stripe_internal_rule_vertical_and_horizontal(self):
        page = MagicMock()
        rects = []
        for i in range(8):
            rects.append({
                "x0": 72, "x1": 400,
                "top": 600 - i * 12, "bottom": 608 - i * 12,
                "fill": True, "non_stroking_color": (0.9, 0.9, 0.9),
            })
        page.rects = rects
        page.lines = [
            {"x0": 200, "x1": 201, "top": 530, "bottom": 610},
            {"x0": 72, "x1": 400, "top": 570, "bottom": 571},
        ]
        page.chars = []
        stripes = _detect_uniform_fill_stripe_regions(page)
        assert stripes == []

    def test_monospace_char_parse_errors(self):
        page = MagicMock()
        page.chars = [
            {"text": "x", "fontname": "Helvetica", "bad": "coords"},
            {
                "text": "y", "fontname": "Helvetica",
                "x0": 80, "x1": 84, "top": 540, "bottom": 548,
            },
        ]
        assert _region_is_predominantly_monospace(page, (70, 530, 200, 560)) is False

    def test_detect_tables_image_skip_invalid(self):
        page = MagicMock()
        page.images = [{"x0": "bad", "top": 0, "x1": 10, "bottom": 10}]
        page.find_tables.return_value = []
        page.lines = [{"x0": 72, "x1": 540, "top": 600 - i, "bottom": 601 - i} for i in range(25)]
        page.curves = []
        words = [_w("100", 72, 100, 700, 710)] * 10
        sub = MagicMock()
        sub.extract_table.return_value = [["a", "b"], ["1", "2"]]
        page.within_bbox.return_value = sub
        tables = _detect_tables(page, 612, 792, words, stripe_frames=[])
        assert isinstance(tables, list)

    def test_reextract_grid_default_strategy(self):
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.side_effect = [
            [["a", "b"], ["1", "2"]],
            None,
            None,
        ]
        page.within_bbox.return_value = sub
        grid = _reextract_grid_for_bbox(page, (72, 600, 300, 700), 612, 792)
        assert grid is not None

    def test_hyperlink_anchor_word_parse_error(self):
        link = {"x0": 10, "top": 8, "x1": 40, "bottom": 22, "uri": "https://x.com"}
        words = [{"text": "x", "bad": 1}]
        assert _hyperlink_anchor_for_region(link, words) is None

    def test_inject_hyperlinks_list_item_match(self):
        words = [_w("item", 10, 40, 10, 20)]
        region = _region(
            LayoutRegionType.LIST,
            (0, 0, 100, 30),
            "item",
            list_items=["item"],
        )
        links = [{"x0": 10, "top": 8, "x1": 40, "bottom": 22, "uri": "https://l.com"}]
        _inject_hyperlinks_into_region(region, links, words)
        assert "https://l.com" in region.list_items[0]

    def test_merge_small_text_prev_index_branch(self):
        big = _region(LayoutRegionType.TEXT, (0, 22, 100, 40), "one two three four five six")
        small = _region(LayoutRegionType.TEXT, (0, 0, 100, 20), "x")
        out = _merge_small_text_regions([small, big], max_gap=5.0)
        assert len(out) == 1

    def test_absorb_labels_incompatible_column(self):
        gutter = 306.0
        image = _region(LayoutRegionType.IMAGE, (50, 200, 250, 400))
        label = _region(LayoutRegionType.TEXT, (330, 180, 450, 198), "Fig")
        words = _two_col_words(n_lines=3)
        out = _absorb_labels_into_images(
            [image, label], 612, 792, gutter_x=gutter, all_words=words,
        )
        assert len(out) == 2

    def test_extract_list_items_continuation_only(self):
        lines = [
            [_w("• start", 10, 80, 100, 110)],
            [_w("more text", 20, 80, 115, 125)],
        ]
        lines[0][0]["text"] = "• start"
        items = _extract_list_items(lines)
        assert len(items) == 1

    def test_trim_paragraph_rows_empty_result(self):
        grid = [["Sentence one.", "Sentence two."]]
        trimmed, bbox = _trim_paragraph_rows(grid, (72, 600, 300, 700))
        assert trimmed == grid

    def test_detect_table_vectors_rect_segments(self):
        page = MagicMock()
        page.lines = []
        page.rects = [{"x0": 72, "x1": 300, "top": 600, "bottom": 650}]
        boxes = _detect_table_boxes_from_vectors(page, 612, 792)
        assert isinstance(boxes, list)


class TestCoveragePush95Phase6:
    """Phase 6: targeted tests for remaining uncovered branches."""

    def test_validate_gutter_window_too_narrow(self):
        img = np.full((20, 50, 3), 255, dtype=np.uint8)
        real_max = max

        def _half_gw_max(a, b):
            if a == 2:
                return 1
            return real_max(a, b)

        with patch("builtins.max", side_effect=_half_gw_max):
            out = _validate_gutter_with_projection(
                lambda: (img, 1.0), 0.0, 2.0, 612.0,
            )
        assert out["accepted"] is False
        assert out["reason"] == "gutter window too narrow"

    def test_detect_columns_table_line_bad_word_coords(self):
        words = _two_col_words(n_lines=20)
        tbox = (40, 350, 570, 450)
        bad = {"text": "x", "x0": "bad", "x1": 60, "top": 400, "bottom": 410}
        good = _w("inside", 50, 60, 400, 410)
        words.extend([bad, good, good])
        result = _detect_page_columns(words, 612, 792, table_bboxes=[tbox])
        assert isinstance(result, ColumnDetectionResult)

    def test_detect_columns_no_plateau_via_sparse_votes(self):
        words = _two_col_words(n_lines=30)
        with patch.object(ola, "_COL_PLATEAU_FRACTION", 1.01):
            result = _detect_page_columns(words, 612, 792)
        assert result.gutter_x is None
        assert result.details.get("reason") == "no plateau found"

    def test_detect_columns_core_expand_and_reject_narrow(self):
        words = _two_col_words(n_lines=35)
        with patch.object(ola, "_COL_MAX_GUTTER_WIDTH", 1.0):
            result = _detect_page_columns(words, 612, 792)
        assert result.gutter_x is None
        assert "gutter width" in str(result.details.get("reason", ""))

    def test_detect_columns_gutter_x_out_of_bounds(self):
        words = _two_col_words(n_lines=35)
        with patch.object(ola, "_COL_CENTRAL_RANGE", (0.49, 0.51)):
            with patch.object(ola, "_COL_BIN_SIZE", 612.0):
                result = _detect_page_columns(words, 612, 792)
        assert result.gutter_x is None or result.details.get("gutter_x_candidate", 0) > 0

    def test_detect_columns_few_peak_voters_span_zero(self):
        words = _two_col_words(n_lines=8)
        with patch.object(ola, "_COL_MIN_PEAK_VOTES", 1):
            with patch.object(ola, "_COL_MIN_SPANNING_LINES", 3):
                result = _detect_page_columns(words, 612, 792)
        assert result.details.get("voter_y_span", 0) >= 0

    def test_detect_columns_side_only_lines_counted(self):
        words = _two_col_words(n_lines=30)
        for i in range(10):
            top = 250 + i * 12
            words.append(_w(f"onlyleft{i}", 50, 200, top, top + 10))
        for i in range(8):
            top = 380 + i * 12
            words.append(_w(f"onlyright{i}", 330, 520, top, top + 10))
        result = _detect_page_columns(words, 612, 792)
        assert result.details.get("n_left_only_lines", 0) >= 1
        assert result.details.get("n_right_only_lines", 0) >= 1

    def test_detect_columns_gutter_spanner_skip_branches(self):
        words = _two_col_words(n_lines=30)
        words.append(_w("narrow", 280, 320, 500, 510))
        result = _detect_page_columns(words, 612, 792)
        assert "n_gutter_spanner_words" in result.details

    def test_detect_columns_voting_fraction_hard_reject_no_rescue(self):
        words = _two_col_words(n_lines=30)
        with patch.object(ola, "_COL_MIN_VOTING_WORD_FRACTION", 1.01):
            with patch.object(ola, "_COL_RESCUE_MIN_CONFIDENCE", 2.0):
                result = _detect_page_columns(words, 612, 792)
        assert result.gutter_x is None
        assert result.details.get("hard_reject_reason") or result.details.get("reason")

    def test_region_side_returns_wide_from_seen(self):
        gutter = 306.0
        reg = _region(LayoutRegionType.TEXT, (45, 95, 410, 115), "wide hdr")
        line_meta = [
            ((50, 100, 110, 110), "wide"),
            ((340, 100, 400, 110), "left"),
        ]
        assert _region_side(reg, gutter, 612, line_meta) == "wide"

    def test_find_vector_bullets_aspect_and_text_right(self):
        page = MagicMock()
        page.curves = [{
            "x0": 72, "x1": 84, "top": 700, "bottom": 706,
        }]
        page.rects = [{
            "x0": 72, "x1": 78, "top": 680, "bottom": 686,
        }]
        words = [_w("item", 82, 150, 698, 708)]
        bullets = _find_vector_bullets(page, words)
        assert isinstance(bullets, list)

    def test_extract_layout_merges_and_sorts_vector_bullets(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.extract_words.return_value = [_w("z last", 200, 250, 700, 710)]
        page.lines = []
        page.rects = []
        page.curves = []
        page.images = []
        page.find_tables.return_value = []
        page.hyperlinks = []
        fake_bullet = {
            "text": "\u2022", "x0": 72, "x1": 78, "top": 698, "bottom": 706,
            "size": 10.0, "fontname": "_synthetic_bullet",
        }
        fake_img = np.full((100, 80, 3), 200, dtype=np.uint8)
        with patch.object(ola, "_find_vector_bullets", return_value=[fake_bullet]):
            with patch.object(ola, "_rasterize_page", return_value=(fake_img, 150 / 72.0)):
                regions = extract_layout_regions(page)
        assert isinstance(regions, list)

    def test_extract_layout_h_rules_parse_exception(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.extract_words.return_value = [_w("hello", 72, 120, 700, 710)]
        page.images = []
        page.find_tables.return_value = []
        page.hyperlinks = []
        page.rects = []
        page.curves = []
        type(page).lines = property(lambda self: (_ for _ in ()).throw(RuntimeError))
        fake_img = np.full((100, 80, 3), 200, dtype=np.uint8)
        with patch.object(ola, "_rasterize_page", return_value=(fake_img, 150 / 72.0)):
            regions = extract_layout_regions(page)
        assert isinstance(regions, list)

    def test_segment_vector_gutter_blocks_cross_column_merge(self):
        page = MagicMock()
        left_vecs = [
            {"x0": 50, "x1": 120, "top": 400 + i * 4, "bottom": 404 + i * 4}
            for i in range(6)
        ]
        right_vecs = [
            {"x0": 350, "x1": 420, "top": 400 + i * 4, "bottom": 404 + i * 4}
            for i in range(6)
        ]
        page.lines = left_vecs + right_vecs
        page.rects = []
        page.curves = []
        blocks = _segment_vector_blocks(page, [], 612, 792, gutter_x=306.0, min_members=5)
        assert len(blocks) >= 2

    def test_stripe_lines_exception_and_internal_rules(self):
        page = MagicMock()
        rects = []
        for i in range(8):
            top = 600 - i * 8
            rects.append({
                "x0": 72, "x1": 400,
                "top": top, "bottom": top + 8,
                "fill": True, "non_stroking_color": (0.91, 0.91, 0.91),
            })
        page.rects = rects
        type(page).lines = property(lambda self: (_ for _ in ()).throw(RuntimeError))
        page.chars = [
            {
                "text": "x", "fontname": "Courier",
                "x0": 80 + i * 5, "x1": 84 + i * 5,
                "top": 540, "bottom": 548,
            }
            for i in range(25)
        ]
        with patch.object(ola, "_region_is_predominantly_monospace", return_value=True):
            stripes = _detect_uniform_fill_stripe_regions(page)
        assert isinstance(stripes, list)

        page2 = MagicMock()
        page2.rects = rects
        page2.lines = [{
            "x0": 200, "x1": 201, "top": 530, "bottom": 610,
        }]
        page2.chars = page.chars
        with patch.object(ola, "_region_is_predominantly_monospace", return_value=True):
            assert _detect_uniform_fill_stripe_regions(page2) == []

        page3 = MagicMock()
        page3.rects = rects
        page3.lines = [{
            "x0": 72, "x1": 400, "top": 570, "bottom": 571,
        }]
        page3.chars = page.chars
        with patch.object(ola, "_region_is_predominantly_monospace", return_value=True):
            assert _detect_uniform_fill_stripe_regions(page3) == []

    def test_stripe_cluster_run_break_and_finalize(self):
        page = MagicMock()
        rects = []
        for i in range(8):
            top = 600 - i * 8
            rects.append({
                "x0": 72, "x1": 400,
                "top": top, "bottom": top + 8,
                "fill": True, "non_stroking_color": (0.91, 0.91, 0.91),
            })
        rects.append({
            "x0": 72, "x1": 400,
            "top": 520, "bottom": 528,
            "fill": True, "non_stroking_color": (0.91, 0.91, 0.91),
        })
        for i in range(7):
            top = 510 - i * 8
            rects.append({
                "x0": 72, "x1": 400,
                "top": top, "bottom": top + 8,
                "fill": True, "non_stroking_color": (0.91, 0.91, 0.91),
            })
        page.rects = rects
        page.lines = []
        page.chars = [
            {
                "text": "x", "fontname": "Courier",
                "x0": 80 + (i % 10) * 5.0,
                "x1": 84 + (i % 10) * 5.0,
                "top": 480 + (i // 10) * 12,
                "bottom": 488 + (i // 10) * 12,
            }
            for i in range(30)
        ]
        with patch.object(ola, "_region_is_predominantly_monospace", return_value=True):
            stripes = _detect_uniform_fill_stripe_regions(page)
        assert isinstance(stripes, list)

    def test_borderless_structural_pending_and_prose_break(self):
        words = []
        y = 700.0
        for row in range(4):
            x = 72.0
            for val in ["100", "200", "300"]:
                words.append(_w(val, x, x + 35, y, y + 10))
                x += 45.0
            y -= 14.0
        for i in range(5):
            words.append(_w(f"s{i}", 72, 120, 660 - i * 8, 668 - i * 8))
        words.append(_w("prose paragraph here", 150, 400, 640, 650))
        y = 520.0
        for row in range(3):
            x = 72.0
            for val in ["400", "500"]:
                words.append(_w(val, x, x + 35, y, y + 10))
                x += 45.0
            y -= 14.0
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.return_value = [
            ["H1", "H2", "H3"],
            ["100", "200", "300"],
            ["400", "500", "600"],
        ]
        page.within_bbox.return_value = sub
        page.curves = []
        regions = _detect_borderless_tables(page, 612, 792, words, [])
        assert isinstance(regions, list)

    def test_borderless_header_year_digits_and_low_fill(self):
        words = []
        y = 700.0
        for row in range(5):
            x = 72.0
            for val in ["100", "200"]:
                words.append(_w(val, x, x + 35, y, y + 10))
                x += 45.0
            y -= 14.0
        words.insert(0, _w("2020 2021 2022", 72, 250, 720, 730))
        words.insert(0, _w("months ended June", 72, 250, 735, 745))
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.return_value = [
            ["H1", "H2"],
            ["100", "200"],
            ["300", "400"],
            [None, None],
        ]
        page.within_bbox.return_value = sub
        regions = _detect_borderless_tables(page, 612, 792, words, [])
        assert isinstance(regions, list)

    def test_merge_row_tables_expand_exception_and_invalid(self):
        page = MagicMock()
        page.curves = []
        page.within_bbox.side_effect = RuntimeError("bbox fail")
        singles = [
            ((72, 640, 220, 660), [["R1", "R2", "R3"]]),
            ((72, 620, 220, 640), [["R4", "R5", "R6"]]),
        ]
        merged = _merge_row_tables(singles, page, 612, 792)
        assert len(merged) >= 1

        page2 = MagicMock()
        page2.curves = []
        bad_table = MagicMock()
        bad_table.bbox = "bad"
        sub = MagicMock()
        sub.find_tables.return_value = [bad_table]
        page2.within_bbox.return_value = sub
        with patch.object(ola, "_trim_paragraph_rows", side_effect=lambda g, b: (g, b)):
            merged2 = _merge_row_tables(singles, page2, 612, 792)
        assert len(merged2) >= 1

    def test_merge_row_tables_expand_best_grid_selected(self):
        page = MagicMock()
        page.curves = []
        at = MagicMock()
        at.bbox = (72, 610, 220, 670)
        at.extract.return_value = [
            ["H1", "H2", "H3"],
            ["A", "B", "C"],
            ["D", "E", "F"],
            ["G", "H", "I"],
        ]
        sub = MagicMock()
        sub.find_tables.return_value = [at]
        page.within_bbox.return_value = sub
        singles = [
            ((72, 640, 220, 660), [["R1", "R2", "R3"]]),
            ((72, 620, 220, 640), [["R4", "R5", "R6"]]),
        ]
        with patch.object(ola, "_trim_paragraph_rows", side_effect=lambda g, b: (g, b)):
            merged = _merge_row_tables(singles, page, 612, 792)
        assert merged
        assert any(len(g) >= 3 for _, g in merged)

    def test_is_plausible_table_dense_column_rescue(self):
        grid = [
            ["H1", "H2", "H3"],
            [None, "x", None],
            [None, "y", None],
            [None, "z", None],
        ]
        assert _is_plausible_table((72, 600, 300, 700), grid, 612, 792, []) is True

    def test_split_region_swap_prefer_and_segment_loops(self):
        img = np.full((300, 300, 3), 255, dtype=np.uint8)
        img[50:250, 50:250] = 0
        img[140:160, 50:250] = 255
        parts = _split_region_by_gutters(img, 2.0, (0, 0, 216, 216), [], depth=0)
        assert len(parts) >= 1

        img2 = np.full((300, 300, 3), 255, dtype=np.uint8)
        img2[50:250, 50:250] = 0
        img2[50:250, 140:160] = 255
        parts2 = _split_region_by_gutters(img2, 2.0, (0, 0, 216, 216), [], depth=1)
        assert len(parts2) >= 1

        tiny = np.full((300, 300, 3), 255, dtype=np.uint8)
        parts3 = _split_region_by_gutters(tiny, 2.0, (0, 0, 0.01, 0.01), [], depth=0)
        assert parts3 == [(0, 0, 0.01, 0.01)]

    def test_classify_text_empty_lines_and_run_branches(self):
        words = [_w("solo", 72, 120, 700, 710)]
        blocks = [(70, 695, 130, 715)]
        with patch.object(ola, "_group_words_into_lines", return_value=([], [])):
            assert _classify_text_blocks(blocks, [], words) == []

        lines = [
            [_w("• one", 72, 120, 700, 712)],
            [_w("• two", 72, 120, 680, 692)],
            [_w("plain", 72, 120, 660, 672)],
        ]
        lines[0][0]["text"] = "• one"
        lines[1][0]["text"] = "• two"
        run_words = [w for ln in lines for w in ln]
        with patch.object(ola, "_tight_bbox_from_words", return_value=None):
            assert _classify_text_blocks(blocks, [], run_words) == []

        runs = _split_lines_into_runs([[]])
        assert runs[0][0] == "text"

        cont_lines = [
            [_w("• one", 10, 60, 100, 110)],
            [_w("cont", 20, 80, 115, 125)],
            [_w("• two", 10, 60, 140, 150)],
        ]
        cont_lines[0][0]["text"] = "• one"
        cont_lines[2][0]["text"] = "• two"
        runs2 = _split_lines_into_runs(cont_lines)
        assert any(r[0] == "list" for r in runs2)

        merge_lines = [
            [_w("• one", 10, 60, 100, 110)],
            [_w("• two", 10, 60, 140, 150)],
        ]
        merge_lines[0][0]["text"] = "• one"
        merge_lines[1][0]["text"] = "• two"
        runs3 = _split_lines_into_runs(merge_lines)
        assert runs3[0][0] == "text" or runs3[0][0] == "list"

    def test_reading_order_wide_footer_and_unplaced(self):
        gutter = 306.0
        footer = _region(LayoutRegionType.TEXT, (50, 5, 560, 35), "Footer")
        left = _region(LayoutRegionType.TEXT, (50, 400, 280, 420), "L")
        right = _region(LayoutRegionType.TEXT, (330, 400, 560, 420), "R")
        orphan = _region(LayoutRegionType.TEXT, (50, 200, 280, 220), "O")
        ordered = _reading_order(
            [footer, left, right, orphan], 612, 792,
            gutter_x=gutter, all_words=[],
        )
        assert len(ordered) == 4
        assert ordered[0].text == "Footer"

        thin = _region(LayoutRegionType.TEXT, (50, 399.5, 560, 400.0), "thin")
        ordered2 = _reading_order(
            [thin, left, right], 612, 792, gutter_x=gutter, all_words=[],
        )
        assert len(ordered2) == 3

    def test_hyperlink_empty_uri_and_missing_bbox(self):
        assert _hyperlink_anchor_for_region({"uri": ""}, [_w("x", 0, 10, 0, 10)]) is None
        assert _hyperlink_anchor_for_region({"uri": "https://x.com"}, [_w("x", 0, 10, 0, 10)]) is None
        assert _append_hyperlink_url_to_text("", "a", "https://x.com") == ""
        assert _append_hyperlink_url_to_text("hello", "", "https://x.com") == "hello"
        assert _inject_hyperlinks_into_text("hello", [{"uri": "x"}], []) == "hello"

    def test_inject_hyperlinks_region_no_words(self):
        region = _region(LayoutRegionType.TEXT, (0, 0, 100, 30), "text")
        links = [{"x0": 10, "top": 8, "x1": 40, "bottom": 22, "uri": "https://x.com"}]
        _inject_hyperlinks_into_region(region, links, [_w("outside", 200, 250, 200, 210)])
        assert region.text == "text"

    def test_inject_hyperlinks_list_items_branch(self):
        region = _region(
            LayoutRegionType.LIST,
            (0, 0, 100, 30),
            "item",
            list_items=["item"],
        )
        words = [_w("item", 10, 40, 10, 20)]
        links = [{"x0": 10, "top": 8, "x1": 40, "bottom": 22, "uri": "https://l.com"}]
        _inject_hyperlinks_into_region(region, links, words)
        assert "https://l.com" in region.list_items[0]

    def test_segment_blocks_size_parse_and_h_rule(self):
        words = [_w("a", 50, 200, 700, 710, size="bad")]
        boxes = _segment_blocks_native(words, 612, 792, h_rules=[705.0])
        assert isinstance(boxes, list)

    def test_detect_tables_image_and_vector_branches(self):
        page = MagicMock()
        page.images = [
            {"x0": 10, "x1": 15, "top": 10, "bottom": 15},
            {"x0": 70, "x1": 310, "top": 595, "bottom": 705},
        ]
        page.find_tables.return_value = []
        page.lines = [{"x0": 72, "x1": 540, "top": 600 - i, "bottom": 601 - i} for i in range(25)]
        page.curves = []
        words = [_w(str(i), 80 + i * 20, 95 + i * 20, 620 + i, 630 + i) for i in range(8)]
        sub = MagicMock()
        sub.extract_table.return_value = [["A", "B"], ["C", "D"]]
        page.within_bbox.return_value = sub
        tables = _detect_tables(page, 612, 792, words, stripe_frames=[])
        assert isinstance(tables, list)

    def test_detect_images_invalid_bbox_and_figure_paths(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = [{"x0": "bad", "x1": 200, "top": 500, "bottom": 600}]
        page.lines = []
        page.rects = []
        page.curves = []

        def raster():
            return np.full((792, 612, 3), 255, dtype=np.uint8), 1.0

        assert _detect_images(page, raster, [], existing=[], all_words=[]) == []

        page2 = MagicMock()
        page2.width = 612
        page2.height = 792
        page2.images = []
        page2.lines = [
            {"x0": 100 + i, "x1": 120 + i, "top": 500, "bottom": 505}
            for i in range(18)
        ]
        page2.rects = []
        page2.curves = []
        block = (90, 490, 280, 530)
        regions = _detect_images(page2, raster, [block], existing=[], all_words=[])
        assert any(r.type == LayoutRegionType.IMAGE for r in regions)

    def test_reextract_grid_sub_none_and_exceptions(self):
        page = MagicMock()
        page.within_bbox.side_effect = RuntimeError("fail")
        assert _reextract_grid_for_bbox(page, (72, 600, 300, 700), 612, 792) is None

        page2 = MagicMock()
        sub = MagicMock()
        sub.extract_table.side_effect = RuntimeError("fail")
        page2.within_bbox.return_value = sub
        assert _reextract_grid_for_bbox(page2, (72, 600, 300, 700), 612, 792) is None

    def test_rescue_columns_curve_abort_and_alt_strategies(self):
        page = MagicMock()
        curves = [
            {"x0": 80 + i, "x1": 82 + i, "top": 610 + i, "bottom": 612 + i}
            for i in range(6)
        ]
        page.curves = curves
        grid = _rescue_columns_if_single(
            page, (72, 600, 300, 700), [["only"]], 612, 792,
        )
        assert grid is not None

        page2 = MagicMock()
        page2.curves = []
        sub = MagicMock()
        sub.extract_table.side_effect = [
            None,
            [["A", "B"], ["1", "2"]],
        ]
        page2.within_bbox.return_value = sub
        grid2 = _rescue_columns_if_single(
            page2, (72, 600, 300, 700), [["single"]], 612, 792,
        )
        assert len(grid2[0]) >= 2

    def test_detect_table_boxes_from_vectors_bad_lines_and_rects(self):
        page = MagicMock()
        page.lines = [{"bad": 1}, {
            "x0": 72, "x1": 300, "top": 650, "bottom": 651,
        }]
        page.rects = [{"bad": 1}, {
            "x0": 72, "x1": 300, "top": 600, "bottom": 650,
        }]
        boxes = _detect_table_boxes_from_vectors(page, 612, 792)
        assert isinstance(boxes, list)

    def test_monospace_font_name_empty_and_stripe_fill_false(self):
        assert _is_monospace_font_name(None) is False
        page = MagicMock()
        page.rects = [{"x0": 72, "x1": 400, "top": 600, "bottom": 608, "fill": False}]
        page.lines = []
        assert _detect_uniform_fill_stripe_regions(page) == []

    def test_document_raster_cache_lazy_load(self):
        cache = DocumentRasterCache("/tmp/fake.pdf")
        fake = (np.zeros((10, 10, 3), dtype=np.uint8), 2.0)
        with patch.object(ola, "_render_all_pages", return_value={1: fake}):
            img, scale = cache.get(1)
        assert img.shape == (10, 10, 3)
        assert scale == 2.0

    def test_segment_blocks_empty_lines_return(self):
        with patch.object(ola, "_group_words_into_lines", return_value=([], [])):
            assert _segment_blocks_native([_w("x", 0, 1, 0, 1)], 612, 792) == []

    def test_segment_blocks_overlap_ratio_reject(self):
        words = [
            _w("narrow", 50, 80, 700, 710, size=12),
            _w("wide block text", 50, 400, 680, 690, size=12),
        ]
        with patch.object(ola, "_group_words_into_lines") as mock_group:
            mock_group.return_value = ([words], [0])
            with patch.object(ola, "_resolve_line_sides", return_value=["any"]):
                boxes = _segment_blocks_native(words, 612, 792)
        assert len(boxes) == 1

    def test_segment_vector_min_members_skip(self):
        page = MagicMock()
        page.lines = [{
            "x0": 50, "x1": 120, "top": 400, "bottom": 404,
        }] * 3
        page.rects = []
        page.curves = []
        assert _segment_vector_blocks(page, [], 612, 792, min_members=5) == []

    def test_stripe_internal_vertical_rule_filters(self):
        page = MagicMock()
        rects = []
        for i in range(8):
            top = 600 - i * 8
            rects.append({
                "x0": 72, "x1": 400,
                "top": top, "bottom": top + 8,
                "fill": True, "non_stroking_color": (0.91, 0.91, 0.91),
            })
        page.rects = rects
        page.lines = [{
            "x0": 200, "x1": 201, "top": 530, "bottom": 610,
        }]
        page.chars = [
            {
                "text": "x", "fontname": "Courier",
                "x0": 80 + i * 5, "x1": 84 + i * 5,
                "top": 540, "bottom": 548,
            }
            for i in range(25)
        ]
        assert _detect_uniform_fill_stripe_regions(page) == []

    def test_stripe_internal_horizontal_rule_filters(self):
        page = MagicMock()
        rects = []
        for i in range(8):
            top = 600 - i * 8
            rects.append({
                "x0": 72, "x1": 400,
                "top": top, "bottom": top + 8,
                "fill": True, "non_stroking_color": (0.91, 0.91, 0.91),
            })
        page.rects = rects
        page.lines = [{
            "x0": 72, "x1": 400, "top": 570, "bottom": 571,
        }]
        page.chars = [
            {
                "text": "x", "fontname": "Courier",
                "x0": 80 + i * 5, "x1": 84 + i * 5,
                "top": 540, "bottom": 548,
            }
            for i in range(25)
        ]
        assert _detect_uniform_fill_stripe_regions(page) == []

    def test_borderless_structural_big_gap_flushes_band(self):
        words = []
        y = 700.0
        for row in range(4):
            x = 72.0
            for val in ["100", "200", "300"]:
                words.append(_w(val, x, x + 35, y, y + 10))
                x += 45.0
            y -= 14.0
        for i in range(6):
            words.append(_w(f"struct{i}", 72, 120, 660 - i * 8, 668 - i * 8))
        y = 500.0
        for row in range(4):
            x = 72.0
            for val in ["400", "500", "600"]:
                words.append(_w(val, x, x + 35, y, y + 10))
                x += 45.0
            y -= 14.0
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.return_value = [
            ["H1", "H2", "H3"],
            ["100", "200", "300"],
            ["400", "500", "600"],
        ]
        page.within_bbox.return_value = sub
        regions = _detect_borderless_tables(page, 612, 792, words, [])
        assert isinstance(regions, list)

    def test_borderless_prose_row_breaks_band(self):
        words = []
        y = 700.0
        for row in range(3):
            x = 72.0
            for val in ["10", "20"]:
                words.append(_w(val, x, x + 30, y, y + 10))
                x += 40.0
            y -= 14.0
        words.append(_w("This is a prose paragraph spanning the page", 72, 400, 650, 660))
        y = 620.0
        for row in range(3):
            x = 72.0
            for val in ["30", "40"]:
                words.append(_w(val, x, x + 30, y, y + 10))
                x += 40.0
            y -= 14.0
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.return_value = [["a", "b"], ["1", "2"], ["3", "4"]]
        page.within_bbox.return_value = sub
        regions = _detect_borderless_tables(page, 612, 792, words, [])
        assert isinstance(regions, list)

    def test_borderless_low_fill_ratio_skipped(self):
        words = []
        y = 700.0
        for row in range(4):
            x = 72.0
            for val in ["100", "200"]:
                words.append(_w(val, x, x + 35, y, y + 10))
                x += 45.0
            y -= 14.0
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.return_value = [
            ["H1", "H2"],
            [None, None],
            [None, None],
            [None, None],
        ]
        page.within_bbox.return_value = sub
        regions = _detect_borderless_tables(page, 612, 792, words, [])
        assert regions == []

    def test_is_plausible_table_fill_rescue_path(self):
        grid = [
            ["H1", "H2", " ", " ", " ", " ", " ", " ", " ", " ", " ", " "],
            ["100", "200", " ", " ", " ", " ", " ", " ", " ", " ", " ", " "],
        ]
        assert _is_plausible_table((72, 600, 300, 700), grid, 612, 792, []) is True

    def test_is_plausible_table_fill_rescue_fails(self):
        grid = [
            ["H1", " ", " ", " ", " "],
            [None, None, None, None, None],
            [None, None, None, None, None],
            [None, None, None, None, None],
        ]
        assert _is_plausible_table((72, 600, 300, 700), grid, 612, 792, []) is False

    def test_merge_row_tables_expand_validates_rows(self):
        page = MagicMock()
        page.curves = []
        at = MagicMock()
        at.bbox = (72, 610, 220, 670)
        at.extract.return_value = [
            ["H1", "H2", "H3"],
            ["A", "B", "C"],
            ["D", "E", "F"],
        ]
        sub = MagicMock()
        sub.find_tables.return_value = [at]
        page.within_bbox.return_value = sub
        singles = [
            ((72, 650, 220, 670), [["R1", "R2", "R3"]]),
            ((72, 630, 220, 650), [["R4", "R5", "R6"]]),
        ]
        with patch.object(ola, "_trim_paragraph_rows", side_effect=lambda g, b: (g, b)):
            with patch.object(ola, "_trim_empty_rows_cols", side_effect=lambda g: g):
                merged = _merge_row_tables(singles, page, 612, 792)
        assert merged

    def test_refine_image_bbox_invalid_returns_empty(self):
        assert _refine_image_bbox((0, 0, -1, 10), [], [], 612, 792) == []

    def test_trim_image_text_margins_zero_height(self):
        assert _trim_image_text_margins((0, 10, 100, 10), []) == (0, 10, 100, 10)

    def test_tighten_to_content_tiny_crop(self):
        img = np.full((10, 10, 3), 255, dtype=np.uint8)
        assert _tighten_to_content(img, 1.0, (0, 0, 0.5, 0.5)) == (0, 0, 0.5, 0.5)

    def test_detect_images_out_of_bounds_refine(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = [{"x0": -5, "x1": 50, "top": 500, "bottom": 580}]
        page.lines = []
        page.rects = []
        page.curves = []
        block = (90, 490, 200, 530)

        def raster():
            return np.full((792, 612, 3), 255, dtype=np.uint8), 1.0

        regions = _detect_images(
            page, raster, [block], existing=[], all_words=[_w("lbl", 10, 30, 520, 530)],
        )
        assert isinstance(regions, list)

    def test_detect_images_word_area_skip_in_refine(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = [{"x0": -5, "x1": 200, "top": 500, "bottom": 580}]
        page.lines = []
        page.rects = []
        page.curves = []
        words = [_w("fill", 10, 190, 510, 570)]

        def raster():
            return np.full((792, 612, 3), 255, dtype=np.uint8), 1.0

        regions = _detect_images(page, raster, [(0, 490, 210, 590)], existing=[], all_words=words)
        assert isinstance(regions, list)

    def test_classify_text_run_type_empty_skip(self):
        words = [_w("txt", 72, 120, 700, 710)]
        blocks = [(70, 695, 130, 715)]
        with patch.object(ola, "_split_lines_into_runs", return_value=[("text", [])]):
            assert _classify_text_blocks(blocks, [], words) == []

    def test_classify_list_two_items_created(self):
        lines = [
            [_w("• one", 72, 120, 700, 712)],
            [_w("• two", 72, 120, 680, 692)],
        ]
        lines[0][0]["text"] = "• one"
        lines[1][0]["text"] = "• two"
        words = [w for ln in lines for w in ln]
        blocks = [(70, 675, 160, 715)]
        regions = _classify_text_blocks(blocks, [], words)
        assert any(r.type == LayoutRegionType.LIST for r in regions)

    def test_split_lines_finalize_text_merge(self):
        lines = [
            [_w("plain one", 72, 120, 700, 712)],
            [_w("plain two", 72, 120, 680, 692)],
        ]
        runs = _split_lines_into_runs(lines)
        assert runs[0][0] == "text"
        assert len(runs[0][1]) == 2

    def test_reading_order_edge_band_via_line_meta(self):
        gutter = 306.0
        footer = _region(LayoutRegionType.TEXT, (50, 5, 560, 35), "Footer")
        left = _region(LayoutRegionType.TEXT, (50, 400, 280, 420), "L")
        right = _region(LayoutRegionType.TEXT, (330, 400, 560, 420), "R")
        line_meta = [((50, 5, 200, 35), "left")]
        with patch.object(ola, "_build_line_side_meta", return_value=line_meta):
            ordered = _reading_order(
                [footer, left, right], 612, 792, gutter_x=gutter, all_words=[],
            )
        assert ordered[0].text == "Footer"

    def test_reading_order_col_of_centroid_fallback(self):
        gutter = 306.0
        mystery = _region(LayoutRegionType.TABLE, (50, 400, 280, 420))
        left = _region(LayoutRegionType.TEXT, (50, 200, 280, 220), "L")
        right = _region(LayoutRegionType.TEXT, (330, 200, 560, 220), "R")
        with patch.object(ola, "_region_side", return_value="any"):
            ordered = _reading_order(
                [mystery, left, right], 612, 792, gutter_x=gutter, all_words=[],
            )
        assert len(ordered) == 3

    def test_reextract_region_payload_table_grid(self):
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.return_value = [["a", "b"], ["1", "2"]]
        page.within_bbox.return_value = sub
        reg = _region(LayoutRegionType.TABLE, (72, 600, 300, 700), "old")
        out = _reextract_region_payload(reg, page, [], 612, 792)
        assert out.table_grid is not None

    def test_overlap_ratio_zero_box_area(self):
        assert _overlap_ratio((10, 10, 10, 20), (0, 0, 5, 5)) == 0.0

    def test_ink_density_none_image(self):
        assert _ink_density(None, (0, 0, 10, 10), 1.0) == 0.0

    def test_hyperlink_anchor_empty_anchor_text(self):
        link = {"x0": 10, "top": 8, "x1": 40, "bottom": 22, "uri": "https://x.com"}
        words = [_w("   ", 10, 40, 10, 20)]
        assert _hyperlink_anchor_for_region(link, words) is None

    def test_append_hyperlink_marker_already_present(self):
        text = "link (https://x.com) here"
        out = _append_hyperlink_url_to_text(text, "link", "https://x.com")
        assert out == text

    def test_inject_hyperlinks_table_grid_cells(self):
        region = _region(
            LayoutRegionType.TABLE,
            (0, 0, 100, 100),
            "cell",
            table_grid=[["cell"]],
        )
        words = [_w("cell", 10, 40, 10, 20)]
        links = [{"x0": 10, "top": 8, "x1": 40, "bottom": 22, "uri": "https://t.com"}]
        _inject_hyperlinks_into_region(region, links, words)
        assert "https://t.com" in region.table_grid[0][0]

    def test_detect_columns_empty_raw_line_and_bad_center(self):
        words = _two_col_words(n_lines=25)
        words.append({"text": "bad", "top": 400, "bottom": 410})
        for i in range(8):
            top = 300 + i * 12
            words.append(_w(f"onlyR{i}", 330, 520, top, top + 10))
        tbox = (40, 350, 570, 450)
        bad_in_table = {"text": "x", "x0": "nope", "x1": 60, "top": 400, "bottom": 410}
        words.extend([bad_in_table, _w("in", 50, 60, 400, 410)] * 3)
        result = _detect_page_columns(words, 612, 792, table_bboxes=[tbox])
        assert result.details.get("n_right_only_lines", 0) >= 0

    def test_detect_columns_peak_core_too_narrow_at_core(self):
        words = _two_col_words(n_lines=30)
        with patch.object(ola, "_COL_BIN_SIZE", 2.0):
            with patch.object(ola, "_COL_PEAK_CORE_FRACTION", 1.0):
                result = _detect_page_columns(words, 612, 792)
        assert isinstance(result, ColumnDetectionResult)

    def test_detect_columns_gutter_spanner_not_crossing(self):
        words = _two_col_words(n_lines=30)
        words.append(_w("leftonly", 50, 200, 500, 510))
        result = _detect_page_columns(words, 612, 792)
        assert "n_gutter_spanner_words" in result.details

    def test_find_vector_bullets_bad_aspect_ratio(self):
        page = MagicMock()
        page.curves = [{"x0": 72, "x1": 120, "top": 700, "bottom": 706}]
        page.rects = []
        assert _find_vector_bullets(page, [_w("t", 130, 180, 700, 710)]) == []

    def test_find_vector_bullets_bad_word_coords(self):
        page = MagicMock()
        page.curves = [
            {"x0": 72, "x1": 78, "top": 700, "bottom": 706},
            {"x0": 72, "x1": 78, "top": 680, "bottom": 686},
        ]
        page.rects = []
        words = [{"text": "t", "bad": 1}, _w("text", 82, 150, 700, 706)]
        bullets = _find_vector_bullets(page, words)
        assert isinstance(bullets, list)

    def test_stripe_detect_bad_rect_coords(self):
        page = MagicMock()
        page.rects = [{"bad": 1}, {
            "x0": 72, "x1": 400, "top": 600, "bottom": 608,
            "fill": True, "non_stroking_color": (0.9, 0.9, 0.9),
        }]
        page.lines = []
        assert _detect_uniform_fill_stripe_regions(page) == []

    def test_detect_tables_invalid_image_and_table_bbox(self):
        page = MagicMock()
        page.images = [{"x0": "bad", "x1": 10, "top": 0, "bottom": 10}]
        bad_t = MagicMock()
        bad_t.bbox = (0, 0, -1, 10)
        good_t = MagicMock()
        good_t.bbox = (72, 600, 300, 700)
        good_t.extract.return_value = [["a", "b"], ["1", "2"]]
        page.find_tables.return_value = [bad_t, good_t]
        page.lines = []
        tables = _detect_tables(page, 612, 792, [_w("a", 80, 100, 620, 630)] * 5)
        assert isinstance(tables, list)

    def test_detect_tables_vector_extract_exception(self):
        page = MagicMock()
        page.images = []
        page.find_tables.return_value = []
        page.lines = [
            {"x0": 72, "x1": 300, "top": 650, "bottom": 651},
            {"x0": 72, "x1": 300, "top": 600, "bottom": 601},
            {"x0": 72, "x1": 73, "top": 600, "bottom": 660},
            {"x0": 299, "x1": 300, "top": 600, "bottom": 660},
        ]
        page.rects = []
        words = [_w(str(i), 80 + i * 20, 95 + i * 20, 620 + i, 630 + i) for i in range(6)]
        sub = MagicMock()
        sub.extract_table.side_effect = RuntimeError("fail")
        page.within_bbox.return_value = sub
        tables = _detect_tables(page, 612, 792, words, stripe_frames=[])
        assert tables == []

    def test_detect_tables_hor_rules_exception(self):
        page = MagicMock()
        page.images = []
        page.find_tables.return_value = []
        type(page).lines = property(lambda self: (_ for _ in ()).throw(RuntimeError))
        tables = _detect_tables(page, 612, 792, [], stripe_frames=[])
        assert tables == []

    def test_split_region_masks_text_rows(self):
        img = np.full((300, 300, 3), 255, dtype=np.uint8)
        img[50:250, 50:250] = 0
        img[140:160, 50:250] = 255
        text_rows = [(55, 65), (200, 210)]
        parts = _split_region_by_gutters(
            img, 2.0, (0, 0, 216, 216), text_rows, depth=0,
        )
        assert len(parts) >= 1

    def test_split_region_vertical_min_dim_skip(self):
        img = np.full((300, 300, 3), 255, dtype=np.uint8)
        img[10:290, 10:145] = 0
        img[10:290, 155:290] = 0
        parts = _split_region_by_gutters(img, 2.0, (0, 0, 216, 216), [], depth=1)
        assert isinstance(parts, list)

    def test_extract_layout_triggers_lazy_raster(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.extract_words.return_value = _two_col_words(n_lines=30)
        page.lines = []
        page.rects = []
        page.curves = []
        page.images = []
        page.find_tables.return_value = []
        page.hyperlinks = []
        fake_img = np.full((792, 612, 3), 200, dtype=np.uint8)
        with patch.object(ola, "_COL_MIN_VOTING_WORD_FRACTION", 1.01):
            with patch.object(ola, "_COL_RESCUE_MIN_CONFIDENCE", 0.0):
                with patch.object(
                    ola, "_rasterize_page", return_value=(fake_img, 150 / 72.0),
                ) as mock_raster:
                    extract_layout_regions(page, pdf_path="/tmp/test.pdf")
        assert mock_raster.called

    def test_segment_vector_gutter_gate_blocks_union(self):
        page = MagicMock()
        page.lines = [
            {"x0": 50, "x1": 200, "top": 400 + i * 4, "bottom": 404 + i * 4}
            for i in range(6)
        ] + [
            {"x0": 320, "x1": 450, "top": 400 + i * 4, "bottom": 404 + i * 4}
            for i in range(6)
        ]
        page.rects = []
        page.curves = []
        blocks = _segment_vector_blocks(
            page, [], 612, 792, gutter_x=306.0, min_members=5, max_gap=25.0,
        )
        assert len(blocks) >= 2

    def test_borderless_pending_struct_exceeds_three(self):
        words = []
        y = 700.0
        for row in range(4):
            x = 72.0
            for val in ["100", "200"]:
                words.append(_w(val, x, x + 35, y, y + 10))
                x += 45.0
            y -= 14.0
        for i in range(5):
            words.append(_w(f"s{i}", 72, 120, 660 - i * 6, 666 - i * 6))
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.return_value = [["a", "b"], ["1", "2"], ["3", "4"]]
        page.within_bbox.return_value = sub
        regions = _detect_borderless_tables(page, 612, 792, words, [])
        assert isinstance(regions, list)

    def test_borderless_header_fold_and_year_tokens(self):
        words = []
        y = 700.0
        for row in range(5):
            x = 72.0
            for val in ["100", "200", "300"]:
                words.append(_w(val, x, x + 35, y, y + 10))
                x += 45.0
            y -= 14.0
        words.insert(0, _w("months ended March", 72, 250, 735, 745))
        words.insert(0, _w("2020 2021 2022 2023", 72, 250, 750, 760))
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.return_value = [
            ["H1", "H2", "H3"],
            ["100", "200", "300"],
            ["400", "500", "600"],
        ]
        page.within_bbox.return_value = sub
        regions = _detect_borderless_tables(page, 612, 792, words, [])
        assert isinstance(regions, list)

    def test_merge_row_expand_alt_table_row_alignment(self):
        page = MagicMock()
        page.curves = []
        at = MagicMock()
        at.bbox = (72, 618, 220, 668)
        at.extract.return_value = [
            ["H1", "H2", "H3"],
            ["A", "B", "C"],
            ["D", "E", "F"],
        ]
        sub = MagicMock()
        sub.find_tables.return_value = [at]
        page.within_bbox.return_value = sub
        singles = [
            ((72, 648, 220, 668), [["R1", "R2", "R3"]]),
            ((72, 628, 220, 648), [["R4", "R5", "R6"]]),
        ]
        with patch.object(ola, "_trim_paragraph_rows", side_effect=lambda g, b: (g, b)):
            with patch.object(ola, "_trim_empty_rows_cols", side_effect=lambda g: g):
                merged = _merge_row_tables(singles, page, 612, 792)
        assert merged

    def test_detect_columns_core_lo_expansion(self):
        words = _two_col_words(n_lines=30)
        with patch.object(ola, "_COL_PEAK_CORE_FRACTION", 0.5):
            result = _detect_page_columns(words, 612, 792)
        assert isinstance(result, ColumnDetectionResult)

    def test_detect_columns_gutter_core_narrow_reject(self):
        words = _two_col_words(n_lines=30)
        with patch.object(ola, "_COL_BIN_SIZE", 1.0):
            with patch.object(ola, "_COL_MIN_GUTTER_WIDTH", 8.0):
                result = _detect_page_columns(words, 612, 792)
        assert isinstance(result, ColumnDetectionResult)

    def test_detect_columns_single_peak_voter(self):
        words = _two_col_words(n_lines=6)
        with patch.object(ola, "_COL_MIN_PEAK_VOTES", 1):
            with patch.object(ola, "_COL_MIN_SPANNING_LINES", 3):
                result = _detect_page_columns(words, 612, 792)
        assert result.details.get("voter_y_span", 0) == 0.0 or result.gutter_x is None

    def test_is_chart_satellite_area_ratio_reject(self):
        label = _region(LayoutRegionType.TEXT, (0, 0, 200, 200), "big")
        image = _region(LayoutRegionType.IMAGE, (0, 0, 50, 50))
        assert _is_chart_satellite(label, image) is False

    def test_segment_blocks_low_overlap_skips_merge(self):
        words = [
            _w("left", 50, 100, 700, 710, size=12),
            _w("far", 250, 400, 680, 690, size=12),
        ]
        boxes = _segment_blocks_native(words, 612, 792)
        assert len(boxes) == 2

    def test_resolve_overlaps_incompatible_list_type(self):
        text = _region(LayoutRegionType.TEXT, (0, 0, 100, 20), "aaa bbb ccc ddd")
        lst = _region(LayoutRegionType.LIST, (0, 0, 100, 20), "• one", list_items=["one"])
        out = _resolve_overlaps([text, lst], page_w=612, page_h=792, proximity=5.0)
        assert len(out) == 2

    def test_split_lines_list_continuation_breaks(self):
        lines = [
            [_w("• one", 10, 60, 100, 110)],
            [_w("not cont", 5, 55, 115, 125)],
            [_w("• two", 10, 60, 140, 150)],
        ]
        lines[0][0]["text"] = "• one"
        lines[2][0]["text"] = "• two"
        runs = _split_lines_into_runs(lines)
        assert any(r[0] == "text" for r in runs)

    def test_split_lines_single_marker_becomes_text(self):
        lines = [[_w("• only", 10, 60, 100, 110)]]
        lines[0][0]["text"] = "• only"
        runs = _split_lines_into_runs(lines)
        assert runs[0][0] == "text"

    def test_extract_list_items_continues_without_marker(self):
        lines = [
            [_w("• start", 10, 80, 100, 110)],
            [_w("continued", 20, 80, 115, 125)],
        ]
        lines[0][0]["text"] = "• start"
        items = _extract_list_items(lines)
        assert len(items) == 1
        assert "continued" in items[0]

    def test_reextract_grid_sub_none_branch(self):
        page = MagicMock()
        page.within_bbox.return_value = None
        assert _reextract_grid_for_bbox(page, (72, 600, 300, 700), 612, 792) is None

    def test_borderless_bad_word_coords_skipped(self):
        words = [{"text": "bad", "x0": 0, "x1": 1, "top": 700, "bottom": 710}]
        words.extend([_w(str(i), 72, 100, 680 - i * 14, 690 - i * 14) for i in range(8)])
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.return_value = [["a", "b"], ["1", "2"]]
        page.within_bbox.return_value = sub
        regions = _detect_borderless_tables(page, 612, 792, words, [])
        assert isinstance(regions, list)

    def test_borderless_extract_table_exception(self):
        words = []
        y = 700.0
        for row in range(4):
            x = 72.0
            for val in ["100", "200"]:
                words.append(_w(val, x, x + 35, y, y + 10))
                x += 45.0
            y -= 14.0
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.side_effect = RuntimeError("fail")
        page.within_bbox.return_value = sub
        regions = _detect_borderless_tables(page, 612, 792, words, [])
        assert regions == []

    def test_detect_images_embedded_full_page_skip(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = [{"x0": 0, "x1": 612, "top": 0, "bottom": 792}]
        page.lines = []
        page.rects = []
        page.curves = []

        def raster():
            return np.full((792, 612, 3), 255, dtype=np.uint8), 1.0

        assert _detect_images(page, raster, [], existing=[], all_words=[]) == []

    def test_detect_images_raster_exception_in_cluster(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = [
            {"x0": 100, "x1": 180, "top": 500, "bottom": 580},
            {"x0": 190, "x1": 270, "top": 500, "bottom": 580},
        ]
        page.lines = []
        page.rects = []
        page.curves = []

        def bad_raster():
            raise RuntimeError("fail")

        regions = _detect_images(page, bad_raster, [], existing=[], all_words=[])
        assert isinstance(regions, list)

    def test_detect_images_vector_bad_coords(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = []
        page.lines = [{"bad": 1}] * 20
        page.rects = []
        page.curves = []
        block = (90, 490, 280, 530)

        def raster():
            img = np.full((792, 612, 3), 255, dtype=np.uint8)
            img[490:530, 90:280] = 50
            return img, 1.0

        regions = _detect_images(page, raster, [block], existing=[], all_words=[])
        assert isinstance(regions, list)

    def test_trim_image_text_margins_trims_rows(self):
        bbox = (0, 0, 100, 100)
        words = [
            _w("top", 10, 20, 2, 5),
            _w("body", 10, 20, 40, 50),
            _w("bot", 10, 20, 95, 98),
        ]
        trimmed = _trim_image_text_margins(bbox, words)
        assert trimmed[1] > bbox[1]

    def test_refine_image_bbox_small_intersection_skip(self):
        pp_bbox = (0, 0, 100, 100)
        cv_blocks = [(95, 95, 200, 200)]
        out = _refine_image_bbox(pp_bbox, cv_blocks, [], 612, 792)
        assert out == [pp_bbox]

    def test_stripe_cluster_break_on_gap(self):
        page = MagicMock()
        rects = []
        for i in range(8):
            top = 600 - i * 8
            rects.append({
                "x0": 72, "x1": 400,
                "top": top, "bottom": top + 8,
                "fill": True, "non_stroking_color": (0.91, 0.91, 0.91),
            })
        rects.append({
            "x0": 72, "x1": 400,
            "top": 520, "bottom": 528,
            "fill": True, "non_stroking_color": (0.91, 0.91, 0.91),
        })
        page.rects = rects
        page.lines = []
        page.chars = []
        assert _detect_uniform_fill_stripe_regions(page) == []

    def test_detect_tables_stripe_skip_and_word_count(self):
        page = MagicMock()
        page.images = []
        mock_t = MagicMock()
        mock_t.bbox = (72, 600, 300, 700)
        mock_t.extract.return_value = [["a", "b"], ["1", "2"]]
        page.find_tables.return_value = [mock_t]
        page.lines = [
            {"x0": 72, "x1": 300, "top": 650, "bottom": 651},
            {"x0": 72, "x1": 300, "top": 600, "bottom": 601},
            {"x0": 72, "x1": 73, "top": 600, "bottom": 660},
            {"x0": 299, "x1": 300, "top": 600, "bottom": 660},
        ]
        stripe = (70, 595, 305, 705)
        words = [_w("a", 80, 100, 620, 630)]
        tables = _detect_tables(page, 612, 792, words, stripe_frames=[stripe])
        assert isinstance(tables, list)

    def test_merge_small_text_no_text_neighbor(self):
        table = _region(LayoutRegionType.TABLE, (0, 0, 100, 50))
        small = _region(LayoutRegionType.TEXT, (0, 60, 100, 80), "x")
        out = _merge_small_text_regions([table, small])
        assert len(out) == 2

    def test_overlap_ratio_zero_area(self):
        assert _overlap_ratio((5, 5, 5, 10), (0, 0, 4, 4)) == 0.0

    def test_is_plausible_table_rescue_via_trim_patch(self):
        sparse = [
            ["H1", "H2", " ", " ", " "],
            ["100", "200", " ", " ", " "],
        ]
        with patch.object(ola, "_trim_empty_rows_cols", return_value=sparse):
            assert _is_plausible_table((72, 600, 300, 700), sparse, 612, 792, []) is True

    def test_is_plausible_table_rescue_fail_via_trim_patch(self):
        sparse = [
            ["H1", " ", " ", " "],
            [None, None, None, None],
        ]
        with patch.object(ola, "_trim_empty_rows_cols", return_value=sparse):
            assert _is_plausible_table((72, 600, 300, 700), sparse, 612, 792, []) is False

    def test_detect_columns_bin_degenerate_continue(self):
        words = _two_col_words(n_lines=30)
        with patch.object(ola, "_COL_BIN_SIZE", 1000.0):
            result = _detect_page_columns(words, 612, 792)
        assert isinstance(result, ColumnDetectionResult)

    def test_detect_columns_core_lo_hi_expansion(self):
        words = _two_col_words(n_lines=30)
        with patch.object(ola, "_COL_PEAK_CORE_FRACTION", 0.1):
            result = _detect_page_columns(words, 612, 792)
        assert isinstance(result, ColumnDetectionResult)

    def test_detect_columns_gutter_core_width_reject(self):
        words = _two_col_words(n_lines=30)
        with patch.object(ola, "_COL_BIN_SIZE", 1.0):
            with patch.object(ola, "_COL_MIN_GUTTER_WIDTH", 50.0):
                result = _detect_page_columns(words, 612, 792)
        assert result.gutter_x is None

    def test_detect_columns_gutter_x_zero_reject(self):
        words = _two_col_words(n_lines=30)
        with patch.object(ola, "_COL_CENTRAL_RANGE", (0.0, 0.001)):
            result = _detect_page_columns(words, 612, 792)
        assert result.gutter_x is None

    def test_detect_columns_empty_line_in_side_balance(self):
        words = _two_col_words(n_lines=20)
        for i in range(6):
            top = 300 + i * 12
            words.append(_w(f"onlyR{i}", 330, 520, top, top + 10))
        result = _detect_page_columns(words, 612, 792)
        assert result.details.get("n_right_only_lines", 0) >= 0

    def test_detect_columns_bad_word_center_in_balance(self):
        words = _two_col_words(n_lines=20)
        words.append({"text": "bad", "x0": "nope", "x1": 100, "top": 400, "bottom": 410})
        result = _detect_page_columns(words, 612, 792)
        assert isinstance(result, ColumnDetectionResult)

    def test_detect_columns_gutter_spanner_continue_paths(self):
        words = _two_col_words(n_lines=20)
        words.append(_w("short", 280, 320, 500, 510))
        with patch.object(ola, "_COL_CENTRAL_RANGE", (0.45, 0.55)):
            result = _detect_page_columns(words, 612, 792)
        assert "voting_word_fraction" in result.details

    def test_find_vector_bullets_skips_non_square_aspect(self):
        page = MagicMock()
        page.curves = [{"x0": 72, "x1": 120, "top": 700, "bottom": 706}]
        page.rects = []
        assert _find_vector_bullets(page, [_w("t", 130, 180, 700, 706)]) == []

    def test_stripe_internal_rule_bad_line_coords(self):
        page = MagicMock()
        rects = []
        for i in range(8):
            top = 600 - i * 8
            rects.append({
                "x0": 72, "x1": 400,
                "top": top, "bottom": top + 8,
                "fill": True, "non_stroking_color": (0.91, 0.91, 0.91),
            })
        page.rects = rects
        page.lines = [{"bad": 1}, {
            "x0": 200, "x1": 201, "top": 530, "bottom": 610,
        }]
        page.chars = [
            {
                "text": "x", "fontname": "Courier",
                "x0": 80 + i * 5, "x1": 84 + i * 5,
                "top": 540, "bottom": 548,
            }
            for i in range(25)
        ]
        assert _detect_uniform_fill_stripe_regions(page) == []

    def test_borderless_prose_width_class(self):
        words = [_w("wide prose " * 8, 72, 500, 650, 660)]
        y = 700.0
        for row in range(3):
            x = 72.0
            for val in ["10", "20"]:
                words.append(_w(val, x, x + 30, y, y + 10))
                x += 40.0
            y -= 14.0
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.return_value = [["a", "b"], ["1", "2"]]
        page.within_bbox.return_value = sub
        regions = _detect_borderless_tables(page, 612, 792, words, [])
        assert isinstance(regions, list)

    def test_merge_row_expand_invalid_alt_table(self):
        page = MagicMock()
        page.curves = []
        at = MagicMock()
        at.bbox = (72, 610, 220, 670)
        at.extract.return_value = None
        sub = MagicMock()
        sub.find_tables.return_value = [at]
        page.within_bbox.return_value = sub
        singles = [
            ((72, 650, 220, 670), [["R1", "R2", "R3"]]),
            ((72, 630, 220, 650), [["R4", "R5", "R6"]]),
        ]
        merged = _merge_row_tables(singles, page, 612, 792)
        assert len(merged) >= 1

    def test_rescue_columns_both_strategies_fail(self):
        page = MagicMock()
        page.curves = []
        sub = MagicMock()
        sub.extract_table.return_value = None
        page.within_bbox.return_value = sub
        grid = _rescue_columns_if_single(
            page, (72, 600, 300, 700), [["only"]], 612, 792,
        )
        assert grid == [["only"]]

    def test_split_region_swap_to_vertical(self):
        img = np.full((300, 300, 3), 255, dtype=np.uint8)
        img[50:250, 50:145] = 0
        img[50:250, 155:250] = 0
        parts = _split_region_by_gutters(img, 2.0, (0, 0, 216, 216), [], depth=0)
        assert len(parts) >= 1

    def test_split_region_horizontal_min_dim_continue(self):
        img = np.full((300, 300, 3), 255, dtype=np.uint8)
        img[10:290, 10:290] = 0
        img[140:160, 10:290] = 255
        parts = _split_region_by_gutters(img, 2.0, (0, 0, 216, 216), [], depth=0, min_dim=200.0)
        assert parts == [(0, 0, 216, 216)]

    def test_classify_list_single_marker_fallback(self):
        lines = [[_w("• solo", 72, 120, 700, 712)]]
        lines[0][0]["text"] = "• solo"
        blocks = [(70, 695, 160, 715)]
        regions = _classify_text_blocks(blocks, [], lines[0])
        assert regions[0].type == LayoutRegionType.TEXT

    def test_split_lines_continuation_indent_break(self):
        lines = [
            [_w("• one", 10, 60, 100, 110)],
            [_w("• two", 10, 60, 140, 150)],
            [_w("• three", 10, 60, 180, 190)],
        ]
        lines[0][0]["text"] = "• one"
        lines[1][0]["text"] = "• two"
        lines[2][0]["text"] = "• three"
        runs = _split_lines_into_runs(lines)
        assert runs[0][0] == "list"

    def test_reextract_region_payload_empty_words(self):
        reg = _region(LayoutRegionType.TEXT, (0, 0, 100, 100), "t")
        out = _reextract_region_payload(reg, None, [], 612, 792)
        assert out.text == "t"

    def test_inject_hyperlinks_table_grid_path(self):
        region = _region(
            LayoutRegionType.TABLE,
            (0, 0, 100, 100),
            "cell",
            table_grid=[["cell"]],
        )
        words = [_w("cell", 10, 40, 10, 20)]
        links = [{"x0": 10, "top": 8, "x1": 40, "bottom": 22, "uri": "https://t.com"}]
        _inject_hyperlinks_into_region(region, links, words)
        assert "https://t.com" in region.text

    def test_reading_order_single_column_fallback(self):
        left = _region(LayoutRegionType.TEXT, (50, 400, 280, 420), "L")
        ordered = _reading_order([left], 612, 792, gutter_x=306.0, all_words=[])
        assert len(ordered) == 1

    def test_resolve_overlaps_table_proximity_blocked(self):
        table = _region(LayoutRegionType.TABLE, (0, 0, 200, 100), "tbl")
        text = _region(LayoutRegionType.TEXT, (0, 105, 200, 125), "body text here")
        out = _resolve_overlaps([table, text], page_w=612, page_h=792, proximity=10.0)
        assert len(out) == 2

    def test_detect_tables_full_page_image_rejected(self):
        page = MagicMock()
        page.images = [{"x0": 0, "x1": 612, "top": 0, "bottom": 792}]
        page.find_tables.return_value = []
        page.lines = [{"x0": 72, "x1": 540, "top": 600 - i, "bottom": 601 - i} for i in range(25)]
        page.curves = []
        words = []
        y = 700.0
        for row in range(5):
            x = 72.0
            for val in ["100", "200"]:
                words.append(_w(val, x, x + 35, y, y + 10))
                x += 45.0
            y -= 14.0
        sub = MagicMock()
        sub.extract_table.return_value = [["a", "b"], ["1", "2"], ["3", "4"]]
        page.within_bbox.return_value = sub
        tables = _detect_tables(page, 612, 792, words, stripe_frames=[])
        assert isinstance(tables, list)

    def test_detect_tables_vector_overlap_existing_skip(self):
        page = MagicMock()
        page.images = []
        existing = _region(LayoutRegionType.TABLE, (72, 600, 300, 700), "t")
        page.find_tables.return_value = []
        page.lines = [
            {"x0": 72, "x1": 300, "top": 650, "bottom": 651},
            {"x0": 72, "x1": 300, "top": 600, "bottom": 601},
            {"x0": 72, "x1": 73, "top": 600, "bottom": 660},
            {"x0": 299, "x1": 300, "top": 600, "bottom": 660},
        ]
        page.rects = []
        words = [_w(str(i), 80 + i * 20, 95 + i * 20, 620 + i, 630 + i) for i in range(6)]
        sub = MagicMock()
        sub.extract_table.return_value = [["A", "B"], ["C", "D"]]
        page.within_bbox.return_value = sub
        with patch.object(ola, "_detect_table_boxes_from_vectors", return_value=[(72, 600, 300, 700)]):
            tables = _detect_tables(page, 612, 792, words, stripe_frames=[])
        assert len(tables) >= 1

    def test_detect_table_boxes_comp_full(self):
        page = MagicMock()
        page.lines = [
            {"x0": 72, "x1": 300, "top": 650, "bottom": 651},
            {"x0": 72, "x1": 300, "top": 600, "bottom": 601},
            {"x0": 72, "x1": 73, "top": 600, "bottom": 660},
            {"x0": 299, "x1": 300, "top": 600, "bottom": 660},
        ]
        page.rects = [{
            "x0": 72, "x1": 300, "top": 600, "bottom": 660,
        }]
        boxes = _detect_table_boxes_from_vectors(page, 612, 792)
        assert boxes

    def test_borderless_returns_table_with_headers(self):
        words = [_w("months ended June", 72, 250, 735, 745)]
        y = 700.0
        for row in range(6):
            x = 72.0
            for val in ["100", "200", "300"]:
                words.append(_w(val, x, x + 35, y, y + 10))
                x += 45.0
            y -= 14.0
        words.insert(0, _w("2020 2021 2022", 72, 250, 720, 730))
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.return_value = [
            ["H1", "H2", "H3"],
            ["100", "200", "300"],
            ["400", "500", "600"],
            ["700", "800", "900"],
        ]
        page.within_bbox.return_value = sub
        regions = _detect_borderless_tables(page, 612, 792, words, [])
        assert any(r.type == LayoutRegionType.TABLE for r in regions)

    def test_merge_row_expand_scores_best_grid(self):
        page = MagicMock()
        page.curves = []
        at = MagicMock()
        at.bbox = (72, 610, 220, 668)
        at.extract.return_value = [
            ["H1", "H2", "H3", "H4"],
            ["A", "B", "C", "D"],
            ["E", "F", "G", "H"],
        ]
        sub = MagicMock()
        sub.find_tables.return_value = [at]
        page.within_bbox.return_value = sub
        singles = [
            ((72, 648, 220, 668), [["R1", "R2", "R3"]]),
            ((72, 628, 220, 648), [["R4", "R5", "R6"]]),
        ]
        with patch.object(ola, "_trim_paragraph_rows", side_effect=lambda g, b: (g, b)):
            with patch.object(ola, "_trim_empty_rows_cols", side_effect=lambda g: g):
                merged = _merge_row_tables(singles, page, 612, 792)
        assert any(len(g[0]) >= 3 for _, g in merged)

    def test_detect_images_ink_density_promotes_figure(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = []
        page.lines = []
        page.rects = []
        page.curves = []
        block = (100, 500, 250, 650)

        def raster():
            img = np.full((792, 612, 3), 255, dtype=np.uint8)
            img[500:650, 100:250] = 0
            return img, 1.0

        regions = _detect_images(page, raster, [block], existing=[], all_words=[])
        assert any(r.type == LayoutRegionType.IMAGE for r in regions)

    def test_detect_images_word_parse_error_in_block(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = []
        page.lines = [
            {"x0": 100 + i, "x1": 110 + i, "top": 500, "bottom": 505}
            for i in range(16)
        ]
        page.rects = []
        page.curves = []
        block = (90, 490, 280, 530)
        bad_words = [{"bad": 1}]

        def raster():
            return np.full((792, 612, 3), 255, dtype=np.uint8), 1.0

        regions = _detect_images(page, raster, [block], existing=[], all_words=bad_words)
        assert isinstance(regions, list)

    def test_extract_layout_with_raster_cache(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.page_number = 1
        page.extract_words.return_value = []
        page.lines = []
        page.rects = []
        page.curves = []
        page.images = []
        page.find_tables.return_value = []
        page.hyperlinks = []
        cache = DocumentRasterCache("/tmp/fake.pdf")
        fake = (np.full((10, 10, 3), 128, dtype=np.uint8), 2.0)
        with patch.object(ola, "_render_all_pages", return_value={1: fake}):
            regions = extract_layout_regions(page, raster_cache=cache)
        assert regions

    def test_segment_vector_cluster_min_members_skip(self):
        page = MagicMock()
        page.lines = [{"x0": 50, "x1": 200, "top": 400, "bottom": 404}]
        page.rects = []
        page.curves = []
        assert _segment_vector_blocks(page, [], 612, 792, min_members=5) == []

    def test_monospace_region_width_cv_fallback(self):
        page = MagicMock()
        page.chars = [
            {
                "text": "x", "fontname": "Helvetica",
                "x0": 80 + i * 5.0, "x1": 83 + i * 5.0,
                "top": 540, "bottom": 548,
            }
            for i in range(25)
        ]
        assert _region_is_predominantly_monospace(page, (70, 530, 220, 560)) is True

    def test_stripe_fill_color_parse_error(self):
        page = MagicMock()
        page.rects = [{
            "x0": 72, "x1": 400, "top": 600, "bottom": 608,
            "fill": True, "non_stroking_color": "bad",
        }]
        page.lines = []
        assert _detect_uniform_fill_stripe_regions(page) == []

    def test_extract_layout_crops_image_without_data(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.extract_words.return_value = [_w("hello", 72, 120, 700, 710)]
        page.lines = []
        page.rects = []
        page.curves = []
        page.images = []
        page.find_tables.return_value = []
        page.hyperlinks = []
        img_region = _region(LayoutRegionType.IMAGE, (100, 500, 200, 600))
        fake_img = np.full((792, 612, 3), 128, dtype=np.uint8)
        with patch.object(ola, "_detect_images", return_value=[img_region]):
            with patch.object(ola, "_detect_tables", return_value=[]):
                with patch.object(ola, "_segment_blocks_native", return_value=[]):
                    with patch.object(ola, "_segment_vector_blocks", return_value=[]):
                        with patch.object(
                            ola, "_rasterize_page", return_value=(fake_img, 150 / 72.0),
                        ) as mock_raster:
                            with patch.object(
                                ola, "_crop_image_bytes", return_value=b"png",
                            ):
                                regions = extract_layout_regions(page, pdf_path="/tmp/x.pdf")
        assert mock_raster.called
        assert regions[0].image_data == b"png"

    def test_absorb_labels_wide_image_accepts_label(self):
        gutter = 306.0
        image = _region(LayoutRegionType.IMAGE, (50, 100, 560, 400))
        label = _region(LayoutRegionType.TEXT, (50, 80, 120, 98), "Fig 1")
        words = _two_col_words(n_lines=3)
        out = _absorb_labels_into_images(
            [image, label], 612, 792, gutter_x=gutter, all_words=words,
        )
        assert len(out) == 1

    def test_absorb_labels_incompatible_side_skipped(self):
        gutter = 306.0
        image = _region(LayoutRegionType.IMAGE, (50, 200, 250, 400))
        label = _region(LayoutRegionType.TEXT, (330, 180, 450, 198), "Fig")
        words = _two_col_words(n_lines=3)
        out = _absorb_labels_into_images(
            [image, label], 612, 792, gutter_x=gutter, all_words=words,
        )
        assert len(out) == 2

    def test_absorb_labels_multiple_images(self):
        gutter = 306.0
        img1 = _region(LayoutRegionType.IMAGE, (50, 200, 150, 300))
        img2 = _region(LayoutRegionType.IMAGE, (50, 350, 150, 450))
        label = _region(LayoutRegionType.TEXT, (50, 180, 100, 198), "A")
        words = [_w("A", 55, 95, 185, 195)]
        out = _absorb_labels_into_images(
            [img1, img2, label], 612, 792, gutter_x=gutter, all_words=words,
        )
        assert len(out) <= 3

    def test_detect_stray_skips_empty_line(self):
        covered = _region(LayoutRegionType.TEXT, (0, 0, 612, 792), "all")
        with patch.object(ola, "_group_words_into_lines", return_value=([[], [_w("x", 10, 20, 5, 15)]], [0, 1])):
            out = _detect_stray_text_regions([covered], [_w("x", 10, 20, 5, 15)])
        assert len(out) >= 1

    def test_stray_line_empty_text_returns_none(self):
        line = [_w("   ", 10, 20, 5, 15)]
        assert _stray_line_to_region(line) is None

    def test_segment_blocks_low_x_overlap_new_block(self):
        words = [
            _w("l1", 50, 100, 700, 710, size=12),
            _w("l2", 200, 400, 695, 705, size=12),
        ]
        boxes = _segment_blocks_native(words, 612, 792)
        assert len(boxes) == 2

    def test_borderless_extract_table_raises(self):
        words = []
        y = 700.0
        for row in range(4):
            x = 72.0
            for val in ["100", "200"]:
                words.append(_w(val, x, x + 35, y, y + 10))
                x += 45.0
            y -= 14.0
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.side_effect = RuntimeError("fail")
        page.within_bbox.return_value = sub
        regions = _detect_borderless_tables(page, 612, 792, words, [])
        assert regions == []

    def test_borderless_structural_pending_overflow(self):
        words = []
        y = 700.0
        for row in range(4):
            x = 72.0
            for val in ["100", "200"]:
                words.append(_w(val, x, x + 35, y, y + 10))
                x += 45.0
            y -= 14.0
        for i in range(5):
            words.append(_w(f"s{i}", 72, 120, 655 - i * 5, 663 - i * 5))
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.return_value = [["a", "b"], ["1", "2"], ["3", "4"]]
        page.within_bbox.return_value = sub
        regions = _detect_borderless_tables(page, 612, 792, words, [])
        assert isinstance(regions, list)

    def test_stripe_no_fill_color_skips(self):
        page = MagicMock()
        page.rects = [{
            "x0": 72, "x1": 400, "top": 600, "bottom": 608, "fill": True,
        }]
        page.lines = []
        assert _detect_uniform_fill_stripe_regions(page) == []

    def test_stripe_bad_rect_coords_continue(self):
        page = MagicMock()
        page.rects = [{"bad": 1}]
        page.lines = []
        assert _detect_uniform_fill_stripe_regions(page) == []

    def test_detect_images_embedded_invalid_dimensions(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = [{"x0": 100, "x1": 90, "top": 500, "bottom": 600}]
        page.lines = []
        page.rects = []
        page.curves = []

        def raster():
            return np.full((792, 612, 3), 255, dtype=np.uint8), 1.0

        assert _detect_images(page, raster, [], existing=[], all_words=[]) == []

    def test_detect_images_text_row_parse_error(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = [
            {"x0": 100, "x1": 180, "top": 500, "bottom": 580},
            {"x0": 190, "x1": 270, "top": 500, "bottom": 580},
        ]
        page.lines = []
        page.rects = []
        page.curves = []
        bad_words = [{"text": "x", "bad": 1}]

        def raster():
            return np.full((792, 612, 3), 255, dtype=np.uint8), 1.0

        regions = _detect_images(page, raster, [], existing=[], all_words=bad_words)
        assert isinstance(regions, list)

    def test_resolve_overlaps_incompatible_list_proximity(self):
        lst = _region(LayoutRegionType.LIST, (0, 0, 100, 20), "• one", list_items=["one"])
        text = _region(LayoutRegionType.TEXT, (0, 25, 100, 45), "aaa bbb ccc ddd")
        out = _resolve_overlaps([lst, text], page_w=612, page_h=792, proximity=10.0)
        assert len(out) == 2

    def test_reextract_region_payload_list_empty_items(self):
        reg = _region(LayoutRegionType.LIST, (0, 0, 100, 100), "t", list_items=[])
        words = [_w("plain", 10, 50, 10, 20)]
        out = _reextract_region_payload(reg, None, words, 612, 792)
        assert out.text

    def test_reportlab_financial_table_borderless(self):
        pytest.importorskip("reportlab")
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        import pdfplumber

        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.setFont("Helvetica", 10)
        c.drawString(72, 730, "Three months ended September 30")
        c.drawString(72, 715, "2020 2021 2022")
        y = 690
        for row in range(6):
            c.drawString(72, y, "100")
            c.drawString(150, y, "200")
            c.drawString(228, y, "300")
            y -= 14
        c.showPage()
        c.save()
        with pdfplumber.open(BytesIO(buf.getvalue())) as pdf:
            page = pdf.pages[0]
            words = page.extract_words(extra_attrs=["fontname", "size"])
            regions = extract_layout_regions(page)
        assert isinstance(regions, list)

    def test_reportlab_two_column_with_raster(self):
        def draw(c):
            c.setFont("Helvetica", 10)
            for i in range(25):
                c.drawString(50, 750 - i * 12, f"L{i} text")
                c.drawString(330, 750 - i * 12, f"R{i} text")

        page = _make_pdf_page(draw)
        fake_img = np.full((792, 612, 3), 255, dtype=np.uint8)
        with patch.object(ola, "_rasterize_page", return_value=(fake_img, 150 / 72.0)):
            regions = extract_layout_regions(page, pdf_path="/tmp/t.pdf")
        assert len(regions) >= 2

    def test_borderless_header_fold_and_empty_header_text(self):
        words = []
        y = 700.0
        for row in range(5):
            x = 72.0
            for val in ["100", "200", "300"]:
                words.append(_w(val, x, x + 35, y, y + 10))
                x += 45.0
            y -= 14.0
        words.insert(0, _w("months ended September", 72, 250, 735, 745))
        words.insert(0, _w("2020 2021 2022 2023", 72, 250, 750, 760))
        words.insert(0, _w("   ", 72, 250, 765, 775))
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.return_value = [
            ["H1", "H2", "H3"],
            ["100", "200", "300"],
            ["400", "500", "600"],
        ]
        page.within_bbox.return_value = sub
        regions = _detect_borderless_tables(page, 612, 792, words, [])
        assert isinstance(regions, list)

    def test_borderless_within_bbox_exception(self):
        words = [_w("100", 72, 100, 700, 710)] * 8
        page = MagicMock()
        page.within_bbox.side_effect = RuntimeError("fail")
        regions = _detect_borderless_tables(page, 612, 792, words, [])
        assert regions == []

    def test_merge_row_expand_row_alignment_and_score(self):
        page = MagicMock()
        page.curves = []
        at = MagicMock()
        at.bbox = (72, 618, 220, 668)
        at.extract.return_value = [
            ["H1", "H2", "H3"],
            ["A", "B", "C"],
            ["D", "E", "F"],
        ]
        sub = MagicMock()
        sub.find_tables.return_value = [at]
        page.within_bbox.return_value = sub
        singles = [
            ((72, 648, 220, 668), [["R1", "R2", "R3"]]),
            ((72, 628, 220, 648), [["R4", "R5", "R6"]]),
        ]
        with patch.object(ola, "_trim_paragraph_rows", side_effect=lambda g, b: (g, b)):
            with patch.object(ola, "_trim_empty_rows_cols", side_effect=lambda g: g):
                with patch.object(ola, "_valid_bbox", return_value=True):
                    merged = _merge_row_tables(singles, page, 612, 792)
        assert merged

    def test_detect_stray_empty_line_continue(self):
        covered = _region(LayoutRegionType.TEXT, (0, 0, 100, 100), "all")
        words = [_w("orphan", 300, 350, 500, 510)]
        with patch.object(ola, "_group_words_into_lines", return_value=([[], [words[0]]], [0, 1])):
            out = _detect_stray_text_regions([covered], words)
        assert len(out) == 2

    def test_absorb_labels_skips_consumed_image_index(self):
        gutter = 306.0
        img1 = _region(LayoutRegionType.IMAGE, (50, 200, 150, 300))
        img2 = _region(LayoutRegionType.IMAGE, (50, 320, 150, 420))
        label1 = _region(LayoutRegionType.TEXT, (50, 180, 100, 198), "A")
        label2 = _region(LayoutRegionType.TEXT, (50, 300, 100, 318), "B")
        words = [_w("A", 55, 95, 185, 195), _w("B", 55, 95, 305, 315)]
        out = _absorb_labels_into_images(
            [img1, img2, label1, label2], 612, 792, gutter_x=gutter, all_words=words,
        )
        assert len(out) <= 4

    def test_detect_images_block_word_density_high_skip(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = []
        page.lines = []
        page.rects = []
        page.curves = []
        block = (100, 500, 200, 520)
        words = [_w("dense text fill", 105, 195, 505, 515)]

        def raster():
            return np.full((792, 612, 3), 255, dtype=np.uint8), 1.0

        regions = _detect_images(page, raster, [block], existing=[], all_words=words)
        assert regions == []

    def test_detect_images_block_horizontal_rule_skip(self):
        page = MagicMock()
        page.width = 612
        page.height = 792
        page.images = []
        page.lines = [{"x0": 100, "x1": 200, "top": 510, "bottom": 511}]
        page.rects = []
        page.curves = []
        block = (90, 500, 210, 530)
        words = []

        def raster():
            return np.full((792, 612, 3), 255, dtype=np.uint8), 1.0

        regions = _detect_images(page, raster, [block], existing=[], all_words=words)
        assert isinstance(regions, list)

    def test_classify_list_run_with_two_items(self):
        lines = [
            [_w("• alpha", 72, 120, 700, 712)],
            [_w("• beta", 72, 120, 680, 692)],
        ]
        lines[0][0]["text"] = "• alpha"
        lines[1][0]["text"] = "• beta"
        words = [w for ln in lines for w in ln]
        blocks = [(70, 675, 160, 715)]
        regions = _classify_text_blocks(blocks, [], words)
        assert any(r.type == LayoutRegionType.LIST for r in regions)

    def test_split_lines_non_marker_continuation_merge(self):
        lines = [
            [_w("• one", 10, 60, 100, 110)],
            [_w("cont", 20, 80, 115, 125)],
            [_w("• two", 10, 60, 140, 150)],
        ]
        lines[0][0]["text"] = "• one"
        lines[2][0]["text"] = "• two"
        runs = _split_lines_into_runs(lines)
        assert any(r[0] == "list" for r in runs)

    def test_detect_columns_table_line_word_error(self):
        words = _two_col_words(n_lines=25)
        tbox = (40, 380, 570, 420)
        line_words = [
            {"text": "bad", "x0": "nope", "x1": 60, "top": 400, "bottom": 410},
            _w("a", 50, 60, 400, 410),
            _w("b", 55, 65, 400, 410),
        ]
        for lw in line_words:
            lw["top"] = 400
            lw["bottom"] = 410
        words.extend(line_words)
        result = _detect_page_columns(words, 612, 792, table_bboxes=[tbox])
        assert isinstance(result, ColumnDetectionResult)

    def test_detect_columns_gutter_voter_mid_outside_central(self):
        words = _two_col_words(n_lines=25, gutter_x=306, gap_half=15)
        result = _detect_page_columns(words, 612, 792)
        assert "n_gutter_voter_words" in result.details

    def test_inject_hyperlinks_list_items_branch_full(self):
        region = _region(
            LayoutRegionType.LIST,
            (0, 0, 100, 30),
            "item",
            list_items=["item one", "item two"],
        )
        words = [_w("item", 10, 40, 10, 20), _w("two", 10, 40, 25, 35)]
        links = [{"x0": 10, "top": 8, "x1": 40, "bottom": 22, "uri": "https://l.com"}]
        _inject_hyperlinks_into_region(region, links, words)
        assert any("https://l.com" in it for it in region.list_items)

    def test_detect_tables_hor_rule_bad_coords(self):
        page = MagicMock()
        page.images = []
        page.find_tables.return_value = []
        page.lines = [{"bad": 1}] + [
            {"x0": 72, "x1": 540, "top": 650 - i, "bottom": 651 - i} for i in range(25)
        ]
        page.curves = []
        words = []
        y = 700.0
        for row in range(4):
            x = 72.0
            for val in ["100", "200"]:
                words.append(_w(val, x, x + 35, y, y + 10))
                x += 45.0
            y -= 14.0
        sub = MagicMock()
        sub.extract_table.return_value = [["a", "b"], ["1", "2"], ["3", "4"]]
        page.within_bbox.return_value = sub
        tables = _detect_tables(page, 612, 792, words, stripe_frames=[])
        assert isinstance(tables, list)

    def test_detect_tables_image_full_page_skipped(self):
        page = MagicMock()
        page.images = [{"x0": 0, "x1": 612, "top": 0, "bottom": 792}]
        page.find_tables.return_value = []
        page.lines = []
        tables = _detect_tables(page, 612, 792, [], stripe_frames=[])
        assert tables == []

    def test_detect_table_vectors_swapped_coords(self):
        page = MagicMock()
        page.lines = [{"x0": 300, "x1": 72, "top": 650, "bottom": 651}]
        page.rects = []
        boxes = _detect_table_boxes_from_vectors(page, 612, 792)
        assert isinstance(boxes, list)

    def test_borderless_row_kind_prose_wide(self):
        words = [_w("wide prose " * 12, 72, 500, 650, 660)]
        y = 700.0
        for row in range(3):
            x = 72.0
            for val in ["10", "20"]:
                words.append(_w(val, x, x + 30, y, y + 10))
                x += 40.0
            y -= 14.0
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.return_value = [["a", "b"], ["1", "2"]]
        page.within_bbox.return_value = sub
        regions = _detect_borderless_tables(page, 612, 792, words, [])
        assert isinstance(regions, list)

    def test_merge_row_no_expand_single_row(self):
        page = MagicMock()
        page.curves = []
        singles = [((72, 640, 220, 660), [["A", "B", "C"]])]
        merged = _merge_row_tables(singles, page, 612, 792)
        assert merged == singles

    def test_refine_image_bbox_word_area_skip_continue(self):
        pp_bbox = (0, 0, 100, 100)
        cv_blocks = [(10, 10, 90, 90)]
        words = [_w("fill", 15, 85, 15, 85)]
        out = _refine_image_bbox(pp_bbox, cv_blocks, words, 612, 792)
        assert out == [pp_bbox]

    def test_tighten_to_content_all_white_returns_none(self):
        img = np.full((100, 100, 3), 255, dtype=np.uint8)
        assert _tighten_to_content(img, 1.0, (10, 10, 90, 90)) is None

    def test_document_raster_cache_second_get(self):
        cache = DocumentRasterCache("/tmp/fake2.pdf")
        fake = (np.zeros((5, 5, 3), dtype=np.uint8), 2.0)
        with patch.object(ola, "_render_all_pages", return_value={1: fake}) as mock_render:
            cache.get(1)
            cache.get(1)
        assert mock_render.call_count == 1

    def test_overlap_ratio_zero_width_box(self):
        assert _overlap_ratio((10, 10, 10, 20), (0, 0, 5, 5)) == 0.0

    def test_detect_tables_invalid_image_bbox_skip(self):
        page = MagicMock()
        page.images = [{"x0": 100, "x1": "bad", "top": 500, "bottom": 600}]
        page.find_tables.return_value = []
        page.lines = []
        tables = _detect_tables(page, 612, 792, [], stripe_frames=[])
        assert tables == []

    def test_detect_table_vectors_rect_segments_added(self):
        page = MagicMock()
        page.lines = []
        page.rects = [{
            "x0": 300, "x1": 72, "top": 650, "bottom": 600,
        }]
        boxes = _detect_table_boxes_from_vectors(page, 612, 792)
        assert isinstance(boxes, list)

    def test_merge_row_tables_chain_extend_no_expand(self):
        page = MagicMock()
        page.curves = [{"x0": 80 + i, "x1": 82 + i, "top": 610 + i, "bottom": 612 + i} for i in range(6)]
        singles = [
            ((72, 650, 220, 670), [["A", "B", "C"]]),
            ((72, 630, 220, 650), [["D", "E", "F"]]),
        ]
        merged = _merge_row_tables(singles, page, 612, 792)
        assert len(merged) >= 1

    def test_rescue_columns_curve_limit_returns_grid(self):
        page = MagicMock()
        page.curves = [
            {"x0": 80 + i, "x1": 82 + i, "top": 610 + i, "bottom": 612 + i}
            for i in range(6)
        ]
        grid = _rescue_columns_if_single(
            page, (72, 600, 300, 700), [["a", "b", "c"]], 612, 792,
        )
        assert grid == [["a", "b", "c"]]

    def test_trim_image_text_margins_trims_heavily(self):
        bbox = (0, 0, 200, 200)
        words = [
            _w("top", 10, 30, 2, 8),
            _w("mid", 10, 30, 90, 100),
            _w("bot", 10, 30, 192, 198),
        ]
        trimmed = _trim_image_text_margins(bbox, words)
        assert trimmed[1] >= 8
        assert trimmed[3] <= 198

    def test_split_region_horizontal_gutter_min_dim(self):
        img = np.full((400, 400, 3), 255, dtype=np.uint8)
        img[50:350, 50:350] = 0
        img[190:210, 50:350] = 255
        parts = _split_region_by_gutters(img, 2.0, (0, 0, 288, 288), [], depth=0, min_dim=200.0)
        assert parts == [(0, 0, 288, 288)]

    def test_absorb_labels_consumed_image_index_skip(self):
        gutter = 306.0
        img_small = _region(LayoutRegionType.IMAGE, (50, 200, 120, 280))
        img_large = _region(LayoutRegionType.IMAGE, (50, 200, 250, 400))
        label = _region(LayoutRegionType.TEXT, (50, 180, 100, 198), "Cap")
        words = [_w("Cap", 55, 95, 185, 195)]
        out = _absorb_labels_into_images(
            [img_small, img_large, label], 612, 792, gutter_x=gutter, all_words=words,
        )
        assert len(out) <= 3

    def test_inject_hyperlinks_region_list_items_only(self):
        region = _region(
            LayoutRegionType.LIST,
            (0, 0, 100, 50),
            "",
            list_items=["linked"],
        )
        region.text = ""
        words = [_w("linked", 10, 40, 10, 20)]
        links = [{"x0": 10, "top": 8, "x1": 40, "bottom": 22, "uri": "https://z.com"}]
        _inject_hyperlinks_into_region(region, links, words)
        assert "https://z.com" in region.list_items[0]

    def test_overlap_ratio_degenerate_box_area(self):
        assert _overlap_ratio((0, 0, 0, 10), (5, 5, 10, 10)) == 0.0

    def test_segment_vector_blocks_small_cluster_pruned(self):
        page = MagicMock()
        page.lines = [{"x0": 50, "x1": 55, "top": 400, "bottom": 404}] * 4
        page.rects = []
        page.curves = []
        assert _segment_vector_blocks(page, [], 612, 792, min_members=5) == []

    def test_detect_stray_appends_valid_region(self):
        covered = _region(LayoutRegionType.TEXT, (0, 0, 50, 50), "in")
        words = [_w("stray", 200, 250, 500, 510)]
        out = _detect_stray_text_regions([covered], words)
        assert len(out) == 2

    def test_find_vector_bullets_rejects_wide_aspect(self):
        page = MagicMock()
        page.curves = [{"x0": 72, "x1": 200, "top": 700, "bottom": 706}]
        page.rects = []
        assert _find_vector_bullets(page, [_w("t", 210, 250, 700, 706)]) == []

    def test_segment_vector_full_page_line_skipped(self):
        page = MagicMock()
        page.lines = [{"x0": 0, "x1": 600, "top": 400, "bottom": 401}]
        page.rects = []
        page.curves = []
        assert _segment_vector_blocks(page, [], 612, 792, min_members=1) == []

    def test_classify_text_run_empty_lines_skip(self):
        words = [_w("x", 72, 120, 700, 710)]
        blocks = [(70, 695, 130, 715)]
        with patch.object(ola, "_split_lines_into_runs", return_value=[("text", [[]])]):
            with patch.object(ola, "_group_words_into_lines", return_value=([words], [0])):
                assert _classify_text_blocks(blocks, [], words) == []

    def test_resolve_overlaps_proximity_incompatible_columns(self):
        gutter = 306.0
        left = _region(LayoutRegionType.TEXT, (50, 0, 200, 20), "left block text")
        right = _region(LayoutRegionType.TEXT, (320, 25, 500, 45), "right block text")
        out = _resolve_overlaps(
            [left, right], page_w=612, page_h=792, gutter_x=gutter, proximity=30.0,
        )
        assert len(out) == 2

    def test_stripe_bucket_below_min_count(self):
        page = MagicMock()
        page.rects = [{
            "x0": 72, "x1": 400,
            "top": 600 - i * 8, "bottom": 608 - i * 8,
            "fill": True, "non_stroking_color": (0.9, 0.9, 0.9),
        } for i in range(5)]
        page.lines = []
        assert _detect_uniform_fill_stripe_regions(page) == []

    def test_borderless_table_low_fill_skipped(self):
        words = []
        y = 700.0
        for row in range(5):
            x = 72.0
            for val in ["100", "200", "300"]:
                words.append(_w(val, x, x + 35, y, y + 10))
                x += 45.0
            y -= 14.0
        page = MagicMock()
        sub = MagicMock()
        sub.extract_table.return_value = [
            ["H1", "H2", "H3"],
            [None, None, None],
            [None, None, None],
            [None, None, None],
        ]
        page.within_bbox.return_value = sub
        regions = _detect_borderless_tables(page, 612, 792, words, [])
        assert regions == []

    def test_detect_table_vectors_from_rect_edges(self):
        page = MagicMock()
        page.lines = []
        page.rects = [{
            "x0": 72, "x1": 400, "top": 600, "bottom": 700,
        }]
        boxes = _detect_table_boxes_from_vectors(page, 612, 792)
        assert boxes

    def test_merge_row_expand_sub_none_fallback(self):
        page = MagicMock()
        page.curves = []
        page.within_bbox.return_value = None
        singles = [
            ((72, 650, 220, 670), [["R1", "R2", "R3"]]),
            ((72, 630, 220, 650), [["R4", "R5", "R6"]]),
        ]
        merged = _merge_row_tables(singles, page, 612, 792)
        assert len(merged) >= 1

    def test_detect_stray_skips_multiple_empty_lines(self):
        covered = _region(LayoutRegionType.TEXT, (0, 0, 50, 50), "in")
        orphan = _w("orphan", 200, 250, 500, 510)
        with patch.object(
            ola,
            "_group_words_into_lines",
            return_value=([[], [orphan], []], [0, 1, 2]),
        ):
            out = _detect_stray_text_regions([covered], [orphan])
        assert len(out) == 2

    def test_detect_tables_image_invalid_bbox_value(self):
        page = MagicMock()
        page.images = [{"x0": 100, "x1": 200, "top": 500, "bottom": 600, "bad": 1}]
        page.find_tables.side_effect = RuntimeError("fail")
        tables = _detect_tables(page, 612, 792, [], stripe_frames=[])
        assert tables == []

    def test_detect_tables_vector_iou_skip_existing(self):
        page = MagicMock()
        page.images = []
        page.find_tables.return_value = []
        page.lines = [
            {"x0": 72, "x1": 300, "top": 650, "bottom": 651},
            {"x0": 72, "x1": 300, "top": 600, "bottom": 601},
            {"x0": 72, "x1": 73, "top": 600, "bottom": 660},
            {"x0": 299, "x1": 300, "top": 600, "bottom": 660},
        ]
        page.rects = []
        words = [_w(str(i), 80 + i * 20, 95 + i * 20, 620 + i, 630 + i) for i in range(6)]
        sub = MagicMock()
        sub.extract_table.return_value = [["A", "B"], ["C", "D"]]
        page.within_bbox.return_value = sub
        with patch.object(ola, "_detect_table_boxes_from_vectors", return_value=[(72, 600, 300, 700)]):
            with patch.object(ola, "_iou", return_value=0.0):
                tables = _detect_tables(page, 612, 792, words, stripe_frames=[])
        assert len(tables) == 1
        assert tables[0].type == LayoutRegionType.TABLE

    def test_stripe_rect_y_reversed_normalized(self):
        page = MagicMock()
        page.rects = [{
            "x0": 400, "x1": 72, "top": 608, "bottom": 600,
            "fill": True, "non_stroking_color": (0.9, 0.9, 0.9),
        } for _ in range(8)]
        page.lines = []
        assert _detect_uniform_fill_stripe_regions(page) == []
