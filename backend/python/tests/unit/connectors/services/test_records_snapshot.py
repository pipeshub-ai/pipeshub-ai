"""The periodic record count: what it announces and what bounds its cost."""

import asyncio
import contextvars
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import platform_events
from app.connectors.services import records_snapshot
from app.connectors.services.records_snapshot import (
    EVENT,
    RecordsSnapshot,
    run_records_snapshot,
)
from app.core.org_context import peek
from tests.support.platform_event_capture import event_calls


class FakeGraph:
    """Orgs, their apps and each app's indexing-status counts; records every read."""

    def __init__(self, orgs: dict[str, dict[str, tuple[str, dict[str, int]]]], domains: dict[str, str] | None = None) -> None:
        self.orgs = orgs
        self.domains = domains or {}
        self.reads: list[tuple] = []
        self.contexts: list[tuple] = []
        self.failing: set[str] = set()

    def _seen(self, *read: str) -> None:
        self.reads.append(read)
        ctx = peek()
        self.contexts.append((read[0], ctx.kind if ctx else None, ctx.org_id if ctx else None))

    async def get_all_orgs(self) -> list[dict]:
        self._seen("orgs")
        return [{"id": org, "domain": self.domains.get(org)} for org in self.orgs]

    async def get_org_apps(self, org_id: str, *, raise_on_error: bool = False) -> list[dict]:
        self._seen("apps", org_id)
        if org_id in self.failing:
            raise RuntimeError("SENTINEL-graph-text")
        return [{"_key": app_id, "type": app_type} for app_id, (app_type, _counts) in self.orgs[org_id].items()]

    async def get_connector_stats(self, org_id: str, connector_id: str) -> dict:
        self._seen("stats", org_id, connector_id)
        if connector_id in self.failing:
            return {"success": False, "message": "no", "data": None}
        counts = self.orgs[org_id][connector_id][1]
        return {"success": True, "data": {"stats": {"total": sum(counts.values()), "indexingStatus": {"NOT_STARTED": 0, **counts}}}}


def _snapshot(graph: FakeGraph, **caps: int) -> RecordsSnapshot:
    return RecordsSnapshot(graph, logging.getLogger("test.records_snapshot"), **caps)


def _announced(mock: MagicMock) -> dict[str, dict]:
    return {call.kwargs["org_id"]: call.kwargs for call in mock.call_args_list}


@pytest.mark.asyncio
async def test_announces_each_orgs_records_by_connector_type_and_status() -> None:
    graph = FakeGraph(
        {
            "o1": {"a1": ("DRIVE", {"COMPLETED": 5, "FAILED": 1}), "a2": ("DRIVE", {"COMPLETED": 2}), "a3": ("KB", {"QUEUED": 4})},
            "o2": {"b1": ("SLACK", {"COMPLETED": 9})},
        },
        domains={"o1": "acme.io"},
    )
    with event_calls(EVENT) as taken:
        assert await _snapshot(graph).tick() == 2

    assert _announced(taken) == {
        "o1": {
            "org_id": "o1", "email_domain": "acme.io", "complete": True,
            "records_by_connector_status": {("DRIVE", "COMPLETED"): 7, ("DRIVE", "FAILED"): 1, ("KB", "QUEUED"): 4},
        },
        "o2": {"org_id": "o2", "email_domain": "unknown", "complete": True, "records_by_connector_status": {("SLACK", "COMPLETED"): 9}},
    }


@pytest.mark.asyncio
async def test_the_payload_names_no_record_and_no_connector_instance() -> None:
    graph = FakeGraph({"o1": {"app-instance-1": ("DRIVE", {"COMPLETED": 5})}})
    with event_calls(EVENT) as taken:
        await _snapshot(graph).tick()

    assert set(taken.call_args.kwargs) == {"org_id", "email_domain", "records_by_connector_status", "complete"}
    assert "app-instance-1" not in repr(taken.call_args.kwargs)


@pytest.mark.asyncio
async def test_reads_nothing_when_nobody_listens() -> None:
    graph = FakeGraph({"o1": {"a1": ("DRIVE", {"COMPLETED": 5})}})
    listeners = platform_events._listeners
    saved = {name: listeners.pop(name) for name in (EVENT, platform_events.WILDCARD) if name in listeners}
    try:
        assert await _snapshot(graph).tick() == 0
    finally:
        listeners.update(saved)

    assert graph.reads == []


