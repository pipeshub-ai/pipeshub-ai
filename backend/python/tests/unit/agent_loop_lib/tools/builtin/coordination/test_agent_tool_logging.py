"""A domain agent (`AgentTool.handle`, a special route) that fails: its `tool_call.end` carries the
cause's class or the child's, and its child's `sub_agent.end` names the caller's run as parent."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.core.types import AgentResult, Goal, ToolCall
from app.agent_loop_lib.tools.builtin.coordination.agent_tool import AgentTool
from tests.unit.agent_loop_lib.runtime.test_run_child_logging import (
    Harness,
    fields,
    named,
    run_turn,
    spec,
    warn_plus,
)

pytestmark = pytest.mark.asyncio


def _with_domain_agent(h: Harness) -> Agent:
    h.registry.register_tool(AgentTool(spec("jira_agent", [], provider="jira"), h.runtime))
    parent = h.parent()
    parent._spec = parent.spec.model_copy(update={"tool_names": [*parent.spec.tool_names, "jira_agent"]})
    return parent


async def test_a_domain_agent_that_raises_is_one_classed_line(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    caplog.set_level(logging.DEBUG)
    real_run = Agent.run

    async def _run(self: Agent, goal: Goal, **kwargs: Any) -> AgentResult:  # noqa: ANN401
        if self.spec.name == "jira_agent":
            raise TimeoutError("jira hung")
        return await real_run(self, goal, **kwargs)

    monkeypatch.setattr(Agent, "run", _run)
    h = Harness("jira")
    h.transports["parent"].add_tool_call(ToolCall(id="call_j", name="jira_agent", arguments={"goal": "find it"})).add_text("done")
    parent = _with_domain_agent(h)

    await run_turn(parent)

    (failed,) = warn_plus(caplog)
    assert failed.getMessage() == "tool_call.end"
    assert (fields(failed)["reason"], fields(failed)["error_class"], fields(failed)["kind"]) == ("exception", "timeout", "sub_agent")
    (end,) = named(caplog, "sub_agent.end")
    assert (fields(end)["outcome"], fields(end)["parent_run_id"], fields(end)["tool_call_id"]) == ("failure", parent.run_ctx.run_id, "call_j")


async def test_a_domain_agent_whose_child_failed_carries_the_childs_class(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    h = Harness("jira")
    h.transports["parent"].add_tool_call(ToolCall(id="call_j", name="jira_agent", arguments={"goal": "find it"})).add_text("done")
    h.transports["jira"].add_error(TransportError("upstream exploded", status_code=500))
    parent = _with_domain_agent(h)

    await run_turn(parent)

    (end,) = [r for r in named(caplog, "tool_call.end") if fields(r)["tool"] == "jira_agent"]
    assert (end.levelno, fields(end)["reason"], fields(end)["error_class"]) == (logging.WARNING, "sub_agent_failed", "upstream_5xx")


async def test_the_inline_childs_ids_do_not_leak_into_the_parent(caplog: pytest.LogCaptureFixture) -> None:
    """`AgentTool.handle` runs its child in the parent's task, so only `run_child`'s reset keeps the
    child's `agent_run_id` off the parent's later lines."""
    from app.agents.agent_loop.usage_transport import UsageReportingTransport

    caplog.set_level(logging.DEBUG)
    h = Harness("jira")
    scripted = h.transports["parent"].add_tool_call(ToolCall(id="call_j", name="jira_agent", arguments={"goal": "find it"})).add_text("done")
    h.runtime.transport_registry.register("parent", lambda: UsageReportingTransport(scripted, lambda *_: None))
    h.transports["jira"].add_text("found")
    parent = _with_domain_agent(h)

    await run_turn(parent)

    (child_end,) = named(caplog, "sub_agent.end")
    assert fields(child_end)["agent_run_id"]
    (tool_end,) = [r for r in named(caplog, "tool_call.end") if fields(r)["tool"] == "jira_agent"]
    llm_ends = named(caplog, "llm_call.end")
    assert len(llm_ends) == 2  # the second is the parent's call after the tool returned
    assert "agent_run_id" not in fields(tool_end)
    assert "agent_run_id" not in fields(llm_ends[1])
