"""`decide()` is the up-front, per-call-site question the plan's core
idea hinges on: not a global switch, but "will this prefix be read
again before the TTL?" These tests pin every branch and assert each
one carries a specific, diagnosable `reason` string.
"""

from __future__ import annotations

from app.llm.prompt_cache.config import CacheConfig
from app.llm.prompt_cache.decision import CacheReuseClass, decide

_ENABLED = CacheConfig(enabled=True, source="env")
_DISABLED = CacheConfig(enabled=False, source="env")


class TestOneShotUnique:
    def test_never_cached_even_when_config_enabled_and_capability_supports_it(self) -> None:
        decision = decide(
            reuse_class=CacheReuseClass.ONE_SHOT_UNIQUE,
            provider="anthropic", model="claude-sonnet-4-6", cache_config=_ENABLED,
        )
        assert decision.enabled is False
        assert decision.reason == "one_shot_unique_never_cached"

    def test_never_cached_even_with_shared_static_enabled_flag(self) -> None:
        # shared_static_enabled must not leak into ONE_SHOT_UNIQUE.
        decision = decide(
            reuse_class=CacheReuseClass.ONE_SHOT_UNIQUE,
            provider="anthropic", model="claude-sonnet-4-6", cache_config=_ENABLED,
            shared_static_enabled=True,
        )
        assert decision.enabled is False


class TestGlobalKillSwitch:
    def test_disabled_config_blocks_multi_turn(self) -> None:
        decision = decide(
            reuse_class=CacheReuseClass.MULTI_TURN,
            provider="anthropic", model="claude-sonnet-4-6", cache_config=_DISABLED,
        )
        assert decision.enabled is False
        assert decision.reason == "cache_disabled_by_env"

    def test_disabled_config_is_checked_before_one_shot_short_circuits_first(self) -> None:
        # ONE_SHOT_UNIQUE's own reason takes priority regardless of config.
        decision = decide(
            reuse_class=CacheReuseClass.ONE_SHOT_UNIQUE,
            provider="anthropic", model="claude-sonnet-4-6", cache_config=_DISABLED,
        )
        assert decision.reason == "one_shot_unique_never_cached"


class TestCapabilityGate:
    def test_unsupported_provider_blocks_multi_turn(self) -> None:
        decision = decide(
            reuse_class=CacheReuseClass.MULTI_TURN,
            provider="ollama", model="llama3", cache_config=_ENABLED,
        )
        assert decision.enabled is False
        assert decision.reason == "capability_mode_none"

    def test_unrecognized_legacy_model_blocks_multi_turn(self) -> None:
        decision = decide(
            reuse_class=CacheReuseClass.MULTI_TURN,
            provider="anthropic", model="claude-2.1", cache_config=_ENABLED,
        )
        assert decision.enabled is False
        assert decision.reason == "capability_mode_none"


class TestMultiTurn:
    def test_enabled_by_default(self) -> None:
        decision = decide(
            reuse_class=CacheReuseClass.MULTI_TURN,
            provider="anthropic", model="claude-sonnet-4-6", cache_config=_ENABLED,
        )
        assert decision.enabled is True
        assert decision.reason == "multi_turn_default_on"
        assert decision.ttl == "5m"

    def test_shared_static_enabled_flag_has_no_effect_on_multi_turn(self) -> None:
        decision = decide(
            reuse_class=CacheReuseClass.MULTI_TURN,
            provider="anthropic", model="claude-sonnet-4-6", cache_config=_ENABLED,
            shared_static_enabled=False,
        )
        assert decision.enabled is True


class TestSharedStatic:
    def test_off_by_default(self) -> None:
        decision = decide(
            reuse_class=CacheReuseClass.SHARED_STATIC,
            provider="anthropic", model="claude-sonnet-4-6", cache_config=_ENABLED,
        )
        assert decision.enabled is False
        assert decision.reason == "shared_static_off_by_default"

    def test_enabled_when_call_site_opts_in(self) -> None:
        decision = decide(
            reuse_class=CacheReuseClass.SHARED_STATIC,
            provider="anthropic", model="claude-sonnet-4-6", cache_config=_ENABLED,
            shared_static_enabled=True,
        )
        assert decision.enabled is True
        assert decision.reason == "shared_static_enabled_for_call_site"

    def test_still_off_when_config_disabled_even_if_call_site_opted_in(self) -> None:
        decision = decide(
            reuse_class=CacheReuseClass.SHARED_STATIC,
            provider="anthropic", model="claude-sonnet-4-6", cache_config=_DISABLED,
            shared_static_enabled=True,
        )
        assert decision.enabled is False
        assert decision.reason == "cache_disabled_by_env"


class TestCacheKey:
    def test_cache_key_passed_through_when_enabled(self) -> None:
        decision = decide(
            reuse_class=CacheReuseClass.MULTI_TURN,
            provider="anthropic", model="claude-sonnet-4-6", cache_config=_ENABLED,
            cache_key="org-123:user-456",
        )
        assert decision.cache_key == "org-123:user-456"

    def test_cache_key_absent_when_disabled(self) -> None:
        decision = decide(
            reuse_class=CacheReuseClass.ONE_SHOT_UNIQUE,
            provider="anthropic", model="claude-sonnet-4-6", cache_config=_ENABLED,
            cache_key="org-123:user-456",
        )
        assert decision.cache_key is None
