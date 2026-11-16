"""``PostgresGraphProvider.check_access`` against the product oracle: the permission conformance corpus (``tests/permissions``) on PostgreSQL.

Every scenario's final world is loaded under its own org and ``check_access`` is asked about every viewer x node pair. The layer must equal
the oracle: no over-admit (a leak) and no under-admit (a gap).
"""
from __future__ import annotations

import hashlib
import itertools
import uuid
from typing import TYPE_CHECKING

import pytest

from app.core.org_context import OrgContext, bind
from tests.integration.graph_permissions.loaders import EDGE_TARGETS, NODE_TARGETS
from tests.permissions import gen
from tests.permissions.graph import VARIANTS, Graph, Namespace, compile_world
from tests.permissions.linked_readers import (
    findings,
    linked_viewers,
    linked_worlds,
    snapshot,
)
from tests.permissions.runner import ConformanceRunner, Report
from tests.permissions.scenario import Scenario, load_all

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable

    from tests.permissions.model import World

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1800)]

# A prefix (and so an org) is never reused by two workers or two runs against one database: each would wipe the other's worlds (``_wipe``).
_RUN = uuid.uuid4().hex[:8]
_COUNTER = itertools.count()


def _hex(org: str) -> str:
    """The kernel wants a 24-hex org id; the harness names orgs ``org-<prefix>``."""
    return hashlib.sha256(org.encode()).hexdigest()[:24]


def _props(props: dict) -> dict:
    return {k: (_hex(v) if k == "orgId" and isinstance(v, str) else v) for k, v in props.items() if v is not None}


class PgLayer:
    """An access layer (see ``tests/permissions/runner.py``) over a live PostgreSQL provider: a world is stored and asked about under its own org."""

    name = "postgres"
    variant = ""      # how a world is stored (``tests.permissions.graph.VARIANTS``)

    def __init__(self, provider, db) -> None:
        self._provider, self._db = provider, db
        self._prefix: dict[World, str] = {}

    def shape(self, graph: Graph) -> Graph:
        return VARIANTS[self.variant](graph)

    async def preload(self, worlds: Iterable[World]) -> None:
        for w in (w for w in dict.fromkeys(worlds) if w not in self._prefix):
            self._prefix[w] = f"w{_RUN}{next(_COUNTER)}"
            graph = self.shape(compile_world(w, self._prefix[w]))
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


@pytest.mark.parametrize("variant", ["accounts", "withdrawn"])
async def test_linked_source_accounts_equal_the_oracle(layer: PgLayer, variant: str) -> None:
    """Every world that links a user to a source account, with the account stored as a SourceAccount node instead of a User: the walk must still
    answer as the oracle does (the account's grants and badges reach the user inside the link's connector only, and nothing else; a withdrawn
    account gives nothing)."""
    layer.variant = variant
    scenarios = [s for s in load_all() if s.final_world().links]
    generated = gen.linked(25)
    assert scenarios and generated
    await layer.preload([*(s.final_world() for s in scenarios), *(w for _, w in generated)])
    runner = ConformanceRunner(layer)
    total = Report()
    for s in scenarios:
        total.merge(await runner.run(s))
    for name, world in generated:
        total.merge(await runner.check_world(world, name, cites=True))
    assert total.pairs > 100, f"the corpus asked {total.pairs} pairs: nothing was checked"
    assert not total.over_admits, f"over-admits (an over-share)\n{_describe(total)}"
    assert not total.findings, _describe(total)


@pytest.mark.parametrize(("variant", "variant_without"), [("accounts", "no_source_org"), ("withdrawn", "unlinked")])
async def test_linked_source_accounts_add_no_organisation_to_roles_details_or_search(layer: PgLayer, pg_provider, pg_db, variant: str,
                                                                                    variant_without: str) -> None:
    """The context role, the record access details and the search scope, in every world that links a user to a source account stored as a
    SourceAccount node: the same as with the linked accounts' organisations removed (with the accounts withdrawn: as with the links removed),
    and no ability on a node ``check_access`` refuses."""
    layer.variant = variant
    without = PgLayer(pg_provider, pg_db)
    without.variant = variant_without
    worlds = linked_worlds(4)
    found: list[str] = []
    try:
        await layer.preload(w for _, w in worlds)
        await without.preload(w for _, w in worlds)
        for name, world in worlds:
            ns, ns_without = await layer._ns(world), await without._ns(world)
            for viewer in linked_viewers(world):
                admitted = await layer.admitted(world, viewer, [n.id for n in world.nodes])
                with bind(OrgContext(_hex(ns.org), None, "job", "job:test", "t")):
                    plain = await snapshot(pg_provider, world, viewer, ns, _hex(ns.org))
                with bind(OrgContext(_hex(ns_without.org), None, "job", "job:test", "t")):
                    stripped = await snapshot(pg_provider, world, viewer, ns_without, _hex(ns_without.org))
                found += findings(name, viewer, plain, stripped, admitted)
    finally:
        await without.release(list(without._prefix))
    assert not found, "\n".join(found[:40])


async def test_a_linked_user_account_still_gives_its_organisation_role(layer: PgLayer, pg_provider) -> None:
    """A source account stored as a User keeps what its organisation gives in the context role, as it did (REGR-11: u reads f as OWNER)."""
    world = next(s for s in load_all() if s.id == "REGR-11").final_world()
    await layer.preload([world])
    ns = await layer._ns(world)
    with bind(OrgContext(_hex(ns.org), None, "job", "job:test", "t")):
        got = await snapshot(pg_provider, world, "u", ns, _hex(ns.org))
    assert got[("context", "f")]["role"] == "OWNER", got
