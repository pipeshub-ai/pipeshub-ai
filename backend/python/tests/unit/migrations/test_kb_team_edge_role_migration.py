from unittest.mock import AsyncMock, MagicMock

import pytest

from app.migrations.kb_team_edge_role_migration import (
    KBTeamEdgeRoleMigrationService,
    run_kb_team_edge_role_migration,
)

FLAG = "/migrations/kb_team_edge_role_v1"
PREREQ = "/migrations/kb_apps_v1"


def _config(flags: dict) -> MagicMock:
    config = MagicMock()
    config.get_config = AsyncMock(side_effect=lambda key, *a, **k: flags.get(key))
    config.set_config = AsyncMock()
    return config


def _provider(result=None, error: Exception | None = None) -> MagicMock:
    provider = MagicMock()
    provider.backfill_kb_team_edge_roles = AsyncMock(
        side_effect=error, return_value=result or {"stamped": 2, "remaining_role_less": 0}
    )
    return provider


@pytest.mark.asyncio
async def test_flag_done_skips_without_touching_provider() -> None:
    provider = _provider()
    config = _config({FLAG: {"done": True}, PREREQ: {"done": True}})

    result = await run_kb_team_edge_role_migration(provider, config, MagicMock())

    assert result["success"] is True and result["skipped"] is True
    provider.backfill_kb_team_edge_roles.assert_not_called()
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_prerequisite_pending_defers_and_does_not_set_flag() -> None:
    provider = _provider()
    config = _config({})

    result = await run_kb_team_edge_role_migration(provider, config, MagicMock())

    assert result["success"] is False and result["skipped"] is True
    assert result["reason"] == "kb_apps pending"
    provider.backfill_kb_team_edge_roles.assert_not_called()
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_prerequisite_flag_not_done_true_is_pending() -> None:
    provider = _provider()
    config = _config({PREREQ: {"done": False}})

    result = await run_kb_team_edge_role_migration(provider, config, MagicMock())

    assert result["skipped"] is True
    provider.backfill_kb_team_edge_roles.assert_not_called()


@pytest.mark.asyncio
async def test_success_sets_flag_once() -> None:
    provider = _provider({"stamped": 4, "remaining_role_less": 0})
    config = _config({PREREQ: {"done": True}})

    result = await run_kb_team_edge_role_migration(provider, config, MagicMock())

    assert result["success"] is True and result["skipped"] is False
    assert result["stamped"] == 4
    config.set_config.assert_awaited_once()
    key, value = config.set_config.call_args.args
    assert key == FLAG
    assert value["done"] is True and value["stamped"] == 4 and value["remaining_role_less"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining", [3, -1])
async def test_role_less_mixed_teams_do_not_block_flag(remaining: int) -> None:
    provider = _provider({"stamped": 1, "remaining_role_less": remaining})
    config = _config({PREREQ: {"done": True}})

    result = await run_kb_team_edge_role_migration(provider, config, MagicMock())

    assert result["success"] is True
    config.set_config.assert_awaited_once()
    assert config.set_config.call_args.args[1]["remaining_role_less"] == remaining


@pytest.mark.asyncio
async def test_provider_failure_does_not_set_flag_and_retries_next_boot() -> None:
    provider = _provider(error=RuntimeError("db down"))
    config = _config({PREREQ: {"done": True}})

    result = await run_kb_team_edge_role_migration(provider, config, MagicMock())

    assert result["success"] is False
    assert "db down" in result["error"]
    config.set_config.assert_not_called()

    provider.backfill_kb_team_edge_roles = AsyncMock(return_value={"stamped": 1, "remaining_role_less": 0})
    retry = await run_kb_team_edge_role_migration(provider, config, MagicMock())
    assert retry["success"] is True
    config.set_config.assert_awaited_once()


@pytest.mark.asyncio
async def test_flag_write_failure_does_not_fail_migration() -> None:
    config = _config({PREREQ: {"done": True}})
    config.set_config = AsyncMock(side_effect=RuntimeError("etcd down"))

    result = await KBTeamEdgeRoleMigrationService(_provider(), config, MagicMock()).migrate()

    assert result["success"] is True


@pytest.mark.asyncio
async def test_unreadable_flag_is_treated_as_not_done() -> None:
    config = MagicMock()
    config.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))
    config.set_config = AsyncMock()
    provider = _provider()

    result = await run_kb_team_edge_role_migration(provider, config, MagicMock())

    assert result["skipped"] is True
    provider.backfill_kb_team_edge_roles.assert_not_called()
