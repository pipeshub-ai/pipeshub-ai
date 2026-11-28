"""The decoding adapter against the unadapted client: malformed answers, token-array chunking, body variants,
identity headers, providers outside the seam, float parsing, cancellation and the caller's retry loop.

Promoted from the QA probes of G34. Every comparison builds the same client twice, with the adapter switched off and on.
"""

from __future__ import annotations

import asyncio
import base64
import json
import random
import struct
import threading
import time
from typing import TYPE_CHECKING, Any

import numpy as np
import orjson
import pytest
from langchain_openai.embeddings import OpenAIEmbeddings
from openai._models import construct_type
from openai.types import CreateEmbeddingResponse

from app.utils import embedding_response_adapter as adapter
from app.utils.aimodels import get_embedding_model
from app.utils.embedding_response_adapter import decode_embedding_responses
from app.utils.embedding_response_decoder import decode_embedding_response
from app.utils.embedding_retry import is_retriable_embedding_error
from app.utils.error_class import classify_error, retry_later
from tests.unit.utils.embedding_fake_server import FakeEmbeddingServer, content_vector

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from langchain_core.embeddings import Embeddings

SEED = 20261127


def _config(endpoint: str, **configuration: object) -> dict[str, Any]:
    return {"isDefault": True, "configuration": {"model": "m", "apiKey": "k", "endpoint": endpoint, **configuration}}


def _build(provider: str, url: str, adapted: bool, monkeypatch: pytest.MonkeyPatch, **conf: object) -> Embeddings:
    with monkeypatch.context() as patch:
        if not adapted:
            patch.setattr("app.utils.embedding_response_adapter.decode_embedding_responses", lambda model: model)
        return get_embedding_model(provider, _config(url, **conf))


def _b64(vector: list[float]) -> str:
    return base64.b64encode(np.asarray(vector, dtype="<f4").tobytes()).decode()


@pytest.fixture
def server() -> Iterator[FakeEmbeddingServer]:
    fake = FakeEmbeddingServer(dims=16, by_content=True).start()
    try:
        yield fake
    finally:
        fake.stop()


MALFORMED: dict[str, bytes] = {
    "truncated": b'{"data": [{"embedding": [0.1, 0.2',
    "html": b"<html>bad gateway</html>",
    "empty": b"",
    "no data": b'{"object": "list"}',
    "empty data": b'{"object":"list","data":[]}',
    "wrong count": json.dumps({"data": [{"embedding": [0.5] * 16}]}).encode(),
    "ragged": json.dumps({"data": [{"embedding": [0.5] * 16}] * 3 + [{"embedding": [0.5] * 15}]}).encode(),
    "nan literal": b'{"data": [' + b",".join([b'{"embedding": [NaN, 1.0]}'] * 4) + b"]}",
    "string element": json.dumps({"data": [{"embedding": [1.0, "2"]}] * 4}).encode(),
    "null embedding": json.dumps({"data": [{"embedding": None}] * 4}).encode(),
    "error object 200": b'{"error": {"message": "upstream overloaded", "type": "server_error"}}',
}


def _outcome(call: Callable[[], list[list[float]]]) -> tuple[Any, ...]:
    from app.services.llm_gateway.embedding_gateway import EmbeddingGateway

    try:
        result = call()
    except Exception as exc:
        return ("err", type(exc).__name__, classify_error(exc), is_retriable_embedding_error(exc), retry_later(exc),
                EmbeddingGateway.retry_kind(exc))
    return ("ok", len(result))


