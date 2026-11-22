# ruff: noqa: F401, F811 (the spend-world fixtures are imported for pytest, then named as arguments)
"""A provider's Retry-After is untrusted input: finite, non-negative, at most an hour, wherever it ends up."""

from __future__ import annotations

import math

import httpx
import pytest

from app.services.llm_gateway import gateway as gateway_module
from app.services.llm_gateway import wire
from app.services.llm_gateway.embedding_gateway import EmbeddingRateLimited
from app.services.llm_gateway.gateway import (
    LLMGateway,
    ProviderUnavailableError,
    RateLimited,
)
from app.services.spend_guard.retry_policy import (
    MAX_RETRY_AFTER_S,
    RetryBudget,
    clamp_retry_after,
)
from app.utils.llm import LLMUnavailableError
from tests.support.spend_world import fleet, sim_clock


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"retry-after": "7"}, 7.0),
        ({"retry-after": "0"}, 0.0),
        ({"retry-after": "1.5"}, 1.5),
        ({"retry-after-ms": "1500"}, 1.5),
        ({"retry-after-ms": "1500", "retry-after": "9"}, 1.5),  # the millisecond header is the more exact one
        ({"retry-after": "1e9"}, MAX_RETRY_AFTER_S),
        ({"retry-after": "86400"}, MAX_RETRY_AFTER_S),
        ({"retry-after-ms": "9999999999"}, MAX_RETRY_AFTER_S),
        ({"retry-after": "3600"}, 3600.0),
        ({"retry-after": "inf"}, None),
        ({"retry-after": "-inf"}, None),
        ({"retry-after": "nan"}, None),
        ({"retry-after": "1e400"}, None),
        ({"retry-after": "-5"}, None),
        ({"retry-after-ms": "-5"}, None),
        ({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}, None),
        ({"retry-after": ""}, None),
        ({}, None),
        # An unusable millisecond header does not hide a good seconds one.
        ({"retry-after-ms": "nan", "retry-after": "4"}, 4.0),
    ],
)
def test_parse_retry_after_accepts_only_a_finite_non_negative_number(headers: dict[str, str], expected: float | None) -> None:
    assert wire.parse_retry_after(httpx.Headers(headers)) == expected


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan"), -1, True, "9", None, object()])
def test_clamp_retry_after_ignores_anything_that_is_not_a_finite_non_negative_number(value: object) -> None:
    assert clamp_retry_after(value) is None


def test_clamp_retry_after_caps_a_long_wait() -> None:
    assert clamp_retry_after(1e12) == MAX_RETRY_AFTER_S and clamp_retry_after(5) == 5.0 and clamp_retry_after(0) == 0.0


def test_the_errors_that_carry_a_wait_never_carry_an_unbounded_one() -> None:
    for value in (1e9, float("inf"), float("nan")):
        error = LLMUnavailableError("x", retry_after=value)
        assert error.retry_after is None or (math.isfinite(error.retry_after) and error.retry_after <= MAX_RETRY_AFTER_S)
        assert RateLimited("x", provider="p", retry_after=value).retry_after in (None, MAX_RETRY_AFTER_S)
        assert 0.0 <= EmbeddingRateLimited("x", retry_after=value).retry_after <= MAX_RETRY_AFTER_S


async def test_the_gateway_waits_the_clamped_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    """A header past the call's allowance raises RateLimited carrying at most an hour, and sleeps nothing."""
    slept: list[float] = []

    async def record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(gateway_module.asyncio, "sleep", record)

    class Throttled:
        async def ainvoke(self, value: object, /, **kwargs: object) -> str:
            wire._request_hook(httpx.Request("POST", "http://host/v1/chat"))
            wire.note_response(429, wire.parse_retry_after(httpx.Headers({"retry-after": "1e9"})))
            raise RuntimeError("HTTP 429")

    with pytest.raises(RateLimited) as raised:
        await LLMGateway(2, retry_budget=RetryBudget()).invoke(Throttled(), "hi", provider="p")
    assert raised.value.retry_after == MAX_RETRY_AFTER_S and slept == []


async def test_a_refused_retry_keeps_the_attempts_it_charged(monkeypatch: pytest.MonkeyPatch, fleet) -> None:
    """A call whose first attempt was charged and whose retry the open breaker then refuses still reports the charge."""
    from app.services.llm_gateway.fleet_breaker import (
        FleetBreaker,
        credential_of,
        provider_pk,
        size_class,
    )

    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(gateway_module.asyncio, "sleep", instant)
    key = "P:m@h"

    class Serving:
        async def ainvoke(self, value: object, /, **kwargs: object) -> str:
            wire._request_hook(httpx.Request("POST", "http://host/v1/chat"))
            wire.note_response(200)
            return "ok"

    class Failing:
        async def ainvoke(self, value: object, /, **kwargs: object) -> str:
            wire._request_hook(httpx.Request("POST", "http://host/v1/chat"))
            wire.note_response(503)
            raise RuntimeError("HTTP 503")

    pk = provider_pk(key, credential_of(Failing()))
    breaker = FleetBreaker(fleet)
    for _ in range(100):  # a healthy window, so the failure that follows is chargeable
        await breaker.admit(pk, size_class(1), "j")
        await breaker.settle(pk, size_class(1), wire.OK)
    # Four failures leave the breaker one short of opening; the call's own failure opens it, and the retry is refused.
    for _ in range(4):
        await breaker.admit(pk, size_class(1), "j")
        await breaker.settle(pk, size_class(1), wire.FAIL)
    with pytest.raises(ProviderUnavailableError) as raised:
        await LLMGateway(2, retry_budget=RetryBudget()).invoke(Failing(), "hi", provider=key)
    assert raised.value.requests == 1 and raised.value.charged == 1 and raised.value.chargeable


@pytest.mark.parametrize("key", ["Retry-After", "RETRY-AFTER", "retry-after", "Retry-After-Ms"])
def test_the_header_is_found_whatever_the_case_of_its_key(key: str) -> None:
    """httpx and requests headers are case-insensitive, a plain dict (a stand-in, a botocore header map) is not."""
    value = "2500" if key.lower().endswith("-ms") else "2.5"
    assert wire.parse_retry_after({key: value}) == 2.5
