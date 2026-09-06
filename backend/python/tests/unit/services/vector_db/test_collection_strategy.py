"""Unit tests for the collection strategy abstractions and the default
SingleCollectionStrategy implementation."""

import pytest

from app.services.vector_db.collections import CollectionType
from app.services.vector_db.strategies.single import SingleCollectionStrategy
from app.services.vector_db.strategy import (
    CollectionStrategy,
    CollectionStrategyFactory,
    DeleteAction,
    DeleteContext,
    QueryContext,
    RecordContext,
)

# ---------------------------------------------------------------------------
# SingleCollectionStrategy
# ---------------------------------------------------------------------------


class TestSingleCollectionStrategy:
    def test_write_resolves_to_records_by_default(self):
        strategy = SingleCollectionStrategy()
        ctx = RecordContext(org_id="org-1")
        assert strategy.resolve_write_collection(ctx) == "records"

    def test_write_ignores_connector_and_org(self):
        """Every context collapses to the same collection under 'single'."""
        strategy = SingleCollectionStrategy()
        ctx_a = RecordContext(org_id="org-1", connector_id="c1", connector_name="GOOGLE_DRIVE")
        ctx_b = RecordContext(org_id="org-2", connector_id="c2", connector_name="SLACK")
        assert (
            strategy.resolve_write_collection(ctx_a)
            == strategy.resolve_write_collection(ctx_b)
            == "records"
        )

    def test_entities_collection_type_resolves_independently(self):
        """Proves the dataset axis works before the entities pipeline exists."""
        strategy = SingleCollectionStrategy()
        ctx = RecordContext(org_id="org-1", collection_type=CollectionType.ENTITIES)
        assert strategy.resolve_write_collection(ctx) == "entities"

    def test_read_resolves_to_single_element_list(self):
        strategy = SingleCollectionStrategy()
        ctx = QueryContext(org_id="org-1")
        assert strategy.resolve_read_collections(ctx, []) == ["records"]

    def test_read_entities_type(self):
        strategy = SingleCollectionStrategy()
        ctx = QueryContext(org_id="org-1", collection_type=CollectionType.ENTITIES)
        assert strategy.resolve_read_collections(ctx, []) == ["entities"]

    def test_delete_scope_is_filtered_delete_on_connector_ids(self):
        strategy = SingleCollectionStrategy()
        ctx = DeleteContext(org_id="org-1", connector_id="conn-1")
        scope = strategy.resolve_delete_scope(ctx)
        assert scope.action == DeleteAction.FILTERED_DELETE
        assert scope.collection_names == ["records"]
        assert scope.filter_field == "connectorIds"
        assert scope.filter_values == ["conn-1"]

    def test_delete_scope_never_drops_under_single(self):
        """Single-collection deployments must never drop the shared collection
        for a single connector's deletion — it would take every other
        connector's data with it."""
        strategy = SingleCollectionStrategy()
        ctx = DeleteContext(
            org_id="org-1", connector_id="conn-1", is_last_writer_to_collection=True
        )
        scope = strategy.resolve_delete_scope(ctx)
        assert scope.action == DeleteAction.FILTERED_DELETE

    def test_strategy_name(self):
        assert SingleCollectionStrategy().strategy_name() == "single"

    def test_implements_collection_strategy_interface(self):
        assert isinstance(SingleCollectionStrategy(), CollectionStrategy)


# ---------------------------------------------------------------------------
# CollectionStrategyFactory
# ---------------------------------------------------------------------------


class TestCollectionStrategyFactory:
    def test_single_is_registered_by_default(self):
        # Importing app.services.vector_db.strategies registers "single" as a
        # side effect; the conftest/other tests importing it earlier is
        # exactly the behavior being verified here.
        from app.services.vector_db import strategies  # noqa: F401

        assert "single" in CollectionStrategyFactory.registered_names()

    def test_create_known_strategy(self):
        from app.services.vector_db import strategies  # noqa: F401

        strategy = CollectionStrategyFactory.create("single")
        assert isinstance(strategy, SingleCollectionStrategy)

    def test_create_unknown_strategy_raises_listing_registered_names(self):
        with pytest.raises(ValueError) as exc:
            CollectionStrategyFactory.create("does_not_exist")
        message = str(exc.value)
        assert "does_not_exist" in message
        assert "single" in message

    def test_register_makes_a_custom_strategy_resolvable(self):
        """Proves the EE plug-in path: a package registering its own strategy
        at import time makes it resolvable by name without touching this
        module."""

        class _FakeEEStrategy(CollectionStrategy):
            def resolve_write_collection(self, ctx: RecordContext) -> str:
                return f"org_{ctx.org_id}_records"

            def resolve_read_collections(self, ctx: QueryContext, managed) -> list:
                return [f"org_{ctx.org_id}_records"]

            def resolve_delete_scope(self, ctx: DeleteContext):
                raise NotImplementedError

            def strategy_name(self) -> str:
                return "fake_ee"

        CollectionStrategyFactory.register("fake_ee", _FakeEEStrategy)
        try:
            assert "fake_ee" in CollectionStrategyFactory.registered_names()
            strategy = CollectionStrategyFactory.create("fake_ee")
            assert isinstance(strategy, _FakeEEStrategy)
            assert (
                strategy.resolve_write_collection(RecordContext(org_id="acme"))
                == "org_acme_records"
            )
        finally:
            # Registration is process-global state; clean up after the test
            # so it doesn't leak into other tests' registered_names() checks.
            CollectionStrategyFactory._builders.pop("fake_ee", None)

    def test_registered_names_is_sorted(self):
        from app.services.vector_db import strategies  # noqa: F401

        CollectionStrategyFactory.register("zzz_test_only", lambda: SingleCollectionStrategy())
        try:
            names = CollectionStrategyFactory.registered_names()
            assert names == sorted(names)
        finally:
            CollectionStrategyFactory._builders.pop("zzz_test_only", None)


# ---------------------------------------------------------------------------
# Context dataclasses: defaults
# ---------------------------------------------------------------------------


class TestContextDefaults:
    def test_record_context_defaults(self):
        ctx = RecordContext(org_id="org-1")
        assert ctx.collection_type == CollectionType.RECORDS
        assert ctx.connector_id is None
        assert ctx.connector_name is None
        assert ctx.embedding_model is None
        assert ctx.embedding_dimension is None

    def test_query_context_defaults(self):
        ctx = QueryContext(org_id="org-1")
        assert ctx.collection_type == CollectionType.RECORDS
        assert ctx.accessible_connector_ids is None
        assert ctx.accessible_connector_names is None

    def test_delete_context_defaults(self):
        ctx = DeleteContext(org_id="org-1", connector_id="conn-1")
        assert ctx.collection_type == CollectionType.RECORDS
        assert ctx.connector_name is None
        assert ctx.is_last_writer_to_collection is None
