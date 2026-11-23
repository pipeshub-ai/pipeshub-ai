"""Stop pressed while a `run_code` call is running: the call is interrupted,
its slot is freed after the backend's cleanup, the model gets no further
turn, and the run ends as cancelled (the path that saves a "Stopped"
message), not as a disconnect. A real `Agent` and `AgentRuntime` with the
production turn guards; only the LLM and the sandbox backend are fakes."""

from __future__ import annotations

import asyncio

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.context import CancellationToken
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.sandbox.coding.base import CodeRequest, CodeResult
from app.agent_loop_lib.sandbox.governor import GovernorLimits, SandboxResourceGovernor
from app.agent_loop_lib.sandbox.manager import SandboxManager, SandboxType
from app.agent_loop_lib.tools.builtin.sandbox.coding_sandbox import CodingSandboxTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

_GOAL = Goal(description="Run the analysis.")


class _Backend:
    """Not metered (per-call slots, like Docker). `execute` blocks until it
    is cancelled, unless `finish` is set, and records what it saw."""

    class capabilities:  # stands in for SandboxCapabilities
        is_metered = False

    instances: list[_Backend]

    def __init__(self, *, finish: bool = False) -> None:
        self.sandbox_id = f"sbx-{id(self)}"
        self.finish = finish
        self.started = asyncio.Event()
        self.cancels = 0

    async def provision(self) -> None:
        return None

    async def destroy(self) -> None:
        return None

    async def upload_file(self, path: str, content: bytes) -> None:
        return None

    async def execute(self, request: CodeRequest) -> CodeResult:
        self.started.set()
        if self.finish:
            return CodeResult(stdout="42\n", stderr="", exit_code=0, language=request.language, duration_ms=1.0)
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.cancels += 1
            await asyncio.sleep(0.05)  # the container being removed
            raise
        raise AssertionError("unreachable")


def _build(
    transport: ScriptedTransport, token: CancellationToken, *, finish: bool = False,
) -> tuple[Agent, list[_Backend], SandboxResourceGovernor]:
    backends: list[_Backend] = []

    def make() -> _Backend:
        backends.append(_Backend(finish=finish))
        return backends[-1]

    governor = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=4))
    manager = SandboxManager(governor=governor, stop_token=token, slot_wait_s=5)
    manager.register_backend_factory(SandboxType.CODING, make)
    registry = ToolRegistry()
    registry.register_tool(CodingSandboxTool(manager))
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    runtime = AgentRuntime(
        transport_registry=transports, tool_registry=registry, cancellation_token=token,
    )
    spec = AgentSpec(
        name="stop-sandbox-agent",
        system_prompt="You are a helpful assistant.",
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=ReActLoop(),
        max_turns=5,
    )
    return Agent(spec, runtime), backends, governor


def _run_code(call_id: str) -> ToolCall:
    return ToolCall(id=call_id, name="run_code", arguments={"code": "while True: pass", "language": "python"})


async def _stop_once_running(token: CancellationToken, backends: list[_Backend], count: int) -> None:
    for _ in range(300):
        if len(backends) >= count and all(b.started.is_set() for b in backends):
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    token.cancel()


async def test_stop_interrupts_a_running_run_code_and_ends_the_run_cancelled() -> None:
    token = CancellationToken()
    transport = ScriptedTransport().add_tool_call(_run_code("c1")).add_text("should never be asked")
    agent, backends, governor = _build(transport, token)

    stopper = asyncio.ensure_future(_stop_once_running(token, backends, 1))
    result = await asyncio.wait_for(agent.run(_GOAL), timeout=5)
    await stopper

    assert result.cancelled is True
    assert len(transport.calls) == 1  # no turn after Stop
    assert backends[0].cancels == 1
    assert governor.snapshot()["total"] == 0
    tool_result = agent.scope.turns[0].tool_results[0]
    assert tool_result.is_error is True
    assert "Stopped by the user" in str(tool_result.content)


async def test_two_parallel_run_code_calls_both_stop() -> None:
    token = CancellationToken()
    transport = ScriptedTransport().add_tool_calls([_run_code("c1"), _run_code("c2")]).add_text("never")
    agent, backends, governor = _build(transport, token)

    stopper = asyncio.ensure_future(_stop_once_running(token, backends, 2))
    result = await asyncio.wait_for(agent.run(_GOAL), timeout=5)
    await stopper

    assert result.cancelled is True
    assert [b.cancels for b in backends] == [1, 1]
    assert governor.snapshot()["total"] == 0
    assert all("Stopped by the user" in str(r.content) for r in agent.scope.turns[0].tool_results)


async def test_stop_after_the_run_finished_keeps_its_real_result() -> None:
    token = CancellationToken()
    transport = ScriptedTransport().add_tool_call(_run_code("c1")).add_text("never")
    agent, backends, _governor = _build(transport, token, finish=True)

    async def stop_after_tool(ctx: object, next_fn) -> None:
        await next_fn()
        token.cancel()

    from app.agent_loop_lib.hooks.events import HookEvent

    agent.runtime.hooks.on(HookEvent.POST_TOOL_USE).use(stop_after_tool)
    result = await asyncio.wait_for(agent.run(_GOAL), timeout=5)

    assert result.cancelled is True
    assert backends[0].cancels == 0
    tool_result = agent.scope.turns[0].tool_results[0]
    assert tool_result.is_error is False
    assert "42" in str(tool_result.content)
