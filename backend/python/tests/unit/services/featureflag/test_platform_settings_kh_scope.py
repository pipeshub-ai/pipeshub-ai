"""The ENABLE_KH_SCOPE_LISTING gate: off by default, read through the config cache."""

from __future__ import annotations

from typing import Any

from app.services.featureflag.platform_settings import (
    PLATFORM_SETTINGS_KEY,
    is_kh_scope_listing_enabled,
)


class Config:
    def __init__(self, settings: Any = None, *, fail: bool = False) -> None:  # noqa: ANN401
        self.settings, self.fail, self.calls = settings, fail, []

    async def get_config(self, key: str, default: Any = None, use_cache: bool = False) -> Any:  # noqa: ANN401
        self.calls.append((key, use_cache))
        if self.fail:
            raise RuntimeError("store down")
        return self.settings if self.settings is not None else default


async def test_off_until_labs_turns_it_on() -> None:
    assert await is_kh_scope_listing_enabled(Config()) is False
    assert await is_kh_scope_listing_enabled(Config({"featureFlags": {}})) is False
    assert await is_kh_scope_listing_enabled(None) is False


async def test_on_when_labs_saved_it_on_in_any_case() -> None:
    assert await is_kh_scope_listing_enabled(Config({"featureFlags": {"ENABLE_KH_SCOPE_LISTING": True}})) is True
    assert await is_kh_scope_listing_enabled(Config({"featureFlags": {"enable_kh_scope_listing": True}})) is True
    assert await is_kh_scope_listing_enabled(Config({"featureFlags": {"ENABLE_KH_SCOPE_LISTING": False}})) is False


async def test_read_through_the_cache_and_off_when_the_store_fails() -> None:
    config = Config({"featureFlags": {"ENABLE_KH_SCOPE_LISTING": True}})
    await is_kh_scope_listing_enabled(config)
    assert config.calls == [(PLATFORM_SETTINGS_KEY, True)]
    assert await is_kh_scope_listing_enabled(Config(fail=True)) is False
