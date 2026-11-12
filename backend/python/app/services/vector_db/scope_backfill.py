"""`ScopeBackfill`: write the scope fields of the points that already exist, one connector instance at a time.

Run by an operator (`python -m app.scripts.scope_projection`), never by itself, and only while the scope fields are on
(`scope_fields_enabled`). It computes nothing of its own: the fields of a point are `scope_fields_for_state`, the function
the live writers use, so a back-filled point carries what a newly indexed one would.

- **Resumable.** The records of the connector are read in key order, a page at a time. After a page is written its last key is
  saved on the app document (`scopeBackfillAfter`); a run that dies anywhere starts again at the last saved key and writes the
  page it was on a second time (the fields are a function of the graph, so that changes nothing). A connector whose saved
  version, or whose scope generation, is not the current one (`ACL_SCOPE_GENERATION`) starts over, a finished one included.
- **Bounded.** One run handles at most `max_pages` pages and returns `running` when there are more; the operator runs it again.
- **Org-scoped.** Every graph read names the organisation, and every write only matches points of that organisation.
- **Fail closed.** A point whose fields could not be computed or written gets the stale marker (legacy, version 0), which keeps
  it on the legacy filter, and is counted in `scopeBackfillStale`. The marker write is tried 4 times with doubling waits; if it still
  fails the virtual record is logged at error and counted (`pipeshub_scope_fields_failed_total{where="marker"}`), and the run reports
  it as stale (exit code 1). Only then can a point keep what it held, which may be exact fields this run wrote before a later step
  failed: run the back-fill again.
- **Safe beside live indexing.** The back-fill writes a point's scope keys under the same per-virtual-record lock the live writers take
  (so in this process the two do not interleave; the command is another process, so only the read-back below covers it). Across processes
  there is no lock, so every write is followed by a second read of the graph: if the fields differ from what was written, the graph changed
  between the first read and the write, and the point is written again from the new graph (up to `MAX_ROUNDS`, then it is marked
  stale). A live write that lands after ours has read the graph after the change and is right; one that landed before ours is
  corrected by the second read.
- **Held to its lease.** The scope fields are written by id like a rewrite's (`ScopeRewriteJob.write_payload`): checked against the connector's
  lease and generation right before the request, and fenced by a time taken before the graph was read, so a request that outlives the lease or is
  held up cannot land over a newer writer's points (a store's by-filter write has no such condition, so none is used). A pass that lost the lease
  ends with `ConnectorBusy` and writes nothing more: its checkpoint is saved by compare-and-set on the lease (`RewriteLease.update_app`).
- **Membership.** A `container` stamp is read through the point's `recordGroupIds`, and a change that only a full pass covers (a change of the
  whole connector, more roots than the list holds) can leave them old, so the pass also brings `connectorIds`, `recordGroupIds` and
  `rootRecordGroupIds` to what the graph says, page by page (`ScopeRewriteJob.refresh_membership`). Those writes are by id, checked against the
  connector's structure generation right before each request, fenced by a time taken before the graph was read (or pgvector's `computed_at`) so a
  held-up writer cannot write older membership over a newer one, and read back from the graph, the store and a search. A write the store did
  not apply ends the pass `failed`. A virtual record whose membership the graph cannot resolve is left on the stale marker (which lands whatever
  fence the points carry, and is read back) and counted once, as the scope fields of one that cannot be projected are, so one such record does not
  hold the whole connector for good: the pass still ends current, with those points on the legacy filter, and says so in the log.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import for_org
from app.services.permissions.metrics import SCOPE_STAMP_FAILED
from app.services.permissions.mode import scope_fields_enabled
from app.services.permissions.rewrite_lease import (
    REWRITE_LEASE_KEY,
    LeaseLost,
    RewriteLease,
)
from app.services.permissions.scope_fields import (
    SCOPE_FIELDS_VERSION,
    current_scope_generation,
)
from app.services.permissions.structure_generation import (
    CLOSED_KEY,
    GENERATION_KEY,
    OPEN_WRITES_KEY,
    OPENED_KEY,
    UNSAFE_KEY,
    clear_pending_covered,
    new_unsafe_mark,
    same_mark,
    structure_generation_of,
    unclosed_writes,
    unsafe_mark,
    whole_number,
)
from app.services.vector_db.membership import (
    STALE_SCOPE,
    resolve_virtual_record_state,
    scope_fields_for_state,
    vrid_write_locks,
)

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable, Mapping

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.vector_db.interface.vector_db import IVectorDBService
    from app.services.vector_db.membership import CollectionLocator

PAGE_SIZE = 100
MAX_PAGES = 20  # records per run: 2,000 at the default page size
MAX_ROUNDS = 3  # write + read-back rounds for one virtual record before it is marked stale
MAX_MOVES = 20  # times the connector's generation may move under one page or record before it is given up (a move costs no round: it is read again)
MARKER_ATTEMPTS = 4  # tries to write the stale marker of one virtual record
MARKER_DELAY_SECONDS = 0.25  # doubled after each failed try

RUNNING, DONE, FAILED = "running", "done", "failed"


class ScopeFieldsOff(RuntimeError):
    """The back-fill was asked for while the scope fields are not written: nothing else would keep them current."""


class ScopeGenerationUnavailable(RuntimeError):
    """The shared scope generation could not be read: every stamp this process writes is untrusted, so a pass would only record a
    finished back-fill that no reader may rely on."""


class UnreadableStructureState(RuntimeError):
    """The connector's stored structure generation or unsafe mark cannot be read: a pass could not say what it stamped from."""


