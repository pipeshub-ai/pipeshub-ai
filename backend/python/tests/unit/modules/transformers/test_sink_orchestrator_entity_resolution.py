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
            {"entityId": "k-sub", "entityType": "subcategory", "name": "Deep", "level": "2",
             "aliases": ["deep dive"], "orgId": "org-1"},
            {"entityId": "k-topic", "entityType": "topic", "name": "Bug", "level": None, "orgId": "org-1"},
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


class TestDuplicateSyncIsOrgScoped:
    """The dedup path projects what the record's edges reach. A node of
    another org must not enter this org's entity space, and a shared legacy
    node's aliases must not either (KG-25)."""

    async def _sync(self, rows: list[dict], upsert: AsyncMock | None = None) -> tuple[bool, AsyncMock]:
        graph = AsyncMock()
        graph.get_taxonomy_entities_for_record = AsyncMock(return_value=rows)
        graph.get_record_group_by_id = AsyncMock(return_value=None)
        store = MagicMock(upsert_entities_batch=upsert or AsyncMock(return_value=0))
        orch = _orchestrator(entity_vector_store=store, graph_provider=graph)
        ok = await orch.sync_entities_for_duplicate({"_key": "rec-1", "orgId": "org-1", "connectorId": "c1"})
        return ok, store.upsert_entities_batch

    async def test_own_node_keeps_its_aliases(self) -> None:
        ok, upsert = await self._sync([
            {"entityId": "t1", "entityType": "topic", "name": "A", "aliases": ["a1"], "orgId": "org-1"},
        ])
        (topic,) = upsert.await_args_list[0].args[0]
        assert ok and topic.aliases == ["a1"]

    async def test_other_orgs_node_is_skipped(self) -> None:
        ok, upsert = await self._sync([
            {"entityId": "t1", "entityType": "topic", "name": "A", "aliases": ["a1"], "orgId": "org-2"},
        ])
        projected = [e.entity_id for call in upsert.await_args_list for e in call.args[0]]
        assert ok and "t1" not in projected

    async def test_legacy_node_is_projected_without_aliases(self) -> None:
        ok, upsert = await self._sync([
            {"entityId": "t1", "entityType": "topic", "name": "A", "aliases": ["org-2 spelling"], "orgId": None},
        ])
        (topic,) = upsert.await_args_list[0].args[0]
        assert ok and topic.entity_id == "t1" and topic.aliases == []

    async def test_global_department_is_kept(self) -> None:
        ok, upsert = await self._sync([
            {"entityId": "d1", "entityType": "department", "name": "Finance", "orgId": None},
        ])
        (dept,) = upsert.await_args_list[0].args[0]
        assert ok and dept.entity_id == "d1"


class TestDuplicateSyncReportsFailure:
    """Reconciliation clears its pending flag only on success, so the sync
    must say when it did not write (KG-51)."""

    async def test_write_failures_report_false(self) -> None:
        ok, _ = await TestDuplicateSyncIsOrgScoped()._sync(
            [{"entityId": "t1", "entityType": "topic", "name": "A", "orgId": "org-1"}],
            upsert=AsyncMock(return_value=1),
        )
        assert ok is False

    async def test_a_raised_error_reports_false(self) -> None:
        ok, _ = await TestDuplicateSyncIsOrgScoped()._sync(
            [{"entityId": "t1", "entityType": "topic", "name": "A", "orgId": "org-1"}],
            upsert=AsyncMock(side_effect=RuntimeError("vector db down")),
        )
        assert ok is False

    async def test_taxonomy_read_failure_reports_false(self) -> None:
        graph = AsyncMock()
        graph.get_taxonomy_entities_for_record = AsyncMock(side_effect=RuntimeError("graph down"))
        orch = _orchestrator(entity_vector_store=MagicMock(upsert_entities_batch=AsyncMock(return_value=0)),
                             graph_provider=graph)
        assert await orch.sync_entities_for_duplicate({"_key": "r", "orgId": "org-1"}) is False

    async def test_nothing_to_do_is_success(self) -> None:
        assert await _orchestrator().sync_entities_for_duplicate({"_key": "r", "orgId": "org-1"}) is True
        orch = _orchestrator(entity_vector_store=MagicMock(upsert_entities_batch=AsyncMock(return_value=0)))
        assert await orch.sync_entities_for_duplicate({"orgId": "org-1"}) is True
