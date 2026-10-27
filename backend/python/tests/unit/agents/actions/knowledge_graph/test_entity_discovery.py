"""Unit tests for ``ops/entity_discovery.py`` (``search_entities``)."""
import json
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.knowledge_graph.ops import entity_discovery, entity_filters
from app.agents.actions.knowledge_graph.ops.entity_discovery import (
    execute_search_entities,
)
from app.agents.actions.knowledge_graph.ops.entity_filters import (
    ENTITY_ID_FILTER_KEY_CACHE_KEY,
    ENTITY_INDEX_CACHE_KEY,
    LIMITED_SCOPE_NOTE,
    RECORD_SCOPED_ENTITY_CACHE_KEY,
)
from app.modules.retrieval.entity_permissions import (
    EntityAccessContext,
    EntityAccessError,
    EntityHit,
    EntitySearchTimedOut,
)
from app.modules.retrieval.selection_scope import SelectionScope

CONTEXT = EntityAccessContext(
    org_id="org-1",
    user_key="ukey",
    app_level_app_ids=frozenset({"kb-1"}),
    record_level_app_ids=frozenset({"conf-1"}),
    record_group_ids=frozenset(),
    app_names={"kb-1": "Team KB", "conf-1": "Confluence"},
)


def _state(**overrides) -> dict:
    state = {
        "org_id": "org-1",
        "user_id": "user-1",
        "graph_provider": MagicMock(),
        "entity_vector_store": MagicMock(),
        "apps": ["conf-1"],
        "kb": ["kb-1"],
    }
    state.update(overrides)
    return state


def _hit(entity_id, entity_type, score=0.9, records=(), more=False) -> EntityHit:
    return EntityHit(
        entity_id=entity_id, entity_type=entity_type, name=f"name {entity_id}",
        score=score, records=list(records), more_records=more,
    )


def _row(key: str, connector_id: str = "conf-1") -> dict:
    return {
        "_key": key, "recordName": f"doc {key}", "recordType": "FILE",
        "connectorId": connector_id, "sourceLastModifiedTimestamp": 1_767_225_600_000,
    }


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> tuple[AsyncMock, AsyncMock]:
    access = AsyncMock(return_value=CONTEXT)
    search = AsyncMock(return_value=[])
    monkeypatch.setattr(entity_filters, "get_entity_access_context", access)
    monkeypatch.setattr(entity_discovery, "search_entities_for_user", search)
    return access, search


class TestEmptyResultInsideASelection:
    @pytest.mark.asyncio
    async def test_it_says_only_the_selection_was_searched(self, patched) -> None:
        patched[0].return_value = replace(CONTEXT, row_scopes=(SelectionScope(groups={"g": "kb-1"}),))
        ok, text = await execute_search_entities(_state(), "KG Tools")
        payload = json.loads(text)
        assert ok and payload["results"] == [] and payload["scopeNote"] == LIMITED_SCOPE_NOTE

    @pytest.mark.asyncio
    async def test_without_a_selection_there_is_no_note(self, patched) -> None:
        ok, text = await execute_search_entities(_state(), "KG Tools")
        assert ok and "scopeNote" not in json.loads(text)


