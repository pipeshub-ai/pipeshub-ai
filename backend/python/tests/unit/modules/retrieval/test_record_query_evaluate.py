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
    unassigned,
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


def test_people_group_as_one_person_per_email_and_once_per_row() -> None:
    rows = [
        _ticket("a", people=[{"role": "ASSIGNED_TO", "id": "ravi", "name": "Ravi", "email": "ravi@x.io"},
                             {"role": "ASSIGNED_TO", "id": "ravi-2", "name": "", "email": "Ravi@X.io"}]),
        _ticket("b", people=[{"role": "ASSIGNED_TO", "id": "ravi-2", "name": None, "email": "ravi@x.io"}]),
        _ticket("c", people=[{"role": "ASSIGNED_TO", "id": "nomail", "name": "Nomail", "email": None},
                             {"role": "ASSIGNED_TO", "id": "nomail-2", "name": "Nomail", "email": None}]),
    ]
    out = groups(q(group_by={"person_role": "assignee"}), rows)
    assert [(g.key, g.label, g.count) for g in out] == [
        ("ravi", "Ravi (ravi@x.io)", 2), ("nomail", "Nomail", 1), ("nomail-2", "Nomail", 1),
    ]


def test_a_record_without_the_grouped_field_is_in_no_group() -> None:
    rows = [_ticket("a", status="OPEN"), _ticket("b", status="OPEN"), _ticket("c", status=None)]
    assert [(g.key, g.count) for g in groups(q(group_by={"field": "status"}), rows)] == [("OPEN", 2)]


def test_a_container_condition_is_scope_and_must_be_top_level() -> None:
    # The provider selects records in nested groups too; the evaluator, which
    # sees only a record's own group, must not reject them.
    query = q(where=[{"field": "container", "in": ["parent-group"]}])
    nested = _ticket("a")
    nested["recordGroupId"] = "child-group"
    assert [r["_key"] for r in tally(query, [nested]).matched] == ["a"]
    with pytest.raises(ValidationError, match="top-level"):
        q(where=[{"any_of": [{"field": "container", "in": ["g"]}, {"field": "status", "in": ["OPEN"]}]}])


# -- records not classified yet (classify on first use)


def _classified(key: str, status: str | None, *, topics: list[str] = (), orgs: list[str] = ()) -> dict:
    row = _ticket(key, orgs=[{"id": o, "name": o} for o in orgs])
    row["extractionStatus"] = status
    row["entities"] = [{"type": "topic", "id": t} for t in topics]
    return row


@pytest.mark.parametrize("status", ["NOT_STARTED", "QUEUED", "IN_PROGRESS", None])
def test_a_record_not_classified_yet_is_unknown_for_a_topic_never_a_miss(status: str | None) -> None:
    query = q(where=[{"entity": {"type": "topic", "id": "billing"}}])
    found = tally(query, [_classified("a", status), _classified("b", "COMPLETED"),
                          _classified("c", status, topics=["billing"])])
    assert [r["_key"] for r in found.matched] == ["c"]
    assert found.unknown == {"classification": 1}


@pytest.mark.parametrize("status", ["COMPLETED", "SKIPPED"])
def test_a_classified_record_without_the_topic_is_a_miss(status: str) -> None:
    query = q(where=[{"entity": {"type": "topic", "id": "billing"}}])
    assert evaluate_all(query.where, _classified("a", status)).value is False


def test_an_exact_entity_is_decided_by_the_provider_whatever_the_classification() -> None:
    query = q(where=[{"entity": {"type": "record_group", "id": "queue"}}])
    assert evaluate_all(query.where, _classified("a", "NOT_STARTED")).value is True


def test_an_organisation_a_record_not_classified_yet_lacks_is_unknown() -> None:
    query = q(where=[{"organization": {"ids": ["acme"]}}])
    rows = [_classified("pending", "NOT_STARTED"), _classified("done", "COMPLETED"),
            _classified("linked", "NOT_STARTED", orgs=["acme"])]
    found = tally(query, rows)
    assert [r["_key"] for r in found.matched] == ["linked"]
    assert found.unknown == {"classification": 1}
    negated = tally(q(where=[{"not": {"organization": {"ids": ["acme"]}}}]), rows)
    assert [r["_key"] for r in negated.matched] == ["done"] and negated.unknown == {"classification": 1}


def test_the_rows_left_unknown_for_classification_are_kept_up_to_a_bound() -> None:
    query = q(where=[{"entity": {"type": "topic", "id": "billing"}}])
    rows = [_classified(f"r{i}", "NOT_STARTED") for i in range(5)]
    found = tally(query, rows, keep_not_classified=3)
    assert [r["_key"] for r in found.not_classified] == ["r0", "r1", "r2"]
    assert found.unknown == {"classification": 5}


def test_a_record_whose_classification_failed_is_unknown_for_its_own_reason() -> None:
    rows = [_classified("failed", "FAILED"), _classified("waiting", "NOT_STARTED"), _classified("done", "COMPLETED"),
            _classified("failed_linked", "FAILED", topics=["billing"], orgs=["acme"])]
    topic = tally(q(where=[{"entity": {"type": "topic", "id": "billing"}}]), rows, keep_not_classified=10)
    assert [r["_key"] for r in topic.matched] == ["failed_linked"]
    assert topic.unknown == {"classification": 1, "classification_failed": 1}
    # Only records still waiting are handed to first use: a failed one would be retried forever.
    assert [r["_key"] for r in topic.not_classified] == ["waiting"]
    org = tally(q(where=[{"organization": {"ids": ["acme"]}}]), rows)
    assert org.unknown == {"classification": 1, "classification_failed": 1}


def test_grouping_by_organisation_never_drops_a_record_it_cannot_place() -> None:
    rows = [_classified("acme1", "COMPLETED", orgs=["acme"]), _classified("acme2", "NOT_STARTED", orgs=["acme"]),
            _classified("waiting", "NOT_STARTED"), _classified("queued", "QUEUED"),
            _classified("failed", "FAILED"), _classified("none", "COMPLETED")]
    query = q(group_by={"organization": True})
    real = groups(query, rows)
    others = unassigned(query, rows)
    assert [(g.label, g.count) for g in real] == [("acme", 2)]
    assert [(g.key, g.label, g.count) for g in others] == [
        ("no_organization", "(no organization)", 1),
        ("classification", "unknown (not classified yet)", 2),
        ("classification_failed", "unknown (classification failed)", 1),
    ]
    # One organisation a record at most, so every record is in exactly one line.
    assert sum(g.count for g in real + others) == len(rows)
    assert unassigned(q(group_by={"field": "status"}), rows) == []
