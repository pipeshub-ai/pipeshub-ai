"""Every direct transport built over an ``httpx.MockTransport``: the real SDK (or raw HTTP) path runs and only the
socket is fake. SDK retries are off so one call is one exchange."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anthropic
import httpx
import openai
from google import genai
from google.genai import types as genai_types

from harness_core.models.sigv4 import AwsCredentials
from harness_core.models.transports.anthropic import AnthropicTransport
from harness_core.models.transports.azure_openai import AzureOpenAITransport
from harness_core.models.transports.bedrock_converse import BedrockConverseTransport
from harness_core.models.transports.gemini import GeminiTransport
from harness_core.models.transports.ollama import OllamaTransport
from harness_core.models.transports.openai import OpenAITransport

if TYPE_CHECKING:
    from harness_core.models.transports.base import LLMTransport

Handler = Callable[[httpx.Request], httpx.Response]

CASSETTES = Path(__file__).parent / "cassettes"


def _http(handler: Handler, base_url: str = "") -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=base_url, transport=httpx.MockTransport(handler))


def openai_over(handler: Handler) -> OpenAITransport:
    transport = OpenAITransport(api_key="sk-test", model="gpt-4o")
    transport._client = openai.AsyncOpenAI(  # pyright: ignore[reportPrivateUsage]
        api_key="sk-test", base_url="http://openai.test/v1", max_retries=0, http_client=_http(handler)
    )
    return transport


def azure_over(handler: Handler) -> AzureOpenAITransport:
    transport = AzureOpenAITransport(
        api_key="az-test", azure_endpoint="https://pxh.openai.azure.com", api_version="2024-10-21", deployment="gpt-4o"
    )
    transport._client = openai.AsyncAzureOpenAI(  # pyright: ignore[reportPrivateUsage]
        api_key="az-test",
        azure_endpoint="https://pxh.openai.azure.com",
        api_version="2024-10-21",
        azure_deployment="gpt-4o",
        max_retries=0,
        http_client=_http(handler),
    )
    return transport


def anthropic_over(handler: Handler) -> AnthropicTransport:
    client = anthropic.AsyncAnthropic(
        api_key="sk-ant-test", base_url="http://anthropic.test", max_retries=0, http_client=_http(handler)
    )
    return AnthropicTransport(api_key="sk-ant-test", model="claude-sonnet-4-5", client=client)


def gemini_over(handler: Handler) -> GeminiTransport:
    transport = GeminiTransport(api_key="g-test", model="gemini-2.5-flash")
    transport._client = genai.Client(  # pyright: ignore[reportPrivateUsage]
        api_key="g-test", http_options=genai_types.HttpOptions(httpx_async_client=_http(handler))
    )
    return transport


def ollama_over(handler: Handler) -> OllamaTransport:
    transport = OllamaTransport(model="llama3.2")
    transport._client = _http(handler, "http://ollama.test")  # pyright: ignore[reportPrivateUsage]
    return transport


def bedrock_over(handler: Handler) -> BedrockConverseTransport:
    return BedrockConverseTransport(
        region="us-east-1",
        credentials=AwsCredentials("AKIDTEST", "secret"),
        model="anthropic.claude-sonnet-4-5",
        http=_http(handler),
    )


@dataclass(frozen=True)
class Rig:
    """One provider: how to build its transport over a handler, and its documented error body for a status."""

    name: str
    build: Callable[[Handler], LLMTransport]
    error: Callable[[int], httpx.Response]


def _openai_error(status: int) -> httpx.Response:
    return httpx.Response(status, json={"error": {"message": "upstream said no", "type": "server_error", "code": None}})


def _anthropic_error(status: int) -> httpx.Response:
    kind = {429: "rate_limit_error", 529: "overloaded_error"}.get(status, "api_error")
    return httpx.Response(status, json={"type": "error", "error": {"type": kind, "message": "upstream said no"}})


def _gemini_error(status: int) -> httpx.Response:
    name = {429: "RESOURCE_EXHAUSTED", 503: "UNAVAILABLE", 504: "DEADLINE_EXCEEDED"}.get(status, "INTERNAL")
    return httpx.Response(status, json={"error": {"code": status, "message": "upstream said no", "status": name}})


def _ollama_error(status: int) -> httpx.Response:
    return httpx.Response(status, json={"error": "upstream said no"})


def _bedrock_error(status: int) -> httpx.Response:
    kind = {429: "ThrottlingException", 503: "ServiceUnavailableException"}.get(status, "InternalServerException")
    return httpx.Response(status, json={"message": "upstream said no"}, headers={"x-amzn-errortype": kind})


RIGS: tuple[Rig, ...] = (
    Rig("openai", openai_over, _openai_error),
    Rig("azure_openai", azure_over, _openai_error),
    Rig("anthropic", anthropic_over, _anthropic_error),
    Rig("gemini", gemini_over, _gemini_error),
    Rig("ollama", ollama_over, _ollama_error),
    Rig("bedrock", bedrock_over, _bedrock_error),
)


def cassette(provider: str, name: str) -> Any:  # noqa: ANN401 - a recorded wire body is whatever the provider sent
    return json.loads((CASSETTES / provider / name).read_text())


def cassette_text(provider: str, name: str) -> str:
    return (CASSETTES / provider / name).read_text()
