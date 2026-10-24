"""``ArtifactRecordBackend`` and the read modes: what an artifact set reads back as, and when
the stored record is served instead."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import Connectors
from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockType,
    CitationMetadata,
    GroupType,
    ImageMetadata,
    SemanticMetadata,
)
from app.models.entities import Record, RecordType
from app.modules.artifacts import reader as reader_module
from app.modules.artifacts import record_backend as backend_module
from app.modules.artifacts.models import MANIFEST_PATH, Manifest
from app.modules.artifacts.reader import (
    ArtifactFirstRecordArtifactReader,
    ConfiguredRecordArtifactReader,
    LegacyRecordArtifactReader,
    ShadowRecordArtifactReader,
    first_difference,
    record_artifact_reader,
)
from app.modules.artifacts.record_backend import (
    ArtifactRecordBackend,
    ArtifactUnavailable,
    classification_sidecar_path,
)
from app.modules.artifacts.store import NodeArtifactStore
from app.modules.artifacts.writer import ArtifactWriter, canonical_json
from app.modules.pipeline.stages.record_io import with_classification
from app.modules.transformers.blob_storage import (
    SHARED_CONTENT_FIELDS,
    STORED_RECORD_FIELDS,
    BlobStorage,
    clean_stored_record,
)
from app.modules.transformers.transformer import TransformContext
from tests.unit.modules.artifacts.fake_storage import FakeStorage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

ORG = "org-1"
VRID = "vr-1"
REV = "0123456789abcdef"
PNG = b"\x89PNG\r\n\x1a\n" + b"pixels" * 50
JPEG = b"\xff\xd8\xff" + b"jpeg" * 40


def _uri(media_type: str, raw: bytes) -> str:
    return f"data:{media_type};base64,{base64.b64encode(raw).decode()}"


def _record(
    *, semantic: SemanticMetadata | None = None, code_facts_version: str | None = None,
    record_id: str = "rec-1", name: str = "deck.pdf", connector_id: str = "conn-1",
) -> Record:
    blocks = [
        Block(id="b0", index=0, type=BlockType.TEXT, data="Revenue grew.", parent_index=0,
              citation_metadata=CitationMetadata(page_number=1)),
        Block(id="b1", index=1, type=BlockType.TEXT, data="Costs fell.", parent_index=0),
        Block(id="i2", index=2, type=BlockType.IMAGE, data={"uri": _uri("image/png", PNG)},
              image_metadata=ImageMetadata(description="chart")),
        # As the image parser writes a .jpg: the URI must come back spelled the same.
        Block(id="i3", index=3, type=BlockType.IMAGE, data={"uri": _uri("image/jpg", JPEG)}),
        Block(id="i4", index=4, type=BlockType.IMAGE, data={"uri": _uri("image/png", PNG)}),
        # Unpadded base64 would not re-encode the same, so it stays inline.
        Block(id="i5", index=5, type=BlockType.IMAGE, data={"uri": _uri("image/png", JPEG).rstrip("=")}),
    ]
    groups = [BlockGroup(index=0, type=GroupType.LIST, children=BlockGroupChildren.from_indices([0, 1]))]
    return Record(
        id=record_id, record_name=name, record_type=RecordType.FILE, external_record_id=f"ext-{record_id}",
        version=1, origin="UPLOAD", connector_name=Connectors.KNOWLEDGE_BASE, connector_id=connector_id,
        org_id=ORG, mime_type="application/pdf", virtual_record_id=VRID, semantic_metadata=semantic,
        block_containers=BlocksContainer(blocks=blocks, block_groups=groups, code_facts_version=code_facts_version),
    )


def _as_stored(record: Record) -> dict[str, Any]:
    """What the stored record reads back as: ``BlobStorage.apply``'s dict through JSON."""
    return json.loads(json.dumps(clean_stored_record(record.model_dump(mode="json", exclude_none=True))))


class _Mappings:
    """The mapping lookups ``BlobStorage`` answers, from a dict."""

    def __init__(self, mappings: dict[str, dict[str, Any]]) -> None:
        self.mappings = mappings
        self.single = AsyncMock(side_effect=lambda vrid: self.mappings.get(vrid))

    async def get_document_ids_by_virtual_record_ids(self, vrids: list[str]) -> dict[str, dict[str, Any]]:
        return {v: self.mappings[v] for v in vrids if v in self.mappings}

    async def get_document_id_by_virtual_record_id(self, vrid: str) -> dict[str, Any] | None:
        return await self.single(vrid)


