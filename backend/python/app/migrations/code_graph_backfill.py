"""Re-index code repositories whose blocks predate the code graph or its current version (O-10).

A repo indexed before the code graph existed has code file records and no
blocks; one indexed under an older PARSER_VERSION has blocks that miss
what the current parser extracts. Either way its edges are built against a
partial or stale symbol table until every file changes. This asks the
repo's connector to re-index it -- the same event the "Reindex" action
sends -- and the build that follows the drain does the rest.

Off by default: it runs only with CODE_GRAPH_BACKFILL=true (and
CODE_GRAPH_ENABLED not false), since it re-indexes every such repo at once on
deploy. Then it runs once per PARSER_VERSION (the flag carries it), in the
connectors service's post-startup task, one repo at a time a few seconds apart
so an install with many repos does not flood indexing at once.

Without the flag an admin builds a repo's graph by re-indexing it: the
record-group reindex (POST /api/v1/record-groups/{id}/reindex, depth -1) sends
the event this module sends, or the connector reindex with statusFilters
["COMPLETED"] (without filters it re-indexes FAILED records only). Each code
file re-indexed is projected to blocks before it is marked COMPLETED, and the
edge build requested once the repo drains writes the edges.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, NamedTuple

from app.config.constants.arangodb import CollectionNames, ProgressStatus, RecordTypes
from app.connectors.services.reindex_events import build_reindex_event
from app.modules.code_graph import enabled as code_graph_switch
from app.modules.code_graph.version import PARSER_VERSION
from app.utils.env_utils import env_bool
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from logging import Logger

    from app.config.configuration_service import ConfigurationService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = ["MIGRATION_FLAG_PREFIX", "StaleRepo", "find_stale_repos", "run_code_graph_backfill"]

MIGRATION_FLAG_PREFIX = "/migrations/code_graph_backfill/"
_PAGE = 2000
_PAUSE_SECONDS = 3.0


class StaleRepo(NamedTuple):
    org_id: str
    connector_id: str
    connector_name: str
    record_group_id: str
    blocks: int
    current_blocks: int


async def find_stale_repos(graph_provider: IGraphDBProvider) -> list[StaleRepo]:
    """Repos with an indexed code file and no blocks, or blocks of another version."""
    groups: dict[str, tuple[str, str, str]] = {}
    after: str | None = None
    while True:
        page = await graph_provider.get_documents_paginated(
            collection=CollectionNames.RECORDS.value, limit=_PAGE, sort_field="_key",
            after_key=after, raise_on_error=True,
            filters={"recordType": RecordTypes.CODE_FILE.value,
                     "indexingStatus": ProgressStatus.COMPLETED.value},
            return_fields=["_key", "orgId", "connectorId", "connectorName", "recordGroupId"],
        )
        for row in page or []:
            group = row.get("recordGroupId")
            if group and group not in groups and row.get("orgId") and row.get("connectorId"):
                groups[group] = (row["orgId"], row["connectorId"], row.get("connectorName") or "")
        if not page or len(page) < _PAGE:
            break
        after = page[-1].get("_key")

    stale: list[StaleRepo] = []
    for group, (org_id, connector_id, connector_name) in sorted(groups.items()):
        scope = {"orgId": org_id, "recordGroupId": group}
        blocks = await graph_provider.count_nodes_by_filters(CollectionNames.BLOCKS.value, scope)
        current = await graph_provider.count_nodes_by_filters(
            CollectionNames.BLOCKS.value, {**scope, "parserVersion": PARSER_VERSION},
        )
        if blocks == 0 or current < blocks:
            stale.append(StaleRepo(org_id, connector_id, connector_name, group, blocks, current))
    return stale


async def run_code_graph_backfill(
    config_service: ConfigurationService,
    graph_provider: IGraphDBProvider,
    kafka_service: Any,
    logger: Logger,
    *,
    pause_seconds: float = _PAUSE_SECONDS,
) -> int:
    """Ask each stale repo's connector to re-index it, then set this version's flag.

    Returns how many repos were asked. A failure leaves the flag unset, so the
    next start tries again; asking twice is harmless (the reindex task dedupes).
    """
    if not code_graph_switch.CODE_GRAPH_ENABLED:
        logger.info("Code graph backfill skipped: CODE_GRAPH_ENABLED is false")
        return 0
    if not env_bool("CODE_GRAPH_BACKFILL", False):
        logger.info("Code graph backfill skipped: set CODE_GRAPH_BACKFILL=true or reindex "
                    "a connector to build its code graph")
        return 0
    flag_key = f"{MIGRATION_FLAG_PREFIX}{PARSER_VERSION}"
    try:
        flag = await config_service.get_config(flag_key, use_cache=False)
    except Exception:
        flag = None
    if isinstance(flag, dict) and flag.get("done") is True:
        return 0

    stale = await find_stale_repos(graph_provider)
    for i, repo in enumerate(stale):
        connector = repo.connector_name.replace(" ", "").lower()
        event = build_reindex_event(
            event_type=f"{connector}.reindex", org_id=repo.org_id, connector_id=repo.connector_id,
            connector_name=connector, record_group_id=repo.record_group_id, depth=-1,
        )
        await kafka_service.publish_event(event["topic"], {
            "eventType": event["eventType"],
            "timestamp": get_epoch_timestamp_in_ms(),
            "payload": event["payload"],
        })
        logger.info("Code graph backfill: re-indexing %s (%d of %d blocks at %s)",
                    repo.record_group_id, repo.current_blocks, repo.blocks, PARSER_VERSION)
        if i + 1 < len(stale):
            await asyncio.sleep(pause_seconds)

    try:
        await config_service.set_config(flag_key, {"done": True, "repos": len(stale)})
    except Exception:
        logger.warning("Code graph backfill finished but its flag was not saved; "
                       "it will look again on the next start", exc_info=True)
    logger.info("Code graph backfill for %s: %d repo(s) re-indexed", PARSER_VERSION, len(stale))
    return len(stale)
