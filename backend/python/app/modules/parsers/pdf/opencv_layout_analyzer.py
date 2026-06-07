"""
Layout region extraction from a pdfplumber Page.

Handles single-column and two-column page layouts. When the page is
detected to have two columns, line-grouping, block-segmentation, and
reading order all become column-aware so that:

  * Words on opposite sides of the gutter are never merged into one
    line (unless they form a genuine full-width run with uniform spacing,
    e.g. a heading).
  * A wide heading/table/figure cannot swallow column blocks via the
    open-blocks attachment heuristic.
  * Reading order is left column top-to-bottom, then right column,
    with full-width elements interleaved at their y position.

Single-column pages take exactly the same path as before -- the new
machinery is gated on a `gutter_x` that is `None` unless column
detection actually finds two columns.
"""

import io
import re
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Tuple, Optional, Dict, Any, Callable

import cv2
import numpy as np
from PIL import Image


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #

class LayoutRegionType(str, Enum):
    TEXT = "text"
    TABLE = "table"
    IMAGE = "image"
    LIST = "list"


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
    list_items: List[str] = field(default_factory=list)


@dataclass
class ColumnDetectionResult:
    """Outcome of page-column detection.

    A non-``None`` ``gutter_x`` indicates the page was classified as
    two-column with the gutter at that x. ``confidence`` is a heuristic
    score in ``[0, 1]`` derived from voting strength, vertical
    distribution of supporting lines, and side-population balance.

    ``details`` carries diagnostic fields (vote counts, peak metrics,
    rejection reason) intended for debugging and tests; production code
    should not branch on its contents. The dataclass is the function's
    return type rather than a bare ``Optional[float]`` so callers can
    introspect confidence and rejection reasons without re-running the
    detector. Existing callers extract ``.gutter_x`` and keep
    behaviour identical.
    """
    gutter_x: Optional[float]
    confidence: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

_DPI = 150

_PRIORITY = {
    LayoutRegionType.IMAGE: 0,
    LayoutRegionType.TABLE: 1,
    LayoutRegionType.LIST: 2,
    LayoutRegionType.TEXT: 3,
}

_PROXIMITY_MERGE_GAP = 2.0

# Maximum bbox edge-to-edge distance (in PDF points) at which the
# same-column-side (gutter) constraint may be relaxed during merging.
# Relaxation applies only when one region is TABLE and the other is TEXT
# and their edge-to-edge gap is at most this value -- e.g. column body
# text whose bbox barely touches a wide table on the other side of the
# gutter. All other type pairs still require matching column side.
# Applies to ``_resolve_overlaps`` (overlap + proximity merging).
_GUTTER_RELAX_GAP = 2.0


def _gutter_relax_merge_pair(
    r: LayoutRegion,
    a: LayoutRegion,
    gap: Optional[float],
) -> bool:
    """True when a near-touching pair may ignore the column-side gate."""
    if gap is None or gap > _GUTTER_RELAX_GAP:
        return False
    types = {r.type, a.type}
    return (
        LayoutRegionType.TABLE in types
        and LayoutRegionType.TEXT in types
    )


# Maximum bbox area (PDF sq points) the non-TABLE side may have in order
# to be eligible for proximity merging with a TABLE region. Prevents a
# table from absorbing -- or being absorbed into -- a substantial
# neighbouring TEXT/IMAGE/LIST block whose bbox merely came within
# ``_PROXIMITY_MERGE_GAP`` of the table. Two near-touching tables, or
# two near-touching non-tables, are unaffected by this gate. Applies to
# the proximity branch of ``_resolve_overlaps``; the overlap branch
# applies the same area gate but only when the overlap is sub-threshold
# (see ``_TABLE_OVERLAP_MIN_RATIO`` /
# ``_table_overlap_with_large_neighbor_allowed``) so genuine duplicate
# detections (one region mostly inside the other) still merge.
_TABLE_PROXIMITY_NEIGHBOR_MAX_AREA = 2000.0


def _table_proximity_neighbor_allowed(
    r: LayoutRegion, a: LayoutRegion
) -> bool:
    """Gate proximity merges between TABLE and non-TABLE regions.

    Returns ``False`` only when exactly one of ``r``/``a`` is a TABLE and
    the other's bbox area exceeds ``_TABLE_PROXIMITY_NEIGHBOR_MAX_AREA``.
    All other pairings (table+table, non-table+non-table, or table+small
    non-table) return ``True`` and are unaffected by this gate.
    """
    r_is_table = r.type == LayoutRegionType.TABLE
    a_is_table = a.type == LayoutRegionType.TABLE
    if r_is_table == a_is_table:
        return True
    # Header-shape bypass: a short TEXT region flush against the top of
    # the table and horizontally contained within it folds in even when
    # its bbox area exceeds the neighbour cap.
    if _text_is_table_top_header(r, a):
        return True
    non_table = a if r_is_table else r
    x0, y0, x1, y1 = non_table.bbox
    area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    return area <= _TABLE_PROXIMITY_NEIGHBOR_MAX_AREA


# Minimum mutual overlap ratio required for a TABLE to merge with a
# substantial non-TABLE neighbour via the overlap branch of
# ``_resolve_overlaps``. Below this floor the overlap is treated as bbox
# jitter at a shared edge -- e.g. a column of body text whose y1 dips a
# couple of points past the top of the table below it across the table's
# full horizontal span, yielding an overlap ratio of ~1-2% from either
# perspective -- rather than as a duplicate detection. Genuine duplicate
# detections (one region mostly inside the other) sit well above this
# threshold and continue to merge. The check is symmetric: it is enough
# for either side's ratio to cross the floor.
_TABLE_OVERLAP_MIN_RATIO = 0.1


def _table_overlap_with_large_neighbor_allowed(
    r: LayoutRegion, a: LayoutRegion
) -> bool:
    """Gate overlap-branch merges between a TABLE and a large non-TABLE
    neighbour.

    Returns ``False`` only when exactly one of ``r``/``a`` is a TABLE,
    the other's bbox area exceeds ``_TABLE_PROXIMITY_NEIGHBOR_MAX_AREA``,
    and the mutual overlap is below ``_TABLE_OVERLAP_MIN_RATIO`` from
    both perspectives. In that case the overlap is treated as bbox
    jitter at a shared edge rather than a duplicate detection, and the
    regions are kept as siblings.

    All other pairings (table+table, non-table+non-table, table+small
    non-table) and pairings with substantial mutual overlap return
    ``True`` and are unaffected by this gate. Mirrors the area-based
    rationale of ``_table_proximity_neighbor_allowed`` for the overlap
    branch.
    """
    r_is_table = r.type == LayoutRegionType.TABLE
    a_is_table = a.type == LayoutRegionType.TABLE
    if r_is_table == a_is_table:
        return True
    # Header-shape bypass: a short TEXT region flush against the top of
    # the table and horizontally contained within it folds in even when
    # its bbox area exceeds the neighbour cap and the overlap with the
    # table is sub-threshold -- which is precisely the situation a
    # missed multi-row column header produces (bbox-jitter overlap at
    # the table's top edge).
    if _text_is_table_top_header(r, a):
        return True
    non_table = a if r_is_table else r
    nx0, ny0, nx1, ny1 = non_table.bbox
    area = max(0.0, nx1 - nx0) * max(0.0, ny1 - ny0)
    if area <= _TABLE_PROXIMITY_NEIGHBOR_MAX_AREA:
        return True
    return max(
        _overlap_ratio(r.bbox, a.bbox),
        _overlap_ratio(a.bbox, r.bbox),
    ) >= _TABLE_OVERLAP_MIN_RATIO


# Minimum fraction of a TABLE that must lie inside a TEXT region for the
# normal type-priority (TABLE > TEXT) to be inverted during overlap
# merging. When this threshold is met, the TABLE is treated as a false
# positive sitting inside a larger text block: the TEXT region becomes
# the merge primary, the merged result is TEXT-typed, and the table_grid
# is discarded. Set generously (0.9) so bbox jitter / small overhangs do
# not prevent the inversion, while partial overlaps that represent real
# layout adjacency (table next to body text) still take the normal path.
# Applies only to the overlap branch of ``_resolve_overlaps``; proximity
# merges never satisfy a containment ratio by construction.
_TABLE_IN_TEXT_CONTAINMENT_RATIO = 0.9


def _table_contained_in_text(
    table: LayoutRegion, text: LayoutRegion
) -> bool:
    """True when ``table`` is mostly enclosed by ``text``.

    Returns ``False`` for any pairing other than (TABLE, TEXT). When
    ``True``, ``_resolve_overlaps`` flips the merge primary so the
    output region carries TEXT semantics rather than absorbing the text
    into the table.
    """
    if (table.type != LayoutRegionType.TABLE
            or text.type != LayoutRegionType.TEXT):
        return False
    return (
        _overlap_ratio(table.bbox, text.bbox)
        >= _TABLE_IN_TEXT_CONTAINMENT_RATIO
    )


# Header-attach relaxation: parameters identifying a TEXT region that
# sits flush against the top of a TABLE and is shaped like a missed
# column header (e.g. group/period bands such as "Three Months Ended
# October 31," that span multiple data-row columns and therefore aren't
# picked up by pdfplumber's grid detection). Such regions should fold
# back into the table even though the area-based gates would otherwise
# reject the merge: the header's bbox area typically exceeds
# ``_TABLE_PROXIMITY_NEIGHBOR_MAX_AREA`` while its overlap with the
# table is only bbox-jitter (sub ``_TABLE_OVERLAP_MIN_RATIO``).
#
# The relaxation is intentionally narrow -- it bypasses only the
# area+overlap gates, only for (TABLE, TEXT) pairs, and only when the
# text region looks geometrically like an attached header (above, flush,
# horizontally contained, short). Titles separated from the table by
# meaningful whitespace, footnotes/legends below the table, paragraphs
# wider than the table, and tall multi-line bodies are all rejected by
# at least one of the four geometric conditions.
#
# Vertical: |table.y0 - text.y1| <= TOLERANCE captures both tiny
# bbox-jitter overlaps (text bottom just past table top) and gap touches
# within proximity. Set to a few points so a header band visually
# touching the table top edge is admitted, while a section title
# separated by even modest whitespace (~5pt+) is not.
_HEADER_ATTACH_VERTICAL_TOLERANCE = 4.0
# Horizontal: the text x-range must be inside the table x-range within
# this margin. Wider-than-table text is a paragraph/caption, not a
# header.
_HEADER_ATTACH_X_MARGIN = 2.0
# Height cap: a few lines of typical 9-10pt body text. Taller blocks
# (titles + subtitles, paragraphs) are excluded even if they happen to
# sit flush against the table.
_HEADER_ATTACH_MAX_HEIGHT = 40.0


def _text_is_table_top_header(
    r: LayoutRegion, a: LayoutRegion
) -> bool:
    """True when one of ``r`` / ``a`` is a TEXT region sitting flush
    against the top of the other's TABLE bbox and shaped like a column
    header.

    Used as a bypass inside the area-based gates
    (``_table_proximity_neighbor_allowed`` and
    ``_table_overlap_with_large_neighbor_allowed``) so that genuine
    column headers split off by detection -- e.g. group/period bands on
    financial statements that don't align with the data-row column grid
    -- are folded back into the table even when the header's bbox area
    exceeds ``_TABLE_PROXIMITY_NEIGHBOR_MAX_AREA``.

    Returns ``False`` for any pairing other than (TABLE, TEXT), for TEXT
    regions sitting below or beside the table (footnote / legend
    territory), for TEXT regions whose x-range escapes the table
    horizontally (paragraph or full-width caption), and for tall TEXT
    regions (multi-line body / stacked title).
    """
    if r.type == LayoutRegionType.TABLE and a.type == LayoutRegionType.TEXT:
        table, text = r, a
    elif a.type == LayoutRegionType.TABLE and r.type == LayoutRegionType.TEXT:
        table, text = a, r
    else:
        return False
    tx0, ty0, tx1, ty1 = text.bbox
    bx0, by0, bx1, by1 = table.bbox
    # Must originate above the table top -- excludes footnotes / legends
    # whose bbox happens to nick the table at the bottom edge.
    if ty0 >= by0:
        return False
    # Text bottom must lie within bbox-jitter distance of the table's
    # top edge. Handles both small overlaps (text.y1 slightly past
    # table.y0) and small gaps (text.y1 just short of table.y0).
    if abs(by0 - ty1) > _HEADER_ATTACH_VERTICAL_TOLERANCE:
        return False
    # x-range must be contained in the table's x-range. Wider text is
    # a paragraph above the table, not a column header.
    if tx0 < bx0 - _HEADER_ATTACH_X_MARGIN:
        return False
    if tx1 > bx1 + _HEADER_ATTACH_X_MARGIN:
        return False
    # Short height only -- multi-row headers up to ~3-4 lines are
    # accepted; taller blocks are body text or stacked titles.
    if (ty1 - ty0) > _HEADER_ATTACH_MAX_HEIGHT:
        return False
    return True


# A TEXT region with fewer than this many whitespace-separated tokens is
# eligible to be absorbed into a closer adjacent TEXT region in reading
# order. Tune via the ``max_small_words`` parameter of
# ``_merge_small_text_regions`` if a different policy is needed per page.
_SMALL_TEXT_WORD_THRESHOLD = 5

# Maximum bbox edge-to-edge distance (in PDF points) at which a small
# TEXT region is allowed to merge with a neighbor. Beyond this, the
# small region is left as its own block even if it has no closer
# TEXT neighbor. Set to ``None`` (or pass ``max_gap=None``) to disable
# the cap entirely.
_SMALL_TEXT_MAX_MERGE_GAP: Optional[float] = 20.0


# --------------------------------------------------------------------------- #
# Column-detection configuration
#
# All tunables for ``_detect_page_columns`` live here so that the
# algorithm's policy choices stay separable from its mechanics. Each
# value was picked to reject realistic false positives (table rows
# without an enclosing table bbox, sidebars, short notes) while still
# accepting genuine two-column body text on typical academic/letter
# layouts. Where a constant is paired with a second one (e.g. min/max
# gutter width), both are listed together.
# --------------------------------------------------------------------------- #

# Minimum number of valid words on a page below which column detection
# is skipped entirely. The statistical signals (peak voting, y-spread,
# side balance) become too noisy on near-empty pages.
_COL_MIN_WORDS = 20

# Minimum number of *middle-spanning* raw lines required before any
# voting tally is even considered. Spanning lines are those that extend
# horizontally to both sides of the page midpoint -- only they can host
# a candidate gutter. Below this many spanners, a peak can be assembled
# by accident.
_COL_MIN_SPANNING_LINES = 5

# Number of significant internal gaps a single line may have before it
# is classified as a tabular row and excluded from voting. Prose lines
# essentially never carry 3+ large internal gaps; rows of a table
# almost always do.
_COL_TABULAR_GAP_COUNT = 3

# Threshold (PDF points) above which an internal line gap is counted
# as "significant" for tabular-row self-detection. Slightly above the
# floor used for gutter candidates because justified prose can yield
# 6-8 pt intra-line gaps that should not count as tabular structure.
_COL_TABULAR_MIN_GAP = 8.0

# Bin size (PDF points) for the gap-position coverage histogram.
_COL_BIN_SIZE = 4.0

# Min/max gap width (PDF points) for a line gap to be a plausible
# gutter candidate. Below the min, the "gap" is intra-line whitespace;
# above the max, it is blank-space layout rather than a true gutter.
#
# Tuned to 8.0 (matching ``_COL_TABULAR_MIN_GAP``) after observing that
# typesetters routinely produce inter-column gutters in the 9-10 pt
# range on academic two-column layouts. The previous 10.0 floor
# rejected gutters that came in fractionally narrow (e.g. 9.96 pt on
# justified two-column body text) and cascaded into a single-region
# page. The tabular-row defense at line level (``_is_tabular_row``,
# 3+ significant gaps) is unaffected -- it counts gaps, not their
# width.
_COL_MIN_GUTTER_WIDTH = 8.0
_COL_MAX_GUTTER_WIDTH = 80.0

# Allowed central x-range of the page for a candidate gutter midpoint,
# expressed as fractions of page width. Slightly wider than the
# previous 0.25-0.75 to accommodate asymmetric two-column layouts
# (e.g. wider main column with a narrower secondary column).
_COL_CENTRAL_RANGE = (0.20, 0.80)

# Fraction of the peak voter count that adjacent bins must reach to
# join the peak plateau. Used to identify "peak supporters" (voters
# whose vote landed in the plateau) for the y-distribution signal in
# Stage 6. No longer used to estimate the gutter's width or centre --
# see ``_COL_PEAK_CORE_FRACTION``.
_COL_PLATEAU_FRACTION = 0.70

# Fraction of the peak voter count that adjacent bins must reach to
# count as part of the peak's "sharp core". Used to estimate the
# gutter's width and centre in Stage 5.
#
# Rationale: a line's vote in Stage 4 is cast on every bin its gap
# spans, so a line with a wide central gap (e.g. a short code line
# in the left column paired with prose in the right) drags the 70 %
# plateau far beyond the true gutter -- even when every spanning
# line agrees on where the gutter sits. The 70 % plateau therefore
# conflates "diffuse / multi-peak histogram" (legitimate reject)
# with "sharp peak whose shoulders are inflated by wide-gap voting"
# (false reject). A higher fraction isolates the sharp core where
# coverage is essentially at the peak, giving a width measure that
# is robust to vote-spreading and a centre that lands on the true
# gutter rather than the midpoint of the inflated plateau.
#
# Tuned to 0.95 to allow 1-bin off-by-one tolerance at typical peak
# magnitudes (~30-40 voters) while still rejecting genuinely flat
# ridges. For small peaks the off-by-one floor ``peak - 1`` may bind
# instead; see the ``min(peak - 1, _COL_PEAK_CORE_FRACTION * peak)``
# threshold in Stage 5.
_COL_PEAK_CORE_FRACTION = 0.95

# Absolute floor on the peak voter count and on the peak/spanner
# ratio. Both must be cleared.
_COL_MIN_PEAK_VOTES = 5
_COL_MIN_PEAK_RATIO = 0.40

# Voting-word fraction: among lines that span the *candidate* gutter
# (computed in Stage 6 after the gutter is discovered), what share of
# their words sit in lines that actually voted for a gap there. This is
# the primary defense against the "undetected table rows synthesize a
# gutter" failure mode. A real two-column body has every gutter-spanning
# line voting (fraction = 1.0). A table band has only the table rows
# voting; body rows from outside the table that span past the candidate
# gutter contribute words to the denominator but no vote, so the
# fraction drops well below this floor.
#
# Earlier revisions computed the fraction against lines spanning the
# page midpoint instead of the candidate gutter. That broke on
# asymmetric layouts where one column is wider than half the page
# (e.g. resumes with a wide left column and narrow sidebar): pure
# left-column lines whose words extended just past mid_page padded
# the denominator without voting, pulling the fraction below 0.80
# even when every line that genuinely spans the gutter voted.
#
# Tuned to 0.80 so a 40-row table over a tiny body still rejects.
_COL_MIN_VOTING_WORD_FRACTION = 0.80

# voter_text_coverage, side_balance, and n_thirds are recorded for
# diagnostics and contribute (only) softly to the confidence score.
# They are NOT hard rejects: each one fails on legitimate two-column
# pages where one column ends earlier than the other (e.g.
# references on the right are shorter than body on the left -- the
# academic-paper-final-page pattern). Earlier revisions tried them
# as hard gates and rejected valid asymmetric layouts. The
# voting_word_fraction check above distinguishes real gutters from
# table bands without depending on column-height symmetry.

# Lower bound on the combined confidence score below which the
# detection is reported as ``None``. The score itself is still
# returned in the result for diagnostics.
_COL_MIN_CONFIDENCE = 0.50

