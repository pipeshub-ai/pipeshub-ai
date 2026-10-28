"""Search hits resolve to the same chat context and citations through every reader.

Each golden case is a stored record and the search hits that point into it.
The baseline serves the record straight from the fixture; every backend in
``READERS`` is built from the same records and must produce identical
flattened results, LLM message content and citations.

The artifact readers store each record with the real ``ArtifactWriter`` over
HTTP into ``FakeStorage``, point the mapping at the revision, and read it back.
A case's ``artifacts.index_time_semantic_metadata`` is what indexing stored as
``metadata.json``; its classification then arrives as the classify stage's
sidecar, as it does when classification runs after indexing.
"""

from __future__ import annotations

import copy
import inspect
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.modules.artifacts.reader import RecordArtifactReader, record_artifact_reader
from app.modules.artifacts.record_backend import ArtifactRecordBackend
from app.modules.artifacts.store import NodeArtifactStore
from app.modules.artifacts.writer import ArtifactWriter, canonical_json
from app.modules.pipeline.stages.record_io import NAMED_ENTITIES_SECTION
from app.modules.transformers.blob_storage import clean_stored_record
from app.utils.chat_helpers import (
    CitationRefMapper,
    build_message_content_array,
    get_flattened_results,
)
from app.utils.citations import normalize_citations_and_chunks
from tests.unit.modules.artifacts.fake_storage import FakeStorage

GOLDEN = Path(__file__).parent / "golden"
CASES = sorted(p.stem for p in GOLDEN.glob("*.json"))
ORG = "org-1"
FRONTEND = "https://app.test"
REV = "0123456789abcdef"


def _load(case: str) -> dict[str, Any]:
    return json.loads((GOLDEN / f"{case}.json").read_text())


GENERATION = "gen-parity"
RUN = f"{GENERATION}:00112233445566778899aabbccddeeff"


class _Config:
    def __init__(self, flags: dict[str, bool] | None = None) -> None:
        self._flags = flags or {}

    async def get_config(self, key: str, default: Any = None, **_: Any) -> Any:  # noqa: ANN401
        if key == "/services/platform/settings":
            return {"featureFlags": self._flags, "artifactReadGeneration": GENERATION}
        return {"frontend": {"publicEndpoint": FRONTEND}}


