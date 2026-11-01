"""``knowledgegraph__find_records`` (``ops/record_query.py``): the policy gate,
query errors, and what the model reads back."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.knowledge_graph.ops import record_query as op
from app.agents.actions.knowledge_graph.ops.entity_filters import LIMITED_SCOPE_NOTE
from app.agents.agent_loop.knowledge_graph_policy import (
    KNOWLEDGE_GRAPH_POLICY_STATE_KEY,
    KnowledgeGraphMode,
    KnowledgeGraphPolicy,
)
from app.modules.retrieval.entity_permissions import EntityAccessError
from app.modules.retrieval.record_query.model import Group, RecordQueryResult


def _state(mode: KnowledgeGraphMode = KnowledgeGraphMode.FULL, *, record_query: bool = True) -> dict[str, Any]:
    return {
        "graph_provider": MagicMock(),
        KNOWLEDGE_GRAPH_POLICY_STATE_KEY: KnowledgeGraphPolicy(mode=mode, record_query=record_query),
    }


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    run = AsyncMock(return_value=RecordQueryResult(total=0))
    monkeypatch.setattr(op, "run_record_query", run)
    monkeypatch.setattr(op, "load_entity_access_context", AsyncMock(return_value=MagicMock(org_id="org", row_scopes=())))
    monkeypatch.setattr(op, "cite_listed_records", AsyncMock(return_value={}))
    monkeypatch.setattr(op, "remember_record_ids", MagicMock())
    monkeypatch.setattr(op, "note_retrieved_by", MagicMock())
    return run


async def test_without_the_flag_it_refuses_and_queries_nothing(engine: AsyncMock) -> None:
    ok, text = await op.execute_find_records(_state(record_query=False))
    assert (ok, text) == (False, op.NOT_ENABLED_MSG)
    engine.assert_not_called()


async def test_an_invalid_query_names_what_exists(engine: AsyncMock) -> None:
    ok, text = await op.execute_find_records(_state(), where=[{"field": "colour", "in": ["red"]}])
    assert ok is False and "fields: amount" in text
    engine.assert_not_called()


@pytest.mark.parametrize("args", [
    {"where": [{"person": {"role": "assignee", "ids": ["p1"]}}]},
    {"group_by": {"organization": True}},
])
async def test_the_taxonomy_arm_takes_no_people_or_organisations(engine: AsyncMock, args: dict) -> None:
    ok, text = await op.execute_find_records(_state(KnowledgeGraphMode.TAXONOMY), **args)
    assert (ok, text) == (False, op.PEOPLE_NOT_ALLOWED_MSG)
    engine.assert_not_called()


async def test_a_failed_lookup_is_an_error_not_zero(engine: AsyncMock) -> None:
    engine.side_effect = EntityAccessError("down")
    ok, text = await op.execute_find_records(_state())
    assert (ok, text) == (False, op.LOOKUP_FAILED_MSG)


async def test_the_total_comes_first_and_rows_are_remembered_for_citing(engine: AsyncMock) -> None:
    engine.return_value = RecordQueryResult(
        total=21, groups=[Group(key="ravi", label="Ravi Shankar", count=13)], unknown={"status": 2},
        rows=[{"title": "Zendesk #5007", "record_id": "r1"}], next_cursor="1",
    )
    ok, text = await op.execute_find_records(
        _state(), where=[{"field": "status", "in": ["OPEN"]}], group_by={"person_role": "assignee"}, limit=1,
    )
    lines = text.splitlines()
    assert ok and lines[0] == "Total: 21 matching records you can open (exact)"
    assert '"field":"status"' in lines[1] and "- Ravi Shankar: 13" in text
    assert "2 lack status" in text and 'cursor="1"' in text
    op.remember_record_ids.assert_called_once()


async def test_an_approximate_total_says_so_and_how_to_narrow(engine: AsyncMock) -> None:
    engine.return_value = RecordQueryResult(total=20000, approximate=True)
    _, text = await op.execute_find_records(_state(), limit=0)
    assert text.startswith("Total: at least 20000") and "narrower condition" in text


def test_the_field_guide_names_fields_but_no_values_and_no_edge_conditions() -> None:
    guide = op.field_guide()
    assert "status" in guide and "amount" in guide
    assert "RESOLVED" not in guide and "linked_to" not in guide and "person" not in guide


async def test_a_stopped_scan_labels_every_aggregate_and_group_approximate(engine: AsyncMock) -> None:
    engine.return_value = RecordQueryResult(
        total=20000, approximate=True, value=1234.5,
        groups=[Group(key="a", label="Ann", count=7, value=10.0), Group(key="b", label="Bob", count=3, value=2.0)],
    )
    _, text = await op.execute_find_records(
        _state(), aggregate={"op": "sum", "field": "amount"}, group_by={"person_role": "owner"}, limit=0,
    )
    figures = [line for line in text.splitlines() if line.startswith(("sum of", "Groups by", "- "))]
    assert len(figures) == 4
    assert all("approximate" in line for line in figures), figures
    assert "exact" not in text.replace("(approximate", "")


async def test_a_complete_scan_labels_every_aggregate_and_group_exact(engine: AsyncMock) -> None:
    engine.return_value = RecordQueryResult(
        total=10, value=99.0, groups=[Group(key="a", label="Ann", count=7, value=60.0)],
    )
    _, text = await op.execute_find_records(
        _state(), aggregate={"op": "sum", "field": "amount"}, group_by={"person_role": "owner"}, limit=0,
    )
    figures = [line for line in text.splitlines() if line.startswith(("sum of", "Groups by", "- "))]
    assert len(figures) == 3 and all("exact" in line and "approximate" not in line for line in figures)


async def test_sort_reaches_the_engine_and_the_rows_say_their_order(engine: AsyncMock) -> None:
    engine.return_value = RecordQueryResult(total=1, rows=[{"title": "Deal", "record_id": "r1"}])
    ok, text = await op.execute_find_records(_state(), sort="amount desc")
    assert ok and engine.await_args.args[2].sort == "amount desc"
    assert "Records 1-1 of 1, amount desc:" in text


async def test_records_not_classified_are_reported_and_queued_for_classification(
    engine: AsyncMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued = MagicMock()
    monkeypatch.setattr(op, "classify_on_first_use_later", queued)
    waiting = [{"_key": "r9", "extractionStatus": "NOT_STARTED"}]
    engine.return_value = RecordQueryResult(total=2, unknown={"classification": 3, "status": 1},
                                            not_classified=waiting)
    state = {**_state(), "config_service": MagicMock(), "org_id": "org"}
    _, text = await op.execute_find_records(state, where=[{"entity": {"type": "topic", "id": "t"}}])
    assert "3 records not classified yet" in text and "unknown" in text
    assert "1 lack status" in text and "lack classification" not in text
    queued.assert_called_once()
    assert queued.call_args.args[0] == {"records": waiting} and queued.call_args.args[1] == "org"


async def test_nothing_is_queued_when_every_record_is_classified(
    engine: AsyncMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued = MagicMock()
    monkeypatch.setattr(op, "classify_on_first_use_later", queued)
    await op.execute_find_records({**_state(), "config_service": MagicMock()})
    queued.assert_not_called()


async def test_each_row_is_cited_with_the_facts_it_shows_and_totals_are_not(engine: AsyncMock) -> None:
    engine.return_value = RecordQueryResult(total=7, groups=[Group(key="a", label="Ann", count=7)], rows=[
        {"title": "Zendesk #1", "type": "TICKET", "status": "Open", "priority": "HIGH", "created": "2026-03-01",
         "people": {"assignee": ["Ann"]}, "record_id": "r1"},
        {"title": "Big deal", "type": "DEAL", "amount": 1000.0, "outcome": "WON", "close_date": "2026-01-15",
         "record_id": "r2"},
    ])
    op.cite_listed_records.return_value = {"r1": "ref1", "r2": "ref2"}
    _, text = await op.execute_find_records(_state(), group_by={"person_role": "assignee"})
    facts = op.cite_listed_records.await_args.args[3]
    assert facts == {"r1": "type TICKET · status Open · priority HIGH · created 2026-03-01",
                     "r2": "type DEAL · amount 1000.0 · outcome WON · close_date 2026-01-15"}
    rows = [line for line in text.splitlines() if line.startswith("- {")]
    assert '"cite":"ref1"' in rows[0] and '"cite":"ref2"' in rows[1]
    assert "ref1" in next(line for line in text.splitlines() if line.startswith("Cite a fact"))
    head = text.split("Records 1-2")[0]
    assert "ref" not in head


async def test_under_a_selection_the_result_says_it_counts_only_the_selection(engine: AsyncMock) -> None:
    op.load_entity_access_context.return_value = MagicMock(org_id="org", row_scopes=(MagicMock(),))
    engine.return_value = RecordQueryResult(total=4)
    _, text = await op.execute_find_records(_state(), limit=0)
    assert text.splitlines()[1] == LIMITED_SCOPE_NOTE


async def test_without_a_selection_there_is_no_scope_note(engine: AsyncMock) -> None:
    _, text = await op.execute_find_records(_state(), limit=0)
    assert LIMITED_SCOPE_NOTE not in text



async def test_records_whose_classification_failed_are_reported_apart(engine: AsyncMock) -> None:
    engine.return_value = RecordQueryResult(total=1, unknown={"classification": 2, "classification_failed": 4})
    _, text = await op.execute_find_records(_state(), where=[{"entity": {"type": "topic", "id": "t"}}])
    assert "2 records not classified yet" in text
    assert "4 records whose classification failed" in text and "reindex" in text
    assert "lack classification" not in text


async def test_records_no_organisation_group_holds_are_listed_apart_and_labelled(engine: AsyncMock) -> None:
    engine.return_value = RecordQueryResult(total=6, groups=[Group(key="acme", label="Acme", count=2)], unassigned=[
        Group(key="classification", label="unknown (not classified yet)", count=3),
        Group(key="classification_failed", label="unknown (classification failed)", count=1),
    ])
    _, text = await op.execute_find_records(_state(), group_by={"organization": True}, limit=0)
    assert "- Acme: 2 (exact)" in text
    assert "- unknown (not classified yet): 3 (exact)" in text
    assert "- unknown (classification failed): 1 (exact)" in text
