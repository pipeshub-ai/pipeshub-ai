"""Gateway and embedding-gateway behaviours found in review: the breaker per attempt, the wait on exhausted attempts, httpx and
Voyage statuses, local embedders that skip the gateway, and a hand-back that survives a failed cleanup."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from requests.structures import CaseInsensitiveDict

from app.services.llm_gateway import gateway as gateway_module
from app.services.llm_gateway import wire
from app.services.llm_gateway.embedding_gateway import (
    EmbeddingGateway,
    _status_of,
    retry_after_of,
)
from app.services.llm_gateway.gateway import (
    LLMGateway,
    ProviderUnavailableError,
    RateLimited,
)
from app.services.spend_guard.retry_policy import RetryBudget, RetryKind
from app.utils.custom_embeddings import VoyageAPIError, _check_response


class _Scripted:
    requests = 0

    def __init__(self, *script: tuple[int, float | None]) -> None:
        self.script = list(script)

    async def ainvoke(self, value: object, /, **kwargs: object) -> str:
        wire._request_hook(httpx.Request("POST", "http://host/v1/chat"))
        self.requests += 1
        status, retry_after = self.script.pop(0) if self.script else (200, None)
        wire.note_response(status, retry_after)
        if status >= 400:
            raise RuntimeError(f"HTTP {status}")
        return "ok"


@pytest.fixture
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    real = asyncio.sleep

    async def quick(_s: float) -> None:
        await real(0)

    monkeypatch.setattr(gateway_module.asyncio, "sleep", quick)


async def test_a_retry_does_not_send_once_the_process_breaker_opened_during_settlement(fast: None) -> None:
    """Without fleet Redis the breaker is rechecked with the slot held on every attempt: a 503 that opens it ends the call."""
    gateway = LLMGateway(2, retry_budget=RetryBudget(), failure_threshold=1, cooldown_seconds=60)
    model = _Scripted((503, None), (503, None), (503, None))
    with pytest.raises(ProviderUnavailableError):
        await gateway.invoke(model, "x", provider="P:m@h")
    assert model.requests == 1


async def test_a_long_wait_on_the_last_attempt_is_still_handed_to_the_caller(fast: None) -> None:
    """Three 429s whose last asks 120 s: the attempts are used, but the provider's wait is not dropped."""
    model = _Scripted((429, 1.0), (429, 1.0), (429, 120.0))
    with pytest.raises(RateLimited) as raised:
        await LLMGateway(2, retry_budget=RetryBudget()).invoke(model, "x", provider="P:m@h")
    assert raised.value.retry_after == 120.0 and model.requests == 3


async def test_a_5xx_without_a_wait_stays_an_ordinary_outage(fast: None) -> None:
    with pytest.raises(ProviderUnavailableError) as raised:
        await LLMGateway(2, retry_budget=RetryBudget()).invoke(_Scripted(*[(503, None)] * 3), "x", provider="P:m@h")
    assert not isinstance(raised.value, RateLimited)


def test_an_httpx_status_error_is_classified_by_its_response_status() -> None:
    for status, kind in ((429, RetryKind.THROTTLE), (503, RetryKind.TRANSIENT), (400, None)):
        response = httpx.Response(status, request=httpx.Request("POST", "http://x"))
        error = httpx.HTTPStatusError("boom", request=response.request, response=response)
        assert _status_of(error) == status and EmbeddingGateway.retry_kind(error) is kind


@pytest.mark.parametrize(("status", "retry_after", "kind"), [(503, None, RetryKind.TRANSIENT), (429, "7", RetryKind.THROTTLE),
                                                             (400, None, None)])
def test_a_voyage_error_keeps_its_status_and_retry_after(status: int, retry_after: str | None, kind: RetryKind | None) -> None:
    class _Http:
        status_code = status
        headers = CaseInsensitiveDict({"Retry-After": retry_after} if retry_after else {})  # as requests returns them

    with pytest.raises(VoyageAPIError) as raised:
        _check_response({"detail": "busy"}, _Http())
    assert EmbeddingGateway.retry_kind(raised.value) is kind
    assert retry_after_of(raised.value) == (float(retry_after) if retry_after else None)


