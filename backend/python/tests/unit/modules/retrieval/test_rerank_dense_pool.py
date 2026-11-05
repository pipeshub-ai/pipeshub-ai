"""RECORD_RERANK_DENSE_POOL: the rerank pool is filled past the fused list with the records a dense-only request
ranks, so the reranker can pick a record the fused search left out. A record it does not pick is never shown, and
with the switch off a search is what it was."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.modules.retrieval.record_cut import FUSION_RECORD_CUT_ENV_VAR
from app.modules.retrieval.record_rerank import POINTS, record_rerank_settings
from app.modules.retrieval.record_rerank.dense_pool import (
    dense_pool_size,
    with_dense_records,
    without_unpicked,
)
from app.services.vector_db.models import HybridSearchRequest, SearchResult
from tests.unit.modules.retrieval.test_record_cut_search import (
    _FakeStore,
    _graph,
    _service,
)
from tests.unit.modules.retrieval.test_record_rerank_search import (  # noqa: F401 - the autouse fixture
    _container_service,
    _isolated,
    _rerank,
    _Scripted,
    _search,
    _shown_records,
)

if TYPE_CHECKING:
    import pytest

FLAG, SIZE = "RECORD_RERANK_DENSE_POOL", "RECORD_RERANK_DENSE_POOL_SIZE"


def _pt(record: str, n: int, score: float) -> SearchResult:
    return SearchResult(id=f"{record}#{n}", score=score, payload={"metadata": {"virtualRecordId": record}})


class TestSettings:
    def test_off_unless_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(FLAG, raising=False)
        assert dense_pool_size() == 0
        assert record_rerank_settings().dense_pool == 0
        monkeypatch.setenv(FLAG, "yes please")
        assert dense_pool_size() == 0

    def test_on_fills_to_200_and_raises_the_pool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(FLAG, "true")
        monkeypatch.delenv(SIZE, raising=False)
        settings = record_rerank_settings()
        assert (settings.dense_pool, settings.pool) == (200, 200)
        monkeypatch.setenv(SIZE, "150")
        assert record_rerank_settings().pool == 150
        monkeypatch.setenv(SIZE, "500")
        assert (record_rerank_settings().dense_pool, record_rerank_settings().pool) == (500, 500)
        monkeypatch.setenv(SIZE, "5000")
        assert dense_pool_size() == 500


class TestWithDenseRecords:
    def test_appends_only_new_records_below_every_fused_score(self) -> None:
        fused = [_pt("a", 0, 0.03), _pt("b", 0, 0.02)]
        dense = [_pt("b", 1, 0.9), _pt("c", 0, 0.85), _pt("c", 1, 0.8), _pt("d", 0, 0.7)]
        out, added = with_dense_records(fused, dense, POINTS, size=10)
        assert [p.id for p in out] == ["a#0", "b#0", "c#0", "c#1", "d#0"]
        assert added == {"c", "d"}
        assert out[:2] == fused
        scores = [p.score for p in out]
        assert scores == sorted(scores, reverse=True) and min(scores) > 0 and len(set(scores)) == len(scores)

    def test_stops_at_the_pool_size(self) -> None:
        fused = [_pt("a", 0, 0.03)]
        dense = [_pt(r, 0, 0.9) for r in "bcdef"]
        out, added = with_dense_records(fused, dense, POINTS, size=3)
        assert [p.id for p in out] == ["a#0", "b#0", "c#0"]
        assert added == {"b", "c"}

    def test_nothing_new_returns_the_fused_list(self) -> None:
        fused = [_pt("a", 0, 0.03)]
        assert with_dense_records(fused, [_pt("a", 1, 0.9)], POINTS, size=5) == (fused, set())


class TestWithoutUnpicked:
    items = [_pt("a", 0, 0.9), _pt("c", 0, 0.8), _pt("b", 0, 0.7), _pt("d", 0, 0.6)]

    def test_keeps_only_the_added_records_the_reranker_placed(self) -> None:
        out = without_unpicked(self.items, POINTS, {"c", "d"}, {"a", "c"})
        assert [p.id for p in out] == ["a#0", "c#0", "b#0"]

    def test_nothing_placed_drops_every_added_record(self) -> None:
        assert [p.id for p in without_unpicked(self.items, POINTS, {"c", "d"}, ())] == ["a#0", "b#0"]

    def test_nothing_added_keeps_everything(self) -> None:
        assert without_unpicked(self.items, POINTS, set(), ()) == self.items


@dataclass
class _DenseFake(_FakeStore):
    """The fused (hybrid) requests never return r11; the dense-only pool request ranks it first."""

    @staticmethod
    def dense(req: HybridSearchRequest) -> bool:
        return req.text_query is None and req.sparse_query is None

    async def query_nearest_points(self, collection_name: str, requests: list[HybridSearchRequest]) -> list:
        out = await super().query_nearest_points(collection_name, requests)
        for i, req in enumerate(requests):
            r11 = [p for p in out[i] if p.payload["metadata"]["virtualRecordId"] == "r11"]
            rest = [p for p in out[i] if p not in r11]
            out[i] = r11 + rest if self.dense(req) else rest
        return out


@dataclass
class _PartialDenseFake(_DenseFake):
    """The dense-only request is answered from a partial search."""

    async def query_nearest_points(self, collection_name: str, requests: list[HybridSearchRequest]) -> list:
        out = await super().query_nearest_points(collection_name, requests)
        return [
            [replace(p, degraded="timeout") for p in batch] if self.dense(req) else batch
            for batch, req in zip(out, requests)
        ]


def _pooled(**settings: object) -> object:
    return _rerank(dense_pool=200, pool=200, **settings)


class TestRecordIdPath:
    async def test_the_reranker_can_pick_a_record_only_the_dense_leg_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _Scripted.ranking = ["r11"]
        await _search(_service(_DenseFake(), _graph()), record_rerank=_rerank())
        fused_only = [c.record_id for c in _Scripted.requests.pop().candidates]
        store = _DenseFake()
        response = await _search(_service(store, _graph()), record_rerank=_pooled())
        (request,) = _Scripted.requests
        candidates = [c.record_id for c in request.candidates]
        assert candidates[:len(fused_only)] == fused_only
        assert candidates[len(fused_only)] == "r11" and candidates.count("r11") == 1
        assert _shown_records(response)[0] == "r11"
        dense = [r for r in store.requests if store.dense(r)]
        assert dense and all(r.filter == store.requests[0].filter for r in dense)
        assert dense[0].limit == 600

    async def test_no_dense_pool_sends_no_dense_request_and_never_offers_it(self) -> None:
        store = _DenseFake()
        _Scripted.ranking = ["r11"]
        response = await _search(_service(store, _graph()), record_rerank=_rerank())
        assert not [r for r in store.requests if store.dense(r)]
        assert "r11" not in [c.record_id for c in _Scripted.requests[0].candidates]
        assert "r11" not in _shown_records(response)

    async def test_an_unpicked_dense_record_is_never_shown_even_on_a_short_page(self) -> None:
        """Bob reads three records; the record cut would fill a short page
        with any record it holds, so an unpicked dense record must be gone
        before it."""
        readers = {("bob", "org-1"): {"r0", "r1", "r11"}}
        _Scripted.ranking = ["r1"]
        response = await _search(_service(_DenseFake(), _graph(readers)), user="bob", record_rerank=_pooled())
        assert "r11" in [c.record_id for c in _Scripted.requests[0].candidates]
        assert _shown_records(response) == ["r1", "r0"]

    async def test_a_failed_rerank_shows_no_dense_record(self) -> None:
        baseline = await _search(_service(_DenseFake(), _graph()))
        _Scripted.error = RuntimeError("down")
        response = await _search(_service(_DenseFake(), _graph()), record_rerank=_pooled())
        assert response == baseline

    async def test_without_the_record_cut_a_picked_record_does_not_lengthen_the_page(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(FUSION_RECORD_CUT_ENV_VAR, "false")
        baseline = await _search(_service(_DenseFake(), _graph()))
        _Scripted.ranking = ["r11"]
        response = await _search(_service(_DenseFake(), _graph()), record_rerank=_pooled())
        assert len(response["searchResults"]) == len(baseline["searchResults"]) == 8
        assert _shown_records(response)[0] == "r11"

    async def test_a_failed_dense_request_reranks_the_fused_list(self) -> None:
        store = _DenseFake()
        plain = store.query_nearest_points

        async def failing_dense(collection_name: str, requests: list[HybridSearchRequest]) -> list:
            if any(store.dense(r) for r in requests):
                raise ConnectionError("dense down")
            return await plain(collection_name, requests)

        store.query_nearest_points = failing_dense
        _Scripted.ranking = ["r3"]
        response = await _search(_service(store, _graph()), record_rerank=_pooled())
        assert "r11" not in [c.record_id for c in _Scripted.requests[0].candidates]
        assert _shown_records(response)[0] == "r3"


class TestContainerPath:
    readers = {("alice", "org-1"): {"long", *(f"r{i}" for i in range(30))} - {"r12"}}

    async def test_the_reranker_can_pick_a_dense_record_the_access_check_admits(self) -> None:
        _Scripted.ranking = ["r11"]
        service = _container_service(_DenseFake(), self.readers)
        response = await _search(service, limit=10, record_rerank=_pooled())
        (request,) = _Scripted.requests
        assert "r11" in [c.record_id for c in request.candidates]
        assert _shown_records(response)[0] == "r11"
        assert "r11" in {r["virtualRecordId"] for r in response["records"]}

    async def test_a_dense_record_the_access_check_denies_is_never_offered(self) -> None:
        readers = {("alice", "org-1"): self.readers[("alice", "org-1")] - {"r11"}}
        _Scripted.ranking = ["r11", "r3"]
        response = await _search(_container_service(_DenseFake(), readers), limit=10, record_rerank=_pooled())
        assert "r11" not in [c.record_id for c in _Scripted.requests[0].candidates]
        assert "r11" not in _shown_records(response)

    async def test_an_unpicked_dense_record_is_dropped(self) -> None:
        _Scripted.ranking = ["r3"]
        response = await _search(_container_service(_DenseFake(), self.readers), limit=10, record_rerank=_pooled())
        assert "r11" in [c.record_id for c in _Scripted.requests[0].candidates]
        assert "r11" not in _shown_records(response)
        assert _shown_records(response)[0] == "r3"

    async def test_no_dense_request_when_verification_is_unavailable(self) -> None:
        store = _DenseFake()
        service = _container_service(store, self.readers)
        service.graph_provider.check_access = AsyncMock(side_effect=PermissionVerificationUnavailableError("down"))
        await _search(service, limit=10, record_rerank=_pooled())
        assert not [r for r in store.requests if store.dense(r)]
        assert _Scripted.requests == []

    async def test_a_failed_access_check_reranks_the_admitted_hits(self) -> None:
        service = _container_service(_DenseFake(), self.readers)
        check = service.graph_provider.check_access.side_effect
        calls = 0

        async def second_call_fails(*args: object, **kwargs: object) -> object:
            nonlocal calls
            calls += 1
            if calls > 1:
                raise ConnectionError("graph down")
            return await check(*args, **kwargs)

        service.graph_provider.check_access = AsyncMock(side_effect=second_call_fails)
        _Scripted.ranking = ["r3"]
        response = await _search(service, limit=10, record_rerank=_pooled())
        assert "r11" not in [c.record_id for c in _Scripted.requests[0].candidates]
        assert _shown_records(response)[0] == "r3"


class TestPartialDenseSearch:
    async def test_a_partial_dense_search_marks_the_response(self) -> None:
        _Scripted.ranking = ["r11"]
        response = await _search(_service(_PartialDenseFake(), _graph()), record_rerank=_pooled())
        assert response["searchDegraded"] is True

    async def test_the_container_path_marks_it_too(self) -> None:
        _Scripted.ranking = ["r11"]
        response = await _search(
            _container_service(_PartialDenseFake(), TestContainerPath.readers), limit=10, record_rerank=_pooled(),
        )
        assert response["searchDegraded"] is True

    async def test_without_the_dense_pool_nothing_is_marked(self) -> None:
        _Scripted.ranking = ["r3"]
        response = await _search(_service(_PartialDenseFake(), _graph()), record_rerank=_rerank())
        assert "searchDegraded" not in response
