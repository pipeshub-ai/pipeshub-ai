"""One started ``KernelDB`` per (config, event loop), shared by every caller.

Some callers build their provider per call (and so cannot own a pool). They acquire the shared database here and release it
when done; the last release closes it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.db.uow import KernelDB

if TYPE_CHECKING:
    from app.core.db.config import DbConfig


@dataclass
class _Entry:
    db: KernelDB
    refs: int


_entries: dict[tuple[DbConfig, int], _Entry] = {}
_lock = asyncio.Lock()


async def acquire_db(cfg: DbConfig) -> KernelDB:
    async with _lock:
        key = (cfg, id(asyncio.get_running_loop()))
        entry = _entries.get(key)
        if entry is None:
            db = KernelDB(cfg)
            await db.start()
            entry = _entries[key] = _Entry(db, 0)
        entry.refs += 1
        return entry.db


async def release_db(cfg: DbConfig) -> None:
    async with _lock:
        key = (cfg, id(asyncio.get_running_loop()))
        entry = _entries.get(key)
        if entry is None:
            return
        entry.refs -= 1
        if entry.refs <= 0:
            del _entries[key]
            await entry.db.close()
