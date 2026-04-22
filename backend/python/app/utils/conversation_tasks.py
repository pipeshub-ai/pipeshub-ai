"""Conversation Tasks — fire-and-forget async work tied to a conversation.

Tools (e.g. execute_query) can register asyncio.Tasks here.  The streaming
layer awaits them before closing the SSE stream so the results (signed URLs,
etc.) can be sent to the client.
"""
from __future__ import annotations

import asyncio
import csv
import io
from typing import Any

from app.utils.logger import create_logger

logger = create_logger("conversation_tasks")

# Hard ceiling on the total time we will spend draining nested tasks. A tool
# that endlessly spawns new conversation tasks must NOT block the stream
# indefinitely — at worst we log and move on.
_DRAIN_OVERALL_TIMEOUT_S = 60.0
# Ceiling on how many drain passes we will perform before declaring the
# conversation finished. With a well-behaved tool this is always 1 or 2.
_MAX_DRAIN_PASSES = 10

_conversation_tasks: dict[str, list[asyncio.Task]] = {}


def register_task(conversation_id: str, task: asyncio.Task) -> None:
    """Append an asyncio.Task to the list for *conversation_id*."""
    if conversation_id not in _conversation_tasks:
        _conversation_tasks[conversation_id] = []
    _conversation_tasks[conversation_id].append(task)
    logger.info(
        "Registered task for conversation %s (total: %d)",
        conversation_id,
        len(_conversation_tasks[conversation_id]),
    )

def _rows_to_csv_bytes(columns: list[str], rows: list[tuple]) -> bytes:
    """Serialise columns + rows into UTF-8 encoded CSV bytes."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")

def pop_tasks(conversation_id: str) -> list[asyncio.Task]:
    """Remove and return all tasks for *conversation_id*."""
    return _conversation_tasks.pop(conversation_id, [])


async def await_and_collect_results(conversation_id: str) -> list[dict[str, Any]]:
    """Await every task registered for *conversation_id* and return results.

    Handles the case where an awaited task *itself* registers new tasks
    (e.g. an artifact upload task that fans out into per-file sub-tasks).
    We loop, draining the registry, until no new tasks appear across a full
    pass — with a hard overall timeout so a pathological tool cannot block
    the stream forever.

    Failed tasks are logged and skipped (they do not propagate).
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + _DRAIN_OVERALL_TIMEOUT_S
    results: list[dict[str, Any]] = []
    passes = 0

    while passes < _MAX_DRAIN_PASSES:
        tasks = pop_tasks(conversation_id)
        if not tasks:
            break
        passes += 1
        remaining = deadline - loop.time()
        if remaining <= 0:
            # Out of time — cancel pending, move on. Log each cancelled task so
            # we notice badly-behaved tools in production.
            for task in tasks:
                if not task.done():
                    task.cancel()
                    logger.warning(
                        "Cancelled slow task for conversation %s (drain deadline exceeded)",
                        conversation_id,
                    )
            break
        for task in tasks:
            try:
                result = await asyncio.wait_for(task, timeout=remaining)
                if result is not None:
                    results.append(result)
            except asyncio.TimeoutError:
                task.cancel()
                logger.warning(
                    "Task for conversation %s timed out during drain; cancelling",
                    conversation_id,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Task failed for conversation %s", conversation_id)
            # Recompute remaining for the next task in this pass.
            remaining = max(0.0, deadline - loop.time())
            if remaining <= 0:
                break

    if passes >= _MAX_DRAIN_PASSES:
        # Pop any stragglers so we don't leave them dangling in the registry
        # for the next caller to pick up.
        leftover = pop_tasks(conversation_id)
        if leftover:
            logger.warning(
                "Drain pass limit hit for conversation %s; cancelling %d leftover task(s)",
                conversation_id, len(leftover),
            )
            for t in leftover:
                if not t.done():
                    t.cancel()

    return results