# --------------------------------------------------------------------------- #
# Projection-based rescue configuration
#
# When ``_detect_page_columns`` is about to hard-reject a candidate
# gutter (voting_word_fraction or confidence gate), a vertical
# ink-projection profile on the rasterized page can rescue it if the
# candidate sits in a clear, low-ink valley. This catches cases where
# the text-side signal is weakened by extraction quirks (justified
# spacing, narrow gutters) but the page is visually unambiguous.
#
# Rescue runs only when:
#   * a ``raster_getter`` callable is provided by the caller,
#   * a candidate ``gutter_x`` was discovered (Stage 5 succeeded),
#   * ``confidence >= _COL_RESCUE_MIN_CONFIDENCE`` -- we don't try to
#     rescue candidates that the text-side signal already considers
#     near-garbage.
#
# All three projection thresholds below are conservative: they let
# through clear visual gutters and reject everything else. They are
# not a substitute for the text-side checks; rescue tightens the
# accept set, it never widens it past what a clear projection shows.
# --------------------------------------------------------------------------- #

# Minimum text-side confidence at which a hard-rejected candidate is
# eligible for projection rescue. Below this the text-side signal is
# too weak to trust the candidate gutter position in the first place.
_COL_RESCUE_MIN_CONFIDENCE = 0.60

# Maximum ratio of (smoothed ink at the candidate gutter) to
# (reference column ink) for the rescue to accept. A typical
# two-column body has a near-zero-ink gutter against text columns
# carrying 50-200 px of ink per column at 150 DPI, so the ratio at a
# real gutter is usually well below 0.05. The 0.10 cap leaves headroom
# for the occasional cross-gutter heading or rule that puts a small
# amount of ink in the gutter band.
_COL_RESCUE_INK_RATIO = 0.10

# Absolute floor on the reference column ink (in pixels, post-smoothing)
# below which the projection signal is considered unreliable. A
# near-empty page can have a "zero-ink gutter" against equally-zero
# columns, which is meaningless. Tuned for 150 DPI; if ``_DPI`` is
# changed substantially this should be scaled in proportion.
_COL_RESCUE_MIN_REFERENCE_INK = 20.0


_LIST_PATTERN = re.compile(
    r'^\s*(?:'
    r'[\u2022\-\*\u2013\u2014\u2023\u2043\u2217\u25aa\u25cf\u25a0\u25c6\u25ba\u2192\u00b7]'
    r'|\(\s*cid\s*:\s*\d+\s*\)'
    r'|\(\s*\d+\s*\)|\d+[.)]'
    r'|\(\s*[a-zA-Z]\s*\)|[a-zA-Z][.)]'
    r'|[ivxlcdmIVXLCDM]+[.)]'
    r')\s+'
)


def _validate_gutter_with_projection(
    raster_getter: Callable[[], Tuple[np.ndarray, float]],
    gutter_x_pts: float,
    gutter_width_pts: float,
    page_w: float,
) -> Dict[str, Any]:
    """Validate a candidate gutter via vertical ink-projection profile.

    Returns a diagnostics dict. ``accepted`` is True when the smoothed
    ink at the candidate gutter drops to at most
    ``_COL_RESCUE_INK_RATIO`` of the reference column ink (median
    column ink in the central 20-80% band, excluding the candidate
    window itself).

    This is the rescue path for candidates the text-side detector
    would otherwise hard-reject; see ``_COL_RESCUE_*`` for tunables.
    The function never raises -- raster or threshold failures return
    ``accepted=False`` with a reason. Caller is responsible for only
    invoking this when ``raster_getter`` is non-None.
    """
    try:
        img_rgb, scale = raster_getter()
    except Exception as e:  # pragma: no cover -- defensive
        return {"accepted": False, "reason": f"raster failed: {e!r}"}
    if img_rgb is None or img_rgb.size == 0:
        return {"accepted": False, "reason": "raster empty"}

    bw = _binarize_foreground(img_rgb)
    H, W = bw.shape
    if W < 10 or H < 10:
        return {"accepted": False, "reason": "raster too small"}

    # Vertical projection: count foreground pixels per column.
    col_ink = (bw > 0).sum(axis=0).astype(np.float64)
    # Light box smoothing suppresses 1-2 px kerning jitter without
    # widening the gutter valley enough to mask a real narrow gutter.
    kernel_n = 5
    kernel = np.ones(kernel_n) / float(kernel_n)
    col_smooth = np.convolve(col_ink, kernel, mode="same")

    gutter_x_px = int(round(gutter_x_pts * scale))
    if gutter_x_px < 0 or gutter_x_px >= W:
        return {"accepted": False, "reason": "gutter x outside raster"}

    # Test window centered on the candidate, sized to half the
    # estimated gutter width on each side. The minimum within this
    # window is what the projection considers the "gutter floor"; the
    # candidate gutter doesn't have to land exactly on the global
    # argmin, just inside a sufficiently low patch.
    half_gw_px = max(2, int(round((gutter_width_pts / 2.0) * scale)))
    lo = max(0, gutter_x_px - half_gw_px)
    hi = min(W, gutter_x_px + half_gw_px + 1)
    if hi - lo < 3:
        return {"accepted": False, "reason": "gutter window too narrow"}
    min_in_window = float(col_smooth[lo:hi].min())

    # Reference ink: median of column ink in the central 20-80% band,
    # excluding the test window. Median is robust to outliers (a
    # single super-dense column from descender alignment, a vertical
    # rule, etc.) and to the gutter valley itself sneaking into the
    # pool. The central band excludes the page margins, which would
    # otherwise drag the median down toward zero.
    central_lo = int(0.20 * W)
    central_hi = int(0.80 * W)
    mask = np.zeros(W, dtype=bool)
    mask[central_lo:central_hi] = True
    mask[lo:hi] = False
    pool = col_smooth[mask]
    if pool.size == 0:
        return {"accepted": False, "reason": "no reference columns"}
    reference_ink = float(np.median(pool))

    if reference_ink < _COL_RESCUE_MIN_REFERENCE_INK:
        return {
            "accepted": False,
            "reason": "page too sparse to trust projection",
            "reference_ink": reference_ink,
            "min_ink_in_window": min_in_window,
        }

    ratio = min_in_window / reference_ink
    accepted = ratio <= _COL_RESCUE_INK_RATIO

    return {
        "accepted": accepted,
        "min_ink_in_window": min_in_window,
        "reference_ink": reference_ink,
        "ink_ratio": ratio,
        "ink_ratio_threshold": _COL_RESCUE_INK_RATIO,
        "gutter_x_px": gutter_x_px,
        "window_px": (lo, hi),
    }


def _detect_page_columns(
    all_words: List[Dict[str, Any]],
    page_w: float,
    page_h: float,
    table_bboxes: Optional[List[Tuple[float, float, float, float]]] = None,
    raster_getter: Optional[Callable[[], Tuple[np.ndarray, float]]] = None,
) -> ColumnDetectionResult:
    """Detect a two-column page gutter, with confidence and diagnostics.

    Returns a :class:`ColumnDetectionResult`. When the page is judged
    single-column, ``gutter_x`` is ``None``; otherwise it carries the
    x-coordinate of the gutter midpoint.

    ``table_bboxes`` (optional): bboxes of regions already known to be
    tables. Lines whose words lie mostly inside one of these bboxes are
    excluded from voting -- their gap pattern is driven by table column
    structure, not page column flow. Critically, this only suppresses
    rows of tables that upstream detection actually found. Rows of
    *undetected* tables are filtered by an independent
    in-function heuristic (see ``_is_tabular_row`` below).

    ``raster_getter`` (optional): a zero-arg callable returning
    ``(img_rgb, scale_px_per_pt)``. When provided, a candidate that
    would otherwise be hard-rejected at the ``voting_word_fraction``
    or ``confidence`` gate may be rescued if a vertical ink-projection
    profile shows a clear valley at the candidate gutter. Rescue is
    capped by ``_COL_RESCUE_MIN_CONFIDENCE`` so it never rescues
    candidates the text-side already considered weak. When omitted
    behavior is identical to the pre-rescue version.

    The detector defends against three classes of false positive:

    1. *Undetected table rows* -- caught by self-detecting tabular
       lines (3+ significant internal gaps) and by the y-distribution
       check on peak supporters (table rows cluster in a narrow band).
    2. *Sidebars and narrow note columns* -- caught by the
       side-population balance check.
    3. *Chance central-gap alignment on single-column pages* -- caught
       by a higher floor on peak voter count and peak/spanner ratio,
       plus the y-distribution and balance checks above.

    Tunables live as module constants prefixed ``_COL_``.
    """
    # ----- Stage 0: trivial rejects -----
    if not all_words or page_w < 100.0 or page_h < 100.0:
        return ColumnDetectionResult(
            None, 0.0, {"reason": "page too small or empty"}
        )

    # ----- Stage 1: filter to usable words -----
    real_words: List[Dict[str, Any]] = []
    for w in all_words:
        if str(w.get("fontname", "")) == "_synthetic_bullet":
            continue
        try:
            float(w["x0"]); float(w["x1"])
            float(w["top"]); float(w["bottom"])
        except (KeyError, ValueError, TypeError):
            continue
        real_words.append(w)
    if len(real_words) < _COL_MIN_WORDS:
        return ColumnDetectionResult(
            None, 0.0,
            {"reason": "too few valid words", "n_words": len(real_words)},
        )

    tbs = list(table_bboxes or [])

    # ----- Stage 2: group words into raw lines by y-overlap -----
    # No horizontal split here -- cross-gutter words on the same y must
    # fuse so we can observe the gap. (Identical to the previous
    # implementation; preserved verbatim.)
    ws = sorted(real_words, key=lambda w: (float(w["top"]), float(w["x0"])))
    raw_lines: List[List[Dict[str, Any]]] = [[ws[0]]]
    for w in ws[1:]:
        prev = raw_lines[-1][-1]
        prev_cy = (float(prev["top"]) + float(prev["bottom"])) / 2.0
        cur_cy = (float(w["top"]) + float(w["bottom"])) / 2.0
        ref_h = min(
            float(prev["bottom"]) - float(prev["top"]),
            float(w["bottom"]) - float(w["top"]),
        )
        ref_h = ref_h if ref_h > 0 else 1.0
        if abs(cur_cy - prev_cy) < 0.5 * ref_h:
            raw_lines[-1].append(w)
        else:
            raw_lines.append([w])

    # ----- Stage 3: per-line helpers -----

    def _line_inside_table(line: List[Dict[str, Any]]) -> bool:
        # Majority-of-word-centroids test against each table bbox.
        # "Majority" (not "all") because pdfplumber sometimes clips a
        # table's bbox tightly around its rules and excludes the
        # header row's text.
        if not tbs:
            return False
        for tx0, ty0, tx1, ty1 in tbs:
            n_in = 0
            for w in line:
                try:
                    cx = (float(w["x0"]) + float(w["x1"])) / 2.0
                    cy = (float(w["top"]) + float(w["bottom"])) / 2.0
                except (KeyError, ValueError, TypeError):
                    continue
                if tx0 <= cx <= tx1 and ty0 <= cy <= ty1:
                    n_in += 1
            if n_in > len(line) / 2:
                return True
        return False

    def _significant_gaps(
        line_sorted: List[Dict[str, Any]],
    ) -> List[Tuple[float, float]]:
        """Return ``(x_left, x_right)`` for each gap >= the tabular
        threshold. Sorted by x. Caller pre-sorts ``line_sorted``."""
        gaps: List[Tuple[float, float]] = []
        for i in range(len(line_sorted) - 1):
            x_a = float(line_sorted[i]["x1"])
            x_b = float(line_sorted[i + 1]["x0"])
            if x_b - x_a >= _COL_TABULAR_MIN_GAP:
                gaps.append((x_a, x_b))
        return gaps

    def _is_tabular_row(sig_gaps: List[Tuple[float, float]]) -> bool:
        # 3+ significant gaps is the signature of a table row. Heading
        # lines like "Title         Page" carry one big gap; running
        # heads like "Section     Title     Page" carry two and remain
        # ambiguous (we let those vote, since y-distribution and side
        # balance will filter chance peaks). Three or more is the
        # decisive cutoff.
        return len(sig_gaps) >= _COL_TABULAR_GAP_COUNT

    # ----- Stage 4: vote on candidate gutter x-positions -----
    bin_size = _COL_BIN_SIZE
    n_bins = max(2, int(np.ceil(page_w / bin_size)))
    coverage = np.zeros(n_bins, dtype=np.float64)
    mid_page = page_w * 0.5
    central_lo = _COL_CENTRAL_RANGE[0] * page_w
    central_hi = _COL_CENTRAL_RANGE[1] * page_w

    n_spanning = 0
    n_excluded_table_bbox = 0
    n_excluded_tabular = 0
    # Word-count bookkeeping for the voting_word_fraction check
    # (Stage 7). A table row contributes few words; a body row in a
    # 2-col layout contributes many. Comparing word counts catches
    # narrow-table false positives that line counts alone miss.
    n_voting_words = 0
    n_spanning_words = 0
    # voters: each is (bin_lo, bin_hi_exclusive, line_y_center) so we
    # can later identify which lines voted for the eventual peak and
    # examine their vertical distribution.
    voters: List[Tuple[int, int, float]] = []

    for line in raw_lines:
        if len(line) < 2:
            continue
        line_sorted = sorted(line, key=lambda w: float(w["x0"]))
        min_x = float(line_sorted[0]["x0"])
        max_x = float(line_sorted[-1]["x1"])
        # Must straddle the middle to plausibly contain a gutter.
        if min_x >= mid_page or max_x <= mid_page:
            continue

        # Filter known table rows first (cheap, deterministic).
        if _line_inside_table(line):
            n_excluded_table_bbox += 1
            continue

        # Then self-detected table rows. We compute the significant-gap
        # list once and reuse it to pick the candidate gutter gap.
        sig_gaps = _significant_gaps(line_sorted)
        if _is_tabular_row(sig_gaps):
            n_excluded_tabular += 1
            continue

        n_spanning += 1
        n_spanning_words += len(line_sorted)

        # Pick the largest gap whose midpoint falls in the central
        # x-range. Restricting to significant gaps (>= _COL_TABULAR_MIN_GAP)
        # rules out routine inter-word whitespace; we still re-check
        # against _COL_MIN_GUTTER_WIDTH because the gutter width floor
        # is a separate, slightly larger policy.
        best_gap = 0.0
        best_xs: Optional[Tuple[float, float]] = None
        for x_a, x_b in sig_gaps:
            mid_g = (x_a + x_b) / 2.0
            if mid_g < central_lo or mid_g > central_hi:
                continue
            gap = x_b - x_a
            if gap > best_gap:
                best_gap = gap
                best_xs = (x_a, x_b)
        if best_xs is None or best_gap < _COL_MIN_GUTTER_WIDTH:
            continue

        b0 = max(0, int(best_xs[0] / bin_size))
        b1 = min(n_bins, int(np.ceil(best_xs[1] / bin_size)))
        if b1 <= b0:
            continue
        for b in range(b0, b1):
            coverage[b] += 1.0
        line_y = (
            float(line_sorted[0]["top"]) + float(line_sorted[0]["bottom"])
        ) / 2.0
        voters.append((b0, b1, line_y))
        n_voting_words += len(line_sorted)

    base_details: Dict[str, Any] = {
        "n_spanning": n_spanning,
        "n_excluded_table_bbox": n_excluded_table_bbox,
        "n_excluded_tabular": n_excluded_tabular,
        "n_voters": len(voters),
        "n_voting_words": n_voting_words,
        "n_spanning_words": n_spanning_words,
    }

    if n_spanning < _COL_MIN_SPANNING_LINES:
        return ColumnDetectionResult(
            None, 0.0,
            {**base_details, "reason": "too few middle-spanning lines"},
        )

    # ----- Stage 5: locate plateau (for Stage 6 supporters) and peak core -----
    peak = float(coverage.max())
    if peak < _COL_MIN_PEAK_VOTES or peak < _COL_MIN_PEAK_RATIO * n_spanning:
        return ColumnDetectionResult(
            None, 0.0,
            {**base_details, "peak": peak,
             "reason": "peak below absolute or ratio floor"},
        )

    # The 70 % plateau is no longer used to estimate the gutter's
    # width or centre (see ``_COL_PEAK_CORE_FRACTION`` and the peak-core
    # block below). It is still computed because Stage 6 uses
    # ``[best_a, best_b]`` to identify "peak supporters" -- voters
    # whose vote landed anywhere in the plateau -- when measuring the
    # y-distribution of the consensus.
    threshold = _COL_PLATEAU_FRACTION * peak
    best_a = best_b = -1
    best_width = 0
    i = 0
    while i < n_bins:
        if coverage[i] >= threshold:
            j = i
            while j < n_bins and coverage[j] >= threshold:
                j += 1
            width = j - i
            if width > best_width:
                best_width = width
                best_a = i
                best_b = j - 1
            i = j + 1
        else:
            i += 1

    if best_a < 0:
        return ColumnDetectionResult(
            None, 0.0,
            {**base_details, "peak": peak, "reason": "no plateau found"},
        )

    # Measure the gutter from the *sharp core* of the peak rather than
    # the 70 % plateau. Each line in Stage 4 casts its vote on every
    # bin its gap spans, so lines with wide central gaps (short
    # left-column code lines paired with full-width right-column
    # prose, page-wide headers, etc.) drag the 70 % shoulders well
    # beyond the true gutter even when every line agrees on the
    # gutter's location. The peak core -- bins at or essentially at
    # the peak value -- is robust to that spreading and locates the
    # gutter precisely. Anchored at the leftmost bin attaining the
    # peak and expanded contiguously in both directions while
    # coverage stays within tolerance of the peak.
    core_threshold = min(peak - 1.0, _COL_PEAK_CORE_FRACTION * peak)
    peak_bin = int(np.argmax(coverage))
    core_lo = peak_bin
    while core_lo > 0 and coverage[core_lo - 1] >= core_threshold:
        core_lo -= 1
    core_hi = peak_bin
    while core_hi < n_bins - 1 and coverage[core_hi + 1] >= core_threshold:
        core_hi += 1

    gutter_width = (core_hi - core_lo + 1) * bin_size
    if gutter_width < _COL_MIN_GUTTER_WIDTH:
        return ColumnDetectionResult(
            None, 0.0,
            {**base_details, "peak": peak, "gutter_width": gutter_width,
             "reason": "peak core narrower than min gutter width"},
        )
    if gutter_width > _COL_MAX_GUTTER_WIDTH:
        return ColumnDetectionResult(
            None, 0.0,
            {**base_details, "peak": peak, "gutter_width": gutter_width,
             "reason": "peak core wider than max gutter width"},
        )

    gutter_x = (core_lo + core_hi + 1) / 2.0 * bin_size
    if gutter_x <= 0.0 or gutter_x >= page_w:
        return ColumnDetectionResult(
            None, 0.0,
            {**base_details, "reason": "gutter x out of page bounds"},
        )

    # ----- Stage 6: validate the candidate -----
    # Peak supporters: voters whose bin range overlaps [best_a, best_b].
    peak_voter_ys = [
        vy for (vb0, vb1, vy) in voters
        if vb0 <= best_b and vb1 > best_a
    ]
    n_peak_voters = len(peak_voter_ys)

    # Vertical extent of the page's text body. The right denominator
    # for "do voters cover most of the body?" is the y-extent of all
    # valid words, not page_h: a page can have large image/whitespace
    # margins, and we'd otherwise wrongly demand voters span those.
    word_ys = [
        (float(w["top"]) + float(w["bottom"])) / 2.0 for w in real_words
    ]
    text_y_min = min(word_ys)
    text_y_max = max(word_ys)
    text_y_span = max(text_y_max - text_y_min, 1.0)

    if n_peak_voters >= 2:
        y_min = min(peak_voter_ys)
        y_max = max(peak_voter_ys)
        voter_y_span = y_max - y_min
    else:
        voter_y_span = 0.0
    voter_text_coverage = voter_y_span / text_y_span

    third_h = page_h / 3.0
    thirds_with_votes = set()
    for vy in peak_voter_ys:
        thirds_with_votes.add(min(2, max(0, int(vy / third_h))))
    n_thirds = len(thirds_with_votes)

    # Side-population balance among single-side lines. A "single-side
    # line" is one whose word centroids all fall on the same side of
    # the candidate gutter. Tidy two-column layouts produce few of
    # these (because every line has content in both columns), so we
    # skip the balance check entirely when neither side has enough
    # samples to give a stable ratio.
    n_left_only = 0
    n_right_only = 0
    for line in raw_lines:
        if not line:
            continue
        if _line_inside_table(line):
            continue
        n_left_words = 0
        n_right_words = 0
        for w in line:
            cx = _word_center_x(w)
            if cx is None:
                continue
            if cx < gutter_x:
                n_left_words += 1
            else:
                n_right_words += 1
        if n_left_words > 0 and n_right_words == 0:
            n_left_only += 1
        elif n_right_words > 0 and n_left_words == 0:
            n_right_only += 1

    max_side = max(n_left_only, n_right_only)
    min_side = min(n_left_only, n_right_only)
    side_balance = (min_side / max_side) if max_side > 0 else 0.0

    # Recompute the voting-word fraction against the *discovered* gutter
    # rather than the page midpoint. Stage 4 used ``mid_page`` as a
    # conservative proxy for "where might a gutter sit?" before we knew
    # the answer; once we've picked a candidate, lines whose content
    # never reaches it aren't relevant to whether a gutter exists there.
    #
    # The asymmetric-layout failure this addresses: when one column is
    # wider than half the page (e.g. a resume with a wide left column
    # and a narrow sidebar), pure left-column body lines whose words
    # extend just past ``mid_page`` are flagged as spanning by Stage 4
    # but cannot possibly host a gap at ``gutter_x``. They padded the
    # old denominator (``n_spanning_words``) without padding the
    # numerator, pulling the fraction below the 0.80 floor.
    #
    # Numerator and denominator are accumulated together so the
    # numerator is by construction a subset of the denominator
    # (fraction stays in [0, 1]). The table-band defense is preserved:
    # in that false-positive scenario the candidate gutter lands on a
    # table column, and single-column body lines that genuinely cross
    # it still lack any central gap, dragging the fraction down exactly
    # as before.
    gutter_span_margin = _COL_MIN_GUTTER_WIDTH / 2.0
    n_gutter_spanner_words = 0
    n_gutter_voter_words = 0
    for line in raw_lines:
        if len(line) < 2:
            continue
        line_sorted = sorted(line, key=lambda w: float(w["x0"]))
        min_x = float(line_sorted[0]["x0"])
        max_x = float(line_sorted[-1]["x1"])

        # Mirror Stage 4 exclusions so the denominator's universe
        # matches the original voter universe minus the
        # mid_page-only "false spanners".
        if _line_inside_table(line):
            continue
        sig_gaps = _significant_gaps(line_sorted)
        if _is_tabular_row(sig_gaps):
            continue

        # Span the actual gutter, not page mid. The margin ensures
        # a line barely touching one side of the gutter doesn't
        # qualify; it tolerates sub-bin alignment jitter.
        if not (
            min_x < gutter_x - gutter_span_margin
            and max_x > gutter_x + gutter_span_margin
        ):
            continue

        n_words = len(line_sorted)
        n_gutter_spanner_words += n_words

        # Replicate the Stage 4 voting test: a significant gap in
        # the central x-range, at least _COL_MIN_GUTTER_WIDTH wide.
        # ``break`` on first hit -- one qualifying gap is enough.
        for x_a, x_b in sig_gaps:
            mid_g = (x_a + x_b) / 2.0
            if mid_g < central_lo or mid_g > central_hi:
                continue
            if (x_b - x_a) < _COL_MIN_GUTTER_WIDTH:
                continue
            n_gutter_voter_words += n_words
            break

    voting_word_fraction = (
        n_gutter_voter_words / n_gutter_spanner_words
        if n_gutter_spanner_words > 0 else 0.0
    )

    # ----- Stage 7: compose confidence + decide -----
    # The confidence formula leans on signals that are robust to
    # asymmetric column heights:
    #   - peak_ratio: fraction of spanning lines that voted. A real
    #     gutter has nearly all spanning lines voting; a table band
    #     has only the table rows voting (body rows are spanning but
    #     gap-less).
    #   - voting_word_fraction: among lines that span the candidate
    #     gutter, what share of their words sit in lines that voted for
    #     a gap there. Penalises word-sparse table bands.
    #   - thirds: peak supporters must populate >= 2 of 3 vertical
    #     thirds of the page.
    # voter_text_coverage and side_balance are computed for
    # diagnostics only -- they failed on legitimate asymmetric
    # layouts in earlier revisions.
    peak_ratio = peak / max(n_spanning, 1)              # 0..1
    peak_ratio_score = min(1.0, peak_ratio / 0.70)      # saturates at 70 %
    word_fraction_score = min(1.0, voting_word_fraction / 0.85)  # at 85 %
    thirds_score = min(1.0, n_thirds / 2.0)             # 1.0 at 2/3 thirds

    confidence = (
        0.40 * peak_ratio_score
        + 0.40 * word_fraction_score
        + 0.20 * thirds_score
    )

    full_details: Dict[str, Any] = {
        **base_details,
        "peak": peak,
        "peak_ratio": peak_ratio,
        "gutter_width": gutter_width,
        "gutter_x_candidate": gutter_x,
        "n_peak_voters": n_peak_voters,
        "voter_y_span": voter_y_span,
        "text_y_span": text_y_span,
        "voter_text_coverage": voter_text_coverage,
        "n_gutter_voter_words": n_gutter_voter_words,
        "n_gutter_spanner_words": n_gutter_spanner_words,
        "voting_word_fraction": voting_word_fraction,
        "n_thirds_with_votes": n_thirds,
        "n_left_only_lines": n_left_only,
        "n_right_only_lines": n_right_only,
        "side_balance": side_balance,
        "confidence": confidence,
    }

    # Hard rejects -- each one is a structural disqualifier
    # independent of the confidence arithmetic. Either gate that fires
    # is allowed to be overridden by a projection-based rescue when a
    # raster getter is available and the candidate is high enough
    # confidence to be worth validating visually. See
    # ``_validate_gutter_with_projection`` for the validator and
    # ``_COL_RESCUE_*`` constants for the policy.
    hard_reject_reason: Optional[str] = None
    if voting_word_fraction < _COL_MIN_VOTING_WORD_FRACTION:
        hard_reject_reason = (
            "voting lines account for too small a share of "
            "spanning-line words"
        )
    elif confidence < _COL_MIN_CONFIDENCE:
        hard_reject_reason = "confidence below threshold"

    if hard_reject_reason is None:
        return ColumnDetectionResult(gutter_x, confidence, full_details)

    rescue_eligible = (
        raster_getter is not None
        and confidence >= _COL_RESCUE_MIN_CONFIDENCE
    )
    if rescue_eligible:
        rescue = _validate_gutter_with_projection(
            raster_getter, gutter_x, gutter_width, page_w,
        )
        full_details = {
            **full_details,
            "hard_reject_reason": hard_reject_reason,
            "rescue": rescue,
        }
        if rescue.get("accepted"):
            full_details["rescued_by_projection"] = True
            return ColumnDetectionResult(
                gutter_x, confidence, full_details,
            )

    return ColumnDetectionResult(
        None, confidence,
        {**full_details, "reason": hard_reject_reason},
    )


