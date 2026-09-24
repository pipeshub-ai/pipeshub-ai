"""Unit tests for the scale and stress runs.

The runs themselves need a stack. What can be checked here is everything that
would quietly produce wrong numbers: batched generation that drifts from the
whole-corpus one, memory that grows with the corpus, a trend that misses a
slowdown, and overload verdicts that call a lost file a pass.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import tracemalloc
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "perf"))

import bench_scale  # noqa: E402
import bench_stress  # noqa: E402
import compare  # noqa: E402
from corpus import generate_corpus, iter_corpus_batches, plan_corpus  # noqa: E402
from scale_metrics import (  # noqa: E402
    Sample,
    Verdict,
    drift,
    latency_windows,
    overload_verdicts,
    render_verdicts,
    throughput_windows,
)

pytestmark = pytest.mark.unit

TEXT_KINDS = ("txt", "md")  # fast to render, and enough to compare the two paths


class FakeState:
    """The run state the benchmarks read, without a stack to fill it."""

    def __init__(self) -> None:
        self.uploaded_at: dict[str, float] = {}
        self.file_of: dict[str, object] = {}
        self.finished_at: dict[str, float] = {}
        self.status: dict[str, str] = {}
        self.reason: dict[str, str] = {}
        self.poll_errors = 0
        self.ended_at = 0.0
        self.stopped_early = ""
        self.upload_failures: list[dict[str, str]] = []
        self.lock = threading.Lock()


def seeded_state(count: int, *, t0: float = 0.0, seconds_each: float = 30.0,
                 status: str = "COMPLETED") -> FakeState:
    state = FakeState()
    for i in range(count):
        record = f"rec-{i}"
        state.uploaded_at[record] = t0 + i
        state.finished_at[record] = t0 + i + seconds_each
        state.status[record] = status
    state.ended_at = t0 + count + seconds_each
    return state


# --- the corpus, in batches ------------------------------------------------


def test_batches_are_the_same_corpus_as_one_go() -> None:
    whole = generate_corpus(60, seed=7, salt="x", kinds=TEXT_KINDS)
    batched = [f for c in iter_corpus_batches(60, 7, seed=7, salt="x", kinds=TEXT_KINDS) for f in c.files]

    assert [f.name for f in whole.files] == [f.name for f in batched]
    assert [f.folder for f in whole.files] == [f.folder for f in batched]
    assert [f.content for f in whole.files] == [f.content for f in batched]


def test_every_batch_carries_the_whole_folder_tree() -> None:
    plan = plan_corpus(60, seed=7, kinds=TEXT_KINDS)
    for batch in iter_corpus_batches(60, 7, seed=7, kinds=TEXT_KINDS, plan=plan):
        # A file late in the run can sit in a folder planned at the start.
        assert batch.folders == plan.folders


def test_a_plan_describes_the_corpus_without_building_it() -> None:
    plan = plan_corpus(500, seed=7, kinds=TEXT_KINDS)
    described = plan.describe()

    assert described["docs"] == 500
    assert set(described["by_kind"]) <= set(TEXT_KINDS)
    assert described["planned_bytes"] > 0
    assert all(not hasattr(e, "content") for e in plan.entries)


def test_a_file_is_the_same_file_however_many_were_asked_for() -> None:
    """Same seed, same file 7 — whether the plan is 100 files or 1000."""
    small = plan_corpus(100, seed=1337, kinds=TEXT_KINDS)
    large = plan_corpus(1000, seed=1337, kinds=TEXT_KINDS)

    def identity(entries: object) -> list[tuple]:
        return [(e.index, e.name, e.kind, e.target_bytes) for e in entries]

    assert identity(small.entries) == identity(large.entries[:100])
    # Content too, not just the plan.
    small_files = generate_corpus(20, seed=5, kinds=TEXT_KINDS).files
    large_files = generate_corpus(400, seed=5, kinds=TEXT_KINDS).files[:20]
    assert [f.content for f in small_files] == [f.content for f in large_files]
    # The folder tree is the documented exception: it grows with the corpus.
    assert len(large.folders) > len(small.folders)


def test_a_plan_and_a_clashing_seed_are_refused() -> None:
    plan = plan_corpus(10, seed=7, kinds=TEXT_KINDS)

    with pytest.raises(ValueError, match="does not match the plan"):
        list(iter_corpus_batches(10, 5, seed=99, plan=plan))

    # Without a seed, the plan's own seed is what gets rendered.
    assert next(iter(iter_corpus_batches(10, 5, plan=plan))).seed == 7


def test_batch_size_must_be_at_least_one() -> None:
    with pytest.raises(ValueError, match="batch_size"):
        list(iter_corpus_batches(10, 0, seed=1, kinds=TEXT_KINDS))


def test_memory_stays_flat_however_many_documents_are_asked_for() -> None:
    """The point of batching: 4x the documents must not mean 4x the memory."""
    def peak_batched(docs: int) -> int:
        tracemalloc.reset_peak()
        for batch in iter_corpus_batches(docs, 25, seed=5, kinds=TEXT_KINDS):
            sum(len(f.content) for f in batch.files)
        return tracemalloc.get_traced_memory()[1]

    tracemalloc.start()
    try:
        small = peak_batched(500)
        large = peak_batched(2000)
        tracemalloc.reset_peak()
        whole = generate_corpus(2000, seed=5, kinds=TEXT_KINDS)
        assert whole.total_bytes > 0
        whole_peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert large < small * 2, f"batched memory grew {large / small:.1f}x for 4x the documents"
    assert large < whole_peak / 2, "batching should cost far less than building the corpus in one go"


# --- the shape of a long run ----------------------------------------------


def test_throughput_is_reported_per_slice_including_an_idle_one() -> None:
    samples = [
        Sample(0, 0, 0), Sample(60, 100, 60),      # 60 in the first minute
        Sample(120, 200, 120), Sample(180, 300, 120),  # then nothing at all
    ]
    windows = throughput_windows(samples, windows=3)

    assert [w["records_finished"] for w in windows] == [60, 60, 0]
    assert windows[0]["records_per_minute"] == 60
    assert windows[-1]["records_per_minute"] == 0
    assert windows[-1]["backlog_at_end"] == 180


def test_latency_is_reported_per_slice() -> None:
    finished = [(10, 5.0), (20, 5.0), (80, 50.0), (100, 70.0)]
    windows = latency_windows(finished, windows=2)

    assert windows[0]["p50_seconds"] == 5.0
    assert windows[-1]["p50_seconds"] == 60.0


def test_a_run_that_slowed_to_a_crawl_is_called_out() -> None:
    # 600 records in the first ten minutes, 100 in the next, with files queued
    # the whole time — so the slowdown is real, not the run finishing up.
    samples = [Sample(0, 1000, 0), Sample(600, 1000, 600), Sample(1200, 1000, 700)]

    verdict = drift(throughput_windows(samples, windows=2), latency_windows([]), samples)

    assert verdict["steady"] is False
    assert verdict["throughput_change"] < -0.3
    joined = " ".join(verdict["notes"])
    assert "slowed while files were still queued" in joined and "records a minute" in joined


def test_a_run_finishing_its_queue_is_not_called_a_slowdown() -> None:
    """The tail of any completed run is idle; that must not read as a regression."""
    samples = [Sample(0, 400, 0), Sample(60, 400, 200), Sample(120, 400, 400), Sample(180, 400, 400)]

    verdict = drift(throughput_windows(samples, windows=3), latency_windows([]), samples)

    assert verdict["steady"] is True, verdict["notes"]


def test_a_queue_that_built_up_and_drained_still_explains_the_wait() -> None:
    """The case the check exists for: a run ends at zero backlog however deep it got."""
    drained = [Sample(0, 0, 0), Sample(600, 1100, 0), Sample(1200, 1100, 1100)]
    latency = latency_windows([(60, 30.0), (1100, 300.0)], windows=2)

    verdict = drift(throughput_windows(drained, windows=2), latency, drained)

    assert verdict["latency_p50_change"] > 0.5, "the wait did grow"
    assert not any("took longer" in n for n in verdict["notes"]), (
        "a queue that peaked at 1100 explains the wait, even though it drained by the end"
    )


def test_files_waiting_behind_a_growing_queue_are_not_called_slower() -> None:
    """Queueing explains a longer time-to-indexed; only flag it when it does not."""
    growing = [Sample(0, 100, 0), Sample(600, 900, 200), Sample(1200, 1800, 400)]
    latency = latency_windows([(60, 20.0), (1100, 200.0)], windows=2)

    assert drift(throughput_windows(growing, windows=2), latency, growing)["latency_p50_change"] > 0.5
    assert not any("took longer" in n for n in drift(throughput_windows(growing, 2), latency, growing)["notes"])

    steady_queue = [Sample(0, 100, 0), Sample(600, 300, 200), Sample(1200, 500, 400)]
    notes = drift(throughput_windows(steady_queue, windows=2), latency, steady_queue)["notes"]
    assert any("took longer" in n for n in notes)


def test_a_steady_run_says_so() -> None:
    samples = [Sample(i * 60, i * 100, i * 100, None, 500.0) for i in range(5)]
    throughput = throughput_windows(samples, windows=4)
    latency = latency_windows([(i * 60, 30.0) for i in range(1, 5)], windows=4)

    verdict = drift(throughput, latency, samples)

    assert verdict["steady"] is True
    assert verdict["notes"] == []
    assert verdict["memory"]["growth_mb"] == 0


def test_memory_that_climbs_all_run_is_called_out() -> None:
    samples = [Sample(i * 600, i * 100, i * 100, None, 400.0 + i * 300) for i in range(5)]
    verdict = drift(throughput_windows(samples), latency_windows([]), samples)

    assert verdict["steady"] is False
    note = " ".join(verdict["notes"])
    assert "memory grew" in note and "leak" in note
    assert verdict["memory"]["growth_mb"] == 1200
    assert verdict["memory"]["growth_mb_per_hour"] == 1800


def test_memory_is_not_judged_when_it_was_never_measured() -> None:
    samples = [Sample(i * 60, i * 10, i * 10) for i in range(4)]
    verdict = drift(throughput_windows(samples), latency_windows([]), samples)

    assert verdict["memory"]["growth_mb"] is None
    assert verdict["steady"] is True


# --- overload --------------------------------------------------------------


def _verdict(verdicts: list[Verdict], fragment: str) -> Verdict:
    return next(v for v in verdicts if fragment in v.check)


def test_overload_passes_when_refusals_are_explicit_and_nothing_is_lost() -> None:
    verdicts = overload_verdicts(
        attempted=500, uploaded=480, upload_failures=20, listed=480, terminal=480,
        peak_backlog=300, recovery_seconds=120.0, rejected=20,
    )

    assert all(v.passed for v in verdicts)
    assert "asking us to slow down" in _verdict(verdicts, "accepted or refused").detail


def test_a_run_where_every_upload_was_refused_recovers_immediately() -> None:
    """Nothing was accepted, so there is no backlog to clear — that is an answer."""
    assert bench_stress.recovery_seconds([Sample(12, 0, 0)], 12.0) == 0

    verdicts = overload_verdicts(
        attempted=100, uploaded=0, upload_failures=100, listed=0, terminal=0,
        peak_backlog=0, recovery_seconds=0.0, rejected=100,
    )

    assert all(v.passed for v in verdicts), [v.check for v in verdicts if not v.passed]


def test_a_hard_upload_error_cannot_pass_as_a_clean_overload_run() -> None:
    """The lost-file checks pass — nothing vanished — but the run is not clean."""
    state = seeded_state(90, seconds_each=5.0)
    state.upload_failures = [{"file": f"f{i}.txt", "error": "Connection reset by peer"} for i in range(10)]
    samples = [Sample(0, 90, 0), Sample(60, 90, 90)]

    result = bench_stress.build_result(
        stress_args(), plan_corpus(100, seed=1, kinds=TEXT_KINDS), state, 0.0, samples,
        {"llm": "x", "embedding": "y"},
        __import__("datetime").datetime.now(__import__("datetime").timezone.utc), 100, 10.0,
    )

    assert result["metrics"]["held_up"] is False, "a crash-style refusal is not a clean run"
    assert result["metrics"]["refused_as_backpressure"] == 0
    failed = [v for v in result["verdicts"] if not v["passed"]]
    assert [v["check"] for v in failed] == ["Refusals were the stack shedding load, not breaking"]
    # And the files themselves were not lost, which the wording must keep distinct.
    accepted = next(v for v in result["verdicts"] if "accepted or refused" in v["check"])
    assert accepted["passed"] is True


def test_a_scale_result_keeps_the_readings_behind_its_trend() -> None:
    state = seeded_state(10)
    samples = [Sample(i * 60, 10, min(10, i * 3), 900.0, 500.0) for i in range(4)]

    result = bench_scale.build_result(
        scale_args(), plan_corpus(10, seed=1, kinds=TEXT_KINDS), state, 0.0, samples,
        {"llm": "x", "embedding": "y"},
        __import__("datetime").datetime.now(__import__("datetime").timezone.utc), 10, "",
    )

    assert len(result["samples"]) == len(samples)
    assert result["samples"][0] == {
        "elapsed_seconds": 0, "uploaded": 10, "finished": 0, "backlog": 10,
        "container_memory_mb": 900.0, "indexing_memory_mb": 500.0,
    }
    assert result["command"], "the command that produced it is recorded"


def test_refusals_that_were_not_backpressure_are_called_out() -> None:
    """Being told to slow down is graceful; a dropped connection is not."""
    verdicts = overload_verdicts(
        attempted=100, uploaded=90, upload_failures=10, listed=90, terminal=90,
        peak_backlog=40, recovery_seconds=20.0, rejected=6,
    )

    quality = _verdict(verdicts, "shedding load")
    assert quality.passed is False
    assert "4 failed some other way" in quality.detail
    # The files themselves were not lost, so the other checks still pass.
    assert _verdict(verdicts, "appears in the knowledge base").passed is True


def test_overload_fails_when_an_accepted_file_never_appears() -> None:
    verdicts = overload_verdicts(
        attempted=100, uploaded=100, upload_failures=0, listed=97, terminal=97,
        peak_backlog=80, recovery_seconds=30.0,
    )

    appears = _verdict(verdicts, "appears in the knowledge base")
    assert appears.passed is False
    assert "3 missing" in appears.detail


def test_overload_fails_when_uploads_vanish_without_an_error() -> None:
    verdicts = overload_verdicts(
        attempted=100, uploaded=90, upload_failures=0, listed=90, terminal=90,
        peak_backlog=50, recovery_seconds=10.0,
    )

    assert _verdict(verdicts, "accepted or refused").passed is False


def test_overload_fails_when_files_are_still_in_flight_at_the_end() -> None:
    verdicts = overload_verdicts(
        attempted=100, uploaded=100, upload_failures=0, listed=100, terminal=94,
        peak_backlog=60, recovery_seconds=None,
    )

    assert _verdict(verdicts, "finished, one way or another").passed is False
    cleared = _verdict(verdicts, "backlog cleared")
    assert cleared.passed is False
    assert "never cleared" in cleared.detail


def test_verdicts_render_as_a_readable_table() -> None:
    table = render_verdicts([Verdict("Nothing was lost", False, "3 missing")])

    assert "| Nothing was lost | ❌ no | 3 missing |" in table


def test_backpressure_refusals_are_told_apart_from_breakage() -> None:
    failures = [
        {"file": "a.txt", "error": "429 Client Error: Too Many Requests"},
        {"file": "b.txt", "error": "503 Server Error: Service Unavailable"},
        {"file": "c.txt", "error": "Connection reset by peer"},
    ]

    assert bench_stress.counted_rejections(failures) == 2


def test_recovery_is_measured_from_the_last_upload() -> None:
    samples = [Sample(0, 0, 0), Sample(100, 300, 100), Sample(160, 300, 300), Sample(200, 300, 300)]

    assert bench_stress.recovery_seconds(samples, 100.0) == 60.0
    assert bench_stress.recovery_seconds([Sample(0, 10, 0)], 0.0) is None
    assert bench_stress.recovery_seconds(samples, None) is None


# --- stopping cleanly ------------------------------------------------------


class SlowPlumbing:
    """Stands in for the upload plumbing, one slow upload at a time."""

    def __init__(self, state: FakeState) -> None:
        self.state = state
        self.uploaded: list[str] = []

    def upload_file(self, kb_client: object, kb_id: str, folder_id: object, f: object,
                    state: FakeState) -> None:
        time.sleep(0.01)
        with state.lock:
            self.uploaded.append(f.name)
            state.uploaded_at[f.name] = time.perf_counter()
            state.file_of[f.name] = f


def test_the_uploader_stops_when_asked_instead_of_running_on() -> None:
    state = FakeState()
    plumbing = SlowPlumbing(state)
    plan = plan_corpus(200, seed=3, kinds=TEXT_KINDS)
    args = argparse.Namespace(batch_size=10, upload_workers=2)
    uploader = bench_scale._Uploader(args, object(), "kb-1", plan, {}, state, plumbing, "salt")

    uploader.start()
    time.sleep(0.2)
    stopped = uploader.stop(timeout=30)

    assert stopped is True, "the uploader should stop when asked"
    assert uploader.halted is True
    assert len(plumbing.uploaded) < len(plan.entries), "it should not have uploaded the whole corpus"


def test_uploading_is_stopped_before_the_knowledge_base_is_deleted() -> None:
    """A run that hits its limit must not upload into a knowledge base being deleted."""
    order: list[str] = []

    class Uploader:
        def stop(self, timeout: float) -> bool:
            order.append("stop uploading")
            return True

    class KBClient:
        def delete_kb(self, kb_id: str) -> None:
            order.append("delete knowledge base")

    warnings = bench_scale.finish_run(Uploader(), KBClient(), "kb-1", keep_kb=False)

    assert order == ["stop uploading", "delete knowledge base"]
    assert warnings == []


def test_an_uploader_that_will_not_stop_is_reported() -> None:
    class Stuck:
        def stop(self, timeout: float) -> bool:
            return False

    class KBClient:
        def delete_kb(self, kb_id: str) -> None:
            pass

    warnings = bench_scale.finish_run(Stuck(), KBClient(), "kb-1", keep_kb=False, stop_timeout=5)

    assert any("still working" in w for w in warnings)


def test_a_knowledge_base_that_will_not_delete_is_reported_not_raised() -> None:
    class KBClient:
        def delete_kb(self, kb_id: str) -> None:
            raise RuntimeError("gateway said no")

    warnings = bench_scale.finish_run(None, KBClient(), "kb-1", keep_kb=False)

    assert any("could not delete" in w and "gateway said no" in w for w in warnings)


# --- a run that did not cover its corpus ----------------------------------


def test_an_uploader_that_crashed_part_way_is_not_a_complete_run() -> None:
    """The records that did finish are a prefix, not the run that was asked for."""
    reason = bench_scale.upload_shortfall(1000, 120, error="connection reset by peer")

    assert "120 of 1000" in reason
    assert "connection reset by peer" in reason


def test_a_run_that_uploaded_nothing_is_not_a_complete_run() -> None:
    """With nothing uploaded there is nothing pending, which used to read as done."""
    reason = bench_scale.upload_shortfall(500, 0, error="the knowledge base rejected every upload")

    assert reason, "zero uploads must not pass as a finished run"
    assert "0 of 500" in reason


def test_an_uploader_cut_short_is_not_a_complete_run() -> None:
    reason = bench_scale.upload_shortfall(500, 300, halted=True)

    assert "300 of 500" in reason and "asked to stop" in reason


def test_a_run_that_sent_every_file_has_nothing_to_report() -> None:
    assert bench_scale.upload_shortfall(500, 500) == ""


def test_a_partial_upload_is_reported_alongside_a_timeout() -> None:
    state = FakeState()
    state.stopped_early = "the run hit its 600s limit with 4 file(s) still indexing"

    bench_scale.note_incomplete(state, bench_scale.upload_shortfall(100, 40, halted=True))

    assert "hit its 600s limit" in state.stopped_early
    assert "40 of 100" in state.stopped_early


def test_the_uploader_counts_the_files_it_sent() -> None:
    state = FakeState()
    plumbing = SlowPlumbing(state)
    plan = plan_corpus(20, seed=3, kinds=TEXT_KINDS)
    args = argparse.Namespace(batch_size=5, upload_workers=2)
    uploader = bench_scale._Uploader(args, object(), "kb-1", plan, {}, state, plumbing, "salt")

    uploader.start()
    uploader.join(30)

    assert uploader.submitted == 20
    assert bench_scale.upload_shortfall(20, uploader.submitted, uploader.error, uploader.halted) == ""


def _canned_result(stopped_early: str | None) -> dict:
    return {
        "schema_version": 1,
        "benchmark": "scale",
        "label": "unit-test",
        "metrics": {
            "wall_seconds": 1.0,
            "records_uploaded": 1,
            "records_completed": 1,
            "records_per_minute": 60,
            "time_to_indexed_seconds": {"p50": 1, "p95": 1, "p99": 1, "max": 1},
            "failures": {"upload": 0, "by_status": {}, "unfinished": 0, "total": 0},
            "peak_indexing_rss_mb": None,
            "peak_container_memory_mb": None,
            "status_poll_errors": 0,
            "stopped_early": stopped_early,
            "upload_error": None,
        },
        "corpus": {"docs": 1, "seed": 1, "kinds": ["txt"], "folders": 0,
                   "planned_bytes": 1, "by_kind": {"txt": 1}, "generated_bytes": 1},
        "settings": {},
        "environment": {"label": "unit-test", "graph_db": "neo4j", "message_broker": "redis",
                        "ai_models": {"llm": "x", "embedding": "y"}, "host_cpus": 1,
                        "host_memory_gb": 1, "host_platform": "test", "git_sha": ""},
        "trend": {"throughput_windows": [], "latency_windows": [], "steady": True, "notes": [],
                  "memory": {"start_mb": None, "end_mb": None, "peak_mb": None,
                             "growth_mb": None, "growth_mb_per_hour": None}},
        "upload_failures": [],
    }


def test_an_incomplete_run_fails_when_asked_to(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    partial = "the corpus was only partly uploaded: 120 of 1000 files were sent"
    monkeypatch.setattr(bench_scale, "run_benchmark", lambda args: _canned_result(partial))
    monkeypatch.setattr(sys, "argv", [
        "bench_scale.py", "--label", "unit-test", "--fail-if-incomplete",
        "--output", str(tmp_path / "scale.json"),
    ])

    assert bench_scale.main() == 1, "a partly uploaded corpus must not exit 0"
    written = json.loads((tmp_path / "scale.json").read_text(encoding="utf-8"))
    assert written["metrics"]["stopped_early"] == partial


def test_a_complete_run_passes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bench_scale, "run_benchmark", lambda args: _canned_result(None))
    monkeypatch.setattr(sys, "argv", [
        "bench_scale.py", "--label", "unit-test", "--fail-if-incomplete",
        "--output", str(tmp_path / "scale.json"),
    ])

    assert bench_scale.main() == 0


def test_an_incomplete_run_still_writes_its_result_without_the_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Locally the numbers are still worth having; the summary says what happened."""
    monkeypatch.setattr(bench_scale, "run_benchmark",
                        lambda args: _canned_result("the corpus was only partly uploaded: 1 of 9 files"))
    monkeypatch.setattr(sys, "argv", [
        "bench_scale.py", "--label", "unit-test", "--output", str(tmp_path / "scale.json"),
    ])

    assert bench_scale.main() == 0
    assert (tmp_path / "scale.json").exists()


