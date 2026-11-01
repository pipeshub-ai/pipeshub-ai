"""The record query model and the evaluator every engine result comes from."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.retrieval.record_query.evaluate import (
    aggregate_value,
    evaluate_all,
    field_value,
    groups,
    tally,
)
from app.modules.retrieval.record_query.model import RecordQuery, day_start_ms

MAR_1 = day_start_ms("2026-03-01")
DAY = 86_400_000


def _ticket(key: str, *, status: str | None = "OPEN", priority: str | None = "HIGH", created: int = MAR_1,
            resolved: int | None = None, people: list | None = None, orgs: list | None = None) -> dict:
    return {
        "_key": key, "recordName": key, "recordType": "TICKET", "connectorName": "ZENDESK",
        "recordGroupId": "queue", "createdAt": created, "updatedAt": created,
        "ticket": {"status": status, "priority": priority, "resolvedAt": resolved},
        "people": people or [], "organizations": orgs or [], "links": [],
    }


def _deal(key: str, amount: float | None, *, closed: bool | None = True, won: bool = True) -> dict:
    return {
        "_key": key, "recordName": key, "recordType": "DEAL", "connectorName": "SALESFORCE",
        "recordGroupId": "acct", "createdAt": MAR_1, "updatedAt": MAR_1,
        "deal": {"amount": amount, "isClosed": closed, "isWon": won, "closeDate": "2026-03-20"},
        "people": [{"role": "OWNED_BY", "id": "leila", "name": "Leila Brandt"}], "organizations": [], "links": [],
    }


def q(**kw: object) -> RecordQuery:
    return RecordQuery.model_validate(kw)


# -- the model


def test_a_query_names_only_declared_fields_values_and_roles() -> None:
    with pytest.raises(ValidationError, match="fields: amount"):
        q(where=[{"field": "colour", "in": ["red"]}])
    with pytest.raises(ValidationError, match="values: NEW"):
        q(where=[{"field": "status", "in": ["opened"]}])
    with pytest.raises(ValidationError, match="roles: assignee"):
        q(where=[{"person": {"role": "sponsor", "ids": ["p"]}}])


def test_each_field_kind_takes_its_own_operator() -> None:
    with pytest.raises(ValidationError, match='takes "from"'):
        q(where=[{"field": "created", "in": ["2026"]}])
    with pytest.raises(ValidationError, match='takes "in"'):
        q(where=[{"field": "status", "from": "OPEN"}])
    with pytest.raises(ValidationError, match="needs a number field"):
        q(aggregate={"op": "sum", "field": "status"})


def test_an_entity_condition_must_be_top_level() -> None:
    q(where=[{"entity": {"type": "topic", "id": "t1"}}])
    with pytest.raises(ValidationError, match="top-level"):
        q(where=[{"any_of": [{"entity": {"type": "topic", "id": "t1"}}, {"field": "status", "in": ["OPEN"]}]}])


def test_limits_bound_what_one_call_returns() -> None:
    with pytest.raises(ValidationError):
        q(limit=51)
    with pytest.raises(ValidationError):
        q(top=21)


# -- three-valued conditions


def test_a_record_without_the_field_is_unknown_not_a_miss() -> None:
    query = q(where=[{"field": "status", "in": ["OPEN"]}])
    found = tally(query, [_ticket("a"), _ticket("b", status="RESOLVED"), _ticket("c", status=None)])
    assert [r["_key"] for r in found.matched] == ["a"]
    assert found.unknown == {"status": 1}


def test_not_keeps_unknown_unknown() -> None:
    row = _ticket("c", status=None)
    assert evaluate_all(q(where=[{"not": {"field": "status", "in": ["OPEN"]}}]).where, row).value is None


def test_any_of_is_true_when_one_part_is_even_if_another_is_unknown() -> None:
    row = _ticket("c", status=None, priority="HIGHEST")
    query = q(where=[{"any_of": [{"field": "status", "in": ["OPEN"]}, {"field": "priority", "in": ["highest"]}]}])
    assert evaluate_all(query.where, row).value is True


def test_all_of_is_false_when_one_part_is_even_if_another_is_unknown() -> None:
    row = _ticket("c", status=None, priority="LOW")
    query = q(where=[{"field": "status", "in": ["OPEN"]}, {"field": "priority", "in": ["HIGH"]}])
    assert evaluate_all(query.where, row).value is False


def test_dates_are_whole_days_both_inclusive() -> None:
    query = q(where=[{"field": "created", "from": "2026-03-01", "to": "2026-03-01"}])
    rows = [_ticket("start", created=MAR_1), _ticket("end", created=MAR_1 + DAY - 1), _ticket("next", created=MAR_1 + DAY)]
    assert [r["_key"] for r in tally(query, rows).matched] == ["start", "end"]


def test_resolution_days_counts_calendar_days_and_is_unknown_while_open() -> None:
    assert field_value(_ticket("a", created=MAR_1 + 3_600_000, resolved=MAR_1 + 2 * DAY), "resolution_days") == 2
    assert field_value(_ticket("b"), "resolution_days") is None


def test_a_person_matches_only_in_the_role_asked() -> None:
    row = _ticket("a", people=[{"role": "ASSIGNED_TO", "id": "ravi", "name": "Ravi"},
                               {"role": "REPORTED_BY", "id": "hiro", "name": "Hiro"}])
    assert evaluate_all(q(where=[{"person": {"role": "assignee", "ids": ["ravi"]}}]).where, row).value is True
    assert evaluate_all(q(where=[{"person": {"role": "assignee", "ids": ["hiro"]}}]).where, row).value is False
    assert evaluate_all(q(where=[{"person": {"ids": ["hiro"]}}]).where, row).value is True


# -- aggregates and groups


def test_deal_outcome_and_sums_skip_unknown_amounts() -> None:
    rows = [_deal("w", 48_000), _deal("l", 30_000, won=False), _deal("o", 9_000, closed=False), _deal("x", None)]
    assert [field_value(r, "deal_outcome") for r in rows] == ["WON", "LOST", "OPEN", "WON"]
    query = q(where=[{"field": "deal_outcome", "in": ["WON"]}], aggregate={"op": "sum", "field": "amount"})
    matched = tally(query, rows).matched
    assert aggregate_value(query, matched) == (48_000.0, 1)


def test_groups_rank_by_count_and_a_row_counts_for_each_person() -> None:
    rows = [
        _ticket("a", people=[{"role": "ASSIGNED_TO", "id": "ravi", "name": "Ravi"}]),
        _ticket("b", people=[{"role": "ASSIGNED_TO", "id": "ravi", "name": "Ravi"},
                             {"role": "ASSIGNED_TO", "id": "ama", "name": "Ama"}]),
        _ticket("c", people=[{"role": "REPORTED_BY", "id": "ama", "name": "Ama"}]),
    ]
    out = groups(q(group_by={"person_role": "assignee"}), rows)
    assert [(g.key, g.count) for g in out] == [("ravi", 2), ("ama", 1)]
    by_month = groups(q(group_by={"month": "created"}), rows)
    assert [(g.key, g.count) for g in by_month] == [("2026-03", 3)]


def test_a_record_without_the_grouped_field_is_in_no_group() -> None:
    rows = [_ticket("a", status="OPEN"), _ticket("b", status="OPEN"), _ticket("c", status=None)]
    assert [(g.key, g.count) for g in groups(q(group_by={"field": "status"}), rows)] == [("OPEN", 2)]
