"""Failure paths of the alert loop (loop.py) that the main loop tests do not reach."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import pytest

from app.services.sandbox_alerts import loop as loop_mod
from app.services.sandbox_alerts.config import AlertSettings
from app.services.sandbox_alerts.loop import SandboxAlertRunner, run_sandbox_alert_loop
from tests.unit.services.sandbox_alerts.fakes import FakeConfig, FakeProducer
from tests.unit.services.sandbox_alerts.test_loop import (
    CRIT_KEY,
    SETTINGS,
    SNAPSHOT,
    WARN_KEY,
    Env,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def _resolved(env: Env) -> list[dict]:
    return [e for _, e, _ in env.producer.sent if e["type"] == "SANDBOX_CAPACITY_RESOLVED"]


class WarningClaimFails(FakeConfig):
    """The warning claim made alongside a critical one fails; *written* says
    whether it reached the store before failing."""

    def __init__(self, clock: Callable[[], float], error: Exception, *, written: bool) -> None:
        super().__init__(clock)
        self.error = error
        self.written = written

    async def create_config_if_absent(self, key: str, value: object, *, ttl_seconds: int | None = None) -> bool:
        if key == WARN_KEY:
            if self.written:
                self._put(key, value, ttl_seconds)
            raise self.error
        return await super().create_config_if_absent(key, value, ttl_seconds=ttl_seconds)


class TestCriticalWarningClaim:
    async def test_a_failed_warning_claim_does_not_stop_the_critical_alert(self, env: Env) -> None:
        env.store = WarningClaimFails(env.clock, ConnectionError("down"), written=False)
        env.critical()
        await env.runner().run_once()
        assert [e["type"] for _, e, _ in env.producer.sent] == ["SANDBOX_CAPACITY_CRITICAL"]
        assert CRIT_KEY in env.store.values and WARN_KEY not in env.store.values

    async def test_a_timed_out_warning_claim_is_given_back_when_the_send_fails(self, env: Env) -> None:
        env.store = WarningClaimFails(env.clock, TimeoutError(), written=True)
        env.producer = FakeProducer(result=False)
        env.critical()
        await env.runner().run_once()
        assert env.producer.sent  # it was tried
        assert CRIT_KEY not in env.store.values
        assert WARN_KEY not in env.store.values

    async def test_a_timed_out_warning_claim_is_kept_when_the_send_works(self, env: Env) -> None:
        env.store = WarningClaimFails(env.clock, TimeoutError(), written=True)
        env.critical()
        await env.runner().run_once()
        assert [e["type"] for _, e, _ in env.producer.sent] == ["SANDBOX_CAPACITY_CRITICAL"]
        assert CRIT_KEY in env.store.values and WARN_KEY in env.store.values


class MarkFails(FakeConfig):
    async def set_config(self, key: str, value: object, *, ttl_seconds: int | None = None) -> bool:
        raise ConnectionError("store down")


class TestResolveFailures:
    async def _fire_then_clear(self, env: Env, runner: SandboxAlertRunner) -> None:
        env.warn()
        await runner.run_once()
        env.clock.now += 200
        env.quiet()
        await runner.run_once()  # clear from here

    async def _tick(self, env: Env, runner: SandboxAlertRunner, seconds: float = 120) -> None:
        env.clock.now += seconds
        env.quiet()
        await runner.run_once()

    async def test_an_unreadable_store_is_checked_again_next_time(self, env: Env) -> None:
        runner = env.runner()
        await self._fire_then_clear(env, runner)
        env.store.fail = True
        await self._tick(env, runner)
        assert _resolved(env) == []
        env.store.fail = False
        await self._tick(env, runner, 10)
        assert len(_resolved(env)) == 1
        assert env.store.values[WARN_KEY]["state"] == "resolved"

    async def test_a_failed_notice_is_sent_again_next_time_and_only_once(self, env: Env) -> None:
        runner = env.runner()
        await self._fire_then_clear(env, runner)
        env.producer.result = False
        await self._tick(env, runner)
        assert len(_resolved(env)) == 1  # tried, not accepted
        assert env.store.values[WARN_KEY]["state"] == "claimed"
        env.producer.result = True
        await self._tick(env, runner, 10)
        await self._tick(env, runner, 10)
        assert len(_resolved(env)) == 2  # one failed try, one sent
        assert env.store.values[WARN_KEY]["state"] == "resolved"

    async def test_a_claim_that_cannot_be_marked_resolved_is_logged_and_sent_once(
        self, env: Env, caplog: pytest.LogCaptureFixture,
    ) -> None:
        env.store = MarkFails(env.clock)
        runner = env.runner()
        await self._fire_then_clear(env, runner)
        with caplog.at_level(logging.WARNING):
            await self._tick(env, runner)
            await self._tick(env, runner, 10)
        assert len(_resolved(env)) == 1
        assert "could not mark resolved" in caplog.text


class TestLoop:
    async def test_no_state_directory_turns_the_alerts_off(
        self, env: Env, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        monkeypatch.setattr(
            "app.services.resource_governor.shared_slots.sandbox_state_directory", lambda: None,
        )
        with caplog.at_level(logging.INFO):
            await asyncio.wait_for(run_sandbox_alert_loop(env.store, settings=SETTINGS), 5)  # type: ignore[arg-type]
        assert "sandbox load alerts off: no POSIX file locks" in caplog.text

    async def test_failures_are_logged_and_the_loop_keeps_going(
        self, env: Env, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        errors = [OSError("read-only"), OSError("read-only"), ValueError("bad"), ValueError("bad")]
        calls = 0
        done = asyncio.Event()

        async def run_once(self: SandboxAlertRunner) -> None:
            nonlocal calls
            calls += 1
            if calls <= len(errors):
                raise errors[calls - 1]
            done.set()

        monkeypatch.setattr(SandboxAlertRunner, "run_once", run_once)
        fast = AlertSettings(**{**SETTINGS.__dict__, "eval_s": 0})
        with caplog.at_level(logging.WARNING):
            task = asyncio.create_task(run_sandbox_alert_loop(
                env.store, settings=fast, state_directory=env.state,  # type: ignore[arg-type]
                producer_factory=env.factory, snapshot=lambda: dict(SNAPSHOT),
            ))
            await asyncio.wait_for(done.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert caplog.text.count("state directory unusable") == 1
        assert caplog.text.count("evaluation failed") == 2

    async def test_the_leader_flag_follows_the_lock(self, env: Env) -> None:
        runner = env.runner()
        assert runner.is_leader is False
        env.quiet()
        await runner.run_once()
        assert runner.is_leader is True
        await runner.close()
        assert runner.is_leader is False


def test_the_default_snapshot_is_the_governors(monkeypatch: pytest.MonkeyPatch) -> None:
    class Governor:
        def snapshot(self) -> dict[str, Any]:
            return {"limit": 3}

    monkeypatch.setattr("app.agent_loop_lib.sandbox.governor.get_default_governor", lambda: Governor())
    assert loop_mod._default_snapshot() == {"limit": 3}


async def test_the_default_producer_is_built_and_initialised(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class Producer(FakeProducer):
        async def initialize(self) -> None:
            seen["initialised"] = True

    async def config_from_service(config_service: object, *, client_id: str) -> str:
        seen["client_id"] = client_id
        return "config"

    def create_producer(*, logger: logging.Logger, config: object) -> Producer:
        seen["config"] = config
        return Producer()

    monkeypatch.setattr(
        "app.services.messaging.utils.MessagingUtils.create_producer_config_from_service", config_from_service,
    )
    monkeypatch.setattr("app.services.messaging.messaging_factory.MessagingFactory.create_producer", create_producer)
    build = loop_mod.default_producer_factory(object(), logging.getLogger("t"))  # type: ignore[arg-type]
    producer = await build()
    assert isinstance(producer, Producer)
    assert seen == {"client_id": "sandbox_alerts", "config": "config", "initialised": True}
