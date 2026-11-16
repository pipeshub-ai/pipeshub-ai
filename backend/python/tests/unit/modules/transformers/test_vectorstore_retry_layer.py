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
from tests.unit.modules.transformers.test_vectorstore_deep import _make_vectorstore

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
