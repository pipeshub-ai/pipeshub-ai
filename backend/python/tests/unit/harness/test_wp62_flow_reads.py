"""WP-62 (WP62-FLOW-1..3): public web research in a chat that read no private data never asks.

A recorded child: 20 approvals labelled ``flow_external_write`` (``sensitivity=unknown``), one per site, for
``web_search`` and ``fetch_url``. The run had read nothing private. Its first six calls were a GitHub toolset's reads
that failed with "Bad credentials": every outcome of a toolset counted as content of unknown origin, a failed one too,
so the flow rule read the run's context as unknown and held every web read after them. A failed call of the
platform's own tool returns the tool's own error text, never record content; a failed MCP call is the server's text and
stays unknown, and a toolset read that succeeded is still unknown.
"""

from __future__ import annotations

from app.harness.labels import own_work_label
from harness_core.testing.contracts.source_ledger import Stream
from tests.unit.harness.engines.test_source_ledger import DIGEST, TOOL_NAMES, WEB_ID

RUN = "run-1"
GITHUB_ID = "github@b5f1941f-d468-4c11-a01d-f214d041d193/list_issues"
MCP_ID = "mcp@f6cf3be4/search"
NAMES = {**TOOL_NAMES, GITHUB_ID: "github__list_issues", MCP_ID: "mcp__search"}


def _call(stream: Stream, invocation: str, tool_id: str, *, status: str, integrity: str) -> None:
    _ = stream.add(
        "tool.intent",
        {
            "invocationId": invocation,
            "toolId": tool_id,
            "argsDigest": DIGEST,
            "effect": "read",
            "sink": {"egress": "external", "argsLeaveBoundary": True},
        },
        run_id=RUN,
    )
    _ = stream.add(
        "tool.outcome", {"invocationId": invocation, "status": status, "labels": {"integrity": integrity}}, run_id=RUN
    )


def _recorded_start(stream: Stream) -> None:
    stream.admitted(RUN)
    for n in range(6):
        _call(stream, f"gh-{n}", GITHUB_ID, status="error", integrity="internal")
    _call(stream, "web-1", WEB_ID, status="ok", integrity="external")


def test_wp62_flow_1_failed_toolset_calls_and_web_reads_leave_the_context_public() -> None:
    stream = Stream()
    _recorded_start(stream)
    label = own_work_label(stream.events, RUN, tool_names=NAMES)
    assert (label.complete, label.sources) == (True, frozenset())


def test_wp62_flow_2_a_toolset_read_that_succeeded_is_still_unknown() -> None:
    stream = Stream()
    _recorded_start(stream)
    _call(stream, "gh-ok", GITHUB_ID, status="ok", integrity="internal")
    assert own_work_label(stream.events, RUN, tool_names=NAMES).complete is False


def test_wp62_flow_3_a_failed_outside_tool_is_still_unknown() -> None:
    stream = Stream()
    _recorded_start(stream)
    _call(stream, "mcp-1", MCP_ID, status="error", integrity="external")
    assert own_work_label(stream.events, RUN, tool_names=NAMES).complete is False
