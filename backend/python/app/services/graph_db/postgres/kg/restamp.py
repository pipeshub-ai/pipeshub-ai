"""Hide first, restamp later: the fences and the durable queue of restamp jobs.

A change that outdates the stamps of more than ``stamp.inline_max()`` other records is not restamped in the writer's unit. In that same unit (so the commit makes both
visible together) the records it reaches are **hidden** and a **job** is queued; the worker (worker.py) restamps them in batches afterwards. A reader never answers from a
stamp that is hidden. There are two kinds of hiding, and a job of either kind is a row of ``kg.restamp_queue``:

* ``rows``: every reached record that kg holds is marked with the job's id (``kg.records.fence_job``). A batch of the job stamps its rows and lifts their marks in the
  same transaction, so a row is hidden until its own new stamp is written. Only the job's batch lifts a mark: a stamp that another projection writes to a marked row (it
  compiles from the graph as it is, under the connector's lock) leaves it hidden until the job has been over it too, which costs a second look, never an answer.
* ``app``: a change to the connector as a whole (its app node, or what its stamps are compiled with) hides the connector (a queue row of kind ``app``) and restamps it
  page by page; the row is deleted with the last page.
* ``app`` with reason ``subtree`` (:data:`SUBTREE`): a change that reaches more than ``stamp.REACH_MAX`` records, which the writer's unit cannot find one by one in its
  time. The unit hides the connector whole and marks what it found (a bounded part of the reach, and the nodes whose children it did not read) with the job's id. The job
  then walks down from its marks in bounded batches (:func:`_walk`), marking every row below them with its id, then restamps its marked rows as a ``rows`` job does
  (:func:`_restamp_marked`); the queue row goes with the last of them, and the connector with it. Its marks are its whole state (with the cursor in its queue row): a batch
  commits or leaves no trace, so a job killed at any point goes on from its last batch. One per connector: a later change of that size marks what it found with the same
  job and sends it back to the start of its walk (:func:`_restart`).

Nothing about a job is lost with a process: marks and queue rows are committed rows, a batch is one transaction (killed mid-batch it leaves no trace), and a job whose
worker stopped heartbeating is claimed again. The reader's rule is :data:`HIDDEN`: a record is not answered from its stamp while it carries a mark or its connector has a
fenced ``app`` job.

Marks cost one row write per hidden record; the alternative of hiding by path ranges was dropped because the set a change reaches is not a path range (extra parents,
record-group members, other connectors' children).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.org_context import OrgContext, bind, spawn_detached
from app.services.graph_db.postgres.kg import locks, stamp
from app.services.graph_db.postgres.kg.locks import NeedLocks, lock_apps
from app.services.graph_db.postgres.kg.model import CODE_COLL, COLL_CODE, KIND, Key
from app.services.graph_db.postgres.kg.reader import load_sks, seeks
from app.services.graph_db.postgres.kg.writers import CAP, KgWriter
from app.telemetry.backend import METRICS_BACKEND

if TYPE_CHECKING:
    from app.core.db import KernelDB, Tx

logger = logging.getLogger(__name__)
CHANNEL = "pipeshub_kg_restamp"
BATCH = 2000                    # records of a ``rows`` job per unit: a batch compiles them from the graph, as an ``app`` job's page does
APP_PAGE = 2000
SUBTREE = "subtree"             # the reason of an ``app`` job that walks down from its marks instead of restamping the connector whole
WALK_PAGE = 200                 # marked rows a batch of a subtree walk reads the children of at once
WALK_FEW = 50                   # children by one index that such a page takes from a row; a row with more is walked on its own, BATCH children a batch
STALE_MS = 120_000              # a claimed job whose worker has not touched it for this long is taken again
MAX_ATTEMPTS = 3                # alert level, not a limit: a stuck fence only hides

# The reader's rule, on a kg.records row aliased ``r``: not answered from the stamp while hidden.
HIDDEN = ("(r.fence_job IS NOT NULL OR EXISTS (SELECT 1 FROM kg.restamp_queue q WHERE q.org_id = r.org_id AND q.app_sk = r.app_sk AND q.kind = 'app' AND q.fenced))")

# The same rule for a row of kg.scopes aliased ``s`` (written with the stamps, so as stale as its record's class). A scope counts only while its record row exists and is not
# hidden: a row whose record is gone (an older process deleted the record) is hidden too, never counted.
HIDDEN_SCOPE = "NOT EXISTS (SELECT 1 FROM kg.records r WHERE r.org_id = s.org_id AND r.sk = s.sk AND NOT " + HIDDEN + ")"

JOBS = METRICS_BACKEND.counter("pipeshub_kg_restamp_jobs_total", "Restamp jobs queued", ["kind"])
BATCHES = METRICS_BACKEND.counter("pipeshub_kg_restamp_batches_total", "Restamp batches committed", ["kind"])
FAILED = METRICS_BACKEND.counter("pipeshub_kg_restamp_failed_total", "Restamp batches that failed and will be retried", ["kind"])


class LostClaim(Exception):
    """Another worker took the job: this batch is rolled back."""


class _Beat:
    """Keeps a claim alive while its batch runs. A batch is one transaction, so its own heartbeat is invisible to other workers until it commits; this touches the claim from
    units of its own every third of the staleness. It and the batch's last touch both compare-and-set on the heartbeat the other wrote, which is why the batch takes ``lock``."""

    def __init__(self, db: KernelDB, org: str, job: Job, worker: str, stale_ms: int) -> None:
        """Start the heartbeat of ``job`` for ``worker``; ``seen`` is the last heartbeat this beat wrote (or the claim's)."""
        self._db, self._org, self._job, self._worker, self._every = db, org, job, worker, stale_ms / 3000
        self.seen = job.heartbeat_ms
        self.lock = asyncio.Lock()
        self._task = spawn_detached(self._run(), name="kg-restamp-beat")

    async def _touch(self) -> bool:
        """One touch in a unit of its own. The unit's connection is taken first and the lock only then, for the touch and the commit: the batch holds a connection and needs this
        lock for its last touch, so waiting for a connection with the lock held would make each wait for the other until the pool timed out."""
        job, held, beat = self._job, False, None
        try:
            with bind(_ctx(self._org)):
                async with self._db.uow(write=True) as tx:
                    await self.lock.acquire()
                    held = True
                    beat = await KgWriter(tx).queue_touch(job.id, self.seen, self._worker, job.attempts, job.cursor)     # None: taken over, the batch finds out at its own touch
            if beat:
                self.seen = beat                                # committed, and still under the lock
            return beat is not None
        finally:
            if held:
                self.lock.release()

    async def _run(self) -> None:
        """Touch the claim every third of the staleness until the batch stops it or the claim is lost."""
        try:
            while True:
                await asyncio.sleep(self._every)
                if not await self._touch():
                    return
        except Exception:
            logger.exception("kg restamp heartbeat stopped; the claim goes stale after %s s", self._every * 3)

    async def stop(self) -> None:
        """Stop the heartbeat and wait for its task."""
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)


