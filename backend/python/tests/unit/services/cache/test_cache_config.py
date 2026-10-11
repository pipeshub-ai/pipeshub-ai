"""Unit tests for Semantic Caching per-organization config read and in-process cache.

Tests:
1. Feature flag disabled -> returns mode="off".
2. Config missing in KV store -> returns mode="off".
3. Malformed config (string instead of dict, invalid mode, invalid types) -> safe fallback to mode="off".
4. Valid config parsed properly.
5. In-process cache (~30s TTL) avoids repeated KV reads.
6. In-process cache expiration after 30s re-reads from KV store.
7. KV read error / exception -> safe fallback to mode="off".
8. mode="off" guarantees zero Redis operations (asserted via mock Redis client).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

from app.services.cache.config import (
    DEFAULT_JACCARD_THRESHOLD,
    DEFAULT_MAX_ENTRIES,
    DEFAULT_SIMILARITY_THRESHOLD,
    DEFAULT_TTL_SECONDS,
    SemanticCacheConfig,
    clear_config_cache,
    get_semantic_cache_config,
)
from app.services.cache.semantic_cache_service import SemanticCacheService


@pytest.fixture(autouse=True)
def _reset_config_cache() -> Generator[None, None, None]:
    """Ensure in-process cache is reset before and after every test."""
    clear_config_cache()
    yield
    clear_config_cache()


def _make_mock_config_service(
    flag_enabled: bool = True,
    kv_value: dict[str, object] | str | None = None,
    raise_kv_error: bool = False,
) -> MagicMock:
    """Helper to build a mock ConfigurationService."""
    mock_service = MagicMock()

    async def _mock_get_config(key: str, default: object = None, use_cache: bool = True) -> object:
        if "platform/settings" in key:
            return {"featureFlags": {"ENABLE_SEMANTIC_CACHE": flag_enabled}}
        if "semanticCache" in key:
            if raise_kv_error:
                raise ConnectionError("KV store unavailable")
            return kv_value
        return default

    mock_service.get_config = AsyncMock(side_effect=_mock_get_config)
    return mock_service


class TestSemanticCacheConfigRead:
    @pytest.mark.asyncio
    async def test_flag_off_returns_mode_off_without_reading_kv(self) -> None:
        """When platform feature flag ENABLE_SEMANTIC_CACHE is off, returns mode='off' immediately."""
        config_service = _make_mock_config_service(flag_enabled=False, kv_value={"mode": "serve"})

        cfg = await get_semantic_cache_config(config_service, org_id="org_123")

        assert cfg.mode == "off"
        # KV key for org semantic cache must NOT be requested if the platform flag is off
        for call_args in config_service.get_config.call_args_list:
            assert "semanticCache" not in call_args[0][0]

    @pytest.mark.asyncio
    async def test_flag_missing_defaults_to_off(self) -> None:
        """When platform settings are empty or missing featureFlags, default is mode='off'."""
        mock_service = MagicMock()
        mock_service.get_config = AsyncMock(return_value={})

        cfg = await get_semantic_cache_config(mock_service, org_id="org_123")

        assert cfg.mode == "off"

    @pytest.mark.asyncio
    async def test_flag_read_exception_defaults_to_off(self) -> None:
        """When platform settings read throws an exception, safe fallback is mode='off'."""
        mock_service = MagicMock()
        mock_service.get_config = AsyncMock(side_effect=RuntimeError("ETCD failure"))

        cfg = await get_semantic_cache_config(mock_service, org_id="org_123")

        assert cfg.mode == "off"

    @pytest.mark.asyncio
    async def test_config_missing_in_kv_returns_mode_off(self) -> None:
        """When flag is enabled but org has no semanticCache config in KV store, returns mode='off'."""
        config_service = _make_mock_config_service(flag_enabled=True, kv_value=None)

        cfg = await get_semantic_cache_config(config_service, org_id="org_empty")

        assert cfg.mode == "off"
        assert cfg.similarity_threshold == DEFAULT_SIMILARITY_THRESHOLD
        assert cfg.ttl_seconds == DEFAULT_TTL_SECONDS
        assert cfg.max_entries_per_context == DEFAULT_MAX_ENTRIES

    @pytest.mark.asyncio
    async def test_malformed_config_non_dict_returns_mode_off(self) -> None:
        """When KV store contains non-dict value (e.g. a raw string or number), falls back to mode='off'."""
        config_service = _make_mock_config_service(flag_enabled=True, kv_value="enabled_mode_string")

        cfg = await get_semantic_cache_config(config_service, org_id="org_malformed")

        assert cfg.mode == "off"

    @pytest.mark.asyncio
    async def test_malformed_config_invalid_mode_falls_back_to_off(self) -> None:
        """When config contains invalid mode string (e.g. 'turbo' or 'active'), falls back to mode='off'."""
        config_service = _make_mock_config_service(
            flag_enabled=True,
            kv_value={"mode": "unrecognized_mode", "similarity_threshold": 0.95},
        )

        cfg = await get_semantic_cache_config(config_service, org_id="org_bad_mode")

        assert cfg.mode == "off"
        assert cfg.similarity_threshold == 0.95

    @pytest.mark.asyncio
    async def test_malformed_config_invalid_numeric_types_use_safe_defaults(self) -> None:
        """When numbers are non-parseable, safe defaults are substituted."""
        config_service = _make_mock_config_service(
            flag_enabled=True,
            kv_value={
                "mode": "serve",
                "similarity_threshold": "not_a_float",
                "ttl_seconds": "invalid_ttl",
                "max_entries_per_context": "bad_int",
                "jaccard_threshold": "not_a_float",
            },
        )

        cfg = await get_semantic_cache_config(config_service, org_id="org_bad_nums")

        assert cfg.mode == "serve"
        assert cfg.similarity_threshold == DEFAULT_SIMILARITY_THRESHOLD
        assert cfg.ttl_seconds == DEFAULT_TTL_SECONDS
        assert cfg.max_entries_per_context == DEFAULT_MAX_ENTRIES
        assert cfg.jaccard_threshold == DEFAULT_JACCARD_THRESHOLD

    @pytest.mark.asyncio
    async def test_valid_config_parsed_successfully(self) -> None:
        """When valid config dict is present, all fields parse correctly."""
        config_service = _make_mock_config_service(
            flag_enabled=True,
            kv_value={
                "mode": "serve",
                "similarity_threshold": 0.96,
                "ttl_seconds": 7200,
                "max_entries_per_context": 20,
                "share_across_org": True,
                "jaccard_threshold": 0.65,
            },
        )

        cfg = await get_semantic_cache_config(config_service, org_id="org_valid")

        assert cfg.mode == "serve"
        assert cfg.similarity_threshold == 0.96
        assert cfg.ttl_seconds == 7200
        assert cfg.max_entries_per_context == 20
        assert cfg.share_across_org is True
        assert cfg.jaccard_threshold == 0.65

    @pytest.mark.asyncio
    async def test_camel_case_config_parsed_successfully(self) -> None:
        """When config uses camelCase keys, all fields parse identically to snake_case."""
        config_service = _make_mock_config_service(
            flag_enabled=True,
            kv_value={
                "mode": "shadow",
                "similarityThreshold": 0.95,
                "ttlSeconds": 3600,
                "maxEntriesPerContext": 15,
                "shareAcrossOrg": True,
                "jaccardThreshold": 0.75,
            },
        )

        cfg = await get_semantic_cache_config(config_service, org_id="org_camel")

        assert cfg.mode == "shadow"
        assert cfg.similarity_threshold == 0.95
        assert cfg.ttl_seconds == 3600
        assert cfg.max_entries_per_context == 15
        assert cfg.share_across_org is True
        assert cfg.jaccard_threshold == 0.75

    @pytest.mark.asyncio
    async def test_kv_error_falls_back_to_mode_off(self) -> None:
        """When reading KV store throws an exception, safe fallback returns mode='off'."""
        config_service = _make_mock_config_service(flag_enabled=True, raise_kv_error=True)

        cfg = await get_semantic_cache_config(config_service, org_id="org_error")

        assert cfg.mode == "off"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("val", "expected"),
        [
            (True, True),
            (False, False),
            ("true", True),
            ("True", True),
            ("1", True),
            ("yes", True),
            ("false", False),
            ("0", False),
            ("no", False),
            (None, False),
            (123, False),
        ],
    )
    async def test_share_across_org_truthy_and_falsy_parsing(self, val: object, expected: bool) -> None:
        config_service = _make_mock_config_service(
            flag_enabled=True,
            kv_value={"mode": "shadow", "share_across_org": val},
        )
        cfg = await get_semantic_cache_config(config_service, org_id=f"org_share_{val}")
        assert cfg.share_across_org is expected


class TestInProcessCaching:
    @pytest.mark.asyncio
    async def test_in_process_cache_avoids_repeated_kv_reads(self) -> None:
        """Consecutive reads within 30s TTL reuse in-process cache without calling KV store again."""
        config_service = _make_mock_config_service(
            flag_enabled=True,
            kv_value={"mode": "shadow", "similarity_threshold": 0.97},
        )

        # Call 1: cache miss
        cfg1 = await get_semantic_cache_config(config_service, org_id="org_cached")
        assert cfg1.mode == "shadow"
        assert cfg1.similarity_threshold == 0.97

        # Call 2: cache hit
        cfg2 = await get_semantic_cache_config(config_service, org_id="org_cached")
        assert cfg2.mode == "shadow"
        assert cfg2 is cfg1

        # Assert KV store was read only once for this org
        kv_calls = [
            call for call in config_service.get_config.call_args_list
            if "/services/org/org_cached/semanticCache" in call[0][0]
        ]
        assert len(kv_calls) == 1

    @pytest.mark.asyncio
    async def test_in_process_cache_expires_after_30s(self) -> None:
        """After 30s TTL expires, next read re-fetches updated config from KV store."""
        config_service = _make_mock_config_service(
            flag_enabled=True,
            kv_value={"mode": "shadow"},
        )

        # First read at t=100
        with patch("time.monotonic", return_value=100.0):
            cfg1 = await get_semantic_cache_config(config_service, org_id="org_exp")
            assert cfg1.mode == "shadow"

        # Change KV store value to 'serve'
        async def _mock_updated_get_config(key: str, default: object = None, use_cache: bool = True) -> object:
            if "platform/settings" in key:
                return {"featureFlags": {"ENABLE_SEMANTIC_CACHE": True}}
            if "semanticCache" in key:
                return {"mode": "serve"}
            return default

        config_service.get_config = AsyncMock(side_effect=_mock_updated_get_config)

        # Read at t=120 (<30s elapsed): should still return cached 'shadow'
        with patch("time.monotonic", return_value=120.0):
            cfg_cached = await get_semantic_cache_config(config_service, org_id="org_exp")
            assert cfg_cached.mode == "shadow"

        # Read at t=131 (>30s elapsed): cache expired, should re-fetch and get 'serve'
        with patch("time.monotonic", return_value=131.0):
            cfg_refreshed = await get_semantic_cache_config(config_service, org_id="org_exp")
            assert cfg_refreshed.mode == "serve"


class TestModeOffZeroRedisCalls:
    @pytest.mark.asyncio
    async def test_mode_off_guarantees_zero_redis_operations(self) -> None:
        """When semantic cache config has mode='off', zero Redis calls occur on the turn."""
        config_service = _make_mock_config_service(flag_enabled=True, kv_value={"mode": "off"})

        cfg = await get_semantic_cache_config(config_service, org_id="org_zero_redis")
        assert cfg.mode == "off"

        # Mock Redis client
        mock_redis = MagicMock()
        mock_redis.eval = AsyncMock()
        mock_redis.evalsha = AsyncMock()
        mock_redis.get = AsyncMock()
        mock_redis.hget = AsyncMock()
        mock_redis.hgetall = AsyncMock()
        mock_redis.hset = AsyncMock()
        mock_redis.set = AsyncMock()

        # Exercise SemanticCacheService.lookup with injected mock Redis
        service = SemanticCacheService(
            config_service=config_service,
            redis_client=mock_redis,
        )
        res = await service.lookup(
            org_id="org_zero_redis",
            user_id="user_1",
            query="test query",
            query_embedding=[0.1, 0.2],
            context_digest="digest123",
            config_hash="hash123",
        )
        assert res.hit is False
        assert res.reasons == ["mode_off"]

        # Assert ZERO Redis calls made
        mock_redis.eval.assert_not_called()
        mock_redis.evalsha.assert_not_called()
        mock_redis.get.assert_not_called()
        mock_redis.hget.assert_not_called()
        mock_redis.hgetall.assert_not_called()
        mock_redis.hset.assert_not_called()
        mock_redis.set.assert_not_called()
        assert len(mock_redis.method_calls) == 0


class TestConfigClamping:
    @pytest.mark.asyncio
    async def test_similarity_threshold_clamped_to_min_and_max(self) -> None:
        """similarity_threshold is clamped to [0.90, 1.0]."""
        # Under min (e.g. 0.85) -> clamped to 0.90
        cs_low = _make_mock_config_service(flag_enabled=True, kv_value={"similarity_threshold": 0.85})
        cfg_low = await get_semantic_cache_config(cs_low, org_id="org_sim_low")
        assert cfg_low.similarity_threshold == 0.90

        # Over max (e.g. 1.25) -> clamped to 1.0
        cs_high = _make_mock_config_service(flag_enabled=True, kv_value={"similarity_threshold": 1.25})
        cfg_high = await get_semantic_cache_config(cs_high, org_id="org_sim_high")
        assert cfg_high.similarity_threshold == 1.0

        # In range (e.g. 0.94) -> kept
        cs_valid = _make_mock_config_service(flag_enabled=True, kv_value={"similarity_threshold": 0.94})
        cfg_valid = await get_semantic_cache_config(cs_valid, org_id="org_sim_valid")
        assert cfg_valid.similarity_threshold == 0.94

    @pytest.mark.asyncio
    async def test_jaccard_threshold_clamped_to_min_and_max(self) -> None:
        """jaccard_threshold is clamped to [0.60, 1.0]."""
        # Under min (e.g. 0.45) -> clamped to 0.60
        cs_low = _make_mock_config_service(flag_enabled=True, kv_value={"jaccard_threshold": 0.45})
        cfg_low = await get_semantic_cache_config(cs_low, org_id="org_jac_low")
        assert cfg_low.jaccard_threshold == 0.60

        # Over max (e.g. 1.50) -> clamped to 1.0
        cs_high = _make_mock_config_service(flag_enabled=True, kv_value={"jaccard_threshold": 1.50})
        cfg_high = await get_semantic_cache_config(cs_high, org_id="org_jac_high")
        assert cfg_high.jaccard_threshold == 1.0

        # In range (e.g. 0.75) -> kept
        cs_valid = _make_mock_config_service(flag_enabled=True, kv_value={"jaccard_threshold": 0.75})
        cfg_valid = await get_semantic_cache_config(cs_valid, org_id="org_jac_valid")
        assert cfg_valid.jaccard_threshold == 0.75

    @pytest.mark.asyncio
    async def test_ttl_seconds_clamped_to_min_and_max(self) -> None:
        """ttl_seconds is clamped to [60, 604800] (1 min to 7 days)."""
        # Under min (e.g. 10s or -100) -> clamped to 60
        cs_low = _make_mock_config_service(flag_enabled=True, kv_value={"ttl_seconds": 10})
        cfg_low = await get_semantic_cache_config(cs_low, org_id="org_ttl_low")
        assert cfg_low.ttl_seconds == 60

        cs_neg = _make_mock_config_service(flag_enabled=True, kv_value={"ttl_seconds": -50})
        cfg_neg = await get_semantic_cache_config(cs_neg, org_id="org_ttl_neg")
        assert cfg_neg.ttl_seconds == 60

        # Over max (e.g. 1,000,000s) -> clamped to 604800
        cs_high = _make_mock_config_service(flag_enabled=True, kv_value={"ttl_seconds": 1000000})
        cfg_high = await get_semantic_cache_config(cs_high, org_id="org_ttl_high")
        assert cfg_high.ttl_seconds == 604800

        # In range (e.g. 3600) -> kept
        cs_valid = _make_mock_config_service(flag_enabled=True, kv_value={"ttl_seconds": 3600})
        cfg_valid = await get_semantic_cache_config(cs_valid, org_id="org_ttl_valid")
        assert cfg_valid.ttl_seconds == 3600

    @pytest.mark.asyncio
    async def test_max_entries_per_context_clamped_to_min_and_max(self) -> None:
        """max_entries_per_context is clamped to [1, 50]."""
        # Under min (e.g. 0 or -5) -> clamped to 1
        cs_low = _make_mock_config_service(flag_enabled=True, kv_value={"max_entries_per_context": 0})
        cfg_low = await get_semantic_cache_config(cs_low, org_id="org_max_low")
        assert cfg_low.max_entries_per_context == 1

        # Over max (e.g. 200) -> clamped to 50
        cs_high = _make_mock_config_service(flag_enabled=True, kv_value={"max_entries_per_context": 200})
        cfg_high = await get_semantic_cache_config(cs_high, org_id="org_max_high")
        assert cfg_high.max_entries_per_context == 50

        # In range (e.g. 25) -> kept
        cs_valid = _make_mock_config_service(flag_enabled=True, kv_value={"max_entries_per_context": 25})
        cfg_valid = await get_semantic_cache_config(cs_valid, org_id="org_max_valid")
        assert cfg_valid.max_entries_per_context == 25
