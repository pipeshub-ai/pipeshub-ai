"""The duplicate user groups merge names ServiceNow only, runs once, and is retried
after a failure (SERVICENOW-08 / N4MISC-02)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.migrations.duplicate_user_groups_migration import (
    DuplicateUserGroupsMigrationService,
)


def _service(provider, done=False) -> tuple[DuplicateUserGroupsMigrationService, MagicMock]:
    config = MagicMock()
    config.get_config = AsyncMock(return_value={"done": True} if done else None)
    config.set_config = AsyncMock()
    return DuplicateUserGroupsMigrationService(provider, config, MagicMock()), config


@pytest.mark.asyncio
async def test_the_servicenow_copies_are_merged_and_the_flag_set() -> None:
    provider = MagicMock()
    provider.merge_duplicate_user_groups = AsyncMock(return_value={"groups": 619, "removed": 1857, "moved": 4})
    service, config = _service(provider)

    assert await service.migrate() == {"success": True, "groups": 619, "removed": 1857, "moved": 4}

    assert provider.merge_duplicate_user_groups.await_args.args == (["SERVICENOW"],)
    key, flag = config.set_config.await_args.args
    assert key == "/migrations/duplicate_user_groups_v1"
    assert flag["done"] is True and flag["removed"] == 1857


@pytest.mark.asyncio
async def test_a_failure_is_retried_next_startup() -> None:
    provider = MagicMock()
    provider.merge_duplicate_user_groups = AsyncMock(side_effect=RuntimeError("2 duplicated group(s) left"))
    service, config = _service(provider)
    assert (await service.migrate())["success"] is False
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_a_backend_without_it_is_skipped_and_not_flagged() -> None:
    provider = MagicMock()
    provider.merge_duplicate_user_groups = AsyncMock(side_effect=NotImplementedError)
    service, config = _service(provider)
    assert (await service.migrate())["unsupported"] is True
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_a_finished_migration_does_not_run_again() -> None:
    provider = MagicMock()
    provider.merge_duplicate_user_groups = AsyncMock()
    service, _ = _service(provider, done=True)
    assert (await service.migrate())["skipped"] is True
    provider.merge_duplicate_user_groups.assert_not_called()
