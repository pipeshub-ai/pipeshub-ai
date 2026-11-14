"""The connectors service's start-up jobs on PostgreSQL: each runs with no org bound, as ``connectors_main`` starts it, and must
still reach every org's data, each org's under its own org (``docs/org-context.md``: enumerate under ``system``, act under ``for_org``).

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.registry.connector_registry import ConnectorRegistry
from app.core import org_context
from app.core.org_context import OrgContext, bind
from app.migrations.code_graph_backfill import run_code_graph_backfill
from app.migrations.nextcloud_user_app_edges_migration import (
    run_nextcloud_user_app_edges_migration,
)
from app.migrations.s3_created_date_filter_migration import (
    run_s3_created_date_filter_migration,
)
from app.migrations.web_browser_identity_migration import (
    run_web_browser_identity_backfill,
)
from app.modules.code_graph import enabled as code_graph_switch
from app.modules.code_graph.build_state import (
    SYNC_POINT_TYPE,
    BuildStateStore,
    sync_point_key_for,
)
from app.modules.code_graph.edge_build_reconciler import (
    reconcile_once,
    request_due_full_rebuilds,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Coroutine

pytestmark = pytest.mark.integration

_LOG = logging.getLogger("startup-jobs")


def _org() -> str | None:
    ctx = org_context.peek()
    return ctx.org_id if ctx else None


class _Config:
    """A config store that records the org bound at each write."""

    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values, self.writes = dict(values or {}), []

    async def get_config(self, key: str, *args: object, **kwargs: object) -> Any:  # noqa: ANN401
        return self.values.get(key)

    async def set_config(self, key: str, value: Any) -> bool:  # noqa: ANN401
        self.values[key] = value
        self.writes.append((key, _org()))
        return True


async def _detached(coro: Coroutine) -> Any:  # noqa: ANN401
    """As ``connectors_main`` runs a start-up job: an empty context, no org bound."""
    return await asyncio.get_running_loop().create_task(coro, context=contextvars.Context())


def _no_org_context_errors(caplog: pytest.LogCaptureFixture, *orgs: str) -> list[str]:
    return [m for r in caplog.records if r.levelno >= logging.WARNING and (
        "org context" in (m := r.getMessage()) or "cannot act for one org" in m or any(f"org={o}" in m for o in orgs))]


@pytest.fixture
async def orgs(pg_provider, org_ctx) -> AsyncIterator[tuple[str, str, str]]:
    tag = uuid.uuid4().hex[:8]
    a, b = uuid.uuid4().hex[:24], uuid.uuid4().hex[:24]
    for org, name in ((a, "a"), (b, "b")):
        with org_ctx(org):
            await pg_provider.batch_upsert_nodes([{"_key": org, "name": f"org {name}", "isActive": True}], "organizations")
    yield a, b, tag
    for org in (a, b):
        with bind(OrgContext(org, None, "job", "job:test", "t")):
            await pg_provider._db.run(lambda tx, org=org: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", org), write=True)
            await pg_provider._db.run(lambda tx, org=org: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)


async def _apps(pg_provider, org_ctx, org: str, apps: list[dict]) -> None:
    with org_ctx(org):
        await pg_provider.batch_upsert_nodes([{**app, "orgId": org, "isActive": True} for app in apps], CollectionNames.APPS.value)


async def test_the_registry_sync_reaches_every_orgs_connectors(pg_provider, org_ctx, orgs, caplog) -> None:
    a, b, tag = orgs
    for org in (a, b):
        await _apps(pg_provider, org_ctx, org, [{"_key": f"gone-{org}-{tag}", "name": "Gone", "type": f"GONE{tag}"}])
    container = MagicMock()
    container.logger.return_value = _LOG
    registry = ConnectorRegistry(container)
    registry._graph_provider = pg_provider

    await _detached(registry.sync_with_database())

    assert not _no_org_context_errors(caplog, a, b)
    for org in (a, b):
        with org_ctx(org):
            assert (await pg_provider.get_document(f"gone-{org}-{tag}", CollectionNames.APPS.value))["isActive"] is False


async def test_the_s3_created_filter_moves_for_every_org_under_its_org(pg_provider, org_ctx, orgs, caplog) -> None:
    a, b, tag = orgs
    created = {"operator": "is_after", "value": {"start": 1000, "end": None}}
    config = _Config()
    for org in (a, b):
        await _apps(pg_provider, org_ctx, org, [{"_key": f"s3-{org}-{tag}", "name": "S3", "type": "S3"}])
        config.values[f"/services/connectors/s3-{org}-{tag}/config"] = {"filters": {"sync": {"values": {"created": created}}}}

    await _detached(run_s3_created_date_filter_migration(config, pg_provider, _LOG))

    assert not _no_org_context_errors(caplog, a, b)
    for org in (a, b):
        path = f"/services/connectors/s3-{org}-{tag}/config"
        assert config.values[path]["filters"]["sync"]["values"] == {"modified": created}
        assert (path, org) in config.writes


async def test_the_web_settings_backfill_reaches_every_org_under_its_org(pg_provider, org_ctx, orgs, caplog) -> None:
    a, b, tag = orgs
    config = _Config()
    for org in (a, b):
        await _apps(pg_provider, org_ctx, org, [{"_key": f"web-{org}-{tag}", "name": "Web", "type": "Web"}])
        config.values[f"/services/connectors/web-{org}-{tag}/config"] = {"sync": {"url": "https://example.com"}}
    container = MagicMock()
    container.config_service.return_value = config

    await _detached(run_web_browser_identity_backfill(container, pg_provider, _LOG))

    assert not _no_org_context_errors(caplog, a, b)
    for org in (a, b):
        path = f"/services/connectors/web-{org}-{tag}/config"
        assert config.values[path]["sync"]["browser_like_identity"] is True
        assert (path, org) in config.writes


async def test_the_nextcloud_edge_cleanup_reaches_every_org(pg_provider, org_ctx, orgs, caplog) -> None:
    a, b, tag = orgs
    for org in (a, b):
        creator, other, app = f"creator-{org}", f"other-{org}", f"nc-{org}-{tag}"
        await _apps(pg_provider, org_ctx, org, [{"_key": app, "name": "Nextcloud", "type": "Nextcloud", "createdBy": creator}])
        with org_ctx(org):
            await pg_provider.batch_upsert_nodes(
                [{"_key": u, "userId": u, "orgId": org, "email": f"{u}@example.com", "isActive": True} for u in (creator, other)],
                CollectionNames.USERS.value)
            await pg_provider.batch_create_edges(
                [{"from_id": u, "from_collection": CollectionNames.USERS.value, "to_id": app, "to_collection": CollectionNames.APPS.value,
                  "syncState": "NOT_STARTED", "lastSyncUpdate": 1} for u in (creator, other)],
                collection=CollectionNames.USER_APP_RELATION.value)

    await _detached(run_nextcloud_user_app_edges_migration(pg_provider, _Config(), _LOG))

    assert not _no_org_context_errors(caplog, a, b)
    for org in (a, b):
        with org_ctx(org):
            edges = await pg_provider.get_edges_to_node(
                f"{CollectionNames.APPS.value}/nc-{org}-{tag}", CollectionNames.USER_APP_RELATION.value, raise_on_error=True)
        assert {str(e.get("from_id") or e.get("_from")).rsplit("/", 1)[-1] for e in edges} == {f"creator-{org}"}


async def test_the_code_edge_reconcile_sweep_reaches_every_org(pg_provider, org_ctx, orgs, caplog) -> None:
    a, b, tag = orgs
    for org in (a, b):
        with org_ctx(org):
            # Owed since the epoch: well past the in-flight window.
            await BuildStateStore(pg_provider).mark_requested(org, f"conn-{org}", f"owed-{tag}", now_ms=0)
            # Built once, never in full: the scheduled full rebuild is owed.
            await pg_provider.upsert_sync_point(
                sync_point_key_for(f"built-{tag}"),
                {"orgId": org, "connectorId": f"conn-{org}", "syncDataPointType": SYNC_POINT_TYPE,
                 "edgeBuildPending": False, "lastEdgeBuildAt": 1},
                CollectionNames.SYNC_POINTS.value)
    started: list[tuple[str, str, str | None]] = []

    class _Events:
        async def start_build(self, org_id: str, connector_id: str, record_group_id: str) -> bool:
            started.append((org_id, record_group_id, _org()))
            return True

    await _detached(request_due_full_rebuilds(pg_provider, _LOG))
    await _detached(reconcile_once(pg_provider, _Events(), _LOG, limit=10_000))

    assert not _no_org_context_errors(caplog, a, b)
    for org in (a, b):
        with org_ctx(org):
            built = await BuildStateStore(pg_provider).read(org, f"built-{tag}")
        assert built.pending and built.full_request_id
    # The full rebuild just asked for is still in flight, so only the long-owed build starts.
    assert sorted(s for s in started if s[1].endswith(tag)) == sorted((org, f"owed-{tag}", org) for org in (a, b))


async def test_the_code_graph_backfill_reaches_every_org_under_its_org(pg_provider, org_ctx, orgs, caplog, monkeypatch) -> None:
    a, b, tag = orgs
    monkeypatch.setenv("CODE_GRAPH_BACKFILL", "true")
    monkeypatch.setattr(code_graph_switch, "CODE_GRAPH_ENABLED", True)
    for org in (a, b):
        with org_ctx(org):
            await pg_provider.batch_upsert_nodes(
                [{"_key": f"file-{org}-{tag}", "orgId": org, "recordType": "CODE_FILE", "indexingStatus": "COMPLETED",
                  "connectorId": f"conn-{org}", "connectorName": "GitHub", "recordGroupId": f"repo-{org}-{tag}"}],
                CollectionNames.RECORDS.value)
    published: list[tuple[str, str | None]] = []

    class _Kafka:
        async def publish_event(self, topic: str, event: dict) -> None:
            published.append((event["payload"]["recordGroupId"], _org()))

    await _detached(run_code_graph_backfill(_Config(), pg_provider, _Kafka(), _LOG, pause_seconds=0))

    assert not _no_org_context_errors(caplog, a, b)
    assert sorted(p for p in published if p[0].endswith(tag)) == sorted((f"repo-{org}-{tag}", org) for org in (a, b))
