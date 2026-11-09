"""The connectors service's startup sweep of stale sync state on PostgreSQL: it runs detached (no org bound) and must still reset
every org's connectors that a crash left SYNCING, FULL_SYNCING or locked, each under its own org (``docs/org-context.md``).

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import uuid
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from app.connectors_main import reset_stale_sync_state
from app.core.org_context import OrgContext, bind

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration


@pytest.fixture
async def crashed_orgs(pg_provider, org_ctx) -> AsyncIterator[tuple[str, str, str]]:
    """Two orgs whose connectors a crash left mid-sync, mid-full-sync, locked in the full-sync prep window, and locked mid-deletion."""
    tag = uuid.uuid4().hex[:8]
    a, b = uuid.uuid4().hex[:24], uuid.uuid4().hex[:24]
    for org, name in ((a, "a"), (b, "b")):
        with org_ctx(org):
            await pg_provider.batch_upsert_nodes([{"_key": org, "name": f"org {name}", "isActive": True}], "organizations")
            await pg_provider.batch_upsert_nodes(
                [{"_key": f"syncing-{name}-{tag}", "type": "JIRA", "status": "SYNCING", "isLocked": False, "orgId": org},
                 {"_key": f"full-{name}-{tag}", "type": "DRIVE", "status": "FULL_SYNCING", "isLocked": True, "orgId": org},
                 {"_key": f"locked-{name}-{tag}", "type": "SLACK", "status": "IDLE", "isLocked": True, "orgId": org},
                 {"_key": f"deleting-{name}-{tag}", "type": "GITLAB", "status": "DELETING", "isLocked": True, "orgId": org},
                 {"_key": f"idle-{name}-{tag}", "type": "CONFLUENCE", "status": "IDLE", "isLocked": False, "orgId": org}],
                "apps")
    yield a, b, tag
    for org in (a, b):
        with bind(OrgContext(org, None, "job", "job:test", "t")):
            await pg_provider._db.run(lambda tx, org=org: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)


async def test_a_detached_startup_sweep_resets_every_orgs_stale_sync_state(pg_provider, org_ctx, crashed_orgs, caplog) -> None:
    a, b, tag = crashed_orgs
    caplog.set_level(logging.INFO)
    # As _post_startup runs it: inside spawn_detached, an empty context. No coordinator: nothing can be syncing at boot in one process.
    with patch("app.connectors_main.get_coordinator", return_value=None):
        await asyncio.get_running_loop().create_task(
            reset_stale_sync_state(pg_provider, logging.getLogger("startup-sweep")), context=contextvars.Context())

    # Only this test's orgs and the org list: the sweep spans every org in the shared database, and leftovers are not this test's.
    assert not [m for r in caplog.records if r.levelno >= logging.WARNING and "failed" in (m := r.getMessage())
                and (f"org={a}" in m or f"org={b}" in m or "Startup sweep for stale sync state failed:" in m)]
    for org, name in ((a, "a"), (b, "b")):
        with org_ctx(org):
            doc = {k: await pg_provider.get_document(f"{k}-{name}-{tag}", "apps") for k in ("syncing", "full", "locked", "deleting", "idle")}
        assert (doc["syncing"]["status"], doc["syncing"]["isLocked"], doc["syncing"]["lastSyncOutcome"]) == ("IDLE", False, "failed")
        assert (doc["full"]["status"], doc["full"]["isLocked"]) == ("IDLE", False)
        assert (doc["full"]["pendingFullSync"], doc["full"]["pendingResync"]) == (True, True)
        assert (doc["locked"]["status"], doc["locked"]["isLocked"]) == ("IDLE", False)
        assert (doc["deleting"]["status"], doc["deleting"]["isLocked"]) == ("DELETING", False)
        assert "lastSyncOutcome" not in doc["idle"]