class _StoredRecords:
    """``BlobStorage`` stand-in holding each record as the legacy stored document."""

    def __init__(self, records: dict[str, dict[str, Any]]) -> None:
        self._records = records
        # Keyed by document id, as storage is: a wrong id from a lookup must not find the record.
        self._documents = {f"doc-{vrid}": record for vrid, record in records.items()}

    async def get_document_ids_by_virtual_record_ids(self, vrids: list[str]) -> dict[str, dict[str, Any]]:
        return {v: {"record_doc_id": f"doc-{v}"} for v in vrids if v in self._records}

    async def get_record_from_storage(
        self, virtual_record_id: str, org_id: str, lookup_result: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if lookup_result is None:
            lookup_result = (await self.get_document_ids_by_virtual_record_ids([virtual_record_id])).get(virtual_record_id)
        record = self._documents.get(lookup_result["record_doc_id"]) if lookup_result else None
        return copy.deepcopy(record) if record is not None else None


class _GoldenReader:
    """The baseline: the fixture record itself, with no storage in between."""

    def __init__(self, records: dict[str, dict[str, Any]]) -> None:
        self._records = records

    async def read_record(
        self, virtual_record_id: str, org_id: str, *, lookup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        record = self._records.get(virtual_record_id)
        return copy.deepcopy(record) if record is not None else None

    async def lookup_many(self, virtual_record_ids: list[str]) -> dict[str, dict[str, Any]]:
        return {}


class _QueryServices:
    """What the chat path needs besides the reader. It has no record download,
    so a reader that is bypassed fails the test instead of passing silently."""

    config_service = _Config()

    async def get_reconciliation_metadata(self, vrid: str, org_id: str) -> None:
        return None


class _Dumped:
    """A stored-record dict that ``build_revision`` dumps as it dumps the model it came from."""

    def __init__(self, value: dict[str, Any]) -> None:
        self._value = value

    def model_dump(self, *, exclude: set[str] | None = None, exclude_none: bool = False, **_: Any) -> dict[str, Any]:  # noqa: ANN401
        return {
            k: copy.deepcopy(v) for k, v in self._value.items()
            if k not in (exclude or set()) and not (exclude_none and v is None)
        }


class _Containers(_Dumped):
    def __init__(self, value: dict[str, Any]) -> None:
        super().__init__(value)
        self.blocks = [_Dumped(b) for b in value.get("blocks", [])]
        self.block_groups = [_Dumped(g) for g in value.get("block_groups", [])]


_STORAGES: list[FakeStorage] = []


@pytest.fixture(autouse=True)
async def _stop_storages() -> AsyncIterator[None]:
    yield
    while _STORAGES:
        await _STORAGES.pop().stop()


async def _artifact_sets(records: dict[str, dict[str, Any]], cases: dict[str, dict[str, Any]]) -> tuple[FakeStorage, dict[str, dict[str, Any]]]:
    """Each record stored as an artifact set, and the mapping entries pointing at them."""
    storage = FakeStorage()
    await storage.start()
    _STORAGES.append(storage)
    store = NodeArtifactStore(storage.blob_storage())
    writer = ArtifactWriter(store)
    mappings: dict[str, dict[str, Any]] = {}
    for vrid, record in records.items():
        stored = cases[vrid].get("artifacts", {})
        later = "index_time_semantic_metadata" in stored
        semantic = stored["index_time_semantic_metadata"] if later else record.get("semantic_metadata")
        written = await writer.write_revision(
            _Dumped({k: v for k, v in record.items() if k != "block_containers"}),  # type: ignore[arg-type]
            ORG, vrid, REV, _Containers(record["block_containers"]),  # type: ignore[arg-type]
            semantic_metadata=semantic, stored_run=RUN,
        )
        assert written is not None
        mappings[vrid] = {
            "record_doc_id": f"doc-{vrid}", "currentRev": REV, "storedRev": REV,
            "storedRun": RUN, "currentRun": RUN, "manifestDocId": written.manifest_ref.document_id,
        }
        if later:
            # As a copy-on-write classification or label repair files it, and names it on the mapping.
            path = f"enrich/classification.{vrid.replace('-', '')[:32].ljust(32, '0')}@4.json"
            plan = await store.presign_revision(ORG, vrid, REV, [(path, "application/json")])
            classification = {k: v for k, v in record["semantic_metadata"].items() if k != NAMED_ENTITIES_SECTION}
            envelope = {"storedRun": RUN, "storedMeta": path, "classification": classification}
            await store.put(plan.files[path], canonical_json(envelope), "application/json")
            mappings[vrid]["storedMeta"] = path
    return storage, mappings


def _cases(records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_vrid = {_load(c)["record"]["virtual_record_id"]: _load(c) for c in CASES}
    return {vrid: by_vrid[vrid] for vrid in records}


async def _artifact_backend(records: dict[str, dict[str, Any]]) -> RecordArtifactReader:
    storage, mappings = await _artifact_sets(records, _cases(records))
    lookups = _StoredRecords({})
    lookups.config_service = _Config({"ENABLE_ARTIFACT_READ": True})  # type: ignore[attr-defined]
    lookups.get_document_ids_by_virtual_record_ids = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda vrids: {v: mappings[v] for v in vrids if v in mappings}
    )
    lookups.get_document_id_by_virtual_record_id = AsyncMock(side_effect=mappings.get)  # type: ignore[attr-defined]
    return ArtifactRecordBackend(lookups, NodeArtifactStore(storage.blob_storage()))  # type: ignore[arg-type]

@pytest.fixture(autouse=True)
def _fresh_membership(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every indexing member follows the stored-write protocol, so artifact-first reads are not held."""
    import time

    from app.services.messaging import membership

    async def fresh(_config_service: object) -> list[dict[str, object]]:
        return [{"checkedAt": int(time.time() * 1000), "untagged": []}]

    monkeypatch.setattr(membership, "read_entries_from_config", fresh)
    membership._READERS.clear()


async def _artifact_first(records: dict[str, dict[str, Any]]) -> RecordArtifactReader:
    """Through the factory with ``ENABLE_ARTIFACT_READ`` on, and no stored record to fall back to."""
    storage, mappings = await _artifact_sets(records, _cases(records))
    blob = storage.blob_storage()
    blob.config_service = _Config({"ENABLE_ARTIFACT_READ": True})
    blob.get_document_ids_by_virtual_record_ids = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda vrids: {v: mappings[v] for v in vrids if v in mappings}
    )
    blob.get_document_id_by_virtual_record_id = AsyncMock(side_effect=mappings.get)  # type: ignore[method-assign]
    blob.get_record_from_storage = AsyncMock(side_effect=AssertionError("fell back to the stored record"))  # type: ignore[method-assign]
    return record_artifact_reader(blob)


ReaderFactory = Callable[[dict[str, dict[str, Any]]], RecordArtifactReader | Awaitable[RecordArtifactReader]]

READERS: list[Any] = [
    pytest.param(lambda records: record_artifact_reader(_StoredRecords(records)), id="legacy"),
    pytest.param(_artifact_backend, id="artifact"),
    pytest.param(_artifact_first, id="artifact-first"),
]


def _graph_record(case: str, record: dict[str, Any]) -> dict[str, Any]:
    key = f"rec-{case}"
    return {
        "_key": key, "recordName": record["record_name"], "recordType": "FILE", "origin": "CONNECTOR",
        "connectorName": "DRIVE", "connectorId": "conn-1", "version": 1,
        "webUrl": f"https://drive.test/{key}", "mimeType": record["mime_type"],
    }


async def _answer_turn(
    case: str, reader: RecordArtifactReader, monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    golden = _load(case)
    record = golden["record"]
    vrid = record["virtual_record_id"]
    monkeypatch.setattr("app.utils.chat_helpers.record_artifact_reader", lambda _blob_store: reader)

    vr_map: dict[str, Any] = {}
    flattened = await get_flattened_results(
        copy.deepcopy(golden["hits"]), _QueryServices(), ORG, golden["multimodal"], vr_map,
        {vrid: _graph_record(case, record)},
    )
    contents, ref_mapper = build_message_content_array(
        flattened, vr_map, is_multimodal_llm=golden["multimodal"], ref_mapper=CitationRefMapper(),
    )
    answer = " ".join(f"Fact {n} [source]({ref})." for n, ref in enumerate(ref_mapper.ref_to_url, 1))
    cited_answer, citations = normalize_citations_and_chunks(
        answer, flattened, records=list(vr_map.values()),
        ref_to_url=ref_mapper.ref_to_url, virtual_record_id_to_result=vr_map,
    )
    return {
        "flattened": flattened,
        "records": vr_map,
        "contents": contents,
        "ref_to_url": ref_mapper.ref_to_url,
        "answer": cited_answer,
        "citations": citations,
    }


@pytest.mark.parametrize("case", CASES)
async def test_baseline_cites_the_stored_content(case: str, monkeypatch: pytest.MonkeyPatch) -> None:
    golden = _load(case)
    turn = await _answer_turn(case, _GoldenReader({golden["record"]["virtual_record_id"]: golden["record"]}), monkeypatch)
    assert turn["flattened"]
    assert turn["citations"]


@pytest.mark.parametrize("make_reader", READERS)
@pytest.mark.parametrize("case", CASES)
async def test_reader_matches_the_stored_record(
    case: str, make_reader: ReaderFactory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    golden = _load(case)
    records = {golden["record"]["virtual_record_id"]: golden["record"]}

    expected = await _answer_turn(case, _GoldenReader(records), monkeypatch)
    reader = make_reader(copy.deepcopy(records))
    if inspect.isawaitable(reader):
        reader = await reader
    actual = await _answer_turn(case, reader, monkeypatch)

    for part in expected:
        assert actual[part] == expected[part], part


def test_corpus_covers_the_stored_shapes() -> None:
    def blocks(case: str) -> list[dict[str, Any]]:
        return _load(case)["record"]["block_containers"]["blocks"]

    def groups(case: str) -> list[dict[str, Any]]:
        return _load(case)["record"]["block_containers"]["block_groups"]

    assert any(g["type"] != "table" for c in CASES for g in groups(c))
    assert any(
        g["type"] == "table" and g["data"].get("table_summary") and g.get("table_metadata", {}).get("num_of_cells")
        for c in CASES for g in groups(c)
    )
    assert any(b["type"] == "table_row" and b["data"].get("row_natural_language_text") for c in CASES for b in blocks(c))
    assert any(b["type"] == "image" and b["data"].get("uri") for c in CASES for b in blocks(c))
    assert any(_load(c)["record"].get("semantic_metadata") for c in CASES)
    assert any("index_time_semantic_metadata" in _load(c).get("artifacts", {}) for c in CASES)


@pytest.mark.parametrize("case", CASES)
def test_cases_hold_what_a_stored_record_can(case: str) -> None:
    """The stored record never keeps an empty value at the top of the record, a block or a group."""
    record = _load(case)["record"]
    assert clean_stored_record(copy.deepcopy(record)) == record


@pytest.mark.parametrize("case", CASES)
def test_a_later_classification_is_what_the_stage_writes(case: str) -> None:
    """The stored record's classification is the sidecar's with the index-time named entities kept."""
    golden = _load(case)
    index_time = golden.get("artifacts", {}).get("index_time_semantic_metadata")
    if index_time is None:
        return
    stored = golden["record"]["semantic_metadata"]
    assert stored[NAMED_ENTITIES_SECTION] == index_time.get(NAMED_ENTITIES_SECTION)
