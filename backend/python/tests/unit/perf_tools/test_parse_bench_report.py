"""Statistics, cgroup parsing and report layout of the parse benchmark."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.perf.parse_bench import report

if TYPE_CHECKING:
    from pathlib import Path


class TestPercentile:
    def test_nearest_rank(self) -> None:
        values = list(range(1, 101))
        assert report.percentile(values, 50) == 50
        assert report.percentile(values, 99) == 99
        assert report.percentile(values, 100) == 100

    def test_empty_is_zero(self) -> None:
        assert report.percentile([], 99) == 0.0

    def test_single_value(self) -> None:
        assert report.percentile([7.0], 99) == 7.0

    def test_unsorted_input(self) -> None:
        assert report.percentile([5, 1, 3], 50) == 3

    @pytest.mark.parametrize(("values", "expected"), [([3, 1, 2], 2), ([1, 2, 3, 4], 2.5), ([], 0.0)])
    def test_median(self, values: list[float], expected: float) -> None:
        assert report.median(values) == expected


def test_latency_by_type_groups_and_rounds() -> None:
    out = report.latency_by_type([("pdf", 1.0), ("pdf", 3.0), ("md", 0.01)])
    assert out["pdf"] == {"n": 2, "p50": 1.0, "p95": 3.0, "p99": 3.0}
    assert out["md"]["n"] == 1


def test_lag_summary_is_milliseconds() -> None:
    out = report.lag_summary([0.001] * 98 + [0.2, 0.3])
    assert out["samples"] == 100
    assert out["p50_ms"] == 1.0
    assert out["p99_ms"] == 200.0
    assert out["max_ms"] == 300.0


class TestCgroup:
    def test_parse_flat_keyed_skips_bad_lines(self) -> None:
        text = "usage_usec 100\nnr_throttled 3\nthrottled_usec 2500\nbad line here\nname notanumber\n"
        assert report.parse_flat_keyed(text) == {"usage_usec": 100, "nr_throttled": 3, "throttled_usec": 2500}

    def test_read_cgroup_reads_counters(self, tmp_path: Path) -> None:
        (tmp_path / "cpu.stat").write_text("usage_usec 900\nnr_throttled 4\nthrottled_usec 1200\n")
        (tmp_path / "memory.peak").write_text("5242880\n")
        (tmp_path / "memory.events").write_text("low 0\noom_kill 2\n")
        got = report.read_cgroup(tmp_path)
        assert got["memory_peak"] == 5242880
        assert got["throttled_usec"] == 1200
        assert got["oom_kill"] == 2
        assert got["memory_current"] is None

    def test_missing_files_are_none_not_zero(self, tmp_path: Path) -> None:
        got = report.read_cgroup(tmp_path)
        assert all(v is None for v in got.values())

    def test_delta_subtracts_counters_and_keeps_peak_absolute(self) -> None:
        before = {"memory_peak": 1, "usage_usec": 100, "nr_throttled": 1, "throttled_usec": 10, "oom_kill": 0}
        after = {"memory_peak": 9, "usage_usec": 400, "nr_throttled": 5, "throttled_usec": 70, "oom_kill": 0}
        assert report.cgroup_delta(before, after) == {
            "memory_peak": 9, "usage_usec": 300, "nr_throttled": 4, "throttled_usec": 60, "oom_kill": 0,
        }

    def test_delta_with_unknown_counter_stays_unknown(self) -> None:
        assert report.cgroup_delta({"throttled_usec": None}, {"throttled_usec": 5})["throttled_usec"] is None

    @pytest.mark.parametrize(("text", "own"), [
        ("0::/\n", True),
        ("0::/user.slice/user-1000.slice/session-1.scope\n", False),
        ("12:cpu:/docker/abc\n11:memory:/docker/abc\n", False),
        ("", False),
    ])
    def test_in_own_cgroup(self, text: str, own: bool) -> None:
        assert report.in_own_cgroup(text) is own

    def test_missing_required_files_are_named(self, tmp_path: Path) -> None:
        (tmp_path / "cpu.stat").write_text("usage_usec 1\n")
        assert report.missing_cgroup_files(tmp_path) == ["memory.peak", "memory.events"]

    def test_all_required_files_present(self, tmp_path: Path) -> None:
        for name in report.REQUIRED_CGROUP_FILES:
            (tmp_path / name).write_text("0\n")
        assert report.missing_cgroup_files(tmp_path) == []

    def test_peak_reader_resets_and_reads(self, tmp_path: Path) -> None:
        (tmp_path / "memory.peak").write_text("999")
        reader = report.PeakReader(tmp_path)
        assert reader.scoped
        reader.close()

    def test_peak_reader_without_file_is_unscoped(self, tmp_path: Path) -> None:
        reader = report.PeakReader(tmp_path)
        assert not reader.scoped
        assert reader.read() is None


class TestMemorySafety:
    def test_under_85_percent_is_ok(self) -> None:
        assert report.memory_safety(3 * 1024**3, 4 * 1024**3) == "75 % (ok)"

    def test_over_85_percent_is_flagged(self) -> None:
        assert "OVER" in report.memory_safety(3.6 * 1024**3, 4 * 1024**3)

    def test_unknown_is_not_ok(self) -> None:
        assert report.memory_safety(None, 4 * 1024**3) == "n/a"


def _result(label: str, mix: str, fps: float, *, cg: dict | None = None, rss: float = 1000.0) -> dict:
    return {
        "config": {"label": label, "mix": mix},
        "status": "ok",
        "files": {"failed": 0},
        "metrics": {
            "files_per_s": fps,
            "loop_lag": {"p50_ms": 1.0, "p99_ms": 40.0},
            "memory": {"peak_tree_rss_mib": rss},
            "cpu_seconds": 12.0,
            "threads": {"peak_total": 30},
            "workers": {"peak_parse_workers": 2},
        },
        "cgroup": cg,
    }


class TestReport:
    def test_median_run_picks_the_middle_throughput(self) -> None:
        runs = [_result("a", "m", f) for f in (3.0, 1.0, 2.0)]
        assert report.median_run(runs)["metrics"]["files_per_s"] == 2.0

    def test_median_run_of_two_is_the_lower(self) -> None:
        runs = [_result("a", "m", f) for f in (4.0, 1.0)]
        assert report.median_run(runs)["metrics"]["files_per_s"] == 1.0

    def test_row_has_one_cell_per_header_column(self) -> None:
        assert len(report.run_row(_result("T", "cpu-heavy", 1.5))) == len(report.HEADER)

    def test_row_shows_cgroup_figures(self) -> None:
        cg = {"memory_peak": 2 * 1024**3, "throttled_usec": 3_500_000}
        row = report.run_row(_result("T", "cpu-heavy", 1.5, cg=cg))
        assert row[7] == "2048 / 3.5s"

    def test_row_without_cgroup_says_so(self) -> None:
        assert report.run_row(_result("T", "cpu-heavy", 1.5))[7] == "n/a"

    def test_markdown_table_shape(self) -> None:
        text = report.markdown_report([_result("T", "cpu-heavy", 1.5)])
        lines = text.splitlines()
        assert len(lines) == 3
        assert lines[1].count("---") == len(report.HEADER)

    def test_targets_table_fills_each_tier(self) -> None:
        by_tier = {
            "T-small": _result("T-small", "cpu-heavy", 0.5, rss=3000.0),
            "T-laptop": _result("T-laptop", "cpu-heavy", 1.0, cg={"memory_peak": 7 * 1024**3, "throttled_usec": 0}),
        }
        text = report.targets_table(by_tier, {"T-small": 4 * 1024**3, "T-laptop": 8 * 1024**3})
        assert "T-small: 0.500" in text
        assert "T-laptop: 1.000" in text
        assert "T-small: 73 % (ok)" in text
        assert "T-laptop: 88 % (OVER 85 %)" in text
        assert "T-small: n/a (no cgroup)" in text


class TestStalls:
    STACK = (
        '  File "/app/a.py", line 10, in outer\n    inner()\n'
        '  File "/app/markdown_it_parser.py", line 99, in parse\n    heavy()\n'
        '  File "/app/regex_tools.py", line 7, in scan\n    re.sub()'
    )

    def test_frames_are_innermost_first(self) -> None:
        assert report.stall_frames(self.STACK) == "regex_tools.py:7 scan <- markdown_it_parser.py:99 parse"

    def test_no_frames(self) -> None:
        assert report.stall_frames("nothing here") == "<no frames>"

    def test_top_stalls_counts_and_orders(self) -> None:
        other = '  File "/app/json_parser.py", line 3, in load\n    json.loads()'
        top = report.top_stalls([self.STACK, other, self.STACK, self.STACK, other], n=2)
        assert [t["count"] for t in top] == [3, 2]
        assert top[1]["where"] == "json_parser.py:3 load"

    def test_top_stalls_limits_to_n(self) -> None:
        stacks = [f'  File "/a/f{i}.py", line {i}, in fn' for i in range(5)]
        assert len(report.top_stalls(stacks, n=3)) == 3

    def test_no_stalls(self) -> None:
        assert report.top_stalls([]) == []


class TestSpreadAndArms:
    def test_spread_is_median_with_min_max(self) -> None:
        assert report.spread([112, 295, 150], 0) == "150 (112-295)"

    def test_spread_of_nothing(self) -> None:
        assert report.spread([]) == "n/a"

    def test_cell_row_reports_runs_and_ranges(self) -> None:
        runs = [_result("T", "io-heavy", f) for f in (4.0, 5.0, 6.0)]
        for run, lag in zip(runs, (100.0, 300.0, 150.0), strict=True):
            run["metrics"]["loop_lag"]["p99_ms"] = lag
        row = report.cell_row(runs)
        assert row[:3] == ["T", "io-heavy", "3"]
        assert row[3] == "5 (4-6)"
        assert row[4] == "150 (100-300)"
        assert len(row) == len(report.CELL_HEADER)

    def test_arms_are_labelled_as_the_coordinator_asked(self) -> None:
        assert report.arm_label({"config": {"pdf_parser": "pdfplumber"}}) == "PdfPlumber arm (not yet the shipped default)"
        assert report.arm_label({"config": {"pdf_parser": "docling"}}) == "Docling arm (upper bound, local Docling)"
        assert report.arm_label({"config": {}}) == "PdfPlumber arm (not yet the shipped default)"
