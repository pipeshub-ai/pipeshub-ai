"""The startup migration that removes the creator-to-admin link earlier Workspace and Dropbox team syncs wrote."""

import logging
from typing import Any

import pytest

from app.core.org_context import peek
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
        out = [
            {"connectorId": app["_key"], "org": app["orgId"], "user": f"creator-{app['_key']}",
             "selfLink": app.get("self_link", False)}
            for app in self.apps
            if app["type"] in app_types and app["creator_listed"] and self.links.pop(app["_key"], None) is not None
        ]
        return out, list(self.failed_orgs)

    failed_orgs: tuple[str, ...] = ()


class FakeConfig:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def get_config(self, key: str, **_: object) -> Any:  # noqa: ANN401
        return self.values.get(key)

    async def set_config(self, key: str, value: Any) -> bool:  # noqa: ANN401
        self.values[key] = value
        return True


def marker_key(connector_id: str) -> str:
    return f"/services/connectors/{connector_id}/listedAuthenticators"


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
    assert set(config.values) == {marker_key(c) for c in ("gm-1", "dr-1", "db-1")}, "the markers only, no once-only flag"
    assert config.values[marker_key("gm-1")] == ["creator-gm-1"], "recorded once"


class TestTheMarker:
    """The creator of a link removed for being listed stays in the listed-authenticators marker, so a sync that
    no longer lists them does not make the link again."""

    async def _migrate(self, graph: FakeGraph, config: FakeConfig) -> dict:
        return await WorkspaceAdminLinkMigrationService(graph, config, logging.getLogger("t")).migrate()

    async def test_each_removed_creator_is_added_beside_the_ones_already_there(self) -> None:
        graph, config = FakeGraph(), FakeConfig()
        config.values[marker_key("gm-1")] = ["earlier"]

        await self._migrate(graph, config)

        assert config.values == {
            marker_key("gm-1"): ["earlier", "creator-gm-1"],
            marker_key("dr-1"): ["creator-dr-1"],
            marker_key("db-1"): ["creator-db-1"],
        }, "a link that stays records nobody"

    async def test_it_is_read_and_written_in_the_links_own_org(self) -> None:
        seen: list[tuple[str, str, str | None, str | None]] = []

        class Spy(FakeConfig):
            def _note(self, op: str, key: str) -> None:
                ctx = peek()
                seen.append((op, key, ctx.kind if ctx else None, ctx.org_id if ctx else None))

            async def get_config(self, key: str, **kw: object) -> Any:  # noqa: ANN401
                self._note("get", key)
                assert kw == {"use_cache": False, "raise_on_error": True}, "a strict read"
                return await super().get_config(key)

            async def set_config(self, key: str, value: Any) -> bool:  # noqa: ANN401
                self._note("set", key)
                return await super().set_config(key, value)

        await self._migrate(FakeGraph(), Spy())

        assert sorted(seen) == sorted(
            (op, marker_key(c), "job", org)
            for c, org in (("gm-1", "org-1"), ("dr-1", "org-1"), ("db-1", "org-2")) for op in ("get", "set")
        ), "never the cross-org context the removal runs in"
        assert peek() is None

    async def test_a_link_to_oneself_records_nobody(self) -> None:
        graph, config = FakeGraph(), FakeConfig()
        graph.apps = [{"_key": "gm-1", "type": "Gmail Workspace", "orgId": "org-1", "creator_listed": True, "self_link": True}]
        graph.links = {"gm-1": ("creator", "creator")}

        assert await self._migrate(graph, config) == {"success": True, "removed": 1}
        assert config.values == {}

    async def test_a_link_with_no_org_is_not_written_unscoped(self, _no_notify, caplog) -> None:
        graph, config = FakeGraph(), FakeConfig()
        graph.apps = [{"_key": "gm-1", "type": "Gmail Workspace", "orgId": None, "creator_listed": True}]
        graph.links = {"gm-1": ("creator", "src")}

        with caplog.at_level(logging.WARNING):
            assert await self._migrate(graph, config) == {"success": True, "removed": 1}

        assert config.values == {} and "not recorded as listed" in caplog.text
        assert _no_notify == [("gm-1", None)]

    @pytest.mark.parametrize("stored", ["creator", [None], {"a": 1}], ids=["string", "null-entry", "mapping"])
    async def test_a_marker_that_cannot_be_read_is_not_rewritten(self, _no_notify, caplog, stored: object) -> None:
        graph, config = FakeGraph(), FakeConfig()
        config.values[marker_key("gm-1")] = stored

        with caplog.at_level(logging.WARNING):
            assert await self._migrate(graph, config) == {"success": True, "removed": 3}

        assert config.values[marker_key("gm-1")] == stored
        assert config.values[marker_key("dr-1")] == ["creator-dr-1"], "the others are still recorded"
        assert "gm-1" not in graph.links and ("gm-1", "org-1") in _no_notify
        assert "could not read" in caplog.text

    @pytest.mark.parametrize("failing", ["get", "set"])
    async def test_a_store_that_is_down_stops_neither_the_removal_nor_the_cache_drops(
        self, _no_notify, caplog, failing: str,
    ) -> None:
        graph = FakeGraph()

        class Down(FakeConfig):
            async def get_config(self, key: str, **kw: object) -> Any:  # noqa: ANN401
                if failing == "get":
                    raise RuntimeError("secret-looking detail")
                return await super().get_config(key, **kw)

            async def set_config(self, key: str, value: Any) -> bool:  # noqa: ANN401
                raise RuntimeError("secret-looking detail")

        with caplog.at_level(logging.WARNING):
            assert await self._migrate(graph, Down()) == {"success": True, "removed": 3}

        assert sorted(graph.links) == ["dbp-1", "gm-out", "jira-1"]
        assert sorted(_no_notify) == [("db-1", "org-2"), ("dr-1", "org-1"), ("gm-1", "org-1")]
        assert "secret-looking detail" not in caplog.text

    @pytest.mark.parametrize(("answers", "stored"), [([False, True], True), ([False, False], False)],
                             ids=["retried-once", "given-up"])
    async def test_a_refused_write_is_retried_once_then_warned(self, caplog, answers: list[bool], stored: bool) -> None:
        graph, config = FakeGraph(), FakeConfig()
        graph.apps = graph.apps[:1]
        left = list(answers)

        async def set_config(key: str, value: object) -> bool:
            if ok := left.pop(0):
                config.values[key] = value
            return ok

        config.set_config = set_config
        with caplog.at_level(logging.WARNING):
            assert await self._migrate(graph, config) == {"success": True, "removed": 1}

        assert left == [] and (marker_key("gm-1") in config.values) is stored
        assert ("could not record the listed authenticator" in caplog.text) is (not stored)


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
    seen: list[str | None] = []

    class Spy(FakeGraph):
        async def remove_listed_authenticator_links(self, app_types: list[str]) -> tuple[list[dict], list[str]]:
            ctx = peek()
            seen.append(ctx.kind if ctx else None)
            return await super().remove_listed_authenticator_links(app_types)

    await WorkspaceAdminLinkMigrationService(Spy(), FakeConfig(), logging.getLogger("t")).migrate()

    assert seen == ["system"]
