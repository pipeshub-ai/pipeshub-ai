"""The batch embedding loop asks the EmbeddingGateway before it repeats a paid request."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import openai
import pytest

from app.exceptions.indexing_exceptions import EmbeddingError
from app.services.llm_gateway.embedding_gateway import (
    EmbeddingGateway,
    EmbeddingRateLimited,
)
from app.services.spend_guard.retry_policy import RetryBudget, RetryKind
from tests.support.vectorstore_world import make_vectorstore as _make_vectorstore

pytestmark = pytest.mark.usefixtures("bound_org")


def _error(status: int, retry_after: str | None = None) -> openai.APIStatusError:
    headers = {"Retry-After": retry_after} if retry_after else {}
    response = httpx.Response(status, headers=headers, request=httpx.Request("POST", "http://x/v1/embeddings"))
    cls = openai.RateLimitError if status == 429 else openai.InternalServerError
    return cls("failed", response=response, body=None)


def _store(*outcomes: object) -> Any:  # noqa: ANN401 - a VectorStore with its embedder mocked
    vs = _make_vectorstore()
    vs.embedding_provider = "openAI"
    vs.dense_embeddings = AsyncMock()
    vs.dense_embeddings.aembed_documents = AsyncMock(side_effect=list(outcomes))
    return vs


async def _run(vs: Any, gateway: EmbeddingGateway) -> tuple[Any, Any]:  # noqa: ANN401
    with patch("app.modules.transformers.vectorstore.get_embedding_gateway", return_value=gateway), \
            patch("app.modules.transformers.vectorstore.asyncio.sleep", AsyncMock()) as sleep:
        try:
            return await vs._embed_documents_with_retry(["hello"], "rec-1"), sleep
        except Exception as exc:
            return exc, sleep


async def test_an_exhausted_retry_budget_sends_the_batch_once() -> None:
    vs = _store(_error(503), _error(503), [[0.1]])
    result, _sleep = await _run(vs, EmbeddingGateway(retry_budget=RetryBudget(capacity=0)))
    assert isinstance(result, EmbeddingError)
    assert vs.dense_embeddings.aembed_documents.await_count == 1


async def test_a_retry_after_beyond_the_allowance_is_raised_without_sleeping() -> None:
    vs = _store(_error(429, retry_after="300"), [[0.1]])
    result, sleep = await _run(vs, EmbeddingGateway(retry_budget=RetryBudget()))
    assert isinstance(result, EmbeddingRateLimited) and result.retry_after == 300.0
    assert vs.dense_embeddings.aembed_documents.await_count == 1
    sleep.assert_not_awaited()


async def test_a_retry_after_that_fits_is_the_wait() -> None:
    vs = _store(_error(429, retry_after="9"), [[0.5]])
    result, sleep = await _run(vs, EmbeddingGateway(retry_budget=RetryBudget()))
    assert result == [[0.5]]
    assert sleep.await_args_list[0].args[0] == 9.0


async def test_a_success_pays_a_retry_token_back() -> None:
    budget = RetryBudget(capacity=500)
    budget.try_spend(RetryKind.THROTTLE)
    vs = _store([[0.1]])
    result, _sleep = await _run(vs, EmbeddingGateway(retry_budget=budget))
    assert result == [[0.1]] and budget.tokens == 496


async def test_the_wait_is_the_gateways_jitter_not_a_fixed_backoff() -> None:
    """Batches that fail together must not wake together: the first retry waits at most the transient base (0.25 s), not 2 s."""
    waits = []
    for _ in range(10):
        vs = _store(_error(503), [[0.1]])
        _result, sleep = await _run(vs, EmbeddingGateway(retry_budget=RetryBudget()))
        waits.append(sleep.await_args_list[0].args[0])
    assert max(waits) <= 0.25 and len(set(waits)) > 1


async def test_the_wait_never_overruns_the_sleep_allowance() -> None:
    """After a 59 s Retry-After only 1 s of the 60 s allowance is left: the next wait is at most that, not a 4 s floor."""
    vs = _store(_error(429, retry_after="59"), _error(503), [[0.1]])
    _result, sleep = await _run(vs, EmbeddingGateway(retry_budget=RetryBudget()))
    waits = [c.args[0] for c in sleep.await_args_list]
    assert waits[0] == 59.0 and sum(waits) <= 60.0


async def test_a_remote_500_is_retried_by_the_gateways_decision() -> None:
    """The SDK no longer retries a 500; the batch loop must let the gateway classify it (twice sent, then vectors)."""
    vs = _store(_error(500), [[0.2]])
    result, _sleep = await _run(vs, EmbeddingGateway(retry_budget=RetryBudget()))
    assert result == [[0.2]] and vs.dense_embeddings.aembed_documents.await_count == 2


async def test_a_request_fault_is_raised_unchanged() -> None:
    """A 400 is not wrapped in EmbeddingError and is sent once."""
    fault = ValueError("input too long")
    vs = _store(fault, [[0.2]])
    result, _sleep = await _run(vs, EmbeddingGateway(retry_budget=RetryBudget()))
    assert result is fault and vs.dense_embeddings.aembed_documents.await_count == 1


async def test_a_local_embedding_server_is_probed_without_the_gateway() -> None:
    """The local server is not paid and keeps its own client retry: its size probe is not repeated by the gateway and spends
    none of the shared retry budget."""
    from app.utils.embedding_server_client import EmbeddingServerEmbeddings

    gateway = EmbeddingGateway(retry_budget=RetryBudget())
    vs = _make_vectorstore()
    local = AsyncMock(spec=EmbeddingServerEmbeddings)
    local.aembed_query = AsyncMock(side_effect=[_error(503)])
    with patch("app.modules.transformers.vectorstore.get_embedding_gateway", return_value=gateway), \
            patch("app.utils.llm.get_default_embedding_model", return_value=local), \
            pytest.raises(Exception):  # noqa: B017 - the build reports the model as unavailable
        await vs._build_embedding_model(None, "hash")
    assert local.aembed_query.await_count == 1, "the gateway must not repeat a local probe"


async def test_a_local_embedding_server_is_not_repeated_by_the_entity_store() -> None:
    from app.modules.transformers.entity_vectorstore import EntityVectorStore
    from app.utils.embedding_server_client import EmbeddingServerEmbeddings

    local = AsyncMock(spec=EmbeddingServerEmbeddings)
    local.embed_documents = lambda texts: (_ for _ in ()).throw(_error(503))
    store = EntityVectorStore.__new__(EntityVectorStore)
    store._dense_embeddings = local
    calls = 0

    def boom(texts: list[str]) -> list[list[float]]:
        nonlocal calls
        calls += 1
        raise _error(503)

    local.embed_documents = boom
    with pytest.raises(openai.InternalServerError):
        await store._embed(["x"])
    assert calls == 1


async def test_a_throttle_on_the_last_attempt_hands_its_wait_to_the_caller() -> None:
    """Two outages, then a 429 asking for 300 s on the third and last attempt: the record is handed back for that wait, not
    failed as an ordinary embedding error and retried on the 15 s backoff."""
    vs = _store(_error(503), _error(503), _error(429, retry_after="300"))
    result, _sleep = await _run(vs, EmbeddingGateway(retry_budget=RetryBudget()))
    assert isinstance(result, EmbeddingRateLimited) and result.retry_after == 300.0


async def test_the_failed_requests_of_the_batch_loop_are_charged_to_the_jobs_send_meter() -> None:
    from app.services.llm_gateway import wire

    class Counting:
        calls = 0

        async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
            wire.note_request_sent()
            wire.note_response(503)
            Counting.calls += 1
            raise _error(503)

    vs = _make_vectorstore()
    vs.embedding_provider = "openAI"
    vs.dense_embeddings = Counting()
    meter = wire.SendMeter(allowance=10)
    with wire.metered(meter):
        result, _sleep = await _run(vs, EmbeddingGateway(retry_budget=RetryBudget()))
    assert isinstance(result, EmbeddingError) and Counting.calls == 3
    assert meter.failed == 3


async def test_a_cancelled_attempt_charges_what_it_sent() -> None:
    """The stage's deadline cancels the loop after a request left: it counts before the cancellation goes on."""
    import asyncio

    from app.services.llm_gateway import wire

    class Hanging:
        async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
            wire.note_request_sent()
            await asyncio.sleep(30)
            return []

    vs = _make_vectorstore()
    vs.embedding_provider = "openAI"
    vs.dense_embeddings = Hanging()
    meter = wire.SendMeter(allowance=10)
    gateway = EmbeddingGateway(retry_budget=RetryBudget())
    with wire.metered(meter), patch("app.modules.transformers.vectorstore.get_embedding_gateway", return_value=gateway):
        task = asyncio.create_task(vs._embed_documents_with_retry(["x"], "rec"))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert meter.failed == 1


