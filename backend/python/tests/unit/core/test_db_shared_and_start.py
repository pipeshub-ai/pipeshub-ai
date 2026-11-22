import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.db import shared
from app.core.db.config import DbConfig
from app.core.db.uow import KernelDB


def test_the_shared_registry_lock_is_per_event_loop() -> None:
    async def two() -> tuple[asyncio.Lock, asyncio.Lock]:
        return shared._loop_lock(), shared._loop_lock()

    a1, a2 = asyncio.run(two())
    (b1, _) = asyncio.run(two())
    assert a1 is a2
    assert a1 is not b1


async def test_start_closes_the_main_pool_when_the_jobs_pool_cannot_be_made() -> None:
    main_pool = MagicMock()
    main_pool.close = AsyncMock()
    calls = []

    async def fake_create_pool(dsn: str, **_kw: object) -> MagicMock:
        calls.append(dsn)
        if len(calls) == 2:
            raise OSError("jobs database down")
        return main_pool

    db = KernelDB(DbConfig(service="indexing", dsn="postgresql://main", jobs_dsn="postgresql://jobs"))
    with patch("app.core.db.uow.create_pool", fake_create_pool), pytest.raises(OSError):
        await db.start()
    main_pool.close.assert_awaited_once()
