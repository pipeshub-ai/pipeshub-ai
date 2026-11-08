"""A timeline's dated searches share one permission walk (``app/services/graph_db/dated_scope.py``).

Each dated read used to walk the user's permissions live, once per search, and
a timeline runs a whole-span search and one per period. Inside a timeline the
graph now walks once for the span and every read filters that walk by its own
dates. Parity of the filter with each provider's queries is checked on real
backends in ``tests/integration/graph_db/test_dated_scope_real_backends.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db import dated_scope as ds
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.dated_scope import (
    DatedRow,
    shared_dated_scope,
    virtual_ids_in_range,
)
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

DAY = 86_400_000
SPAN = {"source_created_after_ms": 0, "source_created_before_ms": 100 * DAY}
PERIODS = [
    {"source_created_after_ms": 0, "source_created_before_ms": 33 * DAY, "dated_only": 1},
    {"source_created_after_ms": 33 * DAY, "source_created_before_ms": 66 * DAY, "dated_only": 1},
    {"source_created_after_ms": 66 * DAY, "source_created_before_ms": 100 * DAY, "dated_only": 1},
]
ROWS = [
    DatedRow("v1", "r1", created=10 * DAY),
    DatedRow("v2", "r2", created=50 * DAY),
    DatedRow("v3", "r3", created=90 * DAY),
    DatedRow("v4", "r4", created=None),
]


@pytest.fixture(params=[False, True], ids=["uploads-dated", "uploads-undated"])
def uploads_undated(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> bool:
    if request.param:
        monkeypatch.setenv("CONTENT_DOCUMENT_DATES", "true")
    else:
        monkeypatch.delenv("CONTENT_DOCUMENT_DATES", raising=False)
    return request.param


class TestTheRangeRules:
    """The providers' time conditions, applied to a row."""

    def test_after_and_before_bounds_are_inclusive(self, uploads_undated: bool) -> None:
        row = DatedRow("v", "r", created=10, updated=20)
        assert ds.row_in_time_range(row, {"source_created_after_ms": 10, "source_created_before_ms": 10})
        assert not ds.row_in_time_range(row, {"source_created_after_ms": 11})
        assert not ds.row_in_time_range(row, {"source_updated_before_ms": 19})

    def test_a_missing_or_non_numeric_date_matches_no_bound(self, uploads_undated: bool) -> None:
        for value in (None, "10", True):
            row = DatedRow("v", "r", created=value)
            assert not ds.row_in_time_range(row, {"source_created_after_ms": 0})
            assert not ds.row_in_time_range(row, {"source_created_before_ms": 99})
        assert ds.row_in_time_range(DatedRow("v", "r"), {})

    def test_zero_is_a_date_like_any_other(self, uploads_undated: bool) -> None:
        # The graph compares the stored number, so 0 is before any positive bound.
        row = DatedRow("v", "r", created=0)
        assert ds.row_in_time_range(row, {"source_created_before_ms": 5})
        assert not ds.row_in_time_range(row, {"source_created_after_ms": 5})

    def test_an_upload_passes_a_before_bound_only_while_uploads_are_undated(self, uploads_undated: bool) -> None:
        upload = DatedRow("v", "r", created=50, origin="upload")
        assert ds.row_in_time_range(upload, {"source_created_before_ms": 10}) is uploads_undated
        # An after-bound still rules it out, either way.
        assert not ds.row_in_time_range(upload, {"source_created_after_ms": 60})

    def test_a_dated_only_range_leaves_uploads_out_while_they_are_undated(self, uploads_undated: bool) -> None:
        upload = DatedRow("v", "r", created=5, origin="upload")
        assert ds.row_in_time_range(upload, {"source_created_before_ms": 10, "dated_only": 1}) is not uploads_undated
        assert ds.row_in_time_range(DatedRow("v", "r", created=5, origin="file"), {"dated_only": 1})

    def test_the_first_row_in_range_wins_for_a_virtual_id(self) -> None:
        # Two copies of one file, dated apart: each range keeps the copy it admits.
        rows = [DatedRow("v", "early", created=1), DatedRow("v", "late", created=9)]
        assert virtual_ids_in_range(rows, {"source_created_after_ms": 5}) == {"v": "late"}
        assert virtual_ids_in_range(rows, {"source_created_after_ms": 0}) == {"v": "early"}


