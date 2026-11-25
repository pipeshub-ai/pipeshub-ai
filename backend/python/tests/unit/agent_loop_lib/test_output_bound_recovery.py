"""P4.17 UX-1, UX-3, UX-4 on today's loop (``Agent``): a capped or cut answer ends coherently."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import UserMessage
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

if TYPE_CHECKING:
    from app.agent_loop_lib.core.messages import Message

_GOAL = Goal(description="Write the Acme pre-renewal report")


def _build(transport: ScriptedTransport, *, max_turns: int = 6) -> Agent:
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    spec = AgentSpec(
        name="report-agent",
        system_prompt="You are a helpful assistant.",
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=ReActLoop(),
        max_turns=max_turns,
    )
    return Agent(spec, AgentRuntime(transport_registry=transports, tool_registry=ToolRegistry()))


def _last_injected(call: dict[str, Any]) -> str:
    messages: list[Message] = call["messages"]
    injected = [m for m in messages if isinstance(m, UserMessage) and m.injected]
    if not injected:
        return ""
    content = injected[-1].content
    return content if isinstance(content, str) else "".join(getattr(p, "text", "") for p in content)


async def test_ux1_a_capped_answer_is_continued_and_joined() -> None:
    transport = ScriptedTransport().add_truncated("Acme's renewal was due 31 January 2026; ")
    transport.add_text("the open P2 SafeStop issue is the main risk.")
    result = await _build(transport).run(_GOAL)
    assert result.success
    assert result.output == "Acme's renewal was due 31 January 2026; the open P2 SafeStop issue is the main risk."
    assert "Continue from where" in _last_injected(transport.calls[1])


async def test_ux3_a_reasoning_only_exhaustion_asks_for_the_answer_not_a_continuation() -> None:
    transport = ScriptedTransport().add_truncated("")
    transport.add_text("Acme's renewal date has passed; the SafeStop issue is the main risk.")
    result = await _build(transport).run(_GOAL)
    assert result.success
    assert result.output == "Acme's renewal date has passed; the SafeStop issue is the main risk."
    note = _last_injected(transport.calls[1])
    assert "produced no answer" in note and "answer now" in note
    assert "Continue from where" not in note


async def test_ux4_a_reply_that_loops_is_trimmed_and_continued_without_the_loop() -> None:
    intro = "Acme pre-renewal report. The renewal was due 31 January 2026. "
    transport = ScriptedTransport().add_text(intro + "The renewal date has passed. " * 400)
    transport.add_text("The main risk is the open SafeStop issue on line 3.")
    result = await _build(transport).run(_GOAL)
    assert result.success
    assert result.output.count("The renewal date has passed.") <= 1
    assert result.output.startswith("Acme pre-renewal report.")
    assert result.output.endswith("The main risk is the open SafeStop issue on line 3.")
    note = _last_injected(transport.calls[1])
    assert "repeating the same text" in note