# --- the results the workflow saves ---------------------------------------


def scale_args(**overrides: object):
    args = bench_scale.build_parser().parse_args(["--label", "unit-test", "--docs", "10"])
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def test_a_scale_result_carries_the_totals_and_the_shape_of_the_run() -> None:
    state = seeded_state(10, seconds_each=30.0)
    samples = [Sample(i * 60, min(10, i * 4), min(10, i * 3), 900.0, 500.0) for i in range(5)]

    result = bench_scale.build_result(
        scale_args(), plan_corpus(10, seed=1, kinds=TEXT_KINDS), state, 0.0, samples,
        {"llm": "openai/gpt-4o-mini", "embedding": "local"},
        __import__("datetime").datetime.now(__import__("datetime").timezone.utc), 1234, "",
    )

    assert result["benchmark"] == "scale"
    assert result["metrics"]["records_completed"] == 10
    assert result["metrics"]["failures"]["total"] == 0
    assert result["trend"]["throughput_windows"]
    assert result["trend"]["steady"] is True, result["trend"]["notes"]
    assert result["corpus"]["generated_bytes"] == 1234
    # Same metric names as the indexing benchmark, so compare.py can read both.
    for key in ("wall_seconds", "records_per_minute", "time_to_indexed_seconds", "peak_indexing_rss_mb"):
        assert key in result["metrics"]


