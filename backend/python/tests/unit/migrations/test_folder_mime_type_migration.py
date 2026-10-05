"""The folder mimeType migration: runs once, is retried after a failure, and
skips a backend that cannot run it."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.migrations.folder_mime_type_migration import FolderMimeTypeMigrationService


def _service(provider, done=False) -> tuple[FolderMimeTypeMigrationService, MagicMock]:
    config = MagicMock()
    config.get_config = AsyncMock(return_value={"done": True} if done else None)
    config.set_config = AsyncMock()
    return FolderMimeTypeMigrationService(provider, config, MagicMock()), config


@pytest.mark.asyncio
async def test_the_flag_is_set_after_a_successful_run() -> None:
    provider = MagicMock()
    provider.normalize_folder_mime_types = AsyncMock(return_value={"normalized": 7})
    service, config = _service(provider)
    assert await service.migrate() == {"success": True, "normalized": 7}
    assert config.set_config.await_args.args[0] == "/migrations/folder_mime_type_v1"


@pytest.mark.asyncio
async def test_a_failure_or_a_partial_run_is_retried_next_startup() -> None:
    provider = MagicMock()
    provider.normalize_folder_mime_types = AsyncMock(side_effect=RuntimeError("2 folders left"))
    service, config = _service(provider)
    assert (await service.migrate())["success"] is False
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_a_backend_without_it_is_skipped_and_not_flagged() -> None:
    provider = MagicMock()
    provider.normalize_folder_mime_types = AsyncMock(side_effect=NotImplementedError)
    service, config = _service(provider)
    assert (await service.migrate())["unsupported"] is True
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_a_finished_migration_does_not_run_again() -> None:
    provider = MagicMock()
    provider.normalize_folder_mime_types = AsyncMock()
    service, _ = _service(provider, done=True)
    assert (await service.migrate())["skipped"] is True
    provider.normalize_folder_mime_types.assert_not_called()
