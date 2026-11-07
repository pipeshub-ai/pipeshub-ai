"""Review fixes for graph schema validation on the Neo4j provider and the named-entity writes.

Mocked client, no database. Assertions are on what is sent and what is raised.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.domain.models import Mention, NamedEntity
from app.modules.named_entities.graph_ops import NamedEntityGraph
from app.modules.named_entities.graph_writer import node_document, value_document
from app.modules.named_entities.resolution import ResolvedEntity
from app.schema.node_validator import SchemaValidationError
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

WRITE = re.compile(r"\b(SET|MERGE|CREATE|DELETE|REMOVE)\b")
RECORDS = "records"


@pytest.fixture(autouse=True)
def _strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRAPH_SCHEMA_ENFORCEMENT", raising=False)


def _provider(rows: list[dict] | None = None, existing: tuple[str, ...] = ()) -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    provider.client = AsyncMock()

    async def answer(query: str, parameters: object = None, *args: object, **kwargs: object) -> list[dict]:
        if rows is not None:
            return rows
        if WRITE.search(query):
            return [{"id": "k"}]
        return [{"id": i} for i in existing]

    provider.client.execute_query = AsyncMock(side_effect=answer)
    return provider


def _queries(provider: Neo4jProvider) -> list[str]:
    return [c.args[0] for c in provider.client.execute_query.await_args_list]


def _params(call: object) -> dict[str, Any]:
    return call.args[1] if len(call.args) > 1 else call.kwargs["parameters"]  # type: ignore[attr-defined]


# ---- 1. named-entity writes -------------------------------------------------------------------


def _entity(kind: EntityKind, name: str) -> NamedEntity:
    return NamedEntity(
        kind=kind, display_name=name, norm_key=name.lower(),
        mentions=[Mention(block_index=0, char_start=0, char_end=len(name), surface=name, extractor="value")],
    )


def _graph(dialect: str = "neo4j") -> tuple[NamedEntityGraph, AsyncMock]:
    execute = AsyncMock(return_value=[])
    return NamedEntityGraph(execute, dialect), execute


@pytest.mark.parametrize("dialect", ["neo4j", "arango"])
async def test_legitimate_named_entity_documents_are_accepted(dialect: str) -> None:
    graph, execute = _graph(dialect)
    node = node_document("o1", ResolvedEntity(entity=_entity(EntityKind.ORGANIZATION, "Acme"), graph_key="n1"), 5)
    value = value_document("o1", "r1", _entity(EntityKind.CURRENCY, "USD 5"), 5)
    await graph.create_if_absent([node])
    await graph.replace_values("r1", [value])
    assert execute.await_count >= 2


async def test_an_invalid_node_in_a_batch_writes_nothing() -> None:
    graph, execute = _graph()
    good = node_document("o1", ResolvedEntity(entity=_entity(EntityKind.ORGANIZATION, "Acme"), graph_key="n1"), 5)
    bad = {**good, "id": "n2", "kind": 7}
    with pytest.raises(SchemaValidationError):
        await graph.create_if_absent([good, bad])
    execute.assert_not_awaited()


async def test_a_node_without_required_fields_is_rejected() -> None:
    graph, execute = _graph()
    with pytest.raises(SchemaValidationError):
        await graph.create_if_absent([{"id": "n1", "name": "Acme"}])
    execute.assert_not_awaited()


async def test_an_invalid_value_row_leaves_the_stale_rows_in_place() -> None:
    graph, execute = _graph()
    good = value_document("o1", "r1", _entity(EntityKind.CURRENCY, "USD 5"), 5)
    with pytest.raises(SchemaValidationError):
        await graph.replace_values("r1", [good, {**good, "id": "v2", "mentionCount": "many"}])
    execute.assert_not_awaited()


async def test_no_values_still_clears_the_record() -> None:
    graph, execute = _graph()
    await graph.replace_values("r1", [])
    assert execute.await_count == 1


@pytest.mark.parametrize("call", ["retry", "reextract", "reproject"])
async def test_persist_retry_markers_are_validated(call: str) -> None:
    graph, execute = _graph()
    ok = {
        "retry": lambda org: graph.schedule_persist_retry(org, "r1", "boom", 5, 1, 2),
        "reextract": lambda org: graph.schedule_reextract(org, "r1", 5),
        "reproject": lambda org: graph.schedule_reproject(org, "r1", 5),
    }[call]
    await ok("o1")
    assert execute.await_count == 1
    with pytest.raises(SchemaValidationError):
        await ok(None)  # orgId must be a string
    assert execute.await_count == 1


async def test_alias_and_orphan_writes_are_validated() -> None:
    graph, execute = _graph()
    await graph.add_aliases("o1", "organization", "n1", ["Acme"], ["acme"], 5)
    await graph.mark_orphans("o1", 5, 10)
    calls = execute.await_count
    with pytest.raises(SchemaValidationError):
        await graph.add_aliases("o1", "organization", "n1", ["Acme"], [3], 5)
    with pytest.raises(SchemaValidationError):
        await graph.mark_orphans("o1", "yesterday", 10)
    assert execute.await_count == calls


async def test_writer_does_not_retry_a_schema_rejection() -> None:
    from app.modules.named_entities.write_retry import retry_named_entity_write

    graph = MagicMock()
    graph.is_named_entity_write_retryable = MagicMock(return_value=False)
    step = AsyncMock(side_effect=SchemaValidationError(RECORDS, "bad"))
    with pytest.raises(SchemaValidationError):
        await retry_named_entity_write(graph, step)
    assert step.await_count == 1


# ---- 2. restore_records and soft_delete_records -----------------------------------------------


async def test_restore_validates_every_item_before_the_statement() -> None:
    p = _provider()
    restores = [{"id": "r1", "set": {"recordName": "a"}}, {"id": "r2", "set": {"recordType": "NOT_A_TYPE"}}]
    with pytest.raises(SchemaValidationError):
        await p.restore_records(restores, "b1")
    p.client.execute_query.assert_not_awaited()


async def test_restore_validates_the_reclaimed_external_id() -> None:
    p = _provider()
    with pytest.raises(SchemaValidationError):
        await p.restore_records([{"id": "r1", "reclaimExternalRecordId": 12}], "b1", connector_id="c1")
    p.client.execute_query.assert_not_awaited()


async def test_restore_accepts_legitimate_items() -> None:
    p = _provider(rows=[{"id": "r1"}])
    got = await p.restore_records(
        [{"id": "r1", "set": {"recordName": "a"}, "reclaimExternalRecordId": "ext-1"}], "b1", connector_id="c1"
    )
    assert got == ["r1"]


async def test_soft_delete_validates_before_the_transaction_begins() -> None:
    p = _provider()
    p.begin_transaction = AsyncMock(return_value="t1")
    p.commit_transaction = AsyncMock()
    with pytest.raises(SchemaValidationError):
        await p.soft_delete_records(["r1"], "c1", delete_source="NOT_A_SOURCE", batch_id="b1")
    p.begin_transaction.assert_not_awaited()
    p.client.execute_query.assert_not_awaited()


async def test_soft_delete_without_a_user_is_accepted() -> None:
    p = _provider(rows=[{"root_keys": [], "marked": []}])
    p.begin_transaction = AsyncMock(return_value="t1")
    p.commit_transaction = AsyncMock()
    await p.soft_delete_records(["r1"], "c1", delete_source="USER", batch_id="b1", deleted_by_user_id=None)
    p.begin_transaction.assert_awaited_once()


# ---- 3. batch_create_entity_relations ---------------------------------------------------------


def _relation(**override: object) -> dict[str, Any]:
    edge = {
        "_from": "records/r1", "_to": "records/r2", "edgeType": "ASSIGNED_TO", "createdAtTimestamp": 1,
    }
    edge.update(override)
    return edge


async def test_entity_relations_are_validated_before_any_is_skipped() -> None:
    p = _provider()
    skipped_for_endpoints = {"edgeType": "ASSIGNED_TO", "createdAtTimestamp": "not a number"}  # no endpoints
    with pytest.raises(SchemaValidationError):
        await p.batch_create_entity_relations([_relation(), skipped_for_endpoints])
    p.client.execute_query.assert_not_awaited()


async def test_entity_relation_without_edge_type_is_rejected_not_skipped() -> None:
    p = _provider()
    edge = _relation()
    del edge["edgeType"]
    with pytest.raises(SchemaValidationError):
        await p.batch_create_entity_relations([edge])
    p.client.execute_query.assert_not_awaited()


async def test_entity_relation_skip_stays_for_edges_the_schema_accepts(monkeypatch: pytest.MonkeyPatch) -> None:
    p = _provider()
    # an edge the schema accepts but that cannot be written: no endpoints
    assert await p.batch_create_entity_relations([_relation(_from="", _to="")]) is True
    p.client.execute_query.assert_not_awaited()
    assert await p.batch_create_entity_relations([_relation()]) is True
    assert p.client.execute_query.await_count == 1
    # enforcement off keeps the old skip for an edge without edgeType
    monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "off")
    edge = _relation()
    del edge["edgeType"]
    assert await p.batch_create_entity_relations([edge]) is True
    assert p.client.execute_query.await_count == 1


# ---- 4. upsert with duplicate ids -------------------------------------------------------------


def _full(key: str) -> dict[str, Any]:
    return {
        "_key": key, "orgId": "o1", "recordName": "a.txt", "externalRecordId": f"ext-{key}",
        "recordType": "FILE", "origin": "CONNECTOR", "createdAtTimestamp": 1, "connectorId": "c1",
    }


@pytest.mark.parametrize("order", ["full_first", "partial_first"])
async def test_full_and_partial_payload_of_an_absent_id_is_accepted(order: str) -> None:
    p = _provider(existing=())
    batch = [_full("x"), {"_key": "x", "recordName": "b"}]
    if order == "partial_first":
        batch.reverse()
    assert await p.batch_upsert_nodes(batch, RECORDS) is True
    probes = [q for q in _queries(p) if not WRITE.search(q)]
    assert probes == []  # nothing incomplete is left to probe


async def test_partials_that_together_are_complete_are_accepted() -> None:
    p = _provider(existing=())
    full = _full("x")
    a = {k: v for k, v in full.items() if k in ("_key", "orgId", "recordName", "externalRecordId", "recordType")}
    b = {k: v for k, v in full.items() if k not in ("orgId", "recordName", "externalRecordId", "recordType")}
    await p.batch_upsert_nodes([a, b], RECORDS)


async def test_only_truly_incomplete_ids_are_probed() -> None:
    p = _provider(existing=())
    with pytest.raises(SchemaValidationError):
        await p.batch_upsert_nodes([_full("x"), {"_key": "y", "recordName": "b"}], RECORDS)
    probes = [c for c in p.client.execute_query.await_args_list if not WRITE.search(c.args[0])]
    assert len(probes) == 1 and _params(probes[0])["ids"] == ["y"]


# ---- 5. compare_and_set_indexing_status -------------------------------------------------------


async def test_queued_swap_validates_the_queue_stamp(monkeypatch: pytest.MonkeyPatch) -> None:
    p = _provider(rows=[])
    seen: list[dict] = []
    real = p.validator.validate_node_update
    monkeypatch.setattr(p.validator, "validate_node_update", lambda c, u: (seen.append(dict(u)), real(c, u))[1])
    await p.compare_and_set_indexing_status(["r1"], ProgressStatus.NOT_STARTED.value, ProgressStatus.QUEUED.value)
    assert seen and "queuedAtTimestamp" in seen[0]
    sent_now = _params(p.client.execute_query.await_args)["now"]
    assert seen[0]["queuedAtTimestamp"] == sent_now


async def test_non_queued_swap_validates_only_the_status(monkeypatch: pytest.MonkeyPatch) -> None:
    p = _provider(rows=[])
    seen: list[dict] = []
    real = p.validator.validate_node_update
    monkeypatch.setattr(p.validator, "validate_node_update", lambda c, u: (seen.append(dict(u)), real(c, u))[1])
    await p.compare_and_set_indexing_status(["r1"], ProgressStatus.QUEUED.value, ProgressStatus.COMPLETED.value)
    assert seen == [{"indexingStatus": ProgressStatus.COMPLETED.value}]


# ---- 6. agent knowledge migration -------------------------------------------------------------


async def _migrate(agent_created_by: str | None) -> dict:
    p = _provider()
    p.delete_edge = AsyncMock()
    p.delete_nodes = AsyncMock()
    p.batch_upsert_nodes = AsyncMock(return_value=True)
    p.batch_create_edges = AsyncMock(return_value=True)
    answers = iter([
        [{"kb_ids": ["kb1"]}],
        [{"agent_key": "a1", "knowledge_key": "old", "filters": None, "existing_connector_ids": [],
          "agent_created_by": agent_created_by}],
    ])
    p.client.execute_query = AsyncMock(side_effect=lambda *a, **k: next(answers))
    await p.migrate_agent_hub_knowledge("o1")
    return p.batch_upsert_nodes.await_args.args[0][0]


async def test_migrated_knowledge_keeps_the_agents_creator() -> None:
    assert (await _migrate("u-42"))["createdBy"] == "u-42"


async def test_migrated_knowledge_falls_back_to_system_without_a_creator() -> None:
    assert (await _migrate(None))["createdBy"] == "system"
