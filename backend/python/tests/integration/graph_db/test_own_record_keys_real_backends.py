"""Which org owns a shared virtual record's records, read in batches on a real Neo4j and ArangoDB.

``own_record_keys`` decides whether a delete drops a virtual record's points or keeps them for the
records still on it. It reads the owners with ``get_nodes_by_field_in`` by ``id`` with
``return_fields=["id", "orgId"]``; each store maps ``id`` to its own key field (ArangoDB filters and
projects ``_key``), so only a real store shows that the batch answers the keys it was asked for and
the org each one names. The PostgreSQL twin is ``postgres/test_own_record_keys_pg.py``.

Runs on Neo4j and ArangoDB (backend-matrix). Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""
from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.models.entities import Record, RecordType
from app.services.vector_db.membership import OWNER_READ_BATCH, own_record_keys
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

RECORDS = CollectionNames.RECORDS.value
ORG = "org-owner-it"
OTHER_ORG = "org-owner-it-other"
logger = logging.getLogger("own-record-keys-it")


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[IGraphDBProvider, str, list[str]]]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            provider = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, "own_record_keys_it")
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(provider, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        created: list[str] = []
        yield provider, uuid.uuid4().hex[:8], created
        if created:
            await provider.delete_nodes(created, RECORDS)


def _node(key: str, vrid: str, org: str) -> dict:
    record = Record(
        id=key, org_id=org, virtual_record_id=vrid, record_name="row", record_type=RecordType.WEBPAGE,
        external_record_id=key, version=1, origin="CONNECTOR", connector_id="conn", connector_name=Connectors.NOTION,
    )
    return {**record.to_arango_base_record(), "virtualRecordId": vrid, "indexingStatus": "COMPLETED", "md5Checksum": "m"}


async def test_a_shared_vrid_answers_only_this_orgs_records_in_batches(world) -> None:
    provider, run, created = world
    vrid = f"{run}-vrid"
    # More records than two batches hold, as Notion pages whose bodies hash the same do, with every
    # 50th one another org's.
    keys = [f"{run}-r{i}" for i in range(2 * OWNER_READ_BATCH + 37)]
    nodes = [_node(k, vrid, OTHER_ORG if i % 50 == 0 else ORG) for i, k in enumerate(keys)]
    for start in range(0, len(nodes), 500):
        assert await provider.batch_upsert_nodes(nodes[start:start + 500], RECORDS)
    created.extend(keys)

    reads = []
    real = provider.get_nodes_by_field_in

    async def spy(*args, **kwargs):  # noqa: ANN202
        reads.append(args)
        return await real(*args, **kwargs)

    provider.get_nodes_by_field_in = spy  # type: ignore[method-assign]
    try:
        own = await own_record_keys(provider, vrid, ORG)
    finally:
        provider.get_nodes_by_field_in = real  # type: ignore[method-assign]

    assert sorted(own) == sorted(k for i, k in enumerate(keys) if i % 50)
    assert len(reads) == 3


async def test_a_vrid_held_only_by_another_org_is_this_orgs_to_drop(world) -> None:
    provider, run, created = world
    vrid = f"{run}-theirs"
    keys = [f"{run}-t{i}" for i in range(3)]
    assert await provider.batch_upsert_nodes([_node(k, vrid, OTHER_ORG) for k in keys], RECORDS)
    created.extend(keys)

    assert await own_record_keys(provider, vrid, ORG) == []
    assert sorted(await own_record_keys(provider, vrid, OTHER_ORG)) == sorted(keys)
