"""The flush (the writer's own transaction) and the take-back of the change log.

``install(db)`` hooks a ``KernelDB``: before a statement that writes graph.nodes or graph.edges the guard (guard.py) is asked; just before COMMIT of a
write unit of the connectors service that wrote the graph, :func:`flush` re-projects the nodes its own changes named (rows the capture triggers
inserted in this very transaction) and acknowledges them. A projection error never fails the connector's write: it is rolled back to a savepoint,
logged and counted, and the change log rows stay. Strict mode (``PG_KG_STRICT=1``, tests) re-raises instead. Nothing drains the log in the background yet (the drain is the next
change): with ``PG_KG_PROJECT=1`` the rows of other roles, and those of a failed flush, wait there.

``RuntimeError`` from :func:`project` (it kept reaching connectors it had not locked, nothing written) is "retry later": the rows stay in the log and are never acknowledged as done.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import time
from typing import TYPE_CHECKING

from app.core.org_context import OrgContext, bind, spawn_detached
from app.services.graph_db.postgres.kg import guard, modes
from app.services.graph_db.postgres.kg.project import decode, project
from app.services.graph_db.postgres.kg.writers import KgWriter
from app.telemetry.backend import METRICS_BACKEND

if TYPE_CHECKING:
    from app.core.db import KernelDB, Tx

logger = logging.getLogger(__name__)
FLUSH_FAILED = METRICS_BACKEND.counter("pipeshub_kg_flush_failed_total", "Projections in a writer's transaction that failed and left the change log rows", ["where"])
FLUSHED = METRICS_BACKEND.counter("pipeshub_kg_flushed_nodes_total", "Nodes projected by a writer's own transaction", ["where"])


_CHOICES = {"PG_KG_PROJECT": ("0", "1"), "PG_KG_FLUSH": ("async", "inline"), "PG_KG_STRICT": ("0", "1"), "PG_KG_GUARD": ("strict",)}


def check_env() -> None:
    """A setting of the projection that is set to something this code does not know is an error that names it: ``PG_KG_PROJECT=true`` must not mean "off" (which deletes the log)."""
    for name, allowed in _CHOICES.items():
        value = os.environ.get(name, "")
        if value and value not in allowed:
            raise ValueError(f"{name}={value!r} is not understood: set it to one of {', '.join(allowed)}, or leave it unset")


def mode() -> str:
    """``off`` (the default: PG_KG_PROJECT is not 1; nothing projects and no worker runs; the connectors service takes back its own log rows at commit and sweeps the others'),
    ``async`` (PG_KG_PROJECT=1: the writer only logs, the worker projects in large batches off its critical path) or ``inline`` (PG_KG_PROJECT=1 and PG_KG_FLUSH=inline: the writer also
    projects its own changes before COMMIT; tests). Another value of these settings raises (:func:`check_env`)."""
    check_env()
    if os.environ.get("PG_KG_PROJECT") != "1":
        return "off"
    return "inline" if os.environ.get("PG_KG_FLUSH") == "inline" else "async"


def enabled() -> bool:
    return mode() == "inline"


def _own_role(tx: Tx) -> bool:
    """The unit runs in an org as the connectors service's own role (not its jobs login, which holds nothing on kg)."""
    return bool(tx.org_id) and tx._db.cfg.service == "connectors" and tx.role == tx._db.cfg.role


async def flush(tx: Tx) -> None:
    """Project this transaction's own changes (connectors role, org unit of work)."""
    org = tx.org_id
    if not _own_role(tx):      # only the connectors role may execute the writers; the others' rows wait
        return
    rows = await tx.fetch("/* pipeshub:kg.flush_read */ SELECT ends FROM kg.graph_changes WHERE org_id = $1 AND txid = pg_current_xact_id()", org)
    if not rows:
        return
    try:
        async with tx.savepoint("kg_flush"):
            await project(tx, org, decode(rows))
            await KgWriter(tx).changes_ack()
        FLUSHED.inc("flush", value=sum(len(r["ends"]) for r in rows))
    except Exception:
        if os.environ.get("PG_KG_STRICT") == "1":
            raise
        FLUSH_FAILED.inc("flush")
        logger.exception("kg projection failed; the change log keeps the changes")


WAKE_GAP_S = 0.1                  # at most one wake-up notification per process in this time; the worker looks again shortly after every wake-up
_last_wake = 0.0
_last_sweep: dict[str, float] = {}


async def wake(tx: Tx) -> None:
    """Tell the worker, at this unit's commit, that the change log has something. Coalesced: a burst of units sends one, and a notification is only a wake-up (the worker
    reads the log itself, polls as a safety net and looks again shortly after every pass that found nothing), so a suppressed one costs nothing. Sent from here and not by the
    capture trigger, whose per-statement notification measured +9% of a writer's time."""
    global _last_wake  # noqa: PLW0603
    now = time.monotonic()
    if now - _last_wake >= WAKE_GAP_S:
        _last_wake = now
        await tx.execute("/* pipeshub:kg.wake */ SELECT pg_notify('pipeshub_kg_changes', '')")      # no payload: any role may LISTEN, and the worker reads the log itself


SWEEP_GAP_S = 30.0               # an org's change log is swept of other roles' rows at most this often per process, while its projection is off
SWEEP_PAGE = 5000                # rows per delete (the vetted delete takes at most this many)
SWEEP_PAGES = 4                  # pages per sweep


TAKE_BACK_FAILED = METRICS_BACKEND.counter("pipeshub_kg_take_back_failed_total", "Take-backs of the change log (with the projection off) that failed and were left for the next sweep", ["where"])
_sweeping: set[str] = set()
_pending: set[asyncio.Task] = set()
_in_sweep: contextvars.ContextVar[bool] = contextvars.ContextVar("kg_in_sweep", default=False)


async def _take_back(tx: Tx) -> None:
    """Projection off: keep no change-log row. Every write unit of the connectors service (not only one that wrote the graph: the indexing and query roles log too, cannot take
    their rows back, and may be the only ones writing) takes back its own rows at commit; rows taken back send every connector of the org back to the walk (one select when nothing is on the copy). The ack runs in a savepoint: a lock wait or a timeout in it rolls back to the savepoint,
    is logged and counted, and the unit goes on to commit, because it must never fail a business write; the rows stay for a later sweep. The rows of the other roles are swept
    after the commit (:func:`_after_commit`). Switching the projection on later starts with a backfill (a later change), which copies every node of the org, so what was dropped
    here is not lost."""
    if not _own_role(tx) or _in_sweep.get():
        return
    try:
        async with tx.savepoint("kg_take_back"):
            taken = await KgWriter(tx).changes_ack()
            if taken:
                await modes.demote_org(tx, tx.org_id)      # the rows just deleted were the only evidence that kg is behind: no copy of this org can be trusted any more
    except Exception:
        TAKE_BACK_FAILED.inc("ack")
        logger.exception("kg take-back of this unit's change-log rows failed; the unit commits and the rows wait for the sweep")


def _after_commit(tx: Tx) -> None:
    """With the projection off, after a write unit of the connectors service has committed: now and then (every ``SWEEP_GAP_S`` per process and org) sweep the committed rows
    the other roles left, in a transaction of its own that cannot fail the unit that asked for it."""
    org = tx.org_id
    if _in_sweep.get() or not _own_role(tx) or mode() != "off" or org in _sweeping:
        return
    now = time.monotonic()
    if now - _last_sweep.get(org, -SWEEP_GAP_S) < SWEEP_GAP_S:
        return
    _last_sweep[org] = now
    _sweeping.add(org)
    task = spawn_detached(_sweep(tx._db, org), name="kg-sweep")
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def _sweep(db: KernelDB, org: str) -> None:
    async def go(tx: Tx) -> None:
        w = KgWriter(tx)
        for _ in range(SWEEP_PAGES):
            ids = [r["id"] for r in await tx.fetch("/* pipeshub:kg.sweep_read */ SELECT id FROM kg.graph_changes WHERE org_id = $1 ORDER BY id LIMIT $2", org, SWEEP_PAGE)]
            if ids:
                await w.changes_delete(ids)
                await modes.demote_org(tx, org)
            if len(ids) < SWEEP_PAGE:
                break
    token = _in_sweep.set(True)
    try:
        with bind(OrgContext(org, None, "job", "job:kg-sweep", "kg-sweep")):
            await db.run(go, write=True)
    except Exception:
        TAKE_BACK_FAILED.inc("sweep")
        logger.warning("kg sweep of the change log failed; the next one tries again", exc_info=True)
    finally:
        _in_sweep.reset(token)
        _sweeping.discard(org)


async def settle_sweeps() -> None:
    """Wait for the sweeps that are running (tests)."""
    while _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)


async def _wrote(tx: Tx) -> bool:
    """Did this unit write the graph? The capture trigger marks the transaction (kg.wrote), whatever the statement looked like (MERGE, quoted names, a CTE); the guard's text test
    saves the question when it already saw one."""
    return tx.kg_dirty or bool(await tx.fetchval("/* pipeshub:kg.wrote */ SELECT current_setting('kg.wrote', true) = '1'"))


async def _pre_commit(tx: Tx) -> None:
    m = mode()
    if m == "off":
        await _take_back(tx)
        return
    if not await _wrote(tx):
        return
    if m == "inline":
        await flush(tx)
    await wake(tx)


def install(db: KernelDB) -> None:
    check_env()
    db.graph_write_hook = guard.check
    db.pre_commit_hook = _pre_commit
    db.post_commit_hook = _after_commit
