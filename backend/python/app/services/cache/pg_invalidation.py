"""Cross-process invalidation of the accessible-records cache when no Redis is shared (``REDIS_MODE=postgres``).

Each process keeps its own cache. A permission change reaches the others as a NOTIFY on one channel, through the process's shared
LISTEN connection; every process then drops the named entry. The payload is the kind of change, the org and the connector or knowledge
base id, never a record id or any content.

It fails closed: the bus reports itself healthy only while the LISTEN connection is up and answering, and the cache is bypassed (every
search reads the graph) while it is not, so a lost notification can only cost speed, never show a record that was revoked.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from app.core.org_context import spawn_detached
from app.services.pg_runtime import ListenHub, PgListener, RuntimeDb, listen_dsn

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Awaitable, Callable

CHANNEL = "pipeshub_acl_cache"
_NOTIFY = "/* pipeshub:acl.notify */ SELECT pg_notify('pipeshub_acl_cache', $1)"

logger = logging.getLogger(__name__)


class PgCacheBus:
    """``apply(kind, org_id, scope_id)`` is awaited in this process for every change published by another one (kind: ``c`` a connector's
    entries, ``m`` its in-flight fills only, ``k`` a knowledge base); ``flush()`` after every (re)connect, because notifications sent
    while the connection was away are lost."""

    def __init__(
        self,
        apply: Callable[[str, str, str], Awaitable[None]],
        flush: Callable[[], Awaitable[None]],
        runtime: RuntimeDb | None = None,
    ) -> None:
        self._apply, self._flush = apply, flush
        self._rt = runtime or RuntimeDb()
        self._dsn = listen_dsn(self._rt.cfg)
        self._listener: PgListener | None = None
        self._task: asyncio.Task | None = None

    @property
    def available(self) -> bool:
        """False when there is no direct address to LISTEN on: nothing could ever invalidate, so no cache should be kept."""
        return self._dsn is not None

    def start(self) -> None:
        if self._dsn is None:
            return
        self._listener = PgListener(self._dsn, CHANNEL, self._on_notify, on_reconnect=self._on_reconnect)
        self._task = self._listener.start()

    def healthy(self) -> bool:
        return self._dsn is not None and ListenHub.for_dsn(self._dsn).healthy(channel=CHANNEL)

    def _on_notify(self, payload: str) -> None:
        try:
            msg = json.loads(payload)
            kind, org, scope = str(msg["k"]), str(msg["o"]), str(msg["i"])
        except (ValueError, KeyError, TypeError):
            spawn_detached(self._flush())      # a message that cannot be read: drop everything rather than guess
            return
        spawn_detached(self._apply(kind, org, scope))

    def _on_reconnect(self) -> None:
        spawn_detached(self._flush())

    async def publish(self, kind: str, org_id: str, scope_id: str) -> None:
        payload = json.dumps({"k": kind, "o": org_id, "i": scope_id}, separators=(",", ":"))
        db = await self._rt.db()
        async with db.runtime_uow(write=True) as tx:
            await tx.execute(_NOTIFY, payload)

    async def stop(self) -> None:
        if self._listener is not None:
            await self._listener.stop()
            self._listener = None
