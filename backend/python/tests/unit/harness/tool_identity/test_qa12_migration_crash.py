"""QA-12 (CG-12, DEP-G2): the agent toolset migration interrupted at every write.

The process dies at the n-th write (a ledger ``set_config`` or a node's guarded replace), for every n the run makes.
A rerun of ``apply`` converges to the uninterrupted run (each re-key once in the ledger); ``revert`` interrupted the
same way, or run straight after an interrupted ``apply``, restores every node and empties the ledger, reporting no
conflict or drift of its own making. No node is ever keyed that the ledger does not name.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.harness.tool_identity.migration import LEDGER_PATH, AgentToolsetMigration

from .support import TOOLSETS, FakeConfig, FakeGraph, instance, toolset


class Died(Exception):
    """The process stopped here."""


def _world() -> tuple[FakeGraph, FakeConfig, dict[str, Any]]:
    graph = FakeGraph()
    graph.agent("agent-1", toolset("ts-1"), toolset("ts-2", name="slack", instance_id="slack-1"))
    graph.agent("agent-2", toolset("ts-3", name="github"))
    graph.agent("agent-4", toolset("ts-6"), toolset("ts-7", name="confluence"))
    config = FakeConfig([instance("jira-1"), instance("conf-1", "confluence"), instance("gh-1", "github"), instance("gh-2", "github")])
    start = _nodes(graph)
    _arm(graph, config)
    return graph, config, start


def _arm(graph: FakeGraph, config: FakeConfig) -> None:
    graph.died_at, graph.counted = 0, 0  # type: ignore[attr-defined]
    for target, name in ((graph, "update_node_if_match"), (config, "set_config")):
        call = getattr(target, name)

        async def wrapped(*args: Any, _call: Any = call, **kwargs: Any) -> Any:  # noqa: ANN401
            graph.counted += 1  # type: ignore[attr-defined]
            if graph.counted == graph.died_at:  # type: ignore[attr-defined]
                raise Died
            return await _call(*args, **kwargs)

        setattr(target, name, wrapped)


def _die_at(graph: FakeGraph, n: int) -> None:
    graph.died_at, graph.counted = n, 0  # type: ignore[attr-defined]


def _nodes(graph: FakeGraph) -> dict[str, dict[str, Any]]:
    return {k: {f: v for f, v in d.items() if f != "updatedAtTimestamp"} for k, d in graph.collections[TOOLSETS].items()}


def _ledger(config: FakeConfig) -> list[tuple[str, str]]:
    return sorted((e["toolsetKey"], e["instanceId"]) for e in (config.values.get(LEDGER_PATH) or {}).get("applied", []))


def _migration(graph: FakeGraph, config: FakeConfig) -> AgentToolsetMigration:
    clock = iter(range(1000, 9000))
    return AgentToolsetMigration(graph, config, now_ms=lambda: next(clock))  # type: ignore[arg-type]


def _no_unledgered_key(graph: FakeGraph, config: FakeConfig, start: dict[str, dict[str, Any]]) -> None:
    named = {key for key, _ in _ledger(config)}
    for key, node in _nodes(graph).items():
        if node.get("instanceId") != start[key].get("instanceId"):
            assert key in named, f"{key} keyed with no ledger entry"


async def _clean() -> tuple[int, int, dict[str, dict[str, Any]], list[tuple[str, str]]]:
    graph, config, _ = _world()
    _ = await _migration(graph, config).apply()
    applied, nodes, ledger = graph.counted, _nodes(graph), _ledger(config)  # type: ignore[attr-defined]
    _die_at(graph, 0)
    _ = await _migration(graph, config).revert()
    return applied, graph.counted, nodes, ledger  # type: ignore[attr-defined]


async def test_qa12_7_apply_dies_at_every_write_and_the_rerun_converges() -> None:
    total, _, nodes, ledger = await _clean()
    assert total == 6 and ledger == [("ts-1", "jira-1"), ("ts-6", "jira-1"), ("ts-7", "conf-1")]
    for n in range(1, total + 1):
        graph, config, start = _world()
        _die_at(graph, n)
        with pytest.raises(Died):
            _ = await _migration(graph, config).apply()
        _no_unledgered_key(graph, config, start)
        _die_at(graph, 0)
        report = await _migration(graph, config).apply()
        assert report.conflicts == [], f"died at write {n}"
        assert (_nodes(graph), _ledger(config)) == (nodes, ledger), f"died at write {n}"


async def test_qa12_8_revert_dies_at_every_write_and_the_rerun_restores_every_node() -> None:
    _, total, _, _ = await _clean()
    for n in range(1, total + 1):
        graph, config, start = _world()
        _ = await _migration(graph, config).apply()
        _die_at(graph, n)
        with pytest.raises(Died):
            _ = await _migration(graph, config).revert()
        _die_at(graph, 0)
        report = await _migration(graph, config).revert()
        assert (report.conflicts, report.drifted) == ([], []), f"died at write {n}"
        assert (_nodes(graph), _ledger(config)) == (start, []), f"died at write {n}"


async def test_qa12_9_revert_straight_after_an_interrupted_apply_restores_every_node() -> None:
    total, _, _, _ = await _clean()
    for n in range(1, total + 1):
        graph, config, start = _world()
        _die_at(graph, n)
        with pytest.raises(Died):
            _ = await _migration(graph, config).apply()
        _die_at(graph, 0)
        report = await _migration(graph, config).revert()
        assert (report.conflicts, report.drifted) == ([], []), f"died at write {n}"
        assert (_nodes(graph), _ledger(config)) == (copy.deepcopy(start), []), f"died at write {n}"
