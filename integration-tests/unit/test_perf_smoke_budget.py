"""The perf smoke's budget: fail only above twice the baseline, or on any error."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "perf"))

import smoke  # noqa: E402

pytestmark = pytest.mark.unit

BASE = {"index p95": 10.0, "search p95": 1.0, "chat p95": 5.0}


def result(**over):
    return {**{k: v * 1.0 for k, v in BASE.items()}, "error_rate": 0.0, **over}


def test_budget_fails_only_above_twice_the_baseline() -> None:
    assert smoke.budget_failures(result(**{"search p95": 1.9}), BASE) == []
    got = smoke.budget_failures(result(**{"search p95": 2.1}), BASE)
    assert len(got) == 1 and "search p95" in got[0] and "2.1" in got[0] and "1.0" in got[0]


def test_any_error_fails_the_budget() -> None:
    got = smoke.budget_failures(result(error_rate=0.02), BASE)
    assert got and "error rate" in got[0]


def test_missing_baseline_reports_only(capsys) -> None:
    assert smoke.load_baseline("no-such-label") == {}
    assert "no baseline: report only" in capsys.readouterr().out
    assert smoke.budget_failures(result(**{"search p95": 99.0}), {}) == []


def test_summarize_reads_the_benchmark_results() -> None:
    indexing = {"metrics": {"time_to_indexed_seconds": {"p95": 4.0}, "records_uploaded": 50, "failures": {"total": 1}}}
    query = {"metrics": {"error_rate": 0.0, "operations": {"search": {"latency_seconds": {"p95": 0.5}}, "chat": {"latency_seconds": {"p95": None}}}}}
    got = smoke.summarize(indexing, query)
    assert got == {"index p95": 4.0, "search p95": 0.5, "chat p95": None, "error_rate": 0.02}