@pytest.fixture
async def storage() -> AsyncIterator[FakeStorage]:
    fake = FakeStorage()
    await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture
def outcomes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(backend_module, "record_artifact_read", seen.append)
    return seen


@pytest.fixture
def shadow_outcomes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str | None]]:
    seen: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        reader_module, "record_artifact_shadow_read", lambda outcome, reason=None: seen.append((outcome, reason)),
    )
    return seen


async def _write(storage: FakeStorage, record: Record) -> dict[str, Any]:
    """Store ``record``'s revision and answer the mapping entry pointing at it."""
    store = NodeArtifactStore(storage.blob_storage())
    written = await ArtifactWriter(store).write_revision(
        record, ORG, VRID, REV, record.block_containers, semantic_metadata=record.semantic_metadata,
    )
    assert written is not None
    return {"record_doc_id": "doc-1", "currentRev": REV, "manifestDocId": written.manifest_ref.document_id}


async def _write_sidecar(storage: FakeStorage, payload: dict[str, Any]) -> None:
    store = NodeArtifactStore(storage.blob_storage())
    path = classification_sidecar_path()
    plan = await store.presign_revision(ORG, VRID, REV, [(path, "application/json")])
    await store.put(plan.files[path], canonical_json(payload), "application/json")


def _backend(storage: FakeStorage, mappings: _Mappings) -> ArtifactRecordBackend:
    return ArtifactRecordBackend(mappings, NodeArtifactStore(storage.blob_storage()))  # type: ignore[arg-type]


def _key(path: str) -> str:
    return f"{FakeStorage.root(VRID, REV)}/{path}"


