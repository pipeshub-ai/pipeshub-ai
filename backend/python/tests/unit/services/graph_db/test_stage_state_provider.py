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
        assert query.index("SET n.matchLock = true") < query.index("WHERE n.status = $expected")
        assert "REMOVE n.matchLock" in query

    @pytest.mark.asyncio
    async def test_cas_that_matched_nothing_is_false(self) -> None:
        assert await _neo4j([]).stage_state_compare_and_set("k", "QUEUED", "IN_PROGRESS", {}) is False

    @pytest.mark.asyncio
    async def test_record_fields_check_the_revision_under_the_lock(self) -> None:
        provider = _neo4j([{"id": "rec-1"}])
        assert await provider.compare_and_set_record_fields(["rec-1"], "rev-a", {"extractionStatus": "SKIPPED"}) == ["rec-1"]
        query = _query(provider.client.execute_query)
        assert query.index("SET n.matchLock = true") < query.index("WHERE n.contentRev = $rev")
        assert provider.client.execute_query.await_args.kwargs["parameters"]["fields"] == {"extractionStatus": "SKIPPED"}

    @pytest.mark.asyncio
    async def test_record_fields_lock_records_in_one_order(self) -> None:
        provider = _neo4j([])
        await provider.compare_and_set_record_fields(["rec-2", "rec-1", "rec-2", ""], "rev-a", {"extractionStatus": "SKIPPED"})
        assert provider.client.execute_query.await_args.kwargs["parameters"]["keys"] == ["rec-1", "rec-2"]

    @pytest.mark.asyncio
    async def test_a_completed_indexing_status_sets_has_indexed_copy(self) -> None:
        provider = _neo4j([{"id": "rec-1"}])
        await provider.compare_and_set_record_fields(["rec-1"], "rev-a", {"indexingStatus": "COMPLETED"})
        fields = provider.client.execute_query.await_args.kwargs["parameters"]["fields"]
        assert fields == {"indexingStatus": "COMPLETED", "hasIndexedCopy": True}
        await provider.compare_and_set_record_fields(["rec-1"], "rev-a", {"indexingStatus": "EMPTY"})
        fields = provider.client.execute_query.await_args.kwargs["parameters"]["fields"]
        assert fields == {"indexingStatus": "EMPTY", "hasIndexedCopy": False}

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


class TestDeletingTheStageStatesOfVirtualRecords:
    @pytest.mark.asyncio
    async def test_arango_removes_every_revision_in_one_statement(self) -> None:
        provider = _arango([3])
        assert await provider.stage_states_delete_for_virtual_records(["vr-2", "vr-1", "vr-2", ""]) == 3
        query = _query(provider.http_client.execute_aql)
        assert "FILTER doc.virtualRecordId IN @ids" in query and "REMOVE doc IN @@collection" in query
        assert "COLLECT WITH COUNT INTO removed RETURN removed" in query
        assert "rev" not in query
        bind = provider.http_client.execute_aql.await_args.args[1]
        assert bind["ids"] == ["vr-1", "vr-2"] and bind["@collection"] == "stageStates"

    @pytest.mark.asyncio
    async def test_neo4j_removes_every_revision_in_one_statement(self) -> None:
        provider = _neo4j([{"deleted": 6}])
        assert await provider.stage_states_delete_for_virtual_records(["vr-2", "vr-1"]) == 6
        query = _query(provider.client.execute_query)
        assert "MATCH (n:StageState) WHERE n.virtualRecordId IN $ids AND n.rev IS NOT NULL DETACH DELETE n" in query
        assert provider.client.execute_query.await_args.kwargs["parameters"] == {"ids": ["vr-1", "vr-2"]}

    @pytest.mark.asyncio
    async def test_a_long_id_list_is_deleted_in_bounded_batches(self) -> None:
        arango, neo4j = _arango([2]), _neo4j([{"deleted": 2}])
        ids = [f"vr-{i:04d}" for i in range(2500)]
        assert await arango.stage_states_delete_for_virtual_records(ids) == 6
        assert await neo4j.stage_states_delete_for_virtual_records(ids) == 6
        arango_batches = [c.args[1]["ids"] for c in arango.http_client.execute_aql.await_args_list]
        neo4j_batches = [c.kwargs["parameters"]["ids"] for c in neo4j.client.execute_query.await_args_list]
        assert [len(b) for b in arango_batches] == [len(b) for b in neo4j_batches] == [1000, 1000, 500]
        assert sum(arango_batches, []) == sorted(ids)

    @pytest.mark.asyncio
    async def test_no_virtual_record_is_no_query(self) -> None:
        arango, neo4j = _arango(), _neo4j()
        assert await arango.stage_states_delete_for_virtual_records([""]) == 0
        assert await neo4j.stage_states_delete_for_virtual_records([]) == 0
        arango.http_client.execute_aql.assert_not_awaited()
        neo4j.client.execute_query.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_outage_raises(self) -> None:
        provider = _arango(side_effect=ArangoQueryError("Query failed", 503, "unavailable"))
        with pytest.raises(ArangoQueryError):
            await provider.stage_states_delete_for_virtual_records(["vr-1"])


