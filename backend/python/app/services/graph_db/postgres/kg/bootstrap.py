"""Bringing every connector of an org to ``enforce``, so the typed graph is read from the start.

A connector created on a PostgreSQL graph has nothing to copy: its app node is projected by the drain and the backfill finds an empty copy, verifies it and the promotion makes it
``enforce``. A connector that already has nodes (an install that ran with the projection off) is copied, verified and promoted the same way, by the same functions
(``backfill.backfill``, ``backfill.promote``); nothing here writes a mode. A connector whose verify finds a difference stays on the walk (``backfill`` demotes it) and is logged; it is
not tried again until the process restarts (``shortcut: a retry is a restart or an operator's backfill, a scheduled re-verify is the drift sampler's job``). A connector that cannot be verified
only because changes were still arriving (a sync running), or because its copy was cleared meanwhile, is tried again after ``RETRY_S``. A copy larger than ``BATCHES`` batches goes on at the next
pass, from its cursor.
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
    import asyncio

    from app.core.db import KernelDB, Tx

logger = logging.getLogger(__name__)
RETRY_S = 30.0
PAGE = 1000                  # nodes per copy batch
DRAIN_ROUNDS = 5             # log rounds (of at most PAGE rows) a connector spends before its stamping and verify, per pass: a log that keeps growing is waited for, not chased
STAMP_PAGE = 2000            # records per stamping page
STAMP_PAGES = 20             # stamping pages (2,000 records each) per connector and pass; the next pass goes on from where this one stopped
BATCHES = 20                 # copy batches per connector and pass: a larger connector is copied over several passes, and the other orgs get their turn in between
PROMOTED = METRICS_BACKEND.counter("pipeshub_kg_bootstrap_total", "Connectors the worker took to enforce, or left on the walk, by outcome", ["outcome"])

# The connectors of the org that are not enforced at this code's epoch with a verified copy.
CANDIDATES = """/* pipeshub:kg.bootstrap_candidates */ SELECT a.id FROM graph.nodes a
    LEFT JOIN kg.node_keys k ON k.org_id = a.org_id AND k.coll = 1 AND k.id = a.id
    LEFT JOIN kg.projection_state s ON s.org_id = k.org_id AND s.app_sk = k.sk
    WHERE a.org_id = $1 AND a.coll = 'apps'
      AND NOT (coalesce(s.mode, 'legacy') = 'enforce' AND s.code_epoch = $2 AND s.verified_at IS NOT NULL)
    ORDER BY a.id"""

_NEVER = float("inf")
_HELD: dict[tuple[str, str], float] = {}                  # connectors the net took back to the walk: not tried again before this time
_STAMP_AFTER: dict[tuple[str, str], str | None] = {}      # where a connector's unfinished stamping goes on (in this process; a restart stamps again from the start)


def hold(org: str, app: str, seconds: float) -> None:
    """Leave the connector alone for ``seconds``: it was just taken back because it differed, and copying it again at once would only repeat that."""
    _HELD[(org, app)] = time.monotonic() + seconds


async def candidates(db: KernelDB, org: str) -> list[str]:
    async def go(tx: Tx) -> list[str]:
        return [r["id"] for r in await tx.fetch(CANDIDATES, org, CODE_EPOCH)]
    with bind(bf._ctx(org)):
        return await db.run(go)


async def bring_to_enforce(db: KernelDB, org: str, due: dict[tuple[str, str], float], stop: asyncio.Event | None = None) -> int:
    """Backfill, verify and promote every connector of ``org`` that is not enforced and whose turn (``due``: when it may be tried again) has come. Returns the connectors it made progress
    on (promoted, or copied a further batch of), so a caller that loops on work looks again at once. A copy is bounded to ``BATCHES`` batches per call and ends at ``stop``: it keeps its cursor
    and the next call goes on. Never raises for one connector: it is logged and left on the walk."""
    progress = 0
    apps = await candidates(db, org)
    if apps and not await bf.principals_copied(db, org):      # the org's principals are shared by its connectors: copied once per pass here, not once per connector
        await bf.backfill_scope(db, org, None, page=PAGE, max_batches=BATCHES, stop=stop)
        if not await bf.principals_copied(db, org):
            return 1                                               # unfinished: the next pass goes on, and the other orgs get their turn
    for app in apps:
        if stop is not None and stop.is_set():
            break
        if due.get((org, app), 0.0) > time.monotonic() or _HELD.get((org, app), 0.0) > time.monotonic():
            continue
        due[(org, app)] = _NEVER                                  # until the outcome says otherwise
        try:
            out = await bf.backfill(db, org, app, page=PAGE, max_batches=BATCHES, drain_rounds=DRAIN_ROUNDS, stop=stop, stamp_pages=STAMP_PAGES, stamp_page=STAMP_PAGE, stamp_after=_STAMP_AFTER.get((org, app)))
            _STAMP_AFTER[(org, app)] = out.stamp_after
            if not out.flipped:
                if out.reason == "unfinished":
                    due[(org, app)] = 0.0                             # it resumes from its cursor: nothing to wait for
                    PROMOTED.inc("copying")
                    progress += 1
                elif out.reason == "differs":
                    PROMOTED.inc("differs")
                    logger.warning("kg: connector %s differs from the graph and stays on the walk: %s", app, out.result.summary() if out.result else "not verified")
                else:                                                 # changes still arriving, or the copy was cleared meanwhile: look again soon
                    due[(org, app)] = time.monotonic() + RETRY_S
                    PROMOTED.inc("waiting")
                continue
            await bf.promote(db, org, app)
            PROMOTED.inc("enforce")
            due.pop((org, app), None)                              # a later demotion makes it a candidate again
            _STAMP_AFTER.pop((org, app), None)
            progress += 1
        except bf.Refused as e:
            due[(org, app)] = time.monotonic() + RETRY_S
            PROMOTED.inc("refused")
            logger.warning("kg: connector %s was not promoted: %s", app, e)
        except Exception:
            due[(org, app)] = time.monotonic() + RETRY_S
            PROMOTED.inc("error")
            logger.exception("kg: connector %s could not be taken to enforce; it stays on the walk", app)
    return progress