@pytest.mark.asyncio
async def test_a_tick_covers_a_capped_number_of_orgs_and_the_next_continues_after_it() -> None:
    graph = FakeGraph({org: {f"{org}-app": ("DRIVE", {"COMPLETED": 1})} for org in ("o1", "o2", "o3", "o4", "o5")})
    snapshot = _snapshot(graph, max_orgs=2)
    covered = []
    with event_calls(EVENT, "org_id") as taken:
        for _ in range(4):
            before = len(graph.reads)
            await snapshot.tick()
            # one listing of orgs, then per org one listing of apps and one count per app
            assert len(graph.reads) - before <= 1 + 2 * 2
            covered.append([call.args[0] for call in taken.call_args_list])
            taken.reset_mock()

    assert covered == [["o1", "o2"], ["o3", "o4"], ["o5", "o1"], ["o2", "o3"]]


@pytest.mark.asyncio
async def test_an_org_with_too_many_connectors_is_counted_in_part_and_says_so() -> None:
    graph = FakeGraph({"o1": {f"a{i}": ("DRIVE", {"COMPLETED": 1}) for i in range(5)}})
    with event_calls(EVENT) as taken:
        await _snapshot(graph, max_connectors=3).tick()

    assert taken.call_args.kwargs["records_by_connector_status"] == {("DRIVE", "COMPLETED"): 3}
    assert taken.call_args.kwargs["complete"] is False
    assert len([read for read in graph.reads if read[0] == "stats"]) == 3


@pytest.mark.asyncio
async def test_a_connector_whose_count_failed_is_left_out_and_the_count_is_not_complete() -> None:
    graph = FakeGraph({"o1": {"a1": ("DRIVE", {"COMPLETED": 5}), "a2": ("SLACK", {"COMPLETED": 2})}})
    graph.failing.add("a2")
    with event_calls(EVENT) as taken:
        await _snapshot(graph).tick()

    assert taken.call_args.kwargs["records_by_connector_status"] == {("DRIVE", "COMPLETED"): 5}
    assert taken.call_args.kwargs["complete"] is False


@pytest.mark.asyncio
async def test_one_orgs_failure_is_a_text_free_warning_and_the_other_orgs_are_still_counted(caplog: pytest.LogCaptureFixture) -> None:
    graph = FakeGraph({"o1": {"a1": ("DRIVE", {"COMPLETED": 5})}, "o2": {"b1": ("SLACK", {"COMPLETED": 2})}})
    graph.failing.add("o1")
    with caplog.at_level(logging.DEBUG, logger="test.records_snapshot"), event_calls(EVENT, "org_id") as taken:
        assert await _snapshot(graph).tick() == 1

    taken.assert_called_once_with("o2")
    assert "records_snapshot.org_failed" in caplog.text
    assert "SENTINEL-graph-text" not in caplog.text


@pytest.mark.asyncio
async def test_orgs_are_listed_under_a_system_context_and_each_org_is_read_under_its_own() -> None:
    graph = FakeGraph({"o1": {"a1": ("DRIVE", {"COMPLETED": 5})}})

    async def tick() -> None:
        with event_calls(EVENT):
            await _snapshot(graph).tick()
        assert peek() is None

    await asyncio.create_task(tick(), context=contextvars.Context())

    assert graph.contexts == [("orgs", "system", None), ("apps", "job", "o1"), ("stats", "job", "o1")]


@pytest.mark.asyncio
async def test_the_loop_survives_a_failed_tick_and_sleeps_the_interval() -> None:
    snapshot = MagicMock()
    snapshot.tick = AsyncMock(side_effect=[RuntimeError("boom"), 1])
    sleeps = AsyncMock(side_effect=[None, asyncio.CancelledError])
    with patch("app.connectors.services.records_snapshot.asyncio.sleep", sleeps), pytest.raises(asyncio.CancelledError):
        await run_records_snapshot(snapshot, logging.getLogger("test.records_snapshot"), 900)

    assert snapshot.tick.await_count == 2
    assert [call.args for call in sleeps.await_args_list] == [(900,), (900,)]


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, 900), ("", 900), ("300", 300), ("0", 0), ("5", 900), ("-1", 900), ("soon", 900)],
)
def test_the_interval_defaults_to_fifteen_minutes_and_zero_turns_it_off(monkeypatch, value: str | None, expected: int) -> None:
    if value is None:
        monkeypatch.delenv("RECORDS_SNAPSHOT_INTERVAL_SECONDS", raising=False)
    else:
        monkeypatch.setenv("RECORDS_SNAPSHOT_INTERVAL_SECONDS", value)

    assert records_snapshot.interval_seconds() == expected


def test_the_caps_come_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("RECORDS_SNAPSHOT_MAX_ORGS_PER_TICK", "3")
    monkeypatch.setenv("RECORDS_SNAPSHOT_MAX_CONNECTORS_PER_ORG", "0")
    snapshot = _snapshot(FakeGraph({}))

    assert (snapshot._max_orgs, snapshot._max_connectors) == (3, records_snapshot.DEFAULT_MAX_CONNECTORS)
