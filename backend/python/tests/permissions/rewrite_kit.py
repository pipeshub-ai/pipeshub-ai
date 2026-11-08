# ruff: noqa: ANN401
"""A structural change through the rewrite job, held to the oracle.

`Env` loads a world as points (any `IVectorDBService`), stamps it with the real back-fill, then `change` moves the graph to the world after a
mutation and registers the roots the sync transaction would (the generation moves, the roots go on the pending list). `settle` runs the real
`ScopeRewriteJob` (and, where it leaves a connector to the full pass, the back-fill) until nothing is pending.

`audit` asks the scope filter the product builds, for every viewer and every stored point, at the moment it is called:

* **wrong admit**: the filter admits a point and the oracle of the graph as it is now denies every record the point stands for (never allowed);
* **admitted while pending**: a point whose stamp changes with the change is admitted by its old stamp (0 allowed while it is being rewritten);
* **inexact**: the filter covers a point and its verdict is not the oracle's (a missing access).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from app.services.permissions.currency import app_is_current
from app.services.permissions.pending import parse, with_roots
from app.services.permissions.scope_fields import (
    SCOPE_FIELDS_VERSION,
    ScopeFields,
    Stamp,
    current_scope_generation,
)
from app.services.permissions.structure_generation import GENERATION_KEY
from app.services.vector_db import scope_expr as sx
from app.services.vector_db.filters import ScopeFilter
from app.services.vector_db.scope_backfill import DONE, ScopeBackfill
from app.services.vector_db.scope_rewrite import PENDING_KEY, UNSCOPED_KEY, RewriteResult, ScopeRewriteJob
from tests.support.in_memory_vector_db import InMemoryVectorDBService

from . import mutations
from .model import HIERARCHY_RELATIONS, Mutation
from .runner import OracleLayer
from .scope_layer import OracleScopeLayer
from .stamp_world import ORG
from .store_harness import Loaded, expected, load, read_stamps, vrids_for

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Collection

    from app.services.permissions.scopes import ViewerScopes
    from app.services.vector_db.interface.vector_db import IVectorDBService

    from .model import World

COLL = "rewrite"
NAME = "w"
PREFIX = f"{NAME}:"
STRUCTURAL = frozenset({"moved", "inherit_set", "rule_set"})
GRANTS = frozenset({"grant_added", "grant_removed"})
#: Mutations of who holds a grant or a membership: they change a viewer's scopes and no stamp.
VIEWER_ONLY = frozenset({"grant_added", "grant_removed", "member_added", "member_removed", "user_removed", "user_deactivated"})
log = logging.getLogger("rewrite-kit")


def descendants(world: World, roots: Collection[str]) -> set[str]:
    """The roots and every node below them over hierarchy edges."""
    below: dict[str, set[str]] = {}
    for n in world.nodes:
        for p in n.parents:
            if p.rel in HIERARCHY_RELATIONS:
                below.setdefault(p.id, set()).add(n.id)
    seen, frontier = set(roots), list(roots)
    while frontier:
        nxt = [c for i in frontier for c in below.get(i, ()) if c not in seen]
        seen.update(nxt)
        frontier = nxt
    return seen


def registration(before: World, after: World, muts: tuple[Mutation, ...], *, stamps_changed: bool = True) -> tuple[dict[str, set[str]], set[str]]:
    """What the sync transaction registers for these mutations: connector -> record roots, and the connectors reached as a whole (a group, or
    anything below a record that is not one). A grant registers only when it makes or unmakes the node's access list of its own."""
    roots: dict[str, set[str]] = {}
    whole: set[str] = set()
    for m in muts:
        if m.kind not in GRANTS | STRUCTURAL:
            continue
        node = after.node(m.node)
        if m.kind in GRANTS:
            held = {g.node for g in before.grants}, {g.node for g in after.grants}
            if (m.node in held[0]) == (m.node in held[1]) or (node.kind != "record" and not stamps_changed):
                continue
        if not node.connector:
            continue
        if node.kind == "record":
            roots.setdefault(node.connector, set()).add(node.id)
        else:
            whole.add(node.connector)
    return roots, whole


@dataclass
class Log:
    leaks: list[tuple] = field(default_factory=list)  # a point admitted that the oracle of the graph as it is now denies
    pending_admits: list[tuple] = field(default_factory=list)  # a point of the changed subtree admitted by its old stamp
    inexact: list[tuple] = field(default_factory=list)  # a covered point whose verdict is not the oracle's
    audits: int = 0
    points: int = 0
    stale_seen: int = 0


