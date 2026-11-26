"""The governor's heavy-parse memory gate follows the limits that bound one Excel document."""

from __future__ import annotations

import pytest

from app.modules.parsers.excel import limits
from app.services.resource_governor import policy as policy_mod
from tests.unit.services.resource_governor.test_policy import _snapshot

MB = 1024 * 1024


def test_the_document_memory_formula_is_the_larger_phase(monkeypatch: pytest.MonkeyPatch) -> None:
    # worker phase and parent phase do not overlap within one document: the peak is the larger
    monkeypatch.setenv("EXCEL_MAX_TEXT_MB", "480")
    monkeypatch.setenv("EXCEL_LOAD_MAX_MB", "1536")
    assert limits.excel_document_memory_bytes() == max(1536 * MB, 3 * 480 * MB) == 1536 * MB
    monkeypatch.setenv("EXCEL_MAX_TEXT_MB", "1000")
    assert limits.excel_document_memory_bytes() == 3000 * MB
    monkeypatch.setenv("EXCEL_MAX_TEXT_MB", "10")
    assert limits.excel_document_memory_bytes() == 1536 * MB


def test_the_defaults_keep_the_assumed_heavy_working_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXCEL_MAX_TEXT_MB", raising=False)
    monkeypatch.delenv("EXCEL_LOAD_MAX_MB", raising=False)
    assert policy_mod.heavy_parse_working_set_gb() == policy_mod.HEAVY_PARSE_WORKING_SET_GB == 1.5


def test_the_governor_admits_fewer_heavy_parses_when_the_excel_budget_rises(monkeypatch: pytest.MonkeyPatch) -> None:
    snap = _snapshot(cpu_quota=16.0, mem_limit_bytes=32 * 1024**3)
    monkeypatch.delenv("EXCEL_MAX_TEXT_MB", raising=False)
    before = policy_mod.heavy_memory_cap(snap)
    monkeypatch.setenv("EXCEL_MAX_TEXT_MB", "2048")  # a 6 GiB output budget
    after = policy_mod.heavy_memory_cap(snap)
    assert before is not None
    assert after is not None
    assert after < before
    assert policy_mod.heavy_parse_working_set_gb() == pytest.approx(6.0)
