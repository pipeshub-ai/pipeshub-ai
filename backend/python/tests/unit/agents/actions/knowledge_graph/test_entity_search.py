"""``knowledgegraph__search_by_entities``: the tool's schema and what its op
does around the linking service. Linking itself is tested in
``tests/unit/modules/named_entities/linking``."""

from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from app.agent_loop_lib.core.responses import TokenUsage
from app.agent_loop_lib.tools.decorators import TOOL_META_ATTR
from app.agents.actions.knowledge_graph.knowledge_graph import KnowledgeGraph
from app.agents.actions.knowledge_graph.ops import entity_search
from app.agents.actions.knowledge_graph.ops.entity_discovery import (
    NAMED_ENTITIES_DISABLED_MSG,
)
from app.agents.actions.knowledge_graph.ops.entity_records import LOOKUP_FAILED_MSG
from app.agents.agent_loop.protocol.run_usage import ENTITY_LINKING_SOURCE, UsageLedger
from app.modules.named_entities.linking.candidates import Candidate, CandidateSource
from app.modules.named_entities.linking.mentions import parse_mentions
from app.modules.named_entities.linking.records import (
    NEARBY,
    SAME_RECORD,
    NameRecords,
    RecordScope,
)
from app.modules.named_entities.linking.service import Linking, LinkResult, LinkStatus
from app.modules.retrieval.entity_permissions import EntityAccessError
from app.modules.retrieval.provenance import RETRIEVED_BY_KEY


def _meta() -> Any:  # noqa: ANN401 - ToolMeta
    return getattr(KnowledgeGraph.search_by_entities, TOOL_META_ATTR)


class TestSchema:
    def test_mentions_are_objects_with_a_required_name_and_a_kind_enum(self) -> None:
        params = {p.name: p for p in _meta().parameters}
        mentions = params["mentions"]
        assert mentions.required and mentions.items["type"] == "object"
        assert mentions.items["required"] == ["name"]
        kinds = mentions.items["properties"]["kind"]["enum"]
        assert {"person", "organization", "product", "location", "email", "url"} <= set(kinds)
        assert not {"date", "currency", "phone", "ip", "address"} & set(kinds)
        assert params["match"].enum == ["any", "all"] and not params["match"].required
        assert not params["query"].required

    def test_the_description_says_when_not_to_use_it_and_that_a_miss_needs_confirming(self) -> None:
        description = _meta().description
        assert "Do not use it for topics" in description and "find_records_by_value" in description
        assert 'match=\\"all\\"' not in description and 'match="all"' in description
        assert "confirm a name that is not found with search" in description

    def test_the_method_passes_its_arguments_through(self) -> None:
        source = inspect.getsource(KnowledgeGraph.search_by_entities)
        assert "execute_search_by_entities(self.state, mentions=mentions, match=match, query=query)" in source


def _candidate(entity_id: str, name: str, kind: str = "organization") -> Candidate:
    return Candidate(entity_id, kind, name, CandidateSource.EXACT, ({"_key": "r1"},), False)


def _linking(*results: LinkResult, timed_out: bool = False) -> Linking:
    names = {c.entity_id: c.name for r in results for c in [*r.accepted, *r.rejected, *r.unchecked]}
    return Linking(results=list(results), timed_out=timed_out, verifier_called=False, names=names)


def _result(
    name: str, status: LinkStatus, *, accepted=(), rejected=(), unchecked=(), kind: str | None = "organization",
) -> LinkResult:
    (mention,) = parse_mentions([{"name": name, **({"kind": kind} if kind else {})}]).mentions
    return LinkResult(mention, status, accepted=list(accepted), rejected=list(rejected), unchecked=list(unchecked))


