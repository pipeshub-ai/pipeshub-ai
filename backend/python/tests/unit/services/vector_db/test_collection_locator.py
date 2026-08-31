"""Unit tests for VirtualRecordCollectionLocator.

The locator is what lets membership and VRID-scoped deletes work under a
strategy whose naming depends on record context: it maps the graph documents
that membership resolution *already fetched* onto collections, so nothing has
to invent "the" collection from an empty context.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.vector_db.collection_locator import (
    StaticCollectionLocator,
    UnresolvableVirtualRecord,
    VirtualRecordCollectionLocator,
)
from app.services.vector_db.collection_manifest import (
    CollectionManifestStore,
    ManagedCollection,
)
from app.services.vector_db.collections import CollectionType
from app.services.vector_db.membership import CollectionLocator
from app.services.vector_db.strategies.single import SingleCollectionStrategy
from app.services.vector_db.strategies.per_connector_type import (
    PerConnectorTypeStrategy,
)
from tests.unit.services.vector_db.test_strategy_contract import (
    PerOrgStrategy,
)


def _manifest_store(entries=()) -> CollectionManifestStore:
    data: dict = {}

    async def get_config(key, default=None):
        return data.get(key, default)

    async def set_config(key, value):
        data[key] = value

    svc = MagicMock()
    svc.get_config = AsyncMock(side_effect=get_config)
    svc.set_config = AsyncMock(side_effect=set_config)
    store = CollectionManifestStore(svc, MagicMock())
    store._cache = {e.name: e for e in entries}
    store._cached_at = float("inf")
    return store


def _locator(strategy, entries=()) -> VirtualRecordCollectionLocator:
    return VirtualRecordCollectionLocator(
        strategy=strategy, manifest_store=_manifest_store(entries), logger=MagicMock()
    )


def _doc(org="org-1", connector="conn-1", name: str | None = "GOOGLE_DRIVE") -> dict:
    return {"orgId": org, "connectorId": connector, "connectorName": name}


class TestSatisfiesTheProtocol:
    def test_virtual_record_locator_is_a_collection_locator(self):
        assert isinstance(_locator(SingleCollectionStrategy()), CollectionLocator)

    def test_static_locator_is_a_collection_locator(self):
        assert isinstance(StaticCollectionLocator(["records"]), CollectionLocator)


class TestUnderSingleStrategy:
    def test_every_record_maps_to_the_one_collection(self):
        locator = _locator(SingleCollectionStrategy())
        names = locator.collections_for_records(
            [_doc(name="GOOGLE_DRIVE"), _doc(name="SLACK"), _doc(org="org-2")]
        )
        assert names == ["records"]

    def test_no_records_yields_no_collections(self):
        assert _locator(SingleCollectionStrategy()).collections_for_records([]) == []


class TestUnderAContextDependentStrategy:
    def test_records_in_two_collections_resolve_to_both(self):
        """The case that makes membership fan out: one VRID, two connectors."""
        locator = _locator(PerConnectorTypeStrategy())
        names = locator.collections_for_records(
            [_doc(name="GOOGLE_DRIVE"), _doc(name="SLACK")]
        )
        assert set(names) == {"google_drive_records", "slack_records"}

    def test_duplicates_are_collapsed_preserving_first_seen_order(self):
        locator = _locator(PerConnectorTypeStrategy())
        names = locator.collections_for_records(
            [_doc(name="SLACK"), _doc(name="GOOGLE_DRIVE"), _doc(name="SLACK")]
        )
        assert names == ["slack_records", "google_drive_records"]

    def test_per_org_records_resolve_per_org(self):
        locator = _locator(PerOrgStrategy())
        names = locator.collections_for_records([_doc(org="a"), _doc(org="b")])
        assert set(names) == {"org_a_records", "org_b_records"}

    def test_a_record_missing_the_required_axis_is_skipped_not_fatal(self):
        """One malformed record must not cost the other records their
        membership rewrite."""
        locator = _locator(PerConnectorTypeStrategy())
        names = locator.collections_for_records(
            [_doc(name=None), _doc(name="SLACK")]
        )
        assert names == ["slack_records"]

    def test_no_record_resolving_raises_rather_than_writing_nowhere(self):
        """Returning [] here would report success having written to nothing —
        the silent-miss this whole design exists to eliminate."""
        locator = _locator(PerConnectorTypeStrategy())
        with pytest.raises(UnresolvableVirtualRecord):
            locator.collections_for_records([_doc(name=None), _doc(name="")])

    def test_resolution_is_pure_and_does_no_io(self):
        """Membership already fetched these documents; resolving must not add a
        round trip, which is the entire justification for this design."""
        store = _manifest_store()
        locator = VirtualRecordCollectionLocator(
            PerConnectorTypeStrategy(), store, MagicMock()
        )

        locator.collections_for_records([_doc(name="SLACK")])

        store._config_service.get_config.assert_not_called()


class TestAllCollections:
    @pytest.mark.asyncio
    async def test_returns_managed_collections_of_this_type(self):
        entries = [
            ManagedCollection("drive_records", "records", 1024, "s"),
            ManagedCollection("slack_records", "records", 1024, "s"),
            ManagedCollection("entities", "entities", 512, "s"),
        ]
        locator = _locator(PerConnectorTypeStrategy(), entries)

        assert set(await locator.all_collections()) == {
            "drive_records",
            "slack_records",
        }

    @pytest.mark.asyncio
    async def test_entities_locator_sees_only_entities(self):
        entries = [
            ManagedCollection("records", "records", 1024, "s"),
            ManagedCollection("entities", "entities", 512, "s"),
        ]
        locator = VirtualRecordCollectionLocator(
            SingleCollectionStrategy(),
            _manifest_store(entries),
            MagicMock(),
            collection_type=CollectionType.ENTITIES,
        )

        assert await locator.all_collections() == ["entities"]

    @pytest.mark.asyncio
    async def test_empty_manifest_yields_nothing(self):
        assert await _locator(SingleCollectionStrategy()).all_collections() == []


class TestStaticLocator:
    def test_sanitizes_its_names(self):
        locator = StaticCollectionLocator(["Records", "_Entities"])
        assert locator.collections_for_records([]) == ["records", "entities"]

    @pytest.mark.asyncio
    async def test_all_collections_matches_per_record(self):
        locator = StaticCollectionLocator(["records"])
        assert list(await locator.all_collections()) == locator.collections_for_records(
            [_doc()]
        )
