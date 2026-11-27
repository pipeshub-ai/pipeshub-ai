"""UX-03 (C3.71), backend half: what a held call would change and cost, read from its arguments and its tool's
declaration (never fetched)."""

from __future__ import annotations

from harness_core.effects.preview import (
    MAX_DIFF_CHARS,
    EffectPreview,
    declared_cost,
    edit_diff,
    preview_effect,
)
from harness_core.tools.base import Tag


def test_ux03_an_edit_reads_as_a_unified_diff_of_its_target() -> None:
    diff = edit_diff({"path": "docs/plan.md", "old_string": "ship in Q3\nowner: Ana", "new_string": "ship in Q4\nowner: Ana"})
    assert diff is not None
    text, cut = diff
    assert not cut
    assert text.splitlines()[:2] == ["--- a/docs/plan.md", "+++ b/docs/plan.md"]
    assert "-ship in Q3" in text and "+ship in Q4" in text and " owner: Ana" in text


def test_ux03_no_edit_no_diff() -> None:
    assert edit_diff({"title": "T", "content": "a whole new page"}) is None
    assert edit_diff({"old_string": "same", "new_string": "same"}) is None
    assert edit_diff({"old_string": 3, "new_string": "x"}) is None


def test_ux03_a_long_diff_is_cut_and_says_so() -> None:
    text, cut = edit_diff({"old_str": "a\n" * 5000, "new_str": "b\n" * 5000}) or ("", False)
    assert cut and len(text) == MAX_DIFF_CHARS


def test_ux03_a_cost_is_only_what_the_tool_declares() -> None:
    assert declared_cost([Tag("category", "write"), Tag("cost_usd", "0.04")]) == 0.04
    assert declared_cost([Tag("cost_usd", "lots")]) is None
    assert declared_cost([Tag("cost_usd", "-1")]) is None
    assert declared_cost([]) is None


def test_ux03_the_wire_form_leaves_out_what_is_unknown() -> None:
    assert preview_effect({"title": "x"}) is None
    assert EffectPreview(cost_usd=0.04).wire() == {"costEstimate": {"usd": 0.04, "basis": "declared"}}
    both = preview_effect({"old_text": "a", "new_text": "b"}, [Tag("cost_usd", "0.5")])
    assert both is not None
    wire = both.wire()
    assert set(wire) == {"diff", "costEstimate"} and "diffTruncated" not in wire
