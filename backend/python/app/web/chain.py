"""Try sources in order until one answers, within one deadline.

Falling back to the next source is the retry: an engine that blocked this server, a key that was
rejected or used up, or a rate limit is remembered for a while, so later calls skip that source
instead of waiting on it again.
"""

import asyncio
import logging
import time
import weakref
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

from app.web.deadline import Deadline
from app.web.errors import (
    AuthFailed,
    Blocked,
    QuotaExceeded,
    RateLimited,
    TimedOut,
    UpstreamError,
    WebError,
)
from app.web.models import Attempt
from app.web.ports import Source

logger = logging.getLogger(__name__)

S = TypeVar("S", bound=Source)
T = TypeVar("T")

# How long a source is skipped after each kind of failure, in seconds.
_COOLDOWN_S: dict[type[WebError], float] = {
    Blocked: 300.0,
    AuthFailed: 600.0,
    QuotaExceeded: 600.0,
    RateLimited: 60.0,
}
_MAX_RETRY_AFTER_S = 3600.0


class Cooldowns:
    """Sources to skip until a time, keyed by ``Source.cooldown_key``. One per process."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._until: dict[str, float] = {}
        self._why: dict[str, WebError] = {}

    def cooling(self, key: str) -> bool:
        return self._until.get(key, 0.0) > self._clock()

    def why(self, key: str) -> WebError | None:
        """The failure a cooling source is sitting out."""
        return self._why.get(key) if self.cooling(key) else None

    def failed(self, key: str, error: WebError) -> None:
        if error.page_level:
            return
        seconds = _COOLDOWN_S.get(type(error))
        if isinstance(error, RateLimited) and error.retry_after_s:
            seconds = min(error.retry_after_s, _MAX_RETRY_AFTER_S)
        if seconds:
            self._until[key] = self._clock() + seconds
            self._why[key] = error

    def answered(self, key: str) -> None:
        self._until.pop(key, None)
        self._why.pop(key, None)


class InFlight:
    """At most ``Source.max_in_flight`` calls of a source at once, per ``cooldown_key``. One per process.

    A free engine scraped from one address blocks it when several searches arrive together (WP62-SRCH:
    of six parallel searches, three were blocked). Held to a few at a time, the first block is remembered before the
    waiting searches reach the engine, and they skip it instead of being blocked too."""

    def __init__(self) -> None:
        # Per event loop: a semaphore is bound to the loop that first waits on it.
        self._loops: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Semaphore]] = (
            weakref.WeakKeyDictionary()
        )

    def slot(self, key: str, limit: int) -> asyncio.Semaphore:
        held = self._loops.setdefault(asyncio.get_running_loop(), {})
        if key not in held:
            held[key] = asyncio.Semaphore(limit)
        return held[key]


def _skipped(source: Source, error: WebError) -> WebError:
    """What a source cooling since this search began answers, without being asked again."""
    skipped = type(error)(f"{error.message} (moments ago; not asked again)", source=source.id, status=error.status)
    skipped.page_level = error.page_level
    return skipped


@dataclass(frozen=True)
class ChainOutcome(Generic[T]):
    value: T
    served_by: str
    attempts: tuple[Attempt, ...]


class ChainFailed(WebError):
    """Every source failed. Reported as the last failure, with all of them in the message."""

    def __init__(self, errors: Sequence[WebError], attempts: Sequence[Attempt]) -> None:
        self.errors = tuple(errors)
        self.attempts = tuple(attempts)
        super().__init__("; ".join(f"{a.source}: {e.message}" for a, e in zip(attempts, errors, strict=True)))

    @property
    def error_type(self) -> str:  # type: ignore[override]
        return self.errors[-1].error_type

    @property
    def retryable(self) -> bool:
        return any(e.retryable for e in self.errors)


async def run_chain(
    sources: Sequence[S],
    call: Callable[[S, Deadline], Awaitable[T]],
    deadline: Deadline,
    *,
    attempt_s: float,
    cooldowns: Cooldowns,
    in_flight: InFlight | None = None,
) -> ChainOutcome[T]:
    """``call`` each source in turn until one returns. Each gets ``attempt_s`` at most, except the
    last one tried, which gets whatever time is left. Sources cooling down are skipped, unless
    every source is. With ``in_flight``, a source that limits its concurrent calls (``max_in_flight``)
    is waited for within its attempt's time, and skipped if it started cooling while this search waited."""
    clock = deadline.clock
    fresh = [s for s in sources if not cooldowns.cooling(s.cooldown_key)]
    # Every source cooling: they are all tried anyway, so a cooldown never empties the chain.
    ready = fresh or list(sources)
    attempts: list[Attempt] = []
    errors: list[WebError] = []
    for index, source in enumerate(ready):
        if deadline.expired:
            break
        last = index == len(ready) - 1
        step = deadline if last else Deadline(min(deadline.expires_at, clock() + attempt_s), clock)
        started = clock()
        try:
            try:
                value = await _call(source, step, call, cooldowns, in_flight, recheck=bool(fresh))
            except WebError:
                raise
            except Exception as e:
                # A source's bug (an answer shaped unlike anything it expects) costs that source,
                # not the whole search: the next one is still tried.
                logger.warning("Web source %s failed unexpectedly", source.id, exc_info=True)
                raise UpstreamError(f"{source.id} failed unexpectedly", source=source.id) from e
        except WebError as e:
            cooldowns.failed(source.cooldown_key, e)
            attempts.append(Attempt(source.id, e.error_type, clock() - started))
            errors.append(e)
            if e.ends_chain:
                break
            continue
        cooldowns.answered(source.cooldown_key)
        attempts.append(Attempt(source.id, None, clock() - started))
        return ChainOutcome(value, source.id, tuple(attempts))
    if not errors:
        raise TimedOut("No time left to try any source")
    raise ChainFailed(errors, attempts)


async def _call(
    source: S,
    step: Deadline,
    call: Callable[[S, Deadline], Awaitable[T]],
    cooldowns: Cooldowns,
    in_flight: InFlight | None,
    *,
    recheck: bool,
) -> T:
    limit = getattr(source, "max_in_flight", None)
    if in_flight is None or not isinstance(limit, int) or limit < 1:
        return await call(source, step)
    slot = in_flight.slot(source.cooldown_key, limit)
    try:
        async with asyncio.timeout(max(step.remaining(), 0.0)):
            await slot.acquire()
    except TimeoutError:
        raise TimedOut(f"{source.id} was busy with other searches", source=source.id) from None
    try:
        why = cooldowns.why(source.cooldown_key) if recheck else None
        if why is not None:
            raise _skipped(source, why)
        return await call(source, step)
    finally:
        slot.release()