class TestWhatTheSpanCovers:
    def test_a_range_within_the_span_is_covered(self) -> None:
        scope = ds.DatedScope(dict(SPAN))
        assert all(scope.covers(p) for p in PERIODS)
        assert scope.covers(SPAN)
        # A bound on the other date only narrows it further.
        assert scope.covers({**PERIODS[0], "source_updated_after_ms": 5})

    def test_a_range_reaching_past_the_span_is_not(self) -> None:
        scope = ds.DatedScope(dict(SPAN))
        assert not scope.covers({"source_created_after_ms": -1, "source_created_before_ms": DAY})
        assert not scope.covers({"source_created_after_ms": 0})  # no end: past the span's
        assert not scope.covers(None)
        assert not scope.covers({})

    def test_an_open_span_covers_any_dated_range(self) -> None:
        assert ds.DatedScope({}).covers({"source_updated_before_ms": 1})

    def test_a_dated_only_span_covers_only_dated_only_ranges(self) -> None:
        scope = ds.DatedScope({**SPAN, "dated_only": 1})
        assert scope.covers(PERIODS[0])
        assert not scope.covers({k: v for k, v in PERIODS[0].items() if k != "dated_only"})

    def test_no_scope_is_found_outside_the_block(self) -> None:
        assert ds.scope_for(PERIODS[0]) is None
        with shared_dated_scope(SPAN) as scope:
            assert ds.scope_for(PERIODS[0]) is scope
            assert ds.scope_for({"source_created_after_ms": -1}) is None
        assert ds.scope_for(PERIODS[0]) is None


class TestOneWalk:
    @pytest.mark.asyncio
    async def test_concurrent_reads_share_one_walk(self) -> None:
        scope = ds.DatedScope(dict(SPAN))
        calls = 0

        async def load() -> list[DatedRow]:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return ROWS

        results = await asyncio.gather(*(
            ds.read_within(scope, "k", load, p, raise_on_error=True, logger=logging.getLogger())
            for p in PERIODS
        ))
        assert calls == 1
        assert results == [{"v1": "r1"}, {"v2": "r2"}, {"v3": "r3"}]

    @pytest.mark.asyncio
    async def test_a_failed_walk_is_not_kept(self) -> None:
        scope = ds.DatedScope(dict(SPAN))
        load = AsyncMock(side_effect=[RuntimeError("graph down"), ROWS])
        with pytest.raises(RuntimeError):
            await ds.read_within(scope, "k", load, PERIODS[0], raise_on_error=True, logger=logging.getLogger())
        # Asked again, the graph is read again.
        assert await ds.read_within(
            scope, "k", load, PERIODS[0], raise_on_error=True, logger=logging.getLogger(),
        ) == {"v1": "r1"}

    @pytest.mark.asyncio
    async def test_a_failed_walk_reads_as_no_records_unless_asked_to_raise(self) -> None:
        scope = ds.DatedScope(dict(SPAN))
        load = AsyncMock(side_effect=RuntimeError("graph down"))
        assert await ds.read_within(
            scope, "k", load, PERIODS[0], raise_on_error=False, logger=logging.getLogger(),
        ) == {}


def _neo4j() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    provider.get_user_by_user_id = AsyncMock(return_value={"id": "u1"})  # type: ignore[method-assign]
    provider.get_gated_apps = AsyncMock(return_value=[{"id": "conn-1", "type": "jira"}])  # type: ignore[method-assign]
    provider._get_kb_virtual_ids = AsyncMock(return_value={})  # type: ignore[method-assign]
    return provider


