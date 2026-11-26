"""CS-1: interrupting a Codex turn that has already ended is a no-op, as for every engine."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, override

import pytest

from harness_core.engines.errors import EngineUnhealthy
from harness_core.engines.events import EngineError, TurnCompleted
from harness_core.engines.types import EngineTask
from harness_core.logs import HarnessLog
from harness_engines.codex.rpc import INVALID_REQUEST, CodexRpcError
from harness_engines.codex.session import CodexSession

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.types import JsonValue

THREAD, TURN = "thr-1", "turn-1"
REFUSED = {"codexErrorInfo": {"responseStreamDisconnected": {"httpStatusCode": 402}}}


class FakeRpc:
    """Answers ``thread/start`` and ``turn/start``; ``turn/interrupt`` gets the scripted error code."""

    def __init__(self, interrupt_code: int) -> None:
        self.incoming: asyncio.Queue[Mapping[str, JsonValue]] = asyncio.Queue()
        self.requests: list[str] = []
        self._interrupt_code = interrupt_code

    async def request(self, method: str, params: Mapping[str, JsonValue]) -> JsonValue:
        self.requests.append(method)
        if method == "thread/start":
            return {"thread": {"id": THREAD}}
        if method == "turn/start":
            return {"turn": {"id": TURN}}
        raise CodexRpcError(method, self._interrupt_code, "no active turn to interrupt")


def _session(rpc: FakeRpc) -> CodexSession:
    return CodexSession(
        engine=None,  # type: ignore[arg-type]
        sandbox=SimpleNamespace(id="sbx-1"),  # type: ignore[arg-type]
        process=None,  # type: ignore[arg-type]
        rpc=rpc,  # type: ignore[arg-type]
        reported_version="0.160.0",
        resume_thread=None,
        secrets=frozenset(),
        log=HarnessLog(logging.getLogger("test.codex.session")),
    )


def _refused_turn(*, separate_error: bool) -> list[Mapping[str, JsonValue]]:
    """The model gateway's budget refusal: Codex ends the turn itself, failed."""
    turn: dict[str, JsonValue] = {"id": TURN, "status": "failed", "error": REFUSED}
    error = {"method": "error", "params": {"threadId": THREAD, "turnId": TURN, "error": REFUSED, "willRetry": False}}
    completed = {"method": "turn/completed", "params": {"threadId": THREAD, "turn": turn}}
    return [error, completed] if separate_error else [completed]


async def _drive(rpc: FakeRpc, session: CodexSession) -> list[str]:
    """Play the controller: interrupt on the first engine error, while the turn's generator is suspended."""
    seen: list[str] = []
    async for event in session.run_turn(EngineTask(turn_id="trn-1", prompt="say never", instructions="")):
        seen.append(type(event).__name__)
        if isinstance(event, EngineError):
            await session.interrupt()
        if isinstance(event, TurnCompleted):
            seen.append(event.reason)
    return seen


async def test_cs1_interrupt_after_the_mapper_saw_turn_completed_sends_nothing() -> None:
    rpc = FakeRpc(INVALID_REQUEST)
    for message in _refused_turn(separate_error=False):
        rpc.incoming.put_nowait(message)
    session = _session(rpc)
    assert await _drive(rpc, session) == ["EngineError", "TurnCompleted", "failed"]
    assert "turn/interrupt" not in rpc.requests


async def test_cs1_interrupt_racing_codexs_own_end_is_a_no_op(caplog: pytest.LogCaptureFixture) -> None:
    rpc = FakeRpc(INVALID_REQUEST)
    for message in _refused_turn(separate_error=True):
        rpc.incoming.put_nowait(message)
    session = _session(rpc)
    with caplog.at_level(logging.INFO, "test.codex.session"):
        seen = await _drive(rpc, session)
    assert seen[:2] == ["EngineError", "TurnCompleted"]
    assert "turn/interrupt" in rpc.requests
    assert "engine.turn.interrupt_after_end" in caplog.text


