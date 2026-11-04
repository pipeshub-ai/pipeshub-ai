"""Named steps inside a stage, collected without passing a timer down."""

import asyncio

import pytest

from app.utils.stage_timer import StageTimer, collect_spans, format_spans, span, spanned


def test_a_span_outside_a_collector_costs_nothing() -> None:
    with span("vector"):
        pass  # nothing to add to, and nothing raised


def test_spans_add_up_by_name() -> None:
    with collect_spans() as spans:
        with span("embed"):
            pass
        with span("vector"):
            pass
        with span("vector"):
            pass
    assert list(spans) == ["embed", "vector"]
    assert len(spans["vector"]) == 2


@pytest.mark.asyncio
async def test_tasks_started_inside_add_to_the_same_collector() -> None:
    @spanned("access")
    async def check() -> int:
        await asyncio.sleep(0)
        return 1

    with collect_spans() as spans:
        assert await asyncio.gather(check(), check(), check()) == [1, 1, 1]
    assert len(spans["access"]) == 3


def test_off_collects_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPESHUB_CHAT_TIMING", "false")
    with collect_spans() as spans:
        with span("vector"):
            pass
    assert spans == {}


def test_format_shows_the_count_only_when_more_than_one() -> None:
    assert format_spans({}) is None
    assert format_spans({"embed": [12.4], "vector": [1000.0, 230.0]}) == "embed=12ms vector=1230ms/2"


def test_the_detail_follows_the_last_mark() -> None:
    lines: list[str] = []

    class _Log:
        def info(self, msg: str, *args: object) -> None:
            lines.append(msg % args)

    timer = StageTimer(enabled=True)
    timer.mark("query_transform")
    timer.mark("retrieval")
    timer.emit(_Log(), "search", detail="vector=1230ms/2", queries=1)
    assert lines and "retrieval=" in lines[0] and "(vector=1230ms/2) | queries=1" in lines[0]
