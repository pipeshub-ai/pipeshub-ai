"""Bringing every connector of an org to ``enforce``, so the typed graph is read from the start.

A connector created on a PostgreSQL graph has nothing to copy: its app node is projected by the drain and the backfill finds an empty copy, verifies it and the promotion makes it
``enforce``. A connector that already has nodes (an install that ran before the projection, or with it off in an earlier release) is copied, verified and promoted the same way, by the same functions
(``backfill.backfill``, which promotes in the unit of its verify); nothing here writes a mode. A connector whose verify finds a difference stays on the walk (``backfill`` demotes it), is logged and counted,
and is copied again from the first node and verified again after ``DIFFERS_RETRY_S``, doubling each time it differs again up to ``DIFFERS_RETRY_MAX_S``. A connector that cannot be verified
only because changes were still arriving (a sync running), or because its copy was cleared meanwhile, is tried again after ``RETRY_S``. A copy larger than ``BATCHES`` batches goes on at the next
pass, from its cursor.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from app import platform_events
from app.core.org_context import bind
from app.services.graph_db.postgres.kg import backfill as bf
from app.services.graph_db.postgres.kg.model import CODE_EPOCH

if TYPE_CHECKING:
    from app.core.db import KernelDB, Tx

logger = logging.getLogger(__name__)
RETRY_S = 30.0
PAGE = 1000                  # nodes per copy batch
DRAIN_ROUNDS = 5             # log rounds (of at most PAGE rows) a connector spends before its stamping and verify, per pass: a log that keeps growing is waited for, not chased
STAMP_PAGE = 2000            # records per stamping page
STAMP_PAGES = 20             # stamping pages (2,000 records each) per connector and pass; the next pass goes on from where this one stopped
BATCHES = 20                 # copy batches per connector and pass: a larger connector is copied over several passes, and the other orgs get their turn in between
CONNECTORS_AT_ONCE = 4       # connectors of an org whose turns run at the same time, each in units of its own under its own lock: one's stamping runs beside another's copy

# The connectors of the org that are not enforced at this code's epoch with a verified copy.
CANDIDATES = """/* pipeshub:kg.bootstrap_candidates */ SELECT a.id FROM graph.nodes a
    LEFT JOIN kg.node_keys k ON k.org_id = a.org_id AND k.coll = 1 AND k.id = a.id
    LEFT JOIN kg.projection_state s ON s.org_id = k.org_id AND s.app_sk = k.sk
    WHERE a.org_id = $1 AND a.coll = 'apps'
      AND NOT (coalesce(s.mode, 'legacy') = 'enforce' AND s.code_epoch = $2 AND s.verified_at IS NOT NULL)
    ORDER BY a.id"""

DIFFERS_RETRY_S = 300.0      # a connector whose verify found a difference is copied again after this long, twice as long after each further difference
DIFFERS_RETRY_MAX_S = 6 * 3600.0

_NEVER = float("inf")
_DIFFERED: dict[tuple[str, str], int] = {}                # connectors whose verify found a difference, and how many times in a row
_RECOPY: set[tuple[str, str]] = set()                      # of those, the ones whose next turn starts with a fresh copy
_HELD: dict[tuple[str, str], float] = {}                  # connectors the net took back to the walk: not tried again before this time
_STAMP_AFTER: dict[tuple[str, str], str | None] = {}      # where a connector's unfinished stamping goes on (in this process; a restart stamps again from the start)
_FILL_AFTER: dict[tuple[str, str], str] = {}              # where a connector's unfinished trash fill goes on (in this process; a restart fills again from the start)
_TOPS_AFTER: dict[tuple[str, str], int] = {}              # where a connector's unfinished pc_top fill goes on (likewise)
_MARKS_AFTER: dict[tuple[str, str], tuple[int, int, str]] = {}      # where a connector's unfinished fill of its grants' marks goes on (likewise)
_LISTING_AFTER: dict[tuple[str, str], int] = {}           # where a connector's unfinished knowledge-base listing fill goes on (likewise)
_PARENTS_AFTER: dict[tuple[str, str], tuple[int, int, str]] = {}      # where a connector's unfinished fill of its grants' node_parent goes on (likewise)


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
    and the next call goes on. Up to ``CONNECTORS_AT_ONCE`` connectors take their turn at the same time (the locks are the ones two worker processes already share, ``locks``), so
    the stamping of a connector whose copy is done does not wait for the copies of the others. Never raises for one connector: it is logged and left on the walk."""
    apps = await candidates(db, org)
    if apps and not await bf.principals_copied(db, org):      # the org's principals are shared by its connectors: copied once per pass here, not once per connector
        await bf.backfill_scope(db, org, None, page=PAGE, max_batches=BATCHES, stop=stop)
        if not await bf.principals_copied(db, org):
            return 1                                               # unfinished: the next pass goes on, and the other orgs get their turn
    # The log is the org's: drained here once, as one turn would, so that the turns' own drains do not find a page another turn has in flight and wait out RETRY_S for it.
    # A failure here holds up no turn: each turn drains again and backs off on its own failure, as it did before this drain.
    try:
        for _ in range(DRAIN_ROUNDS if apps else 0):
            if not (await bf.drain_org(db, org, page=PAGE, max_rows=PAGE, stop=stop)).rows:
                break
    except Exception:
        logger.exception("kg: the change log could not be drained before the connectors' turns; each turn drains it itself")
    gate = asyncio.Semaphore(CONNECTORS_AT_ONCE)

    async def one(app: str) -> int:
        async with gate:
            return await _turn(db, org, app, due, stop)
    return sum(await asyncio.gather(*(one(a) for a in apps)))


