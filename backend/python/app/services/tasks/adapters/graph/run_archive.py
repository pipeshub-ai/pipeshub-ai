"""`GraphRunArchive` -- `IRunArchive` over `IGraphDBProvider`.

Runs live in Redis while they execute and are expired from it afterwards, so
without this a task's history is only as long as its TTL, and a Redis eviction
or restart erases the record that a scheduled workflow ever ran. Archived runs
are the durable answer to "what has this task done".

Uses only generic `IGraphDBProvider` methods (`batch_upsert_nodes`,
`get_document`, `get_documents_paginated`, `get_nodes_by_filters`) so it
works with both ArangoDB and Neo4j backends.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.services.tasks.domain.models import Page, TaskRun
from app.services.tasks.interface.run_archive import IRunArchive

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = ["GraphRunArchive", "ArangoRunArchive"]

logger = logging.getLogger(__name__)

_COLLECTION = CollectionNames.TASK_RUNS.value

_JSON_FIELDS = ("trigger_payload", "usage")
"""The only two non-primitive fields on `TaskRun`. Neo4j node properties may
hold primitives or arrays of primitives, never nested maps, so these are
JSON-encoded; every other field is a scalar or a `list[str]` and is stored
natively so `list_for_task` can filter on it. Encoding is unconditional --
an empty dict must round-trip as `{}` rather than as null, because both
fields are non-Optional and a null would fail `model_validate` on read.
"""


def _to_node(run: TaskRun) -> dict[str, Any]:
    doc = run.model_dump(mode="json")
    doc["id"] = run.run_id
    for key in _JSON_FIELDS:
        doc[key] = json.dumps(doc.get(key) or {})
    return doc


def _from_doc(doc: dict[str, Any]) -> TaskRun:
    payload = {key: value for key, value in doc.items() if not key.startswith("_")}
    payload.pop("id", None)
    for key in _JSON_FIELDS:
        val = payload.get(key)
        if isinstance(val, str):
            try:
                payload[key] = json.loads(val)
            except json.JSONDecodeError:
                logger.warning("run archive: field %s is not valid JSON, defaulting", key)
                payload.pop(key, None)
        elif val is None:
            # Neo4j drops properties set to null rather than storing them, so
            # an absent key and a null one must both fall back to the default.
            payload.pop(key, None)
    return TaskRun.model_validate(payload)


class GraphRunArchive(IRunArchive):
    def __init__(self, graph_provider: "IGraphDBProvider") -> None:
        self._graph = graph_provider

    async def archive(self, run: TaskRun) -> None:
        result = await self._graph.batch_upsert_nodes(nodes=[_to_node(run)], collection=_COLLECTION)
        if result is False:
            raise RuntimeError(f"Failed to archive run {run.run_id}")

    async def get(self, run_id: str) -> TaskRun | None:
        doc = await self._graph.get_document(document_key=run_id, collection=_COLLECTION)
        if doc is None:
            return None
        try:
            return _from_doc(doc)
        except Exception:
            logger.exception("run archive: cannot deserialize run %s", run_id)
            return None

    async def list_for_task(
        self, task_id: str, *, limit: int = 50, offset: int = 0,
    ) -> Page[TaskRun]:
        filters = {"task_id": task_id}
        try:
            docs = await self._graph.get_documents_paginated(
                collection=_COLLECTION,
                skip=offset,
                limit=limit,
                filters=filters,
                sort_field="created_at",
                sort_desc=True,
            )
        except Exception:
            logger.exception("run archive: cannot list runs for task %s", task_id)
            return Page(items=[], total=0, limit=limit, offset=offset)

        runs: list[TaskRun] = []
        for doc in docs:
            try:
                runs.append(_from_doc(doc))
            except Exception:
                logger.exception("run archive: skipping undeserializable run in task %s", task_id)

        total = len(runs) + offset
        if len(docs) == limit:
            try:
                all_ids = await self._graph.get_nodes_by_filters(
                    collection=_COLLECTION,
                    filters=filters,
                    return_fields=["id"],
                )
                total = len(all_ids)
            except Exception:
                total = len(runs) + offset
        return Page(items=runs, total=total, limit=limit, offset=offset)


ArangoRunArchive = GraphRunArchive
