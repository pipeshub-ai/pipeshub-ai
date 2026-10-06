from unittest.mock import AsyncMock, MagicMock

import pytest

from app.migrations.team_org_id_migration import (
    TeamOrgIdMigrationService,
    run_team_org_id_migration,
)

FLAG = "/migrations/team_org_id_v1"


def _service(backfill=None, flag=None):
    graph = MagicMock()
    graph.backfill_team_org_ids = backfill or AsyncMock(
        return_value={"updated": 0, "unresolved_team_ids": []}
    )
    config = MagicMock()
    config.get_config = AsyncMock(return_value=flag)
    config.set_config = AsyncMock()
    return TeamOrgIdMigrationService(graph, config, MagicMock()), graph, config


@pytest.mark.asyncio
async def test_success_sets_flag_with_counts() -> None:
    svc, graph, config = _service(
        AsyncMock(return_value={"updated": 3, "unresolved_team_ids": ["t9"]})
    )

    result = await svc.migrate()

    assert result["success"] is True
    assert result["teams_updated"] == 3
    assert result["teams_unresolved"] == 1
    key, value = config.set_config.await_args.args
    assert key == FLAG
    assert value["done"] is True and value["teams_unresolved"] == 1


@pytest.mark.asyncio
async def test_unresolved_ids_are_logged_not_flagged() -> None:
    svc, _, config = _service(
        AsyncMock(return_value={"updated": 0, "unresolved_team_ids": ["t9", "t10"]})
    )

    await svc.migrate()

    warning = svc.logger.warning.call_args.args[0]
    assert "t9" in warning and "t10" in warning
    assert "t9" not in str(config.set_config.await_args.args[1])


@pytest.mark.asyncio
async def test_failure_leaves_flag_unset_for_retry() -> None:
    svc, _, config = _service(AsyncMock(side_effect=RuntimeError("db down")))

    result = await svc.migrate()

    assert result["success"] is False
    assert "db down" in result["error"]
    config.set_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_flag_set_skips_backfill() -> None:
    svc, graph, config = _service(flag={"done": True})

    result = await svc.migrate()

    assert result["skipped"] is True
    graph.backfill_team_org_ids.assert_not_awaited()
    config.set_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_second_run_after_success_makes_no_changes() -> None:
    state = {"flag": None}
    backfill = AsyncMock(side_effect=[
        {"updated": 2, "unresolved_team_ids": []},
        {"updated": 0, "unresolved_team_ids": []},
    ])
    graph = MagicMock(backfill_team_org_ids=backfill)
    config = MagicMock()
    config.get_config = AsyncMock(side_effect=lambda _k: state["flag"])
    config.set_config = AsyncMock(side_effect=lambda _k, v: state.update(flag=v))

    first = await run_team_org_id_migration(graph, config, MagicMock())
    second = await run_team_org_id_migration(graph, config, MagicMock())

    assert first["teams_updated"] == 2
    assert second["skipped"] is True and second["teams_updated"] == 0
    assert backfill.await_count == 1
