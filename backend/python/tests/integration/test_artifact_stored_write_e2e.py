"""Stored-record writes on real backends: copy-on-write while shadow or read is on, a
compare-and-set-only in-place path while both are off, and what a read serves through both.

Each test drives the real ``SinkOrchestrator.index`` with the real ``BlobStorage`` against the
storage service's real routes (MongoDB plus local disk, S3 or Azure Blob) and a real graph
(Neo4j and ArangoDB). A test that shows an invariant holds is paired with a ``*_control`` test that
breaks the mechanism and shows the invariant then fails. Environment and fixtures:
``tests/integration/stored_write_world.py``. Skipped without ``ARTIFACTS_IT_STORAGE_URL``; the graph
stores are ``NEO4J_IT_URI`` and ``ARANGO_IT_URL`` (``tests/integration/real_graph.py``).
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any
from unittest.mock import AsyncMock

import aiohttp
import pytest

from app.config.constants.arangodb import ProgressStatus
from app.models.blocks import SemanticMetadata
from app.modules.artifacts import record_backend as backend_module
from app.modules.artifacts.stored_retention import (
    StoredRecordRetention,
    StoredRetentionReport,
)
from app.modules.transformers import blob_storage as blob_module
from app.modules.transformers import stored_write as stored_write_module
from app.modules.transformers.blob_storage import (
    DocumentRetiredError,
    StorageDocumentNotFoundError,
)
from app.modules.transformers.stored_write import is_none_run
from app.modules.transformers.transformer import TransformContext
from app.services.messaging.membership import MembershipVerdict
from tests.integration import stored_write_world
from tests.integration.stored_write_world import (
    MAPPING,
    RECORDS,
    A,
    B,
    C,
    World,
    _rev,
    logger,
)

STORAGE_URL = os.environ.get("ARTIFACTS_IT_STORAGE_URL", "")
STORAGE_TYPE = os.environ.get("ARTIFACTS_IT_STORAGE_TYPE", "local")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.timeout(600),
    pytest.mark.skipif(not STORAGE_URL, reason="set ARTIFACTS_IT_STORAGE_URL to a running storage service"),
]

world = stored_write_world.world

BUFFER = re.compile(r"/internal/[0-9a-f]{24}/buffer$")
RETIRE = re.compile(r"/internal/[0-9a-f]{24}/retire$")


def _is_switch(fields: dict[str, Any]) -> bool:
    """A mapping write that names a stored document (not an invalidation)."""
    return bool(fields.get("documentId"))


def _tags(row: dict[str, Any]) -> dict[str, Any]:
    return {e["key"]: e["value"] for e in row.get("customMetadata") or [] if isinstance(e, dict)}


async def _served_matches_stored(world: World, vrid: str) -> None:
    stored = await world.stored(vrid)
    world.reads.clear()
    served = await world.read(vrid)
    assert served == stored, f"served {served} ({world.reads}) while the stored record holds {stored}"


# ---------------------------------------------------------------------------
# Copy-on-write: concurrency and crashes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("blind", [False, True], ids=["cas", "blind_upsert_control"])
async def test_cow_switch_cas_interleaved(world: World, blind: bool) -> None:
    """T2. Two records on one vrid create their documents, then both reach the switch.
    The compare-and-set orders them: one switch is refused, re-reads and switches again; every
    switch bumps ``switchSeq``; served == stored. With the switch as a blind upsert the bumps are lost."""
    vrid = world.vrid()
    x, y = world.record(vrid, A, name="x"), world.record(vrid, B, name="y")
    await world.index(x, _rev(A))
    await world.put_record_node(y, _rev(B))
    first = await world.mapping(vrid)
    assert first["switchSeq"] == 1

    arrived = 0
    both_created = asyncio.Event()
    real_switch = world.blob._switch_mapping

    async def switch(org_id: str, vrid_: str, node: Any, fields: dict[str, Any], **kw: Any) -> bool:  # noqa: ANN401
        nonlocal arrived
        if _is_switch(fields):
            arrived += 1
            if arrived <= 2:
                if arrived == 2:
                    both_created.set()
                await both_created.wait()
            if blind:
                await world.provider.batch_upsert_nodes([{"id": vrid_, **fields}], MAPPING)
                return True
        return await real_switch(org_id, vrid_, node, fields, **kw)

    world.blob._switch_mapping = switch  # type: ignore[method-assign]
    outcomes = await asyncio.gather(
        world.sink.index(TransformContext(record=x.model_copy(deep=True), settings={})),
        world.sink.index(TransformContext(record=y.model_copy(deep=True), settings={})),
        return_exceptions=True,
    )

    mapping = await world.mapping(vrid)
    if blind:
        # Two switches from the same read: the engine refuses one (a write-write conflict) or one bump
        # and the other switch's retirement are lost.
        assert any(isinstance(o, BaseException) for o in outcomes) or mapping["switchSeq"] != 3
        return
    assert not [o for o in outcomes if isinstance(o, BaseException)], outcomes
    assert mapping["switchSeq"] == 3, mapping
    mapped = mapping["documentId"]
    assert mapped != first["documentId"]
    assert all(entry["doc"] != mapped for entry in mapping["retired"])
    assert (await world.row(mapped))["retiredAt"] is None
    assert (await world.row(first["documentId"]))["retiredAt"]
    await _served_matches_stored(world, vrid)


@pytest.mark.asyncio
async def test_cow_crash_before_switch(world: World) -> None:
    """T3. A crash after the create and before the switch leaves the old document mapped and
    its set served; the new document is a stray with a numeric ``createdAt`` for the stray pass."""
    vrid = world.vrid()
    await world.index(world.record(vrid, A), _rev(A))
    before = await world.mapping(vrid)

    async def crash(*_a: Any, **_k: Any) -> bool:  # noqa: ANN401
        raise RuntimeError("crash before the switch")

    world.blob._switch_mapping = crash  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="crash before the switch"):
        await world.index(world.record(vrid, B), _rev(B))

    after = await world.mapping(vrid)
    for key in ("documentId", "switchSeq", "storedRun", "currentRun", "currentRev"):
        assert after[key] == before[key], key
    world.reads.clear()
    assert await world.read(vrid) == A
    assert world.reads == ["served"]
    documents = await world.documents(vrid)
    strays = [d for d in documents if d["_id"] != before["documentId"]]
    assert len(strays) == 1
    assert isinstance(strays[0]["createdAt"], (int, float)), strays[0]


@pytest.mark.asyncio
async def test_cow_crash_after_switch(world: World) -> None:
    """T4. A crash after the switch and before the flip: the set is stale, the stored record
    already holds the new content, the record is still IN_PROGRESS, and a republish heals it."""
    vrid = world.vrid()
    await world.index(world.record(vrid, A), _rev(A))
    rec = world.record(vrid, B)

    async def crash(*_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        raise RuntimeError("crash after the switch")

    real = world.sink._write_artifacts
    world.sink._write_artifacts = crash  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="crash after the switch"):
        await world.index(rec, _rev(B))

    mapping = await world.mapping(vrid)
    assert mapping["storedRun"] != mapping["currentRun"]
    assert await world.stored(vrid) == B
    world.reads.clear()
    assert await world.read(vrid) == B
    assert world.reads == ["stale"]
    node = await world.provider.get_document(rec.id, RECORDS)
    assert node["indexingStatus"] == ProgressStatus.IN_PROGRESS.value

    world.sink._write_artifacts = real  # type: ignore[method-assign]
    await world.index(rec, _rev(B))
    world.reads.clear()
    assert await world.read(vrid) == B
    assert world.reads == ["served"]


@pytest.mark.asyncio
@pytest.mark.skipif(STORAGE_TYPE == "local", reason="not applicable on local: a placeholder row exists only on S3 and Azure Blob")
async def test_cloud_placeholder_failure(world: World) -> None:
    """T15. The upload of a new document is refused after its placeholder row exists: nothing
    changes on the mapping, the old set is served, and the row is a stray with a ``createdAt``."""
    vrid = world.vrid()
    await world.index(world.record(vrid, A), _rev(A))
    before = await world.mapping(vrid)

    world.proxy.refuse.append(re.compile(r"/internal/[0-9a-f]{24}/directUpload$"))
    with pytest.raises(Exception):  # noqa: B017  (the storage error is what the sink raises)
        await world.index(world.record(vrid, B), _rev(B))
    world.proxy.refuse.clear()

    after = await world.mapping(vrid)
    assert (after["documentId"], after["switchSeq"], after["storedRun"]) == (
        before["documentId"], before["switchSeq"], before["storedRun"],
    )
    world.reads.clear()
    assert await world.read(vrid) == A
    assert world.reads == ["served"]
    strays = [d for d in await world.documents(vrid) if d["_id"] != before["documentId"]]
    assert strays
    assert all(isinstance(d["createdAt"], (int, float)) for d in strays)


@pytest.mark.asyncio
async def test_purge_races_cow_sink(world: World) -> None:
    """T16. The record is deleted and the vrid purged between a sink's create and its switch: the
    switch does not happen and the mapping is not resurrected."""
    vrid = world.vrid()
    await world.index(world.record(vrid, A), _rev(A))
    rec = world.record(vrid, B)
    real_retire = world.blob._retire_document

    async def retire_then_purge(org_id: str, document_id: str) -> None:
        await real_retire(org_id, document_id)
        await world.provider.delete_nodes([rec.id], RECORDS)
        # What the vrid purge does. (Its own mapping-node removal matches ``_key``, which
        # Neo4j nodes do not have, so the node is removed by id here.)
        await world.blob.purge_virtual_record_documents(world.org, vrid)
        await world.provider.delete_nodes([vrid], MAPPING)

    world.blob._retire_document = retire_then_purge  # type: ignore[method-assign]
    await world.index(rec, _rev(B))

    assert await world.mapping(vrid) == {}
    assert await world.documents(vrid) == []


@pytest.mark.asyncio
async def test_lineage_tags_and_record_recheck(world: World) -> None:
    """T22. A non-owner connector's write to a shared vrid takes the owner's document's path and
    tags; a record deleted mid-index does not switch."""
    vrid = world.vrid()
    owner, other = world.record(vrid, A, name="owner", connector="conn-owner"), world.record(
        vrid, B, name="other", connector="conn-other",
    )
    await world.index(owner, _rev(A))
    d1 = (await world.mapping(vrid))["documentId"]
    owner_row = await world.row(d1)
    assert _tags(owner_row)["connectorId"] == "conn-owner"

    await world.index(other, _rev(B))
    d2 = (await world.mapping(vrid))["documentId"]
    assert d2 != d1
    row = await world.row(d2)
    assert _tags(row).get("connectorId") == "conn-owner", _tags(row)
    assert row["documentPath"] == owner_row["documentPath"]

    # A record deleted after the create: no switch.
    before = await world.mapping(vrid)
    gone = world.record(vrid, C, name="gone", connector="conn-gone")
    real_retire = world.blob._retire_document

    async def retire_and_delete_record(org_id: str, document_id: str) -> None:
        await real_retire(org_id, document_id)
        await world.provider.delete_nodes([gone.id], RECORDS)

    world.blob._retire_document = retire_and_delete_record  # type: ignore[method-assign]
    await world.index(gone, _rev(C))
    after = await world.mapping(vrid)
    assert (after["documentId"], after["switchSeq"]) == (before["documentId"], before["switchSeq"])


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["artifact", "legacy"], ids=["cow", "in_place"])
async def test_keyed_node_without_document_id(world: World, mode: str) -> None:
    """T23. Reconciliation wrote the mapping node first, so it has no ``documentId``: the first
    stored write switches it by compare-and-set on that absence."""
    vrid = world.vrid()
    rec = world.record(vrid, A)
    await world.put_record_node(rec, _rev(A))
    await world.blob.save_reconciliation_metadata(world.org, rec.id, vrid, {"k": "v"})
    node = await world.mapping(vrid)
    assert not node.get("documentId")
    metadata_doc = node["record_metadata_doc_id"]

    world.read_mode = mode
    await world.sink.index(TransformContext(record=rec.model_copy(deep=True), settings={}))

    node = await world.mapping(vrid)
    assert node["documentId"] and node["switchSeq"] == 1
    assert node["record_metadata_doc_id"] == metadata_doc
    assert await world.stored(vrid) == A


# ---------------------------------------------------------------------------
# The in-place path (both read flags off)
# ---------------------------------------------------------------------------


async def _stale_servable_set(world: World, vrid: str) -> dict[str, Any]:
    """A vrid whose mapping carries a servable copy-on-write run (read on), then both flags off."""
    await world.index(world.record(vrid, A), _rev(A))
    mapping = await world.mapping(vrid)
    assert not is_none_run(mapping["storedRun"]) and mapping["currentRun"] == mapping["storedRun"]
    world.read_mode = "legacy"
    return mapping


@pytest.mark.asyncio
@pytest.mark.parametrize("invalidate", [True, False], ids=["invalidates", "no_invalidation_control"])
async def test_in_place_invalidates_before_upload(
    world: World, invalidate: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T41. An in-place write invalidates the stored run, then uploads, then runs its final
    compare-and-set. A read during it is stale, and afterwards an earlier set is never served over
    the in-place content. Without the invalidation the old set is served over the new content."""
    vrid = world.vrid()
    first = await _stale_servable_set(world, vrid)
    real_switch = world.blob._switch_mapping
    if not invalidate:
        state = {"seq": first["switchSeq"]}

        async def skip_invalidation(org_id: str, vrid_: str, node: Any, fields: dict[str, Any], **kw: Any) -> bool:  # noqa: ANN401
            if not _is_switch(fields) and "storedRun" in fields:
                kw_seq = first["switchSeq"]
                state["seq"] = kw_seq
                return True
            if _is_switch(fields):
                kw["expect_seq"] = state["seq"]
                fields = {**fields, "switchSeq": state["seq"] + 1}
            return await real_switch(org_id, vrid_, node, fields, **kw)

        world.blob._switch_mapping = skip_invalidation  # type: ignore[method-assign]

    during: dict[str, Any] = {}
    real_put = world.blob.update_record_buffer

    async def put(*a: Any, **kw: Any) -> Any:  # noqa: ANN401
        result = await real_put(*a, **kw)
        # The content is in the stored document; the final compare-and-set has not run yet.
        world.read_mode = "artifact"
        during["mapping"] = await world.mapping(vrid)
        during["stored"] = await world.stored(vrid)
        world.reads.clear()
        during["read"] = await world.read(vrid)
        during["reads"] = list(world.reads)
        world.read_mode = "legacy"
        return result

    world.blob.update_record_buffer = put  # type: ignore[method-assign]
    await world.index(world.record(vrid, B), _rev(B))

    assert during["stored"] == B
    if invalidate:
        assert is_none_run(during["mapping"]["storedRun"])
        assert during["reads"] == ["stale"]
        assert during["read"] == B
        # Afterwards the earlier set still is not served over the in-place content.
        world.read_mode = "artifact"
        world.reads.clear()
        assert await world.read(vrid) == B
        assert world.reads == ["stale"]
    else:
        # The old set, built for the previous content, is served over the new stored record.
        assert during["reads"] == ["served"]
        assert during["read"] == A


