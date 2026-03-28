"""
Unit tests for app.modules.agents.qna.cache_manager

Tests all cache layers: CacheEntry, LRUCacheWithTTL, CacheManager, and
the global singleton get_cache_manager().
"""

import time
from unittest.mock import patch

import pytest

from app.modules.agents.qna.cache_manager import (
    CacheEntry,
    CacheManager,
    LRUCacheWithTTL,
    get_cache_manager,
    LLM_CACHE_SIZE,
    LLM_CACHE_TTL,
    TOOL_CACHE_SIZE,
    TOOL_CACHE_TTL,
    RETRIEVAL_CACHE_SIZE,
    RETRIEVAL_CACHE_TTL,
)


# ============================================================================
# 1. CacheEntry
# ============================================================================

class TestCacheEntry:
    """Tests for CacheEntry."""

    def test_init(self):
        """CacheEntry initializes with correct attributes."""
        entry = CacheEntry("key1", "value1", ttl=60)
        assert entry.key == "key1"
        assert entry.value == "value1"
        assert entry.ttl == 60
        assert entry.hits == 0
        assert entry.created_at > 0
        assert entry.last_accessed > 0

    def test_is_expired_false(self):
        """Fresh entry is not expired."""
        entry = CacheEntry("k", "v", ttl=3600)
        assert entry.is_expired() is False

    def test_is_expired_true(self):
        """Entry with short TTL and old creation time is expired."""
        entry = CacheEntry("k", "v", ttl=1)
        entry.created_at = time.time() - 10  # Created 10 seconds ago, TTL is 1
        assert entry.is_expired() is True

    def test_is_expired_with_past_creation(self):
        """Entry created in the past beyond TTL is expired."""
        entry = CacheEntry("k", "v", ttl=10)
        entry.created_at = time.time() - 20  # 20 seconds ago
        assert entry.is_expired() is True

    def test_access_increments_hits(self):
        """access() increments hit counter."""
        entry = CacheEntry("k", "v", ttl=60)
        assert entry.hits == 0
        val = entry.access()
        assert val == "v"
        assert entry.hits == 1

    def test_access_updates_last_accessed(self):
        """access() updates last_accessed timestamp."""
        entry = CacheEntry("k", "v", ttl=60)
        before = entry.last_accessed
        # Small delay to ensure timestamp changes
        entry.access()
        assert entry.last_accessed >= before

    def test_access_returns_value(self):
        """access() returns the stored value."""
        entry = CacheEntry("k", {"data": [1, 2, 3]}, ttl=60)
        result = entry.access()
        assert result == {"data": [1, 2, 3]}

    def test_multiple_accesses(self):
        """Multiple accesses increment hits correctly."""
        entry = CacheEntry("k", "v", ttl=60)
        for _ in range(5):
            entry.access()
        assert entry.hits == 5


# ============================================================================
# 2. LRUCacheWithTTL
# ============================================================================

