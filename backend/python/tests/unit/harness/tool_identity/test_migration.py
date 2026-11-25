"""MG-2..MG-7, MG-9 (P2.2): the agent toolset migration over a graph and a configuration store."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from app.harness.tool_identity.migrate import main, parse_mode
from app.harness.tool_identity.migration import LEDGER_PATH, AgentToolsetMigration

from .support import TOOLSETS, FakeConfig, FakeGraph, instance, toolset

if TYPE_CHECKING:
    import logging

    from app.config.configuration_service import ConfigurationService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


def _world() -> tuple[FakeGraph, FakeConfig]:
    graph = FakeGraph()
    # agent-1: one Jira instance in its org; its legacy toolset moves to it.
    graph.agent("agent-1", toolset("ts-1"), toolset("ts-2", name="slack", instance_id="slack-1"))
    # agent-2: a GitHub toolset with two candidate instances, and a Jira one: held whole.
    graph.agent("agent-2", toolset("ts-3", name="github"), toolset("ts-4"))
    # agent-3: a type with no instance at all.
    graph.agent("agent-3", toolset("ts-5", name="zoom"))
    config = FakeConfig([instance("jira-1"), instance("gh-1", "github"), instance("gh-2", "github"), instance("other", "jira", "org-2")])
    return graph, config


def _migration(graph: FakeGraph, config: FakeConfig) -> AgentToolsetMigration:
    clock = iter(range(1000, 2000))
    return AgentToolsetMigration(graph, config, now_ms=lambda: next(clock))  # type: ignore[arg-type]


def _instances(graph: FakeGraph) -> dict[str, str | None]:
    return {k: v.get("instanceId") for k, v in graph.collections[TOOLSETS].items()}


async def test_mg2_dry_run_reports_and_writes_nothing() -> None:
    graph, config = _world()
    report = await _migration(graph, config).dry_run()
    assert [r["toolsetKey"] for r in report.rekeyed] == ["ts-1"]
    held = {h["agentKey"]: h["unresolved"] for h in report.held}
    assert held["agent-2"] == [{"toolsetKey": "ts-3", "toolset": "github", "reason": "ambiguous", "candidates": ["gh-1", "gh-2"]}]
    assert held["agent-3"][0]["reason"] == "no_instance"
    assert report.counts()["alreadyKeyed"] == 1
    assert (graph.writes, config.sets) == (0, 0)


async def test_mg3_apply_rekeys_writes_the_ledger_and_holds_ambiguous_agents() -> None:
    graph, config = _world()
    report = await _migration(graph, config).apply()
    assert [r["toolsetKey"] for r in report.rekeyed] == ["ts-1"]
    node = graph.collections[TOOLSETS]["ts-1"]
    assert (node["instanceId"], node["instanceName"], node["updatedAtTimestamp"] > 1) == ("jira-1", "jira jira-1", True)
    assert node["displayName"] == "Jira"
    assert _instances(graph)["ts-4"] is None, "agent-2 is held whole: its resolvable Jira toolset is untouched too"
    assert [e["toolsetKey"] for e in config.values[LEDGER_PATH]["applied"]] == ["ts-1"]


async def test_mg4_apply_again_changes_nothing() -> None:
    graph, config = _world()
    await _migration(graph, config).apply()
    writes = graph.writes
    again = await _migration(graph, config).apply()
    assert again.rekeyed == []
    assert graph.writes == writes
    assert len(config.values[LEDGER_PATH]["applied"]) == 1


async def test_mg5_revert_undoes_exactly_the_ledger_and_again_is_a_no_op() -> None:
    graph, config = _world()
    before = {k: dict(v) for k, v in graph.collections[TOOLSETS].items()}
    await _migration(graph, config).apply()
    report = await _migration(graph, config).revert()
    assert [r["toolsetKey"] for r in report.reverted] == ["ts-1"]
    node = graph.collections[TOOLSETS]["ts-1"]
    assert "instanceId" not in node and "instanceName" not in node
    assert {k: v for k, v in node.items() if k != "updatedAtTimestamp"} == {
        k: v for k, v in before["ts-1"].items() if k != "updatedAtTimestamp"
    }
    assert graph.collections[TOOLSETS]["ts-2"]["instanceId"] == "slack-1", "a toolset bound before is not the migration's"
    assert config.values[LEDGER_PATH]["applied"] == []
    writes = graph.writes
    assert (await _migration(graph, config).revert()).reverted == []
    assert graph.writes == writes


async def test_mg6_revert_leaves_a_toolset_an_admin_rebound_and_reports_it() -> None:
    graph, config = _world()
    await _migration(graph, config).apply()
    graph.collections[TOOLSETS]["ts-1"]["instanceId"] = "jira-9"
    report = await _migration(graph, config).revert()
    assert report.reverted == []
    assert report.drifted == [{"agentKey": "agent-1", "toolsetKey": "ts-1", "toolset": "jira", "instanceId": "jira-1", "reason": "changed"}]
    assert graph.collections[TOOLSETS]["ts-1"]["instanceId"] == "jira-9"


async def test_mg7_a_concurrent_edit_wins_and_is_not_in_the_ledger() -> None:
    graph, config = _world()

    def edit(key: str) -> None:
        graph.collections[TOOLSETS][key]["updatedAtTimestamp"] = 99

    graph.before_write = edit
    report = await _migration(graph, config).apply()
    assert report.rekeyed == []
    assert [c["toolsetKey"] for c in report.conflicts] == ["ts-1"]
    assert _instances(graph)["ts-1"] is None
    assert config.values[LEDGER_PATH]["applied"] == []


async def test_an_agent_whose_org_is_unknown_is_held() -> None:
    graph = FakeGraph()
    graph.agent("agent-x", toolset("ts-x"), user="ghost", org=None)
    report = await _migration(graph, FakeConfig([instance("jira-1")])).apply()
    assert report.held[0]["unresolved"][0]["reason"] == "tenant_unknown"
    assert graph.writes == 0


@pytest.mark.parametrize(("argv", "mode"), [([], "dry_run"), (["--apply"], "apply"), (["--revert"], "revert")])
def test_mg9_the_cli_is_a_dry_run_unless_told(argv: list[str], mode: str) -> None:
    assert parse_mode(argv) == mode


def test_mg9_the_cli_refuses_both_flags() -> None:
    with pytest.raises(SystemExit):
        parse_mode(["--apply", "--revert"])


async def test_mg9_the_cli_runs_the_migration_and_prints_its_report(capsys: pytest.CaptureFixture[str]) -> None:
    graph, config = _world()

    async def stores(logger: logging.Logger) -> tuple[IGraphDBProvider, ConfigurationService]:
        return graph, config  # type: ignore[return-value]

    assert await main([], stores) == 0
    printed: dict[str, Any] = json.loads(capsys.readouterr().out)
    assert (printed["mode"], printed["counts"]["rekeyed"], graph.writes) == ("dry_run", 1, 0)
    assert await main(["--apply"], stores) == 0
    assert _instances(graph)["ts-1"] == "jira-1"
    assert await main(["--revert"], stores) == 0
    assert _instances(graph)["ts-1"] is None
