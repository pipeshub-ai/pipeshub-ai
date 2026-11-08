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
import random
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from app.services.permissions.currency import app_is_current
from app.services.permissions.pending import parse
from app.services.permissions.scope_fields import (
    SCOPE_FIELDS_VERSION,
    ScopeFields,
    Stamp,
    current_scope_generation,
)
from app.services.permissions.structure_generation import GENERATION_KEY, PENDING_KEY, register_pending_roots
from app.services.vector_db import scope_expr as sx
from app.services.vector_db.const.const import PAYLOAD_KEYWORD_INDEXES, SCOPE_INTEGER_INDEXES, SCOPE_KEYWORD_INDEXES
from app.services.vector_db.filters import ScopeFilter, ScopeFilterUnsupported
from app.services.vector_db.models import (
    FieldCondition,
    FilterExpression,
    HybridSearchRequest,
)
from app.services.vector_db.scope_backfill import DONE, ScopeBackfill
from app.services.vector_db.scope_rewrite import (
    RewriteResult,
    ScopeRewriteJob,
)
from tests.integration.vector_db.helpers import make_dense
from tests.support.in_memory_vector_db import InMemoryVectorDBService, _passes

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

class FencedMemoryVectorDB(InMemoryVectorDBService):
    """The in-memory store as the three conditional stores behave: a write carrying a `fence` leaves a point whose `scopeFence` is above it
    (the fake models that in `update_payload_by_ids`), and the store can count what a filter matches."""

    generation_fence = True

    async def count_points(self, collection_name: str, count_filter: Any) -> int:
        return sum(1 for p in self._points(collection_name).values() if _passes(p.payload, count_filter))


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
    search_mismatch: list[tuple] = field(default_factory=list)  # a store's search with the filter does not return what the filter tree admits


@dataclass
class Env:
    vdb: IVectorDBService
    loaded: Loaded
    w0: World
    world: World
    share: bool | str
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
    async def make(cls, world: World, vdb: IVectorDBService | None = None, *, share: bool | str = False, chunks: int = 1, settle_seconds: float = 0.2,
                   collection: str = COLL) -> Env:
        vdb = vdb or FencedMemoryVectorDB()
        loaded = await load(vdb, collection, {NAME: world}, share=share, chunks=chunks)
        for field_name, schema in (*PAYLOAD_KEYWORD_INDEXES, *SCOPE_KEYWORD_INDEXES, *SCOPE_INTEGER_INDEXES):      # (the search filter reads them)
            await vdb.create_index(collection, field_name, schema)
        await vdb.make_searchable(collection)      # (a set_payload finds only the points a search sees)
        backfill = ScopeBackfill(loaded.graph, vdb, loaded.locator, log)
        stale = 0
        for app in loaded.apps:
            done = await backfill.run_connector(ORG, app)
            assert done.state == DONE, done.line()
            stale += done.stale
        await vdb.make_searchable(collection)
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
            await register_pending_roots(self.graph, PREFIX + c, [PREFIX + r for r in rs])      # the generation and the roots, as the sync writes them
        for c in whole:
            await self.graph.increment_node_field(PREFIX + c, "apps", GENERATION_KEY)           # a change nobody lists: only a full pass covers it
        return roots, whole

    async def run_job(self, connector: str) -> RewriteResult:
        return await self.job.run_connector(ORG, PREFIX + connector)

    async def settle(self, *, max_runs: int = 8) -> list[RewriteResult]:
        """Rewrite every connector that has roots until none has, with the full pass where the job leaves one to it."""
        results: list[RewriteResult] = []
        for run in range(max_runs + 1):
            busy = [a for a in self.apps if self.graph.app_docs.get(a, {}).get(PENDING_KEY)]
            if not busy:
                return results
            if run == max_runs:
                break
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

    async def audit(self, into: Log, *, exact: bool = True) -> None:
        """Ask the filter, for every viewer and stored point, what the oracle of the graph as it is now says. A point whose stored fields are not
        the graph's projection (its subtree is being rewritten) must not be admitted at all. `exact=False` leaves out the missing-access check:
        a world half way through a sequence may be one the projector gets wrong from scratch."""
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
                if exact and not stale and _covered(payload, flt) and admitted != allowed:
                    into.inexact.append((*tag, "admitted" if admitted else "refused"))


    async def store_audit(self, into: Log) -> None:
        """The real store's search with the scope filter returns exactly the points the filter tree admits (what the rewrite wrote is searchable)."""
        world, layer = self.world, OracleScopeLayer()
        current, roots = await self.current(), self.pending()
        blocked = frozenset(a for a in self.apps if parse(self.graph.app_docs.get(a, {}).get(PENDING_KEY)))
        pids = {p: v for v, ps in self.loaded.points.items() for p in ps}
        payloads = {str(p.id): p.payload for p in await self.vdb.retrieve_points(self.loaded.collection, list(pids))}
        for viewer in world.users():
            flt = ScopeFilter.of(_prefixed(await layer.viewer_scopes(world, viewer), roots), current, version=SCOPE_FIELDS_VERSION,
                                 generation=current_scope_generation(), blocked=blocked)
            req = HybridSearchRequest(dense_query=make_dense([1.0]), filter=FilterExpression(must=[FieldCondition(key="metadata.orgId", value=ORG)]),
                                      limit=1000, scope_filter=flt)
            try:
                [hits] = await self.vdb.query_nearest_points(self.loaded.collection, [req])
            except ScopeFilterUnsupported:
                continue      # the store refuses this filter (Redis: ids with capital letters); the product reads such a viewer with the legacy filter
            got = {str(h.id) for h in hits}
            want = {pid for pid in pids if pid in payloads and sx.evaluate(sx.admits(flt), payloads[pid])}
            if got != want:
                into.search_mismatch.append((viewer, sorted(want - got)[:3], sorted(got - want)[:3]))


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
        await env.audit(into, exact=False)

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


