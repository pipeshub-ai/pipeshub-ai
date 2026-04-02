"""
Unit tests for app.utils.cache_helpers

Tests the LRU cache with TTL for user info.
All external dependencies (graph_provider) are mocked.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# We need to reset the module-level cache between tests to avoid leaking state.
# Import the module to access its globals for cleanup.
import app.utils.cache_helpers as cache_module
from app.utils.cache_helpers import (
    MAX_CACHE_SIZE,
    USER_INFO_CACHE_TTL,
    clear_user_info_cache,
    get_cache_stats,
    get_cached_user_info,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
async def clear_cache():
    """Clear the cache before and after every test."""
    cache_module._user_info_cache.clear()
    yield
    cache_module._user_info_cache.clear()


def _make_graph_provider(user_info=None, org_info=None, user_exc=None, org_exc=None):
    """Create a mock graph provider with configurable return values."""
    gp = AsyncMock()
    if user_exc:
        gp.get_user_by_user_id = AsyncMock(side_effect=user_exc)
    else:
        gp.get_user_by_user_id = AsyncMock(return_value=user_info)
    if org_exc:
        gp.get_document = AsyncMock(side_effect=org_exc)
    else:
        gp.get_document = AsyncMock(return_value=org_info)
    return gp


# ============================================================================
# get_cache_stats
# ============================================================================

class TestGetCacheStats:
    def test_empty_cache(self):
        stats = get_cache_stats()
        assert stats["size"] == 0
        assert stats["max_size"] == MAX_CACHE_SIZE
        assert stats["ttl_seconds"] == USER_INFO_CACHE_TTL

    def test_after_population(self):
        """Stats reflect cache size after entries are added."""
        cache_module._user_info_cache["u1:o1"] = {
            "user_info": {"id": "u1"},
            "org_info": {"id": "o1"},
            "timestamp": datetime.now(),
        }
        stats = get_cache_stats()
        assert stats["size"] == 1


# ============================================================================
# get_cached_user_info — cache miss
# ============================================================================

class TestGetCachedUserInfoMiss:
    @pytest.mark.asyncio
    async def test_cache_miss_fetches_from_db(self):
        user_info = {"_key": "u1", "name": "Alice"}
        org_info = {"_key": "o1", "name": "Acme Corp"}
        gp = _make_graph_provider(user_info=user_info, org_info=org_info)

        result_user, result_org = await get_cached_user_info(gp, "u1", "o1")

        assert result_user == user_info
        assert result_org == org_info
        gp.get_user_by_user_id.assert_awaited_once_with("u1")

    @pytest.mark.asyncio
    async def test_cache_populated_after_miss(self):
        gp = _make_graph_provider(
            user_info={"_key": "u1"},
            org_info={"_key": "o1"},
        )
        await get_cached_user_info(gp, "u1", "o1")

        assert "u1:o1" in cache_module._user_info_cache
        cached = cache_module._user_info_cache["u1:o1"]
        assert cached["user_info"]["_key"] == "u1"
        assert cached["org_info"]["_key"] == "o1"

    @pytest.mark.asyncio
    async def test_db_exception_returns_none_both(self):
        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(side_effect=RuntimeError("db down"))
        gp.get_document = AsyncMock(side_effect=RuntimeError("db down"))

        result_user, result_org = await get_cached_user_info(gp, "u1", "o1")

        # gather with return_exceptions=True catches them, then they are
        # converted to None
        assert result_user is None
        assert result_org is None


# ============================================================================
# get_cached_user_info — cache hit
# ============================================================================

class TestGetCachedUserInfoHit:
    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_values(self):
        gp = _make_graph_provider(
            user_info={"_key": "u1"},
            org_info={"_key": "o1"},
        )
        # First call populates
        await get_cached_user_info(gp, "u1", "o1")
        # Second call should use cache
        gp.get_user_by_user_id.reset_mock()
        gp.get_document.reset_mock()

        result_user, result_org = await get_cached_user_info(gp, "u1", "o1")

        assert result_user == {"_key": "u1"}
        gp.get_user_by_user_id.assert_not_awaited()
        gp.get_document.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_different_users_cached_separately(self):
        gp = _make_graph_provider(
            user_info={"_key": "u1"},
            org_info={"_key": "o1"},
        )
        await get_cached_user_info(gp, "u1", "o1")

        # Change return values for second user
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "u2"})
        gp.get_document = AsyncMock(return_value={"_key": "o1"})
        await get_cached_user_info(gp, "u2", "o1")

        assert len(cache_module._user_info_cache) == 2
        assert "u1:o1" in cache_module._user_info_cache
        assert "u2:o1" in cache_module._user_info_cache


# ============================================================================
# get_cached_user_info — cache expiry
# ============================================================================

class TestGetCachedUserInfoExpiry:
    @pytest.mark.asyncio
    async def test_expired_entry_refetched(self):
        gp = _make_graph_provider(
            user_info={"_key": "u1", "version": 1},
            org_info={"_key": "o1"},
        )
        await get_cached_user_info(gp, "u1", "o1")

        # Manually expire the cache entry
        cache_module._user_info_cache["u1:o1"]["timestamp"] = (
            datetime.now() - timedelta(seconds=USER_INFO_CACHE_TTL + 10)
        )

        # Update mock return to a new version
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "u1", "version": 2})
        gp.get_document = AsyncMock(return_value={"_key": "o1"})

        result_user, _ = await get_cached_user_info(gp, "u1", "o1")

        assert result_user["version"] == 2
        gp.get_user_by_user_id.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_expired_entry_removed_from_cache(self):
        gp = _make_graph_provider(
            user_info={"_key": "u1"},
            org_info={"_key": "o1"},
        )
        await get_cached_user_info(gp, "u1", "o1")

        # Expire it
        cache_module._user_info_cache["u1:o1"]["timestamp"] = (
            datetime.now() - timedelta(seconds=USER_INFO_CACHE_TTL + 10)
        )

        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "u1"})
        gp.get_document = AsyncMock(return_value={"_key": "o1"})
        await get_cached_user_info(gp, "u1", "o1")

        # Should have a fresh entry
        entry = cache_module._user_info_cache["u1:o1"]
        age = (datetime.now() - entry["timestamp"]).total_seconds()
        assert age < 2


# ============================================================================
# get_cached_user_info — LRU eviction
# ============================================================================

class TestGetCachedUserInfoLRU:
    @pytest.mark.asyncio
    async def test_eviction_when_cache_full(self):
        """When cache exceeds MAX_CACHE_SIZE, oldest entry is evicted."""
        # Pre-fill cache to MAX_CACHE_SIZE
        for i in range(MAX_CACHE_SIZE):
            cache_module._user_info_cache[f"u{i}:o{i}"] = {
                "user_info": {"_key": f"u{i}"},
                "org_info": {"_key": f"o{i}"},
                "timestamp": datetime.now(),
            }

        assert len(cache_module._user_info_cache) == MAX_CACHE_SIZE

        # Add one more entry via the function
        gp = _make_graph_provider(
            user_info={"_key": "new_user"},
            org_info={"_key": "new_org"},
        )
        await get_cached_user_info(gp, "new_user", "new_org")

        # The oldest entry (u0:o0) should have been evicted
        assert "u0:o0" not in cache_module._user_info_cache
        assert "new_user:new_org" in cache_module._user_info_cache
        assert len(cache_module._user_info_cache) == MAX_CACHE_SIZE

    @pytest.mark.asyncio
    async def test_cache_hit_moves_to_end(self):
        """Accessing a cached entry should move it to the end (most recently used)."""
        gp = _make_graph_provider(
            user_info={"_key": "u1"},
            org_info={"_key": "o1"},
        )
        # Populate two entries
        await get_cached_user_info(gp, "u1", "o1")
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "u2"})
        gp.get_document = AsyncMock(return_value={"_key": "o2"})
        await get_cached_user_info(gp, "u2", "o2")

        # Access u1:o1 again (cache hit) to move it to the end
        gp.get_user_by_user_id.reset_mock()
        await get_cached_user_info(gp, "u1", "o1")

        # u1:o1 should now be at the end
        keys = list(cache_module._user_info_cache.keys())
        assert keys[-1] == "u1:o1"


# ============================================================================
# clear_user_info_cache
# ============================================================================

class TestClearUserInfoCache:
    @pytest.mark.asyncio
    async def test_clear_all(self):
        cache_module._user_info_cache["u1:o1"] = {
            "user_info": {}, "org_info": {}, "timestamp": datetime.now()
        }
        cache_module._user_info_cache["u2:o1"] = {
            "user_info": {}, "org_info": {}, "timestamp": datetime.now()
        }

        await clear_user_info_cache()
        assert len(cache_module._user_info_cache) == 0

    @pytest.mark.asyncio
    async def test_clear_specific_user(self):
        cache_module._user_info_cache["u1:o1"] = {
            "user_info": {}, "org_info": {}, "timestamp": datetime.now()
        }
        cache_module._user_info_cache["u2:o1"] = {
            "user_info": {}, "org_info": {}, "timestamp": datetime.now()
        }

        await clear_user_info_cache(user_id="u1")

        assert "u1:o1" not in cache_module._user_info_cache
        assert "u2:o1" in cache_module._user_info_cache

    @pytest.mark.asyncio
    async def test_clear_specific_org(self):
        cache_module._user_info_cache["u1:o1"] = {
            "user_info": {}, "org_info": {}, "timestamp": datetime.now()
        }
        cache_module._user_info_cache["u1:o2"] = {
            "user_info": {}, "org_info": {}, "timestamp": datetime.now()
        }
        cache_module._user_info_cache["u2:o1"] = {
            "user_info": {}, "org_info": {}, "timestamp": datetime.now()
        }

        await clear_user_info_cache(org_id="o1")

        assert "u1:o1" not in cache_module._user_info_cache
        assert "u2:o1" not in cache_module._user_info_cache
        assert "u1:o2" in cache_module._user_info_cache

    @pytest.mark.asyncio
    async def test_clear_specific_user_and_org(self):
        cache_module._user_info_cache["u1:o1"] = {
            "user_info": {}, "org_info": {}, "timestamp": datetime.now()
        }
        cache_module._user_info_cache["u1:o2"] = {
            "user_info": {}, "org_info": {}, "timestamp": datetime.now()
        }
        cache_module._user_info_cache["u2:o1"] = {
            "user_info": {}, "org_info": {}, "timestamp": datetime.now()
        }

        await clear_user_info_cache(user_id="u1", org_id="o1")

        assert "u1:o1" not in cache_module._user_info_cache
        assert "u1:o2" in cache_module._user_info_cache
        assert "u2:o1" in cache_module._user_info_cache

    @pytest.mark.asyncio
    async def test_clear_empty_cache(self):
        """Clearing an empty cache should not raise."""
        await clear_user_info_cache()  # Should not raise
        assert len(cache_module._user_info_cache) == 0

    @pytest.mark.asyncio
    async def test_clear_nonexistent_user(self):
        """Clearing a user that doesn't exist should not raise."""
        cache_module._user_info_cache["u1:o1"] = {
            "user_info": {}, "org_info": {}, "timestamp": datetime.now()
        }
        await clear_user_info_cache(user_id="u999")
        assert len(cache_module._user_info_cache) == 1


