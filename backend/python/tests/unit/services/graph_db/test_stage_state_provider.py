"""Stage-state provider methods (UNIT-ST-01..05): one statement per operation, a lost race
is False, an outage raises, and the stored shape matches the pipeline model."""

import logging
from unittest.mock import AsyncMock

import pytest

from app.modules.pipeline.models import Priority, StageState
from app.schema.arango.documents import (
    STAGE_PRIORITIES,
    STAGE_TIERS,
    stage_state_schema,
)
from app.services.graph_db.arango.arango_http_client import (
    ARANGO_CONFLICT,
    ARANGO_UNIQUE_CONSTRAINT_VIOLATED,
    ArangoQueryError,
)
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.resource_governor.models import ParseTier

_logger = logging.getLogger("stage-state-provider")


def _conflict(error_num: int) -> ArangoQueryError:
    return ArangoQueryError("Query failed", 409, f'{{"error":true,"errorNum":{error_num},"code":409}}')


def _arango(rows: object = None, side_effect: object = None) -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(_logger, AsyncMock())
    provider.http_client = AsyncMock()
    provider.http_client.execute_aql = AsyncMock(return_value=rows if rows is not None else [], side_effect=side_effect)
    return provider


def _neo4j(rows: object = None) -> Neo4jProvider:
    provider = Neo4jProvider(_logger, AsyncMock())
    provider.client = AsyncMock()
    provider.client.execute_query = AsyncMock(return_value=rows if rows is not None else [])
    return provider


def _query(mock: AsyncMock) -> str:
    return " ".join(mock.await_args.args[0].split())


class TestArangoQueryError:
    def test_keeps_the_message_and_parses_the_error_number(self) -> None:
        error = _conflict(ARANGO_CONFLICT)
        assert str(error).startswith("Query failed (status=409): ")
        assert error.status == 409 and error.error_num == ARANGO_CONFLICT

    def test_a_non_json_body_has_no_error_number(self) -> None:
        assert ArangoQueryError("Query failed", 500, "gateway timeout").error_num is None


class TestArangoStageStates:
    @pytest.mark.asyncio
    async def test_create_inserts_under_the_key_and_reports_success(self) -> None:
        provider = _arango(["vr:rev:classify"])
        assert await provider.stage_state_create({"id": "vr:rev:classify", "status": "QUEUED"})
        bind = provider.http_client.execute_aql.await_args.args[1]
        assert bind["doc"] == {"_key": "vr:rev:classify", "status": "QUEUED"}
        assert bind["@collection"] == "stageStates"

    @pytest.mark.parametrize("error_num", [ARANGO_UNIQUE_CONSTRAINT_VIOLATED, ARANGO_CONFLICT])
    @pytest.mark.asyncio
    async def test_create_on_an_existing_key_is_false(self, error_num: int) -> None:
        provider = _arango(side_effect=_conflict(error_num))
        assert await provider.stage_state_create({"id": "k"}) is False

    @pytest.mark.asyncio
    async def test_create_raises_on_an_outage(self) -> None:
        provider = _arango(side_effect=ArangoQueryError("Query failed", 503, "unavailable"))
        with pytest.raises(ArangoQueryError):
            await provider.stage_state_create({"id": "k"})

    @pytest.mark.asyncio
    async def test_cas_is_one_filtered_update(self) -> None:
        provider = _arango(["k"])
        assert await provider.stage_state_compare_and_set("k", "QUEUED", "IN_PROGRESS", {"attempt": 1})
        query = _query(provider.http_client.execute_aql)
        assert "FILTER doc._key == @key AND doc.status == @expected" in query
        assert "UPDATE doc WITH MERGE(@fields, { status: @new })" in query

    @pytest.mark.asyncio
    async def test_cas_that_matched_nothing_is_false(self) -> None:
        assert await _arango([]).stage_state_compare_and_set("k", "QUEUED", "IN_PROGRESS", {}) is False

    @pytest.mark.asyncio
    async def test_cas_that_lost_a_write_write_conflict_is_false(self) -> None:
        provider = _arango(side_effect=_conflict(ARANGO_CONFLICT))
        assert await provider.stage_state_compare_and_set("k", "QUEUED", "IN_PROGRESS", {}) is False

    @pytest.mark.asyncio
    async def test_reads_map_the_key_to_id(self) -> None:
        provider = _arango([{"_key": "k", "_id": "stageStates/k", "_rev": "1", "status": "COMPLETED"}])
        assert await provider.stage_state_get("k") == {"id": "k", "status": "COMPLETED"}

    @pytest.mark.asyncio
    async def test_stale_scan_is_ordered_and_bounded(self) -> None:
        provider = _arango([])
        await provider.stage_states_stale(["QUEUED"], 1000, 50)
        query = _query(provider.http_client.execute_aql)
        assert "SORT doc.updatedAtMs ASC LIMIT @limit" in query

    @pytest.mark.asyncio
    async def test_record_fields_are_written_only_on_the_matching_revision(self) -> None:
        provider = _arango(["rec-1"])
        updated = await provider.compare_and_set_record_fields(["rec-1", "rec-1", ""], "rev-a", {"extractionStatus": "COMPLETED"})
        assert updated == ["rec-1"]
        query = _query(provider.http_client.execute_aql)
        assert "doc.contentRev == @rev" in query
        assert provider.http_client.execute_aql.await_args.args[1]["keys"] == ["rec-1"]

    @pytest.mark.asyncio
    async def test_no_record_ids_is_no_query(self) -> None:
        provider = _arango()
        assert await provider.compare_and_set_record_fields([], "rev-a", {}) == []
        provider.http_client.execute_aql.assert_not_awaited()


