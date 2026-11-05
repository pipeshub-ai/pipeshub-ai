"""The lexical leg: the vetted ``retrieval.search_sparse`` function (BM25-style scores over the postings, the membership arms and
tombstones applied inside it, the org taken from the unit of work), joined to the point ids.

The function cannot see the extra conditions on promoted point columns (entity or block), so they are applied to the rows it returns:
it is asked for ``lexical_overfetch`` rows per row wanted when there are any, and the answer may then be shorter than asked.
Parameters: ``$1`` org, ``$2`` collection, ``$3`` term ids, ``$4`` weights, ``$5``-``$9`` the scope, ``$10`` the rows the function
returns, ``$11`` the rows kept, then the scope's extra conditions.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

from app.core.db import DbError, DbTimeout
from app.telemetry.modules import pgvector_search_metrics as telemetry

if TYPE_CHECKING:
    from app.core.db import KernelDB, Tx
    from app.services.vector_db.models import SparseVector
    from app.services.vector_db.pgvector.tuning import Tuning
    from app.services.vector_db.pgvector.visibility import Scope

logger = logging.getLogger(__name__)
MAX_TERMS = 20_000          # what search_sparse accepts
MAX_LEG = 1_000             # the most rows it returns

def lexical_sql(extra: str) -> str:
    """The function's rows in score order with ``ok``: whether the scope's extra conditions (``scope.extra_sql(first_param=11)``: fixed
    text, every value a ``$n``) hold. Without extra conditions every row is ``ok``."""
    return f"""/* pipeshub:pgvector.lexical */
    SELECT p.point_sk, p.point_id, (true{extra}) AS ok
      FROM retrieval.search_sparse($2::smallint, $3::bigint[], $4::real[], $5::text[], $6::text[], $7::text[],
                                   $8::text[], $9::text[], $10::int) s
      JOIN retrieval.points p ON p.org_id = $1 AND p.point_sk = s.point_sk
     ORDER BY s.score DESC, s.point_sk"""


_FLOAT32_MAX = 3.4028234663852886e38


def terms_of(sparse: SparseVector) -> tuple[list[int], list[float]]:
    """Distinct term ids (a repeated index keeps its last weight, as a write does) and their weights; ids are unsigned 32 bit and
    weights finite numbers that fit a 32-bit float (the function's type)."""
    pairs = dict(zip((int(i) for i in sparse.indices), (float(v) for v in sparse.values), strict=True))
    if not pairs:
        raise ValueError("a lexical query has no terms")
    if len(pairs) > MAX_TERMS:
        raise ValueError(f"a lexical query takes at most {MAX_TERMS} terms")
    if any(not 0 <= i < 2**32 for i in pairs) or any(not math.isfinite(w) or abs(w) > _FLOAT32_MAX for w in pairs.values()):
        raise ValueError("a lexical query has a term id outside 0..2^32-1 or a weight that is not a finite 32-bit number")
    return list(pairs), list(pairs.values())


def rows_asked(leg: int, scope: Scope, t: Tuning) -> int:
    """Rows the first statement asks the function for: ``lexical_overfetch`` per row wanted when the extra conditions filter them."""
    return min(leg * (t.lexical_overfetch if scope.filtered else 1), MAX_LEG)


async def _run(tx: Tx, org: str, collection_id: int, sparse: SparseVector, scope: Scope, leg: int, t: Tuning) -> tuple[list[tuple[int, str]], bool]:
    """``(rows, partial)``. With extra conditions the function cannot apply them, so when its answer was full and the conditions dropped
    rows the statement is asked again for four times as many, up to ``MAX_LEG``; ``partial``: that bound was reached with fewer than
    ``leg`` rows kept although the function had more."""
    terms, weights = terms_of(sparse)
    extra, extra_params = scope.extra_sql(first_param=11)
    asked = rows_asked(leg, scope, t)
    while True:
        rows = await tx.fetch(lexical_sql(extra), org, collection_id, terms, weights, *scope.args(), asked, *extra_params)
        kept = [(r["point_sk"], str(r["point_id"])) for r in rows if r["ok"]][:leg]
        more = len(rows) >= asked                       # the function had at least as many as asked: there may be more
        if len(kept) >= leg or not more or not scope.filtered:
            return kept, False
        if asked >= MAX_LEG:
            return kept, True
        asked = min(asked * 4, MAX_LEG)


async def search(
    db: KernelDB, org: str, collection_id: int, sparse: SparseVector, scope: Scope, leg: int, t: Tuning
) -> tuple[list[tuple[int, str]], str | None]:
    """``(rows, degraded)``: the lexical leg's points in order, and why it is incomplete: ``timeout`` (it ran out of time, or no
    connection was free in time) and ``error`` (any other database failure, logged as an error) answer nothing, ``partial`` answers
    what was found within ``MAX_LEG`` rows. The dense leg still answers in every case. A malformed query raises."""
    if not 1 <= leg <= MAX_LEG:
        raise ValueError(f"a lexical leg is 1..{MAX_LEG} rows")
    terms_of(sparse)             # validated before any statement
    try:
        rows, partial = await db.run(
            lambda tx: _run(tx, org, collection_id, sparse, scope, leg, t), timeout_ms=t.h_timeout_ms(leg), custom_plan=True)
        if partial:
            telemetry.DEGRADED.inc("partial", "lexical", telemetry.leg_band(leg))
        return rows, "partial" if partial else None
    except DbTimeout:
        telemetry.TIMEOUT.inc("lexical", "lexical")
        logger.warning("lexical leg ran out of time; answering from the dense leg alone")
        reason = "timeout"
    except DbError:
        logger.error("lexical leg failed; answering from the dense leg alone", exc_info=True)
        reason = "error"
    telemetry.DEGRADED.inc(reason, "lexical", telemetry.leg_band(leg))
    return [], reason
