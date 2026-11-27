"""Decoding an embeddings response body: exact vectors, typed errors, an event loop that keeps running, and speed."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import TYPE_CHECKING

import numpy as np
import pytest
from openai._models import construct_type
from openai.types import CreateEmbeddingResponse

from app.utils.aimodels import get_embedding_model
from app.utils.embedding_response_decoder import (
    EmbeddingResponseError,
    decode_embedding_response,
)
from tests.unit.utils.embedding_fake_server import FakeEmbeddingServer, make_vectors

if TYPE_CHECKING:
    from collections.abc import Callable


def _body(items: list, **extra: object) -> bytes:
    return json.dumps({"object": "list", "data": items, "model": "m", **extra}).encode()


def _b64(vector: list[float]) -> str:
    return base64.b64encode(np.asarray(vector, dtype="<f4").tobytes()).decode()


def _sdk_vectors(body: bytes) -> list[list[float]]:
    """What the OpenAI SDK's own parse (and its base64 post-parse) makes of the same body."""
    response = construct_type(type_=CreateEmbeddingResponse, value=json.loads(body))
    out = []
    for item in response.data:
        raw = item.embedding
        out.append(np.frombuffer(base64.b64decode(raw), dtype="float32").tolist() if isinstance(raw, str) else raw)
    return out


class TestDecodesLikeTheSdk:  # T1
    def test_float_lists_are_element_exact(self) -> None:
        vectors = [[0.1 * i + 1e-9, -2.5e-7 * i, 3.14159265358979] for i in range(1, 6)]
        body = _body([{"index": i, "embedding": v} for i, v in enumerate(vectors)])
        decoded = decode_embedding_response(body)
        assert decoded.embeddings == vectors == _sdk_vectors(body)

    def test_base64_is_float32_exact(self) -> None:
        vectors = make_vectors(4, 16)
        body = _body([{"index": i, "embedding": _b64(v)} for i, v in enumerate(vectors)])
        decoded = decode_embedding_response(body)
        assert decoded.embeddings == vectors == _sdk_vectors(body)

    def test_both_encodings_of_the_same_vectors_agree_and_may_mix_in_one_body(self) -> None:
        vectors = make_vectors(3, 12)
        floats = decode_embedding_response(_body([{"embedding": v} for v in vectors]))
        packed = decode_embedding_response(_body([{"embedding": _b64(v)} for v in vectors]))
        mixed = decode_embedding_response(_body([{"embedding": vectors[0]}, {"embedding": _b64(vectors[1])}, {"embedding": vectors[2]}]))
        assert floats.embeddings == packed.embeddings == mixed.embeddings == vectors

    def test_usage_and_model_are_carried_through(self) -> None:
        usage = {"prompt_tokens": 5, "total_tokens": 5}
        decoded = decode_embedding_response(_body([{"embedding": [1.0, 2.0]}], usage=usage))
        response = decoded.as_response()
        assert response["usage"] == usage
        assert response["model"] == "m"
        assert response["data"][0] == {"object": "embedding", "index": 0, "embedding": [1.0, 2.0]}

    def test_integers_in_a_list_become_floats(self) -> None:
        decoded = decode_embedding_response(_body([{"embedding": [1, 2, 3]}]))
        assert decoded.embeddings == [[1.0, 2.0, 3.0]]
        assert all(type(x) is float for x in decoded.embeddings[0])


NAN_BASE64 = base64.b64encode(np.array([1.0, np.nan], dtype="<f4").tobytes()).decode()
GOOD = [0.5, 1.5]


@pytest.mark.parametrize(
    ("label", "body", "expected_count"),
    [
        ("not json", b"<html>bad gateway</html>", None),
        ("truncated json", b'{"data": [{"embedding": [0.1, 0.2', None),
        ("empty body", b"", None),
        ("a list at the top", b"[1, 2]", None),
        ("no data", b'{"object": "list"}', None),
        ("empty data", _body([]), None),
        ("data is not a list", b'{"data": {"a": 1}}', None),
        ("item without embedding", _body([{"index": 0}]), None),
        ("item is not an object", _body([1, 2]), None),
        ("wrong count", _body([{"embedding": GOOD}]), 2),
        ("ragged dimensions", _body([{"embedding": GOOD}, {"embedding": [1.0]}]), None),
        ("ragged across encodings", _body([{"embedding": GOOD}, {"embedding": _b64([1.0])}]), None),
        ("empty vector", _body([{"embedding": []}]), None),
        ("nan literal", b'{"data": [{"embedding": [NaN, 1.0]}]}', None),
        ("infinity literal", b'{"data": [{"embedding": [Infinity, 1.0]}]}', None),
        ("overflowing number", b'{"data": [{"embedding": [1e999, 1.0]}]}', None),
        ("string element", _body([{"embedding": [1.0, "2.0"]}]), None),
        ("null element", _body([{"embedding": [1.0, None]}]), None),
        ("bool element", _body([{"embedding": [True, False]}]), None),
        ("nested list", _body([{"embedding": [[1.0], [2.0]]}]), None),
        ("embedding is a number", _body([{"embedding": 3}]), None),
        ("base64 nan", _body([{"embedding": NAN_BASE64}]), None),
        ("base64 not whole floats", _body([{"embedding": base64.b64encode(b"123456").decode()}]), None),
        ("base64 garbage", _body([{"embedding": "not base64 !!"}]), None),
        ("base64 empty", _body([{"embedding": ""}]), None),
    ],
)
def test_malformed_bodies_raise_a_typed_error_and_return_nothing(label: str, body: bytes, expected_count: int | None) -> None:  # T2
    with pytest.raises(EmbeddingResponseError):
        decode_embedding_response(body, expected_count=expected_count)