def _word_center_x(w: Dict[str, Any]) -> Optional[float]:
    try:
        return (float(w["x0"]) + float(w["x1"])) / 2.0
    except (KeyError, ValueError, TypeError):
        return None


def _line_side(
    line: List[Dict[str, Any]],
    gutter_x: Optional[float],
) -> str:
    """Classify a line as 'left', 'right', or 'wide' (or 'any' when no
    gutter is known). Side is decided by the x-centre of constituent
    words: a line with words on both sides of the gutter is wide."""
    if gutter_x is None:
        return "any"
    lefts = 0
    rights = 0
    for w in line:
        cx = _word_center_x(w)
        if cx is None:
            continue
        if cx < gutter_x:
            lefts += 1
        else:
            rights += 1
    if lefts > 0 and rights > 0:
        return "wide"
    if lefts > 0:
        return "left"
    return "right"


def _resolve_line_sides(
    lines: List[List[Dict[str, Any]]],
    raw_line_ids: List[int],
    gutter_x: Optional[float],
) -> List[str]:
    """Per-line side labels with wide propagated across split segments.

    ``_group_words_into_lines`` may split one y-band into several lines at
    large horizontal gaps. If any segment of that band is wide, every
    sibling segment is treated as wide so column blocks cannot absorb
    fragments of a full-width heading."""
    sides = [_line_side(line, gutter_x) for line in lines]
    if gutter_x is None or not lines:
        return sides
    by_raw: Dict[int, List[int]] = defaultdict(list)
    for i, raw_id in enumerate(raw_line_ids):
        by_raw[raw_id].append(i)
    for indices in by_raw.values():
        if any(sides[i] == "wide" for i in indices):
            for i in indices:
                sides[i] = "wide"
    return sides


def _region_column_side(
    region: "LayoutRegion",
    gutter_x: Optional[float],
    page_w: Optional[float],
) -> str:
    """Classify a region's column membership: ``'left'``, ``'right'``, or
    ``'wide'`` (``'any'`` when no gutter is known).

    A region is *wide* when it genuinely straddles the gutter (with a small
    slack) and is more than 40 % of the page wide; otherwise it is assigned
    to left or right by its centroid x. Matches the wide definition used by
    ``_reading_order`` so that column-category decisions stay consistent
    across merging and ordering."""
    if gutter_x is None:
        return "any"
    x0, _, x1, _ = region.bbox
    width = x1 - x0
    if (page_w is not None and width > 0.40 * page_w
            and x0 < gutter_x - 5.0 and x1 > gutter_x + 5.0):
        return "wide"
    cx = (x0 + x1) / 2.0
    return "left" if cx < gutter_x else "right"


def _build_line_side_meta(
    all_words: Optional[List[Dict[str, Any]]],
    gutter_x: Optional[float],
) -> List[Tuple[Tuple[float, float, float, float], str]]:
    """Per-line bbox + resolved column side for ``_region_side``."""
    if not all_words or gutter_x is None:
        return []
    lines, raw_ids = _group_words_into_lines(all_words, gutter_x=gutter_x)
    sides = _resolve_line_sides(lines, raw_ids, gutter_x)
    line_meta: List[Tuple[Tuple[float, float, float, float], str]] = []
    for line, side in zip(lines, sides):
        if not line:
            continue
        try:
            lx0 = min(float(w["x0"]) for w in line)
            lx1 = max(float(w["x1"]) for w in line)
            ly0 = min(float(w["top"]) for w in line)
            ly1 = max(float(w["bottom"]) for w in line)
        except (KeyError, ValueError, TypeError):
            continue
        line_meta.append(((lx0, ly0, lx1, ly1), side))
    return line_meta


def _region_side(
    region: LayoutRegion,
    gutter_x: Optional[float],
    page_w: float,
    line_meta: List[Tuple[Tuple[float, float, float, float], str]],
) -> str:
    """Classify a region's column membership from line sides or bbox fallback.

    TEXT / LIST regions aggregate the sides of lines whose centroid sits
    inside the region's bbox. TABLE / IMAGE regions fall back to
    ``_region_column_side``. Returns ``'any'`` when no gutter is known.
    """
    if gutter_x is None:
        return "any"
    if region.type not in (LayoutRegionType.TEXT, LayoutRegionType.LIST):
        return _region_column_side(region, gutter_x, page_w)
    rx0, ry0, rx1, ry1 = region.bbox
    seen: List[str] = []
    for (lx0, ly0, lx1, ly1), s in line_meta:
        cx = (lx0 + lx1) / 2.0
        cy = (ly0 + ly1) / 2.0
        if rx0 <= cx <= rx1 and ry0 <= cy <= ry1:
            seen.append(s)
    if not seen:
        return _region_column_side(region, gutter_x, page_w)
    if "wide" in seen:
        return "wide"
    has_l = "left" in seen
    has_r = "right" in seen
    if has_l and has_r:
        return "wide"
    return "left" if has_l else "right"


# --------------------------------------------------------------------------- #
# Vector-bullet detection
# --------------------------------------------------------------------------- #

