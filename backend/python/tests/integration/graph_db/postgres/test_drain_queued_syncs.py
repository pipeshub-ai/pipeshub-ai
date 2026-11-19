"""The connectors service's drain of syncs parked at the concurrency limit, on PostgreSQL. It is called with no org bound (the startup
drain, inside ``spawn_detached``) and inside one org's context (a finishing sync's finalizer, a lease given back by the event path).
Either way one pass must release every org's queued connectors, each published under its own org (``docs/org-context.md``).

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.sync.sync_dispatcher import SubmitResult
from app.connectors.core.sync.sync_runner import drain_queued_syncs
from app.core import org_context
from app.core.org_context import OrgContext, bind
from app.utils.logger import log_record_fields

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration


@pytest.fixture
async def queued_orgs(pg_provider, org_ctx) -> AsyncIterator[tuple[str, str, str]]:
    """Two orgs, each with a connector parked at the limit (owed a resync) and one disabled while it waited."""
    tag = uuid.uuid4().hex[:8]
    a, b = uuid.uuid4().hex[:24], uuid.uuid4().hex[:24]
    for org, name in ((a, "a"), (b, "b")):
        with org_ctx(org):
            await pg_provider.batch_upsert_nodes([{"_key": org, "name": f"org {name}", "isActive": True}], "organizations")
            apps = [{"_key": f"queued-{name}-{tag}", "type": "JIRA", "isActive": True},
                    {"_key": f"off-{name}-{tag}", "type": "SLACK", "isActive": False}]
            await pg_provider.batch_upsert_nodes(
                [{**app, "status": "QUEUED", "pendingResync": True, "orgId": org} for app in apps], "apps")
            await pg_provider.batch_create_edges(
                [{"from_collection": "organizations", "from_id": org, "to_collection": "apps", "to_id": app["_key"]} for app in apps],
                "orgAppRelation")
    yield a, b, tag
    for org in (a, b):
        with bind(OrgContext(org, None, "job", "job:test", "t")):
            for table in ("graph.edges", "graph.nodes"):
                await pg_provider._db.run(lambda tx, org=org, t=table: tx.execute(f"DELETE FROM {t} WHERE org_id = $1", org), write=True)


def _fakes() -> tuple[MagicMock, MagicMock, list[tuple[str, str, str | None]]]:
    """A dispatcher that records (connector, the spec's org, the org bound when it published), and a one-process coordinator."""
    published: list[tuple[str, str, str | None]] = []

    async def submit(spec) -> SubmitResult:
        bound = org_context.peek()
        published.append((spec.connector_id, spec.org_id, bound.org_id if bound else None))
        return SubmitResult.ACCEPTED

    dispatcher = MagicMock()
    dispatcher.submit = AsyncMock(side_effect=submit)
    # No drain_budget: a shared database's other orgs may hold owed connectors that would use up any fixed budget.
    coordinator = MagicMock(spec=["try_claim_once"])
    coordinator.try_claim_once = AsyncMock(return_value=True)
    return dispatcher, coordinator, published


async def _drain(pg_provider, *, bound_org: str | None) -> list[tuple[str, str, str | None]]:
    dispatcher, coordinator, published = _fakes()
    context = contextvars.Context()          # as spawn_detached: nothing bound
    if bound_org:                            # as a finalizer or the event path: the consumed message's org
        context.run(org_context._ctx.set, OrgContext(bound_org, None, "service", "envelope", "t"))
    with patch("app.connectors.core.sync.sync_dispatcher.get_dispatcher", return_value=dispatcher), \
            patch("app.connectors.core.sync.sync_coordinator.get_coordinator", return_value=coordinator):
        await asyncio.get_running_loop().create_task(drain_queued_syncs(pg_provider, logging.getLogger("drain")), context=context)
    return published


async def _assert_every_org_drained(pg_provider, org_ctx, published, a: str, b: str, tag: str, caplog) -> None:
    # Only this test's orgs and the org list (no org bound): the pass walks every org in the shared database (and drains their
    # leftovers, harmless in a serial run), and another test's leftover org is not this test's.
    assert not [r.getMessage() for r in caplog.records if r.name == "drain" and r.levelno >= logging.ERROR
                and log_record_fields(r).get("org_id") in (a, b, None)]
    mine = sorted(p for p in published if p[0].endswith(tag))      # the database is shared: other tests' queues are not this test's
    assert mine == sorted([(f"queued-a-{tag}", a, a), (f"queued-b-{tag}", b, b)])
    for org, name in ((a, "a"), (b, "b")):
        with org_ctx(org):
            queued = await pg_provider.get_document(f"queued-{name}-{tag}", "apps")
            off = await pg_provider.get_document(f"off-{name}-{tag}", "apps")
        assert (queued["status"], queued["pendingResync"]) == ("QUEUED", False)
        assert (off["status"], off["pendingResync"]) == ("IDLE", False)


async def test_the_startup_drain_releases_every_orgs_queued_syncs(pg_provider, org_ctx, queued_orgs, caplog) -> None:
    a, b, tag = queued_orgs
    caplog.set_level(logging.INFO)
    published = await _drain(pg_provider, bound_org=None)
    await _assert_every_org_drained(pg_provider, org_ctx, published, a, b, tag, caplog)


async def test_a_drain_inside_one_orgs_context_releases_the_other_orgs_queued_syncs(pg_provider, org_ctx, queued_orgs, caplog) -> None:
    a, b, tag = queued_orgs
    caplog.set_level(logging.INFO)
    published = await _drain(pg_provider, bound_org=a)
    await _assert_every_org_drained(pg_provider, org_ctx, published, a, b, tag, caplog)
