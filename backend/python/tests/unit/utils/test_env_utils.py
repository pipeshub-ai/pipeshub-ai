"""Unit tests for app.utils.env_utils (env_bool and env_int)."""

from __future__ import annotations

import pytest

from app.utils.env_utils import env_bool, env_int

_VAR = "TEST_PIPESHUB_ENV_VAR"


class TestEnvBool:
    """Tests for env_bool()."""

    def test_unset_variable_uses_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(_VAR, raising=False)

        assert env_bool(_VAR, default=True) is True
        assert env_bool(_VAR, default=False) is False

    @pytest.mark.parametrize(
        "raw_val",
        ["true", "TRUE", " true ", "  TRuE  "],
    )
    def test_truthy_values_return_true(
        self, monkeypatch: pytest.MonkeyPatch, raw_val: str
    ) -> None:
        monkeypatch.setenv(_VAR, raw_val)

        assert env_bool(_VAR, default=False) is True
        assert env_bool(_VAR, default=True) is True

    @pytest.mark.parametrize(
        "raw_val",
        ["false", "yes", "", "  ", "0", "1", "no", "invalid"],
    )
    def test_non_true_values_return_false(
        self, monkeypatch: pytest.MonkeyPatch, raw_val: str
    ) -> None:
        monkeypatch.setenv(_VAR, raw_val)

        assert env_bool(_VAR, default=True) is False
        assert env_bool(_VAR, default=False) is False


class TestEnvInt:
    """Tests for env_int()."""

    def test_unset_variable_returns_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(_VAR, raising=False)

        assert env_int(_VAR) is None
        assert env_int(_VAR, default=None) is None
        assert env_int(_VAR, default=42) == 42
        assert env_int(_VAR, default=2, lo=5, hi=10) == 2
        assert env_int(_VAR, default=12, lo=5, hi=10) == 12

    @pytest.mark.parametrize("raw_val", ["", " "])
    def test_blank_or_whitespace_returns_default(
        self, monkeypatch: pytest.MonkeyPatch, raw_val: str
    ) -> None:
        monkeypatch.setenv(_VAR, raw_val)

        assert env_int(_VAR) is None
        assert env_int(_VAR, default=10) == 10

    def test_non_numeric_string_returns_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_VAR, "abc")

        assert env_int(_VAR) is None
        assert env_int(_VAR, default=5) == 5

    @pytest.mark.parametrize(
        ("raw_val", "expected"),
        [
            ("12", 12),
            (" 12 ", 12),
            ("-5", -5),
            ("0", 0),
            ("+12", 12),
        ],
    )
    def test_valid_numeric_string_parses_int(
        self,
        monkeypatch: pytest.MonkeyPatch,
        raw_val: str,
        expected: int,
    ) -> None:
        monkeypatch.setenv(_VAR, raw_val)

        assert env_int(_VAR) == expected

    def test_lo_clamping(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_VAR, "1")
        assert env_int(_VAR, lo=3) == 3

        monkeypatch.setenv(_VAR, "5")
        assert env_int(_VAR, lo=3) == 5

    def test_hi_clamping(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_VAR, "9")
        assert env_int(_VAR, hi=4) == 4

        monkeypatch.setenv(_VAR, "2")
        assert env_int(_VAR, hi=4) == 2

    def test_lo_and_hi_clamping(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_VAR, "0")
        assert env_int(_VAR, lo=10, hi=100) == 10

        monkeypatch.setenv(_VAR, "50")
        assert env_int(_VAR, lo=10, hi=100) == 50

        monkeypatch.setenv(_VAR, "200")
        assert env_int(_VAR, lo=10, hi=100) == 100

    @pytest.mark.parametrize(
        ("raw_val", "expected"),
        [
            ("3", 3),
            ("4", 4),
        ],
    )
    def test_values_at_bounds_remain_unchanged(
        self, monkeypatch: pytest.MonkeyPatch, raw_val: str, expected: int
    ) -> None:
        monkeypatch.setenv(_VAR, raw_val)

        assert env_int(_VAR, lo=3, hi=4) == expected
