"""`read_platform_feature_flag(..., fail_closed=True)`.

`ConfigurationService.get_config` answers `default` for a store outage unless it is
asked to raise, so for a flag whose default is on, "could not read" looks like "never
saved" and silently undoes an admin's opt-out. `fail_closed` asks it to raise and
answers False instead; without it the helper behaves exactly as before.
"""

from __future__ import annotations

from app.services.featureflag.platform_settings import (
    PLATFORM_SETTINGS_KEY,
    read_platform_feature_flag,
)


class _Store:
    """Same failure contract as `ConfigurationService.get_config`."""

    def __init__(self, settings: dict[str, object] | None = None, *, fail: bool = False) -> None:
        self._settings = settings
        self._fail = fail
        self.calls: list[dict[str, object]] = []

    async def get_config(
        self, key: str, default: object = None, use_cache: bool = False, *, raise_on_error: bool = False,
    ) -> object:
        self.calls.append({"key": key, "use_cache": use_cache, "raise_on_error": raise_on_error})
        if self._fail:
            if raise_on_error:
                raise RuntimeError("store unreachable")
            return default
        return self._settings if self._settings is not None else default


class TestFailClosed:
    async def test_asks_the_store_to_raise_instead_of_answering_the_default(self) -> None:
        store = _Store({"featureFlags": {"ENABLE_X": True}})
        await read_platform_feature_flag("ENABLE_X", store, default=True, fail_closed=True)
        assert store.calls == [{"key": PLATFORM_SETTINGS_KEY, "use_cache": False, "raise_on_error": True}]

    async def test_an_unreadable_store_answers_false_even_when_the_default_is_on(self) -> None:
        store = _Store({"featureFlags": {"ENABLE_X": False}}, fail=True)
        assert await read_platform_feature_flag("ENABLE_X", store, default=True, fail_closed=True) is False

    async def test_never_saved_settings_still_answer_the_default(self) -> None:
        assert await read_platform_feature_flag("ENABLE_X", _Store(), default=True, fail_closed=True) is True

    async def test_an_absent_flag_key_still_answers_the_default(self) -> None:
        store = _Store({"featureFlags": {"ENABLE_OTHER": False}})
        assert await read_platform_feature_flag("ENABLE_X", store, default=True, fail_closed=True) is True

    async def test_a_saved_value_is_respected(self) -> None:
        store = _Store({"featureFlags": {"enable_x": False}})
        assert await read_platform_feature_flag("ENABLE_X", store, default=True, fail_closed=True) is False


class TestDefaultBehaviourIsUnchanged:
    async def test_does_not_pass_raise_on_error_so_every_store_double_still_works(self) -> None:
        store = _Store({"featureFlags": {"ENABLE_X": True}})
        await read_platform_feature_flag("ENABLE_X", store, default=False)
        assert store.calls[0]["raise_on_error"] is False

    async def test_an_unreadable_store_still_answers_the_default(self) -> None:
        store = _Store(fail=True)
        assert await read_platform_feature_flag("ENABLE_X", store, default=True) is True
        assert await read_platform_feature_flag("ENABLE_X", store, default=False) is False
