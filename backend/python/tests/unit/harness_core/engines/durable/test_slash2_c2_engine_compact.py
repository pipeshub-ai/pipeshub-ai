"""SLASH2 C2 ``/compact [focus]`` on an engine run: the run that takes the request has its engine compact the
session it carried from the person's earlier run before the first turn (``EngineSession.compact``, never a turn of
the person's text), and journals one ``x.pipeshub.context.compacted`` with the turn. A run with no carried state
has nothing to compact; an engine that cannot compact says so and the turn runs on the full context; a re-driven
first turn compacts the uncompacted state again and records it once; the pin is the one admitted. A user who
types ``/compact`` sends text (S0)."""

from __future__ import annotations

from app.harness.contracts.generated.pipeshub.events.x_pipeshub_context_compacted import (
    PipeshubContextCompactedEvent,
)
from harness_core.context.user_compact import CONTEXT_COMPACTED_EVENT
from harness_core.engines.literal import WORD_JOINER
from harness_core.journal.types import TERMINAL_STATUSES, CompactAsk
from harness_core.testing.engines.durable import EngineWorld, ScriptedEngineAdapter
from harness_core.testing.engines.fake_engine import SUMMARY_PREFIX

WRITERS = {CONTEXT_COMPACTED_EVENT: frozenset({"worker"})}
MODELS = {CONTEXT_COMPACTED_EVENT: PipeshubContextCompactedEvent}


def _world(**kwargs: object) -> EngineWorld:
    return EngineWorld(extension_writers=WRITERS, extension_models=MODELS, **kwargs)  # type: ignore[arg-type]


async def _finish(w: EngineWorld, run_id: str) -> str:
    for _ in range(6):
        if w.journal.status(run_id) in TERMINAL_STATUSES:
            break
        if w.journal.status(run_id) == "running":
            w.expire()
        _ = await w.attempt(run_id)
    return str(w.journal.status(run_id))


def _recorded(w: EngineWorld, run_id: str) -> list[dict[str, object]]:
    return [dict(e.data) for e in w.events(run_id) if e.type == CONTEXT_COMPACTED_EVENT]


def _users(w: EngineWorld, call: int = -1) -> list[str]:
    return [m.content for m in w.gateway.model_calls[call].messages if m.role == "user"]


async def _earlier_run(w: EngineWorld) -> None:
    w.admit("run-a", "say first answer")
    assert await _finish(w, "run-a") == "completed"


async def test_the_engine_compacts_the_carried_session_before_the_first_turn() -> None:
    w = _world()
    await _earlier_run(w)
    w.admit("run-b", "say second answer", compact=CompactAsk("keep the numbers"))
    assert await _finish(w, "run-b") == "completed"
    (data,) = _recorded(w, "run-b")
    assert (data["trigger"], data["executor"], data["outcome"], data["focus"]) == (
        "user", "jcode", "compacted", "keep the numbers",
    )
    assert isinstance(data["beforeTokens"], int) and isinstance(data["afterTokens"], int)
    users = _users(w)
    assert any(text.startswith(SUMMARY_PREFIX) for text in users), users
    assert not any("say first answer" in text for text in users), "the earlier turn is in the summary only"
    assert any("say second answer" in text for text in users)
    summarising = w.gateway.model_calls[-2]
    assert not any(m.content.lstrip().startswith("/compact") for m in summarising.messages if m.role == "user")
    types = [e.type for e in w.events("run-b")]
    assert types.index(CONTEXT_COMPACTED_EVENT) < types.index("model.response")


async def test_the_pin_is_the_one_the_run_was_admitted_with() -> None:
    w = _world()
    await _earlier_run(w)
    w.admit("run-b", "say second answer", compact=CompactAsk())
    assert await _finish(w, "run-b") == "completed"
    pins = [e for e in w.events("run-b") if e.type == "run.spec_pinned"]
    earlier = [e for e in w.events("run-a") if e.type == "run.spec_pinned"]
    assert len(pins) == 1
    assert pins[0].data["spec"]["executor"] == earlier[0].data["spec"]["executor"]
    assert pins[0].data["spec"]["model"] == earlier[0].data["spec"]["model"]


async def test_a_first_run_has_nothing_to_compact() -> None:
    w = _world()
    w.admit("run-b", "say only answer", compact=CompactAsk())
    assert await _finish(w, "run-b") == "completed"
    (data,) = _recorded(w, "run-b")
    assert (data["outcome"], data["beforeTokens"], data["afterTokens"]) == ("nothing_to_compact", None, None)
    assert not any(text.startswith(SUMMARY_PREFIX) for text in _users(w))


async def test_an_engine_that_cannot_compact_runs_the_turn_on_the_full_context() -> None:
    w = _world(adapter=ScriptedEngineAdapter(can_compact=False))
    await _earlier_run(w)
    calls = len(w.gateway.model_calls)
    w.admit("run-b", "say second answer", compact=CompactAsk())
    assert await _finish(w, "run-b") == "completed"
    (data,) = _recorded(w, "run-b")
    assert data["outcome"] == "unsupported"
    assert len(w.gateway.model_calls) == calls + 1, "no summarising call"
    assert any("say first answer" in text for text in _users(w))


async def test_a_redriven_first_turn_compacts_again_and_records_once() -> None:
    w = _world()
    await _earlier_run(w)
    w.admit("run-b", "say second answer", compact=CompactAsk())
    assert await w.crash_at("run-b", "engine.kernel.before_turn_commit")
    assert _recorded(w, "run-b") == []
    assert await _finish(w, "run-b") == "completed"
    (data,) = _recorded(w, "run-b")
    assert data["outcome"] == "compacted"
    assert not any("say first answer" in text for text in _users(w))


async def test_an_older_journal_gets_the_compaction_without_the_event() -> None:
    w = _world(journal_accepts=frozenset())
    await _earlier_run(w)
    w.admit("run-b", "say second answer", compact=CompactAsk())
    assert await _finish(w, "run-b") == "completed"
    assert _recorded(w, "run-b") == []
    assert any(text.startswith(SUMMARY_PREFIX) for text in _users(w))


async def test_a_typed_slash_compact_is_text_the_model_reads() -> None:
    w = _world()
    await _earlier_run(w)
    calls = len(w.gateway.model_calls)
    w.admit("run-b", "/compact\nsay two")
    assert await _finish(w, "run-b") == "completed"
    assert _recorded(w, "run-b") == []
    assert len(w.gateway.model_calls) == calls + 1, "no summarising call"
    users = _users(w)
    assert any(text.startswith(WORD_JOINER + "/compact") for text in users), users
    assert any("say first answer" in text for text in users)
