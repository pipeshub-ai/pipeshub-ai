"""Hide first, restamp later: the fences and the durable queue of restamp jobs.

A change that outdates the stamps of more than ``stamp.inline_max()`` other records is not restamped in the writer's unit. In that same unit (so the commit makes both
visible together) the records it reaches are **hidden** and a **job** is queued; the worker (worker.py) restamps them in batches afterwards. A reader never answers from a
stamp that is hidden. There are two kinds of hiding, and a job of either kind is a row of ``kg.restamp_queue``:

* ``rows``: every reached record that kg holds is marked with the job's id (``kg.records.fence_job``). A batch of the job stamps its rows and lifts their marks in the
  same transaction, so a row is hidden until its own new stamp is written. Only the job's batch lifts a mark: a stamp that another projection writes to a marked row (it
  compiles from the graph as it is, under the connector's lock) leaves it hidden until the job has been over it too, which costs a second look, never an answer.
* ``app``: a change to the connector as a whole (its app node, or what its stamps are compiled with) hides the connector (a queue row of kind ``app``) and restamps it
  page by page; the row is deleted with the last page.

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
from app.services.graph_db.postgres.kg.model import CODE_COLL, COLL_CODE, Key
from app.services.graph_db.postgres.kg.reader import load_sks
from app.services.graph_db.postgres.kg.writers import CAP, KgWriter
from app.telemetry.backend import METRICS_BACKEND

if TYPE_CHECKING:
    from app.core.db import KernelDB, Tx

logger = logging.getLogger(__name__)
CHANNEL = "pipeshub_kg_restamp"
BATCH = 2000                    # records of a ``rows`` job per unit: a batch compiles them from the graph, as an ``app`` job's page does
APP_PAGE = 2000
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
        got = await tx.fetch("/* pipeshub:kg.hide_read */ SELECT sk, coll, id, kind, app_sk FROM kg.records WHERE org_id = $1 AND sk = ANY($2::bigint[])", org, sks[i:i + CAP])
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
    for _ in range(locks.MAX_ROUNDS):
        async def go(tx: Tx, held: frozenset[str] = frozenset(extra)) -> tuple[bool, Job]:
            """The batch: lock, restamp the next rows or page, unhide what was restamped, touch the claim, and delete the job when nothing is left."""
            app_id = await _app_id(tx, org, job.app_sk)
            await lock_apps(tx, org, ({app_id} if app_id else set()) | held, org_wide=False)
            w = KgWriter(tx)
            cursor = job.cursor
            if job.kind == "rows":
                size = batch or BATCH
                rows = await tx.fetch("""/* pipeshub:kg.job_rows */ SELECT sk, coll, id, kind FROM kg.records WHERE org_id = $1 AND fence_job = $2 AND sk > $3 ORDER BY sk LIMIT $4""",
                                      org, job.id, int(cursor) if cursor else 0, size)
                if rows:
                    st = await stamp.stamp_keys(tx, org, {(CODE_COLL[r["coll"]], r["id"]) for r in rows}, locked=({app_id} if app_id else set()) | held)
                    # only a row that was restamped is unhidden. A row whose node is gone, or that is not a record or a group, keeps its mark: its stamp is as old as the change, and the
                    # change that removes the row (it waits in the log) takes the mark with it
                    over = [(r["sk"], r["coll"], r["id"], r["kind"]) for r in rows if (CODE_COLL[r["coll"]], r["id"]) in st.keys]
                    if over:
                        await w.fence_rows(over, None)
                    cursor = str(rows[-1]["sk"])
                left = await tx.fetchval("/* pipeshub:kg.job_more */ SELECT EXISTS (SELECT 1 FROM kg.records WHERE org_id = $1 AND fence_job = $2)", org, job.id)
                done = not left
                if left and not rows:
                    cursor = None                       # a whole pass is over and some rows could not be restamped: they stay marked; the next claim starts the pass again
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
            continue
        BATCHES.inc(job.kind)
        return out
    raise RuntimeError("restamp batch kept reaching connectors it had not locked")


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
