"""A failed resolve mark is tried again without a second back-to-normal notice."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.unit.services.sandbox_alerts.test_loop import WARN_KEY, Env

if TYPE_CHECKING:
    from pathlib import Path


def _resolved(env: Env) -> list[dict]:
    return [e for _, e, _ in env.producer.sent if e["type"] == "SANDBOX_CAPACITY_RESOLVED"]


async def _tick(env: Env, runner, seconds: float, kind: str = "quiet") -> None:
    env.clock.now += seconds
    getattr(env, kind)()
    await runner.run_once()


class _FailingSet:
    """``set_config`` reports failure (without raising) the first *n* times."""

    def __init__(self, env: Env, n: int, *, raise_: bool = False) -> None:
        self.left = n
        self.raise_ = raise_
        self.calls = 0
        self._real = env.store.set_config

    async def __call__(self, key, value, *, ttl_seconds=None) -> bool:
        self.calls += 1
        if self.left > 0:
            self.left -= 1
            if self.raise_:
                raise ConnectionError("store down")
            return False
        return await self._real(key, value, ttl_seconds=ttl_seconds)


async def _fire_then_clear(env: Env, failing: _FailingSet) -> object:
    runner = env.runner()
    env.warn()
    await runner.run_once()
    env.store.set_config = failing  # type: ignore[method-assign]
    await _tick(env, runner, 200)
    await _tick(env, runner, 120)
    return runner


async def test_a_mark_that_returns_false_is_retried_without_a_second_notice(tmp_path: Path) -> None:
    env = Env(tmp_path)
    failing = _FailingSet(env, 1)
    runner = await _fire_then_clear(env, failing)
    assert len(_resolved(env)) == 1
    assert env.store.values[WARN_KEY]["state"] == "claimed"
    await _tick(env, runner, 10)
    assert failing.calls == 2
    assert len(_resolved(env)) == 1
    assert env.store.values[WARN_KEY]["state"] == "resolved"
    # A new overload is reported again the same day.
    await _tick(env, runner, 3600, "warn")
    assert [e["type"] for _, e, _ in env.producer.sent][-1] == "SANDBOX_CAPACITY_WARNING"


async def test_a_mark_that_raises_is_retried_without_a_second_notice(tmp_path: Path) -> None:
    env = Env(tmp_path)
    failing = _FailingSet(env, 2, raise_=True)
    runner = await _fire_then_clear(env, failing)
    await _tick(env, runner, 10)
    assert env.store.values[WARN_KEY]["state"] == "claimed"
    await _tick(env, runner, 10)
    assert env.store.values[WARN_KEY]["state"] == "resolved"
    assert len(_resolved(env)) == 1
    await _tick(env, runner, 10)
    assert failing.calls == 3


async def test_a_new_overload_before_the_retry_sends_a_fresh_notice_later(tmp_path: Path) -> None:
    env = Env(tmp_path)
    failing = _FailingSet(env, 1)
    runner = await _fire_then_clear(env, failing)
    await _tick(env, runner, 10, "warn")  # the claim is still held: no new alert
    assert len(_resolved(env)) == 1
    await _tick(env, runner, 200)
    await _tick(env, runner, 120)
    assert len(_resolved(env)) == 2
    assert env.store.values[WARN_KEY]["state"] == "resolved"