@dataclass
class Env:
    vdb: IVectorDBService
    loaded: Loaded
    w0: World
    world: World
    share: bool
    chunks: int
    backfill: ScopeBackfill
    job: ScopeRewriteJob
    #: a test hook: awaited before every write request of the store (a change that commits between the job's read and its write)
    before_write: Callable[[], Awaitable[None]] | None = None
    #: the virtual records whose stamp the changes so far changed (set by `change`)
    changed: set[str] = field(default_factory=set[str])
    subtree: set[str] = field(default_factory=set[str])
    #: virtual records the first back-fill could not stamp (it left the stale marker): the world is not clean to start from
    unstamped: int = 0

    @classmethod
    async def make(cls, world: World, vdb: IVectorDBService | None = None, *, share: bool = False, chunks: int = 1, settle_seconds: float = 0.2,
                   collection: str = COLL) -> Env:
        vdb = vdb or InMemoryVectorDBService()
        loaded = await load(vdb, collection, {NAME: world}, share=share, chunks=chunks)
        backfill = ScopeBackfill(loaded.graph, vdb, loaded.locator, log)
        stale = 0
        for app in loaded.apps:
            done = await backfill.run_connector(ORG, app)
            assert done.state == DONE, done.line()
            stale += done.stale
        env = cls(vdb, loaded, world, world, share, chunks, backfill, ScopeRewriteJob(loaded.graph, vdb, loaded.locator, settle_seconds=settle_seconds))
        env.unstamped = stale
        real = vdb.update_payloads_by_ids

        async def hooked(*args: Any, **kwargs: Any) -> None:
            if env.before_write is not None:
                await env.before_write()
            return await real(*args, **kwargs)

        vdb.update_payloads_by_ids = hooked  # type: ignore[method-assign]
        return env

    @property
    def graph(self) -> Any:
        return self.loaded.graph

    @property
    def apps(self) -> list[str]:
        return list(self.loaded.apps)

    def app_doc(self, connector: str) -> dict[str, Any]:
        return self.graph.app_docs.setdefault(PREFIX + connector, {"_key": PREFIX + connector})

    def pending(self, connector: str | None = None) -> dict[str, int]:
        docs = [self.graph.app_docs.get(a, {}) for a in self.apps if connector is None or a == PREFIX + connector]
        return {r: g for d in docs for r, g in parse(d.get(PENDING_KEY)).items()}

    def vrids(self) -> dict[str, str]:
        """local record id -> prefixed virtual record id (fixed at load)."""
        return {r: PREFIX + v for r, v in vrids_for(self.w0, self.share).items()}

    async def change(self, muts: tuple[Mutation, ...]) -> tuple[dict[str, set[str]], set[str]]:
        """The graph now says the world after `muts` (the sync transaction committed); the stored points are untouched."""
        before, after = self.world, mutations.apply_all(self.world, muts)
        old = await expected(self.loaded)
        self.graph.add(after, PREFIX, vrids_for(self.w0, self.share))
        self.world = after
        new = await expected(self.loaded)
        roots, whole = registration(before, after, muts, stamps_changed=old != new)
        flat = {r for rs in roots.values() for r in rs}
        self.changed |= {v for v in old if old[v] != new.get(v)}
        self.subtree |= descendants(before, flat) | descendants(after, flat)
        for c, rs in roots.items():
            doc = self.app_doc(c)
            gen = int(doc.get(GENERATION_KEY) or 0) + 1  # `bump_generation_in`: the generation and the roots in one write
            doc[GENERATION_KEY], doc[PENDING_KEY] = gen, with_roots(doc.get(PENDING_KEY), [PREFIX + r for r in rs], gen)
        for c in whole:
            doc = self.app_doc(c)
            doc[GENERATION_KEY] = doc[UNSCOPED_KEY] = int(doc.get(GENERATION_KEY) or 0) + 1
        return roots, whole

    async def run_job(self, connector: str) -> RewriteResult:
        return await self.job.run_connector(ORG, PREFIX + connector)

    async def settle(self, *, max_runs: int = 8) -> list[RewriteResult]:
        """Rewrite every connector that has roots until none has, with the full pass where the job leaves one to it."""
        results: list[RewriteResult] = []
        for _ in range(max_runs):
            busy = [a for a in self.apps if self.graph.app_docs.get(a, {}).get(PENDING_KEY)]
            if not busy:
                return results
            for app in busy:
                results.append(await self.job.run_connector(ORG, app))
                if results[-1].state == "full_pass":
                    assert (await self.backfill.run_connector(ORG, app, restart=True)).state == DONE
        raise AssertionError(f"not settled after {max_runs} runs: {self.pending()}")

    async def current(self) -> set[str]:
        return {a for a in self.apps if app_is_current(self.graph.app_docs.get(a))}

    async def problems(self) -> list[str]:
        """What is wrong once everything finished: roots left, a connector not current, stored fields or membership that are not the graph's."""
        out = []
        current = await self.current()
        if self.pending() or current != set(self.apps):
            out.append(f"not settled: current={sorted(current)} pending={self.pending()}")
        stored, want = await read_stamps(self.vdb, self.loaded), await expected(self.loaded)
        wrong = sorted(v for v in self.loaded.points if stored.get(v) != want.get(v))
        if wrong:
            out.append(f"stored fields differ from the projection of the graph: {wrong[:5]}")
        for v, pids in self.loaded.points.items():
            groups = sorted({g for r in self.loaded.records[v] for g in self.graph.stamps.node_map[r].groups})
            for p in await self.vdb.retrieve_points(self.loaded.collection, pids):
                if sorted(p.payload.get("recordGroupIds") or []) != groups:
                    out.append(f"{v}: recordGroupIds {p.payload.get('recordGroupIds')} != {groups}")
        return out

    async def audit(self, into: Log) -> None:
        """Ask the filter, for every viewer and stored point, what the oracle of the graph as it is now says. A point whose stored fields are not
        the graph's projection (its subtree is being rewritten) must not be admitted at all."""
        world, layer, oracle = self.world, OracleScopeLayer(), OracleLayer()
        current, roots = await self.current(), self.pending()
        blocked = frozenset(a for a in self.apps if parse(self.graph.app_docs.get(a, {}).get(PENDING_KEY)))
        ids = [n.id for n in world.nodes]
        alive = {r for r in self.vrids() if (n := world.node(r)).kind == "record" and not n.deleted and not n.placeholder}
        of_vrid: dict[str, list[str]] = {}
        for r, v in self.vrids().items():
            if r in alive:
                of_vrid.setdefault(v, []).append(r)
        pids = {p: v for v, ps in self.loaded.points.items() for p in ps}
        want = await expected(self.loaded)
        payloads = {str(p.id): p.payload for p in await self.vdb.retrieve_points(self.loaded.collection, list(pids))}
        into.audits += 1
        for viewer in world.users():
            scopes = _prefixed(await layer.viewer_scopes(world, viewer), roots)
            flt = ScopeFilter.of(scopes, current, version=SCOPE_FIELDS_VERSION, generation=current_scope_generation(), blocked=blocked)
            truth = oracle.explain(world, viewer, ids)
            for pid, v in pids.items():
                payload = payloads.get(pid)
                if payload is None or v not in of_vrid:
                    continue
                into.points += 1
                stale = ScopeFields.from_payload(payload) != want.get(v)
                into.stale_seen += stale
                admitted = sx.evaluate(sx.admits(flt), payload)
                allowed = any(truth[r].admitted for r in of_vrid[v])
                tag = (viewer, v, pid[:8])
                if admitted and not allowed:
                    into.leaks.append(tag)
                if admitted and stale:
                    into.pending_admits.append(tag)
                if not stale and _covered(payload, flt) and admitted != allowed:
                    into.inexact.append((*tag, "admitted" if admitted else "refused"))


