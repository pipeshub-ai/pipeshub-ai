"""GA-01: every agent made before versioning (OWD-14) gets version 1, its definition as it stands.

Idempotent and safe beside live traffic: ``AgentVersionStore.current`` makes version 1 only for an
agent that has none, and a racing first read (which does the same) claims the same number. An agent
whose org cannot be read (its creator is gone) is left for its first read; it is counted, not failed.
The flag is set once a pass failed nowhere.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from app.agents.versions import AgentVersionStore
from app.config.constants.arangodb import CollectionNames
from app.core.org_context import for_org, org_of, system
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from logging import Logger

    from app.config.configuration_service import ConfigurationService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

MIGRATION_FLAG_KEY = "/migrations/agent_versions_v1"
PAGE = 200
MIGRATION_JOB = "agent-versions-migration"


async def _org_of(graph: IGraphDBProvider, agent: dict[str, Any]) -> str | None:
    creator = agent.get("createdBy")
    if not creator:
        return None
    user = await graph.get_document(str(creator), CollectionNames.USERS.value)
    org = org_of(user)
    return org if isinstance(org, str) and org else None


async def migrate_agent_versions(graph: IGraphDBProvider) -> dict[str, int]:
    with system(MIGRATION_JOB):
        return await _migrate_agent_versions(graph)


async def _migrate_agent_versions(graph: IGraphDBProvider) -> dict[str, int]:
    store = AgentVersionStore(graph)
    counts = {"agents": 0, "versioned": 0, "already": 0, "no_org": 0, "failures": 0}
    after: str | None = None
    while True:
        page = await graph.get_documents_paginated(
            CollectionNames.AGENT_INSTANCES.value, skip=0, limit=PAGE, sort_field="_key",
            after_key=after, raise_on_error=True, return_fields=["_key", "createdBy", "isDeleted"],
        )
        for doc in page:
            key = doc.get("_key")
            if not isinstance(key, str) or not key or doc.get("isDeleted") is True:
                continue
            counts["agents"] += 1
            try:
                org = await _org_of(graph, doc)
                if org is None:
                    counts["no_org"] += 1
                    continue
                # Each agent's reads and writes act for its creator's org (main #4703's per-row binding).
                with for_org(org, job=MIGRATION_JOB):
                    if await store.numbers(org, key):
                        counts["already"] += 1
                        continue
                    agent = cast("dict[str, Any] | None", await graph.get_agent(key, org))  # pyright: ignore[reportUnknownMemberType]
                    if agent is None:
                        continue
                    _ = await store.current(org, key, agent)
                    counts["versioned"] += 1
            except Exception:
                counts["failures"] += 1
        if len(page) < PAGE:
            return counts
        last = page[-1].get("_key")
        after = last if isinstance(last, str) else None


async def run_agent_versions_migration(
    config_service: ConfigurationService, graph: IGraphDBProvider, logger: Logger,
) -> dict[str, Any]:
    flag = cast("object", await config_service.get_config(MIGRATION_FLAG_KEY, use_cache=False))  # pyright: ignore[reportUnknownMemberType]
    if isinstance(flag, dict) and cast("dict[str, object]", flag).get("done") is True:
        return {"skipped": True}
    counts = await migrate_agent_versions(graph)
    if counts["failures"] == 0:
        _ = await config_service.set_config(  # pyright: ignore[reportUnknownMemberType]
            MIGRATION_FLAG_KEY, {"done": True, **counts, "timestamp": get_epoch_timestamp_in_ms()},
        )
    logger.info(
        "Agent versions migration (GA-01): %s agent(s), %s given version 1, %s already versioned, "
        "%s without a readable org, %s failure(s)",
        counts["agents"], counts["versioned"], counts["already"], counts["no_org"], counts["failures"],
    )
    return counts
