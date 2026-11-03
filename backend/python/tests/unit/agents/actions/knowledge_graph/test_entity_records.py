"""Unit tests for ``ops/entity_records.py`` (``find_records_by_entity`` and
record-group scoping for ``search(entity_ids)``)."""
import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.knowledge_graph.ops import entity_filters, entity_records
from app.agents.actions.knowledge_graph.ops.entity_filters import (
    ENTITY_INDEX_CACHE_KEY,
    LIMITED_SCOPE_NOTE,
)
from app.agents.actions.knowledge_graph.ops.entity_records import (
    LOOKUP_FAILED_MSG,
    NO_ACCESSIBLE_RECORDS_MSG,
    NO_FURTHER_RECORDS_MSG,
    execute_find_records_by_entity,
    resolve_entity_virtual_ids,
)
from app.modules.retrieval.entity_permissions import (
    SEARCH_SCOPE_MAX_ENTITIES,
    EntityAccessContext,
    EntityAccessError,
    EntityRecordPage,
)
from app.modules.retrieval.selection_scope import SelectionScope
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

CONTEXT = EntityAccessContext(
    org_id="org-1",
    user_key="ukey",
    app_level_app_ids=frozenset({"kb-1"}),
    record_level_app_ids=frozenset({"conf-1"}),
    record_group_ids=frozenset(),
    app_names={"kb-1": "Team KB", "conf-1": "Confluence"},
)


def _state(**overrides) -> dict:
    state = {"org_id": "org-1", "user_id": "user-1", "graph_provider": MagicMock()}
    state.update(overrides)
    return state


def _row(key: str, **extra: object) -> dict:
    return {"_key": key, "recordName": f"doc {key}", "recordType": "FILE", "connectorId": "conf-1", **extra}


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> tuple[AsyncMock, AsyncMock]:
    access = AsyncMock(return_value=CONTEXT)
    listing = AsyncMock(return_value=EntityRecordPage(records=[], next_cursor=None))
    monkeypatch.setattr(entity_filters, "get_entity_access_context", access)
    monkeypatch.setattr(entity_records, "list_accessible_entity_records", listing)
    return access, listing


