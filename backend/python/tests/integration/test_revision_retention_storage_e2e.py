# ruff: noqa
"""Revision retention end to end: real graph (Neo4j, ArangoDB) and the real storage service.

Revisions are written by the real ``ArtifactWriter`` through the storage
service's artifact routes, the revision pointer is moved by the indexer's own
compare-and-set (``SinkOrchestrator._flip_revision_pointer``), and the sweep
runs against both. Every combination of graph and storage target runs.

Environment:

- ``RETENTION_IT_TARGETS``: ``type=origin`` pairs, comma separated, of running
  storage services (the Node ``/api/v1/document`` routes), e.g.
  ``local=http://127.0.0.1:3001,s3=http://127.0.0.1:3002,azureBlob=http://127.0.0.1:3003``
- ``RETENTION_IT_SCOPED_SECRET``: the scoped JWT secret they verify storage tokens with
- ``RETENTION_IT_LOCAL_ROOT`` (optional): the directory the ``local`` target files under,
  to check that deleted files left the disk
- ``RETENTION_IT_SCALE`` (optional): number of mappings for the scale test (default 5000)
- graph: ``NEO4J_IT_URI`` / ``NEO4J_IT_PASSWORD`` and ``ARANGO_IT_URL`` / ``ARANGO_IT_PASSWORD``,
  as ``tests/integration/real_graph.py`` reads them

Without ``RETENTION_IT_TARGETS``, ``ARTIFACTS_IT_STORAGE_URL`` (with ``ARTIFACTS_IT_SCOPED_SECRET`` and
optionally ``ARTIFACTS_IT_STORAGE_TYPE``) names the one storage service to use.
Skipped when neither is set.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import statistics
import time
import uuid
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.config.constants.neo4j import collection_to_label
from app.config.constants.service import config_node_constants
from app.models.blocks import Block, BlocksContainer, BlockType
from app.models.entities import Record, RecordType
from app.modules.artifacts import retention as retention_module
from app.modules.artifacts import store as store_module
from app.modules.artifacts.models import MANIFEST_PATH
from app.modules.artifacts.retention import RevisionRetentionSweep
from app.modules.artifacts.store import NodeArtifactStore
from app.modules.artifacts.writer import ArtifactWriter
from app.modules.transformers.blob_storage import BlobStorage, close_shared_session
from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.real_graph import (
    ARANGO_PASSWORD,
    ARANGO_URL,
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)
from tests.integration.test_revision_retention_e2e import _World

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

TARGETS = dict(
    pair.split("=", 1) for pair in os.environ.get("RETENTION_IT_TARGETS", "").split(",") if "=" in pair
)
if not TARGETS and os.environ.get("ARTIFACTS_IT_STORAGE_URL"):
    # The storage CI job's variables: one running storage service (type via ARTIFACTS_IT_STORAGE_TYPE).
    TARGETS = {os.environ.get("ARTIFACTS_IT_STORAGE_TYPE", "local"): os.environ["ARTIFACTS_IT_STORAGE_URL"]}
SCOPED_SECRET = os.environ.get("RETENTION_IT_SCOPED_SECRET") or os.environ.get("ARTIFACTS_IT_SCOPED_SECRET", "")
LOCAL_ROOT = os.environ.get("RETENTION_IT_LOCAL_ROOT", "")
SCALE = int(os.environ.get("RETENTION_IT_SCALE", "5000"))
ARANGO_DB = "revision_retention_storage_it"
DAY_MS = 24 * 3600 * 1000

pytestmark = [
    pytest.mark.integration,
    pytest.mark.timeout(900),
    pytest.mark.skipif(not TARGETS, reason="set RETENTION_IT_TARGETS or ARTIFACTS_IT_STORAGE_URL to a running storage service"),
]

logger = logging.getLogger("revision-retention-storage-it")


def _config_service(origin: str, storage_type: str) -> MagicMock:
    values = {
        config_node_constants.SECRET_KEYS.value: {"scopedJwtSecret": SCOPED_SECRET},
        config_node_constants.ENDPOINTS.value: {"cm": {"endpoint": origin}},
        config_node_constants.STORAGE.value: {"storageType": storage_type},
    }
    service = MagicMock()
    service.get_config = AsyncMock(side_effect=lambda key, *a, **kw: values[key])
    return service


@pytest.fixture(params=sorted(TARGETS) or ["none"])
async def target(request: pytest.FixtureRequest) -> AsyncIterator[SimpleNamespace]:
    storage_type = request.param
    store_module.ARTIFACT_ROUTES.reset()
    blob = BlobStorage(logger=logger, config_service=_config_service(TARGETS.get(storage_type, ""), storage_type))
    yield SimpleNamespace(type=storage_type, blob=blob, store=NodeArtifactStore(blob, logger))
    await close_shared_session()


@pytest.fixture(params=["neo4j", "arango"])
async def graph(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[IGraphDBProvider]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            provider = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, ARANGO_DB)
            )
            await provider.ensure_pipeline_schema()
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(provider, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        yield provider


class _Lifecycle(_World):
    """A world whose org id the storage service accepts, with real revisions."""

    def __init__(self, graph: IGraphDBProvider, target: SimpleNamespace) -> None:
        super().__init__(graph)
        self.org_id = uuid.uuid4().hex[:24]
        self.target = target
        self.writer = ArtifactWriter(target.store, logger)
        self.written: dict[tuple[str, str], Any] = {}

    def rev(self) -> str:
        return uuid.uuid4().hex[:16]

    def model(self, record_id: str, vrid: str, text: str, images: int = 0) -> Record:
        blocks = [Block(index=0, type=BlockType.TEXT, data=text)]
        blocks += [
            Block(index=i + 1, type=BlockType.IMAGE,
                  data={"uri": "data:image/png;base64," + __import__("base64").b64encode(
                      b"\x89PNG\r\n\x1a\n" + text.encode() + i.to_bytes(4, "big")).decode()})
            for i in range(images)
        ]
        return Record(
            id=record_id, org_id=self.org_id, virtual_record_id=vrid, record_name="qa.pdf",
            record_type=RecordType.FILE, external_record_id=f"ext-{record_id}", version=1, origin="UPLOAD",
            connector_name=Connectors.KNOWLEDGE_BASE, connector_id=self.connector_id,
            block_containers=BlocksContainer(blocks=blocks, block_groups=[]),
        )

    async def set_rev(self, record_id: str, rev: str) -> None:
        await self.graph.update_node(record_id, CollectionNames.RECORDS.value, {"contentRev": rev})

    async def index(self, record_id: str, vrid: str, rev: str, *, images: int = 0) -> Any:  # noqa: ANN401
        """What indexing does: the record is on ``rev``, its set is written, the pointer moved."""
        await self.set_rev(record_id, rev)
        record = self.model(record_id, vrid, f"content of {rev}", images)
        written = await self.writer.write_revision(record, self.org_id, vrid, rev, record.block_containers)
        assert written is not None
        await self.flip(record, written)
        self.written[(vrid, rev)] = written
        return written

    async def flip(self, record: Record, written: Any) -> None:  # noqa: ANN401
        await SinkOrchestrator._flip_revision_pointer(
            SimpleNamespace(graph_provider=self.graph, logger=logger), record, written,
        )

    async def pointer(self, vrid: str) -> dict[str, Any]:
        doc = await self.graph.get_document(vrid, CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value)
        assert doc is not None
        return doc

    async def listed(self, vrid: str) -> dict[str, dict[str, Any]]:
        return {r["rev"]: r for r in await self.target.store.list_revisions(self.org_id, vrid)}

    async def read_manifest(self, document_id: str) -> dict[str, Any]:
        import json

        urls = await self.target.store.read_urls(self.org_id, document_id, [MANIFEST_PATH])
        return json.loads(await self.target.store.get(urls[MANIFEST_PATH]))

    async def assert_pointer_resolves(self, vrid: str) -> None:
        """The invariant: what the mapping points at is a complete, readable revision."""
        p = await self.pointer(vrid)
        manifest = await self.read_manifest(p["manifestDocId"])
        assert manifest["rev"] == p["currentRev"]
        paths = [
            f["path"] for entry in manifest["artifacts"].values()
            for f in (entry.get("parts") or entry.get("files") or ([entry] if entry.get("path") else []))
        ]
        urls = await self.target.store.read_urls(self.org_id, p["manifestDocId"], paths)
        for path in paths:
            assert await self.target.store.get_if_present(urls[path]) is not None, path

    def on_disk(self, vrid: str, rev: str) -> bool | None:
        if self.target.type != "local" or not LOCAL_ROOT:
            return None
        return (Path(LOCAL_ROOT) / self.org_id / "PipesHub" / "record-revisions" / vrid / rev).exists()

    async def remove(self) -> None:
        for vrid in set(self.vrids):
            with contextlib.suppress(Exception):
                await self.target.blob.purge_virtual_record_documents(self.org_id, vrid)
        await super().remove()


@pytest.fixture
async def life(graph: IGraphDBProvider, target: SimpleNamespace) -> AsyncIterator[_Lifecycle]:
    w = _Lifecycle(graph, target)
    try:
        yield w
    finally:
        await w.remove()


def _sweep(life: _Lifecycle, *, page_size: int = 1, ahead_ms: int = 2 * DAY_MS, **kw: Any) -> RevisionRetentionSweep:  # noqa: ANN401
    return RevisionRetentionSweep(
        life.graph, life.target.store, logger, page_size=page_size,
        now_ms=lambda: get_epoch_timestamp_in_ms() + ahead_ms, **kw,
    )


class _Metrics:
    def __init__(self) -> None:
        self.counts: Counter[tuple[str, str]] = Counter()

    def __call__(self, kind: str, outcome: str, count: int = 1) -> None:
        self.counts[(kind, outcome)] += count


async def test_full_lifecycle_retires_exactly_the_unused_revisions(life: _Lifecycle) -> None:
    vrid = life.vrid("v")
    revs = [life.rev() for _ in range(5)]
    await life.mapping(vrid)
    a = await life.record("a", vrid, None)
    # A second record holding the vrid stays on an older revision.
    b = await life.record("b", vrid, revs[1])
    for rev in revs:
        await life.index(a, vrid, rev)
        await life.state(vrid, rev, "classify", get_epoch_timestamp_in_ms())
    await life.set_rev(b, revs[1])
    pointer = await life.pointer(vrid)
    assert (pointer["currentRev"], pointer["previousRev"]) == (revs[4], revs[3])
    before = await life.listed(vrid)
    assert set(before) == set(revs)
    old_urls = {
        rev: (await life.target.store.read_urls(life.org_id, before[rev]["documentId"], [MANIFEST_PATH]))[MANIFEST_PATH]
        for rev in revs
    }

    metrics = _Metrics()
    with patch.object(retention_module, "record_retention", metrics):
        next_key, report = await _sweep(life).run_page(life.prefix)

    gone, kept = {revs[0], revs[2]}, {revs[1], revs[3], revs[4]}
    assert (report.revisions_deleted, report.stage_states_deleted, report.failed) == (2, 3, 0)
    assert set(await life.listed(vrid)) == kept
    for rev in gone:
        if life.target.type != "local":
            # Signed straight at the vendor: the bytes themselves are gone.
            assert await life.target.store.get_if_present(old_urls[rev]) is None, rev
        assert life.on_disk(vrid, rev) in (None, False)
    for rev in kept:
        assert (await life.read_manifest(before[rev]["documentId"]))["rev"] == rev
        assert life.on_disk(vrid, rev) in (None, True)
    assert await life.revs_with_states_of(vrid, revs) == {revs[1], revs[4]}
    await life.assert_pointer_resolves(vrid)
    assert metrics.counts[("revision", "deleted")] == 2
    assert metrics.counts[("stage_state", "deleted")] == 3
    assert metrics.counts[("revision", "kept")] == 3

    # Repeating the pass changes nothing.
    _, again = await _sweep(life).run_page(life.prefix)
    assert (again.revisions_deleted, again.stage_states_deleted, again.failed) == (0, 0, 0)
    assert set(await life.listed(vrid)) == kept


async def _revs_with_states_of(self: _Lifecycle, vrid: str, revs: list[str]) -> set[str]:
    found: set[str] = set()
    for rev in revs:
        if await self.graph.stage_states_for_revision(vrid, rev):
            found.add(rev)
    return found


_Lifecycle.revs_with_states_of = _revs_with_states_of  # type: ignore[attr-defined]


async def test_trash_keeps_everything_and_restore_resumes_retention(life: _Lifecycle) -> None:
    vrid = life.vrid("t")
    revs = [life.rev() for _ in range(3)]
    await life.mapping(vrid)
    a = await life.record("a", vrid, None)
    for rev in revs:
        await life.index(a, vrid, rev)
        await life.state(vrid, rev, "classify", get_epoch_timestamp_in_ms())
    await life.graph.update_node(a, CollectionNames.RECORDS.value, {
        "isDeleted": True, "deletedAtTimestamp": get_epoch_timestamp_in_ms(),
    })

    _, trashed = await _sweep(life).run_page(life.prefix)

    assert (trashed.revisions_deleted, trashed.stage_states_deleted, trashed.skipped_trashed) == (0, 0, 1)
    assert set(await life.listed(vrid)) == set(revs)
    assert await life.revs_with_states_of(vrid, revs) == set(revs)

    await life.graph.update_node(a, CollectionNames.RECORDS.value, {"isDeleted": False})
    _, restored = await _sweep(life).run_page(life.prefix)

    assert (restored.revisions_deleted, restored.stage_states_deleted, restored.skipped_trashed) == (1, 2, 0)
    assert set(await life.listed(vrid)) == set(revs[1:])
    await life.assert_pointer_resolves(vrid)


class _TtlLeases:
    """Record leases that expire, as Redis ones do."""

    def __init__(self) -> None:
        self.held: dict[str, tuple[str, float]] = {}

    def _live(self, pool: str) -> tuple[str, float] | None:
        e = self.held.get(pool)
        return e if e and e[1] > time.monotonic() else None

    async def try_acquire(self, pool: str, owner: str, limit: int, lease_seconds: float) -> bool:
        live = self._live(pool)
        if live and live[0] != owner:
            return False
        self.held[pool] = (owner, time.monotonic() + lease_seconds)
        return True

    async def renew_many(self, leases: list[tuple[str, str]], lease_seconds: float) -> dict[tuple[str, str], bool]:
        out = {}
        for pool, owner in leases:
            live = self._live(pool)
            out[(pool, owner)] = bool(live and live[0] == owner)
            if out[(pool, owner)]:
                self.held[pool] = (owner, time.monotonic() + lease_seconds)
        return out

    async def release(self, pool: str, owner: str) -> None:
        e = self.held.get(pool)
        if e and e[0] == owner:
            del self.held[pool]


async def test_reindex_to_old_bytes_during_the_delete_waits_for_the_lease(life: _Lifecycle) -> None:
    """Main's design: a re-index needs the record lease the sweep holds across its delete."""
    vrid = life.vrid("o")
    revs = [life.rev() for _ in range(3)]
    await life.mapping(vrid)
    a = await life.record("a", vrid, None)
    for rev in revs:
        await life.index(a, vrid, rev)
    leases = _TtlLeases()
    real_delete = life.target.store.delete_revision
    refused: list[bool] = []

    async def reindex_then_delete(org_id: str, document_id: str, **kw: Any) -> int:  # noqa: ANN401
        if await leases.try_acquire(f"record:{a}", "reindex", 1, 120.0):
            await life.index(a, vrid, revs[0])
            refused.append(False)
        else:
            refused.append(True)
        return await real_delete(org_id, document_id, **kw)

    with patch.object(life.target.store, "delete_revision", side_effect=reindex_then_delete):
        _, report = await _sweep(life, leases=leases).run_page(life.prefix)

    assert refused == [True]
    assert (report.revisions_deleted, report.failed) == (1, 0)
    assert set(await life.listed(vrid)) == set(revs[1:])
    await life.assert_pointer_resolves(vrid)


