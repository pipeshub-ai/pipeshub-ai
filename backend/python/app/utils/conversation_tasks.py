"""Conversation Tasks — fire-and-forget async work tied to a conversation.

Tools (e.g. execute_query) can register asyncio.Tasks here.  The streaming
layer awaits them before closing the SSE stream so the results (signed URLs,
etc.) can be sent to the client.
"""
from __future__ import annotations

import asyncio
import csv
import io
from typing import Any, Dict, List

from app.utils.logger import create_logger

logger = create_logger("conversation_tasks")

_conversation_tasks: Dict[str, List[asyncio.Task]] = {}


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

def _rows_to_csv_bytes(columns: List[str], rows: List[tuple]) -> bytes:
    """Serialise columns + rows into UTF-8 encoded CSV bytes."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")

def pop_tasks(conversation_id: str) -> List[asyncio.Task]:
    """Remove and return all tasks for *conversation_id*."""
    return _conversation_tasks.pop(conversation_id, [])


async def await_and_collect_results(conversation_id: str) -> List[Dict[str, Any]]:
    """Await every task registered for *conversation_id* and return results.

    Failed tasks are logged and skipped (they do not propagate).
    """
    tasks = pop_tasks(conversation_id)
    if not tasks:
        return []

    results: List[Dict[str, Any]] = []
    for task in tasks:
        try:
            result = await task
            if result is not None:
                results.append(result)
        except Exception:
            logger.exception("Task failed for conversation %s", conversation_id)
    return results