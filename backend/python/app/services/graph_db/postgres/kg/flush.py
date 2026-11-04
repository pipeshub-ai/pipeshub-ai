"""The flush (the writer's own transaction) and the take-back of the change log.

``install(db)`` hooks a ``KernelDB``: before a statement that writes graph.nodes or graph.edges the guard (guard.py) is asked; just before COMMIT of a
write unit of the connectors service that wrote the graph, :func:`flush` re-projects the nodes its own changes named (rows the capture triggers
inserted in this very transaction) and acknowledges them. A projection error never fails the connector's write: it is rolled back to a savepoint,
logged and counted, and the change log rows stay. Strict mode (``PG_KG_STRICT=1``, tests) re-raises instead. Nothing drains the log in the background yet (the drain is the next
change): with ``PG_KG_PROJECT=1`` the rows of other roles, and those of a failed flush, wait there.

``RuntimeError`` from :func:`project` (it kept reaching connectors it had not locked, nothing written) is "retry later": the rows stay in the log and are never acknowledged as done.
"""
from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

from app.services.graph_db.postgres.kg import guard
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


async def _take_back(tx: Tx) -> None:
    """Projection off: keep no change-log row. Every write unit of the connectors service (not only one that wrote the graph: the indexing and query roles log too, cannot take
    their rows back, and may be the only ones writing) takes back its own rows at commit and now and then deletes the committed rows the others left. Switching the projection on
    later starts with a backfill (a later change), which copies every node of the org, so what was dropped here is not lost."""
    org = tx.org_id
    if not _own_role(tx):
        return
    w = KgWriter(tx)
    await w.changes_ack()
    now = time.monotonic()
    if now - _last_sweep.get(org, -SWEEP_GAP_S) < SWEEP_GAP_S:
        return
    _last_sweep[org] = now
    for _ in range(SWEEP_PAGES):
        ids = [r["id"] for r in await tx.fetch("/* pipeshub:kg.sweep_read */ SELECT id FROM kg.graph_changes WHERE org_id = $1 AND txid <> pg_current_xact_id() ORDER BY id LIMIT $2",
                                               org, SWEEP_PAGE)]
        if ids:
            await w.changes_delete(ids)
        if len(ids) < SWEEP_PAGE:
            break


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
