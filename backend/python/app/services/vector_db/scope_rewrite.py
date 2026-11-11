"""`ScopeRewriteJob`: rewrite the scope fields of the subtrees a structural change registered as pending, and make the connector current again.

A change that reaches only the subtree of one record (a move, a broken or restored inheritance, a new access rule) registers that record as a
pending root in the transaction that writes it (`permissions.pending`) and moves the connector's structure generation, so at once nothing of the
connector is read from its stamps. For one connector instance the job:

1. **Releases** the connector, when a finished back-fill covers every change that reached more than subtrees: the back-fill's structure
   generation moves up to the generation read, so the stamps outside the pending subtrees are used again while every filter hides the pending
   subtrees (`ScopeFilter.pending`, `blocked`).
2. **Rewrites** each root's subtree: per virtual record the membership and the scope fields come from one read of the graph, only the points whose
   stored fields differ are written (by id, pinned to the organisation), then a second read of the graph and of the store, and a search that shows
   the written fields, prove the result.
3. **Finishes**: the roots rewritten come off the pending list by compare-and-set on the stored string; a root registered again meanwhile stays.

Killed anywhere, the pending list is still on the app document, so the subtree stays hidden and the next run repeats the same steps (the fields are
a pure function of the graph). Anything skipped, failed or unknown keeps its root pending and fails the run; one bad root does not hold the others.

**Writers.** The generation is read before the graph, and again right before every request that writes (`_guard`): a request whose graph is
no longer current is not sent. The request carries a time taken just before the graph was read as its fence where the store has one
(`generation_fence`), pgvector's `computed_at` otherwise, so a held-up writer cannot write over a newer rewrite. The fence is a time, not a
generation, because one virtual record can be shared by two connectors and their generations are separate counters. A point that carries a
fence above this writer's time (another host's clock runs ahead, or an earlier run left it) is followed when that is a plausible time: the next
round reads the graph again, after that write, and writes above it; a value that is no plausible time is not followed and the root stays
pending. **Lock order**: the app document is written only outside the virtual record locks (release before, compare-and-set after); under
them it is only read.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import for_org
from app.services.permissions.currency import app_is_current
from app.services.permissions.errors import ViewerScopesUnavailable
from app.services.permissions.metrics import SCOPE_STAMP_FAILED
from app.services.permissions.mode import scope_fields_enabled
from app.services.permissions.pending import PendingUnreadable, parse, without_done
from app.services.permissions.rewrite_lease import LeaseLost, RewriteLease
from app.services.permissions.rules import Rule, rule_for
from app.services.permissions.scope_fields import (
    SCOPE_FENCE_FIELD,
    SCOPE_KEYS,
    ScopeFields,
    Stamp,
)
from app.services.permissions.structure_generation import (
    GENERATION_KEY,
    PENDING_KEY,
    UNSAFE_KEY,
    same_mark,
    structure_generation_of,
    whole_number,
)
from app.services.vector_db.const.const import (
    CONNECTOR_IDS_FIELD,
    RECORD_GROUP_IDS_FIELD,
    ROOT_RECORD_GROUP_IDS_FIELD,
)
from app.services.vector_db.errors import PartialResult
from app.services.vector_db.membership import (
    STALE_SCOPE,
    resolve_virtual_record_state,
    scope_fields_for_state,
    vrid_write_locks,
)
from app.services.vector_db.models import ScrollWindowExceededError
from app.services.vector_db.scope_backfill import (
    BACKFILL_KEYS,
    MAX_MOVES,
    ScopeFieldsOff,
    UnknownConnector,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.vector_db.interface.vector_db import IVectorDBService
    from app.services.vector_db.membership import CollectionLocator

ROOTS_PER_BATCH = 20  # roots whose subtrees are read and rewritten together
PAGE = 200  # virtual records per write round (they are locked together)
WRITE_BATCH = 500  # points per request to the store
SCAN_BATCH = 200  # virtual records per scroll
SCAN_ATTEMPTS = 3  # scroll-and-count rounds before a scan is called incomplete
SCAN_RETRY_SECONDS = 0.5
SETTLE_SECONDS = 30.0  # how long a write may take to show in a search
MAX_ROUNDS = 3  # write + verify rounds for one page before its virtual records are marked stale
#: How far above the store's clock (this host's, where the store has none) a stored fence is still followed: the skew between two hosts that is
#: believed. A fence further away is not followed: the log says so and the root stays pending.
#: A follow adds a random amount below this to the fence it found, so that two followers that saw the same fence do not write the same value.
FOLLOW_JITTER_MICROS = 10_000
MAX_FENCE_SKEW_MICROS = 10 * 60 * 10**6
MAX_FENCE = 2**53 - 1      # what Qdrant and Redis (which compare a fence as a double) still tell from its successor
MEMBERSHIP_KEYS = (CONNECTOR_IDS_FIELD, RECORD_GROUP_IDS_FIELD, ROOT_RECORD_GROUP_IDS_FIELD)


class RewriteIncomplete(Exception):  # not a RuntimeError: a kill in a test must not be mistaken for it
    """Part of a rewrite was skipped, failed or could not be told: the roots stay pending, hidden, and the next run does it again."""


class GenerationMoved(Exception):
    """The connector's structure generation (or its unsafe mark) moved between the graph read and the write: what was read is old."""


