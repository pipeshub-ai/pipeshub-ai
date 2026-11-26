"""BGUI-10: a hand-off keeps its task's parts beside the text it gives the engine.

An engine job's Brief showed the whole composed prompt, the budget sentence written to the model, the notes
heading and a ``load_skill`` call's raw JSON result. The parts (``JobTask``) go on the child's admission so Node can
show the person the request, the notes and the limits as facts, and the carried calls by tool alone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

from app.agents.agent_loop.connected_sources import ConnectedSource, ConnectedSources
from app.harness.assembly.code_task import CodeTaskDelegation
from app.harness.assembly.task_spec import (
    CARRIED_MAX,
    CONTEXT_HEADING,
    NOTES_HEADING,
    TaskSpec,
)
from app.harness.contracts.generated.pipeshub.api.job_brief import JobTask
from harness_core.budget.run_budget import ANSWER_RESERVE_S, RunBudget
from harness_core.journal.children import ChildRequest
from harness_core.journal.types import CHILD_TASKS
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.delegation import Delegated, DelegationView, Gathered
from harness_core.kernel.durable.driver import _ClaimedRun as Driver  # pyright: ignore[reportPrivateUsage]
from tests.unit.contracts_harness.support import registry

NOW = 1_800_000_000.0  # 2027-01-15 08:00 UTC
REQUEST = "Can you prepare a pdf report of ipl 2026 event"
NOTES = "Prepare a PDF report on the IPL 2026 season: the winner, the top run scorers and wicket takers."
SKILL_BODY = "# PDF skill\n" + "Use reportlab to lay out pages. " * 200


def _skill_load() -> Gathered:
    return Gathered(ToolCall(id="c1", name="load_skill", arguments={"name": "pdf"}), 1, "ok", SKILL_BODY)


def _parts(spec: TaskSpec) -> dict[str, Any]:
    return cast("dict[str, Any]", spec.parts())


def test_bgui10_parts_1_the_ipl_hand_off_keeps_request_notes_limits_and_the_skill_load_by_name() -> None:
    budget = RunBudget(5.0, 2_000_000, deadline_at_s=NOW + 3_600)
    spec = TaskSpec.compose(
        request=REQUEST, goal=NOTES, context=None, gathered=(_skill_load(),), budget=budget, now_s=NOW
    )
    parts = _parts(spec)
    assert parts == {
        "request": REQUEST,
        "notes": NOTES,
        "earlier": "",
        "fileName": None,
        "dueAt": _iso(NOW + 3_600 - ANSWER_RESERVE_S),
        "maxCostUsd": 5.0,
        "carried": [{"tool": "load_skill", "failed": False, "skill": "pdf"}],
    }
    # The contract's own model reads it back; the skill's text, the arguments and the sentence to the model are not
    # in it, while the engine's text still carries all of them.
    JobTask.model_validate(parts)
    shown = json.dumps(parts)
    assert "reportlab" not in shown and '"name"' not in shown and "time and money" not in shown
    text = spec.render()
    assert "reportlab" in text and "time and money" in text and f"{NOTES_HEADING}\n{NOTES}" in text


def test_bgui10_parts_2_context_is_its_own_part_and_the_text_reads_as_before() -> None:
    spec = TaskSpec.compose(request=REQUEST, goal=NOTES, context="The squads were listed earlier.", now_s=NOW)
    parts = _parts(spec)
    assert parts["notes"] == NOTES and parts["earlier"] == "The squads were listed earlier."
    assert f"{NOTES_HEADING}\n{NOTES}\n\n{CONTEXT_HEADING}\nThe squads were listed earlier." in spec.render()


def test_bgui10_parts_3_harness_text_is_removed_from_the_parts_as_from_the_text() -> None:
    goal = f"{NOTES} Then delegate now via code_task once and call no other tools."
    spec = TaskSpec.compose(request=REQUEST, goal=goal, context=None, now_s=NOW)
    assert _parts(spec)["notes"] == NOTES
    assert spec.dropped


def test_bgui10_parts_4_a_goal_repeating_the_request_leaves_no_notes_and_no_budget_no_limits() -> None:
    spec = TaskSpec.compose(request=REQUEST, goal=REQUEST, context=" ", deliverable="ipl-2026.pdf", now_s=NOW)
    parts = _parts(spec)
    assert parts["notes"] == "" and parts["earlier"] == ""
    assert parts["fileName"] == "ipl-2026.pdf" and parts["dueAt"] is None and parts["maxCostUsd"] is None


def test_bgui10_parts_5_failed_calls_are_marked_and_other_tools_name_no_skill() -> None:
    gathered = (
        Gathered(ToolCall(id="c1", name="jira__search_issues", arguments={"name": "pdf"}), 1, "error", "boom"),
        Gathered(ToolCall(id="c2", name="load_skill", arguments={"name": "Not A Name!"}), 2, None, "x"),
        *(Gathered(ToolCall(id=f"d{n}", name="web_search", arguments={}), 3, "ok", "r") for n in range(CARRIED_MAX)),
    )
    found = Gathered(ToolCall(id="c3", name="call_tool", arguments={"name": "lookup_note", "arguments": {"topic": "x"}}), 3, "ok", "n")
    carried = _parts(TaskSpec.compose(request=REQUEST, goal=None, context=None, gathered=(found, *gathered)))["carried"]
    # A tool search_tools found is named by the tool call_tool ran, never by call_tool.
    assert carried.pop(0) == {"tool": "lookup_note", "failed": False}
    assert carried[0] == {"tool": "jira__search_issues", "failed": True}
    assert carried[1] == {"tool": "load_skill", "failed": False}
    assert len(carried) == CARRIED_MAX - 1  # the bound counts the one popped above


def test_bgui10_parts_6_code_task_hands_the_parts_beside_the_goal() -> None:
    call = Delegated("t7.0", ToolCall(id="c7", name="code_task", arguments={"goal": NOTES}), 7, "pending")
    view = DelegationView(batch=(call,), gathered=(_skill_load(),))
    decided = CodeTaskDelegation(request=REQUEST).decide(call, view)
    assert isinstance(decided, ChildRequest)
    task = cast("dict[str, Any]", decided.task)
    assert task["request"] == REQUEST and task["notes"] == NOTES
    assert task["carried"] == [{"tool": "load_skill", "failed": False, "skill": "pdf"}]
    assert "reportlab" in decided.goal


@dataclass
class _Claim:
    accepts: frozenset[str]

    def journal_accepts(self, feature: str) -> bool:
        return feature in self.accepts


@dataclass
class _Driver:
    claim: _Claim


@pytest.mark.parametrize(("accepts", "kept"), [(frozenset[str]({CHILD_TASKS}), True), (frozenset[str](), False)])
def test_bgui10_parts_7_the_parts_go_only_to_a_node_that_keeps_them(accepts: frozenset[str], kept: bool) -> None:
    request = ChildRequest(invocation_id="t1", goal="g", task={"request": "r"})
    out = Driver._with_task(cast("Driver", _Driver(_Claim(accepts))), request)  # pyright: ignore[reportPrivateUsage]
    assert (out.task is not None) is kept
    assert out.goal == "g"


def _iso(epoch_s: float) -> str:
    return datetime.fromtimestamp(epoch_s, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_bgui10_parts_8_the_scripted_coder_loop_makes_its_first_calls_then_hands_off_its_notes() -> None:
    """The e2e stack's scripted model (``deployment/e2e/fake_llm/server.py``): ``handoff-first`` lines are the calls the
    coder's loop makes before ``code_task`` (so the engine child carries them), ``handoff-notes`` its goal."""
    from tests.unit.harness.test_qa5_fake_llm_loop import server  # pyright: ignore[reportPrivateUsage]

    script = f"{REQUEST}\nhandoff-first lookup_note {{\"topic\": \"ipl\"}}\nhandoff-notes {NOTES}\nsay done"
    tools = ["code_task", "lookup_note"]
    first = server._hand_off([server.ChatMessage("user", script)], tools, "c1")  # pyright: ignore[reportPrivateUsage]
    assert first is not None and [(c.name, json.loads(c.arguments)) for c in first.tool_calls] == [
        ("lookup_note", {"topic": "ipl"})
    ]
    after = [
        server.ChatMessage("user", script),
        server.ChatMessage("assistant", tool_calls=first.tool_calls),
        server.ChatMessage("tool", "a note", tool_call_id="c1"),
    ]
    handed = server._hand_off(after, tools, "c2")  # pyright: ignore[reportPrivateUsage]
    assert handed is not None and handed.tool_calls[0].name == "code_task"
    assert json.loads(handed.tool_calls[0].arguments) == {"goal": NOTES}


