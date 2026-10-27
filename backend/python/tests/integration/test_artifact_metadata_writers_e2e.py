"""Classification and label repair on real backends: a metadata writer rewrites only
``semantic_metadata`` of the mapped stored record, by copy-on-write plus a sidecar named by the
write (a flag on) or in place (both off), and a read serves exactly that metadata with the blocks
it belongs to, or falls back. Fixtures: ``tests/integration/stored_write_world.py``.

A test that shows an invariant holds is paired with a ``*_control`` test that breaks the mechanism
and shows the invariant then fails. Skipped without ``ARTIFACTS_IT_STORAGE_URL``; the graph
stores are ``NEO4J_IT_URI`` and ``ARANGO_IT_URL``.
"""

from __future__ import annotations

import os
import re
from typing import Any

import pytest

from app.models.blocks import SemanticMetadata
from app.modules.artifacts import record_backend as backend_module
from app.modules.artifacts.sidecars import ArtifactSidecars
from app.modules.artifacts.store import NodeArtifactStore
from app.modules.indexing.record_label_repair import RecordLabelRepair
from app.modules.pipeline.leases import RecordBusy
from app.modules.pipeline.stages.classify import ClassifyStage
from app.modules.transformers.blob_storage import DocumentRetiredError
from app.modules.transformers.stored_write import (
    StoredWriteBusy,
    is_none_run,
)
from app.services.graph_db.taxonomy import TaxonomyLink
from tests.integration import stored_write_world
from tests.integration.stored_write_world import MAPPING, RECORDS, A, B, World, _rev

STORAGE_URL = os.environ.get("ARTIFACTS_IT_STORAGE_URL", "")
STORAGE_TYPE = os.environ.get("ARTIFACTS_IT_STORAGE_TYPE", "local")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.timeout(600),
    pytest.mark.skipif(not STORAGE_URL, reason="set ARTIFACTS_IT_STORAGE_URL to a running storage service"),
]

world = stored_write_world.world

PRESIGN = re.compile(r"/internal/artifacts/presign$")
TOPIC = "Falcon launch window"
OWN_TOPIC = "Product launch window"


def _meta(summary: str, **fields: Any) -> SemanticMetadata:  # noqa: ANN401
    return SemanticMetadata(summary=summary, **fields)


async def _stored_semantic(world: World, vrid: str) -> dict[str, Any]:
    stored = await world.blob.get_record_from_storage(vrid, world.org)
    assert stored is not None
    return stored.get("semantic_metadata") or {}


async def _read_semantic(world: World, vrid: str) -> tuple[dict[str, Any], list[str]]:
    world.reads.clear()
    reader = backend_module.ArtifactRecordBackend(world.blob)
    lookup = await world.blob.get_document_id_by_virtual_record_id(vrid)
    try:
        record = await reader.read(vrid, world.org, lookup=lookup)
    except backend_module.ArtifactUnavailable:
        return {}, list(world.reads)
    return record.get("semantic_metadata") or {}, list(world.reads)


# ---------------------------------------------------------------------------
# Copy-on-write classification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classification_is_served_from_the_sidecar_the_mapping_names(world: World) -> None:
    vrid = world.vrid()
    rec = world.record(vrid, A)
    await world.index(rec, _rev(A))
    before = await world.mapping(vrid)

    await world.classify(rec, _rev(A), _meta("first", categories=["Finance"]))

    after = await world.mapping(vrid)
    assert after["documentId"] != before["documentId"]
    assert after["storedRun"] == before["storedRun"] and after["currentRun"] == before["currentRun"]
    assert re.fullmatch(r"enrich/classification\.[0-9a-f]{32}@4\.json", after["storedMeta"])
    assert (await world.row(before["documentId"]))["retiredAt"]
    semantic, reads = await _read_semantic(world, vrid)
    assert semantic["summary"] == "first"
    assert semantic == await _stored_semantic(world, vrid)
    assert reads == ["served"]


