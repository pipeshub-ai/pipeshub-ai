"""Revision retention in the recovery pass: bounded per tick, cursor carried, stopped by a lost lease."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import indexing_main as m
from app.modules.artifacts.retention import RetentionReport
from tests.unit.test_indexing_main import (
    _FakeConcurrencyManager,
    _make_container,
    _make_graph_provider,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


@pytest.fixture(autouse=True)
def _cursor() -> Iterator[None]:
    m._retention_cursor = None
    yield
    m._retention_cursor = None


class _Pages:
    """Stands in for the sweep: ``pages`` maps a cursor to (next key, report) or an exception."""

    def __init__(self, pages: dict[str | None, Any]) -> None:
        self.pages = pages
        self.calls: list[str | None] = []
        self.deadlines: list[float | None] = []
        self.grace_ms: int | None = None
        self.leases: object = None

    def __call__(self, graph: object, store: object, logger: object, *, grace_ms: int, leases: object) -> _Pages:
        self.grace_ms, self.leases = grace_ms, leases
        return self

    async def run_page(
        self,
        after_key: str | None,
        *,
        still_leader: Callable[[], bool] | None = None,
        deadline: float | None = None,
    ) -> tuple[str | None, RetentionReport]:
        self.calls.append(after_key)
        self.deadlines.append(deadline)
        outcome = self.pages[after_key]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


async def _tick(pages: _Pages, lease_held: Callable[[], bool] = lambda: True) -> int:
    container = _make_container()
    with patch.object(m, "RevisionRetentionSweep", pages), patch.object(m, "LoopShare") as share:
        share.return_value.pause = AsyncMock()
        return await m._sweep_retired_revisions(
            app_container=container, graph_provider=MagicMock(), logger=MagicMock(), lease_held=lease_held,
        )


def _report(**kwargs: int) -> RetentionReport:
    return RetentionReport(**kwargs)


async def test_a_tick_walks_a_bounded_number_of_pages_and_carries_the_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(m, "RETENTION_MAX_PAGES_PER_TICK", 2)
    pages = _Pages({
        None: ("a", _report(revisions_deleted=1)),
        "a": ("b", _report(stage_states_deleted=2)),
        "b": (None, _report(revisions_deleted=4)),
    })

    assert await _tick(pages) == 3
    assert pages.calls == [None, "a"] and m._retention_cursor == "b"
    assert pages.grace_ms == 24 * 3600 * 1000

    assert await _tick(pages) == 4
    assert pages.calls[-1] == "b" and m._retention_cursor is None


async def test_a_lost_lease_stops_the_tick_before_the_next_page() -> None:
    pages = _Pages({None: ("a", _report()), "a": ("b", _report())})
    held = iter([True, False])

    await _tick(pages, lease_held=lambda: next(held))

    assert pages.calls == [None] and m._retention_cursor == "a"


async def test_an_interrupted_page_keeps_the_cursor_where_it_was() -> None:
    m._retention_cursor = "a"
    pages = _Pages({"a": ("a", _report(interrupted=True)), "b": ("c", _report())})

    await _tick(pages)

    assert pages.calls == ["a"] and m._retention_cursor == "a"


async def test_a_failed_page_keeps_the_cursor_and_ends_the_tick() -> None:
    m._retention_cursor = "a"
    pages = _Pages({"a": RuntimeError("graph down")})

    assert await _tick(pages) == 0
    assert m._retention_cursor == "a"


async def test_a_zero_grace_turns_it_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REVISION_RETENTION_GRACE_SECONDS", "0")
    pages = _Pages({})

    assert await _tick(pages) == 0
    assert pages.calls == []


async def test_the_grace_knob_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REVISION_RETENTION_GRACE_SECONDS", "7200")
    pages = _Pages({None: (None, _report())})

    await _tick(pages)

    assert pages.grace_ms == 7_200_000


async def test_recovery_runs_it_under_the_recovery_lease() -> None:
    container = _make_container()
    container.kafka_consumers[0][1].concurrency_manager = _FakeConcurrencyManager()
    held_during_the_sweep: list[bool] = []

    async def record_lease(**kwargs: Callable[[], bool]) -> int:
        held_during_the_sweep.append(kwargs["lease_held"]())
        return 0

    sweep = AsyncMock(side_effect=record_lease)

    with (
        patch.object(m, "_sweep_retired_revisions", sweep),
        patch.object(m, "_sweep_pipeline_stages", AsyncMock(return_value=0)),
    ):
        await m.recover_in_progress_records(container, _make_graph_provider())

    assert held_during_the_sweep == [True]
    # Once the pass has let the lease go, the sweep would see it as lost.
    assert sweep.await_args.kwargs["lease_held"]() is False
    # Record leases come from the same manager, so a re-index and a delete of its revision never overlap.
    assert sweep.await_args.kwargs["concurrency_manager"] is container.kafka_consumers[0][1].concurrency_manager


async def test_recovery_without_the_lease_does_not_run_it() -> None:
    container = _make_container()
    container.kafka_consumers[0][1].concurrency_manager = _FakeConcurrencyManager({"recovery": "another-replica"})
    sweep = AsyncMock(return_value=0)

    with patch.object(m, "_sweep_retired_revisions", sweep):
        await m.recover_in_progress_records(container, _make_graph_provider())

    sweep.assert_not_awaited()


async def test_a_failed_stage_sweep_does_not_skip_retention() -> None:
    """Each sweep of the pass stands alone: a graph error in the stage sweep must not
    leave old revisions unretired on every pass it keeps failing."""
    container = _make_container()
    container.kafka_consumers[0][1].concurrency_manager = _FakeConcurrencyManager()
    sweep = AsyncMock(return_value=0)

    with (
        patch.object(m, "_sweep_retired_revisions", sweep),
        patch.object(m, "_sweep_pipeline_stages", AsyncMock(side_effect=ConnectionError("graph down"))),
    ):
        await m.recover_in_progress_records(container, _make_graph_provider())

    sweep.assert_awaited_once()


async def test_a_page_cut_short_ends_the_tick_and_the_next_tick_resumes_at_its_key() -> None:
    pages = _Pages({
        None: ("a3", _report(revisions_deleted=1, cut_short=True)),
        "a3": ("b", _report(revisions_deleted=2)),
        "b": (None, _report()),
    })

    assert await _tick(pages) == 1
    assert pages.calls == [None] and m._retention_cursor == "a3"
    await _tick(pages)
    assert pages.calls[1] == "a3"


async def test_every_page_of_a_tick_shares_one_deadline_within_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REVISION_RETENTION_TICK_BUDGET_SECONDS", "7")
    pages = _Pages({None: ("a", _report()), "a": (None, _report())})
    started = m.time.monotonic()

    await _tick(pages)

    first, second = pages.deadlines
    assert first == second and first is not None
    assert started + 7 <= first <= m.time.monotonic() + 7


async def test_the_tick_starts_no_page_once_the_budget_is_spent(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = _Pages({None: ("a", _report()), "a": (None, _report())})
    clock = iter([0.0, 0.0, 100.0])
    monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: next(clock, 100.0)))

    await _tick(pages)

    assert pages.calls == [None] and m._retention_cursor == "a"


async def test_the_pause_between_pages_never_runs_past_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Less budget left than the longest pause: the tick ends instead of sleeping through its deadline."""
    monkeypatch.setenv("REVISION_RETENTION_TICK_BUDGET_SECONDS", "3")
    pages = _Pages({None: ("a", _report()), "a": (None, _report())})
    container = _make_container()

    with patch.object(m, "RevisionRetentionSweep", pages), patch.object(m, "LoopShare") as share:
        share.return_value.pause = AsyncMock()
        await m._sweep_retired_revisions(
            app_container=container, graph_provider=MagicMock(), logger=MagicMock(), lease_held=lambda: True,
        )

    assert pages.calls == [None] and m._retention_cursor == "a"
    share.return_value.pause.assert_not_awaited()