JOB_TASK = {"$ref": "https://schemas.pipeshub.com/harness/v2/ext/pipeshub/api/job-brief.schema.json#/$defs/JobTask"}


def test_bgui10_parts_9_a_server_known_only_from_its_metadata_hands_off_a_task_the_contract_accepts() -> None:
    """QA-FINAL (found by the final pass's docker lane, AC-E6): a server granted whole and never listed (D-LAZY-7)
    has no tool count. Its parts carried `tools: null`, which the contract (and Node's spawn route) refuses, so the
    hand-off failed and no job started. Checked against the JSON Schema itself, so no null-for-absent slips by."""
    lazy = ConnectedSource(name="Atlassian Rovo", group="mcp_rovo", kind="mcp", summary="Jira and Confluence", tools=None)
    counted = ConnectedSource(name="e2esaas", group="mcp_e2esaas", kind="mcp", summary="Documents", tools=3)
    parts = cast("dict[str, Any]", TaskSpec.compose(request=REQUEST, goal=NOTES, context="", gathered=[_skill_load()],
                                                    sources=ConnectedSources((lazy, counted))).parts())
    assert parts["sources"] == [{"name": "Atlassian Rovo", "kind": "mcp", "summary": "Jira and Confluence"},
                                {"name": "e2esaas", "kind": "mcp", "summary": "Documents", "tools": 3}]
    errors = [e.message for e in Draft202012Validator(JOB_TASK, registry=registry()).iter_errors(parts)]
    assert errors == []
