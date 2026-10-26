"""The taxonomy entities a deleted knowledge-base record linked to, as its
``deleteRecord`` names them (``taxonomyRefs``), so the indexing service can
delete the entity points no other record reaches."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.models.entities import EntityType
from app.modules.entity_resolution.models import KINDS_BY_COLLECTION
from app.services.graph_db.common.utils import (
    cleans_taxonomy_points,
    deleted_record_taxonomy_refs,
    record_taxonomy_refs,
)
from app.services.graph_db.taxonomy import (
    TAXONOMY_COLLECTIONS,
    TAXONOMY_ENTITY_TYPE_BY_COLLECTION,
)

KB_FILE = {"_key": "r1", "origin": "UPLOAD", "connectorName": "KB", "virtualRecordId": "vr1"}


def _link(record_id: str, collection: str, entity_id: str) -> dict:
    return {"recordId": record_id, "collection": collection, "entityId": entity_id, "name": entity_id}


def _graph(links: list[dict] | Exception) -> MagicMock:
    graph = MagicMock()
    graph.get_record_taxonomy_links = AsyncMock(
        side_effect=links if isinstance(links, Exception) else None,
        return_value=None if isinstance(links, Exception) else links,
    )
    return graph


def test_every_taxonomy_collection_maps_to_the_type_its_points_are_written_with() -> None:
    departments = CollectionNames.DEPARTMENTS.value
    assert set(TAXONOMY_ENTITY_TYPE_BY_COLLECTION) == set(TAXONOMY_COLLECTIONS) | {departments}
    for collection, kind in KINDS_BY_COLLECTION.items():
        assert TAXONOMY_ENTITY_TYPE_BY_COLLECTION[collection] == kind.entity_type.value
    assert TAXONOMY_ENTITY_TYPE_BY_COLLECTION[departments] == EntityType.DEPARTMENT.value


@pytest.mark.parametrize(
    "record, cleans",
    [
        (KB_FILE, True),
        ({**KB_FILE, "connectorName": None}, True),  # an upload from before connectorName was stored
        ({**KB_FILE, "origin": "CONNECTOR"}, True),
        ({**KB_FILE, "origin": "CONNECTOR", "connectorName": "DRIVE"}, False),
        ({**KB_FILE, "virtualRecordId": None}, False),  # a folder: no vectors, no deleteRecord
    ],
    ids=["kb-file", "kb-legacy-upload", "kb-connector-name", "connector-record", "no-vectors"],
)
def test_only_knowledge_base_records_with_vectors_clean_taxonomy_points(record: dict, cleans: bool) -> None:
    assert cleans_taxonomy_points(record) is cleans


class TestRecordTaxonomyRefs:
    @pytest.mark.asyncio
    async def test_each_link_becomes_a_ref_of_its_point_type(self) -> None:
        graph = _graph([
            _link("r1", CollectionNames.CATEGORIES.value, "c1"),
            _link("r1", CollectionNames.SUBCATEGORIES1.value, "s1"),
            _link("r1", CollectionNames.SUBCATEGORIES3.value, "s3"),
            _link("r1", CollectionNames.TOPICS.value, "t1"),
            _link("r1", CollectionNames.LANGUAGES.value, "l1"),
            _link("r1", CollectionNames.DEPARTMENTS.value, "d1"),
            _link("r2", CollectionNames.TOPICS.value, "t1"),
        ])

        refs = await record_taxonomy_refs(graph, ["r1", "r2", "r1"], MagicMock())

        # Departments are read too: the default read leaves them out.
        graph.get_record_taxonomy_links.assert_awaited_once_with(["r1", "r2"], include_departments=True)
        assert refs == {
            "r1": [
                {"id": "c1", "type": "category"},
                {"id": "s1", "type": "subcategory"},
                {"id": "s3", "type": "subcategory"},
                {"id": "t1", "type": "topic"},
                {"id": "l1", "type": "language"},
                {"id": "d1", "type": "department"},
            ],
            "r2": [{"id": "t1", "type": "topic"}],
        }

    @pytest.mark.asyncio
    async def test_a_record_spelling_a_node_twice_names_it_once(self) -> None:
        graph = _graph([_link("r1", "topics", "t1"), _link("r1", "topics", "t1")])

        assert await record_taxonomy_refs(graph, ["r1"], MagicMock()) == {"r1": [{"id": "t1", "type": "topic"}]}

    @pytest.mark.asyncio
    async def test_rows_without_a_known_collection_or_ids_are_dropped(self) -> None:
        graph = _graph([
            _link("r1", "namedEntities", "n1"),
            _link("r1", "users", "u1"),
            {"recordId": "r1", "collection": "topics", "entityId": None},
            {"recordId": None, "collection": "topics", "entityId": "t1"},
        ])

        assert await record_taxonomy_refs(graph, ["r1"], MagicMock()) == {}

    @pytest.mark.asyncio
    async def test_no_keys_reads_nothing(self) -> None:
        graph = _graph([])

        assert await record_taxonomy_refs(graph, [None, ""], MagicMock()) == {}
        graph.get_record_taxonomy_links.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_read_is_logged_and_never_fails_the_delete(self) -> None:
        logger = MagicMock(spec=logging.Logger)

        refs = await record_taxonomy_refs(_graph(RuntimeError("graph down")), ["r1"], logger)

        assert refs == {}
        logger.warning.assert_called_once()


class TestDeletedRecordTaxonomyRefs:
    @pytest.mark.asyncio
    async def test_connector_records_and_records_without_vectors_are_not_read(self) -> None:
        graph = _graph([_link("r1", "topics", "t1")])
        records = [
            KB_FILE,
            {"_key": "drive-1", "origin": "CONNECTOR", "connectorName": "DRIVE", "virtualRecordId": "vr2"},
            {"_key": "folder-1", "origin": "UPLOAD", "connectorName": "KB"},
        ]

        refs = await deleted_record_taxonomy_refs(graph, records, MagicMock())

        graph.get_record_taxonomy_links.assert_awaited_once_with(["r1"], include_departments=True)
        assert refs == {"r1": [{"id": "t1", "type": "topic"}]}

    @pytest.mark.asyncio
    async def test_neo4j_records_are_keyed_by_id(self) -> None:
        graph = _graph([_link("r1", "topics", "t1")])
        record = {k: v for k, v in KB_FILE.items() if k != "_key"} | {"id": "r1"}

        assert await deleted_record_taxonomy_refs(graph, [record], MagicMock()) == {
            "r1": [{"id": "t1", "type": "topic"}],
        }

    @pytest.mark.asyncio
    async def test_a_delete_of_only_connector_records_reads_nothing(self) -> None:
        graph = _graph([])
        drive = {"_key": "d1", "origin": "CONNECTOR", "connectorName": "DRIVE", "virtualRecordId": "vr"}

        assert await deleted_record_taxonomy_refs(graph, [drive], MagicMock()) == {}
        graph.get_record_taxonomy_links.assert_not_awaited()
