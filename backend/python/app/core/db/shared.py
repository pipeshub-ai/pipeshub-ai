"""One started ``KernelDB`` per (config, event loop), shared by every caller.

Some callers build their provider per call (and so cannot own a pool). They acquire the shared database here and release it
when done; the last release closes it.
"""

from __future__ import annotations

import asyncio
import threading
import weakref
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.db.uow import KernelDB

if TYPE_CHECKING:
    from app.core.db.config import DbConfig


@dataclass
class _Entry:
    db: KernelDB
    refs: int


_entries: dict[tuple[DbConfig, asyncio.AbstractEventLoop], _Entry] = {}
_entries_guard = threading.Lock()
# One asyncio.Lock per event loop (a lock is only safe inside the loop it is used from); the dict of locks is guarded
# by a thread lock because several loops may ask at once. Both registries are keyed by the loop object, not id(loop): the address
# of a closed loop can be reused by a later one, which would then be handed pools that belong to the dead loop.
_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = weakref.WeakKeyDictionary()
_locks_guard = threading.Lock()


def _loop_lock() -> asyncio.Lock:
    with _locks_guard:
        return _locks.setdefault(asyncio.get_running_loop(), asyncio.Lock())


def _prune_closed() -> None:
    """Release, without awaiting, the databases of loops that have been closed (nothing is left to run ``close`` on them)."""
    with _entries_guard:
        gone = [_entries.pop(k) for k in [k for k in _entries if k[1].is_closed()]]
    for entry in gone:
        entry.db.terminate()


async def acquire_db(cfg: DbConfig) -> KernelDB:
    async with _loop_lock():
        _prune_closed()
        key = (cfg, asyncio.get_running_loop())
        with _entries_guard:
            entry = _entries.get(key)
        if entry is None:
            db = KernelDB(cfg)
            await db.start()
            entry = _Entry(db, 0)
            with _entries_guard:
                _entries[key] = entry
        entry.refs += 1
        return entry.db


async def release_db(cfg: DbConfig) -> None:
    async with _loop_lock():
        key = (cfg, asyncio.get_running_loop())
        with _entries_guard:
            entry = _entries.get(key)
        if entry is None:
            return
        entry.refs -= 1
        if entry.refs <= 0:
            with _entries_guard:
                _entries.pop(key, None)
            await entry.db.close()
