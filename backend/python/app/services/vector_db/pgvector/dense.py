"""The dense leg: how many candidates a query has, which tier serves them, one statement per tier, admission and the degraded path.

Recall comes from placement and tiering, not from a probe of each answer:

* **e0** exact scan of every candidate while there are at most ``n_e0`` of them (recall 1.000), in any leaf, at any share;
* **h** bit HNSW with a halfvec rerank of the best ``r`` candidates, for a larger org that owns its leaf or holds less than
  ``promotion_share`` of a shared one;
* **e0x** exact scan up to four times ``n_e0`` and **h_deep** (a deeper rerank above that) for an org that holds a big share of a
  shared leaf and awaits its promotion;
* the **bounded exact** path (the newest ``n_e0`` candidates, flagged as degraded) when no admission slot is free or a tier runs out of
  time. A blind HNSW over a shared leaf is never issued: every H statement runs with iterative scan on.

The membership filter is :mod:`visibility`'s, shared with the lexical leg; the SQL of the exact tiers asks for the groups first, the
vector index asks per row. Parameters are fixed: ``$1`` org, ``$2`` collection, ``$3`` space, ``$4`` query, ``$5``-``$9`` the scope,
``$10`` the number of rows, ``$11`` the rerank depth, then the scope's extra conditions.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import random
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from app.core.db import DbError, DbTimeout
from app.core.org_context import spawn_in_request
from app.services.vector_db.pgvector.errors import PgVectorError
from app.telemetry.modules import pgvector_search_metrics as telemetry

if TYPE_CHECKING:
    from app.core.db import KernelDB, Tx
    from app.services.vector_db.pgvector.tuning import Tuning
    from app.services.vector_db.pgvector.visibility import Scope

logger = logging.getLogger(__name__)
ADMISSION_CLASS = 7370           # advisory-lock class of the admission slots (registered in kernel.lock_keys by retrieval_0001)
_OPERATORS = {"cosine": "OPERATOR(ext.<=>)", "dot": "OPERATOR(ext.<#>)", "l2": "OPERATOR(ext.<->)"}
_SCOPE_TYPES = "_s AS (SELECT $4::ext.halfvec, $5::text[], $6::text[], $7::text[], $8::text[], $9::text[], $10::int, $11::int)"      # types every parameter, used or not
# Two forms of "not tombstoned". The hashed subplan is built once per statement and is the cheaper one (the per-row probe cost 29 % of an
# exact scan when there are no tombstones), but PostgreSQL hashes it only while the set fits work_mem; past that it becomes a list scan
# per candidate row (minutes). So: the hashed form only up to ``tombstone_hash_max`` tombstones, with work_mem raised for it; above that,
# and on every path that must stay bounded whatever the count (the degraded path, the background scans), the anti-join, one primary-key
# probe per candidate row, which costs the same at any count.
_ALIVE_HASHED = "NOT (p.point_sk = ANY (SELECT t.point_sk FROM retrieval.point_tombstones t WHERE t.org_id = $1))"
_ALIVE_ANTI = "NOT EXISTS (SELECT 1 FROM retrieval.point_tombstones t WHERE t.org_id = $1 AND t.point_sk = p.point_sk)"
# The vector-index tier looks at a few hundred rows per statement, so it always tests them one by one. OFFSET 0 is the barrier that keeps
# the planner from pulling the NOT EXISTS up into an anti-join (which drops the HNSW index and scans the whole org) and from hashing
# the whole tombstone set once per statement; the cost is one primary-key probe per row the graph walk returns, at any tombstone count.
_ALIVE_ROW = "NOT EXISTS (SELECT 1 FROM retrieval.point_tombstones t WHERE t.org_id = $1 AND t.point_sk = p.point_sk OFFSET 0)"
_BASE = "p.org_id = $1 AND p.collection_id = $2 AND p.space_id = $3"


class Tier(StrEnum):
    E0 = "e0"
    E0X = "e0x"
    H = "h"
    H_DEEP = "h_deep"


@dataclass(frozen=True)
class Sizing:
    """What the chooser knows about a query. ``n_hat`` is the number of candidates the filter admits, counted up to a cap
    (``capped``: it is at least that many)."""

    n_hat: int
    capped: bool
    own_leaf: bool
    leaf_rows: int
    tombstones: int = 0          # the org's tombstones, counted up to tombstone_hash_max + 1

    @property
    def share(self) -> float:
        """Share of the leaf (a lower bound when capped); 0 while the leaf's size is unknown (never analysed)."""
        return self.n_hat / max(self.leaf_rows, self.n_hat) if self.leaf_rows > 0 else 0.0


