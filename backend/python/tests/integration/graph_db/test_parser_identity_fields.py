"""The parser identity on real ArangoDB and Neo4j: the fields, the adoption write and the QUEUED-duplicate promotion.

* the four fields are accepted by the strict record schema, read back, and read as absent or null on a record that never had them;
* the adoption is one conditional write (it applies once, only while the revision, the timestamp and the copy are the ones read);
* the promotion of QUEUED duplicates by a finished record (``update_queued_duplicates_status``) takes the identity with the copy: a duplicate
  read by the same parser is promoted with it and the promotion's own timestamp, one read by another parser stays QUEUED, and the extension
  the router needs is read from the duplicate's ``files`` document (the ``records`` node has none).

PostgreSQL has the same cases in ``postgres/test_parser_identity_pg.py``. Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""
from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.models.entities import FileRecord, RecordType
from app.modules.pipeline import parser_identity as pi
from app.services.graph_db.conditions import EQ, IS_NULL, Cond
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
FILES = CollectionNames.FILES.value
ORG = "org-parser-identity-it"
logger = logging.getLogger("parser-identity-it")


@pytest.fixture(autouse=True)
def _identity_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORD_PARSER_IDENTITY_WRITE", "true")


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[IGraphDBProvider, str]]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            provider = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, "parser_identity_it")
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(provider, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        run = uuid.uuid4().hex[:8]
        keys = [f"{run}-{n}" for n in ("ref", "q1", "q2", "q3", "adopt")]
        yield provider, run
        with contextlib.suppress(Exception):
            await provider.delete_nodes(keys, RECORDS)
            await provider.delete_nodes(keys, FILES)


async def put(
    provider: IGraphDBProvider, key: str, *, name: str, mime: str, extension: str | None, status: str = "QUEUED", **more: Any,  # noqa: ANN401
) -> None:
    """A file record as the connectors write it: the ``records`` node, and the ``files`` document that holds the extension."""
    record = FileRecord(
        id=key, org_id=ORG, record_name=name, record_type=RecordType.FILE, external_record_id=key, version=1, origin=OriginTypes.UPLOAD,
        connector_id="conn", connector_name=Connectors.KNOWLEDGE_BASE, is_file=True, extension=extension, mime_type=mime,
    )
    assert await provider.batch_upsert_nodes(
        [{**record.to_arango_base_record(), "indexingStatus": status, "md5Checksum": "mm", **more}], RECORDS
    )
    assert await provider.batch_upsert_nodes([record.to_arango_record()], FILES)


def known(identity: str, *, vrid: str, at: int, source: str = pi.SOURCE_PARSED) -> dict[str, Any]:
    return {
        pi.IDENTITY_FIELD: identity, pi.IDENTITY_AT_FIELD: at, pi.IDENTITY_VRID_FIELD: vrid, pi.IDENTITY_SOURCE_FIELD: source,
        "lastIndexTimestamp": at,
    }


async def finished_reference(provider: IGraphDBProvider, run: str, **identity: Any) -> str:  # noqa: ANN401
    key = f"{run}-ref"
    await put(
        provider, key, name="ref.txt", mime="text/plain", extension="txt", status="COMPLETED", virtualRecordId=f"{run}-v",
        extractionStatus="COMPLETED", hasIndexedCopy=True, **{"lastIndexTimestamp": 500, **identity},
    )
    return key


async def queue_three(provider: IGraphDBProvider, run: str) -> None:
    await put(provider, f"{run}-q1", name="q1.txt", mime="text/plain", extension="txt")
    await put(provider, f"{run}-q2", name="q2.md", mime="text/markdown", extension="md")
    # the case the records node alone cannot answer: a blob store's octet-stream, whose extension is only on the files document
    await put(provider, f"{run}-q3", name="q3", mime="application/octet-stream", extension="txt")


async def test_the_fields_are_accepted_and_read_back_and_absent_ones_read_as_unknown(world) -> None:
    provider, run = world
    key = await finished_reference(provider, run)
    assert pi.known_identity(await provider.get_document(key, RECORDS)) is None  # never had them: absent

    assert await provider.batch_update_nodes([{"id": key, **known("inproc:txt@1", vrid=f"{run}-v", at=500)}], RECORDS)

    doc = await provider.get_document(key, RECORDS)
    assert pi.known_identity(doc) == "inproc:txt@1"
    assert doc[pi.IDENTITY_SOURCE_FIELD] == pi.SOURCE_PARSED
    # a later COMPLETED write that carries no identity (an older process) leaves it stale, not wrong
    assert await provider.batch_update_nodes([{"id": key, "lastIndexTimestamp": 501}], RECORDS)
    assert pi.known_identity(await provider.get_document(key, RECORDS)) is None


async def test_the_adoption_is_one_conditional_write(world) -> None:
    provider, run = world
    key = f"{run}-adopt"
    await put(provider, key, name="a.txt", mime="text/plain", extension="txt", status="COMPLETED", virtualRecordId=f"{run}-v",
              contentRev="rev1", lastIndexTimestamp=500, hasIndexedCopy=True)
    fields = pi.indexed_parser_fields("inproc:txt@1", pi.SOURCE_ADOPTED, at=500, vrid=f"{run}-v")
    conditions = [
        Cond("contentRev", EQ, "rev1"), Cond("lastIndexTimestamp", EQ, 500), Cond("virtualRecordId", EQ, f"{run}-v"),
        Cond(pi.IDENTITY_FIELD, IS_NULL),
    ]

    # refused when the record moved since the skip read it
    assert not await provider.update_node_fields_if(key, RECORDS, fields, [Cond("lastIndexTimestamp", EQ, 499), *conditions[:1]])
    assert not await provider.update_node_fields_if(key, RECORDS, fields, [Cond("contentRev", EQ, "other"), *conditions[1:]])
    assert pi.IDENTITY_FIELD not in await provider.get_document(key, RECORDS) or (await provider.get_document(key, RECORDS))[pi.IDENTITY_FIELD] is None

    assert await provider.update_node_fields_if(key, RECORDS, fields, conditions)
    doc = await provider.get_document(key, RECORDS)
    assert pi.known_identity(doc) == "inproc:txt@1" and doc[pi.IDENTITY_SOURCE_FIELD] == pi.SOURCE_ADOPTED
    # once: the identity is no longer absent
    assert not await provider.update_node_fields_if(key, RECORDS, {**fields, pi.IDENTITY_FIELD: "inproc:md@1"}, conditions)
    assert (await provider.get_document(key, RECORDS))[pi.IDENTITY_FIELD] == "inproc:txt@1"


async def test_a_finished_record_promotes_the_duplicates_of_its_parser_and_leaves_the_others_queued(world) -> None:
    provider, run = world
    ref = await finished_reference(provider, run, **known("inproc:txt@1", vrid=f"{run}-v", at=500))
    await queue_three(provider, run)

    promoted = await provider.update_queued_duplicates_status(ref, "COMPLETED", f"{run}-v")

    assert promoted == 2
    for name in ("q1", "q3"):
        doc = await provider.get_document(f"{run}-{name}", RECORDS)
        assert doc["indexingStatus"] == "COMPLETED" and doc["virtualRecordId"] == f"{run}-v"
        assert pi.known_identity(doc) == "inproc:txt@1", name
        assert doc[pi.IDENTITY_SOURCE_FIELD] == pi.SOURCE_TWIN
        assert doc[pi.IDENTITY_AT_FIELD] == doc["lastIndexTimestamp"] != 500  # the promotion's own timestamp
    waiting = await provider.get_document(f"{run}-q2", RECORDS)
    assert waiting["indexingStatus"] == "QUEUED" and waiting.get("virtualRecordId") != f"{run}-v"
    assert waiting.get(pi.IDENTITY_FIELD) is None


async def test_an_adopted_identity_stays_adopted_through_a_promotion(world) -> None:
    provider, run = world
    ref = await finished_reference(provider, run, **known("inproc:txt@1", vrid=f"{run}-v", at=500, source=pi.SOURCE_ADOPTED))
    await put(provider, f"{run}-q1", name="q1.txt", mime="text/plain", extension="txt")

    assert await provider.update_queued_duplicates_status(ref, "COMPLETED", f"{run}-v") == 1

    assert (await provider.get_document(f"{run}-q1", RECORDS))[pi.IDENTITY_SOURCE_FIELD] == pi.SOURCE_ADOPTED


async def test_a_reference_with_no_known_identity_promotes_every_duplicate_and_writes_none(world) -> None:
    provider, run = world
    ref = await finished_reference(provider, run)
    await queue_three(provider, run)

    assert await provider.update_queued_duplicates_status(ref, "COMPLETED", f"{run}-v") == 3

    for name in ("q1", "q2", "q3"):
        assert (await provider.get_document(f"{run}-{name}", RECORDS)).get(pi.IDENTITY_FIELD) is None


async def test_with_the_flag_off_every_duplicate_is_promoted_and_no_field_is_written(world, monkeypatch: pytest.MonkeyPatch) -> None:
    provider, run = world
    monkeypatch.delenv("RECORD_PARSER_IDENTITY_WRITE")
    ref = await finished_reference(provider, run, **known("inproc:txt@1", vrid=f"{run}-v", at=500))
    await queue_three(provider, run)

    assert await provider.update_queued_duplicates_status(ref, "COMPLETED", f"{run}-v") == 3

    for name in ("q1", "q2", "q3"):
        assert (await provider.get_document(f"{run}-{name}", RECORDS)).get(pi.IDENTITY_FIELD) is None


async def test_a_failure_is_propagated_to_every_queued_duplicate_whatever_its_parser(world) -> None:
    provider, run = world
    ref = await finished_reference(provider, run, **known("inproc:txt@1", vrid=f"{run}-v", at=500))
    await queue_three(provider, run)

    assert await provider.update_queued_duplicates_status(ref, "FAILED", f"{run}-v", reason="boom") == 3
