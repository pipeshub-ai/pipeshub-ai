"""The record query engine over a fake provider: permissions, exact totals,
caps and paging. The providers' own queries are tested against real
backends in ``tests/integration/graph_db``."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.modules.retrieval import entity_permissions
from app.modules.retrieval.entity_permissions import (
    EntityAccessContext,
    EntityAccessError,
)
from app.modules.retrieval.record_query import engine, paging
from app.modules.retrieval.record_query.engine import run_record_query
from app.modules.retrieval.record_query.model import RecordQuery, day_start_ms
from app.services.graph_db.common.record_query_rows import (
    CandidateKey,
    RecordCandidates,
)

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
    """Applies the person and source parts of a pushdown, newest first: one
    scan returns the candidates' keys, rows are read by id."""

    def __init__(
        self, rows: list[dict[str, Any]], *, fail: bool = False, nodes: list[dict[str, Any]] | None = None,
    ) -> None:
        self.rows = rows
        self.fail = fail
        self.calls: list[dict[str, Any]] = []
        self.row_reads: list[list[str]] = []
        self.scans = 0
        # Person nodes: {"id", "name", "email", "orgId"}.
        self.nodes = nodes or []
        self.person_lookups: list[tuple[str, list[str]]] = []

    async def get_person_nodes(self, org_id: str, ids: list[str], *, limit: int = 200) -> list[dict[str, Any]]:
        self.person_lookups.append((org_id, list(ids)))
        mine = [n for n in self.nodes if n["orgId"] == org_id]
        emails = {n["email"].lower() for n in mine if n["id"] in ids and n.get("email")}
        found = [n for n in mine if n["id"] in ids or (n.get("email") or "").lower() in emails]
        return [{"id": n["id"], "name": n.get("name"), "email": n.get("email")} for n in found][:limit]

    def _matching(self, kw: dict[str, Any]) -> list[dict[str, Any]]:
        self.scans += 1
        pushed = kw["pushdown"]
        rows = sorted((r for r in self.rows if r["connectorId"] in kw["connector_ids"]),
                      key=lambda r: (-r["updatedAt"], r["_key"]))
        for people in pushed.get("people", []):
            rows = [r for r in rows if any(p["role"] in people["edges"] and p["id"] in people["ids"]
                                           for p in r["people"])]
        return rows

    async def get_record_query_candidates(self, org_id: str, **kw: Any) -> Any:  # noqa: ANN401
        self.calls.append(kw)
        if self.fail:
            raise RuntimeError("graph down")
        rows = self._matching(kw)
        capped = len(rows) >= kw["scan_cap"]
        rows = rows[: kw["scan_cap"]]
        if "offset" in kw:  # the windowed contract this engine replaced; kept to prove the old engine's faults
            window = [dict(r) for r in rows[kw["offset"]: kw["offset"] + kw["window"]]]
            return SimpleNamespace(rows=window, window_size=len(window), capped=capped)
        return RecordCandidates([CandidateKey(r["_key"], r["updatedAt"]) for r in rows], capped=capped)

    async def get_record_query_rows(self, org_id: str, *, record_ids: list[str], **kw: Any) -> list[dict[str, Any]]:  # noqa: ANN401
        self.row_reads.append(list(record_ids))
        by_id = {r["_key"]: r for r in self.rows}
        return [dict(by_id[i]) for i in reversed(record_ids) if i in by_id]


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


async def test_a_total_over_many_windows_is_exact_from_one_scan(readable: set[str]) -> None:
    rows = [_row(i, assignee="ravi") for i in range(1234)]
    readable.update(r["_key"] for r in rows)
    provider = FakeProvider(rows)
    result = await run_record_query(provider, CONTEXT, q(where=[{"person": {"role": "assignee", "ids": ["ravi"]}}]),
                                    window=100)
    assert (result.total, result.approximate) == (1234, False)
    # The candidates are scanned once, however many windows are read: a scan
    # per window costs the cap squared over the window.
    assert provider.scans == 1
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
    query = {"where": [{"field": "status", "in": ["open"]}], "limit": 3}
    first = await run_record_query(FakeProvider(rows), CONTEXT, q(**query))
    assert (first.total, first.unknown, len(first.rows), first.offset) == (4, {"status": 1}, 3, 0)
    assert first.next_cursor
    rest = await run_record_query(FakeProvider(rows), CONTEXT, q(**query, cursor=first.next_cursor))
    assert (len(rest.rows), rest.offset, rest.next_cursor) == (1, 3, None)


