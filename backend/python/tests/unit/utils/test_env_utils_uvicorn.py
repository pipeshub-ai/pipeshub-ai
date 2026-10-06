"""The worker health-check timeout every service passes to uvicorn."""

from __future__ import annotations

import pytest

from app.utils.env_utils import uvicorn_worker_healthcheck_timeout

_NAME = "UVICORN_WORKER_HEALTHCHECK_TIMEOUT_SECONDS"


def test_default_gives_a_starting_or_busy_worker_a_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_NAME, raising=False)
    assert uvicorn_worker_healthcheck_timeout() == 60


def test_the_setting_overrides_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_NAME, "180")
    assert uvicorn_worker_healthcheck_timeout() == 180


@pytest.mark.parametrize("value", ["", "soon", "0", "-3", "2"])
def test_a_blank_unreadable_or_too_small_value_never_goes_below_uvicorns_own_five_seconds(
    monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    monkeypatch.setenv(_NAME, value)
    assert uvicorn_worker_healthcheck_timeout() in (5, 60)
    assert uvicorn_worker_healthcheck_timeout() >= 5