def _find_vector_bullets(
    page,
    all_words: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []
    try:
        sources.extend(page.curves or [])
        sources.extend(page.rects or [])
    except Exception:
        return []

    if not sources:
        return []

    candidates: List[Tuple[float, float, float, float, float]] = []
    for v in sources:
        try:
            x0 = float(v["x0"]); x1 = float(v["x1"])
            y0 = float(v["top"]); y1 = float(v["bottom"])
        except (KeyError, ValueError, TypeError):
            continue
        w_v = x1 - x0
        h_v = y1 - y0
        if w_v < 1.0 or w_v > 12.0 or h_v < 1.0 or h_v > 12.0:
            continue
        if min(w_v, h_v) / max(w_v, h_v) < 0.5:
            continue
        cy = (y0 + y1) / 2.0
        ref_h = max(h_v, 6.0)
        has_text_right = False
        for w in all_words:
            try:
                wx0 = float(w["x0"])
                wy_cy = (float(w["top"]) + float(w["bottom"])) / 2.0
            except (KeyError, ValueError, TypeError):
                continue
            if wx0 < x1 - 0.5:
                continue
            if wx0 - x1 > 40.0:
                continue
            if abs(wy_cy - cy) > ref_h:
                continue
            has_text_right = True
            break
        if not has_text_right:
            continue
        candidates.append((x0, y0, x1, y1, h_v))

    if not candidates:
        return []

    by_x: Dict[int, List[Tuple[float, float, float, float, float]]] = defaultdict(list)
    for c in candidates:
        x_key = int(round(c[0] / 2.0) * 2)
        by_x[x_key].append(c)

    bullets: List[Dict[str, Any]] = []
    for x_key, group in by_x.items():
        if len(group) < 2:
            continue
        for x0, y0, x1, y1, h_v in group:
            bullets.append({
                "text": "\u2022",
                "x0": x0,
                "x1": x1,
                "top": y0,
                "bottom": y1,
                "size": max(8.0, h_v * 2.0),
                "fontname": "_synthetic_bullet",
            })
    return bullets


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def extract_layout_regions(
    page, pdf_path: Optional[str] = None
) -> List[LayoutRegion]:
    page_w = float(page.width)
    page_h = float(page.height)

    try:
        all_words = page.extract_words(
            extra_attrs=["fontname", "size"],
            keep_blank_chars=False,
            use_text_flow=True,
            x_tolerance_ratio=0.15,
        )
    except Exception:
        all_words = []

    if all_words:
        vec_bullets = _find_vector_bullets(page, all_words)
        if vec_bullets:
            all_words = list(all_words) + vec_bullets
            all_words.sort(key=lambda w: (float(w["top"]), float(w["x0"])))

    if not all_words:
        try:
            n_vec = (
                len(page.lines or [])
                + len(page.rects or [])
                + len(page.curves or [])
            )
            n_embed = len(page.images or [])
        except Exception:
            n_vec = n_embed = 0
        if n_vec < 5 and n_embed == 0:
            img_rgb, scale = _rasterize_page(page, _DPI, pdf_path=pdf_path)
            full_bbox = (0.0, 0.0, page_w, page_h)
            return [LayoutRegion(
                type=LayoutRegionType.IMAGE,
                bbox=full_bbox,
                image_data=_crop_image_bytes(img_rgb, full_bbox, scale),
            )]

    # Identify striped-fill code-frame regions once and thread them
    # down to both table detection (so the false-positive TABLE
    # candidates are suppressed) and vector-block segmentation (so the
    # same stripes don't cluster into a vector graphic that would then
    # be promoted to IMAGE). Both consumers expand the bbox by a small
    # margin for membership tests; see the helper docs.
    stripe_frames = _detect_uniform_fill_stripe_regions(page)

    table_regions = _detect_tables(
        page, page_w, page_h, all_words,
        stripe_frames=stripe_frames,
    )

    # Lazy raster cache: rasterization is expensive, so we set up the
    # getter once and let consumers pull on demand. Column detection
    # uses it for the optional projection-based rescue (only invoked
    # when a candidate is about to be hard-rejected); downstream image
    # cropping and ink-density checks share the same cached raster.
    _raster: Dict[str, Any] = {}

    def _get_raster() -> Tuple[np.ndarray, float]:
        if "img" not in _raster:
            _raster["img"], _raster["scale"] = _rasterize_page(
                page, _DPI, pdf_path=pdf_path
            )
        return _raster["img"], _raster["scale"]

    # Detect column gutter once -- threaded through everything that does
    # line grouping or reading-order sorting. Stays None on single-column
    # pages so the rest of the pipeline behaves exactly as before. The
    # detector now returns a result object carrying confidence and
    # diagnostics; downstream code only uses ``gutter_x``.
    _col_result = _detect_page_columns(
        all_words, page_w, page_h,
        table_bboxes=[r.bbox for r in table_regions],
        raster_getter=_get_raster,
    )
    gutter_x = _col_result.gutter_x
    h_rules: List[float] = []
    try:
        for ln in (page.lines or []):
            x0_l = float(ln["x0"]); x1_l = float(ln["x1"])
            y0_l = float(ln["top"]); y1_l = float(ln["bottom"])
            if abs(y1_l - y0_l) < 2.0 and (x1_l - x0_l) >= 0.5 * page_w:
                h_rules.append((y0_l + y1_l) / 2.0)
    except Exception:
        pass

    block_boxes = _segment_blocks_native(
        all_words, page_w, page_h, h_rules, gutter_x=gutter_x,
    )

    block_boxes = block_boxes + _segment_vector_blocks(
        page, block_boxes, page_w, page_h,
        gutter_x=gutter_x,
        stripe_frames=stripe_frames,
    )

    image_regions = _detect_images(
        page, _get_raster, block_boxes,
        existing=table_regions,
        all_words=all_words,
    )

    higher_priority = image_regions + table_regions

    text_list_regions = _classify_text_blocks(
        block_boxes, higher_priority, all_words, gutter_x=gutter_x,
    )

    resolved = _resolve_overlaps(
        table_regions + image_regions + text_list_regions,
        page=page, all_words=all_words, page_w=page_w, page_h=page_h,
        gutter_x=gutter_x,
    )

    # Fold chart furniture (axis tick labels, axis titles, legends/colour
    # keys) that sit just outside an IMAGE back into that image, so the chart
    # is captured as one figure. Any image whose bbox grows has its cached
    # crop invalidated by _merge_regions and is re-rasterized below.
    resolved = _absorb_labels_into_images(
        resolved, page_w, page_h, gutter_x=gutter_x, all_words=all_words,
    )

    # Synthesize TEXT regions for any page words not covered by detection.
    # The follow-up _resolve_overlaps below absorbs them into adjacent
    # regions where appropriate; _merge_small_text_regions later sweeps up
    # short fragments. This step only emits the candidates.
    resolved = _detect_stray_text_regions(
        resolved, all_words, gutter_x=gutter_x,
    )

    resolved = _resolve_overlaps(
        resolved, page=page, all_words=all_words,
        page_w=page_w, page_h=page_h, gutter_x=gutter_x,
    )

    needs_raster = any(
        r.type == LayoutRegionType.IMAGE and r.image_data is None
        for r in resolved
    )
    if needs_raster:
        img_rgb, scale = _get_raster()
        for r in resolved:
            if r.type == LayoutRegionType.IMAGE and r.image_data is None:
                r.image_data = _crop_image_bytes(img_rgb, r.bbox, scale)

    ordered = _reading_order(
        resolved, page_w, page_h, gutter_x=gutter_x, all_words=all_words,
    )

    # Fold short TEXT regions into a closer adjacent TEXT region in
    # reading order. Tables, lists, and images act as boundaries.
    merged = _merge_small_text_regions(ordered)

    # The merge unions bboxes, which can newly overlap a sibling or
    # leave the regions in an order that no longer reflects the union
    # bbox positions -- redo the resolve + reading-order pass.
    merged = _resolve_overlaps(
        merged, page=page, all_words=all_words,
        page_w=page_w, page_h=page_h, gutter_x=gutter_x,
    )
    ordered = _reading_order(
        merged, page_w, page_h, gutter_x=gutter_x, all_words=all_words,
    )
    return _inject_hyperlinks_into_regions(ordered, page, all_words)


# --------------------------------------------------------------------------- #
# Step 2: Native block segmentation
# --------------------------------------------------------------------------- #

def _segment_blocks_native(
    all_words: List[Dict[str, Any]],
    page_w: float,
    page_h: float,
    h_rules: Optional[List[float]] = None,
    gutter_x: Optional[float] = None,
) -> List[Tuple[float, float, float, float]]:
    """Group word-lines into blocks. When `gutter_x` is given, each block
    is restricted to a single side (left / right / wide) -- a wide heading
    cannot absorb a line of column body text just because they y-stack
    closely, and column lines never attach across the gutter."""
    if not all_words:
        return []

    lines, raw_line_ids = _group_words_into_lines(all_words, gutter_x=gutter_x)
    if not lines:
        return []

    line_sides = _resolve_line_sides(lines, raw_line_ids, gutter_x)
    line_records: List[Dict[str, Any]] = []
    for line, side in zip(lines, line_sides):
        if not line:
            continue
        try:
            x0 = min(float(w["x0"]) for w in line)
            x1 = max(float(w["x1"]) for w in line)
            y0 = min(float(w["top"]) for w in line)
            y1 = max(float(w["bottom"]) for w in line)
        except (KeyError, ValueError, TypeError):
            continue
        sizes: List[float] = []
        for w in line:
            s = w.get("size")
            if s is None:
                continue
            try:
                sizes.append(float(s))
            except (TypeError, ValueError):
                pass
        avg_size = (sum(sizes) / len(sizes)) if sizes else max(1.0, y1 - y0)
        line_records.append({
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "size": max(1.0, avg_size),
            "h": max(1.0, y1 - y0),
            "side": side,
        })

    if not line_records:
        return []

    line_records.sort(key=lambda r: (r["y0"], r["x0"]))

    open_blocks: List[Dict[str, Any]] = []
    closed_blocks: List[Dict[str, Any]] = []

    for lr in line_records:
        x0, y0, x1, y1 = lr["x0"], lr["y0"], lr["x1"], lr["y1"]
        size = lr["size"]
        h = lr["h"]
        side = lr["side"]
        line_w = max(1e-6, x1 - x0)

        still_open: List[Dict[str, Any]] = []
        for blk in open_blocks:
            v_gap = y0 - blk["y1"]
            ref_h = max(blk["last_h"], h, blk["size"])
            crosses_rule = False
            if h_rules:
                for ry in h_rules:
                    if blk["y1"] < ry < y0:
                        crosses_rule = True
                        break
            if not crosses_rule and v_gap < ref_h * 1.5:
                still_open.append(blk)
            else:
                closed_blocks.append(blk)
        open_blocks = still_open

        best: Optional[Dict[str, Any]] = None
        best_score = 0.0
        for blk in open_blocks:
            # Column constraint: when a gutter is known, blocks and lines
            # must agree on side. A 'wide' block (heading) does not accept
            # left/right column lines, and a column block does not accept
            # the other column's lines or wide lines.
            if gutter_x is not None and side != blk["side"]:
                continue
            blk_w = max(1e-6, blk["x1"] - blk["x0"])
            ox0 = max(x0, blk["x0"])
            ox1 = min(x1, blk["x1"])
            overlap = max(0.0, ox1 - ox0)
            if overlap <= 0.0:
                continue
            overlap_ratio = overlap / min(line_w, blk_w)
            if overlap_ratio < 0.3:
                continue
            size_ratio = min(size, blk["size"]) / max(size, blk["size"])
            if size_ratio < 0.55:
                continue
            score = overlap_ratio * size_ratio
            if score > best_score:
                best_score = score
                best = blk

        if best is not None:
            best["x0"] = min(best["x0"], x0)
            best["x1"] = max(best["x1"], x1)
            best["y1"] = max(best["y1"], y1)
            best["last_h"] = h
            best["size"] = max(best["size"], size)
        else:
            open_blocks.append({
                "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                "size": size, "last_h": h,
                "side": side,
            })

    closed_blocks.extend(open_blocks)

    out: List[Tuple[float, float, float, float]] = []
    for blk in closed_blocks:
        x0 = max(0.0, blk["x0"])
        y0 = max(0.0, blk["y0"])
        x1 = min(page_w, blk["x1"])
        y1 = min(page_h, blk["y1"])
        if x1 - x0 < 5.0 or y1 - y0 < 5.0:
            continue
        out.append((x0, y0, x1, y1))
    return out


def _segment_vector_blocks(
    page,
    text_blocks: List[Tuple[float, float, float, float]],
    page_w: float,
    page_h: float,
    max_gap: float = 25.0,
    min_members: int = 5,
    gutter_x: Optional[float] = None,
    stripe_frames: Optional[List[Tuple[float, float, float, float]]] = None,
) -> List[Tuple[float, float, float, float]]:
    vecs: List[Tuple[float, float, float, float]] = []
    try:
        sources = (
            list(page.lines or [])
            + list(page.rects or [])
            + list(page.curves or [])
        )
    except Exception:
        sources = []

    # Primitives lying inside a striped-fill code-frame region are part
    # of that frame's rendering (stripe rects, margin strips, outer
    # border lines) rather than a real vector graphic. Excluding them
    # prevents the union-find clustering below from synthesising a
    # page-spanning vector cluster that would later be promoted to
    # IMAGE by ``_detect_images``. Membership is tested against a
    # slightly inflated bbox so the frame's margin strips and outer
    # border lines -- which sit just outside the body-strip cluster
    # bbox -- are caught too. Code blocks rendered as a single large
    # fill (no stripe pattern) produce no stripe_frames and so are
    # unaffected, preserving the existing IMAGE classification for
    # that case.
    _STRIPE_FRAME_INFLATE = 6.0

    def _inside_stripe_frame(
        bbox: Tuple[float, float, float, float],
    ) -> bool:
        if not stripe_frames:
            return False
        for sf in stripe_frames:
            ex_sf = (
                sf[0] - _STRIPE_FRAME_INFLATE,
                sf[1] - _STRIPE_FRAME_INFLATE,
                sf[2] + _STRIPE_FRAME_INFLATE,
                sf[3] + _STRIPE_FRAME_INFLATE,
            )
            if _overlap_ratio(bbox, ex_sf) >= 0.9:
                return True
        return False

    for v in sources:
        try:
            bbox = (
                float(v["x0"]), float(v["top"]),
                float(v["x1"]), float(v["bottom"]),
            )
        except (KeyError, ValueError, TypeError):
            continue
        if bbox[2] < bbox[0] or bbox[3] < bbox[1]:
            continue
        bw = bbox[2] - bbox[0]
        bh = bbox[3] - bbox[1]
        if bw >= 0.85 * page_w and bh < 3.0:
            continue
        if bh >= 0.85 * page_h and bw < 3.0:
            continue
        check_bbox = _inflate_if_thin(bbox, eps=1.0)
        if _inside_stripe_frame(check_bbox):
            continue
        if any(_overlap_ratio(check_bbox, tb) > 0.6 for tb in text_blocks):
            continue
        vecs.append(bbox)

    n = len(vecs)
    if n < min_members:
        return []

    # Per-vector column side, parallel to ``vecs``. When a gutter is
    # known, the union-find below refuses to merge a strictly-left
    # member with a strictly-right one -- without this gate, the
    # frame around a left-column code block and the rendered line
    # backgrounds of a right-column code block (each typically only
    # ~10 PDF points from the gutter edge) get bridged by the 25-pt
    # clustering inflation, producing a single cross-gutter cluster.
    # 'wide' is reserved for primitives that genuinely straddle the
    # gutter (with the same width / slack criteria as
    # ``_region_column_side``); those remain compatible with either
    # side so a real full-width vector (e.g. a horizontal rule that
    # somehow escaped the early thin-line filter) still clusters
    # normally. On single-column pages (``gutter_x is None``) every
    # vector is labelled 'any' and the gate is a no-op.
    sides: List[str] = []
    for b in vecs:
        if gutter_x is None:
            sides.append("any")
            continue
        bx0, _, bx1, _ = b
        bw = bx1 - bx0
        if (bw > 0.40 * page_w
                and bx0 < gutter_x - 5.0 and bx1 > gutter_x + 5.0):
            sides.append("wide")
        else:
            cx = (bx0 + bx1) / 2.0
            sides.append("left" if cx < gutter_x else "right")

    half = max_gap / 2.0
    expanded = [
        (b[0] - half, b[1] - half, b[2] + half, b[3] + half) for b in vecs
    ]

    parent = list(range(n))

    def _find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def _union(i: int, j: int) -> None:
        ri, rj = _find(i), _find(j)
        if ri != rj:
            parent[ri] = rj

    order = sorted(range(n), key=lambda k: expanded[k][0])
    for a in range(n):
        ia = order[a]
        ex_a = expanded[ia]
        sa = sides[ia]
        for b in range(a + 1, n):
            ib = order[b]
            ex_b = expanded[ib]
            if ex_b[0] > ex_a[2]:
                break
            if gutter_x is not None:
                sb = sides[ib]
                # Refuse to bridge across the gutter unless one side is
                # a genuinely wide primitive. Same compatibility rule
                # as ``_segment_blocks_native``'s side gate.
                if sa != sb and sa != "wide" and sb != "wide":
                    continue
            if (ex_a[0] <= ex_b[2] and ex_a[2] >= ex_b[0]
                    and ex_a[1] <= ex_b[3] and ex_a[3] >= ex_b[1]):
                _union(ia, ib)

    groups: Dict[int, Dict[str, float]] = {}
    counts: Dict[int, int] = {}
    for i, b in enumerate(vecs):
        r = _find(i)
        counts[r] = counts.get(r, 0) + 1
        if r not in groups:
            groups[r] = {"x0": b[0], "y0": b[1], "x1": b[2], "y1": b[3]}
        else:
            g = groups[r]
            g["x0"] = min(g["x0"], b[0])
            g["y0"] = min(g["y0"], b[1])
            g["x1"] = max(g["x1"], b[2])
            g["y1"] = max(g["y1"], b[3])

    out: List[Tuple[float, float, float, float]] = []
    for r, g in groups.items():
        if counts[r] < min_members:
            continue
        bbox = (
            max(0.0, g["x0"]),
            max(0.0, g["y0"]),
            min(page_w, g["x1"]),
            min(page_h, g["y1"]),
        )
        if (bbox[2] - bbox[0]) < 20.0 or (bbox[3] - bbox[1]) < 20.0:
            continue
        if (bbox[2] - bbox[0]) >= 0.95 * page_w and (bbox[3] - bbox[1]) >= 0.95 * page_h:
            continue
        out.append(bbox)
    return out


# --------------------------------------------------------------------------- #
# Striped-fill frame detection
#
# Some code-block environments (e.g. LaTeX ``listings`` framed/coloured
# variants) draw their background as a stack of thin filled rectangles
# -- one strip per text line -- rather than as a single fill. That
# rendering pattern mimics a fully-ruled table at the vector level and
# causes both ``page.find_tables()`` and ``_detect_table_boxes_from_vectors``
# to emit false-positive TABLE candidates spanning the entire code
# block.
#
# The helpers below identify such striped-fill regions so the table
# detector can skip candidates that lie inside one. Three independent
# defenses are stacked to keep this from regressing on genuine banded
# data tables:
#
#   1. Structural -- a stripe cluster requires multiple fill rects with
#      matching x-extents, matching color, and edge-to-edge vertical
#      tiling. Zebra-striped rows fail this because they alternate fill
#      with white. Small callouts fail the count threshold.
#   2. Structural -- the cluster bbox must contain no internal column
#      rules or interior horizontal rules. Bordered/ruled tables with
#      coloured rows fail this gate.
#   3. Semantic -- the chars inside the cluster must be predominantly
#      monospace, by font-name match or width-uniformity. Data tables
#      using a proportional font fail this gate.
#
# A real banded data table would have to fail all three gates
# simultaneously to be wrongly suppressed.
# --------------------------------------------------------------------------- #

# Minimum number of fill rectangles in a stripe cluster. Below this
# count the pattern is more likely a small highlighted callout than a
# code-block frame. Tune up if false positives appear on short tables
# with row backgrounds.
_STRIPE_MIN_COUNT = 6

# Maximum |dx| between consecutive stripes' x0 (and x1) for them to be
# treated as horizontally aligned. Tight enough to reject loosely-stacked
# callouts; loose enough to absorb anti-aliasing jitter.
_STRIPE_X_TOL = 2.0

# Maximum vertical gap (PDF points) between consecutive stripes for them
# to be considered edge-to-edge. A small positive value tolerates
# rounding noise but rejects zebra striping, which alternates fill and
# white rows with multi-point gaps.
_STRIPE_GAP_TOL = 1.0

# Maximum per-channel absolute difference between fill colors for two
# rects to belong to the same stripe cluster.
_STRIPE_COLOR_TOL = 0.02

# Inset (PDF points) from the cluster bbox edges within which a
# horizontal or vertical ``page.line`` is considered an INTERNAL rule
# (i.e. real table structure) rather than the frame's own border. Rules
# strictly inside disqualify the cluster from being treated as a code
# frame.
_STRIPE_INTERIOR_INSET = 1.5

# A page.line must be at least this wide (for horizontal rules) or tall
# (for vertical rules) to count as a real structural divider. Filters
# tiny stray segments without affecting real table rules.
_STRIPE_MIN_RULE_DIM = 10.0

# Substrings (case-insensitive) that strongly indicate a monospace font
# in a PDF font name. Subset prefixes such as ``RZDOWE+FiraMono-Regular``
# are handled by substring match. Not exhaustive -- the width-uniformity
# fallback covers fonts whose subset names obscure their identity.
_MONOSPACE_FONT_NAME_HINTS = (
    "mono", "courier", "consolas", "menlo", "monaco", "cascadia",
    "jetbrains", "inconsolata", "sourcecode", "typewriter", "fixedsys",
)

# Fraction of chars whose font name must match a monospace hint before
# the region is judged monospace by name. Below this, the width-CV
# fallback runs.
_STRIPE_MONO_NAME_FRACTION = 0.8

# Coefficient of variation (std / mean) of char advance widths below
# which a region's text is judged uniform-width and therefore monospace.
# Set conservatively -- proportional fonts typically exceed 0.25.
_STRIPE_MONO_WIDTH_CV = 0.15

# Minimum char count inside a stripe region before the monospace test
# returns a meaningful answer. Below this, the helper conservatively
# returns False and leaves the TABLE classification untouched.
_STRIPE_MONO_MIN_CHARS = 20

# Minimum fraction of a TABLE candidate's bbox that must lie inside a
# stripe-frame region for the candidate to be suppressed. High enough
# that a real table merely abutting a code frame is not affected.
_STRIPE_TABLE_OVERLAP_RATIO = 0.8


def _is_monospace_font_name(name: Optional[str]) -> bool:
    if not name:
        return False
    lower = name.lower()
    return any(h in lower for h in _MONOSPACE_FONT_NAME_HINTS)


def _region_is_predominantly_monospace(
    page,
    region: Tuple[float, float, float, float],
) -> bool:
    """True when text inside ``region`` is overwhelmingly monospace.

    Uses font-name substring match as the primary signal and falls back
    to a width-uniformity (CV) test for PDFs whose embedded font subsets
    have anonymised names. Either passing condition returns True; both
    failing -- or insufficient content -- returns False so callers leave
    the existing classification untouched when uncertain.
    """
    try:
        chars = page.chars or []
    except Exception:
        return False
    rx0, ry0, rx1, ry1 = region
    inside: List[Dict[str, Any]] = []
    for c in chars:
        try:
            cx0 = float(c["x0"]); cx1 = float(c["x1"])
            ct = float(c["top"]); cb = float(c["bottom"])
        except (KeyError, ValueError, TypeError):
            continue
        if (cx0 >= rx0 - 1 and cx1 <= rx1 + 1
                and ct >= ry0 - 1 and cb <= ry1 + 1):
            inside.append(c)
    if len(inside) < _STRIPE_MONO_MIN_CHARS:
        return False

    mono_named = sum(
        1 for c in inside if _is_monospace_font_name(c.get("fontname"))
    )
    if mono_named >= _STRIPE_MONO_NAME_FRACTION * len(inside):
        return True

    widths: List[float] = []
    for c in inside:
        text = (c.get("text") or "").strip()
        if not text:
            continue
        try:
            w = float(c["x1"]) - float(c["x0"])
        except (KeyError, ValueError, TypeError):
            continue
        if w > 0:
            widths.append(w)
    if len(widths) < _STRIPE_MONO_MIN_CHARS:
        return False
    mean = sum(widths) / len(widths)
    if mean <= 0:
        return False
    var = sum((w - mean) ** 2 for w in widths) / len(widths)
    std = var ** 0.5
    return (std / mean) < _STRIPE_MONO_WIDTH_CV


def _detect_uniform_fill_stripe_regions(
    page,
) -> List[Tuple[float, float, float, float]]:
    """Identify code-block striped-frame regions to exempt from table detection.

    Returns one bbox per detected cluster. A cluster qualifies only when
    all three gates documented at the top of this section pass:
    structural stripe geometry, absence of internal rules, and
    predominantly monospace content.
    """
    try:
        rects = list(page.rects or [])
    except Exception:
        return []

    candidates: List[
        Tuple[float, float, float, float, Tuple[float, ...]]
    ] = []
    for rc in rects:
        try:
            x0 = float(rc["x0"]); x1 = float(rc["x1"])
            y0 = float(rc["top"]); y1 = float(rc["bottom"])
        except (KeyError, ValueError, TypeError):
            continue
        if x1 - x0 < 20.0 or y1 - y0 < 1.0:
            continue
        if not rc.get("fill"):
            continue
        fill = rc.get("non_stroking_color")
        if fill is None:
            continue
        if isinstance(fill, (int, float)):
            color: Tuple[float, ...] = (float(fill),)
        else:
            try:
                color = tuple(float(c) for c in fill)
            except (TypeError, ValueError):
                continue
        candidates.append((x0, y0, x1, y1, color))

    if len(candidates) < _STRIPE_MIN_COUNT:
        return []

    buckets: Dict[
        Tuple[int, int, Tuple[int, ...]],
        List[Tuple[float, float, float, float]],
    ] = {}
    for x0, y0, x1, y1, color in candidates:
        key = (
            int(round(x0 / _STRIPE_X_TOL)),
            int(round(x1 / _STRIPE_X_TOL)),
            tuple(int(round(c / _STRIPE_COLOR_TOL)) for c in color),
        )
        buckets.setdefault(key, []).append((x0, y0, x1, y1))

    raw_clusters: List[Tuple[float, float, float, float]] = []
    for entries in buckets.values():
        if len(entries) < _STRIPE_MIN_COUNT:
            continue
        entries.sort(key=lambda e: e[1])
        run: List[Tuple[float, float, float, float]] = [entries[0]]
        for cur in entries[1:]:
            prev = run[-1]
            if cur[1] - prev[3] <= _STRIPE_GAP_TOL:
                run.append(cur)
            else:
                if len(run) >= _STRIPE_MIN_COUNT:
                    raw_clusters.append((
                        min(r[0] for r in run),
                        min(r[1] for r in run),
                        max(r[2] for r in run),
                        max(r[3] for r in run),
                    ))
                run = [cur]
        if len(run) >= _STRIPE_MIN_COUNT:
            raw_clusters.append((
                min(r[0] for r in run),
                min(r[1] for r in run),
                max(r[2] for r in run),
                max(r[3] for r in run),
            ))

    if not raw_clusters:
        return []

    try:
        lines = list(page.lines or [])
    except Exception:
        lines = []

    def _has_internal_rule(region: Tuple[float, float, float, float]) -> bool:
        rx0, ry0, rx1, ry1 = region
        for ln in lines:
            try:
                lx0 = float(ln["x0"]); lx1 = float(ln["x1"])
                ly0 = float(ln["top"]); ly1 = float(ln["bottom"])
            except (KeyError, ValueError, TypeError):
                continue
            lw = lx1 - lx0
            lh = ly1 - ly0
            # Internal vertical rule (column divider).
            if lw <= 1.0 and lh >= _STRIPE_MIN_RULE_DIM:
                vx = (lx0 + lx1) / 2.0
                if (rx0 + _STRIPE_INTERIOR_INSET <= vx
                        <= rx1 - _STRIPE_INTERIOR_INSET
                        and ly0 <= ry1 + 1 and ly1 >= ry0 - 1):
                    return True
            # Internal horizontal rule (row divider away from frame top/bottom).
            if lh <= 1.0 and lw >= _STRIPE_MIN_RULE_DIM:
                hy = (ly0 + ly1) / 2.0
                if (ry0 + _STRIPE_INTERIOR_INSET <= hy
                        <= ry1 - _STRIPE_INTERIOR_INSET
                        and lx0 <= rx1 + 1 and lx1 >= rx0 - 1):
                    return True
        return False

    return [
        r for r in raw_clusters
        if not _has_internal_rule(r)
        and _region_is_predominantly_monospace(page, r)
    ]


# --------------------------------------------------------------------------- #
# Step 3: Tables (unchanged from baseline)
# --------------------------------------------------------------------------- #

def _detect_tables(
    page,
    page_w: float,
    page_h: float,
    all_words: List[Dict[str, Any]],
    stripe_frames: Optional[List[Tuple[float, float, float, float]]] = None,
) -> List[LayoutRegion]:
    regions: List[LayoutRegion] = []

    image_bboxes: List[Tuple[float, float, float, float]] = []
    try:
        for im in (page.images or []):
            try:
                ib = (
                    float(im["x0"]), float(im["top"]),
                    float(im["x1"]), float(im["bottom"]),
                )
            except (KeyError, ValueError, TypeError):
                continue
            if not _valid_bbox(ib):
                continue
            iw = ib[2] - ib[0]
            ih = ib[3] - ib[1]
            if iw < 20 or ih < 20:
                continue
            if iw >= 0.9 * page_w and ih >= 0.9 * page_h:
                continue
            image_bboxes.append(ib)
    except Exception:
        pass

    # Striped-fill code-frame regions; see helper docs. Computed at the
    # caller and threaded in so the same set drives both this detector
    # and ``_segment_vector_blocks``. ``None`` means the caller hasn't
    # opted in (back-compat) -- compute it locally in that case.
    if stripe_frames is None:
        stripe_frames = _detect_uniform_fill_stripe_regions(page)

    def _inside_stripe_frame(
        bbox: Tuple[float, float, float, float],
    ) -> bool:
        if not stripe_frames:
            return False
        return any(
            _overlap_ratio(bbox, sf) >= _STRIPE_TABLE_OVERLAP_RATIO
            for sf in stripe_frames
        )

    try:
        tables = page.find_tables()
    except Exception:
        tables = []

    pp_candidates: List[
        Tuple[Tuple[float, float, float, float], Optional[List[List[Optional[str]]]]]
    ] = []
    for t in tables:
        try:
            bbox = tuple(float(v) for v in t.bbox)
        except Exception:
            continue
        if not _valid_bbox(bbox):
            continue
        try:
            grid = t.extract()
        except Exception:
            grid = None
        pp_candidates.append((bbox, grid))

    pp_candidates = _merge_row_tables(pp_candidates, page, page_w, page_h)

    for bbox, grid in pp_candidates:
        if _inside_stripe_frame(bbox):
            continue
        grid = _rescue_columns_if_single(page, bbox, grid, page_w, page_h)
        if not _is_plausible_table(bbox, grid, page_w, page_h, image_bboxes):
            continue
        clean_grid = _trim_empty_rows_cols(grid)
        text = _grid_to_text(clean_grid) if clean_grid else _safe_text_in_bbox(page, bbox)
        regions.append(LayoutRegion(
            type=LayoutRegionType.TABLE,
            bbox=bbox,
            text=text,
            table_grid=clean_grid,
        ))

    vec_boxes = _detect_table_boxes_from_vectors(page, page_w, page_h)
    for box in vec_boxes:
        if _inside_stripe_frame(box):
            continue
        if any(_iou(box, r.bbox) > 0.3 for r in regions):
            continue
        if any(_overlap_ratio(box, ib) > 0.5 for ib in image_bboxes):
            continue
        n_words = sum(1 for w in all_words if _word_center_in_bbox(w, box))
        if n_words < 4:
            continue
        try:
            sub = page.within_bbox(_clamp_bbox(box, page_w, page_h))
            grid = sub.extract_table()
        except Exception:
            grid = None
        if not _is_plausible_table(box, grid, page_w, page_h, image_bboxes):
            continue
        clean_grid = _trim_empty_rows_cols(grid)
        text = _grid_to_text(clean_grid) if clean_grid else _safe_text_in_bbox(page, box)
        regions.append(LayoutRegion(
            type=LayoutRegionType.TABLE,
            bbox=box,
            text=text,
            table_grid=clean_grid,
        ))

    n_hor_rules = 0
    try:
        for ln in (page.lines or []):
            try:
                lw = float(ln["x1"]) - float(ln["x0"])
                lh = float(ln["bottom"]) - float(ln["top"])
            except (KeyError, ValueError, TypeError):
                continue
            if lh < 2.0 and lw > 10.0:
                n_hor_rules += 1
    except Exception:
        pass

    # Borderless detection is a last-resort fallback for pages where
    # pdfplumber's primary table detection found NO candidates at all.
    # The earlier gate of ``not regions`` was too permissive: it fired
    # whenever pdfplumber DID find candidates but they were all rejected
    # by ``_is_plausible_table`` (e.g. legitimate single-column "value
    # list" tables). On rule-heavy pages with several such small tables
    # the fallback then synthesised page-wide bogus tables by chaining
    # prose rows that mention numbers with the real table rows.
    #
    # Gating on the raw upstream candidate lists preserves the original
    # intent (recover tables that pdfplumber missed entirely) while
    # cleanly avoiding that regression: if any upstream path produced a
    # candidate -- plausible or not -- it is evidence of structured
    # tables on the page and we trust those paths over the heuristic.
    if (
        n_hor_rules >= 20
        and not pp_candidates
        and not vec_boxes
    ):
        borderless = _detect_borderless_tables(
            page, page_w, page_h, all_words, image_bboxes,
        )
        regions.extend(borderless)

    return regions


def _reextract_grid_for_bbox(
    page,
    bbox: Tuple[float, float, float, float],
    page_w: float,
    page_h: float,
) -> Optional[List[List[Optional[str]]]]:
    try:
        sub = page.within_bbox(_clamp_bbox(bbox, page_w, page_h))
    except Exception:
        return None
    if sub is None:
        return None

    strategies = [
        None,  # pdfplumber defaults (rule lines)
        {"vertical_strategy": "text",
         "horizontal_strategy": "lines",
         "intersection_tolerance": 5},
        {"vertical_strategy": "text",
         "horizontal_strategy": "text",
         "intersection_tolerance": 5},
    ]
    best: Optional[List[List[Optional[str]]]] = None
    best_cols = 0
    for s in strategies:
        try:
            grid = sub.extract_table(s) if s else sub.extract_table()
        except Exception:
            grid = None
        clean = _trim_empty_rows_cols(grid)
        if not clean or len(clean) < 2 or not clean[0]:
            continue
        n_cols = len(clean[0])
        if n_cols > best_cols:
            best_cols = n_cols
            best = clean
    return best


def _detect_borderless_tables(
    page,
    page_w: float,
    page_h: float,
    all_words: List[Dict[str, Any]],
    image_bboxes: List[Tuple[float, float, float, float]],
) -> List[LayoutRegion]:
    ws_sorted = sorted(
        all_words, key=lambda w: (float(w["top"]), float(w["x0"]))
    )
    y_rows: List[List[Dict[str, Any]]] = []
    for w in ws_sorted:
        try:
            cy = (float(w["top"]) + float(w["bottom"])) / 2.0
        except (KeyError, ValueError, TypeError):
            continue
        if y_rows:
            prev = y_rows[-1][-1]
            pcy = (float(prev["top"]) + float(prev["bottom"])) / 2.0
            ref = min(
                float(prev["bottom"]) - float(prev["top"]),
                float(w["bottom"]) - float(w["top"]),
            ) or 1.0
            if abs(cy - pcy) < 0.6 * ref:
                y_rows[-1].append(w)
                continue
        y_rows.append([w])

    row_recs: List[Dict[str, Any]] = []
    for line in y_rows:
        if not line:
            continue
        try:
            x0 = min(float(w["x0"]) for w in line)
            x1 = max(float(w["x1"]) for w in line)
            y0 = min(float(w["top"]) for w in line)
            y1 = max(float(w["bottom"]) for w in line)
        except (KeyError, ValueError, TypeError):
            continue
        n_num = 0
        n_words = 0
        for w in line:
            n_words += 1
            t = str(w.get("text", ""))
            if any(ch.isdigit() for ch in t):
                digits = sum(1 for ch in t if ch.isdigit())
                non_num = sum(
                    1 for ch in t
                    if not (ch.isdigit() or ch in "$(),.%\u2014-\u2013 ")
                )
                if digits >= 1 and non_num <= 1:
                    n_num += 1
        row_recs.append({
            "x0": x0, "x1": x1, "y0": y0, "y1": y1,
            "cy": (y0 + y1) / 2.0,
            "h": max(1.0, y1 - y0),
            "n_num": n_num,
            "n_words": n_words,
        })

    if not row_recs:
        return []
    row_recs.sort(key=lambda r: r["cy"])

    num_x0s = [r["x0"] for r in row_recs if r["n_num"] >= 1]
    if num_x0s:
        data_left = min(num_x0s)
    else:
        data_left = min((r["x0"] for r in row_recs), default=0.0)

    def _row_kind(r: Dict[str, Any]) -> str:
        if r["n_num"] >= 1:
            return "tabular"
        indent = r["x0"] - data_left
        width = r["x1"] - r["x0"]
        if indent > 40.0:
            return "prose"
        if width > 0.7 * page_w and r["n_words"] >= 6:
            return "prose"
        return "structural"

    bands: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    pending_struct = 0
    prev: Optional[Dict[str, Any]] = None
    for r in row_recs:
        gap = (r["cy"] - prev["cy"]) if prev else 0.0
        ref_h = r["h"]
        kind = _row_kind(r)
        big_gap = prev is not None and gap > max(22.0, ref_h * 2.4)
        if kind == "tabular":
            if current and big_gap:
                bands.append(current)
                current = []
            current.append(r)
            pending_struct = 0
        elif kind == "structural":
            if current:
                if big_gap:
                    bands.append(current)
                    current = []
                    pending_struct = 0
                else:
                    pending_struct += 1
                    if pending_struct > 3:
                        bands.append(current)
                        current = []
                        pending_struct = 0
        else:
            if current:
                bands.append(current)
                current = []
            pending_struct = 0
        prev = r
    if current:
        bands.append(current)

    settings_text = {
        "vertical_strategy": "text",
        "horizontal_strategy": "text",
        "intersection_tolerance": 5,
    }
    settings_lines = {
        "vertical_strategy": "text",
        "horizontal_strategy": "lines",
        "intersection_tolerance": 5,
    }

    out: List[LayoutRegion] = []
    for band in bands:
        if len(band) < 2:
            continue

        by0 = min(r["y0"] for r in band)
        by1 = max(r["y1"] for r in band)
        band_h = band[0]["h"]

        def _looks_like_header(rec: Dict[str, Any]) -> bool:
            txt_words = [
                w for w in all_words
                if rec["y0"] - 1 <= (float(w["top"]) + float(w["bottom"])) / 2 <= rec["y1"] + 1
            ]
            txt = " ".join(str(w.get("text", "")) for w in txt_words).lower()
            if not txt.strip():
                return False
            if any(k in txt for k in (
                "months ended", "year ended", "ended", "january", "february",
                "march", "april", "may", "june", "july", "august",
                "september", "october", "november", "december",
            )):
                return True
            toks = txt.split()
            if toks and all(
                t.strip("$(),.%") .isdigit() and len(t.strip("$(),.%")) == 4
                for t in toks
            ):
                return True
            return False

        hdr_top = by0
        idx = row_recs.index(band[0])
        j = idx - 1
        folded = 0
        while j >= 0 and folded < 3:
            cand = row_recs[j]
            gap = hdr_top - cand["y1"]
            if gap > max(14.0, band_h * 1.5):
                break
            if not _looks_like_header(cand):
                break
            hdr_top = cand["y0"]
            j -= 1
            folded += 1

        data_x0 = min(r["x0"] for r in band)
        data_x1 = max(r["x1"] for r in band)
        hdr_words = [
            w for w in all_words
            if hdr_top - 2 <= (float(w["top"]) + float(w["bottom"])) / 2 < by0 - 1
            and float(w["x0"]) >= data_x0 - 2.0
        ]
        if hdr_words:
            data_x1 = max(data_x1, max(float(w["x1"]) for w in hdr_words))

        bbox = _clamp_bbox(
            (data_x0 - 2.0, hdr_top - 2.0, data_x1 + 2.0, by1 + 2.0),
            page_w, page_h,
        )

        if any(_overlap_ratio(bbox, ib) > 0.5 for ib in image_bboxes):
            continue

        best_clean: Optional[List[List[Optional[str]]]] = None
        best_cols = 0
        try:
            sub = page.within_bbox(bbox)
        except Exception:
            sub = None
        if sub is not None:
            for s in (settings_text, settings_lines):
                try:
                    grid = sub.extract_table(s)
                except Exception:
                    grid = None
                clean = _trim_empty_rows_cols(grid)
                if not clean or len(clean) < 2 or not clean[0]:
                    continue
                n_cols = len(clean[0])
                if n_cols > best_cols:
                    best_cols = n_cols
                    best_clean = clean

        clean = best_clean
        if not clean or len(clean) < 2 or not clean[0] or len(clean[0]) < 2:
            continue
        total = sum(len(r) for r in clean)
        filled = sum(1 for r in clean for c in r if c and str(c).strip())
        if total <= 0 or filled / total < 0.35:
            continue
        text = _grid_to_text(clean)
        out.append(LayoutRegion(
            type=LayoutRegionType.TABLE,
            bbox=bbox,
            text=text,
            table_grid=clean,
        ))
    return out


def _detect_table_boxes_from_vectors(
    page,
    page_w: float,
    page_h: float,
) -> List[Tuple[float, float, float, float]]:
    min_len = max(20.0, page_w * 0.02)
    h_segs: List[Tuple[float, float, float]] = []
    v_segs: List[Tuple[float, float, float]] = []

    try:
        lines = page.lines or []
    except Exception:
        lines = []
    for ln in lines:
        try:
            x0 = float(ln["x0"]); x1 = float(ln["x1"])
            y0 = float(ln["top"]); y1 = float(ln["bottom"])
        except (KeyError, ValueError, TypeError):
            continue
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        dx, dy = x1 - x0, y1 - y0
        if dy <= 2.0 and dx >= min_len:
            h_segs.append((x0, x1, (y0 + y1) / 2.0))
        elif dx <= 2.0 and dy >= min_len:
            v_segs.append((y0, y1, (x0 + x1) / 2.0))

    try:
        rects = page.rects or []
    except Exception:
        rects = []
    for rc in rects:
        try:
            x0 = float(rc["x0"]); x1 = float(rc["x1"])
            y0 = float(rc["top"]); y1 = float(rc["bottom"])
        except (KeyError, ValueError, TypeError):
            continue
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        if (x1 - x0) < min_len or (y1 - y0) < 5.0:
            continue
        h_segs.append((x0, x1, y0))
        h_segs.append((x0, x1, y1))
        v_segs.append((y0, y1, x0))
        v_segs.append((y0, y1, x1))

    if len(h_segs) < 2 or len(v_segs) < 2:
        return []

    n_h, n_v = len(h_segs), len(v_segs)
    parent = list(range(n_h + n_v))

    def _find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def _union(i: int, j: int) -> None:
        ri, rj = _find(i), _find(j)
        if ri != rj:
            parent[ri] = rj

    tol = 3.0
    for i, (hx0, hx1, hy) in enumerate(h_segs):
        for j, (vy0, vy1, vx) in enumerate(v_segs):
            if (hx0 - tol) <= vx <= (hx1 + tol) and (vy0 - tol) <= hy <= (vy1 + tol):
                _union(i, n_h + j)

    comp: Dict[int, Dict[str, Any]] = {}
    for i, (hx0, hx1, hy) in enumerate(h_segs):
        r = _find(i)
        d = comp.setdefault(r, {"x0": hx0, "x1": hx1, "y0": hy, "y1": hy,
                                "nh": 0, "nv": 0})
        d["x0"] = min(d["x0"], hx0); d["x1"] = max(d["x1"], hx1)
        d["y0"] = min(d["y0"], hy);  d["y1"] = max(d["y1"], hy)
        d["nh"] += 1
    for j, (vy0, vy1, vx) in enumerate(v_segs):
        r = _find(n_h + j)
        d = comp.setdefault(r, {"x0": vx, "x1": vx, "y0": vy0, "y1": vy1,
                                "nh": 0, "nv": 0})
        d["x0"] = min(d["x0"], vx);  d["x1"] = max(d["x1"], vx)
        d["y0"] = min(d["y0"], vy0); d["y1"] = max(d["y1"], vy1)
        d["nv"] += 1

    out: List[Tuple[float, float, float, float]] = []
    for d in comp.values():
        if d["nh"] < 2 or d["nv"] < 2:
            continue
        x0, y0, x1, y1 = d["x0"], d["y0"], d["x1"], d["y1"]
        if (x1 - x0) < 30.0 or (y1 - y0) < 20.0:
            continue
        pad = 1.0
        out.append((max(0.0, x0 - pad), max(0.0, y0 - pad),
                    min(page_w, x1 + pad), min(page_h, y1 + pad)))
    return out


def _trim_paragraph_rows(
    grid: List[List[Optional[str]]],
    bbox: Tuple[float, float, float, float],
) -> Tuple[List[List[Optional[str]]], Tuple[float, float, float, float]]:
    if not grid:
        return grid, bbox

    def _looks_like_paragraph(row: List[Optional[str]]) -> bool:
        non_empty = [c.strip() for c in row if c and str(c).strip()]
        if not non_empty:
            return False
        long_cells = sum(1 for c in non_empty if len(c) >= 8)
        if long_cells * 2 >= len(non_empty):
            return True
        if len(non_empty) <= 3 and any(
            c.endswith((".", ",", ";", ":")) for c in non_empty
        ):
            return True
        return False

    def _is_empty(row: List[Optional[str]]) -> bool:
        return not any(c and str(c).strip() for c in row)

    n_top = 0
    n_bot = 0
    rows = list(grid)
    while rows and (_is_empty(rows[0]) or _looks_like_paragraph(rows[0])):
        rows.pop(0)
        n_top += 1
    while rows and (_is_empty(rows[-1]) or _looks_like_paragraph(rows[-1])):
        rows.pop()
        n_bot += 1

    if not rows:
        return grid, bbox
    if n_top == 0 and n_bot == 0:
        return grid, bbox

    orig_n = len(grid)
    row_h = (bbox[3] - bbox[1]) / max(1, orig_n)
    new_y0 = bbox[1] + n_top * row_h
    new_y1 = bbox[3] - n_bot * row_h
    return rows, (bbox[0], new_y0, bbox[2], new_y1)


def _merge_row_tables(
    candidates,
    page,
    page_w: float,
    page_h: float,
):
    if not candidates:
        return []

    singles = []
    others = []
    for bbox, grid in candidates:
        if grid and len(grid) == 1 and grid[0] and len(grid[0]) >= 2:
            singles.append((bbox, grid))
        else:
            others.append((bbox, grid))

    groups = defaultdict(list)
    for bbox, grid in singles:
        key = (round(bbox[0] / 5) * 5, round(bbox[2] / 5) * 5)
        groups[key].append((bbox, grid))

    result = list(others)
    for key, members in groups.items():
        members.sort(key=lambda x: x[0][1])

        chains = [[members[0]]]
        for entry in members[1:]:
            prev = chains[-1][-1]
            prev_h = prev[0][3] - prev[0][1]
            gap = entry[0][1] - prev[0][3]
            if gap < max(prev_h * 3.0, 30.0):
                chains[-1].append(entry)
            else:
                chains.append([entry])

        for chain in chains:
            x0 = min(e[0][0] for e in chain)
            y0 = min(e[0][1] for e in chain)
            x1 = max(e[0][2] for e in chain)
            y1 = max(e[0][3] for e in chain)
            merged_grid = []
            for _, g in chain:
                merged_grid.extend(g)
            n_rows_merged = len(merged_grid)
            n_cols_merged = len(merged_grid[0]) if merged_grid else 0
            row_h = max(8.0, max((c[0][3] - c[0][1]) for c in chain))

            do_expand = True
            if len(chain) == 1:
                bb = chain[0][0]
                if (bb[3] - bb[1]) > 30.0 or n_cols_merged < 3:
                    do_expand = False

            expanded_y0 = max(0.0, y0 - row_h * 5.0)
            if len(chain) >= 2:
                expanded_x0 = max(0.0, x0 - 70.0)
            else:
                expanded_x0 = x0
            expanded_bbox = (expanded_x0, expanded_y0, x1, y1)
            if do_expand:
                n_curves = 0
                for c in (page.curves or []):
                    try:
                        cx = (float(c["x0"]) + float(c["x1"])) / 2
                        cy = (float(c["top"]) + float(c["bottom"])) / 2
                    except (KeyError, ValueError, TypeError):
                        continue
                    if (expanded_bbox[0] <= cx <= expanded_bbox[2]
                            and expanded_bbox[1] <= cy <= expanded_bbox[3]):
                        n_curves += 1
                        if n_curves >= 5:
                            do_expand = False
                            break

            best_bbox = None
            best_grid = None
            best_score = (n_rows_merged, n_cols_merged) if n_rows_merged >= 2 else (0, 0)

            if do_expand:
                strategies = [
                    {"vertical_strategy": "text",
                     "horizontal_strategy": "lines",
                     "intersection_tolerance": 5},
                    {"vertical_strategy": "text",
                     "horizontal_strategy": "text",
                     "intersection_tolerance": 5},
                ]
                try:
                    sub = page.within_bbox(_clamp_bbox(expanded_bbox, page_w, page_h))
                except Exception:
                    sub = None
                if sub is not None:
                    for s in strategies:
                        try:
                            alt_tables = sub.find_tables(s)
                        except Exception:
                            alt_tables = []
                        for at in alt_tables:
                            try:
                                at_bbox = tuple(float(v) for v in at.bbox)
                                at_grid = at.extract()
                            except Exception:
                                continue
                            if not _valid_bbox(at_bbox) or not at_grid:
                                continue
                            if not (at_bbox[1] <= y0 + row_h and at_bbox[3] >= y1 - row_h):
                                continue
                            at_grid, at_bbox = _trim_paragraph_rows(at_grid, at_bbox)
                            trimmed = _trim_empty_rows_cols(at_grid)
                            if not trimmed or not trimmed[0]:
                                continue
                            score = (len(trimmed), len(trimmed[0]))
                            if score[0] < max(2, n_rows_merged):
                                continue
                            if score[1] < 2:
                                continue
                            if score > best_score:
                                best_score = score
                                best_bbox = at_bbox
                                best_grid = at_grid

            if best_grid is not None and best_bbox is not None:
                final_bbox = (
                    min(best_bbox[0], x0),
                    min(best_bbox[1], y0),
                    max(best_bbox[2], x1),
                    max(best_bbox[3], y1),
                )
                result.append((final_bbox, best_grid))
                continue

            if len(chain) >= 2:
                result.append(((x0, y0, x1, y1), merged_grid))
            else:
                result.extend(chain)

    return result


def _rescue_columns_if_single(
    page,
    bbox: Tuple[float, float, float, float],
    grid,
    page_w: float,
    page_h: float,
):
    trimmed = _trim_empty_rows_cols(grid)
    if trimmed and trimmed[0] and len(trimmed[0]) >= 2:
        return grid

    try:
        n_curves = 0
        for c in (page.curves or []):
            try:
                cx = (float(c["x0"]) + float(c["x1"])) / 2
                cy = (float(c["top"]) + float(c["bottom"])) / 2
                if bbox[0] <= cx <= bbox[2] and bbox[1] <= cy <= bbox[3]:
                    n_curves += 1
                    if n_curves >= 5:
                        return grid
            except (KeyError, ValueError, TypeError):
                continue
    except Exception:
        pass

    def _try(settings):
        try:
            sub = page.within_bbox(_clamp_bbox(bbox, page_w, page_h))
            return sub.extract_table(settings)
        except Exception:
            return None

    alt = _try({
        "vertical_strategy": "text",
        "horizontal_strategy": "lines",
        "intersection_tolerance": 5,
    })
    if not alt:
        alt = _try({
            "vertical_strategy": "text",
            "horizontal_strategy": "text",
            "intersection_tolerance": 5,
        })
    if not alt:
        return grid

    alt_trim = _trim_empty_rows_cols(alt)
    orig_cols = len(trimmed[0]) if (trimmed and trimmed[0]) else 0
    alt_cols = len(alt_trim[0]) if (alt_trim and alt_trim[0]) else 0
    return alt if alt_cols > orig_cols else grid


def _trim_empty_rows_cols(grid):
    if not grid or not grid[0]:
        return grid
    rows = [
        row for row in grid
        if any(c is not None and str(c).strip() for c in row)
    ]
    if not rows:
        return []
    n_cols = max(len(r) for r in rows)
    rows = [list(r) + [None] * (n_cols - len(r)) for r in rows]
    keep_cols = [
        ci for ci in range(n_cols)
        if any(r[ci] is not None and str(r[ci]).strip() for r in rows)
    ]
    if not keep_cols:
        return []
    return [[row[ci] for ci in keep_cols] for row in rows]


def _is_plausible_table(
    bbox,
    grid,
    page_w: float,
    page_h: float,
    image_bboxes,
) -> bool:
    x0, y0, x1, y1 = bbox
    bw, bh = x1 - x0, y1 - y0
    if bw <= 0 or bh <= 0:
        return False
    if bw > 0.85 * page_w and bh > 0.7 * page_h:
        return False
    trimmed = _trim_empty_rows_cols(grid)
    if not trimmed or len(trimmed) < 2 or len(trimmed[0]) < 2:
        return False
    existing = sum(1 for row in trimmed for c in row if c is not None)
    filled   = sum(1 for row in trimmed for c in row if c and str(c).strip())
    if existing <= 0:
        return False
    fill_ratio = filled / existing
    if fill_ratio < 0.5:
        n_rows = len(trimmed)
        col_fill = [
            sum(1 for row in trimmed if row[ci] and str(row[ci]).strip())
            for ci in range(len(trimmed[0]))
        ]
        dense_cols = [
            ci for ci, cf in enumerate(col_fill) if cf >= max(2, n_rows * 0.25)
        ]
        rescued = False
        if len(dense_cols) >= 2:
            d_total = n_rows * len(dense_cols)
            d_filled = sum(
                1 for row in trimmed for ci in dense_cols
                if row[ci] and str(row[ci]).strip()
            )
            if d_total > 0 and d_filled / d_total >= 0.5:
                rescued = True
        if not rescued:
            return False
    for ib in image_bboxes:
        if _overlap_ratio(bbox, ib) > 0.5:
            return False
    return True


# --------------------------------------------------------------------------- #
# Step 4: Images (unchanged from baseline)
# --------------------------------------------------------------------------- #

def _refine_image_bbox(pp_bbox, cv_blocks, all_words, page_w, page_h):
    clipped = _clamp_bbox(pp_bbox, page_w, page_h)
    if not _valid_bbox(clipped):
        return []
    refined = []
    for cv in cv_blocks:
        cv_w = cv[2] - cv[0]
        cv_h = cv[3] - cv[1]
        cv_area = max(1.0, cv_w * cv_h)
        words_in = [w for w in all_words if _word_center_in_bbox(w, cv)]
        if words_in:
            total_word_area = 0.0
            for w in words_in:
                try:
                    ww = max(0.0, float(w["x1"]) - float(w["x0"]))
                    wh = max(0.0, float(w["bottom"]) - float(w["top"]))
                    total_word_area += ww * wh
                except (KeyError, ValueError, TypeError):
                    continue
            if total_word_area / cv_area > 0.10:
                continue
        ix0 = max(clipped[0], cv[0])
        iy0 = max(clipped[1], cv[1])
        ix1 = min(clipped[2], cv[2])
        iy1 = min(clipped[3], cv[3])
        if ix1 - ix0 < 5 or iy1 - iy0 < 5:
            continue
        inter_area = (ix1 - ix0) * (iy1 - iy0)
        if inter_area / cv_area < 0.3:
            continue
        refined.append((ix0, iy0, ix1, iy1))
    return refined if refined else [clipped]


def _trim_image_text_margins(bbox, all_words):
    x0, y0, x1, y1 = bbox
    bh = y1 - y0
    if bh <= 0:
        return bbox

    rows = []
    for w in all_words:
        try:
            wx0 = float(w["x0"]); wx1 = float(w["x1"])
            wt = float(w["top"]); wb = float(w["bottom"])
        except (KeyError, ValueError, TypeError):
            continue
        wcx = (wx0 + wx1) / 2.0
        if wcx < x0 or wcx > x1:
            continue
        if wb <= y0 or wt >= y1:
            continue
        rows.append((wt, wb))
    if not rows:
        return bbox

    rows.sort()
    lines = []
    for wt, wb in rows:
        if lines and wt <= lines[-1][1] + 1.0:
            lt, lb = lines[-1]
            lines[-1] = (min(lt, wt), max(lb, wb))
        else:
            lines.append((wt, wb))

    new_y0, new_y1 = y0, y1
    max_trim = bh * 0.35

    for lt, lb in lines:
        line_h = max(1.0, lb - lt)
        if lt > new_y0 + max_trim:
            break
        if lt <= new_y0 + line_h * 1.5:
            new_y0 = lb
        else:
            break

    for lt, lb in reversed(lines):
        line_h = max(1.0, lb - lt)
        if lb < new_y1 - max_trim:
            break
        if lb >= new_y1 - line_h * 1.5:
            new_y1 = lt
        else:
            break

    if new_y1 - new_y0 < 5.0:
        return bbox
    return (x0, new_y0, x1, new_y1)


def _split_region_by_gutters(img_rgb, scale, bbox, text_rows, depth=0, min_dim=40.0):
    if img_rgb is None or depth > 8:
        return [bbox]
    H, W = img_rgb.shape[:2]
    x0 = max(0, int(round(bbox[0] * scale)))
    y0 = max(0, int(round(bbox[1] * scale)))
    x1 = min(W, int(round(bbox[2] * scale)))
    y1 = min(H, int(round(bbox[3] * scale)))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return [bbox]

    gray = cv2.cvtColor(img_rgb[y0:y1, x0:x1], cv2.COLOR_RGB2GRAY)
    nonwhite = (gray < 245).astype(np.float32)
    gutter_px = max(4, int(round(6 * scale / 1.389)))

    def _interior_gutters(profile, base_px):
        guts = []
        in_g = False
        gs = 0
        n = len(profile)
        for i in range(n):
            white = profile[i] < 0.01
            if white and not in_g:
                in_g = True; gs = i
            elif not white and in_g:
                in_g = False
                if i - gs >= gutter_px and gs > 0:
                    guts.append((gs, i))
        return [((base_px + a) / scale, (base_px + b) / scale) for a, b in guts]

    rowprof = nonwhite.mean(axis=1)
    for (tt, tb) in text_rows:
        a = int(round(tt * scale)) - y0
        b = int(round(tb * scale)) - y0
        a = max(0, a); b = min(len(rowprof), b)
        if b > a:
            rowprof[a:b] = 0.0
    h_gutters = _interior_gutters(rowprof, y0)

    colprof = nonwhite.mean(axis=0)
    v_gutters = _interior_gutters(colprof, x0)

    prefer_h = (depth % 2 == 0)
    if prefer_h and not h_gutters and v_gutters:
        prefer_h = False
    elif not prefer_h and not v_gutters and h_gutters:
        prefer_h = True

    if prefer_h and h_gutters:
        segs = []
        prev = bbox[1]
        for (ga, gb) in h_gutters:
            segs.append((prev, ga))
            prev = gb
        segs.append((prev, bbox[3]))
        out = []
        for (sa, sb) in segs:
            if sb - sa < min_dim:
                continue
            out.extend(_split_region_by_gutters(
                img_rgb, scale, (bbox[0], sa, bbox[2], sb),
                text_rows, depth + 1, min_dim,
            ))
        return out if out else [bbox]

    if v_gutters:
        segs = []
        prev = bbox[0]
        for (ga, gb) in v_gutters:
            segs.append((prev, ga))
            prev = gb
        segs.append((prev, bbox[2]))
        out = []
        for (sa, sb) in segs:
            if sb - sa < min_dim:
                continue
            out.extend(_split_region_by_gutters(
                img_rgb, scale, (sa, bbox[1], sb, bbox[3]),
                text_rows, depth + 1, min_dim,
            ))
        return out if out else [bbox]

    return [bbox]


def _tighten_to_content(img_rgb, scale, bbox):
    if img_rgb is None:
        return bbox
    H, W = img_rgb.shape[:2]
    x0 = max(0, int(round(bbox[0] * scale)))
    y0 = max(0, int(round(bbox[1] * scale)))
    x1 = min(W, int(round(bbox[2] * scale)))
    y1 = min(H, int(round(bbox[3] * scale)))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return bbox
    gray = cv2.cvtColor(img_rgb[y0:y1, x0:x1], cv2.COLOR_RGB2GRAY)
    mask = (gray < 245)
    ys, xs = np.where(mask)
    if len(ys) == 0:
        return None
    nx0 = (x0 + int(xs.min())) / scale
    nx1 = (x0 + int(xs.max()) + 1) / scale
    ny0 = (y0 + int(ys.min())) / scale
    ny1 = (y0 + int(ys.max()) + 1) / scale
    return (nx0, ny0, nx1, ny1)


def _detect_images(page, raster_getter, block_boxes, existing, all_words):
    regions = []

    page_w = float(page.width)
    page_h = float(page.height)

    try:
        page_images = page.images or []
    except Exception:
        page_images = []

    embedded = []
    for im in page_images:
        try:
            bbox = (
                float(im["x0"]),
                float(im["top"]),
                float(im["x1"]),
                float(im["bottom"]),
            )
        except (KeyError, ValueError, TypeError):
            continue
        if not _valid_bbox(bbox):
            continue
        bw = bbox[2] - bbox[0]
        bh = bbox[3] - bbox[1]
        if bw < 20 or bh < 20:
            continue
        if bw >= 0.9 * page_w and bh >= 0.9 * page_h:
            continue
        embedded.append(bbox)

    clamped = [_clamp_bbox(b, page_w, page_h) for b in embedded]
    n_emb = len(clamped)
    parent = list(range(n_emb))

    def _find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n_emb):
        for j in range(i + 1, n_emb):
            if _overlap_ratio(clamped[i], clamped[j]) > 0.15:
                ri, rj = _find(i), _find(j)
                if ri != rj:
                    parent[ri] = rj

    clusters = {}
    for i in range(n_emb):
        clusters.setdefault(_find(i), []).append(i)

    text_rows = []
    for w in all_words:
        try:
            text_rows.append((float(w["top"]), float(w["bottom"])))
        except (KeyError, ValueError, TypeError):
            continue

    candidate_bboxes = []
    for members in clusters.values():
        if len(members) >= 2:
            ux0 = min(clamped[m][0] for m in members)
            uy0 = min(clamped[m][1] for m in members)
            ux1 = max(clamped[m][2] for m in members)
            uy1 = max(clamped[m][3] for m in members)
            union = (ux0, uy0, ux1, uy1)
            try:
                img_rgb, scale = raster_getter()
            except Exception:
                img_rgb, scale = None, 1.0
            tiles = _split_region_by_gutters(
                img_rgb, scale, union, text_rows,
            )
            for t in tiles:
                tight = _tighten_to_content(img_rgb, scale, t) if img_rgb is not None else t
                if tight is None:
                    continue
                if _valid_bbox(tight) and (tight[2] - tight[0]) >= 20 and (tight[3] - tight[1]) >= 20:
                    candidate_bboxes.append(tight)
        else:
            m = members[0]
            src = embedded[m]
            out_of_bounds = (
                src[0] < -1.0 or src[1] < -1.0
                or src[2] > page_w + 1.0 or src[3] > page_h + 1.0
            )
            if out_of_bounds:
                refined = _refine_image_bbox(
                    src, block_boxes, all_words, page_w, page_h,
                )
            else:
                refined = [clamped[m]]
            for rb in refined:
                tr = _trim_image_text_margins(rb, all_words)
                if tr is not None and _valid_bbox(tr):
                    rb = tr
                candidate_bboxes.append(rb)

    for r_bbox in candidate_bboxes:
        if any(_overlap_ratio(r_bbox, r.bbox) > 0.5 for r in existing):
            continue
        if any(_overlap_ratio(r_bbox, r.bbox) > 0.5 for r in regions):
            continue
        regions.append(LayoutRegion(
            type=LayoutRegionType.IMAGE,
            bbox=r_bbox,
        ))

    try:
        vector_objects = (
            list(page.lines or [])
            + list(page.rects or [])
            + list(page.curves or [])
        )
    except Exception:
        vector_objects = []

    for box in block_boxes:
        if any(_overlap_ratio(box, r.bbox) > 0.5 for r in regions + existing):
            continue
        area = (box[2] - box[0]) * (box[3] - box[1])
        if area < 4000:
            continue
        words_in = [w for w in all_words if _word_center_in_bbox(w, box)]
        total_word_area = 0.0
        for w in words_in:
            try:
                ww = max(0.0, float(w["x1"]) - float(w["x0"]))
                wh = max(0.0, float(w["bottom"]) - float(w["top"]))
                total_word_area += ww * wh
            except (KeyError, ValueError, TypeError):
                continue
        word_density = total_word_area / area

        if word_density > 0.30:
            continue

        n_vectors = 0
        for v in vector_objects:
            if not _word_center_in_bbox(v, box):
                continue
            try:
                vw = float(v["x1"]) - float(v["x0"])
                vh = float(v["bottom"]) - float(v["top"])
            except (KeyError, ValueError, TypeError):
                continue
            if vh < 3.0 and vw > 5.0 and vw > vh * 2.0:
                continue
            n_vectors += 1

        is_figure = False
        if n_vectors >= 15:
            is_figure = True
        elif n_vectors >= 5 and word_density < 0.20:
            is_figure = True
        else:
            if word_density < 0.10 and n_vectors == 0:
                img_rgb, scale = raster_getter()
                density = _ink_density(img_rgb, box, scale)
                if density >= 0.05:
                    is_figure = True

        if is_figure:
            regions.append(LayoutRegion(
                type=LayoutRegionType.IMAGE,
                bbox=box,
            ))

    return regions


