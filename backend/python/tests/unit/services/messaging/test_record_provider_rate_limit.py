"""A model provider's rate limit past a call's allowance puts the record back for the provider's wait (no attempt spent).

The provider said when it can serve again (its Retry-After, clamped). Redelivering the record after the 15 s retry backoff
would call it again too early and dead-letter the record after three such tries.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

from app.exceptions.indexing_exceptions import ProviderRateLimitedTooLongError
from app.services.llm_gateway.embedding_gateway import EmbeddingRateLimited
from app.services.llm_gateway.gateway import RateLimited
from app.services.messaging.consumer_concurrency import RequeueWithoutAttempt
from app.services.messaging.provider_rate_limit import ProviderRateLimitRequeue
from tests.support.record_event_harness import (
    Harness,
)
from tests.support.record_event_harness import (
    make_payload as _payload,
)
from tests.support.record_event_harness import (
    make_record as _record,
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


async def test_a_parse_now_run_is_handed_back_for_the_wait() -> None:
    """A reader waiting on a parse-now run still gets the provider's wait, not a 15 s retry that dead-letters it."""
    harness = Harness(_record(indexingStatus="QUEUED"))
    harness.fail_with = RateLimited("model busy", provider="p", retry_after=300.0)
    with pytest.raises(ProviderRateLimitRequeue) as raised:
        await harness.run(_payload(parseNow=True, is_final_failure=True))
    assert raised.value.delay_s == 300.0
    assert all(write.get("parseNowStatus") != "FAILED" for write in harness.writes())


async def test_an_enrichment_resume_is_handed_back_and_its_wait_is_recorded_not_future_dated() -> None:
    """The wait is recorded on the record (``deferredUntilMs``) and ``processingStartedAt`` is never moved into the future:
    the monitor and dashboards read it as when processing really began."""
    harness = Harness(
        _record(indexingStatus="COMPLETED", extractionStatus="IN_PROGRESS", processingStartedAt=1_000, origin="UPLOAD")
    )
    harness.fail_with = EmbeddingRateLimited("embeddings busy", retry_after=300.0)
    before = int(time.time() * 1000)
    with pytest.raises(ProviderRateLimitRequeue) as raised:
        await harness.run(_payload(parseNow=False, is_final_failure=True))
    assert raised.value.delay_s == 300.0
    deferred = [w["deferredUntilMs"] for w in harness.writes() if w.get("deferredUntilMs")]
    assert deferred and deferred[-1] >= before + 300_000
    assert all(w.get("processingStartedAt") is None or w["processingStartedAt"] <= int(time.time() * 1000)
               for w in harness.writes()), "processingStartedAt must never be in the future"
    assert all(w.get("indexingStatus") != "FAILED" and w.get("extractionStatus") != "FAILED" for w in harness.writes())


async def test_processing_a_record_again_clears_its_wait() -> None:
    harness = Harness(_record(indexingStatus="QUEUED", deferredUntilMs=1_000))
    await harness.run(_payload(parseNow=False))
    assert {"deferredUntilMs": None} in [{"deferredUntilMs": w["deferredUntilMs"]} for w in harness.writes() if "deferredUntilMs" in w]


async def test_a_failed_cleanup_does_not_replace_the_hand_back() -> None:
    """If putting the record back in line fails, the consumer must still see the hand-back (its delay, no attempt counted),
    not the provider's original error, which it would retry at the 15 s backoff."""
    harness = Harness(_record(indexingStatus="IN_PROGRESS", processingStartedAt=1_000, parsingStatus="IN_PROGRESS"))
    harness.fail_with = RateLimited("model busy", provider="p", retry_after=300.0)
    real = harness.graph.update_node.side_effect

    async def update(doc_id: str, collection: str, fields: dict) -> bool:
        if fields.get("indexingStatus") == "QUEUED":
            raise ConnectionError("graph unavailable")
        return await real(doc_id, collection, fields) if real else True

    harness.graph.update_node = AsyncMock(side_effect=update)
    with pytest.raises(ProviderRateLimitRequeue) as raised:
        await harness.run(_payload(parseNow=False, is_final_failure=False))
    assert raised.value.delay_s == 300.0


HOUR_MS = 3_600_000


async def test_each_handback_adds_its_wait_to_the_records_rate_limited_total() -> None:
    harness = Harness(_record(indexingStatus="QUEUED", rateLimitedForMs=2 * HOUR_MS))
    harness.fail_with = RateLimited("model busy", provider="p", retry_after=600.0)
    with pytest.raises(ProviderRateLimitRequeue):
        await harness.run(_payload(parseNow=False, is_final_failure=False))
    totals = [w["rateLimitedForMs"] for w in harness.writes() if w.get("rateLimitedForMs")]
    assert totals and totals[-1] == 2 * HOUR_MS + 600_000


async def test_a_record_rate_limited_past_the_limit_fails_with_an_explicit_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bound is the time spent rate-limited, not the number of hand-backs: past ``RATE_LIMIT_MAX_WAIT_S`` the record is
    failed, with the reason, never dead-lettered silently."""
    from app.services.messaging.config import messaging_env

    monkeypatch.setattr(type(messaging_env), "rate_limit_max_wait_seconds", property(lambda self: 6 * 3600.0))
    harness = Harness(_record(indexingStatus="IN_PROGRESS", rateLimitedForMs=6 * HOUR_MS - 100_000, processingStartedAt=1_000))
    harness.fail_with = RateLimited("model busy", provider="p", retry_after=600.0)
    with pytest.raises(ProviderRateLimitedTooLongError):
        await harness.run(_payload(parseNow=False, is_final_failure=False))
    failed = [w for w in harness.writes() if w.get("indexingStatus") == "FAILED"]
    assert failed and "rate limiting this file for over 6 hours" in failed[-1]["reason"]
    assert failed[-1].get("rateLimitedForMs", 0) is None, "the total starts from zero if the record is reindexed"


async def test_a_record_within_the_limit_is_still_handed_back() -> None:
    harness = Harness(_record(indexingStatus="QUEUED", rateLimitedForMs=5 * HOUR_MS))
    harness.fail_with = RateLimited("model busy", provider="p", retry_after=600.0)
    with pytest.raises(ProviderRateLimitRequeue):
        await harness.run(_payload(parseNow=False, is_final_failure=False))


async def test_a_record_whose_wait_cannot_be_stored_is_not_handed_back_unbounded() -> None:
    """The hand-back is bounded by the rate-limited total stored with it, not by the broker's redelivery budget. If that write
    is refused (a process on an older record schema, a graph error) the record is not handed back: it stays an ordinary failure,
    retried on the delivery attempts, which do bound it."""
    harness = Harness(_record(indexingStatus="IN_PROGRESS", processingStartedAt=1_000, parsingStatus="IN_PROGRESS"))
    harness.fail_with = RateLimited("model busy", provider="p", retry_after=300.0)
    real = harness.graph.update_node.side_effect

    async def update(doc_id: str, collection: str, fields: dict) -> bool:
        if "deferredUntilMs" in fields:
            raise ValueError("document does not match the collection's schema")
        return await real(doc_id, collection, fields) if real else True

    harness.graph.update_node = AsyncMock(side_effect=update)
    with pytest.raises(Exception) as raised:  # noqa: B017
        await harness.run(_payload(parseNow=False, is_final_failure=False))
    assert not isinstance(raised.value, RequeueWithoutAttempt)
