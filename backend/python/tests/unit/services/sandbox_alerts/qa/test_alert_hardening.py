"""The scope that names the cooldown keys, a release that keeps failing, and
envelope settings that make Node drop every alert."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import pytest

from app.services.sandbox_alerts.config import AlertSettings
from app.services.sandbox_alerts.cooldown import AlertCooldown
from app.services.sandbox_alerts.loop import (
    envelope_blocks_alerts,
    run_sandbox_alert_loop,
)
from tests.unit.services.sandbox_alerts.test_loop import SETTINGS, SNAPSHOT, Env

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


@pytest.mark.parametrize("scope", [".", "..", " .. "])
def test_a_dot_scope_is_replaced_by_the_default(scope: str, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert AlertSettings.from_env({"SANDBOX_ALERT_SCOPE": scope}).scope == "install"
    warnings = [r for r in caplog.records if "SANDBOX_ALERT_SCOPE" in r.getMessage()]
    assert len(warnings) == 1


@pytest.mark.parametrize("scope", ["...", ".a", "a..b", "_x", "pod-1.ns"])
def test_other_scopes_with_dots_are_kept(scope: str) -> None:
    assert AlertSettings.from_env({"SANDBOX_ALERT_SCOPE": scope}).scope == scope


async def test_a_failing_release_logs_one_line_without_a_traceback(
    env: Env, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env.producer.result = False  # every send fails, so every claim is released

    async def broken_release(self: AlertCooldown, *args: object, **kwargs: object) -> None:
        raise ConnectionError("store down")

    monkeypatch.setattr(AlertCooldown, "release_if_ours", broken_release)
    runner = env.runner()
    with caplog.at_level(logging.DEBUG):
        for _ in range(3):
            env.clock.now += 10
            env.warn()
            await runner.run_once()
    lines = [r for r in caplog.records if "could not release the claim" in r.getMessage()]
    assert lines, "the failed release is logged"
    assert not any(r.exc_info for r in lines)
    assert sum(r.levelno == logging.WARNING for r in lines) == 1
    assert "store down" in lines[0].getMessage()


async def test_a_release_that_works_again_warns_again_on_the_next_failure(
    env: Env, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env.producer.result = False
    failing = True
    original = AlertCooldown.release_if_ours

    async def flaky_release(self: AlertCooldown, *args: object, **kwargs: object) -> None:
        if failing:
            raise ConnectionError("store down")
        await original(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(AlertCooldown, "release_if_ours", flaky_release)
    runner = env.runner()
    with caplog.at_level(logging.WARNING):
        env.warn()
        await runner.run_once()
        failing = False
        env.clock.now += SETTINGS.cooldown_s
        env.warn()
        await runner.run_once()
        failing = True
        env.clock.now += 10
        env.warn()
        await runner.run_once()
    warnings = [r for r in caplog.records if "could not release the claim" in r.getMessage()]
    assert len(warnings) == 2


@pytest.mark.parametrize(
    ("env_vars", "why"),
    [
        ({"ENVELOPE_CTX_REQUIRED": "true"}, "ENVELOPE_CTX_REQUIRED"),
        ({"ENVELOPE_SIGN": "true", "ORG_CONTEXT_MODE": "enforce"}, "ORG_CONTEXT_MODE=enforce"),
    ],
)
def test_envelope_settings_that_drop_alerts_are_named(
    monkeypatch: pytest.MonkeyPatch, env_vars: dict[str, str], why: str,
) -> None:
    for key in ("ENVELOPE_CTX_REQUIRED", "ENVELOPE_SIGN", "ORG_CONTEXT_MODE"):
        monkeypatch.delenv(key, raising=False)
    assert envelope_blocks_alerts() is None
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)
    assert why in (envelope_blocks_alerts() or "")


@pytest.mark.parametrize(
    "env_vars",
    [
        {"ENVELOPE_SIGN": "true"},  # shadow mode accepts unsigned messages
        {"ORG_CONTEXT_MODE": "enforce"},  # without signing, unsigned messages are accepted
        {"ENVELOPE_SIGN": "true", "ORG_CONTEXT_MODE": "bogus"},
    ],
)
def test_settings_that_still_deliver_alerts(monkeypatch: pytest.MonkeyPatch, env_vars: dict[str, str]) -> None:
    for key in ("ENVELOPE_CTX_REQUIRED", "ENVELOPE_SIGN", "ORG_CONTEXT_MODE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)
    assert envelope_blocks_alerts() is None


async def test_the_loop_says_once_at_start_that_alerts_will_be_dropped(
    env: Env, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("SANDBOX_MODE", "docker")
    monkeypatch.delenv("SANDBOX_ALERTS", raising=False)
    monkeypatch.setenv("ENVELOPE_CTX_REQUIRED", "true")
    with caplog.at_level(logging.ERROR):
        task = asyncio.create_task(run_sandbox_alert_loop(
            env.store, settings=SETTINGS, state_directory=env.state,  # type: ignore[arg-type]
            producer_factory=env.factory, snapshot=lambda: dict(SNAPSHOT),
        ))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    errors = [r for r in caplog.records if "will not be delivered" in r.getMessage()]
    assert len(errors) == 1
    assert "ENVELOPE_CTX_REQUIRED" in errors[0].getMessage()
