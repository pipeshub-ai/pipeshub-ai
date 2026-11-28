"""C20.53 / G-52 (WP100-LANG-1..4): an engine run answers in the person's language.

The host gives the run's answer-language line (``AssembledEngineRun.answer_language``, from the run's pinned locale);
the kernel puts it at the tail of the run's first task, after the workspace note, so the engine's instructions (what a
session opens with and keeps) never change with it. It is built at each attempt, never journaled with the task, so
a re-driven turn says it once.
"""

from __future__ import annotations

from harness_core.journal.types import TERMINAL_STATUSES
from harness_core.testing.engines.durable import EngineWorld

LINE = "## Answer Language\n\nThis person's PipesHub is set to German (de-DE). Write your answer in German."


async def _finish(w: EngineWorld, run_id: str) -> str:
    for _ in range(6):
        if w.journal.status(run_id) in TERMINAL_STATUSES:
            break
        if w.journal.status(run_id) == "running":
            w.expire()
        _ = await w.attempt(run_id)
    return str(w.journal.status(run_id))


def _first_task(w: EngineWorld) -> str:
    return next(m.content for m in w.gateway.model_calls[-1].messages if m.role == "user")


async def test_wp100_lang_1_the_first_task_ends_with_the_language_line_after_the_workspace_note() -> None:
    w = EngineWorld(answer_language=LINE)
    w.admit("run-a", "say hallo")

    assert await _finish(w, "run-a") == "completed"
    task = _first_task(w)
    assert task.startswith("say hallo")
    assert task.endswith(LINE)
    assert task.index("About your workspace") < task.index(LINE)
    assert task.count("## Answer Language") == 1


async def test_wp100_lang_2_the_engines_instructions_do_not_carry_it() -> None:
    w = EngineWorld(answer_language=LINE)
    w.admit("run-a", "say hallo")

    assert await _finish(w, "run-a") == "completed"
    assert "Answer Language" not in w.gateway.model_calls[-1].system


async def test_wp100_lang_3_a_redriven_first_turn_says_it_once() -> None:
    w = EngineWorld(answer_language=LINE)
    w.admit("run-a", "say hallo")
    assert await w.crash_at("run-a", "engine.turn.after_restore")

    assert await _finish(w, "run-a") == "completed"
    assert _first_task(w).count("## Answer Language") == 1


async def test_wp100_lang_4_without_a_locale_the_task_is_unchanged() -> None:
    plain, worded = EngineWorld(), EngineWorld(answer_language=LINE)
    for w in (plain, worded):
        w.admit("run-a", "say hallo")
        assert await _finish(w, "run-a") == "completed"

    assert "Answer Language" not in _first_task(plain)
    assert _first_task(worded) == f"{_first_task(plain)}\n\n{LINE}"