class TestArtifactRecordBackend:
    async def test_reads_back_what_the_stored_record_holds(self, storage: FakeStorage, outcomes: list[str]) -> None:
        record = _record(semantic=SemanticMetadata(summary="Results", categories=["Finance"]), code_facts_version="3")
        lookup = await _write(storage, record)

        read = await _backend(storage, _Mappings({})).read_record(VRID, ORG, lookup=lookup)

        assert read == _as_stored(record)
        assert outcomes == ["served"]

    async def test_images_come_back_byte_identical(self, storage: FakeStorage) -> None:
        record = _record()
        lookup = await _write(storage, record)
        stored_blocks = storage.puts[_key("blocks/part-0000.jsonl")].data.decode()
        assert '"asset_media_type":"image/jpg"' in stored_blocks
        assert stored_blocks.count('"uri"') == 1

        read = await _backend(storage, _Mappings({})).read_record(VRID, ORG, lookup=lookup)

        assert read is not None
        expected = [b.data["uri"] for b in record.block_containers.blocks if b.type == BlockType.IMAGE]
        assert [b["data"]["uri"] for b in read["block_containers"]["blocks"][2:]] == expected

    async def test_resolves_the_mapping_itself_without_a_lookup(self, storage: FakeStorage) -> None:
        record = _record()
        mappings = _Mappings({VRID: await _write(storage, record)})

        assert await _backend(storage, mappings).read_record(VRID, ORG) == _as_stored(record)
        mappings.single.assert_awaited_once_with(VRID)

    @pytest.mark.parametrize("lookup", [None, {"record_doc_id": "doc-1"}, {"record_doc_id": "d", "currentRev": REV}])
    async def test_no_pointer_is_unavailable(self, storage: FakeStorage, outcomes: list[str], lookup: Any) -> None:  # noqa: ANN401
        backend = _backend(storage, _Mappings({VRID: lookup} if lookup else {}))

        assert await backend.read_record(VRID, ORG, lookup=lookup) is None
        assert outcomes == ["no_pointer"]

    async def test_a_revision_without_its_manifest_is_unavailable(
        self, storage: FakeStorage, outcomes: list[str],
    ) -> None:
        lookup = await _write(storage, _record())
        del storage.puts[_key(MANIFEST_PATH)]

        with pytest.raises(ArtifactUnavailable) as raised:
            await _backend(storage, _Mappings({})).read(VRID, ORG, lookup=lookup)
        assert raised.value.outcome == "missing"
        assert outcomes == ["missing"]

    async def test_a_deleted_set_is_unavailable(self, storage: FakeStorage, outcomes: list[str]) -> None:
        lookup = {**await _write(storage, _record()), "manifestDocId": "gone"}

        assert await _backend(storage, _Mappings({})).read_record(VRID, ORG, lookup=lookup) is None
        assert outcomes == ["missing"]

    @pytest.mark.parametrize("path", ["blocks/part-0000.jsonl", "record.json", "groups.json"])
    async def test_a_file_unlike_its_manifest_entry_is_unavailable(
        self, storage: FakeStorage, outcomes: list[str], path: str,
    ) -> None:
        lookup = await _write(storage, _record())
        stored = storage.puts[_key(path)]
        stored.data = stored.data.replace(b"e", b"E", 1)

        assert await _backend(storage, _Mappings({})).read_record(VRID, ORG, lookup=lookup) is None
        assert outcomes == ["integrity"]

    async def test_a_tampered_image_is_unavailable(self, storage: FakeStorage, outcomes: list[str]) -> None:
        lookup = await _write(storage, _record())
        image = next(k for k in storage.puts if "/images/" in k)
        storage.puts[image].data += b"x"

        assert await _backend(storage, _Mappings({})).read_record(VRID, ORG, lookup=lookup) is None
        assert outcomes == ["integrity"]

    async def test_another_writer_version_is_left_to_the_stored_record(
        self, storage: FakeStorage, outcomes: list[str],
    ) -> None:
        lookup = await _write(storage, _record())
        stored = storage.puts[_key(MANIFEST_PATH)]
        manifest = Manifest.model_validate_json(stored.data)
        stored.data = canonical_json({**manifest.model_dump(mode="json", exclude_none=True), "writerVersion": 1})

        assert await _backend(storage, _Mappings({})).read_record(VRID, ORG, lookup=lookup) is None
        assert outcomes == ["invalid"]

    async def test_a_manifest_of_another_revision_is_unavailable(
        self, storage: FakeStorage, outcomes: list[str],
    ) -> None:
        lookup = {**await _write(storage, _record()), "currentRev": "fedcba9876543210"}
        # The pointer names a revision the document does not hold.
        storage.roots[lookup["manifestDocId"]] = FakeStorage.root(VRID, REV)

        assert await _backend(storage, _Mappings({})).read_record(VRID, ORG, lookup=lookup) is None
        assert outcomes == ["invalid"]

    async def test_over_the_decode_limit_is_left_to_the_stored_record(
        self, storage: FakeStorage, outcomes: list[str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        lookup = await _write(storage, _record())
        monkeypatch.setenv("PIPESHUB_RECORD_MAX_DECODED_BYTES", "100")

        assert await _backend(storage, _Mappings({})).read_record(VRID, ORG, lookup=lookup) is None
        assert outcomes == ["too_large"]

    async def test_a_storage_failure_is_unavailable_without_the_url(
        self, storage: FakeStorage, outcomes: list[str], caplog: pytest.LogCaptureFixture,
    ) -> None:
        lookup = await _write(storage, _record())
        backend = _backend(storage, _Mappings({}))
        backend._store.read_urls = AsyncMock(side_effect=RuntimeError("https://signed?sig=secret"))  # type: ignore[method-assign]

        with caplog.at_level(logging.DEBUG, logger=backend_module.__name__):
            assert await backend.read_record(VRID, ORG, lookup=lookup) is None
        assert outcomes == ["failed"]
        logged = [r.getMessage() for r in caplog.records if r.name == backend_module.__name__]
        assert logged and not any("secret" in line for line in logged)


class TestSharedContent:
    """Content another record stored first keeps that owner's identity in the stored copy;
    the artifact set must read back as that copy, not as the re-indexing record."""

    @staticmethod
    def _blob(owner_copy: dict[str, Any], stored: list[dict[str, Any]]) -> BlobStorage:
        bs = BlobStorage(logger=logging.getLogger("test-shared"), config_service=AsyncMock(), graph_provider=AsyncMock())
        bs.graph_provider.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-x", "rec-y"])
        bs._build_hierarchical_storage_path = AsyncMock(return_value=f"records/{VRID}")  # type: ignore[method-assign]
        bs.get_document_id_by_virtual_record_id = AsyncMock(return_value={"record_doc_id": "doc-1"})  # type: ignore[method-assign]
        bs.get_record_from_storage = AsyncMock(return_value=owner_copy)  # type: ignore[method-assign]

        async def update(org_id: str, doc_id: str, record_dict: dict[str, Any], vrid: str) -> tuple[str, int]:
            stored.append(json.loads(json.dumps(record_dict)))
            return doc_id, 1

        bs.update_record_buffer = update  # type: ignore[method-assign]
        bs._get_current_document_path = AsyncMock(return_value=f"{ORG}/PipesHub/records/{VRID}")  # type: ignore[method-assign]
        bs.store_virtual_record_mapping = AsyncMock(return_value=True)  # type: ignore[method-assign]
        return bs

    async def test_the_set_reads_back_as_the_owners_stored_copy(self, storage: FakeStorage) -> None:
        owner_copy = _as_stored(_record(record_id="rec-x", name="X budget.pdf", connector_id="conn-x"))
        reindexing = _record(record_id="rec-y", name="Y plan.pdf", connector_id="conn-y")
        stored: list[dict[str, Any]] = []
        ctx = TransformContext(record=reindexing)

        await self._blob(owner_copy, stored).apply(ctx)

        assert stored[0]["id"] == "rec-x" and stored[0]["record_name"] == "X budget.pdf"
        assert ctx.settings[STORED_RECORD_FIELDS]["id"] == "rec-x"
        assert "block_containers" not in ctx.settings[STORED_RECORD_FIELDS]
        written = await ArtifactWriter(NodeArtifactStore(storage.blob_storage())).write_revision(
            reindexing, ORG, VRID, REV, reindexing.block_containers,
            stored_record_fields=ctx.settings[STORED_RECORD_FIELDS],
        )
        assert written is not None
        lookup = {"currentRev": REV, "manifestDocId": written.manifest_ref.document_id}

        read = await _backend(storage, _Mappings({})).read_record(VRID, ORG, lookup=lookup)

        assert read == stored[0]
        assert "Y plan.pdf" not in json.dumps(read)

    async def test_a_record_alone_on_its_content_stores_its_own_identity(self) -> None:
        record = _record()
        bs = self._blob({}, stored := [])
        bs.graph_provider.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1"])
        ctx = TransformContext(record=record)

        await bs.apply(ctx)

        assert stored[0] == _as_stored(record)
        assert ctx.settings[STORED_RECORD_FIELDS] == {
            k: v for k, v in stored[0].items() if k not in SHARED_CONTENT_FIELDS
        }


class TestClassification:
    async def test_the_sidecar_wins_over_metadata_json(self, storage: FakeStorage) -> None:
        index_time = SemanticMetadata(named_entities={"organizations": ["Northwind"]})
        lookup = await _write(storage, _record(semantic=index_time))
        later = SemanticMetadata(summary="Vendor review", categories=["Procurement"], named_entities={"x": 1})
        await _write_sidecar(storage, later.model_dump(mode="json", exclude={"named_entities"}))

        read = await _backend(storage, _Mappings({})).read_record(VRID, ORG, lookup=lookup)

        assert read is not None
        # What the classify stage writes into the stored record.
        assert read["semantic_metadata"] == with_classification(
            index_time.model_dump(mode="json", exclude_none=True), later.model_dump(mode="json"),
        )
        assert read["semantic_metadata"]["named_entities"] == {"organizations": ["Northwind"]}

    async def test_the_sidecar_alone_without_index_time_metadata(self, storage: FakeStorage) -> None:
        lookup = await _write(storage, _record())
        await _write_sidecar(storage, {"summary": "Later", "categories": []})

        read = await _backend(storage, _Mappings({})).read_record(VRID, ORG, lookup=lookup)

        assert read is not None
        assert read["semantic_metadata"] == {"summary": "Later", "categories": [], "named_entities": None}

    @pytest.mark.parametrize("missing_status", [404, 403])
    async def test_metadata_json_without_a_sidecar(self, storage: FakeStorage, missing_status: int) -> None:
        storage.missing_status = missing_status
        record = _record(semantic=SemanticMetadata(summary="At index time"))
        lookup = await _write(storage, record)

        read = await _backend(storage, _Mappings({})).read_record(VRID, ORG, lookup=lookup)

        assert read == _as_stored(record)
        assert read is not None and read["semantic_metadata"]["summary"] == "At index time"

    async def test_neither_gives_no_semantic_metadata(self, storage: FakeStorage) -> None:
        lookup = await _write(storage, _record())

        read = await _backend(storage, _Mappings({})).read_record(VRID, ORG, lookup=lookup)

        assert read is not None and "semantic_metadata" not in read

    async def test_a_listed_metadata_json_that_is_gone_is_unavailable(
        self, storage: FakeStorage, outcomes: list[str],
    ) -> None:
        lookup = await _write(storage, _record(semantic=SemanticMetadata(summary="x")))
        del storage.puts[_key("metadata.json")]

        assert await _backend(storage, _Mappings({})).read_record(VRID, ORG, lookup=lookup) is None
        assert outcomes == ["missing"]


class _Legacy:
    def __init__(self, record: dict[str, Any] | None = None, error: Exception | None = None, delay: float = 0) -> None:
        self.record, self.error, self.delay = record, error, delay
        self.calls: list[str] = []

    async def read_record(self, vrid: str, org_id: str, *, lookup: dict[str, Any] | None = None) -> dict[str, Any] | None:
        self.calls.append(vrid)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.record

    async def lookup_many(self, vrids: list[str]) -> dict[str, dict[str, Any]]:
        return {v: {"record_doc_id": f"doc-{v}"} for v in vrids}


class _Artifact:
    def __init__(self, record: dict[str, Any] | None = None, *, delay: float = 0, outcome: str | None = None) -> None:
        self.record, self.delay, self.outcome = record, delay, outcome
        self.cancelled = False

    async def read(self, vrid: str, org_id: str, *, lookup: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.outcome is not None:
            raise ArtifactUnavailable(self.outcome)
        assert self.record is not None
        return self.record


STORED = {"record_name": "a.pdf", "block_containers": {"blocks": [{"index": 0, "data": "x"}], "block_groups": []}}


def _shadow(legacy: _Legacy, artifact: _Artifact, **kw: Any) -> ShadowRecordArtifactReader:  # noqa: ANN401
    return ShadowRecordArtifactReader(legacy, artifact, **kw)  # type: ignore[arg-type]


class TestShadowReader:
    async def test_serves_the_stored_record_and_counts_a_match(
        self, shadow_outcomes: list[tuple[str, str | None]],
    ) -> None:
        served = await _shadow(_Legacy(STORED), _Artifact(json.loads(json.dumps(STORED)))).read_record(VRID, ORG)

        assert served is STORED
        assert shadow_outcomes == [("match", None)]

    async def test_a_mismatch_is_counted_by_reason_and_logged_without_content(
        self, shadow_outcomes: list[tuple[str, str | None]], caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(reader_module, "_MISMATCH_LOG", reader_module._MismatchLog(10.0))
        differs = {**STORED, "block_containers": {"blocks": [{"index": 0, "data": "secret text"}], "block_groups": []}}

        with caplog.at_level(logging.WARNING, logger=reader_module.__name__):
            served = await _shadow(_Legacy(STORED), _Artifact(differs)).read_record(VRID, ORG)
            await _shadow(_Legacy(STORED), _Artifact(differs)).read_record(VRID, ORG)

        assert served is STORED
        assert shadow_outcomes == [("mismatch", "block"), ("mismatch", "block")]
        assert "blocks[0].data" in caplog.text and VRID in caplog.text
        logged = [r.getMessage() for r in caplog.records if r.name == reader_module.__name__]
        assert len(logged) == 1 and "secret text" not in logged[0]

    async def test_an_unavailable_artifact_set_is_counted(self, shadow_outcomes: list[tuple[str, str | None]]) -> None:
        served = await _shadow(_Legacy(STORED), _Artifact(outcome="no_pointer")).read_record(VRID, ORG)

        assert served is STORED
        assert shadow_outcomes == [("unavailable", None)]

    async def test_a_slow_artifact_read_is_cut_off_at_the_grace(
        self, shadow_outcomes: list[tuple[str, str | None]],
    ) -> None:
        artifact = _Artifact(STORED, delay=5)
        loop = asyncio.get_running_loop()
        start = loop.time()

        served = await _shadow(_Legacy(STORED), artifact, grace_seconds=0.05).read_record(VRID, ORG)
        await asyncio.sleep(0)

        assert served is STORED
        assert loop.time() - start < 1
        assert shadow_outcomes == [("timeout", None)]
        assert artifact.cancelled

    async def test_the_artifact_read_runs_beside_the_stored_record_read(
        self, shadow_outcomes: list[tuple[str, str | None]],
    ) -> None:
        loop = asyncio.get_running_loop()
        start = loop.time()

        await _shadow(_Legacy(STORED, delay=0.2), _Artifact(STORED, delay=0.2), grace_seconds=0.05).read_record(VRID, ORG)

        assert loop.time() - start < 0.35
        assert shadow_outcomes == [("match", None)]

    async def test_a_failing_stored_record_read_still_fails(
        self, shadow_outcomes: list[tuple[str, str | None]],
    ) -> None:
        artifact = _Artifact(STORED, delay=5)

        with pytest.raises(RuntimeError, match="storage down"):
            await _shadow(_Legacy(error=RuntimeError("storage down"), delay=0.01), artifact).read_record(VRID, ORG)
        await asyncio.sleep(0)

        assert shadow_outcomes == [("legacy_failed", None)]
        assert artifact.cancelled

    async def test_a_broken_comparison_never_fails_the_served_read(
        self, shadow_outcomes: list[tuple[str, str | None]], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(reader_module, "first_difference", MagicMock(side_effect=RecursionError))

        assert await _shadow(_Legacy(STORED), _Artifact(STORED)).read_record(VRID, ORG) is STORED

    async def test_past_the_in_flight_cap_reads_are_served_unshadowed(
        self, shadow_outcomes: list[tuple[str, str | None]],
    ) -> None:
        slots = threading.BoundedSemaphore(1)
        slots.acquire()
        artifact = MagicMock()

        served = await _shadow(_Legacy(STORED), artifact, slots=slots).read_record(VRID, ORG)

        assert served is STORED
        assert shadow_outcomes == [("skipped", None)]
        artifact.read.assert_not_called()

    async def test_slots_are_released(self, shadow_outcomes: list[tuple[str, str | None]]) -> None:
        slots = threading.BoundedSemaphore(1)
        reader = _shadow(_Legacy(STORED), _Artifact(STORED), slots=slots)

        for _ in range(3):
            await reader.read_record(VRID, ORG)

        assert [o for o, _ in shadow_outcomes] == ["match"] * 3


class TestArtifactFirstReader:
    async def test_serves_the_artifact_set(self) -> None:
        legacy = _Legacy(STORED)
        artifact_record = {"record_name": "from artifacts"}

        reader = ArtifactFirstRecordArtifactReader(legacy, _Artifact(artifact_record))  # type: ignore[arg-type]

        assert await reader.read_record(VRID, ORG) == artifact_record
        assert legacy.calls == []

    async def test_falls_back_per_record(self) -> None:
        legacy = _Legacy(STORED)

        class Partial(_Artifact):
            async def read(self, vrid: str, org_id: str, *, lookup: dict[str, Any] | None = None) -> dict[str, Any]:
                if vrid == "vr-old":
                    raise ArtifactUnavailable("no_pointer")
                return {"record_name": vrid}

        reader = ArtifactFirstRecordArtifactReader(legacy, Partial())  # type: ignore[arg-type]

        assert await reader.read_record("vr-new", ORG) == {"record_name": "vr-new"}
        assert await reader.read_record("vr-old", ORG) is STORED
        assert legacy.calls == ["vr-old"]

    async def test_lookups_are_the_stored_record_mapping(self) -> None:
        reader = ArtifactFirstRecordArtifactReader(_Legacy(), _Artifact())  # type: ignore[arg-type]

        assert await reader.lookup_many(["a"]) == {"a": {"record_doc_id": "doc-a"}}


def _blob(flags: dict[str, bool]) -> MagicMock:
    blob = MagicMock()
    blob.config_service.get_config = AsyncMock(return_value={"featureFlags": flags})
    return blob


class TestModes:
    @pytest.mark.parametrize(("flags", "expected"), [
        ({}, LegacyRecordArtifactReader),
        ({"ENABLE_ARTIFACT_READ_SHADOW": True}, ShadowRecordArtifactReader),
        ({"ENABLE_ARTIFACT_READ": True}, ArtifactFirstRecordArtifactReader),
        ({"ENABLE_ARTIFACT_READ": True, "ENABLE_ARTIFACT_READ_SHADOW": True}, ArtifactFirstRecordArtifactReader),
        ({"ENABLE_ARTIFACT_READ": False, "ENABLE_ARTIFACT_READ_SHADOW": False}, LegacyRecordArtifactReader),
    ])
    async def test_the_flags_pick_the_reader_per_read(
        self, flags: dict[str, bool], expected: type, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        picked: list[object] = []
        original = ConfiguredRecordArtifactReader.reader

        def spy(self: ConfiguredRecordArtifactReader, mode: Any) -> Any:  # noqa: ANN401
            chosen = original(self, mode)
            picked.append(chosen)
            chosen.read_record = AsyncMock(return_value={"ok": True})  # type: ignore[method-assign]
            return chosen

        monkeypatch.setattr(ConfiguredRecordArtifactReader, "reader", spy)

        assert await record_artifact_reader(_blob(flags)).read_record(VRID, ORG) == {"ok": True}
        assert type(picked[0]) is expected

    async def test_lookups_never_change_with_the_mode(self) -> None:
        blob = _blob({"ENABLE_ARTIFACT_READ": True})
        blob.get_document_ids_by_virtual_record_ids = AsyncMock(return_value={"vr-1": {"record_doc_id": "d"}})

        assert await record_artifact_reader(blob).lookup_many(["vr-1"]) == {"vr-1": {"record_doc_id": "d"}}

    async def test_a_flag_flip_takes_effect_on_the_next_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        blob = _blob({})
        blob.get_record_from_storage = AsyncMock(return_value=STORED)
        reader = record_artifact_reader(blob)
        assert await reader.read_record(VRID, ORG) is STORED

        blob.config_service.get_config = AsyncMock(return_value={"featureFlags": {"ENABLE_ARTIFACT_READ": True}})
        monkeypatch.setattr(ArtifactRecordBackend, "read", AsyncMock(return_value={"record_name": "artifact"}))

        assert await reader.read_record(VRID, ORG) == {"record_name": "artifact"}


class TestFirstDifference:
    @pytest.mark.parametrize(("candidate", "expected"), [
        (STORED, None),
        ({**STORED, "record_name": "b.pdf"}, ("record_field", "record_name")),
        ({**STORED, "semantic_metadata": {"summary": "s"}}, ("semantic_metadata", "semantic_metadata")),
        ({**STORED, "block_containers": {**STORED["block_containers"], "code_facts_version": "2"}},
         ("container_field", "block_containers.code_facts_version")),
        ({**STORED, "block_containers": {"blocks": [], "block_groups": []}}, ("block_count", "blocks: 1 != 0")),
        ({**STORED, "block_containers": {"blocks": [{"index": 0, "data": "y"}], "block_groups": []}},
         ("block", "blocks[0].data")),
        ({**STORED, "block_containers": {"blocks": [{"index": 0, "data": "x"}], "block_groups": [{"index": 0}]}},
         ("group_count", "block_groups: 0 != 1")),
    ])
    def test_names_the_first_differing_part(self, candidate: dict[str, Any], expected: Any) -> None:  # noqa: ANN401
        assert first_difference(STORED, candidate) == expected

    def test_an_artifact_set_without_a_stored_record(self) -> None:
        assert first_difference(None, STORED) == ("legacy_missing", "")


def test_mapping_lookups_carry_the_revision_pointer() -> None:
    shaped = BlobStorage._shape_document_lookup(
        {"documentId": "d", "fileSizeBytes": 3, "currentRev": REV, "previousRev": "x", "manifestDocId": "m"}
    )

    assert shaped == {"record_doc_id": "d", "fileSizeBytes": 3, "currentRev": REV, "manifestDocId": "m"}
    assert BlobStorage._shape_document_lookup({"documentId": "d"}) == {"record_doc_id": "d", "fileSizeBytes": None}
