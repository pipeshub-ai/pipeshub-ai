"""K-14: the moved kernel runs without the PipesHub app and emits only its own events."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from harness_core.kernel.agent import Agent
from harness_core.kernel.agent.spec import AgentSpec
from harness_core.kernel.events import AgentEvent, EventEmitter, EventType
from harness_core.kernel.runtime import AgentRuntime

PYTHON_ROOT = Path(__file__).resolve().parents[3]

_KERNEL_MODULES = (
    "harness_core.kernel.agent",
    "harness_core.kernel.agent.loops",
    "harness_core.kernel.hooks.builtin.turn_guards",
    "harness_core.kernel.hooks.builtin.stall_detection",
    "harness_core.kernel.budget.tracker",
    "harness_core.tools.executor",
    "harness_core.models.transports.anthropic",
    "harness_core.models.transports.openai",
    "harness_core.models.transports.azure_openai",
    "harness_core.models.transports.gemini",
    "harness_core.models.transports.ollama",
)


def test_kernel_imports_and_builds_without_the_app() -> None:
    script = (
        "import importlib, sys\n"
        f"for name in {_KERNEL_MODULES!r}: importlib.import_module(name)\n"
        "from harness_core.kernel.runtime import AgentRuntime\n"
        "r = AgentRuntime()\n"
        "assert (r.derive_events, r.spawn_scheduler, r.streamed_answer_argument) == (None, None, None)\n"
        "leaked = sorted(m for m in sys.modules if m == 'app' or m.startswith('app.'))\n"
        "assert not leaked, leaked\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PYTHON_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr


class _Recorder(EventEmitter):
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    async def emit(self, event: AgentEvent) -> None:
        self.events.append(event)


async def test_runtime_without_a_deriver_emits_only_kernel_events() -> None:
    recorder = _Recorder()
    runtime = AgentRuntime(event_emitter=recorder, derive_events=None)
    agent = Agent(AgentSpec(), runtime)

    await agent.emit(EventType.AGENT_START, {"goal": "g"})
    await agent.emit(EventType.TOOL_RESULT, {"tool": "t"})

    assert [e.event_type for e in recorder.events] == [EventType.AGENT_START, EventType.TOOL_RESULT]
