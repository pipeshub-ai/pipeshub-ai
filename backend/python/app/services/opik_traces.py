"""Deleting a conversation's Opik traces (cleanup audit, Opik item).

A durable run's LLM and tool spans nest under one trace per attempt whose
thread is the conversation and whose metadata names the run
(``maybe_start_conversation_trace``, opened around every claimed attempt), so
a conversation's traces are found by its thread id and by its run ids, then
deleted by trace id. Traces written before that trace existed, the legacy
LangChain path's and the indexing pipeline's name no conversation and cannot
be found this way.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import TYPE_CHECKING, Any, Protocol

from harness_core.models.transports.opik_tracing import is_opik_configured

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

# Ids go into Opik's query language between double quotes: only plain id characters may.
SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
FIND_LIMIT = 500
# A conversation has a bounded number of attempts; this caps one call however many Opik keeps returning.
MAX_ROUNDS = 20

__all__ = ["SAFE_ID_PATTERN", "OpikTraceStore", "SdkOpikTraceStore", "erase_conversation_traces", "opik_trace_store"]


class OpikTraceStore(Protocol):
    def find(self, filter_string: str, limit: int) -> list[str]:
        """Ids of the traces matching an Opik query-language filter."""
        ...

    def delete(self, trace_ids: list[str]) -> None: ...

    def delete_threads(self, thread_ids: list[str]) -> None: ...


class SdkOpikTraceStore:
    """The deployment's Opik, through its SDK (configured from the environment, as tracing is)."""

    def __init__(self) -> None:
        import opik

        self._client: Any = opik.Opik()
        self._project = os.getenv("OPIK_PROJECT_NAME") or None

    def find(self, filter_string: str, limit: int) -> list[str]:
        traces = self._client.search_traces(
            project_name=self._project, filter_string=filter_string, max_results=limit, truncate=True,
            exclude=["input", "output", "metadata"],
        )
        return [str(trace.id) for trace in traces if trace.id]

    def delete(self, trace_ids: list[str]) -> None:
        self._client.rest_client.traces.delete_traces(ids=trace_ids)

    def delete_threads(self, thread_ids: list[str]) -> None:
        kwargs = {"project_name": self._project} if self._project else {}
        self._client.rest_client.traces.delete_trace_threads(thread_ids=thread_ids, **kwargs)


def opik_trace_store() -> OpikTraceStore | None:
    """None when Opik is not configured: nothing was ever sent there."""
    return SdkOpikTraceStore() if is_opik_configured() else None


async def erase_conversation_traces(
    store: OpikTraceStore, org_id: str, conversation_id: str, run_ids: Sequence[str]
) -> int:
    """Every trace of the org's conversation thread and of its runs, then the thread once no other trace is in it.

    Opik keeps one project for every org, so each search also names the org: a token of one org never deletes
    another's traces, whatever ids it names. Raises on any Opik failure.
    """
    if not re.match(SAFE_ID_PATTERN, org_id):
        raise ValueError("org id is not a safe Opik filter value")
    org = f' and metadata.org_id = "{org_id}"'
    filters = [f'thread_id = "{conversation_id}"{org}', *(f'metadata.run_id = "{run_id}"{org}' for run_id in run_ids)]
    deleted: set[str] = set()
    for filter_string in filters:
        for _ in range(MAX_ROUNDS):
            # Opik's search may still list a trace just deleted; one asked for again is not counted twice.
            found = [trace_id for trace_id in await asyncio.to_thread(store.find, filter_string, FIND_LIMIT) if trace_id not in deleted]
            if not found:
                break
            await asyncio.to_thread(store.delete, found)
            deleted.update(found)
    if deleted and not await asyncio.to_thread(store.find, f'thread_id = "{conversation_id}"', 1):
        await asyncio.to_thread(store.delete_threads, [conversation_id])
    return len(deleted)
