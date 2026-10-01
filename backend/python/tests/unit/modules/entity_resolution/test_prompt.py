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


def test_items_carry_kind_and_winner_without_scores() -> None:
    items = [
        _name(0, TOPIC, "Bug bash testing session"),
        _name(1, SUBCATEGORY_2, "Integration Testing"),
        _name(2, TOPIC, "Release checklist"),
    ]
    winners = {
        0: WinnerCandidate("k-bug", "Bug bash testing", ("bbt",)),
        1: WinnerCandidate("k-manual", "Manual Testing"),
        2: None,
    }
    prompt = build_prompt("Notes from the bug bash", items, winners)
    rendered = _items_block(prompt)
    assert rendered[0] == {
        "i": 0, "name": "Bug bash testing session", "kind": "topic",
        "match": {"id": "k-bug", "name": "Bug bash testing", "aliases": ["bbt"]},
    }
    assert rendered[1]["kind"] == "subcategory level 2"
    assert rendered[2]["match"] is None
    assert "score" not in prompt


def test_braces_in_names_and_summary_survive() -> None:
    items = [_name(0, TOPIC, "report_{final} v2")]
    prompt = build_prompt("Summary with {curly} braces", items, {0: None})
    assert "report_{final} v2" in prompt
    assert "Summary with {curly} braces" in prompt


def test_summary_is_truncated_and_missing_summary_is_labelled() -> None:
    items = [_name(0, TOPIC, "x y")]
    long_prompt = build_prompt("s" * 5000, items, {0: None})
    assert "s" * 1200 in long_prompt
    assert "s" * 1300 not in long_prompt
    assert "(no summary available)" in build_prompt(None, items, {0: None})


def test_decision_schema_accepts_minimal_and_full_answers() -> None:
    parsed = MergeDecisions.model_validate(
        {"decisions": [{"i": 0, "same": False}, {"i": 1, "same": True, "target": "k-1"}]}
    )
    assert parsed.decisions[0].target == ""
    assert parsed.decisions[0].same_as_item == -1
    assert parsed.decisions[0].canonical_name == ""
    assert parsed.decisions[1].target == "k-1"
    assert MergeDecisions.model_validate({}).decisions == []
