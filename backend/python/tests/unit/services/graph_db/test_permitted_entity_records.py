"""``get_permitted_entity_records`` on both providers (KG-11, KG-37, KG-38):
the permission check runs in the query over one window of the newest
candidates and stops at the limit.

The client is mocked, so these pin the query shape, binds and result
handling; ``tests/integration/graph_db/test_entity_candidates_real_backends.py``
runs the queries against real servers.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.common.utils import PermittedEntityRows
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

TOPIC_REF = {"id": "t1", "type": "topic", "connectorIds": ["c1"]}
RECORD_REF = {"id": "r1", "type": "record", "connectorIds": ["c1"]}
KWARGS = {"app_level_connector_ids": ["kb"], "limit_per_entity": 2, "offset": 20, "window": 180}


def _neo4j(rows: list | Exception) -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    p.client.execute_query = (
        AsyncMock(side_effect=rows) if isinstance(rows, Exception) else AsyncMock(return_value=rows)
    )
    return p


def _arango(rows: list | Exception) -> ArangoHTTPProvider:
    p = ArangoHTTPProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    p.http_client = AsyncMock()
    p.execute_query = (
        AsyncMock(side_effect=rows) if isinstance(rows, Exception) else AsyncMock(return_value=rows)
    )
    return p


def _call(p: Neo4jProvider | ArangoHTTPProvider) -> AsyncMock:
    return p.client.execute_query if isinstance(p, Neo4jProvider) else p.execute_query


class TestNeo4jQuery:
    async def test_window_is_sorted_then_walked_with_the_grants(self) -> None:
        p = _neo4j([])
        await p.get_permitted_entity_records([TOPIC_REF], "org1", "ukey", **KWARGS)
        query = p.client.execute_query.call_args.args[0]
        assert query.index("LIMIT $scan_cap") < query.index("ORDER BY") < query.index("SKIP $offset LIMIT $window")
        assert query.index("SKIP $offset LIMIT $window") < query.index("UNWIND range(0, size(win) - 1) AS pos")
        assert "rec.connectorId IN $app_level_connector_ids" in query
        assert "(a:Anyone)" not in query  # "anyone" shares grant no access (#3691)
        assert "permission_role IS NOT NULL" in query
        assert query.index("UNWIND range(0, size(win) - 1)") < query.index("LIMIT $limit")

    async def test_binds_and_server_timeout(self) -> None:
        p = _neo4j([])
        await p.get_permitted_entity_records([TOPIC_REF], "org1", "ukey", timeout_seconds=3.5, **KWARGS)
        kwargs = p.client.execute_query.call_args.kwargs
        params = kwargs["parameters"]
        assert (params["user_key"], params["offset"], params["window"], params["limit"]) == ("ukey", 20, 180, 2)
        assert params["app_level_connector_ids"] == ["kb"]
        assert kwargs["timeout"] == 3.5

    async def test_record_refs_only_have_a_first_window(self) -> None:
        p = _neo4j([])
        await p.get_permitted_entity_records([RECORD_REF], "org1", "ukey", **KWARGS)
        query = p.client.execute_query.call_args.args[0]
        assert "WHERE $offset = 0 AND" in query


class TestArangoQuery:
    async def test_window_is_sorted_then_walked_with_the_grants(self) -> None:
        p = _arango([])
        await p.get_permitted_entity_records([TOPIC_REF], "org1", "ukey", **KWARGS)
        query = p.execute_query.await_args.args[0]
        assert query.index("LIMIT @scan_cap") < query.index("SORT") < query.index("LIMIT @offset, @window")
        assert "FOR needed IN (app_granted ? [] : [1])" in query
        assert "FOR a IN anyone" not in query  # "anyone" shares grant no access (#3691)
        assert query.index("FILTER app_granted OR checked[0] == true") < query.index("LIMIT @limit")

    async def test_binds_and_server_timeout(self) -> None:
        p = _arango([])
        await p.get_permitted_entity_records([TOPIC_REF], "org1", "ukey", timeout_seconds=3.5, **KWARGS)
        call = p.execute_query.await_args
        binds = call.kwargs["bind_vars"]
        # The first chunk of the window; an empty answer ends the walk.
        assert (binds["user_key"], binds["offset"], binds["window"], binds["limit"]) == ("ukey", 20, 100, 2)
        assert 0 < call.kwargs["timeout_seconds"] <= 3.5
        assert p.execute_query.await_count == 1

    async def test_the_window_is_walked_in_chunks_until_the_limit(self) -> None:
        """AQL runs a subquery for every row of a block before a later LIMIT,
        so the window goes in chunks that stop once the limit is met."""
        p = _arango([])
        p.execute_query = AsyncMock(side_effect=[
            [{"id": "t1", "hits": [], "window_size": 100, "capped": False}],
            [{"id": "t1", "hits": [{"pos": 3, "row": {"_key": "a"}}, {"pos": 9, "row": {"_key": "b"}}],
              "window_size": 80, "capped": True}],
        ])
        out = await p.get_permitted_entity_records([TOPIC_REF], "org1", "ukey", **{**KWARGS, "window": 400})
        rows = out[("topic", "t1")]
        assert [r["_key"] for r in rows] == ["a", "b"]
        assert rows.examined == 110 and rows.capped is True
        offsets = [c.kwargs["bind_vars"]["offset"] for c in p.execute_query.await_args_list]
        assert offsets == [20, 120]

    async def test_short_chunk_ends_the_walk_with_the_true_window_size(self) -> None:
        p = _arango([])
        p.execute_query = AsyncMock(side_effect=[
            [{"id": "t1", "hits": [], "window_size": 100, "capped": False}],
            [{"id": "t1", "hits": [], "window_size": 30, "capped": False}],
        ])
        rows = (await p.get_permitted_entity_records([TOPIC_REF], "org1", "ukey", **{**KWARGS, "window": 400}))[("topic", "t1")]
        assert (rows.window_size, rows.examined) == (130, 130)
        assert p.execute_query.await_count == 2

    async def test_a_walk_past_its_deadline_raises_before_the_next_chunk(self) -> None:
        """Each chunk got at least 0.1 s, so a walk of many chunks could
        overrun the caller's deadline by that much per chunk; and partial
        rows would misstate how much of the window was examined."""
        from unittest.mock import patch

        p = _arango([])
        clock = {"now": 100.0}

        async def slow_chunk(*args: object, **kwargs: object) -> list:
            clock["now"] += 2.0  # each chunk takes 2 s against a 3 s budget
            return [{"id": "t1", "hits": [], "window_size": 100, "capped": False}]

        p.execute_query = AsyncMock(side_effect=slow_chunk)
        with patch("app.services.graph_db.arango.arango_http_provider.time.monotonic", side_effect=lambda: clock["now"]), \
                pytest.raises(TimeoutError):
            await p.get_permitted_entity_records(
                [TOPIC_REF], "org1", "ukey", timeout_seconds=3.0, **{**KWARGS, "window": 2000},
            )
        assert p.execute_query.await_count == 2

    async def test_record_refs_send_no_window_binds(self) -> None:
        p = _arango([])
        await p.get_permitted_entity_records([RECORD_REF], "org1", "ukey", **KWARGS)
        binds = p.execute_query.await_args.kwargs["bind_vars"]
        assert "window" not in binds and "scan_cap" not in binds


@pytest.mark.parametrize("make", [_neo4j, _arango], ids=["neo4j", "arango"])
class TestBoth:
    async def test_hits_come_back_in_window_order_with_the_next_offset(self, make) -> None:
        hits = [{"pos": 7, "row": {"_key": "b"}}, {"pos": 2, "row": {"_key": "a"}}]
        p = make([{"id": "t1", "hits": hits, "window_size": 180, "capped": True}])
        out = await p.get_permitted_entity_records([TOPIC_REF], "org1", "ukey", **KWARGS)
        rows = out[("topic", "t1")]
        assert isinstance(rows, PermittedEntityRows)
        assert [r["_key"] for r in rows] == ["a", "b"]
        assert (rows.examined, rows.window_size, rows.capped) == (8, 180, True)

    async def test_fewer_hits_than_the_limit_walked_the_whole_window(self, make) -> None:
        p = make([{"id": "t1", "hits": [{"pos": 0, "row": {"_key": "a"}}], "window_size": 50, "capped": False}])
        out = await p.get_permitted_entity_records([TOPIC_REF], "org1", "ukey", **KWARGS)
        assert out[("topic", "t1")].examined == 50

    async def test_missing_inputs_make_no_query(self, make) -> None:
        p = make([])
        assert await p.get_permitted_entity_records([], "org1", "ukey", **KWARGS) == {}
        assert await p.get_permitted_entity_records([TOPIC_REF], "", "ukey", **KWARGS) == {}
        assert await p.get_permitted_entity_records([TOPIC_REF], "org1", "", **KWARGS) == {}
        _call(p).assert_not_awaited()

    async def test_unanswered_ref_is_an_empty_window(self, make) -> None:
        p = make([])
        out = await p.get_permitted_entity_records([TOPIC_REF], "org1", "ukey", **KWARGS)
        assert out[("topic", "t1")] == [] and out[("topic", "t1")].window_size == 0

    async def test_failure_raises(self, make) -> None:
        with pytest.raises(RuntimeError):
            await make(RuntimeError("down")).get_permitted_entity_records([TOPIC_REF], "org1", "ukey", **KWARGS)


class TestPermittedEntityRows:
    def test_limit_reached_examines_up_to_the_last_hit(self) -> None:
        rows = PermittedEntityRows.from_window(
            [{"pos": 3, "row": {"_key": "a"}}, {"pos": 9, "row": {"_key": "b"}}],
            limit=2, window_size=100, capped=False,
        )
        assert rows.examined == 10

    def test_extra_hits_past_the_limit_are_dropped(self) -> None:
        rows = PermittedEntityRows.from_window(
            [{"pos": i, "row": {"_key": str(i)}} for i in range(5)], limit=2, window_size=100, capped=False,
        )
        assert [r["_key"] for r in rows] == ["0", "1"] and rows.examined == 2

    def test_a_zero_limit_returns_nothing_and_examines_nothing(self) -> None:
        rows = PermittedEntityRows.from_window(
            [{"pos": 0, "row": {"_key": "a"}}], limit=0, window_size=100, capped=False,
        )
        assert list(rows) == [] and rows.examined == 0 and rows.window_size == 100


class TestAnyoneSharesGrantNothing:
    """As in every access check since #3691: an entity is not reached through
    a record shared with "anyone", which neither backend's walk reads."""

    async def test_neither_walk_reads_anyone_shares(self) -> None:
        neo4j, arango = _neo4j([]), _arango([])
        await neo4j.get_permitted_entity_records([TOPIC_REF], "org1", "ukey", **KWARGS)
        await arango.get_permitted_entity_records([TOPIC_REF], "org1", "ukey", **KWARGS)
        assert "(a:Anyone)" not in neo4j.client.execute_query.call_args.args[0]
        assert all("FOR a IN anyone" not in c.args[0] for c in arango.execute_query.await_args_list)
