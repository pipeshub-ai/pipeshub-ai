"""DataSourceEntitiesProcessor.has_stored_permissions: stored edges, and a read that fails."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)


class FakeStore:
    """Graph store contract: a failed edge read returns [] unless raise_on_error is set."""

    def __init__(self, edges: list[dict[str, Any]], *, edge_read_fails: bool = False, node: object = True) -> None:
        self.edges = edges
        self.edge_read_fails = edge_read_fails
        self.node = SimpleNamespace(id="n1") if node is True else node
        self.asked: list[tuple[str, str, bool]] = []

    async def get_record_group_by_external_id(self, connector_id: str, external_id: str) -> object:
        return self.node

    async def get_user_group_by_external_id(self, connector_id: str, external_id: str, *, raise_on_error: bool = False) -> object:
        assert raise_on_error, "a failed group lookup must raise"
        return self.node

    async def get_app_role_by_external_id(self, connector_id: str, external_id: str, *, raise_on_error: bool = False) -> object:
        assert raise_on_error, "a failed role lookup must raise"
        return self.node

    async def get_edges_to_node(self, node_id: str, edge_collection: str, *, raise_on_error: bool = False) -> list[dict[str, Any]]:
        self.asked.append((node_id, edge_collection, raise_on_error))
        if self.edge_read_fails:
            if raise_on_error:
                raise RuntimeError("edge read failed")
            return []
        return self.edges


def processor_over(store: FakeStore) -> DataSourceEntitiesProcessor:
    processor = DataSourceEntitiesProcessor.__new__(DataSourceEntitiesProcessor)

    class Provider:
        def transaction(self) -> Any:  # noqa: ANN401
            class Tx:
                async def __aenter__(self_inner) -> FakeStore:
                    return store

                async def __aexit__(self_inner, *_: object) -> None:
                    return None

            return Tx()

    processor.data_store_provider = Provider()
    processor.logger = MagicMock()
    return processor


@pytest.mark.parametrize(
    ("entity", "collection"),
    [
        ("record_group", CollectionNames.RECORD_GROUPS.value),
        ("user_group", CollectionNames.GROUPS.value),
        ("role", CollectionNames.ROLES.value),
    ],
)
async def test_stored_edges_count_as_stored_access(entity: str, collection: str) -> None:
    store = FakeStore([{"_from": "users/u1"}])
    assert await processor_over(store).has_stored_permissions(entity, "c1", "ext-1") is True
    assert store.asked == [(f"{collection}/n1", CollectionNames.PERMISSION.value, True)]


async def test_no_edges_or_no_node_is_nothing_stored() -> None:
    assert await processor_over(FakeStore([])).has_stored_permissions("record_group", "c1", "ext-1") is False
    assert await processor_over(FakeStore([], node=None)).has_stored_permissions("role", "c1", "ext-1") is False


async def test_a_kept_node_is_noted_as_seen_by_the_full_sync_in_progress() -> None:
    processor = processor_over(FakeStore([{"_from": "users/u1"}]))
    run = processor.begin_full_sync_run("c1")
    assert await processor.has_stored_permissions("record_group", "c1", "ext-1", keep=True) is True
    assert run.has(CollectionNames.RECORD_GROUPS.value, "n1")

    empty = processor_over(FakeStore([]))
    run = empty.begin_full_sync_run("c1")
    assert await empty.has_stored_permissions("record_group", "c1", "ext-1", keep=True) is False
    assert not run.has(CollectionNames.RECORD_GROUPS.value, "n1"), "nothing stored, nothing kept"


async def test_a_kept_node_keeps_the_groups_and_roles_its_edges_come_from() -> None:
    processor = processor_over(
        FakeStore([{"_from": "roles/r-old"}, {"from_collection": "groups", "from_id": "g1"}, {"_from": "users/u1"}])
    )
    run = processor.begin_full_sync_run("c1")
    assert await processor.has_stored_permissions("record_group", "c1", "ext-1", keep=True) is True
    assert run.has(CollectionNames.ROLES.value, "r-old")
    assert run.has(CollectionNames.GROUPS.value, "g1")


async def test_kept_records_are_noted_as_seen_by_the_full_sync_in_progress() -> None:
    processor = processor_over(FakeStore([]))
    processor.note_kept_records("c1", ["r1"])  # no full sync running: nothing to note
    run = processor.begin_full_sync_run("c1")
    processor.note_kept_records("c1", ["r1", "r2"])
    assert run.has(CollectionNames.RECORDS.value, "r1") and run.has(CollectionNames.RECORDS.value, "r2")


async def test_a_kept_record_group_is_noted_as_seen_by_the_full_sync_in_progress() -> None:
    processor = processor_over(FakeStore([]))
    await processor.note_kept_record_group("c1", "ext-1")  # no full sync running: nothing to note
    run = processor.begin_full_sync_run("c1")
    await processor.note_kept_record_group("c1", "ext-1")
    assert run.has(CollectionNames.RECORD_GROUPS.value, "n1")


async def test_an_edge_read_that_fails_raises_instead_of_reading_as_nothing_stored() -> None:
    store = FakeStore([], edge_read_fails=True)
    with pytest.raises(RuntimeError, match="edge read failed"):
        await processor_over(store).has_stored_permissions("record_group", "c1", "ext-1")
