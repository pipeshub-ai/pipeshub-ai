"""`accuracy_feature_enabled`: a feature's own env var decides when set;
unset or blank, the feature follows HIGH_ACCURACY_MODE."""

from __future__ import annotations

import pytest

from app.utils.env_utils import accuracy_feature_enabled

_FEATURE = "SOME_ACCURACY_FEATURE"


@pytest.mark.parametrize(
    ("high_accuracy", "feature", "expected"),
    [
        (None, None, False),
        ("false", None, False),
        ("true", None, True),
        (" TRUE ", None, True),
        ("true", "", True),
        ("true", "   ", True),
        ("false", "", False),
        ("true", "false", False),
        ("true", "0", False),
        ("true", "garbage", False),
        (None, "true", True),
        ("false", "True", True),
        (None, "1", False),
        (None, "yes", False),
    ],
)
def test_feature_switch_then_high_accuracy_mode(
    monkeypatch: pytest.MonkeyPatch, high_accuracy: str | None, feature: str | None, expected: bool,
) -> None:
    for name, value in (("HIGH_ACCURACY_MODE", high_accuracy), (_FEATURE, feature)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    assert accuracy_feature_enabled(_FEATURE) is expected


def test_reads_the_environment_on_every_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_FEATURE, raising=False)
    monkeypatch.setenv("HIGH_ACCURACY_MODE", "true")
    assert accuracy_feature_enabled(_FEATURE) is True

    monkeypatch.setenv(_FEATURE, "false")
    assert accuracy_feature_enabled(_FEATURE) is False