# ============================================================================
# Constants
# ============================================================================

class TestGetCachedUserInfoGatherFailure:
    """Cover the outer except Exception block (lines 84-86)."""

    @pytest.mark.asyncio
    async def test_gather_itself_raises_returns_none(self):
        """When asyncio.gather itself raises (not individual coroutines),
        the outer except block returns (None, None)."""
        gp = AsyncMock()
        # Make get_user_by_user_id return a coroutine that we can control
        # but make asyncio.gather raise by patching it
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "u1"})
        gp.get_document = AsyncMock(return_value={"_key": "o1"})

        with patch("app.utils.cache_helpers.asyncio.gather", side_effect=RuntimeError("gather exploded")):
            result_user, result_org = await get_cached_user_info(gp, "u_fail", "o_fail")

        assert result_user is None
        assert result_org is None

    @pytest.mark.asyncio
    async def test_gather_failure_does_not_cache(self):
        """When the outer except is triggered, nothing should be cached."""
        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "u1"})
        gp.get_document = AsyncMock(return_value={"_key": "o1"})

        with patch("app.utils.cache_helpers.asyncio.gather", side_effect=RuntimeError("boom")):
            await get_cached_user_info(gp, "u_no_cache", "o_no_cache")

        assert "u_no_cache:o_no_cache" not in cache_module._user_info_cache


