"""Index → stored record → artifact set → pointer → read, end to end on real backends.

The artifact reader serves a record's artifact set only when the set is of the
revision the stored record holds (``storedRev`` on the vrid mapping). These
tests drive the real ``SinkOrchestrator.index`` with the real ``BlobStorage``
and ``ArtifactWriter`` against a running storage service (the Node storage
router on MongoDB + local disk / S3 / Azure Blob) and a real graph (Neo4j and
ArangoDB), then read through ``record_artifact_reader`` with the read flags on.
Only the vector store is a stand-in. A small proxy in front of the storage
service refuses chosen routes, so an artifact write can fail the way a storage
outage fails it.

Environment: as ``test_artifact_store_contract.py`` (``ARTIFACTS_IT_STORAGE_URL``,
``ARTIFACTS_IT_SCOPED_SECRET``, ``ARTIFACTS_IT_STORAGE_TYPE``) and
``tests/integration/real_graph.py``. Skipped without ``ARTIFACTS_IT_STORAGE_URL``.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import os
import statistics
import time
from typing import TYPE_CHECKING, Any

import pytest

from app.config.constants.neo4j import collection_to_label
from app.models.blocks import SemanticMetadata
from app.modules.artifacts import store as store_module
from app.modules.artifacts.reader import record_artifact_reader
from app.modules.transformers.blob_storage import StorageDocumentNotFoundError
from app.modules.transformers.transformer import TransformContext
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.integration import stored_write_world
from tests.integration.stored_write_world import MAPPING, PRESIGN, A, B, World, _rev

if TYPE_CHECKING:
    pass

STORAGE_URL = os.environ.get("ARTIFACTS_IT_STORAGE_URL", "")
STORAGE_TYPE = os.environ.get("ARTIFACTS_IT_STORAGE_TYPE", "local")
#: Where the graph-call and latency figures are written (for a comparison between two checkouts).
FIGURES_OUT = os.environ.get("ARTIFACTS_IT_FIGURES_OUT", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.timeout(600),
    pytest.mark.skipif(not STORAGE_URL, reason="set ARTIFACTS_IT_STORAGE_URL to a running storage service"),
]

#: The fixture every test below takes (it lives in the shared module).
world = stored_write_world.world


# ---------------------------------------------------------------------------
# Functional
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("batched", [False, True], ids=["read_record", "lookup_many"])
async def test_happy_path_serves_the_artifact_set(world: World, batched: bool) -> None:
    vrid = world.vrid()
    rec = world.record(vrid, A)
    await world.index(rec, _rev(A))

    mapping = await world.mapping(vrid)
    assert (mapping.get("currentRev"), mapping.get("storedRev")) == (_rev(A), _rev(A))
    assert await world.read(vrid, batched=batched) == A
    assert world.reads == ["served"]


@pytest.mark.asyncio
@pytest.mark.parametrize("cause", ["storage_refuses", "write_flag_off"])
async def test_new_content_whose_set_is_not_written_is_served_from_the_stored_record(
    world: World, cause: str,
) -> None:
    vrid = world.vrid()
    rec = world.record(vrid, A)
    await world.index(rec, _rev(A))
    assert await world.read(vrid) == A

    if cause == "storage_refuses":
        world.proxy.refuse.append(PRESIGN)
    else:
        world.write_on = False
    await world.index(world.record(vrid, B), _rev(B))

    mapping = await world.mapping(vrid)
    assert (mapping.get("currentRev"), mapping.get("storedRev")) == (_rev(A), _rev(B))
    world.reads.clear()
    for batched in (False, True):
        assert await world.read(vrid, batched=batched) == B
    assert world.reads == ["stale", "stale"]

    # Shadow: serves the stored record, counts the set as unavailable, never a mismatch.
    world.read_mode = "shadow"
    world.shadow.clear()
    assert await world.read(vrid) == B
    assert world.shadow == [("unavailable",)]

    # Writes work again: the next index of B is served from its set.
    world.proxy.refuse.clear()
    world.write_on = True
    world.read_mode = "artifact"
    await world.index(world.record(vrid, B), _rev(B))
    world.reads.clear()
    assert await world.read(vrid) == B
    assert world.reads == ["served"]


@pytest.mark.asyncio
async def test_shadow_matches_on_the_happy_path(world: World) -> None:
    vrid = world.vrid()
    await world.index(world.record(vrid, A), _rev(A))
    world.read_mode = "shadow"
    assert await world.read(vrid) == A
    assert world.shadow == [("match",)]


@pytest.mark.asyncio
async def test_a_re_embed_keeps_the_stored_revision(world: World) -> None:
    vrid = world.vrid()
    rec = world.record(vrid, A)
    await world.index(rec, _rev(A))
    before = await world.mapping(vrid)

    # The vector-only reindex: blocks from the stored record, skip_blob.
    await world.index(rec, _rev(A), skip_blob=True)

    after = await world.mapping(vrid)
    assert (after.get("currentRev"), after.get("storedRev")) == (before["currentRev"], before["storedRev"])
    world.reads.clear()
    assert await world.read(vrid) == A
    assert world.reads == ["served"]


@pytest.mark.asyncio
async def test_classification_rewrites_keep_the_stored_revision(world: World) -> None:
    """A classification changes the stored record's metadata, not its revision: the set is still
    served, now with the classification the stage filed (label repair is in ``test_artifact_metadata_writers_e2e.py``)."""
    vrid = world.vrid()
    rec = world.record(vrid, A)
    await world.index(rec, _rev(A))

    await world.classify(rec, _rev(A), SemanticMetadata(summary="s", categories=["x"]))

    assert (await world.mapping(vrid)).get("storedRev") == _rev(A)
    world.reads.clear()
    assert await world.read(vrid) == A
    assert world.reads == ["served"]
    stored = await world.blob.get_record_from_storage(vrid, world.org)
    assert stored["semantic_metadata"]["summary"] == "s"


@pytest.mark.asyncio
async def test_an_index_without_a_revision_clears_the_stored_revision(world: World) -> None:
    vrid = world.vrid()
    await world.index(world.record(vrid, A), _rev(A))

    await world.index(world.record(vrid, B), None)

    assert not (await world.mapping(vrid)).get("storedRev")
    world.reads.clear()
    assert await world.read(vrid) == B
    assert world.reads == ["stale"]


@pytest.mark.asyncio
async def test_a_mapping_from_before_stored_rev_is_stale_until_one_re_index(world: World) -> None:
    vrid = world.vrid()
    rec = world.record(vrid, A)
    await world.index(rec, _rev(A))
    # What an older release left: the pointer, no storedRev.
    mapping = await world.mapping(vrid)
    if isinstance(world.provider, Neo4jProvider):
        await world.provider.client.execute_query(
            f"MATCH (n:{collection_to_label(MAPPING)} {{id: $id}}) REMOVE n.storedRev", parameters={"id": vrid},
        )
    else:
        await world.provider.http_client.execute_aql(
            f"FOR n IN {MAPPING} FILTER n._key == @k UPDATE n WITH {{storedRev: null}} IN {MAPPING} OPTIONS {{keepNull: false}}",
            {"k": vrid},
        )
    assert "storedRev" not in await world.mapping(vrid)
    assert (await world.mapping(vrid)).get("currentRev") == mapping["currentRev"]

    world.reads.clear()
    assert await world.read(vrid) == A
    assert world.reads == ["stale"]

    await world.index(rec, _rev(A))
    world.reads.clear()
    assert await world.read(vrid) == A
    assert world.reads == ["served"]


@pytest.mark.asyncio
@pytest.mark.parametrize("cause", ["storage_refuses", "write_flag_off"])
async def test_same_source_bytes_re_parsed_whose_set_is_not_written(world: World, cause: str) -> None:
    """A re-index of unchanged source bytes that parses differently (a parser or OCR
    setting changed) keeps the revision. When its set is not written, the stored
    record and the vectors hold the new parse; the reader must not answer with the old."""
    vrid = world.vrid()
    rev = _rev(A)
    await world.index(world.record(vrid, A), rev)
    assert await world.read(vrid) == A

    reparsed = ["alpha one", "alpha two, now with the table text", "alpha three"]
    if cause == "storage_refuses":
        world.proxy.refuse.append(PRESIGN)
    else:
        world.write_on = False
    await world.index(world.record(vrid, reparsed), rev)

    assert await world.stored(vrid) == reparsed
    world.reads.clear()
    served = await world.read(vrid)
    world.read_mode = "shadow"
    world.shadow.clear()
    await world.read(vrid)
    assert served == reparsed, (
        f"served the previous parse ({world.reads}) while the stored record holds the new one; "
        f"shadow says {world.shadow}"
    )


@pytest.mark.asyncio
async def test_same_revision_partial_set_rewrite_is_not_served(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """A rewrite of the same revision's set that fails part way (files overwritten, the
    manifest not) must not serve the mix: the manifest's sha256s catch it."""
    vrid = world.vrid()
    rev = _rev(A)
    await world.index(world.record(vrid, A), rev)
    store = world.sink.artifact_writer.store
    real_put = store.put

    # Every file is overwritten, the manifest is not: the old manifest's sha256s no
    # longer match. (A failure that leaves the changed files untouched is the
    # re-parse case above.)
    async def put(entry: Any, data: bytes, content_type: str | None = None) -> None:  # noqa: ANN401
        if "manifest.json" in entry.url.split("?")[0]:
            raise store_module.ArtifactStoreError("refused by the test")
        await real_put(entry, data, content_type)

    monkeypatch.setattr(store, "put", put)
    reparsed = ["alpha one changed", "alpha two changed"]
    await world.index(world.record(vrid, reparsed), rev)

    world.reads.clear()
    assert await world.read(vrid) == reparsed
    assert world.reads != ["served"]


