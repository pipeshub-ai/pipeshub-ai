"""The revision pointer on a virtual record's mapping node, against a real Neo4j and a real ArangoDB.

After a record's artifact set is written, indexing moves ``currentRev`` on the
vrid's mapping node by compare-and-set (``update_node_fields_if_match``). The
first flip expects the field absent; Neo4j cannot store a null property, so
"absent" and "null" must read the same on both backends. The stored record's
own mapping write, which runs on every index, must leave the pointer alone,
and records which revision the stored record holds (``storedRev``), which the
artifact reader compares with the pointer.

Runs on Neo4j and ArangoDB (backend-matrix). Environment: NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.config.constants.neo4j import collection_to_label
from app.models.entities import Record, RecordType
from app.modules.artifacts.models import ArtifactRef, Manifest, WrittenRevision
from app.modules.transformers.blob_storage import BlobStorage
from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "revision_pointer_it"
MAPPING = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
LABEL = collection_to_label(MAPPING)
REV_1 = "1111111111111111"
REV_2 = "2222222222222222"
REV_3 = "3333333333333333"

logger = logging.getLogger("revision-pointer-it")


@pytest.fixture(params=["neo4j", "arango"])
async def graph(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[IGraphDBProvider, str]]:
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
        await provider.delete_nodes([_record(f"{run}-vr").id], CollectionNames.RECORDS.value)
        if isinstance(provider, Neo4jProvider):
            await provider.client.execute_query(
                f"MATCH (n:{LABEL}) WHERE n.id STARTS WITH $p DETACH DELETE n", parameters={"p": f"{run}-"},
            )
        else:
            await provider.http_client.execute_aql(
                f"FOR n IN {MAPPING} FILTER STARTS_WITH(n._key, @p) REMOVE n IN {MAPPING}", {"p": f"{run}-"},
            )


def _blob(provider: IGraphDBProvider) -> BlobStorage:
    return BlobStorage(logger=logger, config_service=MagicMock(), graph_provider=provider)


async def _store_mapping(
    provider: IGraphDBProvider, vrid: str, doc_id: str = "doc-record", **revision: str | None,
) -> None:
    """What every index writes for the stored record (``store_virtual_record_mapping``)."""
    assert await _blob(provider).store_virtual_record_mapping("org-it", vrid, doc_id, 42, **revision)


async def _store_record(provider: IGraphDBProvider, vrid: str, rev: str) -> None:
    """The record as indexing leaves it: the flip only moves the pointer of a record still on ``rev``."""
    assert await provider.batch_upsert_nodes(
        [{**_record(vrid).to_arango_base_record(), "contentRev": rev}],
        CollectionNames.RECORDS.value,
    )


def _sink(provider: IGraphDBProvider) -> SinkOrchestrator:
    return SinkOrchestrator(
        graphdb=AsyncMock(), blob_storage=AsyncMock(), vector_store=AsyncMock(),
        graph_provider=provider, logger=logger, config_service=MagicMock(),
    )


def _record(vrid: str) -> Record:
    return Record(
        id=f"rec-{vrid}", org_id="org-it", virtual_record_id=vrid, record_name="r",
        record_type=RecordType.FILE, external_record_id="ext", version=1, origin="UPLOAD",
        connector_id="conn", connector_name=Connectors.KNOWLEDGE_BASE,
    )


def _written(vrid: str, rev: str) -> WrittenRevision:
    return WrittenRevision(
        manifest=Manifest(virtualRecordId=vrid, rev=rev, orgId="org-it", producedAt=1, artifacts={}),
        manifest_ref=ArtifactRef(document_id=f"doc-{rev[:4]}", root=f"org-it/PipesHub/record-revisions/{vrid}/{rev}",
                                 path="manifest.json"),
    )


async def _mapping(provider: IGraphDBProvider, vrid: str) -> dict:
    doc = await provider.get_document(vrid, MAPPING)
    assert doc is not None
    return doc


@pytest.mark.asyncio
async def test_first_flip_then_next_and_the_stored_record_write_keeps_it(
    graph: tuple[IGraphDBProvider, str],
) -> None:
    provider, run = graph
    vrid = f"{run}-vr"
    sink = _sink(provider)
    await _store_mapping(provider, vrid)
    assert "currentRev" not in await _mapping(provider, vrid)

    await _store_record(provider, vrid, REV_1)
    await sink._flip_revision_pointer(_record(vrid), _written(vrid, REV_1))
    first = await _mapping(provider, vrid)
    assert first["currentRev"] == REV_1
    assert first.get("previousRev") is None
    assert first["manifestDocId"] == "doc-1111"
    assert first["record_doc_id"] == "doc-record"

    # The next index rewrites the stored record's mapping before its own flip.
    await _store_mapping(provider, vrid, "doc-record-2")
    kept = await _mapping(provider, vrid)
    assert kept["currentRev"] == REV_1 and kept["record_doc_id"] == "doc-record-2"

    await _store_record(provider, vrid, REV_2)
    await sink._flip_revision_pointer(_record(vrid), _written(vrid, REV_2))
    second = await _mapping(provider, vrid)
    assert (second["currentRev"], second["previousRev"], second["manifestDocId"]) == (REV_2, REV_1, "doc-2222")


@pytest.mark.asyncio
async def test_a_stale_expectation_writes_nothing(graph: tuple[IGraphDBProvider, str]) -> None:
    provider, run = graph
    vrid = f"{run}-vr"
    await _store_mapping(provider, vrid)
    assert await provider.update_node_fields_if_match(
        vrid, MAPPING, {"currentRev": REV_1, "previousRev": None, "manifestDocId": "d1"}, {"currentRev": None},
    )

    # A writer that read the field still absent lost to the one above.
    assert not await provider.update_node_fields_if_match(
        vrid, MAPPING, {"currentRev": REV_2, "previousRev": None, "manifestDocId": "d2"}, {"currentRev": None},
    )
    assert not await provider.update_node_fields_if_match(
        vrid, MAPPING, {"currentRev": REV_3, "previousRev": REV_2, "manifestDocId": "d3"}, {"currentRev": REV_2},
    )
    doc = await _mapping(provider, vrid)
    assert (doc["currentRev"], doc["manifestDocId"]) == (REV_1, "d1")


@pytest.mark.asyncio
async def test_absent_and_null_both_count_as_no_revision(graph: tuple[IGraphDBProvider, str]) -> None:
    provider, run = graph
    vrid = f"{run}-vr"
    await _store_mapping(provider, vrid)
    # Writing null: ArangoDB keeps a null field, Neo4j removes the property.
    assert await provider.update_node_fields_if_match(
        vrid, MAPPING, {"currentRev": None}, {"currentRev": None},
    )
    assert (await _mapping(provider, vrid)).get("currentRev") is None

    assert await provider.update_node_fields_if_match(
        vrid, MAPPING, {"currentRev": REV_1, "manifestDocId": "d1"}, {"currentRev": None},
    )
    assert (await _mapping(provider, vrid))["currentRev"] == REV_1


@pytest.mark.asyncio
async def test_racing_flips_from_one_read_settle_on_one(graph: tuple[IGraphDBProvider, str]) -> None:
    provider, run = graph
    vrid = f"{run}-vr"
    await _store_mapping(provider, vrid)

    results = await asyncio.gather(*(
        provider.update_node_fields_if_match(
            vrid, MAPPING, {"currentRev": rev, "previousRev": None, "manifestDocId": f"d-{rev[:1]}"},
            {"currentRev": None},
        )
        for rev in (REV_1, REV_2, REV_3)
    ))

    assert sum(bool(r) for r in results) == 1
    winner = (REV_1, REV_2, REV_3)[results.index(True)]
    doc = await _mapping(provider, vrid)
    assert (doc["currentRev"], doc["manifestDocId"]) == (winner, f"d-{winner[:1]}")


@pytest.mark.asyncio
async def test_no_mapping_node_is_left_absent(graph: tuple[IGraphDBProvider, str]) -> None:
    provider, run = graph
    vrid = f"{run}-missing"

    await _sink(provider)._flip_revision_pointer(_record(vrid), _written(vrid, REV_1))

    assert await provider.get_document(vrid, MAPPING) is None
    assert not await provider.update_node_fields_if_match(
        vrid, MAPPING, {"currentRev": REV_1}, {"currentRev": None},
    )
    assert await provider.get_document(vrid, MAPPING) is None


@pytest.mark.asyncio
async def test_a_run_the_record_has_left_does_not_move_the_pointer(graph: tuple[IGraphDBProvider, str]) -> None:
    """A slower run of an older revision finishing after a newer one must not point back at it."""
    provider, run = graph
    vrid = f"{run}-vr"
    sink = _sink(provider)
    await _store_mapping(provider, vrid)
    await _store_record(provider, vrid, REV_2)
    await sink._flip_revision_pointer(_record(vrid), _written(vrid, REV_2))

    await sink._flip_revision_pointer(_record(vrid), _written(vrid, REV_1))

    doc = await _mapping(provider, vrid)
    assert (doc["currentRev"], doc["manifestDocId"]) == (REV_2, "doc-2222")


@pytest.mark.asyncio
async def test_the_stored_revision_lands_beside_the_pointer(graph: tuple[IGraphDBProvider, str]) -> None:
    """A stored record of a newer revision than the pointer's set reads as such, on both lookups."""
    provider, run = graph
    vrid = f"{run}-vr"
    sink = _sink(provider)
    await _store_mapping(provider, vrid, stored_rev=REV_1)
    await _store_record(provider, vrid, REV_1)
    await sink._flip_revision_pointer(_record(vrid), _written(vrid, REV_1))
    doc = await _mapping(provider, vrid)
    assert (doc["currentRev"], doc["storedRev"]) == (REV_1, REV_1)

    # The stored record moves to REV_2; its artifact set is never written.
    await _store_mapping(provider, vrid, "doc-record-2", stored_rev=REV_2)
    one = await _blob(provider).get_document_id_by_virtual_record_id(vrid)
    many = (await _blob(provider).get_document_ids_by_virtual_record_ids([vrid]))[vrid]
    for lookup in (one, many):
        assert (lookup["currentRev"], lookup["storedRev"], lookup["record_doc_id"]) == (REV_1, REV_2, "doc-record-2")

    # A write that does not name a revision leaves it; one without a revision clears it.
    await _store_mapping(provider, vrid, "doc-record-2")
    assert (await _mapping(provider, vrid))["storedRev"] == REV_2
    await _store_mapping(provider, vrid, "doc-record-2", stored_rev=None)
    cleared = await _mapping(provider, vrid)
    assert cleared.get("storedRev") is None and cleared["currentRev"] == REV_1
    assert "storedRev" not in await _blob(provider).get_document_id_by_virtual_record_id(vrid)
