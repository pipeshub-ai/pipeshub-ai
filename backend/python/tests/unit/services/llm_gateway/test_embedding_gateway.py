"""EmbeddingGateway: the one retry layer of a remote embeddings call, and the dimension probe."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from app.services.llm_gateway import embedding_gateway as gateway_module
from app.services.llm_gateway.embedding_gateway import (
    EmbeddingGateway,
    EmbeddingRateLimited,
)
from app.services.spend_guard.retry_policy import (
    ATTEMPTS_EXHAUSTED,
    BUDGET_EXHAUSTED,
    MAX_ATTEMPTS,
    RETRY,
    RetryBudget,
    RetryKind,
)


def _status_error(status: int, retry_after: str | None = None) -> openai.APIStatusError:
    headers = {"Retry-After": retry_after} if retry_after else {}
    response = httpx.Response(status, headers=headers, request=httpx.Request("POST", "http://x/v1/embeddings"))
    cls = openai.RateLimitError if status == 429 else openai.InternalServerError
    return cls("failed", response=response, body=None)


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    waits: list[float] = []

    async def record(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(gateway_module.asyncio, "sleep", record)
    return waits


class _Embedder:
    """An embeddings client that fails from a script, then answers; counts the requests it received."""

    def __init__(self, *script: BaseException | int, dimension: int = 8, **attrs: Any) -> None:  # noqa: ANN401
        self.script = list(script)
        self.requests = 0
        self.dimension = dimension
        for name, value in attrs.items():
            setattr(self, name, value)

    async def aembed_query(self, _text: str) -> list[float]:
        self.requests += 1
        step = self.script.pop(0) if self.script else None
        if isinstance(step, BaseException):
            raise step
        return [0.0] * self.dimension


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (_status_error(429), RetryKind.THROTTLE),
        (_status_error(503), RetryKind.TRANSIENT),
        (_status_error(502), RetryKind.TRANSIENT),
        (TimeoutError(), RetryKind.TRANSIENT),
        (ConnectionResetError(), RetryKind.TRANSIENT),
        (httpx.ConnectError("refused"), RetryKind.TRANSIENT),
        (SimpleNamespace(), None),
    ],
)
def test_a_failure_is_classified_for_the_policy(exc: Any, kind: RetryKind | None) -> None:  # noqa: ANN401
    error = exc if isinstance(exc, BaseException) else ValueError("bad request")
    assert EmbeddingGateway.retry_kind(error) is kind


def test_sdk_errors_with_a_status_code_are_classified_without_the_openai_types() -> None:
    """Cohere, Mistral and Bedrock errors are not OpenAI's: their status decides."""

    class _Api(Exception):
        def __init__(self, status: int) -> None:
            super().__init__(f"HTTP {status}")
            self.status_code = status

    class _Boto(Exception):
        response = {"ResponseMetadata": {"HTTPStatusCode": 503}}

    assert EmbeddingGateway.retry_kind(_Api(429)) is RetryKind.THROTTLE
    assert EmbeddingGateway.retry_kind(_Api(500)) is RetryKind.TRANSIENT
    assert EmbeddingGateway.retry_kind(_Api(400)) is None
    assert EmbeddingGateway.retry_kind(_Boto()) is RetryKind.TRANSIENT


async def test_a_logical_call_is_sent_at_most_three_times(slept: list[float]) -> None:
    gateway = EmbeddingGateway(retry_budget=RetryBudget())
    calls = 0

    async def always_fails() -> None:
        nonlocal calls
        calls += 1
        raise _status_error(503)

    with pytest.raises(openai.InternalServerError):
        await gateway.call(always_fails)
    assert calls == MAX_ATTEMPTS and len(slept) == MAX_ATTEMPTS - 1


async def test_a_request_fault_is_sent_once(slept: list[float]) -> None:
    calls = 0

    async def bad() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("input too long")

    with pytest.raises(ValueError):
        await EmbeddingGateway(retry_budget=RetryBudget()).call(bad)
    assert calls == 1 and slept == []


async def test_a_retry_after_beyond_the_allowance_is_raised_not_slept(slept: list[float]) -> None:
    async def throttled() -> None:
        raise _status_error(429, retry_after="120")

    with pytest.raises(EmbeddingRateLimited) as raised:
        await EmbeddingGateway(retry_budget=RetryBudget()).call(throttled)
    assert raised.value.retry_after == 120.0 and slept == []


async def test_a_retry_after_that_fits_is_waited_out(slept: list[float]) -> None:
    calls = 0

    async def once_throttled() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _status_error(429, retry_after="9")
        return "ok"

    assert await EmbeddingGateway(retry_budget=RetryBudget()).call(once_throttled) == "ok"
    assert slept == [9.0]


async def test_an_exhausted_budget_means_no_retries(slept: list[float]) -> None:
    budget = RetryBudget(capacity=0)
    calls = 0

    async def fails() -> None:
        nonlocal calls
        calls += 1
        raise _status_error(503)

    with pytest.raises(openai.InternalServerError):
        await EmbeddingGateway(retry_budget=budget).call(fails)
    assert calls == 1


