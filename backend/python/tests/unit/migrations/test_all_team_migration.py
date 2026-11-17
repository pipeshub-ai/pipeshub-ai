"""The All-team migration runs at start-up with no org bound: it lists the orgs under a system context and ensures each org's All team in that org."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.core.org_context import current, current_or_system, peek
from app.migrations.all_team_migration import (
    AllTeamMigrationService,
    run_all_team_migration,
)

if TYPE_CHECKING:
    import pytest

FLAG = AllTeamMigrationService.MIGRATION_FLAG_KEY
LOG = logging.getLogger("all-team-test")


class Flags:
    def __init__(self, **values: object) -> None:
        self.values = dict(values)

    async def get_config(self, key: str, default: object = None, **_kw: object) -> object:
        return self.values.get(key, default)

    async def set_config(self, key: str, value: object, **_kw: object) -> bool:
        self.values[key] = value
        return True


class PgLikeGraph:
    """Answers as the PostgreSQL provider does: the org list needs a bound context (every org under a system one), and one org's
    All team needs that org bound."""

    def __init__(self, orgs: list[str], failing: tuple[str, ...] = ()) -> None:
        self.orgs, self.failing = orgs, failing
        self.listed: list[tuple[str, bool]] = []
        self.ensured: list[tuple[str, str]] = []

    async def get_all_orgs(self, *, active: bool = True, **_kw: object) -> list[dict]:
        self.listed.append((current_or_system().source, active))
        return [{"_key": org} for org in self.orgs]

    async def ensure_all_team_with_users(self, org_id: str) -> None:
        ctx = current()
        assert ctx.org_id == org_id
        self.ensured.append((org_id, ctx.source))
        if org_id in self.failing:
            raise RuntimeError("team write failed")


async def test_the_orgs_are_listed_under_system_and_each_all_team_is_ensured_in_its_own_org() -> None:
    graph, flags = PgLikeGraph(["org-a", "org-b"]), Flags()

    result = await run_all_team_migration(graph, flags, LOG)

    assert result["success"] is True and result["orgs_processed"] == 2, result
    assert graph.listed == [("system:all_team_migration", True)]
    assert graph.ensured == [("org-a", "job:all_team_migration"), ("org-b", "job:all_team_migration")]
    assert flags.values[FLAG]["done"] is True
    assert peek() is None


async def test_one_org_failing_is_its_own_error_the_others_still_run_and_the_flag_stays_unset(caplog: pytest.LogCaptureFixture) -> None:
    graph, flags = PgLikeGraph(["org-a", "org-b", "org-c"], failing=("org-b",)), Flags()

    with caplog.at_level(logging.ERROR, logger=LOG.name):
        result = await run_all_team_migration(graph, flags, LOG)

    assert result["success"] is False and result["orgs_processed"] == 2 and result["failed_orgs"] == 1, result
    assert [org for org, _ in graph.ensured] == ["org-a", "org-b", "org-c"]
    assert [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR] == ["❌ Error processing org org-b: team write failed"]
    assert FLAG not in flags.values


async def test_no_orgs_marks_the_migration_done() -> None:
    graph, flags = PgLikeGraph([]), Flags()

    result = await run_all_team_migration(graph, flags, LOG)

    assert result["success"] is True and result["orgs_processed"] == 0, result
    assert graph.listed == [("system:all_team_migration", True)]
    assert flags.values[FLAG]["done"] is True


async def test_regression_guard_a_done_migration_reads_nothing_from_the_graph() -> None:
    """Passes on main too: the flag is read before any graph call."""
    graph = PgLikeGraph(["org-a"])

    result = await run_all_team_migration(graph, Flags(**{FLAG: {"done": True}}), LOG)

    assert result["skipped"] is True
    assert graph.listed == [] and graph.ensured == []
