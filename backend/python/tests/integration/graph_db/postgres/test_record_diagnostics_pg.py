"""The record processing diagnostics through the PostgreSQL graph provider, with strict schema validation.

- a record without the fields and one with them both write, update and read back;
- JSON null is legal; wrong types are refused and change nothing;
- ``api_record`` strips the fields from what the provider returns.

Needs a migrated database (PG_CORE_URL, PG_POOLER=none), as the other tests in this folder do.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from app.schema.node_validator import SchemaValidationError
from app.services.graph_db.common.utils import api_record

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration
RECORDS = "records"
DIAGNOSTICS = {"firstQueuedAt": 1_700_000_000_000, "failedAt": 1_700_000_100_000, "indexingAttempts": 2, "pageCount": 12}


def _record(key: str, org: str, **extra: object) -> dict[str, object]:
    return {
        "_key": key, "orgId": org, "recordName": key, "externalRecordId": key, "recordType": "FILE",
        "origin": "CONNECTOR", "connectorName": "KB", "connectorId": f"{org}-c", "version": 1,
        "createdAtTimestamp": 1, "updatedAtTimestamp": 1, "indexingStatus": "NOT_STARTED", **extra,
    }


@pytest.fixture
async def org(pg_db, org_ctx, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[str]:
    monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "strict")
    org_id = uuid.uuid4().hex[:24]
    yield org_id
    with org_ctx(org_id):
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", org_id), write=True)
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org_id), write=True)


async def test_old_and_new_records_write_update_and_read_back(pg_provider, org, org_ctx) -> None:
    with org_ctx(org):
        await pg_provider.batch_upsert_nodes([_record("old", org)], RECORDS)
        await pg_provider.batch_upsert_nodes([_record("new", org, **DIAGNOSTICS)], RECORDS)
        await pg_provider.batch_upsert_nodes([_record("nul", org, **dict.fromkeys(DIAGNOSTICS))], RECORDS)
        assert await pg_provider.update_node("old", RECORDS, {"indexingStatus": "QUEUED"})
        assert await pg_provider.update_node("old", RECORDS, {"firstQueuedAt": 5, "indexingAttempts": 0})
        assert await pg_provider.update_node("new", RECORDS, {"failedAt": None, "indexingAttempts": 3})
        old = await pg_provider.get_document("old", RECORDS)
        new = await pg_provider.get_document("new", RECORDS)

        assert (old["firstQueuedAt"], old["indexingAttempts"]) == (5, 0)
        assert (new["firstQueuedAt"], new.get("failedAt"), new["indexingAttempts"], new["pageCount"]) == (
            DIAGNOSTICS["firstQueuedAt"], None, 3, 12,
        )
        for doc in (old, new):
            assert not set(DIAGNOSTICS) & set(api_record(doc))


@pytest.mark.parametrize(
    "bad",
    [
        {"pageCount": 0}, {"pageCount": 1.5}, {"indexingAttempts": -1}, {"failedAt": "x"}, {"firstQueuedAt": "1"},
        {"pageCount": True}, {"indexingAttempts": 2.0}, {"pageCount": 10**30},
    ],
)
async def test_a_wrong_typed_diagnostic_is_refused_and_changes_nothing(pg_provider, org, org_ctx, bad) -> None:
    with org_ctx(org):
        await pg_provider.batch_upsert_nodes([_record("r", org)], RECORDS)
        try:
            refused = not await pg_provider.update_node("r", RECORDS, bad)
        except SchemaValidationError:
            refused = True
        assert refused, bad
        with pytest.raises(SchemaValidationError):
            await pg_provider.batch_upsert_nodes([_record("r2", org, **bad)], RECORDS)
        ((name, value),) = bad.items()
        assert (await pg_provider.get_document("r", RECORDS)).get(name) != value
        assert await pg_provider.get_document("r2", RECORDS) is None
