"""A model missing from the window table runs on the 128k default, which is
far too small for current 1M-window models; the fallback is logged once per
model so the next missing entry shows up in the logs."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.agent_loop_lib.context.base import ContextBudget
from app.agent_loop_lib.modules.providers.budget import pricing
from app.agent_loop_lib.modules.providers.budget.pricing import get_context_window

if TYPE_CHECKING:
    import pytest

_LOGGER = "app.agent_loop_lib.modules.providers.budget.pricing"


def _fallback_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == _LOGGER and r.levelno == logging.INFO]


def test_an_unknown_model_logs_its_fallback_once(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pricing, "_FALLBACK_LOGGED", set())
    caplog.set_level(logging.INFO, logger=_LOGGER)

    assert get_context_window("gpt-7-preview") == 128_000
    assert get_context_window("gpt-7-preview") == 128_000

    records = _fallback_records(caplog)
    assert len(records) == 1
    assert "gpt-7-preview" in records[0].getMessage()


def test_each_unknown_model_is_logged(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pricing, "_FALLBACK_LOGGED", set())
    caplog.set_level(logging.INFO, logger=_LOGGER)

    get_context_window("model-a")
    get_context_window("model-b")

    assert len(_fallback_records(caplog)) == 2


def test_known_configured_and_unnamed_models_log_nothing(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pricing, "_FALLBACK_LOGGED", set())
    caplog.set_level(logging.INFO, logger=_LOGGER)

    get_context_window("claude-sonnet-5-5")
    get_context_window(None)
    ContextBudget.for_model("gpt-7-preview", context_window=400_000)

    assert _fallback_records(caplog) == []