class TestFindRecordsByEntity:
    @pytest.mark.asyncio
    async def test_type_required_when_not_indexed(self, patched) -> None:
        ok, text = await execute_find_records_by_entity(_state(), "t1")
        assert ok is False
        assert "entity_type is required" in text
        patched[1].assert_not_called()

    @pytest.mark.asyncio
    async def test_unsupported_type_rejected(self, patched) -> None:
        ok, _ = await execute_find_records_by_entity(_state(), "t1", entity_type="person")
        assert ok is False

    @pytest.mark.asyncio
    async def test_type_and_name_come_from_search_entities_index(self, patched) -> None:
        patched[1].return_value = EntityRecordPage(records=[_row("r1")], next_cursor=None)
        state = _state(**{ENTITY_INDEX_CACHE_KEY: {"t1": {"type": "topic", "name": "Roadmap"}}})

        ok, text = await execute_find_records_by_entity(state, "t1")

        assert ok is True
        assert 'topic "Roadmap"' in text
        _, kwargs = patched[1].call_args
        assert kwargs["entity_type"] == "topic"

    @pytest.mark.asyncio
    async def test_no_rows_is_the_same_response_as_no_access(self, patched) -> None:
        ok, text = await execute_find_records_by_entity(_state(), "rg-x", entity_type="record_group")
        assert (ok, text) == (True, NO_ACCESSIBLE_RECORDS_MSG)

    @pytest.mark.asyncio
    async def test_no_rows_inside_a_selection_says_only_the_selection_was_searched(self, patched) -> None:
        patched[0].return_value = replace(CONTEXT, row_scopes=(SelectionScope(groups={"g": "conf-1"}),))
        ok, text = await execute_find_records_by_entity(_state(), "rg-x", entity_type="record_group")
        assert (ok, text) == (True, f"{NO_ACCESSIBLE_RECORDS_MSG} {LIMITED_SCOPE_NOTE}")

    @pytest.mark.asyncio
    async def test_empty_page_past_the_end_does_not_claim_the_entity_is_empty(
        self, patched
    ) -> None:
        """Saying "no accessible records" on page 2 contradicts the page of
        results the caller was just shown."""
        ok, text = await execute_find_records_by_entity(
            _state(), "t1", entity_type="topic", cursor="40",
        )
        assert (ok, text) == (True, NO_FURTHER_RECORDS_MSG)

    @pytest.mark.asyncio
    async def test_access_failure_is_a_failed_call(self, patched) -> None:
        patched[1].side_effect = EntityAccessError("db down")
        ok, text = await execute_find_records_by_entity(_state(), "t1", entity_type="topic")
        assert (ok, text) == (False, LOOKUP_FAILED_MSG)

    @pytest.mark.asyncio
    async def test_invalid_cursor_is_reported(self, patched) -> None:
        patched[1].side_effect = ValueError("Invalid cursor 'x'")
        ok, text = await execute_find_records_by_entity(_state(), "t1", entity_type="topic", cursor="x")
        assert ok is False
        assert "Invalid cursor" in text

    @pytest.mark.asyncio
    async def test_renders_rows_cursor_and_remembers_ids(self, patched) -> None:
        patched[1].return_value = EntityRecordPage(
            records=[_row("r1", webUrl="https://x/1", sourceLastModifiedTimestamp=1_767_225_600_000)],
            next_cursor="40",
        )
        state = _state()

        ok, text = await execute_find_records_by_entity(
            state, "t1", entity_type="Topic", record_types=["file"], limit=500,
        )

        assert ok is True
        assert "- [FILE] doc r1 | record_id=r1 | app: Confluence | modified: 2026-01-01 | url=https://x/1" in text
        assert 'cursor="40"' in text
        assert "this topic" in text
        assert state["known_record_ids"] == {"r1"}
        _, kwargs = patched[1].call_args
        assert kwargs["record_types"] == ["FILE"]
        assert kwargs["limit"] == 50

    @pytest.mark.asyncio
    async def test_the_first_page_names_the_same_entity_under_a_learned_alias(self, patched, monkeypatch) -> None:
        monkeypatch.setattr(entity_records, "_named_entities_enabled", AsyncMock(return_value=True))
        family = AsyncMock(return_value=[{"id": "short-1", "kind": "organization", "name": "Pinecrest"}])
        monkeypatch.setattr(entity_records, "alias_family", family)
        patched[1].return_value = EntityRecordPage(records=[_row("r1")], next_cursor="20")
        state = _state(**{ENTITY_INDEX_CACHE_KEY: {"full-1": {"type": "named_entity", "name": "Pinecrest Hospitality"}}})

        ok, first = await execute_find_records_by_entity(state, "full-1")
        ok_next, later = await execute_find_records_by_entity(state, "full-1", cursor="20")

        assert ok and 'Pinecrest (organization): knowledgegraph__find_records_by_entity(entity_id="short-1"' in first
        assert "add them to a count" in first
        assert ok_next and "learned alias" not in later

    @pytest.mark.asyncio
    async def test_a_stalled_alias_lookup_is_left_out_and_the_listing_still_returns(
        self, patched, monkeypatch,
    ) -> None:
        """The alias note is optional: a graph read inside it that never answers must not
        hold the listing, and the reply must read as one with no alias."""
        async def stall(*_args: object, **_kwargs: object) -> None:
            await asyncio.Event().wait()

        monkeypatch.setattr(entity_records, "_named_entities_enabled", AsyncMock(return_value=True))
        monkeypatch.setattr(entity_records, "alias_family", stall)
        monkeypatch.setattr(entity_records, "ALIAS_NOTE_SECONDS", 0.05, raising=False)
        patched[1].return_value = EntityRecordPage(records=[_row("r1")], next_cursor=None)

        ok, listed = await asyncio.wait_for(
            execute_find_records_by_entity(_state(), "e1", entity_type="named_entity"), timeout=2,
        )
        patched[1].return_value = EntityRecordPage(records=[], next_cursor=None)
        ok_empty, empty = await asyncio.wait_for(
            execute_find_records_by_entity(_state(), "e1", entity_type="named_entity"), timeout=2,
        )

        assert ok and "record_id=r1" in listed and "learned alias" not in listed
        assert (ok_empty, empty) == (True, NO_ACCESSIBLE_RECORDS_MSG)

    @pytest.mark.asyncio
    async def test_no_alias_note_for_other_entity_types_or_when_turned_off(self, patched, monkeypatch) -> None:
        from app.modules.named_entities.domain.config import NamedEntityConfig

        family = AsyncMock(return_value=[{"id": "x", "kind": "organization", "name": "X"}])
        monkeypatch.setattr(entity_records, "alias_family", family)
        monkeypatch.setattr(entity_records, "_named_entities_enabled", AsyncMock(return_value=True))
        patched[1].return_value = EntityRecordPage(records=[_row("r1")], next_cursor=None)
        _, topic = await execute_find_records_by_entity(_state(), "t1", entity_type="topic")
        monkeypatch.setattr(
            entity_records, "load_named_entity_config", AsyncMock(return_value=NamedEntityConfig(link_aliases=False)),
        )
        _, off = await execute_find_records_by_entity(_state(), "e1", entity_type="named_entity")
        assert "learned alias" not in topic and "learned alias" not in off
        family.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_each_row_can_be_cited_for_what_the_listing_says(self, patched, monkeypatch) -> None:
        cite = AsyncMock(return_value={"r1": "ref1"})
        monkeypatch.setattr(entity_records, "cite_listed_records", cite)
        monkeypatch.setattr(entity_records, "_named_entities_enabled", AsyncMock(return_value=True))
        patched[1].return_value = EntityRecordPage(
            records=[_row("r1", sourceLastModifiedTimestamp=1_767_225_600_000)], next_cursor=None,
        )
        state = _state(**{ENTITY_INDEX_CACHE_KEY: {"t1": {"type": "named_entity", "name": "Pinecrest"}}})

        ok, text = await execute_find_records_by_entity(state, "t1")

        assert ok and "| record_id=r1 | cite=ref1 |" in text and "Cite a fact this listing states" in text
        facts = cite.await_args.args[3]
        assert facts == {"r1": 'connected to named_entity "Pinecrest" · modified 2026-01-01'}

    @pytest.mark.asyncio
    async def test_an_unnamed_entity_is_cited_in_the_listings_own_words(self, patched, monkeypatch) -> None:
        cite = AsyncMock(return_value={})
        monkeypatch.setattr(entity_records, "cite_listed_records", cite)
        patched[1].return_value = EntityRecordPage(records=[_row("r1")], next_cursor=None)

        ok, text = await execute_find_records_by_entity(_state(), "t1", entity_type="topic")

        assert ok and "cite=" not in text
        assert cite.await_args.args[3] == {"r1": "connected to a topic"}

    @pytest.mark.asyncio
    async def test_a_record_group_listing_cites_its_rows_as_in_the_group(self, patched, monkeypatch) -> None:
        cite = AsyncMock(return_value={})
        monkeypatch.setattr(entity_records, "cite_listed_records", cite)
        patched[1].return_value = EntityRecordPage(records=[_row("r1")], next_cursor=None)
        state = _state(**{ENTITY_INDEX_CACHE_KEY: {"g1": {"type": "record_group", "name": "Finance"}}})

        ok, _ = await execute_find_records_by_entity(state, "g1")

        assert ok and cite.await_args.args[3] == {"r1": 'in record_group "Finance"'}

    @pytest.mark.asyncio
    async def test_listed_records_are_noted_as_found_by_entity(self, patched) -> None:
        patched[1].return_value = EntityRecordPage(
            records=[_row("r1", virtualRecordId="vr-1")], next_cursor=None,
        )
        state = _state(include_retrieval_context=True)

        await execute_find_records_by_entity(state, "t1", entity_type="topic")

        provenance = state["retrieved_by"]["vr-1"]
        assert (provenance.layers, provenance.record_id, provenance.record_name) == (
            ["entity"], "r1", "doc r1",
        )

    @pytest.mark.asyncio
    async def test_hidden_urls_are_not_shown(self, patched) -> None:
        patched[1].return_value = EntityRecordPage(
            records=[_row("r1", webUrl="https://x/secret", hideWeburl=True)], next_cursor=None,
        )
        ok, text = await execute_find_records_by_entity(_state(), "t1", entity_type="topic")
        assert ok is True
        assert "secret" not in text and "url=" not in text

    @pytest.mark.asyncio
    async def test_relative_urls_resolve_against_the_frontend(self, patched, monkeypatch) -> None:
        monkeypatch.setattr(
            entity_records, "resolve_frontend_url", AsyncMock(return_value="https://app.example/"),
        )
        patched[1].return_value = EntityRecordPage(
            records=[_row("r1", webUrl="/record/r1"), _row("r2", webUrl="https://x/2")], next_cursor=None,
        )
        ok, text = await execute_find_records_by_entity(_state(), "t1", entity_type="topic")
        assert ok is True
        assert "url=https://app.example/record/r1" in text
        assert "url=https://x/2" in text

    @pytest.mark.asyncio
    async def test_unresolvable_relative_urls_are_dropped(self, patched, monkeypatch) -> None:
        monkeypatch.setattr(entity_records, "resolve_frontend_url", AsyncMock(return_value=None))
        patched[1].return_value = EntityRecordPage(records=[_row("r1", webUrl="/record/r1")], next_cursor=None)
        ok, text = await execute_find_records_by_entity(_state(), "t1", entity_type="topic")
        assert ok is True
        assert "url=" not in text


    @pytest.mark.asyncio
    async def test_shortened_record_id_is_resolved(self, patched) -> None:
        state = _state(enable_record_id_shortening=True)
        from app.utils.chat_helpers import get_record_id_shortener_if_enabled
        short = get_record_id_shortener_if_enabled(state).get_or_create_short_id("rec-9")
        patched[1].return_value = EntityRecordPage(records=[_row("rec-9")], next_cursor=None)

        ok, text = await execute_find_records_by_entity(state, short, entity_type="record")

        assert ok is True
        _, kwargs = patched[1].call_args
        assert kwargs["entity_id"] == "rec-9"
        assert f"record_id={short}" in text


