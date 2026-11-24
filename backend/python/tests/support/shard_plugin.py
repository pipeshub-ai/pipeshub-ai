"""``--shard=I/N``: run only the I-th (1-based) of N deterministic slices of the collected tests.

A test belongs to the slice ``crc32(node id) % N``, so the slices are disjoint, together cover every test, and a new test
lands in one of them without anyone listing it. Used by CI jobs that split one suite over several runners
(``python -m pytest -p tests.support.shard_plugin --shard=1/3 ...``). Every pytest-xdist worker computes the same slice.
"""

from __future__ import annotations

import zlib

import pytest


def parse_shard(value: str) -> tuple[int, int]:
    try:
        index_text, count_text = value.split("/")
        index, count = int(index_text), int(count_text)
    except ValueError as exc:
        raise pytest.UsageError(f"--shard must look like I/N, got {value!r}") from exc
    if count < 1 or not 1 <= index <= count:
        raise pytest.UsageError(f"--shard {value!r}: I must be between 1 and N")
    return index, count


def shard_of(nodeid: str, count: int) -> int:
    """The 1-based slice a test belongs to."""
    return zlib.crc32(nodeid.encode()) % count + 1


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--shard", default=None, metavar="I/N", help="run only slice I (1-based) of N of the collected tests")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    value = config.getoption("--shard")
    if not value:
        return
    index, count = parse_shard(value)
    kept, dropped = [], []
    for item in items:
        (kept if shard_of(item.nodeid, count) == index else dropped).append(item)
    config.hook.pytest_deselected(items=dropped)
    items[:] = kept
