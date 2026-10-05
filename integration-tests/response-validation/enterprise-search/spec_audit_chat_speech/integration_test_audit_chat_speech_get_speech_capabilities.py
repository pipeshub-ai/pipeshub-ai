"""Strict OpenAPI audit of GET /api/v1/chat/speech/capabilities."""

from __future__ import annotations

from typing import Any

import pytest
from chat_speech_audit_support import (
    INVALID_AUTH_HEADERS,
    ChatSpeechClient,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/chat/speech/capabilities"

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


def test_capabilities_as_admin_reports_both_buckets(
    chat_speech_client: ChatSpeechClient,
) -> None:
    resp = chat_speech_client.capabilities()
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    _assert_capabilities_body(resp.json())


def test_capabilities_as_member_matches_admin(
    chat_speech_client: ChatSpeechClient,
    second_user: SecondUser,
) -> None:
    # No admin gate on the route: the summary is org-wide, so a member sees the same body.
    resp = request_as(second_user, "GET", "/speech/capabilities")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
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
    assert_strict_openapi_response(resp, ROUTE)
