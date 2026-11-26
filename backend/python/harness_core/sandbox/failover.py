"""Sandbox provider retries and failover (CH-16).

A provider that is slow or erroring on create is retried with backoff, and when a deployment configures more than
one provider (another broker: another Docker host today, a Kubernetes or SaaS provider later), a create the first
cannot serve goes to the next. ``acquire`` is idempotent per key on each provider, so a retry never makes a second
sandbox there. A create whose answer was lost on one provider and then served by the next can leave one sandbox
behind on the first; it carries the run's labels, so the reap of the run's next attempt (or the sweeper's) removes it.

Only an outage moves on (``BrokerUnavailable``, or ``IsolationUnavailable`` when another provider may offer the mode):
a refusal (an image that is not allowed, a fenced lease, a bad spec) is the same on every provider and is raised at
once. When every provider stays away the last outage is raised, and the engine run waits for a sandbox as before
(``sandbox_wait``, its queue visible in the chat).
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_core.logs import HarnessLog
from harness_core.sandbox.errors import (
    BrokerUnavailable,
    IsolationUnavailable,
    SandboxNotFound,
)
from harness_core.sandbox.ports import close_broker
from harness_core.sandbox.types import BrokerHealth

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from harness_core.sandbox.ports import SandboxBroker, SandboxHandle
    from harness_core.sandbox.types import LeaseRef, RuntimeMode, SandboxSpec

# Seconds between create attempts on one provider; its length is the retries before the next provider is asked.
CREATE_BACKOFF_S: tuple[float, ...] = (0.5, 2.0)

_LOG = HarnessLog.named("harness_core.sandbox.failover")


@dataclass(frozen=True, slots=True)
class SandboxProvider:
    """One provider in the deployment's order: a name for logs and the broker that reaches it."""

    name: str
    broker: SandboxBroker


class FailoverSandboxBroker:
    """A ``SandboxBroker`` over an ordered list of providers, the first preferred."""

    def __init__(
        self,
        providers: Sequence[SandboxProvider],
        *,
        backoff_s: Sequence[float] = CREATE_BACKOFF_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not providers:
            raise ValueError("a failover broker needs at least one provider")
        self._providers = tuple(providers)
        self._backoff = tuple(backoff_s)
        self._sleep = sleep
        # Which provider made each sandbox this process holds: its release goes back there.
        self._owners: dict[str, SandboxProvider] = {}

    def modes(self) -> frozenset[RuntimeMode]:
        modes: frozenset[RuntimeMode] = frozenset()
        for provider in self._providers:
            modes |= provider.broker.modes()
        return modes

    async def acquire(self, spec: SandboxSpec, *, lease: LeaseRef, idempotency_key: str) -> SandboxHandle:
        last: Exception | None = None
        for index, provider in enumerate(self._providers):
            if spec.mode not in provider.broker.modes():
                continue
            try:
                handle = await self._acquire_on(provider, spec, lease, idempotency_key)
            except (BrokerUnavailable, IsolationUnavailable) as exc:
                last = exc
                following = next((p.name for p in self._providers[index + 1 :] if spec.mode in p.broker.modes()), None)
                _LOG.warning(
                    "sandbox.provider_unavailable",
                    provider=provider.name,
                    errorType=type(exc).__name__,
                    next=following,
                )
                continue
            self._owners[handle.id] = provider
            if index > 0:
                _LOG.warning("sandbox.provider_failover", provider=provider.name, sandboxId=handle.id)
            return handle
        if last is not None:
            raise last
        raise IsolationUnavailable(f"no sandbox provider offers {spec.mode}", reason="mode_unavailable")

    async def _acquire_on(
        self, provider: SandboxProvider, spec: SandboxSpec, lease: LeaseRef, key: str
    ) -> SandboxHandle:
        for attempt in range(len(self._backoff) + 1):
            try:
                return await provider.broker.acquire(spec, lease=lease, idempotency_key=key)
            except BrokerUnavailable as exc:
                if attempt >= len(self._backoff):
                    raise
                delay = self._backoff[attempt]
                _LOG.info(
                    "sandbox.create_retry", provider=provider.name, retry=attempt + 1, delayS=delay,
                    errorType=type(exc).__name__,
                )
                await self._sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    async def aclose(self) -> None:
        """Close every provider's client, the last opened first; one that fails to close does not keep the rest
        open."""
        async with contextlib.AsyncExitStack() as stack:
            for provider in self._providers:
                _ = stack.push_async_callback(close_broker, provider.broker)

    async def release(self, handle: SandboxHandle, *, keep_warm_s: int = 0) -> None:
        owner = self._owners.pop(handle.id, None)
        if owner is not None:
            await owner.broker.release(handle, keep_warm_s=keep_warm_s)
            return
        # Not made by this process (a sandbox kept across workers): the provider that knows it releases it.
        for provider in self._providers:
            try:
                await provider.broker.release(handle, keep_warm_s=keep_warm_s)
                return
            except SandboxNotFound:
                continue
        raise SandboxNotFound(f"no provider holds sandbox {handle.id}")

    async def reap(self, *, labels: Mapping[str, str], older_than_s: int) -> int:
        reaped, reached, last = 0, 0, None
        for provider in self._providers:
            try:
                reaped += await provider.broker.reap(labels=labels, older_than_s=older_than_s)
                reached += 1
            except BrokerUnavailable as exc:
                last = exc
                _LOG.warning("sandbox.reap_unavailable", provider=provider.name, errorType=type(exc).__name__)
        if reached == 0 and last is not None:
            raise last
        return reaped

    async def health(self) -> BrokerHealth:
        """Healthy when any provider is; the modes and the active count of every provider that answers."""
        answers: list[BrokerHealth] = []
        for provider in self._providers:
            try:
                answers.append(await provider.broker.health())
            except BrokerUnavailable as exc:
                _LOG.warning("sandbox.health_unavailable", provider=provider.name, errorType=type(exc).__name__)
        if not answers:
            raise BrokerUnavailable("no sandbox provider answered")
        modes: frozenset[RuntimeMode] = frozenset()
        for answer in answers:
            modes |= answer.modes
        return BrokerHealth(
            ok=any(a.ok for a in answers),
            modes=modes,
            active=sum(a.active for a in answers),
            detail=answers[0].detail,
        )


__all__ = ["CREATE_BACKOFF_S", "FailoverSandboxBroker", "SandboxProvider"]
