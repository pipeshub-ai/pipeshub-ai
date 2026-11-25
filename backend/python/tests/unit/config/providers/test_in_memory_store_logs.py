"""What the in-memory key-value store logs at DEBUG.

Every operation checks every stored key for expiry. When each check logged a
line, one read of a store holding a few hundred keys wrote that many records,
each through the secret-redacting formatter, and a test that happened to run
with this logger at DEBUG took minutes instead of a second.
"""

import asyncio
import logging
import time
from collections.abc import Iterator

import pytest

from app.config.providers import in_memory_store
from app.config.providers.in_memory_store import InMemoryKeyValueStore

KEYS = 500


class _Count(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def records() -> Iterator[list[logging.LogRecord]]:
    logger = in_memory_store.logger
    handler, level = _Count(), logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield handler.records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)


def _filled() -> InMemoryKeyValueStore:
    store = InMemoryKeyValueStore(logging.getLogger("t_in_memory_store"))
    for i in range(KEYS):
        asyncio.run(store.create_key(f"/k/{i}", i, ttl=3600 if i % 2 else None))
    return store


def test_a_read_logs_the_same_few_lines_however_many_keys_are_stored(records) -> None:
    store = _filled()
    records.clear()

    assert asyncio.run(store.get_key("/k/7")) == 7
    assert asyncio.run(store.get_key("/missing")) is None
    asyncio.run(store.delete_key("/k/8"))
    assert len(asyncio.run(store.get_all_keys())) == KEYS - 1

    assert len(records) <= 12, [r.getMessage() for r in records]


def test_expired_keys_are_still_removed_and_counted_once(records) -> None:
    store = _filled()
    for data in store.store.values():
        if data.expiry is not None:
            data.expiry = time.time() - 1
    records.clear()

    assert len(asyncio.run(store.get_all_keys())) == KEYS // 2
    assert [r.getMessage() for r in records if r.getMessage().startswith("Removed")] == [f"Removed {KEYS // 2} expired keys"]