async def test_a_record_added_between_pages_neither_repeats_nor_shifts_a_row(readable: set[str]) -> None:
    rows = [_row(i, assignee="ravi") for i in range(6)]
    readable.update(r["_key"] for r in rows)
    provider = FakeProvider(rows)
    first = await run_record_query(provider, CONTEXT, q(limit=3))
    newer = _row(99, assignee="ravi")
    provider.rows.append(newer)
    readable.add(newer["_key"])
    rest = await run_record_query(provider, CONTEXT, q(limit=3, cursor=first.next_cursor))
    first_ids = [r["record_id"] for r in first.rows]
    rest_ids = [r["record_id"] for r in rest.rows]
    assert first_ids == ["r005", "r004", "r003"]
    assert rest_ids == ["r002", "r001", "r000"]


@pytest.mark.parametrize("cursor", ["3", "abc", "W10", "WyJuZXdlc3QiLDEsMl0", "x" * 600])
def test_a_cursor_the_tool_did_not_print_is_refused(cursor: str) -> None:
    with pytest.raises(ValueError, match="cursor"):
        q(cursor=cursor)


async def test_a_forged_cursor_shows_only_records_the_user_can_read(readable: set[str]) -> None:
    rows = [_row(i, assignee="ravi") for i in range(6)]
    readable.update(r["_key"] for r in rows[::2])
    forged = paging.encode_cursor(paging.NEWEST, (-(MAR_1 + 4), "r004"))
    result = await run_record_query(FakeProvider(rows), CONTEXT, q(cursor=forged))
    assert [r["record_id"] for r in result.rows] == ["r002", "r000"]
    assert result.total == 3


async def test_rows_are_read_only_for_records_the_access_check_admitted(readable: set[str]) -> None:
    rows = [_row(i, assignee="ravi") for i in range(6)]
    readable.update(r["_key"] for r in rows[:2])
    provider = FakeProvider(rows)
    await run_record_query(provider, CONTEXT, q())
    assert sorted(i for read in provider.row_reads for i in read) == ["r000", "r001"]


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


def test_a_row_never_carries_a_url_so_a_hidden_link_cannot_show() -> None:
    row = _row(1, assignee="ravi")
    row["webUrl"] = "https://secret.example/hidden"
    row["hideWeburl"] = True
    assert "url" not in engine.row_view(row) and "secret" not in str(engine.row_view(row))


RAVI_NODES = [
    {"id": "ravi", "name": "Ravi Kumar", "email": "ravi@x.io", "orgId": "org"},
    {"id": "ravi-zd", "name": "", "email": "RAVI@x.io", "orgId": "org"},
    {"id": "ravi-other-org", "name": "Ravi Kumar", "email": "ravi@x.io", "orgId": "elsewhere"},
]


async def test_a_person_is_every_node_of_their_email(readable: set[str]) -> None:
    rows = [_row(1, assignee="ravi"), _row(2, assignee="ravi-zd"), _row(3, assignee="ravi-other-org"),
            _row(4, assignee="ama")]
    readable.update(r["_key"] for r in rows)
    provider = FakeProvider(rows, nodes=RAVI_NODES)
    result = await run_record_query(provider, CONTEXT, q(where=[{"person": {"role": "assignee", "ids": ["ravi"]}}]))
    assert {r["record_id"] for r in result.rows} == {"r001", "r002"}
    assert provider.calls[0]["pushdown"]["people"][0]["ids"] == ["ravi", "ravi-zd"]
    assert provider.person_lookups == [("org", ["ravi"])]


async def test_a_person_nested_in_any_of_is_resolved_too(readable: set[str]) -> None:
    rows = [_row(1, assignee="ravi"), _row(2, assignee="ravi-zd"), _row(3, assignee="ama")]
    readable.update(r["_key"] for r in rows)
    result = await run_record_query(FakeProvider(rows, nodes=RAVI_NODES), CONTEXT, q(where=[{"any_of": [
        {"person": {"ids": ["ravi-zd"]}}, {"field": "status", "in": ["CLOSED"]}]}]))
    assert {r["record_id"] for r in result.rows} == {"r001", "r002"}


async def test_an_id_of_another_org_names_no_one_here(readable: set[str]) -> None:
    rows = [_row(1, assignee="ravi"), _row(2, assignee="ravi-other-org")]
    readable.update(r["_key"] for r in rows)
    provider = FakeProvider(rows, nodes=RAVI_NODES)
    result = await run_record_query(provider, CONTEXT, q(where=[{"person": {"ids": ["ravi-other-org"]}}]))
    # Not widened to the org's Ravi; the foreign node itself the provider never matches.
    assert provider.calls[0]["pushdown"]["people"][0]["ids"] == ["ravi-other-org"]
    assert result.total == 1


async def test_me_is_the_person_asking(readable: set[str]) -> None:
    rows = [_row(1, assignee="u"), _row(2, assignee="u-zd"), _row(3, assignee="ama")]
    readable.update(r["_key"] for r in rows)
    nodes = [{"id": "u", "name": "Me", "email": "me@x.io", "orgId": "org"},
             {"id": "u-zd", "name": "", "email": "me@x.io", "orgId": "org"}]
    result = await run_record_query(FakeProvider(rows, nodes=nodes), CONTEXT,
                                    q(where=[{"person": {"role": "assignee", "ids": ["Me"]}}]))
    assert {r["record_id"] for r in result.rows} == {"r001", "r002"}


