"""Analysis and reporting tests — the parts that decide whether a run is
trustworthy, and therefore the parts most worth pinning.

Runnable without docker or a live stack:  python loadtest/tests/test_analysis.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lt import agent, compare, metrics, profiler, report  # noqa: E402

# A real /proc/<pid>/stat line, with a comm chosen to contain the spaces and
# parentheses that break naive field splitting.
STAT_LINE = (
    "1234 (py thon (x)) S 1 1234 1234 0 -1 4194304 9000 0 12 0 "
    # fields 14..17: utime=700 stime=300 cutime=0 cstime=0
    "700 300 0 0 "
    # 18 priority, 19 nice, 20 num_threads, 21 itrealvalue, 22 starttime
    "20 0 9 0 555 "
    # 23 vsize, 24 rss (pages)
    "123456789 2048 "
    "18446744073709551615 1 2 3 4 5 6 7 8 9\n"
)


def test_proc_stat_field_offsets() -> None:
    ticks, rss, threads = agent.parse_stat(STAT_LINE)
    assert ticks == 1000  # utime + stime
    assert threads == 9
    assert rss == 2048 * agent.PAGE


def test_agent_classifies_services_and_ignores_the_reload_supervisor() -> None:
    assert agent.classify("python -m app.connectors_main") == "connector"
    assert agent.classify("python -m app.indexing_main") == "indexing"
    # The dev image wraps each service in watchmedo, whose own argv carries the
    # module name; counting it would double every dev-mode measurement.
    assert agent.classify("watchmedo auto-restart -- python -m app.connectors_main") is None
    assert agent.classify("/bin/sh -c sleep 1") is None


def test_collector_start_stop_join_does_not_shadow_thread_internals() -> None:
    """threading.Thread has its own _stop() method. A subclass that stores an
    Event under that name makes join() raise 'Event object is not callable'
    after the thread finishes — which killed the first live run."""
    from lt.collect import _Loop

    class Counter(_Loop):
        def __init__(self, path: Path) -> None:
            super().__init__(path, 0.01, "counter")
            self.ticks = 0

        def tick(self, out) -> None:
            self.ticks += 1
            out.write("{}\n")

    with tempfile.TemporaryDirectory() as tmp:
        loop = Counter(Path(tmp) / "out.jsonl")
        loop.start()
        while loop.ticks < 2:
            pass
        loop.stop()
        loop.join(timeout=5)          # raised TypeError before the fix
        assert not loop.is_alive()
        loop.join(timeout=5)          # joining a finished thread must also be safe


def _probe_rows(base_t: float, latencies_ms: list[float], probe: str = "through") -> list[dict]:
    return [
        {"type": "probe", "t": base_t + i, "probe": probe, "status": 200, "latency_ms": ms}
        for i, ms in enumerate(latencies_ms)
    ]


def _iteration(rate: float, p99: float = 40.0, gap: float = 0.5, complete: bool = True, indexed: int = 0) -> dict:
    return {
        "kind": "measured",
        "index": 0,
        "throughput": {
            "records_per_s": rate,
            "duration_s": 100.0,
            "final_records": 1000,
            "expected_records": 1000,
            "complete": complete,
            "indexed_records": indexed,
            "series": {"c1": [[0, 0], [50, 500], [100, 1000]]},
        },
        "latency": {
            "through": {"p50": 10.0, "p95": 30.0, "p99": p99, "max": p99, "max_gap_s": gap, "count": 100, "errors": 0},
            "control": {"p50": 5.0, "p95": 8.0, "p99": 9.0, "max": 9.0, "max_gap_s": 0.5, "count": 100, "errors": 0},
        },
        "resources": {"services": {}, "series": {}},
        "profiles": [],
    }


def test_percentile_is_nearest_rank() -> None:
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert metrics.percentile(values, 50) == 5
    assert metrics.percentile(values, 99) == 10
    assert metrics.percentile(values, 100) == 10
    assert metrics.percentile([], 50) == 0.0


def test_latency_stats_excludes_server_errors_and_finds_gaps() -> None:
    rows = _probe_rows(1000.0, [10, 20, 30, 40])
    # A 500 must not count towards the latency distribution, and the 12s hole it
    # leaves behind is exactly the stall signal we care about.
    rows.append({"type": "probe", "t": 1005.0, "probe": "through", "status": 503, "latency_ms": 1.0})
    rows.append({"type": "probe", "t": 1015.0, "probe": "through", "status": 200, "latency_ms": 12000.0})
    stats = metrics.latency_stats(rows, "through")
    assert stats.errors == 1
    assert stats.max == 12000.0
    assert stats.max_gap_s == 12.0


def test_call_latency_stats_mean_and_percentiles() -> None:
    rows = [
        {"kind": "neo4j", "ms": 10, "ok": True},
        {"kind": "neo4j", "ms": 20, "ok": True},
        {"kind": "neo4j", "ms": 30, "ok": True},
        {"kind": "neo4j", "ms": 999, "ok": False},
        {"kind": "confluence", "ms": 40, "ok": True},
    ]
    neo = metrics.call_latency_stats(rows, "neo4j")
    assert neo.count == 4
    assert neo.errors == 1
    assert neo.mean == 20.0
    assert neo.p50 == 20.0
    assert neo.max == 30.0
    conf = metrics.call_latency_stats(rows, "confluence")
    assert conf.count == 1
    assert conf.mean == 40.0
    empty = metrics.call_latency_stats(rows, "missing")
    assert empty.count == 0
    assert empty.mean == 0.0


def test_throughput_completion_uses_slowest_unit() -> None:
    started = 1000.0
    rows = [
        {"type": "throughput", "t": started + 10, "connector_id": "a", "total": 500, "auto_index_off": 500},
        {"type": "throughput", "t": started + 20, "connector_id": "a", "total": 500, "auto_index_off": 500},
        # b finishes later; duration must follow b, not a.
        {"type": "throughput", "t": started + 10, "connector_id": "b", "total": 100, "auto_index_off": 100},
        {"type": "throughput", "t": started + 40, "connector_id": "b", "total": 500, "auto_index_off": 500},
    ]
    stats = metrics.throughput_stats(rows, started, expected_total=1000)
    assert stats.complete is True
    assert stats.duration_s == 40.0
    assert stats.final_records == 1000
    assert stats.indexed_records == 0


def test_throughput_flags_indexing_leak() -> None:
    started = 1000.0
    rows = [
        # Only 300 of 500 records are AUTO_INDEX_OFF: the manual-index filter
        # did not fully apply, so indexing ran and the run is not connector-only.
        {"type": "throughput", "t": started + 10, "connector_id": "a", "total": 500, "auto_index_off": 300},
    ]
    stats = metrics.throughput_stats(rows, started, expected_total=500)
    assert stats.indexed_records == 200



def _tp_row(started: float, cid: str, total: int) -> dict:
    return {
        "type": "throughput", "t": started + 10, "connector_id": cid,
        "total": total, "auto_index_off": total,
    }


def test_throughput_flags_a_connector_that_duplicated_work() -> None:
    """Units sync identically-sized sources, so disagreement means duplication.

    This is the check that was missing when an N=120 run reported 32,383 records
    against 20,640 expected and was read as a throughput win: records_per_s is
    final/duration, so duplicates raise the numerator directly.
    """
    started = 1000.0
    rows = [
        _tp_row(started, "a", 172),
        _tp_row(started, "b", 172),
        _tp_row(started, "c", 272),  # synced twice
    ]
    stats = metrics.throughput_stats(rows, started, expected_total=516)
    assert stats.modal_records == 172
    assert stats.diverging == {"c": 272}


def test_throughput_accepts_a_fleet_that_agrees() -> None:
    started = 1000.0
    rows = [_tp_row(started, cid, 158) for cid in ("a", "b", "c")]
    stats = metrics.throughput_stats(rows, started, expected_total=474)
    assert stats.diverging == {}


def test_divergence_does_not_hard_code_an_expected_count() -> None:
    """The seeded corpora drift as they are edited, so the invariant is that
    the units agree with each other, not that they hit a fixed number."""
    started = 1000.0
    rows = [_tp_row(started, cid, 999) for cid in ("a", "b", "c")]
    stats = metrics.throughput_stats(rows, started, expected_total=10)
    assert stats.diverging == {}


def test_a_single_connector_cannot_diverge() -> None:
    """With one unit there is no fleet to disagree with; flagging it would make
    every conn1 scenario invalid."""
    started = 1000.0
    stats = metrics.throughput_stats([_tp_row(started, "a", 1000)], started, expected_total=1000)
    assert stats.diverging == {}


def test_aggregate_propagates_divergence() -> None:
    it = {
        "throughput": {
            "records_per_s": 10.0, "duration_s": 1.0, "complete": True,
            "indexed_records": 0, "diverging": {"c": 272}, "modal_records": 172,
        },
        "latency": {"through": {"p99": 1.0, "max_gap_s": 0.1}},
    }
    summary = metrics.aggregate([it, it])
    assert summary["records_diverged"] is True
    assert summary["divergence"]["0"] == {"c": 272}


def test_compare_blocks_on_a_duplicating_run() -> None:
    """Comparing against inflated throughput would read a correctness
    regression as an improvement."""
    def _doc(diverged: bool) -> dict:
        return {
            "scenario": {"source": "minio", "units": 2, "scale": 10, "seed": 1},
            "source": {"deterministic": True},
            "summary": {
                "records_per_s": {"median": 10.0, "cv": 0.01},
                "duration_s": {"median": 1.0},
                "api_p99_ms": {"median": 1.0, "max": 2.0},
                "api_max_gap_s": {"median": 0.1, "max": 0.2},
                "all_complete": True, "indexing_leaked": False,
                "records_diverged": diverged, "reproducible": True, "cv_known": True,
            },
        }

    assert compare.verdict(_doc(False), _doc(True))["state"] == "invalid"

def test_resource_stats_sums_pids_of_one_service() -> None:
    started = 1000.0
    rows = [
        {"type": "sample", "t": started + 1, "pid": 1, "service": "connector",
         "cpu_percent": 40.0, "rss_bytes": 100 * 1024 * 1024, "threads": 5, "fds": 30},
        {"type": "sample", "t": started + 1, "pid": 2, "service": "connector",
         "cpu_percent": 35.0, "rss_bytes": 50 * 1024 * 1024, "threads": 3, "fds": 20},
    ]
    stats = metrics.resource_stats(rows, started)
    assert stats.services["connector"]["cpu_max"] == 75.0
    assert stats.services["connector"]["rss_max_mb"] == 150.0


def test_container_stats_parses_docker_stats() -> None:
    started = 1000.0
    rows = [
        {
            "type": "container",
            "t": started + 1,
            "Name": "neo4j",
            "CPUPerc": "55.5%",
            "MemUsage": "1.5GiB / 3GiB",
        },
        {
            "type": "container",
            "t": started + 2,
            "Name": "neo4j",
            "CPUPerc": "120.0%",
            "MemUsage": "2GiB / 3GiB",
        },
    ]
    stats = metrics.container_stats(rows, started)
    assert stats.containers["neo4j"]["cpu_max"] == 120.0
    assert stats.containers["neo4j"]["mem_max_mb"] == round(2 * 1024, 1)
    assert stats.series["neo4j"][0][1] == 55.5


def test_aggregate_marks_noisy_runs_unreproducible() -> None:
    tight = metrics.aggregate([_iteration(100.0), _iteration(101.0), _iteration(99.0)])
    assert tight["reproducible"] is True

    noisy = metrics.aggregate([_iteration(100.0), _iteration(160.0), _iteration(70.0)])
    assert noisy["reproducible"] is False


def test_single_iteration_is_not_called_reproducible() -> None:
    """cv is 0 by definition for one sample; reporting that as reproducible
    would be the most misleading number the harness could emit."""
    one = metrics.aggregate([_iteration(100.0)])
    assert one["cv_known"] is False
    assert one["reproducible"] is False

    three = metrics.aggregate([_iteration(100.0), _iteration(101.0), _iteration(99.0)])
    assert three["cv_known"] is True
    assert three["reproducible"] is True


def test_filters_payload_nests_by_category() -> None:
    """The backend copies only the `sync`/`indexing` keys and reads them back
    from filters.<category>.values; a flat dict is accepted then dropped."""
    from lt.sources.base import Unit

    unit = Unit(
        index=0,
        expected_records=1,
        label="b",
        sync_filters={"buckets": {"value": ["b"], "operator": "in", "type": "multiselect"}},
    )
    payload = unit.filters_payload()
    assert set(payload) == {"sync", "indexing"}
    assert "buckets" in payload["sync"]["values"]
    # Always present, whatever the source supplied — it is what makes a run
    # connector-only.
    assert payload["indexing"]["values"]["enable_manual_sync"]["value"] is True


def test_aggregate_propagates_invalidity() -> None:
    leaked = metrics.aggregate([_iteration(100.0, indexed=5)])
    assert leaked["indexing_leaked"] is True
    incomplete = metrics.aggregate([_iteration(100.0, complete=False)])
    assert incomplete["all_complete"] is False


def _doc(run_id: str, rates: list[float], *, deterministic: bool = True, indexed: int = 0,
         source: str = "minio", units: int = 1) -> dict:
    iterations = [_iteration(r, indexed=indexed) for r in rates]
    return {
        "run_id": run_id,
        "expected_total": 1000,
        "scenario": {"name": "t", "source": source, "units": units, "scale": 1000, "seed": 1},
        "source": {"name": source, "deterministic": deterministic},
        "target": {"base_url": "http://localhost:3000", "container": "pipeshub-ai"},
        "iterations": iterations,
        "summary": metrics.aggregate(iterations),
        "profiles": [],
    }


def test_verdict_refuses_when_change_is_inside_noise() -> None:
    baseline = _doc("a", [100.0, 104.0, 96.0])
    candidate = _doc("b", [102.0, 106.0, 98.0])
    call = compare.verdict(baseline, candidate)
    assert call["state"] == "inconclusive"


def test_verdict_reports_a_real_improvement() -> None:
    baseline = _doc("a", [100.0, 100.5, 99.5])
    candidate = _doc("b", [200.0, 201.0, 199.0])
    call = compare.verdict(baseline, candidate)
    assert call["state"] == "better"
    assert call["change"] > 0.9


def test_verdict_invalid_on_mismatched_or_leaky_runs() -> None:
    assert compare.verdict(_doc("a", [100.0]), _doc("b", [100.0], units=4))["state"] == "invalid"
    assert compare.verdict(_doc("a", [100.0]), _doc("b", [100.0], indexed=9))["state"] == "invalid"
    assert compare.verdict(_doc("a", [100.0]), _doc("b", [100.0], deterministic=False))["state"] == "invalid"
    assert compare.verdict(_doc("a", [100.0]), _doc("b", [100.0], source="postgres"))["state"] == "invalid"


def test_blocking_analysis_finds_the_longest_stall() -> None:
    speedscope = {
        "shared": {"frames": [{"name": "loop"}, {"name": "execute", "file": "google.py", "line": 42}]},
        "profiles": [
            {
                "type": "sampled",
                "name": "MainThread",
                # Two samples in loop, then five uninterrupted in execute.
                "samples": [[0], [0], [1], [1], [1], [1], [1]],
                "weights": [10, 10, 10, 10, 10, 10, 10],
            }
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "p.json"
        path.write_text(json.dumps(speedscope), encoding="utf-8")
        runs = profiler.analyse_blocking(path)
    assert runs[0]["frame"].startswith("execute")
    assert runs[0]["samples"] == 5
    assert runs[0]["duration_ms"] == 50.0


SPEEDSCOPE = {
    "shared": {
        "frames": [
            {"name": "run_sync", "file": "/app/connector.py", "line": 10},
            {"name": "execute", "file": "/app/google.py", "line": 42},
            {"name": "select", "file": "/app/loop.py", "line": 3},
        ]
    },
    "profiles": [
        {
            "type": "sampled",
            "name": "MainThread",
            # py-spy emits unit=seconds with one weight per sample (0.01 at
            # 100Hz), so 3 consecutive samples is 30ms, not 0.03ms.
            "unit": "seconds",
            "samples": [[0, 1], [0, 1], [0, 1], [0, 2], [0, 1]],
            "weights": [0.01, 0.01, 0.01, 0.01, 0.01],
        }
    ],
}


def test_speedscope_weights_are_converted_to_milliseconds() -> None:
    hottest = profiler.analyse_blocking_doc(SPEEDSCOPE)[0]
    assert hottest["samples"] == 3
    assert hottest["duration_ms"] == 30.0   # 3 x 0.01s, not 0.03


def test_speedscope_with_invalid_utf8_still_parses() -> None:
    """py-spy can emit a frame name containing bytes that are not valid UTF-8.
    Those names are display-only, and a stray byte cost a completed run its
    results once already."""
    raw = json.dumps(SPEEDSCOPE).encode("utf-8").replace(b"execute", b"exec\xf8te")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "p.json"
        path.write_bytes(raw)
        runs = profiler.analyse_blocking(path)
    assert runs, "capture with a bad byte should still yield stall data"


def test_idle_parking_frames_rank_below_real_stalls() -> None:
    """A thread-pool worker parked in queue.get() is waiting, not blocking. It
    is the longest run on any healthy service, so it must not be reported as
    the top stall."""
    doc = {
        "shared": {
            "frames": [
                {"name": "_worker", "file": "/usr/lib/python3.12/concurrent/futures/thread.py", "line": 90},
                {"name": "execute", "file": "/app/google.py", "line": 42},
            ]
        },
        "profiles": [
            {
                "type": "sampled",
                "name": "MainThread",
                "unit": "seconds",
                # 6 idle samples, then 3 real ones — idle is longer.
                "samples": [[0]] * 6 + [[1]] * 3,
                "weights": [0.01] * 9,
            }
        ],
    }
    runs = profiler.analyse_blocking_doc(doc)
    assert runs[0]["frame"].startswith("execute")
    assert runs[0]["idle"] is False
    assert any(r["idle"] and r["frame"].startswith("_worker") for r in runs)


def test_unitless_profiles_are_not_rescaled() -> None:
    from lt import flamegraph

    assert flamegraph.weight_scale({"unit": "seconds"}) == 1000.0
    assert flamegraph.weight_scale({"unit": "milliseconds"}) == 1.0
    assert flamegraph.weight_scale({}) == 1.0


def test_flamegraph_is_inlineable_and_proportional() -> None:
    from lt import flamegraph

    svg = flamegraph.render(SPEEDSCOPE, title="connector")
    # Inlined into the report, so it must not carry an XML prolog or doctype.
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "<?xml" not in svg and "<!DOCTYPE" not in svg
    assert "execute" in svg
    # run_sync is on every sample, so it must span the full width.
    root, total = flamegraph.build_tree(SPEEDSCOPE)
    thread = root.children["MainThread"]
    assert thread.children["run_sync (connector.py:10)"].weight == total


def test_flamegraph_is_well_formed_xml_with_awkward_frame_names() -> None:
    """The SVG is written to a .svg file and opened directly, so it must parse
    as XML and declare the SVG namespace.

    Frame names containing & < > are the trap: escaping and *then* truncating
    to fit the box can cut through "&amp;" and leave "&am", which breaks the
    whole document. Long names are essential to this test — short ones are
    never truncated.
    """
    import xml.etree.ElementTree as ET

    from lt import flamegraph

    nasty = "handle<T&U>::run(a && b) " + "x" * 300
    doc = {
        "shared": {"frames": [{"name": nasty, "file": "/app/a.py", "line": 1}]},
        "profiles": [
            {
                "type": "sampled",
                "name": 'Thread 1 "Main" & co',
                "unit": "seconds",
                "samples": [[0]] * 10,
                "weights": [0.01] * 10,
            }
        ],
    }
    svg = flamegraph.render(doc, title="a & b <test>")

    root = ET.fromstring(svg)  # raises on malformed XML — the reported bug
    assert root.tag == "{http://www.w3.org/2000/svg}svg", "missing xmlns"
    assert "&am<" not in svg and "&am " not in svg


def test_flamegraph_handles_an_empty_capture() -> None:
    from lt import flamegraph

    assert "no samples" in flamegraph.render({"shared": {"frames": []}, "profiles": []})


def test_flamegraph_and_blocking_agree_on_the_same_capture() -> None:
    from lt import flamegraph

    svg = flamegraph.render(SPEEDSCOPE)
    hottest = profiler.analyse_blocking_doc(SPEEDSCOPE)[0]["frame"]
    assert hottest.split(" ")[0] in svg


def test_reports_render_without_placeholders() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        baseline = _doc("baseline", [100.0, 101.0, 99.0])
        html = report.render({**baseline, "_dir": str(directory)})
        assert "<title>" in html and "Reproducible" in html
        # A leaky run must say so loudly rather than presenting a clean number.
        leaky = report.render({**_doc("leaky", [100.0], indexed=3), "_dir": str(directory)})
        assert "Run invalid" in leaky

        out = directory / "compare.html"
        (directory / "a").mkdir()
        (directory / "b").mkdir()
        (directory / "a" / "result.json").write_text(json.dumps(baseline), encoding="utf-8")
        (directory / "b" / "result.json").write_text(
            json.dumps(_doc("candidate", [200.0, 201.0, 199.0])), encoding="utf-8"
        )
        compare.write(directory / "a", directory / "b", out)
        assert "BETTER" in out.read_text(encoding="utf-8")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"  ok   {test.__name__}")
        except Exception as exc:
            failures += 1
            print(f"  FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())


def test_connector_timeline_reports_start_end_and_records() -> None:
    """Per-connector timing, so an aggregate can never hide an outlier."""
    started = 1000.0
    rows = [
        {"type": "throughput", "t": started + 3, "connector_id": "a", "total": 0,
         "auto_index_off": 0, "connector_status": "SYNCING"},
        {"type": "throughput", "t": started + 6, "connector_id": "a", "total": 40,
         "auto_index_off": 40, "connector_status": "SYNCING"},
        {"type": "throughput", "t": started + 9, "connector_id": "a", "total": 100,
         "auto_index_off": 100, "connector_status": "SYNCING"},
        # Records stop at 9s but the sync keeps working until 15s — the tail a
        # record-count-only measurement would miss entirely.
        {"type": "throughput", "t": started + 15, "connector_id": "a", "total": 100,
         "auto_index_off": 100, "connector_status": "IDLE"},
    ]
    [entry] = metrics.connector_timeline(rows, started, {"a": started + 1})

    assert entry["triggered_s"] == 1.0
    assert entry["first_record_s"] == 6.0
    assert entry["last_record_s"] == 9.0
    assert entry["completed_s"] == 15.0          # status-based, not count-based
    assert entry["final_records"] == 100
    assert entry["duration_s"] == 14.0           # trigger -> IDLE, includes the tail
    assert entry["statuses"] == "SYNCING->IDLE"


def test_connector_timeline_falls_back_when_status_is_unavailable() -> None:
    """Older runs and any source without a status feed still get a duration."""
    started = 1000.0
    rows = [
        {"type": "throughput", "t": started + 2, "connector_id": "b", "total": 10,
         "auto_index_off": 10},
        {"type": "throughput", "t": started + 8, "connector_id": "b", "total": 50,
         "auto_index_off": 50},
    ]
    [entry] = metrics.connector_timeline(rows, started, {})
    assert entry["completed_s"] is None
    assert entry["duration_s"] == 8.0            # falls back to last_record_s
    assert entry["final_records"] == 50
