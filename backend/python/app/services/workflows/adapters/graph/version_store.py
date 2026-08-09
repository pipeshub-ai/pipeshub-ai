"""GraphWorkflowVersionStore: IWorkflowVersionStore over IGraphDBProvider.

Workflow versions are immutable — created once by the code generator, never
mutated.

Uses only generic `IGraphDBProvider` methods so it works with both ArangoDB
and Neo4j.

Document design: `bundle_ref`, `tool_pins` and `ir` are stored as JSON-encoded
strings because Neo4j node properties may only hold primitives or arrays of
primitives -- never nested maps or arrays of maps, which `ir.nodes` is. Same
constraint and same workaround as `GraphTaskStore._JSON_FIELDS`. `agent_pins`
needs no encoding: `model_dump(mode="json")` renders the `set` as a list of
strings, which both backends accept natively (and which, unlike a raw `set`,
is JSON-serialisable at all -- the Arango HTTP client would reject the latter).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.services.workflows.domain.errors import WorkflowVersionConflictError
from app.services.workflows.domain.models import WorkflowVersion

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = ["GraphWorkflowVersionStore", "ArangoWorkflowVersionStore"]

logger = logging.getLogger(__name__)

_COLLECTION = CollectionNames.WORKFLOW_VERSIONS.value

_JSON_FIELDS = ("bundle_ref", "tool_pins", "ir")


def _to_node(version: WorkflowVersion) -> dict[str, Any]:
    """Serialise a WorkflowVersion to the generic node format expected by batch_upsert_nodes."""
    d = version.model_dump(mode="json")
    # Map version_id → `id` (the generic node identifier on both backends).
    d["id"] = version.version_id
    if not d.get("created_at"):
        d["created_at"] = datetime.now(timezone.utc).isoformat()
    for field_name in _JSON_FIELDS:
        d[field_name] = json.dumps(d.get(field_name))
    return d


def _from_doc(doc: dict[str, Any]) -> WorkflowVersion:
    d = {k: v for k, v in doc.items() if not k.startswith("_")}
    for field_name in _JSON_FIELDS:
        value = d.get(field_name)
        if isinstance(value, str):
            try:
                d[field_name] = json.loads(value)
            except json.JSONDecodeError:
                raw_preview = value[:200] if len(value) > 200 else value
                logger.warning(
                    "WorkflowVersion %s: field %s is not valid JSON (raw=%r), dropping",
                    d.get("id"), field_name, raw_preview,
                )
                d.pop(field_name, None)
        elif value is None:
            d.pop(field_name, None)
    if "version_id" not in d or not d["version_id"]:
        d["version_id"] = d.get("id", "")
    return WorkflowVersion.model_validate(d)


class GraphWorkflowVersionStore:
    """IWorkflowVersionStore backed by generic IGraphDBProvider."""

    def __init__(self, graph_provider: "IGraphDBProvider") -> None:
        self._graph = graph_provider

    async def save(self, version: WorkflowVersion) -> WorkflowVersion:
        existing = await self._graph.get_document(
            document_key=version.version_id, collection=_COLLECTION,
        )
        if existing is not None:
            raise WorkflowVersionConflictError(
                f"WorkflowVersion {version.version_id} already exists; versions are immutable"
            )

        if not version.version_number:
            latest = await self.get_latest(version.workflow_id, version.org_id)
            version = version.model_copy(
                update={"version_number": (latest.version_number if latest else 0) + 1}
            )

        node = _to_node(version)
        result = await self._graph.batch_upsert_nodes(
            nodes=[node],
            collection=_COLLECTION,
        )
        if result is False:
            raise RuntimeError(f"Failed to persist WorkflowVersion {version.version_id}")
        return version

    async def delete(self, version_id: str, org_id: str) -> bool:
        existing = await self._graph.get_document(
            document_key=version_id, collection=_COLLECTION,
        )
        if existing is None or existing.get("org_id") != org_id:
            return False
        return bool(await self._graph.delete_nodes([version_id], _COLLECTION))

    async def get(self, version_id: str, org_id: str) -> WorkflowVersion | None:
        doc = await self._graph.get_document(
            document_key=version_id,
            collection=_COLLECTION,
        )
        if doc is None:
            return None
        if doc.get("org_id") != org_id:
            return None
        try:
            return _from_doc(doc)
        except Exception:
            logger.exception("Failed to deserialize WorkflowVersion %s", version_id)
            return None

    async def list_for_workflow(
        self, workflow_id: str, org_id: str, *, limit: int = 20, offset: int = 0
    ) -> list[WorkflowVersion]:
        """Newest first. The ordering must happen in the database: re-sorting
        the returned page client-side only reorders the slice the database
        already picked, which for an ASC sort is the *oldest* versions.

        Raises on graph-level failures so the caller can distinguish 'no
        versions' from 'store unreachable'.
        """
        docs = await self._graph.get_documents_paginated(
            collection=_COLLECTION,
            skip=offset,
            limit=limit,
            filters={"workflow_id": workflow_id, "org_id": org_id},
            sort_field="version_number",
            sort_desc=True,
            raise_on_error=True,
        )
        versions = []
        for doc in (docs or []):
            try:
                versions.append(_from_doc(doc))
            except Exception:
                logger.exception("Skipping undeserializable WorkflowVersion in %s", workflow_id)
        return versions

    async def get_latest(self, workflow_id: str, org_id: str) -> WorkflowVersion | None:
        results = await self.list_for_workflow(workflow_id, org_id, limit=1)
        return results[0] if results else None


ArangoWorkflowVersionStore = GraphWorkflowVersionStore
