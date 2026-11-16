"""The graph check against the product oracle, on Neo4j and ArangoDB.

Every scenario's final world is loaded into each store and `check_access` is
asked about every viewer x node pair. The layer must equal the oracle: no
over-admit (a leak) and no under-admit (a gap). Needs the throwaway stores of
`tests/integration/graph_permissions/docker-compose.yml`.
"""

from __future__ import annotations

import logging

import pytest

from . import gen
from .linked_readers import findings, linked_viewers, linked_worlds, snapshot
from .runner import ConformanceRunner, Report
from .scenario import Scenario, load_all

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]
# The readers' statements make Neo4j warn about every property a sparse world lacks; pytest keeps each record of a test (as test_conformance_kh).
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)


def _describe(report: Report) -> str:
    lines = [report.summary()]
    lines += [f"{f.kind} {f.scenario} {f.viewer} -> {f.node}: {f.reason}" for f in report.findings[:40]]
    return "\n".join(lines)


@pytest.mark.parametrize("tier", ["rules", "golden"])
async def test_layer_equals_oracle(graph_layers, tier: str) -> None:
    scenarios: list[Scenario] = load_all()
    if tier == "rules":
        scenarios = [s for s in scenarios if "rules" in s.tags]
    elif tier == "golden":
        scenarios = [s for s in scenarios if "rules" not in s.tags]
    for layer in graph_layers:
        await layer.preload(s.final_world() for s in scenarios)
        runner = ConformanceRunner(layer)
        total = Report()
        for s in scenarios:
            total.merge(await runner.run(s))
        assert not total.over_admits, f"{layer.name} over-admits\n{_describe(total)}"
        assert not total.findings, f"{layer.name}\n{_describe(total)}"


async def test_linked_source_accounts_equal_the_oracle(account_layers) -> None:
    """Every world that links a user to a source account, with the account stored as a SourceAccount node instead of a User: each store must
    still answer as the oracle does (the account's grants and badges reach the user inside the link's connector only, and nothing else)."""
    scenarios = [s for s in load_all() if s.final_world().links]
    generated = gen.linked(25)
    assert scenarios and generated
    for layer in account_layers:
        runner = ConformanceRunner(layer)
        total = Report()
        await layer.preload([*(s.final_world() for s in scenarios), *(w for _, w in generated)])
        for s in scenarios:
            total.merge(await runner.run(s))
        for name, world in generated:
            total.merge(await runner.check_world(world, name, cites=True))
        assert total.pairs > 100, f"{layer.name} asked {total.pairs} pairs: nothing was checked"
        assert not total.over_admits, f"{layer.name} over-admits\n{_describe(total)}"
        assert not total.findings, f"{layer.name}\n{_describe(total)}"


async def test_linked_accounts_add_no_organisation_to_roles_details_or_search(graph_layers, no_source_org_layers) -> None:
    """The context role, the record access details and the search scope, in every world that links a user to a source account: the same with
    the linked accounts' organisations removed, and no ability on a node `check_access` refuses."""
    worlds = linked_worlds(4)
    found: list[str] = []
    for plain, without in zip(graph_layers, no_source_org_layers, strict=True):
        await plain.preload(w for _, w in worlds)
        await without.preload(w for _, w in worlds)
        for name, world in worlds:
            ns, ns_without = await plain._ns(world), await without._ns(world)
            for viewer in linked_viewers(world):
                admitted = await plain.admitted(world, viewer, [n.id for n in world.nodes])
                found += findings(f"{plain.name} {name}", viewer,
                                  await snapshot(plain._provider, world, viewer, ns, ns.org),
                                  await snapshot(without._provider, world, viewer, ns_without, ns_without.org), admitted)
    assert not found, "\n".join(found[:40])