@dataclass(frozen=True)
class Job:
    """A queued restamp job as a worker holds it: ``heartbeat_ms`` and ``cursor`` are the values it last wrote."""
    id: int
    app_sk: int
    kind: str
    reason: str
    fenced: bool
    heartbeat_ms: int
    attempts: int
    cursor: str | None
    enqueued_ms: int = 0


def _ctx(org: str) -> OrgContext:
    """The org context of the restamp worker, for the tenant policy of the units it opens."""
    return OrgContext(org, None, "job", "job:kg-restamp", "kg-restamp")


async def _notify(tx: Tx) -> None:
    """Wake the restamp worker at commit."""
    await tx.execute("/* pipeshub:kg.restamp_notify */ SELECT pg_notify('pipeshub_kg_restamp', '')")


async def enqueue_app(tx: Tx, org: str, app_sk: int, reason: str) -> int:
    """Hide a whole connector and queue its restamp (in the caller's unit)."""
    job = await tx.fetchval("/* pipeshub:kg.job_id */ SELECT nextval('kg.job_id_seq')")
    await KgWriter(tx).queue_add(job, app_sk, "app", reason, fenced=True)
    JOBS.inc("app")
    await _notify(tx)
    return job


async def enqueue_rows(tx: Tx, org: str, keys: set[Key], reason: str, *, skip_apps: frozenset[int] = frozenset(), locked: set[str] | None = None, org_wide: bool = False) -> int:
    """Hide the records of ``keys`` that kg holds and queue their restamp (in the caller's unit), one job per connector the records belong to, so that a job is filed under the
    connector it restamps and does not hold up another connector's flip; returns how many were hidden. A key kg has no row for is left alone: it is stamped when its own
    projection arrives, and a mark would create a row of nothing but a key. ``skip_apps``: connectors (their sks) already hidden whole by a job of their own.

    A mark is a write to the rows of their connector, and a batch of an older job of that connector lifts marks under its lock: the unit marks only the rows of connectors it holds
    (``locked``, unless ``org_wide``), else :class:`NeedLocks` is raised before anything is written. Otherwise a batch that committed after this unit's mark could lift it, and a stamp
    the change outdated would be visible with no job left to restamp it."""
    sk_of = await load_sks(tx, org, {k for k in keys if k[0] in COLL_CODE})
    by_app: dict[int, list[tuple[int, int, str, int]]] = {}
    sks = sorted(sk_of.values())
    for i in range(0, len(sks), CAP):
        # a row a subtree job has marked is hidden already and restamped by that job from the graph as it is then; taking its mark over would lose the job's walk below it
        got = await tx.fetch("""/* pipeshub:kg.hide_read */ SELECT r.sk, r.coll, r.id, r.kind, r.app_sk FROM kg.records r WHERE r.org_id = $1 AND r.sk = ANY($2::bigint[])
            AND NOT EXISTS (SELECT 1 FROM kg.restamp_queue q WHERE q.org_id = r.org_id AND q.id = r.fence_job AND q.kind = 'app' AND q.reason = $3)""", org, sks[i:i + CAP], SUBTREE)
        for r in got:
            by_app.setdefault(r["app_sk"] or 0, []).append((r["sk"], r["coll"], r["id"], r["kind"]))
    if locked is not None and not org_wide:
        want = {a for a in by_app if a not in skip_apps}
        names = {r["sk"]: r["id"] for r in await tx.fetch("/* pipeshub:kg.hide_apps */ SELECT sk, id FROM kg.node_keys WHERE org_id = $1 AND coll = 1 AND sk = ANY($2::bigint[])", org, sorted(want))}
        if 0 in want or want - names.keys():              # records of no connector, or of one whose node key is gone: no connector lock covers them
            raise NeedLocks(set(), wide=True)
        if missing := {names[a] for a in want} - locked:
            raise NeedLocks(missing)
    w = KgWriter(tx)
    for app_sk, rows in sorted(by_app.items()):
        if app_sk in skip_apps:
            continue
        job = await tx.fetchval("/* pipeshub:kg.job_id */ SELECT nextval('kg.job_id_seq')")
        await w.fence_rows(rows, job)
        await w.queue_add(job, app_sk, "rows", reason, fenced=True)
        JOBS.inc("rows")
    hid = {a: r for a, r in by_app.items() if a not in skip_apps}
    if hid:
        await _notify(tx)
    return sum(len(r) for r in hid.values())


