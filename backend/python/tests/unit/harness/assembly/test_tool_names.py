"""PA-13: the names the legacy executor repairs, repaired the same way on the durable path.

The oracle is the legacy agent's own ``ToolExecutor`` built for the same
agent: a doubled name runs the tool it doubles, a toolset with one tool runs
that tool, and an ambiguous toolset name gets the hint listing its tools.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.ports import ToolContext

from . import world as w
from .test_parity import _durable_turn_one, _full_grant, _legacy

if TYPE_CHECKING:
    from collections.abc import Iterator

CTX = ToolContext("run-1", w.ORG, "t1.0", "att-1", "key")


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    with w.deployment():
        yield


async def _both() -> tuple[Any, Any]:
    agent = w.agent_doc()
    spec = w.pinned_spec(**_full_grant(agent))
    durable, _, _ = await _durable_turn_one(w.Services(agent=agent), spec, "quick", "find the bug")
    legacy_agent, _, _ = await _legacy(w.Services(agent=agent), spec, "quick", "find the bug")
    return durable.tools, legacy_agent._executor


async def _legacy_result(executor: Any, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:  # noqa: ANN401
    result = await executor.call_tool(ToolCall(id="c1", name=name, arguments=arguments))
    return str(result.content), result.is_error


@pytest.mark.parametrize(
    ("name", "resolves_to", "arguments"),
    [
        ("jira__search_issuesjira__search_issues", "jira__search_issues", {"jql": "x"}),
        ("jira__create_issuejira__create_issuejira__create_issue", "jira__create_issue", {"title": "t"}),
        ("knowledgegraph", "knowledgegraph__search", {"query": "q"}),
    ],
)
async def test_ut1_a_name_the_executor_repairs_runs_the_tool_it_names(
    name: str, resolves_to: str, arguments: dict[str, Any], caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Name repair only: an unwatched legacy run would refuse the Ask write that the durable kernel asks for
    # (test_sync3_tool_rules).
    monkeypatch.setenv("TOOLSET_TOOL_APPROVALS", "false")
    tools, legacy = await _both()
    caplog.set_level("INFO")
    effect = tools.describe(name)
    assert effect is not None
    # UT-3: the effect class and the journaled toolId are the resolved tool's.
    assert effect == tools.describe(resolves_to)
    executed = await tools.execute(ToolCall(id="c1", name=name, arguments=arguments), CTX)
    content, is_error = await _legacy_result(legacy, name, arguments)
    assert (str(executed.content), executed.status == "error") == (content, is_error)
    assert not is_error
    [line] = [r for r in caplog.records if getattr(r, "harness_event", None) == "tool.name_resolved"]
    fields = line.harness  # type: ignore[attr-defined]
    assert (fields["runId"], fields["toolId"]) == ("run-1", effect.tool_id)
    assert fields["kind"] == ("toolset" if name == "knowledgegraph" else "repeated")


async def test_ut3_a_repaired_write_name_is_a_write() -> None:
    tools, _ = await _both()
    effect = tools.describe("jira__create_issuejira__create_issue")
    assert effect is not None and effect.effect == "write" and effect.needs_intent_commit


@pytest.mark.parametrize("name", ["jira", "nosuchtool", "jira__nosuch"])
async def test_ut2_a_name_nothing_resolves_gets_the_legacy_error_and_never_runs(name: str) -> None:
    tools, legacy = await _both()
    assert tools.describe(name) is None
    content, is_error = await _legacy_result(legacy, name, {})
    assert is_error
    assert tools.unknown(name) == content
    refused = await tools.execute(ToolCall(id="c1", name=name, arguments={}), CTX)
    assert (refused.status, str(refused.content)) == ("error", content)
    if name == "jira":
        assert "that's a toolset name" in content and "jira__search_issues" in content


async def test_c2_the_toolset_hint_lists_what_legacy_lists_and_calling_it_is_still_refused() -> None:
    """The hint names the toolset's tools as legacy's executor does (C-2, was
    UT-2's deviation); since main's #4246 an agent holds only the tools picked
    for it, so a tool the grant lacks is neither listed nor run."""
    from harness_core.tools.executor import ToolExecutor

    agent = w.agent_doc()
    grant = _full_grant(agent)
    grant["tool_ids"] = [t for t in grant["tool_ids"] if "create_issue" not in t]
    durable, _, _ = await _durable_turn_one(
        w.Services(agent=agent), w.pinned_spec(**grant), "quick", "find the bug"
    )
    _legacy_agent, legacy_runtime, _goal = await _legacy(
        w.Services(agent=agent), w.pinned_spec(**grant), "quick", "find the bug"
    )
    hint = durable.tools.unknown("jira")
    assert hint == ToolExecutor(legacy_runtime.tool_registry).unknown_tool_error("jira")
    assert "jira__create_issue" not in hint
    assert durable.tools.describe("jira__create_issue") is None
