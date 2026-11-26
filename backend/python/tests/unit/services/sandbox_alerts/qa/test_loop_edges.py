"""Failures around the claim and the publish, restarts and flapping load."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from app.services.sandbox_alerts.config import AlertSettings
from app.services.sandbox_alerts.cooldown import AlertCooldown
from tests.unit.services.sandbox_alerts.test_loop import (
    CRIT_KEY,
    SETTINGS,
    WARN_KEY,
    Env,
)

if TYPE_CHECKING:
    from pathlib import Path

    from tests.unit.services.sandbox_alerts.fakes import FakeProducer


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def types(env: Env) -> list[str]:
    return [e["type"] for _, e, _ in env.producer.sent]


async def test_a_leader_that_died_after_its_claim_is_not_doubled_by_the_next(env: Env) -> None:
    """At most once: the claim of a worker killed before it published keeps the
    next leader quiet until the cooldown ends (the alert is lost, not sent twice)."""
    await AlertCooldown(env.store, "install", SETTINGS.cooldown_s, clock=env.clock, host="h").claim(  # type: ignore[arg-type]
        "warning", "lost-alert",
    )
    runner = env.runner()
    env.warn()
    await runner.run_once()
    assert env.producer.sent == []
    env.clock.now += SETTINGS.cooldown_s
    env.warn()
    await runner.run_once()
    assert types(env) == ["SANDBOX_CAPACITY_WARNING"]


async def test_restarting_every_worker_during_an_incident_sends_nothing_new(env: Env) -> None:
    env.critical()
    first = env.runner()
    await first.run_once()
    await first.close()
    for _ in range(3):
        env.clock.now += 10
        env.critical()
        runner = env.runner()
        await runner.run_once()
        await runner.close()
    assert types(env) == ["SANDBOX_CAPACITY_CRITICAL"]


async def test_a_broker_that_is_down_releases_the_claim_and_a_later_try_sends_once(env: Env) -> None:
    attempts = 0

    async def factory() -> FakeProducer:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("broker down")
        return env.producer

    from app.services.sandbox_alerts.loop import SandboxAlertRunner

    runner = SandboxAlertRunner(
        env.store, SETTINGS, env.state,  # type: ignore[arg-type]
        producer_factory=factory, snapshot=lambda: {"limit": 1, "ceiling": 1, "mode": "compose"},
        clock=env.clock, host="test-host",
    )
    env.critical()
    await runner.run_once()
    assert env.store.values == {}  # both the critical and the warning claim given back
    env.clock.now += 10
    env.critical()
    await runner.run_once()
    env.clock.now += 10
    env.critical()
    await runner.run_once()
    assert types(env) == ["SANDBOX_CAPACITY_CRITICAL"]
    assert attempts == 2


async def test_a_store_that_recovers_sends_once(env: Env) -> None:
    runner = env.runner()
    env.store.fail = True
    env.warn()
    await runner.run_once()
    env.store.fail = False
    for _ in range(3):
        env.clock.now += 10
        env.warn()
        await runner.run_once()
    assert types(env) == ["SANDBOX_CAPACITY_WARNING"]


async def test_a_store_lost_between_claim_and_release_does_not_break_the_loop(
    env: Env, caplog: pytest.LogCaptureFixture,
) -> None:
    env.producer.result = False
    original = env.store.get_config

    reads = 0

    async def failing_get(*args, **kwargs):  # noqa: ANN202
        nonlocal reads
        reads += 1
        if reads > 1:  # the first read (the critical level's last email) works
            env.store.fail = True
        return await original(*args, **kwargs)

    env.store.get_config = failing_get  # type: ignore[method-assign]
    env.warn()
    with caplog.at_level(logging.WARNING):
        await env.runner().run_once()
    assert any("could not release the claim" in r.getMessage() for r in caplog.records)
    # The claim stays, so this level is quiet until it expires.
    assert WARN_KEY in env.store.values


async def test_load_that_flaps_without_a_full_clear_window_alerts_once_a_day(env: Env) -> None:
    runner = env.runner()
    # A day of 5 min cycles: 4 min overloaded, then quiet for less than the 2 min window.
    for _ in range(24 * 12):
        for _ in range(24):
            env.clock.now += 10
            env.warn()
            await runner.run_once()
        for _ in range(6):
            env.clock.now += 10
            env.quiet()
            await runner.run_once()
    assert types(env).count("SANDBOX_CAPACITY_WARNING") == 1
    assert "SANDBOX_CAPACITY_RESOLVED" not in types(env)


async def test_a_resolved_alert_fires_again_the_same_day_in_app_only(env: Env) -> None:
    runner = env.runner()
    env.warn()
    await runner.run_once()
    for _ in range(40):  # a full clear window and more
        env.clock.now += 10
        env.quiet()
        await runner.run_once()
    env.clock.now += 10
    env.warn()
    await runner.run_once()
    assert types(env) == ["SANDBOX_CAPACITY_WARNING", "SANDBOX_CAPACITY_RESOLVED", "SANDBOX_CAPACITY_WARNING"]
    assert [e["email"] for _, e, _ in env.producer.sent] == [True, False, False]


async def test_a_scope_of_its_own_does_not_share_the_cooldown(env: Env) -> None:
    first = env.runner()
    env.warn()
    await first.run_once()
    await first.close()
    other = AlertSettings(**{**SETTINGS.__dict__, "scope": "pod-b.2_x"})
    env.clock.now += 10
    env.warn()
    await env.runner(other).run_once()
    assert types(env) == ["SANDBOX_CAPACITY_WARNING", "SANDBOX_CAPACITY_WARNING"]
    assert "/services/sandbox/alerts/pod-b.2_x/warning" in env.store.values
    assert CRIT_KEY not in env.store.values