# ---------------------------------------------------------------------------
# Shared content: two records, one vrid
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shared_content_same_bytes_interleaved(world: World) -> None:
    vrid = world.vrid()
    x, y = world.record(vrid, A, name="x"), world.record(vrid, A, name="y")
    await world.put_record_node(y, _rev(A))
    await asyncio.gather(world.index(x, _rev(A)), world.index(y, _rev(A)))

    mapping = await world.mapping(vrid)
    assert (mapping.get("currentRev"), mapping.get("storedRev")) == (_rev(A), _rev(A))
    world.reads.clear()
    assert await world.read(vrid) == A
    assert world.reads == ["served"]


@pytest.mark.asyncio
@pytest.mark.parametrize("order", ["x_then_y", "y_then_x"])
@pytest.mark.parametrize("y_set", ["written", "refused"])
async def test_shared_vrid_two_revisions_interleaved(world: World, order: str, y_set: str) -> None:
    """X stores A, Y stores B on the same vrid; the artifact writes and flips land in
    either order, and Y's set may be refused. Whatever is served is the stored record's content."""
    vrid = world.vrid()
    x, y = world.record(vrid, A, name="x"), world.record(vrid, B, name="y")
    await world.index(x, _rev(A))

    gate_x, gate_y = asyncio.Event(), asyncio.Event()
    real_write = world.sink._write_artifacts

    async def write_artifacts(ctx: TransformContext, record_doc: dict[str, Any]) -> Any:  # noqa: ANN401
        first, second = (gate_x, gate_y) if order == "x_then_y" else (gate_y, gate_x)
        mine = gate_x if ctx.record.id == x.id else gate_y
        other = gate_y if mine is gate_x else gate_x
        if mine is second:
            await first.wait()
        refused = ctx.record.id == y.id and y_set == "refused"
        try:
            if refused:
                world.proxy.refuse.append(PRESIGN)
            return await real_write(ctx, record_doc)
        finally:
            if refused:
                world.proxy.refuse.remove(PRESIGN)
            mine.set()
            del other

    world.sink._write_artifacts = write_artifacts  # type: ignore[method-assign]
    # Both stored-record writes land before either artifact write: X (A) first, then Y (B).
    stored_gate = asyncio.Event()
    real_apply = world.blob.apply

    async def apply(ctx: TransformContext) -> Any:  # noqa: ANN401
        if ctx.record.id == y.id:
            await stored_gate.wait()
        out = await real_apply(ctx)
        if ctx.record.id == x.id:
            stored_gate.set()
        return out

    world.blob.apply = apply  # type: ignore[method-assign]
    await world.put_record_node(y, _rev(B))
    await asyncio.gather(world.index(x, _rev(A)), world.index(y, _rev(B)))

    stored = await world.stored(vrid)
    assert stored == B
    world.reads.clear()
    served = await world.read(vrid)
    assert served == stored, f"served {served} ({world.reads}), stored record holds {stored}; mapping {await world.mapping(vrid)}"


