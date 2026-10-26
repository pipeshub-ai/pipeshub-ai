"""The redundant group edges migration runs once, is retried after a failure,
and skips a backend that cannot run it."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.migrations.redundant_group_edges_migration import (
    RedundantGroupEdgesMigrationService,
)


def _service(provider: MagicMock, done: bool = False) -> tuple[RedundantGroupEdgesMigrationService, MagicMock]:
    config = MagicMock()
    config.get_config = AsyncMock(return_value={"done": True} if done else None)
    config.set_config = AsyncMock()
    return RedundantGroupEdgesMigrationService(provider, config, MagicMock()), config


@pytest.mark.asyncio
async def test_the_flag_is_set_after_a_successful_run() -> None:
    provider = MagicMock()
    provider.drop_redundant_group_edges = AsyncMock(return_value={"removed": 7})
    service, config = _service(provider)
    assert await service.migrate() == {"success": True, "removed": 7}
    key, flag = config.set_config.await_args.args
    assert key == "/migrations/redundant_group_edges_v1"
    assert flag["done"] is True and flag["removed"] == 7


@pytest.mark.asyncio
async def test_a_failure_is_retried_next_startup() -> None:
    provider = MagicMock()
    provider.drop_redundant_group_edges = AsyncMock(side_effect=RuntimeError("3 left"))
    service, config = _service(provider)
    assert (await service.migrate())["success"] is False
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_a_backend_without_it_is_skipped_and_not_flagged() -> None:
    provider = MagicMock()
    provider.drop_redundant_group_edges = AsyncMock(side_effect=NotImplementedError)
    service, config = _service(provider)
    assert (await service.migrate())["unsupported"] is True
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_a_finished_migration_does_not_run_again() -> None:
    provider = MagicMock()
    provider.drop_redundant_group_edges = AsyncMock()
    service, _ = _service(provider, done=True)
    assert (await service.migrate())["skipped"] is True
    provider.drop_redundant_group_edges.assert_not_called()
