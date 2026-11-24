"""A configuration store or broker that hangs fails the evaluation, and the next one retries."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import pytest

from app.services.sandbox_alerts.loop import SandboxAlertRunner
from tests.unit.services.sandbox_alerts.fakes import FakeConfig, FakeProducer
from tests.unit.services.sandbox_alerts.test_loop import (
    SETTINGS,
    SNAPSHOT,
    WARN_KEY,
    Env,
)

if TYPE_CHECKING:
    from pathlib import Path

T = 0.05


class HangingConfig(FakeConfig):
    """Hangs ``create_config_if_absent`` while ``hang`` is set; with
    ``write_first`` the value is stored before it hangs, like a reply lost
    after the write."""

    def __init__(self, clock) -> None:
        super().__init__(clock)
        self.hang = False
        self.write_first = False

    async def create_config_if_absent(self, key: str, value: object, *, ttl_seconds: int | None = None) -> bool:
        if self.hang:
            if self.write_first:
                await super().create_config_if_absent(key, value, ttl_seconds=ttl_seconds)
            await asyncio.Event().wait()
        return await super().create_config_if_absent(key, value, ttl_seconds=ttl_seconds)


class HangingProducer(FakeProducer):
    def __init__(self) -> None:
        super().__init__()
        self.hang = False

    async def send_message(self, topic: str, message: dict, key: str | None = None) -> bool:
        if self.hang:
            await asyncio.Event().wait()
        return await super().send_message(topic, message, key)


@pytest.fixture
def env(tmp_path: Path) -> Env:
    e = Env(tmp_path)
    e.store = HangingConfig(e.clock)
    e.producer = HangingProducer()
    return e


def runner(env: Env, factory=None) -> SandboxAlertRunner:
    return SandboxAlertRunner(
        env.store, SETTINGS, env.state,  # type: ignore[arg-type]
        producer_factory=factory or env.factory, snapshot=lambda: dict(SNAPSHOT),
        clock=env.clock, host="test-host", store_timeout_s=T, publish_timeout_s=T,
    )


async def evaluate(env: Env, r: SandboxAlertRunner) -> float:
    env.clock.now += 10
    env.warn()
    started = time.monotonic()
    await asyncio.wait_for(r.run_once(), 5)
    return time.monotonic() - started


def warnings(caplog: pytest.LogCaptureFixture, text: str) -> int:
    return sum(r.levelno == logging.WARNING and text in r.getMessage() for r in caplog.records)


async def test_a_hanging_store_fails_fast_logs_once_and_sends_after_it_recovers(
    env: Env, caplog: pytest.LogCaptureFixture,
) -> None:
    r = runner(env)
    env.store.hang = True
    with caplog.at_level(logging.DEBUG):
        for _ in range(3):
            assert await evaluate(env, r) < 1
    assert env.producer.sent == []
    assert warnings(caplog, "config store unavailable") == 1
    env.store.hang = False
    await evaluate(env, r)
    await evaluate(env, r)
    assert len(env.producer.sent) == 1


async def test_a_claim_written_before_the_store_hung_is_given_back(env: Env) -> None:
    r = runner(env)
    env.store.hang = env.store.write_first = True
    await evaluate(env, r)
    assert WARN_KEY not in env.store.values
    env.store.hang = False
    await evaluate(env, r)
    assert [e["email"] for _, e, _ in env.producer.sent] == [True]


async def test_a_hanging_publish_releases_the_claim_and_rebuilds_the_producer(
    env: Env, caplog: pytest.LogCaptureFixture,
) -> None:
    r = runner(env)
    env.producer.hang = True
    with caplog.at_level(logging.DEBUG):
        for _ in range(3):
            assert await evaluate(env, r) < 1
    assert WARN_KEY not in env.store.values
    assert env.producer.cleaned
    assert warnings(caplog, "publish failed") == 1
    assert warnings(caplog, "not sent; retrying") == 1
    built = env.built
    env.producer.hang = False
    await evaluate(env, r)
    assert env.built == built + 1
    assert [e["type"] for _, e, _ in env.producer.sent] == ["SANDBOX_CAPACITY_WARNING"]


async def test_a_producer_that_never_connects_is_a_failed_send(env: Env) -> None:
    async def hanging_factory() -> FakeProducer:
        await asyncio.Event().wait()
        raise AssertionError

    r = runner(env, hanging_factory)
    assert await evaluate(env, r) < 1
    assert WARN_KEY not in env.store.values
    assert env.producer.sent == []