@pytest.mark.asyncio
@pytest.mark.parametrize("marked", [True, False], ids=["fenced", "no_mark_control"])
async def test_in_place_retired_fallback(world: World, marked: bool) -> None:
    """T42. A copy-on-write write that crashed after marking its document leaves that document
    mapped and marked. An in-place write then gets 404 DOCUMENT_RETIRED, stores its content in a new
    document with the old one's tags and switches by compare-and-set. Without the mark the
    content would be written into the old document."""
    vrid = world.vrid()
    owner = world.record(vrid, A, name="owner", connector="conn-owner")
    await world.index(owner, _rev(A))
    d1 = (await world.mapping(vrid))["documentId"]

    if marked:
        await world.blob._retire_document(world.org, d1)
        assert (await world.row(d1))["retiredAt"]
    world.read_mode = "legacy"
    writer = world.record(vrid, B, name="writer", connector="conn-writer")
    await world.index(writer, _rev(B))

    mapping = await world.mapping(vrid)
    assert await world.stored(vrid) == B
    if not marked:
        assert mapping["documentId"] == d1
        return
    n = mapping["documentId"]
    assert n != d1
    assert _tags(await world.row(n)).get("connectorId") == "conn-owner"
    assert any(entry["doc"] == d1 for entry in mapping["retired"])
    assert is_none_run(mapping["storedRun"])
    with pytest.raises(StorageDocumentNotFoundError):
        # The retired document still reads the old content.
        _, _ = await world.blob.update_record_buffer(world.org, d1, {"x": 1}, vrid)


