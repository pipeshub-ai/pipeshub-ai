"""C411-FU-1: a worker advertises ``trace_content`` only while its traces hold model content, so the admins' AI Access
page says what the servers do (tracing on and the GenAI content opt-in on), never what Node's own environment says."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.harness.worker.capabilities import PIPESHUB_FEATURES, TRACE_CONTENT, advertised
from app.utils.env_flags import (
    ENV_FLAG_DEFAULTS,
    HARNESS_OTEL,
    OTEL_GENAI_CAPTURE_CONTENT,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


def _features(env: Mapping[str, str | None], monkeypatch: pytest.MonkeyPatch) -> set[str]:
    for name, value in env.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    return {f.root for f in advertised(engines=True, code=True, signin=True).features}


@pytest.mark.parametrize(
    ("otel", "content", "shown"),
    [
        (None, None, False),
        ("true", None, False),
        (None, "true", False),
        ("false", "true", False),
        ("true", "false", False),
        ("true", "true", True),
    ],
)
def test_c411_fu1_trace_content_is_advertised_only_with_tracing_and_content_capture_on(
    otel: str | None, content: str | None, shown: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    features = _features({HARNESS_OTEL: otel, OTEL_GENAI_CAPTURE_CONTENT: content}, monkeypatch)
    assert (TRACE_CONTENT in features) is shown


def test_c411_fu1_off_by_default_and_nothing_else_moves(monkeypatch: pytest.MonkeyPatch) -> None:
    assert TRACE_CONTENT in PIPESHUB_FEATURES
    assert ENV_FLAG_DEFAULTS[HARNESS_OTEL] is False and ENV_FLAG_DEFAULTS[OTEL_GENAI_CAPTURE_CONTENT] is False
    on = _features({HARNESS_OTEL: "true", OTEL_GENAI_CAPTURE_CONTENT: "true"}, monkeypatch)
    off = _features({HARNESS_OTEL: None, OTEL_GENAI_CAPTURE_CONTENT: None}, monkeypatch)
    assert on - {TRACE_CONTENT} == off
