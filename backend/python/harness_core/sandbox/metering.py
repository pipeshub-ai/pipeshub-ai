"""Sandbox-seconds per lease (BU-04, C9.07) and the broker's own metrics port.

A broker meters each sandbox from the moment it is bound to a run (an acquire returns it) to the moment the run lets
it go: a release, or the broker ending it (a reap, a sweep, an erase). A sandbox released with ``keep_warm_s`` (a kept
sign-in, SIGNIN-5) is no run's while it waits: that time is metered as ``kept``, never as ``active``, and a turn that
takes it back starts a new ``active`` interval. Times come from a monotonic clock, so a wall-clock step never bills a
second twice or loses one.

Each closed interval is logged (``sandbox.metered``, with the run's correlation labels: who to bill) and counted by
mode and phase on the metrics port; no org or run is ever a metric label.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

from harness_core.logs import HarnessLog
from harness_core.sandbox.types import RuntimeMode

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from harness_core.sandbox.types import SandboxSpec

Phase = Literal["active", "kept"]
WarmClaim = Literal["warm", "cold", "org_capped"]
# The spec labels a metered line carries: the correlation ids the harness sets, never arbitrary caller labels.
BILLING_LABELS = ("orgId", "sessionId", "runId", "attemptId")
_MODES = frozenset(mode.value for mode in RuntimeMode)


def metric_mode(mode: str) -> str:
    """A runtime mode as a metric label: one the harness ships, else ``other``."""
    return mode if mode in _MODES else "other"


@dataclass(frozen=True, slots=True)
class MeteredInterval:
    sandbox_id: str
    phase: Phase
    mode: str
    seconds: float
    labels: Mapping[str, str] = field(default_factory=lambda: dict[str, str]())


class BrokerMetrics(Protocol):
    def metered(self, interval: MeteredInterval) -> None: ...

    def warm_claim(self, outcome: WarmClaim) -> None:
        """An acquire of a shape the pool serves: taken warm, gone cold, or sent cold for its org's cap."""
        ...

    def pool_depth(self, depth: int) -> None:
        """Unclaimed warm sandboxes this process knows, after any change."""
        ...


class NoopBrokerMetrics:
    def metered(self, interval: MeteredInterval) -> None:
        pass

    def warm_claim(self, outcome: WarmClaim) -> None:
        pass

    def pool_depth(self, depth: int) -> None:
        pass


NOOP_BROKER_METRICS = NoopBrokerMetrics()


@dataclass
class RecordingBrokerMetrics:
    """In memory, for tests and embedders."""

    intervals: list[MeteredInterval] = field(default_factory=list[MeteredInterval])
    claims: list[WarmClaim] = field(default_factory=list["WarmClaim"])
    depths: list[int] = field(default_factory=list[int])

    def metered(self, interval: MeteredInterval) -> None:
        self.intervals.append(dataclasses.replace(interval, mode=metric_mode(interval.mode)))

    def labels(self) -> set[str]:
        """Every metric label value recorded (mode, phase, claim outcome), as an exporter would label them."""
        return {i.mode for i in self.intervals} | {i.phase for i in self.intervals} | set(self.claims)

    def warm_claim(self, outcome: WarmClaim) -> None:
        self.claims.append(outcome)

    def pool_depth(self, depth: int) -> None:
        self.depths.append(depth)

    def seconds(self, phase: Phase, sandbox_id: str | None = None) -> float:
        return sum(i.seconds for i in self.intervals if i.phase == phase and sandbox_id in (None, i.sandbox_id))


@dataclass(slots=True)
class _Open:
    phase: Phase
    since: float
    mode: str
    labels: dict[str, str]


class SandboxMeter:
    """One open interval per sandbox; every transition closes the one before it."""

    def __init__(
        self,
        metrics: BrokerMetrics = NOOP_BROKER_METRICS,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        log: HarnessLog | None = None,
    ) -> None:
        self._metrics = metrics
        self._now = monotonic
        self._log = log or HarnessLog.named("harness_core.sandbox.metering")
        self._open: dict[str, _Open] = {}

    def bound(self, sandbox_id: str, spec: SandboxSpec) -> None:
        """An acquire handed ``sandbox_id`` to a run: active from now. A repeat acquire of a held sandbox (the same
        key twice) changes nothing; one of a kept sandbox closes its kept time."""
        current = self._open.get(sandbox_id)
        if current is not None and current.phase == "active":
            return
        self._close(sandbox_id)
        labels = {k: spec.labels[k] for k in BILLING_LABELS if spec.labels.get(k)}
        self._open[sandbox_id] = _Open("active", self._now(), spec.mode.value, labels)

    def kept(self, sandbox_id: str) -> None:
        """Released with keep-warm: the run's time ends here; the wait is metered as kept."""
        current = self._open.get(sandbox_id)
        if current is None or current.phase == "kept":
            return
        _ = self._close(sandbox_id)
        self._open[sandbox_id] = _Open("kept", self._now(), current.mode, current.labels)

    def ended(self, sandbox_id: str) -> MeteredInterval | None:
        """Released, reaped, swept or erased: whatever interval is open closes."""
        return self._close(sandbox_id)

    def open_phase(self, sandbox_id: str) -> Phase | None:
        current = self._open.get(sandbox_id)
        return current.phase if current is not None else None

    def _close(self, sandbox_id: str) -> MeteredInterval | None:
        current = self._open.pop(sandbox_id, None)
        if current is None:
            return None
        interval = MeteredInterval(
            sandbox_id, current.phase, current.mode, max(0.0, self._now() - current.since), current.labels
        )
        self._metrics.metered(interval)
        self._log.info(
            "sandbox.metered",
            sandboxId=sandbox_id,
            phase=interval.phase,
            mode=interval.mode,
            seconds=round(interval.seconds, 3),
            **interval.labels,
        )
        return interval


__all__ = [
    "BILLING_LABELS",
    "NOOP_BROKER_METRICS",
    "BrokerMetrics",
    "MeteredInterval",
    "NoopBrokerMetrics",
    "Phase",
    "RecordingBrokerMetrics",
    "SandboxMeter",
    "WarmClaim",
    "metric_mode",
]