class TestGetCachedUserInfoCacheMissExplicit:
    """Explicitly test the cache miss branch where cached is None (line 43->56)."""

    @pytest.mark.asyncio
    async def test_completely_empty_cache_miss(self):
        """Cache is completely empty, so .get() returns None, hitting false branch."""
        assert len(cache_module._user_info_cache) == 0
        gp = _make_graph_provider(
            user_info={"_key": "fresh"},
            org_info={"_key": "org_fresh"},
        )
        result_user, result_org = await get_cached_user_info(gp, "fresh", "org_fresh")
        assert result_user == {"_key": "fresh"}
        assert result_org == {"_key": "org_fresh"}
        gp.get_user_by_user_id.assert_awaited_once_with("fresh")

    @pytest.mark.asyncio
    async def test_different_key_is_cache_miss(self):
        """When cache has key A but we request key B, it's a miss."""
        cache_module._user_info_cache["u1:o1"] = {
            "user_info": {"_key": "u1"},
            "org_info": {"_key": "o1"},
            "timestamp": datetime.now(),
        }
        gp = _make_graph_provider(
            user_info={"_key": "u2"},
            org_info={"_key": "o2"},
        )
        result_user, result_org = await get_cached_user_info(gp, "u2", "o2")
        assert result_user == {"_key": "u2"}
        gp.get_user_by_user_id.assert_awaited_once()


class TestCacheConstants:
    def test_ttl_value(self):
        assert USER_INFO_CACHE_TTL == 600

    def test_max_cache_size(self):
        assert MAX_CACHE_SIZE == 1000