# --------------------------------------------------------------------------- #
# Step 5: Text / List
# --------------------------------------------------------------------------- #

def _classify_text_blocks(
    block_boxes,
    higher_priority,
    all_words,
    gutter_x: Optional[float] = None,
):
    regions = []

    for box in block_boxes:
        if any(_overlap_ratio(box, r.bbox) > 0.5 for r in higher_priority):
            continue

        words = [w for w in all_words if _word_center_in_bbox(w, box)]
        if not words:
            continue

        lines, _ = _group_words_into_lines(words, gutter_x=gutter_x)
        if not lines:
            continue

        runs = _split_lines_into_runs(lines)

        for run_type, run_lines in runs:
            if not run_lines:
                continue
            run_words = [w for ln in run_lines for w in ln]
            tight = _tight_bbox_from_words(run_words, pad=2.0)
            if tight is None:
                continue
            text = "\n".join(
                " ".join(w["text"] for w in ln) for ln in run_lines
            ).strip()
            if not text:
                continue

            sizes = [
                float(w["size"]) for w in run_words
                if w.get("size") is not None
                and str(w.get("fontname", "")) != "_synthetic_bullet"
            ]
            avg_size = sum(sizes) / len(sizes) if sizes else 0.0

            bold_count = sum(
                1 for w in run_words
                if "bold" in str(w.get("fontname", "")).lower()
            )
            is_bold = bold_count > len(run_words) / 2

            if run_type == "list":
                items = _extract_list_items(run_lines)
                if len(items) >= 2:
                    regions.append(LayoutRegion(
                        type=LayoutRegionType.LIST,
                        bbox=tight,
                        text=text,
                        font_size=avg_size,
                        is_bold=is_bold,
                        list_items=items,
                    ))
                    continue
            regions.append(LayoutRegion(
                type=LayoutRegionType.TEXT,
                bbox=tight,
                text=text,
                font_size=avg_size,
                is_bold=is_bold,
            ))

    return regions


