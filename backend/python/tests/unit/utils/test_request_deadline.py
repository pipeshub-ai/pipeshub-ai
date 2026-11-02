"""A request's deadline, set by a route and read by the steps under it."""

import asyncio

import pytest

from app.utils.request_deadline import (
    deadline_after,
    search_deadline_seconds,
    seconds_left,
)


def test_no_deadline_unless_one_is_set() -> None:
    assert seconds_left() is None
    with deadline_after(None):
        assert seconds_left() is None


def test_a_deadline_counts_down_and_ends_with_its_block() -> None:
    with deadline_after(10):
        left = seconds_left()
        assert left is not None and 9 < left <= 10
    assert seconds_left() is None


def test_an_earlier_deadline_already_set_is_kept() -> None:
    with deadline_after(5):
        with deadline_after(60):
            left = seconds_left()
            assert left is not None and left <= 5
        with deadline_after(1):
            left = seconds_left()
            assert left is not None and left <= 1


@pytest.mark.asyncio
async def test_tasks_started_inside_share_it() -> None:
    async def read() -> float | None:
        return seconds_left()

    with deadline_after(10):
        lefts = await asyncio.gather(read(), read())
    assert all(left is not None and left <= 10 for left in lefts)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, 50.0), ("", 50.0), ("45", 45.0), ("2.5", 2.5), ("0", None), ("-3", None), ("soon", 50.0),
     ("nan", 50.0), ("inf", 50.0), ("-inf", 50.0)],
)
def test_the_search_deadline_setting(monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: float | None) -> None:
    if raw is None:
        monkeypatch.delenv("SEARCH_DEADLINE_SECONDS", raising=False)
    else:
        monkeypatch.setenv("SEARCH_DEADLINE_SECONDS", raw)
    assert search_deadline_seconds() == expected
