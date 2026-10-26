"""The shared sweep loop: how long it waits after each kind of tick."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from app.modules.indexing.connector_sweep import run_connector_sweep_loop


class _Lock:
    async def release(self) -> None:
        pass

    async def close(self) -> None:
        pass


class _Sweep:
    def __init__(self, outcomes: list[str]) -> None:
        self.outcomes = outcomes
        self.lock = None

    async def tick(self) -> str:
        return self.outcomes.pop(0)


@pytest.mark.asyncio
@pytest.mark.parametrize(("outcome", "interval"), [("page", 5.0), ("waiting", 5.0), ("deferred", 1800.0)])
async def test_the_wait_after_a_tick_follows_its_outcome(outcome: str, interval: float) -> None:
    sleep = AsyncMock()

    async def make_lock() -> _Lock:
        return _Lock()

    sweep = _Sweep([outcome, "idle"])

    async def make_sweep(_lock: _Lock) -> _Sweep:
        return sweep

    await run_connector_sweep_loop(
        logger=logging.getLogger("sweep-test"),
        name="sweep-test",
        make_lock=make_lock,
        make_sweep=make_sweep,
        startup_grace_seconds=0.0,
        busy_interval_seconds=5.0,
        idle_interval_seconds=600.0,
        deferred_interval_seconds=1800.0,
        error_interval_seconds=60.0,
        sleep=sleep,
    )

    assert [c.args[0] for c in sleep.await_args_list] == [0.0, interval]