# The rows of $2 (sks), each a seek of records_pkey, with their connector's sk and whether that connector links records across connectors.
HIDE_READ = """/* pipeshub:kg.hide_subtree_read */ SELECT r.sk, r.coll, r.id, r.kind, r.app_sk, r.fence_job, coalesce(s.cross_app, false) AS cross_app FROM unnest($2::bigint[]) AS q(sk)
    CROSS JOIN LATERAL (SELECT * FROM (SELECT * FROM kg.records x WHERE x.org_id = $1 AND x.sk >= q.sk ORDER BY x.sk LIMIT 1) x WHERE x.sk = q.sk OFFSET 0) r
    LEFT JOIN kg.projection_state s ON s.org_id = r.org_id AND s.app_sk = r.app_sk"""
# Could a row of no connector hide something below it from a fence by connector? True when one of the first $2 + 1 rows of no connector (records_app in its order) has a child
# in kg, or there are more than $2 of them: the check reads at most $2 + 1 rows and three seeks each, whatever their number, and when it cannot tell it says so.
APPLESS_PARENT = """/* pipeshub:kg.appless_parent */ SELECT count(*) > $2 OR coalesce(bool_or(
      EXISTS (SELECT 1 FROM kg.records c WHERE c.org_id = $1 AND c.parent_sk >= r.sk AND c.parent_sk <= r.sk)
      OR EXISTS (SELECT 1 FROM kg.records c WHERE c.org_id = $1 AND c.group_sk >= r.sk AND c.group_sk <= r.sk)
      OR EXISTS (SELECT 1 FROM kg.links l WHERE l.org_id = $1 AND l.parent_sk >= r.sk AND l.parent_sk <= r.sk)), false)
    FROM (SELECT r.sk FROM kg.records r WHERE r.org_id = $1 AND r.app_sk IS NULL ORDER BY r.app_sk, r.sk LIMIT $2 + 1) r"""
# shortcut: an org with more rows of no connector than this finds a large reach whole in the unit (exact, as before this job existed); keep a sticky per-org flag of "a row
# of no connector has a child", written by the projection like cross_app, if such orgs are seen with large reaches
APPLESS_SCAN = 10_000


