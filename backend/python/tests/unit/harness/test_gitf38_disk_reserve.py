"""GITF-38 (WP-63): the repository cache keeps a bounded reserve of free disk.

A clone is refused only when the free space left after it (at the cap size, or the known size) would fall below
reserve = clamp(5% of the volume, 2 GB, 20 GB), or ``HARNESS_REPO_CACHE_MIN_FREE_MB`` when the deployment sets one. A
flat share would hold back 100 GB of a 1 TB server and leave a small VM too little. The admin warning shows below twice
the reserve.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.harness.repositories import ENV_MIN_FREE_MB, fetch_config_from_env
from harness_core.repos.fetch import (
    GIB,
    MIB,
    FetchConfig,
    RepositoryFetcher,
    RepositoryRefused,
    disk_reserve,
    room_for,
)

if TYPE_CHECKING:
    from pathlib import Path


class _Resolver:
    async def resolve(self, host: str) -> tuple[str, ...]:
        return ("93.184.216.34",)


def _fetcher(tmp_path: Path, total: int, free: int, *, min_free_mb: int | None = None) -> RepositoryFetcher:
    config = FetchConfig(
        cache_dir=tmp_path / "cache", min_free_bytes=None if min_free_mb is None else min_free_mb * MIB
    )
    fetcher = RepositoryFetcher(config, _Resolver())  # pyright: ignore[reportArgumentType]
    fetcher.disk_usage = lambda _path: (total, free)
    return fetcher


def test_gitf38_the_reserve_is_five_percent_bounded_by_2_and_20_gb() -> None:
    assert disk_reserve(256 * GIB) == int(256 * GIB * 0.05)
    assert disk_reserve(1024 * GIB) == 20 * GIB
    assert disk_reserve(32 * GIB) == 2 * GIB
    assert disk_reserve(1024 * GIB, min_free_bytes=5 * GIB) == 5 * GIB


@pytest.mark.parametrize(
    ("name", "total_gb", "free_gb", "allowed", "warned"),
    [
        ("a 256 GB laptop with 40 GB free", 256, 40, True, False),
        ("a 256 GB laptop with 20 GB free", 256, 20, True, True),
        ("a 256 GB laptop with 13 GB free", 256, 13, False, True),
        ("a 1 TB server at 9% free", 1024, 92, True, False),
        ("a 32 GB VM nearly full", 32, 2.3, False, True),
    ],
)
def test_gitf38_laptop_to_server(tmp_path: Path, name: str, total_gb: float, free_gb: float, allowed: bool, warned: bool) -> None:
    total, free = int(total_gb * GIB), int(free_gb * GIB)
    fetcher = _fetcher(tmp_path, total, free)
    assert room_for(total, free, 500 * MIB) is allowed, name
    assert fetcher.disk_low() is warned, name
    if allowed:
        fetcher.check_disk(500 * MIB)
    else:
        with pytest.raises(RepositoryRefused) as refused:
            fetcher.check_disk(500 * MIB)
        assert refused.value.reason == "repository_disk_low"
        assert "GB" in refused.value.message and "{" not in refused.value.message
    status = fetcher.status()
    assert status["freeMb"] == free // MIB and status["reserveMb"] == disk_reserve(total) // MIB


def test_gitf38_a_known_size_is_counted_rather_than_the_cap(tmp_path: Path) -> None:
    total, free = 32 * GIB, int(2.4 * GIB)
    fetcher = _fetcher(tmp_path, total, free)
    with pytest.raises(RepositoryRefused):
        fetcher.check_disk(500 * MIB)
    fetcher.check_disk(100 * MIB)


def test_gitf38_the_deployment_override(tmp_path: Path) -> None:
    total, free = 1024 * GIB, 30 * GIB
    assert room_for(total, free, 500 * MIB)
    tight = _fetcher(tmp_path, total, free, min_free_mb=40 * 1024)
    with pytest.raises(RepositoryRefused):
        tight.check_disk(500 * MIB)
    assert fetch_config_from_env({ENV_MIN_FREE_MB: "4096"}).min_free_bytes == 4096 * MIB
    assert fetch_config_from_env({}).min_free_bytes is None
    # As every HARNESS_REPO_* setting: one that cannot be read is logged and the default reserve applies.
    assert fetch_config_from_env({ENV_MIN_FREE_MB: "lots"}).min_free_bytes is None
