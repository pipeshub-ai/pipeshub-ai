"""One-time backfill of a code file's scope (``connectorId``, ``recordGroupId``) and a record group's ``repoKey`` (F-CODEPATH).

A path is looked up within its connector and repo by those fields; until the backfill has run, the lookup reads every code file at the path, as
before. Batches are idempotent and resume where a stopped run left off. Only when no batch finds work is the marker written, so a run cut short
leaves lookups on the old path.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import system
from app.core.org_jobs import for_each_org

if TYPE_CHECKING:
    from logging import Logger

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

MARKER = "code-path-scope-v1"
_BATCH = 5000


def marker_key(org_id: str) -> str:
    # A sync point key is unique across the store on Neo4j and ArangoDB.
    return f"{MARKER}/{org_id}"


async def scope_ready(graph_provider: IGraphDBProvider, org_id: str) -> bool:
    """Whether this org's code files and record groups carry their scope (the backfill's marker)."""
    point = await graph_provider.get_sync_point(marker_key(org_id), CollectionNames.SYNC_POINTS.value, raise_on_error=True)
    return bool(point and point.get("done") is True)


async def run_code_path_scope_backfill(graph_provider: IGraphDBProvider, logger: Logger) -> int:
    """Backfill every org, then mark each. Returns how many rows were updated."""
    total = 0
    # Started detached with no context: an org-scoped store reaches every organisation only under a system context.
    with system("code_path_scope_backfill"):
        while updated := await graph_provider.backfill_code_path_scope(_BATCH):
            total += updated

    async def mark(org_id: str) -> None:
        if not await scope_ready(graph_provider, org_id):
            await graph_provider.upsert_sync_point(marker_key(org_id), {"orgId": org_id, "done": True}, CollectionNames.SYNC_POINTS.value)
    failed = await for_each_org(graph_provider, "code_path_scope_backfill", mark, logger, raise_on_error=True)
    logger.info("Code path scope backfill: %d row(s) updated, %d org(s) left unmarked", total, len(failed))
    return total
