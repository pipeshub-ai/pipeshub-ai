"""The memory of which records have lost a parse worker."""
from __future__ import annotations

from app.modules.parsers.worker_deaths import WorkerDeaths


def test_only_the_first_death_of_a_record_is_first() -> None:
    deaths = WorkerDeaths()

    assert deaths.is_first("a", now=0) is True
    assert deaths.is_first("a", now=1) is False
    assert deaths.is_first("b", now=2) is True


def test_a_death_with_no_record_is_never_first() -> None:
    deaths = WorkerDeaths()

    assert deaths.is_first(None) is False
    assert deaths.is_first("") is False


def test_a_death_outside_the_window_is_a_new_first() -> None:
    deaths = WorkerDeaths(window=100)

    assert deaths.is_first("a", now=0) is True
    assert deaths.is_first("a", now=101) is True


def test_the_memory_is_bounded() -> None:
    deaths = WorkerDeaths(capacity=2)
    for index, record in enumerate("abc"):
        deaths.is_first(record, now=index)

    assert deaths.is_first("a", now=3) is True  # forgotten
    assert deaths.is_first("c", now=4) is False
