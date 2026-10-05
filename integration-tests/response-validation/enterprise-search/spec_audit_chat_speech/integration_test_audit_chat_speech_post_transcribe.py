"""Strict OpenAPI audit of POST /api/v1/chat/transcribe."""

from __future__ import annotations

import pytest
from chat_speech_audit_support import (
    AUDIO_FIELD,
    TRANSCRIBE_ROUTE,
    ChatSpeechClient,
    SpeechCapabilities,
    provider_configured,
    request_as,
    silent_wav,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = TRANSCRIBE_ROUTE
assert ROUTE == "/api/v1/chat/transcribe"

NO_STT_PROVIDER = {"detail": "No Speech-to-Text provider configured"}


def test_transcribe_without_token_is_unauthorized(
    chat_speech_client: ChatSpeechClient,
) -> None:
    resp = chat_speech_client.transcribe(silent_wav(), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_transcribe_without_file_part_is_rejected_by_node(
    chat_speech_client: ChatSpeechClient,
) -> None:
    resp = chat_speech_client.transcribe(None, language="en")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    # Written by the Node controller itself, so neither the FastAPI nor the Node error envelope.
    assert resp.json() == {
        "status": "error",
        "message": "Audio file is required (field 'file')",
    }


def test_transcribe_wrong_file_field_is_an_unhandled_multer_error(
    chat_speech_client: ChatSpeechClient,
) -> None:
    # multer's LIMIT_UNEXPECTED_FILE is not a BaseError, so the error middleware answers 500.
    resp = chat_speech_client.transcribe(silent_wav(), field="audio")
    assert resp.status_code == 500, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR"


def test_transcribe_empty_audio_never_reaches_the_provider(
    chat_speech_client: ChatSpeechClient,
    speech_capabilities: SpeechCapabilities,
) -> None:
    # The provider lookup runs before the empty check, so the answer depends on the org's config.
    configured = provider_configured(speech_capabilities, "stt")
    expected_status = 400 if configured else 409
    expected_body = {"detail": "Empty audio payload"} if configured else NO_STT_PROVIDER

    resp = chat_speech_client.transcribe(b"", language="en")
    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == expected_body


def test_member_transcribe_without_stt_provider_is_conflict(
    second_user: SecondUser,
    speech_capabilities: SpeechCapabilities,
) -> None:
    if provider_configured(speech_capabilities, "stt"):
        pytest.skip("an STT provider is configured; a real transcription would be billed")

    resp = request_as(
        second_user,
        "POST",
        "/transcribe",
        files={AUDIO_FIELD: ("audio.wav", silent_wav(), "audio/wav")},
    )
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == NO_STT_PROVIDER