def _realistic_float_body(count: int = 50, dims: int = 3072) -> bytes:
    rng = np.random.default_rng(11)
    # About 13 characters per number, as the gateway of the 2026-11-27 probe sent (40 KB per 3072-float vector).
    vectors_text = np.round(rng.standard_normal((count, dims)), 9).tolist()
    return _body([{"index": i, "embedding": v} for i, v in enumerate(vectors_text)], usage={"prompt_tokens": 1, "total_tokens": 1})


class _LoopWatch:
    """A heartbeat on the running loop; ``worst`` is the longest the loop went without running it."""

    def __init__(self, tick: float = 0.002) -> None:
        self.tick = tick
        self.worst = 0.0
        self._stop = False

    async def run(self) -> None:
        last = time.perf_counter()
        while not self._stop:
            await asyncio.sleep(self.tick)
            now = time.perf_counter()
            self.worst = max(self.worst, now - last - self.tick)
            last = now

    def stop(self) -> None:
        self._stop = True


async def test_the_loop_stays_responsive_while_a_float_body_is_decoded() -> None:  # T3
    """A 50 x 3072 float reply through the client: no stretch of 20 ms in which the loop could not run."""
    class BigFloats(FakeEmbeddingServer):
        def vectors_for(self, count: int) -> list[list[float]]:
            return make_vectors(count, 3072)

    server = BigFloats(mode="float").start()
    try:
        # Built as production builds it, so the test covers the seam that installs the decoder.
        model = get_embedding_model(
            "lmStudio", {"isDefault": True, "configuration": {"model": "m", "apiKey": "k", "endpoint": server.base_url}}
        )
        # Warm the connection, the imports and the fake server's cached answer (it shares this process's GIL), so
        # the measured call is the steady state.
        texts = [f"chunk {i}" for i in range(50)]
        await model.aembed_documents(texts)
        sample = _realistic_float_body()  # a body of the same shape, to time the SDK's parse on this host
        sdk = _cpu(lambda: construct_type(type_=CreateEmbeddingResponse, value=json.loads(sample)))
        watch = _LoopWatch()
        beat = asyncio.create_task(watch.run())
        await asyncio.sleep(0.05)
        watch.worst = 0.0
        vectors = await model.aembed_documents(texts)
        await asyncio.sleep(0.02)
        watch.stop()
        await beat
    finally:
        server.stop()
    assert len(vectors) == 50 and len(vectors[0]) == 3072
    assert watch.worst < sdk / 5, f"the loop was blocked for {watch.worst * 1000:.0f} ms; SDK parse {sdk * 1000:.0f} ms"


def _cpu(fn: Callable[[], object]) -> float:
    best = float("inf")
    for _ in range(5):
        start = time.thread_time()
        fn()
        best = min(best, time.thread_time() - start)
    return best


@pytest.mark.perf
def test_decoding_50_float_vectors_costs_a_tenth_of_the_sdk_parse() -> None:  # T7
    """Relative to the SDK parse of the same body in the same run: no host-clock bound (WALLCLOCK-1)."""
    body = _realistic_float_body()
    decode_embedding_response(body)  # warm numpy and orjson
    ours = _cpu(lambda: decode_embedding_response(body, expected_count=50))
    sdk = _cpu(lambda: construct_type(type_=CreateEmbeddingResponse, value=json.loads(body)))
    assert ours <= sdk / 10, f"decode {ours * 1000:.1f} ms vs SDK {sdk * 1000:.1f} ms"


async def test_the_stall_of_a_production_batch_on_the_thread_path_is_a_fraction_of_the_sdk_parse() -> None:
    """The GIL limit (see the decoder docstring) pinned at the indexing batch shape, 50 x 3072 floats.

    ``orjson.loads`` holds the GIL, so the loop is not free during the parse; at this size the stall is a few
    milliseconds against the SDK's hundreds. A body that grew 10x, or a parse that got slower, fails this.
    """
    body = _realistic_float_body()
    decode_embedding_response(body)
    sdk = _cpu(lambda: construct_type(type_=CreateEmbeddingResponse, value=json.loads(body)))
    watch = _LoopWatch(tick=0.001)
    beat = asyncio.create_task(watch.run())
    await asyncio.sleep(0.05)
    watch.worst = 0.0
    for _ in range(3):
        await asyncio.to_thread(decode_embedding_response, body, expected_count=50)
        await asyncio.sleep(0.01)
    watch.stop()
    await beat
    assert watch.worst <= sdk / 5, f"loop stalled {watch.worst * 1000:.1f} ms; SDK parse {sdk * 1000:.0f} ms"