class TestResolveEntityVirtualIds:
    @pytest.mark.asyncio
    async def test_dedupes_virtual_ids_across_entities(self, patched) -> None:
        patched[1].side_effect = [
            EntityRecordPage(records=[_row("r1", virtualRecordId="v1"), _row("r2", virtualRecordId="v2")], next_cursor=None),
            EntityRecordPage(records=[_row("r3", virtualRecordId="v1")], next_cursor=None),
        ]

        scope = await resolve_entity_virtual_ids(
            _state(), [("rg-1", "record_group"), ("s1", "subcategory"), ("rg-1", "record_group")],
        )

        assert scope.virtual_ids == ["v1", "v2"]
        assert scope.truncated is False
        assert patched[1].await_count == 2
        assert patched[1].call_args_list[1].kwargs["entity_type"] == "subcategory"

    @pytest.mark.asyncio
    async def test_failure_propagates(self, patched) -> None:
        patched[1].side_effect = EntityAccessError("db down")
        with pytest.raises(EntityAccessError):
            await resolve_entity_virtual_ids(_state(), [("rg-1", "record_group")])

    @pytest.mark.asyncio
    async def test_scan_budget_running_out_is_reported_as_truncated(self, patched) -> None:
        """An empty scope that stopped at the scan budget is not "no records"."""
        patched[1].return_value = EntityRecordPage(records=[], next_cursor="2000")

        scope = await resolve_entity_virtual_ids(_state(), [("rg-1", "record_group")])

        assert scope.virtual_ids == []
        assert scope.truncated is True

    @pytest.mark.asyncio
    async def test_entities_past_the_cap_are_counted_as_skipped(self, patched) -> None:
        """Reported apart from ``truncated``, which is about one entity's
        records, so the search note names what was actually left out."""
        entities = [(f"rg-{i}", "record_group") for i in range(SEARCH_SCOPE_MAX_ENTITIES + 1)]

        scope = await resolve_entity_virtual_ids(_state(), entities)

        assert scope.entities_skipped == 1
        assert patched[1].await_count == SEARCH_SCOPE_MAX_ENTITIES