def _split_lines_into_runs(lines):
    if not lines:
        return []

    meta = []
    for line in lines:
        if not line:
            meta.append((False, 0.0))
            continue
        text = " ".join(w["text"] for w in line).strip()
        try:
            left_x = min(float(w["x0"]) for w in line)
        except (KeyError, ValueError, TypeError):
            left_x = 0.0
        is_marker = _LIST_PATTERN.match(text) is not None
        meta.append((is_marker, left_x))

    n = len(lines)
    if not any(m[0] for m in meta):
        return [("text", list(lines))]

    runs = []
    current = None

    for i in range(n):
        is_marker, lx = meta[i]
        if is_marker:
            if (current is not None and current[0] == "list"
                    and abs(lx - current[2]) <= 5.0):
                current[1].append(i)
            else:
                if current is not None:
                    runs.append(current)
                current = ["list", [i], lx]
        else:
            if current is not None and current[0] == "list":
                if lx >= current[2] + 2.0:
                    current[1].append(i)
                    continue
                runs.append(current)
                current = ["text", [i], None]
            elif current is not None and current[0] == "text":
                current[1].append(i)
            else:
                current = ["text", [i], None]

    if current is not None:
        runs.append(current)

    finalized = []
    for run in runs:
        rtype, idxs = run[0], run[1]
        if rtype == "list":
            n_markers = sum(1 for i in idxs if meta[i][0])
            if n_markers < 2:
                rtype = "text"
        if finalized and finalized[-1][0] == "text" and rtype == "text":
            finalized[-1] = ("text", finalized[-1][1] + idxs)
        else:
            finalized.append((rtype, idxs))

    return [(t, [lines[i] for i in idxs]) for t, idxs in finalized]


