"""What each store is sent for a set of search requests, captured at the client of the real service classes (no server): the
off-identity proof (a request without a scope filter sends exactly what it sent before) and the base of the translation tests.
``captured(store, requests)`` returns the JSON-able commands the store's client received."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.services.vector_db.models import (
    FieldCondition,
    FilterExpression,
    HybridSearchRequest,
    SparseVector,
)
from app.services.vector_db.opensearch.config import OpenSearchConfig
from app.services.vector_db.opensearch.opensearch import OpenSearchService
from app.services.vector_db.qdrant.config import QdrantConfig
from app.services.vector_db.qdrant.qdrant import QdrantService
from app.services.vector_db.redis.config import RedisVectorConfig
from app.services.vector_db.redis.redis_vector import RedisVectorService

ORG = "a" * 24
DENSE = [0.1, 0.2, 0.3, 0.4]


def _org() -> FieldCondition:
    return FieldCondition(key="metadata.orgId", value=ORG)


def plain_requests() -> dict[str, HybridSearchRequest]:
    """Requests without a scope filter, of every shape the three services build a query for."""
    arms = FilterExpression(must=[_org()], should=[FieldCondition(key="recordGroupIds", values=["g1", "g2"]),
                                                    FieldCondition(key="connectorIds", values=["c1"])])
    narrowed = FilterExpression(must=[_org(), FieldCondition(key="metadata.virtualRecordId", values=["r1", "r2"])],
                                must_not=[FieldCondition(key="metadata.blockId", value="b1")])
    return {
        "dense_text_org": HybridSearchRequest(dense_query=DENSE, text_query="hello world", filter=FilterExpression(must=[_org()]), limit=7),
        "dense_text_arms": HybridSearchRequest(dense_query=DENSE, text_query="hello", filter=arms, limit=5),
        "dense_only": HybridSearchRequest(dense_query=DENSE, filter=narrowed, limit=3),
        "text_only": HybridSearchRequest(text_query="only words", filter=FilterExpression(must=[_org()]), limit=4),
        "sparse_dense": HybridSearchRequest(dense_query=DENSE, sparse_query=SparseVector([3, 9], [0.5, 0.25]), filter=arms, limit=6),
        "no_filter": HybridSearchRequest(dense_query=DENSE, text_query="free", limit=2),
        "empty_filter": HybridSearchRequest(dense_query=DENSE, text_query="free", filter=FilterExpression(), limit=2),
    }


def _qdrant() -> tuple[Any, AsyncMock]:
    svc = QdrantService(QdrantConfig(host="localhost", port=6333, api_key="k", prefer_grpc=True, https=False, timeout=180))
    client = AsyncMock()
    client.query_batch_points = AsyncMock(side_effect=lambda collection_name, requests: [MagicMock(points=[]) for _ in requests])
    svc.client = client
    return svc, client


def _opensearch() -> tuple[Any, AsyncMock]:
    svc = OpenSearchService(OpenSearchConfig(host="localhost", port=9200, username="a", password="b", use_ssl=False, verify_certs=False,
                                             ssl_show_warn=False, timeout=300))
    client = AsyncMock()
    client.search = AsyncMock(return_value={"hits": {"hits": []}})
    client.transport = AsyncMock()
    svc.client = client
    svc._ensure_rrf_pipeline = AsyncMock()                      # type: ignore[method-assign]
    return svc, client


def _redis(tagvals: list[bytes]) -> tuple[Any, AsyncMock]:
    svc = RedisVectorService(RedisVectorConfig(host="localhost", port=6379))
    client = AsyncMock()

    async def execute(*cmd: Any) -> Any:  # noqa: ANN401
        return tagvals if cmd[0] == "FT.TAGVALS" else [0]

    client.execute_command = AsyncMock(side_effect=execute)
    svc.client = client
    return svc, client


def _bytes(value: object) -> str:
    """A binary argument (the query vector) as its length and hash: a changed vector changes the golden."""
    raw = bytes(value) if isinstance(value, (bytes, bytearray)) else repr(value).encode()
    return f"<{type(value).__name__}:{len(raw)}:{hashlib.sha256(raw).hexdigest()[:16]}>"


async def captured(store: str, requests: list[HybridSearchRequest], *, tagvals: list[bytes] | None = None) -> list[Any]:
    """The commands the store's client received for ``requests`` (JSON-able, in order)."""
    if store == "qdrant":
        svc, client = _qdrant()
        await svc.query_nearest_points("col", requests)
        return [r.model_dump(mode="json", exclude_none=True) for call in client.query_batch_points.await_args_list for r in call.kwargs["requests"]]       # the fields that are set: the others depend on the client version
    if store == "opensearch":
        svc, client = _opensearch()
        await svc.query_nearest_points("col", requests)
        return sorted((json.loads(json.dumps({"kwargs": c.kwargs}, default=str)) for c in client.search.await_args_list), key=json.dumps)
    svc, client = _redis([b"c1", b"c9"] if tagvals is None else tagvals)
    await svc.query_nearest_points("col", requests)
    return sorted(([a if isinstance(a, str) else _bytes(a) for a in c.args] for c in client.execute_command.await_args_list), key=json.dumps)
