"""Counts the requests every chat and embedding provider's SDK puts on the wire for one call (run by
``tests/integration/spend/test_sdk_retries_on_the_wire.py`` in its own interpreter, ``python -I``).

The transports themselves are patched (httpx sync and async, requests, aiohttp, botocore before-send), so every request any
SDK makes is counted whatever URL it targets, and none leaves the process. Each model is built with
``sdk_retries=INDEXING_SDK_RETRIES`` and called once directly (no gateway): more than one request means the SDK retried. The
same model built with the SDK default is the control.

Usage, from backend/python:  PYTHONPATH=. python -I tests/support/sdk_wire_probe.py [status] [provider]
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any

sys.path.insert(0, os.getcwd())

import httpx
import requests

STATUS = int(sys.argv[1]) if len(sys.argv) > 1 else 500
COUNT = {"n": 0}


def _body() -> bytes:
    return json.dumps({"error": {"message": "boom", "code": STATUS, "status": "INTERNAL", "type": "server_error"},
                       "message": "boom"}).encode()


HEADERS = {"content-type": "application/json", "retry-after": "0", "retry-after-ms": "0", "x-should-retry": "true"}


async def _ahandle(self: Any, request: httpx.Request) -> httpx.Response:  # noqa: ANN401
    COUNT["n"] += 1
    if STATUS == 0:  # a dropped connection
        raise httpx.ConnectError("QA probe: connection reset", request=request)
    return httpx.Response(STATUS, headers=HEADERS, content=_body(), request=request)


def _handle(self: Any, request: httpx.Request) -> httpx.Response:  # noqa: ANN401
    COUNT["n"] += 1
    if STATUS == 0:
        raise httpx.ConnectError("QA probe: connection reset", request=request)
    return httpx.Response(STATUS, headers=HEADERS, content=_body(), request=request)


httpx.AsyncHTTPTransport.handle_async_request = _ahandle  # type: ignore[method-assign]
httpx.HTTPTransport.handle_request = _handle  # type: ignore[method-assign]
# Clients that put aiohttp under httpx (fireworks-ai) or call aiohttp directly: counted, and never on the network.
import aiohttp  # noqa: E402
import httpx_aiohttp.transport  # noqa: E402

httpx_aiohttp.transport.AiohttpTransport.handle_async_request = _ahandle  # type: ignore[method-assign]


async def _aio_request(self: Any, method: str, url: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    COUNT["n"] += 1
    raise aiohttp.ClientConnectionError("QA probe: aiohttp request blocked (counted)")


aiohttp.ClientSession._request = _aio_request  # type: ignore[method-assign]


def _req_send(self: Any, request: Any, **kwargs: Any) -> requests.Response:  # noqa: ANN401
    COUNT["n"] += 1
    if STATUS == 0:
        raise requests.ConnectionError("QA probe: connection reset")
    r = requests.Response()
    r.status_code = STATUS
    r._content = _body()
    r.headers.update(HEADERS)
    r.url = request.url
    r.request = request
    return r


requests.adapters.HTTPAdapter.send = _req_send  # type: ignore[method-assign]

# SDK backoffs change no count: sleep nothing.
import anyio  # noqa: E402

_real_asleep = asyncio.sleep


async def _nosleep(_s: float = 0, *a: Any, **k: Any) -> Any:  # noqa: ANN401
    return await _real_asleep(0)


asyncio.sleep = _nosleep  # type: ignore[assignment]
anyio.sleep = _nosleep  # type: ignore[assignment]
time.sleep = lambda _s: None  # type: ignore[assignment]

# Vertex AI: give the service account a token without the network.
from google.oauth2 import service_account  # noqa: E402


def _refresh(self: Any, request: Any) -> None:  # noqa: ANN401
    import datetime

    self.token = "t"
    self.expiry = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)


service_account.Credentials.refresh = _refresh  # type: ignore[method-assign]

os.environ.setdefault("LLM_WIRE_HOOKS_REQUIRED", "false")
os.environ["AWS_EC2_METADATA_DISABLED"] = "true"  # no instance-metadata lookup: static credentials only

from app.utils.aimodels import (  # noqa: E402
    INDEXING_SDK_RETRIES,
    EmbeddingProvider,
    LLMProvider,
    get_embedding_model,
    get_generator_model,
)
from tests.support.provider_configs import BASE, service_account_json  # noqa: E402

CONF = {**BASE, "serviceAccountJson": service_account_json(), "endpoint": "https://gateway.example.com/v1"}


def _hook_boto(model: Any) -> None:  # noqa: ANN401
    client = getattr(model, "client", None)
    meta = getattr(client, "meta", None)
    if meta is None:
        return
    from botocore.awsrequest import AWSResponse

    class _Raw:
        def stream(self, **_: Any) -> Any:  # noqa: ANN401
            yield _body()

    def before_send(request: Any, **_: Any) -> AWSResponse:  # noqa: ANN401
        COUNT["n"] += 1
        if STATUS == 0:
            from botocore.exceptions import EndpointConnectionError

            raise EndpointConnectionError(endpoint_url=request.url)
        return AWSResponse(request.url, STATUS, {"content-type": "application/json", "x-amzn-ErrorType": "InternalServerException"}, _Raw())

    meta.events.register("before-send.*", before_send)


async def _one(kind: str, provider: str, model_name: str, sdk_retries: int | None) -> tuple[int, str, str]:
    config = {"configuration": {**CONF, "model": model_name}, "isDefault": True}
    if kind == "chat":
        model = get_generator_model(provider, config, model_name, sdk_retries=sdk_retries)
    else:
        model = get_embedding_model(provider, config, model_name, sdk_retries=sdk_retries)
    _hook_boto(model)
    COUNT["n"] = 0
    err = ""
    started = time.monotonic()
    try:
        if kind == "chat":
            await asyncio.wait_for(model.ainvoke("DOC:x hello"), 120)
        else:
            await asyncio.wait_for(model.aembed_documents(["DOC:x"]), 120)
        err = "no error?!"
    except Exception as exc:
        err = f"{type(exc).__name__}"
    return COUNT["n"], type(model).__name__, f"{err} {time.monotonic() - started:.1f}s"


CHAT = [(p.value, "m") for p in LLMProvider] + [("fireworks", "accounts/fireworks/models/llama-v3p1-8b-instruct"), ("azureAI", "claude-3-5-sonnet"), ("anthropic", "claude-3-5-sonnet")]
EMB = [(p.value, "m") for p in EmbeddingProvider if p.value not in {"default", "huggingFace", "sentenceTransformers", "anthropic"}]
NO_LAYER = {"ollama"}  # chat/embeddings clients with no retry layer of their own (default 1 too)


async def main() -> int:
    bad = 0
    print(f"status={STATUS}")
    only = sys.argv[2] if len(sys.argv) > 2 else None
    for kind, rows in (("chat", CHAT), ("emb", EMB)):
        for provider, model_name in rows:
            if only and provider != only:
                continue
            try:
                n0, wrapper, info0 = await _one(kind, provider, model_name, INDEXING_SDK_RETRIES)
                nd, _, infod = await _one(kind, provider, model_name, None)
            except Exception as exc:
                print(f"BUILD-ERR {kind:4} {provider:18} {type(exc).__name__}: {str(exc)[:100]}")
                bad += 1
                continue
            verdict = "OK" if n0 == 1 else ("UNSEEN" if n0 == 0 else "SDK-RETRIES")
            if verdict != "OK":
                bad += 1
            print(f"{verdict:11} {kind:4} {provider:18} {wrapper:30} indexing={n0} ({info0})  default={nd} ({infod})")
    print(f"bad={bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