class CheckpointRefused(RuntimeError):
    """The first checkpoint of a run could not be written (typically an app schema without the new key): the run stopped before it wrote anything."""


def _old_schema(app_id: str, what: str, exc: Exception) -> str:
    return (f"the back-fill {what} of {app_id} could not be written ({type(exc).__name__}). If the graph store's app schema is older than this "
            f"release (it must list {', '.join([REWRITE_LEASE_KEY, *BACKFILL_KEYS.values(), OPENED_KEY, CLOSED_KEY])}), restart a connector or "
            "indexing service, which applies the schema at start, and run again. Nothing was written.")


class OpenMarkNotClosed(RuntimeError):
    """The connector carries an open unsafe mark and the operator did not say the write behind it is over."""


class ConnectorBusy(RuntimeError):
    """Another back-fill or rewrite holds the connector's lease."""


class StaleMarkerNotWritten(RuntimeError):
    """The stale marker of a virtual record did not land after every try: the pass fails."""


class UnknownConnector(ValueError):
    """The connector instance is not an app of this organisation."""


@dataclass
class Progress:
    state: str = RUNNING
    after: str | None = None
    version: int = SCOPE_FIELDS_VERSION
    generation: int = 0
    records: int = 0
    stale: int = 0
    error: str | None = None
    #: The connector's structure generation and unsafe mark as the pass read them BEFORE it read any record: its stamps are current only
    #: while the connector still holds both (`permissions.currency`). -1 / None: not recorded.
    structure_generation: int = -1
    unsafe: int | bool | None = None
    #: Virtual records whose point was written `legacy` (a record the stamper refuses): they stay on the legacy filter, like a stale one.
    #: -1: not recorded (a checkpoint older than the count): its pass says nothing about exactness, so the connector is not exact and a pass starts over.
    legacy: int = 0

    @property
    def exact(self) -> bool:
        """Every point of the connector carries an exact stamp as of this pass: it ended and left none stale or legacy. Points written after the pass
        are not counted (shortcut: a later refused record is not seen until the pass is run again; it costs a short answer, never access)."""
        return self.state == DONE and self.stale == 0 and self.legacy == 0

    def line(self) -> str:
        return f"{self.state}  version={self.version} generation={self.generation} records={self.records} stale={self.stale} structure={self.structure_generation} unsafe={self.unsafe} after={self.after or '-'}" + (
            f" error={self.error}" if self.error else "")


#: The names the checkpoint is kept under on the app document (also read by `permissions.currency`).
BACKFILL_KEYS = {"state": "scopeBackfillState", "after": "scopeBackfillAfter", "version": "scopeBackfillVersion",
         "generation": "scopeBackfillGeneration", "records": "scopeBackfillRecords", "stale": "scopeBackfillStale", "error": "scopeBackfillError",
         "structure_generation": "scopeBackfillStructureGeneration", "unsafe": "scopeBackfillUnsafe",
         "legacy": "scopeBackfillLegacy", "exact": "scopeBackfillExact"}


def _mark_or_unreadable(value: object) -> int | bool | None:
    try:
        return unsafe_mark(value)
    except ValueError:
        return -1      # equals no mark that was ever read: the pass starts over


