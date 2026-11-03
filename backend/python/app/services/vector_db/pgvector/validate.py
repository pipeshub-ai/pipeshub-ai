"""Does bit HNSW plus a halfvec rerank find the exact neighbours in this embedding space?

Binary quantisation keeps the sign of each dimension; how much ranking survives depends on the model. A space that has data is checked: sampled stored vectors are used as queries (each one's own point left out of
both answers, so the query does not find itself), and the vector-index tier (the real statement, at the real rerank depth and
settings) is compared with an exact scan of the same org. The verdict is stored per org and space (``space_checks``), so a restart
or another process does not repeat the scan; it is repeated after ``check_recheck_s``, and a check that could not finish (no
slot in the background pool, a timeout) waits ``check_backoff_s`` times the failed attempts before the next try. Below the floor
(0.95) the space is served by a bounded path and flagged ``quantization`` (see ``dense.choose``).
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.services.vector_db.pgvector import dense
from app.services.vector_db.pgvector.visibility import Scope
from app.telemetry.modules import pgvector_search_metrics as telemetry

if TYPE_CHECKING:
    from app.core.db import KernelDB, Tx
    from app.services.vector_db.pgvector.tuning import Tuning

CHECK_DEADLINE_S = 90     # wall-clock budget of one check: no further sample is started after it (each statement has its own, shorter, timeout)
LEG = 20          # the check asks for ten results the way a search does: a leg of twice the limit
SCAN_BUDGET_ROWS = 30_000_000     # rows the check may scan in total (about half a minute of exact scans)
_BOUNDS = "/* pipeshub:pgvector.check_bounds */ SELECT min(p.point_sk), max(p.point_sk) FROM retrieval.points p WHERE p.org_id = $1 AND p.collection_id = $2 AND p.space_id = $3"
_NEXT = ("/* pipeshub:pgvector.check_next */ SELECT p.point_sk, p.embedding::text AS v FROM retrieval.points p WHERE p.org_id = $1 AND p.collection_id = $2 AND p.space_id = $3 "
         "AND p.point_sk >= $4 ORDER BY p.point_sk LIMIT 1")
_LOAD = ("/* pipeshub:pgvector.space_check_load */ SELECT recall, checked_at, next_try_at, attempts, now() AS now "
         "FROM retrieval.space_checks WHERE org_id = $1 AND space_id = $2")
_SAVE = ("/* pipeshub:pgvector.space_check_save */ INSERT INTO retrieval.space_checks (org_id, space_id, recall, samples, checked_at, attempts, next_try_at) "
         "VALUES ($1, $2, $3, $4, now(), 0, NULL) ON CONFLICT (org_id, space_id) DO UPDATE SET recall = EXCLUDED.recall, "
         "samples = EXCLUDED.samples, checked_at = EXCLUDED.checked_at, attempts = 0, next_try_at = NULL")
_FAIL = ("/* pipeshub:pgvector.space_check_fail */ INSERT INTO retrieval.space_checks (org_id, space_id, attempts, next_try_at) "
         "VALUES ($1, $2, 1, now() + make_interval(secs => $3)) ON CONFLICT (org_id, space_id) DO UPDATE SET "
         "attempts = retrieval.space_checks.attempts + 1, "
         "next_try_at = now() + make_interval(secs => $3 * (retrieval.space_checks.attempts + 1))")


@dataclass(frozen=True)
class Stored:
    recall: float | None
    due: bool           # a check should run now: none finished yet, or the last one is older than check_recheck_s, and no back-off holds


def samples_for(t: Tuning, rows: int) -> int:
    """Queries to check: ``validate_sample``, fewer on a big leaf (each one is an exact scan of the org), never fewer than 5."""
    return max(5, min(t.validate_sample, SCAN_BUDGET_ROWS // max(rows, 1)))


async def load(db: KernelDB, t: Tuning, org: str, space_id: int) -> Stored:
    """The stored verdict of the org's space and whether a check is due. Call inside the org's scope."""
    async def read(tx: Tx) -> Stored:
        r = await tx.fetchrow(_LOAD, org, space_id)
        if r is None:
            return Stored(None, True)
        stale = r["checked_at"] is None or (r["now"] - r["checked_at"]).total_seconds() > t.check_recheck_s
        held = r["next_try_at"] is not None and r["next_try_at"] > r["now"]
        return Stored(r["recall"], stale and not held)

    return await db.run(read, custom_plan=True)


async def save(db: KernelDB, org: str, space_id: int, recall: float, samples: int) -> None:
    async def write(tx: Tx) -> None:
        await tx.execute(_SAVE, org, space_id, recall, samples)

    await db.run(write, write=True, idempotent=True, custom_plan=True)


async def failed(db: KernelDB, t: Tuning, org: str, space_id: int) -> None:
    """A check that could not finish: try again after ``check_backoff_s`` times the number of failed attempts."""
    async def write(tx: Tx) -> None:
        await tx.execute(_FAIL, org, space_id, t.check_backoff_s)

    await db.run(write, write=True, idempotent=True, custom_plan=True)


async def bit_recall(db: KernelDB, t: Tuning, org: str, collection_id: int, space_id: int, dim: int, metric: str,
                     *, samples: int | None = None, seed: int | None = None) -> float | None:
    """Mean recall of the vector-index tier against exact search over ``samples`` stored vectors used as queries (default
    ``validate_sample``), or None when the space holds no points. Takes a slot of the background pool (``Rejected`` when none is
    free: the caller backs off)."""
    rng = random.Random(seed)
    depth = t.rerank_depth(LEG)

    async def run(tx: Tx) -> float | None:
        await dense.admit(tx, "bg", t.check_slots, t.admission_wait_ms)
        lo, hi = await tx.fetchrow(_BOUNDS, org, collection_id, space_id) or (None, None)
        if lo is None:
            return None
        await dense.vector_index_settings(tx, t, depth)
        seen: list[float] = []
        deadline = time.monotonic() + CHECK_DEADLINE_S
        for _ in range(samples or t.validate_sample):
            if time.monotonic() >= deadline:
                break                  # the mean of the samples taken so far is the verdict
            row = await tx.fetchrow(_NEXT, org, collection_id, space_id, rng.randint(lo, hi))
            if row is None:
                continue
            q = dense.Query(org, collection_id, space_id, dim, metric, row["v"], Scope(), LEG + 1, anti=True)
            own = row["point_sk"]       # the query's own point is rank 1 of both answers and says nothing: leave it out
            want = [r["point_sk"] for r in await tx.fetch(dense.exact_sql(q), *q.args(LEG + 1)) if r["point_sk"] != own][:LEG]
            got = {r["point_sk"] for r in await tx.fetch(dense.index_sql(q), *q.args(LEG + 1, depth)) if r["point_sk"] != own}
            if want:
                seen.append(len(set(want) & got) / len(want))
        return sum(seen) / len(seen) if seen else None

    mean = await db.run(run, timeout_ms=60_000, custom_plan=True)       # per statement, and never above the org's own limit
    if mean is not None:
        telemetry.QUANTIZATION_RECALL.observe("ok" if mean >= t.validate_floor else "below_floor", value=mean)
    return mean