async def test_cs1_any_other_interrupt_error_still_fails_the_turn() -> None:
    rpc = FakeRpc(-32603)
    for message in _refused_turn(separate_error=True):
        rpc.incoming.put_nowait(message)
    with pytest.raises(CodexRpcError) as failed:
        _ = await _drive(rpc, _session(rpc))
    assert failed.value.code == -32603


class ResumingRpc(FakeRpc):
    """Answers ``thread/resume`` too, and keeps every request's parameters."""

    def __init__(self) -> None:
        super().__init__(INVALID_REQUEST)
        self.params: list[tuple[str, Mapping[str, JsonValue]]] = []

    async def request(self, method: str, params: Mapping[str, JsonValue]) -> JsonValue:
        self.params.append((method, params))
        if method == "thread/resume":
            self.requests.append(method)
            return {"thread": {"id": THREAD}}
        return await super().request(method, params)


async def test_fixp2_b2_a_resumed_threads_first_turn_carries_the_runs_instructions() -> None:
    """FIX-P2-B2: Codex 0.160 keeps a resumed thread's developer instructions (``thread/resume`` does not replace
    them), so a later run of the conversation never gave the model its own instructions, the memories accepted
    since the thread started among them (S3 memory-basics-15m, 2026-10-22). The run's instructions travel as the
    first input item of its first turn on a resumed thread."""
    rpc = ResumingRpc()
    completed = {"id": TURN, "status": "completed", "items": []}
    for _ in range(2):
        rpc.incoming.put_nowait({"method": "turn/completed", "params": {"threadId": THREAD, "turn": completed}})
    session = CodexSession(
        engine=None,  # type: ignore[arg-type]
        sandbox=SimpleNamespace(id="sbx-1"),  # type: ignore[arg-type]
        process=None,  # type: ignore[arg-type]
        rpc=rpc,  # type: ignore[arg-type]
        reported_version="0.160.0",
        resume_thread="thr-0",
        secrets=frozenset(),
        log=HarnessLog(logging.getLogger("test.codex.session")),
    )
    instructions = "House style.\n\n<remembered>x</remembered>"
    _ = [e async for e in session.run_turn(EngineTask(turn_id="trn-2", prompt="say beta", instructions=instructions))]
    started = [p for m, p in rpc.params if m == "turn/start"]
    assert started[0]["input"] == [{"type": "text", "text": instructions}, {"type": "text", "text": "say beta"}]
    # The same instructions are not sent again in the next turn of this session.
    _ = [e async for e in session.run_turn(EngineTask(turn_id="trn-3", prompt="again", instructions=instructions))]
    started = [p for m, p in rpc.params if m == "turn/start"]
    assert started[1]["input"] == [{"type": "text", "text": "again"}]


class UnopenedRpc(FakeRpc):
    """Codex refuses to open the thread: its required tool gateway never answered (E2E3-F6)."""

    @override
    async def request(self, method: str, params: Mapping[str, JsonValue]) -> JsonValue:
        self.requests.append(method)
        if method in ("thread/start", "thread/resume"):
            raise CodexRpcError(method, -32603, "required MCP servers failed to initialize")
        return await super().request(method, params)


@pytest.mark.parametrize("resume", [None, THREAD])
async def test_e2e3_f6_a_thread_codex_cannot_open_is_an_engine_that_never_came_up(resume: str | None) -> None:
    rpc = UnopenedRpc(INVALID_REQUEST)
    session = CodexSession(
        engine=None,  # type: ignore[arg-type]
        sandbox=SimpleNamespace(id="sbx-1"),  # type: ignore[arg-type]
        process=None,  # type: ignore[arg-type]
        rpc=rpc,  # type: ignore[arg-type]
        reported_version="0.160.0",
        resume_thread=resume,
        secrets=frozenset(),
        log=HarnessLog(logging.getLogger("test.codex.session")),
    )
    with pytest.raises(EngineUnhealthy) as failed:
        _ = await _drive(rpc, session)
    assert isinstance(failed.value.__cause__, CodexRpcError)
    assert "turn/start" not in rpc.requests
