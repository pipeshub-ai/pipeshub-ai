import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.cache.semantic_cache import (
    SemanticCacheScope,
    SemanticCacheService,
    hash_filters,
)
from app.services.vector_db.models import SearchResult, VectorPoint

import asyncio


def test_semantic_cache_hit():
    asyncio.run(_test_semantic_cache_hit())


async def _test_semantic_cache_hit():
    mock_vector_db = AsyncMock()
    mock_vector_db.collection_exists.return_value = True

    # Mock search result with high score
    mock_result = MagicMock(spec=SearchResult)
    mock_result.score = 0.98
    mock_result.payload = {
        "metadata": {"response_text": "This is a cached answer."},
        "page_content": "What is the policy?",
    }
    mock_vector_db.query_nearest_points.return_value = [[mock_result]]

    cache_service = SemanticCacheService(mock_vector_db)

    response = await cache_service.get_cached_response(
        query="What is the policy?",
        embedding=[0.1, 0.2, 0.3],
        filters_hash="hash123",
    )

    assert response == "This is a cached answer."
    mock_vector_db.query_nearest_points.assert_called_once()


def test_semantic_cache_miss():
    asyncio.run(_test_semantic_cache_miss())


async def _test_semantic_cache_miss():
    mock_vector_db = AsyncMock()
    mock_vector_db.collection_exists.return_value = True

    # Mock search result with low score (below threshold)
    mock_result = MagicMock(spec=SearchResult)
    mock_result.score = 0.80
    mock_result.payload = {
        "metadata": {"response_text": "This is a cached answer."},
        "page_content": "What is the policy?",
    }
    mock_vector_db.query_nearest_points.return_value = [[mock_result]]

    cache_service = SemanticCacheService(mock_vector_db)

    response = await cache_service.get_cached_response(
        query="What is the policy?",
        embedding=[0.1, 0.2, 0.3],
        filters_hash="hash123",
    )

    assert response is None


def test_set_cached_response():
    asyncio.run(_test_set_cached_response())


async def _test_set_cached_response():
    mock_vector_db = AsyncMock()
    cache_service = SemanticCacheService(mock_vector_db)

    await cache_service.set_cached_response(
        query="Hello",
        response_text="World",
        embedding=[0.1, 0.2],
        filters_hash="hash123",
        org_id="org1",
        corpus_revision="42",
    )

    mock_vector_db.upsert_points.assert_called_once()
    args, kwargs = mock_vector_db.upsert_points.call_args
    points = args[1]
    assert len(points) == 1
    meta = points[0].payload["metadata"]
    assert meta["query_text"] == "Hello"
    assert meta["response_text"] == "World"
    assert meta["filters_hash"] == "hash123"
    assert meta["orgId"] == "org1"
    assert meta["corpusRevision"] == "42"


def test_hash_filters():
    # Same scope, reordered filters dict → must produce identical hash
    scope1 = SemanticCacheScope(
        orgId="org1",
        userId="u1",
        permissionsRevision="0",
        corpusRevision="5",
        filters={"a": 1, "b": 2},
    )
    scope2 = SemanticCacheScope(
        orgId="org1",
        userId="u1",
        permissionsRevision="0",
        corpusRevision="5",
        filters={"b": 2, "a": 1},  # insertion order differs
    )
    assert hash_filters(scope1) == hash_filters(scope2), (
        "Equivalent filters with different key order must produce the same hash"
    )

    # Different userId → different hash
    scope3 = SemanticCacheScope(
        orgId="org1",
        userId="u2",
        permissionsRevision="0",
        corpusRevision="5",
        filters={"a": 1, "b": 2},
    )
    assert hash_filters(scope1) != hash_filters(scope3), (
        "Different userId must produce a different hash"
    )

    # Different corpusRevision → different hash
    scope4 = SemanticCacheScope(
        orgId="org1",
        userId="u1",
        permissionsRevision="0",
        corpusRevision="6",
        filters={"a": 1, "b": 2},
    )
    assert hash_filters(scope1) != hash_filters(scope4), (
        "Different corpusRevision must produce a different hash"
    )

    # None filters excluded → same hash as filters not provided at all
    scope5 = SemanticCacheScope(
        orgId="org1",
        userId="u1",
        permissionsRevision="0",
        corpusRevision="5",
        filters=None,
    )
    scope6 = SemanticCacheScope(
        orgId="org1",
        userId="u1",
        permissionsRevision="0",
        corpusRevision="5",
    )
    assert hash_filters(scope5) == hash_filters(scope6), (
        "None filters and absent filters must produce the same hash"
    )
