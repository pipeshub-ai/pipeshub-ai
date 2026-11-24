"""Qdrant dense-leg search params, tie-stable ordering, and the build-time ef_construct default."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from qdrant_client.http.models import SearchParams

pytest.importorskip("qdrant_client", reason="qdrant_client not installed")

from app.services.vector_db.models import (
    CollectionConfig,
    HNSWConfig,
    HybridSearchRequest,
    SparseVector,
)
from app.services.vector_db.qdrant import qdrant as qdrant_module
from app.services.vector_db.qdrant.config import QdrantConfig
from app.services.vector_db.qdrant.qdrant import QdrantService
from app.services.vector_db.qdrant.utils import (
    DEFAULT_HNSW_EF,
    DEFAULT_OVERSAMPLING,
    QDRANT_HNSW_EF_ENV,
    QDRANT_OVERSAMPLING_ENV,
    TIE_MARGIN,
    QdrantUtils,
    dense_search_params,
)

pytestmark = pytest.mark.skipif(
    isinstance(__import__("sys").modules.get("qdrant_client"), MagicMock),
    reason="qdrant_client is a test stub",
)


def _params(monkeypatch, **env) -> SearchParams:
    for name in (QDRANT_HNSW_EF_ENV, QDRANT_OVERSAMPLING_ENV):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return dense_search_params()


def test_defaults(monkeypatch) -> None:
    p = _params(monkeypatch)
    assert p.hnsw_ef == DEFAULT_HNSW_EF
    assert p.quantization.rescore is True
    assert p.quantization.oversampling == DEFAULT_OVERSAMPLING


def test_env_overrides(monkeypatch) -> None:
    p = _params(monkeypatch, **{QDRANT_HNSW_EF_ENV: "128", QDRANT_OVERSAMPLING_ENV: "2"})
    assert (p.hnsw_ef, p.quantization.oversampling) == (128, 2.0)


def test_ef_zero_leaves_the_collection_default(monkeypatch) -> None:
    assert _params(monkeypatch, **{QDRANT_HNSW_EF_ENV: "0"}).hnsw_ef is None


@pytest.mark.parametrize("bad", ["fast", "-5", "0.5", "", "nan", "inf", "-inf", "99999"])
def test_bad_values_fall_back(monkeypatch, bad) -> None:
    p = _params(monkeypatch, **{QDRANT_HNSW_EF_ENV: bad, QDRANT_OVERSAMPLING_ENV: bad})
    assert p.hnsw_ef in (DEFAULT_HNSW_EF, None) and p.quantization.oversampling == DEFAULT_OVERSAMPLING


def test_params_ride_on_the_dense_leg_only(monkeypatch) -> None:
    _params(monkeypatch)
    dense, sparse = QdrantUtils.search_request_to_qdrant(
        HybridSearchRequest(dense_query=[0.1], sparse_query=SparseVector(indices=[1], values=[1.0]), limit=20)
    )
    assert dense.params.hnsw_ef == DEFAULT_HNSW_EF and sparse.params is None
    # Each leg reads its fusion depth (limit * 2) and a margin for ties at the cut.
    assert dense.limit == sparse.limit == 40 + TIE_MARGIN


def _point(pid: str, score: float) -> MagicMock:
    return MagicMock(id=pid, score=score, payload={})


def test_stable_ties_breaks_ties_by_id_whatever_the_arrival_order() -> None:
    a = [_point("c", 0.9), _point("b", 0.5), _point("a", 0.5)]
    b = [_point("c", 0.9), _point("a", 0.5), _point("b", 0.5)]
    assert [p.id for p in QdrantUtils.stable_ties(a)] == [p.id for p in QdrantUtils.stable_ties(b)] == ["c", "a", "b"]


def test_stable_ties_keeps_qdrants_order_for_a_distance_metric() -> None:
    # Euclid: smaller is nearer, and Qdrant sends the nearest first. That order stays.
    leg = [_point("near", 0.1), _point("b", 0.5), _point("a", 0.5), _point("far", 0.9)]
    assert [p.id for p in QdrantUtils.stable_ties(leg)] == ["near", "a", "b", "far"]


def _service(*leg_answers: list, stored: list[str] | None = None) -> QdrantService:
    svc = QdrantService(QdrantConfig(host="localhost", port=6333, api_key="k"))
    svc.client = AsyncMock()
    svc.client.query_batch_points.return_value = [MagicMock(points=points) for points in leg_answers]
    ids = stored if stored is not None else sorted({str(p.id) for points in leg_answers for p in points})
    svc.client.retrieve.return_value = [MagicMock(id=i, payload={"page_content": i}) for i in ids]
    return svc


@pytest.mark.asyncio
async def test_query_nearest_points_returns_the_requested_depth_in_stable_order() -> None:
    svc = _service([_point("b", 0.5), _point("a", 0.5), _point("z", 0.1)])
    out = await svc.query_nearest_points("col", [HybridSearchRequest(dense_query=[0.1], limit=2)])
    assert [r.id for r in out[0]] == ["a", "b"]
    assert [r.payload for r in out[0]] == [{"page_content": "a"}, {"page_content": "b"}]


_DENSE = [_point("x", 0.9), _point("p", 0.8), _point("q", 0.8), _point("y", 0.7)]
_SPARSE = [_point("y", 7.0), _point("q", 6.0), _point("x", 5.0), _point("p", 4.0)]


@pytest.mark.asyncio
async def test_a_tie_inside_a_leg_gives_the_same_points_and_scores_whatever_order_qdrant_sends() -> None:
    """p and q tie in the dense leg. Qdrant sends tied points in either order; fused
    on its server, the order a tie came in moved the point a place and changed its
    score. Fused here, both arrival orders give the same answer."""
    request = HybridSearchRequest(dense_query=[0.1], sparse_query=SparseVector(indices=[1], values=[1.0]), limit=4)
    swapped = [_DENSE[0], _DENSE[2], _DENSE[1], _DENSE[3]]
    one = await _service(_DENSE, _SPARSE).query_nearest_points("col", [request])
    two = await _service(swapped, _SPARSE).query_nearest_points("col", [request])
    assert [(r.id, r.score) for r in one[0]] == [(r.id, r.score) for r in two[0]]


@pytest.mark.asyncio
async def test_fused_scores_are_qdrants_rrf_scores() -> None:
    request = HybridSearchRequest(dense_query=[0.1], sparse_query=SparseVector(indices=[1], values=[1.0]), limit=4)
    out = await _service(_DENSE, _SPARSE).query_nearest_points("col", [request])
    # 1 / (2 + index), summed over the legs; dense leg in (-score, id) order: x, p, q, y.
    expected = {
        "x": 1 / 2 + 1 / 4, "p": 1 / 3 + 1 / 5, "q": 1 / 4 + 1 / 3, "y": 1 / 5 + 1 / 2,
    }
    assert {r.id: r.score for r in out[0]} == pytest.approx(expected)
    assert [r.id for r in out[0]] == ["x", "y", "q", "p"]


@pytest.mark.asyncio
async def test_each_leg_is_cut_at_its_fusion_depth_before_fusing() -> None:
    # limit 1: each leg fuses its best 2; the margin's extra point must not score.
    dense = [_point("a", 0.9), _point("b", 0.8), _point("c", 0.7)]
    sparse = [_point("c", 9.0), _point("d", 8.0), _point("a", 7.0)]
    request = HybridSearchRequest(dense_query=[0.1], sparse_query=SparseVector(indices=[1], values=[1.0]), limit=1)
    out = await _service(dense, sparse).query_nearest_points("col", [request])
    # a: first in dense (1/2), third in sparse and past its depth of 2. c: past dense's
    # depth, first in sparse (1/2). They tie at 1/2 and the tie goes by id.
    assert [(r.id, r.score) for r in out[0]] == [("a", 0.5)]


@pytest.mark.asyncio
async def test_several_requests_share_one_search_and_one_payload_read() -> None:
    svc = _service([_point("a", 0.9)], [_point("b", 0.9)], [_point("c", 0.9)])
    out = await svc.query_nearest_points("col", [
        HybridSearchRequest(dense_query=[0.1], sparse_query=SparseVector(indices=[1], values=[1.0]), limit=2),
        HybridSearchRequest(dense_query=[0.2], limit=2),
    ])
    assert [[r.id for r in rs] for rs in out] == [["a", "b"], ["c"]]
    assert svc.client.query_batch_points.await_count == 1
    assert svc.client.retrieve.await_count == 1
    assert svc.client.retrieve.call_args.kwargs["ids"] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_an_l2_leg_ranks_its_nearest_point_first() -> None:
    svc = _service([_point("near", 0.1), _point("mid", 0.4), _point("far", 0.9)])
    out = await svc.query_nearest_points("col", [HybridSearchRequest(dense_query=[0.1], limit=3)])
    assert [r.id for r in out[0]] == ["near", "mid", "far"]


class _TiedLeg:
    """A leg where every point ties: Qdrant returns `limit` of them, from a subset it picks."""

    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.limits: list[int] = []

    async def __call__(self, collection_name: str, requests: list) -> list:
        self.limits.extend(r.limit for r in requests)
        return [MagicMock(points=[_point(i, 0.5) for i in self.order[: r.limit]]) for r in requests]


@pytest.mark.asyncio
async def test_a_tie_past_the_margin_is_read_until_it_ends_so_any_subset_gives_one_answer() -> None:
    """limit 1: the leg fuses its best 2 and asks for 4. Five points tie, so the four
    returned are Qdrant's pick; two calls picking different four must still agree."""
    answers = []
    for order in (["a", "b", "c", "d", "e"], ["e", "d", "c", "b", "a"], ["b", "c", "d", "e", "a"]):
        svc = _service()
        leg = _TiedLeg(order)
        svc.client.query_batch_points = AsyncMock(side_effect=leg.__call__)
        svc.client.retrieve.side_effect = lambda **kw: [MagicMock(id=i, payload={}) for i in kw["ids"]]
        out = await svc.query_nearest_points("col", [HybridSearchRequest(dense_query=[0.1], limit=1)])
        answers.append([(r.id, r.score) for r in out[0]])
        assert leg.limits == [4, 8]  # read again, deeper, once the tie ran past the answer
    assert answers[0] == answers[1] == answers[2] == [("a", 0.5)]


