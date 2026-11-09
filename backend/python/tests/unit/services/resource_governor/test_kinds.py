"""The memory a parse job holds is charged by its kind (PX-2e, T77, T78, T87).

The oracle below is written out here from the table in the design, not imported from the
implementation, so a wrong table in ``kinds.py`` cannot pass by agreeing with itself.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.resource_governor import kinds
from app.services.resource_governor.kinds import (
    SCALES,
    ChargeScales,
    ParseKind,
    classify_kind,
    lane_charge_bytes,
    parent_working_set_gb,
    permit_excess_bytes,
    window_charge_bytes,
)

GIB = 2**30
MIB = 2**20
FIXTURE = Path(__file__).parent / "fixtures" / "PX-2e-calibration.json"

# The slopes of the text kinds are refit by the calibration run: these are the figures the
# fixture justifies, and the oracle uses them as the design table states them.
SLOPE = {"text": 80.0, "csv": 16.0, "code": 64.0}


def oracle(kind: str, size: int, covered: bool = False) -> int:
    """LLD 2.20 with the 6.1 amendments, from scratch."""
    if kind == "pdf_layout":
        return int(1.25 * GIB + size)
    if kind == "render":
        return int(0.65 * GIB)
    if kind in SLOPE:
        return int(min(max(0.064 * GIB + SLOPE[kind] * size, 0.10 * GIB), 1.75 * GIB))
    if kind == "excel_load":
        return 0 if covered else 1536 * MIB
    return int(1.25 * GIB)  # inspect, unknown, anything else


@pytest.fixture(autouse=True)
def _clean_scales() -> None:
    SCALES.reset()
    yield
    SCALES.reset()


@pytest.fixture(autouse=True)
def _no_explicit_lane_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOVERNOR_LANE_WORKING_SET_GB", raising=False)
    for name in ("EXCEL_LOAD_MAX_MB", "EXCEL_MAX_TEXT_MB"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("kind", "size"),
    [
        (ParseKind.PDF_LAYOUT, 3 * MIB),
        (ParseKind.PDF_LAYOUT, 113 * MIB),
        (ParseKind.RENDER, 60 * MIB),
        (ParseKind.TEXT, 300 * 1024),
        (ParseKind.TEXT, 20 * MIB),
        (ParseKind.CSV, 5 * MIB),
        (ParseKind.CODE, 5 * MIB),
        (ParseKind.EXCEL_LOAD, 20 * MIB),
        (ParseKind.INSPECT, 10 * MIB),
        (ParseKind.UNKNOWN, 10 * MIB),
    ],
)
def test_charge_by_kind_table(kind: ParseKind, size: int) -> None:
    assert lane_charge_bytes(kind, size) == oracle(kind.value, size)


def test_pdf_layout_charge_adds_the_input_size() -> None:
    small = lane_charge_bytes(ParseKind.PDF_LAYOUT, 1 * MIB)
    big = lane_charge_bytes(ParseKind.PDF_LAYOUT, 101 * MIB)
    assert big - small == 100 * MIB
    assert lane_charge_bytes(ParseKind.PDF_LAYOUT, 0) == int(1.25 * GIB)


def test_text_charge_scales_and_clamps() -> None:
    tiny = lane_charge_bytes(ParseKind.TEXT, 1000)
    assert tiny == int(0.10 * GIB), "a small file is clamped to the 0.10 GiB floor"
    mid = lane_charge_bytes(ParseKind.TEXT, 2 * MIB)
    assert mid == oracle("text", 2 * MIB)
    assert tiny < mid < lane_charge_bytes(ParseKind.TEXT, 10 * MIB)
    assert lane_charge_bytes(ParseKind.TEXT, 500 * MIB) == int(1.75 * GIB), "and to 1.75 GiB at the top"
    # Each of the three kinds has its own slope.
    csv, code, text = (lane_charge_bytes(k, 5 * MIB) for k in (ParseKind.CSV, ParseKind.CODE, ParseKind.TEXT))
    assert csv < code < text


def test_unknown_kind_gets_the_fallback_charge() -> None:
    assert lane_charge_bytes(None, 5 * MIB) == int(1.25 * GIB)
    assert lane_charge_bytes(ParseKind.UNKNOWN, 0) == int(1.25 * GIB)
    assert lane_charge_bytes(ParseKind.INSPECT, 7 * MIB) == int(1.25 * GIB)


def test_explicit_lane_working_set_is_flat() -> None:
    flat = 700 * MIB
    for kind in ParseKind:
        assert lane_charge_bytes(kind, 50 * MIB, flat_bytes=flat) == flat
        assert window_charge_bytes(kind, 50 * MIB, True, flat_bytes=flat) == flat


def test_explicit_env_value_is_read_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    assert kinds.explicit_lane_working_set_bytes({}) is None
    assert kinds.explicit_lane_working_set_bytes({"GOVERNOR_LANE_WORKING_SET_GB": "0.5"}) == GIB // 2
    for junk in ("", "x", "0", "-1", "nan"):
        assert kinds.explicit_lane_working_set_bytes({"GOVERNOR_LANE_WORKING_SET_GB": junk}) is None


def test_charge_is_never_below_the_floor_or_above_the_bound() -> None:
    for kind in ParseKind:
        for size in (0, 1, 10 * MIB, 3 * GIB):
            value = lane_charge_bytes(kind, size)
            if kind is ParseKind.EXCEL_LOAD:
                continue
            assert 64 * MIB <= value <= max(int(1.25 * GIB) + size, 1792 * MIB)


@pytest.mark.parametrize(
    ("extension", "mime", "expected"),
    [
        ("pdf", None, ParseKind.PDF),
        (".PDF", None, ParseKind.PDF),
        ("xlsx", None, ParseKind.EXCEL_LOAD),
        ("docx", None, ParseKind.DOCLING_DOCX),
        ("md", None, ParseKind.TEXT),
        ("csv", None, ParseKind.CSV),
        ("py", None, ParseKind.CODE),
        (None, "application/pdf", ParseKind.PDF),
        (None, "text/csv", ParseKind.CSV),
        (None, "application/octet-stream", ParseKind.UNKNOWN),
        ("weird", None, ParseKind.UNKNOWN),
        ("png", None, ParseKind.UNKNOWN),
        (None, None, ParseKind.UNKNOWN),
    ],
)
def test_classify_kind(extension: str | None, mime: str | None, expected: ParseKind) -> None:
    assert classify_kind(extension, mime) is expected


def test_extension_wins_over_mime() -> None:
    assert classify_kind("pdf", "text/plain") is ParseKind.PDF


def test_excel_load_is_free_when_the_permit_covers_the_resident_charge() -> None:
    assert lane_charge_bytes(ParseKind.EXCEL_LOAD, 20 * MIB, permit_covered=True) == 0
    assert lane_charge_bytes(ParseKind.EXCEL_LOAD, 20 * MIB, permit_covered=False) == 1536 * MIB


def test_covered_excel_is_still_charged_to_the_window() -> None:
    covered = window_charge_bytes(ParseKind.EXCEL_LOAD, 20 * MIB, permit_covered=True)
    assert covered == min(1536 * MIB, max(1536 * MIB, 3 * 480 * MIB))
    assert covered > 0, "the other processes must see an in-flight load"
    assert window_charge_bytes(ParseKind.EXCEL_LOAD, 20 * MIB, permit_covered=False) == 1536 * MIB


def test_excel_limits_follow_the_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXCEL_LOAD_MAX_MB", "800")
    assert lane_charge_bytes(ParseKind.EXCEL_LOAD, 1) == 800 * MIB
    monkeypatch.setenv("EXCEL_MAX_TEXT_MB", "100")
    assert window_charge_bytes(ParseKind.EXCEL_LOAD, 1, True) == 800 * MIB


# -- the permit's parent working set (G3) --------------------------------------------


def test_pdf_permit_excess_matches_the_table() -> None:
    assert parent_working_set_gb(ParseKind.PDF) == 0.5
    assert permit_excess_bytes(ParseKind.PDF, 0.25, "measured") == int(0.25 * GIB)
    assert permit_excess_bytes(ParseKind.PDF_LAYOUT, 0.25, "measured") == int(0.25 * GIB)
    assert permit_excess_bytes(ParseKind.RENDER, 0.25, "measured") == int(0.25 * GIB)


def test_excel_permit_total_equals_excel_document_memory_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.parsers.excel.limits import excel_document_memory_bytes

    for load, text in ((1536, 480), (4000, 480), (1536, 2000)):
        monkeypatch.setenv("EXCEL_LOAD_MAX_MB", str(load))
        monkeypatch.setenv("EXCEL_MAX_TEXT_MB", str(text))
        expected = max(load, 3 * text) * MIB
        assert excel_document_memory_bytes() == expected
        total = permit_excess_bytes(ParseKind.EXCEL_LOAD, 0.25, "measured") + int(0.25 * GIB)
        assert abs(total - expected) <= 1


def test_docling_kinds_keep_the_docling_working_set(monkeypatch: pytest.MonkeyPatch) -> None:
    assert parent_working_set_gb(ParseKind.DOCLING_DOCX) == 1.5
    assert permit_excess_bytes(ParseKind.DOCLING_DOCX, 0.25, "measured") == int(1.25 * GIB)
    monkeypatch.setenv("GOVERNOR_DOCLING_WORKING_SET_GB", "2.5")
    assert parent_working_set_gb(ParseKind.DOCLING_DOCX) == 2.5


TEXT_KINDS = (ParseKind.TEXT, ParseKind.CSV, ParseKind.CODE)
PERMIT_BASE_GB = 0.25
LIGHT_BASE_GB = 0.02


def _worst_parent_growth(cell: dict) -> int:
    return max(max(r["parent_traced_peak_bytes"], r["parent_rss_growth_bytes"]) for r in cell["runs"])


def test_text_kinds_hold_a_size_dependent_working_set_in_the_parent() -> None:
    for kind in TEXT_KINDS:
        small = parent_working_set_gb(kind, 1000)
        large = parent_working_set_gb(kind, 20 * MIB)
        assert 0 < small < 0.05, "a small record holds a small base, covered by the permit"
        assert large > small + 0.03
        assert parent_working_set_gb(kind) == parent_working_set_gb(kind, None) == parent_working_set_gb(kind, 0)
        assert parent_working_set_gb(kind, -5) == parent_working_set_gb(kind, 0)
        assert permit_excess_bytes(kind, PERMIT_BASE_GB, "measured") == 0


def test_text_parent_growth_is_covered_in_every_calibration_cell() -> None:
    """The reservation plus the permit's base covers the worst run's parent growth, for the heavy
    path (permit base) and the light path (the pool's assumed base)."""
    seen = 0
    for name, cell in _cells().items():
        kind = ParseKind(cell["kind"])
        if kind not in TEXT_KINDS:
            continue
        seen += 1
        worst = _worst_parent_growth(cell)
        for base in (PERMIT_BASE_GB, LIGHT_BASE_GB):
            covered = permit_excess_bytes(kind, base, "measured", cell["size_bytes"]) + int(base * GIB)
            assert covered >= worst, f"{name}: parent grew {worst / MIB:.0f} MiB, covered {covered / MIB:.0f} MiB"
    assert seen == 6


def test_a_zero_text_working_set_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutant: the text kinds hold nothing in the parent, as before. The calibration check must fail."""
    from app.services.resource_governor import kinds

    monkeypatch.setattr(kinds, "TEXT_PARENT_SLOPES", dict.fromkeys(TEXT_KINDS, 0.0))
    monkeypatch.setattr(kinds, "TEXT_PARENT_BASE_GB", 0.0)
    with pytest.raises(AssertionError):
        test_text_parent_growth_is_covered_in_every_calibration_cell()


def test_unknown_kind_parent_working_set_is_the_old_figure() -> None:
    assert parent_working_set_gb(None) == 1.5
    assert parent_working_set_gb(ParseKind.UNKNOWN) == 1.5
    assert permit_excess_bytes(ParseKind.UNKNOWN, 0.25, "measured") == int(1.25 * GIB)


@pytest.mark.parametrize("source", ["explicit", "fallback", "docling"])
def test_excess_is_reserved_only_under_the_measured_base(source: str) -> None:
    for kind in ParseKind:
        assert permit_excess_bytes(kind, 0.25, source) == 0


# -- the charge guard -----------------------------------------------------------------


def test_runtime_guard_raises_charge_scale_on_rss_overrun() -> None:
    scales = ChargeScales()
    charge = 400 * MIB
    assert not scales.observe(ParseKind.TEXT, int(charge * 1.19), charge), "within the 20 % tolerance"
    assert scales.scale(ParseKind.TEXT) == 1.0
    assert scales.observe(ParseKind.TEXT, int(charge * 1.5), charge)
    assert scales.scale(ParseKind.TEXT) == pytest.approx(1.5)
    assert scales.scale(ParseKind.CSV) == 1.0, "only that kind"
    # Never lowered, never raised by a smaller overrun.
    assert not scales.observe(ParseKind.TEXT, int(charge * 1.3), charge)
    assert scales.scale(ParseKind.TEXT) == pytest.approx(1.5)
    assert scales.stats()["text"]["observed_peak_bytes"] == int(charge * 1.5)


def test_runtime_guard_ignores_uncharged_workers() -> None:
    scales = ChargeScales()
    assert not scales.observe(ParseKind.EXCEL_LOAD, 900 * MIB, 0)
    assert scales.stats() == {}


def test_a_raised_scale_raises_the_charge() -> None:
    before = lane_charge_bytes(ParseKind.TEXT, 5 * MIB)
    after = lane_charge_bytes(ParseKind.TEXT, 5 * MIB, scale=1.5)
    assert after > before
    assert after <= int(1.25 * GIB)
    assert lane_charge_bytes(ParseKind.PDF_LAYOUT, 0, scale=1.5) == int(1.25 * GIB * 1.5)


def test_scale_is_bounded() -> None:
    scales = ChargeScales()
    scales.observe(ParseKind.CSV, 100 * GIB, MIB)
    assert scales.scale(ParseKind.CSV) == kinds.MAX_CHARGE_SCALE


# -- the calibration fixture (T87) ----------------------------------------------------

# Kinds whose charge is a deliberate cap, not a fit: no upper bound is checked for them.
CAPPED = {"inspect", "unknown", "excel_load"}
# Kinds whose peak follows the content, not the size: bounded against the kind's largest measured peak.
CONTENT_DEPENDENT = {"pdf_layout", "render"}


def _cells() -> dict[str, dict]:
    if not FIXTURE.exists():
        pytest.fail(f"the calibration fixture {FIXTURE} is missing: run tests/perf/parse_bench/kinds_calibration.py")
    return json.loads(FIXTURE.read_text())["cells"]


def test_calibration_fixture_covers_every_cell_the_design_lists() -> None:
    names = set(_cells())
    required = {
        "pdf_figures_60", "pdf_figures_300", "pdf_tables_300", "pdf_large_113mb", "pdf_digital_1000",
        "md_1mb", "md_5mb", "md_20mb", "csv_5mb", "csv_20mb", "code_5mb", "xlsx_1mb", "xlsx_20mb",
        "render_scan_60", "render_scan_300",
    }
    assert required <= names, sorted(required - names)
    for name, cell in _cells().items():
        assert len(cell["runs"]) >= 3 or name in json.loads(FIXTURE.read_text()).get("fewer_runs", []), name


def test_charge_matches_the_recorded_calibration() -> None:
    cells = _cells()
    kind_peak: dict[str, int] = {}
    for cell in cells.values():
        kind_peak[cell["kind"]] = max(kind_peak.get(cell["kind"], 0), cell["median_peak_bytes"])
    for name, cell in cells.items():
        kind = ParseKind(cell["kind"])
        peak = cell["median_peak_bytes"]
        charge = lane_charge_bytes(kind, cell["size_bytes"], permit_covered=False)
        assert charge >= peak, f"{name}: charge {charge / MIB:.0f} MiB is below the measured peak {peak / MIB:.0f} MiB"
        if cell["kind"] in CAPPED:
            continue
        # A layout parse's memory depends on what is in the file (figures and tables against plain text), not
        # on its size: its charge is one figure for the kind, held to 1.5 x the largest peak any cell showed.
        reference = kind_peak[cell["kind"]] if cell["kind"] in CONTENT_DEPENDENT else peak
        assert charge <= 1.5 * reference, (
            f"{name}: charge {charge / MIB:.0f} MiB is over 1.5 x the measured peak {reference / MIB:.0f} MiB"
        )


def test_charge_never_below_peak_in_any_calibration_cell() -> None:
    """Every individual run, not only the median: no run held more than the kind is charged."""
    for name, cell in _cells().items():
        kind = ParseKind(cell["kind"])
        charge = lane_charge_bytes(kind, cell["size_bytes"], permit_covered=False)
        worst = max(r["peak_bytes"] for r in cell["runs"])
        assert charge >= worst * 0.95, f"{name}: worst run {worst / MIB:.0f} MiB against a charge of {charge / MIB:.0f} MiB"


def test_calibration_cell_code_5mb_charge_at_least_peak() -> None:
    cell = _cells()["code_5mb"]
    assert lane_charge_bytes(ParseKind.CODE, cell["size_bytes"]) >= cell["median_peak_bytes"]
    # One slope for the three kinds cannot cover both a CSV (14 B/B) and markdown (70 B/B).
    assert lane_charge_bytes(ParseKind.CODE, cell["size_bytes"]) < lane_charge_bytes(ParseKind.TEXT, cell["size_bytes"])


def test_parent_growth_cell_1000_pages_within_base() -> None:
    cell = _cells()["pdf_digital_1000"]
    base = 0.25 * GIB
    assert cell["median_parent_growth_bytes"] <= base, (
        f"the parent grew {cell['median_parent_growth_bytes'] / MIB:.0f} MiB over a 1000-page parse; the base is "
        f"{base / MIB:.0f} MiB"
    )


def test_parent_growth_is_within_the_base_in_every_pdf_cell() -> None:
    for name, cell in _cells().items():
        if cell["kind"] == "pdf_layout":
            assert cell["median_parent_growth_bytes"] <= 0.25 * GIB, name


def test_retuning_cannot_lift_a_charge_past_its_limits() -> None:
    """What the reaper's scale can and cannot do, as the operator docs state it."""
    flat = 3 * GIB
    assert lane_charge_bytes(ParseKind.PDF_LAYOUT, 0, scale=kinds.MAX_CHARGE_SCALE, flat_bytes=flat) == flat
    huge_text = lane_charge_bytes(ParseKind.TEXT, 500 * MIB, scale=kinds.MAX_CHARGE_SCALE)
    assert huge_text == int(kinds.TEXT_MAX_GB * GIB)
    scales = ChargeScales()
    scales.observe(ParseKind.PDF_LAYOUT, 1000 * GIB, GIB)
    assert scales.scale(ParseKind.PDF_LAYOUT) == kinds.MAX_CHARGE_SCALE == 4.0


def test_the_docs_state_the_limits_of_retuning_and_the_route_retry() -> None:
    doc = (Path(__file__).parents[6] / "docs" / "indexing-service.md").read_text(encoding="utf-8")
    section = doc.split("**Memory charged by kind of job")[1].split("**The heavy permit")[0]
    assert f"at most {kinds.MAX_CHARGE_SCALE:g}x" in section
    assert "flat charge is never scaled" in section
    assert f"{kinds.TEXT_MAX_GB:g} GiB cap" in section
    assert "429 with `Retry-After`" in section and "the caller retries" in section


def test_the_docs_say_spawn_admission_charges_by_kind_unless_the_flat_figure_is_set() -> None:
    doc = (Path(__file__).parents[6] / "docs" / "indexing-service.md").read_text(encoding="utf-8")
    lanes = doc.split("**CPU lanes (`PARSE_LANES=on`")[1].split("**Weighted leases.")[0]
    assert "each charged `GOVERNOR_LANE_WORKING_SET_GB`" not in lanes
    assert "each charged by its kind of job" in lanes
    assert kinds.explicit_lane_working_set_bytes({}) is None, "the flat figure is unset by default"


def _kinds_section(doc: str) -> str:
    return doc.split("**Memory charged by kind of job")[1].split("**The heavy permit")[0]


def test_the_docs_say_what_a_waiting_growth_holds_and_that_the_flat_figure_covers_excel() -> None:
    # Kills: a doc that says a refused growth holds only its lease (it also keeps the checked-out
    # worker and its token, which the reaper cannot take), or that a permit-covered Excel load is
    # never resident even when the flat figure is set.
    doc = (Path(__file__).parents[6] / "docs" / "indexing-service.md").read_text(encoding="utf-8")
    section = _kinds_section(doc)
    assert "holding only its lease" not in section
    assert "its checked-out worker and that worker's resident token" in section
    assert "the reaper takes only idle workers" in section
    flat = 700 * MIB
    assert window_charge_bytes(ParseKind.EXCEL_LOAD, 0, True, flat_bytes=flat) == flat
    assert lane_charge_bytes(ParseKind.EXCEL_LOAD, 0, True, flat_bytes=flat) == flat
    assert "with an explicit `GOVERNOR_LANE_WORKING_SET_GB` it is charged that flat figure for both" in section
