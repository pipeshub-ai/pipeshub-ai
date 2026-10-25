"""`pdfplumber_enabled`: text PDFs use the PdfPlumber+OpenCV parser unless the
deployment explicitly turns it off."""

from __future__ import annotations

import pytest

from app.utils.env_utils import PDFPLUMBER_PROCESSOR_ENV_VAR, pdfplumber_enabled


def test_unset_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PDFPLUMBER_PROCESSOR_ENV_VAR, raising=False)
    assert pdfplumber_enabled() is True


@pytest.mark.parametrize("value", ["", "   ", "true", "TRUE", "1", "yes", "garbage"])
def test_blank_and_other_values_are_on(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(PDFPLUMBER_PROCESSOR_ENV_VAR, value)
    assert pdfplumber_enabled() is True


@pytest.mark.parametrize("value", ["false", "FALSE", " False ", "0", "no", "off", "OFF"])
def test_explicit_false_is_off(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(PDFPLUMBER_PROCESSOR_ENV_VAR, value)
    assert pdfplumber_enabled() is False