# ---- a throttle's wait on the last attempt, the status over the text, botocore headers, send charging -----------------------


async def test_a_400_whose_body_mentions_429_is_not_retried(fast: None) -> None:
    """The wire says 400 (the request's fault): "429" in the error text must not make it a throttle to repeat."""

    class Bad:
        requests = 0

        async def ainvoke(self, value: object, /, **kwargs: object) -> str:
            wire._request_hook(httpx.Request("POST", "http://host/v1/chat"))
            Bad.requests += 1
            wire.note_response(400)
            raise RuntimeError("invalid request: tokens=429, rate limit header missing")

    with pytest.raises(RuntimeError, match="invalid request"):
        await LLMGateway(2, retry_budget=RetryBudget()).invoke(Bad(), "x", provider="P:m@h")
    assert Bad.requests == 1


def test_the_last_attempt_reports_a_retry_after_beyond_the_allowance_like_any_other() -> None:
    from app.services.spend_guard.retry_policy import (
        RETRY_AFTER_BEYOND_BUDGET,
        decide_retry,
    )

    decision = decide_retry(attempt=2, kind=RetryKind.THROTTLE, budget=RetryBudget(), retry_after_s=120.0)
    assert decision.reason == RETRY_AFTER_BEYOND_BUDGET and not decision.retry


def test_a_botocore_throttle_keeps_its_retry_after() -> None:
    class _Boto(Exception):
        response = {"ResponseMetadata": {"HTTPStatusCode": 429, "HTTPHeaders": {"retry-after": "120"}}}

    assert retry_after_of(_Boto()) == 120.0
    assert EmbeddingGateway.retry_kind(_Boto()) is RetryKind.THROTTLE


async def test_the_embeddings_gateway_hands_over_a_throttles_wait_when_its_attempts_are_used(fast: None) -> None:
    from app.services.llm_gateway.embedding_gateway import EmbeddingRateLimited

    class _Throttle(Exception):
        status_code = 429

        class response:
            headers = {"retry-after": "20"}

    async def call() -> None:
        raise _Throttle("slow down")

    gateway = EmbeddingGateway(retry_budget=RetryBudget(), max_attempts=1)
    with pytest.raises(EmbeddingRateLimited) as raised:
        await gateway.call(call)
    assert raised.value.retry_after == 20.0


async def test_the_failed_requests_of_an_embeddings_call_are_charged_to_the_jobs_send_meter(fast: None) -> None:
    """A failed remote embedding request is a send of the stage job it runs in, like a failed model call."""

    class _Down(Exception):
        status_code = 503

    async def call() -> None:
        wire.note_request_sent()
        wire.note_response(503)
        raise _Down("down")

    meter = wire.SendMeter(allowance=10)
    with wire.metered(meter), pytest.raises(_Down):
        await EmbeddingGateway(retry_budget=RetryBudget()).call(call)
    assert meter.failed == 3, "each of the three attempts sent one request that failed"


async def test_a_call_that_times_out_after_a_429_header_is_not_sent_again(fast: None) -> None:
    """Headers said 429 and the body never came: the caller's time ran out. That is not a throttle to repeat."""

    class Slow:
        requests = 0

        async def ainvoke(self, value: object, /, **kwargs: object) -> str:
            wire._request_hook(httpx.Request("POST", "http://host/v1/chat"))
            Slow.requests += 1
            wire.note_response(429, None)
            raise TimeoutError("read timed out")

    with pytest.raises(Exception):  # noqa: B017 - TimeoutError, or the gateway's wrapper of it
        await LLMGateway(2, retry_budget=RetryBudget()).invoke(Slow(), "x", provider="P:m@h", timeout=0.01)
    assert Slow.requests == 1
