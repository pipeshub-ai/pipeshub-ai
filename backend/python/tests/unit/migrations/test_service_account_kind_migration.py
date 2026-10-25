"""Unit tests for service_account_kind_migration."""

import logging
from unittest.mock import AsyncMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.migrations.service_account_kind_migration import (
    MIGRATION_FLAG_KEY,
    run_service_account_kind_migration,
)


class FakeGraph:
    def __init__(self, users_by_org: dict[str, list[dict]]) -> None:
        self.orgs = [{"_key": org_id, "id": org_id} for org_id in users_by_org]
        self.users_by_org = users_by_org
        self.upserted: list[dict] = []

    async def get_all_orgs(self, active: bool = False) -> list[dict]:
        return self.orgs

    async def get_users(self, org_id: str, active: bool = False) -> list[dict]:
        return self.users_by_org.get(org_id, [])

    async def batch_upsert_nodes(self, nodes: list[dict], collection: str) -> bool:
        assert collection == CollectionNames.USERS.value
        self.upserted.extend(nodes)
        return True


class FakeConfig:
    def __init__(self, initial_values: dict[str, object] | None = None) -> None:
        self.values: dict[str, object] = initial_values or {}

    async def get_config(self, key: str, **_: object) -> object:
        return self.values.get(key)

    async def set_config(self, key: str, value: object) -> None:
        self.values[key] = value


@pytest.mark.asyncio
async def test_migrates_service_account_without_kind() -> None:
    users = {
        "org-1": [
            {"_key": "u1", "email": "alice@example.com", "kind": None},
            {"_key": "sa1", "email": "bot@service.pipeshub.internal"},
            {
                "_key": "sa2",
                "email": "already-done@service.pipeshub.internal",
                "kind": "service",
            },
        ]
    }
    graph = FakeGraph(users)
    config = FakeConfig()
    logger = logging.getLogger("test")

    result = await run_service_account_kind_migration(graph, config, logger)

    assert result["success"] is True
    assert result["users_updated"] == 1
    assert result["skipped"] is False
    assert len(graph.upserted) == 1
    assert graph.upserted[0]["id"] == "sa1"
    assert graph.upserted[0]["kind"] == "service"
    assert config.values.get(MIGRATION_FLAG_KEY, {}).get("done") is True


@pytest.mark.asyncio
async def test_skips_when_already_done() -> None:
    graph = FakeGraph(
        {"org-1": [{"_key": "sa1", "email": "bot@service.pipeshub.internal"}]}
    )
    config = FakeConfig({MIGRATION_FLAG_KEY: {"done": True}})
    logger = logging.getLogger("test")

    result = await run_service_account_kind_migration(graph, config, logger)

    assert result["success"] is True
    assert result["skipped"] is True
    assert result["users_updated"] == 0
    assert len(graph.upserted) == 0


@pytest.mark.asyncio
async def test_error_leaves_flag_unset() -> None:
    graph = FakeGraph(
        {"org-1": [{"_key": "sa1", "email": "bot@service.pipeshub.internal"}]}
    )
    graph.batch_upsert_nodes = AsyncMock(side_effect=RuntimeError("db error"))
    config = FakeConfig()
    logger = logging.getLogger("test")

    result = await run_service_account_kind_migration(graph, config, logger)

    assert result["success"] is False
    assert "db error" in result.get("error", "")
    assert MIGRATION_FLAG_KEY not in config.values


@pytest.mark.asyncio
async def test_preserves_explicit_kind_classification() -> None:
    users = {
        "org-1": [
            {
                "_key": "sa-ind",
                "email": "human-service@service.pipeshub.internal",
                "kind": "individual",
            },
            {
                "_key": "sa-bot",
                "email": "bot@service.pipeshub.internal",
            },
        ]
    }
    graph = FakeGraph(users)
    config = FakeConfig()
    logger = logging.getLogger("test")

    result = await run_service_account_kind_migration(graph, config, logger)

    assert result["success"] is True
    assert result["users_updated"] == 1
    assert len(graph.upserted) == 1
    assert graph.upserted[0]["id"] == "sa-bot"
    assert config.values.get(MIGRATION_FLAG_KEY, {}).get("done") is True


@pytest.mark.asyncio
async def test_batch_upsert_failure_leaves_flag_unset() -> None:
    graph = FakeGraph(
        {"org-1": [{"_key": "sa1", "email": "bot@service.pipeshub.internal"}]}
    )
    graph.batch_upsert_nodes = AsyncMock(return_value=False)
    config = FakeConfig()
    logger = logging.getLogger("test")

    result = await run_service_account_kind_migration(graph, config, logger)

    assert result["success"] is False
    assert result["users_updated"] == 0
    assert result.get("failures") == 1
    assert MIGRATION_FLAG_KEY not in config.values
