"""``get_virtual_record_ids_in_use`` against a real Neo4j and a real ArangoDB.

The orphan sweep in stale recovery asks it once per page instead of asking
``get_records_by_virtual_record_id`` once per VRID, and deletes vectors on the
strength of the answer, so it must agree with the per-VRID read for every
VRID: live and trashed holders kept apart, a record predating ``isDeleted``
counted as live, and no org or connector scoping.

Runs on Neo4j and ArangoDB (backend-matrix). Environment: NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "virtual_record_ids_in_use_it"
RECORDS = CollectionNames.RECORDS.value

logger = logging.getLogger("virtual-record-ids-in-use-it")


def _record(key: str, org_id: str, vrid: str, *, deleted: bool | None) -> dict:
    now = get_epoch_timestamp_in_ms()
    doc = {
        "_key": key,
        "orgId": org_id,
        "recordName": key,
        "externalRecordId": f"ext-{key}",
        "recordType": "FILE",
        "version": 1,
        "origin": "CONNECTOR",
        "connectorName": "GITHUB",
        "connectorId": f"conn-{org_id}",
        "createdAtTimestamp": now,
        "updatedAtTimestamp": now,
        "indexingStatus": "COMPLETED",
        "virtualRecordId": vrid,
    }
    if deleted is not None:
        doc["isDeleted"] = deleted
    return doc


@pytest.fixture(params=["neo4j", "arango"])
async def graph(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[IGraphDBProvider, str]]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            provider = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(provider, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        run = uuid.uuid4().hex[:10]
        yield provider, run
        if isinstance(provider, Neo4jProvider):
            await provider.client.execute_query(
                "MATCH (r:Record) WHERE r.id STARTS WITH $p DETACH DELETE r", parameters={"p": f"{run}-"},
            )
        else:
            await provider.http_client.execute_aql(
                f"FOR r IN {RECORDS} FILTER STARTS_WITH(r._key, @p) REMOVE r IN {RECORDS}", {"p": f"{run}-"},
            )


@pytest.mark.asyncio
async def test_agrees_with_the_per_vrid_read(graph: tuple[IGraphDBProvider, str]) -> None:
    provider, run = graph
    vr = {name: f"{run}-vr-{name}" for name in ("live", "legacy", "trashed", "both", "other_org", "none")}
    records = [
        _record(f"{run}-r1", "org-a", vr["live"], deleted=False),
        # Written before soft delete existed: no isDeleted at all, which is live.
        _record(f"{run}-r2", "org-a", vr["legacy"], deleted=None),
        _record(f"{run}-r3", "org-a", vr["trashed"], deleted=True),
        _record(f"{run}-r4", "org-a", vr["both"], deleted=False),
        _record(f"{run}-r5", "org-a", vr["both"], deleted=True),
        _record(f"{run}-r6", "org-a", vr["both"], deleted=False),
        # Content shared across orgs still counts: the question is whether
        # anything at all references it.
        _record(f"{run}-r7", "org-b", vr["other_org"], deleted=False),
    ]
    assert await provider.batch_upsert_nodes(records, RECORDS)

    asked = [*vr.values(), vr["live"], ""]
    live = await provider.get_virtual_record_ids_in_use(asked)
    trashed = await provider.get_virtual_record_ids_in_use(asked, visibility=RecordVisibility.DELETED)

    assert live == {vr["live"], vr["legacy"], vr["both"], vr["other_org"]}
    assert trashed == {vr["trashed"], vr["both"]}
    for vrid in vr.values():
        for visibility, answer in ((RecordVisibility.LIVE, live), (RecordVisibility.DELETED, trashed)):
            held = await provider.get_records_by_virtual_record_id(
                vrid, raise_on_error=True, visibility=visibility,
            )
            assert (vrid in answer) == bool(held), (vrid, visibility)


@pytest.mark.asyncio
async def test_nothing_asked_is_nothing_in_use(graph: tuple[IGraphDBProvider, str]) -> None:
    provider, _run = graph
    assert await provider.get_virtual_record_ids_in_use([]) == set()
