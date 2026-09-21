"""The `[n]` marker contract. Systems write them, grading strips them, metrics
count them — and all three used to carry their own regex."""

from __future__ import annotations

from benchmarks.harness.citation_markers import MARKER, cited_indices, strip_markers


class TestCitedIndices:
    def test_plain_markers(self) -> None:
        assert cited_indices("a [1] b [2]") == (1, 2)

    def test_markdown_link_form(self) -> None:
        """What a model actually writes when it links a citation. Two of the
        three old copies did not know about this form."""
        assert cited_indices("a [1](http://x/y#z) b") == (1,)

    def test_first_seen_order_deduplicated(self) -> None:
        assert cited_indices("[2] [1] [2]") == (2, 1)

    def test_none_and_empty(self) -> None:
        assert cited_indices(None) == ()
        assert cited_indices("") == ()


class TestStripMarkers:
    def test_removes_the_whole_markdown_link(self) -> None:
        """Stripping only `[1]` would leave a bare `(http://x)` in the claim
        text handed to the judge."""
        assert strip_markers("The answer is X [1](http://x).") == "The answer is X ."

    def test_removes_plain_markers(self) -> None:
        assert strip_markers("X [1] Y [2]") == "X  Y"

    def test_leaves_unmarked_text_alone(self) -> None:
        assert strip_markers("no citations here") == "no citations here"


def test_one_definition_only() -> None:
    """A regression guard: three layers reading the same contract must not
    re-declare it."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "benchmarks" / "harness"
    owners = [
        p for p in root.rglob("*.py")
        if "__pycache__" not in str(p) and "\\[(\\d+)\\]" in p.read_text()
    ]
    assert [p.name for p in owners] == ["citation_markers.py"], [str(p) for p in owners]


def test_marker_is_exported() -> None:
    assert MARKER.pattern.startswith(r"\[(\d+)\]")
