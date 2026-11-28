"""Unit tests for ``ops/entity_discovery.py`` (``search_entities``)."""
import json
import logging
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.knowledge_graph.ops import entity_discovery, entity_filters
from app.agents.actions.knowledge_graph.ops.entity_discovery import (
    INCOMPLETE_NOTE,
    execute_search_entities,
)
from app.agents.actions.knowledge_graph.ops.entity_filters import (
    ENTITY_ID_FILTER_KEY_CACHE_KEY,
    ENTITY_INDEX_CACHE_KEY,
    LIMITED_SCOPE_NOTE,
    RECORD_SCOPED_ENTITY_CACHE_KEY,
)
from app.modules.agents.context.named_sources import NAMED_SOURCES_KEY, NamedSources
from app.modules.agents.privacy.ledger import LabelLedger, bind_ledger
from app.modules.retrieval.entity_permissions import (
    EntityAccessContext,
    EntityAccessError,
    EntityHit,
    EntitySearchHits,
    EntitySearchTimedOut,
)
from app.modules.retrieval.selection_scope import SelectionScope
from app.modules.retrieval.title_hits import TITLE_SEARCH_TOP_K
from tests.support.log_capture import assert_text_free
from tests.support.provider_errors import SENTINELS, openai_error, with_cause

CONTEXT = EntityAccessContext(
    org_id="org-1",
    user_key="ukey",
    app_level_app_ids=frozenset({"kb-1"}),
    record_level_app_ids=frozenset({"conf-1"}),
    record_group_ids=frozenset(),
    app_names={"kb-1": "Team KB", "conf-1": "Confluence"},
)


def _matching_store(*matching: str) -> MagicMock:
    """A vector store whose ``dense_similarities`` scores every name in
    ``matching`` (every name, when none is given) as a match for the query."""
    store = MagicMock()
    store.dense_similarities = AsyncMock(side_effect=lambda query, names: [
        0.9 if not matching or name in matching else 0.2 for name in names
    ])
    return store