@pytest.mark.asyncio
async def test_a_tie_that_ends_inside_the_answer_is_not_read_again() -> None:
    svc = _service()
    svc.client.query_batch_points = AsyncMock(
        side_effect=lambda collection_name, requests: [
            MagicMock(points=[_point("a", 0.9), _point("b", 0.5), _point("c", 0.5), _point("d", 0.1)]) for _ in requests
        ]
    )
    svc.client.retrieve.side_effect = lambda **kw: [MagicMock(id=i, payload={}) for i in kw["ids"]]
    await svc.query_nearest_points("col", [HybridSearchRequest(dense_query=[0.1], limit=1)])
    assert svc.client.query_batch_points.await_count == 1


@pytest.mark.asyncio
async def test_a_point_deleted_before_its_payload_is_read_is_left_out() -> None:
    svc = _service([_point("a", 0.9), _point("b", 0.8)], stored=["b"])
    out = await svc.query_nearest_points("col", [HybridSearchRequest(dense_query=[0.1], limit=2)])
    assert [r.id for r in out[0]] == ["b"]


@pytest.mark.asyncio
async def test_without_payloads_nothing_is_read_back() -> None:
    svc = _service([_point("a", 0.9)])
    out = await svc.query_nearest_points("col", [HybridSearchRequest(dense_query=[0.1], limit=2, with_payload=False)])
    assert [(r.id, r.payload) for r in out[0]] == [("a", {})]
    svc.client.retrieve.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_named_payload_fields_are_the_ones_read_back() -> None:
    svc = _service([_point("a", 0.9)])
    await svc.query_nearest_points(
        "col", [HybridSearchRequest(dense_query=[0.1], limit=2, with_payload=["page_content", "metadata"])],
    )
    assert svc.client.retrieve.call_args.kwargs["with_payload"] == ["page_content", "metadata"]