def test_decide_reports_why_it_refuses() -> None:
    gateway = EmbeddingGateway(retry_budget=RetryBudget())
    assert gateway.decide(0, _status_error(503)).reason == RETRY
    assert gateway.decide(MAX_ATTEMPTS - 1, _status_error(503)).reason == ATTEMPTS_EXHAUSTED
    assert EmbeddingGateway(retry_budget=RetryBudget(capacity=0)).decide(0, _status_error(503)).reason == BUDGET_EXHAUSTED


# ---- the dimension probe -------------------------------------------------------------------------------------------------


async def test_the_dimension_is_probed_once_per_provider_key(slept: list[float]) -> None:
    gateway = EmbeddingGateway(retry_budget=RetryBudget())
    first = _Embedder(model="text-embedding-3-small", dimension=1536, api_key="k1")
    again = _Embedder(model="text-embedding-3-small", dimension=1536, api_key="k1")
    other = _Embedder(model="text-embedding-3-large", dimension=3072, api_key="k1")
    assert await gateway.probe_dimension(first) == 1536  # type: ignore[arg-type]
    assert await gateway.probe_dimension(again) == 1536  # type: ignore[arg-type]
    assert await gateway.probe_dimension(other) == 3072  # type: ignore[arg-type]
    assert (first.requests, again.requests, other.requests) == (1, 0, 1), "a known size is not paid for twice"


async def test_two_credentials_or_two_dimension_settings_do_not_share_a_probe() -> None:
    gateway = EmbeddingGateway(retry_budget=RetryBudget())
    a = _Embedder(model="m", dimension=4, api_key="k1", dimensions=256)
    b = _Embedder(model="m", dimension=4, api_key="k2", dimensions=256)
    c = _Embedder(model="m", dimension=8, api_key="k1", dimensions=512)
    for embedder in (a, b, c):
        await gateway.probe_dimension(embedder)  # type: ignore[arg-type]
    assert (a.requests, b.requests, c.requests) == (1, 1, 1)


async def test_a_model_that_does_not_name_itself_is_never_cached() -> None:
    gateway = EmbeddingGateway(retry_budget=RetryBudget())
    a, b = _Embedder(dimension=4), _Embedder(dimension=9)
    assert await gateway.probe_dimension(a) == 4  # type: ignore[arg-type]
    assert await gateway.probe_dimension(b) == 9  # type: ignore[arg-type]


async def test_the_probe_repeats_a_failed_request_under_the_policy(slept: list[float]) -> None:
    gateway = EmbeddingGateway(retry_budget=RetryBudget())
    embedder = _Embedder(_status_error(503), _status_error(503), model="m", dimension=5)
    assert await gateway.probe_dimension(embedder) == 5  # type: ignore[arg-type]
    assert embedder.requests == MAX_ATTEMPTS

    failing = _Embedder(*[_status_error(503)] * 5, model="m2")
    with pytest.raises(openai.InternalServerError):
        await gateway.probe_dimension(failing)  # type: ignore[arg-type]
    assert failing.requests == MAX_ATTEMPTS, "one logical probe is at most three requests"


async def test_concurrent_probes_of_one_key_pay_once() -> None:
    """Five callers that arrive together for one provider key wait for the first answer: one paid request."""
    import asyncio

    gateway = EmbeddingGateway(retry_budget=RetryBudget())
    release = asyncio.Event()

    class Slow(_Embedder):
        async def aembed_query(self, text: str) -> list[float]:
            await release.wait()
            return await super().aembed_query(text)

    embedders = [Slow(model="m", dimension=6, api_key="k") for _ in range(5)]
    tasks = [asyncio.create_task(gateway.probe_dimension(e)) for e in embedders]  # type: ignore[arg-type]
    await asyncio.sleep(0.05)
    release.set()
    assert await asyncio.gather(*tasks) == [6] * 5
    assert sum(e.requests for e in embedders) == 1


def test_a_cohere_error_keeps_its_retry_after_from_its_own_headers() -> None:
    """Cohere's ApiError carries the headers on the exception and has no response object (the key keeps its casing)."""
    from app.services.llm_gateway.embedding_gateway import retry_after_of

    class ApiError(Exception):
        status_code = 429
        headers = {"Retry-After": "42"}

    assert retry_after_of(ApiError("slow")) == 42.0
    assert EmbeddingGateway.retry_kind(ApiError("slow")) is RetryKind.THROTTLE


async def test_two_azure_deployments_of_one_endpoint_have_their_own_probe() -> None:
    gateway = EmbeddingGateway(retry_budget=RetryBudget())
    a = _Embedder(model="text-embedding", dimension=1536, api_key="k", deployment="small")
    b = _Embedder(model="text-embedding", dimension=3072, api_key="k", deployment="large")
    assert await gateway.probe_dimension(a) == 1536  # type: ignore[arg-type]
    assert await gateway.probe_dimension(b) == 3072  # type: ignore[arg-type]
    assert (a.requests, b.requests) == (1, 1)


def test_requests_transport_failures_are_transient() -> None:
    """Voyage's client is ``requests``: its connection and timeout errors are not the builtin ones."""
    import requests

    for error in (requests.exceptions.ConnectionError("refused"), requests.exceptions.ConnectTimeout("slow"),
                  requests.exceptions.ReadTimeout("slow")):
        assert EmbeddingGateway.retry_kind(error) is RetryKind.TRANSIENT
