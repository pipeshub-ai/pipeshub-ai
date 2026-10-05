"""Strict OpenAPI audit of POST /api/v1/chat/speak."""

from __future__ import annotations

from typing import Any

import pytest
from chat_speech_audit_support import (
    MAX_TTS_TEXT_CHARS,
    ChatSpeechClient,
    SpeechCapabilities,
    provider_configured,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/chat/speak"


def test_speak_without_token_is_unauthorized(
    chat_speech_client: ChatSpeechClient,
) -> None:
    resp = chat_speech_client.speak({"text": "hello"}, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


# All three are refused by the Python handler before it looks up a TTS provider, so none is billed.
@pytest.mark.parametrize(
    ("body", "expected_status"),
    [
        pytest.param({"voice": "alloy"}, 422, id="missing-text"),
        pytest.param({"text": "   "}, 400, id="blank-text"),
        pytest.param({"text": "a" * (MAX_TTS_TEXT_CHARS + 1)}, 413, id="text-too-long"),
    ],
)
def test_speak_rejects_invalid_text(
    chat_speech_client: ChatSpeechClient,
    body: dict[str, Any],
    expected_status: int,
) -> None:
    resp = chat_speech_client.speak(body)
    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    # Node reduces every upstream 4xx to one sentence: a Pydantic 422's list is replaced by its summary.
    assert set(body) == {"detail"}, body
    assert isinstance(body["detail"], str) and body["detail"], body


def test_member_speak_without_tts_provider_is_conflict(
    second_user: SecondUser,
    speech_capabilities: SpeechCapabilities,
) -> None:
    if provider_configured(speech_capabilities, "tts"):
        pytest.skip("a TTS provider is configured; a valid request would bill it")

    resp = request_as(second_user, "POST", "/speak", json={"text": "hello"})
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"detail": "No Text-to-Speech provider configured"}
