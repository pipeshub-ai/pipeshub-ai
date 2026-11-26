"""R44-MCP2's journey checks.

R43MCP-FU-4: journey MR-15 ``mcp.find_read`` takes either way the lazy surface offers to a server's tools, and
still fails a turn that searched knowledge alone (R36LIVE-FU-2's symptom).

A live model (gpt-6-luna, 2 of 2) loaded the named server's toolset with ``fetch_tools`` where the journey
and the pre-registered target expected ``search_tools``. The verdict is read off the person's stream: the first call
(the discovery), whether its result answered the read tool, and any knowledge tool called.

R43MCP-FU-3: the owner-pins journeys counted every listing after the job's claim as the job's, the parent's resumed
attempt's included; only the listings made while the job ran are its.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

E2E = Path(__file__).resolve().parents[5] / "deployment" / "e2e"
sys.path.insert(0, str(E2E))
import journeys  # noqa: E402

READ = "mcp_findsx_lookup_note"


def _call(call_id: str, name: str, result: Any) -> list[SimpleNamespace]:  # noqa: ANN401
    return [SimpleNamespace(type="TOOL_CALL_START", data={"toolCallId": call_id, "toolCallName": name}),
            SimpleNamespace(type="TOOL_CALL_ARGS", data={"toolCallId": call_id, "delta": "{}"}),
            SimpleNamespace(type="TOOL_CALL_RESULT", data={"toolCallId": call_id, "content": json.dumps(result)})]


def _note() -> list[SimpleNamespace]:
    return _call("c2", "call_tool", {"text": "Private note on finance: the finance review is due on Friday."})


def test_a_search_that_answers_the_read_tool_passes() -> None:
    frames = _call("c1", "search_tools", {"matches": [{"name": READ}]}) + _note()
    assert journeys.find_read_verdict(frames, READ) == ("search_tools", True, [])


def test_loading_the_servers_toolset_passes_as_well() -> None:
    frames = _call("c1", "fetch_tools", {"toolset": "mcp_findsx", "tools": [{"name": READ}]}) + _note()
    assert journeys.find_read_verdict(frames, READ) == ("fetch_tools", True, [])


def test_a_knowledge_only_turn_fails() -> None:
    frames = (_call("c1", "search_internal_knowledge", {"records": []})
              + _call("c2", "knowledgegraph__search", {"records": []}))
    path, answered, knowledge = journeys.find_read_verdict(frames, READ)
    assert (path, answered) == ("search_internal_knowledge", False)
    assert knowledge == ["search_internal_knowledge", "knowledgegraph__search"]


def test_a_knowledge_search_beside_the_discovery_is_reported() -> None:
    frames = _call("c1", "fetch_tools", {"tools": [{"name": READ}]}) + _call("c3", "knowledgegraph__search", {})
    assert journeys.find_read_verdict(frames, READ)[2] == ["knowledgegraph__search"]


def test_a_discovery_that_did_not_answer_the_read_tool_fails() -> None:
    frames = _call("c1", "fetch_tools", {"error": "Unknown toolset: mcp_other", "tools": []})
    assert journeys.find_read_verdict(frames, READ) == ("fetch_tools", False, [])


def test_no_tool_call_at_all_fails() -> None:
    assert journeys.find_read_verdict([SimpleNamespace(type="RUN_FINISHED", data={})], READ) == ("", False, [])


def test_the_journey_walks_both_paths() -> None:
    assert journeys.FIND_PATHS == ("search_tools", "fetch_tools")


def test_only_listings_made_while_the_job_ran_count_against_it() -> None:
    served = [10.0, 20.5, 31.0]  # the parent's first use, one while the job ran, the parent's resumed attempt
    assert journeys.listed_for_child(served, claimed=20.0, settled=30.0) == 1
    assert journeys.listed_for_child([10.0, 31.0], claimed=20.0, settled=30.0) == 0


def test_a_job_with_no_settling_logged_counts_everything_after_its_claim() -> None:
    assert journeys.listed_for_child([10.0, 20.5, 31.0], claimed=20.0, settled=None) == 2


def test_a_job_never_claimed_counts_nothing() -> None:
    assert journeys.listed_for_child([10.0], claimed=None, settled=None) == 0