@pytest.mark.asyncio
async def test_shared_vrid_upload_and_mapping_interleaved(world: World) -> None:
    """X uploads A, then Y uploads B and maps it, then X maps: the storage upload and the
    mapping write are two writes. The reader must not serve a set the stored record is not of."""
    vrid = world.vrid()
    x, y = world.record(vrid, A, name="x"), world.record(vrid, B, name="y")
    await world.index(x, _rev(A))
    await world.put_record_node(y, _rev(B))

    x_uploaded, y_done = asyncio.Event(), asyncio.Event()
    state = {"x_mapping_calls": 0}

    def x_is_mapping(stored_rev: object) -> bool:
        if stored_rev == _rev(A) and state["x_mapping_calls"] == 0:
            state["x_mapping_calls"] += 1
            return True
        return False

    # The compare-and-set that names the new document on the mapping.
    real_switch = world.blob._switch_mapping

    async def switch_mapping(org_id: str, virtual_record_id: str, node: Any, fields: dict[str, Any], **kw: Any) -> bool:  # noqa: ANN401
        if x_is_mapping(fields.get("storedRev")):
            x_uploaded.set()
            await y_done.wait()
        return await real_switch(org_id, virtual_record_id, node, fields, **kw)

    world.blob._switch_mapping = switch_mapping  # type: ignore[method-assign]

    async def run_y() -> None:
        await x_uploaded.wait()
        await world.sink.index(TransformContext(record=y.model_copy(deep=True), settings={}))
        y_done.set()

    await world.put_record_node(x, _rev(A))
    await asyncio.gather(world.sink.index(TransformContext(record=x.model_copy(deep=True), settings={})), run_y())

    stored = await world.stored(vrid)
    mapping = await world.mapping(vrid)
    world.reads.clear()
    served = await world.read(vrid)
    assert served == stored, (
        f"served {served} ({world.reads}) while the stored record holds {stored}; "
        f"mapping currentRev={mapping.get('currentRev')} storedRev={mapping.get('storedRev')}"
    )


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["artifact", "shadow"])
async def test_another_org_cannot_read_the_set_or_the_stored_record(world: World, mode: str) -> None:
    vrid = world.vrid()
    await world.index(world.record(vrid, A), _rev(A))
    world.read_mode = mode
    world.reads.clear()

    # The stored-record read answers another org's id as not found (it raises, as it
    # always has with every flag off); what matters is that no content comes back.
    outcomes = []
    for read_mode in (mode, "legacy"):
        world.read_mode = read_mode
        try:
            outcomes.append(await world.read(vrid, org=world.other_org))
        except StorageDocumentNotFoundError:
            outcomes.append("not_found")
    assert outcomes == ["not_found", "not_found"], outcomes
    if mode == "artifact":
        assert world.reads and world.reads[0] not in ("served", "stale"), world.reads


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------


