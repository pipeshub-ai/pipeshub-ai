"""The startup migration that removes the creator-to-admin link earlier Workspace and Dropbox team syncs wrote."""

import logging
from typing import Any

import pytest

from app.config.constants.arangodb import CollectionNames
from app.migrations.workspace_admin_link_migration import (
    WorkspaceAdminLinkMigrationService,
)


class FakeGraph:
    """Apps of several types in two orgs, one AUTHENTICATED_AS link per connector id, and whether the connector lists its creator."""

    def __init__(self) -> None:
        self.apps = [
            {"_key": "gm-1", "type": "Gmail Workspace", "orgId": "org-1", "creator_listed": True},
            {"_key": "dr-1", "type": "Drive Workspace", "orgId": "org-1", "creator_listed": True},
            {"_key": "db-1", "type": "Dropbox", "orgId": "org-2", "creator_listed": True},
            {"_key": "gm-out", "type": "Gmail Workspace", "orgId": "org-1", "creator_listed": False},
            {"_key": "dbp-1", "type": "Dropbox Personal", "orgId": "org-2", "creator_listed": True},
            {"_key": "jira-1", "type": "Jira", "orgId": "org-1", "creator_listed": True},
        ]
        self.links = {a["_key"]: ("creator", "src") for a in self.apps}
        self.passes: list[list[str]] = []

    async def remove_listed_authenticator_links(self, app_types: list[str]) -> tuple[list[dict], list[str]]:
        self.passes.append(list(app_types))
        out = []
        for app in self.apps:
            if app["type"] in app_types and app["creator_listed"] and self.links.pop(app["_key"], None) is not None:
                out.append({"connectorId": app["_key"], "org": app["orgId"]})
        return out, list(self.failed_orgs)

    failed_orgs: tuple[str, ...] = ()


class FakeConfig:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def get_config(self, key: str, **_: object) -> Any:  # noqa: ANN401
        return self.values.get(key)

    async def set_config(self, key: str, value: Any) -> None:  # noqa: ANN401
        self.values[key] = value


@pytest.fixture(autouse=True)
def _no_notify(monkeypatch) -> list[tuple]:
    calls: list[tuple] = []

    async def notify(connector_id: str, org_id: str | None = None) -> None:
        calls.append((connector_id, org_id))

    monkeypatch.setattr("app.migrations.workspace_admin_link_migration.notify_connector_sync_completed", notify)
    return calls


async def test_only_a_listed_creators_link_of_the_three_connector_types_goes(_no_notify) -> None:
    graph, config = FakeGraph(), FakeConfig()

    result = await WorkspaceAdminLinkMigrationService(graph, config, logging.getLogger("t")).migrate()

    assert result == {"success": True, "removed": 3}
    assert sorted(graph.links) == ["dbp-1", "gm-out", "jira-1"], (
        "a creator the connector does not list, a personal Dropbox and any other connector keep their link"
    )
    assert sorted(_no_notify) == [("db-1", "org-2"), ("dr-1", "org-1"), ("gm-1", "org-1")]


async def test_every_startup_runs_again_and_removes_a_link_an_old_replica_wrote() -> None:
    graph, config = FakeGraph(), FakeConfig()
    service = WorkspaceAdminLinkMigrationService(graph, config, logging.getLogger("t"))
    await service.migrate()
    graph.links["gm-1"] = ("creator", "src")  # a replica still on the old version links the creator again

    assert await service.migrate() == {"success": True, "removed": 1}
    assert "gm-1" not in graph.links
    assert config.values == {}, "no once-only flag"


async def test_a_connector_without_a_link_counts_nothing() -> None:
    graph, config = FakeGraph(), FakeConfig()
    graph.links.clear()

    result = await WorkspaceAdminLinkMigrationService(graph, config, logging.getLogger("t")).migrate()

    assert result == {"success": True, "removed": 0}


def test_the_startup_runs_it_before_syncs_resume_and_consumers_start() -> None:
    from pathlib import Path

    source = (Path(__file__).resolve().parents[3] / "app" / "connectors_main.py").read_text(encoding="utf-8")
    call = source.index("run_workspace_admin_link_migration(")
    for later in ("await resume_sync_services(", "await start_kafka_consumers("):
        assert later in source, f"{later} is no longer in the startup: update this test"
        assert call < source.index(later), f"the migration must run before {later}"


class ManyOrgsGraph(FakeGraph):
    def __init__(self, orgs: int) -> None:
        super().__init__()
        for i in range(orgs):
            for k, t in (("gm", "Gmail Workspace"), ("jira", "Jira")):
                self.apps.append({"_key": f"{k}-x{i}", "type": t, "orgId": f"x{i}", "creator_listed": True})
                self.links[f"{k}-x{i}"] = ("creator", "src")


async def test_the_store_is_asked_once_whatever_the_number_of_orgs(_no_notify) -> None:
    graph = ManyOrgsGraph(orgs=2000)

    result = await WorkspaceAdminLinkMigrationService(graph, FakeConfig(), logging.getLogger("t")).migrate()

    assert result == {"success": True, "removed": 3 + 2000}
    assert graph.passes == [["Gmail Workspace", "Drive Workspace", "Dropbox"]], "one call, not one per org and type"
    assert {k for k in graph.links if k.startswith("jira")} == {f"jira-x{i}" for i in range(2000)} | {"jira-1"}
    assert ("gm-x7", "x7") in _no_notify


async def test_a_failed_removal_reports_failure_and_logs_no_message_text(caplog) -> None:
    graph = FakeGraph()

    async def down(_: list[str]) -> tuple[list[dict], list[str]]:
        raise RuntimeError("secret-looking detail")

    graph.remove_listed_authenticator_links = down

    result = await WorkspaceAdminLinkMigrationService(graph, FakeConfig(), logging.getLogger("t")).migrate()

    assert result["success"] is False and "secret-looking detail" not in caplog.text


async def test_the_links_removed_in_the_orgs_that_worked_still_lose_their_caches_when_one_org_fails(_no_notify) -> None:
    graph = FakeGraph()
    graph.failed_orgs = ("org-9",)

    result = await WorkspaceAdminLinkMigrationService(graph, FakeConfig(), logging.getLogger("t")).migrate()

    assert result == {"success": False, "removed": 3, "failed_orgs": ["org-9"]}
    assert sorted(_no_notify) == [("db-1", "org-2"), ("dr-1", "org-1"), ("gm-1", "org-1")], "the links are gone, so nothing later drops these"


async def test_the_removal_runs_in_a_system_context() -> None:
    from app.core.org_context import peek

    seen: list[str | None] = []

    class Spy(FakeGraph):
        async def remove_listed_authenticator_links(self, app_types):
            ctx = peek()
            seen.append(ctx.kind if ctx else None)
            return await super().remove_listed_authenticator_links(app_types)

    await WorkspaceAdminLinkMigrationService(Spy(), FakeConfig(), logging.getLogger("t")).migrate()

    assert seen == ["system"]