class TestLRUCacheWithTTL:
    """Tests for LRUCacheWithTTL."""

    def test_init(self):
        """Cache initializes with correct parameters."""
        cache = LRUCacheWithTTL(max_size=10, default_ttl=60, name="test")
        assert cache.max_size == 10
        assert cache.default_ttl == 60
        assert cache.name == "test"
        assert len(cache.cache) == 0
        assert cache.total_hits == 0
        assert cache.total_misses == 0

    def test_set_and_get(self):
        """Set a value and retrieve it."""
        cache = LRUCacheWithTTL(max_size=10, default_ttl=3600)
        cache.set("key1", "value1")
        result = cache.get("key1")
        assert result == "value1"

    def test_get_nonexistent_returns_none(self):
        """Get nonexistent key returns None."""
        cache = LRUCacheWithTTL(max_size=10, default_ttl=3600)
        result = cache.get("missing")
        assert result is None
        assert cache.total_misses == 1

    def test_get_expired_returns_none(self):
        """Get expired entry returns None and removes it."""
        cache = LRUCacheWithTTL(max_size=10, default_ttl=1)
        cache.set("key1", "value1", ttl=0)  # TTL=0 means expired immediately
        result = cache.get("key1")
        assert result is None
        assert "key1" not in cache.cache

    def test_lru_eviction(self):
        """Oldest entry is evicted when cache is full."""
        cache = LRUCacheWithTTL(max_size=3, default_ttl=3600)
        cache.set("k1", "v1")
        cache.set("k2", "v2")
        cache.set("k3", "v3")
        # Cache full, adding k4 should evict k1
        cache.set("k4", "v4")
        assert cache.get("k1") is None
        assert cache.get("k4") == "v4"

    def test_lru_access_moves_to_end(self):
        """Accessing an entry moves it to the end (most recently used)."""
        cache = LRUCacheWithTTL(max_size=3, default_ttl=3600)
        cache.set("k1", "v1")
        cache.set("k2", "v2")
        cache.set("k3", "v3")
        # Access k1 to move it to the end
        cache.get("k1")
        # Now adding k4 should evict k2 (oldest not accessed)
        cache.set("k4", "v4")
        assert cache.get("k2") is None
        assert cache.get("k1") == "v1"

    def test_set_overwrites_existing(self):
        """Setting same key overwrites the value."""
        cache = LRUCacheWithTTL(max_size=10, default_ttl=3600)
        cache.set("k1", "v1")
        cache.set("k1", "v2")
        assert cache.get("k1") == "v2"
        assert len(cache.cache) == 1

    def test_custom_ttl(self):
        """Custom TTL overrides default."""
        cache = LRUCacheWithTTL(max_size=10, default_ttl=3600)
        cache.set("k1", "v1", ttl=1)
        # Force expiry by backdating creation
        cache.cache["k1"].created_at = time.time() - 10
        result = cache.get("k1")
        assert result is None

    def test_clear_expired(self):
        """clear_expired removes expired entries."""
        cache = LRUCacheWithTTL(max_size=10, default_ttl=3600)
        cache.set("fresh", "v1")
        cache.set("stale", "v2", ttl=1)
        cache.cache["stale"].created_at = time.time() - 10
        # Manually set creation time in the past for stale
        cache.cache["stale"].created_at = time.time() - 10
        cache.cache["stale"].ttl = 1
        removed = cache.clear_expired()
        assert removed == 1
        assert "stale" not in cache.cache
        assert "fresh" in cache.cache

    def test_clear_expired_none_expired(self):
        """clear_expired with no expired entries returns 0."""
        cache = LRUCacheWithTTL(max_size=10, default_ttl=3600)
        cache.set("k1", "v1")
        removed = cache.clear_expired()
        assert removed == 0

    def test_get_stats(self):
        """get_stats returns correct statistics."""
        cache = LRUCacheWithTTL(max_size=10, default_ttl=3600, name="test_cache")
        cache.set("k1", "v1")
        cache.get("k1")  # Hit
        cache.get("k2")  # Miss

        stats = cache.get_stats()
        assert stats["name"] == "test_cache"
        assert stats["size"] == 1
        assert stats["max_size"] == 10
        assert stats["total_hits"] == 1
        assert stats["total_misses"] == 1
        assert stats["hit_rate_percent"] == 50.0

    def test_get_stats_no_requests(self):
        """get_stats with no requests returns 0 hit rate."""
        cache = LRUCacheWithTTL(max_size=10, default_ttl=3600)
        stats = cache.get_stats()
        assert stats["hit_rate_percent"] == 0

    def test_clear(self):
        """clear removes all entries and resets stats."""
        cache = LRUCacheWithTTL(max_size=10, default_ttl=3600)
        cache.set("k1", "v1")
        cache.set("k2", "v2")
        cache.get("k1")  # hit
        cache.get("missing")  # miss

        cache.clear()
        assert len(cache.cache) == 0
        assert cache.total_hits == 0
        assert cache.total_misses == 0

    def test_hit_tracking(self):
        """Hits and misses are tracked correctly."""
        cache = LRUCacheWithTTL(max_size=10, default_ttl=3600)
        cache.set("k1", "v1")

        cache.get("k1")  # Hit
        cache.get("k1")  # Hit
        cache.get("missing")  # Miss

        assert cache.total_hits == 2
        assert cache.total_misses == 1


