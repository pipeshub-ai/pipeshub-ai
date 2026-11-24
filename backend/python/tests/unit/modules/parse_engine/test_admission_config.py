"""The parseAdmission numbers are live: re-read through a 30 s cache, validated, last good value kept."""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.modules.parse_engine.admission import (
    ADMISSION_CONFIG_KEY,
    AdmissionNumbers,
    LiveAdmissionConfig,
)
from tests.support.in_memory_config import InMemoryConfigService

if TYPE_CHECKING:
    import pytest


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class _Store(InMemoryConfigService):
    def __init__(self) -> None:
        super().__init__()
        self.down = False
        self.reads = 0
        self.kwargs: dict = {}

    async def get_config(self, key, default=None, use_cache=True, **kw) -> object:
        self.reads += 1
        self.kwargs = {"use_cache": use_cache, **kw}
        if self.down:
            raise ConnectionError("store down")
        return await super().get_config(key, default, use_cache)


async def test_no_key_means_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARSE_ADMISSION_AGING_S", "600")
    live = LiveAdmissionConfig(_Store(), clock=_Clock())
    assert (await live.refresh()).aging_s == 600.0
    assert live.peek().aging_s == 600.0


async def test_live_reload_after_the_cache_window() -> None:
    store, clock = _Store(), _Clock()
    live = LiveAdmissionConfig(store, env_defaults=AdmissionNumbers(), clock=clock)
    assert (await live.refresh()).large_min == 0.20
    store.values[ADMISSION_CONFIG_KEY] = {"largeMin": 0.1, "agedMin": 0.3}
    clock.t += 10
    assert (await live.refresh()).large_min == 0.20  # inside the 30 s window: no read
    assert store.reads == 1
    clock.t += 21
    n = await live.refresh()
    assert (n.large_min, n.aged_min) == (0.1, 0.3)
    assert store.kwargs["max_age_seconds"] == 30.0 and store.kwargs["raise_on_error"] is True


async def test_invalid_save_is_rejected_and_the_old_value_kept() -> None:
    store, clock = _Store(), _Clock()
    store.values[ADMISSION_CONFIG_KEY] = {"largeMin": 0.1, "agedMin": 0.3}
    live = LiveAdmissionConfig(store, env_defaults=AdmissionNumbers(), clock=clock)
    assert (await live.refresh()).large_min == 0.1
    store.values[ADMISSION_CONFIG_KEY] = {"largeMin": 0.9}  # above aged_min
    clock.t += 31
    n = await live.refresh()
    assert n.large_min == 0.1
    assert live.last_error is not None and live.last_error.startswith("rejected")
    store.values[ADMISSION_CONFIG_KEY] = {"largeMin": 0.15, "agedMin": 0.3}
    clock.t += 31
    assert (await live.refresh()).large_min == 0.15 and live.last_error is None


async def test_store_down_keeps_the_last_good_value() -> None:
    store, clock = _Store(), _Clock()
    store.values[ADMISSION_CONFIG_KEY] = {"largeMin": 0.1, "agedMin": 0.3}
    live = LiveAdmissionConfig(store, env_defaults=AdmissionNumbers(), clock=clock)
    await live.refresh()
    store.down = True
    clock.t += 31
    assert (await live.refresh()).large_min == 0.1
    assert live.last_error is not None and "unreadable" in live.last_error
    # and it does not hammer a store that is down: the next read waits out the retry gap
    reads = store.reads
    clock.t += 1
    await live.refresh()
    assert store.reads == reads
    clock.t += 5
    store.down = False
    store.values[ADMISSION_CONFIG_KEY] = {"largeMin": 0.12, "agedMin": 0.3}
    assert (await live.refresh()).large_min == 0.12


async def test_store_down_at_start_uses_env_defaults() -> None:
    store = _Store()
    store.down = True
    live = LiveAdmissionConfig(store, env_defaults=AdmissionNumbers(aging_s=700.0), clock=_Clock())
    assert (await live.refresh()).aging_s == 700.0


async def test_removing_the_key_returns_to_env_defaults() -> None:
    store, clock = _Store(), _Clock()
    store.values[ADMISSION_CONFIG_KEY] = {"agingS": 500}
    live = LiveAdmissionConfig(store, env_defaults=AdmissionNumbers(), clock=clock)
    assert (await live.refresh()).aging_s == 500
    del store.values[ADMISSION_CONFIG_KEY]
    clock.t += 31
    assert (await live.refresh()).aging_s == 900.0


async def test_no_config_service_is_just_env_defaults() -> None:
    live = LiveAdmissionConfig(None, env_defaults=AdmissionNumbers())
    assert await live.refresh() == AdmissionNumbers()