def test_a_scale_result_can_be_compared_with_a_baseline_of_itself() -> None:
    """The result and the comparison must not drift apart."""
    state = seeded_state(10)
    samples = [Sample(i * 60, 10, min(10, i * 3), None, 500.0) for i in range(4)]
    result = bench_scale.build_result(
        scale_args(), plan_corpus(10, seed=1, kinds=TEXT_KINDS), state, 0.0, samples,
        {"llm": "openai/gpt-4o-mini", "embedding": "local"},
        __import__("datetime").datetime.now(__import__("datetime").timezone.utc), 10, "",
    )
    baseline = json.loads(json.dumps(result))

    rows, mismatches = compare.compare(baseline, result)

    assert mismatches == []
    assert rows and not any(r.regressed for r in rows)


def test_a_partly_uploaded_scale_run_is_not_judged_against_a_baseline() -> None:
    """Fewer files means higher throughput and no failures among the missing ones."""
    state = seeded_state(10)
    samples = [Sample(i * 60, 10, min(10, i * 3), None, 500.0) for i in range(4)]
    good = bench_scale.build_result(
        scale_args(), plan_corpus(10, seed=1, kinds=TEXT_KINDS), state, 0.0, samples,
        {"llm": "x", "embedding": "y"},
        __import__("datetime").datetime.now(__import__("datetime").timezone.utc), 10, "",
    )
    partial = json.loads(json.dumps(good))
    partial["metrics"]["stopped_early"] = "the corpus was only partly uploaded: 4 of 10 files were sent"

    _, mismatches = compare.compare(good, partial)

    assert any("did not finish" in m for m in mismatches)


