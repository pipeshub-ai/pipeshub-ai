"""Every RuntimeDb of a process shares one database runtime per event loop, so a new connector, queue or cache adds no connections."""
import asyncio
from collections.abc import Iterator

import pytest

from app.core.db.config import DbConfig
from app.services import pg_runtime


class FakeDb:
    started = 0

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    async def start(self) -> None:
        FakeDb.started += 1


@pytest.fixture(autouse=True)
def fake_kernel(monkeypatch) -> Iterator[None]:
    FakeDb.started = 0
    monkeypatch.setattr("app.core.db.uow.KernelDB", FakeDb)
    pg_runtime._SHARED.clear()
    yield
    # the fake databases must not outlive the test: a later test would try to close them with the real loop pruning
    pg_runtime._SHARED.clear()


def cfg(**kw) -> DbConfig:
    return DbConfig(service="connectors", dsn="postgresql://x@h/db", **kw)


async def test_runtime_dbs_with_one_config_share_one_pool_set() -> None:
    dbs = [await pg_runtime.RuntimeDb(cfg()).db() for _ in range(6)]  # six consumers: key-value store, queue, cache, state ...
    assert all(d is dbs[0] for d in dbs) and FakeDb.started == 1


async def test_another_configuration_or_another_loop_is_its_own() -> None:
    a = await pg_runtime.RuntimeDb(cfg()).db()
    b = await pg_runtime.RuntimeDb(cfg(pool_max=2)).db()
    assert a is not b
    box = {}

    def other_loop() -> None:
        box["db"] = asyncio.run(pg_runtime.RuntimeDb(cfg()).db())

    await asyncio.to_thread(other_loop)
    assert box["db"] is not a and FakeDb.started == 3
