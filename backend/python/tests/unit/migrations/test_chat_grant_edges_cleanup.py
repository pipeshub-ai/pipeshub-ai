from unittest.mock import AsyncMock, MagicMock

import pytest

from app.migrations.chat_grant_edges_cleanup_migration import (
    ChatGrantEdgesCleanupMigrationService,
    run_chat_grant_edges_cleanup_migration,
)

FLAG = "/migrations/chat_grant_edges_cleanup_v1"


def _config(flags: dict) -> MagicMock:
    config = MagicMock()
    config.get_config = AsyncMock(side_effect=lambda key, *a, **k: flags.get(key))
    config.set_config = AsyncMock()
    return config


def _provider(result: int = 3, error: Exception | None = None) -> MagicMock:
    provider = MagicMock()
    provider.delete_chat_content_reader_edges = AsyncMock(side_effect=error, return_value=result)
    return provider


@pytest.mark.asyncio
async def test_flag_set_makes_no_provider_call() -> None:
    provider = _provider()
    config = _config({FLAG: {"done": True}})

    result = await run_chat_grant_edges_cleanup_migration(provider, config, MagicMock())

    assert result == {"success": True, "skipped": True, "deleted": 0}
    provider.delete_chat_content_reader_edges.assert_not_called()
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_provider_failure_leaves_flag_unset() -> None:
    provider = _provider(error=RuntimeError("boom"))
    config = _config({})

    result = await run_chat_grant_edges_cleanup_migration(provider, config, MagicMock())

    assert result["success"] is False and "boom" in result["error"]
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_success_sets_flag_with_count_and_second_run_skips() -> None:
    flags: dict = {}
    provider = _provider(result=7)
    config = _config(flags)
    config.set_config = AsyncMock(side_effect=lambda key, value: flags.__setitem__(key, value))

    first = await run_chat_grant_edges_cleanup_migration(provider, config, MagicMock())
    assert first == {"success": True, "skipped": False, "deleted": 7}
    assert flags[FLAG]["done"] is True and flags[FLAG]["deleted"] == 7

    second = await run_chat_grant_edges_cleanup_migration(provider, config, MagicMock())
    assert second["skipped"] is True and second["deleted"] == 0
    provider.delete_chat_content_reader_edges.assert_awaited_once_with(1000)


@pytest.mark.asyncio
async def test_unreadable_flag_is_treated_as_not_done() -> None:
    provider = _provider(result=0)
    config = MagicMock()
    config.get_config = AsyncMock(side_effect=RuntimeError("kv down"))
    config.set_config = AsyncMock()

    result = await ChatGrantEdgesCleanupMigrationService(provider, config, MagicMock(), batch_size=5).migrate()

    assert result["success"] is True
    provider.delete_chat_content_reader_edges.assert_awaited_once_with(5)
    config.set_config.assert_awaited_once()


@pytest.mark.asyncio
async def test_flag_write_failure_still_reports_success() -> None:
    provider = _provider(result=2)
    config = _config({})
    config.set_config = AsyncMock(side_effect=RuntimeError("kv down"))

    result = await run_chat_grant_edges_cleanup_migration(provider, config, MagicMock())

    assert result["success"] is True and result["deleted"] == 2
