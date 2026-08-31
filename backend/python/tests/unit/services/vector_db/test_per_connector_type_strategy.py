"""PerConnectorTypeStrategy — the behaviour specific to shipping it in OSS.

The generic contract (determinism, sanitization, `required_axes` honesty,
delete-scope safety) is covered for this strategy alongside every other in
`test_strategy_contract.py`. What is here is what only matters because this one
is selectable in production: the names real `Connectors` values produce, the
read fan-out when nothing can narrow it, and the reasons it never drops a
collection.
"""

import pytest

from app.config.constants.arangodb import Connectors
from app.services.vector_db.collection_manifest import ManagedCollection
from app.services.vector_db.collections import CollectionType, sanitize_collection_name
from app.services.vector_db.strategies.per_connector_type import (
    PerConnectorTypeStrategy,
)
from app.services.vector_db.strategy import (
    CollectionStrategyFactory,
    ContextAxis,
    DeleteAction,
    DeleteContext,
    IncompleteCollectionContext,
    QueryContext,
    RecordContext,
    resolve_write_collection_name,
)


@pytest.fixture
def strategy() -> PerConnectorTypeStrategy:
    return PerConnectorTypeStrategy()


def _managed(*names: str, collection_type: str = "records") -> list[ManagedCollection]:
    return [
        ManagedCollection(
            name=name,
            collection_type=collection_type,
            embedding_dimension=1024,
            strategy_name="per_connector_type",
        )
        for name in names
    ]


class TestSelectableFromConfig:
    def test_is_registered_under_its_persisted_name(self):
        from app.services.vector_db import strategies  # noqa: F401

        assert "per_connector_type" in CollectionStrategyFactory.registered_names()

    def test_factory_builds_it(self):
        from app.services.vector_db import strategies  # noqa: F401

        built = CollectionStrategyFactory.create("per_connector_type")
        assert isinstance(built, PerConnectorTypeStrategy)

    def test_persisted_name_round_trips(self, strategy):
        from app.services.vector_db import strategies  # noqa: F401

        assert isinstance(
            CollectionStrategyFactory.create(strategy.strategy_name()),
            PerConnectorTypeStrategy,
        )


class TestNamesRealConnectorsProduce:
    @pytest.mark.parametrize(
        "connector,expected",
        [
            # The enum *value*, not the member name: GOOGLE_DRIVE is "DRIVE".
            (Connectors.GOOGLE_DRIVE, "drive_records"),
            (Connectors.SLACK, "slack_records"),
            (Connectors.KNOWLEDGE_BASE, "kb_records"),
            # Values with spaces are what makes normalization load-bearing.
            (Connectors.SHAREPOINT_ONLINE, "sharepoint_online_records"),
            (Connectors.GOOGLE_DRIVE_WORKSPACE, "drive_workspace_records"),
        ],
    )
    def test_enum_values_map_to_readable_collections(
        self, strategy, connector, expected
    ):
        ctx = RecordContext.from_record(
            type("R", (), {"connector_id": "c1", "connector_name": connector})(),
            "org-1",
        )
        assert resolve_write_collection_name(strategy, ctx) == expected

    def test_every_connector_enum_value_yields_a_valid_name(self, strategy):
        """Any connector can be the first one indexed; none may produce a name
        the provider would reject."""
        for connector in Connectors:
            ctx = RecordContext(org_id="org-1", connector_name=connector.value)
            name = resolve_write_collection_name(strategy, ctx)
            assert name == sanitize_collection_name(name)
            assert name and not name.startswith(("_", "-", "+"))

    def test_every_connector_enum_value_yields_a_distinct_name(self, strategy):
        """Two connector types sharing a collection would make a per-type drop
        or re-index take the other's data with it."""
        names = {
            resolve_write_collection_name(
                strategy, RecordContext(org_id="o", connector_name=c.value)
            )
            for c in Connectors
        }
        assert len(names) == len(list(Connectors))

    def test_two_instances_of_one_type_share_a_collection(self, strategy):
        """The grouping is the type, not the instance — that is what keeps the
        collection count bounded by kinds of source rather than connections."""
        a = RecordContext(
            org_id="o", connector_id="inst-1", connector_name="GOOGLE_DRIVE"
        )
        b = RecordContext(
            org_id="o", connector_id="inst-2", connector_name="GOOGLE_DRIVE"
        )
        assert resolve_write_collection_name(
            strategy, a
        ) == resolve_write_collection_name(strategy, b)

    def test_org_does_not_affect_the_name(self, strategy):
        """It declares only CONNECTOR_NAME; isolation between orgs stays with
        the payload filters, as under `single`."""
        a = RecordContext(org_id="acme", connector_name="SLACK")
        b = RecordContext(org_id="globex", connector_name="SLACK")
        assert resolve_write_collection_name(
            strategy, a
        ) == resolve_write_collection_name(strategy, b)

    def test_entities_get_their_own_collection(self, strategy):
        ctx = RecordContext(
            org_id="o",
            connector_name="SLACK",
            collection_type=CollectionType.ENTITIES,
        )
        assert resolve_write_collection_name(strategy, ctx) == "slack_entities"

    def test_a_record_without_a_connector_name_is_refused(self, strategy):
        """`_records` would be a plausible-looking name that reads and deletes
        then silently miss."""
        with pytest.raises(IncompleteCollectionContext):
            resolve_write_collection_name(strategy, RecordContext(org_id="o"))

    def test_declares_only_the_axis_it_reads(self, strategy):
        assert strategy.required_axes == frozenset({ContextAxis.CONNECTOR_NAME})


