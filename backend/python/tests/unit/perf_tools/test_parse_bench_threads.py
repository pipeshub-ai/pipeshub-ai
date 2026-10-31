"""Harness pieces for the lane/thread split measurement: flags, per-worker report, cores used."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import TYPE_CHECKING

import pytest

from tests.perf.parse_bench import driver, proc_cpu, report, worker_probe
from tests.perf.parse_bench.corpus import MIME, Entry

if TYPE_CHECKING:
    from pathlib import Path

MIB = report.MIB


class TestOnnxThreadsFlag:
    def test_sets_the_variable_the_workers_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PDF_TABLE_MODEL_THREADS", raising=False)
        driver.apply_onnx_threads(2)
        assert os.environ["PDF_TABLE_MODEL_THREADS"] == "2"

    def test_not_given_leaves_it_alone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PDF_TABLE_MODEL_THREADS", "3")
        driver.apply_onnx_threads(None)
        assert os.environ["PDF_TABLE_MODEL_THREADS"] == "3"

    def test_the_flag_parses_and_rejects_zero(self) -> None:
        assert driver.parser().parse_args(["--corpus", "c", "--onnx-threads", "4"]).onnx_threads == 4
        assert driver.parser().parse_args(["--corpus", "c"]).onnx_threads is None
        with pytest.raises(SystemExit):
            driver.parser().parse_args(["--corpus", "c", "--onnx-threads", "0"])

    def test_the_table_model_reads_what_the_flag_sets(self) -> None:
        """The flag is only worth anything while ``table_detector`` reads this very variable."""
        import inspect

        from app.modules.parsers.pdf import table_detector

        assert 'PDF_TABLE_MODEL_THREADS' in inspect.getsource(table_detector)


class TestReserveEmbeddingFlag:
    def test_default_off(self) -> None:
        assert driver.parser().parse_args(["--corpus", "c"]).reserve_embedding is False
        assert driver.parser().parse_args(["--corpus", "c", "--reserve-embedding"]).reserve_embedding is True

    def test_reaches_the_governor_and_lowers_the_heavy_ceiling(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import argparse
        import asyncio

        from app.modules.parsers import parse_pool
        from app.modules.parsers.pdf import docling_processor, pdf_rasterizer

        monkeypatch.setattr(parse_pool._State, "governor", None)
        monkeypatch.setattr(docling_processor, "_resource_governor", None)
        monkeypatch.setattr(pdf_rasterizer, "_resource_governor", None)
        monkeypatch.setattr(driver, "build_registry", lambda *a: None)

        def heavy(reserve: bool) -> float:
            args = argparse.Namespace(pdf_parser="pdfplumber", ocr_latency=0.0, mem_limit_gb=None, cpus=8, scan_route="ocr",
                                      concurrency=2, gate_timeout=30.0, cgroup="", reserve_embedding=reserve,
                                      no_probe=True, probe_only=0)
            sampler = driver.TreeSampler(mem_limit=None, host_limit_pct=101.0)
            raw = asyncio.run(driver.run(args, [], tmp_path, sampler))
            return raw["ceilings"]["heavy_parse"]

        assert heavy(True) < heavy(False)


class TestSelectOnlyPath:
    def _entries(self) -> list[Entry]:
        return [Entry(f"scanned/{n}.pdf", "scanned", "pdf", MIME["pdf"], 10, "0" * 64) for n in ("a60", "b300")]

    def test_keeps_the_named_file(self) -> None:
        picked = driver.select(self._entries(), "scanned", 1, 1, "b300")
        assert [e.path for e in picked] == ["scanned/b300.pdf"]

    def test_empty_text_keeps_all(self) -> None:
        assert len(driver.select(self._entries(), "scanned", 1, 1)) == 2

    def test_single_kind_mixes_exist(self) -> None:
        for name, kind in (("docx", "docx"), ("digital", "digital"), ("scanned", "scanned"), ("tables", "tables"), ("figures", "figures")):
            assert driver.MIXES[name] == {kind: 1}


class TestRssProfile:
    def test_growth_after_the_mark(self) -> None:
        series = [(0.0, 100 * MIB), (10.0, 300 * MIB), (20.0, 400 * MIB), (25.0, 450 * MIB), (35.0, 700 * MIB)]
        out = driver.rss_profile(series)
        assert out["at_20s"] == 400.0
        assert out["at_30s"] == 450.0
        assert out["peak"] == 700.0
        assert out["growth_after_20s"] == 300.0

    def test_flat_after_the_mark_is_zero_growth(self) -> None:
        series = [(0.0, 100 * MIB), (20.0, 500 * MIB), (30.0, 400 * MIB)]
        assert driver.rss_profile(series)["growth_after_20s"] == 0.0

    def test_a_short_life_has_no_growth_and_no_later_marks(self) -> None:
        out = driver.rss_profile([(0.0, 100 * MIB), (5.0, 200 * MIB)])
        assert out["growth_after_20s"] == 0.0
        assert "at_10s" not in out
        assert out["peak"] == 200.0

    def test_empty(self) -> None:
        assert driver.rss_profile([]) == {}


class TestWorkerReport:
    def test_joins_probe_answers_by_pid(self) -> None:
        workers = [
            {"pid": 7, "peak_threads": 19, "peak_rss": 600 * MIB, "cpu_s": 12.34, "series": [(0.0, 600 * MIB)]},
            {"pid": 8, "peak_threads": 13, "peak_rss": 500 * MIB, "cpu_s": 1.0, "series": []},
        ]
        out = driver.worker_report(workers, [{"pid": 7, "cv2_threads": 4, "cv2_cpus": 4}])
        assert out[0]["cv2_threads"] == 4
        assert out[0]["peak_threads"] == 19
        assert out[0]["peak_rss_mib"] == 600.0
        assert out[1]["cv2_threads"] is None  # no answer from that worker

    def test_the_sampler_keeps_cpu_and_series_for_a_worker_role(self) -> None:
        sampler = driver.TreeSampler(mem_limit=None, host_limit_pct=101.0)
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
        sampler._role = lambda proc, roles: "parse_worker" if proc.pid == child.pid else "parent"  # type: ignore[method-assign]
        try:
            sampler.sample()
            sampler.sample()
        finally:
            child.kill()
            child.wait()
        out = next(w for w in sampler.per_worker() if w["pid"] == child.pid)
        assert out["cpu_s"] >= 0.0
        assert out["series"]


def test_worker_probe_reports_opencv_threads_and_affinity() -> None:
    out = worker_probe.probe(0.0)
    assert out["pid"] == os.getpid()
    assert out["cv2_threads"] >= 1
    assert out["affinity"] == len(os.sched_getaffinity(0))
    assert out["os_threads"] >= 1


class TestParseStat:
    def test_plain(self) -> None:
        line = "12 (python) S 3 12 12 0 -1 4194560 100 0 0 0 40 2 0 0 20 0 5 0 99 1 2 3"
        assert proc_cpu.parse_stat(line) == (3, 42)

    def test_name_with_spaces_and_parentheses(self) -> None:
        line = "12 (a) b (c)) S 3 12 12 0 -1 0 0 0 0 0 7 5 0 0 20 0 1 0 1 1 1 1"
        assert proc_cpu.parse_stat(line) == (3, 12)

    @pytest.mark.parametrize("text", ["", "12 python S 3", "12 (x) S 3 4", "12 (x) S 3 " + " ".join(["z"] * 20)])
    def test_garbage_is_none(self, text: str) -> None:
        assert proc_cpu.parse_stat(text) is None


def _fake_proc(root: Path, procs: dict[int, tuple[int, int]]) -> None:
    for pid, (ppid, ticks) in procs.items():
        d = root / str(pid)
        d.mkdir(exist_ok=True)
        (d / "stat").write_text(f"{pid} (p) S {ppid} 1 1 0 -1 0 0 0 0 0 {ticks} 0 0 0 20 0 1 0 1 1 1 1")
    (root / "self").mkdir(exist_ok=True)  # non-numeric entries are skipped


class TestDescendantCpu:
    def test_counts_only_the_root_and_its_descendants(self, tmp_path: Path) -> None:
        hz = proc_cpu.TICKS_PER_SECOND
        _fake_proc(tmp_path, {10: (1, hz), 11: (10, 2 * hz), 12: (11, 3 * hz), 20: (1, 100 * hz)})
        acct = proc_cpu.DescendantCpu(root=10, proc=tmp_path)
        acct.sample()
        assert acct.total_seconds() == pytest.approx(6.0)

    def test_an_exited_process_keeps_its_cpu(self, tmp_path: Path) -> None:
        hz = proc_cpu.TICKS_PER_SECOND
        _fake_proc(tmp_path, {10: (1, hz), 11: (10, 4 * hz)})
        acct = proc_cpu.DescendantCpu(root=10, proc=tmp_path)
        acct.sample()
        (tmp_path / "11" / "stat").unlink()
        (tmp_path / "11").rmdir()
        acct.sample()
        assert acct.total_seconds() == pytest.approx(5.0)

    def test_window_difference_and_cores(self, tmp_path: Path) -> None:
        hz = proc_cpu.TICKS_PER_SECOND
        _fake_proc(tmp_path, {10: (1, hz), 11: (10, hz)})
        acct = proc_cpu.DescendantCpu(root=10, proc=tmp_path)
        acct.sample()
        before = acct.total_seconds()
        _fake_proc(tmp_path, {10: (1, 3 * hz), 11: (10, 7 * hz)})  # +2 s and +6 s in the window
        acct.sample()
        used = acct.total_seconds() - before
        assert used == pytest.approx(8.0)
        assert proc_cpu.cores_used(used, 4.0) == pytest.approx(2.0)

    def test_busy_counts_processes_that_advanced(self, tmp_path: Path) -> None:
        hz = proc_cpu.TICKS_PER_SECOND
        _fake_proc(tmp_path, {10: (1, 0), 11: (10, 0), 12: (10, 0)})
        acct = proc_cpu.DescendantCpu(root=10, proc=tmp_path)
        acct.sample()
        _fake_proc(tmp_path, {10: (1, 0), 11: (10, hz), 12: (10, hz)})
        acct.sample()
        assert acct.busy_peak == 2
        assert acct.mean_busy() == pytest.approx(1.0)  # 0 busy on tick 1, 2 on tick 2

    def test_cores_used_with_no_wall_time(self) -> None:
        assert proc_cpu.cores_used(5.0, 0.0) == 0.0

    def test_sees_a_real_child_burning_cpu_after_it_exits(self) -> None:
        acct = proc_cpu.DescendantCpu()
        child = subprocess.Popen([sys.executable, "-c", "import time\nt=time.process_time()\nwhile time.process_time()-t<0.4: pass"])
        deadline = time.monotonic() + 10
        while child.poll() is None and time.monotonic() < deadline:
            acct.sample()
            time.sleep(0.05)
        child.wait()
        assert acct.total_seconds() >= 0.3


class TestScanRoute:
    def test_ocr_is_the_default_and_layout_swaps_in_the_plumber(self) -> None:
        assert driver.parser().parse_args(["--corpus", "c"]).scan_route == "ocr"
        assert driver.scan_handler("ocr", "plumber", "ocr") == "ocr"
        assert driver.scan_handler("layout", "plumber", "ocr") == "plumber"

    def test_unknown_route_is_an_error(self) -> None:
        with pytest.raises(ValueError):
            driver.scan_handler("nope", 1, 2)


def test_the_sampler_keeps_an_rss_series_for_the_parent_too() -> None:
    sampler = driver.TreeSampler(mem_limit=None, host_limit_pct=101.0)
    sampler.sample()
    sampler.sample()
    series = sampler.procs[sampler._me.pid]["series"]
    assert len(series) == 2
    assert series[0][0] <= series[1][0]


def test_a_child_met_before_its_parent_is_still_a_raster_child(monkeypatch: pytest.MonkeyPatch) -> None:
    """The process list is not ordered parent first; a raster child seen first was labelled a pool child."""
    sampler = driver.TreeSampler(mem_limit=None, host_limit_pct=101.0)

    class Proc:
        def __init__(self, pid: int, ppid: int, cmd: list[str]) -> None:
            self.pid, self._ppid, self._cmd = pid, ppid, cmd

        def ppid(self) -> int:
            return self._ppid

        def cmdline(self) -> list[str]:
            return self._cmd

    worker = Proc(901, sampler._me.pid, ["python", "-m", "app.modules.parsers.parse_worker", "5", "6"])
    child = Proc(902, 901, ["python", "-c", "from multiprocessing.spawn import spawn_main"])
    monkeypatch.setattr(sampler._ps, "Process", lambda pid: worker if pid == 901 else None)
    assert sampler._role(child, {}) == "raster"  # the worker is not in parent_roles yet
    assert sampler._role(worker, {}) == "parse_worker"