async def _turn(db: KernelDB, org: str, app: str, due: dict[tuple[str, str], float], stop: asyncio.Event | None) -> int:
    """One connector's turn of :func:`bring_to_enforce`: 1 when it made progress."""
    if (stop is not None and stop.is_set()) or due.get((org, app), 0.0) > time.monotonic() or _HELD.get((org, app), 0.0) > time.monotonic():
        return 0
    due[(org, app)] = _NEVER                                  # until the outcome says otherwise
    try:
        if (org, app) in _RECOPY:
            # the copy that differed is not trusted: a fresh one from the first node (its rows that no node explains go too)
            await bf.backfill_scope(db, org, app, page=PAGE, max_batches=0, restart=True)
            _RECOPY.discard((org, app))
        # Before anything verifies it: a copy of epoch 7 code is filled in place to epoch 9, one of epoch 9 to epoch 10, one of epoch 10 to epoch 11, one of epoch 11 to epoch 12 and one of
        # epoch 12 to this code's, each relabelled (an enforced one is then done), never copied again. Each fill is bounded to BATCHES pages per pass, as a copy is: an unfinished
        # fill keeps its cursor and is never verified meanwhile.
        mode = more = None
        for fill, cursors, start in ((bf.fill_trash, _FILL_AFTER, ""), (bf.fill_pc_tops, _TOPS_AFTER, 0), (bf.fill_grant_marks, _MARKS_AFTER, (0, 0, "")),
                                     (bf.fill_kb_listing, _LISTING_AFTER, 0), (bf.fill_grant_parents, _PARENTS_AFTER, (0, 0, ""))):
            mode, more = await fill(db, org, app, page=PAGE, after=cursors.get((org, app), start), max_pages=BATCHES, stop=stop)
            if more is not None:
                cursors[(org, app)] = more
                break
            cursors.pop((org, app), None)
        if more is not None:
            due[(org, app)] = 0.0
            platform_events.emit("knowledge_graph.connector.bootstrapped", outcome="filling")
            return 1
        if mode == "enforce":
            platform_events.emit("knowledge_graph.connector.bootstrapped", outcome="filled")
            due.pop((org, app), None)
            return 1
        out = await bf.backfill(db, org, app, page=PAGE, max_batches=BATCHES, drain_rounds=DRAIN_ROUNDS, stop=stop, stamp_pages=STAMP_PAGES, stamp_page=STAMP_PAGE, stamp_after=_STAMP_AFTER.get((org, app)), enforce=True)
        _STAMP_AFTER[(org, app)] = out.stamp_after
        if not out.flipped:
            if out.reason == "unfinished":
                due[(org, app)] = 0.0                             # it resumes from its cursor: nothing to wait for
                platform_events.emit("knowledge_graph.connector.bootstrapped", outcome="copying")
                return 1
            if out.reason == "differs":
                n = _DIFFERED[(org, app)] = _DIFFERED.get((org, app), 0) + 1
                wait = min(DIFFERS_RETRY_S * 2 ** min(n - 1, 10), DIFFERS_RETRY_MAX_S)
                due[(org, app)] = time.monotonic() + wait
                _STAMP_AFTER.pop((org, app), None)
                _RECOPY.add((org, app))
                platform_events.emit("knowledge_graph.connector.bootstrapped", outcome="differs")
                logger.warning("kg: connector %s differs from the graph (%d time(s) in a row) and stays on the walk; copied again in %.0f s: %s", app, n, wait,
                               out.result.summary() if out.result else "not verified")
            else:                                                 # changes still arriving, or the copy was cleared meanwhile: look again soon
                due[(org, app)] = time.monotonic() + RETRY_S
                platform_events.emit("knowledge_graph.connector.bootstrapped", outcome="waiting")
            return 0
        platform_events.emit("knowledge_graph.connector.bootstrapped", outcome="enforce")
        due.pop((org, app), None)                              # a later demotion makes it a candidate again
        _STAMP_AFTER.pop((org, app), None)
        _DIFFERED.pop((org, app), None)
        return 1
    except Exception:
        due[(org, app)] = time.monotonic() + RETRY_S
        platform_events.emit("knowledge_graph.connector.bootstrapped", outcome="error")
        logger.exception("kg: connector %s could not be taken to enforce; it stays on the walk", app)
    return 0