class TestReadResolution:
    def test_narrows_when_the_caller_knows_the_connector_types(self, strategy):
        names = strategy.resolve_read_collections(
            QueryContext(
                org_id="o", accessible_connector_names=["GOOGLE DRIVE", "SLACK"]
            ),
            _managed("drive_records", "slack_records", "jira_records"),
        )
        assert names == ["google_drive_records", "slack_records"]

    def test_falls_back_to_every_managed_collection_of_this_type(self, strategy):
        """The common case today: the graph query returns no connector info, so
        the search fans out rather than guessing."""
        names = strategy.resolve_read_collections(
            QueryContext(org_id="o"),
            _managed("google_drive_records", "slack_records"),
        )
        assert set(names) == {"google_drive_records", "slack_records"}

    def test_fallback_does_not_cross_collection_types(self, strategy):
        managed = _managed("slack_records") + _managed(
            "slack_entities", collection_type="entities"
        )
        assert strategy.resolve_read_collections(
            QueryContext(org_id="o"), managed
        ) == ["slack_records"]

    def test_nothing_managed_resolves_nothing(self, strategy):
        """Never a fabricated name — an empty result means nowhere to search."""
        assert strategy.resolve_read_collections(QueryContext(org_id="o"), []) == []


class TestDeleteNeverDropsTheCollection:
    def test_filtered_delete_even_when_told_it_is_the_last_writer(self, strategy):
        """A second instance of the same connector type would lose its vectors,
        and nothing here can prove one does not exist."""
        scope = strategy.resolve_delete_scope(
            DeleteContext(
                org_id="o",
                connector_id="inst-1",
                connector_name="GOOGLE_DRIVE",
                is_last_writer_to_collection=True,
            )
        )
        assert scope.action == DeleteAction.FILTERED_DELETE

    def test_targets_only_the_connector_types_collection(self, strategy):
        scope = strategy.resolve_delete_scope(
            DeleteContext(org_id="o", connector_id="inst-1", connector_name="SLACK")
        )
        assert scope.collection_names == ["slack_records"]

    def test_filters_on_the_instance_not_the_type(self, strategy):
        """Deleting one Drive connection must leave the other's points alone."""
        scope = strategy.resolve_delete_scope(
            DeleteContext(
                org_id="o", connector_id="inst-1", connector_name="GOOGLE_DRIVE"
            )
        )
        assert scope.filter_values == ["inst-1"]

    def test_a_missing_connector_id_yields_no_predicate(self, strategy):
        """Which the executor refuses, rather than emptying the collection for
        every instance sharing it."""
        scope = strategy.resolve_delete_scope(
            DeleteContext(org_id="o", connector_id="", connector_name="SLACK")
        )
        assert not scope.filter_values