def _state(**overrides) -> dict:
    state = {
        "org_id": "org-1",
        "user_id": "user-1",
        "graph_provider": MagicMock(),
        "entity_vector_store": _matching_store(),
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
        _, search_kwargs = patched[1].call_args_list[0]
        assert (search_kwargs["entity_types"], search_kwargs["kinds"]) == (["topic"], None)

    @pytest.mark.asyncio
    async def test_a_kind_given_as_an_entity_type_searches_named_entities(self, patched, monkeypatch) -> None:
        monkeypatch.setattr(entity_discovery, "_named_entities_enabled", AsyncMock(return_value=True))
        ok, text = await execute_search_entities(_state(), "ValeHealth", entity_types=["organization", "record"])
        assert ok is True
        assert "ignoredEntityTypes" not in json.loads(text)
        _, search_kwargs = patched[1].call_args_list[0]
        assert (search_kwargs["entity_types"], search_kwargs["kinds"]) == (
            ["organization", "record", "named_entity"], ["organization", "product"],
        )

    @pytest.mark.asyncio
    async def test_an_organisation_search_with_extraction_off_searches_the_organisation_entities(
        self, patched, monkeypatch,
    ) -> None:
        monkeypatch.setattr(entity_discovery, "_named_entities_enabled", AsyncMock(return_value=False))
        ok, _ = await execute_search_entities(_state(), "Orrin Biotech", entity_types=["organization"])
        assert ok is True
        _, search_kwargs = patched[1].call_args_list[0]
        assert (search_kwargs["entity_types"], search_kwargs["kinds"]) == (["organization"], None)

    @pytest.mark.asyncio
    async def test_a_person_search_with_extraction_off_searches_the_people_entities(
        self, patched, monkeypatch,
    ) -> None:
        # "person" is both an entity type (the members records name) and a
        # named-entity kind: read as the kind alone, every people lookup failed
        # while extraction was off, the default.
        monkeypatch.setattr(entity_discovery, "_named_entities_enabled", AsyncMock(return_value=False))
        ok, _ = await execute_search_entities(_state(), "Mira Kowalski", entity_types=["person"])
        assert ok is True
        _, search_kwargs = patched[1].call_args_list[0]
        assert (search_kwargs["entity_types"], search_kwargs["kinds"]) == (["person"], None)

    @pytest.mark.asyncio
    async def test_a_person_search_with_extraction_on_also_searches_named_people(
        self, patched, monkeypatch,
    ) -> None:
        monkeypatch.setattr(entity_discovery, "_named_entities_enabled", AsyncMock(return_value=True))
        ok, _ = await execute_search_entities(_state(), "Mira Kowalski", entity_types=["person"])
        assert ok is True
        _, search_kwargs = patched[1].call_args_list[0]
        assert (search_kwargs["entity_types"], search_kwargs["kinds"]) == (["person", "named_entity"], ["person"])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("entity_type", ["person", "organization"])
    async def test_an_explicit_named_entity_search_is_not_narrowed_by_a_type_that_is_also_a_kind(
        self, patched, monkeypatch, entity_type,
    ) -> None:
        monkeypatch.setattr(entity_discovery, "_named_entities_enabled", AsyncMock(return_value=True))
        ok, _ = await execute_search_entities(_state(), "Mira", entity_types=[entity_type, "named_entity"])
        assert ok is True
        _, search_kwargs = patched[1].call_args_list[0]
        assert (search_kwargs["entity_types"], search_kwargs["kinds"]) == ([entity_type, "named_entity"], None)

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


class TestResultsTheDeadlineCutShort:
    """The agent must not read a match missing from a cut-short search as one
    the user cannot reach, nor see anything the access check did not confirm."""

    @pytest.mark.asyncio
    async def test_cut_short_results_are_listed_and_flagged(self, patched) -> None:
        patched[1].return_value = EntitySearchHits([_hit("t1", "topic", records=[_row("r1")])], incomplete=True)
        ok, text = await execute_search_entities(_state(), "legal")
        payload = json.loads(text)
        assert ok is True
        assert [r["entityId"] for r in payload["results"]] == ["t1"]
        assert payload["incomplete"] is True and payload["incompleteNote"] == INCOMPLETE_NOTE

    @pytest.mark.asyncio
    async def test_no_confirmed_match_in_a_cut_short_search_is_not_a_plain_no_match(self, patched) -> None:
        patched[1].return_value = EntitySearchHits([], incomplete=True)
        ok, text = await execute_search_entities(_state(), "legal")
        payload = json.loads(text)
        assert ok is True and payload["results"] == []
        assert payload["incomplete"] is True and "may be missing" in payload["incompleteNote"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("hits", [EntitySearchHits([_hit("t1", "topic")]), [_hit("t1", "topic")], []])
    async def test_a_finished_search_carries_no_flag(self, patched, hits) -> None:
        patched[1].return_value = hits
        payload = json.loads((await execute_search_entities(_state(), "legal"))[1])
        assert "incomplete" not in payload and "incompleteNote" not in payload

    @pytest.mark.asyncio
    async def test_each_query_of_a_batch_keeps_its_own_flag(self, patched) -> None:
        async def _search(store, graph, context, query, **kwargs) -> EntitySearchHits:
            return EntitySearchHits([_hit(query, "topic")], incomplete=query == "slow")

        patched[1].side_effect = _search
        ok, text = await execute_search_entities(
            _state(), None, queries=["slow", "fast"], entity_search_offered=True,
        )
        by_query = {q["query"]: q for q in json.loads(text)["queries"]}
        assert ok is True
        assert by_query["slow"]["incomplete"] is True
        assert "incomplete" not in by_query["fast"]



class TestATypeFilterThatMatchesNoName:
    """A model guesses the type of a name it has never seen: "SAAS Launch" asked
    for as a record group (a project) when it is an epic, a record. The narrowed
    search then returns its nearest record groups, unrelated, ranked as if they
    matched. When no name in a filtered answer matches the query, entities of
    other types whose names do are listed apart, each one access-checked."""

    UNRELATED = [_hit("rg-sd", "record_group"), _hit("rg-es", "record_group")]
    EPIC = _hit("epic-1", "record", records=[_row("epic-1")])

    @staticmethod
    def _names(*ids: str) -> list[str]:
        return [f"name {i}" for i in ids]

    @pytest.mark.asyncio
    async def test_the_saas_launch_case_lists_the_epic_beside_the_filtered_answer(self, patched) -> None:
        patched[1].side_effect = [self.UNRELATED, [self.EPIC]]
        state = _state(entity_vector_store=_matching_store(*self._names("epic-1")))

        ok, text = await execute_search_entities(state, "SAAS Launch", entity_types=["record_group"])

        payload = json.loads(text)
        assert ok is True
        assert [r["entityId"] for r in payload["results"]] == ["rg-sd", "rg-es"]
        assert [r["entityId"] for r in payload["otherTypeMatches"]] == ["epic-1"]
        assert "SAAS Launch" in payload["otherTypeNote"] and "record_group" in payload["otherTypeNote"]
        # The wider search is of the other types, and reads more candidates than it shows.
        wider = patched[1].call_args_list[1].kwargs
        assert "record_group" not in wider["entity_types"] and "record" in wider["entity_types"]
        assert wider["top_k"] == TITLE_SEARCH_TOP_K
        # An entity id the agent was shown can be used by the next call.
        assert state[ENTITY_INDEX_CACHE_KEY]["epic-1"]["type"] == "record"
        assert "epic-1" in state["known_record_ids"]

    @pytest.mark.asyncio
    async def test_a_filtered_answer_whose_name_matches_is_left_alone(self, patched) -> None:
        patched[1].return_value = self.UNRELATED
        state = _state(entity_vector_store=_matching_store(*self._names("rg-sd")))

        payload = json.loads((await execute_search_entities(state, "Software", entity_types=["record_group"]))[1])

        assert "otherTypeMatches" not in payload
        assert patched[1].await_count == 1

    @pytest.mark.asyncio
    async def test_without_a_type_filter_nothing_more_is_searched(self, patched) -> None:
        patched[1].return_value = self.UNRELATED
        state = _state(entity_vector_store=_matching_store("no name matches"))

        payload = json.loads((await execute_search_entities(state, "SAAS Launch"))[1])

        assert "otherTypeMatches" not in payload
        assert patched[1].await_count == 1

    @pytest.mark.asyncio
    async def test_a_filter_that_found_nothing_still_lists_other_types(self, patched) -> None:
        patched[1].side_effect = [[], [self.EPIC]]
        state = _state(entity_vector_store=_matching_store(*self._names("epic-1")))

        payload = json.loads((await execute_search_entities(state, "SAAS Launch", entity_types=["topic"]))[1])

        assert payload["results"] == [] and payload["message"] == "No accessible entities of the types asked for matched"
        assert [r["entityId"] for r in payload["otherTypeMatches"]] == ["epic-1"]

    @pytest.mark.asyncio
    async def test_only_other_types_whose_names_match_are_listed(self, patched) -> None:
        """The wider search returns nearest neighbours too; one whose name does not
        match is as unrelated as the filtered answer was."""
        patched[1].side_effect = [self.UNRELATED, [self.EPIC, _hit("t-x", "topic")]]
        state = _state(entity_vector_store=_matching_store(*self._names("epic-1")))

        payload = json.loads((await execute_search_entities(state, "SAAS Launch", entity_types=["record_group"]))[1])

        assert [r["entityId"] for r in payload["otherTypeMatches"]] == ["epic-1"]

    @pytest.mark.asyncio
    async def test_an_entity_already_listed_is_not_listed_twice(self, patched) -> None:
        patched[1].side_effect = [self.UNRELATED, [self.UNRELATED[0], self.EPIC]]
        # rg-sd is dropped as already listed, before its name is compared.
        state = _state(entity_vector_store=_matching_store(*self._names("epic-1")))

        payload = json.loads((await execute_search_entities(state, "SAAS Launch", entity_types=["record_group"]))[1])

        assert [r["entityId"] for r in payload["otherTypeMatches"]] == ["epic-1"]

    @pytest.mark.asyncio
    async def test_a_wider_search_cut_by_its_deadline_marks_the_answer_incomplete(self, patched) -> None:
        """The filtered names matched nothing, and the search that could find the meant entity
        ran out of time: "nothing else matched" would be a guess."""
        patched[1].side_effect = [self.UNRELATED, EntitySearchTimedOut("deadline")]
        state = _state(entity_vector_store=_matching_store("no name matches"))

        ok, text = await execute_search_entities(state, "SAAS Launch", entity_types=["record_group"])

        payload = json.loads(text)
        assert ok is True
        assert [r["entityId"] for r in payload["results"]] == ["rg-sd", "rg-es"]
        assert "otherTypeMatches" not in payload
        assert payload["incomplete"] is True and payload["incompleteNote"] == INCOMPLETE_NOTE

    @pytest.mark.asyncio
    async def test_a_wider_search_left_incomplete_marks_the_answer_incomplete(self, patched) -> None:
        patched[1].side_effect = [self.UNRELATED, EntitySearchHits([self.EPIC], incomplete=True)]
        state = _state(entity_vector_store=_matching_store(*self._names("epic-1")))

        payload = json.loads((await execute_search_entities(state, "SAAS Launch", entity_types=["record_group"]))[1])

        assert [r["entityId"] for r in payload["otherTypeMatches"]] == ["epic-1"]
        assert payload["incomplete"] is True

    @pytest.mark.asyncio
    async def test_a_wider_search_that_fails_says_so(self, patched) -> None:
        patched[1].side_effect = [self.UNRELATED, EntityAccessError("down")]
        state = _state(entity_vector_store=_matching_store("no name matches"))

        ok, text = await execute_search_entities(state, "SAAS Launch", entity_types=["record_group"])

        payload = json.loads(text)
        assert ok is True
        assert [r["entityId"] for r in payload["results"]] == ["rg-sd", "rg-es"]
        assert "could not be searched" in payload["otherTypeNote"]
        assert "incomplete" not in payload

    @pytest.mark.asyncio
    async def test_a_match_ranked_past_the_results_shown_is_still_found(self, patched) -> None:
        """The epic ranks after more than top_k other entities of every type."""
        crowd = [_hit(f"t-{i}", "topic") for i in range(TITLE_SEARCH_TOP_K - 1)]
        answers = iter([self.UNRELATED, [*crowd, self.EPIC]])

        async def _search(store, graph, context, query, *, top_k, **kwargs) -> list:
            return next(answers)[:top_k]  # as retrieval does: no more than top_k hits

        patched[1].side_effect = _search
        state = _state(entity_vector_store=_matching_store(*self._names("epic-1")))

        payload = json.loads((await execute_search_entities(
            state, "SAAS Launch", entity_types=["record_group"], top_k=3,
        ))[1])

        assert [r["entityId"] for r in payload["otherTypeMatches"]] == ["epic-1"]

    @pytest.mark.asyncio
    async def test_a_requested_type_is_never_listed_as_another_type(self, patched) -> None:
        """A matching record group ranked past the filtered results is not "of another type", and
        requested types must not take the wider search's candidate slots: it is asked for the
        other types only."""
        patched[1].side_effect = [self.UNRELATED, [self.EPIC]]
        state = _state(entity_vector_store=_matching_store(*self._names("epic-1")))

        payload = json.loads((await execute_search_entities(state, "SAAS Launch", entity_types=["record_group"]))[1])

        wider_types = patched[1].call_args_list[1].kwargs["entity_types"]
        assert "record_group" not in wider_types and "record" in wider_types
        assert [r["entityId"] for r in payload["otherTypeMatches"]] == ["epic-1"]

    @pytest.mark.asyncio
    async def test_named_entities_asked_for_only_by_unservable_kinds_stay_out_of_the_fallback(
        self, patched, monkeypatch,
    ) -> None:
        monkeypatch.setattr(entity_discovery, "_named_entities_enabled", AsyncMock(return_value=True))
        patched[1].side_effect = [self.UNRELATED, [self.EPIC]]
        state = _state(entity_vector_store=_matching_store(*self._names("epic-1")))

        payload = json.loads((await execute_search_entities(
            state, "SAAS Launch", entity_types=["topic"], named_entity_kinds=["person"], entity_search_offered=True,
        ))[1])

        wider = patched[1].call_args_list[1].kwargs
        assert wider["include_named"] is False and "named_entity" not in wider["entity_types"]
        assert payload["kindNotes"]

    @pytest.mark.asyncio
    async def test_a_name_comparison_that_fails_is_not_reported_as_no_match(self, patched) -> None:
        patched[1].return_value = self.UNRELATED
        store = MagicMock()
        store.dense_similarities = AsyncMock(side_effect=RuntimeError("embedding down"))

        payload = json.loads((await execute_search_entities(
            _state(entity_vector_store=store), "SAAS Launch", entity_types=["record_group"],
        ))[1])

        assert [r["entityId"] for r in payload["results"]] == ["rg-sd", "rg-es"]
        assert "could not be compared" in payload["otherTypeNote"] and "No returned name" not in payload["otherTypeNote"]
        assert patched[1].await_count == 1

    @pytest.mark.asyncio
    async def test_the_fallback_cannot_hold_back_the_filtered_answer(self, patched, monkeypatch) -> None:
        """Past the entity search's own budget the filtered results come back, marked incomplete."""
        import asyncio

        monkeypatch.setattr(entity_discovery, "SEARCH_DEADLINE_SECONDS", 0.2)

        async def _search(store, graph, context, query, **kwargs) -> list:
            if patched[1].await_count == 1:
                return self.UNRELATED
            await asyncio.sleep(5)
            return [self.EPIC]

        patched[1].side_effect = _search
        state = _state(entity_vector_store=_matching_store("no name matches"))
        started = asyncio.get_running_loop().time()

        payload = json.loads((await execute_search_entities(state, "SAAS Launch", entity_types=["record_group"]))[1])

        # wall-clock-ok: a hang guard: the fallback search is instant, a stuck one waits out its 30 s budget
        assert asyncio.get_running_loop().time() - started < 2
        assert [r["entityId"] for r in payload["results"]] == ["rg-sd", "rg-es"]
        assert payload["incomplete"] is True

    @pytest.mark.asyncio
    async def test_with_matches_of_other_types_only_the_requested_types_came_up_empty(self, patched) -> None:
        patched[1].side_effect = [[], [self.EPIC]]
        state = _state(entity_vector_store=_matching_store(*self._names("epic-1")))

        payload = json.loads((await execute_search_entities(state, "SAAS Launch", entity_types=["topic"]))[1])

        assert payload["message"] == "No accessible entities of the types asked for matched"

    @pytest.mark.asyncio
    async def test_a_preview_row_without_a_record_type_leaves_type_out(self, patched) -> None:
        """The schema's ``type`` is a string; null would break it."""
        row = {k: v for k, v in _row("r-x").items() if k != "recordType"}
        patched[1].return_value = [_hit("t1", "topic", records=[row])]

        payload = json.loads((await execute_search_entities(_state(), "legal"))[1])

        assert "type" not in payload["results"][0]["records"][0]

    @pytest.mark.asyncio
    async def test_other_type_matches_stop_at_top_k(self, patched) -> None:
        epics = [_hit(f"epic-{i}", "record") for i in range(5)]
        patched[1].side_effect = [self.UNRELATED, epics]
        state = _state(entity_vector_store=_matching_store(*self._names(*(f"epic-{i}" for i in range(5)))))

        payload = json.loads((await execute_search_entities(
            state, "SAAS Launch", entity_types=["record_group"], top_k=2,
        ))[1])

        assert [r["entityId"] for r in payload["otherTypeMatches"]] == ["epic-0", "epic-1"]

    @pytest.mark.asyncio
    async def test_a_word_that_is_no_entity_type_is_explained_not_dropped(self, patched) -> None:
        patched[1].return_value = self.UNRELATED
        state = _state()

        payload = json.loads((await execute_search_entities(
            state, "SAAS Launch", entity_types=["record_group", "project"],
        ))[1])

        assert payload["ignoredEntityTypes"] == ["project"]
        assert "'record_group'" in payload["entityTypesNote"] and "'record'" in payload["entityTypesNote"]
        assert "Omit entity_types" in payload["entityTypesNote"]

    @pytest.mark.asyncio
    async def test_only_words_that_are_no_entity_type_name_the_types_that_hold_them(self, patched) -> None:
        ok, text = await execute_search_entities(_state(), "SAAS Launch", entity_types=["epic"])

        assert ok is False
        message = json.loads(text)["message"]
        assert "an epic is a 'record'" in message.lower() and "supported:" in message
        patched[1].assert_not_awaited()


class TestSearch:
    @pytest.mark.asyncio
    async def test_scope_comes_from_agent_sources(self, patched) -> None:
        await execute_search_entities(_state(), "legal", entity_types=["topic", "product"], top_k=99)

        _, kwargs = patched[0].call_args
        assert kwargs["source_ids"] == ["conf-1", "kb-1"]
        _, search_kwargs = patched[1].call_args_list[0]
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


class _Shown(LabelLedger):
    """A ledger that only keeps what a writer showed the model."""

    labels = True

    def __init__(self) -> None:
        self.records: set[str] = set()
        self.nodes: set[str] = set()

    def shown(self, *, records=(), vrids=(), nodes=(), call_id=None) -> None:
        self.records.update(records)
        self.nodes.update(nodes)


class TestTheAppsItNames:
    """Each entity lists the apps its records are in. An app named for records the result does not show is a
    source named by the tool, so the answer's label lists it; the shown records cover their own apps."""

    @pytest.mark.asyncio
    async def test_an_app_named_for_records_it_does_not_show_is_labelled(self, patched) -> None:
        patched[0].return_value = replace(CONTEXT, app_names={**CONTEXT.app_names, "drive-9": "Team Drive"})
        patched[1].return_value = [
            *(_hit(f"t{i}", "topic", records=[_row(f"r{i}")]) for i in range(1, 4)),
            _hit("c1", "category", records=[_row("r5", "drive-9")]),
        ]
        ledger = _Shown()

        with bind_ledger(ledger):
            _, text = await execute_search_entities(_state(), "legal")

        listed = json.loads(text)["results"][3]
        assert listed["apps"] == ["Team Drive"] and "records" not in listed
        assert "drive-9" in ledger.nodes
        assert ledger.records == {"r1", "r2", "r3"}
        assert "conf-1" not in ledger.nodes


class TestInATurnOthersRead:
    """The run may name only the sources its prompts name: an entity lists the others without their names, and
    the label needs no node for them."""

    @staticmethod
    def _limited(*named: str) -> dict:
        return _state(**{NAMED_SOURCES_KEY: NamedSources(knowledge=tuple({"connectorId": c} for c in named), limited=True)})

    @pytest.mark.asyncio
    async def test_an_app_it_may_not_name_is_counted_not_named(self, patched) -> None:
        patched[1].return_value = [
            _hit("t1", "topic", records=[_row("r1"), _row("r2", "kb-1")]),
            *(_hit(f"t{i}", "topic", records=[_row(f"r{i}")]) for i in range(2, 4)),
            _hit("c1", "category", records=[_row("r5", "kb-1")]),
        ]
        ledger = _Shown()

        with bind_ledger(ledger):
            _, text = await execute_search_entities(self._limited("conf-1"), "legal")

        results = json.loads(text)["results"]
        assert results[0]["apps"] == ["Confluence"] and results[0]["otherSources"] == 1
        assert "app" not in results[0]["records"][1]
        assert "apps" not in results[3] and results[3]["otherSources"] == 1
        assert "Team KB" not in text and "kb-1" not in text
        assert "kb-1" not in ledger.nodes
        assert ledger.records == {"r1", "r2", "r3"}

    @pytest.mark.asyncio
    async def test_a_collection_it_may_not_name_is_left_out_and_counted(self, patched) -> None:
        # A collection is a record group: its own hit carries its name and id.
        patched[1].return_value = [
            _hit("kb-9", "record_group", records=[_row("r1", "kb-1")]),
            _hit("t1", "topic", records=[_row("r2")]),
        ]
        state = self._limited("conf-1")
        ledger = _Shown()

        with bind_ledger(ledger):
            ok, text = await execute_search_entities(state, "private notes")

        payload = json.loads(text)
        assert ok and [r["entityId"] for r in payload["results"]] == ["t1"]
        assert "kb-9" not in text and "r1" not in text
        assert payload["note"].startswith("1 other source not listed")
        assert "kb-9" not in (state.get(ENTITY_ID_FILTER_KEY_CACHE_KEY) or {})
        assert "kb-9" not in (state.get(RECORD_SCOPED_ENTITY_CACHE_KEY) or {})
        assert ledger.records == {"r2"} and not ledger.nodes

    @pytest.mark.asyncio
    async def test_a_picked_collection_and_a_group_in_a_picked_app_are_listed(self, patched) -> None:
        patched[1].return_value = [
            _hit("kb-9", "record_group", records=[_row("r1", "kb-1")]),
            _hit("g-1", "record_group", records=[_row("r2")]),
            _hit("g-2", "record_group", records=[_row("r3", "slack-1")]),
            _hit("g-3", "record_group"),
        ]

        _, text = await execute_search_entities(self._limited("kb-9", "conf-1"), "notes")

        payload = json.loads(text)
        assert [r["entityId"] for r in payload["results"]] == ["kb-9", "g-1"]
        assert "g-2" not in text and "g-3" not in text
        assert payload["note"].startswith("2 other sources not listed")

    @pytest.mark.asyncio
    async def test_when_it_may_name_no_hit_it_says_what_it_left_out(self, patched) -> None:
        patched[1].return_value = [_hit("kb-9", "record_group", records=[_row("r1", "kb-1")])]

        ok, text = await execute_search_entities(self._limited(), "private notes")

        payload = json.loads(text)
        assert ok and payload["results"] == [] and "kb-9" not in text
        assert payload["note"].startswith("1 other source not listed")
        assert "message" not in payload

    @pytest.mark.asyncio
    async def test_a_run_nobody_else_reads_names_them_as_before(self, patched) -> None:
        patched[1].return_value = [
            _hit("t1", "topic", records=[_row("r1"), _row("r2", "kb-1")]),
            _hit("kb-9", "record_group", records=[_row("r3", "kb-1")]),
        ]

        _, text = await execute_search_entities(_state(), "legal")

        payload = json.loads(text)
        topic, collection = payload["results"]
        assert topic["apps"] == ["Confluence", "Team KB"] and "otherSources" not in topic
        assert collection["name"] == "name kb-9" and "note" not in payload


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
        assert search.call_args_list[0].kwargs["kinds"] == ["organization", "product"]
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
        assert search.call_args_list[0].kwargs["entity_types"] == ["topic"] and not search.call_args_list[0].kwargs["kinds"]

    @pytest.mark.asyncio
    async def test_an_unservable_kind_named_as_an_entity_type_is_explained_too(self, patched) -> None:
        # The people records name are still searched; only named people are not.
        _, search = patched
        ok, payload = await self._offered(query="Sam", entity_types=["person"])
        assert ok and "search_by_entities" in " ".join(payload["kindNotes"])
        assert (search.call_args_list[0].kwargs["entity_types"], search.call_args_list[0].kwargs["kinds"]) == (["person"], None)

    @pytest.mark.asyncio
    async def test_person_beside_an_explicit_named_entity_keeps_the_named_entity_search(self, patched) -> None:
        _, search = patched
        ok, _ = await self._offered(query="Sam", entity_types=["person", "named_entity"])
        assert ok
        assert (search.call_args_list[0].kwargs["entity_types"], search.call_args_list[0].kwargs["kinds"]) == (
            ["person", "named_entity"], None,
        )


class TestWithoutEntitySearch:
    """Not offered: no kind check, no kind on hits, no note naming a tool the
    request does not have, exactly as before."""

    @pytest.fixture(autouse=True)
    def _extraction_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(entity_discovery, "_named_entities_enabled", AsyncMock(return_value=True))

    @pytest.mark.asyncio
    async def test_kinds_go_to_the_search_with_the_kinds_their_names_are_filed_under(self, patched) -> None:
        _, search = patched
        ok, text = await execute_search_entities(_state(), "Sam", named_entity_kinds=["person", "organization"])
        assert ok and search.call_args_list[0].kwargs["kinds"] == ["person", "organization", "product"]
        assert "kindNotes" not in json.loads(text) and "search_by_entities" not in text

    @pytest.mark.asyncio
    async def test_a_place_kind_reaches_every_place_kind(self, patched) -> None:
        _, search = patched
        await execute_search_entities(_state(), "Hamburg", named_entity_kinds=["state"])
        assert set(search.call_args_list[0].kwargs["kinds"]) == {"location", "city", "state", "country_region"}

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


class TestNoReadableMatch:
    """Run over the real access probe: a query whose only hits U cannot read must
    answer as a query with no hits, also when the probe runs out of time."""

    @staticmethod
    async def _ask(monkeypatch: pytest.MonkeyPatch, hits: list[dict], *, stall: bool) -> tuple[bool, str]:
        import asyncio

        from app.modules.retrieval import entity_permissions as ep
        from tests.unit.modules.retrieval.entity_access_fakes import entity_graph

        monkeypatch.setattr(ep, "SEARCH_DEADLINE_SECONDS", 0.05)
        monkeypatch.setattr(ep, "PROBE_MIN_SECONDS", 0.05)
        monkeypatch.setattr(entity_filters, "get_entity_access_context", AsyncMock(return_value=CONTEXT))
        rows = [_row(f"d{i}") for i in range(2000)]

        async def build(refs: list, org: str, *, limit_per_entity: int, offset: int, **_: object) -> dict:
            if stall and offset:
                await asyncio.sleep(1.0)
            return {r["id"]: rows[offset:offset + limit_per_entity] for r in refs}

        store = MagicMock()
        store.search_entities_passes = AsyncMock(side_effect=lambda q, org, passes, **k: [list(hits) for _ in passes])
        return await execute_search_entities(_state(graph_provider=entity_graph(build), entity_vector_store=store), "Globex")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stall", [False, True])
    async def test_unreadable_hits_read_as_no_hits(self, monkeypatch: pytest.MonkeyPatch, stall: bool) -> None:
        unknown = await self._ask(monkeypatch, [], stall=False)
        hit = {"entityId": "t-globex", "entityType": "topic", "name": "Globex", "score": 0.9}
        reply = await self._ask(monkeypatch, [hit], stall=stall)
        assert reply == unknown and json.loads(reply[1])["message"] == "No accessible entities matched"
        assert "Globex" not in reply[1] and "t-globex" not in reply[1]


class TestRecordHitsWhoseContentIsNotIndexed:
    """A record found by name is labelled when search cannot read its text yet."""

    @staticmethod
    def _record_hit(status: str | None, *, indexed: bool, key: str = "r1") -> EntityHit:
        return replace(
            _hit(key, "record", records=[_row(key)]), indexing_status=status, indexed=indexed,
        )

    @pytest.mark.asyncio
    async def test_an_unindexed_record_carries_its_status_and_the_note(self, patched) -> None:
        patched[1].return_value = [self._record_hit("QUEUED", indexed=False)]
        ok, text = await execute_search_entities(_state(), "plan", entity_types=["record"])
        (item,) = json.loads(text)["results"]
        assert ok and item["indexingStatus"] == "QUEUED"
        assert "content is not indexed yet (status: QUEUED)" in item["note"]

    @pytest.mark.asyncio
    async def test_a_record_with_automatic_indexing_off_is_found_with_the_note(self, patched) -> None:
        patched[1].return_value = [self._record_hit("AUTO_INDEX_OFF", indexed=False)]
        _, text = await execute_search_entities(_state(), "plan", entity_types=["record"])
        (item,) = json.loads(text)["results"]
        assert "automatic indexing is off" in item["note"]

    @pytest.mark.asyncio
    async def test_an_indexed_record_is_unchanged(self, patched) -> None:
        patched[1].return_value = [self._record_hit("COMPLETED", indexed=True)]
        _, text = await execute_search_entities(_state(), "plan", entity_types=["record"])
        (item,) = json.loads(text)["results"]
        assert "note" not in item and "indexingStatus" not in item

    @pytest.mark.asyncio
    async def test_a_missing_status_does_not_crash(self, patched) -> None:
        patched[1].return_value = [self._record_hit(None, indexed=False)]
        _, text = await execute_search_entities(_state(), "plan", entity_types=["record"])
        (item,) = json.loads(text)["results"]
        assert "status: unknown" in item["note"]

    @pytest.mark.asyncio
    async def test_the_record_is_remembered_so_it_can_be_fetched(self, patched) -> None:
        patched[1].return_value = [self._record_hit("QUEUED", indexed=False)]
        state = _state()
        await execute_search_entities(state, "plan", entity_types=["record"])
        assert "r1" in state["known_record_ids"]


@pytest.mark.asyncio
async def test_a_failed_query_embedding_is_logged_by_type_without_the_provider_text(patched, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    patched[1].side_effect = with_cause(EntityAccessError("Entity vector search failed"), openai_error(429))

    ok, _ = await execute_search_entities(_state(), "legal")

    assert ok is False
    (line,) = [r for r in caplog.records if r.getMessage() == "search_entities failed"]
    assert (line.levelno, line.error_type, line.cause_type, line.exc_info) == (
        logging.WARNING, "EntityAccessError", "RateLimitError", None,
    )
    assert_text_free(caplog.records, *SENTINELS)
