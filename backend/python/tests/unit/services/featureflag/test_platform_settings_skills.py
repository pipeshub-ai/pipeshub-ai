"""`is_skills_enabled` / the shared `_platform_flag` helper it and
`is_actions_enabled` both call — the query service does not wire an
`EtcdProvider`, so `config_service.get_config(PLATFORM_SETTINGS_KEY, ...)`
is the primary (live, uncached) resolution path; `FeatureFlagService` is
only a fallback for services that DO have one."""

from __future__ import annotations

from unittest.mock import AsyncMock

from app.services.featureflag.platform_settings import (
    is_actions_enabled,
    is_skills_enabled,
)


def _config_service(settings: dict | None) -> AsyncMock:
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value=settings if settings is not None else {})
    return svc


class TestIsSkillsEnabled:
    async def test_defaults_to_true_when_settings_are_empty(self) -> None:
        assert await is_skills_enabled(_config_service({})) is True

    async def test_defaults_to_true_when_feature_flags_key_is_absent(self) -> None:
        assert await is_skills_enabled(_config_service({"other": "stuff"})) is True

    async def test_stored_false_wins_over_the_default(self) -> None:
        svc = _config_service({"featureFlags": {"ENABLE_SKILLS": False}})
        assert await is_skills_enabled(svc) is False

    async def test_stored_true_is_respected(self) -> None:
        svc = _config_service({"featureFlags": {"ENABLE_SKILLS": True}})
        assert await is_skills_enabled(svc) is True

    async def test_flag_key_is_case_insensitive_on_read(self) -> None:
        svc = _config_service({"featureFlags": {"enable_skills": False}})
        assert await is_skills_enabled(svc) is False

    async def test_a_read_failure_falls_back_to_the_default_true(self) -> None:
        svc = AsyncMock()
        svc.get_config = AsyncMock(side_effect=RuntimeError("kv store unavailable"))
        assert await is_skills_enabled(svc) is True

    async def test_reads_are_live_uncached(self) -> None:
        """Flipping the flag in Labs must take effect on the very next
        request, not after a service restart."""
        svc = _config_service({"featureFlags": {"ENABLE_SKILLS": False}})
        await is_skills_enabled(svc)
        _, kwargs = svc.get_config.call_args
        assert kwargs.get("use_cache") is False

    async def test_no_config_service_falls_back_to_feature_flag_service_or_default(self) -> None:
        """The connectors service path (no per-request `config_service`) —
        exercised via the `FeatureFlagService` fallback in `_platform_flag`;
        an unconfigured/unavailable service must not raise, just return the
        default."""
        assert await is_skills_enabled(None) is True


class TestSharedPlatformFlagHelperAlsoServesActions:
    """`is_actions_enabled` and `is_skills_enabled` are identical except
    flag name + default — both must go through the same `_platform_flag`
    helper (DRY), proven here by exercising `is_actions_enabled` too."""

    async def test_actions_defaults_to_true(self) -> None:
        assert await is_actions_enabled(_config_service({})) is True

    async def test_actions_stored_false_wins(self) -> None:
        svc = _config_service({"featureFlags": {"ENABLE_ACTIONS": False}})
        assert await is_actions_enabled(svc) is False

    async def test_actions_and_skills_are_independent_flags(self) -> None:
        svc = _config_service({"featureFlags": {"ENABLE_ACTIONS": False, "ENABLE_SKILLS": True}})
        assert await is_actions_enabled(svc) is False
        assert await is_skills_enabled(svc) is True
