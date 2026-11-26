import time

from app.services import pg_runtime
from app.services.pg_runtime import ListenHub, RuntimeDb


def test_healthy_needs_the_channel_to_be_listened() -> None:
    hub = ListenHub("postgresql://unused")
    hub._con = object()  # type: ignore[assignment]  # a live connection, as far as healthy() can tell
    hub.last_ok = time.monotonic()
    assert hub.healthy()
    assert not hub.healthy(channel="pipeshub_kv_changed")
    hub._listened.add("pipeshub_kv_changed")
    assert hub.healthy(channel="pipeshub_kv_changed")
    assert not hub.healthy(channel="other")


def test_runtime_db_terminates_the_pools_of_a_closed_loop() -> None:
    import asyncio
    from unittest.mock import MagicMock

    dead = asyncio.new_event_loop()
    dead.close()
    gone_db = MagicMock()
    task = MagicMock()
    task.done.return_value, task.cancelled.return_value, task.exception.return_value = True, False, None
    task.result.return_value = gone_db
    cfg = MagicMock()
    rt = RuntimeDb(cfg)
    pg_runtime._SHARED[dead] = {cfg: task}

    async def run() -> None:
        async def start() -> object:
            return MagicMock()

        rt._start = start  # type: ignore[method-assign]  # no database in a unit test
        await rt.db()

    asyncio.run(run())
    gone_db.terminate.assert_called_once()
    assert dead not in pg_runtime._SHARED



def test_hub_reconnects_when_its_new_connection_is_cut_before_the_listens() -> None:
    """A connection terminated between connect and LISTEN refuses every LISTEN at once, without yielding: the hub must reconnect,
    not re-LISTEN on it in a loop that never lets its event loop (or the process's other threads) run."""
    import asyncio
    from unittest.mock import MagicMock, patch

    import asyncpg

    class Spun(BaseException):  # not an Exception: _listen cannot swallow it, so the old loop ends instead of spinning forever
        pass

    dead_calls = 0

    async def run() -> None:
        listening = asyncio.Event()

        class Dead(MagicMock):
            async def add_listener(self, channel: str, cb: object) -> None:
                nonlocal dead_calls
                dead_calls += 1
                if dead_calls > 100:
                    raise Spun("LISTEN re-tried on a closed connection")
                raise asyncpg.exceptions.InterfaceError("connection is closed")

            async def close(self) -> None:
                pass

        class Live(Dead):
            async def add_listener(self, channel: str, cb: object) -> None:
                listening.set()

        connections = [Dead(), Live()]

        async def connect(*_a: object, **_k: object) -> object:
            return connections.pop(0)

        hub = ListenHub("postgresql://unused")
        hub._ops, hub._wanted = asyncio.Lock(), asyncio.Event()
        hub._wanted.set()
        hub._subs = {"ch": [pg_runtime._Subscription("ch", lambda _p: None, None, asyncio.get_running_loop())]}
        with patch.object(pg_runtime.asyncpg, "connect", connect), patch.object(pg_runtime, "_ping_interval", lambda: 60):
            task = asyncio.create_task(hub._run())
            done, _ = await asyncio.wait({task, asyncio.create_task(listening.wait())}, timeout=10, return_when=asyncio.FIRST_COMPLETED)
            task.cancel()
        assert task not in done, task.exception()
        assert listening.is_set() and hub._listened == {"ch"}

    asyncio.run(run())
    assert dead_calls == 1
