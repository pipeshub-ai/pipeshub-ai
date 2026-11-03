"""The recall check (``record_query/recall.py``): search hits the engine did
not match are judged on what the graph knows; a contradicting fact drops
one, a missing one makes it a possible match, never part of a total."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from app.modules.retrieval.entity_permissions import EntityAccessContext
from app.modules.retrieval.record_query import recall
from app.modules.retrieval.record_query.model import RecordQuery, RecordQueryResult
from app.services.graph_db.common.record_query_rows import (
    CandidateKey,
    RecordCandidates,
)

CONTEXT = EntityAccessContext(
    org_id="org", user_key="u", app_level_app_ids=frozenset(), record_level_app_ids=frozenset({"zd"}),
    record_group_ids=frozenset(), app_names={"zd": "Zendesk"},
)


def _row(key: str, *, assignee: str | None = None, status: str | None = "OPEN", source: str = "ZENDESK",
         vid: str | None = None, title: str = "Secret title") -> dict[str, Any]:
    return {
        "_key": key, "recordName": title, "recordType": "TICKET", "connectorName": source, "connectorId": "zd",
        "virtualRecordId": vid or f"v-{key}", "createdAt": 1, "updatedAt": 1,
        "ticket": {"status": status} if status else None,
        "people": [{"role": "ASSIGNED_TO", "id": assignee, "name": assignee}] if assignee else [],
        "organizations": [], "links": [],
    }


class _Graph:
    """The recall scan applies ``records`` and ``sources``, as a provider would."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = {r["_key"]: r for r in rows}
        self.scans: list[dict[str, Any]] = []
        self.reads: list[list[str]] = []

    async def get_record_query_candidates(self, org_id: str, **kw: Any) -> RecordCandidates:  # noqa: ANN401
        self.scans.append(kw)
        pushed = kw["pushdown"]
        keys = [k for k in pushed["records"][0] if k in self.rows
                and ("sources" not in pushed or self.rows[k]["connectorName"] in pushed["sources"])]
        return RecordCandidates([CandidateKey(k, 1) for k in keys])

    async def get_record_query_rows(self, org_id: str, *, record_ids: list[str], **kw: Any) -> list[dict]:  # noqa: ANN401
        self.reads.append(list(record_ids))
        return [dict(self.rows[i]) for i in record_ids]