async def enqueue_subtree(tx: Tx, org: str, keys: set[Key], *, skip_apps: frozenset[int] = frozenset(), locked: set[str] | None = None, org_wide: bool = False) -> int | None:
    """Hide whole the connectors of the records of ``keys`` that kg holds, mark those records, and queue the walk below them (in the caller's unit): one job of kind ``app`` and
    reason :data:`SUBTREE` per connector, or the one it already has, sent back to the start of its walk. Returns how many rows were marked, or None, having written nothing, when
    hiding the connectors whole would not hide everything below the marks, or the check cannot tell (:data:`APPLESS_PARENT`): a row of no connector among them or with a child
    in kg, or a connector that links records to another connector's (the walk would reach rows of connectors that are not hidden). ``skip_apps``: connectors (their sks) hidden whole by a job of their own.

    The marks are rows of their connectors: as in :func:`enqueue_rows`, the unit must hold their locks (``locked``, unless ``org_wide``) or :class:`NeedLocks` is raised first."""
    sk_of = await load_sks(tx, org, {k for k in keys if k[0] in COLL_CODE})
    sks = sorted(sk_of.values())
    rows = []
    async with seeks(tx):                  # each row by its key, each row of no connector by records_app and its children by their indexes, whatever the statistics
        for i in range(0, len(sks), CAP):
            rows += await tx.fetch(HIDE_READ, org, sks[i:i + CAP])
        if any(r["app_sk"] is None or r["cross_app"] for r in rows) or await tx.fetchval(APPLESS_PARENT, org, APPLESS_SCAN):
            return None
    by_app: dict[int, list] = {}
    for r in rows:
        if r["app_sk"] not in skip_apps and r["kind"] != KIND["apps"]:          # an app's row is never restamped by a batch (and what hangs below it is in the reach already)
            by_app.setdefault(r["app_sk"], []).append(r)
    await _need_apps(tx, org, set(by_app), locked, org_wide=org_wide)
    w = KgWriter(tx)
    have = {j.app_sk: j for j in await pending(tx, org) if j.kind == "app" and j.reason == SUBTREE}
    for app_sk, got in sorted(by_app.items()):
        job = have.get(app_sk)
        if job is None:
            jid = await tx.fetchval("/* pipeshub:kg.job_id */ SELECT nextval('kg.job_id_seq')")
            await w.queue_add(jid, app_sk, "app", SUBTREE, fenced=True)
            JOBS.inc(SUBTREE)
        else:
            jid = job.id
            await _restart(tx, org, job)
        await w.fence_rows([(r["sk"], r["coll"], r["id"], r["kind"]) for r in got if r["fence_job"] != jid], jid)
    if by_app:
        await _notify(tx)
    return sum(len(r) for r in by_app.values())


async def _need_apps(tx: Tx, org: str, app_sks: set[int], locked: set[str] | None, *, org_wide: bool) -> None:
    """The unit holds the locks of these connectors (their sks), or :class:`NeedLocks` names the ones it lacks."""
    if locked is None or org_wide or not app_sks:
        return
    names = {r["sk"]: r["id"] for r in await tx.fetch("/* pipeshub:kg.hide_apps */ SELECT sk, id FROM kg.node_keys WHERE org_id = $1 AND coll = 1 AND sk = ANY($2::bigint[])", org, sorted(app_sks))}
    if app_sks - names.keys():                       # a connector whose node key is gone: no connector lock covers it
        raise NeedLocks(set(), wide=True)
    if missing := set(names.values()) - locked:
        raise NeedLocks(missing)


async def _restart(tx: Tx, org: str, job: Job) -> None:
    """Send a subtree job back to the start of its walk, so that it walks below the marks just added too, and give its claim back: the worker that holds it finds at its next touch
    that the claim moved (:class:`LostClaim`, its batch rolled back) and the job is claimed again at once. A compare-and-set on the heartbeat, retried while a heartbeat races it."""
    w = KgWriter(tx)
    for _ in range(8):
        r = await tx.fetchrow("/* pipeshub:kg.queue_row */ SELECT heartbeat_ms, claimed_by, attempts FROM kg.restamp_queue WHERE org_id = $1 AND id = $2", org, job.id)
        if r is None:
            raise RuntimeError(f"restamp job {job.id} went while a change joined it")      # the unit rolls back: the change stays in the log
        worker = r["claimed_by"] or "kg-restart"
        if await w.queue_touch(job.id, r["heartbeat_ms"], worker, r["attempts"], None):
            await w.queue_release(job.id, worker)
            return
    raise RuntimeError(f"restamp job {job.id} could not be sent back to the start of its walk")


async def pending(tx: Tx, org: str) -> list[Job]:
    """Every queued job of the org, oldest first."""
    rows = await tx.fetch("""/* pipeshub:kg.queue_read */ SELECT id, app_sk, kind, reason, fenced, heartbeat_ms, attempts, cursor, enqueued_ms FROM kg.restamp_queue
        WHERE org_id = $1 ORDER BY id""", org)
    return [Job(r["id"], r["app_sk"], r["kind"], r["reason"], r["fenced"], r["heartbeat_ms"], r["attempts"], r["cursor"], r["enqueued_ms"]) for r in rows]


