"""Search hits resolve to the same chat context and citations through every reader.

Each golden case is a stored record and the search hits that point into it.
The baseline serves the record straight from the fixture; every backend in
``READERS`` is built from the same records and must produce identical
flattened results, LLM message content and citations.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.modules.artifacts.reader import RecordArtifactReader, record_artifact_reader
from app.utils.chat_helpers import (
    CitationRefMapper,
    build_message_content_array,
    get_flattened_results,
)
from app.utils.citations import normalize_citations_and_chunks

GOLDEN = Path(__file__).parent / "golden"
CASES = sorted(p.stem for p in GOLDEN.glob("*.json"))
ORG = "org-1"
FRONTEND = "https://app.test"


def _load(case: str) -> dict[str, Any]:
    return json.loads((GOLDEN / f"{case}.json").read_text())


class _Config:
    async def get_config(self, key: str, default: Any = None, **_: Any) -> Any:  # noqa: ANN401
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


ReaderFactory = Callable[[dict[str, dict[str, Any]]], RecordArtifactReader]

READERS: list[Any] = [
    pytest.param(lambda records: record_artifact_reader(_StoredRecords(records)), id="legacy"),
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
    actual = await _answer_turn(case, make_reader(copy.deepcopy(records)), monkeypatch)

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