@dataclass(frozen=True)
class Plan:
    tier: Tier
    r: int
    timeout_ms: int
    slot: str | None
    share_band: str
    flag: str | None = None          # the answer is bounded by design (the bit index failed its recall check for this space)


def share_band(sz: Sizing, theta: float) -> str:
    return "own_leaf" if sz.own_leaf else "awaiting_promotion" if sz.share >= theta else "lt_1pct" if sz.share < 0.01 else "1pct_to_theta"


def size_band(n: int, n_e0: int) -> str:
    return "le_n_e0" if n <= n_e0 else "le_200k" if n <= 200_000 else "le_1m" if n <= 1_000_000 else "gt_1m"


def count_cap(t: Tuning, sz_leaf: tuple[bool, int] | None = None) -> int:
    """How far to count candidates. A shared leaf: past 4 x n_e0 nothing changes. An org that owns its leaf: the index serves it as
    soon as it has more than ``n_own`` candidates holding ``own_min_share`` of the leaf, or more than ``n_e0``, so counting stops
    there (reaching the cap means "use the index")."""
    if sz_leaf is None or not sz_leaf[0] or sz_leaf[1] <= 0:
        return 4 * t.n_e0 + 1
    return min(max(t.n_own + 1, -(-int(t.own_min_share * 1000) * sz_leaf[1] // 1000)), t.n_e0 + 1)


def choose(sz: Sizing, leg: int, t: Tuning, *, bq_ok: bool = True) -> Plan:
    """The tier for ``sz`` (see the module docstring). ``bq_ok=False``: the bit index failed its recall check for this embedding
    space; everything above ``n_e0`` is then served by a bounded path and flagged ``quantization``: exact while there are at most
    4 x ``n_e0`` candidates, otherwise the index with a rerank window twice the deep one."""
    band = share_band(sz, t.promotion_share)
    exact_slot = "e0" if sz.n_hat > t.slot_min_candidates else None
    if sz.own_leaf and sz.capped:                # counted up to the point where the index is the better answer
        pass
    elif sz.n_hat <= t.n_e0 and not sz.capped:
        return Plan(Tier.E0, 0, t.e0_timeout_ms, exact_slot, band)
    if not bq_ok:
        if sz.n_hat <= 4 * t.n_e0 and not sz.capped:
            return Plan(Tier.E0X, 0, t.h_timeout_ms(leg), "e0", band, "quantization")
        return Plan(Tier.H_DEEP, 2 * t.rerank_depth(leg, deep=True), t.h_timeout_ms(leg), "h", band, "quantization")
    awaiting = not sz.own_leaf and sz.share >= t.promotion_share
    if not awaiting:
        return Plan(Tier.H, t.rerank_depth(leg), t.h_timeout_ms(leg), "h", band)
    if sz.n_hat <= 4 * t.n_e0 and not sz.capped:
        return Plan(Tier.E0X, 0, t.h_timeout_ms(leg), "e0", band)
    return Plan(Tier.H_DEEP, t.rerank_depth(leg, deep=True), t.h_timeout_ms(leg), "h", band)


@dataclass(frozen=True)
class Query:
    """One dense leg: which org, collection and space, the query vector as halfvec text, the scope and the number of rows."""

    org: str
    collection_id: int
    space_id: int
    dim: int
    metric: str
    vector: str
    scope: Scope
    leg: int
    anti: bool = False           # test for tombstones by anti-join (robust at any count) instead of a hashed set

    def alive(self) -> str:
        return _ALIVE_ANTI if self.anti else _ALIVE_HASHED

    def args(self, rows: int, depth: int = 0) -> tuple[object, ...]:
        """The parameters ``$1`` to ``$11`` and the scope's extra conditions after them."""
        return (self.org, self.collection_id, self.space_id, self.vector, *self.scope.args(), rows, depth, *self.scope.extra_sql()[1])


@dataclass(frozen=True)
class Leg:
    """The dense leg's answer: the points in order (``point_sk``, ``point_id``), the plan that served it and why it is degraded."""

    rows: list[tuple[int, str]]
    plan: Plan
    degraded: str | None = None


class Rejected(PgVectorError):
    """No admission slot became free in time."""


# ---- SQL ---------------------------------------------------------------------------------------------------------------------

def _dist(q: Query, alias: str) -> str:
    if q.metric not in _OPERATORS:
        raise PgVectorError(f"unknown distance metric {q.metric!r}")
    return f"{alias}.embedding {_OPERATORS[q.metric]} $4::ext.halfvec"


def exact_sql(q: Query) -> str:
    """Exact distance over every candidate. ``+ 0`` keeps the planner off the vector index; the tie-break is ``point_sk``."""
    return f"""/* pipeshub:pgvector.dense_exact */
        WITH {_SCOPE_TYPES}
        SELECT p.point_sk, p.point_id FROM retrieval.points p
         WHERE {_BASE}{q.scope.candidates()} AND {q.alive()}
         ORDER BY ({_dist(q, 'p')}) + 0, p.point_sk LIMIT $10"""


def index_sql(q: Query) -> str:
    """Bit HNSW over the candidates the filter admits, then an exact rerank of the best ``$11`` by halfvec distance."""
    d = int(q.dim)
    return f"""/* pipeshub:pgvector.dense_index */
        WITH {_SCOPE_TYPES}
        SELECT x.point_sk, x.point_id FROM (
          SELECT p.point_sk, p.point_id, p.embedding FROM retrieval.points p
           WHERE {_BASE}{q.scope.probe()} AND {_ALIVE_ROW}
           ORDER BY p.bq::bit({d}) OPERATOR(ext.<~>) ext.binary_quantize($4::ext.halfvec)::bit({d}) LIMIT $11) x
        CROSS JOIN LATERAL (SELECT {_dist(q, 'x')} AS d) z
        ORDER BY z.d, x.point_sk LIMIT $10"""


def bounded_sql(q: Query, *, narrow: bool) -> str:
    """The degraded path: exact distance over the newest ``$11`` candidates of the same filter. ``narrow`` (the filter is known to
    admit few candidates): by groups, so the work is the candidates and never a walk through the org; otherwise by the per-row probe
    (a filter that admits more than ``n_e0`` is not rare, so the walk finds its rows soon)."""
    where = q.scope.candidates() if narrow else q.scope.probe()
    return f"""/* pipeshub:pgvector.dense_bounded */
        WITH {_SCOPE_TYPES}
        SELECT x.point_sk, x.point_id FROM (
          SELECT p.point_sk, p.point_id, p.embedding FROM retrieval.points p
           WHERE {_BASE}{where} AND {_ALIVE_ANTI} ORDER BY p.point_sk DESC LIMIT $11) x
        ORDER BY ({_dist(q, 'x')}) + 0, x.point_sk LIMIT $10"""


def count_sql(q: Query) -> str:
    return f"""/* pipeshub:pgvector.dense_count */
        WITH {_SCOPE_TYPES}
        SELECT count(*) FROM (SELECT 1 FROM retrieval.points p WHERE {_BASE}{q.scope.candidates()} LIMIT $10) c"""


_TOMBSTONES = """/* pipeshub:pgvector.tombstone_count */
        SELECT count(*) FROM (SELECT 1 FROM retrieval.point_tombstones t WHERE t.org_id = $1 LIMIT $2) c"""
_LEAF = """/* pipeshub:pgvector.dense_leaf */
        SELECT c.relname, c.reltuples::bigint FROM retrieval.points p JOIN pg_class c ON c.oid = p.tableoid
         WHERE p.org_id = $1 AND p.collection_id = $2 AND p.space_id = $3 LIMIT 1"""
# one statement, no round trip per slot: the first slot (from a random start) that the instance grants, none = all taken
_ADMIT = """/* pipeshub:pgvector.admit */
        SELECT s FROM (SELECT (($2::int + g) % $1::int) AS s FROM generate_series(0, $1::int - 1) g) x
         WHERE pg_try_advisory_xact_lock(7370, $3::int + s) LIMIT 1"""      # 7370 is ADMISSION_CLASS (a test keeps the two equal)
_MAX_CONNECTORS = 1_000
# loose index scan: the next distinct member after the last one, so the cost is the number of connectors, not of memberships
_CONNECTORS = """/* pipeshub:pgvector.connectors */
        WITH RECURSIVE c(member_id) AS (
          (SELECT m.member_id FROM retrieval.group_members m WHERE m.org_id = $1 AND m.collection_id = $2 AND m.kind = 1
            ORDER BY m.member_id LIMIT 1)
          UNION ALL
          SELECT (SELECT m.member_id FROM retrieval.group_members m WHERE m.org_id = $1 AND m.collection_id = $2 AND m.kind = 1
                     AND m.member_id > c.member_id ORDER BY m.member_id LIMIT 1) FROM c WHERE c.member_id IS NOT NULL)
        SELECT member_id FROM c WHERE member_id IS NOT NULL LIMIT $3"""
# is there a point whose group has no connector row? (stops at the first one; with none it visits every point, hence the timeout)
_UNCONNECTED = """/* pipeshub:pgvector.unconnected */
        SELECT EXISTS (SELECT 1 FROM retrieval.points p WHERE p.org_id = $1 AND p.collection_id = $2
          AND NOT EXISTS (SELECT 1 FROM retrieval.group_members m WHERE m.org_id = $1 AND m.collection_id = $2
                            AND m.group_key = p.group_key AND m.kind = 1))"""
_SLOT_BASE = {"h": 0, "e0": 10_000, "bg": 20_000}      # bg: the recall sampler and the quantisation check, a pool of their own


async def admit(tx: Tx, slot: str, slots: int, wait_ms: int) -> None:
    """Takes one of ``slots`` transaction-scoped advisory locks of ``slot`` ('h', 'e0' or 'bg'), waiting up to ``wait_ms`` with a jittered
    backoff. The lock lives in the database instance, so the cap holds across processes, pods and poolers, and a transaction-mode
    pooler is safe (the lock goes at COMMIT or ROLLBACK, a timeout included). Raises :class:`Rejected` when none is free in time."""
    t0 = time.monotonic()
    deadline = t0 + wait_ms / 1000
    while True:
        if await tx.fetchval(_ADMIT, slots, random.randrange(slots), _SLOT_BASE[slot]) is not None:
            telemetry.ADMISSION_WAIT.observe(slot, value=time.monotonic() - t0)
            return
        if time.monotonic() >= deadline:
            telemetry.ADMISSION_WAIT.observe(slot, value=time.monotonic() - t0)
            telemetry.ADMISSION_REJECTED.inc(slot)
            raise Rejected(f"no {slot} admission slot became free within {wait_ms} ms")
        await asyncio.sleep(random.uniform(0.002, 0.005))


async def vector_index_settings(tx: Tx, t: Tuning, r: int) -> None:
    """Per statement: iterative scan on (the filter is applied to what the graph walk returns), a search width that covers the rerank
    depth, and scan memory of at least 256 MB whatever the organisation's work_mem limit is."""
    mb, mult = t.scan_memory()
    await tx.fetchval(
        "/* pipeshub:pgvector.index_settings */ SELECT set_config('hnsw.ef_search', $1, true), set_config('hnsw.iterative_scan', 'relaxed_order', true), "
        "set_config('hnsw.max_scan_tuples', $2, true), set_config('work_mem', $3, true), set_config('hnsw.scan_mem_multiplier', $4, true)",
        str(min(r, t.ef_cap)), str(max(200_000, 2 * r)), f"{mb}MB", str(mult))


async def _run(tx: Tx, q: Query, p: Plan, t: Tuning) -> list[tuple[int, str]]:
    if p.slot is not None:
        await admit(tx, p.slot, _slots(t, p.slot), t.admission_wait_ms)
    if p.tier in (Tier.H, Tier.H_DEEP):
        await vector_index_settings(tx, t, p.r)
        rows = await tx.fetch(index_sql(q), *q.args(q.leg, p.r))
    else:
        if not q.anti:          # the query class sets no work_mem; the hashed set of tombstones must fit it
            await tx.fetchval("/* pipeshub:pgvector.exact_memory */ SELECT set_config('work_mem', $1, true)", f"{t.scan_work_mem_mb}MB")
        rows = await tx.fetch(exact_sql(q), *q.args(q.leg))
    return [(r["point_sk"], str(r["point_id"])) for r in rows]


class Sizer:
    """Candidate counts and leaf sizes, reused for ``estimate_ttl_s`` / ``leaf_ttl_s``. A count costs up to ``count_cap`` index
    probes and is the same for every query of one filter, so it is paid once per filter and minute, not per query. Queries of one filter
    that miss together (a batch, parallel requests of one user) share one measurement instead of each running the count."""

    def __init__(self, db: KernelDB, t: Tuning) -> None:
        self._db, self._t = db, t
        self._counts: dict[tuple, tuple[float, int, bool, int]] = {}
        self._leaves: dict[tuple, tuple[float, bool, int]] = {}
        self._connectors: dict[tuple, tuple[float, frozenset[str] | None]] = {}
        self._inflight: dict[tuple, asyncio.Future] = {}
        self._connectors_inflight: dict[tuple, asyncio.Future] = {}

    async def of(self, q: Query) -> Sizing:
        """The sizing of ``q``. When the database cannot answer (timeout, no pooled connection) the last answer is used, however old;
        with none, the :class:`DbError` goes to the caller, which takes the degraded path."""
        now = time.monotonic()
        lkey = (q.org, q.collection_id, q.space_id)
        ckey = (*lkey, q.scope.key())
        leaf, count = self._leaves.get(lkey), self._counts.get(ckey)
        if leaf is None or leaf[0] < now or count is None or count[0] < now:
            try:
                leaf, count = await self._shared_measure(q, lkey, ckey, now)
            except DbError:
                if leaf is None or count is None:
                    raise
                telemetry.TIMEOUT.inc("sizing", "stale")
        return Sizing(count[1], count[2], leaf[1], leaf[2], count[3])

    async def _shared_measure(self, q: Query, lkey: tuple, ckey: tuple, now: float) -> tuple[tuple, tuple]:
        task = self._inflight.get(ckey)
        if task is None:
            task = self._inflight[ckey] = spawn_in_request(self._measure(q, lkey, ckey, now))
            task.add_done_callback(lambda t: (self._inflight.pop(ckey, None), t.cancelled() or t.exception()))      # also marks a failure as seen
        return await asyncio.shield(task)      # one waiter giving up must not cancel the measurement the others wait for

    async def _measure(self, q: Query, lkey: tuple, ckey: tuple, now: float) -> tuple[tuple, tuple]:
        async def read(tx: Tx) -> tuple[tuple[str, int] | None, int, bool]:
            row = await tx.fetchrow(_LEAF, q.org, q.collection_id, q.space_id)
            leaf = (row["relname"], max(row["reltuples"], 0)) if row else None
            own = bool(leaf and leaf[0].startswith("points_o_"))
            # No count when the whole leaf is small. The leaf's size is its statistics (reltuples, refreshed by autovacuum and reused for
            # leaf_ttl_s = 5 min), so it can be stale by a few minutes of growth. That is safe: it only decides between the exact tier and
            # the index, an org that outgrew the figure is scanned exactly for at most those minutes, the exact tier has its own timeout,
            # and a timeout takes the bounded degraded path. It can never return a wrong row.
            if leaf and 0 < leaf[1] <= self._t.n_own:
                return leaf, leaf[1], False, await tx.fetchval(_TOMBSTONES, q.org, self._t.tombstone_hash_max + 1)
            cap = count_cap(self._t, (own, leaf[1]) if leaf else None)
            n = await tx.fetchval(count_sql(q), *q.args(cap))
            tomb = await tx.fetchval(_TOMBSTONES, q.org, self._t.tombstone_hash_max + 1)
            return leaf, n, n >= cap, tomb

        leaf_row, n, capped, tomb = await self._db.run(read, custom_plan=True, timeout_ms=self._t.e0_timeout_ms * 3)
        if len(self._counts) > 4096:
            self._counts.clear()
        leaf = self._leaves[lkey] = (now + self._t.leaf_ttl_s, bool(leaf_row and leaf_row[0].startswith("points_o_")),
                                     leaf_row[1] if leaf_row else 0)
        count = self._counts[ckey] = (now + self._t.estimate_ttl_s, n, capped, tomb)
        return leaf, count

    async def connectors(self, org: str, collection_id: int) -> frozenset[str] | None:
        """The connectors of the org's collection, when a user who can read all of them may skip the membership arms; else None.

        That is safe only when there is at least one connector, there are not more than ``_MAX_CONNECTORS`` (found by hopping
        through the index) and **every point's group has a connector row**: a point without one is admitted by no connector, so a
        user's other arms (record groups, direct records) are the only way to it. The check is bounded by a short timeout; on a
        timeout or any database error there is no elimination (the arms are applied). The answer is reused for a short time, so a
        connector created inside the window is not yet in it: the arms are then dropped for a user who may not read it, for at most
        ``connector_ttl_s``."""
        key = (org, collection_id)
        hit = self._connectors.get(key)
        if hit is None or hit[0] < time.monotonic():
            task = self._connectors_inflight.get(key)
            if task is None:          # one check for the callers that miss together; it can scan every point of the org
                task = self._connectors_inflight[key] = spawn_in_request(self._check_connectors(org, collection_id), name="pgvector-connectors")
                task.add_done_callback(lambda t: (self._connectors_inflight.pop(key, None), t.cancelled() or t.exception()))
            return await asyncio.shield(task)
        return hit[1]

    async def _check_connectors(self, org: str, collection_id: int) -> frozenset[str] | None:
        async def read(tx: Tx) -> frozenset[str] | None:
            found = [r["member_id"] for r in await tx.fetch(_CONNECTORS, org, collection_id, _MAX_CONNECTORS + 1)]
            if not found or len(found) > _MAX_CONNECTORS:
                return None
            if await tx.fetchval(_UNCONNECTED, org, collection_id):
                return None
            return frozenset(found)

        ttl = self._t.connector_ttl_s
        try:
            value = await self._db.run(read, custom_plan=True, timeout_ms=self._t.connector_check_ms)
        except DbError:
            # Too big to check inside the budget (or the database is busy): keep the arms applied and do not look again soon, so a
            # large org never pays for a scan of its points every connector_ttl_s.
            value, ttl = None, self._t.connector_fail_ttl_s
        self._connectors[(org, collection_id)] = (time.monotonic() + ttl, value)
        return value


async def search(db: KernelDB, sizer: Sizer, q: Query, t: Tuning, *, bq_ok: bool = True) -> tuple[Leg, Sizing]:
    """The dense leg. Never returns a silent shortfall: a leg that could not run in its tier (no slot, a timeout, a sizing that
    could not be read) is answered by the bounded exact path, flagged on the leg (``degraded``) and counted; only when that fails too
    is it empty, which is counted as well."""
    band = telemetry.leg_band(q.leg)
    narrow = False
    try:
        sz = await sizer.of(q)
    except DbError:
        sz = Sizing(0, False, False, 0, t.tombstone_hash_max + 1)
        plan = Plan(Tier.E0, 0, t.degraded_timeout_ms, None, "unknown")
        telemetry.TIER.inc(plan.tier.value, band)
        return await _degrade(db, q, t, plan, sz, "sizing", narrow=False, band=band), sz
    plan = choose(sz, q.leg, t, bq_ok=bq_ok)
    q = dataclasses.replace(q, anti=sz.tombstones > t.tombstone_hash_max)
    telemetry.TIER.inc(plan.tier.value, band)
    narrow = sz.n_hat <= t.n_e0 and not sz.capped
    try:
        rows = await db.run(lambda tx: _run(tx, q, plan, t), timeout_ms=plan.timeout_ms, custom_plan=True)
        return Leg(rows, plan, plan.flag), sz
    except Rejected:
        reason = "admission"
    except DbTimeout:
        reason = "timeout"
        telemetry.TIMEOUT.inc("dense", plan.tier.value)
    return await _degrade(db, q, t, plan, sz, reason, narrow=narrow, band=band), sz


async def _degrade(db: KernelDB, q: Query, t: Tuning, plan: Plan, sz: Sizing, reason: str, *, narrow: bool, band: str) -> Leg:
    """Exact over at most ``degraded_rows`` of the newest candidates, without a slot and inside its own short budget. The work is
    capped well below an exact tier's, so a rejection sheds load instead of repeating it."""
    telemetry.DEGRADED.inc(reason, plan.tier.value, band)
    logger.warning("dense leg degraded (%s) in tier %s: answering from at most %d of the newest candidates", reason, plan.tier.value, t.degraded_rows)
    try:
        rows = await db.run(lambda tx: _bounded(tx, q, t, narrow=narrow), timeout_ms=t.degraded_timeout_ms, custom_plan=True)
        return Leg(rows, plan, reason)
    except DbError:
        telemetry.DEGRADED.inc("overload", plan.tier.value, band)
        telemetry.EMPTY_LEG.inc(plan.tier.value)
        return Leg([], plan, "overload")


async def _bounded(tx: Tx, q: Query, t: Tuning, *, narrow: bool) -> list[tuple[int, str]]:
    rows = await tx.fetch(bounded_sql(q, narrow=narrow), *q.args(q.leg, t.degraded_rows))
    return [(r["point_sk"], str(r["point_id"])) for r in rows]


async def exact(db: KernelDB, q: Query, *, timeout_ms: int, t: Tuning, slot: str | None = "bg") -> list[tuple[int, str]]:
    """Exact top rows over the filter's candidates, for the recall sampler and the quantisation check (own unit of work; takes a slot
    of the background pool unless ``slot`` is None; the sampler skips the sample when none is free)."""
    async def run(tx: Tx) -> list[tuple[int, str]]:
        if slot:
            await admit(tx, slot, _slots(t, slot), t.admission_wait_ms)
        # the statement budget is the unit of work's (``timeout_ms`` below, and never above the org's own limit): not set again here
        await tx.fetchval("/* pipeshub:pgvector.exact_settings */ SELECT set_config('max_parallel_workers_per_gather', '2', true), "
                          "set_config('work_mem', $1, true)", f"{t.scan_work_mem_mb}MB")
        return [(r["point_sk"], str(r["point_id"])) for r in await tx.fetch(exact_sql(q), *q.args(q.leg))]

    return await db.run(run, timeout_ms=timeout_ms, custom_plan=True)


def _slots(t: Tuning, slot: str) -> int:
    return {"h": t.h_slots, "e0": t.e0_slots, "bg": t.check_slots}[slot]
