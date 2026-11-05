"""RECORD_RERANK through `search_with_filters` and the prefetch, over the fake
vector store and graph of the record-cut tests (which honour permission
filters as the real backends do).

The reranked order must decide which records survive the record cut, the
reranker must see and return only records the user may read, and with the
switch off, or when the reranker fails, a search is exactly what it was.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.modules.retrieval.retrieval_service as mod
from app.agents.chat_modes.prefetch import prefetch_retrieval
from app.exceptions.fastapi_responses import Status
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.modules.retrieval.context.ordering import order_for_reading
from app.modules.retrieval.context.ranking import rank_units
from app.modules.retrieval.record_cut import FUSION_RECORD_CUT_ENV_VAR
from app.modules.retrieval.record_rerank import (
    RecordRerank,
    RecordReranker,
    RecordRerankError,
    RecordRerankSettings,
    RerankRequest,
    register_record_reranker,
)
from app.modules.retrieval.record_rerank import factory as factory_mod
from app.services.graph_db.interface.graph_db_provider import (
    AccessCheck,
    AccessibleContainers,
)
from app.services.vector_db.models import SearchResult
from tests.unit.modules.retrieval import test_narrow_access_fallback as narrow
from tests.unit.modules.retrieval.test_record_cut_search import (
    _READERS,
    ORG_1,
    _FakeStore,
    _graph,
    _service,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

_STRATEGY = "search_test_strategy"


class _Scripted(RecordReranker):
    """Returns ``ranking`` (or raises ``error``) and remembers each request."""

    ranking: list[str] = []
    error: Exception | None = None
    requests: list[RerankRequest] = []

    def __init__(self, _deps: object) -> None:
        pass

    async def rank(self, request: RerankRequest) -> list[str]:
        type(self).requests.append(request)
        if type(self).error is not None:
            raise type(self).error
        return list(type(self).ranking)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in (
        "HIGH_ACCURACY_MODE", "RECORD_RERANK", "RECORD_RERANK_STRATEGY", "RELEVANCE_ORDERED_CONTEXT",
        "SEARCH_NARROW_ACCESS_FALLBACK",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(FUSION_RECORD_CUT_ENV_VAR, "true")
    register_record_reranker(_STRATEGY)(_Scripted)
    _Scripted.ranking, _Scripted.error, _Scripted.requests = [], None, []
    mod._user_cache.clear()
    mod.narrow_access_memo.clear()
    with patch(
        "app.agents.actions.knowledge_graph.location.resolve_ancestor_locations",
        AsyncMock(return_value={}),
    ):
        yield
    mod._user_cache.clear()
    mod.narrow_access_memo.clear()
    factory_mod._REGISTRY.pop(_STRATEGY, None)


def _rerank(**settings: object) -> RecordRerank:
    return RecordRerank(question="q", settings=RecordRerankSettings(strategy=_STRATEGY, **settings))


def _container_service(store: _FakeStore, readers: dict[tuple[str, str], set[str]]) -> mod.RetrievalService:
    graph = _graph(readers)
    service = _service(store, graph)
    graph.get_accessible_containers = AsyncMock(return_value=AccessibleContainers(app_ids=frozenset({"app-1"})))
    return service


async def _search(service: mod.RetrievalService, user: str = "alice", **kwargs: object) -> dict:
    kwargs.setdefault("limit", 8)
    return await service.search_with_filters(["q"], user, ORG_1, **kwargs)


def _shown(response: dict) -> list[str]:
    return [hit["metadata"]["point_id"] for hit in response.get("searchResults", [])]


def _shown_records(response: dict) -> list[str]:
    return list(dict.fromkeys(hit["metadata"]["virtualRecordId"] for hit in response.get("searchResults", [])))


class TestOrderBeforeTheCut:
    async def test_reranked_records_survive_the_cut(self) -> None:
        """Without the rerank, r9-r11 sit past the 24 fused points' cut to 8."""
        baseline = await _search(_service(_FakeStore(), _graph()))
        assert _shown(baseline) == ["long#0", "long#1", "long#2", "long#3", "r0#0", "r1#0", "r2#0", "r3#0"]

        _Scripted.ranking = ["r10", "r11", "r9"]
        response = await _search(_service(_FakeStore(), _graph()), record_rerank=_rerank())

        assert _shown(response) == ["r10#0", "r11#0", "r9#0", "long#0", "r0#0", "r1#0", "r2#0", "r3#0"]
        scores = [hit["score"] for hit in response["searchResults"]]
        assert scores == sorted(scores, reverse=True)
        assert len(set(scores)) == len(scores)

    async def test_the_strategy_sees_titles_and_the_fused_order(self) -> None:
        _Scripted.ranking = ["r5"]
        await _search(_service(_FakeStore(), _graph()), record_rerank=_rerank())

        (request,) = _Scripted.requests
        assert request.question == "q"
        assert request.org_id == ORG_1
        assert request.top == 8
        assert [c.record_id for c in request.candidates] == ["long", *(f"r{i}" for i in range(12))]
        assert request.candidates[1].title == "r0.txt"
        assert request.candidates[1].text == "r0 block 0"

    async def test_the_fused_first_record_is_kept_in_the_top_by_default(self) -> None:
        _Scripted.ranking = [f"r{i}" for i in range(11, 3, -1)]
        response = await _search(_service(_FakeStore(), _graph()), limit=12, record_rerank=_rerank())
        assert _shown_records(response)[:8] == ["r11", "r10", "r9", "r8", "r7", "r6", "r5", "long"]

    async def test_matched_summary_points_and_stored_summaries_are_the_candidate_text(self) -> None:
        store = _FakeStore()
        store.points.insert(0, SearchResult(
            id="r1#summary", score=2.0,
            payload={"page_content": "R1 is the plan", "metadata": {
                "virtualRecordId": "r1", "orgId": ORG_1, "blockType": "record_summary", "isRecordSummary": True,
            }, "connectorIds": ["app-1"]},
        ))
        service = _service(store, _graph())
        service._record_summaries = AsyncMock(return_value={"r2": "R2 summary"})
        _Scripted.ranking = ["r2"]
        await _search(service, record_rerank=_rerank())

        texts = {c.record_id: c.text for c in _Scripted.requests[0].candidates}
        assert texts["r1"] == "R1 is the plan"
        assert texts["r2"] == "R2 summary"
        assert texts["r3"] == "r3 block 0"
        asked = service._record_summaries.await_args.args[2]
        assert "r1" not in asked and "r2" in asked