def _count_or_unrecorded(value: object) -> int:
    """A saved count, or -1 when the checkpoint has none (or one that is not a whole non-negative number): never 0, which would say "none left"."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else -1


class AppDocCheckpoints:
    """The checkpoint lives on the connector's app document, so it survives the process and needs no other store."""

    def __init__(self, graph_provider: IGraphDBProvider) -> None:
        self._graph = graph_provider

    async def load(self, app_id: str) -> Progress | None:
        doc = await self._graph.get_document(app_id, CollectionNames.APPS.value, raise_on_error=True)  # a failed read is not "no checkpoint"
        if not doc or doc.get(BACKFILL_KEYS["state"]) is None:
            return None
        structure = whole_number(doc.get(BACKFILL_KEYS["structure_generation"]))
        return Progress(
            str(doc[BACKFILL_KEYS["state"]]), doc.get(BACKFILL_KEYS["after"]), int(doc.get(BACKFILL_KEYS["version"]) or 0),
            int(doc.get(BACKFILL_KEYS["generation"]) if doc.get(BACKFILL_KEYS["generation"]) is not None else -1),
            int(doc.get(BACKFILL_KEYS["records"]) or 0), int(doc.get(BACKFILL_KEYS["stale"]) or 0), doc.get(BACKFILL_KEYS["error"]),
            structure_generation=-1 if structure is None else structure,        # a value that is not a number starts the pass over
            unsafe=_mark_or_unreadable(doc.get(BACKFILL_KEYS["unsafe"])), legacy=_count_or_unrecorded(doc.get(BACKFILL_KEYS["legacy"])))

    async def save(self, app_id: str, p: Progress, lease: RewriteLease | None = None) -> None:
        """With a lease the write is conditional on it (`LeaseLost`): a pass that lost the connector writes no checkpoint."""
        updates = {BACKFILL_KEYS[k]: getattr(p, k) for k in BACKFILL_KEYS}
        if lease:
            await lease.update_app(updates)
        elif not await self._graph.update_node(app_id, CollectionNames.APPS.value, updates):
            raise RuntimeError("the scope back-fill checkpoint was not saved")