@pytest.mark.asyncio
async def test_in_place_plain_404_uses_the_writers_own_tags(world: World) -> None:
    vrid = world.vrid()
    await world.index(world.record(vrid, A, name="owner", connector="conn-owner"), _rev(A))
    d1 = (await world.mapping(vrid))["documentId"]
    await world.blob.purge_document(world.org, d1)
    world.read_mode = "legacy"

    await world.index(world.record(vrid, B, name="writer", connector="conn-writer"), _rev(B))

    n = (await world.mapping(vrid))["documentId"]
    assert n != d1
    assert _tags(await world.row(n)).get("connectorId") == "conn-writer"
    assert await world.stored(vrid) == B


@pytest.mark.asyncio
async def test_flag_on_during_in_place_write(world: World) -> None:
    """T45. A flag turns on while an in-place write is running and a copy-on-write write switches
    the mapping: the in-place write's final compare-and-set fails, it is superseded, and served == stored."""
    vrid = world.vrid()
    await world.index(world.record(vrid, A, name="a"), _rev(A))
    world.read_mode = "legacy"
    slow = world.record(vrid, B, name="slow")
    fast = world.record(vrid, C, name="fast")
    await world.put_record_node(fast, _rev(C))

    put_started, fast_done = asyncio.Event(), asyncio.Event()
    real_put = world.blob.update_record_buffer

    async def put(*a: Any, **kw: Any) -> Any:  # noqa: ANN401
        put_started.set()
        await fast_done.wait()
        return await real_put(*a, **kw)

    world.blob.update_record_buffer = put  # type: ignore[method-assign]
    ctx = TransformContext(record=slow.model_copy(deep=True), settings={})

    async def run_fast() -> None:
        await put_started.wait()
        world.read_mode = "artifact"
        await world.sink.index(TransformContext(record=fast.model_copy(deep=True), settings={}))
        fast_done.set()

    await world.put_record_node(slow, _rev(B))
    await asyncio.gather(world.sink.index(ctx), run_fast())

    assert ctx.settings.get("run_superseded") is True
    assert await world.stored(vrid) == C
    await _served_matches_stored(world, vrid)