class TestDownstreamFollowsTheReranking:
    async def test_context_ranking_and_reading_order(self) -> None:
        _Scripted.ranking = ["r3", "r2"]
        response = await _search(_service(_FakeStore(), _graph()), record_rerank=_rerank())
        hits = response["searchResults"]
        units = [
            {"virtual_record_id": h["metadata"]["virtualRecordId"], "block_index": i, "score": h["score"]}
            for i, h in enumerate(reversed(hits))
        ]
        ranked = rank_units(units)
        expected = ["r3", "r2", "long", "r0", "r1", "r4", "r5"]
        assert list(dict.fromkeys(u["virtual_record_id"] for u in ranked)) == expected
        reading = order_for_reading(ranked, by_relevance=True)
        assert list(dict.fromkeys(u["virtual_record_id"] for u in reading)) == expected


class TestPermissions:
    async def test_record_id_path_offers_only_readable_records(self) -> None:
        _Scripted.ranking = ["elsewhere", "long", "r1"]
        response = await _search(_service(_FakeStore(), _graph()), user="bob", record_rerank=_rerank())

        offered = {c.record_id for c in _Scripted.requests[0].candidates}
        assert offered <= _READERS[("bob", ORG_1)]
        assert set(_shown_records(response)) <= _READERS[("bob", ORG_1)]
        assert _shown_records(response)[0] == "r1"

    async def test_record_id_path_never_offers_a_record_the_access_check_denies(self) -> None:
        """The enumeration lists r2, as it lists a RESTRICTED page to every
        member of its space; the access check denies it, so the strategy
        never reads it and the search never shows it."""
        graph = _graph()
        check = graph.check_access.side_effect

        async def deny_r2(user_key: str, org_id: str, **kwargs: Any) -> AccessCheck:  # noqa: ANN401
            kwargs["virtual_record_ids"] = [v for v in kwargs.get("virtual_record_ids", ()) if v != "r2"]
            kwargs["node_ids"] = [n for n in kwargs.get("node_ids", ()) if n != "rec-r2"]
            return await check(user_key, org_id, **kwargs)

        graph.check_access.side_effect = deny_r2
        _Scripted.ranking = ["r2", "r3"]
        response = await _search(_service(_FakeStore(), graph), record_rerank=_rerank())

        assert "r2" not in {c.record_id for c in _Scripted.requests[0].candidates}
        assert "r2" not in _shown_records(response)
        assert _shown_records(response)[0] == "r3"

    async def test_container_path_reranks_after_verification(self) -> None:
        readers = {("dave", ORG_1): {"secret", "long"}}
        _Scripted.ranking = ["elsewhere", "r0", "long"]
        service = _container_service(_FakeStore(), readers)
        response = await _search(service, user="dave", limit=10, record_rerank=_rerank())

        (request,) = _Scripted.requests
        assert {c.record_id for c in request.candidates} == {"secret", "long"}
        assert set(_shown_records(response)) <= readers[("dave", ORG_1)]
        assert _shown_records(response) == ["long", "secret"]
        assert {r["virtualRecordId"] for r in response["records"]} <= readers[("dave", ORG_1)]

    async def test_container_path_skips_the_rerank_when_verification_is_unavailable(self) -> None:
        """The caller discards that page and reports the outage, so no model call is made for it."""
        service = _container_service(_FakeStore(), {("dave", ORG_1): {"secret", "long"}})
        service.graph_provider.check_access = AsyncMock(side_effect=PermissionVerificationUnavailableError("down"))
        _Scripted.ranking = ["long"]
        response = await _search(service, user="dave", limit=10, record_rerank=_rerank())
        assert response["status"] == Status.PERMISSION_CHECK_UNAVAILABLE.value
        assert _Scripted.requests == []

    async def test_container_path_without_the_rerank_is_unchanged(self) -> None:
        readers = {("dave", ORG_1): {"secret", "long"}}
        response = await _search(_container_service(_FakeStore(), readers), user="dave", limit=10)
        assert _shown_records(response) == ["secret", "long"]

    async def test_a_narrow_user_is_reranked_once_on_the_record_id_page(self) -> None:
        """The container page that the record-id search replaces is not
        reranked; the replacement is, over records the user may read."""
        corpus = narrow._corpus(3, records=2000)
        readable = set(random.Random(3).sample(corpus.vrids, 20))
        access = narrow._Access(readable, set(readable))
        reference = await narrow._search(
            narrow._service(narrow._FakeStore(list(corpus.points)), narrow._graph(access), containers=False), 10,
        )
        mod.narrow_access_memo.clear()
        pick = _shown_records(reference)[-1]
        _Scripted.ranking = [pick]
        store = narrow._FakeStore(list(corpus.points))
        response = await narrow._search(
            narrow._service(store, narrow._graph(access)), 10, record_rerank=_rerank(),
        )

        assert store.used_record_ids() == [False, True]
        (request,) = _Scripted.requests
        assert {c.record_id for c in request.candidates} <= readable
        assert _shown_records(response)[0] == pick
        assert narrow._vrids(response) <= readable


