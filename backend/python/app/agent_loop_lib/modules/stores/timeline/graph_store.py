"""`GraphTimelineStore`: durable `TimelineStore` backed by `IGraphDBProvider`,
following the same conventions as `GraphCheckpointStore`
(`app/agent_loop_lib/modules/stores/checkpoint/graph_store.py`) and
`GraphSkillStore` (`app/agents/agent_loop/skills/graph_store.py`) -- generic
`IGraphDBProvider` surface only, never `execute_query`.

`TimelineEntry` fields are individually useful for a dashboard/audit view
(unlike `AgentCheckpoint`, which nothing but this same store's own
`load()`/`latest()`/`history()` ever reads), so this store denormalizes
every field onto the document instead of collapsing to one opaque JSON
blob -- `detail: dict[str, Any]` is the sole exception, JSON-encoded because
its shape is genuinely arbitrary per `event_type` and Neo4j node properties
cannot hold a nested map (same constraint `GraphSkillStore`'s
`resourcePaths`/`resourceContents` works around).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.modules.stores.state.base import AgentStatus
from app.agent_loop_lib.modules.stores.timeline.base import TimelineEntry, TimelineStore
from app.config.constants.arangodb import CollectionNames
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = ["GraphTimelineStore"]

_TIMELINE = CollectionNames.AGENT_TIMELINE_ENTRIES.value


def _doc_id(doc: dict) -> str | None:
    return doc.get("id") or doc.get("_key")


class GraphTimelineStore(TimelineStore):
    def __init__(self, graph_provider: "IGraphDBProvider", org_id: str) -> None:
        self._graph = graph_provider
        self._org_id = org_id

    @staticmethod
    def _doc_to_entry(doc: dict) -> TimelineEntry:
        detail = doc.get("detail") or "{}"
        return TimelineEntry(
            entry_id=_doc_id(doc) or "",
            sequence_id=doc.get("sequenceId", 0),
            trace_id=doc.get("traceId", ""),
            run_id=doc.get("runId", ""),
            agent_id=doc.get("agentId", ""),
            parent_run_id=doc.get("parentRunId"),
            timestamp=doc.get("timestamp", ""),
            status=AgentStatus(doc.get("status") or AgentStatus.IDLE.value),
            event_type=doc.get("eventType", ""),
            summary=doc.get("summary", ""),
            detail=json.loads(detail) if isinstance(detail, str) else dict(detail),
            role_name=doc.get("roleName", ""),
            model=doc.get("model", ""),
        )

    def _entry_to_doc(self, entry: TimelineEntry) -> dict[str, Any]:
        return {
            "id": entry.entry_id,
            "orgId": self._org_id,
            "sequenceId": entry.sequence_id,
            "traceId": entry.trace_id,
            "runId": entry.run_id,
            "agentId": entry.agent_id,
            "parentRunId": entry.parent_run_id,
            "timestamp": entry.timestamp,
            "status": entry.status.value,
            "eventType": entry.event_type,
            "summary": entry.summary,
            "detail": json.dumps(entry.detail),
            "roleName": entry.role_name,
            "model": entry.model,
            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
        }

    async def append(self, entry: TimelineEntry) -> None:
        await self._graph.batch_upsert_nodes([self._entry_to_doc(entry)], _TIMELINE)

    async def get_by_trace(self, trace_id: str) -> list[TimelineEntry]:
        docs = await self._graph.get_nodes_by_filters(_TIMELINE, {"orgId": self._org_id, "traceId": trace_id})
        entries = [self._doc_to_entry(d) for d in docs]
        entries.sort(key=lambda e: e.sequence_id)
        return entries

    async def get_by_run(self, run_id: str) -> list[TimelineEntry]:
        docs = await self._graph.get_nodes_by_filters(_TIMELINE, {"orgId": self._org_id, "runId": run_id})
        entries = [self._doc_to_entry(d) for d in docs]
        entries.sort(key=lambda e: e.sequence_id)
        return entries

    async def clear(self, trace_id: str) -> None:
        docs = await self._graph.get_nodes_by_filters(_TIMELINE, {"orgId": self._org_id, "traceId": trace_id})
        keys = [_doc_id(d) for d in docs if _doc_id(d) is not None]
        if keys:
            await self._graph.delete_nodes(keys, _TIMELINE)
