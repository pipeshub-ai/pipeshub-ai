"""Tests for the logging provider port and how it is resolved."""

import logging
from collections.abc import Iterator
from typing import NoReturn

import pytest

import app.edition as edition
from app.utils.structured_logging import (
    DefaultLoggingProvider,
    StructuredLoggingProvider,
    get_provider,
)


@pytest.fixture(autouse=True)
def _clear_provider_cache() -> Iterator[None]:
    get_provider.cache_clear()
    yield
    get_provider.cache_clear()


class TestDefaultLoggingProvider:
    def test_console_formatter_is_the_default(self) -> None:
        default = logging.Formatter("%(message)s")
        assert DefaultLoggingProvider().console_formatter(default) is default

    def test_file_logging_is_on(self) -> None:
        assert DefaultLoggingProvider().file_logging_enabled() is True

    def test_org_is_left_off(self) -> None:
        assert DefaultLoggingProvider().hash_org_id("org-1") is None


class _Custom(StructuredLoggingProvider):
    def console_formatter(self, default: logging.Formatter) -> logging.Formatter:
        return default

    def file_logging_enabled(self) -> bool:
        return False

    def hash_org_id(self, org_id: str) -> str:
        return f"h:{org_id}"


class TestGetProvider:
    def test_default_when_no_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(edition, "structured_logging_provider", lambda: None)
        assert isinstance(get_provider(), DefaultLoggingProvider)

    def test_override_is_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        custom = _Custom()
        monkeypatch.setattr(edition, "structured_logging_provider", lambda: custom)
        assert get_provider() is custom

    def test_is_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[int] = []
        monkeypatch.setattr(edition, "structured_logging_provider", lambda: calls.append(1))
        assert get_provider() is get_provider()
        assert len(calls) == 1

    def test_wrong_type_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(edition, "structured_logging_provider", lambda: object())
        with pytest.raises(TypeError, match="StructuredLoggingProvider"):
            get_provider()

    def test_override_errors_propagate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom() -> NoReturn:
            raise RuntimeError("misconfigured")

        monkeypatch.setattr(edition, "structured_logging_provider", _boom)
        with pytest.raises(RuntimeError, match="misconfigured"):
            get_provider()