def random_change(world: World, rng: random.Random) -> Mutation | None:
    """One structural change a sync could report on a record of this world: a move (to another record or group of the same app, never into
    its own subtree), an inheritance break or restore, or a new access rule. None when the world has nothing to change."""
    records = [n for n in world.nodes if n.kind == "record" and n.connector and not n.deleted and not n.placeholder]
    if not records:
        return None
    node = rng.choice(records)
    kind = rng.choice(["moved", "inherit_set", "rule_set"])
    if kind == "rule_set":
        return Mutation("rule_set", node.id, value=rng.choice(["RESTRICTED", "STRICT", ""]))
    if kind == "inherit_set":
        if not node.parents:
            return None
        parent = rng.choice(node.parents).id
        return Mutation("inherit_set", node.id, ref=parent, value="false" if parent in node.inherits else "true")
    own = descendants(world, [node.id])
    targets = [n for n in world.nodes if n.kind in ("record", "group") and n.connector == node.connector and n.id not in own
              and n.id not in {p.id for p in node.parents} and not n.deleted]
    if not targets:
        return None
    to = rng.choice(targets)
    return Mutation("moved", node.id, ref=to.id, value=to.id if to.kind == "group" else "")


class Killed(BaseException):
    """A process killed mid-run: not an Exception, so nothing in the job may swallow it."""


class Tap:
    """Every call the job makes to the graph and to the store goes through `before(name)` first, which may raise (a kill, a transient failure) or
    return "drop" (a write the store acknowledged and lost)."""

    GRAPH = ("get_document", "get_records_by_virtual_record_id", "get_edges_from_node", "update_node_fields_if_match")
    STAMPS = ("nodes", "subtree_vrids", "app")
    STORE = ("scroll", "count_points", "retrieve_points", "update_payloads_by_ids", "make_searchable")

    def __init__(self, env: Env, before: Callable[[str], Awaitable[str | None]], after: Callable[[str], Awaitable[None]] | None = None) -> None:
        self.calls: list[str] = []
        self.before, self.after = before, after
        for obj, names in ((env.graph, self.GRAPH), (env.graph.stamps, self.STAMPS), (env.vdb, self.STORE)):
            for name in names:
                setattr(obj, name, self._wrap(name, getattr(obj, name)))

    def _wrap(self, name: str, real: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        async def tapped(*args: Any, **kwargs: Any) -> Any:
            self.calls.append(name)
            if await self.before(name) == "drop":
                return None
            out = await real(*args, **kwargs)
            if self.after is not None:
                await self.after(name)
            return out

        return tapped


async def held_up_writer(env: Env, layer: str, monkeypatch: Any) -> None:
    """Writer A reads the graph after a first move and is held up; a second move commits and writer B (another process: its own loop, so its
    own virtual record locks) rewrites and finishes; A goes on. `generation_check`: A is held before its request and the check right before
    the request stops it. `store_fence`: A is held at the store's door with the check disabled and the store refuses its older fence.
    Whatever A does, the store never holds fields older than the graph's, and nothing leaks."""
    import asyncio

    from app.services.vector_db.scope_rewrite import RewriteIncomplete

    from .model import Mutation as M

    await env.change((M("moved", "page:Handbook", "space:ALL", "space:ALL"),))
    b = ScopeRewriteJob(env.graph, env.vdb, env.loaded.locator, settle_seconds=0.5)
    seen, held, stale_writes = Log(), {"done": False}, []
    if layer == "store_fence":
        async def no_check(*a: Any, **k: Any) -> None:
            return None

        monkeypatch.setattr(ScopeRewriteJob, "_guard", no_check)

    async def hold() -> None:
        if held["done"]:
            return
        held["done"] = True
        await env.change((M("moved", "page:Handbook", "space:HR", "space:HR"),))        # the second move

        async def b_finishes() -> None:
            for _ in range(4):
                if env.pending():
                    await b.run_connector(ORG, PREFIX + "app")

        await asyncio.to_thread(asyncio.run, b_finishes())
        assert not await env.problems()

    async def at_the_door(name: str) -> str | None:
        if name == "update_payloads_by_ids" and held["done"] and not stale_writes:
            stale_writes.append(name)      # A's first request after B finished
        if layer == "generation_check" and name == "retrieve_points":
            await hold()
        return None

    async def after(name: str) -> None:
        if name == "update_payloads_by_ids" and stale_writes:
            await env.audit(seen)
            assert not await env.problems(), "a held-up writer's older fields are in the store"

    if layer == "store_fence":
        env.before_write = hold
    Tap(env, at_the_door, after)
    try:
        await env.job.run_connector(ORG, PREFIX + "app")
    except RewriteIncomplete:
        pass          # (a writer that is refused ends incomplete: fine)
    env.before_write = None
    assert held["done"] and not seen.leaks and not seen.pending_admits
    await env.settle()
    assert not await env.problems()


__all__ = ["COLL", "FencedMemoryVectorDB", "NAME", "PREFIX", "Env", "Killed", "held_up_writer", "Log", "Tap", "descendants", "play", "random_change", "registration"]
