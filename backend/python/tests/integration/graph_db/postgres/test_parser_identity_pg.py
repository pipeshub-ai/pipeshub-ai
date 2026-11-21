"""The parser identity on PostgreSQL, against a real database: the fields, the adoption write and the QUEUED-duplicate promotion.

The answers are those of ``Neo4jProvider`` and ``ArangoHTTPProvider`` (``integration/graph_db/test_parser_identity_fields.py``): a duplicate read by
the same parser is promoted with the identity and the promotion's own timestamp, another is left QUEUED, and the extension the router needs comes
from the duplicate's ``files`` row (the SELECT joins it). Needs a migrated database: PG_CORE_URL, PG_POOLER=none.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from app.modules.pipeline import parser_identity as pi
from app.services.graph_db.conditions import EQ, IS_NULL, Cond

from .test_identity import w as _identity_world  # noqa: F401

if TYPE_CHECKING:
    from .test_access import World

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _identity_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORD_PARSER_IDENTITY_WRITE", "true")


@pytest.fixture
def w(request: pytest.FixtureRequest) -> World:
    return request.getfixturevalue("_identity_world")


async def put(w: World, key: str, *, name: str, mime: str, extension: str | None, **doc: Any) -> None:  # noqa: ANN401
    await w.nodes("records", {
        "_key": key, "recordName": name, "recordType": "FILE", "mimeType": mime, "connectorId": "c1", "md5Checksum": "mm",
        "indexingStatus": "QUEUED", **doc,
    })
    await w.nodes("files", {"_key": key, "name": name, "isFile": True, "extension": extension})


def known(identity: str, *, vrid: str, at: int, source: str = pi.SOURCE_PARSED) -> dict[str, Any]:
    return {
        pi.IDENTITY_FIELD: identity, pi.IDENTITY_AT_FIELD: at, pi.IDENTITY_VRID_FIELD: vrid, pi.IDENTITY_SOURCE_FIELD: source,
        "lastIndexTimestamp": at,
    }


async def reference(w: World, **identity: Any) -> None:  # noqa: ANN401
    await put(
        w, "ref", name="ref.txt", mime="text/plain", extension="txt", indexingStatus="COMPLETED", virtualRecordId="v1",
        extractionStatus="COMPLETED", hasIndexedCopy=True, **{"lastIndexTimestamp": 500, **identity},
    )


async def queue_three(w: World) -> None:
    await put(w, "q1", name="q1.txt", mime="text/plain", extension="txt")
    await put(w, "q2", name="q2.md", mime="text/markdown", extension="md")
    await put(w, "q3", name="q3", mime="application/octet-stream", extension="txt")  # the extension is only on the files row


async def doc(w: World, key: str) -> dict[str, Any]:
    return await w.p.get_document(key, "records")


async def test_the_fields_read_back_and_absent_ones_read_as_unknown(w: World) -> None:
    await reference(w)
    assert pi.known_identity(await doc(w, "ref")) is None

    assert await w.p.batch_update_nodes([{"id": "ref", **known("inproc:txt@1", vrid="v1", at=500)}], "records")

    assert pi.known_identity(await doc(w, "ref")) == "inproc:txt@1"
    assert await w.p.batch_update_nodes([{"id": "ref", "lastIndexTimestamp": 501}], "records")
    assert pi.known_identity(await doc(w, "ref")) is None


async def test_the_adoption_is_one_conditional_write(w: World) -> None:
    await put(w, "a", name="a.txt", mime="text/plain", extension="txt", indexingStatus="COMPLETED", virtualRecordId="v1", contentRev="rev1", lastIndexTimestamp=500)
    fields = pi.indexed_parser_fields("inproc:txt@1", pi.SOURCE_ADOPTED, at=500, vrid="v1")
    conditions = [Cond("contentRev", EQ, "rev1"), Cond("lastIndexTimestamp", EQ, 500), Cond("virtualRecordId", EQ, "v1"), Cond(pi.IDENTITY_FIELD, IS_NULL)]

    assert not await w.p.update_node_fields_if("a", "records", fields, [Cond("lastIndexTimestamp", EQ, 499), *conditions[:1]])
    assert not await w.p.update_node_fields_if("a", "records", fields, [Cond("contentRev", EQ, "other"), *conditions[1:]])
    assert await w.p.update_node_fields_if("a", "records", fields, conditions)
    assert pi.known_identity(await doc(w, "a")) == "inproc:txt@1"
    assert not await w.p.update_node_fields_if("a", "records", {**fields, pi.IDENTITY_FIELD: "inproc:md@1"}, conditions)


async def test_a_finished_record_promotes_the_duplicates_of_its_parser_and_leaves_the_others_queued(w: World) -> None:
    await reference(w, **known("inproc:txt@1", vrid="v1", at=500))
    await queue_three(w)

    assert await w.p.update_queued_duplicates_status("ref", "COMPLETED", "v1") == 2

    for key in ("q1", "q3"):
        d = await doc(w, key)
        assert d["indexingStatus"] == "COMPLETED" and d["virtualRecordId"] == "v1"
        assert pi.known_identity(d) == "inproc:txt@1", key
        assert d[pi.IDENTITY_SOURCE_FIELD] == pi.SOURCE_TWIN
        assert d[pi.IDENTITY_AT_FIELD] == d["lastIndexTimestamp"] != 500
    waiting = await doc(w, "q2")
    assert waiting["indexingStatus"] == "QUEUED" and waiting.get(pi.IDENTITY_FIELD) is None


async def test_an_adopted_identity_stays_adopted_through_a_promotion(w: World) -> None:
    await reference(w, **known("inproc:txt@1", vrid="v1", at=500, source=pi.SOURCE_ADOPTED))
    await put(w, "q1", name="q1.txt", mime="text/plain", extension="txt")
    assert await w.p.update_queued_duplicates_status("ref", "COMPLETED", "v1") == 1
    assert (await doc(w, "q1"))[pi.IDENTITY_SOURCE_FIELD] == pi.SOURCE_ADOPTED


async def test_a_reference_with_no_known_identity_promotes_every_duplicate_and_writes_none(w: World) -> None:
    await reference(w)
    await queue_three(w)
    assert await w.p.update_queued_duplicates_status("ref", "COMPLETED", "v1") == 3
    for key in ("q1", "q2", "q3"):
        assert (await doc(w, key)).get(pi.IDENTITY_FIELD) is None


async def test_with_the_flag_off_every_duplicate_is_promoted_and_no_field_is_written(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RECORD_PARSER_IDENTITY_WRITE")
    await reference(w, **known("inproc:txt@1", vrid="v1", at=500))
    await queue_three(w)
    assert await w.p.update_queued_duplicates_status("ref", "COMPLETED", "v1") == 3
    for key in ("q1", "q2", "q3"):
        assert (await doc(w, key)).get(pi.IDENTITY_FIELD) is None


async def test_a_failure_is_propagated_to_every_queued_duplicate_whatever_its_parser(w: World) -> None:
    await reference(w, **known("inproc:txt@1", vrid="v1", at=500))
    await queue_three(w)
    assert await w.p.update_queued_duplicates_status("ref", "FAILED", None, reason="boom") == 3


async def _queue_json_and_csv_readers(w: World) -> None:
    await put(w, "q1", name="q1.csv", mime="application/json", extension="csv")
    await put(w, "q2", name="q2.csv", mime="text/csv", extension="csv")


async def test_a_service_copy_promotes_only_the_duplicate_the_service_reads_as_json(w, monkeypatch) -> None:
    monkeypatch.setenv("USE_PARSING_SERVICE", "true")
    await reference(w, **known("svc:json@1", vrid="v1", at=500))
    await _queue_json_and_csv_readers(w)
    assert await w.p.update_queued_duplicates_status("ref", "COMPLETED", "v1") == 1
    assert (await doc(w, "q1"))["indexingStatus"] == "COMPLETED"
    assert (await doc(w, "q2"))["indexingStatus"] == "QUEUED"


async def test_an_in_process_copy_promotes_both_csv_readers(w, monkeypatch) -> None:
    monkeypatch.setenv("USE_PARSING_SERVICE", "false")
    await reference(w, **known("inproc:csv@1", vrid="v1", at=500))
    await _queue_json_and_csv_readers(w)
    assert await w.p.update_queued_duplicates_status("ref", "COMPLETED", "v1") == 2


async def test_a_flip_to_the_other_pipeline_leaves_the_duplicate_queued(w, monkeypatch) -> None:
    monkeypatch.setenv("USE_PARSING_SERVICE", "false")
    await reference(w, **known("svc:json@1", vrid="v1", at=500))
    await _queue_json_and_csv_readers(w)
    assert await w.p.update_queued_duplicates_status("ref", "COMPLETED", "v1") == 0


async def test_a_source_that_moved_to_another_copy_leaves_every_duplicate_queued(w) -> None:
    await reference(w, **known("inproc:txt@1", vrid="v1", at=500))
    await put(w, "q1", name="q1.txt", mime="text/plain", extension="txt")
    assert await w.p.update_queued_duplicates_status("ref", "COMPLETED", "elsewhere") == 0
    assert (await doc(w, "q1"))["indexingStatus"] == "QUEUED"
    assert await w.p.update_queued_duplicates_status("ref", "COMPLETED", "v1") == 1
