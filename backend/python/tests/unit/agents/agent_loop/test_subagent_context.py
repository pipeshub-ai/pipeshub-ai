"""SC-11 (CE-16): a sub-agent spawned on the current turn is told the user's
request verbatim, never gets the parent's attachment images, and keeps its own
per-run context state.

Before, a child's only user message was the parent model's paraphrase of the
task, the root's attachment image was injected into every child's request (the
PRE_MODEL hooks are shared by the whole spawn tree), and children shared the
parent's retrieved-image stash.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.context.manager import ContextManager
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ImagePart,
    TextPart,
    ToolCall,
    UserMessage,
)
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.builtin.coordination.agent_tool import AgentTool
from app.agent_loop_lib.tools.builtin.coordination.spawn_agent import SpawnAgentTool
from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.factory import PipesHubAgentFactory
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

EARLIER = "QUESTION A: how do SMS appointment reminders work?"
CURRENT = "QUESTION B: build a PDF of the open billing tickets assigned to me"
PARAPHRASE = "Create a PDF report of the tickets above"
_IMAGE = {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}}


def _model() -> ModelSpec:
    return ModelSpec(provider="scripted", model="scripted-model")


def _child_spec(role_name: str = "pdf", **_: Any) -> AgentSpec:  # noqa: ANN401
    return AgentSpec(name=f"child-{role_name}", system_prompt="You build documents.",
                     model=_model(), loop=ReActLoop(), max_turns=3)


def _parent(transport: ScriptedTransport, *, tool: str) -> tuple[Agent, list[tuple[str | None, Any]]]:
    """A root agent on the second turn of a chat, with the production PRE_MODEL
    pipeline and an image attached to the current question."""
    context = AgentContext(org_id="o", user_id="u", user_email="u@example.com")
    context.attachment_image_blocks = [_IMAGE]
    hooks = PipesHubAgentFactory._build_hooks(context, model_name="scripted-model")
    memos: list[tuple[str | None, Any]] = []

    async def _record_memo(ctx: Any, next_fn: Any) -> None:  # noqa: ANN401
        memos.append((ctx.scope.run.identity.run_id, getattr(ctx, "compaction_memo", None)))
        await next_fn()

    hooks.on(HookEvent.PRE_MODEL).use(_record_memo)

    registry = ToolRegistry()
    registry.register_tool(TaskCompleteTool())
    transport_registry = TransportRegistry()
    transport_registry.register("scripted", lambda: transport)
    runtime = AgentRuntime(transport_registry=transport_registry, tool_registry=registry,
                           hooks=hooks, spec_factory=_child_spec)
    registry.register_tool(SpawnAgentTool())
    registry.register_tool(AgentTool(_child_spec(), runtime, name="pdf_agent"))
    spec = AgentSpec(name="assistant", system_prompt="You help.", tool_names=[tool, "task_complete"],
                     model=_model(), loop=ReActLoop(), max_turns=4)
    agent = Agent(spec, runtime)
    history = ContextManager()
    agent.seed_context(history)
    return agent, memos


async def _run(tool: str, call_arguments: dict) -> tuple[ScriptedTransport, list]:
    transport = ScriptedTransport()
    transport.add_tool_call(ToolCall(id="spawn1", name=tool, arguments=call_arguments))
    transport.add_text("child: report built")
    transport.add_text("Here is your report.")
    agent, memos = _parent(transport, tool=tool)
    await agent.context.add(UserMessage(content=EARLIER))
    await agent.context.add(AssistantMessage(content=[TextPart(text="Enable reminders in Settings.")]))
    await agent.run(Goal(description=CURRENT))
    return transport, memos


def _texts(messages: list) -> list[str]:
    return [
        m.content if isinstance(m.content, str) else " ".join(p.text for p in m.content if isinstance(p, TextPart))
        for m in messages if isinstance(m, UserMessage)
    ]


def _images(messages: list) -> int:
    return sum(
        isinstance(p, ImagePart)
        for m in messages if isinstance(m, UserMessage) and isinstance(m.content, list)
        for p in m.content
    )


_SPAWNS = {
    "agent_tool": ("pdf_agent", {"goal": PARAPHRASE}),
    "spawn_agent": ("spawn_agent", {"role": "pdf", "goal": PARAPHRASE, "reasoning": "separate workstream"}),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("spawn", _SPAWNS)
async def test_child_request_carries_the_users_words(spawn) -> None:
    transport, _ = await _run(*_SPAWNS[spawn])
    parent_call, child_call = transport.calls[0]["messages"], transport.calls[1]["messages"]

    child_request = _texts(child_call)[-1]
    assert CURRENT in child_request
    assert PARAPHRASE in child_request
    assert child_request.index(CURRENT) < child_request.index(PARAPHRASE)
    assert EARLIER not in " ".join(_texts(child_call))
    assert _texts(parent_call)[-1] == CURRENT


@pytest.mark.asyncio
@pytest.mark.parametrize("spawn", _SPAWNS)
async def test_child_gets_no_parent_attachment_images(spawn) -> None:
    transport, _ = await _run(*_SPAWNS[spawn])
    assert _images(transport.calls[0]["messages"]) == 1, "the parent's own call carries the attachment"
    assert _images(transport.calls[1]["messages"]) == 0


@pytest.mark.asyncio
async def test_child_keeps_its_own_compaction_memo() -> None:
    _, memos = await _run(*_SPAWNS["agent_tool"])
    by_run = dict(memos)
    assert len(by_run) == 2
    parent_memo, child_memo = by_run.values()
    assert parent_memo is not None and child_memo is not None
    assert parent_memo is not child_memo
