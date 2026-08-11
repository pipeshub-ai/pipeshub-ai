"""`resolve_cache_config` — enabled by default, `ENABLE_PROMPT_CACHING=false`
(env) is a hard floor; above that floor, the `ENABLE_PROMPT_CACHING` Labs
feature flag (Phase 7) can still turn it off. Mirrors the codebase's dominant
env-bool test pattern (case-insensitive, "true" is the only truthy spelling)
plus `FeatureFlagService` singleton isolation, matching
`tests/unit/services/featureflag/test_featureflag.py`.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.llm.prompt_cache.config import ENABLE_PROMPT_CACHING_ENV_VAR, resolve_cache_config
from app.services.featureflag.featureflag import FeatureFlagService
from app.services.featureflag.interfaces.config import IConfigProvider


@pytest.fixture(autouse=True)
def reset_feature_flag_singleton():
    FeatureFlagService.reset_instance()
    yield
    FeatureFlagService.reset_instance()


def _mock_provider(flag_value: bool | None) -> MagicMock:
    provider = MagicMock(spec=IConfigProvider)
    provider.get_flag_value = MagicMock(return_value=flag_value)
    return provider


class TestEnvFloor:
    """`ENABLE_PROMPT_CACHING=false` (env) disables regardless of the
    platform flag — it is checked BEFORE the flag service, so an enabled
    flag can never re-enable caching once the env floor says no."""

    def test_explicit_false_disables_even_if_flag_would_enable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENABLE_PROMPT_CACHING_ENV_VAR, "false")
        FeatureFlagService.get_service(provider=_mock_provider(True))
        config = resolve_cache_config()
        assert config.enabled is False
        assert config.source == "env"

    def test_case_insensitive_true_leaves_flag_service_as_the_decider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENABLE_PROMPT_CACHING_ENV_VAR, "TRUE")
        FeatureFlagService.get_service(provider=_mock_provider(True))
        assert resolve_cache_config().enabled is True

    def test_arbitrary_value_is_falsy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENABLE_PROMPT_CACHING_ENV_VAR, "nope")
        assert resolve_cache_config().enabled is False


class TestPlatformFlagAboveTheEnvFloor:
    """With the env var unset (defaults to enabled), the platform
    `ENABLE_PROMPT_CACHING` Labs flag is consulted and decides."""

    def test_flag_service_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ENABLE_PROMPT_CACHING_ENV_VAR, raising=False)
        FeatureFlagService.get_service(provider=_mock_provider(False))
        config = resolve_cache_config()
        assert config.enabled is False
        assert config.source == "feature_flag"

    def test_flag_service_enables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ENABLE_PROMPT_CACHING_ENV_VAR, raising=False)
        FeatureFlagService.get_service(provider=_mock_provider(True))
        config = resolve_cache_config()
        assert config.enabled is True
        assert config.source == "feature_flag"

    def test_flag_absent_from_provider_defaults_to_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Flag not present in etcd/env-file (e.g. never seeded) — the
        service's own `default=True` kicks in, not a hard False."""
        monkeypatch.delenv(ENABLE_PROMPT_CACHING_ENV_VAR, raising=False)
        FeatureFlagService.get_service(provider=_mock_provider(None))
        config = resolve_cache_config()
        assert config.enabled is True
        assert config.source == "feature_flag"


class TestFeatureFlagServiceUnavailable:
    """When `FeatureFlagService` cannot be consulted at all (raises), the
    env floor's own default is used and `source` stays "env" — this is the
    path exercised by every test elsewhere in the suite that constructs
    caching objects directly without any feature-flag wiring at all."""

    def test_exception_from_flag_service_falls_back_to_env_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(ENABLE_PROMPT_CACHING_ENV_VAR, raising=False)
        broken_provider = MagicMock(spec=IConfigProvider)
        broken_provider.get_flag_value = MagicMock(side_effect=RuntimeError("boom"))
        FeatureFlagService.get_service(provider=broken_provider)

        config = resolve_cache_config()
        assert config.enabled is True
        assert config.source == "env"
