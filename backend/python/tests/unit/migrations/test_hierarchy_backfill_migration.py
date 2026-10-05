"""The hierarchy backfill runs once, is retried after a failure, and skips a
backend that cannot run it."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.migrations.hierarchy_backfill_migration import HierarchyBackfillMigrationService


def _service(provider, done=False) -> tuple[HierarchyBackfillMigrationService, MagicMock]:
    config = MagicMock()
    config.get_config = AsyncMock(return_value={"done": True} if done else None)
    config.set_config = AsyncMock()
    return HierarchyBackfillMigrationService(provider, config, MagicMock()), config


ADDED = {
    "collection_roots": 1, "groups": 2, "group_roots": 3, "nested_inheritance": 4, "cross_group_children": 5,
}


@pytest.mark.asyncio
async def test_the_flag_is_set_after_a_successful_run() -> None:
    provider = MagicMock()
    provider.backfill_hierarchy = AsyncMock(return_value={"added": ADDED})
    service, config = _service(provider)
    assert await service.migrate() == {"success": True, "added": ADDED}
    key, flag = config.set_config.await_args.args
    assert key == "/migrations/hierarchy_backfill_v1"
    assert flag["done"] is True and flag["added"] == ADDED


@pytest.mark.asyncio
async def test_a_failure_is_retried_next_startup() -> None:
    provider = MagicMock()
    provider.backfill_hierarchy = AsyncMock(side_effect=RuntimeError("3 left"))
    service, config = _service(provider)
    assert (await service.migrate())["success"] is False
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_a_backend_without_it_is_skipped_and_not_flagged() -> None:
    provider = MagicMock()
    provider.backfill_hierarchy = AsyncMock(side_effect=NotImplementedError)
    service, config = _service(provider)
    assert (await service.migrate())["unsupported"] is True
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_a_finished_migration_does_not_run_again() -> None:
    provider = MagicMock()
    provider.backfill_hierarchy = AsyncMock()
    service, _ = _service(provider, done=True)
    assert (await service.migrate())["skipped"] is True
    provider.backfill_hierarchy.assert_not_called()
