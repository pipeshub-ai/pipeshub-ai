"""Driver logic that needs no parsers: selection, probe override, sampler limits, CLI guards."""

from __future__ import annotations

import dataclasses
import os
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from tests.perf.parse_bench import driver
from tests.perf.parse_bench.corpus import MIME, Entry

if TYPE_CHECKING:
    from pathlib import Path


def _entries() -> list[Entry]:
    kinds = {"digital": "pdf", "tables": "pdf", "large": "pdf", "docx": "docx", "md": "md", "json": "json",
             "scanned": "pdf"}
    return [Entry(f"{k}-{i}.{ext}", k, ext, MIME[ext], 10, "0" * 64) for k, ext in kinds.items() for i in range(2)]


class TestSelect:
    def test_cpu_heavy_has_no_light_files(self) -> None:
        picked = driver.select(_entries(), "cpu-heavy", 1, 1)
        assert picked
        assert {e.kind for e in picked} <= set(driver.MIXES["cpu-heavy"])
        assert "md" not in {e.kind for e in picked}

    def test_copies_multiply(self) -> None:
        picked = driver.select(_entries(), "io-heavy", 1, 1)
        assert sum(e.kind == "md" for e in picked) == 2 * driver.MIXES["io-heavy"]["md"]

    def test_passes_repeat_the_mix(self) -> None:
        one = driver.select(_entries(), "laptop-typical", 1, 1)
        assert len(driver.select(_entries(), "laptop-typical", 3, 1)) == 3 * len(one)

    def test_same_seed_same_order_other_seed_other_order(self) -> None:
        a = [e.path for e in driver.select(_entries(), "all", 1, 5)]
        assert a == [e.path for e in driver.select(_entries(), "all", 1, 5)]
        assert a != [e.path for e in driver.select(_entries(), "all", 1, 6)]

    def test_missing_kinds_select_nothing(self) -> None:
        only_md = [e for e in _entries() if e.kind == "md"]
        assert driver.select(only_md, "cpu-heavy", 1, 1) == []


@dataclasses.dataclass(frozen=True)
class _Snap:
    cpu_quota: float
    mem_limit_bytes: int | None
    mem_working_set_bytes: int | None
    mem_working_set_raw_bytes: int | None = None
    mem_baseline_bytes: int | None = None


class _Inner:
    def snapshot(self) -> _Snap:
        return _Snap(8.0, 32 * 1024**3, 10 * 1024**3, 10 * 1024**3, 1)


class TestTierProbe:
    def test_without_overrides_it_passes_through(self) -> None:
        snap = driver.TierProbe(_Inner(), None, None, SimpleNamespace(latest_tree_rss=5)).snapshot()
        assert snap.cpu_quota == 8.0
        assert snap.mem_limit_bytes == 32 * 1024**3

    def test_cpu_override(self) -> None:
        snap = driver.TierProbe(_Inner(), 2, None, SimpleNamespace(latest_tree_rss=5)).snapshot()
        assert snap.cpu_quota == 2.0
        assert snap.mem_limit_bytes == 32 * 1024**3

    def test_memory_override_uses_the_process_tree_and_drops_the_baseline(self) -> None:
        snap = driver.TierProbe(_Inner(), None, 4 * 1024**3, SimpleNamespace(latest_tree_rss=777)).snapshot()
        assert snap.mem_limit_bytes == 4 * 1024**3
        assert snap.mem_working_set_bytes == 777
        assert snap.mem_baseline_bytes is None

    def test_memory_override_before_the_first_sample_falls_back(self) -> None:
        snap = driver.TierProbe(_Inner(), None, 4 * 1024**3, SimpleNamespace(latest_tree_rss=0)).snapshot()
        assert snap.mem_working_set_bytes == 10 * 1024**3


