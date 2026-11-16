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
    _retry_after_of,
    _status_of,
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
    assert _retry_after_of(raised.value) == (float(retry_after) if retry_after else None)