def _count_calls(provider: Any, counts: dict[str, int]) -> list[tuple[str, Any]]:  # noqa: ANN401
    patched = []
    for name, _fn in inspect.getmembers(type(provider), inspect.iscoroutinefunction):
        if name.startswith("_"):
            continue
        bound = getattr(provider, name)

        def wrap(bound: Any = bound, name: str = name) -> Any:  # noqa: ANN401
            async def counted(*a: Any, **kw: Any) -> Any:  # noqa: ANN401
                counts[name] = counts.get(name, 0) + 1
                return await bound(*a, **kw)
            return counted

        setattr(provider, name, wrap())
        patched.append((name, bound))
    return patched


def _figures(key: str, value: Any) -> None:  # noqa: ANN401
    if not FIGURES_OUT:
        return
    data: dict[str, Any] = {}
    with contextlib.suppress(FileNotFoundError, json.JSONDecodeError), open(FIGURES_OUT) as f:
        data = json.load(f)
    data[key] = value
    with open(FIGURES_OUT, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)


@pytest.mark.asyncio
async def test_graph_calls_per_index(world: World) -> None:
    vrid = world.vrid()
    first = world.record(vrid, A)
    await world.put_record_node(first, _rev(A))
    counts: dict[str, int] = {}
    patched = _count_calls(world.provider, counts)
    try:
        await world.sink.index(TransformContext(record=first.model_copy(deep=True), settings={}))
        first_counts = dict(counts)
        counts.clear()
        changed = world.record(vrid, B)
        for name, bound in patched:
            setattr(world.provider, name, bound)
        await world.put_record_node(changed, _rev(B))
        patched = _count_calls(world.provider, counts)
        await world.sink.index(TransformContext(record=changed.model_copy(deep=True), settings={}))
    finally:
        for name, bound in patched:
            setattr(world.provider, name, bound)
    engine = "neo4j" if isinstance(world.provider, Neo4jProvider) else "arango"
    _figures(f"graph_calls.{engine}.first_index", first_counts)
    _figures(f"graph_calls.{engine}.next_index", counts)
    # One graph.read of the record, one before the flip; the mapping read by the lookups.
    assert first_counts.get("get_document", 0) <= 6, first_counts
    assert sum(first_counts.values()) <= 20, first_counts