def _state(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    state: dict[str, Any] = {
        "org_id": "org", "user_id": "user", "graph_provider": AsyncMock(), "query": "the question",
        "include_retrieval_context": True,
    }
    state.update(overrides)
    return state


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The op with its collaborators replaced: what linking and the records
    stage return, and what execute_search receives."""
    calls: dict[str, Any] = {"linking": None, "scope": None, "search": []}
    monkeypatch.setattr(entity_search, "_named_entities_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(entity_search, "load_entity_access_context", AsyncMock(return_value=SimpleNamespace(org_id="org", row_scopes=())))

    class _Linker:
        def __init__(self, *args: object) -> None:
            pass

        async def link(self, mentions: list[Any], question: str) -> Linking:
            calls["question"] = question
            if isinstance(calls["linking"], BaseException):
                raise calls["linking"]
            return calls["linking"]

    async def _records(graph: object, context: object, results: list[Any], *, match_all: bool) -> RecordScope:
        calls["match_all"] = match_all
        return calls["scope"]

    async def _search(state: dict[str, Any], query: str, **kwargs: Any) -> str:  # noqa: ANN401
        calls["search"].append((query, kwargs))
        state.setdefault("virtual_record_id_to_result", {}).update(calls.get("shown", {}))
        return kwargs["preamble"] + "PASSAGES"

    monkeypatch.setattr(entity_search, "EntityLinker", _Linker)
    monkeypatch.setattr(entity_search, "readable_records", _records)
    monkeypatch.setattr(entity_search, "execute_search", _search)
    return calls


@pytest.mark.asyncio
async def test_disabled_extraction_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(entity_search, "_named_entities_enabled", AsyncMock(return_value=False))
    assert await entity_search.execute_search_by_entities(_state(), mentions=[{"name": "Acme"}]) == (
        False, NAMED_ENTITIES_DISABLED_MSG,
    )


@pytest.mark.asyncio
async def test_no_state_is_a_failed_call() -> None:
    assert await entity_search.execute_search_by_entities({}, mentions=["Acme"]) == (False, LOOKUP_FAILED_MSG)


@pytest.mark.asyncio
@pytest.mark.parametrize("mentions", [[{"kind": "person"}], [], None, "", [{"name": 7}], [{"name": "Q3", "kind": "date"}]])
async def test_no_usable_name_explains_the_argument(wired: dict[str, Any], mentions: Any) -> None:  # noqa: ANN401
    ok, message = await entity_search.execute_search_by_entities(_state(), mentions=mentions)
    assert not ok and 'mentions=[{"name"' in message
    assert wired["search"] == []


@pytest.mark.asyncio
async def test_linked_names_are_searched_inside_their_records(wired: dict[str, Any]) -> None:
    acme, globex = _candidate("e-acme", "Acme"), _candidate("e-globex", "Globex")
    wired["linking"] = _linking(
        _result("Acme", LinkStatus.MATCHED, accepted=[acme]),
        _result("Globex", LinkStatus.CHECKED, accepted=[globex], rejected=[_candidate("e-gx", "Globex Labs")]),
    )
    wired["scope"] = RecordScope(
        per_name=[NameRecords("Acme", ["r1", "r2"], True), NameRecords("Globex", ["r2"], False)],
        record_ids=["r2"], virtual_record_ids=["v2"], together={"r2": (NEARBY, 7)}, record_names={"r2": "Plan"},
    )
    wired["shown"] = {"v2": {"id": "r2", "record_name": "Plan"}, "v-other": {"id": "rx", "record_name": "X"}}
    state = _state(virtual_record_id_to_result={"v-other": {"id": "rx", "record_name": "X"}})

    result = await entity_search.execute_search_by_entities(
        state, mentions=[{"name": "Acme", "kind": "organization"}, {"name": "Globex", "kind": "organization"}],
        match="all",
    )

    ((query, kwargs),) = wired["search"]
    assert query == "Acme Globex" and kwargs["scope_virtual_record_ids"] == ["v2"]
    assert wired["match_all"] is True and wired["question"] == "the question"
    assert result.endswith("PASSAGES")
    assert '- "Acme" (organization): Acme (organization); exact match; mentioned in 2 readable records.' in result
    assert "Globex (organization); checked; mentioned in 1+ readable records. Not the same: Globex Labs" in result
    assert 'Plan (block 7): knowledgegraph__fetch_record(record_ids=["r2"], start_block=5)' in result
    assert "r2" in state["known_record_ids"]
    provenance = state[RETRIEVED_BY_KEY]
    assert provenance["v2"].layers == ["entity"] and "v-other" not in provenance
    assert entity_search.COVERAGE_NOTE not in result, "every name was found"


@pytest.mark.asyncio
@pytest.mark.parametrize("unlinked", [
    _result("Initech", LinkStatus.NOT_FOUND),
    _result("Initech", LinkStatus.UNVERIFIED, unchecked=[_candidate("e-i", "Initech Labs")]),
    _result("Initech", LinkStatus.AMBIGUOUS),
], ids=["not-found", "unverified", "ambiguous"])
async def test_all_with_a_name_that_did_not_link_searches_nothing(
    monkeypatch: pytest.MonkeyPatch, unlinked: LinkResult,
) -> None:
    """The real records stage: a name with no entity has no records, so no
    record mentions every name; the linked names keep their counts."""
    from app.modules.named_entities.linking import records as records_module
    from app.modules.retrieval.entity_permissions import EntityRecordPage

    async def _list(graph: object, context: object, *, entity_id: str, entity_type: str, limit: int) -> EntityRecordPage:
        return EntityRecordPage(records=[{"_key": f"r-{entity_id}", "virtualRecordId": f"v-{entity_id}"}], next_cursor=None)

    monkeypatch.setattr(records_module, "list_accessible_entity_records", _list)
    searches: list[Any] = []
    monkeypatch.setattr(entity_search, "_named_entities_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(entity_search, "load_entity_access_context", AsyncMock(return_value=SimpleNamespace(org_id="org", row_scopes=())))
    monkeypatch.setattr(entity_search, "execute_search", AsyncMock(side_effect=lambda *a, **k: searches.append(k)))
    linking = _linking(
        _result("Acme", LinkStatus.MATCHED, accepted=[_candidate("e1", "Acme")]),
        unlinked,
        _result("Globex", LinkStatus.MATCHED, accepted=[_candidate("e2", "Globex")]),
    )

    class _Linker:
        def __init__(self, *args: object) -> None:
            pass

        async def link(self, *args: object, **kwargs: object) -> Linking:
            return linking

    monkeypatch.setattr(entity_search, "EntityLinker", _Linker)
    ok, text = await entity_search.execute_search_by_entities(
        _state(), mentions=["Acme", "Initech", "Globex"], match="all",
    )
    assert ok and searches == []
    assert "Together:" not in text and "matched no readable entity" not in text
    assert "Acme (organization); exact match; mentioned in 1 readable record." in text
    assert text.endswith('No readable record mentions all of them; try match="any" or fewer names.')


@pytest.mark.asyncio
async def test_an_exact_first_name_says_which_longer_names_it_left_out(wired: dict[str, Any]) -> None:
    sam = _candidate("e-sam", "Sam", kind="person")
    (mention,) = parse_mentions([{"name": "Sam", "kind": "person"}]).mentions
    wired["linking"] = _linking(
        LinkResult(mention, LinkStatus.MATCHED, accepted=[sam], unchecked=[_candidate("e-lee", "Sam Lee", kind="person")]),
        LinkResult(mention, LinkStatus.MATCHED, accepted=[sam], more_longer_names=True),
    )
    wired["scope"] = RecordScope(per_name=[NameRecords("Sam", ["r1"], True)], record_ids=["r1"], virtual_record_ids=["v1"])
    text = await entity_search.execute_search_by_entities(_state(), mentions=[{"name": "Sam", "kind": "person"}, "Sam Q"])
    assert "Longer names that may be the same could not be checked and were not used: Sam Lee (person)" in text
    assert "Many more readable names start with it" in text


@pytest.mark.asyncio
async def test_a_query_is_passed_as_given(wired: dict[str, Any]) -> None:
    wired["linking"] = _linking(_result("Acme", LinkStatus.MATCHED, accepted=[_candidate("e", "Acme")]))
    wired["scope"] = RecordScope(per_name=[NameRecords("Acme", ["r1"], True)], record_ids=["r1"], virtual_record_ids=["v1"])
    await entity_search.execute_search_by_entities(_state(), mentions=["Acme"], query="  renewal terms ")
    assert wired["search"][0][0] == "renewal terms"
    assert wired["match_all"] is False


@pytest.mark.asyncio
async def test_match_all_with_one_name_is_any(wired: dict[str, Any]) -> None:
    wired["linking"] = _linking(_result("Acme", LinkStatus.MATCHED, accepted=[_candidate("e", "Acme")]))
    wired["scope"] = RecordScope(per_name=[NameRecords("Acme", ["r1"], True)], record_ids=["r1"], virtual_record_ids=["v1"])
    text = await entity_search.execute_search_by_entities(_state(), mentions=["Acme"], match="all")
    assert wired["match_all"] is False and "Together:" not in text


@pytest.mark.asyncio
async def test_nothing_readable_is_answered_without_a_search_and_says_coverage_is_partial(
    wired: dict[str, Any],
) -> None:
    lookalike = _candidate("e-lf", "Acme Financial")
    wired["linking"] = _linking(_result("Acme Robotics", LinkStatus.NOT_FOUND, rejected=[lookalike]))
    wired["scope"] = RecordScope(per_name=[])

    ok, text = await entity_search.execute_search_by_entities(
        _state(), mentions=[{"name": "Acme Robotics", "kind": "organization"}],
    )

    assert ok and wired["search"] == []
    assert "no readable entity by this name. Not the same: Acme Financial (organization)." in text
    assert f"Note: {entity_search.COVERAGE_NOTE}" in text, "older records may still name it"
    assert text.endswith("No readable record mentions these names as entities.")


@pytest.mark.asyncio
async def test_a_name_not_found_beside_found_ones_still_says_coverage_is_partial(wired: dict[str, Any]) -> None:
    wired["linking"] = _linking(
        _result("Acme", LinkStatus.MATCHED, accepted=[_candidate("e", "Acme")]),
        _result("Initech", LinkStatus.NOT_FOUND),
    )
    wired["scope"] = RecordScope(per_name=[NameRecords("Acme", ["r1"], True)], record_ids=["r1"], virtual_record_ids=["v1"])
    text = await entity_search.execute_search_by_entities(_state(), mentions=["Acme", "Initech"])
    assert entity_search.COVERAGE_NOTE in text and text.endswith("PASSAGES")


@pytest.mark.asyncio
async def test_missing_and_unreadable_read_the_same(wired: dict[str, Any]) -> None:
    """Candidates the user cannot read never reach the linking result, so a name
    whose entity exists but is unreadable renders as a name with no entity."""
    wired["linking"] = _linking(_result("Acme", LinkStatus.NOT_FOUND))
    wired["scope"] = RecordScope(per_name=[])
    first = await entity_search.execute_search_by_entities(_state(), mentions=[{"name": "Acme", "kind": "organization"}])
    second = await entity_search.execute_search_by_entities(_state(), mentions=[{"name": "Acme", "kind": "organization"}])
    assert first == second


@pytest.mark.asyncio
async def test_unchecked_ambiguous_and_timed_out_are_explained(wired: dict[str, Any]) -> None:
    wired["linking"] = _linking(
        _result("Globex", LinkStatus.UNVERIFIED, unchecked=[_candidate("e-n", "Globex Systems Inc")]),
        _result("Sam", LinkStatus.AMBIGUOUS, kind="person"),
        timed_out=True,
    )
    wired["scope"] = RecordScope(per_name=[])
    ok, text = await entity_search.execute_search_by_entities(
        _state(), mentions=[{"name": "Globex", "kind": "organization"}, {"name": "Sam", "kind": "person"}],
    )
    assert ok
    assert "could not be checked and were not used: Globex Systems Inc (organization)" in text
    assert '"Sam" (person): too many readable entities start with this name; give the full name.' in text
    assert "access checks were cut short" in text


@pytest.mark.asyncio
async def test_an_unknown_match_falls_back_to_any_with_a_note(wired: dict[str, Any]) -> None:
    wired["linking"] = _linking(_result("Acme", LinkStatus.MATCHED, accepted=[_candidate("e", "Acme")]))
    wired["scope"] = RecordScope(per_name=[NameRecords("Acme", ["r1"], True)], record_ids=["r1"], virtual_record_ids=["v1"])
    text = await entity_search.execute_search_by_entities(_state(), mentions=["Acme", "Globex"], match="both")
    assert wired["match_all"] is False and "match 'both' is not 'any' or 'all'" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [EntityAccessError("graph down"), RuntimeError("driver blew up")])
async def test_a_lookup_failure_is_an_error_not_an_empty_answer(wired: dict[str, Any], failure: Exception) -> None:
    wired["linking"] = failure
    assert await entity_search.execute_search_by_entities(_state(), mentions=["Acme"]) == (False, LOOKUP_FAILED_MSG)
    assert wired["search"] == []


@pytest.mark.asyncio
async def test_the_whole_lookup_has_a_deadline(wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(entity_search, "DEADLINE_SECONDS", 0.01)

    class _Slow:
        def __init__(self, *args: object) -> None:
            pass

        async def link(self, *args: object, **kwargs: object) -> None:
            await asyncio.sleep(1)

    monkeypatch.setattr(entity_search, "EntityLinker", _Slow)
    assert await entity_search.execute_search_by_entities(_state(), mentions=["Acme"]) == (False, entity_search.TOO_SLOW_MSG)


def test_together_line_without_nearby_records_counts_only() -> None:
    scope = RecordScope(per_name=[], record_ids=["r1"], virtual_record_ids=["v1"], together={"r1": (SAME_RECORD, -1)})
    linking = _linking(
        _result("Acme", LinkStatus.MATCHED, accepted=[_candidate("e1", "Acme")]),
        _result("Globex", LinkStatus.MATCHED, accepted=[_candidate("e2", "Globex")]),
    )
    text = entity_search.render_preamble(linking, scope, match_all=True, notes=[])
    assert "Together: 1 readable record(s) searched." in text


def test_json_safe_preamble() -> None:
    linking = _linking(_result('Acme "Corp"', LinkStatus.NOT_FOUND))
    text = entity_search.render_preamble(linking, RecordScope(per_name=[]), match_all=False, notes=["a note"])
    assert json.loads(json.dumps({"entities": text}))["entities"] == text
    assert text.startswith("Entities:\n") and "Note: a note" in text


class TestVerifierModel:
    """The name check's model: the ``query`` role at low effort, the chat's
    model when none can be built, and its usage on the request's ledger."""

    @pytest.mark.asyncio
    async def test_the_query_role_at_low_effort(self) -> None:
        role_llm = MagicMock(name="role")
        lookup = AsyncMock(return_value=(role_llm, {"configuration": {"model": "small-model"}}))
        with patch("app.utils.llm.get_llm_for_role", lookup):
            assert await entity_search.verifier_model(_state(config_service=MagicMock(), llm="chat")) == (
                role_llm, "small-model",
            )
        assert lookup.await_args.args[1] == "query" and lookup.await_args.kwargs == {"reasoning_effort": "low"}

    @pytest.mark.asyncio
    async def test_a_failed_or_stalled_lookup_falls_back_to_the_chat_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(entity_search, "VERIFIER_LLM_LOOKUP_SECONDS", 0.01)

        async def stalled(*args: object, **kwargs: object) -> None:
            await asyncio.sleep(1)

        chat = MagicMock(name="chat")
        for lookup in (AsyncMock(side_effect=RuntimeError("no roles")), stalled):
            with patch("app.utils.llm.get_llm_for_role", lookup):
                assert await entity_search.verifier_model(_state(config_service=MagicMock(), llm=chat)) == (chat, "")

    @pytest.mark.asyncio
    async def test_its_usage_is_reported_under_its_own_source(self) -> None:
        ledger = UsageLedger()
        reply = AIMessage(content='{"1": [1]}', usage_metadata={"input_tokens": 90, "output_tokens": 4, "total_tokens": 94})

        class _Llm:
            async def ainvoke(self, messages: list[Any], config: Any = None) -> AIMessage:  # noqa: ANN401
                return reply

        from app.modules.named_entities.linking.verifier import (
            OfferedCandidate,
            VerifyItem,
        )

        with patch.object(entity_search, "verifier_model", AsyncMock(return_value=(_Llm(), "m"))):
            verifier = entity_search._verifier(_state(usage_ledger=ledger))
            decided = await verifier.verify("q", [VerifyItem("Acme", None, [OfferedCandidate("ACME", "organization", "1 record")])])
        assert decided == {0: [0]}
        assert ledger.entries == [(ENTITY_LINKING_SOURCE, TokenUsage(input_tokens=90, output_tokens=4))]

    @pytest.mark.asyncio
    async def test_nothing_is_recorded_unless_the_request_opted_in(self) -> None:
        verifier = entity_search._verifier(_state(usage_ledger=None))
        assert verifier._on_llm_usage is None


class TestPermissions:
    """The real linking path over a graph where one record names Contoso: a
    user who cannot read it, or a user of another org, learns nothing of it."""

    ORG = "org-1"
    RECORD = {"_key": "rec-msa", "connectorId": "kb-1", "virtualRecordId": "v-msa", "recordName": "Contoso MSA"}

    def _graph(self) -> MagicMock:
        from app.modules.named_entities.keys import named_entity_key
        from tests.unit.modules.retrieval.entity_access_fakes import entity_graph

        node_id = named_entity_key(self.ORG, "organization", "name:organization:contoso")
        nodes = {node_id: {"id": node_id, "kind": "organization", "name": "Contoso", "normKey": "name:organization:contoso"}}
        graph = entity_graph(
            lambda refs, org_id, **kwargs: {ref["id"]: [dict(self.RECORD)] for ref in refs if ref["id"] in nodes},
            app_level=("kb-1",),
        )
        graph.get_named_entities_by_ids = AsyncMock(
            side_effect=lambda org, ids: [nodes[i] for i in ids if i in nodes and org == self.ORG],
        )
        graph.find_named_entities_by_key_prefix = AsyncMock(return_value=[])
        graph.get_named_entity_mentions_in_records = AsyncMock(side_effect=lambda org, entity_ids, record_ids: [
            {"recordId": r, "entityId": e, "extractedNames": ["Contoso"], "blockIndexes": [0], "mentionCount": 1}
            for e in entity_ids for r in record_ids if e in nodes and r == self.RECORD["_key"]
        ])
        return graph

    def _context(self, *, org: str, apps: frozenset[str]) -> Any:  # noqa: ANN401
        from app.modules.retrieval.entity_permissions import EntityAccessContext

        return EntityAccessContext(
            org_id=org, user_key="u", app_level_app_ids=apps, record_level_app_ids=frozenset(),
            record_group_ids=frozenset(), app_names={"kb-1": "Legal KB", "kb-2": "Sales KB"},
        )

    async def _run(
        self, monkeypatch: pytest.MonkeyPatch, context: Any, match: str = "any",  # noqa: ANN401
    ) -> tuple[Any, dict[str, Any], list[Any]]:
        searches: list[Any] = []

        async def _search(state: dict[str, Any], query: str, **kwargs: Any) -> str:  # noqa: ANN401
            searches.append(kwargs["scope_virtual_record_ids"])
            return kwargs["preamble"] + "PASSAGES"

        monkeypatch.setattr(entity_search, "_named_entities_enabled", AsyncMock(return_value=True))
        monkeypatch.setattr(entity_search, "load_entity_access_context", AsyncMock(return_value=context))
        monkeypatch.setattr(entity_search, "execute_search", _search)
        state = _state(org_id=context.org_id, graph_provider=self._graph())
        result = await entity_search.execute_search_by_entities(
            state, mentions=[{"name": "Contoso", "kind": "organization"}, {"name": "Initech"}], match=match,
        )
        return result, state, searches

    @pytest.mark.asyncio
    async def test_a_reader_gets_the_record_searched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result, _state_after, searches = await self._run(monkeypatch, self._context(org=self.ORG, apps=frozenset({"kb-1"})))
        assert searches == [["v-msa"]]
        assert '"Contoso" (organization): Contoso (organization); exact match; mentioned in 1 readable record.' in result

    @pytest.mark.asyncio
    async def test_a_reader_asking_for_all_with_a_name_nobody_has_gets_no_search(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (ok, text), _state_after, searches = await self._run(
            monkeypatch, self._context(org=self.ORG, apps=frozenset({"kb-1"})), match="all",
        )
        assert ok and searches == []
        assert "mentioned in 1 readable record" in text and "No readable record mentions all of them" in text

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("org", "apps"), [
        ("org-1", frozenset({"kb-2"})),
        ("org-1", frozenset()),
        ("org-2", frozenset({"kb-1"})),
    ], ids=["another-user", "no-access", "another-org"])
    async def test_anyone_else_learns_nothing_of_it(
        self, monkeypatch: pytest.MonkeyPatch, org: str, apps: frozenset[str],
    ) -> None:
        (ok, text), _state_after, _searches = await self._run(monkeypatch, self._context(org=org, apps=apps))
        assert ok is True
        assert '"Contoso" (organization): no readable entity by this name.' in text
        for leaked in ("Contoso MSA", "rec-msa", "v-msa", "Legal KB", "Not the same"):
            assert leaked not in text

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("org", "apps"), [("org-1", frozenset({"kb-2"})), ("org-2", frozenset({"kb-1"}))])
    async def test_nothing_reaches_the_search_the_record_memory_or_the_trace(
        self, monkeypatch: pytest.MonkeyPatch, org: str, apps: frozenset[str],
    ) -> None:
        _result, state, searches = await self._run(monkeypatch, self._context(org=org, apps=apps))
        assert searches == []
        assert "rec-msa" not in (state.get("known_record_ids") or set())
        assert not state.get(RETRIEVED_BY_KEY)


