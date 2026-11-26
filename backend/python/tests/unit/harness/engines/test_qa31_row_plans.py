"""QA31-6..QA31-8: Claude Code's rows of the e2e matrix, their plans on the matrix lane, and how a run is read back."""

from __future__ import annotations

from typing import Any

import pytest

from tests.unit.harness.engines.matrix.assembly import JIRA_READ, JIRA_WRITE, mcp_tool
from tests.unit.harness.engines.matrix.rows import (
    NOT_ON_LANE,
    ArrayRow,
    Outcome,
    covering,
    engine_ran,
    engine_rows,
    observe,
    plan_row,
    prompt,
)

ROWS = engine_rows("claude_code")
BY_ID = {row.row_id: row for row in ROWS}


def test_qa31_6_every_claude_code_row_is_selected() -> None:
    ids = [row.row_id for row in ROWS]
    assert len(ids) == len(set(ids))
    assert sorted(i for i in ids if i.startswith("PW-")) == [
        "PW-003", "PW-013", "PW-020", "PW-027", "PW-042", "PW-043", "PW-044", "PW-045", "PW-046", "PW-047", "PW-094",
        "PW-099", "PW-130", "PW-131",
    ]
    assert sorted(i for i in ids if i.startswith("T3-")) == ["T3-002", "T3-020", "T3-027", "T3-036", "T3-039", "T3-043"]
    assert [i for i in ids if i.startswith("EM-")] == ["EM-18", "EM-54"]
    assert all(row.factors["engine"] == "claude_code" for row in ROWS)
    # The oracle's turn hands off to the engine (CTL-6); the matrix lane runs that engine directly.
    assert all(covering().oracle(dict(row.factors))["executor"] == "loop+code_task:claude_code" for row in ROWS)
    assert all(row.expected().executor == "engine:claude_code" for row in ROWS)


# (row, script, tools, org layer, flow guard on, decision, scope)
PLANS: list[tuple[str, str, tuple[str, ...], dict[str, Any] | None, bool, str, str | None]] = [
    ("PW-003", "read-then-write-mcp", (mcp_tool("echo"),), None, True, "approved", None),
    ("PW-013", "read-then-write-toolset", (JIRA_READ, JIRA_WRITE), {"writes": "ask"}, False, "approved", None),
    ("PW-020", "code-then-answer", (), {"flow": {"level": "deny"}}, True, "none", None),
    ("PW-027", "answer", (), None, False, "none", None),
    ("PW-042", "read-then-write-toolset", (JIRA_READ, JIRA_WRITE), {"writes": "ask"}, False, "approved", None),
    ("PW-043", "read-then-write-mcp", (mcp_tool("echo"),), None, False, "denied", None),
    ("PW-044", "read-then-write-toolset", (JIRA_READ, JIRA_WRITE), {"writes": "ask"}, False, "expire", None),
    ("PW-045", "read-then-answer", (), {"flow": {"level": "deny"}}, True, "none", None),
    ("PW-046", "answer", (), {"flow": {"level": "deny"}}, True, "none", None),
    ("PW-047", "answer", (), {"writes": "ask"}, True, "none", None),
    ("PW-094", "read-then-write-toolset", (JIRA_READ, JIRA_WRITE), None, False, "approved", "conversation"),
    # QA-6F: the attended Claude Code memory row the repaired array added; memory has no wiring on this lane (QA31-O2).
    ("PW-099", "answer", (), None, False, "none", None),
    # R31JOURNEY-FU-7: the default chat's Agent mode handing its job to Claude Code, the org's coding engine.
    ("PW-130", "read-then-write-mcp", (mcp_tool("echo"),), {"writes": "ask"}, False, "denied", None),
    ("PW-131", "read-then-write-mcp", (mcp_tool("echo"),), None, False, "expire", None),
    ("T3-002", "read-then-write-toolset", (JIRA_READ, JIRA_WRITE), {"writes": "ask"}, True, "approved", None),
    ("T3-020", "code-then-answer", (), {"writes": "ask"}, True, "none", None),
    ("T3-027", "read-then-write-mcp", (mcp_tool("echo"),), None, False, "expire", None),
    ("T3-036", "answer", (), None, False, "none", None),
    ("T3-039", "answer", (), None, False, "none", None),
    ("T3-043", "answer", (), {"writes": "ask", "flow": {"level": "deny"}}, True, "none", None),
    ("EM-18", "read-then-write-mcp", (mcp_tool("echo"),), None, True, "approved", None),
    ("EM-54", "answer", (), None, True, "none", None),
]


@pytest.mark.parametrize(("row_id", "script", "tools", "layer", "flow_guard", "decision", "scope"), PLANS)
def test_qa31_7_each_row_is_planned_on_the_lane(
    row_id: str, script: str, tools: tuple[str, ...], layer: dict[str, Any] | None, flow_guard: bool,
    decision: str, scope: str | None,
) -> None:
    plan = plan_row(BY_ID[row_id])
    assert (plan.script, plan.tools, plan.mcp) == (script, tools, script == "read-then-write-mcp")
    assert plan.approvals_document("org-1") == (None if layer is None else {"default": layer})
    assert (plan.flow_guard, plan.decision, plan.scope) == (flow_guard, decision, scope)
    assert plan.mode == BY_ID[row_id].factors["mode"]


