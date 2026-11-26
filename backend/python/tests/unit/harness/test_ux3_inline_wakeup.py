"""UX3 ST-6, LAT1 NT-8: the query service's inline driver hears a Stop and a
decision over Redis, and its claimer hears run-ready.

The wake reaches the model call or reads in flight (the cancel watcher), and
an approval is held in the run instead of suspending it at once.
"""

from __future__ import annotations

from typing import Any

import pytest

import app.harness.inline as inline_module


class _Journal:
    def __init__(self, **kwargs: Any) -> None:  # noqa: ANN401
        pass

    async def claim(self, run_id: str | None = None, *, worker_id: str, org_id: str | None = None) -> None:
        return None

    async def aclose(self) -> None:
        return None


class _Live:
    wakeup = object()

    ready = object()

    def interaction_wakeup(self) -> object:
        return self.wakeup

    def run_ready_signal(self) -> object:
        return self.ready

    def activity_bus(self) -> object:
        return self

    async def aclose(self) -> None:
        return None


class _Config:
    async def get_config(self, *_: Any, **__: Any) -> dict[str, str]:  # noqa: ANN401
        return {"current": "phw_node"}


class _Container:
    def config_service(self) -> _Config:
        return _Config()


@pytest.mark.parametrize("live", [_Live(), None])
async def test_st6_the_inline_driver_is_built_with_the_cancel_wakeup(
    monkeypatch: pytest.MonkeyPatch, live: _Live | None
) -> None:
    built: dict[str, Any] = {}

    async def create(_config: object) -> _Live | None:
        return live

    def driver(**kwargs: Any) -> object:  # noqa: ANN401
        built.update(kwargs)
        return object()

    claimed: dict[str, Any] = {}

    class _Claimer:
        def __init__(self, **kwargs: Any) -> None:  # noqa: ANN401
            claimed.update(kwargs)

        def start(self) -> None:
            return None

        async def claim_run(self, org_id: str, run_id: str) -> bool:
            return False

        async def stop(self) -> None:
            return None

    monkeypatch.setattr(inline_module, "NodeJournal", _Journal)
    monkeypatch.setattr(inline_module.RedisLiveBus, "create", staticmethod(create))
    monkeypatch.setattr(inline_module, "RunDriver", driver)
    monkeypatch.setattr(inline_module, "RunClaimer", _Claimer)
    inline = inline_module.InlineClaimer(_Container())
    assert await inline.hint("org-1", "run-1") is False
    assert built["cancel_wakeup"] is (_Live.wakeup if live is not None else None)
    assert built["interactions"] is (_Live.wakeup if live is not None else None)
    assert claimed["ready"] is (_Live.ready if live is not None else None)
    inline._ready = None  # the fake signal has no connection to close
    await inline.aclose()
