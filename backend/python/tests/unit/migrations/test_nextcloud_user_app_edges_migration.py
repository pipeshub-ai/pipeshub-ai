"""The startup migration that takes away user-to-app edges earlier Nextcloud syncs gave other users."""

import logging
from typing import Any

import pytest

from app.config.constants.arangodb import CollectionNames
from app.migrations.nextcloud_user_app_edges_migration import (
    NextcloudUserAppEdgesMigrationService,
)


class FakeGraph:
    """Apps, users and USER_APP_RELATION edges, answering in Neo4j's or Arango's edge shape."""

    def __init__(self, shape: str) -> None:
        self.shape = shape
        self.apps = [
            {"_key": "nc-1", "type": "Nextcloud", "createdBy": "mongo-alice"},
            {"_key": "nc-2", "type": "Nextcloud", "createdBy": "mongo-carol"},
        ]
        self.users = {"mongo-alice": {"_key": "u-alice"}, "mongo-carol": {"_key": "u-carol"}}
        self.edges = {("u-alice", "nc-1"), ("u-bob", "nc-1"), ("u-carol", "nc-2"), ("u-bob", "drive-1")}
        self.deleted: list[tuple[str, str]] = []

    async def get_nodes_by_filters(self, collection: str, filters: dict[str, Any], **_: object) -> list[dict]:
        assert collection == CollectionNames.APPS.value
        return [a for a in self.apps if all(a.get(k) == v for k, v in filters.items())]

    async def get_user_by_user_id(self, user_id: str, **_: object) -> dict | None:
        return self.users.get(user_id)

    async def get_edges_to_node(self, node_id: str, edge_collection: str, **_: object) -> list[dict]:
        assert edge_collection == CollectionNames.USER_APP_RELATION.value
        app = node_id.split("/", 1)[1]
        users = sorted(u for u, a in self.edges if a == app)
        if self.shape == "neo4j":
            return [{"from_id": u, "from_collection": "users", "to_id": app} for u in users]
        return [{"_from": f"users/{u}", "_to": f"apps/{app}"} for u in users]

    async def delete_edge(self, from_id: str, from_collection: str, to_id: str, to_collection: str,
                          collection: str, **_: object) -> bool:
        assert (from_collection, to_collection, collection) == ("users", "apps", CollectionNames.USER_APP_RELATION.value)
        self.edges.discard((from_id, to_id))
        self.deleted.append((from_id, to_id))
        return True


class FakeConfig:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def get_config(self, key: str, **_: object) -> Any:  # noqa: ANN401
        return self.values.get(key)

    async def set_config(self, key: str, value: Any) -> None:  # noqa: ANN401
        self.values[key] = value


@pytest.mark.parametrize("shape", ["neo4j", "arango"])
async def test_only_the_creators_edge_into_each_nextcloud_app_is_kept(shape: str) -> None:
    graph, config = FakeGraph(shape), FakeConfig()

    result = await NextcloudUserAppEdgesMigrationService(graph, config, logging.getLogger("t")).migrate()

    assert result == {"success": True, "removed": 1}
    assert graph.deleted == [("u-bob", "nc-1")]
    assert graph.edges == {("u-alice", "nc-1"), ("u-carol", "nc-2"), ("u-bob", "drive-1")}, "other apps untouched"
    assert config.values[NextcloudUserAppEdgesMigrationService.MIGRATION_FLAG_KEY]["done"] is True


async def test_a_second_startup_does_nothing() -> None:
    graph, config = FakeGraph("neo4j"), FakeConfig()
    service = NextcloudUserAppEdgesMigrationService(graph, config, logging.getLogger("t"))
    await service.migrate()
    graph.edges.add(("u-bob", "nc-1"))

    assert (await service.migrate())["skipped"] is True
    assert ("u-bob", "nc-1") in graph.edges


async def test_an_app_whose_creator_is_unknown_keeps_its_edges_and_retries_next_startup() -> None:
    graph, config = FakeGraph("arango"), FakeConfig()
    del graph.users["mongo-alice"]

    result = await NextcloudUserAppEdgesMigrationService(graph, config, logging.getLogger("t")).migrate()

    assert result["success"] is False and result["unresolved"] == ["nc-1"]
    assert ("u-bob", "nc-1") in graph.edges, "nothing is removed without knowing who may keep access"
    assert NextcloudUserAppEdgesMigrationService.MIGRATION_FLAG_KEY not in config.values


async def test_a_failed_read_leaves_the_flag_unset() -> None:
    graph, config = FakeGraph("neo4j"), FakeConfig()

    async def down(*_: object, **__: object) -> list[dict]:
        raise RuntimeError("graph down")

    graph.get_nodes_by_filters = down

    result = await NextcloudUserAppEdgesMigrationService(graph, config, logging.getLogger("t")).migrate()

    assert result["success"] is False
    assert config.values == {}
