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
    q = QdrantUtils.search_request_to_qdrant(
        HybridSearchRequest(dense_query=[0.1], sparse_query=SparseVector(indices=[1], values=[1.0]), limit=20)
    )
    dense, sparse = q.prefetch
    assert dense.params.hnsw_ef == DEFAULT_HNSW_EF and sparse.params is None
    assert q.limit == 20 + TIE_MARGIN


def _point(pid: str, score: float) -> MagicMock:
    return MagicMock(id=pid, score=score, payload={})


def test_stable_top_breaks_ties_by_id_whatever_the_arrival_order() -> None:
    a = [_point("b", 0.5), _point("a", 0.5), _point("c", 0.9)]
    b = [_point("a", 0.5), _point("c", 0.9), _point("b", 0.5)]
    assert [p.id for p in QdrantUtils.stable_top(a, 3)] == [p.id for p in QdrantUtils.stable_top(b, 3)] == ["c", "a", "b"]
    assert [p.id for p in QdrantUtils.stable_top(a, 2)] == ["c", "a"]


@pytest.mark.asyncio
async def test_query_nearest_points_returns_the_requested_depth_in_stable_order() -> None:
    svc = QdrantService(QdrantConfig(host="localhost", port=6333, api_key="k"))
    svc.client = AsyncMock()
    batch = MagicMock(points=[_point("b", 0.5), _point("a", 0.5), _point("z", 0.1)])
    svc.client.query_batch_points.return_value = [batch]
    out = await svc.query_nearest_points("col", [HybridSearchRequest(dense_query=[0.1], limit=2)])
    assert [r.id for r in out[0]] == ["a", "b"]


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
