"""Strict OpenAPI audit of POST /api/v1/chat/speak.

Successes run against a LiteLLM-proxy TTS provider pointed at a local fake of the
OpenAI audio API, so nothing is billed and the audio bytes are known.
"""

from __future__ import annotations

from typing import Any

import pytest
from chat_speech_audit_support import (
    FAKE_AUDIO,
    FAKE_PROVIDER,
    FAKE_TTS_MODEL,
    MAX_TTS_TEXT_CHARS,
    NO_TTS_PROVIDER,
    PROVIDER_FAILURE_MARKER,
    SPEAK_ROUTE,
    ChatSpeechClient,
    SpeechCapabilities,
    request_as,
    request_with_headers,
)
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = SPEAK_ROUTE


def _assert_audio(resp: Any, mime: str) -> None:
    assert resp.status_code == 200, resp.text[:500]
    assert resp.headers["Content-Type"] == mime, resp.headers
    assert resp.headers["Cache-Control"] == "no-store", resp.headers
    assert resp.headers["X-TTS-Provider"] == FAKE_PROVIDER, resp.headers
    assert resp.headers["X-TTS-Model"] == FAKE_TTS_MODEL, resp.headers
    assert resp.content == FAKE_AUDIO


@pytest.mark.parametrize(
    ("audio_format", "mime"),
    [
        pytest.param(None, "audio/mpeg", id="default-mp3"),
        pytest.param("mp3", "audio/mpeg", id="mp3"),
        pytest.param("opus", "audio/ogg", id="opus"),
        pytest.param("aac", "audio/aac", id="aac"),
        pytest.param("flac", "audio/flac", id="flac"),
        pytest.param("wav", "audio/wav", id="wav"),
        pytest.param("pcm", "audio/pcm", id="pcm"),
    ],
)
def test_speak_returns_audio_in_the_requested_format(
    chat_speech_client: ChatSpeechClient,
    fake_speech_providers: dict[str, str],
    audio_format: str | None,
    mime: str,
) -> None:
    body: dict[str, Any] = {"text": "hello from the spec audit"}
    if audio_format is not None:
        body["format"] = audio_format
    resp = chat_speech_client.speak(body)
    _assert_audio(resp, mime)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_speak_with_every_documented_field(
    chat_speech_client: ChatSpeechClient, fake_speech_providers: dict[str, str]
) -> None:
    resp = chat_speech_client.speak({"text": "hello", "voice": "alloy", "format": "wav", "speed": 1.5})
    _assert_audio(resp, "audio/wav")
    assert_strict_openapi_exchange(resp, ROUTE)


def test_speak_accepts_null_optional_fields(
    chat_speech_client: ChatSpeechClient, fake_speech_providers: dict[str, str]
) -> None:
    resp = chat_speech_client.speak({"text": "hello", "voice": None, "format": None, "speed": None})
    _assert_audio(resp, "audio/mpeg")
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("reason", "fields", "mime"),
    [
        pytest.param("an unknown format falls back to mp3", {"format": "ogg"}, "audio/mpeg", id="unknown-format"),
        pytest.param("a speed above 4.0 is clamped", {"speed": 10}, "audio/mpeg", id="speed-above-range"),
        pytest.param("a speed below 0.25 is clamped", {"speed": 0.01}, "audio/mpeg", id="speed-below-range"),
        pytest.param("fields the model does not declare are ignored", {"specAuditExtra": 1}, "audio/mpeg", id="unknown-field"),
    ],
)
def test_speak_tolerates_what_the_spec_does_not_allow(
    chat_speech_client: ChatSpeechClient,
    fake_speech_providers: dict[str, str],
    reason: str,
    fields: dict[str, Any],
    mime: str,
) -> None:
    with outside_request_contract(reason):
        resp = chat_speech_client.speak({"text": "hello", **fields})
    _assert_audio(resp, mime)
    assert_strict_openapi_response(resp, ROUTE)


def test_speak_provider_failure_is_bad_gateway(
    chat_speech_client: ChatSpeechClient, fake_speech_providers: dict[str, str]
) -> None:
    resp = chat_speech_client.speak({"text": f"please fail {PROVIDER_FAILURE_MARKER}"})
    assert resp.status_code == 502, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    detail = resp.json()["detail"]
    # The service's words for a 5xx stay in the log; the caller gets generic advice.
    assert "upstream provider error" not in detail, detail
    assert set(resp.json()) == {"detail"}


def test_speak_without_token_is_unauthorized(
    chat_speech_client: ChatSpeechClient,
) -> None:
    resp = chat_speech_client.speak({"text": "hello"}, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_speak_with_token_lacking_chat_scope_is_forbidden(
    pipeshub_client: PipeshubClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = request_with_headers(
        pipeshub_client, narrow_scope_headers, "POST", "/speak", json={"text": "hello"}
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_FORBIDDEN", resp.text[:500]


# All are refused by the Python handler before it looks up a TTS provider, so none is billed.
@pytest.mark.parametrize(
    ("body", "expected_status"),
    [
        pytest.param({"voice": "alloy"}, 422, id="missing-text"),
        pytest.param({"text": 42}, 422, id="text-not-a-string"),
        pytest.param({"text": "hello", "speed": "fast"}, 422, id="speed-not-a-number"),
        pytest.param({"text": ""}, 400, id="empty-text"),
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
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)
    payload = resp.json()
    # Node reduces every upstream 4xx to one sentence: a Pydantic 422's list is replaced by its summary.
    assert set(payload) == {"detail"}, payload
    assert isinstance(payload["detail"], str) and payload["detail"], payload


def test_speak_without_tts_provider_is_conflict(
    chat_speech_client: ChatSpeechClient,
    second_user: SecondUser,
    no_speech_provider: SpeechCapabilities,
) -> None:
    for resp in (
        chat_speech_client.speak({"text": "hello"}),
        request_as(second_user, "POST", "/speak", json={"text": "hello"}),
    ):
        assert resp.status_code == 409, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert resp.json() == NO_TTS_PROVIDER
