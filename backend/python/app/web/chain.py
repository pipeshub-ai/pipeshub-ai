"""Try sources in order until one answers, within one deadline.

Falling back to the next source is the retry: an engine that blocked this server, a key that was
rejected or used up, or a rate limit is remembered for a while, so later calls skip that source
instead of waiting on it again.
"""

import logging
import time
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

    def cooling(self, key: str) -> bool:
        return self._until.get(key, 0.0) > self._clock()

    def failed(self, key: str, error: WebError) -> None:
        if error.page_level:
            return
        seconds = _COOLDOWN_S.get(type(error))
        if isinstance(error, RateLimited) and error.retry_after_s:
            seconds = min(error.retry_after_s, _MAX_RETRY_AFTER_S)
        if seconds:
            self._until[key] = self._clock() + seconds

    def answered(self, key: str) -> None:
        self._until.pop(key, None)


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
) -> ChainOutcome[T]:
    """``call`` each source in turn until one returns. Each gets ``attempt_s`` at most, except the
    last one tried, which gets whatever time is left. Sources cooling down are skipped, unless
    every source is."""
    clock = deadline.clock
    ready = [s for s in sources if not cooldowns.cooling(s.cooldown_key)] or list(sources)
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
                value = await call(source, step)
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