class TestSamplerAbort:
    def test_stops_at_85_percent_of_the_emulated_limit(self) -> None:
        sampler = driver.TreeSampler(mem_limit=1000 * 1024**2)
        sampler.check_limits(849 * 1024**2, 10.0)
        assert sampler.abort_reason is None
        sampler.check_limits(850 * 1024**2, 10.0)
        assert "85 %" in (sampler.abort_reason or "")

    def test_no_limit_means_no_memory_abort(self) -> None:
        sampler = driver.TreeSampler(mem_limit=None)
        sampler.check_limits(10**12, 10.0)
        assert sampler.abort_reason is None

    def test_stops_when_the_host_is_nearly_out_of_memory(self) -> None:
        sampler = driver.TreeSampler(mem_limit=None)
        sampler.check_limits(1, 91.0)
        assert "host memory" in (sampler.abort_reason or "")

    def test_a_real_sample_records_this_process(self) -> None:
        sampler = driver.TreeSampler(mem_limit=None, host_limit_pct=101.0)
        sampler.sample()
        assert sampler.abort_reason is None
        assert sampler.peak_tree_rss > 0
        assert sampler.peak_threads >= 1

    def test_the_thread_can_be_started_stopped_and_joined(self) -> None:
        # ``threading.Thread`` calls its own ``_stop()`` while joining; an attribute of that name
        # broke join() with "'Event' object is not callable".
        sampler = driver.TreeSampler(mem_limit=None, host_limit_pct=101.0)
        sampler.start()
        sampler.stop()
        sampler.join(timeout=10)
        assert not sampler.is_alive()

    def test_records_a_child_process(self) -> None:
        import subprocess
        import sys

        sampler = driver.TreeSampler(mem_limit=None, host_limit_pct=101.0)
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
        try:
            sampler.sample()
        finally:
            child.kill()
            child.wait()
        assert any(w["pid"] == child.pid and w["peak_rss"] > 0 for w in sampler.per_worker())


_ENV_KEYS = ("ENABLE_PDFPLUMBER_PROCESSOR", "SECRET_KEY", "TELEMETRY_COLLECTION_FLAG")
_ENV_AT_IMPORT = {k: os.environ.get(k) for k in _ENV_KEYS}


