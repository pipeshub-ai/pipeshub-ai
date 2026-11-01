"""find_records_by_value as a listing: pages of readable records, most recently
modified first, each with the values that matched, and a continue call."""

from __future__ import annotations

import base64
import json
import re
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from app.agents.actions.knowledge_graph.ops import values
from app.agents.actions.knowledge_graph.ops.entity_records import LOOKUP_FAILED_MSG
from app.modules.named_entities.linking.candidates import Candidate, CandidateSource
from app.modules.named_entities.linking.service import Linking, LinkResult, LinkStatus
from app.modules.retrieval.entity_permissions import EntityAccessContext
from app.modules.retrieval.selection_scope import SelectionScope
from app.services.graph_db.interface.graph_db_provider import AccessCheck

if TYPE_CHECKING:
    from collections.abc import Sequence

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


# A date window in epoch ms (2026-03-01 to 2026-03-02 UTC).
_WINDOW = (1_772_323_200_000, 1_772_409_600_000)


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
    # A date window ranks its hits by their dates; no spans read leaves them unranked.
    graph.get_named_entity_date_spans = AsyncMock(return_value={})
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
    # Loaded to list and again to cite, only ever for the readable ones.
    assert {tuple(call.args[0]) for call in graph.get_records_by_record_ids.await_args_list} == {("r001", "r005")}


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


# --- the listed records can be cited ---------------------------------------------------