class ScopeBackfill:
    def __init__(self, graph_provider: IGraphDBProvider, vector_db: IVectorDBService, locator: CollectionLocator,
                 logger: logging.Logger, *, page_size: int = PAGE_SIZE, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._graph, self._vdb, self._locator, self._log = graph_provider, vector_db, locator, logger
        self._page_size = page_size
        self._checkpoints = AppDocCheckpoints(graph_provider)
        from app.services.vector_db.scope_rewrite import (
            GenerationMoved,  # (it imports this module)
            ScopeRewriteJob,
        )

        self._moved = GenerationMoved
        self._membership = ScopeRewriteJob(graph_provider, vector_db, locator, logger=logger)

    async def run_connector(self, org_id: str, app_id: str, *, restart: bool = False, max_pages: int = MAX_PAGES,
                            close_open_mark: bool = False) -> Progress:
        """One pass at a time per connector: the pass holds the connector's rewrite lease (`permissions.rewrite_lease`), renewed before each page, so
        it never runs beside another back-fill or a rewrite. `ConnectorBusy` while another holds it. A killed pass leaves the lease to run out."""
        if not scope_fields_enabled():
            raise ScopeFieldsOff("turn the scope fields on (ACL_SCOPE_FIELDS) before back-filling")
        if current_scope_generation() < 0:  # UNAVAILABLE_GENERATION: before any read, write or checkpoint
            raise ScopeGenerationUnavailable("the scope generation could not be read; fix the configuration store and run again")
        if not org_id:
            raise UnknownConnector("an organisation is required")
        with for_org(org_id, job="scope-backfill"):
            if not (await self._graph.permission_stamp_graph().app(org_id, app_id)).connector_type:
                raise UnknownConnector(f"{app_id} is not a connector of this organisation")
            lease = RewriteLease(self._graph, app_id, clock=self._clock)
            try:
                acquired = await lease.acquire()
            except Exception as exc:  # the first write of a run now: an app schema without the lease key refuses it
                raise CheckpointRefused(_old_schema(app_id, "lease", exc)) from exc
            if not acquired:
                raise ConnectorBusy(f"{app_id} is held by another back-fill or rewrite; run again when it has finished or its lease has run out")
            self._membership.attach(lease)
            try:
                progress = await self._pass(org_id, app_id, lease, restart=restart, max_pages=max_pages, close_open_mark=close_open_mark)
            except LeaseLost as exc:  # (release is a no-op unless the lease is still ours)
                await lease.release()
                raise ConnectorBusy(f"{app_id} was taken over by another back-fill or rewrite; this pass wrote nothing more. Run again when it has finished") from exc
            except Exception:
                await lease.release()
                raise
            await lease.release()
            return progress

    async def _pass(self, org_id: str, app_id: str, lease: RewriteLease, *, restart: bool, max_pages: int, close_open_mark: bool) -> Progress:
        stamps = self._graph.permission_stamp_graph()
        p = await self._checkpoints.load(app_id)
        # What the connector's structure was BEFORE any record is read: a change that commits after this moves the generation, so the
        # connector is not current for stamps written from a graph that may predate it (see `permissions.currency`).
        app = await self._graph.get_document(app_id, CollectionNames.APPS.value, raise_on_error=True) or {}
        structure = structure_generation_of(app)
        try:
            mark = unsafe_mark(app.get(UNSAFE_KEY))
        except ValueError:
            structure = None
        in_flight = unclosed_writes(app, allow_closed_ahead=close_open_mark)
        if in_flight is None:
            structure = None
        if structure is None:  # a stored generation, mark or counter that is not readable is not read as "never changed" or "none": nothing is written
            raise UnreadableStructureState(
                f"the structure state of {app_id} is not readable: generation {app.get(GENERATION_KEY)!r}, unsafe mark {app.get(UNSAFE_KEY)!r}, "
                f"opened {app.get(OPENED_KEY)!r}, closed {app.get(CLOSED_KEY)!r}")
        if in_flight != 0:
            if not close_open_mark:
                raise OpenMarkNotClosed(
                    f"{app_id} has {in_flight} unclassified write(s) that started and never wrote their closing mark, so a pass cannot tell whether "
                    "they finished. If no write of this organisation's connectors is still running, run again with --close-open-mark.")
            # The operator says the writes are over: close them and write a mark now, which the pass that starts after it covers.
            mark = new_unsafe_mark()
            if not await self._graph.update_node(app_id, CollectionNames.APPS.value, {CLOSED_KEY: app.get(OPENED_KEY) or 0, OPEN_WRITES_KEY: None, UNSAFE_KEY: mark}):
                raise CheckpointRefused(f"the open writes of {app_id} could not be closed")
        # A checkpoint of another field version, scope generation, structure generation or unsafe mark says nothing about the points now:
        # start over. Nothing clears the mark: the pass records the value it read, and the connector is current only while it still reads
        # the same (a mark written during the pass is a different value, and nothing the pass does can erase it).
        generation = current_scope_generation()
        if (p is None or restart or p.version != SCOPE_FIELDS_VERSION or p.generation != generation or p.structure_generation != structure
                or not same_mark(p.unsafe, mark) or p.legacy < 0):
            p = Progress(generation=generation, structure_generation=structure, unsafe=mark)
            try:
                await self._checkpoints.save(app_id, p, lease)
            except LeaseLost:
                raise
            except Exception as exc:  # the first write of a run: nothing else has been written
                raise CheckpointRefused(_old_schema(app_id, "checkpoint", exc)) from exc
        if p.state == DONE:
            await clear_pending_covered(self._graph, app_id, p.structure_generation)
            return p
        p.state, p.error = RUNNING, None
        try:
            for _ in range(max_pages):
                await lease.renew()      # outside the virtual-record locks (the app document is written only there)
                ids = await stamps.record_page(org_id, app_id, p.after, self._page_size)
                if not ids:
                    p.state = DONE
                    break
                nodes = await stamps.nodes(org_id, frozenset(ids))
                vrids = sorted({n.vrid for n in nodes.values() if n.vrid and not n.deleted})
                # one scan for the page, taken before the records are locked: a point indexed after it was stamped by its indexer from the graph then
                scanned = await self._membership.scan_page(org_id, vrids) if vrids else {}
                outcomes = {vrid: await self._stamp(org_id, app_id, vrid, scanned) for vrid in vrids}
                stale_vrids = {vrid for vrid, outcome in outcomes.items() if outcome == "stale"}
                page_legacy = sum(1 for outcome in outcomes.values() if outcome == "legacy")
                # a virtual record is counted once, whichever step left it on the stale marker; this raises only for a write the store did not apply
                page_stale = len(stale_vrids | await self._membership.refresh_membership(org_id, app_id, vrids))
                # counted with the page, so a page written again after a failure is not counted twice
                moved = replace(p, after=ids[-1], records=p.records + len(ids), stale=p.stale + page_stale, legacy=p.legacy + page_legacy)
                await self._checkpoints.save(app_id, moved, lease)  # only after the page is written
                p = moved
        except LeaseLost:
            raise      # another holder owns the connector and its checkpoint: this pass ends without writing either
        except Exception as exc:
            p.state, p.error = FAILED, type(exc).__name__
            SCOPE_STAMP_FAILED.inc("backfill")
            self._log.warning("scope back-fill of %s stopped at %s: %s", app_id, p.after, p.error)
        await self._checkpoints.save(app_id, p, lease)
        if p.state == DONE:  # the pass wrote every stamp from the graph as of its generation, so it covers the roots registered up to it
            await clear_pending_covered(self._graph, app_id, p.structure_generation)
            if p.stale:
                self._log.warning("scope back-fill of %s is done and current with %d virtual record(s) on the stale marker: their points are read "
                                  "with the legacy filter until the pass is run again", app_id, p.stale)
        return p

    async def _stamp(self, org_id: str, app_id: str, vrid: str, scanned: Mapping[str, Mapping[str, dict[str, Any]]]) -> str:
        """`done`, `legacy` (written, but as a stamp that decides nothing), `skipped` (no point to write) or `stale` (the stale marker was written and read back; one that did not land raises, so the
        pass fails: see `_mark`)."""
        collections: list[str] | tuple[str, ...] = ()
        floor, rounds, moves = 0, 0, 0      # (floor: a plausible fence a point carried above this pass's time: the next round writes above it)
        try:
            async with vrid_write_locks([vrid]):
                while rounds < MAX_ROUNDS and moves <= MAX_MOVES:
                    epoch, at = await self._membership.begin(app_id)      # before the graph is read: the generation to check and the fence
                    state = await resolve_virtual_record_state(self._graph, vrid)
                    collections = self._locator.collections_for_records(state.records)
                    if not collections:
                        return "skipped"
                    fields = await scope_fields_for_state(self._graph, state, org_id, self._log)
                    if fields == STALE_SCOPE:
                        await self._mark(org_id, app_id, vrid)
                        return "stale"
                    try:
                        taken, floor = await self._membership.write_payload(org_id, app_id, collections, vrid, fields.to_payload(), epoch, at, floor, scanned)
                    except self._moved:
                        moves += 1      # the connector changed after the graph was read: nothing was sent, read it again (a move costs no round)
                        continue
                    if floor < 0:      # a fence no round will pass: every try would be refused again
                        self._log.error("virtual record %s carries a fence that is no plausible time; marked stale until it is indexed again", vrid)
                        break
                    rounds += 1
                    # the graph may have changed between the read and the write: write again if it says something else now
                    again = await scope_fields_for_state(self._graph, await resolve_virtual_record_state(self._graph, vrid), org_id, self._log)
                    if taken and again == fields:
                        return "done" if fields.exact else "legacy"
                await self._mark(org_id, app_id, vrid)
                return "stale"
        except (StaleMarkerNotWritten, LeaseLost):
            raise
        except Exception as exc:
            SCOPE_STAMP_FAILED.inc("backfill")
            self._log.warning("scope fields of %s not written: %s", vrid, type(exc).__name__)
            await self._mark(org_id, app_id, vrid)
            return "stale"

    async def _mark(self, org_id: str, app_id: str, vrid: str) -> None:
        """The stale marker, through the engine's: written by id whatever fence the points carry, at the database's time on pgvector, and read
        back. Tried again with a growing wait; raises (logged at error with the virtual record, counted) when it still did not land, which fails
        the pass: the point then keeps what it holds, which may admit."""
        delay = MARKER_DELAY_SECONDS
        for attempt in range(MARKER_ATTEMPTS):
            try:
                return await self._membership.mark_stale(org_id, app_id, [vrid])
            except LeaseLost:
                raise      # not a store fault: another holder owns the connector, and writes the marker if it still wants one
            except Exception as exc:
                last = exc
            if attempt + 1 < MARKER_ATTEMPTS:
                await asyncio.sleep(delay)
                delay *= 2
        SCOPE_STAMP_FAILED.inc("marker")
        self._log.error("scope fields of virtual record %s may be wrong and could not be marked stale (%s); run the back-fill again", vrid, type(last).__name__)
        raise StaleMarkerNotWritten(vrid) from last


async def status(graph_provider: IGraphDBProvider, org_id: str, app_id: str) -> Progress | None:
    """The saved progress of a connector of this organisation; `UnknownConnector` for any other app."""
    with for_org(org_id, job="scope-backfill"):
        if not (await graph_provider.permission_stamp_graph().app(org_id, app_id)).connector_type:
            raise UnknownConnector(f"{app_id} is not a connector of this organisation")
        return await AppDocCheckpoints(graph_provider).load(app_id)
