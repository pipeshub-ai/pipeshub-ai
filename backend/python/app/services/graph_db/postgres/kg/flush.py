"""The flush (the writer's own transaction) and the take-back of the change log.

``install(db)`` hooks a ``KernelDB``: before a statement that writes graph.nodes or graph.edges the guard (guard.py) is asked; just before COMMIT of a
write unit of the connectors service that wrote the graph, :func:`flush` re-projects the nodes its own changes named (rows the capture triggers
inserted in this very transaction) and acknowledges them. A projection error never fails the connector's write: it is rolled back to a savepoint,
logged and counted, and the change log rows stay. Strict mode (``PG_KG_STRICT=1``, tests) re-raises instead. The rows of other roles, and those of a failed flush, wait there for
the worker's drain.

``RuntimeError`` from :func:`project` (it kept reaching connectors it had not locked, nothing written) is "retry later": the rows stay in the log and are never acknowledged as done.
"""
from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

from app.core.db import notify
from app.services.graph_db.postgres.kg import guard, stamp
from app.services.graph_db.postgres.kg.project import decode, grant_only, project
from app.services.graph_db.postgres.kg.writers import KgWriter
from app.plugins.telemetry.backend import METRICS_BACKEND

if TYPE_CHECKING:
    from app.core.db import KernelDB, Tx

logger = logging.getLogger(__name__)
FLUSH_FAILED = METRICS_BACKEND.counter("pipeshub_kg_flush_failed_total", "Projections in a writer's transaction that failed and left the change log rows", ["where"])
FLUSHED = METRICS_BACKEND.counter("pipeshub_kg_flushed_nodes_total", "Nodes projected by a writer's own transaction", ["where"])


_CHOICES = {"PG_KG_PROJECT": ("1",), "PG_KG_READ": ("0", "1"), "PG_KG_COUNTERS": ("0", "1"), "PG_KG_FLUSH": ("async", "inline"), "PG_KG_STRICT": ("0", "1"),
            "PG_KG_GUARD": ("strict",)}


def check_env() -> None:
    """A setting of the projection or the read that is set to something this code does not know is an error that names it: ``PG_KG_PROJECT=false`` must not mean "on". Unset (or empty) and 1
    are on, 0 is off. The projection cannot be off: the provider of a PostgreSQL graph is not built with ``PG_KG_PROJECT=0``."""
    if os.environ.get("PG_KG_PROJECT") == "0":
        raise ValueError("PG_KG_PROJECT=0 is not supported with a PostgreSQL graph (DATA_STORE=postgres): the knowledge hub lists sources from the typed graph, so every source "
                         "would show as preparing. Unset PG_KG_PROJECT; to answer access checks from the graph walk instead of the typed graph, set PG_KG_READ=0")
    for name, allowed in _CHOICES.items():
        value = os.environ.get(name, "")
        if value and value not in allowed:
            raise ValueError(f"{name}={value!r} is not understood: set it to one of {', '.join(allowed)}, or leave it unset")
    stamp.inline_max()
    from app.services.graph_db.postgres.kg import (
        net,  # imports backfill, which imports this module
    )
    net.check_env()


def enabled() -> bool:
    """The writer also projects its own changes before COMMIT (``PG_KG_FLUSH=inline``, tests); by default it only logs them and the worker projects them in large batches off its
    critical path. A value this code does not know raises (:func:`check_env`)."""
    check_env()
    return os.environ.get("PG_KG_FLUSH") == "inline"


def _own_role(tx: Tx) -> bool:
    """The unit runs in an org as the connectors service's own role (not its jobs login, which holds nothing on kg)."""
    return bool(tx.org_id) and tx._db.cfg.service == "connectors" and tx.role == tx._db.cfg.role


async def flush(tx: Tx) -> None:
    """Project this transaction's own changes (connectors role, org unit of work)."""
    org = tx.org_id
    if not _own_role(tx):      # only the connectors role may execute the writers; the others' rows wait
        return
    rows = await tx.fetch("/* pipeshub:kg.flush_read */ SELECT ends, grant_only FROM kg.graph_changes WHERE org_id = $1 AND txid = pg_current_xact_id()", org)
    if not rows:
        return
    try:
        async with tx.savepoint("kg_flush"):
            await project(tx, org, decode(rows), grant_only=grant_only(rows))
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
        await notify(tx, "pipeshub_kg_changes")      # no payload: any role may LISTEN, and the worker reads the log itself


async def _wrote(tx: Tx) -> bool:
    """Did this unit write the graph? The capture trigger marks the transaction (kg.wrote), whatever the statement looked like (MERGE, quoted names, a CTE); the guard's text test
    saves the question when it already saw one."""
    return tx.kg_dirty or bool(await tx.fetchval("/* pipeshub:kg.wrote */ SELECT current_setting('kg.wrote', true) = '1'"))


async def _pre_commit(tx: Tx) -> None:
    if not await _wrote(tx):
        return
    if enabled():
        await flush(tx)
    await wake(tx)


def install(db: KernelDB) -> None:
    check_env()
    db.graph_write_hook = guard.check
    db.pre_commit_hook = _pre_commit
