"""One retry layer, end to end: a real client built by the factory with the indexing setting, against a real socket
that answers 500, puts exactly one request on the wire per gateway attempt, and three for the logical call.

Without the setting (the SDK's defaults) the same call multiplies: the SDK's own retries sit inside each of the gateway's
attempts. No external network; the socket is a local fake, and the gateway's sleeps are skipped.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.llm_gateway import gateway as gateway_module
from app.services.llm_gateway.gateway import (
    LLMGateway,
    ProviderUnavailableError,
    provider_key,
)
from app.services.spend_guard.retry_policy import MAX_ATTEMPTS
from app.utils.aimodels import (
    INDEXING_SDK_RETRIES,
    get_embedding_model,
    get_generator_model,
)
from tests.support.fake_llm_server import FakeLLMServer, Reply
from tests.support.provider_configs import BASE

pytestmark = pytest.mark.timeout(180)


@pytest.fixture(autouse=True)
def _no_aws_lookup_leaves_the_process(monkeypatch: pytest.MonkeyPatch) -> Any:  # noqa: ANN401
    """Static dummy credentials and a disabled metadata service: building a Bedrock client connects nowhere but the local fake.
    Every connection the process opens is recorded; one to another host (the instance-metadata address) fails the test."""
    import socket

    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIADUMMY")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "dummy")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    foreign: list[object] = []
    real = socket.create_connection

    def watched(address: tuple[str, int], *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        if address[0] not in {"127.0.0.1", "localhost", "::1"}:
            foreign.append(address)
        return real(address, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", watched)
    yield
    assert not foreign, f"a connection left for another host: {foreign}"


@pytest.fixture(autouse=True)
def _no_gateway_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(gateway_module.asyncio, "sleep", instant)


# provider -> (configuration overrides for the fake's address, environment variables that aim the SDK at it)
def _sites(base: str) -> dict[str, tuple[dict[str, str], dict[str, str]]]:
    return {
        "openAI": ({"endpoint": f"{base}/v1"}, {}),
        "openAICompatible": ({"endpoint": f"{base}/v1"}, {}),
        "lmStudio": ({"endpoint": f"{base}/v1"}, {}),
        "litellmProxy": ({"endpoint": f"{base}/v1"}, {}),
        "azureAI": ({"endpoint": f"{base}/v1", "model": "gpt-x"}, {}),
        "together": ({"endpoint": f"{base}/v1"}, {}),
        "ollama": ({"endpoint": base}, {}),
        "anthropic": ({}, {"ANTHROPIC_API_URL": base}),
        "groq": ({}, {"GROQ_API_BASE": base}),
        "mistral": ({}, {"MISTRAL_BASE_URL": base}),
        "xai": ({}, {"XAI_API_BASE": f"{base}/v1"}),
        "bedrock": ({}, {"AWS_ENDPOINT_URL_BEDROCK_RUNTIME": base}),
    }


SITES = sorted(_sites("http://x"))


async def _requests_for_one_logical_call(
    monkeypatch: pytest.MonkeyPatch, provider: str, *, sdk_retries: int | None, status: int = 500
) -> tuple[int, str]:
    server = await FakeLLMServer(lambda body: Reply(status=status)).start()
    try:
        overrides, env = _sites(f"http://127.0.0.1:{server.port}")[provider]
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        configuration = {**BASE, **overrides}
        model = get_generator_model(provider, {"configuration": configuration, "isDefault": True}, "m", sdk_retries=sdk_retries)
        with pytest.raises(ProviderUnavailableError):
            await LLMGateway(2).invoke(model, "DOC:x", provider=provider_key(model))
        return server.total, type(model).__name__
    finally:
        await server.stop()


@pytest.mark.parametrize("status", [500, 429])
@pytest.mark.parametrize("site", SITES)
async def test_one_request_per_attempt(monkeypatch: pytest.MonkeyPatch, site: str, status: int) -> None:
    total, wrapper = await _requests_for_one_logical_call(
        monkeypatch, site, sdk_retries=INDEXING_SDK_RETRIES, status=status
    )
    assert total == MAX_ATTEMPTS, f"{site} ({wrapper}) HTTP {status}: {total} requests reached the socket, expected 3"


async def test_negative_control_sdk_defaults_multiply_the_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    """Built without the indexing setting, the OpenAI client retries a 429 twice inside each gateway attempt: more requests
    reach the provider than the three one logical call is allowed (nine, until the process breaker opens after five failures
    and the next attempt is refused: six)."""
    total, _ = await _requests_for_one_logical_call(monkeypatch, "openAI", sdk_retries=None, status=429)
    assert total > MAX_ATTEMPTS


@pytest.mark.parametrize("provider", ["openAI", "openAICompatible", "together"])
async def test_embeddings_send_one_request_per_attempt(monkeypatch: pytest.MonkeyPatch, provider: str) -> None:
    """A remote embeddings client built for indexing makes one request per attempt; the EmbeddingGateway repeats it, three
    requests for one logical call."""
    from app.services.llm_gateway.embedding_gateway import EmbeddingGateway
    from app.services.spend_guard.retry_policy import RetryBudget

    server = await FakeLLMServer(lambda body: Reply(status=500)).start()
    try:
        configuration = {**BASE, "endpoint": f"http://127.0.0.1:{server.port}/v1"}
        embeddings = get_embedding_model(
            provider, {"configuration": configuration, "isDefault": True}, "m", sdk_retries=INDEXING_SDK_RETRIES
        )

        async def sleepless(_seconds: float) -> None:
            return None

        from app.services.llm_gateway import embedding_gateway as module

        monkeypatch.setattr(module.asyncio, "sleep", sleepless)
        with pytest.raises(Exception):  # noqa: B017 - the provider's own error type, whatever the wrapper
            await EmbeddingGateway(retry_budget=RetryBudget()).call(lambda: embeddings.aembed_documents(["DOC:x"]))
        assert server.total == MAX_ATTEMPTS, f"{provider}: {server.total} requests for one logical call"
    finally:
        await server.stop()
