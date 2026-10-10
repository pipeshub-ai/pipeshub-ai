"""The hierarchy backfill runs once, is retried after a failure, and skips a
backend that cannot run it."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.migrations.hierarchy_backfill_migration import (
    HierarchyBackfillMigrationService,
    HierarchyNestedGroupRootsMigrationService,
)


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


# Stacks that ran the backfill before nested_group_roots existed never run it again,
# so the shape has a one-shot step of its own.

def _nested_service(
    provider, *, backfill_done: bool = True, nested_done: bool = False,
) -> tuple[HierarchyNestedGroupRootsMigrationService, MagicMock]:
    flags = {
        "/migrations/hierarchy_backfill_v1": {"done": True} if backfill_done else None,
        "/migrations/hierarchy_nested_group_roots_v1": {"done": True} if nested_done else None,
    }
    config = MagicMock()
    config.get_config = AsyncMock(side_effect=lambda key, *a, **k: flags.get(key))
    config.set_config = AsyncMock()
    return HierarchyNestedGroupRootsMigrationService(provider, config, MagicMock()), config


@pytest.mark.asyncio
async def test_the_nested_group_roots_step_runs_on_a_stack_that_already_ran_the_backfill() -> None:
    provider = MagicMock()
    provider.backfill_hierarchy = AsyncMock(return_value={"added": {"nested_group_roots": 363}})
    service, config = _nested_service(provider, backfill_done=True)

    assert await service.migrate() == {"success": True, "added": {"nested_group_roots": 363}}

    provider.backfill_hierarchy.assert_awaited_once_with(shapes=["nested_group_roots"])
    key, flag = config.set_config.await_args.args
    assert key == "/migrations/hierarchy_nested_group_roots_v1"
    assert flag["done"] is True and flag["added"] == {"nested_group_roots": 363}


@pytest.mark.asyncio
async def test_the_nested_group_roots_step_runs_once() -> None:
    provider = MagicMock()
    provider.backfill_hierarchy = AsyncMock()
    service, config = _nested_service(provider, nested_done=True)

    assert (await service.migrate())["skipped"] is True
    provider.backfill_hierarchy.assert_not_called()
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_a_failed_nested_group_roots_step_is_not_flagged() -> None:
    provider = MagicMock()
    provider.backfill_hierarchy = AsyncMock(side_effect=RuntimeError("Hierarchy backfill left 2 edge(s)"))
    service, config = _nested_service(provider)

    assert (await service.migrate())["success"] is False
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_the_full_backfill_still_runs_every_shape() -> None:
    provider = MagicMock()
    provider.backfill_hierarchy = AsyncMock(return_value={"added": ADDED})
    service, _ = _service(provider)
    await service.migrate()
    provider.backfill_hierarchy.assert_awaited_once_with()
