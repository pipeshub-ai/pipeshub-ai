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


# ---- limits of visible ancestors ----------------------------------------------------------------


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_v2_ancestor_limits_apply_to_an_unlimited_member(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``/batch/service`` is unlimited but ``/batch`` allows 2 CPUs and 4 GiB: the member may use no more
    (cgroup-v2 limits are hierarchical)."""
    root = tmp_path / "cgroup"
    _write(root / "batch" / "service" / "cpu.max", "max 100000")
    _write(root / "batch" / "service" / "memory.max", "max")
    _write(root / "batch" / "cpu.max", "200000 100000")
    _write(root / "batch" / "memory.max", str(4 * GIB))
    _point(monkeypatch, tmp_path, "0::/batch/service\n")
    assert cgroup.cpu_quota() == (2.0, "v2")
    assert cgroup.memory_limit() == (4 * GIB, "v2")


def test_the_smallest_limit_wins_whichever_level_holds_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "cgroup"
    _write(root / "a" / "b" / "cpu.max", "100000 100000")
    _write(root / "a" / "cpu.max", "800000 100000")
    _write(root / "a" / "b" / "memory.max", str(2 * GIB))
    _write(root / "a" / "memory.max", str(8 * GIB))
    _point(monkeypatch, tmp_path, "0::/a/b\n")
    assert cgroup.cpu_quota() == (1.0, "v2")
    assert cgroup.memory_limit() == (2 * GIB, "v2")


def test_v1_ancestor_limits_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mem, cpu = tmp_path / "cgroup" / "memory", tmp_path / "cgroup" / "cpu"
    _write(mem / "jobs" / "a" / "memory.limit_in_bytes", UNLIMITED)
    _write(mem / "jobs" / "memory.limit_in_bytes", str(6 * GIB))
    _write(mem / "memory.limit_in_bytes", UNLIMITED)
    _write(cpu / "jobs" / "a" / "cpu.cfs_quota_us", "-1")
    _write(cpu / "jobs" / "a" / "cpu.cfs_period_us", "100000")
    _write(cpu / "jobs" / "cpu.cfs_quota_us", "300000")
    _write(cpu / "jobs" / "cpu.cfs_period_us", "100000")
    _point(monkeypatch, tmp_path, "4:memory:/jobs/a\n3:cpu,cpuacct:/jobs/a\n")
    assert cgroup.memory_limit() == (6 * GIB, "v1")
    assert cgroup.cpu_quota() == (3.0, "v1")


def test_without_visible_ancestors_the_members_own_limit_is_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path / "cgroup" / "cpu.max", "300000 100000")
    _write(tmp_path / "cgroup" / "memory.max", str(3 * GIB))
    _point(monkeypatch, tmp_path, "0::/\n")
    assert cgroup.cpu_quota() == (3.0, "v2")
    assert cgroup.memory_limit() == (3 * GIB, "v2")


def test_no_limit_anywhere_is_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "cgroup"
    _write(root / "x" / "y" / "cpu.max", "max 100000")
    _write(root / "x" / "cpu.max", "max 100000")
    _write(root / "x" / "y" / "memory.max", "max")
    _point(monkeypatch, tmp_path, "0::/x/y\n")
    assert cgroup.cpu_quota() == (None, "none")
    assert cgroup.memory_limit() == (None, "none")


# ---- v2 mount layouts: the member's path first, the file at the mount root only as a fallback ----
#
# Kernel cgroup-v2 doc: the hierarchy's real root has no resource-control files; a cgroup namespace
# shows its own root cgroup as the mount root (so its files exist there) and /proc/self/cgroup gives
# the path relative to that root.


@pytest.mark.parametrize("root_files", [False, True], ids=["real_root_without_files", "root_file_present"])
def test_v2_host_mount_walks_to_the_ancestor_whatever_the_mount_root_holds(
    root_files: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "cgroup"
    if root_files:  # not what a real root has, but a reader must not stop at a file there
        _write(root / "cpu.max", "max 100000")
        _write(root / "memory.max", "max")
    _write(root / "batch" / "service" / "cpu.max", "max 100000")
    _write(root / "batch" / "service" / "memory.max", "max")
    _write(root / "batch" / "cpu.max", "200000 100000")
    _write(root / "batch" / "memory.max", str(4 * GIB))
    _point(monkeypatch, tmp_path, "0::/batch/service\n")
    assert cgroup.cpu_quota() == (2.0, "v2")
    assert cgroup.memory_limit() == (4 * GIB, "v2")


def test_v2_namespace_rooted_above_the_member_reads_the_members_own_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The mount root is the namespace root cgroup (``/batch``, unlimited); the member is ``/service`` below it with 2 CPUs."""
    root = tmp_path / "cgroup"
    _write(root / "cpu.max", "max 100000")
    _write(root / "memory.max", "max")
    _write(root / "service" / "cpu.max", "200000 100000")
    _write(root / "service" / "memory.max", str(4 * GIB))
    _point(monkeypatch, tmp_path, "0::/service\n")
    assert cgroup.cpu_quota() == (2.0, "v2")
    assert cgroup.memory_limit() == (4 * GIB, "v2")
    assert probe._resolve_cgroup_path("cpu", "cpu.max") == root / "service" / "cpu.max"
    assert probe._cgroup_v2_cpu_quota() == 2.0
    assert probe._cgroup_v2_memory()[0] == 4 * GIB
    assert cgroup.memory_domain_id() == probe.memory_domain_id() is not None


@pytest.mark.parametrize("membership", ["0::/\n", "0::/docker/abc123\n", ""], ids=["private_namespace", "subtree_mount", "no_proc_file"])
def test_v2_container_own_cgroup_mounted_at_the_root_is_the_fallback(
    membership: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "cgroup"
    _write(root / "cpu.max", "300000 100000")
    _write(root / "memory.max", str(3 * GIB))
    _point(monkeypatch, tmp_path, membership)
    assert cgroup.cpu_quota() == (3.0, "v2")
    assert cgroup.memory_limit() == (3 * GIB, "v2")
    assert probe._cgroup_v2_cpu_quota() == 3.0
    assert probe._resolve_cgroup_path("cpu", "cpu.max") == root / "cpu.max"