async def test_reindex_after_the_sweeps_lease_lapsed_mid_delete_keeps_a_resolvable_pointer(
    life: _Lifecycle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On real storage: the sweep's lease (term scaled 120 s -> 1 s) lapses while its delete
    is in flight (a slow tree delete, or a retry after a 120 s read timeout); the re-index that then
    takes the lease re-files the old revision and flips the pointer onto it; the delete lands after."""
    monkeypatch.setenv("INDEXING_CONCURRENCY_LEASE_SECONDS", "1.0")
    vrid = life.vrid("x")
    revs = [life.rev() for _ in range(3)]
    await life.mapping(vrid)
    a = await life.record("a", vrid, None)
    for rev in revs:
        await life.index(a, vrid, rev)
    leases = _TtlLeases()
    real_delete = life.target.store.delete_revision
    events: list[str] = []

    async def reindex() -> None:
        while not await leases.try_acquire(f"record:{a}", "reindex", 1, 120.0):
            await asyncio.sleep(0.05)
        events.append("reindex took the lease")
        await life.index(a, vrid, revs[0])
        events.append("rev0 re-filed and the pointer flipped")

    async def slow_delete(org_id: str, document_id: str, **kw: Any) -> int:  # noqa: ANN401
        writer = asyncio.create_task(reindex())
        await asyncio.sleep(1.5)  # the request is still in flight when the lease lapses
        await writer
        events.append("sweep delete lands")
        return await real_delete(org_id, document_id, **kw)

    with patch.object(life.target.store, "delete_revision", side_effect=slow_delete):
        await _sweep(life, leases=leases).run_page(life.prefix)

    logger.warning("lease-lapse race: %s", events)
    assert (await life.pointer(vrid))["currentRev"] == revs[0]
    await life.assert_pointer_resolves(vrid)


async def test_the_grace_boundary_on_storage_stamps(life: _Lifecycle) -> None:
    vrid = life.vrid("g")
    revs = [life.rev() for _ in range(3)]
    await life.mapping(vrid)
    a = await life.record("a", vrid, None)
    for rev in revs:
        await life.index(a, vrid, rev)
    listed = (await life.listed(vrid))[revs[0]]
    stamp = max(listed["createdAt"], listed["updatedAt"])
    grace = 60_000

    at = RevisionRetentionSweep(life.graph, life.target.store, logger, grace_ms=grace, page_size=1,
                                now_ms=lambda: stamp + grace)
    _, kept = await at.run_page(life.prefix)
    assert kept.revisions_deleted == 0
    past = RevisionRetentionSweep(life.graph, life.target.store, logger, grace_ms=grace, page_size=1,
                                  now_ms=lambda: stamp + grace + 1)
    _, deleted = await past.run_page(life.prefix)
    assert deleted.revisions_deleted == 1
    assert set(await life.listed(vrid)) == set(revs[1:])


async def test_a_mapping_without_a_pointer_deletes_no_set_but_retires_unheld_states(life: _Lifecycle) -> None:
    vrid = life.vrid("u")
    revs = [life.rev() for _ in range(3)]
    await life.mapping(vrid)
    a = await life.record("a", vrid, None)
    for rev in revs:
        await life.set_rev(a, rev)
        record = life.model(a, vrid, rev)
        assert await life.writer.write_revision(record, life.org_id, vrid, rev, record.block_containers)
        await life.state(vrid, rev, "classify", get_epoch_timestamp_in_ms())

    _, report = await _sweep(life).run_page(life.prefix)

    assert (report.revisions_deleted, report.stage_states_deleted) == (0, 2)
    assert set(await life.listed(vrid)) == set(revs)


async def test_another_orgs_token_cannot_list_or_delete_and_says_so(life: _Lifecycle) -> None:
    vrid = life.vrid("i")
    revs = [life.rev() for _ in range(2)]
    await life.mapping(vrid)
    a = await life.record("a", vrid, None)
    for rev in revs:
        await life.index(a, vrid, rev)
    mine = (await life.listed(vrid))[revs[0]]
    other = uuid.uuid4().hex[:24]

    assert await life.target.store.list_revisions(other, vrid) == []
    answered = await life.target.store.delete_revision(other, mine["documentId"])

    assert set(await life.listed(vrid)) == set(revs)
    # Nothing was removed, so the answer must not read as a removal.
    assert not answered


async def test_a_revision_already_gone_is_not_counted_as_deleted(life: _Lifecycle) -> None:
    """A concurrent purge removed the revision after the sweep listed it."""
    vrid = life.vrid("d")
    revs = [life.rev() for _ in range(3)]
    await life.mapping(vrid)
    a = await life.record("a", vrid, None)
    for rev in revs:
        await life.index(a, vrid, rev)
    real_delete = life.target.store.delete_revision

    async def purged_meanwhile(org_id: str, document_id: str, **kw: Any) -> int | None:  # noqa: ANN401
        await real_delete(org_id, document_id)
        return await real_delete(org_id, document_id, **kw)

    metrics = _Metrics()
    with patch.object(life.target.store, "delete_revision", side_effect=purged_meanwhile), \
            patch.object(retention_module, "record_retention", metrics):
        _, report = await _sweep(life).run_page(life.prefix)

    assert report.revisions_deleted == 0
    assert metrics.counts[("revision", "deleted")] == 0
    assert metrics.counts[("revision", "gone")] == 1
    assert report.revisions_kept >= 1
    assert set(await life.listed(vrid)) == set(revs[1:])
    await life.assert_pointer_resolves(vrid)


# --- scale ---------------------------------------------------------------------------------


class _Counting:
    """Counts calls to the wrapped object's coroutine methods."""

    def __init__(self, inner: Any) -> None:  # noqa: ANN401
        self._inner = inner
        self.calls: Counter[str] = Counter()

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        attr = getattr(self._inner, name)
        if not asyncio.iscoroutinefunction(attr):
            return attr

        async def counted(*a: Any, **kw: Any) -> Any:  # noqa: ANN401
            self.calls[name] += 1
            return await attr(*a, **kw)

        return counted


async def _scale_world(life: _Lifecycle, n: int, with_sets: int) -> list[str]:
    vrids = [life.vrid(f"{i:06d}") for i in range(n)]
    now = get_epoch_timestamp_in_ms()
    rows = [
        {"id": v, "orgId": life.org_id, "virtualRecordId": v, "documentId": f"doc-{v}",
         "currentRev": "c0c0c0c0c0c0c0c0", "previousRev": "b0b0b0b0b0b0b0b0", "manifestDocId": f"m-{v}"}
        for v in vrids
    ]
    for start in range(0, n, 500):
        await life.graph.batch_upsert_nodes(rows[start:start + 500], CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value)
    life.vrids += vrids
    # One unheld stage state per vrid, so every page also retires states.
    sem = asyncio.Semaphore(16)

    async def state(v: str) -> None:
        async with sem:
            await life.state(v, "a0a0a0a0a0a0a0a0", "classify", now)

    await asyncio.gather(*(state(v) for v in vrids))
    # The first `with_sets` vrids also have three filed revisions, the oldest retirable.
    headers, endpoint, _ = await life.target.blob._get_auth_and_config(life.org_id)
    async with aiohttp.ClientSession() as session:
        async def presign(v: str, rev: str) -> None:
            async with sem, session.post(
                f"{endpoint}/api/v1/document/internal/artifacts/presign", headers=headers,
                json={"virtualRecordId": v, "rev": rev, "files": [{"path": "record.json", "contentType": "application/json"}]},
            ) as r:
                assert r.status == 200, await r.text()

        await asyncio.gather(*(
            presign(v, rev) for v in vrids[:with_sets]
            for rev in ("a0a0a0a0a0a0a0a0", "b0b0b0b0b0b0b0b0", "c0c0c0c0c0c0c0c0")
        ))
    return vrids


async def test_scale_pages_stay_bounded(life: _Lifecycle, record_property: Any) -> None:  # noqa: ANN401
    if life.target.type != "local":
        pytest.skip(
            f"not applicable on {life.target.type}: scale is measured once, on the local target;"
            " the vendor does not change the call pattern"
        )
    with_sets = 500
    vrids = await _scale_world(life, SCALE, with_sets)
    graph = _Counting(life.graph)
    store = _Counting(life.target.store)
    sweep = RevisionRetentionSweep(graph, store, logger, page_size=200,  # type: ignore[arg-type]
                                   now_ms=lambda: get_epoch_timestamp_in_ms() + 2 * DAY_MS)
    timings: list[float] = []
    totals = retention_module.RetentionReport()
    key: str | None = life.prefix
    for _ in range(SCALE // 200):
        started = time.perf_counter()
        key, report = await sweep.run_page(key)
        timings.append(time.perf_counter() - started)
        totals.add(report)
    p95 = statistics.quantiles(timings, n=20)[18]
    record_property("page_seconds", {"p50": statistics.median(timings), "p95": p95, "max": max(timings)})
    record_property("graph_calls", dict(graph.calls))
    record_property("store_calls", dict(store.calls))
    logger.warning("scale %s: p50=%.3fs p95=%.3fs max=%.3fs graph=%s store=%s", type(life.graph).__name__,
                   statistics.median(timings), p95, max(timings), dict(graph.calls), dict(store.calls))

    assert totals.scanned == SCALE and totals.failed == 0
    assert (totals.revisions_deleted, totals.stage_states_deleted) == (with_sets, SCALE)
    pages = SCALE // 200
    # Per page: one pointer read, one trash read, one stage read and one stage delete;
    # per vrid with a retirable set: one mapping and one holders re-read.
    assert graph.calls["get_revision_pointers"] == pages
    assert graph.calls["get_virtual_record_ids_in_use"] == pages
    assert graph.calls["stage_states_unheld_revisions"] == pages
    # Main (0b9ae86e1): one stage-state delete and one holders read per vrid with work, under its leases.
    assert graph.calls["stage_states_delete_unheld_revisions"] == SCALE
    assert graph.calls["get_virtual_record_holders"] == SCALE
    assert graph.calls["get_document"] == with_sets
    assert store.calls["list_revisions"] == SCALE
    assert store.calls["delete_revision"] == with_sets
    # Four pages run per 60 s recovery tick, after the rest of recovery: they must
    # leave most of the tick to it.
    assert p95 < 5.0, timings
    assert sorted(set(vrids))[0] > life.prefix


async def test_stage_state_queries_use_indexes(life: _Lifecycle) -> None:
    if life.target.type != "local":
        pytest.skip(f"not applicable on {life.target.type}: graph-only, measured once on the local target")
    vrids = [life.vrid(f"q{i}") for i in range(3)]
    queries: list[tuple[str, dict[str, Any]]] = []
    g = life.graph
    if isinstance(g, Neo4jProvider):
        real = g._stage_cypher

        async def capture(query: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
            queries.append((query, parameters))
            return await real(query, parameters)

        with patch.object(g, "_stage_cypher", side_effect=capture):
            await g.stage_states_unheld_revisions(vrids, updated_before_ms=1)
            await g.get_revision_pointers(life.prefix, 10)
        client = g.client
        state_label = collection_to_label(CollectionNames.STAGE_STATES.value)
        for query, parameters in queries:
            async with client.driver.session(database=client.database) as session:
                summary = await (await session.run("EXPLAIN " + query, parameters)).consume()
            ops: list[str] = []

            def walk(plan: Any, ops: list[str] = ops) -> None:  # noqa: ANN401
                ops.append(f"{plan['operatorType']} {plan.get('args', {}).get('Details', '')}")
                for child in plan.get("children", []):
                    walk(child, ops)

            walk(summary.plan)
            scans = [o for o in ops if o.startswith(("AllNodesScan", "NodeByLabelScan"))]
            assert not scans, (query, ops)
            assert not [o for o in ops if state_label in o and "Scan" in o and "Index" not in o], ops
    else:
        assert isinstance(g, ArangoHTTPProvider)
        real = g.http_client.execute_aql

        async def capture(query: str, bind_vars: dict[str, Any] | None = None, **kw: Any) -> Any:  # noqa: ANN401
            queries.append((query, dict(bind_vars or {})))
            return await real(query, bind_vars, **kw)

        with patch.object(g.http_client, "execute_aql", side_effect=capture):
            await g.stage_states_unheld_revisions(vrids, updated_before_ms=1)
            await g.get_revision_pointers(life.prefix, 10)
        async with aiohttp.ClientSession(auth=aiohttp.BasicAuth("root", ARANGO_PASSWORD)) as session:
            for query, bind_vars in queries:
                async with session.post(f"{ARANGO_URL}/_db/{ARANGO_DB}/_api/explain",
                                        json={"query": query, "bindVars": bind_vars}) as r:
                    plan = await r.json()
                assert r.status == 200, plan
                full = [n for n in plan["plan"]["nodes"] if n["type"] == "EnumerateCollectionNode"]
                assert not full, (query, [(n["collection"], n.get("filter")) for n in full])
