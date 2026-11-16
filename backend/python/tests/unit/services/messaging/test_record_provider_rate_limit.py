"""A model provider's rate limit past a call's allowance puts the record back for the provider's wait (no attempt spent).

The provider said when it can serve again (its Retry-After, clamped). Redelivering the record after the 15 s retry backoff
would call it again too early and dead-letter the record after three such tries.
"""

from __future__ import annotations

import pytest

from app.services.llm_gateway.embedding_gateway import EmbeddingRateLimited
from app.services.llm_gateway.gateway import RateLimited
from app.services.messaging.consumer_concurrency import RequeueWithoutAttempt
from app.services.messaging.provider_rate_limit import ProviderRateLimitRequeue
from tests.unit.services.messaging.test_record_handler_parse_now import (
    Harness,
    _payload,
    _record,
)


@pytest.mark.parametrize("final", [False, True])
@pytest.mark.parametrize(
    "error",
    [RateLimited("model busy", provider="p", retry_after=300.0), EmbeddingRateLimited("embeddings busy", retry_after=300.0)],
    ids=["llm", "embeddings"],
)
async def test_a_provider_rate_limit_hands_the_record_back_for_its_wait(error: Exception, final: bool) -> None:
    harness = Harness(_record(indexingStatus="QUEUED"))
    harness.fail_with = error
    with pytest.raises(ProviderRateLimitRequeue) as raised:
        await harness.run(_payload(parseNow=False, is_final_failure=final))
    assert isinstance(raised.value, RequeueWithoutAttempt) and raised.value.delay_s == 300.0
    assert raised.value.pauses_consumer is False
    # A wait is not a verdict: the record is not marked FAILED, even on its last attempt.
    assert all(write.get("indexingStatus") != "FAILED" for write in harness.writes())


async def test_an_ordinary_failure_is_still_an_ordinary_retry() -> None:
    harness = Harness(_record(indexingStatus="QUEUED"))
    harness.fail_with = RuntimeError("parser busy")
    with pytest.raises(Exception) as raised:
        await harness.run(_payload(parseNow=False, is_final_failure=False))
    assert not isinstance(raised.value, RequeueWithoutAttempt)


@pytest.mark.parametrize("final", [False, True])
async def test_the_handed_back_record_is_queued_so_stale_recovery_does_not_republish_it(final: bool) -> None:
    """A record whose attempt ran longer than the lease must not stay IN_PROGRESS with its old start time: the stale scan
    would call the rate-limited provider again within a minute. It goes back in line like a transient failure."""
    harness = Harness(_record(indexingStatus="IN_PROGRESS", processingStartedAt=1_000, parsingStatus="IN_PROGRESS"))
    harness.fail_with = RateLimited("model busy", provider="p", retry_after=300.0)
    with pytest.raises(ProviderRateLimitRequeue):
        await harness.run(_payload(parseNow=False, is_final_failure=final))
    queued = [w for w in harness.writes() if w.get("indexingStatus") == "QUEUED"]
    assert queued and queued[-1]["processingStartedAt"] is None
    assert all(w.get("indexingStatus") != "FAILED" for w in harness.writes())
