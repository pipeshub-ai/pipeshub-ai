"""Embedding clients decode responses through the one decoder, and behave as before otherwise.

Each test talks to an in-process OpenAI-compatible server on loopback (``embedding_fake_server``); the "before"
side of every comparison is the same client built with the adapter switched off.
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING, Any

import openai
import pytest
from langchain_openai.embeddings import OpenAIEmbeddings

from app.utils import aimodels, embedding_response_adapter
from app.utils.aimodels import get_embedding_model
from app.utils.embedding_response_adapter import decode_embedding_responses
from app.utils.embedding_response_decoder import (
    OFF_LOOP_BODY_BYTES,
    EmbeddingResponseError,
)
from tests.unit.utils.embedding_fake_server import FakeEmbeddingServer, content_vector

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


@pytest.fixture
def server() -> Iterator[FakeEmbeddingServer]:
    fake = FakeEmbeddingServer(dims=16).start()
    try:
        yield fake
    finally:
        fake.stop()


def _config(endpoint: str, **configuration: object) -> dict:
    return {"isDefault": True, "configuration": {"model": "m", "apiKey": "k", "endpoint": endpoint, **configuration}}


def _client(url: str, adapted: bool, monkeypatch: pytest.MonkeyPatch) -> OpenAIEmbeddings:
    if not adapted:
        monkeypatch.setattr("app.utils.embedding_response_adapter.decode_embedding_responses", lambda model: model)
    model = get_embedding_model("openAICompatible", _config(url))
    assert isinstance(model, OpenAIEmbeddings)
    return model


TEXTS = ["alpha", "beta", "gamma", "delta"]


class TestSameAnswersAsTheUnpatchedClient:  # T4
    @pytest.mark.parametrize("mode", ["float", "base64", "honour"])
    async def test_async_documents_and_query(self, server: FakeEmbeddingServer, mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
        server.mode = mode
        with monkeypatch.context() as before:
            baseline = _client(server.base_url, False, before)
            expected_docs = await baseline.aembed_documents(TEXTS)
            expected_query = await baseline.aembed_query("alpha")
        adapted = _client(server.base_url, True, monkeypatch)
        assert await adapted.aembed_documents(TEXTS) == expected_docs
        assert await adapted.aembed_query("alpha") == expected_query
        assert len(expected_docs) == len(TEXTS) and len(expected_docs[0]) == 16

    @pytest.mark.parametrize("mode", ["float", "base64", "honour"])
    def test_sync_documents_and_query(self, server: FakeEmbeddingServer, mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
        server.mode = mode
        with monkeypatch.context() as before:
            baseline = _client(server.base_url, False, before)
            expected_docs = baseline.embed_documents(TEXTS)
            expected_query = baseline.embed_query("alpha")
        adapted = _client(server.base_url, True, monkeypatch)
        assert adapted.embed_documents(TEXTS) == expected_docs
        assert adapted.embed_query("alpha") == expected_query

    @pytest.mark.parametrize("provider", ["lmStudio", "litellmProxy", "azureAI"])
    async def test_providers_that_skip_the_token_length_check_answer_the_same(
        self, server: FakeEmbeddingServer, provider: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        results = []
        for adapted in (False, True):
            with monkeypatch.context() as patch:
                if not adapted:
                    patch.setattr("app.utils.embedding_response_adapter.decode_embedding_responses", lambda model: model)
                client = get_embedding_model(provider, _config(server.base_url))
                results.append(await client.aembed_documents(TEXTS))
        assert results[0] == results[1]

    @pytest.mark.parametrize("status", [401, 429, 500, 503])
    async def test_error_statuses_raise_the_same_error_after_the_same_number_of_requests(
        self, server: FakeEmbeddingServer, status: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[bool, tuple[type, int]] = {}
        for adapted in (False, True):
            server.requests.clear()
            server.statuses[:] = [status] * 5
            with monkeypatch.context() as patch:
                client = _client(server.base_url, adapted, patch)
                with pytest.raises(openai.APIStatusError) as raised:
                    await client.aembed_documents(TEXTS)
            seen[adapted] = (type(raised.value), len(server.requests))
        assert seen[True] == seen[False]
        assert seen[True][1] == 1  # these clients are built without SDK retries; the adapter adds none

    def test_sync_error_status_is_the_same_error(self, server: FakeEmbeddingServer, monkeypatch: pytest.MonkeyPatch) -> None:
        server.statuses[:] = [429]
        client = _client(server.base_url, True, monkeypatch)
        with pytest.raises(openai.RateLimitError):
            client.embed_documents(TEXTS)

    async def test_timeout_raises_the_same_error(self, server: FakeEmbeddingServer, monkeypatch: pytest.MonkeyPatch) -> None:
        server.delay = 0.6
        for adapted in (False, True):
            with monkeypatch.context() as patch:
                client = _client(server.base_url, adapted, patch)
                with pytest.raises(openai.APITimeoutError):
                    await client.aembed_documents(TEXTS, timeout=0.1)

    async def test_a_truncated_body_is_a_typed_error_not_a_partial_result(
        self, server: FakeEmbeddingServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server.mode = "garbage"
        client = _client(server.base_url, True, monkeypatch)
        with pytest.raises(EmbeddingResponseError):
            await client.aembed_documents(TEXTS)
        with pytest.raises(EmbeddingResponseError):
            client.embed_documents(TEXTS)


class TestRequestsAreUnchanged:  # T6
    async def test_the_request_reaches_the_server_as_before_and_usage_is_kept(
        self, server: FakeEmbeddingServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent: dict[bool, dict[str, Any]] = {}
        for adapted in (False, True):
            server.requests.clear()
            with monkeypatch.context() as patch:
                client = _client(server.base_url, adapted, patch)
                await client.aembed_documents(TEXTS)
            sent[adapted] = server.requests[0]
        before, after = sent[False], sent[True]
        assert after["path"] == before["path"] == "/v1/embeddings"
        assert after["body"] == before["body"]
        assert after["body"]["model"] == "m"
        for header in ("Authorization", "User-Agent", "Content-Type", "X-Stainless-Lang"):
            assert after["headers"].get(header) == before["headers"].get(header), header
        # Only the SDK's raw-response marker is added; no header the gateway could key on is lost.
        assert set(before["headers"]) - set(after["headers"]) == set()
        assert set(after["headers"]) - set(before["headers"]) <= {"X-Stainless-Raw-Response"}

    def test_the_decoded_response_keeps_the_usage_the_server_reported(self, server: FakeEmbeddingServer, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(server.base_url, True, monkeypatch)
        response = client.client.create(input=["a", "b"], model="m")
        assert response["usage"] == {"prompt_tokens": 6, "total_tokens": 6}
        assert [item["index"] for item in response["data"]] == [0, 1]


OPENAI_PROTOCOL: dict[str, Callable[[], Any]] = {
    "openAI": lambda: get_embedding_model("openAI", _config("https://models.example/v1")),
    "openAI without endpoint": lambda: get_embedding_model("openAI", _config(None)),
    "azureAI": lambda: get_embedding_model("azureAI", _config("https://models.example/v1")),
    "azureOpenAI": lambda: get_embedding_model(
        "azureOpenAI", _config("https://models.example", deploymentName="d", apiVersion="2024-02-01")
    ),
    "openAICompatible": lambda: get_embedding_model("openAICompatible", _config("https://models.example/v1")),
    "openRouter": lambda: get_embedding_model("openRouter", _config(None)),
    "lmStudio": lambda: get_embedding_model("lmStudio", _config("https://models.example/v1")),
    "litellmProxy": lambda: get_embedding_model("litellmProxy", _config("https://models.example/v1")),
}
OTHER_PROVIDERS: dict[str, Callable[[], Any]] = {
    "ollama": lambda: get_embedding_model("ollama", _config("https://models.example")),
    "together": lambda: get_embedding_model("together", _config("https://models.example/v1")),
}


class TestEveryOpenAIProtocolProviderGetsTheAdapter:  # T5
    @pytest.mark.parametrize("name", list(OPENAI_PROTOCOL))
    def test_adapted(self, name: str) -> None:
        model = OPENAI_PROTOCOL[name]()
        assert "create" in vars(model.client)
        assert "create" in vars(model.async_client)

    @pytest.mark.parametrize("name", list(OTHER_PROVIDERS))
    def test_other_providers_are_left_alone(self, name: str) -> None:
        model = OTHER_PROVIDERS[name]()
        for attr in ("client", "async_client"):
            assert "create" not in getattr(getattr(model, attr, None), "__dict__", {})

    def test_it_is_applied_once_through_the_decoration_seam(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[Any] = []
        original = aimodels._decorate_embeddings
        monkeypatch.setattr(aimodels, "_decorate_embeddings", lambda *a: calls.append(a) or original(*a))
        model = get_embedding_model("openAICompatible", _config("https://models.example/v1"))
        assert len(calls) == 1
        assert "create" in vars(model.async_client)


class TestTheAdapterChecksTheItemCount:
    """The adapter hands the decoder how many inputs the request had, so a gateway that drops one fails loudly."""

    async def test_async_short_answer(self, server: FakeEmbeddingServer, monkeypatch: pytest.MonkeyPatch) -> None:
        server.short = True
        client = _client(server.base_url, True, monkeypatch)
        with pytest.raises(EmbeddingResponseError):
            await client.aembed_documents(TEXTS)

    def test_sync_short_answer(self, server: FakeEmbeddingServer, monkeypatch: pytest.MonkeyPatch) -> None:
        server.short = True
        client = _client(server.base_url, True, monkeypatch)
        with pytest.raises(EmbeddingResponseError):
            client.embed_documents(TEXTS)


class TestLargeBodiesDecodeOffTheLoop:
    """Where the async path decodes: on a worker thread from ``off_loop_bytes`` up, inline below it."""

    @staticmethod
    def _model(server: FakeEmbeddingServer, off_loop_bytes: int) -> tuple[OpenAIEmbeddings, list[int]]:
        threads: list[int] = []
        real = embedding_response_adapter.decode_embedding_response

        def recording(body: bytes, **kwargs: Any) -> Any:  # noqa: ANN401
            threads.append(threading.get_ident())
            return real(body, **kwargs)

        embedding_response_adapter.decode_embedding_response = recording  # restored by the caller's fixture
        model = OpenAIEmbeddings(model="m", api_key="k", base_url=server.base_url, check_embedding_ctx_length=False, max_retries=0)
        return decode_embedding_responses(model, off_loop_bytes=off_loop_bytes), threads

    @pytest.fixture(autouse=True)
    def _restore(self) -> Iterator[None]:
        real = embedding_response_adapter.decode_embedding_response
        yield
        embedding_response_adapter.decode_embedding_response = real

    async def test_a_body_at_the_threshold_goes_to_a_thread(self, server: FakeEmbeddingServer) -> None:
        model, threads = self._model(server, off_loop_bytes=1)
        await model.aembed_documents(TEXTS)
        assert threads and all(t != threading.get_ident() for t in threads)

    async def test_a_body_under_the_threshold_decodes_inline(self, server: FakeEmbeddingServer) -> None:
        model, threads = self._model(server, off_loop_bytes=1 << 30)
        await model.aembed_documents(TEXTS)
        assert threads == [threading.get_ident()]

    async def test_the_default_threshold_splits_a_small_and_a_production_sized_body(self) -> None:
        big = FakeEmbeddingServer(dims=3072).start()
        small = FakeEmbeddingServer(dims=16).start()
        try:
            model_big, threads_big = self._model(big, OFF_LOOP_BODY_BYTES)
            await model_big.aembed_documents([f"t{i}" for i in range(50)])
            model_small, threads_small = self._model(small, OFF_LOOP_BODY_BYTES)
            await model_small.aembed_documents(TEXTS)
        finally:
            big.stop()
            small.stop()
        assert threads_big and threads_big[0] != threading.get_ident()
        assert threads_small == [threading.get_ident()]


class TestConcurrentCallersDoNotCrossTalk:
    """32 callers at once, each with texts of its own; the answers stay with their callers."""

    @staticmethod
    def _expected(texts: list[str], dims: int) -> list[list[float]]:
        return [content_vector(t, dims) for t in texts]

    async def test_async(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = FakeEmbeddingServer(dims=3072, by_content=True).start()
        try:
            model = get_embedding_model("lmStudio", _config(server.base_url))
            batches = [[f"req{r}-chunk{i}" for i in range(20)] for r in range(32)]
            for batch in batches:  # render each answer once, so the server does not hold the GIL while measured
                server.reply_bytes({"input": batch, "model": "m"}, False)
            results = await asyncio.gather(*(model.aembed_documents(b) for b in batches))
        finally:
            server.stop()
        for batch, got in zip(batches, results, strict=True):
            assert got == self._expected(batch, 3072)

    def test_sync_threads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = FakeEmbeddingServer(dims=16, by_content=True).start()
        try:
            model = get_embedding_model("lmStudio", _config(server.base_url))
            batches = [[f"s{r}-{i}" for i in range(7)] for r in range(32)]
            results: dict[int, Any] = {}

            def run(r: int) -> None:
                try:
                    results[r] = model.embed_documents(batches[r])
                except Exception as exc:
                    results[r] = exc

            workers = [threading.Thread(target=run, args=(r,)) for r in range(32)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
        finally:
            server.stop()
        for r, batch in enumerate(batches):
            assert results[r] == self._expected(batch, 16), results[r]
