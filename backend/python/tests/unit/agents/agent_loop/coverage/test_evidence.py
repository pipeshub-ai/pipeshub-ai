"""What counts as shown to the model: search and prefetch blocks, and only the
blocks a fetch rendered."""

from __future__ import annotations

from app.agents.agent_loop.coverage.evidence import collect_evidence, evidence_digest
from app.agents.agent_loop.coverage.state import coverage_state


def _search_state(*records: tuple[str, str, str, list[str]]) -> dict:
    """(vrid, record id, name, passages shown by search)."""
    return {
        "virtual_record_id_to_result": {vrid: {"id": rid, "record_name": name} for vrid, rid, name, _ in records},
        "final_results": [
            {"virtual_record_id": vrid, "content": passage, "block_index": i}
            for vrid, _rid, _name, passages in records for i, passage in enumerate(passages)
        ],
    }


def test_a_fetch_counts_only_the_blocks_it_rendered() -> None:
    state = {
        "virtual_record_id_to_result": {"vr-1": {
            "id": "rec-1", "record_name": "kiln-schedule",
            "block_containers": {"blocks": [
                {"index": 0, "data": "Kiln B fires on Tuesdays."},
                {"index": 1, "data": "Kiln B cools for nine hours."},
            ]},
        }},
        "fetch_render_outcomes": {"rec-1": [{"virtualRecordId": "vr-1", "shownBlocks": [0], "complete": False}]},
    }
    [record] = collect_evidence(state).records
    assert record.fetched and not record.complete
    assert record.passages == ["Kiln B fires on Tuesdays."]


def test_a_listed_name_alone_is_not_a_read_record() -> None:
    state = _search_state(("vr-1", "rec-1", "kiln-schedule", []))
    evidence = collect_evidence(state)
    assert evidence.read_records() == []
    assert evidence.texts() == []


def test_the_digest_puts_cited_records_first_and_labels_them() -> None:
    state = _search_state(
        ("vr-1", "rec-1", "other", ["unrelated text"]),
        ("vr-2", "rec-2", "kiln-schedule", ["Kiln B fires on Tuesdays."]),
    )
    digest = evidence_digest(collect_evidence(state), focus_terms=["kiln"], cited_record_ids=["rec-2"])
    assert digest.index("[R2] kiln-schedule (blocks from search, cited in the draft)") < digest.index("[R1] other")


def test_the_state_is_shared_by_every_reader_of_the_request() -> None:
    tool_state: dict = {}
    coverage_state(tool_state).note_searched("Kiln twelve firing schedule")
    assert coverage_state(tool_state).was_searched("firing schedules for kiln twelve")
    assert not coverage_state(tool_state).was_searched("kiln seven firing schedule")
