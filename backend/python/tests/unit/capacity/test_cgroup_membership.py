"""cgroup v1 file selection follows this process's own membership before any file at the controller root.

With the host's full v1 hierarchy mounted, ``<root>/memory/memory.limit_in_bytes`` is the hierarchy root's
(unlimited), and the container's own limit sits under the path ``/proc/self/cgroup`` names. cgroup v1 and
``cgroup_namespaces(7)`` define that path as relative to the mounted hierarchy root. When only the
container's own cgroup is mounted at the controller directory, no such nested path exists and the direct
file is the answer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.capacity import cgroup
from app.services.resource_governor import probe

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

GIB = 2**30
UNLIMITED = str(2**63 - 4096)


def _point(monkeypatch: pytest.MonkeyPatch, root: Path, membership: str) -> None:
    (root / "self_cgroup").write_text(membership)
    monkeypatch.setattr(cgroup, "CGROUP_ROOT", root / "cgroup")
    monkeypatch.setattr(cgroup, "PROC_SELF_CGROUP", root / "self_cgroup")
    monkeypatch.setattr(cgroup, "BOOT_ID", root / "boot_id")
    monkeypatch.setattr(probe, "_CGROUP_ROOT", root / "cgroup")
    monkeypatch.setattr(probe, "_PROC_SELF_CGROUP", root / "self_cgroup")
    monkeypatch.setattr(probe, "_BOOT_ID", root / "boot_id")
    (root / "boot_id").write_text("boot-1\n")
    probe.memory_domain_id.cache_clear()


def _limit(directory: Path, value: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "memory.limit_in_bytes"
    path.write_text(value)
    return path


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    yield
    probe.memory_domain_id.cache_clear()


def test_full_hierarchy_mount_uses_the_members_own_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "cgroup" / "memory"
    _limit(root, UNLIMITED)  # the hierarchy root's file
    _limit(root / "jobs" / "a", str(4 * GIB))
    _limit(root / "jobs" / "b", str(8 * GIB))
    _point(monkeypatch, tmp_path, "4:memory:/jobs/a\n")
    assert cgroup.memory_limit() == (4 * GIB, "v1")
    assert probe._resolve_cgroup_path("memory", "memory.limit_in_bytes", "memory.max") == root / "jobs" / "a" / "memory.limit_in_bytes"


def test_sibling_members_get_different_domain_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "cgroup" / "memory"
    _limit(root, UNLIMITED)
    _limit(root / "jobs" / "a", str(4 * GIB))
    _limit(root / "jobs" / "b", str(4 * GIB))
    ids = {}
    for name in ("a", "b"):
        _point(monkeypatch, tmp_path, f"4:memory:/jobs/{name}\n")
        ids[name] = cgroup.memory_domain_id()
        assert ids[name] == probe.memory_domain_id()
    assert ids["a"] and ids["a"] != ids["b"]


@pytest.mark.parametrize("membership", ["4:memory:/\n", "4:memory:/docker/abc123\n", ""], ids=["root", "host_namespace_subtree_mount", "no_proc_file"])
def test_container_relative_mount_keeps_the_direct_file(membership: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "cgroup" / "memory"
    _limit(root, str(2 * GIB))  # only the container's own cgroup is mounted here
    _point(monkeypatch, tmp_path, membership)
    assert cgroup.memory_limit() == (2 * GIB, "v1")
    assert probe._resolve_cgroup_path("memory", "memory.limit_in_bytes") == root / "memory.limit_in_bytes"


def test_nothing_mounted_is_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _point(monkeypatch, tmp_path, "4:memory:/jobs/a\n")
    assert cgroup.memory_limit() == (None, "none")
    assert probe._resolve_cgroup_path("memory", "memory.limit_in_bytes") is None