# ============================================================================
# 3. CacheManager
# ============================================================================

class TestCacheManager:
    """Tests for CacheManager."""

    def test_init(self):
        """CacheManager initializes with three cache layers."""
        cm = CacheManager()
        assert isinstance(cm.llm_cache, LRUCacheWithTTL)
        assert isinstance(cm.tool_cache, LRUCacheWithTTL)
        assert isinstance(cm.retrieval_cache, LRUCacheWithTTL)

    def test_llm_cache_sizes(self):
        """LLM cache has correct size and TTL."""
        cm = CacheManager()
        assert cm.llm_cache.max_size == LLM_CACHE_SIZE
        assert cm.llm_cache.default_ttl == LLM_CACHE_TTL

    def test_tool_cache_sizes(self):
        """Tool cache has correct size and TTL."""
        cm = CacheManager()
        assert cm.tool_cache.max_size == TOOL_CACHE_SIZE
        assert cm.tool_cache.default_ttl == TOOL_CACHE_TTL

    def test_retrieval_cache_sizes(self):
        """Retrieval cache has correct size and TTL."""
        cm = CacheManager()
        assert cm.retrieval_cache.max_size == RETRIEVAL_CACHE_SIZE
        assert cm.retrieval_cache.default_ttl == RETRIEVAL_CACHE_TTL

    # --- LLM Response Caching ---

    def test_llm_response_cache_miss(self):
        """Cache miss returns None."""
        cm = CacheManager()
        result = cm.get_llm_response("What is X?")
        assert result is None

    def test_llm_response_set_and_get(self):
        """Set and get LLM response."""
        cm = CacheManager()
        cm.set_llm_response("What is X?", "X is a variable.")
        result = cm.get_llm_response("What is X?")
        assert result == "X is a variable."

    def test_llm_response_with_context(self):
        """LLM response with context is cached separately."""
        cm = CacheManager()
        ctx1 = {"has_internal_data": True, "tools": ["jira"]}
        ctx2 = {"has_internal_data": False, "tools": []}

        cm.set_llm_response("query", "answer1", context=ctx1)
        cm.set_llm_response("query", "answer2", context=ctx2)

        assert cm.get_llm_response("query", context=ctx1) == "answer1"
        assert cm.get_llm_response("query", context=ctx2) == "answer2"

    def test_llm_response_case_insensitive(self):
        """Query normalization makes caching case-insensitive."""
        cm = CacheManager()
        cm.set_llm_response("What is X?", "X is a variable.")
        result = cm.get_llm_response("what is x?")
        assert result == "X is a variable."

    def test_llm_response_whitespace_normalized(self):
        """Query normalization strips whitespace."""
        cm = CacheManager()
        cm.set_llm_response("  hello  ", "world")
        result = cm.get_llm_response("hello")
        assert result == "world"

    # --- Tool Result Caching ---

    def test_tool_result_cache_miss(self):
        """Cache miss returns None."""
        cm = CacheManager()
        result = cm.get_tool_result("calculator.add", {"a": 1, "b": 2})
        assert result is None

    def test_tool_result_idempotent_tool_cached(self):
        """Idempotent tool results are cached."""
        cm = CacheManager()
        cm.set_tool_result("calculator.add", {"a": 1}, (True, 42))
        result = cm.get_tool_result("calculator.add", {"a": 1})
        assert result == (True, 42)

    def test_tool_result_non_idempotent_not_cached(self):
        """Non-idempotent tools are not cached (get returns None)."""
        cm = CacheManager()
        cm.set_tool_result("jira.create_issue", {"title": "New"}, (True, {"key": "J-1"}))
        result = cm.get_tool_result("jira.create_issue", {"title": "New"})
        assert result is None

    def test_tool_result_get_idempotent_tools(self):
        """Various idempotent tool patterns are cached."""
        cm = CacheManager()
        idempotent_tools = [
            "calculator.add",
            "web_search.search",
            "get_users.list",
            "list_channels.all",
            "search_issues.query",
            "fetch_page.content",
            "retrieve_data.all",
        ]
        for tool_name in idempotent_tools:
            cm.set_tool_result(tool_name, {"q": "test"}, "result")
            result = cm.get_tool_result(tool_name, {"q": "test"})
            assert result == "result", f"Failed for {tool_name}"

    def test_tool_result_failed_not_cached(self):
        """Failed tool results (False, error) are not cached."""
        cm = CacheManager()
        cm.set_tool_result("calculator.add", {"a": 1}, (False, "error"))
        result = cm.get_tool_result("calculator.add", {"a": 1})
        assert result is None

    def test_tool_result_args_order_independent(self):
        """Args are hashed consistently regardless of dict ordering."""
        cm = CacheManager()
        cm.set_tool_result("calculator.add", {"a": 1, "b": 2}, "result")
        result = cm.get_tool_result("calculator.add", {"b": 2, "a": 1})
        assert result == "result"

    # --- Retrieval Caching ---

    def test_retrieval_cache_miss(self):
        """Cache miss returns None."""
        cm = CacheManager()
        result = cm.get_retrieval_results("query", None, 10)
        assert result is None

    def test_retrieval_set_and_get(self):
        """Set and get retrieval results."""
        cm = CacheManager()
        results = [{"text": "chunk1"}, {"text": "chunk2"}]
        cm.set_retrieval_results("query", {"apps": ["jira"]}, 10, results)
        cached = cm.get_retrieval_results("query", {"apps": ["jira"]}, 10)
        assert cached == results

    def test_retrieval_different_filters(self):
        """Different filters produce different cache keys."""
        cm = CacheManager()
        cm.set_retrieval_results("query", {"apps": ["jira"]}, 10, [{"text": "jira"}])
        cm.set_retrieval_results("query", {"apps": ["slack"]}, 10, [{"text": "slack"}])

        jira_result = cm.get_retrieval_results("query", {"apps": ["jira"]}, 10)
        slack_result = cm.get_retrieval_results("query", {"apps": ["slack"]}, 10)
        assert jira_result == [{"text": "jira"}]
        assert slack_result == [{"text": "slack"}]

    def test_retrieval_different_limits(self):
        """Different limits produce different cache keys."""
        cm = CacheManager()
        cm.set_retrieval_results("query", None, 5, [1, 2])
        cm.set_retrieval_results("query", None, 10, [1, 2, 3])

        assert cm.get_retrieval_results("query", None, 5) == [1, 2]
        assert cm.get_retrieval_results("query", None, 10) == [1, 2, 3]

    # --- Cache Management ---

    def test_clear_expired(self):
        """clear_expired returns counts per cache."""
        cm = CacheManager()
        result = cm.clear_expired()
        assert "llm" in result
        assert "tool" in result
        assert "retrieval" in result
        assert all(isinstance(v, int) for v in result.values())

    def test_get_all_stats(self):
        """get_all_stats returns stats for all caches."""
        cm = CacheManager()
        stats = cm.get_all_stats()
        assert "llm_cache" in stats
        assert "tool_cache" in stats
        assert "retrieval_cache" in stats

    def test_clear_all(self):
        """clear_all empties all caches."""
        cm = CacheManager()
        cm.set_llm_response("q", "a")
        cm.set_tool_result("calculator.add", {"a": 1}, "result")
        cm.set_retrieval_results("q", None, 10, [1])

        cm.clear_all()

        assert cm.get_llm_response("q") is None
        assert cm.get_tool_result("calculator.add", {"a": 1}) is None
        assert cm.get_retrieval_results("q", None, 10) is None


