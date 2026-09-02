"""Unit tests for app.services.vector_db.collection_registry.CollectionRegistry."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.exceptions.indexing_exceptions import VectorStoreError
from app.services.vector_db.collection_manifest import (
    CollectionManifestStore,
    ManagedCollection,
)
from app.services.vector_db.collection_registry import CollectionRegistry
from app.services.vector_db.models import CollectionConfig, VectorCollectionInfo
from app.services.vector_db.strategies.single import SingleCollectionStrategy
from app.services.vector_db.strategy import (
    DeleteAction,
    DeleteContext,
    DeleteScope,
    QueryContext,
    RecordContext,
)


def _make_config_service():
    """In-memory fake ConfigurationService: enough of get_config/set_config
    for the manifest persistence CollectionRegistry relies on."""
    store: dict = {}

    async def get_config(key, default=None):
        return store.get(key, default)

    async def set_config(key, value):
        store[key] = value

    svc = MagicMock()
    svc.get_config = AsyncMock(side_effect=get_config)
    svc.set_config = AsyncMock(side_effect=set_config)
    return svc


def _make_vdb(exists=False, dimension=None, name="records"):
    """A vector DB double whose collection state is stated explicitly.

    A bare AsyncMock returns a truthy MagicMock for ``get_collection_info``,
    which reads as "the collection exists" and silently changes what a test is
    actually asserting — so every test says which it wants.

    ``exists`` applies to ``name`` only. Answering "yes" for every name would
    make adoption look like it found an entities collection on a deployment
    that has never had one.
    """
    vdb = AsyncMock()

    async def get_collection_info(collection_name):
        present = exists and collection_name == name
        return VectorCollectionInfo(
            name=collection_name,
            exists=present,
            dense_dimension=dimension if present else None,
        )

    async def collection_exists(collection_name):
        return exists and collection_name == name

    vdb.get_collection_info = AsyncMock(side_effect=get_collection_info)
    vdb.collection_exists = AsyncMock(side_effect=collection_exists)
    return vdb


def _make_registry(
    vector_db_service=None, strategy=None, config_service=None, liveness_probe=None
):
    return CollectionRegistry(
        vector_db_service=vector_db_service or _make_vdb(),
        strategy=strategy or SingleCollectionStrategy(),
        collection_config_factory=lambda size, sparse_idf=False: CollectionConfig(
            embedding_size=size
        ),
        manifest_store=CollectionManifestStore(
            config_service or _make_config_service(), MagicMock()
        ),
        logger=MagicMock(),
        liveness_probe=liveness_probe,
    )


# ---------------------------------------------------------------------------
# strategy_name
# ---------------------------------------------------------------------------


class TestRegistryBasics:
    def test_strategy_name_property(self):
        registry = _make_registry()
        assert registry.strategy_name == "single"


# ---------------------------------------------------------------------------
# ensure_collection
# ---------------------------------------------------------------------------


class TestEnsureCollection:
    @pytest.mark.asyncio
    async def test_creates_collection_when_missing(self):
        vdb = AsyncMock()
        vdb.get_collection_info = AsyncMock(
            return_value=VectorCollectionInfo(name="records", exists=False)
        )
        registry = _make_registry(vector_db_service=vdb)

        name = await registry.ensure_collection(RecordContext(org_id="org-1"), embedding_size=768)

        assert name == "records"
        vdb.create_collection.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_idempotent_when_collection_already_exists_same_dim(self):
        vdb = AsyncMock()
        vdb.get_collection_info = AsyncMock(
            return_value=VectorCollectionInfo(name="records", exists=True, dense_dimension=768)
        )
        registry = _make_registry(vector_db_service=vdb)

        name = await registry.ensure_collection(RecordContext(org_id="org-1"), embedding_size=768)

        assert name == "records"
        vdb.create_collection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dimension_mismatch_raises(self):
        vdb = AsyncMock()
        vdb.get_collection_info = AsyncMock(
            return_value=VectorCollectionInfo(name="records", exists=True, dense_dimension=512)
        )
        registry = _make_registry(vector_db_service=vdb)

        with pytest.raises(VectorStoreError):
            await registry.ensure_collection(RecordContext(org_id="org-1"), embedding_size=768)

    @pytest.mark.asyncio
    async def test_second_call_uses_existence_cache_not_vector_db(self):
        vdb = AsyncMock()
        vdb.get_collection_info = AsyncMock(
            return_value=VectorCollectionInfo(name="records", exists=False)
        )
        registry = _make_registry(vector_db_service=vdb)

        await registry.ensure_collection(RecordContext(org_id="org-1"), embedding_size=768)
        vdb.get_collection_info.reset_mock()

        name = await registry.ensure_collection(RecordContext(org_id="org-1"), embedding_size=768)

        assert name == "records"
        vdb.get_collection_info.assert_not_called()

    @pytest.mark.asyncio
    async def test_cached_existence_does_not_skip_the_dimension_check(self):
        """A model swap mid-process must not ride the existence cache.

        The cache proves "this collection exists", not "at your width". If a
        hit short-circuited the check, points of the new width would be
        upserted into the old collection until the TTL expired.
        """
        vdb = _make_vdb(exists=True, dimension=768)
        registry = _make_registry(vector_db_service=vdb)
        await registry.ensure_collection(RecordContext(org_id="org-1"), embedding_size=768)

        with pytest.raises(VectorStoreError):
            await registry.ensure_collection(
                RecordContext(org_id="org-1"), embedding_size=1024
            )

    @pytest.mark.asyncio
    async def test_read_path_cache_entry_does_not_satisfy_the_write_path(self):
        """resolve_for_query records existence without a dimension; ensure_collection
        must still verify rather than trust that entry."""
        vdb = _make_vdb(exists=True, dimension=512)
        registry = _make_registry(vector_db_service=vdb)
        await registry.resolve_for_query(QueryContext(org_id="org-1"))
        vdb.get_collection_info.reset_mock()

        with pytest.raises(VectorStoreError):
            await registry.ensure_collection(
                RecordContext(org_id="org-1"), embedding_size=768
            )
        vdb.get_collection_info.assert_awaited()

    @pytest.mark.asyncio
    async def test_concurrent_creation_race_treated_as_success(self):
        """Two callers racing to create the same collection: the loser's
        "already exists" from the vector DB must not surface as an error."""
        vdb = AsyncMock()
        vdb.get_collection_info = AsyncMock(
            return_value=VectorCollectionInfo(name="records", exists=False)
        )
        vdb.create_collection = AsyncMock(side_effect=Exception("Collection already exists"))
        registry = _make_registry(vector_db_service=vdb)

        # Re-check after the race loses reports the collection now exists
        # with a matching dimension.
        vdb.get_collection_info.side_effect = [
            VectorCollectionInfo(name="records", exists=False),
            VectorCollectionInfo(name="records", exists=True, dense_dimension=768),
        ]

        name = await registry.ensure_collection(RecordContext(org_id="org-1"), embedding_size=768)
        assert name == "records"

    @pytest.mark.asyncio
    async def test_records_entry_in_manifest(self):
        vdb = AsyncMock()
        vdb.get_collection_info = AsyncMock(
            return_value=VectorCollectionInfo(name="records", exists=False)
        )
        registry = _make_registry(vector_db_service=vdb)

        await registry.ensure_collection(
            RecordContext(org_id="org-1", embedding_model="text-embed-3"), embedding_size=768
        )

        managed = await registry.list_managed_collections()
        assert len(managed) == 1
        assert managed[0] == ManagedCollection(
            name="records",
            collection_type="records",
            embedding_dimension=768,
            strategy_name="single",
            embedding_model="text-embed-3",
        )


# ---------------------------------------------------------------------------
# resolve_for_query
# ---------------------------------------------------------------------------


class TestResolveForQuery:
    @pytest.mark.asyncio
    async def test_returns_existing_collections_only(self):
        vdb = AsyncMock()
        vdb.collection_exists = AsyncMock(return_value=True)
        registry = _make_registry(vector_db_service=vdb)

        result = await registry.resolve_for_query(QueryContext(org_id="org-1"))

        assert result == ["records"]

    @pytest.mark.asyncio
    async def test_skips_nonexistent_collection_without_error(self):
        vdb = AsyncMock()
        vdb.collection_exists = AsyncMock(return_value=False)
        registry = _make_registry(vector_db_service=vdb)

        result = await registry.resolve_for_query(QueryContext(org_id="org-1"))

        assert result == []

    @pytest.mark.asyncio
    async def test_existence_check_error_is_swallowed(self):
        vdb = AsyncMock()
        vdb.collection_exists = AsyncMock(side_effect=Exception("boom"))
        registry = _make_registry(vector_db_service=vdb)

        result = await registry.resolve_for_query(QueryContext(org_id="org-1"))

        assert result == []


# ---------------------------------------------------------------------------
# resolve_delete_scope
# ---------------------------------------------------------------------------


class _DroppingStrategy(SingleCollectionStrategy):
    """Stands in for a future per-connector-type strategy that wants a drop."""

    def __init__(self) -> None:
        self.seen_contexts: list = []

    def resolve_delete_scope(self, ctx):
        self.seen_contexts.append(ctx)
        return DeleteScope(
            action=DeleteAction.DROP_COLLECTION,
            collection_names=["Google Drive Records"],
        )


class TestResolveDeleteScope:
    @pytest.mark.asyncio
    async def test_delegates_to_strategy_and_sanitizes_names(self):
        registry = _make_registry()
        ctx = DeleteContext(org_id="org-1", connector_id="conn-1")

        scope = await registry.resolve_delete_scope(ctx)

        assert scope.action == DeleteAction.FILTERED_DELETE
        assert scope.collection_names == ["records"]
        assert scope.filter_field == "connectorIds"
        assert scope.filter_values == ["conn-1"]

    @pytest.mark.asyncio
    async def test_drop_is_downgraded_when_liveness_is_unproven(self):
        """No probe configured => the flag stays None => never drop.

        Dropping a collection another connector still writes to would delete
        that connector's vectors, so an unproven flag must read as unsafe.
        """
        strategy = _DroppingStrategy()
        registry = _make_registry(strategy=strategy)

        scope = await registry.resolve_delete_scope(
            DeleteContext(org_id="org-1", connector_id="conn-1")
        )

        assert scope.action == DeleteAction.FILTERED_DELETE
        # A drop scope carries no filter; the downgrade must supply one rather
        # than hand the executor a predicate-less delete.
        assert scope.filter_field == "connectorIds"
        assert scope.filter_values == ["conn-1"]
        assert scope.collection_names == ["google_drive_records"]

    @pytest.mark.asyncio
    async def test_drop_is_honoured_when_probe_proves_last_writer(self):
        strategy = _DroppingStrategy()
        probe = AsyncMock(return_value=True)
        registry = _make_registry(strategy=strategy, liveness_probe=probe)

        scope = await registry.resolve_delete_scope(
            DeleteContext(org_id="org-1", connector_id="conn-1")
        )

        assert scope.action == DeleteAction.DROP_COLLECTION
        assert strategy.seen_contexts[-1].is_last_writer_to_collection is True
        # The probe is asked about the sanitized collection the connector writes to.
        assert probe.await_args.args[1] == "records"

    @pytest.mark.asyncio
    async def test_drop_is_downgraded_when_probe_says_not_last_writer(self):
        registry = _make_registry(
            strategy=_DroppingStrategy(), liveness_probe=AsyncMock(return_value=False)
        )

        scope = await registry.resolve_delete_scope(
            DeleteContext(org_id="org-1", connector_id="conn-1")
        )

        assert scope.action == DeleteAction.FILTERED_DELETE

    @pytest.mark.asyncio
    async def test_probe_failure_degrades_to_filtered_delete(self):
        registry = _make_registry(
            strategy=_DroppingStrategy(),
            liveness_probe=AsyncMock(side_effect=Exception("graph down")),
        )

        scope = await registry.resolve_delete_scope(
            DeleteContext(org_id="org-1", connector_id="conn-1")
        )

        assert scope.action == DeleteAction.FILTERED_DELETE

    @pytest.mark.asyncio
    async def test_caller_supplied_flag_is_not_reprobed(self):
        probe = AsyncMock(return_value=False)
        registry = _make_registry(strategy=_DroppingStrategy(), liveness_probe=probe)

        scope = await registry.resolve_delete_scope(
            DeleteContext(
                org_id="org-1", connector_id="conn-1", is_last_writer_to_collection=True
            )
        )

        assert scope.action == DeleteAction.DROP_COLLECTION
        probe.assert_not_awaited()


# ---------------------------------------------------------------------------
# delete_collection
# ---------------------------------------------------------------------------


class TestDeleteCollection:
    @pytest.mark.asyncio
    async def test_drop_is_idempotent_on_missing_collection(self):
        vdb = AsyncMock()
        vdb.delete_collection = AsyncMock(side_effect=Exception("collection not found"))
        registry = _make_registry(vector_db_service=vdb)

        await registry.delete_collection("records")  # must not raise

    @pytest.mark.asyncio
    async def test_drop_reraises_unexpected_errors(self):
        vdb = AsyncMock()
        vdb.delete_collection = AsyncMock(side_effect=Exception("connection refused"))
        registry = _make_registry(vector_db_service=vdb)

        with pytest.raises(Exception, match="connection refused"):
            await registry.delete_collection("records")

    @pytest.mark.asyncio
    async def test_drop_again_is_noop_removes_from_manifest(self):
        vdb = AsyncMock()
        vdb.get_collection_info = AsyncMock(
            return_value=VectorCollectionInfo(name="records", exists=False)
        )
        registry = _make_registry(vector_db_service=vdb)
        await registry.ensure_collection(RecordContext(org_id="org-1"), embedding_size=768)
        assert len(await registry.list_managed_collections()) == 1

        await registry.delete_collection("records")

        assert await registry.list_managed_collections() == []


# ---------------------------------------------------------------------------
# recreate_all_collections
# ---------------------------------------------------------------------------


class TestRecreateAllCollections:
    @pytest.mark.asyncio
    async def test_drops_and_recreates_every_managed_collection(self):
        vdb = AsyncMock()
        vdb.get_collection_info = AsyncMock(
            return_value=VectorCollectionInfo(name="records", exists=False)
        )
        registry = _make_registry(vector_db_service=vdb)
        await registry.ensure_collection(RecordContext(org_id="org-1"), embedding_size=768)
        vdb.create_collection.reset_mock()

        recreated = await registry.recreate_all_collections(records_dimension=1024)

        assert recreated == ["records"]
        vdb.delete_collection.assert_awaited_once_with("records")
        vdb.create_collection.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_noop_when_nothing_managed_and_nothing_live(self):
        registry = _make_registry(vector_db_service=_make_vdb(exists=False))
        recreated = await registry.recreate_all_collections(records_dimension=1024)
        assert recreated == []

    @pytest.mark.asyncio
    async def test_adopts_and_rebuilds_a_pre_manifest_collection(self):
        """The upgrade path: a live collection created before the manifest existed.

        The manifest is only written by the indexing write path, so a
        deployment that upgraded into the registry has data and an empty
        manifest. Without adoption the model-change rebuild would drop
        nothing, leave the old-dimension collection in place, and wedge
        indexing on a dimension mismatch forever.
        """
        vdb = _make_vdb(exists=True, dimension=768)
        registry = _make_registry(vector_db_service=vdb)
        assert await registry.list_managed_collections() != []

        recreated = await registry.recreate_all_collections(records_dimension=1024)

        assert recreated == ["records"]
        vdb.delete_collection.assert_awaited_once_with("records")
        vdb.create_collection.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_adoption_survives_an_unreachable_vector_db(self):
        vdb = _make_vdb()
        vdb.get_collection_info = AsyncMock(side_effect=Exception("connection refused"))
        registry = _make_registry(vector_db_service=vdb)

        assert await registry.list_managed_collections() == []


# ---------------------------------------------------------------------------
# Provider collection-count advisory
# ---------------------------------------------------------------------------


class TestAdvisoryCeiling:
    """A soft guideline (shard budgets, per-index memory), not a hard limit.

    It is reached gradually as connectors are added, so refusing to create the
    collection would fail indexing for whichever record happened to arrive at
    the boundary. Warn, and keep the data flowing.
    """

    def _registry_with_ceiling(self, vdb, ceiling):
        from app.services.vector_db.collection_manifest import CollectionManifestStore
        from app.services.vector_db.strategies.per_connector_type import (
            PerConnectorTypeStrategy,
        )

        logger = MagicMock()
        return (
            CollectionRegistry(
                vector_db_service=vdb,
                strategy=PerConnectorTypeStrategy(),
                collection_config_factory=lambda size, sparse_idf=False: CollectionConfig(
                    embedding_size=size
                ),
                manifest_store=CollectionManifestStore(_make_config_service(), MagicMock()),
                logger=logger,
                max_collections_advisory=ceiling,
            ),
            logger,
        )

    @pytest.mark.asyncio
    async def test_warns_when_a_new_collection_crosses_the_ceiling(self):
        registry, logger = self._registry_with_ceiling(_make_vdb(), ceiling=1)

        await registry.ensure_collection(
            RecordContext(org_id="o", connector_name="SLACK"), 1024
        )
        await registry.ensure_collection(
            RecordContext(org_id="o", connector_name="JIRA"), 1024
        )

        assert any(
            "recommends" in str(c) for c in logger.warning.call_args_list
        ), logger.warning.call_args_list

    @pytest.mark.asyncio
    async def test_creates_the_collection_anyway(self):
        vdb = _make_vdb()
        registry, _ = self._registry_with_ceiling(vdb, ceiling=1)

        await registry.ensure_collection(
            RecordContext(org_id="o", connector_name="SLACK"), 1024
        )
        name = await registry.ensure_collection(
            RecordContext(org_id="o", connector_name="JIRA"), 1024
        )

        assert name == "jira_records"

    @pytest.mark.asyncio
    async def test_stays_quiet_below_the_ceiling(self):
        registry, logger = self._registry_with_ceiling(_make_vdb(), ceiling=100)

        await registry.ensure_collection(
            RecordContext(org_id="o", connector_name="SLACK"), 1024
        )

        assert not any(
            "recommends" in str(c) for c in logger.warning.call_args_list
        )

    @pytest.mark.asyncio
    async def test_no_ceiling_configured_never_warns(self):
        registry, logger = self._registry_with_ceiling(_make_vdb(), ceiling=None)

        await registry.ensure_collection(
            RecordContext(org_id="o", connector_name="SLACK"), 1024
        )

        assert not any(
            "recommends" in str(c) for c in logger.warning.call_args_list
        )
