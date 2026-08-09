"""`GraphTaskStore`: `ITaskStore` backed by `IGraphDBProvider` -- works
unmodified against either `ArangoHTTPProvider` or `Neo4jProvider` since it
only calls the generic, backend-agnostic surface of that interface
(`get_document`, `batch_upsert_nodes`, `delete_nodes`, `get_nodes_by_filters`)
-- never `execute_query` (AQL-specific). Mirrors the conventions established
by `GraphSkillStore` (`app/agents/agent_loop/skills/graph_store.py`).

Document design: nested/complex fields (`principal`, `clarifications`,
`steps`, `retry_policy`, `budget`) are stored as JSON-encoded strings,
because Neo4j node properties only allow primitives or arrays of
primitives, not nested maps or arrays of maps -- the same constraint
`GraphSkillStore` works around for its `resourcePaths`/`resourceContents`
pair. Flat scalar and list[str] fields (`title`, `status`, `tool_names`,
...) are stored natively so `get_nodes_by_filters`/`list()` can filter on
them without a JSON-decode-then-scan for the common case (status, enabled,
created_by_user_id).
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.services.tasks.domain.errors import (
    OptimisticConcurrencyError,
    TaskNotFoundError,
)
from app.services.tasks.domain.models import Page, TaskDefinition, TaskQuery, TaskStatus
from app.services.tasks.interface.task_store import ITaskStore

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = ["GraphTaskStore"]

_TASKS = CollectionNames.TASKS.value

_JSON_FIELDS = ("principal", "clarifications", "steps", "retry_policy", "budget")


def _doc_id(doc: dict) -> str | None:
    """Backend-agnostic identifier read (see `GraphSkillStore` for the
    same convention): Arango hands back `_key`, Neo4j hands back the node's
    own `id` property."""
    return doc.get("id") or doc.get("_key")


class GraphTaskStore(ITaskStore):
    def __init__(self, graph_provider: "IGraphDBProvider") -> None:
        self._graph = graph_provider

    @staticmethod
    def _task_to_doc(task: TaskDefinition) -> dict[str, Any]:
        payload = task.model_dump(mode="json")
        doc: dict[str, Any] = {"id": task.task_id}
        for field_name, value in payload.items():
            if field_name == "task_id":
                continue
            if field_name in _JSON_FIELDS:
                doc[field_name] = json.dumps(value)
            else:
                doc[field_name] = value
        return doc

    @staticmethod
    def _doc_to_task(doc: dict[str, Any]) -> TaskDefinition:
        payload: dict[str, Any] = {}
        for key, value in doc.items():
            if key in ("_key", "_id", "_rev"):
                continue
            if key in _JSON_FIELDS and isinstance(value, str):
                payload[key] = json.loads(value) if value else None
            else:
                payload[key] = value
        payload["task_id"] = _doc_id(doc)
        return TaskDefinition.model_validate(payload)

    async def create(self, task: TaskDefinition) -> TaskDefinition:
        doc = self._task_to_doc(task)
        await self._graph.batch_upsert_nodes([doc], _TASKS)
        return task

    async def get(self, task_id: str, org_id: str) -> TaskDefinition | None:
        doc = await self._graph.get_document(task_id, _TASKS)
        if doc is None or doc.get("org_id") != org_id:
            return None
        return self._doc_to_task(doc)

    async def update(self, task: TaskDefinition, *, expected_revision: int) -> TaskDefinition:
        existing = await self._graph.get_document(task.task_id, _TASKS)
        if existing is None or existing.get("org_id") != task.org_id:
            raise TaskNotFoundError(task.task_id)
        actual_revision = existing.get("revision", 0)
        if actual_revision != expected_revision:
            raise OptimisticConcurrencyError(task.task_id, expected_revision, actual_revision)

        updated = task.model_copy(update={"revision": expected_revision + 1})
        doc = self._task_to_doc(updated)
        await self._graph.batch_upsert_nodes([doc], _TASKS)
        return updated

    async def delete(self, task_id: str, org_id: str) -> bool:
        existing = await self._graph.get_document(task_id, _TASKS)
        if existing is None or existing.get("org_id") != org_id:
            return False
        return await self._graph.delete_nodes([task_id], _TASKS)

    async def list(self, query: TaskQuery) -> Page[TaskDefinition]:
        filters: dict[str, Any] = {"org_id": query.org_id}
        if query.created_by_user_id is not None:
            filters["created_by_user_id"] = query.created_by_user_id
        if query.status is not None:
            filters["status"] = query.status.value if isinstance(query.status, TaskStatus) else query.status
        if query.enabled is not None:
            filters["enabled"] = query.enabled
        if query.created_from_conversation_id is not None:
            filters["created_from_conversation_id"] = query.created_from_conversation_id

        docs = await self._graph.get_nodes_by_filters(_TASKS, filters)
        tasks = [self._doc_to_task(d) for d in docs]

        if query.text_search:
            needle = query.text_search.lower()
            tasks = [t for t in tasks if needle in t.title.lower() or needle in t.description.lower()]

        tasks.sort(key=lambda t: t.created_at, reverse=True)
        total = len(tasks)
        page_items = tasks[query.offset: query.offset + query.limit]
        return Page(items=page_items, total=total, limit=query.limit, offset=query.offset)
