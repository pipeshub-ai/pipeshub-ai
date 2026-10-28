"""find_records_by_value as a listing: pages of readable records, most recently
modified first, each with the values that matched, and a continue call."""

from __future__ import annotations

import base64
import json
import re
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.agents.actions.knowledge_graph.ops import values
from app.agents.actions.knowledge_graph.ops.entity_records import LOOKUP_FAILED_MSG
from app.modules.retrieval.entity_permissions import EntityAccessContext
from app.modules.retrieval.selection_scope import SelectionScope
from app.services.graph_db.interface.graph_db_provider import AccessCheck

pytestmark = pytest.mark.asyncio

APP = "app-1"


def _context(**extra: object) -> EntityAccessContext:
    fields = {"app_level_app_ids": frozenset({APP}), **extra}
    return EntityAccessContext(
        org_id="org", user_key="uk", record_level_app_ids=frozenset(), record_group_ids=frozenset(),
        app_names={APP: "Invoices"}, **fields,
    )


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    monkeypatch.setattr(values, "_named_entities_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(values, "resolve_frontend_url", AsyncMock(return_value=None))
    context = AsyncMock(return_value=_context())
    monkeypatch.setattr(values, "load_entity_access_context", context)
    return context


def _graph(rows: list[dict[str, Any]], readable: set[str] | None = None) -> AsyncMock:
    """``rows``: hits ``{recordId, recordType, sortTs}``; every hit is readable unless ``readable`` says."""
    graph = AsyncMock()
    graph.get_records_for_named_entities = AsyncMock(return_value={
        "hits": [{"virtualRecordId": f"v-{r['recordId']}", "connectorId": APP, **r} for r in rows],
        "truncated": False,
    })
    graph.filter_accessible_record_ids = AsyncMock(
        side_effect=lambda ids, *_: set(ids) if readable is None else set(ids) & readable,
    )
    graph.get_records_by_record_ids = AsyncMock(side_effect=lambda ids, *_: [
        {"_key": i, "recordName": f"doc-{i}", "recordType": "FILE", "connectorId": APP} for i in ids
    ])
    graph.get_named_entity_values_in_records = AsyncMock(return_value={})
    return graph


def _rows(count: int, record_type: str = "FILE") -> list[dict[str, Any]]:
    # r000 is the newest.
    return [{"recordId": f"r{i:03}", "recordType": record_type, "sortTs": 10_000 - i} for i in range(count)]


def _state(graph: AsyncMock) -> dict[str, Any]:
    return {"graph_provider": graph, "org_id": "org", "user_id": "u1", "config_service": object()}


def _listed(body: str) -> list[str]:
    return re.findall(r"record_id=([\w-]+)", body)


def _cursor(body: str) -> str | None:
    found = re.search(r'cursor="([^"]+)"', body)
    return found.group(1) if found else None


async def test_pages_run_to_the_end_without_repeats_or_gaps(env: AsyncMock) -> None:
    graph = _graph(_rows(45))
    seen: list[str] = []
    cursor = None
    bodies = []
    for _ in range(3):
        ok, body = await values.execute_find_records_by_value(
            _state(graph), amount_min=50_000, currency="USD", cursor=cursor,
        )
        assert ok
        bodies.append(body)
        seen.extend(_listed(body))
        cursor = _cursor(body)
    assert seen == [f"r{i:03}" for i in range(45)]
    assert cursor is None and "That was the last page." in bodies[-1]
    assert 'knowledgegraph__find_records_by_value(amount_min=50000, currency="USD", cursor="9981:r019")' in bodies[0]

    ok, body = await values.execute_find_records_by_value(_state(graph), amount_min=50_000, currency="USD", cursor="45")
    assert (ok, body) == (True, values.NO_FURTHER_RECORDS_MSG)


async def test_a_complete_first_page_says_that_is_all(env: AsyncMock) -> None:
    ok, body = await values.execute_find_records_by_value(_state(_graph(_rows(3))), kinds=["date"])
    assert ok and _listed(body) == ["r000", "r001", "r002"]
    assert "That is all 3." in body and "cursor=" not in body
    assert body.startswith('Records matching kinds=["date"], newest first (3 shown):')


async def test_most_recently_modified_comes_first_and_ties_fall_back_to_the_id(env: AsyncMock) -> None:
    rows = [
        {"recordId": "b", "recordType": "FILE", "sortTs": 5},
        {"recordId": "a", "recordType": "FILE", "sortTs": 5},
        {"recordId": "c", "recordType": "FILE", "sortTs": 9},
    ]
    ok, body = await values.execute_find_records_by_value(_state(_graph(rows)), kinds=["date"])
    assert ok and _listed(body) == ["c", "a", "b"]


async def test_the_cursor_is_a_position_and_later_pages_check_only_their_own_window(env: AsyncMock) -> None:
    rows = _rows(3000)
    readable = {f"r{i:03}" for i in range(0, 3000, 2)}
    graph = _graph(rows, readable)

    ok, body = await values.execute_find_records_by_value(_state(graph), kinds=["date"], cursor="2400")

    assert ok and _listed(body) == [f"r{i:03}" for i in range(2400, 2440, 2)]
    assert _cursor(body) == "7562:r2438"
    checked = graph.filter_accessible_record_ids.await_args_list[0].args[0]
    assert checked[0] == "r2400" and len(graph.filter_accessible_record_ids.await_args_list) == 1


async def test_unreadable_records_are_never_named(env: AsyncMock) -> None:
    graph = _graph(_rows(10), readable={"r001", "r005"})
    ok, body = await values.execute_find_records_by_value(_state(graph), kinds=["date"])
    assert ok and _listed(body) == ["r001", "r005"]
    assert re.findall(r"doc-(r\d+)", body) == ["r001", "r005"]
    graph.get_records_by_record_ids.assert_awaited_once_with(["r001", "r005"], "org")


async def test_record_types_are_kept_before_any_permission_check(env: AsyncMock) -> None:
    graph = _graph([*_rows(2, "MAIL"), {"recordId": "f1", "recordType": "FILE", "sortTs": 1}])
    ok, body = await values.execute_find_records_by_value(_state(graph), kinds=["date"], record_types=["file"])
    assert ok and _listed(body) == ["f1"]
    assert graph.filter_accessible_record_ids.await_args.args[0] == ["f1"]
    assert 'record_types=["FILE"]' in body


async def test_matched_values_are_shown_per_record(env: AsyncMock) -> None:
    graph = _graph(_rows(2))
    graph.get_named_entity_values_in_records = AsyncMock(return_value={"r000": ["USD 52,000.00", "USD 61,000.00"]})

    ok, body = await values.execute_find_records_by_value(_state(graph), amount_min=50_000, currency="USD")

    lines = body.splitlines()
    assert lines[1].endswith("| matched: USD 52,000.00; USD 61,000.00") and "matched" not in lines[2]
    org, query, ids, per_record = graph.get_named_entity_values_in_records.await_args.args
    assert (org, ids, per_record) == ("org", ["r000", "r001"], values._VALUES_PER_RECORD)
    assert (query.amount_min, query.currency) == (50_000, "USD")


async def test_a_record_deleted_since_it_matched_is_dropped(env: AsyncMock) -> None:
    graph = _graph(_rows(2))
    graph.get_records_by_record_ids = AsyncMock(return_value=[{"_key": "r001", "recordName": "doc-r001"}])
    ok, body = await values.execute_find_records_by_value(_state(graph), kinds=["date"])
    assert ok and _listed(body) == ["r001"]


async def test_a_failed_details_fetch_is_a_failure_not_an_empty_page(env: AsyncMock) -> None:
    graph = _graph(_rows(2))
    graph.get_records_by_record_ids = AsyncMock(return_value=[])
    assert await values.execute_find_records_by_value(_state(graph), kinds=["date"]) == (False, LOOKUP_FAILED_MSG)


@pytest.mark.parametrize(("limit", "shown"), [(0, 20), (5, 5), (500, 50)])
async def test_limit_is_bounded_and_a_non_default_one_is_kept_in_the_continue_call(
    env: AsyncMock, limit: int, shown: int,
) -> None:
    ok, body = await values.execute_find_records_by_value(_state(_graph(_rows(80))), kinds=["date"], limit=limit)
    assert ok and len(_listed(body)) == shown
    assert ("limit=" in body) is (shown != 20)


async def test_a_paraphrased_cursor_envelope_is_accepted_and_garbage_is_refused(env: AsyncMock) -> None:
    graph = _graph(_rows(30))
    envelope = base64.b64encode(json.dumps({"offset": 20}).encode()).decode()
    ok, body = await values.execute_find_records_by_value(_state(graph), kinds=["date"], cursor=envelope)
    assert ok and _listed(body)[0] == "r020"
    for bad in ("x", "-1"):
        ok, message = await values.execute_find_records_by_value(_state(graph), kinds=["date"], cursor=bad)
        assert not ok and "Invalid cursor" in message


async def test_an_unknown_kind_points_to_record_types(env: AsyncMock) -> None:
    ok, message = await values.execute_find_records_by_value(_state(_graph([])), kinds=["invoice"])
    assert not ok
    assert message.startswith("Unknown entity kind: invoice.") and "record_types=[...]" in message


async def test_a_too_broad_filter_counted_over_readable_records_lists_up_to_the_scan_limit(env: AsyncMock) -> None:
    graph = _graph([])
    graph.get_records_for_named_entities = AsyncMock(side_effect=[
        {"hits": [], "truncated": True},
        {"hits": [], "truncated": True},
        {"hits": [{"recordId": "r1", "virtualRecordId": "v1", "connectorId": APP}], "truncated": False},
    ])
    graph.get_accessible_virtual_record_ids = AsyncMock(return_value={"v1": "r1"})

    ok, body = await values.execute_find_records_by_value(_state(graph), kinds=["date"])

    assert ok and _listed(body) == ["r1"]
    assert graph.get_records_for_named_entities.await_args.kwargs["limit"] == values._SCAN_LIMIT


async def test_inside_a_selection_paging_still_applies_the_selection_gate(env: AsyncMock) -> None:
    env.return_value = _context(row_scopes=(SelectionScope(groups={"proj-a": APP}),))
    graph = _graph(_rows(30))
    graph.get_knowledge_hub_access_v3 = AsyncMock(return_value={})
    in_scope = {f"r{i:03}" for i in range(0, 30, 3)}
    graph.check_access = AsyncMock(side_effect=lambda user_key, org_id, *, node_ids, **_: AccessCheck(
        node_ids=frozenset(node_ids), node_ids_in_scope=frozenset(node_ids) & in_scope,
    ))

    ok, body = await values.execute_find_records_by_value(_state(graph), kinds=["date"], limit=5)

    assert ok and _listed(body) == ["r000", "r003", "r006", "r009", "r012"]
    assert _cursor(body) == "9988:r012"


async def test_listed_records_are_remembered_by_name_and_cited(env: AsyncMock) -> None:
    state = _state(_graph(_rows(2)))
    await values.execute_find_records_by_value(state, kinds=["date"])
    assert state["known_record_ids"] == {"r000", "r001"}


async def test_record_ids_are_shortened_when_the_turn_shortens_them(env: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Short:
        def get_or_create_short_id(self, record_id: str) -> str:
            return f"s{record_id}"

    monkeypatch.setattr(values, "get_record_id_shortener_if_enabled", lambda state: _Short())
    ok, body = await values.execute_find_records_by_value(_state(_graph(_rows(1))), kinds=["date"])
    assert ok and _listed(body) == ["sr000"]


async def test_a_record_changed_between_pages_neither_repeats_nor_hides_the_rest(env: AsyncMock) -> None:
    rows = _rows(30)
    graph = _graph(rows)
    ok, first = await values.execute_find_records_by_value(_state(graph), kinds=["date"])
    # Before page 2: a new record matches as the newest, and r025 is modified.
    rows[25] = {**rows[25], "sortTs": 99_999}
    graph = _graph([{"recordId": "new", "recordType": "FILE", "sortTs": 100_000}, *rows])

    ok, second = await values.execute_find_records_by_value(_state(graph), kinds=["date"], cursor=_cursor(first))

    assert ok and _listed(second) == [f"r{i:03}" for i in range(20, 30) if i != 25]
    assert not set(_listed(first)) & set(_listed(second))


@pytest.mark.parametrize("limit", [1, 2, 3, 6])
async def test_paging_across_small_batches_lists_every_readable_record_once(
    env: AsyncMock, monkeypatch: pytest.MonkeyPatch, limit: int,
) -> None:
    monkeypatch.setattr(values, "_CHECK_BATCH", 3)
    readable = {"r000", "r002", "r003", "r005", "r008", "r009"}
    graph = _graph(_rows(10), readable)
    seen: list[str] = []
    cursor = None
    for _ in range(12):
        ok, body = await values.execute_find_records_by_value(_state(graph), kinds=["date"], limit=limit, cursor=cursor)
        assert ok
        seen.extend(_listed(body))
        cursor = _cursor(body)
        if cursor is None:
            break
    assert seen == sorted(readable)


async def test_a_page_stops_at_the_scan_budget_and_hands_back_a_cursor(
    env: AsyncMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(values, "_CHECK_BATCH", 10)
    monkeypatch.setattr(values, "_MAX_SCAN", 20)
    graph = _graph(_rows(100), readable={"r050"})

    ok, body = await values.execute_find_records_by_value(_state(graph), kinds=["date"])

    assert ok and _listed(body) == [] and "more remain unchecked" in body
    assert _cursor(body) == "9981:r019" and graph.filter_accessible_record_ids.await_count == 2
    ok, body = await values.execute_find_records_by_value(_state(graph), kinds=["date"], cursor="9981:r019")
    ok, body = await values.execute_find_records_by_value(_state(graph), kinds=["date"], cursor=_cursor(body))
    assert _listed(body) == ["r050"]


async def test_a_failed_values_lookup_still_lists_the_records(env: AsyncMock) -> None:
    graph = _graph(_rows(2))
    graph.get_named_entity_values_in_records = AsyncMock(side_effect=RuntimeError("db down"))
    ok, body = await values.execute_find_records_by_value(_state(graph), amount_min=1, currency="USD")
    assert ok and _listed(body) == ["r000", "r001"] and "matched" not in body


async def test_an_unknown_record_type_lists_the_valid_ones(env: AsyncMock) -> None:
    graph = _graph(_rows(2))
    ok, message = await values.execute_find_records_by_value(_state(graph), kinds=["date"], record_types=["invoice"])
    assert not ok and message.startswith("Unknown record type: INVOICE.") and "FILE" in message
    graph.get_records_for_named_entities.assert_not_called()



async def test_source_ids_keep_one_source_and_are_repeated_in_the_continue_call(env: AsyncMock) -> None:
    env.return_value = _context(app_level_app_ids=frozenset({APP, "kb-other"}))
    graph = _graph([])
    graph.get_records_for_named_entities = AsyncMock(return_value={"hits": [
        *({"recordId": f"r{i:03}", "virtualRecordId": "v", "connectorId": APP, "sortTs": 100 - i} for i in range(25)),
        {"recordId": "other", "virtualRecordId": "v", "connectorId": "kb-other", "sortTs": 999},
    ], "truncated": False})
    ok, body = await values.execute_find_records_by_value(_state(graph), kinds=["date"], source_ids=[APP])
    assert ok and "other" not in _listed(body) and len(_listed(body)) == 20
    assert f'source_ids=["{APP}"]' in body


async def test_a_source_outside_the_conversation_says_so(env: AsyncMock) -> None:
    graph = _graph(_rows(3))
    ok, body = await values.execute_find_records_by_value(_state(graph), kinds=["date"], source_ids=["kb-hidden"])
    assert (ok, body) == (True, values.NO_SOURCE_MSG)
    graph.filter_accessible_record_ids.assert_not_called()


async def test_percent_bounds_written_as_whole_numbers_get_a_hint(env: AsyncMock) -> None:
    ok, body = await values.execute_find_records_by_value(_state(_graph([])), percent_min=15, percent_max=20)
    assert ok and "0.15 for 15%" in body



async def test_a_turn_with_no_sources_and_none_named_just_matches_nothing(env: AsyncMock) -> None:
    env.return_value = _context(app_level_app_ids=frozenset())
    ok, body = await values.execute_find_records_by_value(_state(_graph(_rows(3))), kinds=["date"])
    assert (ok, body) == (True, values.NO_MATCH_MSG)
