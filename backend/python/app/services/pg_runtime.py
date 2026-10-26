"""PostgreSQL access for the runtime's own state (key-value entries, the work queue) when no Redis is deployed.

Reads run in ``KernelDB.runtime_uow()``: the service role, no org. LISTEN needs a session, so it uses a dedicated direct
connection (``PG_LISTEN_URL``, or ``PG_CORE_URL`` when ``PG_POOLER=none``); behind a transaction pooler without a
listen URL there is no push, and every listener polls instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import random
import threading
import time
import weakref
from typing import TYPE_CHECKING

import asyncpg

from app.core.db.config import DbConfig
from app.core.org_context import spawn_detached

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.core.db.uow import KernelDB

logger = logging.getLogger(__name__)


def _ping_interval() -> float:
    return float(os.getenv("PG_LISTEN_PING_S", "10"))


def _ping_timeout() -> float:
    return float(os.getenv("PG_LISTEN_PING_TIMEOUT_S", "5"))


KV_CHANNEL = "pipeshub_kv_changed"
QUEUE_CHANNEL = "pipeshub_queue"


def runtime_service() -> str:
    """The service role this process connects as (``pipeshub_<service>``)."""
    return os.getenv("PG_RUNTIME_SERVICE", "connectors")


def runtime_db_config() -> DbConfig:
    """``PG_CORE_URL`` is the login of ``PG_RUNTIME_SERVICE``'s role (process_monitor.sh hands each process its own)."""
    return DbConfig.from_env(runtime_service())


def listen_dsn(cfg: DbConfig | None = None) -> str | None:
    """A direct (session) DSN for LISTEN, or None when only a transaction pooler is configured."""
    explicit = os.getenv("PG_LISTEN_URL", "")
    if explicit:
        return _plain(explicit)
    cfg = cfg or runtime_db_config()
    return _plain(cfg.dsn) if cfg.pooler == "none" else None


def _plain(dsn: str) -> str:
    return dsn.replace("postgresql+psycopg://", "postgresql://").replace(
        "postgresql+asyncpg://", "postgresql://"
    )


# One database runtime per (configuration, event loop) for the whole process. Every RuntimeDb (key-value store, queue, cache
# invalidation, state, health) used to start a runtime of its own, so each added pools of connections, and every connector
# instance that reached one added more. Weak on the loop: a finished thread's loop releases its entry.
_SHARED: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[DbConfig, asyncio.Task[KernelDB]]] = weakref.WeakKeyDictionary()
_SHARED_LOCK = threading.Lock()


class RuntimeDb:
    """The shared KernelDB of this process for the runtime tables, per event loop, made on first use. Safe to call from any thread.

    It is used from the configuration service's watch thread as well as the main loop, so it takes a thread lock rather than an
    asyncio one. Two RuntimeDb objects with the same configuration share one KernelDB (one set of pools) on a loop.
    """

    def __init__(self, cfg: DbConfig | None = None) -> None:
        self._cfg = cfg

    @property
    def cfg(self) -> DbConfig:
        if self._cfg is None:
            self._cfg = runtime_db_config()
        return self._cfg

    async def db(self) -> KernelDB:
        loop = asyncio.get_running_loop()
        cfg = self.cfg
        with _SHARED_LOCK:
            for old in [lp for lp in _SHARED if lp.is_closed()]:
                # its loop can run nothing any more: release the pools without awaiting
                for gone in _SHARED.pop(old).values():
                    if gone.done() and not gone.cancelled() and gone.exception() is None:
                        gone.result().terminate()
            per_loop = _SHARED.setdefault(loop, {})
            task = per_loop.get(cfg)
            if task is None or (
                task.done() and (task.cancelled() or task.exception() is not None)
            ):
                # detached: the caller's request context (its org) must not follow the pool's start-up; a failed start is retried by the next caller
                task = per_loop[cfg] = spawn_detached(self._start())
        return await asyncio.shield(task)

    async def _start(self) -> KernelDB:
        from app.core.db.uow import KernelDB

        db = KernelDB(self.cfg)
        await db.start()
        return db


class _Subscription:
    __slots__ = ("channel", "on_notify", "on_reconnect", "on_lost", "loop", "active")

    def __init__(self, channel, on_notify, on_reconnect, loop, on_lost=None) -> None:  # noqa: ANN001
        (
            self.channel,
            self.on_notify,
            self.on_reconnect,
            self.on_lost,
            self.loop,
            self.active,
        ) = (
            channel,
            on_notify,
            on_reconnect,
            on_lost,
            loop,
            True,
        )

    def lost(self) -> None:
        if self.active and self.on_lost is not None:
            self.loop.call_soon_threadsafe(self._call_lost)

    def _call_lost(self) -> None:
        try:
            if self.active and self.on_lost is not None:
                self.on_lost()
        except Exception:
            logger.exception("LISTEN %s on_lost callback failed", self.channel)

    def deliver(self, payload: str) -> None:
        if self.active:
            self.loop.call_soon_threadsafe(self._call, payload)

    def reconnected(self) -> None:
        if self.active and self.on_reconnect is not None:
            self.loop.call_soon_threadsafe(self._reconnect)

    def _call(self, payload: str) -> None:
        if not self.active:
            return
        try:
            self.on_notify(payload)
        except Exception:
            logger.exception("LISTEN %s callback failed", self.channel)

    def _reconnect(self) -> None:
        try:
            res = self.on_reconnect() if self.on_reconnect else None
            if asyncio.iscoroutine(res):
                spawn_detached(res)
        except Exception:
            logger.exception("LISTEN %s reconnect callback failed", self.channel)