def _person(entity_id: str, name: str, source: CandidateSource = CandidateSource.PREFIX, records: int = 2) -> Candidate:
    return Candidate(entity_id, "person", name, source, tuple({"_key": f"r{i}"} for i in range(records)), False)


def _render(*results: LinkResult, scope: RecordScope | None = None) -> str:
    names = {c.entity_id: c.name for r in results for c in [*r.accepted, *r.rejected, *r.unchecked, *r.namesakes, *r.left_out]}
    linking = Linking(results=list(results), timed_out=False, verifier_called=False, names=names)
    scope = scope or RecordScope(per_name=[NameRecords("Lucas", ["r1"], True)], record_ids=["r1"], virtual_record_ids=["v1"])
    return entity_search.render_preamble(linking, scope, match_all=False, notes=[])


def test_a_first_name_lists_the_people_who_share_it_and_never_says_not_the_same() -> None:
    (mention,) = parse_mentions([{"name": "Lucas", "kind": "person"}]).mentions
    people = [_person("e1", "Lucas Adeyemi", records=12), _person("e2", "Lucas Bergstrom", records=1)]
    text = _render(LinkResult(mention, LinkStatus.MATCHED, accepted=[_person("e0", "Lucas", CandidateSource.EXACT)], namesakes=people))
    assert 'Several people are named "Lucas": Lucas Adeyemi (person, 12 records), Lucas Bergstrom (person, 1 record).' in text
    assert "say which one is meant" in text and "Not the same" not in text


