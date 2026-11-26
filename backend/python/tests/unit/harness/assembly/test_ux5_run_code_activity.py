"""UX5 RC: ``run_code`` on the durable loop shows its program's output live and journals its record.

The real assembler and the local backend (as P5.11's tests), the activity bus in memory; the broker backend's
streaming is driven with a process the test releases chunk by chunk.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
from contextlib import ExitStack
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app.agent_loop_lib.sandbox.coding.broker import run_command
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from harness_core.activity import recording
from harness_core.journal.memory import InMemoryLiveBus
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable import ToolContext
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort
from tests.harness_replay.artifacts import ParityArtifactRegistry
from tests.support.sandbox_env import sandbox_backend, sandbox_env

from . import world as w

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from harness_core.activity import CommandRecord, CommandStatus, OutputStream
    from harness_core.driver import AssembledRuntime

CODE = "import sys\nprint('hello')\nprint('oops', file=sys.stderr)\n"


@pytest.fixture(autouse=True)
def _deployment() -> Iterator[None]:
    with ExitStack() as stack:
        stack.enter_context(w.deployment())
        stack.enter_context(
            patch("app.services.artifact_registry.ArtifactRegistryService", new=ParityArtifactRegistry(w.ORG, "ses-1"))
        )
        stack.enter_context(patch.dict(os.environ, {**sandbox_env("host"), "SANDBOX_ALLOW_NETWORK": "false"}))
        stack.enter_context(sandbox_backend("host"))
        yield


async def _build(bus: InMemoryLiveBus, *, accepts: frozenset[str] | None = None) -> AssembledRuntime:
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    assembler = PipesHubRuntimeAssembler(w.Services(), model_factory=w.model_factory(port), activity=bus)
    claim = w.claim(w.pinned_spec(tool_ids=[], sandbox="agent-code"))
    return await assembler.build(dataclasses.replace(claim, accepts=accepts))  # type: ignore[arg-type]


async def _call(runtime: AssembledRuntime, code: str = CODE, invocation: str = "t1.0") -> Any:  # noqa: ANN401
    ctx = ToolContext("run-1", w.ORG, invocation, "att-1", f"{w.ORG}:run-1:{invocation}")
    call = ToolCall(id="c1", name="run_code", arguments={"code": code, "language": "python"})
    return await runtime.tools.execute(call, ctx)


async def test_rc2_rc4_the_call_journals_its_record_and_shows_its_output() -> None:
    bus = InMemoryLiveBus()
    runtime = await _build(bus)
    execution = await _call(runtime)
    [record] = [e for e in execution.events if e.type == "x.pipeshub.command.finished"]
    data = record.data
    assert (data["commandId"], data["invocationId"], data["source"], data["language"]) == (
        "t1.0",
        "t1.0",
        "run_code",
        "python",
    )
    assert data["command"] == CODE
    assert data["exitCode"] == 0
    assert data["status"] == "completed"
    assert isinstance(data["durationMs"], int)
    assert data["stdout"]["tail"] == "hello\n"  # pyright: ignore[reportIndexIssue,reportCallIssue,reportArgumentType]
    assert data["stderr"]["tail"] == "oops\n"  # pyright: ignore[reportIndexIssue,reportCallIssue,reportArgumentType]
    assert "log" in data
    assert record.critical is False
    await runtime.close()  # type: ignore[misc]
    frames = await bus.read("run-1")
    assert next(f.kind for f in frames) == "command_started"
    assert frames[-1].kind == "command_finished"
    # The local backend cannot stream: its output arrives once, at completion (RC-4).
    shown = {f.data["stream"]: f.data["text"] for f in frames if f.kind == "command_output"}
    assert shown == {"stdout": "hello\n", "stderr": "oops\n"}


async def test_rc2_a_failing_program_is_a_failed_command() -> None:
    bus = InMemoryLiveBus()
    runtime = await _build(bus)
    execution = await _call(runtime, "raise SystemExit(3)\n")
    [record] = [e for e in execution.events if e.type == "x.pipeshub.command.finished"]
    assert (record.data["status"], record.data["exitCode"]) == ("failed", 3)
    await runtime.close()  # type: ignore[misc]


async def test_rc3_a_recorded_answer_is_not_shown_again() -> None:
    bus = InMemoryLiveBus()
    first = await _build(bus)
    _ = await _call(first)
    await first.close()  # type: ignore[misc]
    before = len(await bus.read("run-1"))
    retried = await _build(bus)
    again = await _call(retried)
    await retried.close()  # type: ignore[misc]
    assert [e for e in again.events if e.type == "x.pipeshub.command.finished"] == []
    assert len(await bus.read("run-1")) == before


async def test_rc5_an_older_node_gets_frames_but_no_event() -> None:
    bus = InMemoryLiveBus()
    runtime = await _build(bus, accepts=frozenset({"approval_reasons"}))
    execution = await _call(runtime)
    await runtime.close()  # type: ignore[misc]
    assert [e for e in execution.events if e.type == "x.pipeshub.command.finished"] == []
    assert [f.kind for f in await bus.read("run-1")][-1] == "command_finished"


class _Collecting:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []

    def write(self, stream: OutputStream, text: str) -> None:
        if text:
            self.writes.append((stream, text))

    async def finish(
        self, *, exit_code: int | None, status: CommandStatus, duration_ms: int | None = None
    ) -> CommandRecord:
        raise NotImplementedError


class _Process:
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.stdout = self._stdout()
        self.stderr = self._stderr()

    async def _stdout(self) -> AsyncIterator[bytes]:
        yield b"first \xc3"
        yield b"\xa9\n"
        _ = await self.release.wait()
        yield b"second\n"

    async def _stderr(self) -> AsyncIterator[bytes]:
        _ = await self.release.wait()
        yield b"warn\n"

    async def wait(self, timeout_s: float) -> int:
        _ = await self.release.wait()
        return 0


class _Handle:
    def __init__(self, process: _Process) -> None:
        self.process = process

    async def exec(self, command: list[str], *, env: dict[str, str]) -> _Process:
        return self.process


async def test_rc1_the_broker_hands_each_chunk_over_before_the_program_ends() -> None:
    process = _Process()
    recorder = _Collecting()
    with recording(recorder):
        running = asyncio.create_task(run_command(_Handle(process), ["python"], {}, 30))  # type: ignore[arg-type]
        while "".join(t for _, t in recorder.writes) != "first é\n":
            _ = await asyncio.sleep(0)
        assert not process.release.is_set()
        assert running.done() is False
        process.release.set()
        exit_code, stdout, stderr = await running
    assert (exit_code, stdout, stderr) == (0, "first é\nsecond\n", "warn\n")
    assert "".join(t for s, t in recorder.writes if s == "stdout") == "first é\nsecond\n"
    assert [t for s, t in recorder.writes if s == "stderr"] == ["warn\n"]


async def test_rc1_without_a_recorder_the_broker_is_unchanged() -> None:
    process = _Process()
    process.release.set()
    assert await run_command(_Handle(process), ["python"], {}, 30) == (0, "first é\nsecond\n", "warn\n")  # type: ignore[arg-type]


def _with_transport(port: ScriptedModelPort) -> Any:  # noqa: ANN401
    from harness_core.testing.model_gateway import RecordedTransport

    plain = w.model_factory(port)

    async def build(run: Any, services: object) -> Any:  # noqa: ANN401
        return dataclasses.replace(await plain(run, services), transport=RecordedTransport())

    return build


@pytest.mark.followups
async def test_fu1_a_top_level_run_is_assembled_with_the_suggestions_step(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.harness.assembly import assembler as assembly
    from app.harness.assembly.followups import FollowupsStep

    # Followups come with the harness (CTL-5); the suite turns them off, this test is about the product's wiring.
    monkeypatch.setattr(assembly, "FOLLOWUP_SUGGESTIONS", True)

    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    assembler = PipesHubRuntimeAssembler(w.Services(), model_factory=_with_transport(port))
    runtime = await assembler.build(w.claim(w.pinned_spec(tool_ids=[], sandbox="none")))  # type: ignore[arg-type]
    assert [type(step) for step in runtime.after_run if isinstance(step, FollowupsStep)] == [FollowupsStep]
    await runtime.close()  # type: ignore[misc]


async def test_fu3_other_tests_run_without_the_suggestions_step() -> None:
    from app.harness.assembly.followups import FollowupsStep

    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    assembler = PipesHubRuntimeAssembler(w.Services(), model_factory=_with_transport(port))
    runtime = await assembler.build(w.claim(w.pinned_spec(tool_ids=[], sandbox="none")))  # type: ignore[arg-type]
    assert not any(isinstance(step, FollowupsStep) for step in runtime.after_run)
    await runtime.close()  # type: ignore[misc]
