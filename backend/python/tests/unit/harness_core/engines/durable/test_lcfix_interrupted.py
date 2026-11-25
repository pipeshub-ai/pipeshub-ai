"""LCFIX: a turn whose engine went away (its sandbox container killed) is never saved as the turn's end. Whatever the
adapter does with its dead stream, the controller commits no ``turn_end`` revision without a completed turn: the
attempt is released as a crash and the next one re-drives the turn from the revision before it, so a command the
turn ran is not applied twice (the opencode case of the LIFECYCLE gallery: ``start-t3`` twice in notes.md)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.engines.events import TurnCompleted
from harness_core.journal.types import TERMINAL_STATUSES
from harness_core.testing.engines.durable import (
    ORG,
    SESSION,
    EngineWorld,
    ScriptedEngineAdapter,
)
from harness_core.workspace.types import ObjectScope

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.engines.events import EngineEvent
    from harness_core.engines.types import EngineTask, FollowUp

RUN = "run-killed"


class _KilledAfterWork:
    """The engine did the turn's work in the sandbox, then its output ended before ``TurnCompleted``."""

    def __init__(self, inner: object) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def run_turn(self, task: EngineTask | FollowUp) -> AsyncIterator[EngineEvent]:
        async for event in self._inner.run_turn(task):  # type: ignore[attr-defined]
            if not isinstance(event, TurnCompleted):
                yield event


async def test_lcfix_a_turn_whose_engine_vanished_is_redriven_from_the_revision_before_it() -> None:
    w = EngineWorld()
    adapter = w.adapter
    assert isinstance(adapter, ScriptedEngineAdapter)
    opened = adapter.open

    async def open_once_killed(*args: object, **kwargs: object) -> object:
        session = await opened(*args, **kwargs)  # type: ignore[arg-type]
        return _KilledAfterWork(session) if len(adapter.restored) == 1 else session

    adapter.open = open_once_killed  # type: ignore[method-assign]
    w.admit(RUN, "write notes.md start-t3\nsay done")
    first = await w.attempt(RUN)
    assert (first.status, first.reason) == ("released", "engine_crashed")
    assert "workspace.committed" not in w.types(RUN), "the dead sandbox's files were saved as the turn's end"
    assert "notes.md" not in (await w.store.head(ObjectScope(ORG), f"ws:{SESSION}")).files
    for _ in range(4):
        if w.journal.status(RUN) in TERMINAL_STATUSES:
            break
        _ = await w.attempt(RUN)
    assert w.journal.status(RUN) == "completed"
    saved = [e.data["changed"] for e in w.events(RUN) if e.type == "workspace.committed"]
    assert saved == [["/notes.md"]]
    assert (await w.store.head(ObjectScope(ORG), f"ws:{SESSION}")).files["notes.md"] is not None
