"""Put a record back in line when its source rate-limits the content fetch.

A connector's stream answers 429 with a ``Retry-After`` when the source (or
the connector's own quota reserve) will not serve the record yet. Retrying it
a few times within seconds, and then failing it, loses a record the source
would serve after its reset. Waiting it out inside the consumer would hold a
worker slot for up to an hour and stall every other connector behind it.

So the record handler acknowledges the event as handled and leaves the record
QUEUED with ``retryNotBefore``. Nothing on the broker then holds it, and no
failed attempt is counted. The stranded-record sweep in ``indexing_main``
re-sends it once that time has passed, skipping the minimum age and back-off
it applies to lost events, since it knows exactly why this one is waiting.

``rateLimitedSince`` is when this run of waits began. A wait that would end
more than ``INDEXING_RATE_LIMIT_MAX_DEFER_SECONDS`` (24h) after it ends the
record FAILED instead, so no record waits for ever. The default is a day
because the limits indexing meets reset well inside one (GitHub's primary
window is an hour, secondary limits a minute or so; a daily API quota is the
longest common one).

A record still refused after a day is not waiting on a reset: something is
spending the quota as fast as it comes back. On a large first sync that is
usually the connector's own backlog, the other records ahead of it using each
window's quota as it opens, so the connector keeps indexing while this record
keeps missing out. The record handler tells the two apart before failing one:
if a record of the same connector finished indexing during the wait, the
record is only waiting its turn, and its run of waits starts again
(``new_run``). Only a record whose connector got nothing indexed for the whole
allowance ends FAILED.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from tenacity import RetryError

from app.config.constants.arangodb import ProgressStatus
from app.config.constants.http_status_code import HttpStatusCode
from app.modules.indexing.record_diagnostics import WriteKind
from app.modules.indexing.record_diagnostics import reference as run_reference
from app.utils.api_call import ApiCallError

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

RETRY_NOT_BEFORE_FIELD = "retryNotBefore"
RATE_LIMITED_SINCE_FIELD = "rateLimitedSince"
# A record handed back to the broker for a wait (a model provider's rate limit): the time the broker's delayed message
# fires. The recovery scans leave the record alone until then. ``processingStartedAt`` stays the time processing really
# began; nothing is dated into the future.
DEFERRED_UNTIL_FIELD = "deferredUntilMs"
# When (ms) a record was first handed back for a model provider's rate limit: set once, never moved. The wall time since then is
# what ``RATE_LIMIT_MAX_WAIT_S`` bounds, whatever the waits were (a Retry-After of 1 s cannot go on for ever).
RATE_LIMITED_SINCE_MS_FIELD = "rateLimitedSinceMs"

# A Retry-After of 0 still has to land after the queue time it is written
# with, or the sweep could not tell the wait from an old one.
_MIN_WAIT_MS = 1000


@dataclass(frozen=True)
class DeferralPlan:
    not_before_ms: int
    since_ms: int
    give_up: bool


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _causes(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    pending: list[BaseException | None] = [exc]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        # make_api_call's tenacity wrapper does not re-raise, so a 429 that was
        # retried arrives wrapped.
        if isinstance(current, RetryError) and current.last_attempt.failed:
            pending.append(current.last_attempt.exception())
        pending.extend((current.__cause__, current.__context__))


def rate_limit_retry_after(exc: BaseException) -> float | None:
    """Seconds the source asked us to wait, when ``exc`` is a 429 that said."""
    for cause in _causes(exc):
        if (
            isinstance(cause, ApiCallError)
            and cause.status_code == HttpStatusCode.TOO_MANY_REQUESTS.value
            and cause.retry_after_seconds is not None
        ):
            return max(0.0, float(cause.retry_after_seconds))
    return None


def is_deferred_until_later(record: Mapping[str, object], now_ms: float) -> bool:
    """Whether the record was handed back for a wait that is not over yet (``deferredUntilMs`` in the future)."""
    until = _number(record.get(DEFERRED_UNTIL_FIELD))
    return until is not None and until > now_ms


def handback_wait_ms(wait_s: float) -> int:
    """How long a hand-back's deferral lasts: the wait in milliseconds, at least ``_MIN_WAIT_MS``."""
    return max(_MIN_WAIT_MS, math.ceil(wait_s * 1000))


HANDBACK_BACKOFF_FIRST_S = 30.0
HANDBACK_MAX_S = 3600.0


