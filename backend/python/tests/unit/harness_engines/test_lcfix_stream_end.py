"""LCFIX: every adapter reports an engine whose output ended mid-turn (its container was killed) as a crash, never as
a completed turn, so the controller never saves the dead sandbox's files as the turn's end (opencode's own case is
``opencode/test_driver.py::test_lg2_a_dead_event_stream_is_a_crash_and_is_logged``)."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.errors import EngineCrashed
from harness_core.engines.jsonlines import StreamEnd
from harness_core.engines.types import EngineTask
from harness_core.logs import HarnessLog
from harness_engines.claude_code.session import ClaudeSession
from harness_engines.claude_code.stream import ClaudeStreamEnded
from harness_engines.codex.rpc import CodexStreamEnded
from harness_engines.codex.session import CodexSession

if TYPE_CHECKING:
    from harness_core.engines.events import EngineEvent
    from harness_core.engines.types import JsonValue

TASK = EngineTask(turn_id="trn-1", prompt="shell sleep 40", instructions="")
LOG = HarnessLog(logging.getLogger("test.lcfix.stream_end"))


class _Channel:
    """The engine's output: it answers what the session asks, then ends as a killed process's does."""

    def __init__(self) -> None:
        self.incoming: asyncio.Queue[object] = asyncio.Queue()
        self.incoming.put_nowait(StreamEnd(ConnectionResetError("container killed")))

    async def request(self, method: str, params: object) -> JsonValue:
        return {"thread": {"id": "thr-1"}} if method == "thread/start" else {"turn": {"id": "turn-1"}}

    async def control(self, subtype: str, body: object) -> JsonValue:
        return {}

    async def send(self, message: object) -> None:
        return None


async def _events(session: CodexSession | ClaudeSession) -> list[EngineEvent]:
    return [event async for event in session.run_turn(TASK)]


async def test_lcfix_codex_whose_app_server_output_ends_mid_turn_is_a_crash() -> None:
    session = CodexSession(
        engine=None,  # type: ignore[arg-type]
        sandbox=SimpleNamespace(id="sbx-1"),  # type: ignore[arg-type]
        process=None,  # type: ignore[arg-type]
        rpc=_Channel(),  # type: ignore[arg-type]
        reported_version="0.160.0",
        resume_thread=None,
        secrets=frozenset(),
        log=LOG,
    )
    with pytest.raises(CodexStreamEnded) as gone:
        _ = await _events(session)
    assert isinstance(gone.value, EngineCrashed) and isinstance(gone.value.__cause__, ConnectionResetError)


async def test_lcfix_claude_code_whose_output_ends_mid_turn_is_a_crash() -> None:
    session = ClaudeSession(
        engine=None,  # type: ignore[arg-type]
        sandbox=SimpleNamespace(id="sbx-1"),  # type: ignore[arg-type]
        process=None,  # type: ignore[arg-type]
        stream=_Channel(),  # type: ignore[arg-type]
        reported_version="2.1.294",
        resume_session=None,
        secrets=frozenset(),
        log=LOG,
    )
    with pytest.raises(ClaudeStreamEnded) as gone:
        _ = await _events(session)
    assert isinstance(gone.value, EngineCrashed) and isinstance(gone.value.__cause__, ConnectionResetError)
