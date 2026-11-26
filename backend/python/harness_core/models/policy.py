"""``ModelPolicy`` (P4.7): which pinned route a run's model calls fall back to, and when.

Built from the run's pinned ``RunSpec.model`` only, so a fallback can never reach a
route admission did not pin, and a child's policy is its own spec's routes (A1:
those it was granted of its parent's, in the parent's order). Route 0 is the
primary; a later route is a target only for the failures its ``on`` names. A
budget or credential refusal is never a trigger: those end the run, they are not
a reason to spend on another model (refuse, never downgrade).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, get_args

import httpx

from harness_core.refusals import HarnessRefusal

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.access.types import PinnedRoute
    from harness_core.contracts.generated.run_spec import ModelPolicy as SpecModelPolicy

FallbackTrigger = Literal["5xx", "timeout", "content_filter", "429_exhausted"]
TRIGGERS: frozenset[str] = frozenset(get_args(FallbackTrigger))
# Transient on the same route: retried there first. A content filter repeats its refusal;
# a rate limit has its own backoff.
RETRIED: frozenset[FallbackTrigger] = frozenset({"5xx", "timeout"})

DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_DELAY_S = 1.0

# ``ModelFailed.error_type`` / a test model's error ``kind`` -> trigger.
_KINDS: dict[str, FallbackTrigger] = {
    "server_error": "5xx",
    "timeout": "timeout",
    "content_filter": "content_filter",
}
_FILTER_HINTS = ("content_filter", "content management policy", "content_policy", "responsibleaipolicyviolation")


@dataclass(frozen=True)
class PolicyRoute:
    index: int
    provider: str
    model: str
    # ``spec_route_id``: the id a gateway token and a child grant name it by.
    route_id: str
    on: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ModelPolicyConfig:
    """What a host tunes; the routes come from the pinned spec, never from here."""

    max_retries: int = DEFAULT_MAX_RETRIES
    retry_delay_s: float = DEFAULT_RETRY_DELAY_S

    def delay_s(self, failures: int) -> float:
        """The wait before the retry after ``failures`` consecutive transient failures."""
        return self.retry_delay_s * (2 ** max(0, failures - 1))


@dataclass(frozen=True)
class ModelPolicy:
    routes: tuple[PolicyRoute, ...]
    config: ModelPolicyConfig = ModelPolicyConfig()

    @classmethod
    def from_spec(cls, model: SpecModelPolicy, config: ModelPolicyConfig | None = None) -> ModelPolicy:
        from harness_core.access.types import spec_route_id

        routes = tuple(
            PolicyRoute(
                index=i,
                provider=route.provider,
                model=route.model,
                route_id=spec_route_id(route),
                on=frozenset(route.on or ()) if i > 0 else frozenset(),
            )
            for i, route in enumerate(model.policy)
        )
        return cls(routes, config or ModelPolicyConfig())

    @classmethod
    def from_pinned(cls, routes: Sequence[PinnedRoute], config: ModelPolicyConfig | None = None) -> ModelPolicy:
        """The same policy over a run's model grant (WP-100): the routes and triggers Node read from the pinned spec,
        so the Model Gateway moves an engine's call exactly where the kernel would move a loop run's."""
        return cls(
            tuple(
                PolicyRoute(
                    index=i,
                    provider=route.provider,
                    model=route.model,
                    route_id=route.route_id,
                    on=route.on if i > 0 else frozenset(),
                )
                for i, route in enumerate(routes)
            ),
            config or ModelPolicyConfig(),
        )

    @property
    def primary(self) -> PolicyRoute:
        return self.routes[0]

    def next_route(self, current: PolicyRoute, trigger: FallbackTrigger) -> PolicyRoute | None:
        """The first route after ``current`` that takes ``trigger``."""
        return next((r for r in self.routes[current.index + 1 :] if trigger in r.on), None)

    def routed_to(self, provider: str, model: str) -> PolicyRoute:
        """The route a journaled ``model.fallback`` moved to; the primary when the pin has no such route."""
        return next(
            (r for r in self.routes[1:] if (r.provider, r.model) == (provider, model)),
            self.primary,
        )

    def pinned(self, route: PolicyRoute) -> bool:
        return 0 <= route.index < len(self.routes) and self.routes[route.index] == route

    def retry_delay_s(self, failures: int) -> float:
        return self.config.delay_s(failures)


def _status(exc: BaseException) -> int | None:
    for name in ("status_code", "status"):
        value = getattr(exc, name, None)
        if isinstance(value, int):
            return value
    return None


# A provider that cannot be reached is down, as a 5xx says (a gateway's call fails this way, MGW-20).
_UNREACHABLE: tuple[type[BaseException], ...] = (ConnectionError, httpx.NetworkError, httpx.RemoteProtocolError)


def _one(exc: BaseException) -> FallbackTrigger | None:
    for name in ("error_type", "kind", "code"):
        value = getattr(exc, name, None)
        if isinstance(value, str) and value in _KINDS:
            return _KINDS[value]
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or "timeout" in type(exc).__name__.lower():
        return "timeout"
    status = _status(exc)
    if status == 408:
        return "timeout"
    if status is not None and 500 <= status <= 599:
        return "5xx"
    if isinstance(exc, _UNREACHABLE):
        return "5xx"
    text = str(exc).lower()
    if any(hint in text for hint in _FILTER_HINTS):
        return "content_filter"
    return None


def classify_failure(exc: BaseException) -> FallbackTrigger | None:
    """The fallback trigger a failed model call is, looking through its causes; ``None`` when
    another route would not help (a refusal, a malformed request, an unknown failure)."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, HarnessRefusal):
            return None
        trigger = _one(current)
        if trigger is not None:
            return trigger
        current = current.__cause__
    return None


__all__ = [
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_RETRY_DELAY_S",
    "RETRIED",
    "TRIGGERS",
    "FallbackTrigger",
    "ModelPolicy",
    "ModelPolicyConfig",
    "PolicyRoute",
    "classify_failure",
]