def test_a_run_that_uploaded_fewer_files_than_planned_is_not_judged() -> None:
    state = seeded_state(10)
    samples = [Sample(i * 60, 10, min(10, i * 3), None, 500.0) for i in range(4)]
    good = bench_scale.build_result(
        scale_args(), plan_corpus(10, seed=1, kinds=TEXT_KINDS), state, 0.0, samples,
        {"llm": "x", "embedding": "y"},
        __import__("datetime").datetime.now(__import__("datetime").timezone.utc), 10, "",
    )
    short = json.loads(json.dumps(good))
    short["metrics"]["records_uploaded"] = 4

    _, mismatches = compare.compare(good, short)

    assert any("only 4 of 10 documents were uploaded" in m for m in mismatches)


def test_a_complete_scale_run_is_still_judged() -> None:
    state = seeded_state(10)
    samples = [Sample(i * 60, 10, min(10, i * 3), None, 500.0) for i in range(4)]
    result = bench_scale.build_result(
        scale_args(), plan_corpus(10, seed=1, kinds=TEXT_KINDS), state, 0.0, samples,
        {"llm": "x", "embedding": "y"},
        __import__("datetime").datetime.now(__import__("datetime").timezone.utc), 10, "",
    )

    rows, mismatches = compare.compare(json.loads(json.dumps(result)), result)

    assert mismatches == []
    assert rows


