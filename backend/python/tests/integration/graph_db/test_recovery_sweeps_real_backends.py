"""The recovery sweeps that run per organisation, on real Neo4j and ArangoDB.

Two organisations hold the same ids on the same connector. Each sweep is run for one of them and must change and read
only that organisation's rows; the reconcile read must tell each row's organisation, so its follow-up can run under it.

Runs in backend-matrix on both graph jobs. Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.core.org_context import for_org, system
from app.services.graph_db.interface.graph_db_provider import (
    DUPLICATE_RECONCILE_DUE_AT_FIELD,
    DUPLICATE_RECONCILE_PENDING_FIELD,
)
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "recovery_sweeps_it"
RECORDS = CollectionNames.RECORDS.value
APPS = CollectionNames.APPS.value
logger = logging.getLogger("recovery-sweeps-it")


@pytest.fixture(params=["neo4j", "arango"])
async def graph(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[IGraphDBProvider]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            provider = await (connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, ARANGO_DB))
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(provider, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        yield provider


async def _two_orgs(graph: IGraphDBProvider) -> tuple[list[str], list[str], list[str]]:
    """For each of two organisations an inactive connector and one queued record on it (both stores key a node by its id alone)."""
    run = uuid.uuid4().hex[:10]
    orgs = [f"org-rs-{run}-{i}" for i in (1, 2)]
    connectors = [f"conn-rs-{run}-{i}" for i in (1, 2)]
    records = [f"rec-rs-{run}-{i}" for i in (1, 2)]
    for org, connector, record in zip(orgs, connectors, records, strict=True):
        await graph.batch_upsert_nodes([{"_key": connector, "orgId": org, "name": "c", "type": "SLACK", "appGroup": "Slack", "scope": "team", "isActive": False, "createdAtTimestamp": 1}], APPS)
        await graph.batch_upsert_nodes([{
            "_key": record, "id": record, "orgId": org, "recordName": record, "connectorId": connector, "origin": "CONNECTOR",
            "indexingStatus": ProgressStatus.QUEUED.value, "recordType": "FILE", "externalRecordId": record,
            "version": 1, "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
        }], RECORDS)
    return orgs, connectors, records


async def test_the_inactive_connector_sweep_changes_only_the_organisation_it_runs_for(graph: IGraphDBProvider) -> None:
    from app.indexing_main import _sweep_queued_records_for_inactive_connectors

    orgs, _, records = await _two_orgs(graph)
    with for_org(orgs[0], job="test.queued_sweep"):
        await _sweep_queued_records_for_inactive_connectors(graph_provider=graph, logger=logger, page_size=500, org_id=orgs[0])
    assert (await graph.get_document(records[0], RECORDS))["indexingStatus"] == ProgressStatus.AUTO_INDEX_OFF.value
    assert (await graph.get_document(records[1], RECORDS))["indexingStatus"] == ProgressStatus.QUEUED.value


async def test_the_reconcile_read_tells_each_rows_organisation(graph: IGraphDBProvider) -> None:
    run = uuid.uuid4().hex[:10]
    orgs = [f"org-rc-{run}-{i}" for i in (1, 2)]
    keys = [f"rec-rc-{run}-{i}" for i in (1, 2)]
    for org, key in zip(orgs, keys, strict=True):
        await graph.batch_upsert_nodes([{
            "_key": key, "id": key, "orgId": org, "recordName": key, "connectorId": "c", "origin": "CONNECTOR", "recordType": "FILE",
            "externalRecordId": key, "version": 1, "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
            DUPLICATE_RECONCILE_PENDING_FIELD: True, DUPLICATE_RECONCILE_DUE_AT_FIELD: 5,
        }], RECORDS)
    with system("test.pending_reconciles"):
        rows = {r["_key"]: r for r in await graph.get_records_pending_duplicate_reconcile(due_before_ms=10, limit=100000)}
    assert [rows[k]["orgId"] for k in keys] == orgs


async def test_the_orphan_sweep_filters_the_mapping_scan_on_its_organisation(graph: IGraphDBProvider) -> None:
    import app.indexing_main as m

    run = uuid.uuid4().hex[:10]
    orgs = [f"org-om-{run}-{i}" for i in (1, 2)]
    mapping = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
    for org in orgs:
        await graph.batch_upsert_nodes([{"_key": f"v-{run}-{org}", "id": f"v-{run}-{org}", "orgId": org, "virtualRecordId": f"v-{run}-{org}"}], mapping)
    seen: list[str] = []
    original = graph.get_documents_paginated

    async def spy(collection: str, *a: object, **k: object) -> list[dict]:
        rows = await original(collection, *a, **k)
        seen.extend(str(r.get("orgId")) for r in rows)
        return rows

    graph.get_documents_paginated = spy  # type: ignore[method-assign]
    pipeline = MagicMock()
    pipeline.rewrite_or_delete_vector_membership = AsyncMock(return_value="kept")
    m._orphan_sweep_cursor = {}
    try:
        with for_org(orgs[0], job="test.orphan_sweep"):
            await m._sweep_orphaned_virtual_record_mappings(graph_provider=graph, pipeline=pipeline, logger=logger, page_size=500, org_id=orgs[0])
    finally:
        graph.get_documents_paginated = original  # type: ignore[method-assign]
        m._orphan_sweep_cursor = {}
    assert seen and set(seen) == {orgs[0]}


async def test_the_lane_upkeep_reads_only_the_connectors_of_each_organisation(graph: IGraphDBProvider) -> None:
    from app.modules.indexing import lane_upkeep

    orgs, connectors, _ = await _two_orgs(graph)
    found = await lane_upkeep._read_apps(graph, logger, [orgs[0]])
    assert found is not None and connectors[0] in found and connectors[1] not in found
