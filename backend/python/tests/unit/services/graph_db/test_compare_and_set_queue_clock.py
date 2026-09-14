"""A swap into QUEUED stamps queuedAtTimestamp, on both graph backends.

It is the clock the stranded-record sweep ages rows on; the swap is the moment
a connector-published record's event is known to be on the broker.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

NOT_STARTED = ProgressStatus.NOT_STARTED.value
QUEUED = ProgressStatus.QUEUED.value
FAILED = ProgressStatus.FAILED.value


def _neo4j() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    provider.client.execute_query = AsyncMock(return_value=[{"id": "r1"}])
    return provider


def _arango() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
    provider.http_client = AsyncMock()
    provider.http_client.execute_aql = AsyncMock(return_value=["r1"])
    return provider


class TestNeo4jCompareAndSet:
    @pytest.mark.asyncio
    async def test_a_swap_into_queued_stamps_the_queue_clock(self) -> None:
        provider = _neo4j()
        before = get_epoch_timestamp_in_ms()

        assert await provider.compare_and_set_indexing_status(["r1"], NOT_STARTED, QUEUED) == ["r1"]

        call = provider.client.execute_query.await_args
        assert "n.queuedAtTimestamp = $now" in call.args[0]
        assert call.kwargs["parameters"]["now"] >= before

    @pytest.mark.asyncio
    async def test_other_swaps_leave_it_alone(self) -> None:
        provider = _neo4j()

        await provider.compare_and_set_indexing_status(["r1"], QUEUED, FAILED)

        assert "queuedAtTimestamp" not in provider.client.execute_query.await_args.args[0]


class TestArangoCompareAndSet:
    @pytest.mark.asyncio
    async def test_a_swap_into_queued_stamps_the_queue_clock(self) -> None:
        provider = _arango()
        before = get_epoch_timestamp_in_ms()

        assert await provider.compare_and_set_indexing_status(["r1"], NOT_STARTED, QUEUED) == ["r1"]

        query, bind_vars = provider.http_client.execute_aql.await_args.args[:2]
        assert "queuedAtTimestamp: @now" in query
        assert bind_vars["now"] >= before

    @pytest.mark.asyncio
    async def test_other_swaps_bind_no_unused_variable(self) -> None:
        """AQL rejects a query that declares a bind variable it never uses."""
        provider = _arango()

        await provider.compare_and_set_indexing_status(["r1"], QUEUED, FAILED)

        query, bind_vars = provider.http_client.execute_aql.await_args.args[:2]
        assert "@now" not in query
        assert "now" not in bind_vars
