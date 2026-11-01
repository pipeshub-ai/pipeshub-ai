"""RECORD_RERANK_TOOL_SEARCHES: an agent's own searches are reranked like the prefetch, anchored on the user's
question: the reranker reads it beside the search query, and with the dense pool on its dense records join the pool
(their share kept free for them). Off, a tool search is what it was."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.modules.retrieval.record_rerank import (
    POINTS,
    RecordRerank,
    tool_search_record_rerank,
)
from app.modules.retrieval.record_rerank.dense_pool import (
    DENSE_POINTS_PER_RECORD,
    anchor_pool_records,
    fill_pool,
)
from app.modules.retrieval.record_rerank.settings import RecordRerankSettings
from app.services.vector_db.models import SearchResult
from tests.unit.agents.actions.knowledge_graph.test_outside_dates import (
    _THE_WEEK,
    _Index,
    _run,
    _state,
)
from tests.unit.modules.retrieval.test_record_cut_search import (
    _FakeStore,
    _graph,
    _service,
)
from tests.unit.modules.retrieval.test_record_rerank_search import (  # noqa: F401 - the autouse fixture
    _STRATEGY,
    _container_service,
    _isolated,
    _Scripted,
    _search,
    _shown_records,
)

if TYPE_CHECKING:
    import pytest

    from app.services.vector_db.models import HybridSearchRequest

TOOL, RERANK, POOL = "RECORD_RERANK_TOOL_SEARCHES", "RECORD_RERANK", "RECORD_RERANK_DENSE_POOL"
_QUESTION = "which crew resurfaced the north car park?"


class TestToolSearchRerank:
    def test_off_unless_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(RERANK, "true")
        monkeypatch.delenv(TOOL, raising=False)
        assert tool_search_record_rerank("q", _QUESTION) == {}

    def test_off_without_the_rerank_itself(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(RERANK, raising=False)
        monkeypatch.setenv(TOOL, "true")
        assert tool_search_record_rerank("q", _QUESTION) == {}

    def test_on_anchors_the_rerank_on_the_user_question(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(RERANK, "true")
        monkeypatch.setenv(TOOL, "true")
        rerank = tool_search_record_rerank('"car park" resurfacing', _QUESTION)["record_rerank"]
        assert rerank.anchor == _QUESTION
        assert rerank.question == f'{_QUESTION}\n(Search query: "car park" resurfacing)'

    def test_a_search_for_the_question_itself_has_no_anchor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(RERANK, "true")
        monkeypatch.setenv(TOOL, "true")
        for user_question in ("q", None, "", " q "):
            rerank = tool_search_record_rerank("q", user_question)["record_rerank"]
            assert (rerank.anchor, rerank.question) == ("", "q")


def _pt(record: str, score: float) -> SearchResult:
    return SearchResult(id=f"{record}#0", score=score, payload={"metadata": {"virtualRecordId": record}})


class TestFillPool:
    def test_the_query_leaves_the_anchor_share_free_and_the_anchor_follows(self) -> None:
        fused = [_pt("a", 0.9)]
        dense = [_pt(r, 0.8) for r in "bcdefghij"]
        anchor = [_pt(r, 0.7) for r in "wxyz"]
        items, added = fill_pool(fused, dense, anchor, POINTS, size=10)
        assert [p.id[0] for p in items] == list("abcdefwxyz")
        assert added == set("bcdefwxyz")
        scores = [p.score for p in items]
        assert scores == sorted(scores, reverse=True)

    def test_without_anchor_points_the_query_fills_the_pool(self) -> None:
        items, _ = fill_pool([_pt("a", 0.9)], [_pt(r, 0.8) for r in "bcdefghij"], [], POINTS, size=10)
        assert len(items) == 10

    def test_the_anchor_share(self) -> None:
        assert (anchor_pool_records(200), anchor_pool_records(500), anchor_pool_records(2)) == (80, 200, 1)


@dataclass
class _AnchorFake(_FakeStore):
    """The query's own requests never return r12; the anchor's dense request (its smaller limit) ranks it first."""

    anchor_limit: int = anchor_pool_records(200) * DENSE_POINTS_PER_RECORD

    def anchor(self, req: HybridSearchRequest) -> bool:
        return req.text_query is None and req.limit == self.anchor_limit

    async def query_nearest_points(self, collection_name: str, requests: list[HybridSearchRequest]) -> list:
        out = await super().query_nearest_points(collection_name, requests)
        for i, req in enumerate(requests):
            r12 = [p for p in out[i] if p.payload["metadata"]["virtualRecordId"] == "r12"]
            rest = [p for p in out[i] if p not in r12]
            out[i] = r12 + rest if self.anchor(req) else rest
        return out


def _rerank(anchor: str) -> RecordRerank:
    settings = RecordRerankSettings(strategy=_STRATEGY, dense_pool=200, pool=200)
    return RecordRerank(question="q", settings=settings, anchor=anchor)


class TestAnchorLeg:
    async def test_the_reranker_can_pick_a_record_only_the_question_finds(self) -> None:
        _Scripted.ranking = ["r12"]
        store = _AnchorFake()
        service = _service(store, _graph())
        response = await _search(service, record_rerank=_rerank(_QUESTION))
        (request,) = _Scripted.requests
        assert "r12" in [c.record_id for c in request.candidates]
        assert _shown_records(response)[0] == "r12"
        anchor = [r for r in store.requests if store.anchor(r)]
        assert anchor and all(r.filter == store.requests[0].filter for r in anchor)
        embedded = [call.args[0] for call in service.get_embedding_model_instance.return_value.aembed_query.await_args_list]
        assert _QUESTION in embedded

    async def test_no_anchor_sends_no_anchor_request(self) -> None:
        _Scripted.ranking = ["r12"]
        store = _AnchorFake()
        await _search(_service(store, _graph()), record_rerank=_rerank(""))
        assert not [r for r in store.requests if store.anchor(r)]
        assert "r12" not in [c.record_id for c in _Scripted.requests[0].candidates]

    async def test_a_failed_embedding_reranks_the_search_pool(self) -> None:
        _Scripted.ranking = ["r3"]
        store = _AnchorFake()
        service = _service(store, _graph())
        embedder = service.get_embedding_model_instance.return_value
        query_vector = embedder.aembed_query.return_value

        async def embed(text: str) -> list[float]:
            if text == _QUESTION:
                raise ConnectionError("embedding down")
            return query_vector

        embedder.aembed_query.side_effect = embed
        response = await _search(service, record_rerank=_rerank(_QUESTION))
        assert not [r for r in store.requests if store.anchor(r)]
        assert _shown_records(response)[0] == "r3"

    async def test_the_container_path_offers_the_question_records_the_access_check_admits(self) -> None:
        readers = {("alice", "org-1"): {"long", *(f"r{i}" for i in range(30))}}
        _Scripted.ranking = ["r12"]
        response = await _search(_container_service(_AnchorFake(), readers), limit=10, record_rerank=_rerank(_QUESTION))
        assert "r12" in [c.record_id for c in _Scripted.requests[0].candidates]
        assert _shown_records(response)[0] == "r12"


class TestTheSearchTool:
    async def test_off_the_tool_search_is_called_as_before(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(RERANK, "true")
        index = _Index(_THE_WEEK)
        await _run({**_state(index), "query": _QUESTION})
        assert "record_rerank" not in index.calls[0]

    async def test_on_the_tool_search_asks_for_the_rerank_anchored_on_the_user_question(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(RERANK, "true")
        monkeypatch.setenv(TOOL, "true")
        index = _Index(_THE_WEEK)
        await _run({**_state(index), "query": _QUESTION})
        rerank = index.calls[0]["record_rerank"]
        assert rerank.anchor == _QUESTION
        assert rerank.question.startswith(f"{_QUESTION}\n(Search query: ")
