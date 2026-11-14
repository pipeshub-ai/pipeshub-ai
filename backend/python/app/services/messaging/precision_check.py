"""Handing back, as they are read, the events that would write vectors of the wrong precision.

The weight precision is one setting of the embedding server, so a mismatch with an index is a state of the
deployment, not of a record. A ``PrecisionVerdict`` recomputes that state every few seconds from what the
server says and what the indexes hold. A ``PrecisionFilter`` sits beside the connector-off filter in each
consumer's read path and, while the verdict is mismatch, hands back only the event types that write vectors,
before anything is downloaded or parsed. Deletes, the cleanup's own event and every other event pass.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from app.modules.indexing.connector_off_events import connector_gated_event
from app.services.messaging.precision_flip import CLEANUP_HINT, setting_for
from app.services.vector_db.collection_pin import FP32

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from logging import Logger

    from app.services.messaging.config import StreamMessage

VERDICT_KEY = "/services/indexing/precision_verdict"
POLL_SECONDS = 10.0
VERDICT_TTL_SECONDS = 30
FIRST_HAND_BACK_SECONDS = 60.0
LATER_HAND_BACK_SECONDS = 600.0
_JITTER = 0.2
HANDBACKS_KEY = "_precision_handbacks"


@dataclass(frozen=True)
class ServerState:
    serving: str | None  # the precision of the loaded model, None while it is not loaded
    intent: str | None
    fallback: bool
    next: str | None = None


@dataclass(frozen=True)
class IndexState:
    """A records collection of a model, and the precision its index holds (None for a fresh one)."""

    name: str
    model: str
    precision: str | None


@dataclass
class Verdict:
    mismatch: bool = False
    reasons: list[str] = field(default_factory=list)
    #: The precisions the disagreeing indexes hold: what the setting would have to be put back to.
    holds: list[str] = field(default_factory=list)


def evaluate(states: dict[str, ServerState], indexes: Sequence[IndexState]) -> Verdict:
    """The rule: mismatch when serving differs from an index, or when a fallback cannot be written through.

    1. the serving precision is known and an index of that model holds the other one;
    2. the server fell back to fp32 (int8 asked for) and an index of that model is not fp32;
    3. the server fell back and no index of that model is fp32 (a fresh deployment cannot claim a pin).
    An unloaded model (serving unknown) is not a mismatch: the check in front of each batch decides.
    """
    reasons: list[str] = []
    holds: list[str] = []
    for model, state in states.items():
        mine = [i for i in indexes if i.model == model]
        for index in mine:
            if state.serving is not None and index.precision is not None and state.serving != index.precision:
                reasons.append(f"'{index.name}' holds {index.precision} and {model} now serves {state.serving}")
                holds.append(index.precision)
            elif state.fallback and index.precision is not None and index.precision != FP32:
                reasons.append(f"{model} fell back to fp32 and '{index.name}' holds {index.precision}")
                holds.append(index.precision)
        if state.fallback and not any(i.precision == FP32 for i in mine):
            reasons.append(f"{model} fell back to fp32 where int8 was asked for and no index of it is fp32")
    return Verdict(bool(reasons), reasons, sorted(set(holds)))


class PrecisionVerdict:
    def __init__(
        self,
        *,
        fetch_states: Callable[[], Awaitable[dict[str, ServerState]]],
        indexes: Callable[[], Awaitable[Sequence[IndexState]]],
        publish: Callable[[Verdict], Awaitable[None]] | None = None,
        logger: Logger,
        clock: Callable[[], float] = time.monotonic,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self._fetch_states = fetch_states
        self._indexes = indexes
        self._publish = publish
        self._logger = logger
        self._clock = clock
        self._period = poll_seconds
        self._verdict = Verdict()
        self._last_ok = clock()
        self._forced_until = 0.0
        self._published: tuple[bool, float] | None = None
        self.changes = 0

    @property
    def mismatch(self) -> bool:
        """Mismatch while the last good recomputation says so, or for a period after a flip was seen in a write."""
        return self._verdict.mismatch or self._clock() < self._forced_until

    @property
    def reasons(self) -> list[str]:
        return list(self._verdict.reasons)

    def force_mismatch(self) -> None:
        """A write saw a flip: hand back from now on without waiting for the next poll (at least one period)."""
        self._forced_until = max(self._forced_until, self._clock() + self._period)

    async def refresh(self) -> Verdict:
        """Recompute now. A failed read keeps the last verdict for two periods, then reports match."""
        try:
            states = await self._fetch_states()
            indexes = await self._indexes()
            verdict = evaluate(states, indexes)
        except Exception as exc:
            if self._clock() - self._last_ok > 2 * self._period:
                if self._verdict.mismatch:
                    self.changes += 1
                self._verdict = Verdict()
            self._logger.warning("Could not recompute the weight-precision verdict: %s", type(exc).__name__)
            return self._verdict
        self._last_ok = self._clock()
        if verdict.mismatch != self._verdict.mismatch:
            self.changes += 1
            if verdict.mismatch:
                self._logger.error(
                    "Indexing is waiting for the embedding precision: %s. To continue, set %s and restart the embedding server, %s.",
                    "; ".join(verdict.reasons),
                    setting_for(verdict.holds[0] if len(verdict.holds) == 1 else None),
                    CLEANUP_HINT,
                )
            else:
                self._logger.info("The embedding precision agrees with the indexes again")
        self._verdict = verdict
        await self._write()
        return verdict

    def clear(self) -> None:
        """Forget the cached state before recomputing (after the cleanup dropped the indexes)."""
        self._verdict = Verdict()
        self._forced_until = 0.0

    async def _write(self) -> None:
        if self._publish is None:
            return
        now = self._clock()
        if self._published is not None and self._published[0] == self._verdict.mismatch and now - self._published[1] < self._period:
            return
        try:
            await self._publish(self._verdict)
            self._published = (self._verdict.mismatch, now)
        except Exception:
            self._logger.warning("Could not write the weight-precision verdict", exc_info=True)


@runtime_checkable
class PrecisionHandBack(Protocol):
    def hand_back(self, messages: Sequence[StreamMessage | None]) -> dict[int, float]:
        """Positions of messages to hand back and how long to wait; never raises."""
        ...


class PrecisionFilter:
    def __init__(self, verdict: PrecisionVerdict, logger: Logger, *, rng: random.Random | None = None) -> None:
        self._verdict = verdict
        self._logger = logger
        self._rng = rng or random.Random()

    def hand_back(self, messages: Sequence[StreamMessage | None]) -> dict[int, float]:
        mismatch = self._verdict.mismatch
        out: dict[int, float] = {}
        for position, message in enumerate(messages):
            if message is None or not connector_gated_event(str(message.eventType), {}):
                continue
            if not mismatch or _is_waiting(message):
                continue
            first = int((message.payload or {}).get(HANDBACKS_KEY) or 0) == 0
            base = FIRST_HAND_BACK_SECONDS if first else LATER_HAND_BACK_SECONDS
            out[position] = base * (1 + self._rng.uniform(-_JITTER, _JITTER))
        return out


def _is_waiting(message: StreamMessage) -> bool:
    """Whether the message carries a wait that has not run out: a hand-back already put it behind a delay.

    It is checked again when that delay is over, at dispatch, so reading it now must not send it round again.
    """
    try:
        return float((message.payload or {}).get("_retry_not_before") or 0) > time.time()
    except (TypeError, ValueError):
        return False


def counted(payload: dict[str, Any]) -> dict[str, Any]:
    """The republished payload's extra keys: one more hand-back counted."""
    return {HANDBACKS_KEY: int(payload.get(HANDBACKS_KEY) or 0) + 1}


_active: PrecisionVerdict | None = None


def set_active_verdict(verdict: PrecisionVerdict | None) -> None:
    global _active  # noqa: PLW0603
    _active = verdict


def active_verdict() -> PrecisionVerdict | None:
    """The verdict this process runs, for the one place that must refresh it at once (the cleanup)."""
    return _active