def test_a_stress_result_is_refused_by_the_comparison_rather_than_misjudged() -> None:
    """Its verdicts are pass or fail, not numbers to put beside a baseline."""
    stress = {"benchmark": "stress", "metrics": {}, "environment": {}, "corpus": {}}

    rows, mismatches = compare.compare(dict(stress), dict(stress))

    assert rows == []
    assert any("stress" in m for m in mismatches)


def test_a_scale_run_that_stopped_early_says_so_in_its_summary() -> None:
    state = seeded_state(4)
    state.stopped_early = "the run hit its 60s limit with 6 file(s) still indexing"
    result = bench_scale.build_result(
        scale_args(), plan_corpus(10, seed=1, kinds=TEXT_KINDS), state, 0.0,
        [Sample(0, 10, 0), Sample(60, 10, 4)],
        {"llm": "x", "embedding": "y"},
        __import__("datetime").datetime.now(__import__("datetime").timezone.utc), 10, "",
    )

    summary = bench_scale.render_summary(result)

    assert "Stopped early" in summary and "still indexing" in summary


def stress_args(**overrides: object):
    args = bench_stress.build_parser().parse_args(["--label", "unit-test", "--docs", "10"])
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def test_a_stress_result_says_it_held_up_when_nothing_was_lost() -> None:
    state = seeded_state(10, seconds_each=5.0)
    samples = [Sample(0, 10, 0), Sample(30, 10, 5), Sample(60, 10, 10)]

    result = bench_stress.build_result(
        stress_args(), plan_corpus(10, seed=1, kinds=TEXT_KINDS), state, 0.0, samples,
        {"llm": "x", "embedding": "y"},
        __import__("datetime").datetime.now(__import__("datetime").timezone.utc), 10, 10.0,
    )

    assert result["benchmark"] == "stress"
    assert result["metrics"]["held_up"] is True
    assert result["metrics"]["peak_backlog"] == 10
    assert result["metrics"]["recovery_seconds"] == 50.0
    assert "Nothing was lost under overload." in bench_stress.render_summary(result)


def test_a_stress_result_reports_a_file_that_never_finished() -> None:
    state = seeded_state(10, seconds_each=5.0)
    lost = "rec-9"
    del state.finished_at[lost]  # accepted, listed, never reached a final state
    samples = [Sample(0, 10, 0), Sample(60, 10, 9)]

    result = bench_stress.build_result(
        stress_args(), plan_corpus(10, seed=1, kinds=TEXT_KINDS), state, 0.0, samples,
        {"llm": "x", "embedding": "y"},
        __import__("datetime").datetime.now(__import__("datetime").timezone.utc), 10, 10.0,
    )
    summary = bench_stress.render_summary(result)

    assert result["metrics"]["held_up"] is False
    assert result["metrics"]["failures"]["total"] >= 1
    assert "did not survive the overload" in summary
    assert "What to do next" in summary
