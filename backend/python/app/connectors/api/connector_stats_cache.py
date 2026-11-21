"""The answer of ``GET /api/v1/stats``, shared for a short while per (org, connector).

The stats count every record of a connector by indexing, extraction and entities state, which on a large connector costs seconds of database time,
and the connector page, the MCP sources tool and API clients ask for them in bursts. Calls for the same (org, connector) share one count while it runs
and reuse its answer for ``TTL_S`` seconds after it started, so an answer is at most ``TTL_S`` old, or as old as the count still running when the call came.
The counts are the org's, not the viewer's (no user enters the count), and the route authorizes every caller before it reads here.
A failed count is not kept. Callers that must see their own writes (the local repository status, the sync) call the graph provider directly.
"""
from __future__ import annotations

import asyncio
import copy
import time
import weakref
from typing import Any

from app.core.org_context import spawn_in_request

TTL_S = 2.0
MAX_KEYS = 1024        # (org, connector) pairs kept per provider; the oldest goes first

# provider -> {(org, connector): (started, count task)}; per provider, so two providers in one process never share an answer
_entries: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


async def connector_stats(graph: Any, org_id: str, connector_id: str) -> dict:  # noqa: ANN401
    entries = _entries.setdefault(graph, {})
    key = (org_id, connector_id)
    now = time.monotonic()
    hit = entries.get(key)
    if hit is None or (hit[1].done() and now - hit[0] >= TTL_S):
        entries.pop(key, None)        # re-inserted last, so the dict stays oldest-first
        while len(entries) >= MAX_KEYS:
            entries.pop(next(iter(entries)))
        # the caller's context: every caller sharing this count is of the same org (it is in the key)
        hit = entries[key] = (now, spawn_in_request(graph.get_connector_stats(org_id, connector_id), name="connector-stats"))

        def forget_failure(task: asyncio.Task, entry: tuple = hit) -> None:
            if (task.cancelled() or task.exception() is not None or not task.result().get("success")) and entries.get(key) is entry:
                del entries[key]
        hit[1].add_done_callback(forget_failure)
    # shield: one caller going away must not cancel the count the others wait on; each caller gets its own copy to change
    return copy.deepcopy(await asyncio.shield(hit[1]))
