"""The final answer of a run carries ``searchDegraded`` when a search of that run was answered with less than the full search."""
import asyncio
import contextvars
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from app.agents.agent_loop.protocol.formatter import AGUIFormatter, LegacyFormatter
from app.modules.retrieval import degraded_notice


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(run_id="r", conversation_id="c")


def _in_fresh_context(fn: Callable[[], Any]) -> Any:  # noqa: ANN401
    """Each scenario runs in its own context, as each request does."""
    return contextvars.copy_context().run(fn)


def test_legacy_complete_event_is_flagged_only_after_a_degraded_search() -> None:
    def scenario() -> tuple[dict, dict]:
        degraded_notice.track()
        clean = LegacyFormatter().answer_final(_ctx(), completion_data={"answer": "a"})[0]["data"]
        degraded_notice.note()
        flagged = LegacyFormatter().answer_final(_ctx(), completion_data={"answer": "a"})[0]["data"]
        return clean, flagged

    clean, flagged = _in_fresh_context(scenario)
    assert "searchDegraded" not in clean
    assert flagged["searchDegraded"] is True


def test_agui_run_finished_result_is_flagged() -> None:
    def scenario() -> list:
        degraded_notice.track()
        degraded_notice.note()
        return AGUIFormatter().answer_final(_ctx(), completion_data={"answer": "a"})

    frames = " ".join(str(f) for f in _in_fresh_context(scenario))
    assert frames.count("searchDegraded") == 2          # in the state snapshot and in the RUN_FINISHED result


def test_nothing_is_tracked_outside_a_run() -> None:
    def scenario() -> bool:
        degraded_notice.note()
        return degraded_notice.was_degraded()

    assert _in_fresh_context(scenario) is False


def test_a_note_made_by_a_task_of_the_run_is_seen_by_the_run() -> None:
    async def run() -> bool:
        degraded_notice.track()

        async def child() -> None:
            degraded_notice.note()

        await asyncio.create_task(child())
        return degraded_notice.was_degraded()

    assert asyncio.run(run()) is True
