"""Golden diff: ids are renamed by first appearance, everything else must match."""

from __future__ import annotations

import uuid

from tests.perf.parse_bench import golden


def _payload(ids: list[str]) -> dict:
    return {
        "blocks": [{"id": ids[0], "parent_id": ids[1], "data": "text"}],
        "block_groups": [{"id": ids[1], "children": [ids[0]]}],
    }


def test_fresh_uuids_give_the_same_digest() -> None:
    a = _payload([str(uuid.uuid4()), str(uuid.uuid4())])
    b = _payload([str(uuid.uuid4()), str(uuid.uuid4())])
    assert golden.digest(a) == golden.digest(b)


def test_changed_text_changes_the_digest() -> None:
    a = _payload([str(uuid.uuid4()), str(uuid.uuid4())])
    b = _payload([str(uuid.uuid4()), str(uuid.uuid4())])
    b["blocks"][0]["data"] = "other"
    assert golden.digest(a) != golden.digest(b)


def test_changed_reference_structure_changes_the_digest() -> None:
    one, two = str(uuid.uuid4()), str(uuid.uuid4())
    swapped = _payload([one, two])
    swapped["block_groups"][0]["children"] = [two]
    assert golden.digest(_payload([one, two])) != golden.digest(swapped)


def test_a_uuid_in_the_document_text_is_content_not_an_id() -> None:
    a = _payload([str(uuid.uuid4()), str(uuid.uuid4())])
    b = _payload([str(uuid.uuid4()), str(uuid.uuid4())])
    a["blocks"][0]["data"] = "550e8400-e29b-41d4-a716-446655440000"
    b["blocks"][0]["data"] = "123e4567-e89b-12d3-a456-426614174000"
    assert golden.digest(a) != golden.digest(b)
    c = _payload([str(uuid.uuid4()), str(uuid.uuid4())])
    c["blocks"][0]["data"] = "see 550e8400-e29b-41d4-a716-446655440000 here"
    d = _payload([str(uuid.uuid4()), str(uuid.uuid4())])
    d["blocks"][0]["data"] = "see 123e4567-e89b-12d3-a456-426614174000 here"
    assert golden.digest(c) != golden.digest(d)


def test_key_order_is_irrelevant() -> None:
    assert golden.digest({"a": 1, "b": 2}) == golden.digest({"b": 2, "a": 1})


def test_non_ascii_is_kept() -> None:
    assert golden.canonicalize({"t": "café"}) == '{"t": "café"}'


class TestCompare:
    def _entry(self, sha: str, blocks: int = 3) -> dict:
        return {"sha256": sha, "blocks": blocks, "groups": 1}

    def test_identical_is_empty(self) -> None:
        g = {"a.pdf": self._entry("x"), "b.pdf": {"error": "PARSE_FAILED"}}
        assert golden.compare(g, dict(g)) == []

    def test_hash_difference_names_the_file_and_counts(self) -> None:
        diff = golden.compare({"a.pdf": self._entry("x", 3)}, {"a.pdf": self._entry("y", 4)})
        assert diff == ["a.pdf: blocks 3 -> 4, groups 1 -> 1, hash differs"]

    def test_missing_and_extra_files_are_reported(self) -> None:
        diff = golden.compare({"a.pdf": self._entry("x")}, {"b.pdf": self._entry("x")})
        assert diff == ["a.pdf: missing from this run", "b.pdf: not in the golden file"]

    def test_a_new_error_is_a_difference(self) -> None:
        diff = golden.compare({"a.pdf": self._entry("x")}, {"a.pdf": {"error": "PARSE_FAILED"}})
        assert len(diff) == 1
        assert "a.pdf" in diff[0]

    def test_summarize_counts(self) -> None:
        out = golden.summarize({"x": 1}, 5, 2)
        assert (out["blocks"], out["groups"]) == (5, 2)
        assert len(out["sha256"]) == 64

    def test_a_subset_run_ignores_files_it_did_not_parse(self) -> None:
        g = {"a.pdf": self._entry("x"), "b.pdf": self._entry("y")}
        assert golden.compare(g, {"a.pdf": self._entry("x")}, subset=True) == []
        assert golden.compare(g, {"a.pdf": self._entry("x")}) == ["b.pdf: missing from this run"]

    def test_a_subset_run_still_catches_a_changed_file(self) -> None:
        g = {"a.pdf": self._entry("x"), "b.pdf": self._entry("y")}
        assert golden.compare(g, {"a.pdf": self._entry("z")}, subset=True)
