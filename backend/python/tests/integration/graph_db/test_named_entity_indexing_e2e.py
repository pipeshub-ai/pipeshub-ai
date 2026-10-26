"""Indexing to query on a real Neo4j and a real ArangoDB, with a scripted model.

A record's blocks go through the production extraction stage (deterministic
recognizers plus the agent loop) and persist stage, then the agent tool reads
them back. Only the model, the agent's app scope and the per-record permission
answer are fakes; the real permission query is covered by
``test_record_visibility_e2e.test_search_permission_checks``.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_named_entity_indexing_e2e.py -m integration
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.agent_loop_lib.core.messages import AssistantMessage, ToolCall
from app.agent_loop_lib.core.responses import StopReason
from app.agents.actions.knowledge_graph.ops import values
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.models.blocks import Block, BlocksContainer, BlockType
from app.modules.indexing.duplicate_reconcile import DuplicateReconciler
from app.modules.named_entities import stage
from app.modules.named_entities.graph_ops import NamedEntityQuery
from app.modules.named_entities.graph_writer import NamedEntityGraphWriter
from app.modules.named_entities.strategies.agent.transport import SlottedTransport
from app.modules.named_entities.sweep import NamedEntitySweeper
from app.modules.retrieval.entity_permissions import EntityAccessContext
from tests.integration.graph_db.test_named_entity_graph_contract import (  # noqa: F401
    _ms,
    _records,
    graph,
)
from tests.unit.agents.adapter.support.scripted_transport import (
    ScriptedStep,
    ScriptedTransport,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

HOUR_MS = 3_600_000
_TEXT = "Acme Robotics signed a $25,000 order on March 3, 2026. Card 4111 1111 1111 1111 was declined."


def _call(name: str, arguments: dict, call_id: str) -> ScriptedStep:
    return ScriptedStep(
        message=AssistantMessage(content="", tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)]),
        stop_reason=StopReason.TOOL_USE,
    )


def _model() -> ScriptedTransport:
    return ScriptedTransport([
        _call("submit_entities", {
            "entities": [{"text": "Acme Robotics", "block": 0, "kind": "organization", "normalized": ""}],
        }, "1"),
        _call("finish", {"reason": "done"}, "2"),
    ])


def _record(record_id: str, org_id: str) -> SimpleNamespace:
    block = Block(type=BlockType.TEXT, data=_TEXT, index=0)
    return SimpleNamespace(
        id=record_id,
        org_id=org_id,
        record_name="Order confirmation",
        record_type=SimpleNamespace(value="FILE"),
        block_containers=BlocksContainer(blocks=[block], block_groups=[]),
        source_updated_at=_ms(2026, 3, 10),
        semantic_metadata=None,
    )


class _UserGraph:
    """The real provider, seen by a user whose permission check passes only ``readable``."""

    def __init__(self, provider, readable: set[str]) -> None:
        self._provider = provider
        self._readable = readable
        self.checked: list[str] = []

    async def filter_accessible_record_ids(self, record_ids, user_id, org_id):
        self.checked.extend(record_ids)
        return set(record_ids) & self._readable

    def __getattr__(self, name):
        return getattr(self._provider, name)


def _agent_scope(*app_ids: str) -> EntityAccessContext:
    return EntityAccessContext(
        org_id="", user_key="u", app_level_app_ids=frozenset(app_ids),
        record_level_app_ids=frozenset(), record_group_ids=frozenset(), app_names={},
    )


async def _index(provider, record) -> None:
    ctx = SimpleNamespace(record=record)
    with patch.object(stage, "indexing_llm", AsyncMock(return_value=(None, SlottedTransport(_model()), "fake", "fake-agent"))):
        extraction = await stage.extract_named_entities(ctx, config_service=None, enabled=True)
    assert (extraction.strategy, extraction.status) == ("agent", "COMPLETED")
    await stage.persist_named_entities(
        ctx, extraction, graph_provider=provider, graph_data_store=GraphDataStore(stage.logger, provider), enabled=True,
    )


async def test_indexed_values_are_queryable_and_only_for_readable_in_scope_records(graph) -> None:  # noqa: F811
    provider, org_id, _ = graph
    readable, private = await _records(provider, org_id, 2)
    (other_app,) = await _records(provider, org_id, 1, connector_id=f"{org_id}-other")
    for record_id in (readable, private, other_app):
        await _index(provider, _record(record_id, org_id))

    stored = await provider.get_named_entities_for_record(readable)
    kinds = {row["kind"] for row in stored}
    assert {"organization", "currency", "date"} <= kinds
    assert not any("4111" in json.dumps(row, default=str) for row in stored)
    doc = await provider.get_document(readable, "records")
    assert doc.get("entityExtractionStatus") is None

    user_graph = _UserGraph(provider, {readable, other_app})
    state = {"graph_provider": user_graph, "org_id": org_id, "user_id": "u"}
    with (
        patch.object(values, "_named_entities_enabled", AsyncMock(return_value=True)),
        patch.object(values, "load_entity_access_context", AsyncMock(return_value=_agent_scope(f"{org_id}-conn"))),
    ):
        for query in (
            {"amount_min": 20_000, "amount_max": 30_000, "currency": "USD"},
            {"date_from": _ms(2026, 3, 1), "date_to": _ms(2026, 3, 31)},
            {"kinds": ["organization"], "name": "acme"},
        ):
            ok, body = await values.execute_find_records_by_value(state, **query)
            assert ok, body
            assert json.loads(body)["records"] == [{"virtualRecordId": f"v-{readable}", "recordId": readable}], query
        assert other_app not in user_graph.checked

        ok, body = await values.execute_find_records_by_value(state, amount_min=1, amount_max=100, currency="USD")
        assert ok and json.loads(body)["records"] == []


async def test_a_deleted_records_entities_leave_the_graph_and_the_agent_tool_after_the_sweep(graph) -> None:  # noqa: F811
    provider, org_id, _ = graph
    (record,) = await _records(provider, org_id, 1)
    await _index(provider, _record(record, org_id))
    assert await provider.get_named_entities_for_record(record)

    await NamedEntityGraphWriter(provider, GraphDataStore(stage.logger, provider), stage.logger).clear_for_record(record)
    vectors = AsyncMock()
    clock = [1_000 * HOUR_MS]
    sweeper = NamedEntitySweeper(
        provider, vectors, grace_ms=24 * HOUR_MS, batch_size=50, now_ms=lambda: clock[0], logger_=stage.logger,
    )
    await sweeper.sweep_org(org_id)
    clock[0] += 25 * HOUR_MS
    # Typed values were rows of the record and went with the clear; only entity nodes are swept.
    assert (await sweeper.sweep_org(org_id)).deleted >= 1
    vectors.delete_entities.assert_awaited()

    hits = await provider.get_records_for_named_entities(org_id, None, NamedEntityQuery(kinds=["organization"], name_prefix="acme"))
    assert hits["hits"] == []
    assert (await provider.query_named_entities(org_id, NamedEntityQuery(kinds=["organization"])))["entities"] == []


async def test_a_promoted_duplicate_exposes_the_same_entities(graph) -> None:  # noqa: F811
    provider, org_id, _ = graph
    primary, duplicate = await _records(provider, org_id, 2)
    shared = f"v-{primary}"
    assert await provider.update_node(duplicate, "records", {"virtualRecordId": shared})
    await _index(provider, _record(primary, org_id))
    assert await provider.get_named_entities_for_record(duplicate) == []

    reconciler = DuplicateReconciler(graph_provider=provider, sink=None, sync_vector_membership=AsyncMock(), logger=stage.logger)
    assert await reconciler.reconcile(primary, shared) is True

    mine = sorted((row["kind"], row["normKey"]) for row in await provider.get_named_entities_for_record(primary))
    assert mine and mine == sorted((row["kind"], row["normKey"]) for row in await provider.get_named_entities_for_record(duplicate))
    hits = await provider.get_records_for_named_entities(org_id, None, NamedEntityQuery(kinds=["organization"], name_prefix="acme"))
    assert {hit["recordId"] for hit in hits["hits"]} == {primary, duplicate}
