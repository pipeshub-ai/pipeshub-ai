"""LC2FIX-3: an engine's own compaction mid-turn reaches the journal as ``x.pipeshub.context.compacted`` with
``trigger`` ``auto`` and the engine's counts, journaled with the turn, so the chat shows "Context compacted" as it
does for ``/compact``; a Node that does not accept ``context_compacted_auto`` gets the turn without it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.harness.contracts.generated.pipeshub.events.x_pipeshub_context_compacted import (
    PipeshubContextCompactedEvent,
)
from harness_core.context.user_compact import CONTEXT_COMPACTED_EVENT
from harness_core.engines.events import ContextCompacted, EngineEventMeta, TurnCompleted
from harness_core.journal.types import CONTEXT_COMPACTED, TERMINAL_STATUSES
from harness_core.testing.engines.durable import EngineWorld, ScriptedEngineAdapter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

WRITERS = {CONTEXT_COMPACTED_EVENT: frozenset({"worker"})}
MODELS = {CONTEXT_COMPACTED_EVENT: PipeshubContextCompactedEvent}


class _Compacting:
    """The engine's session, compacting once just before its turn ends."""

    def __init__(self, inner: object) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def run_turn(self, task: object) -> AsyncIterator[object]:
        async for event in self._inner.run_turn(task):  # type: ignore[attr-defined]
            if isinstance(event, TurnCompleted):
                yield ContextCompacted(EngineEventMeta(event.meta.turn_id, 9_999), 260_000, 12_000)
            yield event


class _Adapter(ScriptedEngineAdapter):
    async def open(self, sandbox, launch, restore, *, correlation=None):  # type: ignore[no-untyped-def] # noqa: ANN202
        return _Compacting(await super().open(sandbox, launch, restore, correlation=correlation))


async def _run(**kwargs: object) -> EngineWorld:
    w = EngineWorld(extension_writers=WRITERS, extension_models=MODELS, adapter=_Adapter(), **kwargs)  # type: ignore[arg-type]
    w.admit("run-a", "say the answer")
    for _ in range(6):
        if w.journal.status("run-a") in TERMINAL_STATUSES:
            break
        _ = await w.attempt("run-a")
    assert w.journal.status("run-a") == "completed"
    return w


async def test_the_engines_own_compaction_is_journaled_for_the_chat() -> None:
    w = await _run()
    (data,) = [dict(e.data) for e in w.events("run-a") if e.type == CONTEXT_COMPACTED_EVENT]
    assert data == {
        "trigger": "auto", "executor": "jcode", "outcome": "compacted",
        "beforeTokens": 260_000, "afterTokens": 12_000, "userMessagesKept": False,
    }


async def test_a_node_without_the_auto_row_gets_the_turn_without_it() -> None:
    w = await _run(journal_accepts=frozenset({CONTEXT_COMPACTED}))
    assert [e for e in w.events("run-a") if e.type == CONTEXT_COMPACTED_EVENT] == []