@pytest.fixture
def readable(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    allowed: set[str] = set()

    async def admitted(provider: object, context: object, keys: Any) -> frozenset[str]:  # noqa: ANN401
        return frozenset(k for k in keys if k in allowed)

    monkeypatch.setattr(recall, "admitted_node_ids", admitted)
    return allowed


def _query(where: list[dict[str, Any]]) -> RecordQuery:
    query = RecordQuery.model_validate({"where": where})
    for cond in query.where:
        if hasattr(cond, "person") and cond.person.ids is None:
            cond.person.ids = ["ravi"]
    return query


def _result(query: RecordQuery, matched: set[str], matched_vids: set[str] = frozenset()) -> RecordQueryResult:
    return RecordQueryResult(total=len(matched), matched_ids=frozenset(matched),
                             matched_virtual_ids=frozenset(matched_vids), connector_ids=["zd"],
                             resolved_query=query)


def test_the_terms_are_names_values_and_mention_text_never_ids_or_filters() -> None:
    query = RecordQuery.model_validate({"where": [
        {"person": {"role": "assignee", "name": "ravi"}},
        {"person": {"ids": ["u-9", "me"]}},
        {"organization": {"ids": ["o-1"]}},
        {"field": "status", "in": ["IN_PROGRESS"]},
        {"field": "source", "in": ["ZENDESK"]},
        {"field": "created", "from": "2026-03-01", "to": "2026-03-31"},
        {"mentions": {"name": "Northwind", "amount": {"from": 50000, "currency": "USD"}, "percent": {"to": 0.15}}},
    ]})
    names = {"ravi": "Ravi Shankar", "u-9": "Ann Lee", "o-1": "Acme Corp"}
    assert recall.recall_terms(query, names) == [
        "Ravi Shankar", "Ann Lee", "Acme Corp", "in progress", "Northwind", "50000", "USD", "15%",
    ]
    assert recall.search_time_range(query) == {
        "source_created_after_ms": 1_772_323_200_000, "source_created_before_ms": 1_775_001_599_999,
    }


def test_a_query_search_has_nothing_to_look_for_is_skipped() -> None:
    query = RecordQuery.model_validate({"where": [{"field": "source", "in": ["JIRA"]}]})
    assert recall.recall_terms(query, {}) == []
    assert recall.start_search(query, {}, lambda *_: None) is None  # type: ignore[arg-type,return-value]


async def test_a_hit_the_graph_contradicts_is_dropped_and_one_it_cannot_decide_is_possible(
    readable: set[str],
) -> None:
    rows = [
        _row("matched", assignee="ravi"),
        _row("other_assignee", assignee="ann"),     # the role is filled by someone else: a miss
        _row("no_assignee"),                        # no edge at all: unknown, possible
        _row("closed", status="CLOSED"),            # a status that differs: a miss
        _row("no_status", status=None),             # no field: unknown, possible
        _row("elsewhere", source="JIRA"),           # outside the source: the scan drops it
        _row("unreadable"),                         # the access check refuses it
        _row("copy", vid="v-matched"),              # another copy of a counted record
    ]
    readable.update(r["_key"] for r in rows if r["_key"] != "unreadable")
    query = _query([{"person": {"role": "assignee", "name": "Ravi"}}, {"field": "status", "in": ["OPEN"]},
                    {"field": "source", "in": ["ZENDESK"]}])
    graph = _Graph(rows)
    check = await recall.reconcile(graph, CONTEXT, _result(query, {"matched"}, {"v-matched"}),
                                   [r["_key"] for r in rows], correlation_id="q1")
    assert sorted(r["_key"] for r in check.possible) == ["no_assignee", "no_status"]
    assert check.ran and check.dropped == 5
    (scan,) = graph.scans
    # Edges extraction writes are left to the evaluator, never to the scan.
    assert "people" not in scan["pushdown"] and scan["pushdown"]["sources"] == ["ZENDESK"]
    assert "unreadable" not in graph.reads[0] and "matched" not in scan["pushdown"]["records"][0]


async def test_a_hit_the_mention_did_not_find_is_possible(readable: set[str]) -> None:
    readable.add("r2")
    query = RecordQuery.model_validate({"where": [{"mentions": {"name": "Acme"}}]})
    query.where[0].resolve(frozenset({"r1"}))
    check = await recall.reconcile(_Graph([_row("r1"), _row("r2")]), CONTEXT, _result(query, {"r1"}), ["r1", "r2"])
    assert [r["_key"] for r in check.possible] == ["r2"]


async def test_the_log_carries_counts_and_the_correlation_id_never_content(
    readable: set[str], caplog: pytest.LogCaptureFixture,
) -> None:
    readable.add("no_assignee")
    query = _query([{"person": {"role": "assignee", "name": "Ravi"}}])
    with caplog.at_level(logging.INFO, logger=recall.logger.name):
        await recall.reconcile(_Graph([_row("no_assignee")]), CONTEXT, _result(query, set()), ["no_assignee"],
                               correlation_id="q-42")
    (line,) = [r.getMessage() for r in caplog.records if "recall" in r.getMessage()]
    assert "q-42" in line and "possible=1" in line and "'ZENDESK': 1" in line and "'person': 1" in line
    assert "Secret title" not in line and "Ravi" not in line


async def test_a_failed_or_late_search_never_fails_the_call() -> None:
    query = _query([{"person": {"role": "assignee", "name": "Ravi"}}])

    async def broken(text: str, time_range: Any) -> list[str]:  # noqa: ANN401
        raise RuntimeError("search down")

    async def slow(text: str, time_range: Any) -> list[str]:  # noqa: ANN401
        await asyncio.sleep(5)
        return []

    for search, deadline in ((broken, 1.0), (slow, 0.05)):
        task = recall.start_search(query, {"Ravi": "Ravi"}, search, deadline_seconds=deadline)
        check = await recall.finish_recall(_Graph([]), CONTEXT, _result(query, set()), task)
        assert check.failed and not check.ran


async def test_no_terms_is_reported_as_skipped() -> None:
    query = RecordQuery.model_validate({"where": []})
    check = await recall.finish_recall(_Graph([]), CONTEXT, _result(query, set()), None)
    assert check.skipped == "no_terms" and not check.ran


async def test_the_search_runs_beside_the_engine_with_the_query_terms_and_window() -> None:
    seen: list[tuple[str, Any]] = []

    async def search(text: str, time_range: Any) -> list[str]:  # noqa: ANN401
        seen.append((text, time_range))
        return ["r1"]

    query = RecordQuery.model_validate({"where": [{"field": "status", "in": ["OPEN"]},
                                                  {"field": "updated", "from": "2026-03-01"}]})
    task = recall.start_search(query, {}, search)
    assert await task == ["r1"]
    assert seen == [("open", {"source_updated_after_ms": 1_772_323_200_000})]
