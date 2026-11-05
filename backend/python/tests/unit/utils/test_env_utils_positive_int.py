"""env_positive_int: a cap env var that is zero, negative or junk falls back to the default."""

import logging

import pytest

from app.embedding_main import MAX_EMBEDDING_INPUTS
from app.utils.env_utils import env_positive_int


@pytest.mark.parametrize("raw", ["0", "-5", "abc", "1.5"])
def test_unusable_value_uses_default_and_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, raw: str,
) -> None:
    monkeypatch.setenv("CAP_X", raw)
    with caplog.at_level(logging.WARNING):
        assert env_positive_int("CAP_X", 2048) == 2048
    assert "CAP_X" in caplog.text


@pytest.mark.parametrize(("raw", "expected"), [(None, 7), ("", 7), ("  ", 7), ("1", 1), (" 99 ", 99)])
def test_unset_blank_and_valid(monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: int) -> None:
    if raw is None:
        monkeypatch.delenv("CAP_X", raising=False)
    else:
        monkeypatch.setenv("CAP_X", raw)
    assert env_positive_int("CAP_X", 7) == expected


def test_shipped_embedding_input_cap_is_the_documented_default() -> None:
    assert MAX_EMBEDDING_INPUTS == 2048
