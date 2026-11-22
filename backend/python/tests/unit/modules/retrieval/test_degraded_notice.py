"""Each chat run has its own partial-search scope: one answer's partial search never marks the next answer of the same task."""
import asyncio
from collections.abc import AsyncGenerator

import pytest

from app.agents.agent_loop import stream_bridge
from app.agents.chat_modes import bridge
from app.modules.retrieval import degraded_notice

pytestmark = pytest.mark.asyncio


@degraded_notice.scoped
async def _run(partial: bool) -> AsyncGenerator[dict, None]:
    if partial:
        # a search made by a task the run spawned reports into the run's scope
        await asyncio.create_task(asyncio.sleep(0, result=degraded_notice.note("timeout")))
    yield degraded_notice.flag({"answer": "x"})


async def _answer(partial: bool) -> dict:
    events = [event async for event in _run(partial)]
    return events[0]


async def test_a_clean_run_after_a_partial_one_in_the_same_task_has_no_flag() -> None:
    assert (await _answer(True)).get("searchDegraded") is True
    assert "searchDegraded" not in await _answer(False)


async def test_a_partial_run_after_a_clean_one_is_flagged() -> None:
    assert "searchDegraded" not in await _answer(False)
    assert (await _answer(True)).get("searchDegraded") is True


async def test_the_scope_ends_with_the_stream() -> None:
    await _answer(True)
    assert degraded_notice.was_degraded() is False


async def test_both_chat_entry_points_start_their_own_scope() -> None:
    assert hasattr(stream_bridge.run_agent_loop_stream, "__wrapped__")
    assert hasattr(bridge.run_chat_stream, "__wrapped__")


async def test_closing_the_wrapped_stream_closes_the_run_it_wraps_at_once() -> None:
    ended: list[str] = []

    @degraded_notice.scoped
    async def run() -> AsyncGenerator[int, None]:
        try:
            yield 1
            await asyncio.sleep(60)
        finally:
            ended.append("teardown")

    stream = run()
    await stream.__anext__()
    await stream.aclose()

    assert ended == ["teardown"]
    assert degraded_notice.was_degraded() is False