class TestStrictAndExcludedScope:
    @pytest.mark.asyncio
    async def test_strict_scope_and_exclusions_reach_the_access_context(self, patched) -> None:
        state = _state(
            filters={"strictScope": True, "apps": ["conf-1"]},
            excluded_app_ids=frozenset({"demo-1"}),
        )

        await execute_find_records_by_entity(state, "t1", entity_type="topic")

        _, kwargs = patched[0].call_args
        assert kwargs["strict"] is True
        assert kwargs["source_ids"] == ["conf-1"]
        assert kwargs["exclude_app_ids"] == frozenset({"demo-1"})

    @pytest.mark.asyncio
    async def test_strict_empty_scope_lists_nothing_without_a_graph_query(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        graph = MagicMock()
        graph.get_entity_access_context = AsyncMock()
        graph.get_entity_candidate_records = AsyncMock()
        state = _state(graph_provider=graph, filters={"strictScope": True})

        ok, text = await execute_find_records_by_entity(state, "t1", entity_type="topic")

        assert (ok, text) == (True, NO_ACCESSIBLE_RECORDS_MSG)
        graph.get_entity_access_context.assert_not_called()
        graph.get_entity_candidate_records.assert_not_called()


class TestEmptyWindow:
    @pytest.mark.asyncio
    async def test_an_empty_window_with_nothing_readable_further_reads_as_an_unknown_entity(
        self, patched, monkeypatch,
    ) -> None:
        """A continuation would say the entity exists and has records the user cannot read."""
        probe = AsyncMock(return_value={})
        monkeypatch.setattr(entity_records, "first_readable_offsets", probe)
        patched[1].return_value = EntityRecordPage(records=[], next_cursor="1000")
        _, dry = await execute_find_records_by_entity(_state(), "t1", entity_type="topic")
        patched[1].return_value = EntityRecordPage(records=[], next_cursor=None)
        _, unknown = await execute_find_records_by_entity(_state(), "nope", entity_type="topic")

        assert dry == unknown
        assert "cursor=" not in dry and "t1" not in dry
        assert probe.await_args.kwargs["offset"] == 1000

    @pytest.mark.asyncio
    async def test_an_empty_window_lists_from_the_next_readable_record_further_down(
        self, patched, monkeypatch,
    ) -> None:
        monkeypatch.setattr(
            entity_records, "first_readable_offsets", AsyncMock(return_value={("topic", "t1"): 1400}),
        )
        patched[1].side_effect = [
            EntityRecordPage(records=[], next_cursor="1000"),
            EntityRecordPage(records=[_row("r9")], next_cursor="1401"),
        ]
        ok, text = await execute_find_records_by_entity(_state(), "t1", entity_type="topic")

        assert ok and "doc r9" in text
        assert patched[1].await_args.kwargs["cursor"] == "1400"
        assert 'More records may exist: knowledgegraph__find_records_by_entity(entity_id="t1", ' in text


def test_candidate_rows_carry_the_hide_url_flag() -> None:
    assert "hideWeburl" in Neo4jProvider._ENTITY_CANDIDATE_RECORD_PROJECTION
    assert "hideWeburl" in ArangoHTTPProvider._ENTITY_CANDIDATE_RECORD_FIELDS


class TestUploadDates:
    @pytest.mark.asyncio
    async def test_a_knowledge_base_upload_is_listed_by_its_upload_time(self, patched) -> None:
        uploaded = 1_792_090_099_000  # 2026-10-15
        patched[1].return_value = EntityRecordPage(
            records=[_row(
                "r1", connectorId="kb-1", connectorName="KB", origin="UPLOAD",
                createdAtTimestamp=uploaded, sourceCreatedAtTimestamp=uploaded - 30,
                sourceLastModifiedTimestamp=uploaded - 30,
            )],
            next_cursor=None,
        )

        ok, text = await execute_find_records_by_entity(_state(), "t1", entity_type="topic")

        assert ok is True
        assert "uploaded: 2026-10-15" in text
        assert "modified:" not in text
