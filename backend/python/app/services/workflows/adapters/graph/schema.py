"""Ensure the workflow collections exist.

`IGraphDBProvider.ensure_schema()` is owned by the connectors service, so a
query-only process started against a fresh database has no `workflowVersions`
/ `workflowSources` collection and fails on the first code generation. This
creates just those two, idempotently, without pulling the full schema
bootstrap into the query service.

Backends that create their containers implicitly (Neo4j labels) need nothing
here and report success.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.config.constants.arangodb import CollectionNames

if TYPE_CHECKING:
    from logging import Logger

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = ["WORKFLOW_COLLECTIONS", "ensure_workflow_collections"]

WORKFLOW_COLLECTIONS = (
    CollectionNames.WORKFLOW_VERSIONS.value,
    CollectionNames.WORKFLOW_SOURCES.value,
    # The run archive lives here too: it is created by the same query-service
    # startup path, and a missing collection would silently turn every
    # terminal run's archive write into an error.
    CollectionNames.TASK_RUNS.value,
    # Everything else the task engine writes from the query service, for the
    # same reason: task creation and headless agent runs are triggered from
    # chat, so they must not depend on the connectors service having booted
    # against this database first.
    CollectionNames.TASKS.value,
    CollectionNames.AGENT_CHECKPOINTS.value,
    CollectionNames.AGENT_TIMELINE_ENTRIES.value,
)

_logger = logging.getLogger(__name__)


async def ensure_workflow_collections(
    graph_provider: "IGraphDBProvider", *, logger: "Logger | None" = None,
) -> bool:
    """Best-effort; returns True when every collection is known to exist."""
    log = logger or _logger
    client = getattr(graph_provider, "http_client", None)
    if client is None or not hasattr(client, "create_collection"):
        # Label-based backend (Neo4j): nothing to pre-create.
        return True

    ok = True
    for name in WORKFLOW_COLLECTIONS:
        try:
            if await client.collection_exists(name):
                continue
            created = await client.create_collection(name)
            if created:
                log.info("workflows: created collection %s", name)
            else:
                ok = False
        except Exception:
            # A concurrent creator winning the race is the common case here.
            log.warning("workflows: could not ensure collection %s", name, exc_info=True)
            ok = False
    return ok
