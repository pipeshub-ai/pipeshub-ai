"""The pin on the PostgreSQL key-value store: atomic claim, an uncached re-read when NOTIFY is not heard, and release.

The store's own SQL is interpreted by a small fake of the runtime database, so the real
``PostgresKeyValueStore`` code (the ``ON CONFLICT ... WHERE expires_at <= now()`` claim, the range listing, the
confirmed delete) is what runs. Nothing here needs a server.
"""

import asyncio
import contextlib
import json
import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.config.configuration_service import ConfigurationService
from app.config.providers import postgres_store
from app.config.providers.postgres_store import PostgresKeyValueStore
from app.services.vector_db.collection_pin import FP32, INT8, CollectionPinStore
from app.services.vector_db.precision_guard import PrecisionGuard
from tests.unit.services.vector_db.test_collection_pin import _FakeDb

log = logging.getLogger("pg-kv-test")


class _Tx:
    def __init__(self, table: dict) -> None:
        self.table = table

    async def fetchval(self, sql: str, *args):  # noqa: ANN202
        key = args[0]
        if "kv.setnx" in sql:
            if key in self.table:  # a live row blocks the claim; a pin has no expiry, so it is always live
                return None
            self.table[key] = args[1]
            return True
        if "kv.set" in sql:
            self.table[key] = args[1]
            return True
        if "kv.get" in sql:
            return self.table.get(key)
        if "kv.delete" in sql:
            return True if self.table.pop(key, None) is not None else None
        raise AssertionError(f"unexpected statement: {sql[:40]}")

    async def fetch(self, sql: str, prefix: str, end):  # noqa: ANN202
        assert "kv.range" in sql
        return [{"key": k} for k in sorted(self.table) if k >= prefix and (end is None or k < end)]

    async def execute(self, sql: str, *args):  # noqa: ANN202
        return None


class _Runtime:
    """One database, shared by every store object: each ``PostgresKeyValueStore`` is a process."""

    def __init__(self) -> None:
        self.table: dict = {}
        self.cfg = SimpleNamespace()

    async def db(self):  # noqa: ANN202
        outer = self

        class _Db:
            @contextlib.asynccontextmanager
            async def runtime_uow(self, write: bool = False):  # noqa: ANN202
                await asyncio.sleep(0)  # a yield point: two claims really interleave
                yield _Tx(outer.table)

        return _Db()


def _process(runtime: _Runtime):  # noqa: ANN202
    store = PostgresKeyValueStore(lambda v: json.dumps(v).encode(), lambda b: json.loads(b), runtime=runtime)
    with patch.object(postgres_store, "listen_dsn", return_value=None):  # no direct address: NOTIFY is never heard
        config = ConfigurationService(log, store)
    return config, CollectionPinStore(config, log)


@pytest.mark.asyncio
async def test_two_processes_claiming_with_different_precision_leave_one_pin() -> None:
    runtime = _Runtime()
    (_, pins_a), (_, pins_b) = _process(runtime), _process(runtime)
    one, two = await asyncio.gather(pins_a.claim("c", INT8, "o1"), pins_b.claim("c", FP32, "o1"))
    assert one.precision == two.precision
    assert len(runtime.table) == 1


@pytest.mark.asyncio
async def test_a_process_that_missed_the_notification_sees_the_release_on_an_uncached_read() -> None:
    runtime = _Runtime()
    (_, pins_a), (_, pins_b) = _process(runtime), _process(runtime)
    await pins_a.claim("c", INT8, "o1")
    assert (await pins_b.get("c", "o1")).precision == INT8  # B now holds the pin in its cache
    await pins_a.release("c", "o1")
    assert (await pins_b.get("c", "o1")).precision == INT8  # NOTIFY fails open: the cache is stale
    assert await pins_b.get("c", "o1", fresh=True) is None  # the re-read that claims and restores rely on


@pytest.mark.asyncio
async def test_a_derived_restore_does_not_resurrect_a_released_pin_on_this_store() -> None:
    runtime = _Runtime()
    db = _FakeDb()
    db.add(FP32, n=2)
    (_, pins_a), (_, pins_b) = _process(runtime), _process(runtime)
    guard_b = PrecisionGuard(pins_b, db, log)
    assert (await guard_b.standing("c", "o1")).precision == FP32  # B has seen "tagged fp32 present"
    db.points.clear()
    await pins_a.claim("c", FP32, "o1")
    await pins_a.release("c", "o1")
    await guard_b.restore("c", "o1", FP32)
    assert runtime.table == {}


@pytest.mark.asyncio
async def test_release_removes_nested_per_org_pins_and_orphans_by_listing_the_directory() -> None:
    runtime = _Runtime()
    _, pins = _process(runtime)
    await pins.claim("c", INT8)
    await pins.claim("c", INT8, "o1")
    await pins.claim("gone", FP32)
    await pins.release_collection("c")
    assert list(await pins.keys()) == [pins.key("gone")]
    assert await pins.release_all() == 1
    assert await pins.keys() == []


@pytest.mark.asyncio
async def test_a_pin_is_never_replaced_by_a_later_claim() -> None:
    runtime = _Runtime()
    (_, pins_a), (_, pins_b) = _process(runtime), _process(runtime)
    await pins_a.claim("c", INT8, "o1")
    assert (await pins_b.claim("c", FP32, "o1")).precision == INT8