async def test_a_citation_states_a_name_the_record_matched_beside_its_values(
    env: AsyncMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cite = AsyncMock(return_value={})
    monkeypatch.setattr(values, "cite_listed_records", cite)
    # A name is linked to its entities first; the citation states the entity it matched.
    _linker(monkeypatch, "matched", accepted=[_candidate("e-1", "Acme")])
    graph = _graph(_rows(1))
    graph.get_named_entity_values_in_records = AsyncMock(return_value={"r000": ["USD 1,250,000"]})
    await values.execute_find_records_by_value(_state(graph), name="acme", amount_min=1_000_000)
    assert cite.await_args.args[3]["r000"].startswith(
        'matched amount_min=1000000, entity_ids=["e-1"] · extracted values: USD 1,250,000'
    )


async def test_listed_records_are_citable_for_the_values_they_matched(
    env: AsyncMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agents.actions.knowledge_graph import listing_citations
    from app.utils.citations import (
        RECORD_PAGE_LISTED_FACTS,
        normalize_citations_and_chunks,
    )

    monkeypatch.setattr(listing_citations, "resolve_frontend_url", AsyncMock(return_value="https://ph.example"))
    graph = _graph(_rows(2))
    graph.get_named_entity_values_in_records = AsyncMock(return_value={"r000": ["USD 1,686,400"]})
    state = _state(graph)

    ok, body = await values.execute_find_records_by_value(state, amount_min=1_000_000, currency="USD")

    assert ok and body.count("Cite a fact this listing states") == 1
    refs = re.findall(r"cite=(\S+)", body)
    assert len(refs) == 2
    answers = (
        f"Sales forecast Q1 states USD 1,686,400 [source]({refs[0]}).",
        # A bare link to the record's page is resolved the same way.
        "Sales forecast Q1 states USD 1,686,400 [doc](https://ph.example/record/r000).",
    )
    for answer in answers:
        _, citations = normalize_citations_and_chunks(
            answer, [], state["tool_records"], ref_to_url=state["citation_ref_mapper"].ref_to_url,
        )
        assert len(citations) == 1
        assert citations[0]["metadata"]["blockType"] == RECORD_PAGE_LISTED_FACTS
        assert citations[0]["content"].startswith(
            'matched amount_min=1000000, currency="USD" · extracted values: USD 1,686,400',
        )


async def test_without_its_matched_values_a_citation_still_states_the_filter_matched(
    env: AsyncMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cite = AsyncMock(return_value={})
    monkeypatch.setattr(values, "cite_listed_records", cite)
    graph = _graph(_rows(1))
    graph.get_named_entity_values_in_records = AsyncMock(side_effect=RuntimeError("values lookup down"))
    ok, _ = await values.execute_find_records_by_value(_state(graph), amount_min=1_000_000, currency="USD")
    assert ok and cite.await_args.args[3]["r000"].startswith('matched amount_min=1000000, currency="USD"')


async def test_a_listing_whose_records_cannot_be_cited_is_shown_as_before(env: AsyncMock) -> None:
    graph = _graph(_rows(1))
    graph.get_records_by_record_ids = AsyncMock(side_effect=[
        [{"_key": "r000", "recordName": "doc-r000", "recordType": "FILE", "connectorId": APP}],
        RuntimeError("records store down"),
    ])
    ok, body = await values.execute_find_records_by_value(_state(graph), amount_min=1)
    assert ok and _listed(body) == ["r000"] and "cite=" not in body and "Cite a fact" not in body


async def test_a_zero_date_bound_is_neither_applied_nor_stated(env: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
    # A strict tool schema sends every parameter, so an unused bound arrives as 0.
    cite = AsyncMock(return_value={})
    monkeypatch.setattr(values, "cite_listed_records", cite)
    graph = _graph(_rows(1))
    graph.get_named_entity_date_spans = AsyncMock(return_value={})
    ok, body = await values.execute_find_records_by_value(_state(graph), date_from=1_700_000_000_000, date_to=0)
    assert ok and "date_to" not in body.splitlines()[0]
    assert "date_to" not in cite.await_args.args[3]["r000"]


# --- an entity and a value in one call -------------------------------------------------

def _candidate(entity_id: str, name: str, kind: str = "organization") -> Candidate:
    return Candidate(entity_id, kind, name, CandidateSource.EXACT, (), False)


def _linker(
    monkeypatch: pytest.MonkeyPatch, status: str, *,
    accepted: Sequence[Candidate] = (), namesakes: Sequence[Candidate] = (), timed_out: bool = False,
) -> AsyncMock:
    async def link(mentions: list, question: str) -> Linking:
        result = LinkResult(mentions[0], LinkStatus(status), accepted=list(accepted), namesakes=list(namesakes))
        names = {c.entity_id: c.name for c in [*accepted, *namesakes]}
        return Linking([result], timed_out=timed_out, verifier_called=False, names=names)

    linker = AsyncMock()
    linker.link = AsyncMock(side_effect=link)
    monkeypatch.setattr(values, "build_linker", lambda *_, **__: linker)
    return linker.link


def _query_arg(graph: AsyncMock) -> tuple[list[str] | None, Any]:
    args = graph.get_records_for_named_entities.await_args.args
    return args[1], args[2]


async def test_entity_ids_and_an_amount_are_one_query_and_the_continue_call_keeps_the_ids(env: AsyncMock) -> None:
    graph = _graph(_rows(30))
    graph.get_named_entity_mentions_in_records = AsyncMock(return_value=[
        {"entityId": "e-1", "recordId": "r000", "extractedNames": ["Pinecrest Hospitality"]},
    ])
    ok, body = await values.execute_find_records_by_value(
        _state(graph), entity_ids=["e-1"], amount_min=80_000, currency="USD",
    )
    assert ok
    ids, query = _query_arg(graph)
    assert ids == ["e-1"] and query.amount_min == 80_000 and query.currency == "USD"
    assert body.startswith("Records mentioning Pinecrest Hospitality and matching")
    assert 'entity_ids=["e-1"]' in body.split("More records match: ")[1]


async def test_a_short_id_from_search_entities_is_resolved(env: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
    graph = _graph(_rows(1))
    shortener = type("S", (), {
        "resolve": lambda self, short: {"E1": "e-full"}.get(short, short),
        "get_or_create_short_id": lambda self, full: full,
    })()
    monkeypatch.setattr(values, "get_record_id_shortener_if_enabled", lambda _state: shortener)
    await values.execute_find_records_by_value(_state(graph), entity_ids=["E1"], amount_min=1)
    assert _query_arg(graph)[0] == ["e-full"]


async def test_a_name_is_linked_and_every_accepted_entity_counts(env: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
    # The full name and its short form, split into two nodes, both accepted.
    link = _linker(monkeypatch, "checked", accepted=[_candidate("e-1", "Pinecrest Hospitality"), _candidate("e-2", "Pinecrest")])
    graph = _graph(_rows(30))
    ok, body = await values.execute_find_records_by_value(
        _state(graph), name="Pinecrest Hospitality", amount_min=80_000, currency="USD",
    )
    assert ok and _query_arg(graph)[0] == ["e-1", "e-2"]
    # The fake links any name: check it was asked about the one given.
    assert [m.name for m in link.await_args.args[0]] == ["Pinecrest Hospitality"]
    more = body.split("More records match: ")[1]
    assert 'entity_ids=["e-1", "e-2"]' in more and "name=" not in more


async def test_a_name_is_never_a_prefix(env: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
    _linker(monkeypatch, "not_found")
    graph = _graph(_rows(3))
    ok, body = await values.execute_find_records_by_value(_state(graph), name="Lum", amount_min=1)
    assert not ok and "no readable entity by this name" in body and "entity_ids" in body
    graph.get_records_for_named_entities.assert_not_called()


async def test_an_ambiguous_name_lists_who_it_may_be_and_lists_no_records(
    env: AsyncMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _linker(monkeypatch, "ambiguous", namesakes=[_candidate("p-1", "Sam Lee", "person"), _candidate("p-2", "Sam Ito", "person")])
    graph = _graph(_rows(3))
    ok, body = await values.execute_find_records_by_value(_state(graph), name="Sam", amount_min=1)
    assert not ok and "Sam Lee" in body and "Sam Ito" in body
    assert 'pass its id as entity_ids: Sam Lee: "p-1"; Sam Ito: "p-2"' in body
    graph.get_records_for_named_entities.assert_not_called()


async def test_a_lookup_cut_short_is_not_reported_as_no_such_entity(env: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
    _linker(monkeypatch, "not_found", timed_out=True)
    ok, body = await values.execute_find_records_by_value(_state(_graph(_rows(1))), name="Pinecrest", amount_min=1)
    assert (ok, body) == (False, values.NAME_TOO_SLOW_MSG)


async def test_a_name_and_entity_ids_are_a_union(env: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
    _linker(monkeypatch, "matched", accepted=[_candidate("e-2", "Pinecrest")])
    graph = _graph(_rows(1))
    await values.execute_find_records_by_value(_state(graph), name="Pinecrest", entity_ids=["e-1"], amount_min=1)
    assert _query_arg(graph)[0] == ["e-1", "e-2"]


async def test_an_entity_kind_does_not_narrow_ids_but_a_value_kind_still_does(
    env: AsyncMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The linker may accept a product filed as its company; "organization" must not drop it.
    link = _linker(monkeypatch, "matched", accepted=[_candidate("e-9", "Acme Cloud", "product")])
    graph = _graph(_rows(1))
    await values.execute_find_records_by_value(_state(graph), name="Acme Cloud", kinds=["organization", "currency"])
    assert _query_arg(graph)[0] == ["e-9"] and _query_arg(graph)[1].kinds == ["currency"]
    assert [m.name for m in link.await_args.args[0]] == ["Acme Cloud"]


async def test_too_many_entity_ids_are_refused(env: AsyncMock) -> None:
    ok, body = await values.execute_find_records_by_value(
        _state(_graph([])), entity_ids=[f"e-{i}" for i in range(21)], amount_min=1,
    )
    assert not ok and "at most 20" in body


async def test_an_entity_no_listed_record_spells_is_not_named(env: AsyncMock) -> None:
    # A foreign or unreadable id: its stored name must never be shown. The fake spells it
    # only on a record the user cannot read, so it would show if that record were asked about.
    graph = _graph(_rows(1))

    async def mentions(_org: str, entity_ids: list[str], record_ids: list[str]) -> list[dict[str, Any]]:
        hidden = {"entityId": "e-x", "recordId": "r-hidden", "extractedNames": ["Secret Corp"]}
        return [hidden] if "r-hidden" in record_ids else []

    graph.get_named_entity_mentions_in_records = AsyncMock(side_effect=mentions)
    ok, body = await values.execute_find_records_by_value(_state(graph), entity_ids=["e-x"], amount_min=1)
    assert ok and body.startswith("Records matching") and "Secret Corp" not in body
    assert graph.get_named_entity_mentions_in_records.await_args.args[2] == ["r000"]


@pytest.mark.parametrize("error", [RuntimeError("verifier exploded"), TimeoutError()])
async def test_a_name_lookup_that_fails_or_runs_out_of_time_is_a_failure_not_a_miss(
    env: AsyncMock, monkeypatch: pytest.MonkeyPatch, error: Exception,
) -> None:
    linker = AsyncMock()
    linker.link = AsyncMock(side_effect=error)
    monkeypatch.setattr(values, "build_linker", lambda *_, **__: linker)
    graph = _graph(_rows(1))
    ok, body = await values.execute_find_records_by_value(_state(graph), name="Pinecrest", amount_min=1)
    expected = values.NAME_TOO_SLOW_MSG if isinstance(error, TimeoutError) else LOOKUP_FAILED_MSG
    assert (ok, body) == (False, expected)
    graph.get_records_for_named_entities.assert_not_called()


async def test_an_access_check_failure_while_linking_is_a_lookup_failure(
    env: AsyncMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.retrieval.entity_permissions import EntityAccessError

    linker = AsyncMock()
    linker.link = AsyncMock(side_effect=EntityAccessError("down"))
    monkeypatch.setattr(values, "build_linker", lambda *_, **__: linker)
    ok, body = await values.execute_find_records_by_value(_state(_graph(_rows(1))), name="Pinecrest")
    assert (ok, body) == (False, LOOKUP_FAILED_MSG)


async def test_names_the_check_could_not_settle_are_refused_with_the_candidates(
    env: AsyncMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.named_entities.linking.service import LinkResult

    async def link(mentions: list, question: str) -> Linking:
        unchecked = [_candidate("e-1", "Pinecrest Hospitality")]
        result = LinkResult(mentions[0], LinkStatus.UNVERIFIED, unchecked=unchecked)
        return Linking([result], timed_out=False, verifier_called=True, names={"e-1": "Pinecrest Hospitality"})

    linker = AsyncMock()
    linker.link = AsyncMock(side_effect=link)
    monkeypatch.setattr(values, "build_linker", lambda *_, **__: linker)
    ok, body = await values.execute_find_records_by_value(_state(_graph(_rows(1))), name="Pinecrest", amount_min=1)
    assert not ok and "Pinecrest Hospitality" in body and "could not be checked" in body


async def test_a_name_alone_lists_the_records_that_mention_it(env: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
    _linker(monkeypatch, "matched", accepted=[_candidate("e-1", "Pinecrest")])
    graph = _graph(_rows(2))
    ok, body = await values.execute_find_records_by_value(_state(graph), name="Pinecrest")
    ids, query = _query_arg(graph)
    assert ok and ids == ["e-1"] and query is None and _listed(body) == ["r000", "r001"]


async def test_entity_ids_with_only_entity_kinds_still_constrain_by_the_ids(env: AsyncMock) -> None:
    graph = _graph(_rows(1))
    ok, _ = await values.execute_find_records_by_value(_state(graph), entity_ids=["e-1"], kinds=["Organization"])
    ids, query = _query_arg(graph)
    assert ok and ids == ["e-1"] and query is None


async def test_kinds_are_matched_whatever_their_case(env: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
    link = _linker(monkeypatch, "matched", accepted=[_candidate("e-9", "Acme Cloud", "product")])
    graph = _graph(_rows(1))
    await values.execute_find_records_by_value(_state(graph), name="Acme Cloud", kinds=["Organization", "CURRENCY"])
    assert _query_arg(graph)[1].kinds == ["currency"]
    # The entity kind still steers the link.
    assert link.await_args.args[0][0].kind_hint.value == "organization"


async def test_an_unknown_kind_beside_entity_ids_still_points_to_record_types(env: AsyncMock) -> None:
    ok, body = await values.execute_find_records_by_value(
        _state(_graph(_rows(1))), entity_ids=["e-1"], kinds=["invoice"],
    )
    assert not ok and "record_types" in body


async def test_what_an_accepted_name_may_leave_out_is_said(env: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.named_entities.linking.service import LinkResult

    async def link(mentions: list, question: str) -> Linking:
        result = LinkResult(
            mentions[0], LinkStatus.MATCHED, accepted=[_candidate("e-1", "Acme")],
            left_out=[_candidate("e-2", "Acme Corp")],
        )
        return Linking([result], timed_out=False, verifier_called=True, names={"e-1": "Acme", "e-2": "Acme Corp"})

    linker = AsyncMock()
    linker.link = AsyncMock(side_effect=link)
    monkeypatch.setattr(values, "build_linker", lambda *_, **__: linker)
    ok, body = await values.execute_find_records_by_value(_state(_graph(_rows(1))), name="Acme", amount_min=1)
    assert ok and "Note on name: Other names that start with it, not included: Acme Corp" in body


async def test_several_entities_are_named_as_one_of_them(env: AsyncMock) -> None:
    """The ids match a record that mentions any one of them, so the header must not read as both."""
    graph = _graph(_rows(2))
    graph.get_named_entity_mentions_in_records = AsyncMock(return_value=[
        {"entityId": "e-1", "recordId": "r000", "extractedNames": ["Acme"]},
        {"entityId": "e-2", "recordId": "r001", "extractedNames": ["Contoso"]},
    ])
    ok, body = await values.execute_find_records_by_value(_state(graph), entity_ids=["e-1", "e-2"], amount_min=1)
    assert ok and body.startswith("Records mentioning one of Acme, Contoso and matching")


def test_entity_ids_with_a_value_kind_are_two_constraints_not_an_impossible_one() -> None:
    from app.modules.named_entities.graph_ops import (
        NamedEntityQuery,
        filter_constraints,
    )

    constraints = filter_constraints(["e-1"], NamedEntityQuery(kinds=["currency"]))
    assert [[(part.source, part.binds.get("ids") or part.binds.get("kinds")) for part in c] for c in constraints] == [
        [("node", ["e-1"])], [("value", ["currency"])],
    ]


# --- a date listing names the day before the ranges that only cover it ----------------

async def test_records_stating_a_date_inside_the_window_come_before_wider_ranges(env: AsyncMock) -> None:
    # r000 is the newest but only covers the day through a year-long range.
    graph = _graph(_rows(4))
    day, year = 86_400_000, 365 * 86_400_000
    # r000: a year-long range; r001: its quarter; r002, r003: name the day.
    graph.get_named_entity_date_spans = AsyncMock(return_value={
        "r000": (False, year), "r001": (False, 92 * day), "r002": (True, day), "r003": (True, day),
    })
    ok, body = await values.execute_find_records_by_value(_state(graph), date_from=_WINDOW[0], date_to=_WINDOW[1])
    assert ok and _listed(body) == ["r002", "r003", "r001", "r000"]
    assert "naming a date in the range first" in body.splitlines()[0]
    org, ids, start, end = graph.get_named_entity_date_spans.await_args.args
    assert (org, sorted(ids), start, end) == ("org", ["r000", "r001", "r002", "r003"], *_WINDOW)


async def test_covering_dates_come_narrowest_span_first_whatever_their_age(env: AsyncMock) -> None:
    graph = _graph(_rows(3))
    day = 86_400_000
    # r000 (newest) covers the day with a 30-day span, r001 with a 2-day trip; r002 names it.
    graph.get_named_entity_date_spans = AsyncMock(return_value={
        "r000": (False, 30 * day), "r001": (False, 2 * day), "r002": (True, day),
    })
    ok, body = await values.execute_find_records_by_value(_state(graph), date_from=_WINDOW[0], date_to=_WINDOW[1])
    assert ok and _listed(body) == ["r002", "r001", "r000"]


async def test_a_failed_date_span_read_is_a_lookup_failure_not_a_crash(env: AsyncMock) -> None:
    graph = _graph(_rows(3))
    graph.get_named_entity_date_spans = AsyncMock(side_effect=RuntimeError("driver down"))
    ok, body = await values.execute_find_records_by_value(_state(graph), date_from=_WINDOW[0], date_to=_WINDOW[1])
    assert (ok, body) == (False, values.LOOKUP_FAILED_MSG)


async def test_pages_of_a_ranked_date_listing_run_across_tiers_without_repeats_or_gaps(env: AsyncMock) -> None:
    graph = _graph(_rows(30))
    graph.get_named_entity_date_spans = AsyncMock(return_value={
        f"r{i:03}": (i % 3 == 0, 86_400_000 if i % 3 == 0 else 400 * 86_400_000) for i in range(30)
    })
    seen: list[str] = []
    cursor = None
    for _ in range(10):
        ok, body = await values.execute_find_records_by_value(
            _state(graph), date_from=_WINDOW[0], date_to=_WINDOW[1], limit=7, cursor=cursor,
        )
        seen += _listed(body)
        cursor = _cursor(body)
        if cursor is None:
            break
    inside = [f"r{i:03}" for i in range(0, 30, 3)]
    assert seen == inside + [f"r{i:03}" for i in range(30) if f"r{i:03}" not in inside]


async def test_a_listing_without_a_date_window_is_not_ranked(env: AsyncMock) -> None:
    graph = _graph(_rows(3))
    graph.get_named_entity_date_spans = AsyncMock(return_value={})
    ok, body = await values.execute_find_records_by_value(_state(graph), amount_min=1)
    assert ok and _listed(body) == ["r000", "r001", "r002"] and "in the range first" not in body
    graph.get_named_entity_date_spans.assert_not_awaited()


@pytest.mark.parametrize(("cursor", "resume"), [
    ("9981:r019", (0, -9981, "r019")),
    ("r1:9981:r019", (1, -9981, "r019")),
    # A record id holding a colon still reads as the first tier's two-part form,
    # even when its first part is a number.
    ("9981:a:b", (0, -9981, "a:b")),
    ("9981:2:abc", (0, -9981, "2:abc")),
    ("40", 40),
])
def test_a_cursor_resumes_in_its_tier(cursor: str, resume: object) -> None:
    assert values._parse_resume(cursor) == resume


async def test_one_day_asked_as_a_point_is_the_whole_day(env: AsyncMock) -> None:
    graph = _graph(_rows(1))
    graph.get_named_entity_date_spans = AsyncMock(return_value={"r000": (True, 86_400_000)})
    day = 1_751_932_800_000  # 2025-07-08 00:00 UTC
    ok, body = await values.execute_find_records_by_value(_state(graph), date_from=day, date_to=day)
    query = graph.get_records_for_named_entities.await_args.args[2]
    assert ok and (query.date_start_ms, query.date_end_ms) == (day, day + 86_400_000)
    _, _, start, end = graph.get_named_entity_date_spans.await_args.args
    assert (start, end) == (day, day + 86_400_000)
    assert f"date_to={day + 86_400_000}" in body.splitlines()[0]


# --- dates are ISO, and a date-only end takes in its whole day -------------------------

_DAY = 86_400_000
_MAR_1 = 1_772_323_200_000  # 2026-03-01T00:00:00Z


def _window(graph: AsyncMock) -> tuple[int, int]:
    query = graph.get_records_for_named_entities.await_args.args[2]
    return query.date_start_ms, query.date_end_ms


@pytest.mark.parametrize(("date_from", "date_to", "window"), [
    # A month, both ends as dates: through the last millisecond of March 31.
    ("2026-03-01", "2026-03-31", (_MAR_1, _MAR_1 + 31 * _DAY - 1)),
    # One day asked as a point is that whole day.
    ("2026-03-01", "2026-03-01", (_MAR_1, _MAR_1 + _DAY - 1)),
    # A date-time end is used as given.
    ("2026-03-01", "2026-03-02T00:00:00Z", (_MAR_1, _MAR_1 + _DAY)),
    # An end past its month's last day is clamped, as the search tools do.
    ("2026-02-01", "2026-02-30", (_MAR_1 - 28 * _DAY, _MAR_1 - 1)),
    # A Python caller's epoch ms is unchanged.
    (_MAR_1, _MAR_1 + _DAY, (_MAR_1, _MAR_1 + _DAY)),
])
async def test_dates_are_iso_and_a_date_only_end_is_the_whole_day(
    env: AsyncMock, date_from: str | int, date_to: str | int, window: tuple[int, int],
) -> None:
    graph = _graph(_rows(1))
    ok, _ = await values.execute_find_records_by_value(_state(graph), date_from=date_from, date_to=date_to)
    assert ok and _window(graph) == window


@pytest.mark.parametrize(("date_from", "message"), [
    ("2026-03-01T00:00:00", "has no timezone"),
    ("March 1", "is not a date"),
    ("1772323200000", "is not a date"),
    ("2026-W10-1", "is not a date"),
    # A compact date or a year sent as a number would search January 1970.
    (20260301, "is not a date"),
    (2026.0, "is not a date"),
    (2026, "is not a date"),
    (float("nan"), "is not a date"),
    (float("inf"), "is not a date"),
])
async def test_a_date_that_does_not_read_is_refused_with_why(env: AsyncMock, date_from: str | float, message: str) -> None:
    graph = _graph(_rows(1))
    ok, body = await values.execute_find_records_by_value(_state(graph), date_from=date_from)
    assert not ok and message in body and body.startswith("date_from")
    graph.get_records_for_named_entities.assert_not_awaited()


async def test_a_compact_date_is_refused_never_read_as_epoch_milliseconds(env: AsyncMock) -> None:
    """"20260301" as digits would be 20,260,301 ms: a window in January 1970."""
    graph = _graph(_rows(1))
    ok, body = await values.execute_find_records_by_value(_state(graph), date_from="20260301")
    assert (ok, body) == (False, "date_from: '20260301' is not a date. Use 'YYYY-MM-DD' or a full datetime with a timezone.")
    graph.get_records_for_named_entities.assert_not_awaited()


async def test_the_continue_call_repeats_the_dates_as_written_and_passes_the_schema(env: AsyncMock) -> None:
    from app.agent_loop_lib.tools.toolset import ToolsetBuilder
    from app.agents.actions.knowledge_graph.knowledge_graph import KnowledgeGraph

    graph = _graph(_rows(30))
    ok, body = await values.execute_find_records_by_value(_state(graph), date_from="2026-03-01", date_to="2026-03-31")
    assert ok and 'date_from="2026-03-01", date_to="2026-03-31"' in body
    tools = ToolsetBuilder(KnowledgeGraph(state={}), name="knowledgegraph", description="", path_prefix="/tools/knowledgegraph").tools
    (tool,) = [t for t in tools if t.name == "knowledgegraph__find_records_by_value"]
    tool.validate({"date_from": "2026-03-01", "date_to": "2026-03-31", "cursor": _cursor(body)})


async def test_a_float_epoch_is_read_and_a_blank_end_is_left_open(env: AsyncMock) -> None:
    graph = _graph(_rows(1))
    ok, _ = await values.execute_find_records_by_value(_state(graph), date_from=float(_MAR_1), date_to="  ")
    assert ok and _window(graph)[0] == _MAR_1


async def test_an_inverted_date_range_names_its_fields(env: AsyncMock) -> None:
    graph = _graph(_rows(1))
    ok, body = await values.execute_find_records_by_value(_state(graph), date_from="2026-04-01", date_to="2026-03-01")
    assert (ok, body) == (False, "date_from must be on or before date_to.")
    graph.get_records_for_named_entities.assert_not_awaited()


async def test_an_unset_end_sent_as_zero_or_blank_is_not_repeated(env: AsyncMock) -> None:
    for unset in (0, "  "):
        graph = _graph(_rows(30))
        ok, body = await values.execute_find_records_by_value(_state(graph), date_from="2026-03-01", date_to=unset)
        assert ok and "date_to" not in body and 'date_from="2026-03-01"' in body


@pytest.mark.parametrize("epoch_ms", [-86_400_000, 86_400_000, 99_999_999_999, 12_345_678])
async def test_epoch_ms_before_1973_and_before_1970_still_read(env: AsyncMock, epoch_ms: int) -> None:
    graph = _graph(_rows(1))
    ok, _ = await values.execute_find_records_by_value(_state(graph), date_from=epoch_ms)
    assert ok and _window(graph)[0] == epoch_ms
