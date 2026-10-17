"""The App orgId backfill: runs once, is retried after a failure, and skips a
backend that cannot run it."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.migrations.app_org_id_migration import AppOrgIdMigrationService


def _service(provider, done=False) -> tuple[AppOrgIdMigrationService, MagicMock]:
    config = MagicMock()
    config.get_config = AsyncMock(return_value={"done": True} if done else None)
    config.set_config = AsyncMock()
    return AppOrgIdMigrationService(provider, config, MagicMock()), config


@pytest.mark.asyncio
async def test_the_flag_is_set_after_a_successful_run() -> None:
    provider = MagicMock()
    provider.backfill_app_org_ids = AsyncMock(return_value={"backfilled": 3, "ambiguous": 1})
    service, config = _service(provider)
    assert await service.migrate() == {"success": True, "backfilled": 3, "ambiguous": 1}
    assert config.set_config.await_args.args[0] == "/migrations/app_org_id_v1"


@pytest.mark.asyncio
async def test_a_failure_is_retried_next_startup() -> None:
    provider = MagicMock()
    provider.backfill_app_org_ids = AsyncMock(side_effect=RuntimeError("down"))
    service, config = _service(provider)
    assert (await service.migrate())["success"] is False
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_a_backend_without_it_is_skipped_and_not_flagged() -> None:
    provider = MagicMock()
    provider.backfill_app_org_ids = AsyncMock(side_effect=NotImplementedError)
    service, config = _service(provider)
    assert (await service.migrate())["unsupported"] is True
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_a_finished_migration_does_not_run_again() -> None:
    provider = MagicMock()
    provider.backfill_app_org_ids = AsyncMock()
    service, _ = _service(provider, done=True)
    assert (await service.migrate())["skipped"] is True
    provider.backfill_app_org_ids.assert_not_called()
