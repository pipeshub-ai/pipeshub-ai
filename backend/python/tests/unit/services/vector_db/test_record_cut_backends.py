"""The deepest request the record cut can send fits every vector backend.

`FUSION_RECORD_CUT` asks for up to 300 points per query (the over-fetch cap)
and each backend widens its legs further. These pin what Qdrant, OpenSearch
and Redis are asked for at that depth, against their own hard limits.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.retrieval.record_cut import MAX_FUSION_DEPTH, RecordCut
from app.modules.retrieval.retrieval_service import _OVERFETCH_ABSOLUTE_CAP
from app.services.vector_db.models import HybridSearchRequest, SparseVector
from app.services.vector_db.opensearch.utils import OpenSearchUtils
from app.services.vector_db.qdrant.utils import QdrantUtils
from app.services.vector_db.redis import redis_vector
from app.services.vector_db.redis.config import RedisVectorConfig

# OpenSearch's default index.max_result_window and its k-NN `k` ceiling.
_OPENSEARCH_MAX_WINDOW = 10_000


def _deepest() -> int:
    """The largest fused request any limit up to the cap produces."""
    return max(
        RecordCut(depth=depth).fused_limit(limit, _OVERFETCH_ABSOLUTE_CAP)
        for depth in range(1, MAX_FUSION_DEPTH + 1)
        for limit in range(1, _OVERFETCH_ABSOLUTE_CAP + 1)
    )


def _request(**kwargs) -> HybridSearchRequest:
    return HybridSearchRequest(dense_query=[0.1, 0.2], limit=_deepest(), **kwargs)


def test_the_deepest_request_is_the_cap() -> None:
    assert _deepest() == _OVERFETCH_ABSOLUTE_CAP == 300


def test_qdrant_legs() -> None:
    query = QdrantUtils.search_request_to_qdrant(
        _request(sparse_query=SparseVector(indices=[1], values=[1.0]))
    )

    assert query.limit == 300
    assert [leg.limit for leg in query.prefetch] == [600, 600]


def test_opensearch_window_and_k() -> None:
    body = OpenSearchUtils.build_hybrid_query(_request(text_query="quarterly revenue"))

    knn = body["query"]["hybrid"]["queries"][1]["knn"]["dense_embedding"]
    assert body["size"] == 300
    assert knn["k"] == 600
    assert knn["k"] <= _OPENSEARCH_MAX_WINDOW
    assert body["size"] <= _OPENSEARCH_MAX_WINDOW


@pytest.mark.asyncio
async def test_redis_window_and_limit() -> None:
    service = redis_vector.RedisVectorService(RedisVectorConfig(host="localhost", port=6379))
    client = AsyncMock()
    client.execute_command = AsyncMock(return_value=[0])
    pipeline = MagicMock()
    pipeline.execute = AsyncMock(return_value=[])
    client.pipeline = MagicMock(return_value=pipeline)
    service.client = client

    await service.query_nearest_points("records", [_request(text_query="quarterly revenue")])

    args = list(client.execute_command.await_args.args)
    window = int(args[args.index("K") + 1])
    limit = int(args[args.index("LIMIT") + 2])
    assert (window, limit) == (600, 300)
    assert int(args[args.index("WINDOW") + 1]) == window
    assert window <= redis_vector._MAX_SEARCH_RESULTS
