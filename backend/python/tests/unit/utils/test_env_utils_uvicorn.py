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


@pytest.mark.parametrize(
    ("value", "expected"),
    [("", 60), ("soon", 60), ("0", 5), ("-3", 5), ("2", 5)],
)
def test_a_blank_or_unreadable_value_uses_the_default_and_a_small_one_stops_at_uvicorns_own_five(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: int,
) -> None:
    monkeypatch.setenv(_NAME, value)
    assert uvicorn_worker_healthcheck_timeout() == expected
