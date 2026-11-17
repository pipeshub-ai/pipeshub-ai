"""Tests for the live limit's readers, on fake /proc trees."""

from __future__ import annotations

import errno
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent_loop_lib.sandbox.host_reading import (
    CgroupReader,
    HostReader,
    boot_id_hash,
    parse_meminfo,
    parse_proc_stat_cpu,
)

GiB = 2**30

MEMINFO = "MemTotal:       16384000 kB\nMemFree:  100 kB\nMemAvailable:    8192000 kB\n"


def _proc(tmp_path: Path, *, meminfo: str = MEMINFO, stat: str | None = None,
          cpu_psi: str | None = None, mem_psi: str | None = None) -> Path:
    proc = tmp_path / "proc"
    (proc / "pressure").mkdir(parents=True)
    (proc / "meminfo").write_text(meminfo)
    if stat is not None:
        (proc / "stat").write_text(stat)
    if cpu_psi is not None:
        (proc / "pressure" / "cpu").write_text(cpu_psi)
    if mem_psi is not None:
        (proc / "pressure" / "memory").write_text(mem_psi)
    return proc


class TestParsers:
    def test_meminfo_is_parsed(self) -> None:
        assert parse_meminfo(MEMINFO) == (16384000 * 1024, 8192000 * 1024)

    def test_missing_mem_available_is_none(self) -> None:
        assert parse_meminfo("MemTotal: 1024 kB\n") == (1024 * 1024, None)

    def test_busy_excludes_idle_and_iowait(self) -> None:
        assert parse_proc_stat_cpu("cpu  100 0 0 900 50 0 0 0 7 7\ncpu0 1 1 1 1 1\n") == (100, 1050)

    def test_junk_stat_is_none(self) -> None:
        assert parse_proc_stat_cpu("cpu a b c d e\n") is None
        assert parse_proc_stat_cpu("") is None


class TestHostReader:
    def test_util_is_the_busy_delta(self, tmp_path: Path) -> None:
        proc = _proc(tmp_path, stat="cpu  100 0 0 900 0 0 0 0\n")
        reader = HostReader(proc)
        assert reader.read().cpu_util is None
        (proc / "stat").write_text("cpu  150 0 0 950 0 0 0 0\n")
        sample = reader.read()
        assert sample.cpu_util == pytest.approx(0.5)
        assert sample.cpu_counters == (150, 1100)
        assert sample.mem_total == 16384000 * 1024
        assert sample.mem_available == 8192000 * 1024
        assert sample.source == "host"

    def test_counter_reset_gives_none(self, tmp_path: Path) -> None:
        proc = _proc(tmp_path, stat="cpu  150 0 0 950 0 0 0 0\n")
        reader = HostReader(proc)
        reader.read()
        (proc / "stat").write_text("cpu  100 0 0 900 0 0 0 0\n")
        assert reader.read().cpu_util is None

    def test_psi_is_parsed_as_a_fraction(self, tmp_path: Path) -> None:
        proc = _proc(
            tmp_path,
            cpu_psi="some avg10=12.50 avg60=1.00 avg300=0.00 total=1\nfull avg10=0.00 avg60=0 avg300=0 total=0\n",
            mem_psi="some avg10=9.00 avg60=0 avg300=0 total=1\nfull avg10=3.00 avg60=0 avg300=0 total=1\n",
        )
        sample = HostReader(proc).read()
        assert sample.cpu_psi_some_avg10 == pytest.approx(0.125)
        assert sample.mem_psi_full_avg10 == pytest.approx(0.03)

    def test_missing_psi_is_none(self, tmp_path: Path) -> None:
        sample = HostReader(_proc(tmp_path)).read()
        assert sample.cpu_psi_some_avg10 is None and sample.mem_psi_full_avg10 is None
        assert sample.mem_total is not None

    def test_unsupported_read_is_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        proc = _proc(tmp_path, cpu_psi="some avg10=1.00\n")
        original = Path.read_text

        def read_text(self: Path, *args: object, **kwargs: object) -> str:
            if self.name == "cpu":
                raise OSError(errno.EOPNOTSUPP, "not supported")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", read_text)
        assert HostReader(proc).read().cpu_psi_some_avg10 is None

    def test_memory_reads_only_meminfo(self, tmp_path: Path) -> None:
        assert HostReader(_proc(tmp_path)).memory() == (8192000 * 1024, 16384000 * 1024)

    def test_no_proc_gives_an_empty_sample(self, tmp_path: Path) -> None:
        sample = HostReader(tmp_path / "missing").read()
        assert (sample.mem_total, sample.mem_available, sample.cpu_util) == (None, None, None)


class TestCgroupReader:
    def test_maps_limit_and_raw_working_set(self) -> None:
        snap = SimpleNamespace(
            mem_limit_bytes=10 * GiB, mem_working_set_raw_bytes=4 * GiB, mem_working_set_bytes=3 * GiB,
            cpu_utilisation=0.4, cpu_pressure=0.1,
        )
        reader = CgroupReader(lambda: SimpleNamespace(snapshot=lambda: snap))
        sample = reader.read()
        assert (sample.mem_total, sample.mem_available) == (10 * GiB, 6 * GiB)
        assert (sample.cpu_util, sample.cpu_psi_some_avg10, sample.source) == (0.4, 0.1, "cgroup")
        assert reader.memory() == (6 * GiB, 10 * GiB)

    def test_a_failing_probe_gives_an_empty_sample(self) -> None:
        def boom() -> None:
            raise RuntimeError("x")

        reader = CgroupReader(lambda: SimpleNamespace(snapshot=boom))
        assert reader.read().mem_available is None
        assert reader.memory() == (None, None)


def test_boot_id_hash_matches_the_proxy(tmp_path: Path) -> None:
    from app.sandbox.docker_proxy import boot_id_hash as proxy_hash

    path = tmp_path / "sys" / "kernel" / "random"
    path.mkdir(parents=True)
    (path / "boot_id").write_text("0f1e2d3c-aaaa-bbbb-cccc-000011112222\n")
    digest = boot_id_hash(tmp_path)
    assert digest is not None and len(digest) == 32
    assert digest == proxy_hash(str(path / "boot_id"))
    assert boot_id_hash(tmp_path / "missing") is None
