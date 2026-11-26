"""Request metrics of the Model Gateway and the Tool Gateway (C20.48, OB-04), and the lookups of a run's read cache
(C7.14) beside them: ports, no-op defaults, recorders.

What reaches the port is already a label: a route name from the gateway's own fixed list, an outcome, a refusal
reason code, an effect class, a call disposition. Nothing a caller sent (a path, a tool name, an org or run id, a
prompt) is ever one, so a series cannot carry tenant content and the label space is fixed by this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Protocol, get_args

GatewayName = Literal["model", "tool"]
Outcome = Literal["ok", "refused", "error", "not_found"]

# Each gateway's routes, as the server names them; anything else is "unmatched".
MODEL_ROUTES: tuple[str, ...] = (
    "healthz",
    "metrics",
    "models",
    "chat_completions",
    "responses",
    "responses_stored",
    "messages",
    "count_tokens",
    "gemini",
)
TOOL_ROUTES: tuple[str, ...] = ("mcp",)
UNMATCHED = "unmatched"
OTHER = "other"

# Refusal reason codes are constants in the code (``reason_code``); one that does not look like one, or one past the
# cap, is counted as "other" rather than grow the label space.
_REASON = re.compile(r"[a-z][a-z0-9_]{0,47}")
MAX_REASONS = 64
# What a tool call's effect can be, plus the gateway's own tools and calls no grant covers.
TOOL_FAMILIES: frozenset[str] = frozenset(
    {"read", "workspace", "write", "execute", "gateway", "disclosure", "ungranted"}
)
DISPOSITIONS: frozenset[str] = frozenset(
    {"executed", "recorded", "denied", "deferred", "asked", "unknown", "in_flight", "refused"}
)


def route_label(gateway: GatewayName, route: str | None) -> str:
    known = MODEL_ROUTES if gateway == "model" else TOOL_ROUTES
    return route if route in known else UNMATCHED


@dataclass(frozen=True, slots=True)
class GatewayRequest:
    """One HTTP request a gateway answered: ``seconds`` until its last byte (a stream's end)."""

    gateway: GatewayName
    route: str
    outcome: Outcome
    seconds: float
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ToolCallObserved:
    """One ``tools/call`` the Tool Gateway answered, by the call's effect class and how it ended."""

    family: str
    disposition: str
    seconds: float


def outcome_of(status: int, *, refused: bool) -> Outcome:
    if refused:
        return "refused"
    if status == 404:
        return "not_found"
    return "ok" if status < 400 else "error"


class GatewayMetrics(Protocol):
    def request(self, observed: GatewayRequest) -> None: ...

    def tool_call(self, observed: ToolCallObserved) -> None: ...


@dataclass
class ReasonLabels:
    """Turns refusal reason codes into labels: a code-shaped value up to ``MAX_REASONS`` distinct, else "other"."""

    seen: set[str] = field(default_factory=set[str])

    def label(self, reason: str | None) -> str:
        if reason is None:
            return "none"
        if reason in self.seen:
            return reason
        if _REASON.fullmatch(reason) is None or len(self.seen) >= MAX_REASONS:
            return OTHER
        self.seen.add(reason)
        return reason


def bounded(observed: GatewayRequest, reasons: ReasonLabels) -> GatewayRequest:
    """``observed`` with every label inside this module's fixed space."""
    outcome = observed.outcome if observed.outcome in get_args(Outcome) else "error"
    return GatewayRequest(
        gateway=observed.gateway,
        route=route_label(observed.gateway, observed.route),
        outcome=outcome,
        seconds=max(observed.seconds, 0.0),
        reason=reasons.label(observed.reason) if outcome == "refused" else None,
    )


def bounded_call(observed: ToolCallObserved) -> ToolCallObserved:
    return ToolCallObserved(
        family=observed.family if observed.family in TOOL_FAMILIES else OTHER,
        disposition=observed.disposition if observed.disposition in DISPOSITIONS else OTHER,
        seconds=max(observed.seconds, 0.0),
    )


ReadCacheExecutor = Literal["loop", "engine"]
ReadCacheOutcome = Literal["hit", "miss", "expired"]


@dataclass(frozen=True, slots=True)
class ReadCacheObserved:
    """One lookup in a run's read cache (C7.14): which executor's cache, and whether the first answer served it."""

    executor: ReadCacheExecutor
    outcome: ReadCacheOutcome


class ReadCacheMetrics(Protocol):
    def lookup(self, observed: ReadCacheObserved) -> None: ...


def bounded_lookup(observed: ReadCacheObserved) -> ReadCacheObserved:
    executor = observed.executor if observed.executor in get_args(ReadCacheExecutor) else "loop"
    outcome = observed.outcome if observed.outcome in get_args(ReadCacheOutcome) else "miss"
    return ReadCacheObserved(executor, outcome)


class NoopReadCacheMetrics:
    def lookup(self, observed: ReadCacheObserved) -> None:
        pass


NOOP_READ_CACHE_METRICS = NoopReadCacheMetrics()


@dataclass
class RecordingReadCacheMetrics:
    lookups: list[ReadCacheObserved] = field(default_factory=list[ReadCacheObserved])

    def lookup(self, observed: ReadCacheObserved) -> None:
        self.lookups.append(bounded_lookup(observed))


class NoopGatewayMetrics:
    def request(self, observed: GatewayRequest) -> None:
        pass

    def tool_call(self, observed: ToolCallObserved) -> None:
        pass


NOOP_GATEWAY_METRICS = NoopGatewayMetrics()


@dataclass
class RecordingGatewayMetrics:
    """In memory, for tests and embedders: what the gateways observed, bounded as an exporter would see it."""

    requests: list[GatewayRequest] = field(default_factory=list[GatewayRequest])
    calls: list[ToolCallObserved] = field(default_factory=list[ToolCallObserved])
    reasons: ReasonLabels = field(default_factory=ReasonLabels)

    def request(self, observed: GatewayRequest) -> None:
        self.requests.append(bounded(observed, self.reasons))

    def tool_call(self, observed: ToolCallObserved) -> None:
        self.calls.append(bounded_call(observed))


__all__ = [
    "DISPOSITIONS",
    "MAX_REASONS",
    "MODEL_ROUTES",
    "NOOP_GATEWAY_METRICS",
    "NOOP_READ_CACHE_METRICS",
    "OTHER",
    "TOOL_FAMILIES",
    "TOOL_ROUTES",
    "UNMATCHED",
    "GatewayMetrics",
    "GatewayName",
    "GatewayRequest",
    "NoopGatewayMetrics",
    "NoopReadCacheMetrics",
    "Outcome",
    "ReadCacheExecutor",
    "ReadCacheMetrics",
    "ReadCacheObserved",
    "ReadCacheOutcome",
    "ReasonLabels",
    "RecordingGatewayMetrics",
    "RecordingReadCacheMetrics",
    "ToolCallObserved",
    "bounded",
    "bounded_call",
    "bounded_lookup",
    "outcome_of",
    "route_label",
]