class TestGuards:
    @pytest.mark.asyncio
    async def test_blank_query_fails(self, patched) -> None:
        ok, text = await execute_search_entities(_state(), "  ")
        assert ok is False
        assert json.loads(text)["status"] == "error"

    @pytest.mark.asyncio
    async def test_missing_store_fails(self, patched) -> None:
        ok, _ = await execute_search_entities(_state(entity_vector_store=None), "legal")
        assert ok is False

    @pytest.mark.asyncio
    async def test_only_unknown_entity_types_fails(self, patched) -> None:
        ok, text = await execute_search_entities(_state(), "legal", entity_types=["planet"])
        assert ok is False
        assert "Unsupported" in json.loads(text)["message"]
        patched[1].assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "ask",
        [{"entity_types": ["named_entity"]}, {"named_entity_kinds": ["currency"]}],
    )
    async def test_a_named_entity_search_with_the_flag_off_does_not_search_every_type(
        self, patched, monkeypatch, ask,
    ) -> None:
        monkeypatch.setattr(entity_discovery, "_named_entities_enabled", AsyncMock(return_value=False))
        ok, text = await execute_search_entities(_state(), "acme", **ask)
        assert ok is False
        assert json.loads(text)["message"] == entity_discovery.NAMED_ENTITIES_DISABLED_MSG
        patched[1].assert_not_called()

    @pytest.mark.asyncio
    async def test_the_flag_off_drops_only_the_named_entity_type(self, patched, monkeypatch) -> None:
        monkeypatch.setattr(entity_discovery, "_named_entities_enabled", AsyncMock(return_value=False))
        ok, _ = await execute_search_entities(
            _state(), "acme", entity_types=["named_entity", "topic"], named_entity_kinds=["currency"],
        )
        assert ok is True
        _, search_kwargs = patched[1].call_args
        assert (search_kwargs["entity_types"], search_kwargs["kinds"]) == (["topic"], None)

    @pytest.mark.asyncio
    async def test_a_kind_given_as_an_entity_type_searches_named_entities(self, patched, monkeypatch) -> None:
        monkeypatch.setattr(entity_discovery, "_named_entities_enabled", AsyncMock(return_value=True))
        ok, text = await execute_search_entities(_state(), "ValeHealth", entity_types=["organization", "record"])
        assert ok is True
        assert "ignoredEntityTypes" not in json.loads(text)
        _, search_kwargs = patched[1].call_args
        assert (search_kwargs["entity_types"], search_kwargs["kinds"]) == (["record", "named_entity"], ["organization"])

    @pytest.mark.asyncio
    async def test_access_failure_is_a_failed_call_not_empty_results(self, patched) -> None:
        patched[0].side_effect = EntityAccessError("db down")
        ok, text = await execute_search_entities(_state(), "legal")
        assert ok is False
        assert "try again" in json.loads(text)["message"]

    @pytest.mark.asyncio
    async def test_search_failure_is_a_failed_call(self, patched) -> None:
        patched[1].side_effect = EntityAccessError("vector down")
        ok, _ = await execute_search_entities(_state(), "legal")
        assert ok is False

    @pytest.mark.asyncio
    async def test_a_timeout_is_reported_as_one_not_as_no_matches(self, patched) -> None:
        patched[1].side_effect = EntitySearchTimedOut("deadline")
        ok, text = await execute_search_entities(_state(), "legal")
        message = json.loads(text)["message"]
        assert ok is False
        assert "timed out" in message and "No accessible entities matched" not in message


