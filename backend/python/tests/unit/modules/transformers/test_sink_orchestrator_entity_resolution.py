"""SinkOrchestrator.resolve_entities and the level passthrough on the dedup path."""

from unittest.mock import AsyncMock, MagicMock

from app.modules.transformers.sink_orchestrator import SinkOrchestrator


def _orchestrator(resolver=None, entity_vector_store=None, graph_provider=None) -> SinkOrchestrator:
    return SinkOrchestrator(
        graphdb=MagicMock(),
        blob_storage=MagicMock(),
        vector_store=MagicMock(),
        graph_provider=graph_provider or AsyncMock(),
        logger=MagicMock(),
        config_service=MagicMock(),
        entity_vector_store=entity_vector_store,
        entity_resolver=resolver,
    )


def _ctx(metadata) -> MagicMock:
    ctx = MagicMock()
    ctx.record.semantic_metadata = metadata
    return ctx


class TestResolveEntities:
    async def test_no_resolver_is_a_noop(self) -> None:
        orch = _orchestrator()
        await orch.resolve_entities(_ctx(MagicMock()))

    async def test_missing_metadata_skips_the_resolver(self) -> None:
        resolver = MagicMock(resolve=AsyncMock())
        await _orchestrator(resolver).resolve_entities(_ctx(None))
        resolver.resolve.assert_not_awaited()

    async def test_resolver_receives_the_context(self) -> None:
        resolver = MagicMock(resolve=AsyncMock())
        ctx = _ctx(MagicMock())
        await _orchestrator(resolver).resolve_entities(ctx)
        resolver.resolve.assert_awaited_once_with(ctx)

    async def test_resolver_errors_propagate(self) -> None:
        resolver = MagicMock(resolve=AsyncMock(side_effect=RuntimeError("graph down")))
        try:
            await _orchestrator(resolver).resolve_entities(_ctx(MagicMock()))
        except RuntimeError as exc:
            assert "graph down" in str(exc)
        else:
            raise AssertionError("expected the resolver error to propagate")


class TestDuplicateSyncCarriesLevel:
    async def test_taxonomy_rows_level_reaches_the_entity_record(self) -> None:
        graph = AsyncMock()
        graph.get_taxonomy_entities_for_record = AsyncMock(return_value=[
            {"entityId": "k-sub", "entityType": "subcategory", "name": "Deep", "level": "2", "aliases": ["deep dive"]},
            {"entityId": "k-topic", "entityType": "topic", "name": "Bug", "level": None},
        ])
        graph.get_record_group_by_id = AsyncMock(return_value=None)
        store = MagicMock(upsert_entities_batch=AsyncMock())
        orch = _orchestrator(entity_vector_store=store, graph_provider=graph)
        await orch.sync_entities_for_duplicate({"_key": "rec-1", "orgId": "org-1", "connectorId": "c1"})
        (entities,) = store.upsert_entities_batch.await_args.args
        by_id = {e.entity_id: e for e in entities}
        assert by_id["k-sub"].level == "2"
        assert by_id["k-sub"].aliases == ["deep dive"]
        assert by_id["k-topic"].level is None
