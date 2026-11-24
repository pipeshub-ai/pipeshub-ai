"""How many times the SDK client inside a built model repeats a failed request on its own.

Read from the client objects the factories build (no request is sent), one reader per wrapper family. A model of a family
this module does not know raises, so a new provider cannot pass the call-site contract unmeasured.
"""

from __future__ import annotations

from typing import Any

from app.services.llm_gateway import wire


def _attr(obj: Any, path: str) -> Any:  # noqa: ANN401
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


def _all(model: Any, paths: tuple[str, ...]) -> int:  # noqa: ANN401
    """The largest retry count over the clients found at ``paths`` (at least one must exist)."""
    counts = []
    for path in paths:
        try:
            counts.append(int(_attr(model, path)))
        except AttributeError:
            continue
    if not counts:
        raise AssertionError(f"{type(model).__name__}: none of {paths} exists; teach tests/support/sdk_retries.py")
    return max(counts)


def _genai_uses_httpx(model: Any) -> bool:  # noqa: ANN401
    apis = list(wire._genai_api_clients(model))
    return bool(apis) and all(not api._use_aiohttp() for api in apis)


def sdk_retries(model: Any) -> int:  # noqa: ANN401
    """Retries (repeats after the first request) one request of ``model`` may get from its SDK."""
    name = type(model).__name__
    # OpenAI-protocol chat and embeddings clients (OpenAI, Azure, xAI, Together, ...).
    openai_paths = ("root_client.max_retries", "root_async_client.max_retries", "client._client.max_retries",
                    "async_client._client.max_retries")
    if name in {"ChatOpenAI", "AzureChatOpenAI", "ChatXAI", "ChatTogether", "ChatGroq", "OpenAIEmbeddings",
                "AzureOpenAIEmbeddings", "TogetherEmbeddings"}:
        return _all(model, openai_paths)
    if name == "ChatAnthropic":
        return _all(model, ("_client.max_retries", "_async_client.max_retries"))
    if name in {"ChatBedrockConverse", "BedrockEmbeddings"}:
        retries = model.client.meta.config.retries
        total = retries.get("total_max_attempts")
        return int(total) - 1 if total is not None else int(retries.get("max_attempts", 0))
    if name == "ChatFireworks":
        # Its own tenacity layer (0 or None disables it) and the Fireworks SDK client's.
        return max(int(model.max_retries or 0), _all(model, ("client._client.max_retries",
                                                             "async_client._client.max_retries")))
    if name == "FireworksEmbeddings":
        return int(model.client.max_retries)
    if name in {"ChatMistralAI", "MistralAIEmbeddings"}:
        # ``max_retries`` is a tenacity attempt count (0 and 1 both mean one attempt).
        return max(0, int(model.max_retries) - 1)
    if name == "ChatGoogleGenerativeAI":
        # google-genai counts attempts, and sends aiohttp requests (with an implicit retry) unless routed through httpx.
        if not _genai_uses_httpx(model):
            return 99
        return max(0, int(model.max_retries) - 1)
    if name == "GoogleGenerativeAIEmbeddings":
        # The transport (aiohttp repeats a dropped connection by itself) and the retry options (attempts) are separate.
        if not _genai_uses_httpx(model):
            return 99
        attempts = [
            getattr(getattr(api._http_options, "retry_options", None), "attempts", None)
            for api in wire._genai_api_clients(model)
        ]
        return max((max(0, int(a) - 1) for a in attempts if a), default=0)
    if name in {"ChatCohere", "CohereEmbeddings"}:
        # The HTTP client retries 408, 429 and 5xx twice unless the request option says otherwise; the embeddings
        # wrapper adds a tenacity layer of its own that counts attempts.
        http = _cohere_http_retries(model)
        if name == "CohereEmbeddings":
            return max(http, max(0, int(model.max_retries) - 1))
        return http
    if name == "VoyageEmbeddings":
        return max(0, int(model.max_retries) - 1)
    if name in {"ChatOllama", "OllamaEmbeddings", "JinaEmbeddings"}:
        return 0  # no retry layer in the client
    raise AssertionError(f"no reader for {name}: teach tests/support/sdk_retries.py")


def _cohere_http_retries(model: Any) -> int:  # noqa: ANN401
    """How many times the Cohere SDK repeats a request that gets a 500, measured on the client the factory built: its
    underlying httpx client is given a transport that answers 500 and counts, a request goes through the SDK's own
    ``HttpClient.request`` (the factory's wrapper included), and the SDK's backoff sleep is skipped."""
    from unittest.mock import patch

    import httpx

    http = model.client._client_wrapper.httpx_client
    requests: list[int] = []

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(1)
        return httpx.Response(500, json={})

    original = http.httpx_client
    http.httpx_client = httpx.Client(transport=httpx.MockTransport(answer))
    try:
        with patch("cohere.core.http_client.time.sleep"):
            http.request("v2/chat", method="POST", json={})
    finally:
        http.httpx_client = original
    return len(requests) - 1
