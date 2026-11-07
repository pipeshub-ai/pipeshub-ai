"""Which person roles each connector's record types carry (``docs/record-query.md``).

The recall check treats a search hit with no edge of the asked role as a
possible match, since the graph may have missed it. That is only true of a
record type that ever carries the role: a mail has no assignee. Each
connector node holds the ``RECORD_TYPE:EDGE_TYPE`` pairs its records carry,
written beside the edges; a connector whose set is not known to be exact yet
gets its backfill started here, and its hits stay possible meanwhile.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.core.org_context import spawn_in_request

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Iterable

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

# The connectors whose backfill is running in this process, so a burst of
# queries starts one each.
_running: dict[tuple[str, str], asyncio.Task[None]] = {}


async def known_role_carriers(
    graph: IGraphDBProvider, org_id: str, app_ids: Iterable[str],
) -> dict[str, frozenset[str]]:
    """The exact pairs of each connector in ``app_ids`` that has them; one
    left out decides nothing. Never raises: a failed read decides nothing."""
    wanted = sorted({a for a in app_ids if a})
    if not wanted:
        return {}
    try:
        stored = await graph.get_role_carriers(org_id, wanted)
    except Exception as exc:  # NotImplementedInPostgres too: every hit stays possible
        logger.info("role carriers: not read (%s) apps=%d", type(exc).__name__, len(wanted))
        return {}
    for app_id, pairs in stored.items():
        if pairs is None:
            _start_backfill(graph, org_id, app_id)
    return {app_id: pairs for app_id, pairs in stored.items() if pairs is not None}


def _start_backfill(graph: IGraphDBProvider, org_id: str, app_id: str) -> None:
    key = (org_id, app_id)
    if key in _running:
        return
    task = spawn_in_request(_backfill(graph, org_id, app_id), name=f"role-carriers-{app_id}")
    _running[key] = task
    task.add_done_callback(lambda _t: _running.pop(key, None))


async def _backfill(graph: IGraphDBProvider, org_id: str, app_id: str) -> None:
    try:
        await graph.backfill_role_carriers(org_id, app_id)
    except Exception as exc:  # the next query that meets the connector tries again
        logger.warning("role carriers: backfill failed app=%s (%s)", app_id, type(exc).__name__)
    else:
        logger.info("role carriers: backfilled app=%s", app_id)


__all__ = ["known_role_carriers"]
