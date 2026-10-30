"""Summaries over finished runs, and the tier runner's command lines (no Docker needed)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from tests.perf.parse_bench import matrix

RUN_TIERS = Path(matrix.__file__).with_name("run_tiers.sh")


def _write(path: Path, label: str, mix: str, fps: float, status: str = "ok") -> None:
    path.write_text(json.dumps({
        "config": {"label": label, "mix": mix},
        "status": status,
        "files": {"failed": 0},
        "metrics": {
            "files_per_s": fps, "loop_lag": {"p50_ms": 1.0, "p99_ms": 20.0},
            "memory": {"peak_tree_rss_mib": 900.0}, "cpu_seconds": 5.0,
            "threads": {"peak_total": 20}, "workers": {"peak_parse_workers": 1},
        },
        "cgroup": {"memory_peak": 2 * 1024**3, "throttled_usec": 1_000_000},
    }))


def test_summarize_reports_the_median_of_three(tmp_path: Path) -> None:
    for i, fps in enumerate((1.0, 9.0, 2.0)):
        _write(tmp_path / f"T-small-cpu-heavy-r{i}.json", "T-small", "cpu-heavy", fps)
    text = matrix.summarize([tmp_path], {"T-small": 4.0})
    assert "| T-small | cpu-heavy | 2.000 |" in text
    assert "T-small/cpu-heavy=3" in text
    assert "T-small: 50 % (ok)" in text


def test_summarize_labels_the_arm_and_shows_the_spread_of_each_cell(tmp_path: Path) -> None:
    for i, fps in enumerate((1.0, 9.0, 2.0)):
        _write(tmp_path / f"T-small-io-heavy-r{i}.json", "T-small", "io-heavy", fps)
    text = matrix.summarize([tmp_path], {})
    assert "Arm: PdfPlumber arm (not yet the shipped default)" in text
    assert "| T-small | io-heavy | 3 | 2 (1-9) |" in text


def test_summarize_lists_runs_that_did_not_finish(tmp_path: Path) -> None:
    _write(tmp_path / "a.json", "T-small", "cpu-heavy", 1.0)
    _write(tmp_path / "b.json", "T-small", "io-heavy", 0.0, status="aborted: tree RSS too high")
    text = matrix.summarize([tmp_path], {})
    assert "Not finished: T-small/io-heavy: aborted" in text


def test_driver_command_passes_tier_flags_and_golden_only_once(tmp_path: Path) -> None:
    args = matrix.argparse.Namespace(
        corpus=tmp_path, label="L", passes=1, max_host_pct=85.0, cpus=2, mem_limit_gb=4.0,
        golden_check=tmp_path / "g.json", driver_args=["--ocr-latency", "0"],
    )
    first = matrix.driver_command(args, "cpu-heavy", 1, tmp_path / "o.json")
    second = matrix.driver_command(args, "cpu-heavy", 2, tmp_path / "o.json")
    assert "--golden-check" in first
    assert "--golden-check" not in second
    assert first[first.index("--cpus") + 1] == "2"
    assert first[-2:] == ["--ocr-latency", "0"]


def test_run_all_skips_existing_results(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(matrix.subprocess, "run", lambda cmd, check: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    out = tmp_path / "out"
    out.mkdir()
    (out / "L-cpu-heavy-r1.json").write_text("{}")
    ns = matrix.argparse.Namespace(
        corpus=tmp_path, out_dir=out, label="L", mixes=["cpu-heavy"], repeats=2, passes=1, cpus=0,
        mem_limit_gb=0.0, max_host_pct=85.0, golden_check=None, force=False, driver_args=[],
        busy_retries=0, busy_wait=0.0,
    )
    assert matrix.run_all(ns) == 0
    assert len(calls) == 1
    assert calls[0][calls[0].index("--out") + 1].endswith("L-cpu-heavy-r2.json")


class TestRunTiersScript:
    def _dry(self, tmp_path: Path, **env: str) -> str:
        (tmp_path / "corpus").mkdir(exist_ok=True)
        (tmp_path / "corpus" / "manifest.json").write_text("{}")
        full = {**os.environ, "BENCH_OUT": str(tmp_path / "out"), "BENCH_CORPUS": str(tmp_path / "corpus"), **env}
        done = subprocess.run(["bash", str(RUN_TIERS), "--dry-run"], capture_output=True, text=True, env=full, check=True)
        return done.stdout

    def test_cgroup_limits_per_tier(self, tmp_path: Path) -> None:
        out = self._dry(tmp_path, BENCH_TIERS="T-small:2:4 T-laptop:4:8", BENCH_REPEATS="1", BENCH_MIXES="cpu-heavy")
        assert "--cpus 2 --memory 4g --memory-swap 4g" in out
        assert "--cpus 4 --memory 8g --memory-swap 8g" in out

    def test_a_fresh_container_per_run_so_memory_peak_is_that_runs(self, tmp_path: Path) -> None:
        out = self._dry(tmp_path, BENCH_TIERS="T-small:2:4", BENCH_REPEATS="3", BENCH_MIXES="cpu-heavy io-heavy")
        assert out.count("docker run") == 6  # 2 mixes x 3 repeats, one container each
        assert out.count("--out /out/T-small-cpu-heavy-r") == 3

    def test_the_cgroup_is_private_required_and_read_from_the_container_root(self, tmp_path: Path) -> None:
        out = self._dry(tmp_path, BENCH_TIERS="T-small:1:1", BENCH_REPEATS="1", BENCH_MIXES="cpu-heavy")
        assert "--cgroupns=private" in out
        assert "--require-cgroup" in out
        assert "--cgroup /sys/fs/cgroup" in out

    def test_results_are_handed_back_to_the_invoking_user(self, tmp_path: Path) -> None:
        out = self._dry(tmp_path, BENCH_TIERS="T-small:1:1", BENCH_REPEATS="1", BENCH_MIXES="cpu-heavy")
        assert f"HOST_OWNER={os.getuid()}:{os.getgid()}" in out
        assert "chown" in out

    def test_a_missing_corpus_is_generated_in_the_image_not_with_host_python(self, tmp_path: Path) -> None:
        env = {**os.environ, "BENCH_OUT": str(tmp_path / "o"), "BENCH_CORPUS": str(tmp_path / "none"),
               "BENCH_TIERS": "T-small:1:1", "BENCH_REPEATS": "1", "BENCH_MIXES": "cpu-heavy"}
        out = subprocess.run(["bash", str(RUN_TIERS), "--dry-run"], capture_output=True, text=True, env=env, check=True).stdout
        corpus_line = next(line for line in out.splitlines() if "parse_bench.corpus" in line)
        assert corpus_line.startswith("docker run")
        assert not (tmp_path / "none").exists()

    def test_the_busy_wait_is_bounded_and_configurable(self, tmp_path: Path) -> None:
        text = RUN_TIERS.read_text()
        assert "BENCH_BUSY_RETRIES:-10" in text
        assert "RUN FAILED" in text  # a run that never starts is reported and fails the script

    def test_a_tier_that_does_not_fit_the_host_is_skipped_not_run(self, tmp_path: Path) -> None:
        out = self._dry(tmp_path, BENCH_TIERS="T-big:4096:100000")
        assert "skip T-big" in out
        assert "docker run" not in out

    def test_checkout_code_is_mounted_by_default_and_not_when_disabled(self, tmp_path: Path) -> None:
        assert ":/app/python/app:ro" in self._dry(tmp_path, BENCH_TIERS="T-small:1:1")
        assert ":/app/python/app:ro" not in self._dry(tmp_path, BENCH_TIERS="T-small:1:1", BENCH_MOUNT_APP="0")

    def test_dry_run_creates_nothing(self, tmp_path: Path) -> None:
        self._dry(tmp_path, BENCH_TIERS="T-small:1:1")
        assert not (tmp_path / "out").exists()


def test_a_busy_host_is_waited_out_then_the_run_goes_ahead(tmp_path: Path, monkeypatch) -> None:
    codes = iter([matrix.BUSY, matrix.BUSY, 0])
    calls: list[int] = []
    monkeypatch.setattr(matrix.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        matrix.subprocess, "run",
        lambda cmd, check: calls.append(1) or subprocess.CompletedProcess(cmd, next(codes)),
    )
    ns = matrix.argparse.Namespace(
        corpus=tmp_path, out_dir=tmp_path / "o", label="L", mixes=["cpu-heavy"], repeats=1, passes=1, cpus=0,
        mem_limit_gb=0.0, max_host_pct=85.0, golden_check=None, force=False, driver_args=[],
        busy_retries=5, busy_wait=0.0,
    )
    assert matrix.run_all(ns) == 0
    assert len(calls) == 3


def test_a_host_that_stays_busy_is_reported_not_hung(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(matrix.time, "sleep", lambda s: None)
    monkeypatch.setattr(matrix.subprocess, "run", lambda cmd, check: subprocess.CompletedProcess(cmd, matrix.BUSY))
    ns = matrix.argparse.Namespace(
        corpus=tmp_path, out_dir=tmp_path / "o", label="L", mixes=["cpu-heavy"], repeats=1, passes=1, cpus=0,
        mem_limit_gb=0.0, max_host_pct=85.0, golden_check=None, force=False, driver_args=[],
        busy_retries=2, busy_wait=0.0,
    )
    assert matrix.run_all(ns) == matrix.BUSY