# ============================================================================
# 4. get_cache_manager() - Singleton
# ============================================================================

class TestGetCacheManager:
    """Tests for get_cache_manager() singleton."""

    def test_returns_cache_manager_instance(self):
        """Returns a CacheManager instance."""
        cm = get_cache_manager()
        assert isinstance(cm, CacheManager)

    def test_returns_same_instance(self):
        """Multiple calls return the same instance (singleton)."""
        cm1 = get_cache_manager()
        cm2 = get_cache_manager()
        assert cm1 is cm2

    def test_instance_is_functional(self):
        """Singleton instance is functional for all operations."""
        cm = get_cache_manager()
        # Ensure we can use it
        cm.set_llm_response("singleton_test", "works")
        result = cm.get_llm_response("singleton_test")
        assert result == "works"
        # Clean up
        cm.llm_cache.cache.pop(
            cm._compute_query_hash("singleton_test"), None
        )


# ============================================================================
# 5. _normalize_query and _compute_query_hash
# ============================================================================

class TestCacheManagerHashFunctions:
    """Tests for internal hash/normalization methods."""

    def test_normalize_query(self):
        """Query normalization lowercases and strips."""
        cm = CacheManager()
        assert cm._normalize_query("  HELLO World  ") == "hello world"

    def test_compute_query_hash_deterministic(self):
        """Same inputs produce same hash."""
        cm = CacheManager()
        h1 = cm._compute_query_hash("test", {"has_internal_data": True})
        h2 = cm._compute_query_hash("test", {"has_internal_data": True})
        assert h1 == h2

    def test_compute_query_hash_different_context(self):
        """Different contexts produce different hashes."""
        cm = CacheManager()
        h1 = cm._compute_query_hash("test", {"has_internal_data": True})
        h2 = cm._compute_query_hash("test", {"has_internal_data": False})
        assert h1 != h2

    def test_compute_query_hash_no_context(self):
        """Hash works without context."""
        cm = CacheManager()
        h = cm._compute_query_hash("test")
        assert isinstance(h, str)
        assert len(h) == 16

    def test_compute_tool_hash_deterministic(self):
        """Same tool name and args produce same hash."""
        cm = CacheManager()
        h1 = cm._compute_tool_hash("calc.add", {"a": 1, "b": 2})
        h2 = cm._compute_tool_hash("calc.add", {"a": 1, "b": 2})
        assert h1 == h2

    def test_compute_tool_hash_different_args(self):
        """Different args produce different hashes."""
        cm = CacheManager()
        h1 = cm._compute_tool_hash("calc.add", {"a": 1})
        h2 = cm._compute_tool_hash("calc.add", {"a": 2})
        assert h1 != h2

    def test_compute_retrieval_hash_no_filters(self):
        """Retrieval hash works without filters."""
        cm = CacheManager()
        h = cm._compute_retrieval_hash("query", None, 10)
        assert isinstance(h, str)
        assert len(h) == 16


# ============================================================================
# 6. Constants
# ============================================================================

class TestCacheConstants:
    """Tests for cache constants."""

    def test_llm_cache_size(self):
        assert LLM_CACHE_SIZE == 1000

    def test_tool_cache_size(self):
        assert TOOL_CACHE_SIZE == 500

    def test_retrieval_cache_size(self):
        assert RETRIEVAL_CACHE_SIZE == 200

    def test_llm_cache_ttl(self):
        assert LLM_CACHE_TTL == 3600

    def test_tool_cache_ttl(self):
        assert TOOL_CACHE_TTL == 300

    def test_retrieval_cache_ttl(self):
        assert RETRIEVAL_CACHE_TTL == 1800
