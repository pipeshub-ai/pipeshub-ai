"""Any environment gives usable settings, and the levels match their definitions."""

from __future__ import annotations

import re

from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.sandbox_alerts.config import AlertSettings
from app.services.sandbox_alerts.cooldown import AlertCooldown
from app.services.sandbox_alerts.evaluator import evaluate
from app.services.sandbox_alerts.window import WindowTotals

KEYS = (
    "SANDBOX_ALERTS", "SANDBOX_ALERT_WINDOW_S", "SANDBOX_ALERT_EVAL_S", "SANDBOX_ALERT_WARN_BUSY_PCT",
    "SANDBOX_ALERT_WARN_MIN_WAITED", "SANDBOX_ALERT_CRIT_MIN_REFUSED", "SANDBOX_ALERT_CRIT_REFUSED_PCT",
    "SANDBOX_ALERT_COOLDOWN_S", "SANDBOX_ALERT_SCOPE",
)
VALUES = st.one_of(
    st.text(max_size=12),
    st.sampled_from(["", " ", "nan", "inf", "-inf", "1e400", "-1", "0", "0.5", "1e9", "true", "OFF", "a/b",
                     "../x", "x" * 64, "pod-1.a_b", "١٢٣", "1_000"]),
    st.floats(allow_nan=True, allow_infinity=True).map(str),
    st.integers(min_value=-10**12, max_value=10**12).map(str),
)


@given(env=st.dictionaries(st.sampled_from(KEYS), VALUES))
@settings(max_examples=400, deadline=None)
def test_any_environment_gives_settings_in_range(env: dict[str, str]) -> None:
    s = AlertSettings.from_env(env)
    assert 120 <= s.window_s <= 3600
    assert 10 <= s.eval_s <= 600
    assert 1 <= s.warn_busy_pct <= 100
    assert s.warn_min_waited >= 1 and s.crit_min_refused >= 1
    assert 0 <= s.crit_refused_pct <= 100
    assert 60 <= s.cooldown_s <= 604800
    assert re.fullmatch(r"[A-Za-z0-9._-]{1,63}", s.scope)
    key = AlertCooldown(None, s.scope, s.cooldown_s, host="h").key("warning")  # type: ignore[arg-type]
    assert key.count("/") == 5 and ".." not in key.split("/")


@given(
    busy=st.integers(0, 400), waited=st.integers(0, 10**6), refused=st.integers(0, 10**6),
    runs=st.integers(0, 10**6), window_s=st.integers(120, 3600),
    busy_pct=st.floats(1, 100), min_waited=st.integers(1, 1000),
    min_refused=st.integers(1, 1000), refused_pct=st.floats(0, 100),
)
@settings(max_examples=400, deadline=None)
def test_levels_match_their_definitions(
    busy: int, waited: int, refused: int, runs: int, window_s: int,
    busy_pct: float, min_waited: int, min_refused: int, refused_pct: float,
) -> None:
    runs = max(runs, refused)
    s = AlertSettings(window_s=window_s, warn_busy_pct=busy_pct, warn_min_waited=min_waited,
                      crit_min_refused=min_refused, crit_refused_pct=refused_pct)
    d = evaluate(WindowTotals(window_s=window_s, busy_ticks=busy, waited=waited, refused=refused, runs=runs), s)
    ticks = window_s // 10
    assert 0 <= d.busy_percent <= 100
    assert 0 <= d.refused_percent <= 100
    assert d.warning == (min(busy, ticks) * 100 >= busy_pct * ticks and waited >= min_waited)
    assert d.critical == (refused >= min_refused and refused * 100 >= refused_pct * runs)
    assert d.level == ("critical" if d.critical else "warning" if d.warning else None)


def test_thresholds_met_exactly_fire() -> None:
    s = AlertSettings()  # 60 % of 180 ticks, 30 waited; 10 refused and 5 %
    at = evaluate(WindowTotals(window_s=1800, busy_ticks=108, waited=30, refused=10, runs=200), s)
    assert at.warning and at.critical
    below = evaluate(WindowTotals(window_s=1800, busy_ticks=107, waited=30, refused=10, runs=201), s)
    assert not below.warning and not below.critical
