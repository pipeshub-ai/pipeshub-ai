"""The merge prompt: pairwise items, no scores, braces-safe, bounded summary."""

import json

from app.modules.entity_resolution.models import (
    SUBCATEGORY_2,
    TOPIC,
    ExtractedName,
    MergeDecisions,
    WinnerCandidate,
)
from app.modules.entity_resolution.prompt import build_prompt, candidate_handles


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
    handles = candidate_handles(items, candidates)
    assert rendered[0] == {
        "i": 0, "name": "Bug bash testing session", "kind": "topic",
        "matches": [{"id": handles["k-bug"], "name": "Bug bash testing", "aliases": ["bbt"]}],
    }
    assert rendered[1]["matches"] == [{"id": handles["k-manual"], "name": "Manual Testing", "aliases": []}]
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
        ids = tuple(m["name"] for m in rendered[0]["matches"])
        assert sorted(ids) == ["Release 0", "Release 1", "Release 2"]
        again = _items_block(build_prompt(None, [_name(0, TOPIC, raw)], {0: offered}))
        assert tuple(m["name"] for m in again[0]["matches"]) == ids
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


def test_aliases_shown_per_candidate_are_capped() -> None:
    """Nodes keep up to 200 spellings; the prompt shows a few per candidate,
    or three popular candidates would multiply every item's size."""
    from app.modules.entity_resolution.prompt import PROMPT_ALIASES_PER_MATCH

    offered = tuple(
        WinnerCandidate(f"k{i}", f"Release {i}", tuple(f"spelling {i}-{j}" for j in range(200)))
        for i in range(3)
    )
    rendered = _items_block(build_prompt(None, [_name(0, TOPIC, "Release x")], {0: offered}))
    assert all(len(m["aliases"]) == PROMPT_ALIASES_PER_MATCH for m in rendered[0]["matches"])


def test_node_ids_never_reach_the_prompt() -> None:
    """A node id is a UUID the model would read for every match and copy into
    every merge; the prompt shows the first 8 hex digits, one handle per node."""
    from app.modules.entity_resolution.keys import taxonomy_node_key

    shared = WinnerCandidate(taxonomy_node_key("acme", "topics", "release notes"), "Release notes")
    other = WinnerCandidate(taxonomy_node_key("acme", "topics", "release plan"), "Release plan")
    items = [_name(0, TOPIC, "Release note"), _name(1, TOPIC, "Release notes v2")]
    candidates = {0: (shared, other), 1: (shared,)}
    prompt = build_prompt("s", items, candidates)
    assert shared.entity_id not in prompt and other.entity_id not in prompt
    handles = candidate_handles(items, candidates)
    assert sorted(handles) == sorted([shared.entity_id, other.entity_id])
    assert handles[shared.entity_id] == shared.entity_id.replace("-", "")[:8]
    assert len(set(handles.values())) == 2
    rendered = _items_block(prompt)
    assert rendered[1]["matches"][0]["id"] == handles[shared.entity_id]


def test_items_are_one_per_line() -> None:
    items = [_name(0, TOPIC, "Release notes"), _name(1, TOPIC, "Release plan")]
    block = build_prompt("s", items, {0: (), 1: ()})
    body = block[block.index("# Items\n") + len("# Items\n"):block.index("\n\n# Output")]
    assert body.splitlines() == [
        "[",
        '{"i":0,"name":"Release notes","kind":"topic","matches":[]},',
        '{"i":1,"name":"Release plan","kind":"topic","matches":[]}',
        "]",
    ]


def test_handles_grow_until_they_are_unique() -> None:
    a = WinnerCandidate("abcdef01-0000-4000-8000-000000000001", "Release notes")
    b = WinnerCandidate("abcdef01-0000-4000-8000-000000000002", "Release plan")
    handles = candidate_handles([_name(0, TOPIC, "Release")], {0: (a, b)})
    assert len(set(handles.values())) == 2
    assert all(a.entity_id.replace("-", "").startswith(h) or b.entity_id.replace("-", "").startswith(h)
               for h in handles.values())