class ListenHub:
    """The one LISTEN connection of this process (per DSN). Every channel and every subscriber shares it, so ten consumers and the
    configuration service cost one of the server's ``max_connections``, not eleven.

    It lives on a thread of its own, with its own event loop, so it outlives whichever caller's loop first needed it; each callback is
    handed back to the loop of the subscriber that registered it. After a reconnect every subscriber's ``on_reconnect`` runs, because
    notifications sent while the connection was away are lost.
    """

    _hubs: dict[str, ListenHub] = {}
    _hubs_lock = threading.Lock()

    @classmethod
    def for_dsn(cls, dsn: str) -> ListenHub:
        with cls._hubs_lock:
            hub = cls._hubs.get(dsn)
            if hub is None:
                hub = cls._hubs[dsn] = cls(dsn)
            return hub

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._lock = threading.Lock()
        self._subs: dict[str, list[_Subscription]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._con: asyncpg.Connection | None = None
        self._started = threading.Event()
        self._thread_started = False
        self._cbs: dict[str, object] = {}
        self.last_ok = 0.0
        self._ops: asyncio.Lock | None = (
            None  # one statement at a time on the connection
        )
        self._listened: set[str] = set()  # channels LISTENed on the live connection
        self._wanted: asyncio.Event | None = (
            None  # set while a subscriber exists: with none, the hub holds no connection
        )

    def _cb(self, channel: str):  # noqa: ANN202
        cb = self._cbs.get(channel)
        if cb is None:
            cb = self._cbs[channel] = (
                lambda _c, _pid, _ch, payload, ch=channel: self._fan_out(ch, payload)
            )
        return cb

    @property
    def connections(self) -> int:
        """1 while the connection is up (for tests and diagnostics)."""
        return 1 if self._con is not None else 0

    def subscribe(
        self,
        channel: str,
        on_notify: Callable[[str], None],
        on_reconnect: Callable[[], Awaitable[None] | None] | None,
        loop: asyncio.AbstractEventLoop,
        on_lost: Callable[[], None] | None = None,
    ) -> _Subscription:
        sub = _Subscription(channel, on_notify, on_reconnect, loop, on_lost)
        with self._lock:
            first = channel not in self._subs
            self._subs.setdefault(channel, []).append(sub)
            if (
                not self._thread_started
            ):  # exactly one hub thread, however many subscribers race to be first
                self._thread_started = True
                threading.Thread(
                    target=self._thread_main, name="pg-listen-hub", daemon=True
                ).start()
        self._started.wait(5)
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._want)
        if first and self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._listen(channel), self._loop)
        elif self._con is not None:
            sub.reconnected()  # joining a live connection: re-read what may have been missed before the LISTEN
        return sub

    def unsubscribe(self, sub: _Subscription) -> None:
        """Idempotent: a subscription is removed once, and the channel is dropped only if that left it with no subscriber."""
        with self._lock:
            subs = self._subs.get(sub.channel, [])
            if not sub.active or sub not in subs:
                sub.active = False
                return
            sub.active = False
            subs.remove(sub)
            empty = not subs
            if empty:
                self._subs.pop(sub.channel, None)
        if empty and self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._unlisten(sub.channel), self._loop)
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._want)

    # -- hub thread ----------------------------------------------------------------------------------------------
    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._ops = asyncio.Lock()
        self._wanted = asyncio.Event()
        with self._lock:
            if self._subs:
                self._wanted.set()
        self._loop = loop
        self._started.set()
        loop.run_until_complete(self._run())

    def _fan_out(self, channel: str, payload: str) -> None:
        self.last_ok = time.monotonic()
        with self._lock:
            subs = list(self._subs.get(channel, []))
        for s in subs:
            s.deliver(payload)

    async def _listen(self, channel: str) -> None:
        assert self._ops is not None
        async with self._ops:
            con = self._con
            if con is None or channel in self._listened:
                return
            self._listened.add(channel)
            try:
                await con.add_listener(channel, self._cb(channel))
            except Exception as e:
                self._listened.discard(channel)
                logger.warning("LISTEN %s failed: %s", channel, e)

    async def _unlisten(self, channel: str) -> None:
        assert self._ops is not None
        async with self._ops:
            with self._lock:
                wanted = channel in self._subs
            con = self._con
            if con is not None and channel in self._listened and not wanted:
                self._listened.discard(channel)
                with contextlib.suppress(Exception):
                    await con.remove_listener(channel, self._cb(channel))

    def _want(self) -> None:
        """On the hub's loop: with no subscriber left, drop the connection and stop reconnecting; the next subscriber wakes it."""
        assert self._wanted is not None
        with self._lock:
            wanted = bool(self._subs)
        if wanted:
            self._wanted.set()
        else:
            self._wanted.clear()
            if self._con is not None:
                self._con.terminate()

    async def _keepalive(self, con: asyncpg.Connection, lost: asyncio.Event) -> None:
        """A half-open connection (a NAT that forgot it, a dropped route) never says it is gone: ask it. A connection that does not
        answer is dropped, which reconnects, re-LISTENs and tells every subscriber to re-read what it may have missed."""
        while not lost.is_set():
            await asyncio.sleep(_ping_interval())
            try:
                assert self._ops is not None
                async with self._ops:
                    await asyncio.wait_for(
                        con.execute("/* pipeshub:listen.ping */ SELECT 1"),
                        _ping_timeout(),
                    )
                self.last_ok = time.monotonic()
            except Exception as e:
                logger.warning(
                    "LISTEN hub: the connection stopped answering (%s: %s); reconnecting",
                    type(e).__name__,
                    e,
                )
                con.terminate()
                lost.set()
                return

    def healthy(self, max_age: float | None = None, channel: str | None = None) -> bool:
        """True while the connection is up and answered (or delivered) within ``max_age`` seconds (three pings by default), and,
        when ``channel`` is given, that channel is LISTENed on it (a new connection listens channel by channel). Code that trusts
        a notification to invalidate something must treat False as "the notification may not arrive"."""
        age = max_age if max_age is not None else 3 * _ping_interval() + _ping_timeout()
        if channel is not None and channel not in self._listened:
            return False
        return self._con is not None and (time.monotonic() - self.last_ok) <= age

    async def _run(self) -> None:
        attempt = 0
        while True:
            assert self._wanted is not None
            await self._wanted.wait()
            con: asyncpg.Connection | None = None
            try:
                con = await asyncpg.connect(
                    self._dsn, server_settings={"application_name": "pipeshub-listen"}
                )
                lost = asyncio.Event()
                con.add_termination_listener(lambda _c, ev=lost: ev.set())
                self._listened = set()
                self._con = con
                while (
                    True
                ):  # channels subscribed while connecting are caught by the next pass
                    with self._lock:
                        missing = [ch for ch in self._subs if ch not in self._listened]
                    if not missing:
                        break
                    for ch in missing:
                        await self._listen(ch)
                attempt = 0
                with self._lock:
                    everyone = [s for subs in self._subs.values() for s in subs]
                for s in everyone:
                    s.reconnected()
                self.last_ok = time.monotonic()
                ping = spawn_detached(self._keepalive(con, lost))
                try:
                    await lost.wait()
                finally:
                    ping.cancel()
            except Exception as e:
                logger.warning("LISTEN hub: %s: %s", type(e).__name__, e)
            finally:
                self._con = None
                self._listened = set()
                with self._lock:
                    everyone = [s for subs in self._subs.values() for s in subs]
                for s in everyone:
                    s.lost()  # subscribers that sleep until a notification must not trust one now
                if con is not None:
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(con.close(), 2)
            attempt += 1
            await asyncio.sleep(
                min(0.5 * 2 ** (attempt - 1), 30) * (0.5 + random.random())
            )