def _arango() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
    provider.http_client = AsyncMock()
    provider.http_client.execute_aql = AsyncMock(return_value=[])  # no app is a KB
    provider._get_user_app_ids = AsyncMock(return_value=["conn-1"])  # type: ignore[method-assign]
    provider._get_kb_virtual_ids = AsyncMock(return_value={})  # type: ignore[method-assign]
    return provider


@pytest.mark.parametrize("make", [_neo4j, _arango], ids=["neo4j", "arango"])
class TestProvidersWalkOncePerTimeline:
    @pytest.mark.asyncio
    async def test_the_periods_of_a_timeline_walk_the_permissions_once(self, make) -> None:
        provider = make()
        provider._get_virtual_ids_for_connector = AsyncMock(return_value=ROWS)  # type: ignore[method-assign]

        with shared_dated_scope(SPAN):
            results = await asyncio.gather(*(
                provider.get_accessible_virtual_record_ids("user-1", "org-1", time_range=p) for p in PERIODS
            ))

        assert results == [{"v1": "r1"}, {"v2": "r2"}, {"v3": "r3"}]
        walk = provider._get_virtual_ids_for_connector
        assert walk.await_count == 1
        assert walk.await_args.kwargs["time_range"] == SPAN
        assert walk.await_args.kwargs["with_dates"] is True

    @pytest.mark.asyncio
    async def test_a_dated_read_outside_a_timeline_walks_as_before(self, make) -> None:
        provider = make()
        provider._get_virtual_ids_for_connector = AsyncMock(return_value={"v1": "r1"})  # type: ignore[method-assign]

        for p in PERIODS:
            await provider.get_accessible_virtual_record_ids("user-1", "org-1", time_range=p)

        walk = provider._get_virtual_ids_for_connector
        assert walk.await_count == len(PERIODS)
        assert [c.kwargs["time_range"] for c in walk.await_args_list] == PERIODS
        assert all("with_dates" not in c.kwargs for c in walk.await_args_list)

    @pytest.mark.asyncio
    async def test_a_range_the_span_does_not_cover_walks_live(self, make) -> None:
        provider = make()
        provider._get_virtual_ids_for_connector = AsyncMock(return_value={"v9": "r9"})  # type: ignore[method-assign]
        outside = {"source_created_after_ms": -DAY}

        with shared_dated_scope(SPAN):
            result = await provider.get_accessible_virtual_record_ids("user-1", "org-1", time_range=outside)

        assert result == {"v9": "r9"}
        assert provider._get_virtual_ids_for_connector.await_args.kwargs["time_range"] == outside


