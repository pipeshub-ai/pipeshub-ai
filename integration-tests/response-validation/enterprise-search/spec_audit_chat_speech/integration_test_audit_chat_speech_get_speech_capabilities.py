"""Strict OpenAPI audit of GET /api/v1/chat/speech/capabilities."""

from __future__ import annotations

from typing import Any

import pytest
from chat_speech_audit_support import (
    CAPABILITIES_ROUTE,
    FAKE_PROVIDER,
    FAKE_STT_MODEL,
    FAKE_TTS_MODEL,
    INVALID_AUTH_HEADERS,
    ChatSpeechClient,
    SpeechCapabilities,
    request_as,
    request_with_headers,
)
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = CAPABILITIES_ROUTE

SUMMARY_KEYS = {
    "provider",
    "model",
    "defaultModel",
    "models",
    "isDefault",
    "modelKey",
    "friendlyName",
}


def _assert_capabilities_body(body: Any) -> None:
    assert isinstance(body, dict), body
    assert set(body) == {"tts", "stt"}, body
    for kind in ("tts", "stt"):
        summary = body[kind]
        if summary is None:
            continue
        assert set(summary) == SUMMARY_KEYS, f"{kind}: {sorted(summary)}"
        assert isinstance(summary["models"], list)
        assert isinstance(summary["isDefault"], bool)
        # The Python handler derives both from the first entry of the model list.
        expected_default = summary["models"][0] if summary["models"] else None
        assert summary["model"] == expected_default
        assert summary["defaultModel"] == expected_default


def test_capabilities_without_providers_reports_both_buckets_null(
    chat_speech_client: ChatSpeechClient, no_speech_provider: SpeechCapabilities
) -> None:
    resp = chat_speech_client.capabilities()
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"tts": None, "stt": None}


def test_capabilities_summarise_configured_providers(
    chat_speech_client: ChatSpeechClient, fake_speech_providers: dict[str, str]
) -> None:
    resp = chat_speech_client.capabilities()
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    _assert_capabilities_body(body)
    for kind, model in (("tts", FAKE_TTS_MODEL), ("stt", FAKE_STT_MODEL)):
        summary = body[kind]
        assert summary["provider"] == FAKE_PROVIDER, summary
        assert summary["models"] == [model], summary
        assert summary["isDefault"] is True, summary
        assert summary["modelKey"] == fake_speech_providers[kind], summary
        assert summary["friendlyName"].startswith(f"spec-audit {kind}"), summary
        # Secrets stay on the server.
        assert "apiKey" not in summary and "endpoint" not in summary, summary


def test_capabilities_as_member_matches_admin(
    chat_speech_client: ChatSpeechClient,
    second_user: SecondUser,
    speech_config_lock: None,
) -> None:
    # No admin gate on the route: the summary is org-wide, so a member sees the same body.
    resp = request_as(second_user, "GET", "/speech/capabilities")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    _assert_capabilities_body(resp.json())

    admin = chat_speech_client.capabilities()
    assert admin.status_code == 200, admin.text[:500]
    assert resp.json() == admin.json()


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param(None, id="no_token"),
        pytest.param(INVALID_AUTH_HEADERS, id="invalid_token"),
    ],
)
def test_capabilities_without_valid_token_is_unauthorized(
    chat_speech_client: ChatSpeechClient,
    headers: dict[str, str] | None,
) -> None:
    kwargs: dict[str, Any] = {"headers": headers} if headers else {}
    resp = chat_speech_client.capabilities(auth=False, **kwargs)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_capabilities_with_token_lacking_chat_scope_is_forbidden(
    pipeshub_client: PipeshubClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = request_with_headers(pipeshub_client, narrow_scope_headers, "GET", "/speech/capabilities")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_FORBIDDEN", resp.text[:500]
