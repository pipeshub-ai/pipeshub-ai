"""The test-only Gmail endpoint setting, and body decoding with or without padding."""

import base64

import pytest

from app.connectors.sources.google.common.gmail_endpoint import ENV, gmail_api_base_url
from app.connectors.sources.google.common.gmail_message_parts import decode_body_data


def test_unset_means_google(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV, raising=False)
    assert gmail_api_base_url() is None


@pytest.mark.parametrize("value", ["http://gmail-sim.internal", "http://gmail-sim.internal/", "https://sim:8106"])
def test_a_bare_origin_is_taken(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(ENV, value)
    assert gmail_api_base_url() == value.rstrip("/")


@pytest.mark.parametrize("value", ["gmail-sim.internal", "ftp://x", "http://x/path", "http://u:p@x", "http://x?q=1"])
def test_anything_else_is_refused(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(ENV, value)
    with pytest.raises(ValueError):
        gmail_api_base_url()


@pytest.mark.parametrize("raw", [b"hi", b"hello", b"hello!", "Résumé – 日本".encode()])
def test_decode_with_and_without_padding(raw: bytes) -> None:
    padded = base64.urlsafe_b64encode(raw).decode()
    assert decode_body_data(padded) == raw
    assert decode_body_data(padded.rstrip("=")) == raw


def test_decode_nothing() -> None:
    assert decode_body_data(None) == b"" and decode_body_data("") == b""
