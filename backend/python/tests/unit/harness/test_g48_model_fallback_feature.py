"""G-48 (C20.49) G48-6, WP100-FB-1: a worker advertises ``model_fallback`` while its fallback is on
(``HARNESS_MODEL_FALLBACK``, default on since WP-100), so Node pins an agent's other models as alternates only where a
worker (and its Model Gateway) would move to them; an operator who sets it off gets one route, as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.harness.worker.capabilities import (
    MODEL_FALLBACK,
    PIPESHUB_FEATURES,
    advertised,
)
from app.utils.env_flags import ENV_FLAG_DEFAULTS, HARNESS_MODEL_FALLBACK

if TYPE_CHECKING:
    import pytest


def _features(*, value: str | None, monkeypatch: pytest.MonkeyPatch) -> set[str]:
    if value is None:
        monkeypatch.delenv(HARNESS_MODEL_FALLBACK, raising=False)
    else:
        monkeypatch.setenv(HARNESS_MODEL_FALLBACK, value)
    return {f.root for f in advertised(engines=True, code=True, signin=True).features}


def test_g48_6_model_fallback_is_advertised_only_with_the_fallback_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert MODEL_FALLBACK in PIPESHUB_FEATURES
    assert MODEL_FALLBACK in _features(value="true", monkeypatch=monkeypatch)
    assert MODEL_FALLBACK not in _features(value="false", monkeypatch=monkeypatch)
    # Everything else is advertised as before.
    assert _features(value="true", monkeypatch=monkeypatch) - {
        MODEL_FALLBACK
    } == _features(value="false", monkeypatch=monkeypatch)


def test_wp100_fb_1_the_fallback_is_on_by_default_and_the_broad_flag_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert ENV_FLAG_DEFAULTS[HARNESS_MODEL_FALLBACK] is True
    assert MODEL_FALLBACK in _features(value=None, monkeypatch=monkeypatch)
    # HARNESS_MODEL_POLICY gated nothing but this move once same-route retries ran without it (P4.9 D-1): retired.
    assert "HARNESS_MODEL_POLICY" not in ENV_FLAG_DEFAULTS
