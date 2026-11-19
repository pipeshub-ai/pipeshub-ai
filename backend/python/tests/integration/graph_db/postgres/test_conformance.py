"""``PostgresGraphProvider.check_access`` against the product oracle: the permission conformance corpus (``tests/permissions``) on PostgreSQL.

Every scenario's final world is loaded under its own org and ``check_access`` is asked about every viewer x node pair. The layer must equal
the oracle: no over-admit (a leak) and no under-admit (a gap).
"""
from __future__ import annotations

import hashlib
import itertools
import os
from typing import TYPE_CHECKING

import pytest

from app.core.org_context import OrgContext, bind
from tests.integration.graph_permissions.loaders import EDGE_TARGETS, NODE_TARGETS
from tests.permissions import gen
from tests.permissions.graph import Graph, Namespace, compile_world
from tests.permissions.runner import ConformanceRunner, Report
from tests.permissions.scenario import Scenario, load_all

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable

    from tests.permissions.model import World

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1800)]

_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "")
_COUNTER = itertools.count(int(_WORKER[2:]) * 1_000_000 if _WORKER[2:].isdigit() else 0)      # a prefix is never reused by two workers


def _hex(org: str) -> str:
    """The kernel wants a 24-hex org id; the harness names orgs ``org-<prefix>``."""
    return hashlib.sha1(org.encode()).hexdigest()[:24]


def _props(props: dict) -> dict:
    return {k: (_hex(v) if k == "orgId" and isinstance(v, str) else v) for k, v in props.items() if v is not None}


class PgLayer:
    """An access layer (see ``tests/permissions/runner.py``) over a live PostgreSQL provider: a world is stored and asked about under its own org."""

    name = "postgres"

    def __init__(self, provider, db) -> None:
        self._provider, self._db = provider, db
        self._prefix: dict[World, str] = {}

    def shape(self, graph: Graph) -> Graph:
        return graph

    async def preload(self, worlds: Iterable[World]) -> None:
        for w in (w for w in dict.fromkeys(worlds) if w not in self._prefix):
            self._prefix[w] = f"w{next(_COUNTER)}"
            graph = compile_world(w, self._prefix[w])
            org = _hex(Namespace(self._prefix[w]).org)
            kind = {n["id"]: NODE_TARGETS[n["kind"]] for n in graph["nodes"]}
            await self._wipe(org)      # a world a killed run left behind must not collide
            with bind(OrgContext(org, None, "job", "job:test", "t")):
                for k, coll in NODE_TARGETS.items():
                    if rows := [_props(n["props"]) for n in graph["nodes"] if n["kind"] == k]:
                        await self._provider.batch_upsert_nodes(rows, coll)
                for et, coll in EDGE_TARGETS.items():
                    if rows := [{"from_id": e["from"], "from_collection": kind[e["from"]], "to_id": e["to"], "to_collection": kind[e["to"]], **_props(e["props"])}
                                for e in graph["edges"] if e["type"] == et]:
                        await self._provider.batch_create_edges(rows, coll)

    async def _wipe(self, org: str) -> None:
        async def go(tx) -> None:
            await tx.execute("DELETE FROM graph.edges WHERE org_id = $1", org)
            await tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org)
        with bind(OrgContext(org, None, "job", "job:test", "t")):
            await self._db.run(go, write=True)

    async def release(self, worlds: Iterable[World]) -> None:
        for w in dict.fromkeys(worlds):
            if w in self._prefix:
                await self._wipe(_hex(Namespace(self._prefix.pop(w)).org))

    async def _ns(self, world: World) -> Namespace:
        if world not in self._prefix:
            await self.preload([world])
        return Namespace(self._prefix[world])

    async def admitted(self, world: World, viewer: str, node_ids: list[str]) -> set[str]:
        ns = await self._ns(world)
        org = _hex(ns.org)
        with bind(OrgContext(org, None, "job", "job:test", "t")):
            found = await self._provider.check_access(ns.id(viewer), org, node_ids=[ns.id(n) for n in node_ids])
        # a placeholder asked by id is reachable (the hub opens it) but is not a record the user may read
        return {ns.local(n) for n in found.node_ids - found.placeholder_ids}

    async def cited(self, world: World, viewer: str, vrids: list[str], indexed_only: bool) -> dict[str, str | None]:
        ns = await self._ns(world)
        org = _hex(ns.org)
        stored = [ns.id(v) for v in vrids]
        with bind(OrgContext(org, None, "job", "job:test", "t")):
            found = await self._provider.check_access(ns.id(viewer), org, virtual_record_ids=stored, indexed_only=indexed_only)
        return {v: (None if found.records_by_vrid.get(s) is None else ns.local(found.records_by_vrid[s])) for v, s in zip(vrids, stored, strict=True)}


@pytest.fixture
async def layer(pg_provider, pg_db) -> AsyncIterator[PgLayer]:
    layer = PgLayer(pg_provider, pg_db)
    yield layer
    await layer.release(list(layer._prefix))


def _describe(report: Report) -> str:
    return "\n".join([report.summary(), *(f"{f.kind} {f.scenario} {f.viewer} -> {f.node}: {f.reason}" for f in report.findings[:40])])


@pytest.mark.parametrize("tier", ["rules", "golden"])
async def test_layer_equals_oracle(layer: PgLayer, tier: str) -> None:
    scenarios: list[Scenario] = [s for s in load_all() if ("rules" in s.tags) == (tier == "rules")]
    await layer.preload(s.final_world() for s in scenarios)
    runner = ConformanceRunner(layer)
    total = Report()
    for s in scenarios:
        total.merge(await runner.run(s))
    assert total.pairs > 100, f"the corpus asked {total.pairs} pairs: nothing was checked"
    assert not total.over_admits, f"over-admits (a leak)\n{_describe(total)}"
    assert not total.findings, _describe(total)


CHUNK = 25      # worlds stored at once


@pytest.mark.parametrize("kind", sorted(gen.GENERATORS))
async def test_generated_worlds_equal_the_oracle(layer: PgLayer, kind: str) -> None:
    """Generated worlds (``PERM_GEN_WORLDS``, ``PERM_GEN_SEED``): every viewer x node pair and every citation, layer against oracle."""
    runner = ConformanceRunner(layer)
    for seed in gen.seeds():
        worlds = gen.generate(kind, seed, gen.world_count())
        for start in range(0, len(worlds), CHUNK):
            chunk = worlds[start:start + CHUNK]
            await layer.preload(chunk)
            for offset, world in enumerate(chunk):
                report = await runner.check_world(world, f"{kind}-{seed}-{start + offset}", cites=True)
                assert not report.over_admits, f"{kind} seed {seed} world {start + offset}: over-admit (a leak)\n{_describe(report)}"
                assert not report.findings, f"{kind} seed {seed} world {start + offset}\n{_describe(report)}"
            await layer.release(chunk)
