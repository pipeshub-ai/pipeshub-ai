"""A model provider's rate limit past a call's allowance puts the record back for the provider's wait (no attempt spent).

The provider said when it can serve again (its Retry-After, clamped). Redelivering the record after the 15 s retry backoff
would call it again too early and dead-letter the record after three such tries.
"""

from __future__ import annotations

import math
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


@pytest.fixture(autouse=True)
def _deferral_fields_are_written(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hand-back writes ``deferredUntilMs`` and ``rateLimitedSinceMs`` only when ``RECORD_DEFERRAL_FIELDS_WRITE`` is on."""
    monkeypatch.setenv("RECORD_DEFERRAL_FIELDS_WRITE", "true")


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
    assert isinstance(raised.value, RequeueWithoutAttempt) and 300.0 <= raised.value.delay_s <= 330.0
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
    assert 300.0 <= raised.value.delay_s <= 330.0
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
    assert 300.0 <= raised.value.delay_s <= 330.0
    deferred = [w["deferredUntilMs"] for w in harness.writes() if w.get("deferredUntilMs")]
    assert deferred and deferred[-1] >= before + 300_000, "the deferral lasts at least the provider's wait"
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
    assert 300.0 <= raised.value.delay_s <= 330.0


HOUR_MS = 3_600_000


async def test_the_first_handback_stamps_its_time_and_later_ones_keep_it() -> None:
    """``rateLimitedSinceMs`` is set once and never moved: it is the start of the wall-time bound."""
    first = Harness(_record(indexingStatus="QUEUED"))
    first.fail_with = RateLimited("model busy", provider="p", retry_after=600.0)
    before = int(time.time() * 1000)
    with pytest.raises(ProviderRateLimitRequeue):
        await first.run(_payload(parseNow=False, is_final_failure=False))
    stamps = [w["rateLimitedSinceMs"] for w in first.writes() if w.get("rateLimitedSinceMs")]
    assert stamps and stamps[-1] >= before

    earlier = int(time.time() * 1000) - 2 * HOUR_MS
    later = Harness(_record(indexingStatus="QUEUED", rateLimitedSinceMs=earlier))
    later.fail_with = RateLimited("model busy", provider="p", retry_after=600.0)
    with pytest.raises(ProviderRateLimitRequeue):
        await later.run(_payload(parseNow=False, is_final_failure=False))
    stamps = [w["rateLimitedSinceMs"] for w in later.writes() if w.get("rateLimitedSinceMs")]
    assert stamps == [earlier], "the time of the first hand-back is not moved by later ones"


async def test_a_record_rate_limited_past_the_limit_fails_with_an_explicit_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bound is wall time since the first hand-back: past ``RATE_LIMIT_MAX_WAIT_S`` the record is failed, with the reason,
    never dead-lettered silently."""
    from app.services.messaging.config import messaging_env

    monkeypatch.setattr(type(messaging_env), "rate_limit_max_wait_seconds", property(lambda self: 6 * 3600.0))
    since = int(time.time() * 1000) - 6 * HOUR_MS + 100_000  # 100 s of the limit left; the wait asked for is 600 s
    harness = Harness(_record(indexingStatus="IN_PROGRESS", rateLimitedSinceMs=since, processingStartedAt=1_000))
    harness.fail_with = RateLimited("model busy", provider="p", retry_after=600.0)
    with pytest.raises(ProviderRateLimitedTooLongError):
        await harness.run(_payload(parseNow=False, is_final_failure=False))
    failed = [w for w in harness.writes() if w.get("indexingStatus") == "FAILED"]
    assert failed and "rate limiting this file for over 6 hours" in failed[-1]["reason"]
    assert failed[-1].get("rateLimitedSinceMs", 0) is None, "the clock starts again if the record is reindexed"


async def test_a_record_within_the_limit_is_still_handed_back() -> None:
    harness = Harness(_record(indexingStatus="QUEUED", rateLimitedSinceMs=int(time.time() * 1000) - 4 * HOUR_MS))
    harness.fail_with = RateLimited("model busy", provider="p", retry_after=600.0)
    with pytest.raises(ProviderRateLimitRequeue):
        await harness.run(_payload(parseNow=False, is_final_failure=False))


async def test_a_short_retry_after_repeated_is_backed_off_and_fails_the_record_after_the_wall_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Retry-After: 1`` on every call: each hand-back waits at least 30 s x 2^(n-1) (capped at an hour), so over the six-hour
    wall-time limit the provider is called about fourteen times, not about 21,600. The clock is injected: each delivery
    happens when the previous hand-back's delay is over."""
    from app.services.messaging import config as config_module
    from app.services.messaging.kafka.handlers import record as record_module

    monkeypatch.setattr(type(config_module.messaging_env), "rate_limit_max_wait_seconds", property(lambda self: 6 * 3600.0))
    clock = {"now": 1_800_000_000_000}
    monkeypatch.setattr(record_module, "get_epoch_timestamp_in_ms", lambda: clock["now"])
    stored: dict = _record(indexingStatus="QUEUED")
    delays: list[float] = []
    outcome: BaseException | None = None
    for _ in range(200):
        harness = Harness(dict(stored))
        harness.fail_with = RateLimited("model busy", provider="p", retry_after=1.0)
        real = harness.graph.update_node.side_effect

        async def update(doc_id: str, collection: str, fields: dict, *, _real=real, _stored=stored) -> bool:
            _stored.update(fields)
            return await _real(doc_id, collection, fields) if _real else True

        harness.graph.update_node = AsyncMock(side_effect=update)
        try:
            await harness.run(_payload(parseNow=False, is_final_failure=False))
        except ProviderRateLimitedTooLongError as exc:
            outcome = exc
            break
        except ProviderRateLimitRequeue as requeue:
            delays.append(requeue.delay_s)
            clock["now"] += math.ceil(requeue.delay_s * 1000) + 1
    assert isinstance(outcome, ProviderRateLimitedTooLongError), "the record was handed back for ever"
    calls = len(delays) + 1
    assert 8 <= calls <= 14, f"{calls} provider calls over six hours of Retry-After: 1: {[round(x) for x in delays]}"
    assert delays[0] >= 30.0 and all(b >= a for a, b in zip(delays, delays[1:])), "the waits do not shrink"
    assert max(delays) <= 3600.0


def test_the_handback_delay_is_at_least_the_exponential_backoff_the_provider_wait_and_thirty_seconds() -> None:
    import random

    from app.modules.indexing.rate_limit_deferral import handback_delay_s

    rng = random.Random(1)
    now = 10_000_000_000
    for elapsed_s, wait_s, floor in [(0, 1, 30), (0, 600, 600), (30, 1, 60), (90, 1, 120), (5_000, 1, 3600), (0, 90_000, 3600)]:
        delay = handback_delay_s(wait_s, now - elapsed_s * 1000 if elapsed_s else None, now, rng=rng)
        assert floor <= delay <= min(3600.0, floor * 1.1 + 1e-6), (elapsed_s, wait_s, delay)


async def test_the_deferral_fields_are_not_written_while_the_setting_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """In the release that adds the fields they are accepted by the schema and not written (a process on the older schema
    would refuse them): a rate-limited record keeps the older bounded behaviour, retried on its delivery attempts. No write,
    the final status write included, names a field the older schema lacks."""
    from app.services.messaging.config import messaging_env

    monkeypatch.setattr(type(messaging_env), "record_deferral_fields_write", property(lambda self: False))
    for final in (False, True):
        harness = Harness(_record(indexingStatus="IN_PROGRESS", processingStartedAt=1_000, parsingStatus="IN_PROGRESS"))
        harness.fail_with = RateLimited("model busy", provider="p", retry_after=300.0)
        with pytest.raises(Exception) as raised:
            await harness.run(_payload(parseNow=False, is_final_failure=final))
        assert not isinstance(raised.value, RequeueWithoutAttempt)
        for write in harness.writes():
            assert not {"deferredUntilMs", "rateLimitedSinceMs"} & set(write), write


async def test_the_deferral_fields_are_written_and_honoured_when_the_setting_is_on() -> None:
    harness = Harness(_record(indexingStatus="QUEUED"))
    harness.fail_with = RateLimited("model busy", provider="p", retry_after=300.0)
    with pytest.raises(ProviderRateLimitRequeue):
        await harness.run(_payload(parseNow=False, is_final_failure=False))
    written = [w for w in harness.writes() if "deferredUntilMs" in w]
    assert written and "rateLimitedSinceMs" in written[-1]


def test_the_setting_defaults_off_and_the_schema_lists_both_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """The setting is off by default in this release, and the schema (the newer one) lists both fields."""
    from app.schema.arango.documents import record_schema
    from app.services.messaging.config import messaging_env

    monkeypatch.delenv("RECORD_DEFERRAL_FIELDS_WRITE", raising=False)
    assert messaging_env.record_deferral_fields_write is False
    properties = record_schema["rule"]["properties"]
    assert "deferredUntilMs" in properties and "rateLimitedSinceMs" in properties


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
    with pytest.raises(Exception) as raised:
        await harness.run(_payload(parseNow=False, is_final_failure=False))
    assert not isinstance(raised.value, RequeueWithoutAttempt)


async def test_a_wait_that_rounds_to_nothing_still_defers_for_the_minimum() -> None:
    """A positive sub-millisecond Retry-After still defers the record for the minimum wait."""
    harness = Harness(_record(indexingStatus="QUEUED"))
    harness.fail_with = RateLimited("model busy", provider="p", retry_after=0.0004)
    before = int(time.time() * 1000)
    with pytest.raises(ProviderRateLimitRequeue):
        await harness.run(_payload(parseNow=False, is_final_failure=False))
    until = [w["deferredUntilMs"] for w in harness.writes() if w.get("deferredUntilMs")]
    assert until and until[-1] >= before + 1000


async def test_a_store_that_answers_false_does_not_count_as_a_stored_wait() -> None:
    harness = Harness(_record(indexingStatus="QUEUED"))
    harness.fail_with = RateLimited("model busy", provider="p", retry_after=300.0)
    real = harness.graph.update_node.side_effect

    async def update(doc_id: str, collection: str, fields: dict) -> bool:
        if "deferredUntilMs" in fields:
            return False
        return await real(doc_id, collection, fields) if real else True

    harness.graph.update_node = AsyncMock(side_effect=update)
    with pytest.raises(Exception) as raised:
        await harness.run(_payload(parseNow=False, is_final_failure=False))
    assert not isinstance(raised.value, RequeueWithoutAttempt)


async def test_with_the_setting_off_no_write_touches_either_field_even_when_a_resume_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """The old record schema refuses both fields: with ``RECORD_DEFERRAL_FIELDS_WRITE`` off nothing writes them, clears and
    the failed-resume path included."""
    from app.services.messaging.config import messaging_env

    monkeypatch.setattr(type(messaging_env), "record_deferral_fields_write", property(lambda self: False))
    harness = Harness(_record(indexingStatus="COMPLETED", extractionStatus="IN_PROGRESS", origin="UPLOAD", processingStartedAt=1_000))
    await harness.handler._fail_resumed_enrichment("rec-1")
    await harness.handler._clear_handed_back_wait("rec-1", {"deferredUntilMs": 5})
    await harness.handler._end_provider_rate_limit_total("rec-1", {"rateLimitedSinceMs": 5})
    for write in harness.writes():
        assert not {"deferredUntilMs", "rateLimitedSinceMs"} & set(write), write


async def test_the_first_handback_is_held_to_the_whole_allowance(monkeypatch: pytest.MonkeyPatch) -> None:
    """With a 10 s allowance a hand-back of at least 30 s cannot be made, even the first (no start time yet)."""
    from app.services.messaging.config import messaging_env

    monkeypatch.setattr(type(messaging_env), "rate_limit_max_wait_seconds", property(lambda self: 10.0))
    harness = Harness(_record(indexingStatus="QUEUED"))
    harness.fail_with = RateLimited("model busy", provider="p", retry_after=1.0)
    with pytest.raises(ProviderRateLimitedTooLongError):
        await harness.run(_payload(parseNow=False, is_final_failure=False))


async def test_a_duplicate_delivery_during_the_wait_keeps_the_wait_and_calls_nothing() -> None:
    """A second ``newRecord`` for a record handed back for a provider's wait is returned for what is left of the wait: the wait
    is kept (not cleared), no attempt is spent, and the model is not called."""
    until = int(time.time() * 1000) + 300_000
    harness = Harness(_record(indexingStatus="QUEUED", deferredUntilMs=until))
    with pytest.raises(ProviderRateLimitRequeue) as raised:
        await harness.run(_payload(parseNow=False, is_final_failure=False))
    assert 290.0 <= raised.value.delay_s <= 300.0
    assert harness.runs == [], "the pipeline ran during the provider's wait"
    assert all("deferredUntilMs" not in w for w in harness.writes()), "the wait was cleared"


async def test_the_limit_check_and_the_stored_deadline_use_one_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.messaging.kafka.handlers import record as record_module

    draws = iter([120.0, 132.0, 140.0, 150.0])
    monkeypatch.setattr(record_module, "handback_delay_s", lambda *a, **k: next(draws))
    harness = Harness(_record(indexingStatus="QUEUED"))
    harness.fail_with = RateLimited("model busy", provider="p", retry_after=5.0)
    before = int(time.time() * 1000)
    with pytest.raises(ProviderRateLimitRequeue) as raised:
        await harness.run(_payload(parseNow=False, is_final_failure=False))
    stored = [w["deferredUntilMs"] for w in harness.writes() if w.get("deferredUntilMs")][-1]
    assert raised.value.delay_s == 120.0
    assert before + 120_000 <= stored <= before + 121_000, "the stored deadline is not the delay that was checked"


async def test_a_consumer_whose_clock_runs_behind_does_not_shorten_the_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Elapsed time is derived only from stamps the record carries (``max(now, deferredUntilMs) - rateLimitedSinceMs``), so a
    consumer 15 minutes behind the one that stamped the first hand-back cannot step the backoff back."""
    from app.services.messaging import config as config_module
    from app.services.messaging.kafka.handlers import record as record_module

    monkeypatch.setattr(type(config_module.messaging_env), "rate_limit_max_wait_seconds", property(lambda self: 6 * 3600.0))
    true_now = {"ms": 1_800_000_000_000}
    skew = {"ms": 0}
    monkeypatch.setattr(record_module, "get_epoch_timestamp_in_ms", lambda: true_now["ms"] + skew["ms"])
    stored: dict = _record(indexingStatus="QUEUED")
    calls, outcome = 0, None
    for _ in range(200):
        harness = Harness(dict(stored))
        harness.fail_with = RateLimited("model busy", provider="p", retry_after=1.0)
        real = harness.graph.update_node.side_effect

        async def update(doc_id: str, collection: str, fields: dict, *, _real=real, _stored=stored) -> bool:
            _stored.update(fields)
            return await _real(doc_id, collection, fields) if _real else True

        harness.graph.update_node = AsyncMock(side_effect=update)
        calls += 1
        try:
            await harness.run(_payload(parseNow=False, is_final_failure=False))
        except ProviderRateLimitedTooLongError as exc:
            outcome = exc
            break
        except ProviderRateLimitRequeue as requeue:
            skew["ms"] = -15 * 60_000  # every later consumer runs 15 minutes behind the one that stamped the first hand-back
            true_now["ms"] += math.ceil(requeue.delay_s * 1000) + 1
    assert isinstance(outcome, ProviderRateLimitedTooLongError)
    assert calls <= 14, f"{calls} provider calls with a consumer clock 15 minutes behind"


def test_the_handback_delay_is_jittered_by_at_most_ten_percent() -> None:
    import random

    from app.modules.indexing.rate_limit_deferral import handback_delay_s

    rng = random.Random(7)
    delays = [handback_delay_s(1.0, None, 0, rng=rng) for _ in range(400)]
    assert all(30.0 <= d <= 33.0 + 1e-9 for d in delays)
    assert len({round(d, 6) for d in delays}) > 100, "the delays are not jittered"
    assert max(delays) > 32.0 and min(delays) < 31.0


async def test_the_final_status_write_does_not_touch_the_fields_while_the_setting_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The write that ends a record (FAILED here) names ``rateLimitedSinceMs`` only when the setting is on."""
    from app.services.messaging.config import messaging_env

    async def end_record(on: bool) -> list[dict]:
        monkeypatch.setattr(type(messaging_env), "record_deferral_fields_write", property(lambda self: on))
        harness = Harness(_record(indexingStatus="IN_PROGRESS", rateLimitedSinceMs=1_700_000_000_000, deferredUntilMs=5))
        await harness.handler._RecordEventHandler__update_document_status(
            record_id="rec-1", indexing_status="FAILED", extraction_status="FAILED", reason="x"
        )
        return harness.writes()

    off = await end_record(False)
    assert off and all(not {"rateLimitedSinceMs", "deferredUntilMs"} & set(w) for w in off), off
    on = await end_record(True)
    assert any("rateLimitedSinceMs" in w for w in on), "with the setting on the record's clock is cleared when it ends"


def test_the_startup_warning_is_logged_when_the_setting_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock

    from app.indexing_main import warn_if_deferral_fields_write

    logger = MagicMock()
    monkeypatch.setenv("RECORD_DEFERRAL_FIELDS_WRITE", "true")
    warn_if_deferral_fields_write(logger)
    assert logger.warning.call_count == 1 and "rolled back" in logger.warning.call_args.args[0]
    logger.reset_mock()
    monkeypatch.delenv("RECORD_DEFERRAL_FIELDS_WRITE")
    warn_if_deferral_fields_write(logger)
    logger.warning.assert_not_called()
