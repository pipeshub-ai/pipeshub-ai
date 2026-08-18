"""Tests for ``app.modules.agents.enumeration.answer``.

Two properties carry the whole design and are easy to regress:

* every listed record is cited, and the citation is minted against the record
  landing URL — the only form the resolver can match for a whole record;
* the total is the size of the permission-filtered set, so a corpus larger than
  the listing cap is still counted correctly.
"""
from __future__ import annotations

import pytest

from app.modules.agents.enumeration.answer import (
    build_enumeration_answer,
    compose_text,
    record_landing_url,
)


class FakeMapper:
    """Mirrors CitationRefMapper: idempotent, same URL always the same ref."""

    def __init__(self) -> None:
        self._seen: dict[str, str] = {}

    def get_or_create_ref(self, url: str) -> str:
        if url not in self._seen:
            self._seen[url] = f"ref{len(self._seen) + 1}"
        return self._seen[url]


def _record(rid: str, name: str, **extra) -> dict:
    return {"id": rid, "record_name": name, **extra}


def _lookup_from(records: dict[str, dict]):
    async def lookup(vrid: str, record_id: str):
        return records.get(vrid)
    return lookup


class TestComposeText:
    def test_empty_corpus_cites_nothing(self) -> None:
        text = compose_text([], 0)
        assert "no documents" in text
        assert "[source]" not in text

    def test_singular_noun(self) -> None:
        assert compose_text([("a", "ref1", None)], 1).startswith("There are 1 document:")

    def test_remainder_is_reported_when_listing_is_capped(self) -> None:
        text = compose_text([("a", "ref1", None)], 500)
        assert "There are 500 documents:" in text
        assert "499 more not listed above" in text


class TestBuildEnumerationAnswer:
    async def test_every_listed_record_is_cited(self) -> None:
        recs = {"v1": _record("r1", "alpha", summary="First."), "v2": _record("r2", "beta")}
        result = await build_enumeration_answer(
            accessible={"v1": "r1", "v2": "r2"}, record_lookup=_lookup_from(recs),
            ref_mapper=FakeMapper(), org_id="o1",
        )
        assert result.total == 2
        assert result.text.count("[source](ref") == 2

    async def test_citation_uses_the_record_landing_url(self) -> None:
        """A connector's external webUrl matches neither resolver path, so the
        ref must be minted against /record/{id} even when one is present."""
        recs = {"v1": _record("r1", "drive doc", webUrl="https://drive.google.com/file/d/xyz")}
        mapper = FakeMapper()
        result = await build_enumeration_answer(
            accessible={"v1": "r1"}, record_lookup=_lookup_from(recs),
            ref_mapper=mapper, org_id="o1",
        )
        assert record_landing_url("r1") in mapper._seen
        assert "https://drive.google.com" not in mapper._seen
        # The resolver indexes on block_web_url; the external link stays in
        # metadata for the chip target.
        doc = result.final_results[0]
        assert doc["block_web_url"] == "/record/r1"
        assert doc["metadata"]["webUrl"] == "https://drive.google.com/file/d/xyz"

    async def test_citation_metadata_carries_every_required_field(self) -> None:
        """A citation missing any of these fails schema validation on save and
        takes the whole conversation with it."""
        recs = {"v1": _record("r1", "alpha")}
        result = await build_enumeration_answer(
            accessible={"v1": "r1"}, record_lookup=_lookup_from(recs),
            ref_mapper=FakeMapper(), org_id="o1",
        )
        meta = result.final_results[0]["metadata"]
        for field in ("orgId", "mimeType", "recordId", "recordName", "origin"):
            assert meta.get(field), f"{field} is required by the citation schema"

    async def test_unresolvable_records_are_not_listed_but_are_still_counted(self) -> None:
        """The total is what the permission filter returned. Dropping a record
        that failed to hydrate would silently under-count the corpus."""
        recs = {"v1": _record("r1", "alpha")}
        result = await build_enumeration_answer(
            accessible={"v1": "r1", "v2": "r2"}, record_lookup=_lookup_from(recs),
            ref_mapper=FakeMapper(), org_id="o1",
        )
        assert result.total == 2
        assert result.listed == 1
        assert result.text.count("[source](ref") == 1

    async def test_listing_is_capped_but_the_total_is_exact(self) -> None:
        recs = {f"v{i}": _record(f"r{i}", f"doc {i}") for i in range(10)}
        result = await build_enumeration_answer(
            accessible={f"v{i}": f"r{i}" for i in range(10)},
            record_lookup=_lookup_from(recs), ref_mapper=FakeMapper(),
            org_id="o1", limit=3,
        )
        assert result.total == 10
        assert result.listed == 3
        assert "7 more not listed above" in result.text

    async def test_order_is_stable_across_calls(self) -> None:
        """A map's iteration order is not a promise, and an answer that changes
        between identical asks is indistinguishable from a wrong one."""
        recs = {f"v{i}": _record(f"r{i}", f"doc {i}") for i in range(5)}
        accessible = {f"v{i}": f"r{i}" for i in range(5)}
        first = await build_enumeration_answer(
            accessible=accessible, record_lookup=_lookup_from(recs),
            ref_mapper=FakeMapper(), org_id="o1")
        second = await build_enumeration_answer(
            accessible=dict(reversed(list(accessible.items()))),
            record_lookup=_lookup_from(recs), ref_mapper=FakeMapper(), org_id="o1")
        assert first.text == second.text

    async def test_no_access_produces_no_citations(self) -> None:
        result = await build_enumeration_answer(
            accessible={}, record_lookup=_lookup_from({}),
            ref_mapper=FakeMapper(), org_id="o1",
        )
        assert result.is_empty and result.total == 0
        assert "[source]" not in result.text