def _connector_delete_ready(provider: ArangoHTTPProvider | Neo4jProvider, *, stages_ok: bool = True) -> None:
    """Stub every step of a connector delete but the stage states, which are what is checked."""
    provider.get_document = AsyncMock(return_value={"_key": "c1", "type": "Drive"})
    provider._collect_connector_entities = AsyncMock(return_value={
        "record_keys": [], "record_ids": [], "virtual_record_ids": [], "record_group_keys": [],
        "role_keys": [], "group_keys": [], "all_node_ids": [],
    })
    provider._get_all_edge_collections = AsyncMock(return_value=["permission"])
    provider._collect_isoftype_targets = AsyncMock(return_value=([], True))
    provider._delete_edges_by_connector_id = AsyncMock(return_value=(0, []))
    provider._delete_isoftype_targets_from_collected = AsyncMock(return_value=(0, []))
    provider._delete_nodes_by_keys = AsyncMock(return_value=(1, 0))
    provider.remove_authenticated_as = AsyncMock()
    provider.kh_scope_forget = AsyncMock()
    provider._delete_nodes_by_connector_id = AsyncMock(return_value=(3, True))
    provider._delete_stage_states_by_connector_id = AsyncMock(return_value=(3, stages_ok))
    provider.begin_transaction = AsyncMock(return_value="txn1")
    provider.commit_transaction = AsyncMock()
    provider.rollback_transaction = AsyncMock()
    if isinstance(provider, Neo4jProvider):
        # Auto-committed batches stash the ids the cleanup needs and drop the stash with the app.
        provider._stash_connector_delete_handles = AsyncMock(side_effect=lambda _c, groups, vrids: (list(groups), vrids))
        provider._delete_connector_with_its_delete_handles = AsyncMock(return_value=1)


def _providers() -> list[ArangoHTTPProvider | Neo4jProvider]:
    return [_arango(), _neo4j()]


class TestAConnectorDeleteTakesItsStageStates:
    @pytest.mark.parametrize("provider", _providers(), ids=["arango", "neo4j"])
    @pytest.mark.asyncio
    async def test_its_stage_states_go_in_the_delete_transaction(self, provider) -> None:
        _connector_delete_ready(provider)

        result = await provider.delete_connector_instance("c1", "org1")

        assert result["success"] is True
        provider._delete_stage_states_by_connector_id.assert_awaited_once_with("txn1", "c1")
        assert "stageStates" in provider.begin_transaction.await_args.kwargs["write"]
        provider.commit_transaction.assert_awaited_once_with("txn1")

    @pytest.mark.parametrize("provider", _providers(), ids=["arango", "neo4j"])
    @pytest.mark.asyncio
    async def test_a_failed_stage_state_delete_rolls_the_delete_back(self, provider) -> None:
        _connector_delete_ready(provider, stages_ok=False)

        result = await provider.delete_connector_instance("c1", "org1")

        assert result["success"] is False
        provider.rollback_transaction.assert_awaited_once_with("txn1")
        provider.commit_transaction.assert_not_awaited()


class TestAConnectorsStageStatesGoInBoundedBatches:
    @pytest.mark.asyncio
    async def test_arango_repeats_a_counted_batch_until_one_comes_back_short(self) -> None:
        provider = _arango()
        provider.http_client.execute_aql = AsyncMock(side_effect=[[5000], [5000], [7]])

        assert await provider._delete_stage_states_by_connector_id("txn1", "c1") == (10007, True)

        calls = provider.http_client.execute_aql.await_args_list
        assert len(calls) == 3
        assert all(c.kwargs["txn_id"] == "txn1" for c in calls)
        assert calls[0].kwargs["bind_vars"] == {"@collection": "stageStates", "connector_id": "c1", "batch": 5000}
        query = " ".join(calls[0].kwargs["query"].split())
        assert "LIMIT @batch REMOVE doc IN @@collection COLLECT WITH COUNT INTO removed RETURN removed" in query

    @pytest.mark.asyncio
    async def test_neo4j_repeats_a_counted_batch_until_one_comes_back_short(self) -> None:
        provider = _neo4j()
        provider.client.execute_query = AsyncMock(side_effect=[[{"deleted": 5000}], [{"deleted": 0}]])

        assert await provider._delete_stage_states_by_connector_id("txn1", "c1") == (5000, True)

        calls = provider.client.execute_query.await_args_list
        assert len(calls) == 2 and all(c.kwargs["txn_id"] == "txn1" for c in calls)
        assert calls[0].kwargs["parameters"] == {"connector_id": "c1", "batch": 5000}
        assert "WITH n LIMIT $batch DETACH DELETE n" in " ".join(calls[0].args[0].split())

    @pytest.mark.parametrize("provider", _providers(), ids=["arango", "neo4j"])
    @pytest.mark.asyncio
    async def test_a_failed_batch_reports_failure(self, provider) -> None:
        client = getattr(provider, "http_client", None) if isinstance(provider, ArangoHTTPProvider) else provider.client
        method = "execute_aql" if isinstance(provider, ArangoHTTPProvider) else "execute_query"
        setattr(client, method, AsyncMock(side_effect=RuntimeError("down")))

        assert await provider._delete_stage_states_by_connector_id("txn1", "c1") == (0, False)