@pytest.mark.asyncio
async def test_the_metadata_switch_stamps_the_switch_fields(world: World) -> None:
    """T61. ``switchDoc = documentId`` and ``updatedAt == switchUpdatedAt`` after the switch (no fingerprint),
    ``switchSeq`` bumped, the replaced document on ``retired`` with the sidecar it named."""
    vrid = world.vrid()
    rec = world.record(vrid, A)
    await world.index(rec, _rev(A))
    await world.classify(rec, _rev(A), _meta("one"))
    first = await world.mapping(vrid)

    await world.classify(rec, _rev(A), _meta("two"))

    second = await world.mapping(vrid)
    assert second["switchSeq"] == first["switchSeq"] + 1
    assert second["switchDoc"] == second["documentId"] != first["documentId"]
    assert second["updatedAt"] == second["switchUpdatedAt"] == second["lastSwitchAt"]
    entry = next(e for e in second["retired"] if e["doc"] == first["documentId"])
    assert entry["sidecar"] == first["storedMeta"] and entry["rev"] == _rev(A)


@pytest.mark.asyncio
@pytest.mark.parametrize("cas", [True, False], ids=["cas", "unconditional_switch_control"])
async def test_classification_cas_vs_reindex(world: World, cas: bool) -> None:
    """T5. A classification racing a re-index (a barrier between its read and its switch): its
    compare-and-set fails, its copy is left as a stray, the job is retried; served == stored. An
    unconditional metadata switch writes the old blocks back over the new content."""
    vrid = world.vrid()
    rec = world.record(vrid, A)
    await world.index(rec, _rev(A))
    await world.classify(rec, _rev(A), _meta("first"))

    real_retire = world.blob._retire_document
    state = {"done": False}

    async def reindex_then_retire(org_id: str, document_id: str) -> None:
        if not state["done"]:
            state["done"] = True
            world.blob._retire_document = real_retire  # type: ignore[method-assign]
            await world.index(world.record(vrid, B), _rev(B))
        await real_retire(org_id, document_id)

    real_switch = world.blob._switch_mapping

    async def unconditional(org_id: str, vrid_: str, node: Any, fields: dict[str, Any], **kw: Any) -> bool:  # noqa: ANN401
        await world.provider.batch_upsert_nodes([{"id": vrid_, **fields}], MAPPING)
        return True

    world.blob._retire_document = reindex_then_retire  # type: ignore[method-assign]
    if not cas:
        world.blob._switch_mapping = unconditional  # type: ignore[method-assign]

    if cas:
        with pytest.raises(RecordBusy):
            await world.classify(rec, _rev(A), _meta("racing"))
    else:
        await world.classify(rec, _rev(A), _meta("racing"))
    world.blob._switch_mapping = real_switch  # type: ignore[method-assign]

    if not cas:
        # The old blocks are stored again: the re-index's content is lost.
        assert await world.stored(vrid) == A
        return
    assert await world.stored(vrid) == B
    # The job is retried against the new mapping and succeeds.
    await world.put_record_node(world.record(vrid, B), _rev(B))
    await world.classify(world.record(vrid, B), _rev(B), _meta("retried"))
    assert await world.stored(vrid) == B
    assert (await _stored_semantic(world, vrid))["summary"] == "retried"
    semantic, _ = await _read_semantic(world, vrid)
    assert semantic["summary"] == "retried"