class TestMain:
    @pytest.fixture(autouse=True)
    def _restore_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # ``driver.main`` sets these before its early returns; put each back as it was.
        for key in _ENV_KEYS:
            if key in os.environ:
                monkeypatch.setenv(key, os.environ[key])
            else:
                monkeypatch.setenv(key, "")
                monkeypatch.delenv(key)

    def test_refuses_to_start_on_a_busy_host(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(driver, "host_load", lambda: {"loadavg_1m": 9.0, "cpu_pct": 91.0, "mem_pct": 40.0})
        assert driver.main(["--corpus", str(tmp_path)]) == 3

    def test_required_cgroup_counters_missing_is_a_loud_failure(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = driver.main(["--corpus", str(tmp_path), "--cgroup", str(tmp_path), "--require-cgroup"])
        assert code == 6
        assert "memory.peak" in capsys.readouterr().err

    def test_required_cgroup_with_no_path_fails_too(self, tmp_path: Path) -> None:
        assert driver.main(["--corpus", str(tmp_path), "--cgroup", "", "--require-cgroup"]) == 6

    def test_cpus_beyond_the_host_is_an_error(self) -> None:
        with pytest.raises(SystemExit):
            driver.pin_cpus(100000)

    def test_unknown_mix_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            driver.main(["--corpus", str(tmp_path), "--mix", "nope"])


def test_serialize_dumps_a_container_and_keeps_a_docling_document_as_text() -> None:
    class Container:
        def model_dump_json(self) -> str:
            return '{"blocks": [1, 2], "block_groups": [3]}'

    dumped = driver.serialize(SimpleNamespace(block_container=Container(), raw_document=None))
    assert dumped == {"blocks": [1, 2], "block_groups": [3]}
    text = '{"texts": [1, 2, 3], "tables": [4]}'
    assert driver.serialize(SimpleNamespace(block_container=None, raw_document=text)) == text


def test_summaries_count_blocks_and_groups_for_both_shapes() -> None:
    blocks = driver.summarize_payload({"blocks": [1, 2], "block_groups": [3]})
    assert (blocks["blocks"], blocks["groups"]) == (2, 1)
    docling = driver.summarize_payload('{"texts": [1, 2, 3], "tables": [4]}')
    assert (docling["blocks"], docling["groups"]) == (3, 1)


class _FakeContainer:
    def model_dump_json(self) -> str:
        return '{"blocks": [{"id": "a"}], "block_groups": []}'


class _FakeParser:
    async def parse(self, content: bytes, name: str, config: dict) -> SimpleNamespace:
        return SimpleNamespace(block_container=_FakeContainer(), raw_document=None)


class _FakeRegistry:
    def resolve(self, mime: str, ext: str, provider: object) -> _FakeParser:
        return _FakeParser()


def _run_with_spy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, hash_in_window: bool, n_files: int = 3) -> tuple[list[float], tuple[float, float]]:
    """Run the real ``driver.run`` with fake parsers; return when hashing helpers were called and the window."""
    import argparse
    import asyncio
    import time

    from app.modules.parsers import parse_pool
    from app.modules.parsers.pdf import docling_processor, pdf_rasterizer
    from tests.perf.parse_bench import golden

    # ``run`` wires its governor into three process-wide slots; put them back afterwards so no other
    # test in this worker finds a closed governor (or a parse pool) there.
    monkeypatch.setattr(parse_pool._State, "governor", None)
    monkeypatch.setattr(docling_processor, "_resource_governor", None)
    monkeypatch.setattr(pdf_rasterizer, "_resource_governor", None)
    calls: list[float] = []
    for name in ("summarize", "digest", "canonicalize"):
        original = getattr(golden, name)

        def spy(*args, _orig=original, **kwargs):  # noqa: ANN202
            calls.append(time.monotonic())
            return _orig(*args, **kwargs)

        monkeypatch.setattr(golden, name, spy)
    monkeypatch.setattr(driver, "build_registry", lambda *a: _FakeRegistry())
    if hash_in_window:  # what the first version of the driver did
        real_serialize = driver.serialize

        def serialize_and_hash(result: object) -> object:
            payload = real_serialize(result)
            driver.summarize_payload(payload)
            return payload

        monkeypatch.setattr(driver, "serialize", serialize_and_hash)
    files = []
    for i in range(n_files):
        (tmp_path / f"n{i}.md").write_text("# t")
        files.append(Entry(f"n{i}.md", "md", "md", MIME["md"], 3, "0" * 64))
    args = argparse.Namespace(pdf_parser="pdfplumber", ocr_latency=0.0, mem_limit_gb=None, cpus=None,
                              concurrency=2, gate_timeout=30.0, cgroup="")
    sampler = driver.TreeSampler(mem_limit=None, host_limit_pct=101.0)
    raw = asyncio.run(driver.run(args, files, tmp_path, sampler))
    assert raw["ok"] == n_files
    assert len(raw["golden"]) == n_files
    return calls, raw["window"]


def test_hashing_does_not_run_inside_the_timed_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The loop-lag figures must not contain the harness's own hashing (found in a first run:
    uuid renaming held the loop for 100s of ms and was blamed on the parsers)."""
    calls, (start, end) = _run_with_spy(tmp_path, monkeypatch, hash_in_window=False)
    assert calls, "the golden hashing never ran, so this test would prove nothing"
    assert all(t >= end for t in calls), "golden work ran inside the timed window"
    assert all(t > start for t in calls)


def test_the_window_check_catches_hashing_put_back_inside(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls, (_, end) = _run_with_spy(tmp_path, monkeypatch, hash_in_window=True)
    assert any(t < end for t in calls)


def test_main_tests_leave_the_environment_as_they_found_it() -> None:
    """Placed after ``TestMain`` on purpose: a leak from it would show here."""
    assert {k: os.environ.get(k) for k in _ENV_KEYS} == _ENV_AT_IMPORT


def test_a_file_is_read_only_once_it_holds_a_concurrency_slot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reading every file at t0 puts the whole corpus in memory and a blocking read inside the timed
    window; reads must wait for the semaphore (concurrency is 2 in the helper)."""
    import asyncio
    from pathlib import Path as _Path

    reads: list[str] = []
    seen_at_first_parse: list[int] = []
    real_read = _Path.read_bytes

    def counting_read(self: _Path) -> bytes:
        reads.append(self.name)
        return real_read(self)

    async def parse(self: object, content: bytes, name: str, config: dict) -> SimpleNamespace:
        seen_at_first_parse.append(len(reads))
        await asyncio.sleep(0.05)
        return SimpleNamespace(block_container=_FakeContainer(), raw_document=None)

    monkeypatch.setattr(_Path, "read_bytes", counting_read)
    monkeypatch.setattr(_FakeParser, "parse", parse)
    _run_with_spy(tmp_path, monkeypatch, hash_in_window=False, n_files=8)
    assert seen_at_first_parse
    assert seen_at_first_parse[0] <= 2, f"{seen_at_first_parse[0]} files were read before the first parse started"


def test_stack_collector_keeps_only_the_watchdog_stacks() -> None:
    import logging

    handler = driver.StackCollector()
    logger = logging.getLogger("collector-test")
    logger.addHandler(handler)
    logger.propagate = False
    logger.warning("unrelated warning")
    logger.warning("Event loop bench has not run for 1.0s; it is executing:\n  File \"/a/b.py\", line 1, in f")
    assert len(handler.stacks) == 1
    assert "b.py" in handler.stacks[0]
