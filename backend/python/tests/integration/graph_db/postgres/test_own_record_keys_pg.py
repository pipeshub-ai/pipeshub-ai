"""Which org owns a shared virtual record's records, read in batches on a real PostgreSQL graph.

The Neo4j and ArangoDB twin is ``../test_own_record_keys_real_backends.py``. Here every read is scoped
to the bound org, so the batched ``get_nodes_by_field_in("records", "id", ...)`` must answer this
org's records on a virtual record another org shares, and nothing of the other org's.
Needs a migrated database: PG_CORE_URL (a ``pipeshub_connectors_login`` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.models.entities import Record, RecordType
from app.services.vector_db.membership import OWNER_READ_BATCH, own_record_keys

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

RECORDS = CollectionNames.RECORDS.value


@pytest.fixture
async def orgs(pg_provider, pg_db, org_ctx) -> AsyncIterator[tuple[str, str]]:
    a, b = uuid.uuid4().hex[:24], uuid.uuid4().hex[:24]
    yield a, b
    for o in (a, b):
        with org_ctx(o):
            await pg_db.run(lambda tx, o=o: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", o), write=True)


def _node(key: str, vrid: str, org: str) -> dict:
    record = Record(
        id=key, org_id=org, virtual_record_id=vrid, record_name="row", record_type=RecordType.WEBPAGE,
        external_record_id=key, version=1, origin="CONNECTOR", connector_id="conn", connector_name=Connectors.NOTION,
    )
    return {**record.to_arango_base_record(), "virtualRecordId": vrid, "indexingStatus": "COMPLETED", "md5Checksum": "m"}


async def test_a_shared_vrid_answers_each_org_its_own_records_in_batches(pg_provider, orgs, org_ctx) -> None:
    a, b = orgs
    vrid = f"vrid-{uuid.uuid4().hex[:8]}"
    mine = [f"a-{i}" for i in range(2 * OWNER_READ_BATCH + 37)]
    theirs = [f"b-{i}" for i in range(3)]
    with org_ctx(a):
        for start in range(0, len(mine), 500):
            assert await pg_provider.batch_upsert_nodes([_node(k, vrid, a) for k in mine[start:start + 500]], RECORDS)
    with org_ctx(b):
        assert await pg_provider.batch_upsert_nodes([_node(k, vrid, b) for k in theirs], RECORDS)

    reads = []
    real = pg_provider.get_nodes_by_field_in

    async def spy(*args, **kwargs):  # noqa: ANN202
        reads.append(args)
        return await real(*args, **kwargs)

    pg_provider.get_nodes_by_field_in = spy
    try:
        with org_ctx(a):
            assert sorted(await own_record_keys(pg_provider, vrid, a)) == sorted(mine)
        assert len(reads) == 3
        with org_ctx(b):
            assert sorted(await own_record_keys(pg_provider, vrid, b)) == sorted(theirs)
    finally:
        pg_provider.get_nodes_by_field_in = real
