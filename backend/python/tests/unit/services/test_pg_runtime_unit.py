import time

from app.services.pg_runtime import ListenHub, RuntimeDb


def test_shared_runtime_db_is_one_per_process() -> None:
    assert RuntimeDb.shared() is RuntimeDb.shared()


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
    rt = RuntimeDb(MagicMock())
    rt._dbs[dead] = task

    async def run() -> None:
        async def start() -> object:
            return MagicMock()

        rt._start = start  # type: ignore[method-assign]  # no database in a unit test
        await rt.db()

    asyncio.run(run())
    gone_db.terminate.assert_called_once()
    assert dead not in rt._dbs