class PgListener:
    """LISTEN on one channel, through the process's shared connection (:class:`ListenHub`). ``on_notify(payload)`` and
    ``on_reconnect()`` run on the loop that called :meth:`start`."""

    def __init__(
        self,
        dsn: str,
        channel: str,
        on_notify: Callable[[str], None],
        on_reconnect: Callable[[], Awaitable[None] | None] | None = None,
        on_lost: Callable[[], None] | None = None,
    ) -> None:
        self._on_lost = on_lost
        self._dsn, self._channel, self._on_notify, self._on_reconnect = (
            dsn,
            channel,
            on_notify,
            on_reconnect,
        )
        self._task: asyncio.Task | None = None
        self._sub: _Subscription | None = None

    def start(self) -> asyncio.Task:
        """Begin listening; the returned task runs until cancelled (the shape the configuration service expects of a subscription)."""
        loop = asyncio.get_running_loop()
        hub = ListenHub.for_dsn(self._dsn)
        self._sub = hub.subscribe(
            self._channel, self._on_notify, self._on_reconnect, loop, self._on_lost
        )

        async def hold() -> None:
            try:
                await asyncio.Event().wait()
            finally:
                hub.unsubscribe(self._sub)  # type: ignore[arg-type]

        self._task = spawn_detached(hold())
        return self._task

    def healthy(self) -> bool:
        """The shared connection is up and answering: a notification can be trusted to arrive."""
        return ListenHub.for_dsn(self._dsn).healthy(channel=self._channel)

    async def stop(self) -> None:
        if self._sub is not None:
            ListenHub.for_dsn(self._dsn).unsubscribe(self._sub)
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
