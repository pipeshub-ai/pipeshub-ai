"""Driver logic that needs no parsers: selection, probe override, sampler limits, CLI guards."""

from __future__ import annotations

import dataclasses
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


class TestMain:
    def test_refuses_to_start_on_a_busy_host(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(driver, "host_load", lambda: {"loadavg_1m": 9.0, "cpu_pct": 91.0, "mem_pct": 40.0})
        assert driver.main(["--corpus", str(tmp_path)]) == 3

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


def test_hashing_does_not_run_inside_the_timed_window() -> None:
    """The loop-lag figures must not contain the harness's own hashing (found in a first run:
    uuid renaming held the loop for 100s of ms and was blamed on the parsers)."""
    import inspect

    source = inspect.getsource(driver.run)
    end_of_window = source.index("wall = time.monotonic() - t0")
    for marker in ("summarize", "golden.", "canonicalize", "digest("):
        assert marker not in source[:end_of_window], f"{marker} runs before the timed window ends"


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
