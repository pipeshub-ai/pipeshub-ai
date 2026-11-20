"""Shared helpers for vector DB integration tests."""
from __future__ import annotations

import asyncio
import uuid
from typing import List

import pytest

from app.services.vector_db.models import (
    CollectionConfig,
    DistanceMetric,
    FieldCondition,
    FilterExpression,
    VectorPoint,
)

DIM = 8  # small fixed dimension for tests


def strict_service(request: pytest.FixtureRequest, backend: str) -> object:
    """The service fixture of a backend; in a run that selects integration tests, one that is down fails.

    The shared service fixtures skip when their server is down, which is right for a developer running the
    whole directory and for the general unit run that happens to collect this file. A job that runs
    ``-m integration -k <backend>`` asked for that backend, and a skip would let it pass without running
    the scenario.
    """
    try:
        return request.getfixturevalue(f"{backend}_service")
    except pytest.skip.Exception as skipped:
        marker = (request.config.getoption("markexpr", default="") or "").replace("not integration", "")
        if "integration" not in marker:
            raise
        pytest.fail(f"the {backend} service was selected but is unavailable: {skipped}", pytrace=False)


def point_id(name: str) -> str:
    """A stable UUID for a readable test name: Qdrant accepts only UUIDs or
    integers as point ids, and the other backends take any string."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"pipeshub-vector-it/{name}"))


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
            id=point_id("doc-python"),
            dense_vector=make_dense([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
            payload={
                "page_content": "Python is a high-level programming language",
                "metadata": {"orgId": org_id, "virtualRecordId": "r1"},
            },
        ),
        VectorPoint(
            id=point_id("doc-java"),
            dense_vector=make_dense([0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
            payload={
                "page_content": "Java is a statically typed programming language",
                "metadata": {"orgId": org_id, "virtualRecordId": "r2"},
            },
        ),
        VectorPoint(
            id=point_id("doc-cooking"),
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