@pytest.mark.asyncio
@pytest.mark.parametrize("expects_run_and_meta", [True, False], ids=["cas", "no_run_meta_expectation_control"])
async def test_classification_switch_is_refused_when_the_stored_meta_changed_under_it(
    world: World, expects_run_and_meta: bool,
) -> None:
    """QA-05cb-6. The mapping's ``storedMeta`` changes between a classification's read and its switch
    while ``documentId`` and ``switchSeq`` stay as read. Only the ``{storedRun, storedMeta}``
    expectation can see it; without it the classification switches over the other classification."""
    vrid = world.vrid()
    rec = world.record(vrid, A)
    await world.index(rec, _rev(A))
    await world.classify(rec, _rev(A), _meta("first"))

    real_retire = world.blob._retire_document
    real_switch = world.blob._switch_mapping
    state = {"changed": False}

    async def change_meta_then_retire(org_id: str, document_id: str) -> None:
        if not state["changed"]:
            state["changed"] = True
            await world.provider.update_node_fields_if_match(
                vrid, MAPPING, {"storedMeta": "changed-by-another-writer"}, {"documentId": document_id},
            )
        await real_retire(org_id, document_id)

    async def without_run_and_meta(org_id: str, vrid_: str, node: Any, fields: dict[str, Any], **kw: Any) -> bool:  # noqa: ANN401
        kw.pop("expect_also", None)
        return await real_switch(org_id, vrid_, node, fields, **kw)

    world.blob._retire_document = change_meta_then_retire  # type: ignore[method-assign]
    if not expects_run_and_meta:
        world.blob._switch_mapping = without_run_and_meta  # type: ignore[method-assign]
    try:
        if expects_run_and_meta:
            with pytest.raises(RecordBusy):
                await world.classify(rec, _rev(A), _meta("racing"))
            assert (await world.mapping(vrid))["storedMeta"] == "changed-by-another-writer"
        else:
            await world.classify(rec, _rev(A), _meta("racing"))
            assert (await world.mapping(vrid))["storedMeta"] != "changed-by-another-writer"
    finally:
        world.blob._retire_document = real_retire  # type: ignore[method-assign]
        world.blob._switch_mapping = real_switch  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_sidecar_refused_is_stale_sidecar(world: World) -> None:
    """T6. The sidecar cannot be filed after a copy-on-write classification: the stored record has the
    new classification, the read is stale_sidecar and falls back to the stored record."""
    vrid = world.vrid()
    rec = world.record(vrid, A)
    await world.index(rec, _rev(A))

    world.proxy.refuse.append(PRESIGN)
    await world.classify(rec, _rev(A), _meta("only in the stored record"))
    world.proxy.refuse.clear()

    mapping = await world.mapping(vrid)
    assert mapping["storedMeta"]
    assert (await _stored_semantic(world, vrid))["summary"] == "only in the stored record"
    world.reads.clear()
    served = await world.read(vrid)
    assert served == A and world.reads == ["stale_sidecar"]
    # The next classification files its own sidecar and the set is served again.
    await world.classify(rec, _rev(A), _meta("filed"))
    world.reads.clear()
    assert await world.read(vrid) == A and world.reads == ["served"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stamp", [True, False], ids=["stamp", "reader_ignores_stamp_control"])
async def test_a_sidecar_of_another_write_is_never_served(
    world: World, stamp: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T6 control. The file at the named path was stamped for another stored write: the stamp makes it
    stale_sidecar; a reader that ignored the stamp serves a classification the stored record does not hold."""
    vrid = world.vrid()
    rec = world.record(vrid, A)
    await world.index(rec, _rev(A))
    await world.classify(rec, _rev(A), _meta("old classification"))
    old_path = (await world.mapping(vrid))["storedMeta"]

    # The next classification's sidecar cannot be filed; a stale copy of the old envelope sits at its path instead.
    world.proxy.refuse.append(PRESIGN)
    await world.classify(rec, _rev(A), _meta("new classification"))
    world.proxy.refuse.clear()
    new_path = (await world.mapping(vrid))["storedMeta"]
    assert new_path != old_path
    sidecars = ArtifactSidecars(NodeArtifactStore(world.blob, stored_write_world.logger), world.blob.config_service)
    await sidecars.write_at(world.org, vrid, _rev(A), new_path, {
        "storedRun": "gen-it:some-other-write", "storedMeta": new_path,
        "classification": {"summary": "old classification"},
    })

    if not stamp:
        monkeypatch.setattr(
            backend_module.ArtifactRecordBackend, "_classification",
            staticmethod(lambda data, lookup, path: __import__("json").loads(data)["classification"]),
        )
    semantic, reads = await _read_semantic(world, vrid)
    stored = await _stored_semantic(world, vrid)
    if stamp:
        assert reads == ["stale_sidecar"]
        assert semantic == {}
    else:
        assert semantic.get("summary") == "old classification" != stored["summary"]


@pytest.mark.asyncio
async def test_reparse_after_classification(world: World) -> None:
    """T7. A same-revision re-parse after a classification: the sink's switch clears ``storedMeta``, the
    old classification is not served over the new blocks, ``metadata.json`` is (and equals the stored record)."""
    vrid = world.vrid()
    rec = world.record(vrid, A)
    await world.index(rec, _rev(A))
    await world.classify(rec, _rev(A), _meta("classified"))
    assert (await world.mapping(vrid))["storedMeta"]

    reparsed = ["alpha one", "alpha two, re-parsed"]
    await world.index(world.record(vrid, reparsed), _rev(A))

    mapping = await world.mapping(vrid)
    assert not mapping.get("storedMeta")
    world.reads.clear()
    assert await world.read(vrid) == reparsed
    assert world.reads == ["served"]
    semantic, _ = await _read_semantic(world, vrid)
    assert semantic == await _stored_semantic(world, vrid)
    assert semantic.get("summary") != "classified"


@pytest.mark.asyncio
async def test_sidecar_path_from_stored_meta(world: World) -> None:
    """T10. A reader one stage version behind the writer: the path comes from the mapping, so a
    sidecar filed by an older stage version is served."""
    vrid = world.vrid()
    rec = world.record(vrid, A)
    await world.index(rec, _rev(A))

    await world.classify(rec, _rev(A), _meta("by an older stage"), version=ClassifyStage.version + 1)

    mapping = await world.mapping(vrid)
    assert mapping["storedMeta"].endswith(f"@{ClassifyStage.version + 1}.json")
    world.reads.clear()
    assert await world.read(vrid) == A and world.reads == ["served"]
    semantic, _ = await _read_semantic(world, vrid)
    assert semantic["summary"] == "by an older stage"


@pytest.mark.asyncio
@pytest.mark.parametrize("keep", [False, True], ids=["identity_unchanged", "keep_owner_identity_control"])
async def test_classification_keeps_identity(world: World, keep: bool) -> None:
    """T9. Shared vrid, the owner gone, the classifying record not the smallest holder: the identity
    fields of the stored copy are unchanged by the classification. Through ``_keep_owner_identity`` (the
    sink's merge) the copy would lose them."""
    vrid = world.vrid()
    owner = world.record(vrid, A, name="a-owner")
    z = world.record(vrid, A, name="z-classifier")
    y = world.record(vrid, A, name="y-holder")
    await world.index(owner, _rev(A))
    await world.put_record_node(y, _rev(A))
    await world.put_record_node(z, _rev(A))
    await world.provider.delete_nodes([owner.id], RECORDS)
    before = await world.blob.get_record_from_storage(vrid, world.org)
    assert before["id"] == owner.id

    if keep:
        # The sink's merge for a record that is not the smallest holder: only the shared content survives,
        # so the identity of the stored copy would be dropped.
        lookup = await world.blob.get_document_id_by_virtual_record_id(vrid)
        own = {**before, "id": z.id, "record_name": "z-classifier.txt", "semantic_metadata": {"summary": "c"}}
        merged = await world.blob._keep_owner_identity(world.org, z.id, vrid, own, lookup, [y.id, z.id])
        assert "id" not in merged and "record_name" not in merged
        return
    await world.classify(z, _rev(A), _meta("c"))
    after = await world.blob.get_record_from_storage(vrid, world.org)
    for key in ("id", "record_name", "connector_id", "org_id"):
        assert after.get(key) == before.get(key), key
    assert after["semantic_metadata"]["summary"] == "c"
    assert after["block_containers"] == before["block_containers"]


@pytest.mark.asyncio
async def test_metadata_switch_record_recheck(world: World) -> None:
    """T60. The record is deleted between the metadata writer's lease and its switch: no switch, busy."""
    vrid = world.vrid()
    rec = world.record(vrid, A)
    await world.index(rec, _rev(A))
    before = await world.mapping(vrid)
    real_retire = world.blob._retire_document

    async def retire_and_delete_record(org_id: str, document_id: str) -> None:
        await real_retire(org_id, document_id)
        await world.provider.delete_nodes([rec.id], RECORDS)

    world.blob._retire_document = retire_and_delete_record  # type: ignore[method-assign]
    with pytest.raises((StoredWriteBusy, RecordBusy)):
        await world.classify(rec, _rev(A), _meta("late"))

    after = await world.mapping(vrid)
    assert (after["documentId"], after["switchSeq"]) == (before["documentId"], before["switchSeq"])


# ---------------------------------------------------------------------------
# In place (both flags off)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metadata_writers_in_place(world: World) -> None:
    """T46. Flags off: a classification invalidates a servable run (or skips that when the run is already a
    ``none`` run), overwrites the document, switches by compare-and-set and files no sidecar; a retired document
    (a copy-on-write write that did not finish) gets a new document with its tags."""
    vrid = world.vrid()
    rec = world.record(vrid, A, connector="conn-owner")
    await world.index(rec, _rev(A))
    servable = await world.mapping(vrid)
    assert not is_none_run(servable["storedRun"])
    world.read_mode = "legacy"
    cas_calls: list[dict[str, Any]] = []
    real_switch = world.blob._switch_mapping

    async def watching(org_id: str, vrid_: str, node: Any, fields: dict[str, Any], **kw: Any) -> bool:  # noqa: ANN401
        cas_calls.append(fields)
        return await real_switch(org_id, vrid_, node, fields, **kw)

    world.blob._switch_mapping = watching  # type: ignore[method-assign]

    await world.classify(rec, _rev(A), _meta("in place"))

    assert len(cas_calls) == 2  # invalidation + final
    mapping = await world.mapping(vrid)
    assert mapping["documentId"] == servable["documentId"] and is_none_run(mapping["storedRun"])
    assert not mapping.get("storedMeta") and mapping["switchSeq"] == servable["switchSeq"] + 2
    assert (await _stored_semantic(world, vrid))["summary"] == "in place"

    cas_calls.clear()
    await world.classify(rec, _rev(A), _meta("again"))
    assert len(cas_calls) == 1  # already a none run: no invalidation
    assert (await world.mapping(vrid))["switchSeq"] == mapping["switchSeq"] + 1

    # A document marked retired and still mapped: the PUT is refused, the content goes to a new document.
    await world.blob._retire_document(world.org, mapping["documentId"])
    cas_calls.clear()
    await world.classify(rec, _rev(A), _meta("after the mark"))
    final = await world.mapping(vrid)
    assert final["documentId"] != mapping["documentId"]
    assert (await world.row(final["documentId"]))["customMetadata"]
    assert (await _stored_semantic(world, vrid))["summary"] == "after the mark"
    assert any(e["doc"] == mapping["documentId"] for e in final["retired"])

    with pytest.raises(DocumentRetiredError):
        await world.blob.update_record_buffer(world.org, mapping["documentId"], {"x": 1}, vrid)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalidate", [True, False], ids=["invalidates", "no_invalidation_control"])
async def test_in_place_classification_is_not_served_over_an_old_set(
    world: World, invalidate: bool,
) -> None:
    """T46 control. After an in-place classification the set built for an earlier run is not served over the
    new metadata; without the invalidation the old set is served while the stored record holds new metadata."""
    vrid = world.vrid()
    rec = world.record(vrid, A)
    await world.index(rec, _rev(A))
    world.read_mode = "legacy"
    real_switch = world.blob._switch_mapping
    during: dict[str, Any] = {}
    real_put = world.blob.update_record_buffer

    if not invalidate:
        async def skip(org_id: str, vrid_: str, node: Any, fields: dict[str, Any], **kw: Any) -> bool:  # noqa: ANN401
            if "documentId" not in fields and "storedRun" in fields:
                return True
            if "documentId" in fields:
                kw["expect_seq"] = node["switchSeq"]
                fields = {**fields, "switchSeq": node["switchSeq"] + 1}
            return await real_switch(org_id, vrid_, node, fields, **kw)

        world.blob._switch_mapping = skip  # type: ignore[method-assign]

    async def put(*a: Any, **kw: Any) -> Any:  # noqa: ANN401
        result = await real_put(*a, **kw)
        world.read_mode = "artifact"
        world.reads.clear()
        during["read"] = await _read_semantic(world, vrid)
        during["stored"] = await _stored_semantic(world, vrid)
        world.read_mode = "legacy"
        return result

    world.blob.update_record_buffer = put  # type: ignore[method-assign]
    await world.classify(rec, _rev(A), _meta("new metadata"))

    semantic, _ = during["read"]
    assert during["stored"]["summary"] == "new metadata"
    if invalidate:
        assert semantic == {}  # stale: the stored record is read instead
    else:
        assert semantic.get("summary") != "new metadata"  # the set built before the write


# ---------------------------------------------------------------------------
# Label repair
# ---------------------------------------------------------------------------


class _Leader:
    async def try_acquire(self) -> bool:
        return True

    async def refresh(self) -> bool:
        return True

    async def release(self) -> None:
        pass

    async def close(self) -> None:
        pass


def _links(record_id: str) -> list[TaxonomyLink]:
    link = TaxonomyLink.from_row({
        "recordId": record_id, "collection": "topics", "entityId": "t-launch", "name": TOPIC,
        "extractedName": OWN_TOPIC, "canonical": True, "migrated": False,
    })
    assert link is not None
    return [link]


def _repair(world: World) -> RecordLabelRepair:
    return RecordLabelRepair(
        logger=stored_write_world.logger, graph_provider=world.provider, blob_store=world.blob, lock=_Leader(),
        cutoff_ms=1_000_000, page_size=10, lease_manager=lambda: None,
        sidecars=ArtifactSidecars(NodeArtifactStore(world.blob, stored_write_world.logger), world.blob.config_service),
    )


async def _indexed_and_classified(world: World, texts: list[str]) -> tuple[str, Any]:
    vrid = world.vrid()
    rec = world.record(vrid, texts)
    await world.index(rec, _rev(texts))
    assert await world.provider.batch_update_nodes(
        [{"id": rec.id, "lastExtractionTimestamp": 500, "processingStartedAt": None}], RECORDS,
    )
    await world.classify(rec, _rev(texts), _meta("s", topics=[TOPIC]))
    return vrid, rec


@pytest.mark.asyncio
async def test_label_repair_is_served_from_its_sidecar(world: World) -> None:
    vrid, rec = await _indexed_and_classified(world, A)

    assert await _repair(world)._repair_record(rec.id, vrid, _links(rec.id)) is True

    semantic = await _stored_semantic(world, vrid)
    assert semantic["topics"] == [OWN_TOPIC] and semantic["own_labels"] is True
    served, _ = await _read_semantic(world, vrid)
    assert served == semantic
    world.reads.clear()
    assert await world.read(vrid) == A and world.reads == ["served"]


@pytest.mark.asyncio
@pytest.mark.parametrize("under_lease", [True, False], ids=["lookup_under_lease", "outside_lease_lookup_control"])
async def test_label_repair_lookup_under_lease(world: World, under_lease: bool) -> None:
    """T8. A sink replaces the stored document between label repair's plan and its write: the write reads the
    mapping again under the lease and repairs the current document. A repair that wrote into the document its
    outside-lease lookup named would be refused (retired) and lost."""
    vrid, rec = await _indexed_and_classified(world, A)
    repair = _repair(world)
    plan = repair._plan

    async def plan_then_reindex(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        planned = await plan(*args, **kwargs)
        # A sink switches the mapping, and the record is classified again for its new document.
        await world.index(world.record(vrid, A), _rev(A))
        await world.classify(world.record(vrid, A), _rev(A), _meta("s", topics=[TOPIC]))
        return planned

    repair._plan = plan_then_reindex  # type: ignore[method-assign]
    stale_lookup = await world.blob.get_document_id_by_virtual_record_id(vrid)
    if not under_lease:
        async def stale_rewrite(org_id: str, record_id: str, vrid_: str, transform: Any, **kw: Any) -> Any:  # noqa: ANN401
            stored = await world.blob.get_record_from_storage(vrid_, org_id)
            new = await transform(stored)
            await world.blob.update_record_buffer(
                org_id, stale_lookup["record_doc_id"], {**stored, "semantic_metadata": new}, vrid_,
            )
            return new

        world.blob.rewrite_semantic_metadata = stale_rewrite  # type: ignore[method-assign]

    if under_lease:
        assert await repair._repair_record(rec.id, vrid, _links(rec.id)) is True
        semantic = await _stored_semantic(world, vrid)
        assert semantic["topics"] == [OWN_TOPIC]
        served, _ = await _read_semantic(world, vrid)
        assert served == semantic
    else:
        with pytest.raises(Exception):  # noqa: B017
            await repair._repair_record(rec.id, vrid, _links(rec.id))
        assert (await _stored_semantic(world, vrid)).get("topics") != [OWN_TOPIC]


@pytest.mark.asyncio
async def test_label_repair_in_place_files_no_sidecar(world: World) -> None:
    vrid, rec = await _indexed_and_classified(world, A)
    world.read_mode = "legacy"
    before = await world.mapping(vrid)

    assert await _repair(world)._repair_record(rec.id, vrid, _links(rec.id)) is True

    after = await world.mapping(vrid)
    assert after["documentId"] == before["documentId"] and not after.get("storedMeta")
    assert (await _stored_semantic(world, vrid))["topics"] == [OWN_TOPIC]


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metadata_writer_call_counts(world: World) -> None:
    """T62. With a flag on a classification downloads the mapped document once (main downloads it twice: once
    for the section update and once for the write); with both off its graph writes equal main's."""
    vrid = world.vrid()
    rec = world.record(vrid, A)
    await world.index(rec, _rev(A))
    reads: list[str] = []
    real_get = world.blob.get_record_from_storage

    async def counting(*a: Any, **kw: Any) -> Any:  # noqa: ANN401
        reads.append("get")
        return await real_get(*a, **kw)

    world.blob.get_record_from_storage = counting  # type: ignore[method-assign]
    await world.classify(rec, _rev(A), _meta("cow"))
    assert len(reads) == 1

    world.read_mode = "legacy"
    reads.clear()
    await world.classify(rec, _rev(A), _meta("in place"))
    assert len(reads) == 1