class Unplaceable(Exception):
    """A virtual record whose fields or membership cannot be told from the graph."""


@dataclass(frozen=True)
class Epoch:
    """What the app document said just before a graph read: the read is good to write only while it still does."""

    generation: int
    unsafe: object
    leased: bool = True      # the app document still names this rewriter as the holder of the connector


@dataclass
class RewriteResult:
    org_id: str
    connector_id: str
    #: "idle" (nothing pending), "done", "full_pass" (left to the back-fill: a change reached more than subtrees, the pass is not current, or
    #: the stamps below a root cannot be told by name), "changed" (the connector changed while this ran: the roots stay, the next run covers them)
    state: str = "idle"
    roots: list[str] = field(default_factory=list[str])
    released: bool = False
    points_written: int = 0  # points whose stored fields differed
    points_touched: int = 0  # points that were right and were written again to carry this writer's fence


@dataclass(frozen=True)
class _Plan:
    """One virtual record as the graph says it."""

    payload: dict[str, Any]
    collections: list[str]


def releasable(app: Mapping[str, object], generation: int) -> bool:
    """The stamps outside the pending subtrees are right: a finished pass (current field version, ACL generation and unsafe mark, no write in
    flight) read the structure at `recorded`, and every bump since (`recorded < g <= generation`) registered a root at exactly that generation.
    A bump that listed nothing (a change of the whole connector, more roots than the list holds, a record the registry could not list) leaves a
    generation no root carries, and only a full pass covers it. A root registered again at a newer generation hides the number it had: that
    costs a full pass, never a release."""
    recorded = whole_number(app.get(BACKFILL_KEYS["structure_generation"]))
    if recorded is None or not recorded <= generation:
        return False
    try:
        listed = set(parse(app.get(PENDING_KEY)).values())
    except PendingUnreadable:
        return False
    if not all(g in listed for g in range(recorded + 1, generation + 1)):
        return False
    return app_is_current({**app, BACKFILL_KEYS["structure_generation"]: generation})


async def compare_and_set_app(graph: IGraphDBProvider, connector_id: str, updates: Mapping[str, Any], expected: Mapping[str, Any], *, attempts: int = 5) -> bool:
    """`update_node_fields_if_match` on the app document, retrying a write conflict."""
    for attempt in range(attempts):
        try:
            return bool(await graph.update_node_fields_if_match(connector_id, CollectionNames.APPS.value, dict(updates), dict(expected)))
        except Exception as exc:
            if attempt + 1 == attempts or not graph.is_write_conflict(exc):
                raise
            await asyncio.sleep(0.05 * (attempt + 1))
    return False


async def remove_pending(graph: IGraphDBProvider, connector_id: str, done: Mapping[str, int], *, attempts: int = 5) -> None:
    """Take the roots in `done` (root -> the generation the graph was read at) off the pending list. A root registered again after that generation
    stays. Compare-and-set on the stored string, retried: no registration is lost."""
    for _ in range(attempts):
        raw = ((await graph.get_document(connector_id, CollectionNames.APPS.value, raise_on_error=True)) or {}).get(PENDING_KEY)
        try:
            left = without_done(raw, done)
        except PendingUnreadable as exc:
            raise RewriteIncomplete(f"the pending roots of {connector_id} do not parse") from exc
        if left == (raw or None) or await compare_and_set_app(graph, connector_id, {PENDING_KEY: left}, {PENDING_KEY: raw}):
            return
    raise RewriteIncomplete(f"could not take the rewritten roots off the pending list of {connector_id}")


