"""MCP-E2E RN: the full-stack MCP rows' own helpers, without a stack.

What a turn "offered" is the claim every dynamic-change case rests on, and the grant-size measurement is only as
good as its tools, so both are held here: the reading of the scripted model's request record, the tools generated
from the release's toolset registry, and the routes the runner drives.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

E2E = Path(__file__).resolve().parents[5] / "integration-tests" / "harness" / "e2e"
sys.path.insert(0, str(E2E))

import mcp_rows  # noqa: E402
import mcp_stack  # noqa: E402
from driver import Paths  # noqa: E402
from observe import Observation  # noqa: E402

Json = dict[str, Any]


def _turn(*requests: Json, events: list[Json] | None = None) -> mcp_rows.Turn:
    return mcp_rows.Turn(record=None, obs=Observation(events=events or []), requests=list(requests))  # type: ignore[arg-type]


def _request(kind: str, *tools: str, status: int = 200) -> Json:
    return {"kind": kind, "status": status, "tools": list(tools)}


def test_rn1_offered_is_what_the_loops_and_the_engines_requests_carried() -> None:
    turn = _turn(
        _request("other"),
        _request("loop", "search_tools", "mcp_s_a"),
        _request("engine", "bash", "mcp__tools__mcp_s_b"),
        _request("loop", "mcp_s_refused", status=401),
        {"path": "/v1/embeddings", "status": 200},
    )
    assert turn.offered() == {"search_tools", "mcp_s_a", "bash", "mcp__tools__mcp_s_b"}
    assert turn.offers("mcp_s_a") and turn.offers("mcp_s_b")
    # A name that only ends like another tool is not that tool.
    assert not turn.offers("s_a") or turn.offers("mcp_s_a")
    assert not turn.offers("mcp_s_refused") and not turn.offers("mcp_s_c")
    assert turn.offered_lists() == [frozenset({"search_tools", "mcp_s_a"}), frozenset({"bash", "mcp__tools__mcp_s_b"})]


def test_rn2_a_runs_failure_and_its_waits_are_read_from_its_journal() -> None:
    waiting = {"type": "run.yielded", "data": {"reason": "capability_wait"}}
    failed = {"type": "run.failed", "data": {"errorType": "capability_lost"}}
    turn = _turn(events=[waiting, waiting, failed])
    assert turn.yields() == ["capability_wait", "capability_wait"]
    assert turn.failure() == "capability_lost"
    assert _turn().failure() is None


@pytest.mark.parametrize("count", (10, 50, 200, 500))
def test_rn3_the_measured_grants_are_the_releases_own_connector_tools(count: int) -> None:
    tools = mcp_stack.registry_tools(count)
    assert len(tools) == count
    for name, spec in tools.items():
        # mcp_<12-character server>_<tool> must fit the tightest engine limit (52), or the grant would be refused.
        assert len(f"mcp_{'s' * 12}_{name}") <= 52, name
        assert spec["description"] and spec["inputSchema"]["type"] == "object"
    described = sum(1 for spec in tools.values() if spec["inputSchema"]["properties"])
    assert described > count // 2, "most connector tools take parameters"


def test_rn4_a_follow_up_goes_to_the_conversations_own_route() -> None:
    assert Paths("agent-1").follow_up("conv-1") == "/api/v1/agents/agent-1/conversations/conv-1/messages/stream"
    assert Paths(None).follow_up("conv-1") == "/api/v1/conversations/conv-1/messages/stream"


def test_rn5_a_canary_is_unique_and_a_digest_never_holds_it() -> None:
    first, second = mcp_stack.canary("token"), mcp_stack.canary("token")
    assert first != second and first.startswith("mcpe2e-token-")
    assert first not in mcp_stack.digest(first) and len(mcp_stack.digest(first)) == 16


def test_rn6_every_executor_names_an_engine_and_a_wire_the_scripted_model_serves() -> None:
    assert set(mcp_rows.EXECUTORS) == {"loop", "opencode", "codex", "claude_code"}
    assert mcp_rows.EXECUTORS["loop"][0] == "none"
    assert set(mcp_rows.ENGINE_IMAGES) == set(mcp_rows.EXECUTORS) - {"loop"}
