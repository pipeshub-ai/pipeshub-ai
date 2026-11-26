"""MCPNOTE-1: the human-test smoke's default-chat MCP check asks exactly what it names.

The smoke failed once an admin had added a second MCP server (Atlassian Rovo) that wanted a
sign-in: the turn ran with e2esaas and called its tool, but carried a notice about Rovo, and the check failed on any
notice. It now passes when the run was admitted with e2esaas's tools and no notice names e2esaas; a notice naming
another server is reported, not failed.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "deployment" / "e2e"))

smoke = importlib.import_module("smoke_human")


def _events(*notices: dict[str, Any], tools: tuple[str, ...] = ("mcp@inst-saas/lookup_note",)) -> list[dict[str, Any]]:
    return [
        {"type": "run.admitted", "runId": "run-1", "data": {}},
        *({"type": "x.pipeshub.run.notice", "runId": "run-1", "data": notice} for notice in notices),
        {"type": "run.spec_pinned", "runId": "run-1",
         "data": {"spec": {"grants": {"tools": [{"toolId": tool} for tool in tools]}}}},
        # Another run of the session is not this turn's.
        {"type": "x.pipeshub.run.notice", "runId": "run-0", "data": {"code": "mcp_unavailable", "item": "e2esaas"}},
    ]


def test_a_notice_about_another_server_passes_and_is_reported() -> None:
    rovo = {"code": "mcp_unavailable", "item": "Atlassian Rovo", "reason": "sign_in"}
    ok, detail, others = smoke.mcp_admission(_events(rovo), "run-1", {"inst-saas"})
    assert ok, detail
    assert others == ["Atlassian Rovo"]


def test_no_notice_passes_with_nothing_to_report() -> None:
    assert smoke.mcp_admission(_events(), "run-1", {"inst-saas"})[0::2] == (True, [])


def test_a_notice_naming_e2esaas_fails() -> None:
    ok, detail, _ = smoke.mcp_admission(
        _events({"code": "mcp_unavailable", "item": "e2esaas", "reason": "unreachable"}), "run-1", {"inst-saas"}
    )
    assert not ok
    assert "notices about it=" in detail and "unreachable" in detail


def test_a_notice_naming_no_server_may_be_about_e2esaas_so_it_fails() -> None:
    assert not smoke.mcp_admission(_events({"code": "mcp_unavailable"}), "run-1", {"inst-saas"})[0]


def test_a_run_without_e2esaas_tools_fails_even_with_no_notice() -> None:
    ok, detail, _ = smoke.mcp_admission(_events(tools=("mcp@inst-other/search",)), "run-1", {"inst-saas"})
    assert not ok
    assert "tools=[]" in detail


def test_a_run_never_admitted_on_the_harness_fails() -> None:
    assert not smoke.mcp_admission([], "", {"inst-saas"})[0]


def _whole(instance: str) -> list[dict[str, Any]]:
    """R26-LAZY, ADR-OWD-07 D-LAZY-2: the spec a worker that lists at first use is pinned with: the server granted whole."""
    mcp = [{"instanceId": instance, "manifestDigest": "sha256:" + "0" * 64, "tools": "at_first_use"}]
    return [
        {"type": "run.admitted", "runId": "run-1", "data": {}},
        {"type": "run.spec_pinned", "runId": "run-1",
         "data": {"spec": {"grants": {"tools": []}, "extensions": {"x.pipeshub": {"mcp": mcp}}}}},
    ]


def test_lazy_a_run_granted_e2esaas_whole_passes() -> None:
    ok, detail, _ = smoke.mcp_admission(_whole("inst-saas"), "run-1", {"inst-saas"})
    assert ok, detail
    assert "at first use" in detail


def test_lazy_a_run_granted_another_server_whole_still_fails() -> None:
    assert not smoke.mcp_admission(_whole("inst-other"), "run-1", {"inst-saas"})[0]
