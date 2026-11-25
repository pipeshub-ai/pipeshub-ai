"""Pooled MCP sessions: one per (tenant, server, credential owner), kept open across requests.

A session is shared by concurrent leases (MCP multiplexes requests over one session).
The ``variant`` names what the session was opened with (the server's configuration and
a fingerprint of its credential): a lease with another variant opens a new session and
retires the old one, which closes when its last lease ends. Idle and over-age sessions
with no lease are closed by ``sweep()``. A session is never handed to another owner's
request, even if two keys were ever to collide.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, TypeVar

from harness_core.logs import HarnessLog
from harness_core.mcp_hub.errors import SessionOwnerMismatch
from harness_core.mcp_hub.types import PoolConfig, PooledSessionInfo

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from harness_core.clock import Clock
    from harness_core.mcp_hub.types import SessionKey

S = TypeVar("S")
_LOG_NAME = "harness_core.mcp_hub"


@dataclass
class _Entry(Generic[S]):
    key: SessionKey
    variant: str
    session: S
    opened_at: float
    last_used: float
    leases: int = 0
    retired: bool = False
    closed: bool = False


class SessionLease(Generic[S]):
    """One use of a pooled session; ``release`` when done, ``discard`` when the session broke."""

    def __init__(self, entry: _Entry[S], give_back: Callable[[_Entry[S], bool], Awaitable[None]]) -> None:
        self._entry = entry
        self._give_back = give_back
        self._done = False

    @property
    def session(self) -> S:
        return self._entry.session

    @property
    def key(self) -> SessionKey:
        return self._entry.key

    async def release(self) -> None:
        if not self._done:
            self._done = True
            await self._give_back(self._entry, False)

    async def discard(self) -> None:
        if not self._done:
            self._done = True
            await self._give_back(self._entry, True)


class SessionPool(Generic[S]):
    def __init__(
        self,
        *,
        clock: Clock,
        close: Callable[[S], Awaitable[None]],
        config: PoolConfig | None = None,
        log: HarnessLog | None = None,
    ) -> None:
        self._clock = clock
        self._close = close
        self._config = config or PoolConfig()
        self._log = log or HarnessLog.named(_LOG_NAME)
        self._entries: dict[str, _Entry[S]] = {}
        self._retired: list[_Entry[S]] = []
        self._locks: dict[str, asyncio.Lock] = {}

    async def acquire(self, key: SessionKey, variant: str, opener: Callable[[], Awaitable[S]]) -> SessionLease[S]:
        slot = key.digest()
        async with self._locks.setdefault(slot, asyncio.Lock()):
            entry = self._entries.get(slot)
            if entry is not None:
                if entry.key != key:
                    self._log.warning(
                        "mcp_hub.refused",
                        orgId=key.tenant,
                        mcpServer=key.server_id,
                        reasonCode=SessionOwnerMismatch.reason_code,
                    )
                    raise SessionOwnerMismatch("a pooled session belongs to another credential owner")
                now = self._clock.monotonic()
                if entry.variant == variant and now - entry.opened_at < self._config.max_age_s:
                    entry.leases += 1
                    entry.last_used = now
                    return SessionLease(entry, self._release)
                await self._retire(slot, entry, reason="replaced" if entry.variant != variant else "max_age")
            session = await opener()
            now = self._clock.monotonic()
            entry = _Entry(key=key, variant=variant, session=session, opened_at=now, last_used=now, leases=1)
            self._entries[slot] = entry
            self._log.info("mcp_hub.session_opened", orgId=key.tenant, mcpServer=key.server_id, keyDigest=slot)
            return SessionLease(entry, self._release)

    async def sweep(self) -> int:
        """Close idle and over-age sessions that no lease holds; the number closed."""
        now = self._clock.monotonic()
        closed = 0
        for slot, entry in list(self._entries.items()):
            # Each close below yields: another sweep, or an acquire that replaced this slot, may have run since.
            if entry.leases or self._entries.get(slot) is not entry:
                continue
            if now - entry.last_used >= self._config.idle_ttl_s:
                reason = "idle"
            elif now - entry.opened_at >= self._config.max_age_s:
                reason = "max_age"
            else:
                continue
            del self._entries[slot]
            await self._close_entry(entry, reason)
            closed += 1
        return closed

    async def close_all(self) -> int:
        entries = [*self._entries.values(), *self._retired]
        self._entries.clear()
        self._retired.clear()
        for entry in entries:
            await self._close_entry(entry, "stopped")
        return len(entries)

    def sessions(self) -> tuple[PooledSessionInfo, ...]:
        return tuple(
            PooledSessionInfo(e.key, e.variant, e.leases, e.opened_at, e.last_used) for e in self._entries.values()
        )

    async def _retire(self, slot: str, entry: _Entry[S], *, reason: str) -> None:
        del self._entries[slot]
        if entry.leases:
            entry.retired = True
            self._retired.append(entry)
        else:
            await self._close_entry(entry, reason)

    async def _release(self, entry: _Entry[S], broken: bool) -> None:  # noqa: FBT001 - the lease's callback
        entry.leases = max(0, entry.leases - 1)
        entry.last_used = self._clock.monotonic()
        slot = entry.key.digest()
        if broken and self._entries.get(slot) is entry:
            del self._entries[slot]
            entry.retired = True
            self._retired.append(entry)
        if entry.retired and not entry.leases:
            if entry in self._retired:
                self._retired.remove(entry)
            await self._close_entry(entry, "broken" if broken else "replaced")

    async def _close_entry(self, entry: _Entry[S], reason: str) -> None:
        if entry.closed:
            return
        entry.closed = True
        try:
            await self._close(entry.session)
        except Exception as exc:
            self._log.warning(
                "mcp_hub.session_close_failed",
                orgId=entry.key.tenant,
                mcpServer=entry.key.server_id,
                errorType=type(exc).__name__,
            )
        self._log.info(
            "mcp_hub.session_closed",
            orgId=entry.key.tenant,
            mcpServer=entry.key.server_id,
            keyDigest=entry.key.digest(),
            reason=reason,
        )


__all__ = ["SessionLease", "SessionPool"]
