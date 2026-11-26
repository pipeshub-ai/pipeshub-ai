"""A user's text reaches an engine as text, never as one of the engine's own commands (SLASH0).

Claude Code 2.1.294 dispatches a turn or a steer whose text starts with ``/``: ``/compact`` summarises the
session, ``/clear`` empties it, ``/model`` and ``/config`` change what the run was admitted with, and
``/<skill>`` expands a mounted skill. A word joiner (U+2060) in front makes the first character something no
engine reads as a command and no ``trim`` removes; the model still reads the slash text. Every other text is
handed over unchanged, the same object.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from harness_core.engines.native import RecordsNative
from harness_core.engines.types import EngineTask, FollowUp

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.context.user_compact import CompactReport
    from harness_core.engines.events import EngineEvent, EngineState
    from harness_core.engines.native import NativeTape
    from harness_core.engines.ports import EngineSession
    from harness_core.engines.types import SessionHealth
    from harness_core.interactions.ports import InteractionDecision

WORD_JOINER = "⁠"


def literal_text(text: str) -> str:
    return WORD_JOINER + text if text.lstrip().startswith("/") else text


def literal_task(task: EngineTask | FollowUp) -> EngineTask | FollowUp:
    if isinstance(task, FollowUp):
        text = literal_text(task.text)
        return task if text is task.text else dataclasses.replace(task, text=text)
    prompt = literal_text(task.prompt)
    return task if prompt is task.prompt else dataclasses.replace(task, prompt=prompt)


class LiteralTextSession:
    """The session the controller hands every turn and steer to, whatever the adapter."""

    def __init__(self, inner: EngineSession) -> None:
        self._inner = inner

    def run_turn(self, task: EngineTask | FollowUp) -> AsyncIterator[EngineEvent]:
        return self._inner.run_turn(literal_task(task))

    async def steer(self, text: str) -> None:
        await self._inner.steer(literal_text(text))

    async def interrupt(self) -> None:
        await self._inner.interrupt()

    async def compact(self, focus: str | None, *, instructions: str = "") -> CompactReport:
        # The adapter's own method, reached only from the controller's compact step: the focus is data it places,
        # never a turn's text, so nothing here can start an engine command from what a user typed.
        return await self._inner.compact(focus, instructions=instructions)

    async def answer(self, request_id: str, decision: InteractionDecision) -> None:
        await self._inner.answer(request_id, decision)

    async def capture_state(self) -> EngineState:
        return await self._inner.capture_state()

    async def health(self) -> SessionHealth:
        return await self._inner.health()

    async def close(self) -> None:
        await self._inner.close()

    def tap_native(self, tape: NativeTape) -> None:
        """The adapter's native transcript (C2.39), when it records one; a session that cannot leaves the tape empty."""
        if isinstance(self._inner, RecordsNative):
            self._inner.tap_native(tape)


def literal_session(session: EngineSession) -> EngineSession:
    return session if isinstance(session, LiteralTextSession) else LiteralTextSession(session)


__all__ = ["WORD_JOINER", "LiteralTextSession", "literal_session", "literal_task", "literal_text"]
