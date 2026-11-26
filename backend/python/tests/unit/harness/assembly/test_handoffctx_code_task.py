"""HANDOFF-CTX HC-2..HC-5: a code_task engine child starts with what the chat already gathered.

A Jira MCP conversation: the loop called getAccessibleAtlassianResources, searchJiraIssuesUsingJql
and getJiraIssue, then handed off (DEEPROUTE escalation, many_steps). The engine child got only the model's brief and
made the same calls again, its first one refused for the ``cloudId`` the parent had already found. The child's goal
now carries each earlier call, its arguments and its result as the chat read it, with the instruction not to repeat
them, inside a fixed size.
"""

from __future__ import annotations

import json

from app.harness.assembly.code_task import (
    GATHERED_HEADING,
    GATHERED_MAX_CHARS,
    GATHERED_RESULT_MAX_CHARS,
    CodeTaskDelegation,
)
from harness_core.journal.children import ChildRequest
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.delegation import Delegated, DelegationView, Gathered

CLOUD = "4d1f-cloud-7a"
SEARCH = "mcp_atlassian_rovo_searchJiraIssuesUsingJql"


def _code_task(goal: str = "Summarise the open bugs") -> Delegated:
    return Delegated("t7.0", ToolCall(id="c7", name="code_task", arguments={"goal": goal}), 7, "pending")


def _gathered() -> tuple[Gathered, ...]:
    return (
        Gathered(
            ToolCall(id="c1", name="mcp_atlassian_rovo_getAccessibleAtlassianResources", arguments={}),
            1, "ok", [{"id": CLOUD, "name": "acme"}],
        ),
        Gathered(
            ToolCall(id="c2", name=SEARCH, arguments={"cloudId": CLOUD, "jql": "type = Bug AND status = Open"}),
            2, "ok", {"issues": [{"key": "PIPE-101", "summary": "Login loops"}, {"key": "PIPE-102", "summary": "Sync"}]},
        ),
        Gathered(
            ToolCall(id="c3", name="mcp_atlassian_rovo_getJiraIssue", arguments={"cloudId": CLOUD, "issueIdOrKey": "X"}),
            3, "error", "Issue X does not exist",
        ),
    )


def _goal(view: DelegationView) -> str:
    call = view.batch[0]
    decided = CodeTaskDelegation().decide(call, view)
    assert isinstance(decided, ChildRequest), decided
    return decided.goal


def test_hc2_the_childs_goal_carries_each_earlier_call_its_arguments_and_result() -> None:
    goal = _goal(DelegationView(batch=(_code_task(),), gathered=_gathered()))
    assert goal.startswith("Summarise the open bugs"), goal
    assert GATHERED_HEADING in goal
    assert SEARCH in goal and json.dumps({"cloudId": CLOUD, "jql": "type = Bug AND status = Open"}) in goal
    # The id the engine's first call lacked, and the issues it fetched again.
    assert CLOUD in goal and "PIPE-101" in goal and "Login loops" in goal
    # A failed call is shown as failed, so the engine neither trusts nor blindly repeats it.
    assert "(failed)" in goal and "Issue X does not exist" in goal
    assert "do not call" in goal.lower()


def test_hc3_without_earlier_results_the_goal_is_as_before() -> None:
    assert _goal(DelegationView(batch=(_code_task(),))) == "Summarise the open bugs"


def test_hc4_results_are_shortened_to_a_fixed_size_and_every_call_is_still_named() -> None:
    big = "x" * (GATHERED_RESULT_MAX_CHARS * 3)
    many = tuple(
        Gathered(ToolCall(id=f"c{i}", name=f"tool_{i}", arguments={"page": i}), i, "ok", big) for i in range(1, 12)
    )
    goal = _goal(DelegationView(batch=(_code_task(),), gathered=many))
    section = goal.split(GATHERED_HEADING, 1)[1]
    assert len(section) <= GATHERED_MAX_CHARS + 2_000, len(section)
    assert all(f"tool_{i}(" in section for i in range(1, 12)), "every call is named, even past the size"
    assert "shortened" in section


def test_hc5_a_decision_is_the_same_for_the_same_view() -> None:
    view = DelegationView(batch=(_code_task(),), gathered=_gathered())
    assert _goal(view) == _goal(view)
