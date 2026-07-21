"""Shared helpers for vector DB integration tests."""
from __future__ import annotations

import asyncio
from typing import List

from app.services.vector_db.models import (
    CollectionConfig,
    DistanceMetric,
    FieldCondition,
    FilterExpression,
    HybridSearchRequest,
    SparseVector,
    VectorPoint,
)


DIM = 8  # small fixed dimension for tests


def make_dense(values: List[float]) -> List[float]:
    """Pad/trim a float list to DIM."""
    vec = list(values)
    if len(vec) < DIM:
        vec.extend([0.0] * (DIM - len(vec)))
    return vec[:DIM]


def sample_points(org_id: str = "org-test") -> List[VectorPoint]:
    """Return 3 sample VectorPoints spanning lexical and semantic axes."""
    return [
        VectorPoint(
            id="doc-python",
            dense_vector=make_dense([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
            payload={
                "page_content": "Python is a high-level programming language",
                "metadata": {"orgId": org_id, "virtualRecordId": "r1"},
            },
        ),
        VectorPoint(
            id="doc-java",
            dense_vector=make_dense([0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
            payload={
                "page_content": "Java is a statically typed programming language",
                "metadata": {"orgId": org_id, "virtualRecordId": "r2"},
            },
        ),
        VectorPoint(
            id="doc-cooking",
            dense_vector=make_dense([0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
            payload={
                "page_content": "Cooking pasta requires boiling water",
                "metadata": {"orgId": org_id, "virtualRecordId": "r3"},
            },
        ),
    ]


def make_collection_config() -> CollectionConfig:
    return CollectionConfig(
        embedding_size=DIM,
        distance_metric=DistanceMetric.COSINE,
    )


def org_filter(org_id: str) -> FilterExpression:
    return FilterExpression(
        must=[FieldCondition(key="metadata.orgId", value=org_id)]
    )


async def wait_for(coro, timeout: float = 30.0, poll: float = 0.5):
    """Poll an async callable until it returns truthy or times out."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        try:
            result = await coro()
            if result:
                return result
        except Exception:
            pass
        if asyncio.get_event_loop().time() >= deadline:
            raise TimeoutError("Timed out waiting for condition")
        await asyncio.sleep(poll)