class TestFallbackAndOff:
    @pytest.mark.parametrize("container_path", [False, True])
    @pytest.mark.parametrize("error", [RecordRerankError("malformed"), RuntimeError("down")])
    async def test_a_failing_strategy_leaves_the_search_exactly_as_it_was(self, container_path, error) -> None:
        def build() -> mod.RetrievalService:
            store = _FakeStore()
            return _container_service(store, _READERS) if container_path else _service(store, _graph())

        baseline = await _search(build(), limit=10)
        _Scripted.error = error
        response = await _search(build(), limit=10, record_rerank=_rerank())
        assert _Scripted.requests
        assert response == baseline

    async def test_a_ranking_equal_to_the_search_order_changes_nothing(self) -> None:
        baseline = await _search(_service(_FakeStore(), _graph()))
        _Scripted.ranking = ["long", "r0", "r1"]
        assert await _search(_service(_FakeStore(), _graph()), record_rerank=_rerank()) == baseline

    async def test_an_unknown_strategy_changes_nothing(self) -> None:
        baseline = await _search(_service(_FakeStore(), _graph()))
        rerank = RecordRerank(question="q", settings=RecordRerankSettings(strategy="not_registered"))
        assert await _search(_service(_FakeStore(), _graph()), record_rerank=rerank) == baseline

    async def test_off_a_search_never_builds_a_reranker(self, monkeypatch) -> None:
        monkeypatch.setenv("HIGH_ACCURACY_MODE", "true")
        with patch.object(mod, "create_record_reranker", side_effect=AssertionError("built")):
            baseline = await _search(_service(_FakeStore(), _graph()))
        assert _shown(baseline)