class TestSearch:
    @pytest.mark.asyncio
    async def test_scope_comes_from_agent_sources(self, patched) -> None:
        await execute_search_entities(_state(), "legal", entity_types=["topic", "person"], top_k=99)

        _, kwargs = patched[0].call_args
        assert kwargs["source_ids"] == ["conf-1", "kb-1"]
        _, search_kwargs = patched[1].call_args
        assert search_kwargs["entity_types"] == ["topic"]
        assert search_kwargs["top_k"] == 25

    @pytest.mark.asyncio
    async def test_no_hits_is_success(self, patched) -> None:
        ok, text = await execute_search_entities(_state(), "legal")
        assert ok is True
        assert json.loads(text)["results"] == []

    @pytest.mark.asyncio
    async def test_results_shape_previews_and_caches(self, patched) -> None:
        patched[1].return_value = [
            _hit("t1", "topic", records=[_row("r1"), _row("r2", "kb-1")], more=True),
            _hit("rg-1", "record_group", records=[_row("r3")]),
            _hit("rec-9", "record", records=[_row("rec-9")]),
            _hit("d1", "department", records=[_row("r4")]),
            _hit("c1", "category", records=[_row("r5")]),
        ]
        state = _state()

        ok, text = await execute_search_entities(state, "legal")

        assert ok is True
        results = json.loads(text)["results"]
        topic = results[0]
        assert topic["apps"] == ["Confluence", "Team KB"]
        assert [r["recordId"] for r in topic["records"]] == ["r1", "r2"]
        assert topic["records"][0]["modified"] == "2026-01-01"
        assert topic["moreRecords"] is True
        assert "records" not in results[2]
        assert "records" in results[3]
        assert "records" not in results[4]
        assert "connectedEntities" not in topic
        # The fused rank score is not a similarity; the order already shows rank.
        assert all("score" not in r for r in results)

        assert state["known_record_ids"] == {"r1", "r2", "r3", "rec-9", "r4"}
        assert state[ENTITY_INDEX_CACHE_KEY]["rec-9"]["type"] == "record"
        assert state[ENTITY_ID_FILTER_KEY_CACHE_KEY]["t1"] == ("topics", "name t1")
        assert state[RECORD_SCOPED_ENTITY_CACHE_KEY] == {"rg-1": "record_group"}

    @pytest.mark.asyncio
    async def test_records_it_shows_are_noted_as_found_by_entity(self, patched) -> None:
        patched[1].return_value = [
            _hit(f"t{i}", "topic", records=[{**_row(f"r{i}"), "virtualRecordId": f"vr-{i}"}])
            for i in range(1, 5)
        ]
        state = _state(include_retrieval_context=True)

        await execute_search_entities(state, "legal")

        assert state["retrieved_by"]["vr-1"].layers == ["entity"]
        # Only the first three entities' records are previewed.
        assert set(state["retrieved_by"]) == {"vr-1", "vr-2", "vr-3"}

    @pytest.mark.asyncio
    async def test_record_ids_go_through_shortener(self, patched) -> None:
        patched[1].return_value = [
            _hit("rec-9", "record", records=[_row("rec-9")]),
            _hit("t1", "topic", records=[_row("r1")]),
        ]
        state = _state(enable_record_id_shortening=True)

        _, text = await execute_search_entities(state, "legal")

        results = json.loads(text)["results"]
        assert results[0]["entityId"] == "R1"
        assert results[1]["entityId"] == "t1"
        assert results[1]["records"][0]["recordId"] == "R2"


class TestStrictAndExcludedScope:
    @pytest.mark.asyncio
    async def test_excluded_demo_apps_reach_the_access_context(self, patched) -> None:
        await execute_search_entities(_state(excluded_app_ids=frozenset({"demo-1"})), "legal")

        _, kwargs = patched[0].call_args
        assert kwargs["exclude_app_ids"] == frozenset({"demo-1"})
        assert kwargs["strict"] is False

    @pytest.mark.asyncio
    async def test_strict_empty_scope_finds_nothing_without_a_vector_query(self) -> None:
        """An agent whose only sources were removed searches nothing in content
        search; entity search must not widen to the whole org instead."""
        graph = MagicMock()
        graph.get_entity_access_context = AsyncMock()
        store = MagicMock()
        store.search_entities = AsyncMock()
        state = _state(
            apps=[], kb=[], graph_provider=graph, entity_vector_store=store,
            filters={"strictScope": True},
        )

        ok, text = await execute_search_entities(state, "legal")

        assert ok is True
        assert json.loads(text)["results"] == []
        graph.get_entity_access_context.assert_not_called()
        store.search_entities.assert_not_called()


@pytest.mark.asyncio
async def test_aliases_are_not_shown(patched) -> None:
    """KG-17: an alias is a spelling from some record, possibly one the user
    cannot read; it stays a matching aid and never reaches the tool output."""
    patched[1].return_value = [_hit("t1", "topic", records=[_row("r1")])]
    ok, text = await execute_search_entities(_state(), "legal")
    assert ok is True
    assert all("aliases" not in r for r in json.loads(text)["results"])


