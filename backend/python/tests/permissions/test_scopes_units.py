"""ViewerScopes against the oracle-derived scopes, without a store: the resolver over the in-memory fakes."""

from __future__ import annotations

import asyncio

import pytest

from . import gen
from .scenario import Scenario, load_all
from .scope_layer import (
    FakeScopeLayer,
    OracleScopeLayer,
    ScopeConformanceRunner,
    ScopeReport,
)

SCENARIOS = load_all()


def _describe(report: ScopeReport) -> str:
    return "\n".join([report.summary(), *(f"{f.kind} {f.scenario} {f.viewer} {f.field}: {f.ids}" for f in report.findings[:30])])


async def test_every_corpus_world_resolves_to_the_oracle_scopes() -> None:
    runner = ScopeConformanceRunner(FakeScopeLayer())
    total = ScopeReport()
    for world in dict.fromkeys(s.final_world() for s in SCENARIOS):
        total.merge(await runner.check_world(world))
    assert total.viewers > 300
    assert not total.findings, _describe(total)


@pytest.mark.parametrize("kind", sorted(gen.GENERATORS))
async def test_generated_worlds_resolve_to_the_oracle_scopes(kind: str) -> None:
    runner = ScopeConformanceRunner(FakeScopeLayer())
    total = ScopeReport()
    for seed in gen.seeds():
        for world in gen.generate(kind, seed, gen.world_count()):
            total.merge(await runner.check_world(world))
    assert not total.findings, _describe(total)


def _scenario(scenario_id: str) -> Scenario:
    return next(s for s in SCENARIOS if s.id == scenario_id)


def test_the_corpus_has_scopes_to_test() -> None:
    """A vacuous equality (no scope anywhere) would pass; require every kind of set to be non-empty somewhere."""
    layer = OracleScopeLayer()
    seen = dict.fromkeys(("readable_groups", "readable_scopes", "unreadable_restricted"), 0)
    for world in dict.fromkeys(s.final_world() for s in SCENARIOS):
        for viewer in world.users():
            got = asyncio.run(layer.viewer_scopes(world, viewer))
            for name in seen:
                seen[name] += bool(getattr(got, name))
    assert all(seen.values()), seen


async def test_confluence_hr_matches_the_source_document() -> None:
    """Source doc, salary bands: Bob (hr-all + hr-comp) reads the restricted pages, Dave and Erin (hr-all only) have the
    restricted pages among their unreadable ones, Carol (finance) reaches the app but no space."""
    world = next(s.world for s in SCENARIOS if s.world.nodes and any(n.id == "page:Compensation" for n in s.world.nodes))
    layer = OracleScopeLayer()
    bob, dave, erin, carol = [await layer.viewer_scopes(world, v) for v in ("bob", "dave", "erin", "carol")]
    restricted = {"page:Compensation", "page:2027-bands"}
    assert restricted <= bob.readable_scopes and not (restricted & bob.unreadable_restricted)
    assert "space:HR" in dave.readable_groups and restricted <= dave.unreadable_restricted
    assert restricted <= erin.unreadable_restricted and "space:HR" in erin.readable_scopes
    assert not carol.readable_groups and "space:HR" in carol.unreadable_restricted
    assert {t for t in bob.principal_tokens if t.startswith("g:")} >= {"g:g:hr-all", "g:g:hr-comp"}