def handback_delay_s(wait_s: float, since_ms: int | None, now_ms: int, *, rng: random.Random | None = None) -> float:
    """How long the record's next rate-limit hand-back lasts: at least ``clamp(max(Retry-After, 30 s x 2^(n-1)), 30 s, 3600 s)``
    for its n-th hand-back, plus up to 10 % jitter (never past an hour). A provider answering ``Retry-After: 1`` is then
    called about fourteen times per record over six hours (30, 60, 120 ... 3600 s), not about twenty thousand.

    ``n`` is read from the time since the first hand-back (the field that bounds them), so no counter is stored: after k
    hand-backs of pure backoff that time is 30 x (2^k - 1) s, so ``n = 1 + floor(log2(elapsed / 30 + 1))``. Longer
    Retry-After waits only make the elapsed time, and so ``n``, grow faster.
    """
    elapsed_s = max(0.0, (now_ms - since_ms) / 1000.0) if since_ms else 0.0
    n = 1 + int(math.log2(elapsed_s / HANDBACK_BACKOFF_FIRST_S + 1.0))
    backoff = HANDBACK_BACKOFF_FIRST_S * 2 ** (n - 1)
    delay = min(HANDBACK_MAX_S, max(HANDBACK_BACKOFF_FIRST_S, wait_s, backoff))
    return min(HANDBACK_MAX_S, delay * (1.0 + (rng or random).uniform(0.0, 0.1)))


def provider_rate_limit_wait(exc: BaseException) -> float | None:
    """Seconds a model provider asked us to wait when ``exc`` (or a cause) is its rate limit past the call's allowance
    (``RateLimited``, ``EmbeddingRateLimited``: marked ``rate_limited``); None otherwise. Never longer than the clamp the
    error already applies."""
    for cause in _causes(exc):
        wait = getattr(cause, "retry_after", None)
        if getattr(cause, "rate_limited", False) is True and isinstance(wait, (int, float)) and not isinstance(wait, bool):
            return max(0.0, float(wait)) if math.isfinite(float(wait)) else None
    return None


def plan_deferral(
    record: Mapping[str, object],
    retry_after_seconds: float,
    *,
    now_ms: int,
    max_total_seconds: float,
    new_run: bool = False,
) -> DeferralPlan:
    """When to send the record again, and whether waiting has gone on too long.

    The run of waits carries on from the record's last deferral unless that
    one ended more than the whole allowance ago, which means the record was
    left alone long enough to count as a new wait. ``new_run`` starts a new
    run now regardless, for a record found to be waiting behind its own
    connector's backlog; it still gives up if this one wait is too long.
    """
    max_total_ms = max_total_seconds * 1000
    not_before = now_ms + max(_MIN_WAIT_MS, math.ceil(retry_after_seconds * 1000))
    since = _number(record.get(RATE_LIMITED_SINCE_FIELD))
    previous_not_before = _number(record.get(RETRY_NOT_BEFORE_FIELD))
    if (
        new_run
        or since is None
        or previous_not_before is None
        or now_ms - previous_not_before > max_total_ms
    ):
        since = now_ms
    since_ms = int(min(since, now_ms))
    return DeferralPlan(
        not_before_ms=not_before,
        since_ms=since_ms,
        give_up=not_before - since_ms > max_total_ms,
    )


def deferral_updates(plan: DeferralPlan, *, reason: str, record: Mapping[str, object], now_ms: int) -> dict:
    """The record's fields while it waits: QUEUED, with nothing claiming it."""
    updates: dict[str, object] = {
        "indexingStatus": ProgressStatus.QUEUED.value,
        "queuedAtTimestamp": now_ms,
        "processingStartedAt": None,
        "reason": reason,
        RETRY_NOT_BEFORE_FIELD: plan.not_before_ms,
        RATE_LIMITED_SINCE_FIELD: plan.since_ms,
    }
    if record.get("parsingStatus") == ProgressStatus.IN_PROGRESS.value:
        updates["parsingStatus"] = ProgressStatus.NOT_STARTED.value
    # A deferral continues the run the record is in; a record from before the run fields existed gains its start.
    updates.update(run_reference(record, WriteKind.QUEUE, now_ms))
    return updates


def is_deferred(record: Mapping[str, object]) -> bool:
    return record.get(RETRY_NOT_BEFORE_FIELD) is not None or record.get(RATE_LIMITED_SINCE_FIELD) is not None


def cleared_deferral() -> dict[str, None]:
    return {RETRY_NOT_BEFORE_FIELD: None, RATE_LIMITED_SINCE_FIELD: None}


def deferral_due(record: Mapping[str, object], now_ms: float) -> bool | None:
    """For the sweep: None if the record is not waiting on a rate limit, else
    whether its wait is over.

    A wait counts only while it is the latest thing to have queued the record
    and the sweep has not already re-sent it: a queue time or a re-send at or
    after ``retryNotBefore`` means something has acted on the record since.
    """
    not_before = _number(record.get(RETRY_NOT_BEFORE_FIELD))
    if not_before is None:
        return None
    for later in (record.get("queuedAtTimestamp"), record.get("lastRepublishedAt")):
        stamp = _number(later)
        if stamp is not None and stamp >= not_before:
            return None
    return now_ms >= not_before
