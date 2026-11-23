"""A provider's Retry-After into the process-wide read pause: finite, at most an hour, and from every provider's throttle."""

from __future__ import annotations

import math

import httpx
import openai
import pytest

from app.services.messaging.backpressure import BackpressureCoordinator
from app.utils.embedding_retry import signal_backpressure_if_rate_limited


def _openai_429(header: str) -> openai.RateLimitError:
    request = httpx.Request("POST", "http://127.0.0.1:9/v1/embeddings")
    response = httpx.Response(429, headers={"retry-after": header}, request=request, json={"error": {"message": "slow"}})
    return openai.RateLimitError("slow", response=response, body=None)


@pytest.mark.parametrize("header", ["30", "3600", "86400", "1e9", "inf", "nan", "-5"])
def test_the_pause_from_a_provider_retry_after_is_bounded(header: str) -> None:
    coordinator = BackpressureCoordinator()
    signal_backpressure_if_rate_limited(_openai_429(header), service_name="openAI", coordinator=coordinator)
    remaining = coordinator.pause_remaining()
    assert math.isfinite(remaining) and remaining <= 3600.0 + 1, f"paused {remaining:.6g}s"
    if header == "30":
        assert 29 <= remaining <= 30
    if header in {"3600", "86400", "1e9"}:
        assert 3599 <= remaining <= 3600.0 + 1
    if header in {"inf", "nan", "-5"}:
        assert remaining == 0.0, "an unusable header pauses nothing"


@pytest.mark.parametrize("seconds", [1e9, float("inf"), float("nan"), 86_400.0])
def test_the_coordinator_itself_clamps_whatever_it_is_given(seconds: float) -> None:
    coordinator = BackpressureCoordinator()
    coordinator.signal("any", seconds)
    assert coordinator.pause_remaining() <= 3600.0 + 1


def test_a_throttle_from_a_provider_that_is_not_openai_signals() -> None:
    """Cohere, Voyage and the like raise their own errors with a 429 status and response headers."""

    class _ProviderError(Exception):
        status_code = 429

        class response:
            headers = {"retry-after": "12"}

    coordinator = BackpressureCoordinator()
    signal_backpressure_if_rate_limited(_ProviderError("slow down"), service_name="cohere", coordinator=coordinator)
    assert 11 <= coordinator.pause_remaining() <= 12


def test_an_outage_or_a_request_fault_does_not_pause_reads() -> None:
    class _Down(Exception):
        status_code = 503

        class response:
            headers = {"retry-after": "30"}

    coordinator = BackpressureCoordinator()
    signal_backpressure_if_rate_limited(_Down("down"), service_name="cohere", coordinator=coordinator)
    signal_backpressure_if_rate_limited(ValueError("bad"), service_name="cohere", coordinator=coordinator)
    assert coordinator.pause_remaining() == 0.0