class TestWhereEntitySearchIsOffered:
    """``entity_search_offered``: the variant ``search_entities`` that comes with
    ``search_by_entities``. Without it, every call behaves as before."""

    @pytest.fixture(autouse=True)
    def _extraction_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(entity_discovery, "_named_entities_enabled", AsyncMock(return_value=True))

    @staticmethod
    async def _offered(**kwargs) -> tuple[bool, dict]:
        ok, text = await execute_search_entities(_state(), entity_search_offered=True, **kwargs)
        return ok, json.loads(text)

    @pytest.mark.asyncio
    async def test_each_query_is_searched_and_its_results_grouped_under_it(self, patched) -> None:
        _, search = patched

        async def by_query(store, graph, context, query, **kwargs) -> list[EntityHit]:
            return [_hit(f"e-{query}", "named_entity")] if query != "Nobody" else []

        search.side_effect = by_query
        ok, payload = await self._offered(query=None, queries=["Contoso", "Nobody", "Contoso", "  ", 7])
        assert ok and payload["status"] == "success"
        assert [q["query"] for q in payload["queries"]] == ["Contoso", "Nobody"]
        assert payload["queries"][0]["results"][0]["entityId"] == "e-Contoso"
        assert payload["queries"][1]["message"] == "No accessible entities matched"
        assert search.await_count == 2

    @pytest.mark.asyncio
    async def test_query_and_queries_together_search_each_once(self, patched) -> None:
        _, search = patched
        ok, payload = await self._offered(query="Contoso", queries=["Contoso", "Initech"])
        assert ok and [q["query"] for q in payload["queries"]] == ["Contoso", "Initech"]
        assert search.await_count == 2

    @pytest.mark.asyncio
    async def test_a_single_query_keeps_its_shape(self, patched) -> None:
        _, search = patched
        search.return_value = [_hit("e1", "named_entity")]
        ok, payload = await self._offered(query="Contoso")
        assert ok and "queries" not in payload and payload["results"]

    @pytest.mark.asyncio
    async def test_at_most_five_queries(self, patched) -> None:
        _, search = patched
        ok, payload = await self._offered(query=None, queries=[f"Org {i}" for i in range(7)])
        assert ok and len(payload["queries"]) == entity_discovery.MAX_QUERIES
        assert "Only the first 5" in payload["note"]
        assert search.await_count == entity_discovery.MAX_QUERIES

    @pytest.mark.asyncio
    async def test_every_query_failing_is_a_failed_call(self, patched) -> None:
        _, search = patched
        search.side_effect = EntityAccessError("down")
        ok, payload = await self._offered(query=None, queries=["A", "B"])
        assert not ok and payload["status"] == "error" and len(payload["queries"]) == 2

    @pytest.mark.asyncio
    async def test_one_query_failing_keeps_the_others(self, patched) -> None:
        _, search = patched

        async def flaky(store, graph, context, query, **kwargs) -> list[EntityHit]:
            if query == "B":
                raise EntityAccessError("down")
            return [_hit("e-a", "named_entity")]

        search.side_effect = flaky
        ok, payload = await self._offered(query=None, queries=["A", "B"])
        assert ok and "results" in payload["queries"][0] and "error" in payload["queries"][1]

    @pytest.mark.asyncio
    async def test_an_unexpected_failure_in_one_query_keeps_the_others(self, patched) -> None:
        _, search = patched

        async def broken(store, graph, context, query, **kwargs) -> list[EntityHit]:
            if query == "B":
                raise RuntimeError("driver blew up")
            return [_hit("e-a", "named_entity")]

        search.side_effect = broken
        ok, payload = await self._offered(query=None, queries=["A", "B"])
        assert ok and payload["queries"][0]["results"][0]["entityId"] == "e-a"
        assert payload["queries"][1]["error"] == "Entity search failed — try again."

    @pytest.mark.asyncio
    async def test_a_hit_carries_its_kind(self, patched) -> None:
        _, search = patched
        hit = _hit("e1", "named_entity")
        search.return_value = [EntityHit(**{**hit.__dict__, "kind": "organization"})]
        _, payload = await self._offered(query="Acme")
        assert payload["results"][0]["kind"] == "organization"

    @pytest.mark.asyncio
    async def test_kinds_it_cannot_serve_are_explained_and_not_sent(self, patched) -> None:
        _, search = patched
        _, payload = await self._offered(query="Acme", named_entity_kinds=["organization", "person", "date", "company"])
        assert search.call_args.kwargs["kinds"] == ["organization"]
        notes = " ".join(payload["kindNotes"])
        assert "search_by_entities" in notes and "find_records_by_value" in notes and "'company'" in notes

    @pytest.mark.asyncio
    async def test_only_unservable_kinds_never_widen_to_every_kind(self, patched) -> None:
        _, search = patched
        ok, payload = await self._offered(query="Sam", named_entity_kinds=["person"])
        assert ok and payload["results"] == [] and payload["kindNotes"]
        search.assert_not_called()

    @pytest.mark.asyncio
    async def test_unservable_kinds_beside_other_types_keep_searching_those_types(self, patched) -> None:
        _, search = patched
        search.return_value = [_hit("t1", "topic")]
        ok, payload = await self._offered(query="Sam", entity_types=["topic"], named_entity_kinds=["person"])
        assert ok and payload["results"][0]["entityId"] == "t1" and payload["kindNotes"]
        assert search.call_args.kwargs["entity_types"] == ["topic"] and not search.call_args.kwargs["kinds"]

    @pytest.mark.asyncio
    async def test_an_unservable_kind_named_as_an_entity_type_is_explained_too(self, patched) -> None:
        _, search = patched
        ok, payload = await self._offered(query="Sam", entity_types=["person"])
        assert ok and payload["results"] == [] and "search_by_entities" in " ".join(payload["kindNotes"])
        search.assert_not_called()