def _extract_list_items(run_lines):
    items = []
    current = None
    for line in run_lines:
        text = " ".join(w["text"] for w in line).strip()
        m = _LIST_PATTERN.match(text)
        if m is not None:
            if current is not None:
                items.append(current.strip())
            current = text[m.end():].strip()
        else:
            if current is not None:
                current = (current + " " + text).strip()
    if current is not None:
        items.append(current.strip())
    return [it for it in items if it]


def _group_words_into_lines(
    words,
    gutter_x: Optional[float] = None,
    gutter_gap_threshold: float = 8.0,
):
    """Group `words` into lines.

    Returns ``(final_lines, raw_line_ids)`` where ``raw_line_ids[i]`` is the
    y-band index that produced ``final_lines[i]`` (used to propagate ``wide``
    across horizontal-gap splits via ``_resolve_line_sides``).

    Two phases:
      1. Vertical grouping by y-center proximity (raw_lines).
      2. Within each raw_line, split into separate lines wherever the
         horizontal gap exceeds a threshold.

    When `gutter_x` is supplied, any gap that straddles the gutter uses a
    stricter threshold (`gutter_gap_threshold`, default 8 pt). This stops
    body words on opposite sides of a real column gutter (typically a
    15-25 pt gap) from being fused into the same line, while leaving wide
    headings (uniform small gaps across the page) intact.
    """
    if not words:
        return [], []
    ws = sorted(words, key=lambda w: (w["top"], w["x0"]))
    raw_lines = [[ws[0]]]
    for w in ws[1:]:
        prev = raw_lines[-1][-1]
        prev_cy = (prev["top"] + prev["bottom"]) / 2
        cur_cy = (w["top"] + w["bottom"]) / 2
        ref_h = min(prev["bottom"] - prev["top"], w["bottom"] - w["top"]) or 1
        if abs(cur_cy - prev_cy) < 0.5 * ref_h:
            raw_lines[-1].append(w)
        else:
            raw_lines.append([w])
    
    final_lines: List[List[Dict[str, Any]]] = []
    raw_line_ids: List[int] = []
    for raw_idx, line in enumerate(raw_lines):
        if len(line) <= 1:
            final_lines.append(line)
            raw_line_ids.append(raw_idx)
            continue
        line.sort(key=lambda w: w["x0"])
        avg_h = sum(
            max(1.0, float(w["bottom"]) - float(w["top"])) for w in line
        ) / len(line)
        gap_threshold = max(8.0, avg_h * 2.0)
        current = [line[0]]
        for w in line[1:]:
            gap = float(w["x0"]) - float(current[-1]["x1"])
            local_threshold = gap_threshold
            if gutter_x is not None:
                prev_cx = (
                    float(current[-1]["x0"]) + float(current[-1]["x1"])
                ) / 2.0
                cur_cx = (float(w["x0"]) + float(w["x1"])) / 2.0
                if prev_cx < gutter_x <= cur_cx:
                    # Crossing the gutter -- use stricter threshold so a
                    # real column gap (typically 15-25 pt) splits but a
                    # heading's uniform 2-5 pt word spacing does not.
                    local_threshold = min(local_threshold, gutter_gap_threshold)
            if gap > local_threshold:
                final_lines.append(current)
                raw_line_ids.append(raw_idx)
                current = [w]
            else:
                current.append(w)
        if current:
            final_lines.append(current)
            raw_line_ids.append(raw_idx)
    return final_lines, raw_line_ids


# --------------------------------------------------------------------------- #
# Step 6: Overlap resolution
# --------------------------------------------------------------------------- #

def _resolve_overlaps(
    regions,
    page=None,
    all_words=None,
    page_w=None,
    page_h=None,
    proximity=_PROXIMITY_MERGE_GAP,
    gutter_x: Optional[float] = None,
):
    """Fold overlapping or near-touching regions into single regions.

    Any overlap > 0 % between two regions in the *same column category*
    (left+left, right+right, wide+wide, or any+any on single-column pages)
    causes them to be merged. Cross-category overlaps (e.g. a left-column
    text against a wide table) are normally *kept as-is* -- they are real
    layout facts and shouldn't be collapsed. The same column-category
    gate applies to the proximity merge that follows.

    The column-category gate is RELAXED only for TABLE+TEXT pairs that are
    essentially touching (edge-to-edge gap <= ``_GUTTER_RELAX_GAP``), so
    column body text can merge with a wide table whose bbox barely sits on
    the other side of the gutter. All other type pairs must match column
    side even when overlapping or within ``proximity``."""

    def area(r):
        return max(0.0, r.bbox[2] - r.bbox[0]) * max(0.0, r.bbox[3] - r.bbox[1])

    def _compatible(r, a, gap: Optional[float] = None):
        # On single-column pages both sides resolve to "any" and the
        # column gate is a no-op, preserving the old unconditional merge
        # behaviour.
        # Plus: a LIST region only merges with another LIST. Stray text
        # or paragraph regions that overlap a list seed their own region
        # instead of being folded into it -- collapsing them would lose
        # the list_items structure. This LIST type gate is never relaxed.
        if (r.type == LayoutRegionType.LIST) != (a.type == LayoutRegionType.LIST):
            return False
        # Same-column-side (gutter) gate; relaxed only for TABLE+TEXT
        # pairs within _GUTTER_RELAX_GAP (see _gutter_relax_merge_pair).
        if _gutter_relax_merge_pair(r, a, gap):
            return True
        return (_region_column_side(r, gutter_x, page_w)
                == _region_column_side(a, gutter_x, page_w))

    def _proximity_partner(r, accepted):
        best_idx = -1
        best_gap = None
        for i, a in enumerate(accepted):
            hgap, vgap = _bbox_gap(r.bbox, a.bbox)
            gap = None
            if _x_projections_overlap(r.bbox, a.bbox) and 0.0 < vgap <= proximity:
                gap = vgap
            elif _y_projections_overlap(r.bbox, a.bbox) and 0.0 < hgap <= proximity:
                gap = hgap
            if gap is None:
                continue
            # _compatible sees the measured gap so it can relax the
            # column-side gate for near-touching pairs.
            if not _compatible(r, a, gap=gap):
                continue
            # A TABLE may not absorb (or be absorbed by) a substantial
            # non-TABLE neighbour via proximity alone. Table+table and
            # non-table+non-table pairs are unaffected.
            if not _table_proximity_neighbor_allowed(r, a):
                continue
            if best_gap is None or gap < best_gap:
                best_gap = gap
                best_idx = i
        return best_idx

    dirty = set()  # id()s of regions produced or affected by a merge

    def _pass(input_regions):
        ordered = sorted(
            input_regions, key=lambda r: (_PRIORITY[r.type], -area(r))
        )
        accepted: List[LayoutRegion] = []
        changed = False
        for r in ordered:
            if area(r) <= 0:
                continue

            # Pick the best overlap partner. Overlapping bboxes pass
            # gap=0 into _compatible; the column-side gate is relaxed
            # only for TABLE+TEXT pairs. The LIST type gate can still
            # block here, and a TABLE may not absorb (or be absorbed by)
            # a large non-TABLE neighbour on a sliver of overlap -- see
            # _table_overlap_with_large_neighbor_allowed. Substantial
            # mutual overlap (one region mostly inside the other) is
            # unaffected and still merges.
            best_idx = -1
            best_ratio = 0.0
            for i, a in enumerate(accepted):
                if not _compatible(r, a, gap=0.0):
                    continue
                if not _table_overlap_with_large_neighbor_allowed(r, a):
                    continue
                ov = _overlap_ratio(r.bbox, a.bbox)
                if ov > best_ratio:
                    best_ratio = ov
                    best_idx = i

            if best_idx != -1 and best_ratio > 0.0:
                partner = accepted[best_idx]
                # Normally the already-accepted ``partner`` is the
                # higher-priority region and remains the merge primary.
                # Exception: when a TABLE is almost entirely enclosed by
                # a TEXT region, the table is treated as a false positive
                # inside a larger text block -- the TEXT region becomes
                # the primary so the merged result is TEXT-typed and the
                # table_grid is discarded. Given the priority sort
                # (TABLE before TEXT), the contained-TABLE always lives
                # in ``accepted`` while ``r`` is the enclosing TEXT.
                if _table_contained_in_text(partner, r):
                    merged = _merge_regions(r, partner)
                else:
                    merged = _merge_regions(partner, r)
                dirty.add(id(merged))
                accepted[best_idx] = merged
                changed = True
                continue

            pidx = _proximity_partner(r, accepted)
            if pidx != -1:
                merged = _merge_regions(accepted[pidx], r)
                dirty.add(id(merged))
                accepted[pidx] = merged
                changed = True
                continue

            accepted.append(r)
        return accepted, changed

    current = list(regions)
    for _ in range(8):
        current, changed = _pass(current)
        if not changed:
            break

    for i, r in enumerate(current):
        if id(r) in dirty:
            current[i] = _reextract_region_payload(
                r, page, all_words, page_w, page_h, gutter_x=gutter_x,
            )

    return current


def _merge_regions(primary: LayoutRegion, other: LayoutRegion) -> LayoutRegion:
    new_bbox = (
        min(primary.bbox[0], other.bbox[0]),
        min(primary.bbox[1], other.bbox[1]),
        max(primary.bbox[2], other.bbox[2]),
        max(primary.bbox[3], other.bbox[3]),
    )

    new_text = primary.text or ""
    o_text = (other.text or "").strip()
    if o_text and o_text not in new_text:
        new_text = (new_text + ("\n" if new_text else "") + o_text).strip()

    if primary.type == LayoutRegionType.LIST and other.list_items:
        seen = set(primary.list_items)
        extra = [it for it in other.list_items if it not in seen]
        new_list_items = list(primary.list_items) + extra
    else:
        new_list_items = list(primary.list_items)

    new_image_data = primary.image_data
    if (primary.type == LayoutRegionType.IMAGE
            and new_image_data is not None
            and new_bbox != primary.bbox):
        new_image_data = None

    return LayoutRegion(
        type=primary.type,
        bbox=new_bbox,
        text=new_text,
        font_size=primary.font_size,
        is_bold=primary.is_bold,
        image_data=new_image_data,
        image_ext=primary.image_ext,
        table_grid=primary.table_grid,
        list_items=new_list_items,
    )


def _reextract_region_payload(
    region, page, all_words, page_w, page_h,
    gutter_x: Optional[float] = None,
):
    if region.type == LayoutRegionType.TABLE:
        if page is None:
            return region
        grid = _reextract_grid_for_bbox(page, region.bbox, page_w, page_h)
        if grid:
            region.table_grid = grid
            region.text = _grid_to_text(grid)
        return region

    if region.type in (LayoutRegionType.TEXT, LayoutRegionType.LIST):
        if not all_words:
            return region
        words = [w for w in all_words if _word_center_in_bbox(w, region.bbox)]
        if not words:
            return region
        lines, _ = _group_words_into_lines(words, gutter_x=gutter_x)
        if not lines:
            return region
        run_words = [w for ln in lines for w in ln]

        text = "\n".join(
            " ".join(w["text"] for w in ln) for ln in lines
        ).strip()
        if text:
            region.text = text

        sizes = [
            float(w["size"]) for w in run_words
            if w.get("size") is not None
            and str(w.get("fontname", "")) != "_synthetic_bullet"
        ]
        if sizes:
            region.font_size = sum(sizes) / len(sizes)
        bold_count = sum(
            1 for w in run_words
            if "bold" in str(w.get("fontname", "")).lower()
        )
        region.is_bold = bold_count > len(run_words) / 2

        if region.type == LayoutRegionType.LIST:
            items = _extract_list_items(lines)
            if items:
                region.list_items = items

    return region


# --------------------------------------------------------------------------- #
# Step 6.4: Fold chart furniture (axis labels / legends) into images
# --------------------------------------------------------------------------- #

_IMAGE_LABEL_GAP = 14.0          # max gap (pt) between a label and the image
_IMAGE_LABEL_MAX_AREA_RATIO = 0.25  # a satellite must not dwarf its image


def _is_chart_satellite(
    label: LayoutRegion,
    image: LayoutRegion,
    gap_tol: float = _IMAGE_LABEL_GAP,
) -> bool:
    if label.type not in (LayoutRegionType.TEXT, LayoutRegionType.LIST):
        return False

    a_lab = max(0.0, label.bbox[2] - label.bbox[0]) * max(0.0, label.bbox[3] - label.bbox[1])
    a_img = max(0.0, image.bbox[2] - image.bbox[0]) * max(0.0, image.bbox[3] - image.bbox[1])
    if a_img <= 0 or a_lab > _IMAGE_LABEL_MAX_AREA_RATIO * a_img:
        return False

    if _overlap_ratio(label.bbox, image.bbox) > 0.0:
        return True

    hgap, vgap = _bbox_gap(label.bbox, image.bbox)
    lcx = (label.bbox[0] + label.bbox[2]) / 2.0
    lcy = (label.bbox[1] + label.bbox[3]) / 2.0

    if (_x_projections_overlap(label.bbox, image.bbox)
            and vgap <= gap_tol
            and image.bbox[0] - gap_tol <= lcx <= image.bbox[2] + gap_tol):
        return True
    if (_y_projections_overlap(label.bbox, image.bbox)
            and hgap <= gap_tol
            and image.bbox[1] - gap_tol <= lcy <= image.bbox[3] + gap_tol):
        return True
    return False


def _absorb_labels_into_images(
    regions: List[LayoutRegion],
    page_w: float,
    page_h: float,
    gap_tol: float = _IMAGE_LABEL_GAP,
    gutter_x: Optional[float] = None,
    all_words: Optional[List[Dict[str, Any]]] = None,
) -> List[LayoutRegion]:
    """Fold chart furniture into IMAGE regions.

    On two-column pages, left- or right-column images only absorb labels
    on the same column side; wide (gutter-crossing) images may absorb any
    adjacent label. Single-column pages (``gutter_x`` is ``None``) keep
    the original unconditional merge behaviour. Column side uses
    ``_region_side`` (line-based for TEXT/LIST, bbox-based for IMAGE).
    """
    image_idx = [i for i, r in enumerate(regions)
                 if r.type == LayoutRegionType.IMAGE]
    if not image_idx:
        return regions

    def _area(b):
        return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])

    line_meta = _build_line_side_meta(all_words, gutter_x)

    def _label_side_compatible(label: LayoutRegion, image: LayoutRegion) -> bool:
        if gutter_x is None:
            return True
        image_side = _region_side(image, gutter_x, page_w, line_meta)
        if image_side == "wide":
            return True
        return _region_side(label, gutter_x, page_w, line_meta) == image_side

    image_idx.sort(key=lambda i: -_area(regions[i].bbox))
    consumed: set = set()
    max_area = 0.95 * page_w * page_h

    for ii in image_idx:
        if ii in consumed:
            continue
        changed = True
        while changed:
            changed = False
            for ti, t in enumerate(regions):
                if ti == ii or ti in consumed:
                    continue
                if not _is_chart_satellite(t, regions[ii], gap_tol):
                    continue
                if not _label_side_compatible(t, regions[ii]):
                    continue
                grown = (
                    min(regions[ii].bbox[0], t.bbox[0]),
                    min(regions[ii].bbox[1], t.bbox[1]),
                    max(regions[ii].bbox[2], t.bbox[2]),
                    max(regions[ii].bbox[3], t.bbox[3]),
                )
                if _area(grown) > max_area:
                    continue
                regions[ii] = _merge_regions(regions[ii], t)
                consumed.add(ti)
                changed = True

    return [r for i, r in enumerate(regions) if i not in consumed]