@pytest.mark.asyncio
@pytest.mark.parametrize("retry", [True, False], ids=["retried", "no_retry_control"])
async def test_in_place_bounded_retry(world: World, retry: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    """T56. A transient 503 from the buffer PUT is retried: one success, one final compare-and-set. Three
    503s fail the attempt with no delete and no blind upsert. With the retry removed one 503 fails the attempt."""
    vrid = world.vrid()
    await world.index(world.record(vrid, A), _rev(A))
    world.read_mode = "legacy"
    monkeypatch.setattr(blob_module, "IN_PLACE_PUT_BACKOFF_SECONDS", (0.0, 0.0))
    if not retry:
        monkeypatch.setattr(blob_module, "IN_PLACE_PUT_ATTEMPTS", 1)

    world.proxy.fail_next.append(["PUT", BUFFER, 503, 1])
    puts_before = len([s for s in world.proxy.seen if s.startswith("PUT") and "/buffer" in s])
    if retry:
        await world.index(world.record(vrid, B), _rev(B))
        puts = len([s for s in world.proxy.seen if s.startswith("PUT") and "/buffer" in s]) - puts_before
        assert puts == 2
        assert await world.stored(vrid) == B
    else:
        with pytest.raises(Exception):  # noqa: B017
            await world.index(world.record(vrid, B), _rev(B))
        return

    # Three 503s: the attempt fails; nothing is deleted and the mapping is not upserted.
    mapping = await world.mapping(vrid)
    upserts = []
    real_upsert = world.provider.batch_upsert_nodes

    async def watching_upsert(nodes: list[dict], collection: str, *a: Any, **kw: Any) -> Any:  # noqa: ANN401
        if collection == MAPPING:
            upserts.append(nodes)
        return await real_upsert(nodes, collection, *a, **kw)

    world.provider.batch_upsert_nodes = watching_upsert  # type: ignore[method-assign]
    world.proxy.fail_next.clear()
    world.proxy.fail_next.append(["PUT", BUFFER, 503, 3])
    world.proxy.seen.clear()
    with pytest.raises(Exception):  # noqa: B017
        await world.index(world.record(vrid, C), _rev(C))
    assert not [s for s in world.proxy.seen if s.startswith("DELETE")]
    assert upserts == []
    assert (await world.mapping(vrid))["documentId"] == mapping["documentId"]
    assert len([s for s in world.proxy.seen if s.startswith("PUT") and "/buffer" in s]) == 3


# ---------------------------------------------------------------------------
# Reconciliation, the flip and the settings read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flip_writes_no_updated_at(world: World) -> None:
    """T39″ (flip). The sink's mapping write and the flip leave ``updatedAt == switchUpdatedAt``."""
    vrid = world.vrid()
    await world.index(world.record(vrid, A), _rev(A))
    mapping = await world.mapping(vrid)
    assert mapping["currentRun"] == mapping["storedRun"]
    assert mapping["updatedAt"] == mapping["switchUpdatedAt"]
    assert mapping["documentId"] == mapping["switchDoc"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["artifact", "legacy"], ids=["cow", "in_place"])
async def test_reconciliation_adopts_by_cas(world: World, mode: str) -> None:
    """T39″. A reconciliation write leaves the mapping consistent with its last switch (no
    fingerprint) and does not bump ``switchSeq``; a switch landing meanwhile makes it retry; two
    conflicts fall back to the plain upsert."""
    vrid = world.vrid()
    world.read_mode = mode
    rec = world.record(vrid, A)
    await world.index(rec, _rev(A))
    before = await world.mapping(vrid)

    await world.blob.save_reconciliation_metadata(world.org, rec.id, vrid, {"k": 1})
    after = await world.mapping(vrid)
    assert after["record_metadata_doc_id"]
    assert after["switchSeq"] == before["switchSeq"]
    assert after["documentId"] == after["switchDoc"] == before["documentId"]
    assert after["updatedAt"] == after["switchUpdatedAt"]

    # A conflicting switch between the read and the CAS: retried.
    real = world.provider.update_node_fields_if_match
    calls = {"n": 0}

    async def conflicting(key: str, collection: str, updates: dict, expected: dict, *a: Any, **kw: Any) -> bool:  # noqa: ANN401
        if collection == MAPPING and "record_metadata_doc_id" in updates:
            calls["n"] += 1
            if calls["n"] == 1:
                return False
        return await real(key, collection, updates, expected, *a, **kw)

    world.provider.update_node_fields_if_match = conflicting  # type: ignore[method-assign]
    await world.blob.save_reconciliation_metadata(world.org, rec.id, vrid, {"k": 2})
    assert calls["n"] == 2
    final = await world.mapping(vrid)
    assert final["updatedAt"] == final["switchUpdatedAt"]

    # Two conflicts: today's upsert, without adoption.
    calls["n"] = -10

    async def always_conflicting(key: str, collection: str, updates: dict, expected: dict, *a: Any, **kw: Any) -> bool:  # noqa: ANN401
        if collection == MAPPING and "record_metadata_doc_id" in updates:
            return False
        return await real(key, collection, updates, expected, *a, **kw)

    world.provider.update_node_fields_if_match = always_conflicting  # type: ignore[method-assign]
    await world.blob.save_reconciliation_metadata(world.org, rec.id, vrid, {"k": 3})
    last = await world.mapping(vrid)
    assert last["record_metadata_doc_id"] == final["record_metadata_doc_id"]
    assert last["documentId"] == final["documentId"]


# ---------------------------------------------------------------------------
# The retire mark, through the real storage router
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retire_mark_refuses_buffer(world: World) -> None:
    """T59. ``POST /internal/:id/retire`` sets ``retiredAt``; a buffer PUT is then 404
    ``DOCUMENT_RETIRED``; reads and downloads still work; the mark is idempotent and org-scoped."""
    vrid = world.vrid()
    await world.index(world.record(vrid, A), _rev(A))
    d = (await world.mapping(vrid))["documentId"]
    assert (await world.row(d))["retiredAt"] is None

    await world.blob._retire_document(world.org, d)
    first = (await world.row(d))["retiredAt"]
    assert isinstance(first, (int, float))
    await world.blob._retire_document(world.org, d)
    assert (await world.row(d))["retiredAt"] == first

    with pytest.raises(DocumentRetiredError):
        await world.blob.update_record_buffer(world.org, d, {"x": 1}, vrid)
    assert await world.stored(vrid) == A
    assert (await world.row(d)) is not None

    # Another organisation cannot mark it.
    other = world.record(world.vrid("other"), A, org=world.other_org)
    del other
    headers, endpoint, _ = await world.blob._get_auth_and_config(world.other_org)
    async with aiohttp.ClientSession() as session, session.post(
        f"{endpoint}/api/v1/document/internal/{d}/retire", headers=headers,
    ) as resp:
        assert resp.status == 404


@pytest.mark.asyncio
async def test_retire_route_missing_is_remembered_and_the_switch_goes_on(
    world: World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An older storage service has no retire route: the switch still happens and the write is counted unfenced."""
    vrid = world.vrid()
    await world.index(world.record(vrid, A), _rev(A))
    blob_module._RETIRE_ROUTE["down_until"] = 0.0
    world.proxy.refuse.append(RETIRE)
    seen: list[str] = []
    monkeypatch.setattr(blob_module, "record_retire_mark", seen.append)

    # A 500 from the route is a failed mark; the switch goes on.
    await world.index(world.record(vrid, B), _rev(B))
    world.proxy.refuse.clear()
    assert seen == ["failed"]
    assert await world.stored(vrid) == B
    mapping = await world.mapping(vrid)
    assert mapping["switchSeq"] == 2


# ---------------------------------------------------------------------------
# The reader's run rule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("rule", [True, False], ids=["run_rule", "reader_run_check_off_control"])
async def test_a_re_parse_of_the_same_bytes_whose_set_is_not_written_is_never_served_stale(
    world: World, rule: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T1. The same source bytes parse differently (same revision) and the set is not written: the
    stored record holds the new parse. The run check makes the old set stale; with it off, the old set is served."""
    vrid = world.vrid()
    rev = _rev(A)
    await world.index(world.record(vrid, A), rev)
    if not rule:
        monkeypatch.setattr(backend_module, "check_stored_write", lambda *_a, **_k: None)
    reparsed = ["alpha one", "alpha two, now with the table text", "alpha three"]
    world.write_on = False

    await world.index(world.record(vrid, reparsed), rev)

    stored = await world.stored(vrid)
    assert stored == reparsed
    world.reads.clear()
    served = await world.read(vrid)
    if rule:
        assert served == reparsed and world.reads == ["stale"]
    else:
        assert served == A and world.reads == ["served"]


# ---------------------------------------------------------------------------
# Retirement sweep, stray pass and membership, on real backends
# ---------------------------------------------------------------------------


def _retention(world: World, verdict: Any = None, **kw: Any) -> Any:  # noqa: ANN401
    from app.modules.artifacts.stored_retention import StoredRecordRetention
    from app.services.messaging.membership import MembershipVerdict

    async def membership() -> MembershipVerdict:
        return verdict or MembershipVerdict(True)

    return StoredRecordRetention(
        world.provider, world.blob, stored_write_world.logger, membership=membership,
        grace_ms=kw.pop("grace_ms", 1), stray_grace_seconds=kw.pop("stray_grace_seconds", 0), **kw,
    )


async def _sweep_all(world: World, sweep: Any) -> Any:  # noqa: ANN401
    from app.modules.artifacts.stored_retention import StoredRetentionReport

    total = StoredRetentionReport()
    for run in (sweep.run_page, sweep.run_strays):
        key = None
        for _ in range(200):
            key, report = await run(key)
            total.add(report)
            if key is None:
                break
        else:
            raise AssertionError(f"{run.__name__} never reached the end")
    return total


@pytest.mark.asyncio
async def test_the_sweep_marks_then_purges_the_replaced_document_and_never_the_mapped_one(world: World) -> None:
    """T47 on real backends. A switch whose mark failed leaves an unmarked entry; the sweep marks it and does not
    purge on that visit, then purges it once the mark is old, keeps the mapped document, and the mapping reads
    the same afterwards."""
    vrid = world.vrid()
    await world.index(world.record(vrid, A), _rev(A))
    d1 = (await world.mapping(vrid))["documentId"]
    real = world.blob._retire_document

    async def failing(org_id: str, document_id: str) -> str:
        return "failed"

    world.blob._retire_document = failing  # type: ignore[method-assign]
    await world.index(world.record(vrid, B), _rev(B))
    world.blob._retire_document = real  # type: ignore[method-assign]
    mapping = await world.mapping(vrid)
    d2 = mapping["documentId"]
    assert [e["doc"] for e in mapping["retired"]] == [d1]
    assert (await world.row(d1))["retiredAt"] is None

    sweep = _retention(world)
    first = await _sweep_all(world, sweep)
    assert first.marked >= 1
    assert await world.row(d1) is not None and (await world.row(d1))["retiredAt"]
    await asyncio.sleep(0.05)
    second = await _sweep_all(world, sweep)

    assert second.purged >= 1
    assert await world.row(d1) is None
    after = await world.mapping(vrid)
    assert after["documentId"] == d2 and after["retired"] == [] and after["switchSeq"] == mapping["switchSeq"]
    await _served_matches_stored(world, vrid)


@pytest.mark.asyncio
async def test_the_stray_pass_collects_a_losers_document_and_nothing_a_mapping_names(world: World) -> None:
    vrid = world.vrid()
    await world.index(world.record(vrid, A), _rev(A))

    async def crash(*_a: Any, **_k: Any) -> bool:  # noqa: ANN401
        raise RuntimeError("crash before the switch")

    real = world.blob._switch_mapping
    world.blob._switch_mapping = crash  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await world.index(world.record(vrid, B), _rev(B))
    world.blob._switch_mapping = real  # type: ignore[method-assign]
    mapped = (await world.mapping(vrid))["documentId"]
    assert len(await world.documents(vrid)) == 2
    await asyncio.sleep(0.05)

    await world.provider.update_node_fields_if_match(
        vrid, MAPPING, {"lastSwitchAt": 1}, {"documentId": mapped},
    )
    report = await _sweep_all(world, _retention(world))

    assert report.strays_purged >= 1
    assert [d["_id"] for d in await world.documents(vrid)] == [mapped]
    assert await world.stored(vrid) == A


@pytest.mark.asyncio
async def test_with_both_flags_off_a_full_cycle_leaves_no_retired_entries_and_no_strays(world: World) -> None:
    """T51."""
    world.read_mode = "legacy"
    vrid = world.vrid()
    rec = world.record(vrid, A)
    await world.index(rec, _rev(A))
    await world.classify(rec, _rev(A), SemanticMetadata(summary="s", categories=["x"]))
    await world.index(world.record(vrid, B), _rev(B))
    await world.blob.save_reconciliation_metadata(world.org, rec.id, vrid, {"k": 1})

    mapping = await world.mapping(vrid)
    assert mapping["retired"] == []
    docs = [d["_id"] for d in await world.documents(vrid)]
    assert docs == [mapping["documentId"]]
    report = await _sweep_all(world, _retention(world))
    assert report.purged == 0 and report.strays_purged == 0
    assert await world.stored(vrid) == B


@pytest.mark.asyncio
@pytest.mark.parametrize("ignore", [False, True], ids=["membership_hold", "reader_ignores_membership_control"])
async def test_readers_fall_back_while_an_older_member_is_live(
    world: World, ignore: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T53. Read on, the membership entries list an untagged member (an older pod writing in place): the
    reader serves the stored record. A fresh tagged-only entry serves the set. The control reader that ignores
    the entries serves the set over an in-place write by that pod."""
    import time

    from app.services.messaging import membership as m

    entries: list[dict[str, Any]] = []
    monkeypatch.setattr(m, "read_entries_from_config", AsyncMock(side_effect=lambda cs: list(entries)))
    m._READERS.clear()
    vrid = world.vrid()
    await world.index(world.record(vrid, A), _rev(A))
    now = int(time.time() * 1000)

    entries[:] = [{"checkedAt": now, "untagged": []}]
    m._READERS.clear()
    world.reads.clear()
    assert await world.read(vrid) == A and world.reads == ["served"]

    # An older pod now writes the stored document in place, without invalidating the run.
    d = (await world.mapping(vrid))["documentId"]
    stored = await world.blob.get_record_from_storage(vrid, world.org)
    stored["block_containers"]["blocks"][0]["data"] = "written by an older pod"
    await world.blob.update_record_buffer(world.org, d, stored, vrid)
    entries[:] = [{"checkedAt": now, "untagged": ["indexing-old-0"]}]
    m._READERS.clear()
    if ignore:
        entries[:] = [{"checkedAt": now, "untagged": []}]
    world.reads.clear()
    served = await world.read(vrid)
    stored_now = await world.stored(vrid)
    if ignore:
        assert served != stored_now  # the set is served over the older pod's content: the R1-5 class
    else:
        assert served == stored_now and "membership_hold" in world.reads and "served" not in world.reads


@pytest.mark.asyncio
@pytest.mark.parametrize("with_seq", [True, False], ids=["seq_in_cas", "no_seq_control"])
async def test_cow_switch_is_refused_by_a_bump_that_changes_neither_document_nor_run(
    world: World, with_seq: bool,
) -> None:
    """QA-05cb-6. Between a copy-on-write sink's create and its switch another writer bumps ``switchSeq`` on
    the same document (as an in-place invalidation does). Only ``switchSeq`` in the compare-and-set can
    tell: the sink's first switch is refused, it re-reads and switches again, so both bumps count. A
    compare-and-set on the document alone applies the stale switch and loses the other writer's bump."""
    vrid = world.vrid()
    rec = world.record(vrid, A)
    await world.index(rec, _rev(A))
    first = await world.mapping(vrid)

    real_switch = world.blob._switch_mapping
    real_retire = world.blob._retire_document
    state = {"bumped": False}

    async def bump_then_retire(org_id: str, document_id: str) -> None:
        if not state["bumped"]:
            state["bumped"] = True
            node = await world.mapping(vrid)
            assert await real_switch(
                org_id, vrid, node, {"switchSeq": node["switchSeq"] + 1, "switchDoc": node["documentId"]},
            )
        await real_retire(org_id, document_id)

    async def document_only(org_id: str, vrid_: str, node: Any, fields: dict[str, Any], **kw: Any) -> bool:  # noqa: ANN401
        if not _is_switch(fields):
            return await real_switch(org_id, vrid_, node, fields, **kw)
        return bool(await world.provider.update_node_fields_if_match(
            vrid_, MAPPING, fields, {"documentId": node.get("documentId")},
        ))

    world.blob._retire_document = bump_then_retire  # type: ignore[method-assign]
    if not with_seq:
        world.blob._switch_mapping = document_only  # type: ignore[method-assign]
    await world.index(world.record(vrid, B), _rev(B))
    world.blob._switch_mapping = real_switch  # type: ignore[method-assign]
    world.blob._retire_document = real_retire  # type: ignore[method-assign]

    mapping = await world.mapping(vrid)
    assert state["bumped"]
    if with_seq:
        assert mapping["switchSeq"] == first["switchSeq"] + 2, mapping
        await _served_matches_stored(world, vrid)
    else:
        assert mapping["switchSeq"] == first["switchSeq"] + 1, "the control no longer shows the lost bump"


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", ["cow", "in_place", "classify_in_place"])
async def test_a_writer_stalled_past_the_stray_grace_never_switches_to_a_purged_document(
    world: World, layout: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """QA-05cb-2. A writer pauses after creating its document for longer than the stray grace G; the
    stray pass then purges that document. The writer deadline D (< G) makes it give up instead of
    switching the mapping to a document that no longer exists. Timings are scaled (D 2 s, G 5 s, stall
    7 s); ``in_place`` is the plain-404 replacement, ``classify_in_place`` the retired-document one."""
    world.read_mode = "artifact" if layout == "cow" else "legacy"
    if layout != "cow":
        world.generation = None
    vrid = world.vrid("stall")
    rec = world.record(vrid, A)
    await world.index(rec, _rev(A))
    before = await world.mapping(vrid)
    if layout == "in_place":
        await world.blob.purge_document(world.org, before["documentId"])
    elif layout == "classify_in_place":
        await world.blob._retire_document(world.org, before["documentId"])
    monkeypatch.setattr(blob_module, "WRITER_DEADLINE_SECONDS", 2)
    monkeypatch.setattr(stored_write_module, "WRITER_DEADLINE_SECONDS", 2)
    stall = 7.0
    created: list[str] = []
    original = world.blob.save_record_to_storage

    async def slow_create(*a: Any, **kw: Any) -> Any:  # noqa: ANN401
        result = await original(*a, **kw)
        created.append(result[0])
        await asyncio.sleep(stall)
        return result

    world.blob.save_record_to_storage = slow_create  # type: ignore[method-assign]

    async def clean() -> MembershipVerdict:
        return MembershipVerdict(True)

    retention = StoredRecordRetention(world.provider, world.blob, logger, membership=clean, grace_ms=0, stray_grace_seconds=5)

    async def stray_pass_later() -> StoredRetentionReport:
        await asyncio.sleep(stall - 1.0)
        report = StoredRetentionReport()
        await retention._collect_strays(vrid, world.org, report)
        return report

    stray = asyncio.create_task(stray_pass_later())
    try:
        if layout == "classify_in_place":
            from app.modules.pipeline.leases import RecordBusy

            with pytest.raises(RecordBusy, match="deadline"):  # the stage retries a superseded write
                await world.classify(rec, _rev(A), SemanticMetadata(summary="stalled"))
        else:
            await world.index(world.record(vrid, B, name="a"), _rev(B))
    finally:
        report = await stray
    after = await world.mapping(vrid)
    mapped = after.get("documentId")
    assert mapped is not None
    assert created, "the writer created no document, so the scenario did not run"
    if mapped in created:
        assert await world.row(mapped) is not None, (
            f"{layout}: the mapping names {mapped}, a document the stray pass purged "
            f"({report.strays_purged} purged) before the switch"
        )