async def claim(db: KernelDB, org: str, worker: str, *, stale_ms: int = STALE_MS) -> Job | None:
    """The oldest job nobody holds (or whose holder stopped touching it), claimed by compare-and-set on its heartbeat. Staleness is judged and the heartbeat written by the database's
    clock, never the worker's: a worker with a fast clock can neither take a live claim nor leave a heartbeat in the future that would keep the job from being taken again."""
    async def go(tx: Tx) -> Job | None:
        """One claim attempt: the first claimable job whose compare-and-set touch wins."""
        rows = await tx.fetch("""/* pipeshub:kg.queue_claimable */ SELECT id, app_sk, kind, reason, fenced, heartbeat_ms, attempts, cursor, enqueued_ms FROM kg.restamp_queue
            WHERE org_id = $1 AND (claimed_by IS NULL OR heartbeat_ms < floor(extract(epoch FROM clock_timestamp()) * 1000) - $2) ORDER BY id LIMIT 8""", org, stale_ms)
        for r in rows:
            beat = await KgWriter(tx).queue_touch(r["id"], r["heartbeat_ms"], worker, r["attempts"] + 1, r["cursor"])
            if beat:
                return Job(r["id"], r["app_sk"], r["kind"], r["reason"], r["fenced"], beat, r["attempts"] + 1, r["cursor"], r["enqueued_ms"])
        return None
    with bind(_ctx(org)):
        return await db.run(go, write=True)


async def release(db: KernelDB, org: str, job: Job, worker: str) -> bool:
    """Give the claim of ``job`` back so that another worker takes it at once instead of when the claim goes stale. Only the claim: the cursor stays, and every batch committed whole, so
    the next claimant resumes where this one stopped. A failure is logged and leaves the claim to go stale, as after a crash."""
    async def go(tx: Tx) -> bool:
        return await KgWriter(tx).queue_release(job.id, worker)
    try:
        with bind(_ctx(org)):
            return await db.run(go, write=True)
    except Exception:
        logger.exception("kg restamp job %s: the claim could not be given back; it is claimed again once it is stale", job.id)
        return False


async def _app_id(tx: Tx, org: str, app_sk: int) -> str | None:
    """The id of the connector with this sk, or None (a job of records with no connector, or a connector whose key is gone)."""
    return await tx.fetchval("/* pipeshub:kg.app_id */ SELECT id FROM kg.node_keys WHERE org_id = $1 AND sk = $2 AND coll = 1", org, app_sk)


async def run_batch(db: KernelDB, org: str, job: Job, worker: str, *, batch: int | None = None, stale_ms: int = STALE_MS) -> tuple[bool, Job]:
    """One unit of a claimed job; returns (finished, the job as touched). The unit takes the connector's lock (and, when the stamps reach others, theirs), stamps the next
    rows, clears their marks, heartbeats and, when nothing is left, deletes the queue row: all or nothing. While it runs the claim is kept alive (:class:`_Beat`), so a batch
    slower than ``stale_ms`` is not taken over."""
    beat = _Beat(db, org, job, worker, stale_ms)
    try:
        return await _run_batch(db, org, job, worker, beat, batch)
    finally:
        await beat.stop()


async def _run_batch(db: KernelDB, org: str, job: Job, worker: str, beat: _Beat, batch: int | None) -> tuple[bool, Job]:
    """One batch in a unit of its own, retried with the larger lock set whenever the stamps reach a connector it does not hold."""
    extra: set[str] = set()
    wide = False
    for _ in range(locks.MAX_ROUNDS):
        async def go(tx: Tx, held: frozenset[str] = frozenset(extra), *, wide: bool = wide) -> tuple[bool, Job]:
            """The batch: lock, restamp the next rows or page, unhide what was restamped, touch the claim, and delete the job when nothing is left."""
            app_id = await _app_id(tx, org, job.app_sk)
            await lock_apps(tx, org, ({app_id} if app_id else set()) | held, org_wide=wide)
            w = KgWriter(tx)
            cursor = job.cursor
            mine = ({app_id} if app_id else set()) | held
            if job.kind == "rows":
                done, cursor = await _restamp_marked(tx, org, job, cursor, batch or BATCH, mine)
            elif job.reason == SUBTREE:
                if cursor is None or cursor.startswith("w"):
                    done, cursor = False, await _walk(tx, org, job, cursor, batch or BATCH, mine)
                else:
                    done, cursor = await _restamp_marked(tx, org, job, cursor[1:] or None, batch or BATCH, mine)
                    cursor = None if cursor is None else "s" + cursor       # stuck rows: the next claim walks from the start again (its marks only), then restamps
            else:
                if not app_id:
                    done = True
                else:
                    after = await stamp.stamp_app(tx, org, app_id, cursor, batch or APP_PAGE, locked={app_id} | held)
                    done, cursor = after is None, after or cursor
            async with beat.lock:
                t = await w.queue_touch(job.id, beat.seen, worker, job.attempts, cursor)
                if not t:
                    raise LostClaim(job.id)
            if done:
                await w.queue_done(job.id)
            return done, Job(job.id, job.app_sk, job.kind, job.reason, job.fenced, t, job.attempts, cursor, job.enqueued_ms)
        try:
            with bind(_ctx(org)):
                out = await db.run(go, write=True)
        except NeedLocks as e:
            extra |= e.apps
            wide = wide or e.wide
            continue
        BATCHES.inc(job.kind)
        return out
    raise RuntimeError("restamp batch kept reaching connectors it had not locked")


