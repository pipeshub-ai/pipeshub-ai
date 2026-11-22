"""A LISTEN connection that goes silent (a NAT that forgot it, a dropped route) is noticed, replaced and re-read."""

import asyncio
import socket
import threading
import time
from urllib.parse import urlparse

import pytest

from app.services.pg_runtime import ListenHub, PgListener


class Relay:
    """A TCP relay to the database. freeze() stops forwarding on the connections open now, without closing them; new ones work."""

    def __init__(self, host: str, port: int) -> None:
        self.target = (host, port)
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(16)
        self.port = self.srv.getsockname()[1]
        self.frozen_before = 0.0
        self.conns: list[socket.socket] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def freeze(self) -> None:
        self.frozen_before = time.monotonic()

    def _accept(self) -> None:
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            up = socket.create_connection(self.target)
            self.conns += [c, up]
            born = time.monotonic()
            for a, b in ((c, up), (up, c)):
                threading.Thread(
                    target=self._pipe, args=(a, b, born), daemon=True
                ).start()

    def _pipe(self, src: socket.socket, dst: socket.socket, born: float) -> None:
        try:
            while data := src.recv(65536):
                if born < self.frozen_before:
                    continue  # swallowed: the other side sees silence, not a close
                dst.sendall(data)
        except OSError:
            pass

    def close(self) -> None:
        self.srv.close()
        for c in self.conns:
            c.close()


@pytest.mark.chaos
async def test_a_silent_connection_is_replaced_and_subscribers_re_read(
    runtime, monkeypatch, uid
) -> None:
    monkeypatch.setenv("PG_LISTEN_PING_S", "0.4")
    monkeypatch.setenv("PG_LISTEN_PING_TIMEOUT_S", "0.6")
    u = urlparse(runtime.cfg.dsn)
    relay = Relay(u.hostname, u.port)
    dsn = f"postgresql://{u.username}@127.0.0.1:{relay.port}{u.path}"
    got: list[str] = []
    reconnects: list[float] = []
    lst = PgListener(
        dsn,
        "pipeshub_kv_changed",
        got.append,
        on_reconnect=lambda: reconnects.append(time.monotonic()),
    )
    lst.start()
    try:
        for _ in range(50):
            if reconnects:
                break
            await asyncio.sleep(0.1)
        assert reconnects, "never connected"
        hub = ListenHub.for_dsn(dsn)
        assert hub.healthy()
        db = await runtime.db()

        async def notify(text: str) -> None:
            async with db.runtime_uow(write=True) as tx:
                await tx.execute(
                    "/* pipeshub:test.k */ SELECT pg_notify('pipeshub_kv_changed', $1)",
                    text,
                )

        await notify(f"a{uid}")
        for _ in range(30):
            if f"a{uid}" in got:
                break
            await asyncio.sleep(0.1)
        assert f"a{uid}" in got
        n_before = len(reconnects)
        relay.freeze()  # the connection is now a black hole
        await asyncio.sleep(0.1)
        assert hub.healthy()  # not yet known to be dead
        for _ in range(60):
            if len(reconnects) > n_before:
                break
            await asyncio.sleep(0.1)
        assert len(reconnects) > n_before, (
            "the silent connection was never noticed"
        )  # replaced, and the subscriber told to re-read
        await notify(f"b{uid}")
        for _ in range(30):
            if f"b{uid}" in got:
                break
            await asyncio.sleep(0.1)
        assert f"b{uid}" in got  # and it listens again
        assert hub.healthy()
    finally:
        await lst.stop()
        relay.close()


@pytest.mark.chaos
async def test_an_idle_consumer_falls_back_to_polling_when_its_listen_connection_goes_silent(
    runtime, monkeypatch
) -> None:
    """The consumer sleeps up to five minutes on a healthy LISTEN connection. When that connection dies silently, a message published
    meanwhile must not wait for the five minutes: the consumer wakes, sees the connection is down and polls."""
    import uuid
    from logging import getLogger

    from app.services.messaging.config import RedisStreamsConfig
    from app.services.messaging.postgres.consumer import PgMessagingConsumer
    from app.services.messaging.postgres.producer import PgMessagingProducer
    from app.services.messaging.postgres.queue import PgQueue

    monkeypatch.setenv("PG_LISTEN_PING_S", "0.4")
    monkeypatch.setenv("PG_LISTEN_PING_TIMEOUT_S", "0.6")
    u = urlparse(runtime.cfg.dsn)
    relay = Relay(u.hostname, u.port)
    monkeypatch.setenv(
        "PG_LISTEN_URL", f"postgresql://{u.username}@127.0.0.1:{relay.port}{u.path}"
    )
    topic = f"ka{uuid.uuid4().hex[:6]}"
    got: list[int] = []

    async def handler(m) -> bool:
        got.append(m.payload["i"])
        return True

    queue = PgQueue(runtime)
    c = PgMessagingConsumer(
        getLogger("t"),
        RedisStreamsConfig(
            topics=[topic], group_id="g", client_id="c", block_ms=60000, batch_size=5
        ),
        queue=queue,
    )
    try:
        await c.start(handler)
        await asyncio.sleep(1.5)  # idle, asleep on a healthy connection
        relay.freeze()
        await asyncio.sleep(0.2)
        await PgMessagingProducer(getLogger("t"), queue=queue).send_event(
            topic, "e", {"i": 1}
        )
        t0 = time.monotonic()
        while not got and time.monotonic() - t0 < 20:  # noqa: ASYNC110 - a deadline-bound poll of what another task appends
            await asyncio.sleep(0.1)
        assert got == [1], "the message waited for the safety-net timer"
        assert time.monotonic() - t0 < 20
    finally:
        await c.stop()
        relay.close()


@pytest.mark.chaos
async def test_a_listen_connection_the_server_terminates_is_replaced_within_a_couple_of_seconds(
    runtime,
) -> None:
    import uuid
    from logging import getLogger

    from app.services.messaging.config import RedisStreamsConfig
    from app.services.messaging.postgres.consumer import PgMessagingConsumer
    from app.services.messaging.postgres.producer import PgMessagingProducer
    from app.services.messaging.postgres.queue import PgQueue

    topic = f"tk{uuid.uuid4().hex[:6]}"
    got: list[int] = []

    async def handler(m) -> bool:
        got.append(m.payload["i"])
        return True

    queue = PgQueue(runtime)
    c = PgMessagingConsumer(
        getLogger("t"),
        RedisStreamsConfig(
            topics=[topic], group_id="g", client_id="c", block_ms=60000, batch_size=5
        ),
        queue=queue,
    )
    try:
        await c.start(handler)
        await asyncio.sleep(1.5)
        import asyncpg

        admin = await asyncpg.connect(
            runtime.cfg.dsn.replace("pipeshub_connectors_login", "postgres")
        )
        n = await admin.fetchval(
            "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE application_name = 'pipeshub-listen'"
        )
        await admin.close()
        assert n >= 1
        await asyncio.sleep(0.3)
        await PgMessagingProducer(getLogger("t"), queue=queue).send_event(
            topic, "e", {"i": 1}
        )
        t0 = time.monotonic()
        while not got and time.monotonic() - t0 < 20:  # noqa: ASYNC110 - a deadline-bound poll of what another task appends
            await asyncio.sleep(0.05)
        assert got == [1]
        assert time.monotonic() - t0 < 3.0, time.monotonic() - t0
    finally:
        await c.stop()
