"""Reconciling an effect whose outcome is unknown (P2.9, R-31).

A non-idempotent write whose ``tool.intent`` is journaled and whose outcome is
not (a crash, a lost acknowledgement) may or may not have happened. A
``Reconciler`` decides it by reading the provider, never by writing to it:

1. Not before ``dispatched_at + tool_timeout + lease``: by then the attempt
   that sent the request has fenced itself, so nothing of it is still in flight.
2. A read-after-write lookup by a client-generated tag decides on one answer.
   A lookup that is only a search (eventually consistent) needs two negatives
   ``index_lag_s`` apart; anything less is ``still_unknown`` and goes to a person.
3. Lookups are bounded in number and time; a failed lookup is never a negative.

Product-neutral: ids, time and sleeping are injected; the hooks that know a
provider are adapters (``ReconcileLookup``).
"""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Final, Literal, Protocol

from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator, Mapping
    from datetime import datetime

    from pydantic import JsonValue

    from harness_core.clock import Clock

ReconcileAnswer = Literal["applied", "not_applied", "still_unknown"]
ReconcileReason = Literal[
    "found",
    "absent",
    "window_beyond_hold",
    "lookup_failed",
    "no_hook",
    "undecidable",
    "write_refused",
    "recall_spent",
]

EFFECT_TAG_PREFIX: Final = "pxh-e-"
MAX_WAIT_S: Final = 300.0
MAX_LOOKUPS: Final = 3
RETRY_S: Final = 5.0
LOOKUP_TIMEOUT_S: Final = 30.0