class ScopeRewriteJob:
    def __init__(self, graph_provider: IGraphDBProvider, vector_db: IVectorDBService, locator: CollectionLocator, *,
                 logger: logging.Logger | None = None, settle_seconds: float = SETTLE_SECONDS, clock: Callable[[], float] = time.time) -> None:
        self._graph, self._vdb, self._locator = graph_provider, vector_db, locator
        self._log = logger or logging.getLogger(__name__)
        self._settle = settle_seconds
        self._clock = clock      # the lease's (the fence's is the store's)
        self._lease: RewriteLease | None = None

    def attach(self, lease: RewriteLease) -> None:
        """Run under a lease taken by the caller (the back-fill's membership step): the guard then also stops a write once it is lost."""
        self._lease = lease

    async def run_connector(self, org_id: str, connector_id: str) -> RewriteResult:
        """One rewriter per connector: while another holds its lease the result is "busy" and nothing is read or written. The lease is given back
        on a normal end or an error, and left to run out when the process is killed (a `BaseException`), as a dead process would leave it."""
        if not scope_fields_enabled():
            raise ScopeFieldsOff("turn the scope fields on (ACL_SCOPE_FIELDS) before rewriting")
        with for_org(org_id, job="scope-rewrite"):
            lease = self._lease = RewriteLease(self._graph, connector_id, clock=self._clock)
            if not await lease.acquire():
                return RewriteResult(org_id, connector_id, state="busy")
            try:
                result = await self._run(org_id, connector_id)
            except LeaseLost:
                await lease.release()      # (a no-op unless it is still ours)
                return RewriteResult(org_id, connector_id, state="busy")
            except Exception:
                await lease.release()
                raise
            await lease.release()
            return result

    async def _app(self, connector_id: str) -> dict[str, Any]:
        return dict(await self._graph.get_document(connector_id, CollectionNames.APPS.value, raise_on_error=True) or {})

    async def _run(self, org_id: str, connector_id: str) -> RewriteResult:
        result = RewriteResult(org_id, connector_id)
        stamps = self._graph.permission_stamp_graph()
        # the app document is read before the graph: a change that commits after this moves the generation, and every write is checked against it
        app = await self._app(connector_id)
        connector_type = (await stamps.app(org_id, connector_id)).connector_type
        if not connector_type:
            raise UnknownConnector(f"{connector_id} is not a connector of this organisation")
        try:
            snapshot = parse(app.get(PENDING_KEY))
        except PendingUnreadable as exc:      # never "no roots": the connector is not current and stays hidden
            raise RewriteIncomplete(f"the pending roots of {connector_id} do not parse") from exc
        if not snapshot:
            return result
        generation = structure_generation_of(app)
        if generation is None:
            raise RewriteIncomplete(f"the structure generation of {connector_id} is not readable: {app.get(GENERATION_KEY)!r}")
        result.roots = roots = sorted(snapshot)
        batches = [roots[i:i + ROOTS_PER_BATCH] for i in range(0, len(roots), ROOTS_PER_BATCH)]
        recorded = whole_number(app.get(BACKFILL_KEYS["structure_generation"]))
        if recorded != generation:
            # Before the hide lifts. A subtree deeper than the walk means the whole connector is reached. And a Union point below a root need not
            # name it in `pathIds`, so the hide cannot cover it: if one of those would change, only a full pass is safe. Either way nothing is read
            # from the connector's stamps until it ran.
            try:
                unnamed = False
                for batch in batches:
                    vrids = sorted(await stamps.subtree_vrids(org_id, batch))
                    unnamed = unnamed or (rule_for(connector_type) is Rule.UNION and await self._unnamed_change(org_id, vrids, set(roots)))
            except ViewerScopesUnavailable:
                unnamed = True
            if unnamed or not releasable(app, generation):
                result.state = "full_pass"
                return result
            expected = {GENERATION_KEY: app.get(GENERATION_KEY), BACKFILL_KEYS["structure_generation"]: app.get(BACKFILL_KEYS["structure_generation"])}
            if not await compare_and_set_app(self._graph, connector_id, {BACKFILL_KEYS["structure_generation"]: generation}, expected):
                result.state = "changed"
                return result
            result.released = True
        rewritten: list[str] = []
        failed: dict[str, Exception] = {}
        for batch in batches:
            try:
                await self._rewrite(org_id, connector_id, batch, result)
                rewritten += batch
            except LeaseLost:
                raise
            except Exception as exc:
                # One bad root must not hold the healthy roots of its connector hidden: take the batch again root by root. Those that fail stay.
                if len(batch) == 1:
                    failed[batch[0]] = exc
                    continue
                for root in batch:
                    try:
                        await self._rewrite(org_id, connector_id, [root], result)
                        rewritten.append(root)
                    except LeaseLost:
                        raise
                    except Exception as one:
                        failed[root] = one
        if rewritten:
            await remove_pending(self._graph, connector_id, dict.fromkeys(rewritten, generation))
        if failed:
            first = next(iter(failed))
            raise RewriteIncomplete(f"{len(failed)} of {len(roots)} roots of {connector_id} were left pending, the others were rewritten. "
                                    f"First, {first}: {type(failed[first]).__name__}: {failed[first]}") from failed[first]
        result.state = "done"
        return result

    async def _rewrite(self, org_id: str, connector_id: str, roots: list[str], result: RewriteResult) -> None:
        vrids = sorted(await self._graph.permission_stamp_graph().subtree_vrids(org_id, roots))
        for i in range(0, len(vrids), PAGE):
            if self._lease:
                await self._lease.renew()      # outside the virtual-record locks: the app document is written only there
            await self._page(org_id, connector_id, vrids[i:i + PAGE], result)

    # ---- one page -----------------------------------------------------------------------------------------

    async def refresh_membership(self, org_id: str, connector_id: str, vrids: Sequence[str]) -> set[str]:
        """Bring `connectorIds`, `recordGroupIds` and `rootRecordGroupIds` of these virtual records' points to what the graph says, nothing else
        (a `container` stamp is read through them). For the full back-fill, which writes scope fields only. Runs inside the organisation, with the
        guard, the fence and the read-backs of every other write.

        A virtual record whose membership the graph cannot tell is left on the stale marker (its points are not admitted by any stamp), as the
        back-fill's own `_stamp` does, and returned: the pass goes on. A write the store did not apply raises
        `RewriteIncomplete` (the points may hold the wrong membership under a stamp that admits). So does a connector that keeps changing under a
        page, or moves while its stale marker is written: a release (`releasable`) can make a finished pass current without a new one, as long as
        every change since it listed a root, and nothing would then write these records."""
        ordered, stale = sorted(set(vrids)), set[str]()
        for i in range(0, len(ordered), PAGE):
            stale |= await self._page(org_id, connector_id, ordered[i:i + PAGE], RewriteResult(org_id, connector_id), keys=MEMBERSHIP_KEYS)
        return stale

    async def _page(self, org_id: str, connector_id: str, vrids: list[str], result: RewriteResult, *, keys: Sequence[str] | None = None) -> set[str]:
        """Rewrite these virtual records. With `keys` (the back-fill's membership step) a virtual record that cannot be placed is left on the
        stale marker and returned, where a rewrite of roots raises, so that the root stays. A connector that keeps changing raises in both.

        shortcut: PAGE virtual records are read, written and proved under their locks in one block that must end inside
        `MEMBERSHIP_LOCK_TIMEOUT_SECONDS` (120 s). The page size is unmeasured at scale; measure it before this is reachable. Every run also
        writes every point of the page again, whether it differs or not (a time fence is always older than the one a point has, so a right point
        takes the new fence, which the change-and-inverse case needs): PAGE x chunks writes a page, which the same measurement must include."""
        todo, failed, unplaced, floor, rounds, moves, moved, top = vrids, dict[str, str](), dict[str, str](), 0, 0, 0, False, 0      # (`unplaced` is every round's)
        async with vrid_write_locks(vrids):
            while todo:
                epoch, at = await self._epoch(connector_id), await self._now()      # the generation and the time, both before the graph is read
                fence = max(_micros(at), floor)
                plans, cannot = await self._plans(org_id, todo, keys)
                unplaced |= cannot
                try:
                    written = await self._write(org_id, connector_id, plans, epoch, fence, at, result)
                    todo, top = await self._differs(org_id, plans, written, keys)
                    if top > fence and todo and (floor := await self._follow(connector_id, epoch, top, fence)) < 0:
                        failed.update(dict.fromkeys(todo, f"a fence ({top}) that is no plausible time, not followed"))
                        break
                except GenerationMoved:
                    moves += 1      # the graph changed after it was read; nothing from it was sent: read it again (a move costs no round)
                    if moves > MAX_MOVES:
                        moved = True
                        break
                    continue
                rounds += 1
                if todo and rounds >= MAX_ROUNDS:
                    SCOPE_STAMP_FAILED.inc("rewrite")
                    if top == 0 and _fenced_by_time(self._vdb):
                        self._log.warning("%s refused or lost the write of %d virtual record(s) of %s: a group row computed after %s? (a host clock "
                                          "ahead of the database's); the root stays pending", type(self._vdb).__name__, len(todo), connector_id, at)
                    failed.update(dict.fromkeys(todo, "a write did not show, or the points kept changing"))
                    break
            stale = sorted({*failed, *(unplaced if keys is not None else ())})
            if stale and not moved:      # fail closed: no stamp of these points admits (they are on the legacy filter) until they are written again
                await self._mark_stale(org_id, connector_id, stale, epoch)
        if keys is None:
            failed |= unplaced
        if moved:
            failed.update(dict.fromkeys(todo, "the connector kept changing"))
        if failed:
            first = min(failed)
            raise RewriteIncomplete(f"{len(failed)} virtual record(s) left unwritten or stale. First, {first}: {failed[first]}")
        return set(unplaced) if keys is not None else set()

    async def _follow(self, connector_id: str, epoch: Epoch, top: int, fence: int) -> int:
        """A point carries a fence above this writer's time: another host's clock runs ahead, or an earlier run left it. Follow it when it is a
        plausible time (the next round reads the graph again, after that write, and writes above it), only while the connector has not changed
        since this graph was read: the floor of the next round. -1 for a fence that is no plausible time (counted and logged, not followed)."""
        now = _micros(await self._now())
        if top >= MAX_FENCE - FOLLOW_JITTER_MICROS or top > max(now, fence) + MAX_FENCE_SKEW_MICROS:
            SCOPE_STAMP_FAILED.inc("fence_out_of_range")
            self._log.error("scope fence %d on a point of %s is not a plausible time (the store's clock: %d, at most %d s ahead is "
                            "followed): not followed; the rewrite stays incomplete until the point is repaired (index the record "
                            "again)", top, connector_id, now, MAX_FENCE_SKEW_MICROS // 10**6)
            return -1
        await self._guard(connector_id, epoch)
        # shortcut: two followers can still draw the same jitter (1 in FOLLOW_JITTER_MICROS); the last of them to write keeps the
        # point, and the other's read-back then sees fields that are not its own and fails its root (hidden), not a wrong stamp.
        return top + 1 + secrets.randbelow(FOLLOW_JITTER_MICROS)

    async def _now(self) -> datetime:
        """The clock a store's fence is measured in: the database's for pgvector (`fence_time`), this host's for the others. Qdrant, OpenSearch
        and Redis have no clock to ask a client for, so their fences are host times and the skew between hosts is what `MAX_FENCE_SKEW_MICROS` bounds."""
        fn = getattr(self._vdb, "fence_time", None)
        return await fn() if fn is not None else _clock()

    async def _epoch(self, connector_id: str) -> Epoch:
        app = await self._app(connector_id)
        if (generation := structure_generation_of(app)) is None:
            raise RewriteIncomplete(f"the structure generation of {connector_id} is not readable")
        return Epoch(generation, app.get(UNSAFE_KEY), self._lease.holds(app) if self._lease else True)

    async def _guard(self, connector_id: str, epoch: Epoch) -> None:
        """Right before a request that writes: the connector did not change since the graph was read."""
        now = await self._epoch(connector_id)
        if not now.leased:
            raise LeaseLost(f"{connector_id} is held by another rewriter")
        if now.generation != epoch.generation or not same_mark(now.unsafe, epoch.unsafe):
            raise GenerationMoved(f"{connector_id} changed after its graph was read")

    async def _plan_one(self, org_id: str, vrid: str, keys: Sequence[str] | None = None) -> _Plan:
        # one read of the graph gives the membership and the fields, so a point never holds the one of one read and the other of another
        state = await resolve_virtual_record_state(self._graph, vrid, org_id=org_id)
        if not state.complete:
            raise Unplaceable("the graph read was incomplete")
        if not state.records:  # its records are gone: the points await the delete path and stay on the legacy filter; membership is left as it is
            return _Plan({} if keys else ScopeFields.legacy().to_payload(), list(await self._locator.all_collections(fresh=True, strict=True)))
        if not (state.connector_ids and state.roots_resolved):
            raise Unplaceable("membership unreadable (no connector, or the group roots could not be resolved)")
        member = {CONNECTOR_IDS_FIELD: state.connector_ids, RECORD_GROUP_IDS_FIELD: state.record_group_ids,
                  ROOT_RECORD_GROUP_IDS_FIELD: state.root_record_group_ids}
        collections = list(self._locator.collections_for_records(state.records))
        if keys:      # the membership alone: a virtual record whose scope fields cannot be told still gets the membership the graph gives it
            return _Plan(member, collections)
        fields = await scope_fields_for_state(self._graph, state, org_id, self._log)
        if fields == STALE_SCOPE:
            # The projector gives the stale marker for a virtual record whose records it skips (a placeholder). With a point behind it that is a
            # failure (the point keeps what it held); with none there is nothing to write.
            if any([await self._scan(org_id, name, [vrid]) for name in collections]):
                raise Unplaceable("no projection")
            return _Plan({}, [])
        return _Plan(fields.to_payload() | member, collections)

    async def _plans(self, org_id: str, vrids: Sequence[str], keys: Sequence[str] | None = None) -> tuple[dict[str, _Plan], dict[str, str]]:
        """The plan of each virtual record; with `keys` only those payload keys of it."""
        plans: dict[str, _Plan] = {}
        unplaced: dict[str, str] = {}
        for v in vrids:
            try:
                plans[v] = await self._plan_one(org_id, v, keys)
            except Exception as exc:  # a virtual record that cannot be told keeps its points as they are; the run fails and its root stays
                SCOPE_STAMP_FAILED.inc("rewrite")
                unplaced[v] = f"{type(exc).__name__}: {exc}"
                self._log.warning("scope rewrite: %s: %s", v, unplaced[v])
        return plans, unplaced

    async def _write(self, org_id: str, connector_id: str, plans: Mapping[str, _Plan], epoch: Epoch, fence: int, at: datetime, result: RewriteResult) -> dict[tuple[str, str], str]:
        """Write the points whose stored fields differ, and give the others this writer's fence. Returns (collection, point id) -> virtual record."""
        by_collection: dict[str, list[str]] = {}
        for v, plan in plans.items():
            for name in plan.collections:
                by_collection.setdefault(name, []).append(v)
        written: dict[tuple[str, str], str] = {}
        for name, group in by_collection.items():
            found = await self._scan(org_id, name, group)
            held = await self._stored(name, list(found))
            todo: dict[str, dict[str, Any]] = {}
            touch: dict[str, dict[str, Any]] = {}
            for pid, payload in found.items():
                v = _vrid_of(payload)
                if v not in plans:
                    continue
                written[(name, pid)] = v
                if pid not in held or not _holds(held[pid], plans[v].payload):
                    todo[pid] = plans[v].payload
                elif self._needs_fence(held[pid], fence):
                    touch[pid] = plans[v].payload  # right already, written again so a stale writer's fields are refused over it
            await self._put(org_id, connector_id, name, todo, epoch, fence, at)
            await self._put(org_id, connector_id, name, touch, epoch, fence, at)
            result.points_written += len(todo)
            result.points_touched += len(touch)
        return written

    def _needs_fence(self, stored: Mapping[str, Any], fence: int) -> bool:
        if self._vdb.generation_fence:
            return int(stored.get(SCOPE_FENCE_FIELD) or 0) < fence
        return _fenced_by_time(self._vdb)  # pgvector's fence is a time on the group row, which a read of the point does not show

    async def _put(self, org_id: str, connector_id: str, name: str, updates: Mapping[str, dict[str, Any]], epoch: Epoch, fence: int | None,
                   at: datetime) -> None:
        """One guarded request per batch. `fence` None: no fence (the stores that have a conditional write then write unconditionally); pgvector
        always has `computed_at`, and a row computed later still refuses it."""
        if not updates:
            return
        extra: dict[str, Any] = ({"fence": fence} if fence is not None else {}) if self._vdb.generation_fence else {"computed_at": at} if _fenced_by_time(self._vdb) else {}
        ids = list(updates)
        for i in range(0, len(ids), WRITE_BATCH):
            await self._guard(connector_id, epoch)
            await self._vdb.update_payloads_by_ids(name, {p: updates[p] for p in ids[i:i + WRITE_BATCH]}, org_id=org_id, **extra)

    async def _differs(self, org_id: str, plans: Mapping[str, _Plan], written: Mapping[tuple[str, str], str],
                       keys: Sequence[str] | None = None) -> tuple[list[str], int]:
        """The virtual records whose graph, stored points or search view do not show what was written (a second read of each), and the highest
        fence found on a point that kept other fields than were written (0 if none)."""
        again, _ = await self._plans(org_id, list(plans), keys)
        bad = {v for v, p in plans.items() if v not in again or not _same(again[v].payload, p.payload)}
        by_collection: dict[str, list[str]] = {}
        for name, pid in written:
            by_collection.setdefault(name, []).append(pid)
        top = 0
        for name, ids in by_collection.items():
            more, seen = await self._not_shown(org_id, name, ids, plans, written)
            bad |= more
            top = max(top, seen)
        return sorted(bad), top

    async def _not_shown(self, org_id: str, name: str, ids: list[str], plans: Mapping[str, _Plan],
                         written: Mapping[tuple[str, str], str]) -> tuple[set[str], int]:
        """Stored first (a write that was refused, lost or overwritten), then searched: a store that searches an older view than it stores
        (OpenSearch between refreshes) is asked to publish (by the scan), and polled. Also the highest fence on a point that did not take the write."""
        stored = await self._stored(name, ids)
        refused = {pid for pid, payload in stored.items() if not _holds(payload, plans[written[(name, pid)]].payload)}
        bad = {written[(name, pid)] for pid in refused}
        top = max((int(stored[pid].get(SCOPE_FENCE_FIELD) or 0) for pid in refused), default=0)      # (a fence that is no number raises: the root fails)
        wanted = {pid: plans[written[(name, pid)]].payload for pid in ids if written[(name, pid)] not in bad}
        vrids = sorted({written[(name, pid)] for pid in wanted})
        deadline, delay = time.monotonic() + self._settle, 0.05
        while wanted:
            view = await self._scan(org_id, name, vrids, (*SCOPE_KEYS, *MEMBERSHIP_KEYS))
            lagging = {pid for pid, want in wanted.items() if pid not in view or not _holds(view[pid], want)}
            if not lagging or time.monotonic() >= deadline:
                return bad | {written[(name, pid)] for pid in lagging}, top
            await asyncio.sleep(delay)
            delay = min(delay * 2, 1.0)
        return bad, top

    async def _mark_stale(self, org_id: str, connector_id: str, vrids: Sequence[str], epoch: Epoch) -> None:
        """The legacy marker (version 0) on every point of these virtual records, so that no stamp of them admits. It is written whatever fence the
        points carry (unfenced; on pgvector at the database's time now, later than this round's own write) and read back: a marker a store refused
        leaves the old stamp, which may admit, so that raises, and so does a connector that moved before the write (`GenerationMoved`)."""
        marker, at = STALE_SCOPE.to_payload(), await self._now()
        for name in set(await self._locator.all_collections(fresh=True, strict=True)):
            found = await self._scan(org_id, name, list(vrids))
            await self._put(org_id, connector_id, name, dict.fromkeys(found, marker), epoch, None, at)
            left = [pid for pid, payload in (await self._stored(name, list(found))).items() if not _holds(payload, marker)]
            if left:
                raise RewriteIncomplete(f"the stale marker did not land on {len(left)} point(s) of {name} (a fence or a computed_at above this "
                                        f"writer's): their stamp may still admit")

    async def mark_stale(self, org_id: str, connector_id: str, vrids: Sequence[str]) -> None:
        """The stale marker on these virtual records' points, read back (see `_mark_stale`). For the back-fill's own marker; raises when it did not
        land. It takes no virtual record lock: the caller holds it or has none."""
        await self._mark_stale(org_id, connector_id, vrids, await self._epoch(connector_id))

    async def begin(self, connector_id: str) -> tuple[Epoch, datetime]:
        """What `write_payload` needs, taken before the graph is read (the connector's generation and lease, and the time that is the fence)."""
        return await self._epoch(connector_id), await self._now()

    async def scan_page(self, org_id: str, vrids: Sequence[str]) -> dict[str, dict[str, dict[str, Any]]]:
        """One scan (one refresh, scroll and count per batch) of a page of virtual records in every collection, for `write_payload`'s `scanned`."""
        return {name: await self._scan(org_id, name, list(vrids)) for name in set(await self._locator.all_collections(fresh=True, strict=True))}

    async def write_payload(self, org_id: str, connector_id: str, collections: Sequence[str], vrid: str, payload: dict[str, Any], epoch: Epoch,
                            at: datetime, floor: int = 0, scanned: Mapping[str, Mapping[str, dict[str, Any]]] | None = None) -> tuple[bool, int]:
        """The back-fill's write of the scope fields: every point of the virtual record, by id, guarded (lease and generation) and fenced like a
        rewrite's (at `at`, or above `floor`), then read back. Returns whether every point holds it, and the floor for the next try (above a
        plausible fence a refused point carries, as a rewrite follows it; negative for one that is no plausible time, which no round will pass):
        False means read the graph again. `LeaseLost` and `GenerationMoved` send nothing more."""
        fence, refused, top = max(_micros(at), floor), False, 0
        for name in collections:
            found = ({p: x for p, x in scanned[name].items() if _vrid_of(x) == vrid} if scanned and name in scanned
                     else await self._scan(org_id, name, [vrid]))
            await self._put(org_id, connector_id, name, dict.fromkeys(found, payload), epoch, fence, at)
            held = await self._stored(name, list(found))
            bad = [p for p in held.values() if not _holds(p, payload)]
            refused |= bool(bad)
            top = max([top, *(int(p.get(SCOPE_FENCE_FIELD) or 0) for p in bad)])
        if refused and top > fence:
            floor = await self._follow(connector_id, epoch, top, fence)      # (above `fence`, so above the old floor; or -1)
        return not refused, floor

    async def _stored(self, collection: str, point_ids: list[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(point_ids), SCAN_BATCH):
            out.update({str(p.id): p.payload for p in await self._vdb.retrieve_points(collection, point_ids[i:i + SCAN_BATCH])})
        return out

    async def _scan(self, org_id: str, collection: str, vrids: Sequence[str], keys: Sequence[str] = ()) -> dict[str, dict[str, Any]]:
        """The points of these virtual records (a point per chunk), by id, with their payload `keys`. **Complete or an error**: a store can answer
        a scroll with fewer points than it holds (RediSearch stops a query that runs past its timeout), and a caller that rewrites "every point"
        would leave the missing ones as they were under a hide that lifts. So each batch is counted by the store (`count_points`) and the two
        must agree; a store that says a read was partial, or cannot count, fails the same way."""
        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(vrids), SCAN_BATCH):
            out.update(await self._scan_batch(org_id, collection, list(vrids[i:i + SCAN_BATCH]), keys))
        return out

    async def _scan_batch(self, org_id: str, collection: str, vrids: list[str], keys: Sequence[str]) -> dict[str, dict[str, Any]]:
        try:
            return await self._scan_counted(org_id, collection, vrids, keys)
        except ScrollWindowExceededError:  # a store that can page only so far (Redis: 10,000 results): halve until each part fits
            if len(vrids) == 1:
                raise
            half = len(vrids) // 2
            return {**await self._scan_batch(org_id, collection, vrids[:half], keys), **await self._scan_batch(org_id, collection, vrids[half:], keys)}

    async def _scan_counted(self, org_id: str, collection: str, vrids: list[str], keys: Sequence[str]) -> dict[str, dict[str, Any]]:
        flt = await self._vdb.filter_collection(must={"orgId": org_id, "virtualRecordId": vrids})
        for attempt in range(SCAN_ATTEMPTS):
            got: dict[str, dict[str, Any]] = {}
            try:
                await self._vdb.make_searchable(collection)      # a point written a moment ago is in neither the scroll nor the count (OpenSearch)
                offset: str | None = None
                while True:
                    page = await self._vdb.scroll(collection, flt, 500, offset, with_payload=["metadata.virtualRecordId", *keys])
                    got.update({str(p.id): p.payload for p in page.points})
                    if not (offset := page.next_offset):
                        break
                counted = await self._vdb.count_points(collection, flt)
                if len(got) == counted:
                    return got
                problem = f"the scroll saw {len(got)} points and the store counts {counted}"
            except PartialResult as exc:
                problem = str(exc)
            if attempt + 1 == SCAN_ATTEMPTS:
                raise PartialResult(f"{collection}: {problem}")
            await asyncio.sleep(SCAN_RETRY_SECONDS * (attempt + 1))
        raise PartialResult(f"{collection}: no scan attempted")  # SCAN_ATTEMPTS is at least 1

    async def _unnamed_change(self, org_id: str, vrids: list[str], all_roots: set[str]) -> bool:
        """Whether some point of these virtual records would change and does not name any pending root in its stored `pathIds` (see `_run`).
        shortcut: projects every virtual record below the roots, not only the unnamed ones; upgrade when a Union connector's rewrite is slow."""
        for i in range(0, len(vrids), PAGE):
            plans, unplaced = await self._plans(org_id, vrids[i:i + PAGE])
            if unplaced:  # cannot tell: only a full pass is safe
                return True
            by_collection: dict[str, list[str]] = {}
            for v, plan in plans.items():
                for name in plan.collections:
                    by_collection.setdefault(name, []).append(v)
            for name, group in by_collection.items():
                found = await self._scan(org_id, name, group)
                for pid, stored in (await self._stored(name, list(found))).items():
                    fields = ScopeFields.from_payload(stored)
                    if (fields is not None and fields.stamp in (Stamp.UNION, Stamp.INTERSECTION, Stamp.OVERRIDE) and not all_roots & set(fields.path_ids)
                            and not _holds(stored, plans[_vrid_of(found[pid])].payload)):
                        return True
        return False


def _clock() -> datetime:
    return datetime.now(UTC)


def _micros(at: datetime) -> int:
    return int(at.timestamp() * 1_000_000)


def _vrid_of(payload: Mapping[str, Any]) -> str:
    """The virtual record id of a point's payload (nested, or the flat dotted key some stores return)."""
    return str((payload.get("metadata") or {}).get("virtualRecordId") or payload.get("metadata.virtualRecordId") or "")


def _same(a: object, b: object) -> bool:
    return sorted(map(str, a)) == sorted(map(str, b)) if isinstance(a, list | tuple) and isinstance(b, list | tuple) else a == b


def _holds(stored: Mapping[str, Any], want: Mapping[str, Any]) -> bool:
    """Whether a stored payload holds these values (lists as sets of ids, in any order)."""
    return all(_same(stored.get(k) if stored.get(k) is not None else [] if isinstance(v, list) else None, v) for k, v in want.items())


def _fenced_by_time(vdb: IVectorDBService) -> bool:
    return "computed_at" in inspect.signature(vdb.update_payload_by_ids).parameters


__all__ = ["PENDING_KEY", "RewriteIncomplete", "RewriteResult", "ScopeRewriteJob", "releasable", "remove_pending"]
