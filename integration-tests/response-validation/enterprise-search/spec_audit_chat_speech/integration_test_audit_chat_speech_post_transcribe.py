"""Strict OpenAPI audit of POST /api/v1/chat/transcribe.

Successes run against a LiteLLM-proxy STT provider pointed at a local fake of the
OpenAI audio API, so nothing is billed and the transcript is known.
"""

from __future__ import annotations

import pytest
from chat_speech_audit_support import (
    AUDIO_FIELD,
    FAKE_PROVIDER,
    FAKE_STT_MODEL,
    FAKE_TRANSCRIPT,
    MAX_STT_AUDIO_BYTES,
    NO_STT_PROVIDER,
    PROVIDER_FAILURE_MARKER,
    TRANSCRIBE_ROUTE,
    ChatSpeechClient,
    SpeechCapabilities,
    request_as,
    request_with_headers,
    silent_wav,
)
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = TRANSCRIBE_ROUTE

TRANSCRIBED = {"text": FAKE_TRANSCRIPT, "provider": FAKE_PROVIDER, "model": FAKE_STT_MODEL}


@pytest.mark.parametrize("language", [None, "en"], ids=["auto-detect", "language-hint"])
def test_transcribe_returns_the_providers_transcript(
    chat_speech_client: ChatSpeechClient,
    fake_speech_providers: dict[str, str],
    language: str | None,
) -> None:
    resp = chat_speech_client.transcribe(silent_wav(), language=language)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == TRANSCRIBED


def test_transcribe_provider_failure_is_bad_gateway(
    chat_speech_client: ChatSpeechClient, fake_speech_providers: dict[str, str]
) -> None:
    resp = chat_speech_client.transcribe(silent_wav() + PROVIDER_FAILURE_MARKER.encode())
    assert resp.status_code == 502, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    detail = resp.json()["detail"]
    assert set(resp.json()) == {"detail"}
    # The service's words for a 5xx stay in the log; the caller gets generic advice.
    assert "upstream provider error" not in detail, detail


def test_transcribe_empty_audio_with_a_provider_is_bad_request(
    chat_speech_client: ChatSpeechClient, fake_speech_providers: dict[str, str]
) -> None:
    resp = chat_speech_client.transcribe(b"", language="en")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"detail": "Empty audio payload"}


def test_transcribe_empty_audio_without_a_provider_is_conflict(
    chat_speech_client: ChatSpeechClient, no_speech_provider: SpeechCapabilities
) -> None:
    # The provider lookup runs before the empty check.
    resp = chat_speech_client.transcribe(b"", language="en")
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == NO_STT_PROVIDER


def test_member_transcribe_without_stt_provider_is_conflict(
    second_user: SecondUser, no_speech_provider: SpeechCapabilities
) -> None:
    resp = request_as(
        second_user,
        "POST",
        "/transcribe",
        files={AUDIO_FIELD: ("audio.wav", silent_wav(), "audio/wav")},
    )
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == NO_STT_PROVIDER


def test_transcribe_without_token_is_unauthorized(
    chat_speech_client: ChatSpeechClient,
) -> None:
    resp = chat_speech_client.transcribe(silent_wav(), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_transcribe_with_token_lacking_chat_scope_is_forbidden(
    pipeshub_client: PipeshubClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = request_with_headers(
        pipeshub_client,
        narrow_scope_headers,
        "POST",
        "/transcribe",
        files={AUDIO_FIELD: ("audio.wav", silent_wav(), "audio/wav")},
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_FORBIDDEN", resp.text[:500]


def test_transcribe_without_file_part_is_rejected_by_node(
    chat_speech_client: ChatSpeechClient,
) -> None:
    resp = chat_speech_client.transcribe(None, language="en")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    # Written by the Node controller itself, so neither the FastAPI nor the Node error envelope.
    assert resp.json() == {
        "status": "error",
        "message": "Audio file is required (field 'file')",
    }


def _assert_upload_refused_as_internal_error(resp) -> None:  # noqa: ANN001 - requests.Response
    assert resp.status_code == 500, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]


def test_transcribe_file_under_another_field_is_an_internal_error(
    chat_speech_client: ChatSpeechClient,
) -> None:
    # API bug: multer's LIMIT_UNEXPECTED_FILE is a caller mistake, yet it reaches the
    # error middleware as an unknown error.
    _assert_upload_refused_as_internal_error(chat_speech_client.transcribe(silent_wav(), field="audio"))


def test_transcribe_two_files_is_an_internal_error(
    chat_speech_client: ChatSpeechClient,
) -> None:
    resp = chat_speech_client.post(
        "/transcribe",
        files=[
            (AUDIO_FIELD, ("one.wav", silent_wav(), "audio/wav")),
            (AUDIO_FIELD, ("two.wav", silent_wav(), "audio/wav")),
        ],
    )
    _assert_upload_refused_as_internal_error(resp)


def test_transcribe_audio_over_25_mib_is_an_internal_error(
    chat_speech_client: ChatSpeechClient,
) -> None:
    # Held in memory only: multer refuses it before anything is forwarded or stored.
    resp = chat_speech_client.transcribe(b"\x00" * (MAX_STT_AUDIO_BYTES + 1))
    _assert_upload_refused_as_internal_error(resp)