def effect_tag(effect_key: str) -> str:
    """What a write carries so a later read can find it; no part of the key leaves in clear."""
    return EFFECT_TAG_PREFIX + hashlib.sha256(effect_key.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class UnknownEffect:
    """One call whose effect is unknown. ``dispatched_at`` is its intent's journal time."""

    run_id: str
    invocation_id: str
    tool_id: str
    tool_name: str
    arguments: Mapping[str, JsonValue]
    effect_key: str
    dispatched_at: datetime
    lease_s: float

    @property
    def tag(self) -> str:
        return effect_tag(self.effect_key)


@dataclass(frozen=True)
class ReconcileEvidence:
    """What the outcome of an ``applied`` effect journals: what the provider holds."""

    content: JsonValue
    receipt: Mapping[str, JsonValue] = field(default_factory=dict[str, "JsonValue"])


@dataclass(frozen=True)
class ReconcileVerdict:
    answer: ReconcileAnswer
    reason: ReconcileReason
    evidence: ReconcileEvidence | None = None
    lookups: int = 0


@dataclass(frozen=True)
class ReconcileTiming:
    """A hook's provider: how long the write may take, and how its read sees it."""

    tool_timeout_s: float
    read_after_write: bool
    index_lag_s: float = 0.0


@dataclass(frozen=True)
class LookupFound:
    evidence: ReconcileEvidence


@dataclass(frozen=True)
class LookupAbsent:
    pass


@dataclass(frozen=True)
class LookupUndecidable:
    """The read cannot decide this call (it would see only part of its effect)."""

    detail: str


type LookupResult = LookupFound | LookupAbsent | LookupUndecidable


class ReconcileLookup(Protocol):
    """A connector's read for one tool: finds the effect a call's tag marks. Never writes."""

    @property
    def timing(self) -> ReconcileTiming: ...

    async def lookup(self, effect: UnknownEffect) -> LookupResult:
        """Raises when the provider could not be read (not a negative)."""
        ...


class Reconciler(Protocol):
    def covers(self, tool_id: str) -> bool:
        """Whether a hook was opted in for the tool."""
        ...

    async def reconcile(self, effect: UnknownEffect) -> ReconcileVerdict: ...


class ReconcileWriteRefused(Exception):
    """A write attempted while reconciling: finding out never writes."""


_reconciling: ContextVar[bool] = ContextVar("harness_reconciling", default=False)
_stamp: ContextVar[str | None] = ContextVar("harness_effect_stamp", default=None)


@contextmanager
def reconciling() -> Iterator[None]:
    token = _reconciling.set(True)
    try:
        yield
    finally:
        _reconciling.reset(token)


def refuse_write_while_reconciling(operation: str) -> None:
    """Called by a client layer (and every fake) before it writes."""
    if _reconciling.get():
        raise ReconcileWriteRefused(operation)


@contextmanager
def stamped_effect(effect_key: str) -> Iterator[None]:
    """Around one call of a tool a hook covers: the tool tags its write with ``current_effect_tag``."""
    token = _stamp.set(effect_tag(effect_key))
    try:
        yield
    finally:
        _stamp.reset(token)


def current_effect_tag() -> str | None:
    """The tag the running call's write carries; ``None`` outside a stamped call (unchanged requests)."""
    return _stamp.get()


@dataclass
class TimedReconciler:
    """``Reconciler`` over per-tool hooks, applying R-31's timing and read rules."""

    hooks: Mapping[str, ReconcileLookup]
    clock: Clock
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    max_wait_s: float = MAX_WAIT_S
    max_lookups: int = MAX_LOOKUPS
    retry_s: float = RETRY_S
    lookup_timeout_s: float = LOOKUP_TIMEOUT_S
    log: HarnessLog = field(default_factory=lambda: HarnessLog.named("harness_core.effects"))

    def covers(self, tool_id: str) -> bool:
        return tool_id in self.hooks

    async def reconcile(self, effect: UnknownEffect) -> ReconcileVerdict:
        hook = self.hooks.get(effect.tool_id)
        if hook is None:
            return ReconcileVerdict("still_unknown", "no_hook")
        timing = hook.timing
        ready = effect.dispatched_at + timedelta(seconds=timing.tool_timeout_s + effect.lease_s)
        wait = (ready - self.clock.now()).total_seconds()
        if wait > self.max_wait_s:
            return ReconcileVerdict("still_unknown", "window_beyond_hold")
        if wait > 0:
            await self.sleep(wait)
        lookups = 0
        negatives = 0
        failures = 0
        while failures < self.max_lookups:
            lookups += 1
            try:
                with reconciling():
                    result = await asyncio.wait_for(hook.lookup(effect), self.lookup_timeout_s)
            except ReconcileWriteRefused:
                self._warn(effect, "write_refused", lookups)
                return ReconcileVerdict("still_unknown", "write_refused", lookups=lookups)
            except Exception as exc:
                failures += 1
                self.log.warning(
                    "reconcile.lookup_failed",
                    invocationId=effect.invocation_id,
                    toolId=effect.tool_id,
                    errorType=type(exc).__name__,
                    failures=failures,
                )
                if failures < self.max_lookups:
                    await self.sleep(self.retry_s)
                continue
            match result:
                case LookupFound(evidence=evidence):
                    return ReconcileVerdict("applied", "found", evidence, lookups)
                case LookupUndecidable():
                    return ReconcileVerdict("still_unknown", "undecidable", lookups=lookups)
                case LookupAbsent():
                    negatives += 1
                    if timing.read_after_write or negatives >= 2:
                        return ReconcileVerdict("not_applied", "absent", lookups=lookups)
                    await self.sleep(timing.index_lag_s)
        self._warn(effect, "lookup_failed", lookups)
        return ReconcileVerdict("still_unknown", "lookup_failed", lookups=lookups)

    def _warn(self, effect: UnknownEffect, reason: str, lookups: int) -> None:
        self.log.warning(
            "reconcile.undecided",
            invocationId=effect.invocation_id,
            toolId=effect.tool_id,
            reason=reason,
            lookups=lookups,
        )


__all__ = [
    "EFFECT_TAG_PREFIX",
    "LOOKUP_TIMEOUT_S",
    "MAX_LOOKUPS",
    "MAX_WAIT_S",
    "RETRY_S",
    "LookupAbsent",
    "LookupFound",
    "LookupResult",
    "LookupUndecidable",
    "ReconcileAnswer",
    "ReconcileEvidence",
    "ReconcileLookup",
    "ReconcileReason",
    "ReconcileTiming",
    "ReconcileVerdict",
    "ReconcileWriteRefused",
    "Reconciler",
    "TimedReconciler",
    "UnknownEffect",
    "current_effect_tag",
    "effect_tag",
    "reconciling",
    "refuse_write_while_reconciling",
    "stamped_effect",
]
