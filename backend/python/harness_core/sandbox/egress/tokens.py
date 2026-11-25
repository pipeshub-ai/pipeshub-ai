"""Run-bound tokens: an in-memory source, and the proxy's refreshing cache."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from datetime import timedelta
from typing import TYPE_CHECKING

from harness_core.sandbox.egress.errors import TokenUnavailable
from harness_core.sandbox.egress.types import IssuedToken

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Collection

    from harness_core.clock import Clock
    from harness_core.sandbox.egress.ports import TokenSource
    from harness_core.sandbox.egress.types import EgressBinding
    from harness_core.sandbox.types import TokenRoute

# ARCHITECTURE §4.8: gateway tokens live ten minutes; the proxy refreshes its copy before then.
DEFAULT_TOKEN_TTL_S = 600
DEFAULT_REFRESH_BEFORE_S = 60
TOKEN_PREFIX = "pxt_"


class InMemoryTokenSource:
    """HMAC-derived tokens per (sandbox, credential ref, generation); the stand-in until E2's Node authority.

    Deterministic from the secret, so a test that holds the secret can tell
    which sandbox's token reached a gateway.
    """

    def __init__(
        self,
        secret: bytes,
        clock: Clock,
        *,
        ttl_s: int = DEFAULT_TOKEN_TTL_S,
        credential_refs: Collection[str] | None = None,
    ) -> None:
        if not secret:
            raise ValueError("secret is empty")
        self._secret = secret
        self._clock = clock
        self._ttl_s = ttl_s
        self._refs = frozenset(credential_refs) if credential_refs is not None else None
        self._generations: dict[tuple[str, str], int] = {}
        self.issued = 0

    @staticmethod
    def derive(secret: bytes, sandbox_id: str, credential_ref: str, generation: int) -> str:
        message = f"{sandbox_id}\x00{credential_ref}\x00{generation}".encode()
        return TOKEN_PREFIX + hmac.new(secret, message, hashlib.sha256).hexdigest()[:48]

    async def issue(self, route: TokenRoute, binding: EgressBinding) -> IssuedToken:
        if self._refs is not None and route.credential_ref not in self._refs:
            raise TokenUnavailable("unknown credential ref")
        key = (binding.sandbox_id, route.credential_ref)
        generation = self._generations.get(key, 0)
        self._generations[key] = generation + 1
        self.issued += 1
        value = self.derive(self._secret, binding.sandbox_id, route.credential_ref, generation)
        return IssuedToken(value=value, expires_at=self._clock.now() + timedelta(seconds=self._ttl_s))


class TokenCache:
    """The proxy's copy of each binding's tokens: refreshed before expiry, dropped with the binding."""

    def __init__(
        self,
        source: TokenSource,
        clock: Clock,
        *,
        refresh_before_s: int = DEFAULT_REFRESH_BEFORE_S,
        on_issued: Callable[[str, str, IssuedToken], Awaitable[None]] | None = None,
    ) -> None:
        self._source = source
        self._on_issued = on_issued
        self._clock = clock
        self._refresh_before = timedelta(seconds=refresh_before_s)
        self._current: dict[tuple[str, str], IssuedToken] = {}
        # Every token handed out and not yet expired, for replay detection across sandboxes.
        self._live: dict[str, tuple[str, IssuedToken]] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        # The last token of each binding that went, until it expires: what the run's last reports are sent with.
        self._retired: dict[tuple[str, str], IssuedToken] = {}

    async def token(self, route: TokenRoute, binding: EgressBinding) -> str:
        key = (binding.sandbox_id, route.credential_ref)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            current = self._current.get(key)
            if current is not None and current.expires_at - self._clock.now() > self._refresh_before:
                return current.value
            try:
                issued = await self._source.issue(route, binding)
            except TokenUnavailable:
                raise
            except Exception as exc:
                raise TokenUnavailable(f"token source failed: {type(exc).__name__}") from exc
            self.restore(binding.sandbox_id, route.credential_ref, issued)
            if self._on_issued is not None:
                await self._on_issued(binding.sandbox_id, route.credential_ref, issued)
            return issued.value

    def restore(self, sandbox_id: str, credential_ref: str, issued: IssuedToken) -> None:
        """Hold a token obtained earlier (by this process, or before a restart) as the current one."""
        self._current[(sandbox_id, credential_ref)] = issued
        self._live[issued.value] = (sandbox_id, issued)

    def forget_current(self, sandbox_id: str) -> None:
        """The sandbox was bound again for another attempt: its next request gets a token of its own. Tokens
        already handed out stay known for replay detection until they expire."""
        for key in [k for k in self._current if k[0] == sandbox_id]:
            del self._current[key]

    async def reporting_token(self, route: TokenRoute, binding: EgressBinding) -> str:
        """The token a report of the binding's run goes with: the current one, or once the binding has gone, its
        last one while it is valid. Node issues none for an attempt that no longer holds its run, so a report of the
        run's last seconds (an alert raised as its sandbox is released) is sent with the token it already had."""
        retired = self._retired.get((binding.sandbox_id, route.credential_ref))
        if retired is not None and (binding.sandbox_id, route.credential_ref) not in self._current:
            if retired.expires_at > self._clock.now():
                return retired.value
            del self._retired[(binding.sandbox_id, route.credential_ref)]
        return await self.token(route, binding)

    def drop(self, sandbox_id: str) -> None:
        now = self._clock.now()
        for key in [k for k, t in self._retired.items() if t.expires_at <= now]:
            del self._retired[key]
        for key in [k for k in self._current if k[0] == sandbox_id]:
            self._retired[key] = self._current[key]
            del self._current[key]
            _ = self._locks.pop(key, None)
        for value in [v for v, (owner, _) in self._live.items() if owner == sandbox_id]:
            del self._live[value]

    def live(self) -> frozenset[str]:
        now = self._clock.now()
        for value in [v for v, (_, t) in self._live.items() if t.expires_at <= now]:
            del self._live[value]
        return frozenset(self._live)


__all__ = ["DEFAULT_REFRESH_BEFORE_S", "DEFAULT_TOKEN_TTL_S", "TOKEN_PREFIX", "InMemoryTokenSource", "TokenCache"]
