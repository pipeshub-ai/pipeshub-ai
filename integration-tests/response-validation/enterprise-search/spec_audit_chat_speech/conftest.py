"""Shared fixtures for the strict OpenAPI audit of the speech routes under /api/v1/chat."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_INTEGRATION_ROOT = Path(__file__).resolve().parents[3]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.pipeshub_client import PipeshubClient  # noqa: E402

from chat_speech_audit_support import (  # noqa: E402
    ChatSpeechClient,
    SpeechCapabilities,
)


@pytest.fixture(scope="session")
def chat_speech_client(pipeshub_client: PipeshubClient) -> ChatSpeechClient:
    return ChatSpeechClient(pipeshub_client)


@pytest.fixture(scope="session")
def speech_capabilities(chat_speech_client: ChatSpeechClient) -> SpeechCapabilities:
    """The org's ``{"tts": ..., "stt": ...}`` summary; tells a test whether a provider would be billed."""
    resp = chat_speech_client.capabilities()
    if resp.status_code != 200:
        pytest.skip(
            f"speech capabilities returned {resp.status_code}, cannot tell whether "
            f"a TTS/STT provider is configured: {resp.text[:200]}"
        )
    return resp.json()