async def _restamp_marked(tx: Tx, org: str, job: Job, cursor: str | None, size: int, locked: set[str]) -> tuple[bool, str | None]:
    """One batch of restamping the rows marked with the job's id, in sk order after ``cursor``; returns (finished, the new cursor). None: a pass is over and some rows could not be
    restamped (they stay marked, and the next claim starts the pass again)."""
    rows = await tx.fetch("""/* pipeshub:kg.job_rows */ SELECT sk, coll, id, kind FROM kg.records WHERE org_id = $1 AND fence_job = $2 AND sk > $3 ORDER BY sk LIMIT $4""",
                          org, job.id, int(cursor) if cursor else 0, size)
    if rows:
        st = await stamp.stamp_keys(tx, org, {(CODE_COLL[r["coll"]], r["id"]) for r in rows}, locked=locked)
        # only a row that was restamped is unhidden. A row whose node is gone keeps its mark: its stamp is as old as the change, and the change that removes the row (it waits
        # in the log) takes the mark with it. An app's row has no stamp to restamp, so its mark is lifted (kept, it would hold the job, and a subtree job its connector, forever)
        over = [(r["sk"], r["coll"], r["id"], r["kind"]) for r in rows if (CODE_COLL[r["coll"]], r["id"]) in st.keys or r["kind"] == KIND["apps"]]
        if over:
            await KgWriter(tx).fence_rows(over, None)
        cursor = str(rows[-1]["sk"])
    left = await tx.fetchval("/* pipeshub:kg.job_more */ SELECT EXISTS (SELECT 1 FROM kg.records WHERE org_id = $1 AND fence_job = $2)", org, job.id)
    if left and not rows:
        return False, None
    return not left, cursor


# The walk of a subtree job, all under reader.seeks, each read a seek of its own index by its key as a range of one value: the next marked rows after a cursor (records_fence),
# and the rows that name one of them as their parent (records_parent) or group (records_group) or a link from it (links_pkey, the child's row by records_pkey). A page reads
# at most WALK_FEW + 1 rows by each index of each row; a row with more is walked by itself, one index at a time, after a cursor of its own.
WALK_NEXT = """/* pipeshub:kg.walk_next */ SELECT r.sk FROM kg.records r WHERE r.org_id = $1 AND r.fence_job >= $2 AND r.fence_job <= $2 AND r.sk > $3 ORDER BY r.fence_job, r.sk LIMIT $4"""
# Each read returns its rows with the parent's sk; a link whose child kg holds no row of comes back with NULL columns, so it counts against the limit.
WALK_PAGE_PARENT = """/* pipeshub:kg.walk_page_parent */ SELECT p.sk AS parent, c.sk, c.coll, c.id, c.kind, c.app_sk, c.fence_job FROM unnest($2::bigint[]) AS p(sk) CROSS JOIN LATERAL (
        SELECT c.sk, c.coll, c.id, c.kind, c.app_sk, c.fence_job FROM kg.records c WHERE c.org_id = $1 AND c.parent_sk >= p.sk AND c.parent_sk <= p.sk ORDER BY c.parent_sk, c.sk LIMIT $3) c"""
WALK_PAGE_GROUP = """/* pipeshub:kg.walk_page_group */ SELECT p.sk AS parent, c.sk, c.coll, c.id, c.kind, c.app_sk, c.fence_job FROM unnest($2::bigint[]) AS p(sk) CROSS JOIN LATERAL (
        SELECT c.sk, c.coll, c.id, c.kind, c.app_sk, c.fence_job FROM kg.records c WHERE c.org_id = $1 AND c.group_sk >= p.sk AND c.group_sk <= p.sk ORDER BY c.group_sk, c.sk LIMIT $3) c"""
