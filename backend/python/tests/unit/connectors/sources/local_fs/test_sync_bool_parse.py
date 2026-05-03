"""Tests for :mod:`app.connectors.sources.local_fs.sync_bool_parse`."""

import pytest

from app.connectors.sources.local_fs.sync_bool_parse import parse_sync_bool


class TestParseSyncBool:
    @pytest.mark.parametrize(
        "raw, default, expected",
        [
            (True, False, True),
            (False, True, False),
            ("true", False, True),
            ("TRUE", False, True),
            ("True", False, True),
            ("  yes  ", False, True),
            ("YES", False, True),
            ("1", False, True),
            ("on", False, True),
            ("ON", False, True),
            (" 1 ", False, True),
            ("\ttrue\n", False, True),
            ("false", True, False),
            ("FALSE", True, False),
            ("0", True, False),
            ("no", True, False),
            ("NO", True, False),
            ("off", True, False),
            ("OFF", True, False),
            ("", True, False),
            ("   ", True, False),
        ],
    )
    def test_truthy_and_falsey_strings(self, raw, default, expected):
        assert parse_sync_bool(raw, default) is expected

    def test_unrecognized_string_is_false(self):
        """Strings that are not explicit truthy tokens are treated as false."""
        assert parse_sync_bool("maybe", True) is False
        assert parse_sync_bool("maybe", False) is False
        assert parse_sync_bool("2", True) is False
        assert parse_sync_bool("trueish", True) is False
        assert parse_sync_bool("disabled", True) is False

    def test_non_bool_non_str_uses_default(self):
        assert parse_sync_bool(None, True) is True
        assert parse_sync_bool(None, False) is False
        assert parse_sync_bool(42, False) is False
        assert parse_sync_bool(42, True) is True
        assert parse_sync_bool([], True) is True
        assert parse_sync_bool({}, False) is False
        assert parse_sync_bool(0, True) is True  # int 0 is NOT treated as falsey
        assert parse_sync_bool(1, False) is False  # int 1 is NOT treated as truthy
        assert parse_sync_bool(0.0, True) is True

    def test_bool_overrides_default(self):
        # bool input must always win over default — we check this exhaustively
        # because bool is a subclass of int in Python and the isinstance order matters.
        assert parse_sync_bool(True, False) is True
        assert parse_sync_bool(True, True) is True
        assert parse_sync_bool(False, True) is False
        assert parse_sync_bool(False, False) is False

    def test_returns_strict_bool_type(self):
        # Must be the *bool* singletons, not truthy/falsy values, so callers
        # can rely on `is True` / `is False` checks downstream.
        assert isinstance(parse_sync_bool("true", False), bool)
        assert isinstance(parse_sync_bool("nope", True), bool)
        assert isinstance(parse_sync_bool(None, True), bool)
