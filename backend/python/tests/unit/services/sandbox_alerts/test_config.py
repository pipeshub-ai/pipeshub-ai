from __future__ import annotations

import logging

import pytest

from app.services.sandbox_alerts.config import AlertSettings


def test_defaults() -> None:
    assert AlertSettings.from_env({}) == AlertSettings()
    assert AlertSettings().scope == "install"


def test_blank_means_the_default() -> None:
    assert AlertSettings.from_env({"SANDBOX_ALERT_WINDOW_S": " ", "SANDBOX_ALERT_SCOPE": ""}) == AlertSettings()


@pytest.mark.parametrize(
    ("key", "raw", "field", "expected"),
    [
        ("SANDBOX_ALERT_WINDOW_S", "60", "window_s", 120),
        ("SANDBOX_ALERT_WINDOW_S", "9000", "window_s", 3600),
        ("SANDBOX_ALERT_EVAL_S", "1", "eval_s", 10),
        ("SANDBOX_ALERT_WARN_BUSY_PCT", "150", "warn_busy_pct", 100),
        ("SANDBOX_ALERT_WARN_MIN_WAITED", "0", "warn_min_waited", 1),
        ("SANDBOX_ALERT_CRIT_MIN_REFUSED", "-3", "crit_min_refused", 1),
        ("SANDBOX_ALERT_CRIT_REFUSED_PCT", "101", "crit_refused_pct", 100),
        ("SANDBOX_ALERT_COOLDOWN_S", "999999999", "cooldown_s", 604800),
    ],
)
def test_out_of_range_values_are_clamped_with_one_warning(
    caplog: pytest.LogCaptureFixture, key: str, raw: str, field: str, expected: float,
) -> None:
    with caplog.at_level(logging.WARNING):
        settings = AlertSettings.from_env({key: raw})
    assert getattr(settings, field) == expected
    assert sum(key in r.getMessage() for r in caplog.records) == 1


@pytest.mark.parametrize("raw", ["inf", "-inf", "nan", "1e999"])
@pytest.mark.parametrize(
    ("key", "field"),
    [("SANDBOX_ALERT_WARN_MIN_WAITED", "warn_min_waited"), ("SANDBOX_ALERT_WINDOW_S", "window_s"),
     ("SANDBOX_ALERT_WARN_BUSY_PCT", "warn_busy_pct")],
)
def test_a_value_that_is_not_finite_uses_the_default_with_one_warning(
    caplog: pytest.LogCaptureFixture, key: str, field: str, raw: str,
) -> None:
    with caplog.at_level(logging.WARNING):
        settings = AlertSettings.from_env({key: raw})
    assert getattr(settings, field) == getattr(AlertSettings(), field)
    assert sum(key in r.getMessage() for r in caplog.records) == 1


@pytest.mark.parametrize("raw", ["false", "off", "0", "FALSE"])
def test_alerts_can_be_turned_off(raw: str) -> None:
    assert AlertSettings.from_env({"SANDBOX_ALERTS": raw}).enabled is False


def test_a_short_cooldown_warns_it_is_for_testing(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        settings = AlertSettings.from_env({"SANDBOX_ALERT_COOLDOWN_S": "600"})
    assert settings.cooldown_s == 600
    assert any("testing only" in r.getMessage() for r in caplog.records)


def test_an_invalid_scope_falls_back_to_install(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert AlertSettings.from_env({"SANDBOX_ALERT_SCOPE": "pod/one"}).scope == "install"
    assert AlertSettings.from_env({"SANDBOX_ALERT_SCOPE": "pipeshub-7d9c-x2"}).scope == "pipeshub-7d9c-x2"
    assert any("SANDBOX_ALERT_SCOPE" in r.getMessage() for r in caplog.records)