def test_namesakes_without_an_exact_node_ask_for_the_full_name_without_the_coverage_note() -> None:
    (mention,) = parse_mentions([{"name": "Lucas", "kind": "person"}]).mentions
    people = [_person("e1", "Lucas Adeyemi"), _person("e2", "Lucas Nakamura")]
    text = _render(LinkResult(mention, LinkStatus.AMBIGUOUS, namesakes=people))
    assert '- "Lucas" (person): several readable people have this first name: Lucas Adeyemi (person, 2 records)' in text
    assert entity_search.COVERAGE_NOTE not in text and entity_search.COUNT_NOTE in text


def test_a_longer_name_the_check_left_out_beside_an_exact_match_is_not_called_someone_else() -> None:
    (mention,) = parse_mentions([{"name": "Sam", "kind": "person"}]).mentions
    text = _render(LinkResult(
        mention, LinkStatus.MATCHED, accepted=[_person("e0", "Sam", CandidateSource.EXACT)],
        left_out=[_person("e1", "Sam Lee")], rejected=[_person("e2", "Sam Lee Partners", CandidateSource.DENSE)],
    ))
    assert "Other names that start with it, not included: Sam Lee (person); call again with the full name" in text
    assert "Not the same: Sam Lee Partners (person)." in text


def test_a_printed_count_is_marked_as_a_minimum() -> None:
    text = _render(_result("Acme", LinkStatus.MATCHED, accepted=[_candidate("e", "Acme")]))
    assert f"Note: {entity_search.COUNT_NOTE}" in text


def test_a_namesake_only_answer_neither_says_absent_nor_ends_with_nothing_found() -> None:
    (mention,) = parse_mentions([{"name": "Lucas", "kind": "person"}]).mentions
    people = [_person("e1", "Lucas Adeyemi"), _person("e2", "Lucas Nakamura")]
    linking = Linking(
        results=[LinkResult(mention, LinkStatus.AMBIGUOUS, namesakes=people, rejected=[_person("e9", "Lucas Arts")])],
        timed_out=False, verifier_called=True, names={c.entity_id: c.name for c in [*people]},
    )
    text = entity_search.render_preamble(linking, RecordScope(per_name=[]), match_all=False, notes=[])
    assert entity_search.COVERAGE_NOTE not in text and entity_search.COUNT_NOTE in text
    assert "Not the same: Lucas Arts (person)." in text
    assert "full name of the person meant" in entity_search._nothing_to_search(linking, match_all=False)



def test_a_resolved_name_gives_the_call_that_lists_every_record() -> None:
    text = _render(_result("Acme", LinkStatus.MATCHED, accepted=[_candidate("e-acme", "Acme")]))
    assert 'Every record: knowledgegraph__find_records_by_entity(entity_id="e-acme", entity_type="named_entity").' in text
