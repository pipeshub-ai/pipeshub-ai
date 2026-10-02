"""The merge prompt: pairwise items, no scores, braces-safe, bounded summary."""

import json

from app.modules.entity_resolution.models import (
    SUBCATEGORY_2,
    TOPIC,
    ExtractedName,
    MergeDecisions,
    WinnerCandidate,
)
from app.modules.entity_resolution.prompt import build_prompt


def _name(index, kind, raw) -> ExtractedName:
    return ExtractedName(index=index, kind=kind, raw=raw, display=raw.strip(), normalized=raw.strip().casefold())


def _items_block(prompt: str) -> list[dict]:
    start = prompt.index("# Items\n") + len("# Items\n")
    end = prompt.index("\n\n# Output")
    return json.loads(prompt[start:end])


def test_items_carry_kind_and_candidates_without_scores() -> None:
    items = [
        _name(0, TOPIC, "Bug bash testing session"),
        _name(1, SUBCATEGORY_2, "Integration Testing"),
        _name(2, TOPIC, "Release checklist"),
    ]
    candidates = {
        0: (WinnerCandidate("k-bug", "Bug bash testing", ("bbt",)),),
        1: (WinnerCandidate("k-manual", "Manual Testing"),),
        2: (),
    }
    prompt = build_prompt("Notes from the bug bash", items, candidates)
    rendered = _items_block(prompt)
    assert rendered[0] == {
        "i": 0, "name": "Bug bash testing session", "kind": "topic",
        "matches": [{"id": "k-bug", "name": "Bug bash testing", "aliases": ["bbt"]}],
    }
    assert rendered[1]["kind"] == "subcategory level 2"
    assert rendered[2]["matches"] == []
    assert "score" not in prompt


def test_candidate_order_is_shuffled_but_reproducible() -> None:
    """KG-12: a model favours the first option it sees (arXiv 2405.16884),
    so the best-ranked candidate is not always shown first; the same input
    always gives the same order, so a run can be reproduced."""
    offered = tuple(WinnerCandidate(f"k{i}", f"Release {i}") for i in range(3))
    orders = set()
    for raw in ("Release a", "Release b", "Release c", "Release d", "Release e", "Release f"):
        rendered = _items_block(build_prompt(None, [_name(0, TOPIC, raw)], {0: offered}))
        ids = tuple(m["id"] for m in rendered[0]["matches"])
        assert sorted(ids) == ["k0", "k1", "k2"]
        again = _items_block(build_prompt(None, [_name(0, TOPIC, raw)], {0: offered}))
        assert tuple(m["id"] for m in again[0]["matches"]) == ids
        orders.add(ids)
    assert len(orders) > 1


def test_braces_in_names_and_summary_survive() -> None:
    items = [_name(0, TOPIC, "report_{final} v2")]
    prompt = build_prompt("Summary with {curly} braces", items, {0: ()})
    assert "report_{final} v2" in prompt
    assert "Summary with {curly} braces" in prompt


def test_summary_is_truncated_and_missing_summary_is_labelled() -> None:
    items = [_name(0, TOPIC, "x y")]
    long_prompt = build_prompt("s" * 5000, items, {0: ()})
    assert "s" * 1200 in long_prompt
    assert "s" * 1300 not in long_prompt
    assert "(no summary available)" in build_prompt(None, items, {0: ()})


def test_decision_schema_accepts_minimal_and_full_answers() -> None:
    parsed = MergeDecisions.model_validate(
        {"decisions": [{"i": 0, "same": False}, {"i": 1, "same": True, "target": "k-1"}]}
    )
    assert parsed.decisions[0].target == ""
    assert parsed.decisions[0].same_as_item == -1
    assert parsed.decisions[0].canonical_name == ""
    assert parsed.decisions[1].target == "k-1"
    assert MergeDecisions.model_validate({}).decisions == []