class _ScrollPage:
    def __init__(self, points: list[SearchResult]) -> None:
        self.points = points


class TestRecordSummaries:
    async def test_reads_the_summary_points_of_the_asked_records(self) -> None:
        store = _FakeStore()
        summary = SearchResult(
            id="s1", score=0.0,
            payload={"page_content": "  r1 in short ", "metadata": {"virtualRecordId": "r1"}},
        )
        store.scroll = AsyncMock(return_value=_ScrollPage([summary]))
        service = _service(store, _graph())

        assert await service._record_summaries(["records"], ORG_1, ["r1", "r2"]) == {"r1": "r1 in short"}
        flt = store.scroll.await_args.args[1]
        assert flt["must"] == {"orgId": ORG_1, "virtualRecordId": ["r1", "r2"], "blockType": "record_summary"}

    async def test_a_store_that_cannot_answer_yields_none_and_warns_once(self) -> None:
        store = _FakeStore()
        store.scroll = AsyncMock(side_effect=RuntimeError("unknown field"))
        service = _service(store, _graph())

        assert await service._record_summaries(["records"], ORG_1, ["r1"]) == {}
        assert await service._record_summaries(["records"], ORG_1, ["r1"]) == {}
        assert service.logger.warning.call_count == 1


async def _prefetch_kwargs(monkeypatch: pytest.MonkeyPatch, **env: str) -> dict[str, Any]:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    retrieval = MagicMock()
    retrieval.search_with_filters = AsyncMock(return_value={"status_code": 500, "message": "stop here"})
    await prefetch_retrieval(
        query="where is the depot roof quote?", org_id=ORG_1, user_id="alice", retrieval_service=retrieval,
        graph_provider=MagicMock(), blob_store=MagicMock(), filters=None, limit=None, is_multimodal_llm=False,
        previous_conversations=None, logger=MagicMock(),
    )
    return retrieval.search_with_filters.await_args.kwargs


class TestPrefetch:
    async def test_off_the_prefetch_search_is_called_as_before(self, monkeypatch) -> None:
        kwargs = await _prefetch_kwargs(monkeypatch, HIGH_ACCURACY_MODE="true")
        assert "record_rerank" not in kwargs

    async def test_on_the_prefetch_asks_for_the_rerank_of_its_question(self, monkeypatch) -> None:
        kwargs = await _prefetch_kwargs(monkeypatch, RECORD_RERANK="true", RECORD_RERANK_STRATEGY=_STRATEGY)
        assert kwargs["record_rerank"] == RecordRerank(
            question="where is the depot roof quote?", settings=RecordRerankSettings(strategy=_STRATEGY),
        )