async def test_a_batch_that_times_out_is_charged_as_a_failed_send() -> None:
    """A request sent and never answered (the batch timeout ends it) is a failed send of the stage job, like a 503."""
    import asyncio

    from app.services.llm_gateway import wire

    class Hanging:
        calls = 0

        async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
            wire.note_request_sent()
            Hanging.calls += 1
            await asyncio.Event().wait()  # never answers
            return [[0.0]]

    vs = _make_vectorstore()
    vs.embedding_provider = "openAI"
    vs.dense_embeddings = Hanging()
    meter = wire.SendMeter(allowance=10)
    real_sleep = asyncio.sleep
    with patch("app.modules.transformers.vectorstore._REMOTE_EMBEDDING_BATCH_TIMEOUT_S", 0.05), \
            patch("app.modules.transformers.vectorstore.get_embedding_gateway",
                  return_value=EmbeddingGateway(retry_budget=RetryBudget())), \
            patch("app.modules.transformers.vectorstore.asyncio.sleep", AsyncMock(side_effect=lambda s: real_sleep(0))), \
            wire.metered(meter):
        with pytest.raises(EmbeddingError):
            await vs._embed_documents_with_retry(["hello"], "rec-1")
    assert Hanging.calls >= 1 and meter.failed == Hanging.calls


async def test_an_entity_query_embedding_is_repeated_by_the_gateway_on_a_transient_failure() -> None:
    """The entity store's model is built without SDK retries; the query path must not let the first 503 escape."""
    from collections import OrderedDict

    from app.modules.transformers.entity_vectorstore import EntityVectorStore

    class Remote:
        calls = 0

        def embed_query(self, text: str) -> list[float]:
            Remote.calls += 1
            if Remote.calls == 1:
                raise _error(503)
            return [0.5, 0.5]

    store = EntityVectorStore.__new__(EntityVectorStore)
    store._dense_embeddings = Remote()
    store._query_vector_cache = OrderedDict()
    store._generation = 0
    store._sparse_embedder = None
    gateway = EmbeddingGateway(retry_budget=RetryBudget())
    with patch("app.modules.transformers.entity_vectorstore.get_embedding_gateway", return_value=gateway), \
            patch("app.services.llm_gateway.embedding_gateway.asyncio.sleep", AsyncMock()):
        dense, _sparse = await store._query_vectors("acme")
    assert dense == [0.5, 0.5] and Remote.calls == 2