WALK_PAGE_LINKS = """/* pipeshub:kg.walk_page_links */ SELECT p.sk AS parent, c.sk, c.coll, c.id, c.kind, c.app_sk, c.fence_job FROM unnest($2::bigint[]) AS p(sk) CROSS JOIN LATERAL (
        SELECT l.kind, l.child_sk FROM kg.links l WHERE l.org_id = $1 AND l.parent_sk >= p.sk AND l.parent_sk <= p.sk ORDER BY l.parent_sk, l.kind, l.child_sk LIMIT $3) l
      LEFT JOIN LATERAL (SELECT * FROM (SELECT * FROM kg.records x WHERE x.org_id = $1 AND x.sk >= l.child_sk ORDER BY x.sk LIMIT 1) s WHERE s.sk = l.child_sk OFFSET 0) c ON true"""
# One row's children by one index: by parent and by group after the child's sk $3, by links after (kind $3, child $4), returning the link even when kg holds no row of its child,
# so that the cursor moves past it.
WALK_ONE_PARENT = """/* pipeshub:kg.walk_one_parent */ SELECT c.sk, c.coll, c.id, c.kind, c.app_sk, c.fence_job FROM kg.records c WHERE c.org_id = $1 AND c.parent_sk >= $2 AND c.parent_sk <= $2
        AND c.sk > $3 ORDER BY c.parent_sk, c.sk LIMIT $4"""
WALK_ONE_GROUP = """/* pipeshub:kg.walk_one_group */ SELECT c.sk, c.coll, c.id, c.kind, c.app_sk, c.fence_job FROM kg.records c WHERE c.org_id = $1 AND c.group_sk >= $2 AND c.group_sk <= $2
        AND c.sk > $3 ORDER BY c.group_sk, c.sk LIMIT $4"""
WALK_ONE_LINKS = """/* pipeshub:kg.walk_one_links */ SELECT l.kind AS lk, l.child_sk AS lc, c.sk, c.coll, c.id, c.kind, c.app_sk, c.fence_job FROM (SELECT l.kind, l.child_sk FROM kg.links l
        WHERE l.org_id = $1 AND l.parent_sk >= $2 AND l.parent_sk <= $2 AND (l.kind, l.child_sk) > ($3::smallint, $4::bigint) ORDER BY l.parent_sk, l.kind, l.child_sk LIMIT $5) l
      LEFT JOIN LATERAL (SELECT * FROM (SELECT * FROM kg.records x WHERE x.org_id = $1 AND x.sk >= l.child_sk ORDER BY x.sk LIMIT 1) s WHERE s.sk = l.child_sk OFFSET 0) c ON true"""
LINKS = 2                       # the index of the links among a row's three


async def _walk(tx: Tx, org: str, job: Job, cursor: str | None, size: int, locked: set[str]) -> str:
    """One batch of a subtree job's walk down from its marks; returns the new cursor. Every row below a marked row that kg holds (as its child, member or by a link) is marked with the
    job's id. The marked rows are taken in sk order, so a row marked behind the position of the walk sets ``dirty`` and the walk takes another pass; it is over after a pass that
    marked nothing behind it (cursor "s": the restamp of the marks comes next). It reads kg's rows only: a child the graph names that kg's rows do not waits in the log for its own
    projection, which restamps it, and until then its connector is not current (drainer.is_current). A row of no connector is not marked: it has no class, so no reader answers
    it from its stamps, and no child in kg (``enqueue_subtree``).

    Cursor: "w<dirty>:<position>" between rows (the next marked rows are those after the position), "w<dirty>:<row>:<index>:<a>:<b>" inside the children of one row."""
    parts = [int(x) for x in cursor[1:].split(":")] if cursor else [0, 0]
    dirty = parts[0]
    async with seeks(tx):
        if len(parts) == 2:
            page = [r["sk"] for r in await tx.fetch(WALK_NEXT, org, job.id, parts[1], WALK_PAGE)]
            if not page:
                return "w0:0" if dirty else "s"
            got: dict[tuple[int, int], list] = {}
            reads = (await tx.fetch(WALK_PAGE_PARENT, org, page, WALK_FEW + 1), await tx.fetch(WALK_PAGE_GROUP, org, page, WALK_FEW + 1),
                     await tx.fetch(WALK_PAGE_LINKS, org, page, WALK_FEW + 1))
            for n, rows in enumerate(reads):
                for r in rows:
                    got.setdefault((r["parent"], n), []).append(r)
            big = next((p for p in page if any(len(got.get((p, n), [])) > WALK_FEW for n in range(len(reads)))), None)
            done = page if big is None else page[:page.index(big)]
            behind = page[-1] if big is None else big          # where the walk goes on from: a row marked at or before it is not reached in this pass
            new = await _mark(tx, org, job, [r for p in done for n in range(len(reads)) for r in got.get((p, n), [])], locked)
            dirty |= any(sk <= behind for sk in new)
            return f"w{dirty:d}:{behind}" if big is None else f"w{dirty:d}:{big}:0:0:0"
        row, idx, a, b = parts[1:]
        links = idx == LINKS
        if idx == 0:
            rows = await tx.fetch(WALK_ONE_PARENT, org, row, a, size)
        elif idx == 1:
            rows = await tx.fetch(WALK_ONE_GROUP, org, row, a, size)
        else:
            rows = await tx.fetch(WALK_ONE_LINKS, org, row, a, b, size)
        new = await _mark(tx, org, job, [r for r in rows if r["sk"] is not None], locked)
        dirty |= any(sk <= row for sk in new)
    if len(rows) == size:
        last = rows[-1]
        return f"w{dirty:d}:{row}:{idx}:{last['lk']}:{last['lc']}" if links else f"w{dirty:d}:{row}:{idx}:{last['sk']}:0"
    return f"w{dirty:d}:{row}" if links else f"w{dirty:d}:{row}:{idx + 1}:0:0"