@pytest.mark.parametrize("label", list(MALFORMED))
def test_a_malformed_200_never_gives_vectors_and_is_never_retried(
    server: FakeEmbeddingServer, label: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    server.custom = MALFORMED[label]
    texts = ["a", "b", "c", "d"]
    before = _outcome(lambda: _build("openAICompatible", server.base_url, False, monkeypatch).embed_documents(texts))
    after = _outcome(lambda: _build("openAICompatible", server.base_url, True, monkeypatch).embed_documents(texts))
    assert after[0] == "err"
    assert after[1] == "EmbeddingResponseError"
    if before[0] == "err":  # where the SDK path also failed, error class and retry decision are the same
        assert after[3:] == before[3:]
    assert after[5] is None
    assert after[3] is False


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_the_token_array_chunked_path_is_identical(server: FakeEmbeddingServer, asynchronous: bool) -> None:
    """check_embedding_ctx_length on: texts become token arrays, are split by length, sent in several requests and averaged."""
    rng = random.Random(SEED)
    words = ["alpha", "beta", "gamma", "delta", "kernel", "chromium", "render", "frame"]
    texts = [" ".join(rng.choice(words) for _ in range(rng.randint(1, 140))) for _ in range(23)]
    results = []
    for adapted in (False, True):
        model = OpenAIEmbeddings(
            model="text-embedding-3-large", api_key="k", base_url=server.base_url, check_embedding_ctx_length=True,
            embedding_ctx_length=40, chunk_size=5, max_retries=0,
        )
        if adapted:
            decode_embedding_responses(model)
            assert "create" in vars(model.client)
        server.requests.clear()
        results.append(await model.aembed_documents(texts) if asynchronous else model.embed_documents(texts))
        sent = [r["body"]["input"] for r in server.requests]
        assert all(isinstance(batch[0], list) for batch in sent), "token arrays expected"
        assert len(sent) > 3, "several requests expected"
    assert results[0] == results[1]
    assert len(results[1]) == len(texts)


@pytest.mark.parametrize("variant", ["extra fields", "mixed", "shuffled index", "ints"])
def test_body_variants_answer_as_the_unadapted_client_does(
    server: FakeEmbeddingServer, variant: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    texts = ["one", "two", "three", "four"]
    vectors = [content_vector(t, 16) for t in texts]
    items: list[dict[str, Any]] = [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)]
    payload: dict[str, Any] = {"object": "list", "data": items, "model": "m", "usage": {"prompt_tokens": 4, "total_tokens": 4}}
    if variant == "extra fields":
        payload["x_gateway"] = {"cost": 0.1}
        payload["id"] = "emb-1"
        for item in items:
            item["extra"] = [1, 2]
    elif variant == "mixed":
        for i in (1, 3):
            items[i]["embedding"] = _b64(vectors[i])
    elif variant == "shuffled index":  # neither path reorders by index
        payload["data"] = [items[2], items[0], items[3], items[1]]
    elif variant == "ints":
        items[0]["embedding"] = [1] * 16
    server.custom = json.dumps(payload).encode()
    after = _build("openAICompatible", server.base_url, True, monkeypatch).embed_documents(texts)
    if variant == "mixed":  # the SDK path leaves an item it did not ask to be base64 undecoded; here all are vectors
        assert after == vectors
    else:
        assert after == _build("openAICompatible", server.base_url, False, monkeypatch).embed_documents(texts)


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_identity_headers_reach_the_gateway_with_the_adapter(
    server: FakeEmbeddingServer, asynchronous: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.model_identity.call_context import (
        ModelCallContext,
        bind_call_context,
    )
    from app.services.model_identity.principal import user_principal
    from app.services.model_identity.workload import Workload

    alice = user_principal({"userId": "u-alice", "email": "alice@example.com"}, "org-1")
    model = _build("litellmProxy", server.base_url, True, monkeypatch, identityForwarding={"enabled": True})
    assert "create" in vars(model.client)
    assert "create" in vars(model.async_client)
    ctx = ModelCallContext(principal=alice, workload=Workload.QUERY_EMBEDDING, request_id="req-qa")
    if asynchronous:
        async with bind_call_context(ctx):
            out = await model.aembed_documents(["x", "y"])
    else:
        with bind_call_context(ctx):
            out = model.embed_documents(["x", "y"])
    assert out == [content_vector(t, 16) for t in ("x", "y")]
    headers = {k.lower(): v for k, v in server.requests[-1]["headers"].items()}
    assert headers.get("x-litellm-end-user-id") == "u-alice"
    assert headers.get("x-pipeshub-request-id") == "req-qa"
    assert headers.get("x-pipeshub-workload") == "query_embedding"


def test_wire_hooks_stay_installed_on_adapted_clients(server: FakeEmbeddingServer, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.llm_gateway.wire import find_http_clients, has_wire_hook

    clients = find_http_clients(_build("openAICompatible", server.base_url, True, monkeypatch))
    assert clients
    assert all(has_wire_hook(c) for c in clients)


@pytest.mark.parametrize("provider", ["fireworks", "together"])
def test_providers_outside_the_seam_are_untouched_and_still_answer(
    server: FakeEmbeddingServer, provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = _build(provider, server.base_url, True, monkeypatch)
    for attr in ("client", "async_client"):
        sub = getattr(model, attr, None)
        assert "create" not in getattr(sub, "__dict__", {})
        assert "create" not in getattr(getattr(sub, "embeddings", None), "__dict__", {})
    if provider == "together":  # FireworksEmbeddings ignores the endpoint (always its cloud): untouched is the claim
        before = _build(provider, server.base_url, False, monkeypatch).embed_documents(["p", "q"])
        assert model.embed_documents(["p", "q"]) == before


def test_orjson_and_json_parse_every_float_shape_servers_emit_to_the_same_bits() -> None:
    rng = random.Random(SEED)
    vals: list[str] = []
    for _ in range(100_000):
        kind = rng.random()
        if kind < 0.3:
            vals.append(repr(struct.unpack("<f", struct.pack("<f", rng.gauss(0, 0.05)))[0]))
        elif kind < 0.5:
            vals.append(f"{rng.gauss(0, 1):.{rng.randint(1, 25)}f}")
        elif kind < 0.7:
            vals.append(f"{rng.gauss(0, 1):.{rng.randint(1, 20)}e}")
        elif kind < 0.8:
            vals.append(repr(struct.unpack("<d", struct.pack("<Q", rng.getrandbits(52) | (rng.randint(1, 2046) << 52)))[0]))
        elif kind < 0.9:
            vals.append(rng.choice(["-0.0", "0.0", "0", "-0", "1E5", "1e-320", "4.9e-324", "2.2250738585072014e-308",
                                    "1.7976931348623157e308", "0.1e1", "-1.0000000000000002", "9007199254740993.0"]))
        else:
            vals.append("0." + "".join(rng.choice("0123456789") for _ in range(rng.randint(18, 40))))
    body = ("[" + ",".join(vals) + "]").encode()
    bad = [(t, a, b) for t, a, b in zip(vals, orjson.loads(body), json.loads(body), strict=True)
           if struct.pack("<d", float(a)) != struct.pack("<d", float(b))]
    assert not bad, bad[:5]


def test_generated_bodies_decode_to_the_bits_the_sdk_parse_gives() -> None:
    rng = np.random.default_rng(SEED)
    for _ in range(300):
        count, dims = int(rng.integers(1, 9)), int(rng.integers(1, 300))
        arr = (rng.standard_normal((count, dims)) * 10 ** rng.uniform(-8, 3)).astype(np.float32)
        items = []
        for i in range(count):
            mode = rng.integers(0, 3)
            if mode == 0:
                embedding: Any = arr[i].tolist()
            elif mode == 1:
                embedding = _b64(arr[i].tolist())
            else:
                embedding = [float(x) for x in rng.standard_normal(dims)]  # full float64 precision
            items.append({"object": "embedding", "index": i, "embedding": embedding})
        body = json.dumps({"object": "list", "data": items, "model": "m"}).encode()
        ours = decode_embedding_response(body, expected_count=count).embeddings
        sdk = construct_type(type_=CreateEmbeddingResponse, value=json.loads(body))
        ref = [np.frombuffer(base64.b64decode(it.embedding), dtype="float32").tolist() if isinstance(it.embedding, str)
               else it.embedding for it in sdk.data]
        assert [[struct.pack("<d", x) for x in v] for v in ours] == [[struct.pack("<d", float(x)) for x in v] for v in ref]


async def test_cancelling_during_the_thread_decode_leaves_the_client_working(monkeypatch: pytest.MonkeyPatch) -> None:
    server = FakeEmbeddingServer(dims=3072, by_content=True).start()
    try:
        model = _build("lmStudio", server.base_url, True, monkeypatch)
        texts = [f"c{i}" for i in range(60)]
        started = threading.Event()
        real = adapter._decode

        def slow_decode(body: bytes, expected: int | None) -> dict[str, Any]:
            started.set()
            time.sleep(0.3)
            return real(body, expected)

        monkeypatch.setattr(adapter, "_decode", slow_decode)
        task = asyncio.create_task(model.aembed_documents(texts))
        assert await asyncio.to_thread(started.wait, 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        monkeypatch.setattr(adapter, "_decode", real)
        assert await model.aembed_documents(texts) == [content_vector(t, 3072) for t in texts]
    finally:
        server.stop()


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_the_callers_retry_loop_sees_the_same_attempts(
    server: FakeEmbeddingServer, status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.utils import embedding_retry

    monkeypatch.setattr(embedding_retry, "retry_delay_seconds", lambda attempt: 0.0)
    attempts: dict[bool, tuple[str, int, Any]] = {}
    for adapted in (False, True):
        model = _build("openAICompatible", server.base_url, adapted, monkeypatch)
        server.requests.clear()
        server.statuses[:] = [status, status]
        try:
            out = await embedding_retry.await_with_retry(
                lambda model=model: model.aembed_documents(["r1", "r2"]), max_retries=3, service_name="qa", operation="embed",
            )
            attempts[adapted] = ("ok", len(server.requests), out)
        except Exception as exc:
            attempts[adapted] = ("err", len(server.requests), type(exc).__name__)
    assert attempts[True] == attempts[False]
