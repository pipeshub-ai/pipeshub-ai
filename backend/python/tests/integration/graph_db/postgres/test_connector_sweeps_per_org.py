"""The connector sweeps (record label repair, stored-content heal) on PostgreSQL, where every read of a connector or record needs one org.

A tick lists the orgs and sweeps each one's connectors under that org's own context: with two orgs, each org's connector is visited and
repaired inside its org, and nothing raises for the missing org that an unscoped read of the connectors would hit.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import copy
import logging
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.services.stored_content_heal import (
    HEAL_VERSION,
    StoredContentHeal,
    StoredContentHealState,
)
from app.core.org_context import current_org_id
from app.modules.indexing.record_label_repair import (
    REPAIR_VERSION,
    RecordLabelRepair,
    RecordLabelRepairState,
)
from app.modules.transformers.blob_storage import StoredRecordNotFound

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]

logger = logging.getLogger("pg-connector-sweeps")


class _Leader:
    async def try_acquire(self) -> bool:
        return True

    async def refresh(self) -> bool:
        return True


class _Blob:
    """Stored records by virtual record id; each write keeps the org it was for and the org bound at the time."""

    def __init__(self) -> None:
        self.stored: dict[str, dict[str, Any]] = {}
        self.writes: list[tuple[str, str, str]] = []
        self.config_service = MagicMock()
        self.config_service.get_config = AsyncMock(return_value={"featureFlags": {}})

    async def get_document_id_by_virtual_record_id(self, vrid: str) -> dict[str, str] | None:
        return {"record_doc_id": f"doc-{vrid}"} if vrid in self.stored else None

    async def get_document_ids_by_virtual_record_ids(self, vrids: list[str], *, raise_on_error: bool = False) -> dict[str, dict]:
        return {v: {"record_doc_id": f"doc-{v}"} for v in vrids if v in self.stored}

    async def get_record_from_storage(self, vrid: str, org_id: str, lookup_result: object = None) -> dict[str, Any] | None:
        return copy.deepcopy(self.stored.get(vrid))

    async def rewrite_semantic_metadata(self, org_id: str, record_id: str, vrid: str, transform: Any, *,  # noqa: ANN401
                                        stored_write: object, sidecar: object = None) -> dict[str, Any] | None:
        stored = await self.get_record_from_storage(vrid, org_id)
        if stored is None:
            raise StoredRecordNotFound(vrid)
        new = await transform(stored)
        if new is None:
            return None
        self.stored[vrid] = {**stored, "semantic_metadata": new}
        self.writes.append((org_id, vrid, current_org_id()))
        return self.stored[vrid]


@pytest.fixture
async def two_orgs(pg_provider, pg_db, org_ctx, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, str]]:
    """Two orgs, each with one connector, one record whose stored labels lost the record's own spelling, and its topic edge."""
    tag = uuid.uuid4().hex[:8]
    apps = {uuid.uuid4().hex[:24]: f"conn-{n}-{tag}" for n in ("a", "b")}
    try:
        for org, app in apps.items():
            record, vrid = f"rec-{app}", f"vr-{app}"
            with org_ctx(org):
                await pg_provider.batch_upsert_nodes([{"_key": org, "name": "Org", "isActive": True}], "organizations")
                await pg_provider.batch_upsert_nodes([{"_key": app, "name": "Web", "type": "WEB", "isActive": True, "orgId": org}], "apps")
                await pg_provider.batch_upsert_nodes([{
                    "_key": record, "orgId": org, "recordName": record, "connectorId": app, "virtualRecordId": vrid,
                    "indexingStatus": "COMPLETED", "isDeleted": False, "lastExtractionTimestamp": 1,
                }], "records")
                await pg_provider.batch_upsert_nodes([{
                    "_key": f"t-{app}", "name": "Falcon launch window", "normalizedName": "falcon launch window", "orgId": org,
                    "createdAtTimestamp": 1,
                }], "topics")
                await pg_provider.batch_create_edges([{
                    "from_collection": "records", "from_id": record, "to_collection": "topics", "to_id": f"t-{app}",
                    "extractedName": "Product launch window", "createdAtTimestamp": 1,
                }], "belongsToTopic")
        # The database is shared with other tests: the sweep lists every org, as it does in service, and sweeps only these two.
        real = pg_provider.get_all_orgs

        async def these_orgs(**kw: object) -> list[dict]:
            return [o for o in await real(**kw) if o.get("_key") in apps]

        monkeypatch.setattr(pg_provider, "get_all_orgs", these_orgs)
        yield apps
    finally:
        for org in apps:
            with org_ctx(org):
                await pg_db.run(lambda tx, org=org: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", org), write=True)
                await pg_db.run(lambda tx, org=org: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)


async def _app(pg_provider, org_ctx, org: str, app: str) -> dict:
    with org_ctx(org):
        doc = await pg_provider.get_document(app, "apps", raise_on_error=True)
    assert doc is not None
    return doc


async def test_the_label_repair_repairs_each_orgs_connector_inside_that_org(pg_provider, org_ctx, two_orgs) -> None:
    blob = _Blob()
    for app in two_orgs.values():
        blob.stored[f"vr-{app}"] = {
            "id": f"rec-{app}", "record_name": app,
            "semantic_metadata": {"summary": "s", "categories": [], "topics": ["Falcon launch window"]},
        }
    repair = RecordLabelRepair(logger=logger, graph_provider=pg_provider, blob_store=blob, lock=_Leader(), cutoff_ms=10, page_size=10)

    outcomes = [await repair.tick() for _ in range(3)]

    assert outcomes == ["page", "page", "idle"]
    assert sorted(blob.writes) == sorted((org, f"vr-{app}", org) for org, app in two_orgs.items())
    for org, app in two_orgs.items():
        assert blob.stored[f"vr-{app}"]["semantic_metadata"]["topics"] == ["Product launch window"]
        doc = await _app(pg_provider, org_ctx, org, app)
        assert doc[RecordLabelRepairState.STATE] == REPAIR_VERSION
        assert doc[RecordLabelRepairState.REPAIRED] == 1


async def test_the_stored_content_heal_checks_each_orgs_connector_inside_that_org(pg_provider, org_ctx, two_orgs) -> None:
    blob, checked = _Blob(), []
    for app in two_orgs.values():
        blob.stored[f"vr-{app}"] = {"id": f"rec-{app}"}
    storage = MagicMock()

    async def find_missing_documents(org_id: str, doc_ids: list[str]) -> list[str]:
        checked.append((org_id, current_org_id()))
        return []

    storage.find_missing_documents = find_missing_documents
    heal = StoredContentHeal(logger=logger, graph_provider=pg_provider, blob_store=blob, storage=storage, publish=AsyncMock(), lock=_Leader())

    outcomes = [await heal.tick() for _ in range(3)]

    assert outcomes == ["page", "page", "idle"]
    assert sorted(checked) == sorted((org, org) for org in two_orgs)
    for org, app in two_orgs.items():
        doc = await _app(pg_provider, org_ctx, org, app)
        assert doc[StoredContentHealState.STATE] == HEAL_VERSION
        assert doc[StoredContentHealState.CHECKED] == 1


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
async def test_the_heal_window_counts_every_orgs_holders_each_read_in_its_own_org(pg_provider, org_ctx, two_orgs, monkeypatch, mode: str) -> None:
    """The re-index window is one for every org: a holder another org is still indexing fills a slot, and is read only inside its own org
    (another org's records read as nothing there, and refuse under enforce)."""
    monkeypatch.setenv("ORG_CONTEXT_MODE", mode)
    blob, checked = _Blob(), []
    for org, app in two_orgs.items():
        blob.stored[f"vr-{app}"] = {"id": f"rec-{app}"}
        with org_ctx(org):
            await pg_provider.batch_upsert_nodes([{"_key": f"held-{app}", "orgId": org, "recordName": "held", "connectorId": "held",
                                                   "indexingStatus": "QUEUED", "isDeleted": False}], "records")
    storage = MagicMock()

    async def find_missing_documents(org_id: str, doc_ids: list[str]) -> list[str]:
        checked.append(org_id)
        return []

    storage.find_missing_documents = find_missing_documents
    heal = StoredContentHeal(logger=logger, graph_provider=pg_provider, blob_store=blob, storage=storage, publish=AsyncMock(), lock=_Leader(),
                             max_in_flight=2)
    held = {f"held-{app}": org for org, app in two_orgs.items()}
    heal._in_flight = dict(held)

    assert await heal.tick() == "waiting"
    assert heal._in_flight == held
    assert checked == []

    for org, app in two_orgs.items():
        with org_ctx(org):
            await pg_provider.update_node(f"held-{app}", "records", {"indexingStatus": "COMPLETED"})
    assert [await heal.tick() for _ in range(3)] == ["page", "page", "idle"]
    assert heal._in_flight == {}
    assert sorted(checked) == sorted(two_orgs)
