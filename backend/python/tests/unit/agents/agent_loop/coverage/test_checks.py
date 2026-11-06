"""The deterministic signals that send a draft to the checker."""

from __future__ import annotations

import pytest

from app.agents.agent_loop.coverage.checks import (
    find_admissions,
    mentions_conflict,
    near_duplicate_reads,
    same_subject,
    unread_references,
)
from app.agents.agent_loop.coverage.evidence import collect_evidence
from app.agents.agent_loop.coverage.facets import Facet, FacetPlan


def _state(*records: tuple[str, str, str, list[str]]) -> dict:
    return {
        "virtual_record_id_to_result": {vrid: {"id": rid, "record_name": name} for vrid, rid, name, _ in records},
        "final_results": [
            {"virtual_record_id": vrid, "content": passage, "block_index": i}
            for vrid, _rid, _name, passages in records for i, passage in enumerate(passages)
        ],
    }


@pytest.mark.parametrize("sentence", [
    "The closest match is the greenhouse sensor rollout.",
    "This record does not meet the two-week condition.",
    "I did not retrieve DOC-5521, so the humidity limit is unconfirmed.",
    "No record mentions the second shift's start time.",
    "The incident review does not specify who paged the vendor.",
    "I'm not certain it's the one you mean.",
])
def test_an_admission_is_found_in_any_sentence(sentence: str) -> None:
    assert find_admissions("The pump room reopens on the 9th.\n" + sentence) == [sentence]


def test_a_plain_answer_admits_nothing() -> None:
    assert find_admissions("The pump room reopens on the 9th, per the facilities calendar.") == []


def test_conflict_wording_is_a_signal() -> None:
    assert mentions_conflict("The two calendars disagree on the reopening date.")
    assert not mentions_conflict("The pump room reopens on the 9th.")


@pytest.mark.parametrize(("a", "b", "same"), [
    ("pump room reopening plan", "pump room reopening plan v2", True),
    ("pump room reopening plan", "pump room reopening plan (revised)", True),
    ("pump room reopening plan", "boiler room reopening checklist", False),
    ("pump notes", "pump notes (draft)", False),  # one distinctive word is too few to tell
])
def test_same_subject_reads_distinctive_name_words(a: str, b: str, same: bool) -> None:
    assert same_subject(a, b) is same


def test_lookalike_reads_count_only_when_one_is_cited() -> None:
    evidence = collect_evidence(_state(
        ("vr-1", "rec-1", "pump room reopening plan", ["Reopens on the 9th."]),
        ("vr-2", "rec-2", "pump room reopening plan v2", ["Reopens on the 12th."]),
    ))
    assert near_duplicate_reads(evidence, ["rec-2"]) == [("rec-1", "rec-2")]
    assert near_duplicate_reads(evidence, []) == []


def test_a_reference_counts_as_read_once_a_record_names_or_quotes_it() -> None:
    plan = FacetPlan(facets=[Facet(id="f1", kind="reference", text="DOC-5521", terms=["DOC-5521"])])
    unread = collect_evidence(_state(("vr-1", "rec-1", "pump room plan", ["Reopens on the 9th."])))
    assert [ref.text for ref in unread_references(plan, unread)] == ["DOC-5521"]
    read = collect_evidence(_state(("vr-2", "rec-2", "DOC-5521 humidity limits", ["Keep it under 60%."])))
    assert unread_references(plan, read) == []
