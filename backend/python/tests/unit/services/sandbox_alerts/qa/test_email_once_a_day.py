"""In-app on every new overload, email at most once per level per cooldown."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.services.sandbox_alerts.cooldown import AlertCooldown
from tests.unit.services.sandbox_alerts.test_loop import (
    CRIT_KEY,
    SETTINGS,
    WARN_KEY,
    Env,
)

if TYPE_CHECKING:
    from pathlib import Path

    from app.services.sandbox_alerts.loop import SandboxAlertRunner

DAY = SETTINGS.cooldown_s


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def alerts(env: Env) -> list[tuple[str, bool]]:
    return [(e["payload"]["level"], e["email"]) for _, e, _ in env.producer.sent
            if e["type"] != "SANDBOX_CAPACITY_RESOLVED"]


def resolved(env: Env) -> int:
    return sum(e["type"] == "SANDBOX_CAPACITY_RESOLVED" for _, e, _ in env.producer.sent)


async def cycle(env: Env, runner: SandboxAlertRunner, kind: str, *, gap_s: float = 3600) -> None:
    """Overload of *kind*, then quiet for longer than a window (back to normal)."""
    env.clock.now += 10
    getattr(env, kind)()
    await runner.run_once()
    for _ in range(int(gap_s // 10)):
        env.clock.now += 10
        env.quiet()
        await runner.run_once()


async def test_overloads_that_return_within_a_day_alert_in_app_each_time_and_email_once(env: Env) -> None:
    runner = env.runner()
    for _ in range(5):
        await cycle(env, runner, "warn")
    assert alerts(env) == [("warning", True)] + [("warning", False)] * 4
    assert resolved(env) == 5
    # The day since the email has passed: the next one is emailed again.
    env.clock.now += DAY - 5 * 3600
    await cycle(env, runner, "warn")
    assert alerts(env)[-1] == ("warning", True)


async def test_each_level_emails_once_a_day_and_critical_counts_for_warning(env: Env) -> None:
    runner = env.runner()
    for kind in ("warn", "critical", "warn", "critical", "warn", "critical"):
        await cycle(env, runner, kind)
    assert alerts(env) == [
        ("warning", True), ("critical", True),
        ("warning", False), ("critical", False), ("warning", False), ("critical", False),
    ]
    emails = [level for level, email in alerts(env) if email]
    assert emails == ["warning", "critical"]


async def test_a_critical_email_stops_a_warning_email_later_that_day(env: Env) -> None:
    runner = env.runner()
    await cycle(env, runner, "critical")
    await cycle(env, runner, "warn")
    assert alerts(env) == [("critical", True), ("warning", False)]


async def test_a_failed_send_after_taking_over_keeps_the_last_email_time(env: Env) -> None:
    runner = env.runner()
    await cycle(env, runner, "warn")
    resolved_value = dict(env.store.values[WARN_KEY])
    assert resolved_value["state"] == "resolved"
    env.producer.result = False
    env.clock.now += 10
    env.warn()
    await runner.run_once()
    assert env.store.values[WARN_KEY] == resolved_value  # put back, not deleted
    env.producer.result = True
    env.clock.now += 10
    env.warn()
    await runner.run_once()
    assert alerts(env)[-1] == ("warning", False)


async def test_a_claim_stored_before_email_times_were_kept_counts_from_its_claim(env: Env) -> None:
    c = AlertCooldown(env.store, "install", DAY, clock=env.clock, host="h")  # type: ignore[arg-type]
    assert await c.claim("critical", "old")
    old = dict(env.store.values[CRIT_KEY])
    del old["emailedAt"]
    old["state"] = "resolved"
    env.store.values[CRIT_KEY] = old
    env.clock.now += 3600
    claim = await c.claim_alert("critical", "new")
    assert claim is not None and claim.email is False
    assert env.store.values[CRIT_KEY]["emailedAt"] == int((env.clock.now - 3600) * 1000)
