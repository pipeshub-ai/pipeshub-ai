"""Start-up steps and detached loops that already know each item's org, on PostgreSQL against a real database: the All-team migration, the
storage-release reconciler and named-entity completion. Each runs in an empty ``contextvars.Context()``, as start-up and ``spawn_detached`` do.

The database is shared, so each test asserts on its own orgs only. The All-team migration and named-entity completion also work other tests'
leftover orgs and due jobs (an All team, a gone record's job cleared); the suite runs serially, so no other test is mid-run.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import uuid
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.services import storage_release
from app.connectors.services.storage_release import (
    GRACE_MS,
    StorageReleaseReconciler,
    record_pending_storage_release,
)
from app.core.org_context import peek
from app.migrations.all_team_migration import run_all_team_migration
from app.modules.indexing import named_entity_completion
from app.modules.transformers.blob_storage import StorageDocumentNotFoundError
from app.utils.time_conversion import get_epoch_timestamp_in_ms

from .test_legacy_migrations import orgs as orgs

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from .test_access import World

pytestmark = pytest.mark.integration
LOG = logging.getLogger("boot-steps")


async def unbound(coro: Coroutine[Any, Any, Any]) -> Any:  # noqa: ANN401
    """Run as start-up runs it: no org context at all."""
    return await asyncio.create_task(coro, context=contextvars.Context())


class _KV:
    """The configuration store, in memory."""

    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    async def get_config(self, key: str, default: object = None, **_kw: object) -> object:
        return self.values.get(key, default)

    async def set_config(self, key: str, value: object, **_kw: object) -> bool:
        self.values[key] = value
        return True

    async def delete_config(self, key: str) -> bool:
        return self.values.pop(key, None) is not None

    async def list_keys_in_directory(self, directory: str) -> list[str]:
        return [k for k in self.values if k.startswith(directory)]


async def test_the_all_team_migration_runs_with_no_org_bound(orgs: Callable[[], World], org_ctx, caplog: pytest.LogCaptureFixture) -> None:
    worlds = [orgs(), orgs()]
    for w in worlds:
        with org_ctx(w.org):
            await w.nodes("organizations", {"_key": w.org, "isActive": True})
            for key, ts in (("young", 9), ("old", 1)):
                await w.nodes("users", {"_key": key, "createdAtTimestamp": ts, "isActive": True})
                await w.edge("belongsTo", ("users", key), ("organizations", w.org), entityType="ORGANIZATION")

    with caplog.at_level(logging.INFO, logger=LOG.name):
        result = await unbound(run_all_team_migration(worlds[0].p, _KV(), LOG))

    assert "error" not in result and result["orgs_processed"] >= 2, result
    lines = [r.getMessage() for r in caplog.records]
    for w in worlds:
        assert not any(f"Error processing org {w.org}" in line for line in lines)
        with org_ctx(w.org):
            members = {e["from_id"]: e["role"] for e in await w.p.get_edges_to_node(f"teams/all_{w.org}", "permission")}
        assert members == {"old": "OWNER", "young": "READER"}


async def test_the_storage_release_tick_reads_each_app_in_its_intents_org(orgs: Callable[[], World], org_ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = orgs(), orgs()
    present, elsewhere, gone = (f"app-{uuid.uuid4().hex[:12]}" for _ in range(3))
    with org_ctx(a.org):
        await a.nodes("apps", {"_key": present, "type": "DRIVE", "isActive": True})
    with org_ctx(b.org):
        await b.nodes("apps", {"_key": elsewhere, "type": "DRIVE", "isActive": True})
    released: list[tuple[str, object]] = []

    async def release(*_a: object, connector_id: str, **_kw: object) -> bool:
        released.append((connector_id, peek()))
        return True

    monkeypatch.setattr(storage_release, "release_connector_storage", release)
    monkeypatch.setattr(storage_release, "StorageCleanupHelper", lambda *_a: SimpleNamespace(close=AsyncMock()))
    now = get_epoch_timestamp_in_ms()

    async def tick(connector_id: str) -> str:
        kv = _KV()
        await record_pending_storage_release(kv, org_id=a.org, connector_id=connector_id, now_ms=now - GRACE_MS - 1)
        reconciler = StorageReleaseReconciler(logger=MagicMock(), graph_provider=a.p, config_service=kv,
                                              lock=SimpleNamespace(try_acquire=AsyncMock(return_value=True)), now_ms=lambda: now)
        return await unbound(reconciler.tick())

    assert await tick(present) == "idle"           # org A still holds it: nothing is released
    assert await tick(elsewhere) == "released"     # only org B holds the id: it is gone for org A (a system read would answer B's copy)
    assert await tick(gone) == "released"
    assert [(c, ctx.kind, ctx.org_id, ctx.source) for c, ctx in released] == [
        (c, "job", a.org, "job:storage_release") for c in (elsewhere, gone)
    ]


async def test_named_entity_completion_works_each_orgs_job_in_that_org_with_no_org_bound(orgs: Callable[[], World], org_ctx,
                                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = orgs(), orgs()
    vrid = f"v-{uuid.uuid4().hex[:12]}"
    for w in (a, b):
        with org_ctx(w.org):
            await w.nodes("organizations", {"_key": w.org, "isActive": True})      # the system read of the jobs sweeps the organisations
    with org_ctx(a.org):
        await a.nodes("records", {"_key": "ra", "recordName": "ra", "virtualRecordId": vrid})
        await a.p.schedule_named_entity_reextract(a.org, "ra")
    with org_ctx(b.org):
        await b.p.schedule_named_entity_reextract(b.org, "rb")       # its record is gone
    read: list[tuple[str, str, str | None]] = []

    async def from_storage(virtual_record_id: str, org_id: str) -> None:
        read.append((virtual_record_id, org_id, getattr(peek(), "org_id", None)))
        raise StorageDocumentNotFoundError(virtual_record_id)

    sink = MagicMock()
    sink.blob_storage.get_record_from_storage = from_storage
    monkeypatch.setattr(named_entity_completion, "llm_mostly_free", lambda: True)

    await unbound(named_entity_completion.complete_named_entities(graph_provider=a.p, sink=sink, logger=LOG, page_size=10_000))

    assert [r for r in read if r[0] == vrid] == [(vrid, a.org, a.org)]       # org A's record was found in org A
    for w, record in ((a, "ra"), (b, "rb")):
        with org_ctx(w.org):
            assert [j for j in await w.p.get_due_named_entity_persist_retries(10_000, reextract=True) if j["recordId"] == record] == []
