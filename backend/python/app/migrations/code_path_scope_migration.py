"""One-time backfill of a code file's scope (``connectorId``, ``recordGroupId``) and a record group's ``repoKey`` (F-CODEPATH).

A path is looked up within its connector and repo by those fields; until the backfill has run, the lookup reads every code file at the path, as
before. Batches are idempotent and resume where a stopped run left off. Only when no batch finds work is the marker written, so a run cut short
leaves lookups on the old path.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.config.constants.arangodb import CollectionNames
from app.core.org_jobs import for_each_org

if TYPE_CHECKING:
    from logging import Logger

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

MARKER = "code-path-scope-v1"
# shortcut: each batch looks for unfilled rows from the start of the org, so a batch slows as the org fills (0.3 s to 0.6 s over 130k code
# files, about 2.5 s near 1M; about 5 min in all, every transaction short); give the stores a keyset cursor if a run gets much longer.
_BATCH = 5000
_IDLE_PASSES = 100


def marker_key(org_id: str) -> str:
    # A sync point key is unique across the store on Neo4j and ArangoDB.
    return f"{MARKER}/{org_id}"


async def scope_ready(graph_provider: IGraphDBProvider, org_id: str) -> bool:
    """Whether this org's code files and record groups carry their scope (the backfill's marker)."""
    point = await graph_provider.get_sync_point(marker_key(org_id), CollectionNames.SYNC_POINTS.value, raise_on_error=True)
    return bool(point and point.get("done") is True)


async def run_code_path_scope_backfill(graph_provider: IGraphDBProvider, logger: Logger) -> int:
    """Backfill each org, internal and external, and mark it once none of its rows is left. Returns how many rows were updated.

    Each org is filled, checked and marked on its own: an org whose backfill fails or makes no progress stays unmarked (one WARNING), and the
    others are still marked."""
    total = 0

    async def fill(org_id: str) -> None:
        nonlocal total
        idle = 0
        while True:
            if updated := await graph_provider.backfill_code_path_scope(_BATCH, org_id=org_id):
                total, idle = total + updated, 0
                continue
            # A batch can update nothing while rows remain (writers filled what it picked): done only when none is left.
            if not await graph_provider.code_path_scope_pending(org_id=org_id):
                break
            idle += 1
            if idle >= _IDLE_PASSES:
                raise RuntimeError("code path scope backfill made no progress; it runs again at the next start")
        if not await scope_ready(graph_provider, org_id):
            await graph_provider.upsert_sync_point(marker_key(org_id), {"orgId": org_id, "done": True}, CollectionNames.SYNC_POINTS.value)
    # A failed org list raises: the run is then not taken as done.
    failed = await for_each_org(graph_provider, "code_path_scope_backfill", fill, logger, raise_on_error=True, external=True)
    logger.info("Code path scope backfill: %d row(s) updated, %d org(s) left unmarked", total, len(failed))
    return total