class TestTheWalkReturnsDates:
    @pytest.mark.asyncio
    async def test_neo4j_connector_walk_returns_each_row_with_its_dates(self) -> None:
        provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
        provider.client = AsyncMock()
        provider.client.execute_query = AsyncMock(return_value=[
            {"virtualId": "v", "recordId": "a", "created": 1, "updated": 2, "origin": None},
            {"virtualId": "v", "recordId": "b", "created": 3, "updated": 4, "origin": "upload"},
        ])
        rows = await provider._get_virtual_ids_for_connector("u", "o", "c", None, time_range=SPAN, with_dates=True)
        assert rows == [DatedRow("v", "a", 1, 2, None), DatedRow("v", "b", 3, 4, "upload")]
        query = provider.client.execute_query.await_args.args[0]
        assert "r.sourceCreatedAtTimestamp AS created" in query

    @pytest.mark.asyncio
    async def test_neo4j_kb_walk_returns_each_row_with_its_dates(self) -> None:
        provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
        provider.client = AsyncMock()
        provider.client.execute_query = AsyncMock(return_value=[
            {"virtualId": "v", "recordId": "a", "created": 1, "updated": 2, "origin": "upload"},
        ])
        rows = await provider._get_kb_virtual_ids("u", "o", None, None, time_range=SPAN, with_dates=True)
        assert rows == [DatedRow("v", "a", 1, 2, "upload")]
        query = provider.client.execute_query.await_args.args[0]
        assert query.count("created: r.sourceCreatedAtTimestamp") == 2
        assert "pair.created AS created" in query

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["_get_virtual_ids_for_connector", "_get_kb_virtual_ids"])
    async def test_arango_walks_return_every_pair_with_its_dates(self, method: str) -> None:
        provider = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
        provider.execute_query = AsyncMock(return_value=[  # type: ignore[method-assign]
            {"virtualRecordId": "v", "recordId": "a", "created": 1, "updated": 2, "origin": None},
            {"virtualRecordId": "v", "recordId": "b", "created": 3, "updated": 4, "origin": None},
        ])
        args = ("u", "o", "c", None) if method == "_get_virtual_ids_for_connector" else ("u", "o", None, None)
        rows = await getattr(provider, method)(*args, time_range=SPAN, with_dates=True)
        assert rows == [DatedRow("v", "a", 1, 2, None), DatedRow("v", "b", 3, 4, None)]
        query = provider.execute_query.await_args.args[0]
        # Every pair comes back; the first in range is picked after the range is applied.
        assert "COLLECT virtualRecordId" not in query
        assert "created: record.sourceCreatedAtTimestamp" in query

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["_get_virtual_ids_for_connector", "_get_kb_virtual_ids"])
    async def test_arango_live_reads_still_dedupe_in_the_query(self, method: str) -> None:
        provider = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
        provider.execute_query = AsyncMock(return_value=[{"virtualRecordId": "v", "recordId": "a"}])  # type: ignore[method-assign]
        args = ("u", "o", "c", None) if method == "_get_virtual_ids_for_connector" else ("u", "o", None, None)
        assert await getattr(provider, method)(*args, time_range=SPAN) == {"v": "a"}
        query = provider.execute_query.await_args.args[0]
        assert "COLLECT virtualRecordId = pair.virtualRecordId INTO groups" in query
        assert "created: record" not in query


class TestTheTimelineOpensTheScope:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("start", [10 * DAY, None], ids=["with-start", "without-start"])
    async def test_every_dated_search_of_a_timeline_reads_from_one_scope(self, start: int | None) -> None:
        from app.agents.actions.knowledge_graph.ops import timeline

        seen: list[ds.DatedScope | None] = []

        async def search(time_range: dict | None, limit: int) -> dict:
            seen.append(ds.scope_for(time_range) if time_range else ds._scope.get())
            return {"searchResults": [], "virtual_to_record_map": {}, "status_code": 200}

        end = 200 * DAY
        await timeline._collect(search, start, end, "created", logging.getLogger())

        assert len(seen) >= 3
        assert seen[0] is not None and all(s is seen[0] for s in seen)
        expected = {"source_created_before_ms": end}
        if start is not None:
            expected["source_created_after_ms"] = start
        assert seen[0].span == expected


@pytest.mark.asyncio
async def test_closing_the_scope_cancels_a_walk_nobody_waits_for() -> None:
    from app.services.graph_db.dated_scope import shared_dated_scope

    started = asyncio.Event()

    async def slow_walk() -> list:
        started.set()
        await asyncio.sleep(3600)
        return []

    with shared_dated_scope({"source_created_after_ms": 0}) as scope:
        waiter = asyncio.ensure_future(scope.rows(("connector", "u", "o", "c", ()), slow_walk))
        await started.wait()
        walk = next(iter(scope._reads.values()))
        waiter.cancel()  # the search gives up; the shielded walk keeps going
        with contextlib.suppress(asyncio.CancelledError):
            await waiter
        assert not walk.done()
    await asyncio.sleep(0)
    assert walk.cancelled()
    assert scope._reads == {}