async def test_people_group_by_person_with_a_readable_label(readable: set[str]) -> None:
    rows = [_row(1, assignee="ravi"), _row(2, assignee="ravi-zd"), _row(3, assignee="ama")]
    rows[0]["people"][0]["email"] = "ravi@x.io"
    rows[1]["people"][0].update(email="RAVI@x.io", name=None)
    readable.update(r["_key"] for r in rows)
    result = await run_record_query(FakeProvider(rows), CONTEXT, q(group_by={"by_person": True}))
    assert [(g.key, g.label, g.count) for g in result.groups] == [
        ("ravi", "Ravi (ravi@x.io)", 2), ("ama", "Ama", 1),
    ]


async def test_a_person_lookup_failure_is_an_error_not_an_empty_answer(readable: set[str]) -> None:
    provider = FakeProvider([])

    async def down(*_: Any, **__: Any) -> list[dict[str, Any]]:  # noqa: ANN401
        raise RuntimeError("graph down")

    provider.get_person_nodes = down  # type: ignore[method-assign]
    with pytest.raises(EntityAccessError):
        await run_record_query(provider, CONTEXT, q(where=[{"person": {"ids": ["ravi"]}}]))


async def test_sort_by_a_number_field_puts_records_without_it_last_either_way(readable: set[str]) -> None:
    rows = [_row(i, assignee="ravi", amount=a) for i, a in enumerate([500.0, None, 2000.0, 1000.0, None])]
    readable.update(r["_key"] for r in rows)
    desc = await run_record_query(FakeProvider(rows), CONTEXT, q(sort="amount desc"))
    asc = await run_record_query(FakeProvider(rows), CONTEXT, q(sort="Amount  ASC"))
    oldest = await run_record_query(FakeProvider(rows), CONTEXT, q(sort="oldest"))
    assert [r["record_id"] for r in desc.rows] == ["r002", "r003", "r000", "r004", "r001"]
    assert [r["record_id"] for r in asc.rows] == ["r000", "r003", "r002", "r004", "r001"]
    assert [r["record_id"] for r in oldest.rows] == ["r000", "r001", "r002", "r003", "r004"]


async def test_a_sorted_query_pages_by_its_own_cursor(readable: set[str]) -> None:
    rows = [_row(i, assignee="ravi", amount=float(i % 3)) for i in range(7)]
    readable.update(r["_key"] for r in rows)
    seen: list[str] = []
    cursor = None
    while True:
        page = await run_record_query(FakeProvider(rows), CONTEXT, q(sort="amount desc", limit=2, cursor=cursor))
        seen += [r["record_id"] for r in page.rows]
        if not page.next_cursor:
            break
        cursor = page.next_cursor
    everything = await run_record_query(FakeProvider(rows), CONTEXT, q(sort="amount desc", limit=50))
    assert seen == [r["record_id"] for r in everything.rows] and len(set(seen)) == 7
    with pytest.raises(ValueError, match="cursor"):
        q(sort="newest", cursor=cursor)


@pytest.mark.parametrize("sort", ["status asc", "amount up", "colour desc", "amount desc created asc", "person desc"])
def test_a_sort_on_anything_but_one_date_or_number_field_is_refused(sort: str) -> None:
    with pytest.raises(ValueError, match="sort is newest, oldest"):
        q(sort=sort)


async def test_records_waiting_for_classification_are_handed_back_bounded(readable: set[str]) -> None:
    rows = []
    for i in range(120):
        row = _row(i, assignee="ravi")
        row.update(extractionStatus="NOT_STARTED" if i % 2 else "COMPLETED", entities=[], indexingStatus="COMPLETED",
                   virtualRecordId=f"v{i}", contentRev="rev", mimeType="text/plain")
        rows.append(row)
    readable.update(r["_key"] for r in rows)
    result = await run_record_query(FakeProvider(rows), CONTEXT, q(where=[{"entity": {"type": "topic", "id": "t"}}]))
    assert (result.total, result.unknown) == (0, {"classification": 60})
    assert len(result.not_classified) == engine.MAX_TO_CLASSIFY
    assert {d["extractionStatus"] for d in result.not_classified} == {"NOT_STARTED"}
    assert set(result.not_classified[0]) == {"_key", "virtualRecordId", "contentRev", "connectorId", "connectorName",
                                             "mimeType", "indexingStatus", "extractionStatus"}
    shown = await run_record_query(FakeProvider(rows), CONTEXT, q(limit=5))
    assert [d["_key"] for d in shown.not_classified] == [r["record_id"] for r in shown.rows
                                                          if int(r["record_id"][1:]) % 2]