@pytest.mark.asyncio
async def test_reader_latency(world: World) -> None:
    vrid = world.vrid()
    await world.index(world.record(vrid, A * 50), _rev(A * 50))
    reader = record_artifact_reader(world.blob)

    async def timed(n: int) -> list[float]:
        out = []
        for _ in range(n):
            t = time.perf_counter()
            assert await reader.read_record(vrid, world.org) is not None
            out.append((time.perf_counter() - t) * 1000)
        return out

    await timed(3)
    world.read_mode = "artifact"
    served = await timed(30)
    world.read_mode = "legacy"
    legacy = await timed(30)

    # The stale answer costs no storage call at all.
    await world.provider.batch_upsert_nodes(
        [{"id": vrid, "storedRev": "0" * 16}], MAPPING,
    )
    world.read_mode = "artifact"
    world.proxy.seen.clear()
    world.reads.clear()
    stale = await timed(30)
    assert set(world.reads) == {"stale"}
    assert not [s for s in world.proxy.seen if "read-urls" in s], world.proxy.seen
    engine = "neo4j" if isinstance(world.provider, Neo4jProvider) else "arango"
    _figures(f"latency_ms.{engine}.{STORAGE_TYPE}", {
        "served_median": statistics.median(served), "legacy_median": statistics.median(legacy),
        "stale_median": statistics.median(stale),
    })
    # The stale check is in memory: a stale read costs what the stored-record read costs.
    assert statistics.median(stale) <= statistics.median(legacy) * 1.5 + 10
