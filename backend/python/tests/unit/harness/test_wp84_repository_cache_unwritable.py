"""WP84-GITH (WP-77 open item): a repository cache the worker cannot write reads as that, never as a full disk.

A recorded stack's cache sat under a root-owned data volume. Measuring the disk created the cache folder first, the
``PermissionError`` was taken for "no disk", and the admins' Git hosts screen said the disk was almost full while it had
room. The status now measures the volume from the nearest folder that exists and reports the cache's own state apart
(``cacheUnwritable``), so the screen can say what is wrong and what to do.
"""

from __future__ import annotations

import os
import shutil
from typing import TYPE_CHECKING, NamedTuple

import pytest

from harness_core.repos.fetch import GIB, FetchConfig, RepositoryFetcher

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="root writes through any folder mode")


class _Usage(NamedTuple):
    total: int
    used: int
    free: int


class _Resolver:
    async def resolve(self, host: str) -> tuple[str, ...]:
        return ("93.184.216.34",)


@pytest.fixture
def roomy_disk(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """A 1 TB volume with 900 GB free, whatever the machine's disk holds; records each folder it was asked about."""
    asked: list[str] = []

    def usage(path: os.PathLike[str] | str) -> _Usage:
        asked.append(os.fspath(path))
        if not os.path.isdir(path):
            raise FileNotFoundError(2, "No such file or directory", os.fspath(path))
        return _Usage(1024 * GIB, 124 * GIB, 900 * GIB)

    monkeypatch.setattr(shutil, "disk_usage", usage)
    return asked


def _fetcher(cache_dir: Path) -> RepositoryFetcher:
    return RepositoryFetcher(FetchConfig(cache_dir=cache_dir), _Resolver())  # pyright: ignore[reportArgumentType]


def test_wp84_gith_1_a_cache_the_worker_cannot_create_is_unwritable_not_disk_low(
    tmp_path: Path, roomy_disk: list[str]
) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o755)
    data.chmod(0o555)
    try:
        status = _fetcher(data / "harness" / "repository-cache").status()
    finally:
        data.chmod(0o755)
    assert status["cacheUnwritable"] is True
    assert status["diskLow"] is False
    assert status["freeMb"] == 900 * 1024
    assert roomy_disk and all(os.path.isdir(path) for path in roomy_disk)


def test_wp84_gith_2_an_existing_cache_folder_it_may_not_write_is_unwritable(
    tmp_path: Path, roomy_disk: list[str]
) -> None:
    cache = tmp_path / "repository-cache"
    cache.mkdir(mode=0o700)
    cache.chmod(0o500)
    try:
        status = _fetcher(cache).status()
    finally:
        cache.chmod(0o700)
    assert status["cacheUnwritable"] is True
    assert status["diskLow"] is False


def test_wp84_gith_3_a_writable_cache_is_neither(tmp_path: Path, roomy_disk: list[str]) -> None:
    status = _fetcher(tmp_path / "repository-cache").status()
    assert status["cacheUnwritable"] is False
    assert status["diskLow"] is False


def test_wp84_gith_4_a_low_disk_under_an_unwritable_cache_still_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: _Usage(32 * GIB, 31 * GIB, 1 * GIB))
    data = tmp_path / "data"
    data.mkdir(mode=0o755)
    data.chmod(0o555)
    try:
        status = _fetcher(data / "repository-cache").status()
    finally:
        data.chmod(0o755)
    assert status["cacheUnwritable"] is True
    assert status["diskLow"] is True


class _Store:
    def __init__(self, *, fails: bool = False) -> None:
        self.written: list[tuple[str, dict[str, object]]] = []
        self.fails = fails

    async def set_config(self, key: str, value: dict[str, object]) -> bool:
        if self.fails:
            raise ConnectionError("store down")
        self.written.append((key, value))
        return True


async def test_wp84_fu3_a_worker_publishes_its_cache_state_when_it_starts(
    tmp_path: Path, roomy_disk: list[str]
) -> None:
    """WP84-FU-3: the cache's state was published only after a ``repository_fetch`` call, so an admin saw an
    unwritable cache only once a clone had been refused. The worker now publishes it when it starts."""
    from app.harness.repositories import REPOSITORY_CACHE_KEY, publish_cache_status

    data = tmp_path / "data"
    data.mkdir(mode=0o755)
    data.chmod(0o555)
    store = _Store()
    try:
        await publish_cache_status(store, _fetcher(data / "repository-cache"))
    finally:
        data.chmod(0o755)
    [(key, status)] = store.written
    assert key == REPOSITORY_CACHE_KEY
    assert status["cacheUnwritable"] is True and status["diskLow"] is False


async def test_wp84_fu3_a_start_publish_that_cannot_reach_the_store_does_not_stop_the_worker(
    tmp_path: Path, roomy_disk: list[str]
) -> None:
    from app.harness.repositories import publish_cache_status

    await publish_cache_status(_Store(fails=True), _fetcher(tmp_path / "repository-cache"))


async def test_wp84_fu3_a_cache_setting_the_worker_cannot_read_does_not_stop_its_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.harness import repositories

    def broken() -> RepositoryFetcher:
        raise ValueError("HARNESS_REPOSITORY_CACHE_MB is not a number")

    monkeypatch.setattr(repositories, "deployment_fetcher", broken)
    store = _Store()
    await repositories.publish_cache_status(store)
    assert store.written == []


def test_wp84_fu3_the_worker_publishes_before_it_claims() -> None:
    """The start publish sits in ``run_worker`` beside the other start work, before the claimer starts."""
    import inspect

    from app import harness_main

    source = inspect.getsource(harness_main.run_worker)
    assert "publish_cache_status(" in source
    assert source.index("publish_cache_status(") < source.index("claimer.start()")
