"""The record query engine over a fake provider: permissions, exact totals,
caps and paging. The providers' own queries are tested against real
backends in ``tests/integration/graph_db``."""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.retrieval import entity_permissions
from app.modules.retrieval.entity_permissions import (
    EntityAccessContext,
    EntityAccessError,
)
from app.modules.retrieval.record_query import engine
from app.modules.retrieval.record_query.engine import run_record_query
from app.modules.retrieval.record_query.model import RecordQuery, day_start_ms
from app.services.graph_db.common.record_query_rows import RecordCandidates

MAR_1 = day_start_ms("2026-03-01")


def _row(i: int, *, assignee: str, status: str = "OPEN", amount: float | None = None) -> dict[str, Any]:
    return {
        "_key": f"r{i:03d}", "recordName": f"Ticket {i}", "recordType": "TICKET", "connectorName": "ZENDESK",
        "connectorId": "zd", "recordGroupId": "queue", "createdAt": MAR_1 + i, "updatedAt": MAR_1 + i,
        "ticket": {"status": status, "sourceStatus": status.title(), "priority": "HIGH", "resolvedAt": None},
        "deal": {"amount": amount, "isClosed": True, "isWon": True} if amount is not None else None,
        "people": [{"role": "ASSIGNED_TO", "id": assignee, "name": assignee.title()}],
        "organizations": [{"id": "orrin", "name": "Orrin Biotech"}], "links": [],
    }


class FakeProvider:
    """Applies the person and source parts of a pushdown, newest first, in windows."""

    def __init__(self, rows: list[dict[str, Any]], *, fail: bool = False) -> None:
        self.rows = sorted(rows, key=lambda r: (-r["updatedAt"], r["_key"]))
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def get_record_query_candidates(self, org_id: str, **kw: Any) -> RecordCandidates:  # noqa: ANN401
        self.calls.append(kw)
        if self.fail:
            raise RuntimeError("graph down")
        pushed = kw["pushdown"]
        rows = [r for r in self.rows if r["connectorId"] in kw["connector_ids"]]
        for people in pushed.get("people", []):
            rows = [r for r in rows if any(p["role"] in people["edges"] and p["id"] in people["ids"]
                                           for p in r["people"])]
        capped = len(rows) > kw["scan_cap"]
        rows = rows[: kw["scan_cap"]]
        window = rows[kw["offset"]: kw["offset"] + kw["window"]]
        return RecordCandidates(rows=window, window_size=len(window), capped=capped)


CONTEXT = EntityAccessContext(
    org_id="org", user_key="u", app_level_app_ids=frozenset(), record_level_app_ids=frozenset({"zd"}),
    record_group_ids=frozenset(), app_names={"zd": "Zendesk"},
)


@pytest.fixture
def readable(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Record keys the access check admits; everything else is unreadable."""
    allowed: set[str] = set()

    async def admitted(provider: object, context: object, keys: Any) -> frozenset[str]:  # noqa: ANN401
        return frozenset(k for k in keys if k in allowed)

    monkeypatch.setattr(engine, "admitted_node_ids", admitted)
    return allowed


def q(**kw: object) -> RecordQuery:
    return RecordQuery.model_validate(kw)


async def test_a_record_the_user_cannot_read_is_in_no_total_group_sum_or_row(readable: set[str]) -> None:
    rows = [_row(i, assignee="ravi" if i % 2 else "ama", amount=1000.0) for i in range(10)]
    readable.update(r["_key"] for r in rows[:6])
    result = await run_record_query(
        FakeProvider(rows), CONTEXT, q(aggregate={"op": "sum", "field": "amount"},
                                       group_by={"person_role": "assignee"}, limit=50),
    )
    assert result.total == 6 and result.value == 6000.0
    assert sum(g.count for g in result.groups) == 6
    assert {r["record_id"] for r in result.rows} == {r["_key"] for r in rows[:6]}


async def test_a_total_over_many_windows_is_exact(readable: set[str]) -> None:
    rows = [_row(i, assignee="ravi") for i in range(1234)]
    readable.update(r["_key"] for r in rows)
    provider = FakeProvider(rows)
    result = await run_record_query(provider, CONTEXT, q(where=[{"person": {"role": "assignee", "ids": ["ravi"]}}]),
                                    window=100)
    assert (result.total, result.approximate) == (1234, False)
    assert len(provider.calls) == 13
    assert provider.calls[0]["pushdown"]["people"] == [{"edges": ["ASSIGNED_TO"], "ids": ["ravi"]}]


async def test_a_scan_stopped_by_its_cap_says_the_total_is_approximate(readable: set[str]) -> None:
    rows = [_row(i, assignee="ravi") for i in range(300)]
    readable.update(r["_key"] for r in rows)
    result = await run_record_query(FakeProvider(rows), CONTEXT, q(), scan_cap=200, window=100)
    assert (result.total, result.approximate) == (200, True)


async def test_a_provider_failure_is_an_error_not_an_empty_answer(readable: set[str]) -> None:
    with pytest.raises(EntityAccessError):
        await run_record_query(FakeProvider([], fail=True), CONTEXT, q())


async def test_rows_page_with_a_cursor_and_unknowns_are_counted(readable: set[str]) -> None:
    rows = [_row(i, assignee="ravi") for i in range(5)]
    rows[4]["ticket"]["status"] = None
    readable.update(r["_key"] for r in rows)
    first = await run_record_query(FakeProvider(rows), CONTEXT, q(where=[{"field": "status", "in": ["open"]}], limit=3))
    assert (first.total, first.unknown, len(first.rows), first.next_cursor) == (4, {"status": 1}, 3, "3")
    rest = await run_record_query(FakeProvider(rows), CONTEXT,
                                  q(where=[{"field": "status", "in": ["open"]}], limit=3, cursor="3"))
    assert (len(rest.rows), rest.next_cursor) == (1, None)


async def test_describe_lists_the_top_values_among_matches(readable: set[str]) -> None:
    rows = [_row(i, assignee="ravi", status="OPEN" if i < 3 else "PENDING") for i in range(5)]
    readable.update(r["_key"] for r in rows)
    result = await run_record_query(FakeProvider(rows), CONTEXT, q(describe=["status"], limit=0))
    assert [(g.key, g.count) for g in result.described["status"]] == [("OPEN", 3), ("PENDING", 2)]
    assert result.rows == []


async def test_a_record_group_the_user_cannot_reach_lists_only_app_level_connectors(readable: set[str]) -> None:
    provider = FakeProvider([])
    await run_record_query(provider, CONTEXT, q(where=[{"entity": {"type": "record_group", "id": "secret"}}]))
    assert provider.calls == []  # no app-level connector: nothing to list


def test_a_row_reads_as_names_and_leaves_empty_values_out() -> None:
    view = engine.row_view(_row(1, assignee="ravi"))
    assert view["people"] == {"assignee": ["Ravi"]} and view["organizations"] == ["Orrin Biotech"]
    assert view["status"] == "Open" and "amount" not in view and view["created"] == "2026-03-01"
    assert entity_permissions.RECORD_GROUP_ENTITY_TYPE == "record_group"
