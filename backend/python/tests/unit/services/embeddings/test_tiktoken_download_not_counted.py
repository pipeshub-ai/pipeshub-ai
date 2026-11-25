"""langchain-openai's tiktoken vocabulary download is not one of a job's paid sends.

On a pod with no cached vocabulary the first OpenAI or Azure embedding downloads it with ``requests``. The send meter and the
wire scope are fed by hooks on the model's own httpx (and botocore) clients, so a ``requests`` call elsewhere is neither
admitted against the allowance nor counted. These tests pin that with the production wiring: an allowance of one send, an empty
tiktoken cache, a blocked download host and a local fake endpoint (nothing leaves the process).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import requests

from app.services.llm_gateway import wire
from app.utils.aimodels import INDEXING_SDK_RETRIES, get_embedding_model
from tests.support.provider_configs import BASE

DOWNLOAD_HOST = "openaipublic.blob.core.windows.net"


def _model(provider: str) -> Any:  # noqa: ANN401
    config = {"configuration": {**BASE, "model": "text-embedding-3-small", "endpoint": "https://gateway.example.com/v1"},
              "isDefault": True}
    return get_embedding_model(provider, config, "text-embedding-3-small", sdk_retries=INDEXING_SDK_RETRIES)


@pytest.fixture
def downloads(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> list[str]:  # noqa: ANN401
    """An empty tiktoken cache, and the download host blocked (it fails at once and is recorded)."""
    import tiktoken.registry

    # tiktoken keeps built encodings for the life of the process: an earlier test that built cl100k_base would otherwise
    # answer without looking at the (empty) cache directory, and no download would be attempted.
    monkeypatch.setattr(tiktoken.registry, "ENCODINGS", {})
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("DATA_GYM_CACHE_DIR", raising=False)
    seen: list[str] = []

    def send(self: Any, request: Any, **_: Any) -> Any:  # noqa: ANN401
        seen.append(request.url)
        raise requests.exceptions.ConnectionError(f"blocked: {request.url}")

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)

    async def handle(self: Any, request: httpx.Request) -> httpx.Response:  # noqa: ANN401
        seen.append(str(request.url))
        body = {"object": "list", "model": "m", "usage": {"prompt_tokens": 1, "total_tokens": 1},
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}]}
        return httpx.Response(200, content=json.dumps(body).encode(), headers={"content-type": "application/json"},
                              request=request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    return seen


@pytest.mark.parametrize("provider", ["azureOpenAI", "azureAI"])
async def test_the_vocabulary_download_is_neither_admitted_nor_counted(provider: str, downloads: list[str]) -> None:
    model = _model(provider)
    meter = wire.SendMeter(allowance=1)
    with wire.metered(meter), wire.wire_scope() as seen, pytest.raises(requests.exceptions.ConnectionError):
        await model.aembed_documents(["hello"])
    assert any(DOWNLOAD_HOST in url for url in downloads), "the download was not attempted: the test no longer tests anything"
    assert seen.requests == 0 and meter.failed == 0, "the download was counted as a send"
    assert not seen.allowance_refused


async def test_the_one_allowed_send_still_goes_to_the_endpoint_after_the_download(
    downloads: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the vocabulary in hand (the download done), the single allowed send is the embedding request itself."""
    import tiktoken

    class Encoding:
        def encode_ordinary(self, text: str) -> list[int]:
            return [1]

        def encode(self, text: str, **_: Any) -> list[int]:  # noqa: ANN401
            return [1]

    monkeypatch.setattr(tiktoken, "encoding_for_model", lambda *_a, **_k: Encoding())
    monkeypatch.setattr(tiktoken, "get_encoding", lambda *_a, **_k: Encoding())
    model = _model("azureOpenAI")
    with wire.metered(wire.SendMeter(allowance=1)), wire.wire_scope() as seen:
        vectors = await model.aembed_documents(["hello"])
    assert vectors == [[0.1, 0.2]] and seen.requests == 1
    assert not any(DOWNLOAD_HOST in url for url in downloads)