class TestNeo4jStageStates:
    @pytest.mark.asyncio
    async def test_create_merges_on_the_unique_id_and_reports_whether_it_created(self) -> None:
        provider = _neo4j([{"created": True}])
        assert await provider.stage_state_create({"id": "k", "status": "QUEUED"})
        query = _query(provider.client.execute_query)
        assert "MERGE (n:StageState {id: $id}) ON CREATE SET n += $props" in query
        provider.client.execute_query = AsyncMock(return_value=[{"created": False}])
        assert await provider.stage_state_create({"id": "k"}) is False

    @pytest.mark.asyncio
    async def test_cas_takes_the_write_lock_before_reading_status(self) -> None:
        provider = _neo4j([{"id": "k"}])
        assert await provider.stage_state_compare_and_set("k", "QUEUED", "IN_PROGRESS", {"attempt": 1})
        query = _query(provider.client.execute_query)
        assert query.index("SET n.__cas = true") < query.index("WHERE n.status = $expected")
        assert "REMOVE n.__cas" in query

    @pytest.mark.asyncio
    async def test_cas_that_matched_nothing_is_false(self) -> None:
        assert await _neo4j([]).stage_state_compare_and_set("k", "QUEUED", "IN_PROGRESS", {}) is False

    @pytest.mark.asyncio
    async def test_record_fields_check_the_revision_under_the_lock(self) -> None:
        provider = _neo4j([{"id": "rec-1"}])
        assert await provider.compare_and_set_record_fields(["rec-1"], "rev-a", {"extractionStatus": "SKIPPED"}) == ["rec-1"]
        query = _query(provider.client.execute_query)
        assert query.index("SET n.__cas = true") < query.index("WHERE n.contentRev = $rev")

    @pytest.mark.asyncio
    async def test_an_unconnected_client_raises(self) -> None:
        provider = Neo4jProvider(_logger, AsyncMock())
        provider.client = None
        with pytest.raises(RuntimeError, match="not connected"):
            await provider.stage_state_get("k")


class TestStageStateSchema:
    def test_enums_match_the_pipeline_model(self) -> None:
        assert STAGE_TIERS == [tier.value for tier in ParseTier]
        assert STAGE_PRIORITIES == [priority.value for priority in Priority]

    def test_properties_are_the_model_fields_except_the_key(self) -> None:
        aliases = {field.alias or name for name, field in StageState.model_fields.items()}
        properties = set(stage_state_schema["rule"]["properties"])
        assert properties == aliases - {"key"}
        assert set(stage_state_schema["rule"]["required"]) <= properties
