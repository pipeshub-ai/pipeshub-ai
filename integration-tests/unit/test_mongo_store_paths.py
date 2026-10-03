"""Unit tests for how helper/mongo_store.py finds a record's stored content (no live services).

Indexing files a record's processed content under the record's place in its
collection (``records/<kbId>/<folder>/<name>``), so the probes must:

* match a path and the folders below it, never a sibling whose name only starts the same;
* find the folder from the ``record_<virtualRecordId>`` document inside one collection,
  or in the flat ``records/<virtualRecordId>`` folder indexing falls back to, and refuse
  to guess when there is none or more than one.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from helper import mongo_store
from helper.mongo_store import MongoStoreProbe

pytestmark = pytest.mark.unit

ORG = "6abffdc23081615fc6a004d6"
VRID = "2873dacb-8607-441f-85de-a7680db63bf7"
KB = f"{ORG}/PipesHub/records/31214236-ed6d-4ea1-af7c-0b18d04e58ee"


@pytest.mark.parametrize(
    ("path", "matches"),
    [
        (f"{KB}/policy-433e53", True),
        (f"{KB}/policy-433e53/6abffe413081615fc6a009f6", True),
        (f"{KB}/policy-433e53-2", False),
        (f"{KB}/policy-433e5", False),
        (f"{KB}/folder/policy-433e53", False),
    ],
)
def test_under_matches_the_path_and_its_folders_only(path: str, matches: bool) -> None:
    assert bool(re.search(mongo_store._under(f"{KB}/policy-433e53"), path)) is matches


@pytest.mark.parametrize(
    ("path", "inside"),
    [
        (f"{KB}/policy-433e53", True),
        (f"{KB}/policy-433e53/6abffe413081615fc6a009f6", True),
        (f"{KB}/policy-433e53-2", False),
        (f"{KB}/policy-433e5", False),
        (f"{KB}/folder/policy-433e53", False),
    ],
)
def test_is_within_agrees_with_the_mongodb_match(path: str, inside: bool) -> None:
    folder = f"{KB}/policy-433e53"
    assert mongo_store.is_within(path, folder) is inside
    assert mongo_store.is_within(path, f"{folder}/") is inside
    assert bool(re.search(mongo_store._under(folder), path)) is inside


def test_under_ignores_a_trailing_slash() -> None:
    assert re.search(mongo_store._under(f"{KB}/"), f"{KB}/policy-433e53")


def test_under_quotes_regex_characters_in_names() -> None:
    pattern = mongo_store._under(f"{KB}/Q3 (draft).v2")
    assert re.search(pattern, f"{KB}/Q3 (draft).v2")
    assert not re.search(pattern, f"{KB}/Q3 draft-v2")


class _Documents:
    """Answers ``distinct`` with one list per call, the last one repeating."""

    def __init__(self, *answers: list[str]) -> None:
        self.answers = list(answers)
        self.queries: list[dict[str, Any]] = []

    def distinct(self, field: str, query: dict[str, Any]) -> list[str]:
        assert field == "documentPath"
        self.queries.append(query)
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def _probe(monkeypatch: pytest.MonkeyPatch, documents: _Documents) -> MongoStoreProbe:
    monkeypatch.setattr(mongo_store, "_POLL_INTERVAL", 0)
    probe = MongoStoreProbe(uri="mongodb://unused", db_name="unused")
    monkeypatch.setattr(probe, "_documents", lambda: documents)
    return probe


@pytest.mark.asyncio
async def test_envelope_path_reads_the_records_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    documents = _Documents([f"{KB}/policy-433e53"])
    path = await _probe(monkeypatch, documents).envelope_path(ORG, VRID, within=KB)

    assert path == f"{KB}/policy-433e53"
    query = documents.queries[0]
    assert query["documentName"] == f"record_{VRID}"
    assert query["$or"] == [
        {"documentPath": {"$regex": mongo_store._under(KB)}},
        {"documentPath": {"$regex": mongo_store._under(f"{ORG}/PipesHub/records/{VRID}")}},
    ]
    # orgId is a BSON ObjectId in Mongo; a string-only filter would match nothing.
    assert any(not isinstance(form, str) for form in query["orgId"]["$in"])


@pytest.mark.asyncio
async def test_envelope_path_accepts_the_flat_fallback_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    flat = f"{ORG}/PipesHub/records/{VRID}"
    assert await _probe(monkeypatch, _Documents([flat])).envelope_path(ORG, VRID, within=KB) == flat


@pytest.mark.asyncio
async def test_envelope_path_waits_for_indexing_to_store_it(monkeypatch: pytest.MonkeyPatch) -> None:
    documents = _Documents([], [], [f"{KB}/policy-433e53"])
    path = await _probe(monkeypatch, documents).envelope_path(ORG, VRID, within=KB, timeout=5)

    assert path == f"{KB}/policy-433e53"
    assert len(documents.queries) == 3


@pytest.mark.asyncio
async def test_envelope_path_fails_when_nothing_was_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(AssertionError, match="never stored the record's content"):
        await _probe(monkeypatch, _Documents([])).envelope_path(ORG, VRID, within=KB, timeout=0)


@pytest.mark.asyncio
async def test_envelope_path_refuses_to_pick_between_two_folders(monkeypatch: pytest.MonkeyPatch) -> None:
    documents = _Documents([f"{KB}/a/policy-433e53", f"{KB}/b/policy-433e53"])
    with pytest.raises(AssertionError, match="2 folders"):
        await _probe(monkeypatch, documents).envelope_path(ORG, VRID, within=KB, timeout=0)
