"""``find_co_mentioned_entities``: entities mentioned with one entity, counted over
the records the user can read and spelled as those records spell them."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.knowledge_graph.ops import co_mentions
from app.modules.retrieval.entity_permissions import EntityAccessError, EntityRecordPage

ACME = "e-acme"


def _record(key: str, virtual: str | None = None) -> dict[str, Any]:
    return {"_key": key, "virtualRecordId": virtual or f"v-{key}", "recordName": f"doc {key}", "recordType": "FILE"}


def _mention(record: str, entity: str, kind: str, *names: str) -> dict[str, Any]:
    return {"recordId": record, "entityId": entity, "kind": kind, "extractedNames": list(names)}


# r1..r3 mention Acme and are readable. A record the user cannot read never reaches
# the mentions read, because only the listing's records are passed to it.
_ROWS = [
    _mention("r1", ACME, "organization", "Acme Corp"),
    _mention("r2", ACME, "organization", "Acme"),
    _mention("r3", ACME, "organization", "Acme Corp"),
    _mention("r1", "p-jane", "person", "Jane Doe"),
    _mention("r2", "p-jane", "person", "Jane Doe"),
    _mention("r3", "p-jane", "person", "Jane"),
    _mention("r2", "p-raj", "person", "Raj"),
    _mention("r1", "o-globex", "organization", "Globex"),
    _mention("r1", "x-mail", "email", "jane@acme.com"),
]


@pytest.fixture
def listing(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    monkeypatch.setattr(co_mentions, "load_entity_access_context", AsyncMock(return_value=MagicMock(org_id="org")))
    monkeypatch.setattr(co_mentions, "_named_entities_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(co_mentions, "resolve_frontend_url", AsyncMock(return_value=None))
    page = AsyncMock(return_value=EntityRecordPage(records=[_record("r1"), _record("r2"), _record("r3")], next_cursor=None))
    monkeypatch.setattr(co_mentions, "list_accessible_entity_records", page)
    monkeypatch.setattr(co_mentions, "record_line", lambda record, *_: f"- {record['_key']}")
    return page


def _state(rows: list[dict[str, Any]] = _ROWS) -> dict[str, Any]:
    graph = MagicMock()
    graph.get_named_entity_mentions_from_records = AsyncMock(return_value=rows)
    graph.resolve_named_entity_redirects = AsyncMock(return_value={})
    return {"graph_provider": graph, "org_id": "org"}


async def test_entities_are_counted_over_readable_records_most_first(listing: AsyncMock) -> None:
    state = _state()
    ok, body = await co_mentions.execute_find_co_mentioned_entities(state, entity_ids=[ACME])
    lines = body.splitlines()
    assert ok and lines[0] == 'Entities mentioned with "Acme Corp", counted over all 3 readable records that mention it:'
    assert lines[1].startswith("- Jane Doe (person) | entity_id=p-jane | 3 records, e.g. r1, r2, r3")
    assert lines[2].startswith("- Globex (organization)") and lines[3].startswith("- Raj (person)")
    # The entity itself and contact details are not listed.
    assert "entity_id=e-acme" not in body and "jane@acme.com" not in body
    org, record_ids = state["graph_provider"].get_named_entity_mentions_from_records.await_args.args
    assert (org, record_ids) == ("org", ["r1", "r2", "r3"])


async def test_a_name_is_spelled_as_the_readable_records_spell_it_never_from_the_node(listing: AsyncMock) -> None:
    """A node may be named after a record the user cannot read; only edges of readable records spell it."""
    rows = [_mention("r1", ACME, "organization", "Acme"), _mention("r1", "p-1", "person", "Miguel")]
    ok, body = await co_mentions.execute_find_co_mentioned_entities(_state(rows), entity_ids=[ACME])
    assert ok and "- Miguel (person)" in body and "Gallagher" not in body
    # An entity no readable record spells is left out.
    rows = [_mention("r1", ACME, "organization", "Acme"), _mention("r1", "p-2", "person")]
    ok, body = await co_mentions.execute_find_co_mentioned_entities(_state(rows), entity_ids=[ACME])
    assert ok and "p-2" not in body and body.startswith("No other entities")


async def test_kinds_narrow_and_contact_details_need_asking_for(listing: AsyncMock) -> None:
    ok, body = await co_mentions.execute_find_co_mentioned_entities(_state(), entity_ids=[ACME], kinds=["person"])
    assert ok and "Globex" not in body and "Jane Doe" in body
    ok, body = await co_mentions.execute_find_co_mentioned_entities(_state(), entity_ids=[ACME], kinds=["email"])
    assert ok and "jane@acme.com (email)" in body and "Jane Doe" not in body


async def test_copies_of_one_document_count_once_and_ids_share_the_cap(
    listing: AsyncMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing.side_effect = [
        EntityRecordPage(records=[_record("r1", "v-1"), _record("r1-copy", "v-1")], next_cursor=None),
        EntityRecordPage(records=[_record("r2")], next_cursor=None),
    ]
    monkeypatch.setattr(co_mentions, "SEARCH_SCOPE_MAX_RECORDS", 3)
    state = _state()
    ok, _ = await co_mentions.execute_find_co_mentioned_entities(state, entity_ids=[ACME, "e-acme-2"])
    assert ok and state["graph_provider"].get_named_entity_mentions_from_records.await_args.args[1] == ["r1", "r2"]
    assert [call.kwargs["limit"] for call in listing.await_args_list] == [3, 2]


async def test_a_partial_listing_says_it_counted_a_sample(listing: AsyncMock) -> None:
    listing.return_value = EntityRecordPage(records=[_record("r1"), _record("r2"), _record("r3")], next_cursor="9")
    ok, body = await co_mentions.execute_find_co_mentioned_entities(_state(), entity_ids=[ACME])
    assert ok and "counted over a sample of 3 readable records (the newest)" in body.splitlines()[0]
    listing.return_value = EntityRecordPage(records=[_record("r1")], next_cursor=None, capped=True)
    ok, body = await co_mentions.execute_find_co_mentioned_entities(_state(), entity_ids=[ACME])
    assert ok and "a sample of 1" in body


async def test_no_readable_record_is_one_answer_whatever_the_reason(listing: AsyncMock) -> None:
    listing.return_value = EntityRecordPage(records=[], next_cursor=None)
    for ids in (["unknown"], ["unreachable"]):
        state = _state()
        assert await co_mentions.execute_find_co_mentioned_entities(state, entity_ids=ids) == (True, co_mentions.NO_RECORDS_MSG)
        state["graph_provider"].get_named_entity_mentions_from_records.assert_not_awaited()


async def test_a_failed_listing_is_a_failure_not_an_empty_answer(listing: AsyncMock) -> None:
    listing.side_effect = EntityAccessError("down")
    ok, body = await co_mentions.execute_find_co_mentioned_entities(_state(), entity_ids=[ACME])
    assert (ok, body) == (False, co_mentions.LOOKUP_FAILED_MSG)


@pytest.mark.parametrize("page", [
    EntityRecordPage(records=[], next_cursor="2000"),
    EntityRecordPage(records=[], next_cursor=None, capped=True),
])
async def test_an_id_whose_scan_ran_out_unread_reads_as_an_unknown_one(listing: AsyncMock, page) -> None:
    """"Too slow" would say the id names an entity with records the user cannot read."""
    listing.return_value = page
    assert await co_mentions.execute_find_co_mentioned_entities(_state(), entity_ids=[ACME]) == (
        True, co_mentions.NO_RECORDS_MSG,
    )


async def test_a_slow_listing_with_nothing_found_reads_as_an_unknown_id(
    listing: AsyncMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _slow(*_: object, **__: object) -> EntityRecordPage:
        await asyncio.sleep(1)
        return EntityRecordPage(records=[], next_cursor=None)

    listing.side_effect = _slow
    monkeypatch.setattr(co_mentions, "DEADLINE_SECONDS", 0.01)
    assert await co_mentions.execute_find_co_mentioned_entities(_state(), entity_ids=[ACME]) == (True, co_mentions.NO_RECORDS_MSG)


async def test_a_slow_later_id_keeps_the_records_already_listed_as_a_sample(
    listing: AsyncMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _second_stalls(*_: object, entity_id: str, **__: object) -> EntityRecordPage:
        if entity_id == ACME:
            return EntityRecordPage(records=[_record("r1"), _record("r2"), _record("r3")], next_cursor=None)
        await asyncio.sleep(5)
        return EntityRecordPage(records=[], next_cursor=None)

    listing.side_effect = _second_stalls
    monkeypatch.setattr(co_mentions, "DEADLINE_SECONDS", 0.2)
    ok, body = await co_mentions.execute_find_co_mentioned_entities(_state(), entity_ids=[ACME, "e-acme-2"])
    assert ok and "a sample of 3 readable records" in body and "Jane Doe" in body


async def test_extraction_off_or_no_ids_is_refused(listing: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
    ok, body = await co_mentions.execute_find_co_mentioned_entities(_state(), entity_ids=[" "])
    assert not ok and "entity_ids" in body
    monkeypatch.setattr(co_mentions, "_named_entities_enabled", AsyncMock(return_value=False))
    assert await co_mentions.execute_find_co_mentioned_entities(_state(), entity_ids=[ACME]) == (
        False, co_mentions.NAMED_ENTITIES_DISABLED_MSG,
    )


async def test_example_records_are_remembered_for_citation(listing: AsyncMock) -> None:
    state = _state()
    ok, body = await co_mentions.execute_find_co_mentioned_entities(state, entity_ids=[ACME], limit=1)
    assert ok and "Example records:\n- r1\n- r2\n- r3" in body
    assert state["known_record_ids"] == {"r1", "r2", "r3"}



async def test_listed_entities_can_be_followed_up_without_a_search(listing: AsyncMock) -> None:
    from app.agents.actions.knowledge_graph.ops.entity_filters import (
        ENTITY_INDEX_CACHE_KEY,
    )

    state = _state()
    await co_mentions.execute_find_co_mentioned_entities(state, entity_ids=[ACME])
    assert state[ENTITY_INDEX_CACHE_KEY]["p-jane"] == {"type": "named_entity", "name": "Jane Doe"}


async def test_kinds_are_checked_and_take_in_their_family(listing: AsyncMock) -> None:
    rows = [*_ROWS, _mention("r2", "c-pdx", "city", "Portland")]
    ok, body = await co_mentions.execute_find_co_mentioned_entities(_state(rows), entity_ids=[ACME], kinds=["location"])
    assert ok and "Portland (city)" in body and "Jane Doe" not in body
    ok, body = await co_mentions.execute_find_co_mentioned_entities(_state(), entity_ids=[ACME], kinds=["people"])
    assert not ok and "'people' is not a named-entity kind" in body and "person" in body
    ok, body = await co_mentions.execute_find_co_mentioned_entities(_state(), entity_ids=[ACME], kinds=["person", "date"])
    assert ok and "Jane Doe" in body and "find_records_by_value" in body


async def test_a_merged_id_lists_the_node_it_moved_to(listing: AsyncMock) -> None:
    state = _state()
    state["graph_provider"].resolve_named_entity_redirects = AsyncMock(return_value={"e-old": ACME})
    ok, body = await co_mentions.execute_find_co_mentioned_entities(state, entity_ids=["e-old"])
    assert ok and listing.await_args.kwargs["entity_id"] == ACME and "entity_id=e-acme" not in body


async def test_the_subject_is_named_by_any_of_its_ids(listing: AsyncMock) -> None:
    rows = [_mention("r1", "e-alias", "organization", "Acme"), _mention("r1", "p-jane", "person", "Jane")]
    ok, body = await co_mentions.execute_find_co_mentioned_entities(_state(rows), entity_ids=[ACME, "e-alias"])
    assert ok and body.startswith('Entities mentioned with "Acme"')


async def test_example_records_are_bounded(listing: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
    records = [_record(f"r{i}") for i in range(60)]
    listing.return_value = EntityRecordPage(records=records, next_cursor=None)
    rows = [_mention(f"r{i}", f"p-{i}", "person", f"Person {i:02d}") for i in range(60)]
    ok, body = await co_mentions.execute_find_co_mentioned_entities(_state(rows), entity_ids=[ACME], limit=50)
    example_lines = body.split("Example records:\n", 1)[1].split("\n\n", 1)[0].splitlines()
    assert ok and len(example_lines) == 30 and body.count("| entity_id=p-") == 50



async def test_a_bare_kind_and_a_text_limit_are_read(listing: AsyncMock) -> None:
    ok, body = await co_mentions.execute_find_co_mentioned_entities(
        _state(), entity_ids=[ACME], kinds="person", limit="1",  # type: ignore[arg-type]
    )
    assert ok and body.count("| entity_id=") == 1 and "Jane Doe" in body
    ok, body = await co_mentions.execute_find_co_mentioned_entities(
        _state(), entity_ids=[ACME], limit="many",  # type: ignore[arg-type]
    )
    assert ok and "Jane Doe" in body


async def test_an_invalid_kind_error_lists_contact_kinds_too(listing: AsyncMock) -> None:
    ok, body = await co_mentions.execute_find_co_mentioned_entities(_state(), entity_ids=[ACME], kinds=["people"])
    assert not ok and "email" in body.split("Pass kinds from:")[1]