def test_qa31_7_the_plans_cover_every_row() -> None:
    assert sorted(p[0] for p in PLANS) == sorted(BY_ID)


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r.row_id)
def test_qa31_7_a_factor_the_lane_cannot_apply_is_named(row: ArrayRow) -> None:
    plan = plan_row(row)
    expected = {name: row.factors[name] for name in NOT_ON_LANE}
    if row.factors["tool_family"] in ("skills", "memory"):
        expected["tool_family"] = row.factors["tool_family"]
    assert dict(plan.not_on_lane) == expected


@pytest.mark.parametrize("script", ["read-then-write-mcp", "read-then-write-toolset"])
def test_qa31_7_a_sink_script_reads_a_private_record_before_it_writes(script: str) -> None:
    lines = prompt(script, private_record="rec-private", marker="M-1").splitlines()
    read = next(i for i, line in enumerate(lines) if "rec-private" in line)
    write = next(i for i, line in enumerate(lines) if "M-1" in line)
    assert read < write and lines[-1] == "say M-1 done"


def _event(kind: str, **data: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"type": kind, "data": data}


PINNED = _event("run.spec_pinned")
ASKED = _event("interaction.requested", interactionId="ix-1")


DEADLINE = _event("run.failed", errorType="deadline_exceeded")


@pytest.mark.parametrize(
    ("events", "status", "effects", "expected"),
    [
        ([PINNED], "completed", 0, Outcome("durable", "engine:claude_code", False, "answered", 0)),
        ([PINNED], "completed", 1, Outcome("durable", "engine:claude_code", False, "written", 1)),
        (
            [PINNED, ASKED, _event("interaction.resolved", decision="approved")], "completed", 1,
            Outcome("durable", "engine:claude_code", True, "approved:written", 1),
        ),
        (
            [PINNED, ASKED, _event("interaction.resolved", decision="approved"),
             _event("interaction.granted", interactionId="ix-1", scope="conversation")], "completed",
            1, Outcome("durable", "engine:claude_code", True, "approved:written+grant", 1),
        ),
        (
            [PINNED, ASKED, _event("interaction.resolved", decision="denied")], "completed", 0,
            Outcome("durable", "engine:claude_code", True, "denied:user", 0),
        ),
        # The sweeper ends the run at its deadline; the open approval is expired by the reading (Node's timeline).
        ([PINNED, ASKED, DEADLINE], "failed", 0, Outcome("durable", "engine:claude_code", True, "expired:approval_expired", 0)),
        # Approved, yet nothing reached the SaaS: not a written outcome.
        (
            [PINNED, ASKED, _event("interaction.resolved", decision="approved")], "completed", 0,
            Outcome("durable", "engine:claude_code", True, "completed:['approved']", 0),
        ),
        (
            [PINNED, ASKED, _event("run.failed", errorType="worker_failed")], "failed", 0,
            Outcome("durable", "engine:claude_code", True, "failed:[]", 0),
        ),
        (
            [PINNED, _event("run.failed", errorType="binary_mismatch")], "failed", 0,
            Outcome("durable", "engine:claude_code", False, "failed:binary_mismatch", 0),
        ),
    ],
)
def test_qa31_8_a_run_is_read_back_as_the_oracle_answers(
    events: list[dict[str, Any]], status: str, effects: int, expected: Outcome
) -> None:
    assert observe(events, status=status, effects=effects, engine="claude_code") == expected


def test_qa37_e2_no_engine_row_sets_a_role_model_and_one_that_did_is_named() -> None:
    assert {row.factors["child_model"] for row in ROWS} == {"none"}
    assert {row.expected().child for row in ROWS} == {"inherit"}
    row = BY_ID["PW-046"]
    asked = plan_row(ArrayRow("X-1", {**row.factors, "child_model": "different"}))
    assert asked.not_on_lane["child_model"] == "different"
    assert "child_model" not in plan_row(row).not_on_lane


def test_qa37_e2_a_child_spawned_with_a_role_model_is_read_back() -> None:
    spawned = _event("child.spawned", childRunId="c-1", grantDigest="sha256:" + "0" * 64)
    inherited = observe([PINNED, spawned], status="completed", effects=0, engine="claude_code")
    assert inherited == Outcome("durable", "engine:claude_code", False, "answered", 0, "inherit")
    model = {"provider": "anthropic", "model": "claude-haiku-4-5", "role": "default"}
    role = observe([PINNED, {**spawned, "data": {**spawned["data"], "model": model}}], status="completed", effects=0,
                   engine="claude_code")
    assert role.child == "role:set"
    assert role != BY_ID["PW-046"].expected()


def test_qa31_8_a_run_no_engine_ran_is_not_an_engine_run() -> None:
    seen = observe([PINNED], status="completed", effects=0, engine=None)
    assert seen.executor == "loop"


def test_qa31f_a_turn_lost_before_its_checkpoint_still_ran_on_the_engine() -> None:
    """PW-027 and PW-042 under load: the engine's sandbox could not be made, so the journal holds the engine's
    progress lines and no ``engine.*`` event. The run was the engine's, never the loop's."""
    events = [{"type": t} for t in (
        "run.admitted", "run.claimed", "run.spec_pinned", "message.completed", "turn.started",
        "x.pipeshub.engine.progress", "run.quarantined", "run.failed",
    )]
    assert engine_ran(events)
    assert not engine_ran([PINNED, {"type": "model.request"}, {"type": "run.completed"}])