# --------------------------------------------------------------------------- #
# Step 6.6: Detect page text left uncovered by region detection
# --------------------------------------------------------------------------- #
# Words whose centers fall outside every existing region are grouped into
# lines and emitted as standalone TEXT regions. Folding these into nearby
# regions (when appropriate) and absorbing tiny fragments is left to the
# downstream ``_resolve_overlaps`` (proximity merging) and
# ``_merge_small_text_regions`` passes -- this step only synthesizes the
# regions; it does not decide which ones survive on their own.
# --------------------------------------------------------------------------- #


def _stray_line_to_region(line):
    tight = _tight_bbox_from_words(line, pad=2.0)
    if tight is None:
        return None
    text = " ".join(w["text"] for w in line).strip()
    if not text:
        return None
    sizes = [
        float(w["size"]) for w in line
        if w.get("size") is not None
        and str(w.get("fontname", "")) != "_synthetic_bullet"
    ]
    avg_size = sum(sizes) / len(sizes) if sizes else 0.0
    bold_count = sum(
        1 for w in line if "bold" in str(w.get("fontname", "")).lower()
    )
    return LayoutRegion(
        type=LayoutRegionType.TEXT,
        bbox=tight,
        text=text,
        font_size=avg_size,
        is_bold=bold_count > len(line) / 2,
    )


def _detect_stray_text_regions(
    regions,
    all_words,
    gutter_x: Optional[float] = None,
):
    """Emit TEXT regions for page words not covered by ``regions``.

    ``gutter_x`` is forwarded to ``_group_words_into_lines`` so words on
    opposite sides of a column gutter are not fused into a single line.
    No proximity- or column-side-based merging into existing regions is
    performed here; that is the job of the downstream overlap-resolve
    and small-text-merge passes.
    """
    if not all_words:
        return regions

    real_words = [
        w for w in all_words
        if str(w.get("fontname", "")) != "_synthetic_bullet"
    ]
    if not real_words:
        return regions

    def _covered(w):
        return any(_word_center_in_bbox(w, r.bbox) for r in regions)

    stray_words = [w for w in real_words if not _covered(w)]
    if not stray_words:
        return regions

    stray_lines, _ = _group_words_into_lines(stray_words, gutter_x=gutter_x)

    out = list(regions)
    for line in stray_lines:
        if not line:
            continue
        new_region = _stray_line_to_region(line)
        if new_region is not None:
            out.append(new_region)
    return out


# --------------------------------------------------------------------------- #
# Step 7: Reading order
# --------------------------------------------------------------------------- #

def _reading_order(
    regions,
    page_w: float,
    page_h: float,
    gutter_x: Optional[float] = None,
    all_words: Optional[List[Dict[str, Any]]] = None,
):
    """Sort regions into natural reading order.

    Single column (no `gutter_x`): simple top-to-bottom, ties broken
    left-to-right.

    Two column (`gutter_x` known): cut the page into horizontal bands at
    each wide (gutter-crossing) region's y-extent; within each band emit
    wide regions first (top-to-bottom), then the entire left column
    top-to-bottom, then the entire right column top-to-bottom. This
    yields the natural "left col top-to-bottom, then right col" flow,
    while still interleaving full-width headings, tables, and figures
    at their correct vertical position.

    Region column-membership (left / right / wide) is derived from the
    sides of the *lines* that fall inside each region's bbox -- not from
    the region's bbox geometry alone. ``_resolve_line_sides`` already
    paints every segment of a cross-gutter raw line as ``"wide"``, so
    members of a horizontal row (e.g. the three author cells at the top
    of a paper) inherit ``"wide"`` and get emitted together as part of
    the same band cut, in their natural left-to-right order. TEXT and
    LIST regions use this line-side aggregation; tables, images, and any
    region we cannot align to lines fall back to bbox-based
    classification via ``_region_column_side``.
    """
    if not regions:
        return []

    if gutter_x is None:
        return sorted(regions, key=lambda r: (r.bbox[1], r.bbox[0]))

    gx = gutter_x

    line_meta = _build_line_side_meta(all_words, gx)

    side_cache: Dict[int, str] = {
        id(r): _region_side(r, gx, page_w, line_meta) for r in regions
    }

    def is_wide(r):
        if side_cache[id(r)] == "wide":
            return True
        # Header/footer fallback: a region positioned in the very top or
        # very bottom of the page that genuinely spans both columns
        # (its bbox crosses the gutter) is treated as wide even when
        # the line-side machinery did not flag it. Gutter-crossing is
        # required because a normal column-body block is already ~half
        # the page wide on a typical 2-column layout, so width alone
        # cannot distinguish a real page-spanning footer from an
        # ordinary column block that happens to sit in the top/bottom
        # 8% band.
        x0, _, x1, _ = r.bbox
        width = x1 - x0
        cy = (r.bbox[1] + r.bbox[3]) / 2.0
        in_edge_band = cy < 0.08 * page_h or cy > 0.92 * page_h
        crosses_gutter = x0 < gx < x1
        if in_edge_band and crosses_gutter and width > 0.30 * page_w:
            return True
        return False

    def col_of(r):
        # is_wide() callers have already filtered out wide regions, so
        # the cache value here is "left" or "right" (or, in the defensive
        # bbox-fallback path for regions with no aligned lines, falls
        # back to bbox-centroid against the gutter).
        s = side_cache[id(r)]
        if s == "left":
            return 0
        if s == "right":
            return 1
        cx = (r.bbox[0] + r.bbox[2]) / 2.0
        return 0 if cx < gx else 1

    narrow = [r for r in regions if not is_wide(r)]
    left = [r for r in narrow if col_of(r) == 0]
    right = [r for r in narrow if col_of(r) == 1]
    multi_col = len(left) >= 1 and len(right) >= 1

    if not multi_col:
        return sorted(regions, key=lambda r: (r.bbox[1], r.bbox[0]))

    wide = [r for r in regions if is_wide(r)]
    cuts = sorted(
        {0.0, page_h, *(r.bbox[1] for r in wide), *(r.bbox[3] for r in wide)}
    )

    placed: List[LayoutRegion] = []
    for i in range(len(cuts) - 1):
        y0, y1 = cuts[i], cuts[i + 1]
        if y1 - y0 < 1.0:
            continue
        band = [
            r for r in regions
            if y0 <= ((r.bbox[1] + r.bbox[3]) / 2.0) < y1
        ]
        if not band:
            continue
        band_wide = sorted(
            [r for r in band if is_wide(r)],
            key=lambda r: (r.bbox[1], r.bbox[0]),
        )
        band_narrow = [r for r in band if not is_wide(r)]
        band_left = sorted(
            [r for r in band_narrow if col_of(r) == 0],
            key=lambda r: (r.bbox[1], r.bbox[0]),
        )
        band_right = sorted(
            [r for r in band_narrow if col_of(r) == 1],
            key=lambda r: (r.bbox[1], r.bbox[0]),
        )
        placed.extend(band_wide + band_left + band_right)

    placed_ids = {id(r) for r in placed}
    for r in regions:
        if id(r) not in placed_ids:
            placed.append(r)
    return placed


# --------------------------------------------------------------------------- #
# Small-text-region merging
# --------------------------------------------------------------------------- #

def _word_count(text: str) -> int:
    return len((text or "").split())


def _bbox_edge_distance(a, b) -> float:
    """Edge-to-edge distance between two bboxes; 0 if they touch/overlap."""
    hgap, vgap = _bbox_gap(a, b)
    return float(np.hypot(hgap, vgap))


def _merge_small_text_regions(
    regions: List[LayoutRegion],
    max_small_words: int = _SMALL_TEXT_WORD_THRESHOLD,
    max_gap: Optional[float] = _SMALL_TEXT_MAX_MERGE_GAP,
) -> List[LayoutRegion]:
    """Absorb small TEXT regions into a closer adjacent TEXT neighbor.

    ``regions`` must already be in reading order. A TEXT region is
    "small" when its word count is below ``max_small_words``. For each
    small TEXT region, the closer of its two nearest TEXT neighbors
    (previous and next in reading order, by bbox edge-to-edge distance)
    absorbs it -- *provided* that distance is at most ``max_gap`` PDF
    points. If the only available TEXT neighbors are all farther than
    ``max_gap``, the small region is left alone. Pass ``max_gap=None``
    to disable the cap. Non-TEXT regions act as opaque boundaries -- a
    small TEXT cannot merge across a table, list, or image.

    Text is concatenated in reading order, the bbox is set to the
    union of the two, and typography is carried from the larger
    contributor.

    The pass is iterative on the running result: after a merge, the
    merged region is re-examined and may itself merge again if it is
    still small. This is the "use updated regions for deciding the next
    merge" behaviour.
    """
    if not regions:
        return list(regions)

    out = list(regions)
    i = 0
    while i < len(out):
        r = out[i]
        if r.type != LayoutRegionType.TEXT:
            i += 1
            continue
        if _word_count(r.text) >= max_small_words:
            i += 1
            continue

        # Nearest TEXT neighbor on each side in reading order.
        prev_idx: Optional[int] = None
        for j in range(i - 1, -1, -1):
            if out[j].type == LayoutRegionType.TEXT:
                prev_idx = j
                break
        next_idx: Optional[int] = None
        for j in range(i + 1, len(out)):
            if out[j].type == LayoutRegionType.TEXT:
                next_idx = j
                break

        if prev_idx is None and next_idx is None:
            i += 1
            continue

        candidates: List[Tuple[int, float]] = []
        if prev_idx is not None:
            candidates.append(
                (prev_idx, _bbox_edge_distance(r.bbox, out[prev_idx].bbox))
            )
        if next_idx is not None:
            candidates.append(
                (next_idx, _bbox_edge_distance(r.bbox, out[next_idx].bbox))
            )
        if max_gap is not None:
            candidates = [c for c in candidates if c[1] <= max_gap]
        if not candidates:
            i += 1
            continue
        target_idx = min(candidates, key=lambda t: t[1])[0]
        target = out[target_idx]

        # Reading-order-preserving concatenation: earlier region first.
        if target_idx < i:
            merged_text = (target.text + " " + r.text).strip()
        else:
            merged_text = (r.text + " " + target.text).strip()
        merged_bbox = (
            min(target.bbox[0], r.bbox[0]),
            min(target.bbox[1], r.bbox[1]),
            max(target.bbox[2], r.bbox[2]),
            max(target.bbox[3], r.bbox[3]),
        )
        # Carry the larger contributor's typography forward.
        larger = (
            target if _word_count(target.text) >= _word_count(r.text) else r
        )
        out[target_idx] = LayoutRegion(
            type=LayoutRegionType.TEXT,
            bbox=merged_bbox,
            text=merged_text,
            font_size=larger.font_size,
            is_bold=larger.is_bold,
        )

        # Remove the absorbed region and re-examine the merged result,
        # which may itself still be small.
        out.pop(i)
        if target_idx < i:
            i = target_idx
        else:
            # ``target`` shifted left by one after the pop.
            i = target_idx - 1

    return out


# --------------------------------------------------------------------------- #
# Rasterization
# --------------------------------------------------------------------------- #

def _rasterize_page(
    page, dpi: int, pdf_path: Optional[str] = None
) -> Tuple[np.ndarray, float]:
    from pdf2image import convert_from_bytes, convert_from_path

    page_number = int(getattr(page, "page_number", 1))
    if pdf_path:
        images = convert_from_path(
            pdf_path,
            dpi=dpi,
            first_page=page_number,
            last_page=page_number,
            fmt="png",
        )
    else:
        stream = page.pdf.stream
        stream.seek(0)
        pdf_bytes = stream.read()
        images = convert_from_bytes(
            pdf_bytes,
            dpi=dpi,
            first_page=page_number,
            last_page=page_number,
            fmt="png",
        )
    pil_img = images[0].convert("RGB")
    img_rgb = np.array(pil_img)
    scale = dpi / 72.0
    return img_rgb, scale


def _binarize_foreground(img_rgb: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)
    bw = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
        cv2.THRESH_BINARY_INV, 35, 12,
    )
    return bw


# --------------------------------------------------------------------------- #
# Geometry / helpers
# --------------------------------------------------------------------------- #

def _valid_bbox(b) -> bool:
    if b is None or len(b) != 4:
        return False
    x0, y0, x1, y1 = b
    return x1 > x0 and y1 > y0


def _inflate_if_thin(b, eps=0.5):
    x0, y0, x1, y1 = b
    if x1 - x0 < 1.0:
        x0 -= eps; x1 += eps
    if y1 - y0 < 1.0:
        y0 -= eps; y1 += eps
    return (x0, y0, x1, y1)


def _clamp_bbox(b, page_w: float, page_h: float):
    x0, y0, x1, y1 = b
    return (
        max(0.0, min(x0, page_w)),
        max(0.0, min(y0, page_h)),
        max(0.0, min(x1, page_w)),
        max(0.0, min(y1, page_h)),
    )


def _iou(a, b) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0 = max(ax0, bx0); iy0 = max(ay0, by0)
    ix1 = min(ax1, bx1); iy1 = min(ay1, by1)
    iw = max(0.0, ix1 - ix0); ih = max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    a_area = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    b_area = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = a_area + b_area - inter
    return inter / union if union > 0 else 0.0


def _bbox_gap(a, b) -> Tuple[float, float]:
    hgap = max(a[0] - b[2], b[0] - a[2], 0.0)
    vgap = max(a[1] - b[3], b[1] - a[3], 0.0)
    return hgap, vgap


def _x_projections_overlap(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2]


def _y_projections_overlap(a, b) -> bool:
    return a[1] < b[3] and b[1] < a[3]


def _overlap_ratio(box: Tuple[float, float, float, float],
                   ref: Tuple[float, float, float, float]) -> float:
    bx0, by0, bx1, by1 = box
    rx0, ry0, rx1, ry1 = ref
    ix0 = max(bx0, rx0); iy0 = max(by0, ry0)
    ix1 = min(bx1, rx1); iy1 = min(by1, ry1)
    iw = max(0.0, ix1 - ix0); ih = max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    box_area = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    if box_area <= 0:
        return 0.0
    return inter / box_area


def _word_center_in_bbox(item, bbox) -> bool:
    try:
        x0 = float(item["x0"]); x1 = float(item["x1"])
        y0 = float(item["top"]); y1 = float(item["bottom"])
    except (KeyError, ValueError, TypeError):
        return False
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    return bbox[0] <= cx <= bbox[2] and bbox[1] <= cy <= bbox[3]


def _tight_bbox_from_words(words, pad: float = 0.0):
    if not words:
        return None
    try:
        x0 = min(float(w["x0"]) for w in words)
        x1 = max(float(w["x1"]) for w in words)
        y0 = min(float(w["top"]) for w in words)
        y1 = max(float(w["bottom"]) for w in words)
    except (KeyError, ValueError, TypeError):
        return None
    return (x0 - pad, y0 - pad, x1 + pad, y1 + pad)


def _ink_density(img_rgb: np.ndarray, bbox, scale: float) -> float:
    if img_rgb is None:
        return 0.0
    H, W = img_rgb.shape[:2]
    x0 = max(0, int(round(bbox[0] * scale)))
    y0 = max(0, int(round(bbox[1] * scale)))
    x1 = min(W, int(round(bbox[2] * scale)))
    y1 = min(H, int(round(bbox[3] * scale)))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return 0.0
    crop = img_rgb[y0:y1, x0:x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    return float((gray < 245).mean())


def _crop_image_bytes(img_rgb: np.ndarray, bbox, scale: float) -> Optional[bytes]:
    if img_rgb is None:
        return None
    H, W = img_rgb.shape[:2]
    x0 = max(0, int(round(bbox[0] * scale)))
    y0 = max(0, int(round(bbox[1] * scale)))
    x1 = min(W, int(round(bbox[2] * scale)))
    y1 = min(H, int(round(bbox[3] * scale)))
    if x1 - x0 < 1 or y1 - y0 < 1:
        return None
    crop = img_rgb[y0:y1, x0:x1]
    buf = io.BytesIO()
    Image.fromarray(crop).save(buf, format="PNG")
    return buf.getvalue()


def _safe_text_in_bbox(page, bbox) -> str:
    try:
        return (
            page.within_bbox(bbox).extract_text(x_tolerance_ratio=0.15) or ""
        ).strip()
    except Exception:
        return ""


def _hyperlink_bbox(link: Dict[str, Any]) -> Optional[Tuple[float, float, float, float]]:
    try:
        return (
            float(link["x0"]),
            float(link["top"]),
            float(link["x1"]),
            float(link["bottom"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _hyperlink_uri(link: Dict[str, Any]) -> str:
    uri = link.get("uri")
    if uri is None:
        return ""
    if isinstance(uri, bytes):
        return uri.decode("utf-8", errors="replace").strip()
    return str(uri).strip()


def _page_hyperlinks(page) -> List[Dict[str, Any]]:
    try:
        return list(page.hyperlinks or [])
    except Exception:
        return []


def _hyperlink_anchor_for_region(
    link: Dict[str, Any],
    region_words: List[Dict[str, Any]],
) -> Optional[Tuple[str, str]]:
    uri = _hyperlink_uri(link)
    if not uri:
        return None
    link_bbox = _hyperlink_bbox(link)
    if link_bbox is None:
        return None
    link_words = [w for w in region_words if _word_center_in_bbox(w, link_bbox)]
    if not link_words:
        return None
    link_words.sort(key=lambda w: (float(w["top"]), float(w["x0"])))
    anchor = " ".join(str(w.get("text", "")) for w in link_words).strip()
    if not anchor:
        return None
    return anchor, uri


def _append_hyperlink_url_to_text(text: str, anchor: str, uri: str) -> str:
    if not text or not anchor or not uri:
        return text
    marker = f"{anchor} ({uri})"
    if marker in text:
        return text
    pos = text.find(anchor)
    if pos < 0:
        return text
    return text[: pos + len(anchor)] + f" ({uri})" + text[pos + len(anchor) :]


def _inject_hyperlinks_into_text(
    text: str,
    hyperlinks: List[Dict[str, Any]],
    region_words: List[Dict[str, Any]],
) -> str:
    if not text or not hyperlinks or not region_words:
        return text
    spans: List[Tuple[str, str]] = []
    for link in hyperlinks:
        pair = _hyperlink_anchor_for_region(link, region_words)
        if pair is not None:
            spans.append(pair)
    if not spans:
        return text
    for anchor, uri in sorted(spans, key=lambda x: len(x[0]), reverse=True):
        text = _append_hyperlink_url_to_text(text, anchor, uri)
    return text


def _inject_hyperlinks_into_region(
    region: LayoutRegion,
    hyperlinks: List[Dict[str, Any]],
    all_words: List[Dict[str, Any]],
) -> None:
    if not hyperlinks or region.type == LayoutRegionType.IMAGE:
        return
    region_words = [w for w in all_words if _word_center_in_bbox(w, region.bbox)]
    if not region_words:
        return

    if region.text:
        region.text = _inject_hyperlinks_into_text(
            region.text, hyperlinks, region_words,
        )

    if region.list_items:
        region.list_items = [
            _inject_hyperlinks_into_text(item, hyperlinks, region_words)
            for item in region.list_items
        ]

    if region.table_grid:
        region.table_grid = [
            [
                _inject_hyperlinks_into_text(
                    str(cell or ""), hyperlinks, region_words,
                )
                for cell in row
            ]
            for row in region.table_grid
        ]
        region.text = _grid_to_text(region.table_grid)


def _inject_hyperlinks_into_regions(
    regions: List[LayoutRegion],
    page,
    all_words: List[Dict[str, Any]],
) -> List[LayoutRegion]:
    hyperlinks = _page_hyperlinks(page)
    if not hyperlinks:
        return regions
    for region in regions:
        _inject_hyperlinks_into_region(region, hyperlinks, all_words)
    return regions


def _grid_to_text(grid) -> str:
    rows = []
    for row in (grid or []):
        cells = [(c or "").strip() for c in row]
        rows.append(" | ".join(cells))
    return "\n".join(rows).strip()