async def _mark(tx: Tx, org: str, job: Job, rows: list, locked: set[str]) -> set[int]:
    """Mark with the job's id the rows of ``rows`` (kg.records rows) that a connector holds and the job has not marked; returns their sks. A row of another connector is marked under
    that connector's lock (:class:`NeedLocks` asks for it): a batch of another job lifts marks under it."""
    new = {r["sk"]: r for r in rows if r["app_sk"] is not None and r["fence_job"] != job.id}
    if not new:
        return set()
    await _need_apps(tx, org, {r["app_sk"] for r in new.values()}, locked, org_wide=False)
    await KgWriter(tx).fence_rows([(r["sk"], r["coll"], r["id"], r["kind"]) for r in new.values()], job.id)
    return set(new)


def _stopped(stop: asyncio.Event | None) -> bool:
    """Is the stop event set?"""
    return stop is not None and stop.is_set()


async def run_job(db: KernelDB, org: str, job: Job, worker: str, *, batch: int | None = None, max_batches: int | None = None, stale_ms: int = STALE_MS,
                  stop: asyncio.Event | None = None) -> bool:
    """Batches until the job is finished (True), ``max_batches`` ran or ``stop`` is set (False). A claim lost to another worker ends it quietly. ``stop`` is looked at before each batch,
    never inside one (a batch commits or rolls back whole): a job left unfinished keeps its marks, so what it hides stays hidden, and a stop gives the claim back so that another worker takes the job at once (after a crash it is claimed again once its claim has gone stale)."""
    n = 0
    while (max_batches is None or n < max_batches) and not _stopped(stop):
        try:
            done, job = await run_batch(db, org, job, worker, batch=batch, stale_ms=stale_ms)
        except LostClaim:
            return False
        except Exception:
            FAILED.inc(job.kind)
            raise
        n += 1
        if done:
            return True
        if job.cursor is None:
            return False                                   # a pass is over and some rows could not be restamped: they stay hidden, and the job is looked at again once its claim is stale
    if _stopped(stop):
        await release(db, org, job, worker)                # a graceful stop: the rows this job hides must not wait out the claim
    return False


async def run_jobs(db: KernelDB, org: str, worker: str, *, stale_ms: int = STALE_MS, batch: int | None = None, max_jobs: int | None = None, stop: asyncio.Event | None = None) -> int:
    """Claim and finish jobs until none is claimable or ``stop`` is set (checked before each claim and each batch); returns the jobs finished."""
    done = 0
    while (max_jobs is None or done < max_jobs) and not _stopped(stop):
        job = await claim(db, org, worker, stale_ms=stale_ms)
        if job is None:
            return done
        try:
            if await run_job(db, org, job, worker, batch=batch, stale_ms=stale_ms, stop=stop):
                done += 1
        except Exception:
            logger.exception("kg restamp job %s failed; it is claimed again after %s ms", job.id, stale_ms)
            return done
    return done


async def oldest_seconds(tx: Tx, org: str) -> float:
    """Age of the oldest fence (queued job) by the database's clock, 0 when none: the figure to alert on (a stuck fence only hides, but hides for as long)."""
    r = await tx.fetchval("/* pipeshub:kg.fence_age */ SELECT floor(extract(epoch FROM clock_timestamp()) * 1000) - min(enqueued_ms) FROM kg.restamp_queue WHERE org_id = $1 AND fenced", org)
    return 0.0 if r is None else max(0.0, float(r) / 1000)
