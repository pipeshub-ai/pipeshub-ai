"""Shared fixtures for the strict OpenAPI audit of the speech routes under /api/v1/chat."""

from __future__ import annotations

import fcntl
import logging
import sys
from collections.abc import Iterator
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
    fake_openai_audio_server,
    forget_access_token,
    mint_narrow_scope_token,
    speech_provider_body,
)

logger = logging.getLogger("chat-speech-audit")

_PROVIDERS_PATH = "/api/v1/configurationManager/ai-models/providers"
_SPEECH_CONFIG_LOCK = Path("/tmp/pipeshub-spec-audit-chat-speech.lock")


@pytest.fixture(scope="session")
def chat_speech_client(pipeshub_client: PipeshubClient) -> ChatSpeechClient:
    return ChatSpeechClient(pipeshub_client)


def _capabilities(client: ChatSpeechClient) -> SpeechCapabilities:
    resp = client.capabilities()
    if resp.status_code != 200:
        pytest.fail(
            f"speech capabilities returned {resp.status_code}, cannot tell whether "
            f"a TTS/STT provider is configured: {resp.text[:200]}"
        )
    return resp.json()


@pytest.fixture
def speech_config_lock() -> Iterator[None]:
    """Serialises tests that read or change the org's speech providers across xdist workers.

    Without it one worker's fake provider breaks another's "no provider" test, and concurrent
    provider writes to the shared AI model config lose each other's entries.
    """
    with _SPEECH_CONFIG_LOCK.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@pytest.fixture
def no_speech_provider(
    chat_speech_client: ChatSpeechClient, speech_config_lock: None
) -> SpeechCapabilities:
    """The org has neither a TTS nor an STT provider, which this stack ships with."""
    capabilities = _capabilities(chat_speech_client)
    if capabilities.get("tts") or capabilities.get("stt"):
        pytest.fail(
            "a TTS or STT provider is configured on this org outside these tests; "
            f"the 409 paths cannot be reached: {capabilities}"
        )
    return capabilities


@pytest.fixture(scope="session")
def fake_provider_endpoint() -> Iterator[str]:
    with fake_openai_audio_server() as endpoint:
        yield endpoint


@pytest.fixture
def fake_speech_providers(
    pipeshub_client: PipeshubClient,
    no_speech_provider: SpeechCapabilities,
    fake_provider_endpoint: str,
) -> Iterator[dict[str, str]]:
    """A LiteLLM-proxy TTS and STT provider pointed at the local fake; removed on teardown.

    Yields ``{"tts": modelKey, "stt": modelKey}``.
    """
    created: dict[str, str] = {}
    try:
        for kind in ("tts", "stt"):
            resp = pipeshub_client.request(
                "POST", _PROVIDERS_PATH, json=speech_provider_body(kind, fake_provider_endpoint)
            )
            assert resp.status_code < 300, f"adding the fake {kind} provider: {resp.status_code} {resp.text[:300]}"
            created[kind] = resp.json()["details"]["modelKey"]
        yield created
    finally:
        for kind, model_key in created.items():
            resp = pipeshub_client.request("DELETE", f"{_PROVIDERS_PATH}/{kind}/{model_key}")
            if resp.status_code >= 300:
                logger.warning("could not delete the fake %s provider %s: %s", kind, model_key, resp.text[:300])


@pytest.fixture(scope="session")
def narrow_scope_headers(pipeshub_client: PipeshubClient) -> Iterator[dict[str, str]]:
    """Headers of an OAuth token of the suite's own client without ``conversation:chat``."""
    token = mint_narrow_scope_token(pipeshub_client.base_url, pipeshub_client.timeout_seconds)
    try:
        yield {"Authorization": f"Bearer {token}"}
    finally:
        forget_access_token(token)