class TestWithoutEntitySearch:
    """Not offered: no kind check, no kind on hits, no note naming a tool the
    request does not have, exactly as before."""

    @pytest.fixture(autouse=True)
    def _extraction_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(entity_discovery, "_named_entities_enabled", AsyncMock(return_value=True))

    @pytest.mark.asyncio
    async def test_kinds_go_to_the_search_as_given(self, patched) -> None:
        _, search = patched
        ok, text = await execute_search_entities(_state(), "Sam", named_entity_kinds=["person", "organization"])
        assert ok and search.call_args.kwargs["kinds"] == ["person", "organization"]
        assert "kindNotes" not in json.loads(text) and "search_by_entities" not in text

    @pytest.mark.asyncio
    async def test_hits_carry_no_kind(self, patched) -> None:
        _, search = patched
        hit = _hit("e1", "named_entity")
        search.return_value = [EntityHit(**{**hit.__dict__, "kind": "organization"})]
        _, text = await execute_search_entities(_state(), "Acme")
        assert "kind" not in json.loads(text)["results"][0]

    @pytest.mark.asyncio
    async def test_queries_are_not_taken(self, patched) -> None:
        ok, _ = await execute_search_entities(_state(), None, queries=["Acme"])
        assert ok is False


class TestUploadDates:
    @pytest.mark.asyncio
    async def test_a_previewed_upload_shows_its_upload_date_not_a_modified_date(self, patched) -> None:
        uploaded = 1_792_090_099_000  # 2026-10-15
        upload = {
            **_row("r1", "kb-1"), "connectorName": "KB", "origin": "UPLOAD",
            "createdAtTimestamp": uploaded, "sourceCreatedAtTimestamp": uploaded - 30,
            "sourceLastModifiedTimestamp": uploaded - 30,
        }
        patched[1].return_value = [_hit("t1", "topic", records=[upload])]

        ok, text = await execute_search_entities(_state(), "legal")

        assert ok is True
        preview = json.loads(text)["results"][0]["records"][0]
        assert preview["uploaded"] == "2026-10-15"
        assert "modified" not in preview