def _prefixed(scopes: ViewerScopes, pending: Collection[str]) -> ViewerScopes:
    def add(xs: frozenset[str]) -> frozenset[str]:
        return frozenset(PREFIX + x for x in xs)

    return replace(scopes, gated_apps=add(scopes.gated_apps), open_apps=add(scopes.open_apps), readable_groups=add(scopes.readable_groups),
                   readable_scopes=add(scopes.readable_scopes), unreadable_restricted=add(scopes.unreadable_restricted),
                   legacy_apps=add(scopes.legacy_apps), pending=frozenset(pending))


def _covered(payload: dict[str, Any], flt: ScopeFilter) -> bool:
    """Whether the filter decides this point by its stamp (otherwise the legacy filter and the post-check do)."""
    fields = ScopeFields.from_payload(payload)
    connectors = set(payload.get("connectorIds") or [])
    if fields is None or not fields.trusted or fields.stamp is Stamp.LEGACY or not connectors or not connectors <= flt.current:
        return False
    return not (fields.stamp is Stamp.CONTAINER and connectors & flt.blocked)


async def play(env: Env, muts: list[Mutation], mode: str, into: Log) -> None:
    """Commit `muts` in order and rewrite. `batched`: all committed, then one rewrite. `midflight`: the first committed, each later one
    committed after a page of the rewrite read the graph and before it wrote. Audited at every commit, every write and at the end."""
    queue = list(muts)

    async def commit() -> None:
        await env.change((queue.pop(0),))
        await env.audit(into)

    await commit()
    if mode == "batched":
        while queue:
            await commit()
    else:
        async def before_write() -> None:
            if queue:
                await commit()

        env.before_write = before_write
    try:
        while True:
            await env.settle()
            if not queue:
                break
            await commit()
    finally:
        env.before_write = None


__all__ = ["COLL", "NAME", "PREFIX", "Env", "Log", "descendants", "play", "registration"]
