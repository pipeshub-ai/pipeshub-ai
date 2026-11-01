"""``knowledgegraph__find_records`` (``ops/record_query.py``): the policy gate,
query errors, and what the model reads back."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.knowledge_graph.ops import record_query as op
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
    monkeypatch.setattr(op, "load_entity_access_context", AsyncMock(return_value=MagicMock()))
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