@pytest.mark.asyncio
@pytest.mark.parametrize("env,explicit,expected", [(None, None, 256), ("128", None, 128), ("128", 64, 64)])
async def test_ef_construct_default_env_and_explicit(monkeypatch, env, explicit, expected) -> None:
    monkeypatch.delenv(qdrant_module.QDRANT_HNSW_EF_CONSTRUCT_ENV, raising=False)
    if env:
        monkeypatch.setenv(qdrant_module.QDRANT_HNSW_EF_CONSTRUCT_ENV, env)
    svc = QdrantService(QdrantConfig(host="localhost", port=6333, api_key="k"))
    svc.client = AsyncMock()
    cfg = CollectionConfig(hnsw=HNSWConfig(ef_construct=explicit) if explicit else None)
    await svc.create_collection("c", cfg)
    assert svc.client.create_collection.call_args.kwargs["hnsw_config"].ef_construct == expected


@pytest.mark.parametrize("bad", ["nan", "inf", "5", "0.9", "x"])
def test_oversampling_rejects_non_finite_and_out_of_range(monkeypatch, bad) -> None:
    assert _params(monkeypatch, **{QDRANT_OVERSAMPLING_ENV: bad}).quantization.oversampling == DEFAULT_OVERSAMPLING


@pytest.mark.parametrize("bad", ["4097", "-1", "nan", "inf"])
def test_ef_out_of_range_falls_back(monkeypatch, bad) -> None:
    assert _params(monkeypatch, **{QDRANT_HNSW_EF_ENV: bad}).hnsw_ef == DEFAULT_HNSW_EF


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["3", "99999", "nan", "inf", "x"])
async def test_ef_construct_bad_values_fall_back(monkeypatch, bad) -> None:
    monkeypatch.setenv(qdrant_module.QDRANT_HNSW_EF_CONSTRUCT_ENV, bad)
    svc = QdrantService(QdrantConfig(host="localhost", port=6333, api_key="k"))
    svc.client = AsyncMock()
    await svc.create_collection("c", CollectionConfig())
    assert svc.client.create_collection.call_args.kwargs["hnsw_config"].ef_construct == 256
