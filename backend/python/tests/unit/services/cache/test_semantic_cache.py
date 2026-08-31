import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.cache.semantic_cache import SemanticCacheService, hash_filters
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
    mock_result.payload = {"response_text": "This is a cached answer."}
    mock_vector_db.query_nearest_points.return_value = [[mock_result]]
    
    cache_service = SemanticCacheService(mock_vector_db)
    
    response = await cache_service.get_cached_response(
        query="What is the policy?",
        embedding=[0.1, 0.2, 0.3],
        filters_hash="hash123"
    )
    
    assert response == "This is a cached answer."
    mock_vector_db.query_nearest_points.assert_called_once()

def test_semantic_cache_miss():
    asyncio.run(_test_semantic_cache_miss())

async def _test_semantic_cache_miss():
    mock_vector_db = AsyncMock()
    mock_vector_db.collection_exists.return_value = True
    
    # Mock search result with low score
    mock_result = MagicMock(spec=SearchResult)
    mock_result.score = 0.80
    mock_result.payload = {"response_text": "This is a cached answer."}
    mock_vector_db.query_nearest_points.return_value = [[mock_result]]
    
    cache_service = SemanticCacheService(mock_vector_db)
    
    response = await cache_service.get_cached_response(
        query="What is the policy?",
        embedding=[0.1, 0.2, 0.3],
        filters_hash="hash123"
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
        filters_hash="hash123"
    )
    
    mock_vector_db.upsert_points.assert_called_once()
    args, kwargs = mock_vector_db.upsert_points.call_args
    points = args[1]
    assert len(points) == 1
    assert points[0].payload["query_text"] == "Hello"
    assert points[0].payload["response_text"] == "World"
    assert points[0].payload["filters_hash"] == "hash123"

def test_hash_filters():
    assert hash_filters(None) == "none"
    assert hash_filters({}) == "none"
    hash1 = hash_filters({"a": 1, "b": 2})
    hash2 = hash_filters({"b": 2, "a": 1})
    assert hash1 == hash2
