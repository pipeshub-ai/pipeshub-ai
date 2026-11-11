"""Bringing every connector of an org to ``enforce``, so the typed graph is read from the start.

A connector created on a PostgreSQL graph has nothing to copy: its app node is projected by the drain and the backfill finds an empty copy, verifies it and the promotion makes it
``enforce``. A connector that already has nodes (an install that ran with the projection off) is copied, verified and promoted the same way, by the same functions
(``backfill.backfill``, ``backfill.promote``); nothing here writes a mode. A connector whose verify finds a difference stays on the walk (``backfill`` demotes it) and is logged; it is
not tried again until the process restarts (``shortcut: a retry is a restart or an operator's backfill, a scheduled re-verify is the drift sampler's job``). A connector that cannot be verified
only because changes were still arriving (a sync running) is tried again after ``RETRY_S``.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from app.core.org_context import bind
from app.services.graph_db.postgres.kg import backfill as bf
from app.services.graph_db.postgres.kg.model import CODE_EPOCH
from app.telemetry.backend import METRICS_BACKEND

if TYPE_CHECKING:
    from app.core.db import KernelDB, Tx

logger = logging.getLogger(__name__)
RETRY_S = 30.0
PROMOTED = METRICS_BACKEND.counter("pipeshub_kg_bootstrap_total", "Connectors the worker took to enforce, or left on the walk, by outcome", ["outcome"])

# The connectors of the org that are not enforced at this code's epoch with a verified copy.
CANDIDATES = """/* pipeshub:kg.bootstrap_candidates */ SELECT a.id FROM graph.nodes a
    LEFT JOIN kg.node_keys k ON k.org_id = a.org_id AND k.coll = 1 AND k.id = a.id
    LEFT JOIN kg.projection_state s ON s.org_id = k.org_id AND s.app_sk = k.sk
    WHERE a.org_id = $1 AND a.coll = 'apps'
      AND NOT (coalesce(s.mode, 'legacy') = 'enforce' AND s.code_epoch = $2 AND s.verified_at IS NOT NULL)
    ORDER BY a.id"""

_NEVER = float("inf")


async def candidates(db: KernelDB, org: str) -> list[str]:
    async def go(tx: Tx) -> list[str]:
        return [r["id"] for r in await tx.fetch(CANDIDATES, org, CODE_EPOCH)]
    with bind(bf._ctx(org)):
        return await db.run(go)


async def bring_to_enforce(db: KernelDB, org: str, due: dict[tuple[str, str], float]) -> int:
    """Backfill, verify and promote every connector of ``org`` that is not enforced and whose turn (``due``: when it may be tried again) has come. Returns the connectors promoted.
    Never raises for one connector: it is logged and left on the walk."""
    promoted = 0
    for app in await candidates(db, org):
        if due.get((org, app), 0.0) > time.monotonic():
            continue
        due[(org, app)] = _NEVER                                  # until the outcome says otherwise
        try:
            out = await bf.backfill(db, org, app)
            if not out.flipped:
                if out.pending:
                    due[(org, app)] = time.monotonic() + RETRY_S      # changes were still arriving: look again soon
                    PROMOTED.inc("waiting")
                else:
                    PROMOTED.inc("differs")
                    logger.warning("kg: connector %s differs from the graph and stays on the walk: %s", app, out.result.summary() if out.result else "not verified")
                continue
            await bf.promote(db, org, app)
            PROMOTED.inc("enforce")
            promoted += 1
        except bf.Refused as e:
            due[(org, app)] = time.monotonic() + RETRY_S
            PROMOTED.inc("refused")
            logger.warning("kg: connector %s was not promoted: %s", app, e)
        except Exception:
            due[(org, app)] = time.monotonic() + RETRY_S
            PROMOTED.inc("error")
            logger.exception("kg: connector %s could not be taken to enforce; it stays on the walk", app)
    return promoted
