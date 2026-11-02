from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest

from app.api.routes.health import _settings_refusal


def _config(provider: str, endpoint: str) -> dict:
    return {
        "provider": provider,
        "configuration": {"model": "gpt-5.6-luna", "apiKey": "sk-test", "endpoint": endpoint},
    }


@pytest.fixture(autouse=True)
def _no_dns() -> Iterator[None]:
    with patch("app.api.routes.health.require_public_endpoint", new=AsyncMock()):
        yield


@pytest.mark.asyncio
@pytest.mark.parametrize(("endpoint", "base"), [
    ("https://us.api.openai.com/v1/responses", "https://us.api.openai.com/v1"),
    ("https://api.openai.com/v1/chat/completions/", "https://api.openai.com/v1"),
    ("http://vllm.internal:8000/v1/completions", "http://vllm.internal:8000/v1"),
    ("https://gw.example.com/openai/v1/embeddings", "https://gw.example.com/openai/v1"),
    ("https://gw.example.com/v1/Responses", "https://gw.example.com/v1"),
])
@pytest.mark.parametrize("provider", ["openAI", "openAICompatible", "litellmProxy"])
async def test_operation_path_in_endpoint_is_refused_with_the_base_url(
    provider: str, endpoint: str, base: str,
) -> None:
    """The SDK appends the operation path itself; a pasted one doubles it and
    the provider's 404 reads as "model not found"."""
    response = await _settings_refusal("llm", _config(provider, endpoint))
    assert response is not None
    assert response.status_code == 400
    body = response.body.decode()
    assert base in body
    assert "invalid_endpoint_path" in body


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", [
    "https://us.api.openai.com/v1",
    "https://api.openai.com/v1/",
    "https://gw.example.com/responses-proxy/v1",
    "https://gw.example.com/embeddings/v1",
])
async def test_base_urls_pass(endpoint: str) -> None:
    assert await _settings_refusal("llm", _config("openAICompatible", endpoint)) is None


@pytest.mark.asyncio
async def test_providers_that_do_not_take_an_sdk_base_url_are_not_checked() -> None:
    config = _config("azureOpenAI", "https://res.openai.azure.com/openai/v1/responses")
    assert await _settings_refusal("llm", config) is None
