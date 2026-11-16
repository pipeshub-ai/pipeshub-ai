"""The cost-class benchmark's pure parts: salts, schedules, latency, acceptance, verdicts, calibration.

The benchmark needs a stack; these pin what would quietly produce wrong numbers: a salt that leaves the
bytes (and so the md5 dedup) alone, a latency measured from the wrong clock, a filter that lets a copy
through, a saturated sibling reported as a result, a knee that ignores efficiency.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import itertools
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "integration-tests" / "perf"))

import bench_costclass as bc
import compare
import salt
import tiers
from fake_ai import server as fake

pytestmark = pytest.mark.unit

_CORPUS = bc.load_cost_corpus_module()


@pytest.fixture(scope="module")
def corpus_dir(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, object]:
    out = tmp_path_factory.mktemp("cost")
    return out, _CORPUS.generate(out, profile="cost-smoke", seed=21)


def _entry(manifest: object, kind: str, ext: str) -> object:
    return next(e for e in manifest.entries if e.kind == kind and e.ext == ext)  # type: ignore[attr-defined]


# -- salts ------------------------------------------------------------------------------------------


def _text_of(data: bytes, ext: str) -> str:
    if ext == "pdf":
        import pypdfium2 as pdfium

        return pdfium.PdfDocument(data)[0].get_textpage().get_text_range()
    if ext == "docx":
        import docx

        return "\n".join(p.text for p in docx.Document(io.BytesIO(data)).paragraphs)
    if ext == "xlsx":
        import openpyxl

        return "\n".join(str(c.value) for row in openpyxl.load_workbook(io.BytesIO(data)).active.iter_rows() for c in row)
    if ext == "pptx":
        from pptx import Presentation

        return "\n".join(
            sh.text_frame.text for sl in Presentation(io.BytesIO(data)).slides for sh in sl.shapes if sh.has_text_frame
        )
    return data.decode()


@pytest.mark.parametrize(("kind", "ext"), [
    ("small-text", "txt"), ("small-text", "md"), ("small-text", "html"), ("small-pdf", "pdf"),
    ("office", "docx"), ("office", "xlsx"), ("office", "pptx"), ("scanned", "pdf"),
])
def test_salt_per_upload(corpus_dir: tuple[Path, object], kind: str, ext: str) -> None:
    """1000 salts of one template: 1000 md5s, and the salt is in the extracted text, with pages unchanged."""
    out, manifest = corpus_dir
    entry = _entry(manifest, kind, ext)
    raw = (out / entry.path).read_bytes()  # type: ignore[attr-defined]
    salted = [salt.salt_bytes(raw, ext, salt.salt_token("run", n)) for n in range(1000)]
    assert len({hashlib.md5(s).hexdigest() for s in salted}) == 1000
    assert hashlib.md5(raw).hexdigest() not in {hashlib.md5(s).hexdigest() for s in salted}
    texts = {_text_of(s, ext) for s in salted[:40]}
    assert len(texts) == 40
    assert all(f"run:{n}" in _text_of(salted[n], ext) for n in range(0, 40, 13))
    if ext == "pdf":
        import pypdfium2 as pdfium

        assert len(pdfium.PdfDocument(salted[7])) == entry.pages  # type: ignore[attr-defined]
        renders = {pdfium.PdfDocument(s)[0].render(0.2).to_pil().tobytes() for s in salted[:12]}
        assert len(renders) == 12  # page 1 looks different too, for the vision model
    if ext == "pptx":
        from pptx import Presentation

        assert len(Presentation(io.BytesIO(salted[3])).slides) == entry.pages  # type: ignore[attr-defined]


def test_salting_a_thousand_page_pdf_is_cheap() -> None:
    import pypdfium2 as pdfium

    big = _CORPUS.light_pdf(_CORPUS.Words(random.Random(1)), 1000)
    start = time.perf_counter()
    out = salt.salt_bytes(big, "pdf", "run:1")
    assert time.perf_counter() - start < 0.05
    assert len(pdfium.PdfDocument(out)) == 1000


def test_salting_twice_stacks_and_still_opens(corpus_dir: tuple[Path, object]) -> None:
    import pypdfium2 as pdfium

    out, manifest = corpus_dir
    raw = (out / _entry(manifest, "small-pdf", "pdf").path).read_bytes()  # type: ignore[attr-defined]
    twice = salt.salt_bytes(salt.salt_bytes(raw, "pdf", "a:1"), "pdf", "b:2")
    text = pdfium.PdfDocument(twice)[0].get_textpage().get_text_range()
    assert "a:1" in text
    assert "b:2" in text


def test_salt_refuses_a_format_it_cannot_salt() -> None:
    with pytest.raises(ValueError, match="no salt"):
        salt.salt_bytes(b"x", "png", "a:1")


# -- schedule ---------------------------------------------------------------------------------------


def test_schedule_is_seeded_and_open_loop() -> None:
    a = bc.poisson_arrivals(0.2, 3600, seed=4)
    assert a == bc.poisson_arrivals(0.2, 3600, seed=4)
    assert a != bc.poisson_arrivals(0.2, 3600, seed=5)
    assert 0.85 * 720 < len(a) < 1.15 * 720
    assert a == sorted(a)
    # Arrivals are a function of the seed alone: a plan built after a stall is the same plan.
    assert bc.build_plan("b05", seed=4, duration_s=600).arrivals == bc.build_plan("b05", seed=4, duration_s=600).arrivals


@pytest.mark.parametrize(("cpus", "huge"), [(2, 1), (4, 3), (8, 5), (16, 10), (24, 15)])
def test_b01_huge_count_scales_with_the_tier(cpus: int, huge: int) -> None:
    plan = bc.build_plan("b01", tier_cpus=cpus, duration_s=300)
    assert sum(1 for a in plan.arrivals if a.role == "load" and a.kind == "huge-pdf") == huge
    first_probe = min(a.at_s for a in plan.arrivals if a.role == "probe")
    assert first_probe >= 60  # the probes start once the huge documents are in flight
    assert {a.kind for a in plan.arrivals if a.role == "probe"} <= set(bc.SMALL_KINDS)


def test_plan_without_a_needed_kind_is_refused() -> None:
    with pytest.raises(ValueError, match="no 'huge-pdf'"):
        bc.build_plan("b02", available={"small-pdf": ["x"]})


def test_docling_rate_is_lowered_to_keep_utilisation() -> None:
    assert bc.lambda_for_rho(0.55, 10, 1.0, 4) == pytest.approx(0.2)  # capped at the design rate
    lowered = bc.lambda_for_rho(0.55, 10, 2.0, 4)
    assert lowered == pytest.approx(0.11)
    assert bc.rho_docling(lowered, 10, 2.0, 4) == pytest.approx(0.55)


# -- latency ----------------------------------------------------------------------------------------


def _sample(seq: int = 0, **kw: object) -> bc.Sample:
    base = {
        "seq": seq, "kind": "small-pdf", "role": "probe", "name": f"{seq}.pdf", "md5": f"m{seq}",
        "scheduled_arrival_ms": 1_000_000.0, "send_ms": 1_000_000.0, "status": "COMPLETED",
        "last_index_ms": 1_010_000.0, "created_ms": 1_000_500.0, "virtual_record_id": f"v{seq}",
    }
    base.update(kw)
    return bc.Sample(**base)  # type: ignore[arg-type]


def test_latency_from_schedule() -> None:
    """A 30 s stalled upload adds 30 s, because the clock starts at the scheduled arrival."""
    on_time = _sample(send_ms=1_000_000.0, created_ms=1_000_100.0, last_index_ms=1_010_000.0)
    stalled = _sample(1, send_ms=1_030_000.0, created_ms=1_030_100.0, last_index_ms=1_040_000.0)
    assert bc.gated_latency_s(on_time) == pytest.approx(10.0)
    assert bc.gated_latency_s(stalled) == pytest.approx(40.0)
    # What createdAtTimestamp would have said: the stall disappears. It is a diagnostic column only.
    assert bc.diagnostic_latencies(stalled)["from_created_s"] == pytest.approx(9.9)
    assert bc.diagnostic_latencies(stalled)["upload_delay_s"] == pytest.approx(30.0)
    assert bc.gated_latency_s(_sample(last_index_ms=None)) is None


# -- acceptance filters -----------------------------------------------------------------------------


def test_acceptance_filters() -> None:
    good = _sample(0)
    failed = _sample(1, status="FAILED")
    two = _sample(2, record_count=2)
    shared_a, shared_b = _sample(3, virtual_record_id="same"), _sample(4, virtual_record_id="same")
    no_stamp = _sample(5, last_index_ms=None)
    early = _sample(6, last_index_ms=900_000.0)  # indexed before it was scheduled: a skewed clock
    future = _sample(7, last_index_ms=9_999_999_999.0)
    no_vrid = _sample(8, virtual_record_id=None)
    accepted, rejected = bc.filter_samples(
        [good, failed, two, shared_a, shared_b, no_stamp, early, future, no_vrid], now_ms=1_100_000.0)
    assert [s.seq for s in accepted] == [0]
    assert {k: sorted(s.seq for s in v) for k, v in rejected.items()} == {
        "not_completed": [1], "multiple_records": [2], "shared_vrid": [3, 4], "missing_timestamp": [5],
        "skewed_timestamp": [6, 7], "missing_virtual_record_id": [8],
    }


def test_duplicate_upload_rejected() -> None:
    """The same bytes uploaded twice: the copy is rejected by the md5 rule, and the original is not blamed."""
    original = _sample(0, md5="same-bytes", virtual_record_id="v")
    copy = _sample(1, md5="same-bytes", virtual_record_id="v", control=True, role="control", control_of=0)
    accepted, rejected = bc.filter_samples([original, copy])
    assert [s.seq for s in accepted] == [0]
    assert [s.seq for s in rejected["duplicate_md5"]] == [1]
    assert bc.rejected_ratio(accepted, rejected) == 0.0  # a deliberate control is not a bad sample
    # An accidental duplicate (not a control) is a real rejection and counts.
    accidental = _sample(2, md5="same-bytes", virtual_record_id="v2")
    accepted, rejected = bc.filter_samples([original, accidental])
    assert [s.seq for s in rejected["duplicate_md5"]] == [2]
    assert bc.rejected_ratio(accepted, rejected) == 1.0


def test_cache_hits_not_rejected() -> None:
    """A probe that hit its own record's cache is a real, slower record: kept, counted, in the p95."""
    plain = [_sample(n, last_index_ms=1_010_000.0) for n in range(19)]
    retried = _sample(19, last_index_ms=1_300_000.0, gateway_calls=6, cache_hits=4)
    accepted, rejected = bc.filter_samples([*plain, retried])
    assert not rejected
    row = bc.per_kind_summary(accepted)["small-pdf"]
    assert row["p95"] > 10.0 + 1  # the slow retried probe moves the tail
    assert row["probes_with_cache_hits"] == 1
    assert row["cache_hits"] == 4
    assert row["gateway_calls"] == 6


# -- statistics -------------------------------------------------------------------------------------


def test_percentiles_and_variation() -> None:
    stats = bc.summarise([float(n) for n in range(1, 101)])
    assert stats["p50"] == pytest.approx(50.5)
    assert stats["p95"] == pytest.approx(95.05)
    assert stats["n"] == 100
    assert bc.summarise([])["p50"] is None
    assert bc.coefficient_of_variation([100.0, 100.0, 100.0]) == 0.0
    assert bc.coefficient_of_variation([90.0, 100.0, 110.0]) == pytest.approx(0.1)
    assert bc.coefficient_of_variation([5.0]) is None
    assert bc.repeats_stable([100, 101, 99])
    assert not bc.repeats_stable([100, 130, 80])


# -- verdicts ---------------------------------------------------------------------------------------


def _verdict(**kw: object) -> dict:
    base = {
        "accepted": 100, "rejected_n": 0, "failed": 0, "missing": 0, "timed_out": 0, "upload_failures": 0,
        "saturated_siblings": [], "limit_violations": [], "control_expected": False, "control_rejected": False,
    }
    base.update(kw)
    return bc.run_verdict(**base)  # type: ignore[arg-type]


def test_failures_fail_the_run() -> None:
    assert _verdict()["exit_code"] == 0
    for kw in ({"failed": 1}, {"missing": 1}, {"timed_out": 2}, {"upload_failures": 1}):
        v = _verdict(**kw)
        assert v["status"] == "failed"
        assert v["exit_code"] != 0
        assert v["reasons"]
    v = _verdict(control_expected=True, control_rejected=False)
    assert v["status"] == "failed"
    assert "control" in v["reasons"][0]
    assert _verdict(control_expected=True, control_rejected=True)["status"] == "pass"
    assert _verdict(limit_violations=["oom_kill 1"])["status"] == "failed"


def test_rejection_budget_invalidates() -> None:
    assert _verdict(rejected_n=1)["status"] == "pass"  # exactly 1 %
    v = _verdict(rejected_n=2)
    assert v["status"] == "invalid"
    assert v["exit_code"] == bc.EXIT_INVALID
    assert _verdict(accepted=0, rejected_n=3)["status"] == "invalid"


def test_sibling_saturation_invalidates() -> None:
    v = _verdict(saturated_siblings=["neo4j"])
    assert v["status"] == "invalid"  # not failed, not passed
    assert v["exit_code"] not in (bc.EXIT_PASS, bc.EXIT_FAILED)
    assert "neo4j" in v["reasons"][0]
    both = _verdict(saturated_siblings=["qdrant"], failed=1)
    assert both["status"] == "failed"  # a real failure is not hidden by an invalid run
    assert both["invalid_reasons"]


def test_sibling_cpu_sampler() -> None:
    """Share of the sibling's own cpuset, from cpu.stat deltas, not of the host's CPUs."""
    series = {
        "neo4j": [(0.0, 0), (100.0, 360_000_000)],  # 3.6 CPU-s per s over a 4-CPU set: 90 %
        "qdrant": [(0.0, 0), (100.0, 100_000_000)],  # 1 CPU: 25 % of 4
        "redis": [(0.0, 0), (100.0, 90_000_000)],  # 0.9 CPU on a 1-CPU set: 90 %
        "mongo": [(5.0, 10)],  # one reading: unmeasured
    }
    report = bc.sibling_report(series, {"neo4j": 4, "qdrant": 4, "redis": 1, "mongo": 2})
    assert report["saturated"] == ["neo4j", "redis"]
    assert report["unmeasured"] == ["mongo"]
    assert report["average_share"]["qdrant"] == pytest.approx(0.25)
    # the average runs from the first reading to the last: a burst between two early readings does not stand for the run
    three = bc.sibling_report({"n": [(0.0, 0), (1.0, 4_000_000), (100.0, 4_000_000)]}, {"n": 4})
    assert three["saturated"] == [] and three["average_share"]["n"] == pytest.approx(0.01)
    assert bc.parse_cpuset("0-3,8,10-11") == 7
    assert bc.parse_cpuset("") == 0
    assert bc.parse_cpu_stat("usage_usec 123\nuser_usec 5\nnr_throttled 0\n")["usage_usec"] == 123


def test_connection_limits() -> None:
    peaks = {"redis_clients": 6000.0, "neo4j_connections": 80.0, "pg_backends": 120.0}
    bad = bc.check_limits(peaks, redis_maxclients=10000, neo4j_budget=100, pg_max_connections=100, oom_kills=0)
    assert any("redis" in b for b in bad)
    assert any("postgres" in b for b in bad)
    assert not any("neo4j" in b for b in bad)
    assert bc.check_limits({}, redis_maxclients=10000, neo4j_budget=None, pg_max_connections=None, oom_kills=2) == [
        "oom_kill 2"]
    assert bc.check_limits({"redis_clients": 100.0}, redis_maxclients=64, neo4j_budget=None,
                           pg_max_connections=None, oom_kills=0)  # maxclients 64: the check fires


# -- calibration ------------------------------------------------------------------------------------


def test_calibration_maths() -> None:
    # 10 CPU-s over 5 busy seconds: 2 cores. Never a division by an idle or absent process.
    assert bc.cores_per_unit(10.0, 5.0) == pytest.approx(2.0)
    assert bc.cores_per_unit(1.0, 0.0) is None
    assert bc.docling_s_d(busy_s=21.0, pages=20) == pytest.approx(1.05)
    assert bc.docling_s_d(busy_s=0.0, pages=20) is None
    assert bc.untracked_cpu(container_cpu_s=500.0, wall_s=100.0, tracked_unit_cpu_s=350.0) == pytest.approx(1.5)
    table = bc.weights_table({"docling": 1.0, "parse": 1.05, "inspect": None, "raster": 1.5, "embed": 2.0})
    assert table["docling"]["plan_change"] is True  # 50 % under its design weight
    assert table["parse"]["plan_change"] is False
    assert table["raster"]["plan_change"] is True
    assert table["inspect"]["measured"] is None
    assert table["inspect"]["plan_change"] is False
    assert "not_measurable" in table["inspect"]
    log = [
        {"family": "docling", "kind": "small-pdf", "wall_s": 10.0, "pages": 10,
         "proc": {"pids": 1, "readings": 20, "cpu_s": 25.0, "busy_s": 10.0}},
        {"family": "embed", "kind": "small-text", "wall_s": 8.0, "pages": 0,
         "proc": {"pids": 1, "readings": 20, "cpu_s": 8.0, "busy_s": 4.0}},
    ]
    report = bc.calibration_report(log)
    assert report["cores_per_unit"] == {"docling": 2.5, "embed": 2.0}
    assert report["s_d"] == pytest.approx(1.0)
    assert report["weights"]["docling"]["plan_change"] is False


def test_calibration_measures_the_family_process_not_the_pipeline() -> None:
    """Docling runs 2 cores for 5 s inside a 50 s record; the Docling process idles for the other 45 s (the pipeline around it is not its CPU). The family
    is 2 cores and 5 s per page, whatever the end-to-end wall says."""
    series = [(float(t), 2.0 * t) for t in range(6)] + [(float(t), 10.0 + 0.0 * t) for t in range(6, 51)]
    cpu, busy = bc.busy_and_cpu(series)
    assert (cpu, busy) == (pytest.approx(10.0), pytest.approx(5.0))
    row = {"family": "docling", "kind": "small-pdf", "wall_s": 50.0, "pages": 1,
           "proc": {"pids": 1, "readings": len(series), "cpu_s": cpu, "busy_s": busy}}
    report = bc.calibration_report([row])
    assert report["cores_per_unit"]["docling"] == pytest.approx(2.0)
    assert report["s_d"] == pytest.approx(5.0)
    # a process that was not found, or never ran, is not measured: never 0 and never the container's CPU
    gone = {**row, "proc": {"pids": 0, "readings": 0, "cpu_s": 0.0, "busy_s": 0.0}}
    idle = {**row, "proc": {"pids": 1, "readings": 30, "cpu_s": 0.0, "busy_s": 0.0}}
    for r in (gone, idle):
        rep = bc.calibration_report([r])
        assert rep["cores_per_unit"]["docling"] is None and rep["s_d"] is None
        assert rep["detail"]["docling"]["why_not_measured"]


def test_pool_families_are_reported_not_measurable() -> None:
    assert set(bc.FAMILY_KIND) == {"docling", "embed"}
    assert set(bc.NOT_MEASURABLE_FAMILIES) == {"parse", "inspect", "raster"}
    plan = bc.build_plan("calibrate")
    assert {a.role for a in plan.arrivals} == {"unit:docling", "unit:embed"}
    report = bc.calibration_report([])
    assert set(report["not_measurable"]) == {"parse", "inspect", "raster"}
    assert all(report["weights"][f]["measured"] is None and report["weights"][f]["plan_change"] is False
               for f in report["not_measurable"])
    assert "cgroup" not in report["method"] and "/proc" in report["method"]


def test_proc_stat_family_pids_and_sampler() -> None:
    stat = "123 (docling main (x)) S 1 2 3 4 5 6 7 8 9 10 250 50 0 0 20 0 1 0 100"
    assert bc.process_cpu_ticks(stat) == 300  # utime + stime, with a ")" and spaces in the name
    assert bc.process_cpu_ticks("garbage") is None
    top = ("PID PPID COMMAND\n10 1 python -m app.docling_main\n11 10 python -c from multiprocessing.spawn\n"
           "12 11 worker\n20 1 python -m app.indexing_main\n21 20 helper\n")
    assert bc.family_pids(top, "docling_main") == [10, 11, 12]
    assert bc.family_pids(top, "embedding_main") == []
    ticks = {10: 100, 11: 100}  # a parent and a pool child, 100 ticks each
    sampler = bc.ProcessCpuSampler(
        "c", "docling_main", top=lambda: top,
        read_stat=lambda pid: f"{pid} (x) S " + " ".join(["0"] * 10 + [str(ticks[pid]), "0"]))
    sampler.tick = 100.0
    sampler.pids = bc.family_pids(top, "docling_main")[:2]
    assert sampler._cpu_s() == pytest.approx(0.0)  # the first reading of each pid is its baseline
    ticks.update({10: 200, 11: 200})
    assert sampler._cpu_s() == pytest.approx(2.0)  # then the tree's CPU since, summed


def test_knee() -> None:
    linear = {8: 10.0, 12: 14.8, 16: 19.0, 24: 20.0}
    result = bc.knee(linear)
    assert result["cell_cpus"] == 16  # T24 is 20 / 30 of linear: past the knee
    assert result["curve"][16] == pytest.approx(0.95)
    assert result["confidence"] == "high"  # every tier measured, T24 bracketed it
    assert bc.knee({8: 10.0, 12: 15.0, 16: 20.0, 24: 30.0})["cell_cpus"] == 24
    assert bc.knee({8: 10.0, 12: 15.0, 16: 20.0, 24: 30.0})["confidence"] == "medium"  # a lower bound
    assert bc.knee({12: 14.0})["cell_cpus"] is None  # no valid T8 point
    assert bc.knee({8: 10.0, 16: None, 24: None})["cell_cpus"] == 8  # invalid points are left out
    assert bc.knee({8: 10.0, 16: None, 24: None})["confidence"] == "low"
    assert bc.sensitivity(10.0, 16.0)["ok"] is True
    assert bc.sensitivity(10.0, 10.2)["ok"] is False


def test_knee_stops_at_the_first_tier_that_falls_short() -> None:
    """T12 at 0.73 of linear and T16 back at 0.9: the knee is T8, with low confidence, not 16."""
    k = bc.knee({8: 10.0, 12: 11.0, 16: 18.0})
    assert k["cell_cpus"] == 8 and k["confidence"] == "low" and k["recovered_after_short"] is True
    assert k["first_short_tier"] == 12
    assert bc.knee({8: 10.0, 12: 14.0, 16: 17.0, 24: 18.0})["cell_cpus"] == 16  # a multiple of 4


def _knee_result(tier: int, rpm: float | None, status: str = "pass", processes: int = 1, scenario: str = "b06-knee",
                 corpus: str = "c1", profile: str = "legacy", bound: str = "not_docling") -> dict:
    return {"plan": {"scenario": scenario, "corpus_id": corpus},
            "environment": {"tier": {"cpus": tier}, "indexing_processes": processes, "capacity_profile": profile},
            "knee_attribution": {"bound": bound, "docling_busy_fraction": 0.4, "indexing_cores": 0.9},
            "verdict": {"status": status}, "metrics": {"records_per_minute": rpm}}


def test_knee_from_results_uses_only_valid_one_process_runs() -> None:
    good = [_knee_result(8, 10.0), _knee_result(12, 14.8), _knee_result(16, 19.0), _knee_result(24, 20.0)]
    out = bc.knee_from_results(good)
    assert out["cell_cpus"] == 16 and out["confidence"] == "high" and out["excluded"] == []
    bad = [_knee_result(8, 10.0), _knee_result(12, 14.8, status="invalid"), _knee_result(16, 19.0, processes=2),
           _knee_result(24, 20.0, scenario="b06")]
    out = bc.knee_from_results(bad)
    assert out["cell_cpus"] == 8 and out["confidence"] == "low"
    assert {e["tier"] for e in out["excluded"]} == {12, 16, 24}
    assert bc.knee_from_results([_knee_result(12, 14.8)])["cell_cpus"] is None  # no T8: nothing reported
    assert bc.knee_from_results([_knee_result(8, 10.0, corpus="a"), _knee_result(12, 14.0, corpus="b")])["confidence"] == "low"


# -- repeats: the knee over several runs per tier, and the baseline step ------------------------------------------------


def _shuffles(items: list, seeds: tuple[int, ...] = (1, 2, 3, 4, 5, 6)) -> list[list]:
    out = []
    for seed in seeds:
        copy = list(items)
        random.Random(seed).shuffle(copy)
        out.append(copy)
    return [list(reversed(items)), *out]


def test_the_knee_over_repeats_does_not_depend_on_the_order_of_the_files() -> None:
    """Per tier the point is the median of its repeats: any order of the same files gives the same knee."""
    files = [_knee_result(8, 100.0), _knee_result(8, 102.0), _knee_result(8, 98.0),
             _knee_result(12, 150.0), _knee_result(12, 149.0), _knee_result(12, 110.0),   # one bad repeat
             _knee_result(16, 150.0), _knee_result(16, 152.0), _knee_result(16, 151.0)]
    baseline = bc.knee_from_results(files)
    assert baseline["cell_cpus"] == 12 and baseline["repeats"][12]["median"] == 149.0
    assert baseline["repeats"][12]["values"] == [110.0, 149.0, 150.0]
    for shuffled in _shuffles(files):
        out = bc.knee_from_results(shuffled)
        assert out == baseline, (out["cell_cpus"], baseline["cell_cpus"])
    # last-wins would read T12 as 110 or 150 depending on the order: the two orders below pin that it does not
    assert bc.knee_from_results(files)["curve"] == bc.knee_from_results(list(reversed(files)))["curve"]


def test_a_docling_capped_repeat_is_left_out_of_the_median_and_listed() -> None:
    files = [_knee_result(8, 100.0), _knee_result(12, 150.0, bound="docling"), _knee_result(12, 149.0),
             _knee_result(12, 151.0), _knee_result(16, 120.0)]
    out = bc.knee_from_results(files)
    assert out["docling_capped_repeats"] == [{"tier": 12, "records_per_minute": 150.0}]
    assert out["repeats"][12] == {"runs": 3, "used": 2, "docling_capped": 1, "values": [149.0, 151.0], "median": 150.0}
    assert out["cell_cpus"] == 12 and "bound" not in out
    for shuffled in _shuffles(files):
        assert bc.knee_from_results(shuffled) == out


def test_a_tier_whose_every_repeat_was_capped_makes_the_knee_docling_bound() -> None:
    files = [_knee_result(8, 100.0), _knee_result(12, 150.0), _knee_result(16, 150.0, bound="docling"),
             _knee_result(16, 151.0, bound="docling")]
    for shuffled in _shuffles(files):
        out = bc.knee_from_results(shuffled)
        assert out["cell_cpus"] is None and out["bound"] == "docling" and out["docling_bound_tiers"] == [16]
        assert out["docling_capped_repeats"] == [] and out["record_as_decision"] is False


def _result_with(p50: float, tier: int = 4, status: str = "pass", scenario: str = "b05", rpm: float = 30.0) -> dict:
    r = _result(status=status)
    r["plan"]["scenario"] = scenario
    r["environment"]["tier"] = {"cpus": tier, "mem_gb": tier * 4}
    r["metrics"]["by_kind"] = {"small-pdf": {"p50": p50, "p95": p50 * 2}, "scanned": {"p50": 90.0, "p95": 200.0}}
    r["metrics"]["records_per_minute"] = rpm
    return r


def test_a_baseline_needs_three_passing_repeats_with_a_steady_p50() -> None:
    steady = [_result_with(40.0), _result_with(41.0), _result_with(39.5)]
    for shuffled in _shuffles(steady):
        out = bc.baseline_from_results(shuffled)
        assert out["baseline_valid"] is True and out["reasons"] == []
        assert out["groups"][0]["median"]["small-pdf p50"] == 40.0 and out["groups"][0]["repeats"] == 3
    # CV above 10 %: refused, and the reason names the metric and the CV
    noisy = [_result_with(40.0), _result_with(55.0), _result_with(30.0)]
    out = bc.baseline_from_results(noisy)
    assert out["baseline_valid"] is False
    assert any("small-pdf p50 varies too much" in r and "CV" in r for r in out["reasons"]), out["reasons"]
    # too few repeats
    assert any("at least 3" in r for r in bc.baseline_from_results(steady[:2])["reasons"])
    # a repeat that did not pass
    out = bc.baseline_from_results([*steady[:2], _result_with(40.0, status="invalid")])
    assert out["baseline_valid"] is False and any("did not pass" in r for r in out["reasons"])
    assert bc.baseline_from_results([])["baseline_valid"] is False


def test_the_baseline_checks_throughput_sensitivity_between_two_burst_tiers() -> None:
    def burst(tier: int, rpm: float) -> list[dict]:
        return [_result_with(40.0, tier=tier, scenario="b06", rpm=rpm + d) for d in (0.0, 0.3, -0.3)]

    flat = bc.baseline_from_results([*burst(4, 30.0), *burst(16, 31.0)])
    assert flat["baseline_valid"] is False and flat["sensitivity"]["ok"] is False
    assert any("less than the 30 %" in r for r in flat["reasons"])
    good = bc.baseline_from_results([*burst(4, 30.0), *burst(16, 90.0)])
    assert good["baseline_valid"] is True and good["sensitivity"] == {"ok": True, "difference": 0.667, "tiers": [4, 16]}


def test_baseline_from_exits_non_zero_for_an_unsteady_baseline_and_writes_nothing_to_commit(tmp_path: Path) -> None:
    def write(name: str, p50: float) -> str:
        f = tmp_path / name
        f.write_text(json.dumps(_result_with(p50)))
        return str(f)

    good = [write("a.json", 40.0), write("b.json", 41.0), write("c.json", 39.0)]
    assert bc.main(["--baseline-from", *good, "--output", str(tmp_path / "ok.json")]) == 0
    assert json.loads((tmp_path / "ok.json").read_text())["baseline_valid"] is True
    bad = [write("d.json", 40.0), write("e.json", 70.0), write("f.json", 30.0)]
    assert bc.main(["--baseline-from", *bad, "--output", str(tmp_path / "bad.json")]) == bc.EXIT_INVALID
    assert json.loads((tmp_path / "bad.json").read_text())["baseline_valid"] is False


def test_runner_repeats_every_tier_on_a_fresh_stack_and_checks_the_baseline(tmp_path: Path) -> None:
    tree, log, env = _stub_tree(tmp_path)
    env.update({"BENCH_TIERS": "T2:2:8", "BENCH_REPEATS": "3"})
    p = _runner(tree, env, "--scenario", "b05")
    calls = log.read_text().splitlines()
    benches = [c for c in calls if c.startswith("bench") and "--baseline-from" not in c]
    assert len(benches) == 3 and p.returncode == 0, p.stderr[-300:]
    assert [c for c in calls if " up -d " in c].__len__() == 3  # a fresh stack for every repeat
    last = [c for c in calls if c.startswith("bench --baseline-from")]
    assert len(last) == 1 and all(f"costclass-local-T2-r{n}.json" in last[0] for n in (1, 2, 3))
    (tmp_path / "one").mkdir()
    tree1, log1, env1 = _stub_tree(tmp_path / "one")
    env1["BENCH_TIERS"] = "T2:2:8"
    _runner(tree1, env1, "--scenario", "b05")
    assert not any("--baseline-from" in c for c in log1.read_text().splitlines())  # one run is not a baseline
    env1["BENCH_REPEATS"] = "0"
    assert _runner(tree1, env1, "--scenario", "b05").returncode == 64


def test_one_indexing_process_is_checked_not_assumed(monkeypatch: pytest.MonkeyPatch) -> None:
    assert bc.indexing_workers_from_env(None) == 1 and bc.indexing_workers_from_env("") == 1
    assert bc.indexing_workers_from_env("4") == 4 and bc.indexing_workers_from_env("x") == -1
    args = bc.parse_args(["--scenario", "b06-knee", "--label", "x", "--app-container", "app"])
    monkeypatch.setattr(bc, "container_env", lambda _c, name: "3" if name == "INDEXING_UVICORN_WORKERS" else "")
    with pytest.raises(SystemExit, match="one indexing process"):
        bc.check_stack(args)
    monkeypatch.setattr(bc, "container_env", lambda _c, name: "1" if name == "INDEXING_UVICORN_WORKERS" else "")
    assert bc.check_stack(args)["indexing_processes"] == 1
    cal = bc.parse_args(["--scenario", "calibrate", "--label", "x", "--app-container", "app"])
    monkeypatch.setattr(bc, "container_env", lambda _c, name: "2" if name == "DOCLING_UVICORN_WORKERS" else "1")
    with pytest.raises(SystemExit, match="single Docling worker"):
        bc.check_stack(cal)


def test_knee_scenario_wiring(tmp_path: Path) -> None:
    """The compose passes one indexing process to the app, and --knee-from writes CELL_CPUS and its confidence."""
    compose = (_ROOT / "deployment/docker-compose/docker-compose.bench.yml").read_text()
    assert "INDEXING_UVICORN_WORKERS=${BENCH_INDEXING_WORKERS:-1}" in compose
    files = []
    for tier, rpm in ((8, 10.0), (12, 14.8), (16, 19.0), (24, 20.0)):
        f = tmp_path / f"r{tier}.json"
        f.write_text(json.dumps(_knee_result(tier, rpm)))
        files.append(str(f))
    out = tmp_path / "knee.json"
    assert bc.main(["--knee-from", *files, "--output", str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["cell_cpus"] == 16 and data["confidence"] == "high"
    assert bc.main(["--knee-from", files[1], "--output", str(out)]) != 0  # no T8 point: no knee


def test_chaos_flag_and_record_fields() -> None:
    assert bc.parse_chaos("kill-app-at=300") == ("kill-app", 300.0)
    assert bc.parse_chaos(None) is None
    with pytest.raises(ValueError, match="unknown"):
        bc.parse_chaos("stop-app")
    nested = {"record": {"indexingStatus": "COMPLETED", "lastIndexTimestamp": 5, "createdAtTimestamp": 1,
                         "virtualRecordId": "v", "stageState": {"parse": {"gatewayCalls": 3, "cacheHits": 1}}}}
    fields = bc.record_fields(nested)
    assert (fields["status"], fields["last_index_ms"], fields["gateway_calls"], fields["cache_hits"]) == (
        "COMPLETED", 5, 3, 1)
    flat = bc.record_fields({"indexingStatus": "FAILED"})
    assert flat["gateway_calls"] is None  # a build that does not write the counters says so, it does not invent them


# -- compare ----------------------------------------------------------------------------------------


def _result(p95: float = 100.0, mode: str = "real", corpus: str = "c1", status: str = "pass") -> dict:
    return {
        "benchmark": "costclass",
        "environment": {"label": "x-t4", "ai_models": {"llm": "a"}, "embedding_mode": mode},
        "plan": {"scenario": "b05", "tier": "4cpu/16gb", "corpus_id": corpus, "seed": 1},
        "metrics": {"records_per_minute": 30.0, "by_kind": {
            "small-pdf": {"p50": 40.0, "p95": p95}, "scanned": {"p50": 90.0, "p95": 200.0}},
            "failures": {"total": 0}},
        "verdict": {"status": status, "reasons": []},
    }


def test_compare_per_class_budgets() -> None:
    rows, mismatches = compare.compare(_result(), _result())
    assert not mismatches
    assert not any(r.regressed for r in rows)  # identical input is not flagged
    rows, mismatches = compare.compare(_result(), _result(p95=125.0))  # +25 % in one kind
    assert not mismatches
    flagged = [r.name for r in rows if r.regressed]
    assert flagged == ["small-pdf p95"]  # only that kind
    rows, _ = compare.compare(_result(), _result(p95=110.0))
    assert not any(r.regressed for r in rows)
    for other in (_result(mode="fake"), _result(corpus="c2")):
        _, mismatches = compare.compare(_result(), other)
        assert mismatches  # not like for like: no verdict
    _, mismatches = compare.compare(_result(), _result(status="invalid"))
    assert any("invalid" in m for m in mismatches)


# -- runner: fit, busy, dry run ---------------------------------------------------------------------


def test_tier_fit_and_busy() -> None:
    four = tiers.Tier("T4", 4, 16)
    assert tiers.fit(four, 48, 188, tiers.SIBLINGS_STANDARD)[0]
    ok, why = tiers.fit(tiers.Tier("T48", 48, 192), 48, 188, tiers.SIBLINGS_STANDARD)
    assert not ok
    assert "needs" in why  # a misfit says why
    assert tiers.fit(tiers.Tier("T24", 24, 96), 48, 188, tiers.SIBLINGS_KNEE)[0]  # the knee runs 24 + 16 CPUs here
    assert not tiers.fit(tiers.Tier("T32", 32, 128), 48, 188, tiers.SIBLINGS_KNEE)[0]  # T32 does not fit
    assert tiers.busy(84.9, 10) is None
    assert "CPU" in tiers.busy(85, 10)  # type: ignore[operator]
    assert "memory" in tiers.busy(10, 90)  # type: ignore[operator]
    reads = iter([(90.0, 10.0)] * 3)
    slept: list[float] = []
    reason = tiers.wait_until_quiet(lambda: next(reads), 3, 5.0, sleep=slept.append)
    assert reason and "CPU" in reason  # still busy after the retries: refuse
    assert slept == [5.0, 5.0]
    calm = iter([(90.0, 10.0), (20.0, 10.0)])
    assert tiers.wait_until_quiet(lambda: next(calm), 3, 1.0, sleep=lambda _s: None) is None
    assert tiers.cpuset_string([0, 1, 2, 5, 7, 8]) == "0-2,5,7-8"
    with pytest.raises(ValueError, match="bad tier"):
        tiers.parse_tiers("T4:4")
    with pytest.raises(ValueError, match="idle cores"):
        tiers.assign_cpusets(list(range(5)), 4, tiers.SIBLINGS_STANDARD)
    sets = tiers.assign_cpusets(list(range(48)), 16, tiers.SIBLINGS_KNEE)
    cpus = [c for s in sets.values() for part in s.split(",") for c in range(int(part.split("-")[0]), int(part.split("-")[-1]) + 1)]
    assert len(cpus) == len(set(cpus)) == 16 + 16 + 1 + 1  # disjoint: app, siblings, fake provider, aux


def test_dry_run_golden() -> None:
    env = {
        **os.environ, "BENCH_TIERS": "T2:2:8 T4:4:16 T48:48:192", "BENCH_ORDER": ",".join(map(str, range(48))),
        "BENCH_HOST_CPUS": "48", "BENCH_HOST_MEM_GB": "188",
    }
    out = subprocess.run(
        ["bash", str(_ROOT / "integration-tests/perf/run_tiers_e2e.sh"), "--scenario", "b05", "--dry-run"],
        env=env, capture_output=True, text=True, check=False, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines() == [
        "RUN T2 scenario=b05 cpus=2 mem=8g app=0-1 neo4j=2-5 qdrant=6-9 redis=10 mongo=11-12 fake_ai=13 aux=14",
        "RUN T4 scenario=b05 cpus=4 mem=16g app=0-3 neo4j=4-7 qdrant=8-11 redis=12 mongo=13-14 fake_ai=15 aux=16",
        "SKIP T48: needs 61 CPUs, host has 48 with 4 kept free",
    ]
    assert not subprocess.run(["bash", "-n", str(_ROOT / "integration-tests/perf/run_tiers_e2e.sh")], check=False).returncode


def test_bench_compose_pins_every_container() -> None:
    """Every sized service has a limit and a cpuset that fails closed when unset; siblings do not share one."""
    class Loader(yaml.SafeLoader):
        """Compose's merge tags, read as the value they carry."""

    for tag in ("!override", "!reset"):
        Loader.add_multi_constructor(tag, lambda ld, _suffix, node: ld.construct_object(node, deep=True)
                                     if not isinstance(node, yaml.ScalarNode) else None)
        Loader.add_constructor(tag, lambda ld, node: ld.construct_sequence(node, deep=True)
                               if isinstance(node, yaml.SequenceNode) else (
                                   ld.construct_mapping(node, deep=True) if isinstance(node, yaml.MappingNode) else None))
    doc = yaml.load((_ROOT / "deployment/docker-compose/docker-compose.bench.yml").read_text(), Loader=Loader)
    sets = []
    for name in ("pipeshub-ai", "neo4j", "qdrant", "redis", "mongodb", "fake-ai", "mailpit", "minio"):
        svc = doc["services"][name]
        assert svc["cpuset"].startswith("${BENCH_") and ":?" in svc["cpuset"], name
        assert svc["mem_limit"] and svc["memswap_limit"] == svc["mem_limit"], name
        sets.append(svc["cpuset"])
    assert len(set(sets)) == len(sets) - 1  # mailpit and minio share the auxiliary set, nothing else does
    assert doc["services"]["mailpit"]["cpuset"] == doc["services"]["minio"]["cpuset"]
    assert doc["services"]["pipeshub-ai"]["ports"] == ["${BENCH_PORT_HTTP:?set BENCH_PORT_HTTP}:3000"]
    assert "ports" not in doc["services"]["fake-ai"]
    assert "pipeshub-ai-replica" not in doc["services"]  # a replica run is not built, so nothing pretends to it


# -- fake provider ----------------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0


def test_fake_quota() -> None:
    clock = _Clock()
    gate = fake.QuotaGate(concurrency=2, rpm=3, clock=lambda: clock.now)
    assert gate.try_enter() is None
    assert gate.try_enter() is None
    assert gate.try_enter() == 1.0  # a third in flight is over the concurrency quota
    gate.leave()
    assert gate.try_enter() is None  # a slot freed: the third request start of the minute
    gate.leave()
    gate.leave()
    retry = gate.try_enter()  # 4th start inside the minute: rpm quota
    assert retry is not None and retry >= 1.0
    clock.now = 61.0
    assert gate.try_enter() is None  # the window moved on
    off = fake.QuotaGate()
    assert all(off.try_enter() is None for _ in range(50))  # quota off: never refuses


def _serve(argv: list[str]) -> tuple[ThreadingHTTPServer, str]:
    import argparse

    ns = argparse.Namespace(base_ms=150.0, per_kb_ms=0.0, fake_embeddings="--fake-embeddings" in argv,
                            embedding_dim=8, quota_concurrency=1 if "--quota" in argv else 0, quota_rpm=0)
    server = ThreadingHTTPServer(("127.0.0.1", 0), fake.make_handler(fake.Config(ns)))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _post(url: str, body: dict) -> tuple[int, dict, dict]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read()), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read()), dict(exc.headers)


def test_fake_provider_answers_and_embeddings_are_opt_in() -> None:
    server, url = _serve([])
    try:
        status, body, _ = _post(f"{url}/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}]})
        assert status == 200
        assert body["choices"][0]["message"]["content"].startswith("Fake answer")
        schema = {"type": "object", "properties": {"title": {"type": "string"}, "n": {"type": "integer"},
                                                    "tags": {"type": "array", "items": {"type": "string"}},
                                                    "kind": {"enum": ["a", "b"]}}}
        _, body, _ = _post(f"{url}/v1/chat/completions", {
            "messages": [{"role": "user", "content": "x"}],
            "response_format": {"type": "json_schema", "json_schema": {"schema": schema}}})
        parsed = json.loads(body["choices"][0]["message"]["content"])
        assert isinstance(parsed["title"], str) and isinstance(parsed["n"], int) and parsed["kind"] == "a"
        image = {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}}]}
        _, body, _ = _post(f"{url}/v1/chat/completions", {"messages": [image]})
        assert "Page text" in body["choices"][0]["message"]["content"]
        assert _post(f"{url}/v1/embeddings", {"input": "x"})[0] == 404  # real embedding is the default
    finally:
        server.shutdown()
    server, url = _serve(["--fake-embeddings"])
    try:
        status, body, _ = _post(f"{url}/v1/embeddings", {"input": ["a", "b"]})
        assert status == 200 and len(body["data"]) == 2 and len(body["data"][0]["embedding"]) == 8
    finally:
        server.shutdown()


def test_fake_provider_returns_429_over_quota() -> None:
    server, url = _serve(["--quota"])
    results: list[tuple[int, dict]] = []

    def call() -> None:
        status, _, headers = _post(f"{url}/v1/chat/completions", {"messages": [{"role": "user", "content": "q"}]})
        results.append((status, headers))

    try:
        threads = [threading.Thread(target=call) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        server.shutdown()
    statuses = sorted(s for s, _ in results)
    assert statuses.count(200) >= 1
    assert statuses.count(429) >= 1
    assert all("Retry-After" in h for s, h in results if s == 429)


def test_refused_oauth_scopes_are_dropped_and_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stack from an older checkout refuses a scope the suite asks for: drop it, report it, carry on."""
    import local_auth

    message = 'HTTP 400: {"error":{"code":"OAUTH_INVALID_SCOPE","metadata":{"invalidScopes":["code:write","x:y"]}}}'
    assert bc.parse_invalid_scopes(message) == ["code:write", "x:y"]
    assert bc.parse_invalid_scopes("HTTP 500: boom") == []
    monkeypatch.setattr(local_auth, "TEST_CLIENT_SCOPES", ("openid", "code:write", "x:y"))
    calls: list[tuple[str, ...]] = []

    def fake(_base: str) -> None:
        calls.append(local_auth.TEST_CLIENT_SCOPES)
        if "code:write" in local_auth.TEST_CLIENT_SCOPES:
            raise RuntimeError(message)

    monkeypatch.setattr(bc, "ensure_client_credentials", fake)
    assert bc.ensure_credentials("http://x") == ["code:write", "x:y"]
    assert calls[-1] == ("openid",)

    def broken(_base: str) -> None:
        raise RuntimeError("HTTP 401: nope")

    monkeypatch.setattr(local_auth, "TEST_CLIENT_SCOPES", ("openid", "kb:upload", "code:write"))
    refused = 'HTTP 400: {"error":{"metadata":{"invalidScopes":["kb:upload"]}}}'

    def needs_upload(_base: str) -> None:
        if "kb:upload" in local_auth.TEST_CLIENT_SCOPES:
            raise RuntimeError(refused)

    monkeypatch.setattr(bc, "ensure_client_credentials", needs_upload)
    with pytest.raises(RuntimeError, match="kb:upload"):  # a scope the run needs stops it at credentials
        bc.ensure_credentials("http://x")

    monkeypatch.setattr(bc, "ensure_client_credentials", broken)
    with pytest.raises(RuntimeError, match="401"):
        bc.ensure_credentials("http://x")


# -- the live path against a fake knowledge base ----------------------------------------------------


class _FakeKB:
    """A KB client: each upload takes *upload_s*, every record completes *index_s* after it. With *dedup*
    a record whose bytes match an earlier record takes that record's virtualRecordId, as the product does."""

    def __init__(self, upload_s: float = 0.0, index_s: float = 0.05, dedup: bool = True, shared: str = "",
                 copy_status: str = "COMPLETED", fail_upload_of: str = "") -> None:
        self.upload_s, self.index_s, self.dedup, self.shared = upload_s, index_s, dedup, shared
        self.copy_status, self.fail_upload_of = copy_status, fail_upload_of
        self.lock = threading.Lock()
        self.records: dict[str, dict] = {}
        self.upload_starts: list[float] = []
        self.vrid_by_md5: dict[str, str] = {}

    def upload_file(self, kb_id: str, name: str, data: bytes, mimetype: str = "") -> dict:
        with self.lock:
            self.upload_starts.append(time.time())
        if self.fail_upload_of and name.endswith(self.fail_upload_of) and not name.startswith("dup-"):
            raise RuntimeError("upload refused")
        time.sleep(self.upload_s)
        now = time.time() * 1000
        md5 = hashlib.md5(data).hexdigest()
        with self.lock:
            if self.shared and name.endswith(self.shared):
                vrid = "shared"
            elif self.dedup:
                # the product's rule: a twin is found only once its record is final
                twin = next((r for r in self.records.values() if r["md5"] == md5 and r["done"] <= now), None)
                vrid = twin["vrid"] if twin else f"v-{name}"
            else:
                vrid = f"v-{name}"
            rid = f"r-{name}"
            self.records[rid] = {"id": rid, "name": name, "created": now, "vrid": vrid, "md5": md5,
                                 "done": now + self.index_s * 1000,
                                 "status": self.copy_status if name.startswith("dup-") else "COMPLETED"}
        return {"records": [{"recordId": rid}]}

    def list_records(self, kb_id: str, page: int = 1, limit: int = 200) -> dict:
        now = time.time() * 1000
        with self.lock:
            items = [{"id": r["id"], "name": r["name"], "indexingStatus": r.get("status", "COMPLETED") if now >= r["done"] else "IN_PROGRESS"}
                     for r in self.records.values()]
        return {"items": items, "pagination": {"totalPages": 1}}

    def get_record(self, rid: str) -> dict:
        r = self.records[rid]
        return {"record": {"indexingStatus": r.get("status", "COMPLETED"), "lastIndexTimestamp": r["done"],
                           "createdAtTimestamp": r["created"], "virtualRecordId": r["vrid"]}}


@pytest.fixture(scope="module")
def live_corpus(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, object, dict, dict]:
    out = tmp_path_factory.mktemp("live-cost")
    manifest = _CORPUS.generate(out, profile="cost-smoke", seed=9137)
    by_kind, entries = bc.corpus_templates(manifest, out)
    return out, manifest, by_kind, entries


def _args(**kw: object) -> argparse.Namespace:
    base = {"upload_workers": 4, "timeout": 60.0, "poll_interval": 0.05, "inject_duplicate": False, "label": "t",
            "tier_cpus": 4, "tier_mem_gb": 16, "scenario": "b05", "seed": 1, "neo4j_connection_budget": None,
            "pg_max_connections": None, "fake_embeddings": False}
    base.update(kw)
    return argparse.Namespace(**base)


def _plan(kind: str, offsets: list[float], template: str, control: bool = False) -> bc.Plan:
    arrivals = [bc.Arrival(n, kind, at, "probe", template) for n, at in enumerate(offsets)]
    if control:
        arrivals.append(bc.Arrival(len(arrivals), kind, offsets[0] + 0.01, "control", template, control_of=0))
    return bc.Plan("t", arrivals, "cost-smoke")


def _build(plan: bc.Plan, manifest: object, run: bc.Run, args: argparse.Namespace, t0: float,
            sampler: bc.Sampler | None = None, siblings: dict | None = None, **kw: object) -> dict:
    sampler = sampler or bc.Sampler("http://127.0.0.1:1", None, {}, 60, None, None, None)  # never started
    return bc.build_result(args, plan, manifest, run, sampler, siblings or {}, {}, bc.datetime.now(bc.UTC), t0,
                           time.time(), 0, [], **kw)


def _live(live_corpus: tuple, kb: _FakeKB, plan: bc.Plan, args: argparse.Namespace) -> tuple[bc.Run, float]:
    out, _manifest, _by_kind, entries = live_corpus
    run, t0 = bc.Run(), time.time()
    bc._run_open_loop(plan, kb, "kb", run, out, entries, "t", t0, args)
    return run, t0


def test_duplicate_control_fails_when_the_product_indexes_the_copy_again(live_corpus: tuple) -> None:
    """Same bytes, own virtualRecordId, indexed: the product did the work twice. The control must say so."""
    _out, manifest, by_kind, _entries = live_corpus
    plan = _plan("small-text", [0.0, 0.1], by_kind["small-text"][0], control=True)
    for dedup, want in ((True, "pass"), (False, "failed")):
        args = _args(inject_duplicate=True)
        run, t0 = _live(live_corpus, _FakeKB(index_s=0.4, dedup=dedup), plan, args)
        result = _build(plan, manifest, run, args, t0)
        nc = result["metrics"]["negative_control"]
        assert result["verdict"]["status"] == want, result["verdict"]
        assert nc["deduplicated"] is dedup
        assert nc["controls"][0]["md5_match"] is True
        assert nc["controls"][0]["outcome"] == ("deduplicated" if dedup else "indexed_separately")


def test_control_is_judged_by_md5_and_vrid_not_by_its_label() -> None:
    orig = _sample(0, md5="a", virtual_record_id="v0")

    def judge(**kw: object) -> dict:
        ctl = _sample(1, md5=kw.pop("md5", "a"), virtual_record_id=kw.pop("vrid", "v0"), control=True,  # type: ignore[arg-type]
                      role="control", control_of=0, **kw)
        return bc.judge_control([orig, ctl])["controls"][0]  # type: ignore[index]

    assert judge()["outcome"] == "deduplicated"
    assert judge(vrid="v1")["outcome"] == "indexed_separately"
    assert judge(md5="b", vrid="v1")["outcome"] == "bytes_differ_from_original"
    assert judge(vrid=None)["outcome"] == "no_virtual_record_id"
    assert judge(status="IN_PROGRESS", vrid="v1")["outcome"] == "not_deduplicated"
    assert judge(upload_error="boom")["outcome"] == "upload_failed"
    assert bc.judge_control([orig]) is None
    # a control with other bytes is not rejected as a duplicate: it is not one
    other = _sample(1, md5="zzz", control=True, role="control", control_of=0)
    _acc, rej = bc.filter_samples([orig, other])
    assert "duplicate_md5" not in rej and [s.seq for s in rej["control_not_a_duplicate"]] == [1]
    # and the original is never blamed for sharing its vrid with the control
    twin = _sample(1, md5="a", virtual_record_id="v0", control=True, role="control", control_of=0)
    acc, _rej = bc.filter_samples([orig, twin])
    assert [s.seq for s in acc] == [0]


def test_a_control_never_races_its_original_for_the_bytes() -> None:
    """Whichever worker starts first, the control waits for the original's bytes."""
    cb = bc.ControlBytes()
    got: list[tuple[bytes, str] | None] = []
    t = threading.Thread(target=lambda: got.append(cb.get(0, 5.0)))
    t.start()
    time.sleep(0.1)
    assert not got  # waiting
    cb.put(0, b"abc", "n")
    t.join(5)
    assert got == [(b"abc", "n")]
    assert bc.ControlBytes().get(7, 0.05) is None  # an original that never started: no bytes, no guess


def test_the_copy_is_sent_only_after_its_original_is_final(live_corpus: tuple) -> None:
    """Beside its original (still being indexed) the copy would race it and both would be indexed: no verdict on
    the dedup. The control goes after the original reached a final status, and a copy whose original never does
    fails with that cause."""
    _out, _manifest, by_kind, entries = live_corpus
    plan = _plan("small-text", [0.0], by_kind["small-text"][0], control=True)
    kb = _FakeKB(index_s=0.6)
    run, _t0 = _live(live_corpus, kb, plan, _args(inject_duplicate=True))
    original, control = run.samples[0], run.samples[1]
    assert control.send_ms is not None and control.send_ms >= original.last_index_ms  # after the original finished
    out = live_corpus[0]
    arrival = bc.Arrival(1, "small-text", 0.0, "control", by_kind["small-text"][0], control_of=0)
    cb, lonely = bc.ControlBytes(), bc.Run()
    cb.put(0, b"abc", "n")
    sample = bc._upload_one(arrival, _FakeKB(), "kb", lonely, out, entries, "t", time.time(), cb, 0.1)
    assert "did not reach a final status" in sample.upload_error and sample.control


def test_control_whose_original_never_started_fails_the_run(live_corpus: tuple) -> None:
    out, _manifest, by_kind, entries = live_corpus
    arrival = bc.Arrival(1, "small-text", 0.0, "control", by_kind["small-text"][0], control_of=0)
    run = bc.Run()
    sample = bc._upload_one(arrival, _FakeKB(), "kb", run, out, entries, "t", time.time(), bc.ControlBytes(), 0.05)
    assert sample.upload_error and sample.control
    assert bc.judge_control([sample])["controls"][0]["outcome"] == "upload_failed"  # type: ignore[index]


def test_open_loop_gates_on_the_schedule_and_never_waits_for_the_system(live_corpus: tuple) -> None:
    """12 arrivals 0.05 s apart, uploads of 0.5 s, 2 workers: the schedule is kept in the samples and the
    client queueing shows in the gated latency, not in a later arrival."""
    _out, _manifest, by_kind, _entries = live_corpus
    offsets = [round(0.05 * n, 3) for n in range(12)]
    kb = _FakeKB(upload_s=0.5)
    run, t0 = _live(live_corpus, kb, _plan("small-text", offsets, by_kind["small-text"][0]), _args(upload_workers=2))
    samples = sorted(run.samples.values(), key=lambda s: s.seq)
    assert len(samples) == 12
    for smp, at in zip(samples, offsets, strict=True):
        assert smp.scheduled_arrival_ms == pytest.approx((t0 + at) * 1000.0, abs=1.0)  # the schedule, not the send
    last = samples[-1]
    assert bc.gated_latency_s(last) - bc.diagnostic_latencies(last)["from_send_s"] > 2.0


def test_open_loop_starts_every_upload_on_schedule_when_the_system_is_slow(live_corpus: tuple) -> None:
    _out, _manifest, by_kind, _entries = live_corpus
    offsets = [round(0.1 * n, 3) for n in range(10)]
    kb = _FakeKB(upload_s=1.0)
    _run, t0 = _live(live_corpus, kb, _plan("small-text", offsets, by_kind["small-text"][0]), _args(upload_workers=16))
    starts = sorted(kb.upload_starts)
    assert all(abs((st - t0) - at) < 0.25 for st, at in zip(starts, offsets, strict=True)), [round(x - t0, 2) for x in starts]


def test_arrivals_still_queued_at_the_deadline_are_counted(live_corpus: tuple) -> None:
    """8 arrivals, 2 workers, uploads of 1 s, a 1.2 s deadline: every arrival is a sample, the rest failed."""
    _out, manifest, by_kind, _entries = live_corpus
    plan = _plan("small-text", [0.0] * 8, by_kind["small-text"][0])
    args = _args(upload_workers=2, timeout=1.2)
    run, t0 = _live(live_corpus, _FakeKB(upload_s=1.0), plan, args)
    assert len(run.samples) == 8
    result = _build(plan, manifest, run, args, t0)
    assert result["metrics"]["failures"]["upload"] >= 4
    assert result["verdict"]["status"] == "failed"


def test_saturated_sibling_in_the_sampler_makes_the_result_invalid(live_corpus: tuple) -> None:
    _out, manifest, by_kind, _entries = live_corpus
    plan = _plan("small-text", [0.0], by_kind["small-text"][0])
    args = _args()
    run, t0 = _live(live_corpus, _FakeKB(), plan, args)
    sampler = bc.Sampler("http://127.0.0.1:1", None, {"neo4j": 4}, 60, None, None, None)
    sampler.sibling_cpu["neo4j"] = [(0.0, 0), (5.0, 18_000_000), (10.0, 36_000_000)]  # 90 % of 4
    result = _build(plan, manifest, run, args, t0, sampler, {"neo4j": 4})
    assert result["verdict"]["status"] == "invalid" and result["verdict"]["exit_code"] == 3
    # the average over the whole run, not the first two readings: a burst early then idle is under the limit
    sampler.sibling_cpu["neo4j"] = [(0.0, 0), (1.0, 4_000_000), (100.0, 4_000_000)]
    assert _build(plan, manifest, run, args, t0, sampler, {"neo4j": 4})["verdict"]["status"] == "pass"


def test_a_shared_vrid_rejects_both_probes_and_invalidates(live_corpus: tuple) -> None:
    _out, manifest, by_kind, _entries = live_corpus
    plan = _plan("small-text", [0.0, 0.0, 0.0, 0.0], by_kind["small-text"][0])
    args = _args()
    run, t0 = _live(live_corpus, _FakeKB(shared="small-text-00.txt"), plan, args)
    result = _build(plan, manifest, run, args, t0)
    assert result["metrics"]["rejected"] == {"shared_vrid": 4}
    assert result["verdict"]["status"] == "invalid" and result["verdict"]["exit_code"] == 3


def test_sample_rows_carry_the_evidence_for_the_acceptance_decision(live_corpus: tuple) -> None:
    _out, manifest, by_kind, _entries = live_corpus
    plan = _plan("small-text", [0.0, 0.1], by_kind["small-text"][0], control=True)
    args = _args(inject_duplicate=True)
    run, t0 = _live(live_corpus, _FakeKB(), plan, args)
    rows = _build(plan, manifest, run, args, t0)["samples"]
    assert all(r["md5"] and r["virtual_record_id"] and r["record_id"] and r["record_count"] == 1 for r in rows)
    control = next(r for r in rows if r["role"] == "control")
    original = next(r for r in rows if r["seq"] == control["control_of"])
    assert control["md5"] == original["md5"] and control["virtual_record_id"] == original["virtual_record_id"]


def test_skew_rule_fires_on_the_scheduled_arrival_alone() -> None:
    """Indexed 5 s before it was scheduled, while createdAt agrees with the index time: only the schedule clause sees it."""
    early = _sample(0, scheduled_arrival_ms=1_000_000.0, last_index_ms=995_000.0, created_ms=None)
    accepted, rejected = bc.filter_samples([early])
    assert not accepted and [s.seq for s in rejected["skewed_timestamp"]] == [0]


def test_fit_counts_the_siblings_cpus() -> None:
    """A tier that fits alone (32 + fake + aux of 44 usable) must not fit with 11 sibling CPUs, the fake and the aux set."""
    ok, why = tiers.fit(tiers.Tier("T32", 32, 64), 48, 188, tiers.SIBLINGS_STANDARD)
    assert not ok and "CPUs" in why
    assert tiers.fit(tiers.Tier("T32", 32, 64), 48, 188, {})[0]


def test_pdf_salt_overlay_draws_a_pattern_that_differs_per_token() -> None:
    def blocks(token: str) -> set[bytes]:
        return {ln for ln in salt._overlay(token).splitlines() if ln.endswith(b" re f")}

    patterns = {frozenset(blocks(f"run:{n}")) for n in range(20)}
    assert len(patterns) == 20 and all(patterns)


# -- unpinned siblings, chaos, budgets ---------------------------------------------------------------


def test_an_unpinned_sibling_is_unmeasured_and_invalidates_the_run() -> None:
    assert bc.cpu_limit("", 0) == 0.0  # no cpuset and no limit: no share can be computed
    assert bc.cpu_limit("0-3", 0) == 4.0
    assert bc.cpu_limit("", 500_000_000) == 0.5  # judged against its own limit, not rounded up
    assert bc.cpu_limit("0-7", 2_000_000_000) == 2.0  # the tighter of the two
    rep = bc.sibling_report({"x": [(0.0, 0), (10.0, 40_000_000)]}, {"x": bc.cpu_limit("", 0)})
    assert rep["unmeasured"] == ["x"] and rep["saturated"] == []
    v = _verdict(unmeasured_siblings=["x"])
    assert v["status"] == "invalid" and "x" in v["reasons"][0]
    busy_half = bc.sibling_report({"m": [(0.0, 0), (10.0, 4_000_000)]}, {"m": bc.cpu_limit("", 500_000_000)})
    assert busy_half["saturated"] == ["m"]  # 0.4 core on a 0.5-core limit: 80 %


def _kill_recorder(killed: list[str], hit: list[int] | None = None):
    def kill(container: str) -> list[int] | None:
        killed.append(container)
        return [7] if hit is None else hit
    return kill


def test_chaos_kill_fires_once_and_is_cancelled_when_the_run_ends_first() -> None:
    killed: list[str] = []
    fired = bc.ChaosKill("app", 0.05, kill=_kill_recorder(killed), top=lambda: "PID PPID ARGS\n")
    assert fired.timer.daemon  # never keeps the process alive
    fired.start()
    fired.timer.join(timeout=10)
    assert not fired.timer.is_alive()
    assert killed == ["app"] and fired.fired
    late = bc.ChaosKill("app", 0.3, kill=_kill_recorder(killed), top=lambda: "PID PPID ARGS\n")
    late.start()
    late.cancel()
    late.timer.join(timeout=10)
    assert not late.timer.is_alive()
    assert killed == ["app"] and not late.fired


def test_chaos_verdict_needs_the_kill_and_a_restart() -> None:
    v = _verdict(chaos_expected=True, chaos_fired=False)
    assert v["status"] == "invalid" and "kill" in v["reasons"][0]
    v = _verdict(chaos_expected=True, chaos_fired=True, restarts=0)
    assert v["status"] == "failed" and "did not restart" in v["reasons"][0]
    v = _verdict(chaos_expected=True, chaos_fired=True, restarts=1)
    assert v["status"] == "pass" and v["disturbed"] is True
    with pytest.raises(SystemExit, match="app-container"):
        bc.execute(bc.parse_args(["--scenario", "smoke", "--label", "x", "--chaos", "kill-app-at=5",
                                  "--base-url", "http://127.0.0.1:1"]))


def test_bench_compose_never_hands_the_app_to_docker_s_restart_policy() -> None:
    # Docker applies no restart policy to a container it was told to kill, so the chaos run kills the
    # indexing process and the app's process monitor restarts it; a policy would only hide a real crash.
    compose = (_ROOT / "deployment/docker-compose/docker-compose.bench.yml").read_text()
    app = compose.split("  neo4j:")[0]
    assert 'restart: "no"' in app and "BENCH_APP_RESTART" not in compose


def test_smoke_budgets_are_enforced() -> None:
    assert bc.budget_violations(599.0, 1799.0, 600.0, 1800.0) == []
    assert any("p95" in b for b in bc.budget_violations(601.0, 10.0, 600.0, 1800.0))
    assert any("wall" in b for b in bc.budget_violations(10.0, 1801.0, 600.0, 1800.0))
    assert bc.budget_violations(None, 10.0, 600.0, None)  # no latency at all cannot pass a latency budget
    assert bc.budget_violations(9999.0, 9999.0, None, None) == []  # not asked for
    v = _verdict(budget_failures=["p95 700s > budget 600s"])
    assert v["status"] == "failed" and v["exit_code"] == bc.EXIT_FAILED


def test_workflow_smoke_enforces_budgets_and_weekly_runs_the_fake_embedding_control() -> None:
    wf = yaml.safe_load((_ROOT / ".github/workflows/perf-costclass.yml").read_text())
    steps = {s.get("name", ""): s.get("run", "") for s in wf["jobs"]["costclass"]["steps"]}
    smoke = steps["Smoke"]
    assert "--max-p95-s 600" in smoke and "--max-wall-s 1800" in smoke and "--fail-on-log-errors" in smoke
    weekly = next(v for k, v in steps.items() if k.startswith("Weekly"))
    assert "BENCH_FAKE_AI_ARGS=\"--fake-embeddings\"" in weekly
    assert "extra=(--fake-embeddings --fake-ai-container" in weekly and '"${extra[@]}"' in weekly
    assert _weekly_legs()[0]["fake_embeddings"] is True  # the control is the first leg


# -- the fake provider and the product's log ---------------------------------------------------------


def test_the_fake_names_no_departments_the_organization_does_not_have() -> None:
    """A made-up department name sends the product down its "not found" path (a warning per record): the fake
    answers an empty list for it, and still fills every other list."""
    schema = {"type": "object", "properties": {
        "departments": {"type": "array", "items": {"type": "string"}},
        "topics": {"type": "array", "items": {"type": "string"}}}}
    body = {"messages": [{"role": "user", "content": "classify"}],
            "response_format": {"type": "json_schema", "json_schema": {"schema": schema}}}
    answer = json.loads(fake.chat_answer(body)["choices"][0]["message"]["content"])
    assert answer["departments"] == [] and len(answer["topics"]) == 1
    required = {"type": "object", "properties": {"departments": {"type": "array", "minItems": 1, "items": {"type": "string"}}}}
    assert len(fake.example_from_schema(required)["departments"]) == 1  # a schema that demands one still gets one


def test_the_fake_answers_the_count_a_prompt_asks_for() -> None:
    prompt = ("Rows Data (Total: 6 rows):\nRow 1: {}\nRow 6: {}\nYou must return EXACTLY 6 descriptions - one for each row.")
    assert fake.requested_count(prompt) == 6
    assert fake.requested_count("EXACTLY one description") is None
    assert fake.requested_count("Row 1: a\nRow 4: b") == 4
    schema = {"type": "object", "properties": {"descriptions": {"type": "array", "items": {"type": "string"}},
                                                "headers": {"type": "array", "items": {"type": "string"}}}}
    body = {"messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_schema", "json_schema": {"schema": schema}}}
    answer = json.loads(fake.chat_answer(body)["choices"][0]["message"]["content"])
    assert len(answer["descriptions"]) == 6 and len(set(answer["descriptions"])) == 6
    assert len(answer["headers"]) == 1  # a list nothing counts keeps its default length
    tool_body = {"messages": body["messages"], "tools": [{"type": "function", "function": {
        "name": "emit", "parameters": schema}}], "tool_choice": {"type": "function", "function": {"name": "emit"}}}
    args = json.loads(fake.chat_answer(tool_body)["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"])
    assert len(args["descriptions"]) == 6
    # a reflection round repeats the count, and the last request wins; maxItems is honoured
    assert fake.requested_count("exactly 3 descriptions\n...\nexactly 6 descriptions are required") == 6
    capped = {"type": "object", "properties": {"descriptions": {"type": "array", "maxItems": 2, "items": {"type": "string"}}}}
    assert len(fake.example_from_schema(capped, count=6)["descriptions"]) == 2


def test_a_product_error_line_fails_the_run_unless_it_is_allowed() -> None:
    log = ("2026-11-15 10:00:00 - app - INFO - ok\n2026-11-15 10:00:01 - app - ERROR - streaming.py:1442 - Reflection failed\n"
           '{"level":"error","msg":"boom"}\n2026-11-15 WARNING no errors here\nraised 0 errors\n')
    found = bc.scan_error_lines(log)
    assert len(found) == 2 and "Reflection failed" in found[0]
    assert bc.scan_error_lines(log, (("Reflection failed", "fake model, known"), ('"level":"error"', "node, known"))) == []
    v = _verdict(log_errors=found)
    assert v["status"] == "failed" and "ERROR" in v["reasons"][0]
    assert _verdict(log_errors=[])["status"] == "pass"


def test_variant_flags_that_changed_nothing_are_gone_and_the_ones_left_take_effect(monkeypatch: pytest.MonkeyPatch) -> None:
    for flag in ("--store", "--broker", "--source", "--replicas", "--graph-db"):
        with pytest.raises(SystemExit):
            bc.parse_args(["--scenario", "b05", "--label", "x", flag, "x"])
    with pytest.raises(SystemExit):
        bc.parse_args(["--scenario", "b07", "--label", "x"])
    args = bc.parse_args(["--scenario", "b05", "--label", "x", "--fake-ai-base-ms", "2000", "--fake-embeddings"])
    with pytest.raises(SystemExit, match="fake-ai-container"):
        bc.check_stack(args)
    args = bc.parse_args(["--scenario", "b05", "--label", "x", "--fake-ai-base-ms", "2000", "--fake-ai-container", "f"])
    monkeypatch.setattr(bc, "fake_ai_stats", lambda _c: {"config": {"base_ms": 300.0, "fake_embeddings": False}})
    with pytest.raises(SystemExit, match="not applied"):
        bc.check_stack(args)  # the flag did not reach the fake: the run stops instead of being labelled
    monkeypatch.setattr(bc, "fake_ai_stats", lambda _c: {"config": {"base_ms": 2000.0, "fake_embeddings": False}})
    assert bc.check_stack(args)["fake_ai"]["base_ms"] == 2000.0
    emb = bc.parse_args(["--scenario", "b05", "--label", "x", "--fake-embeddings", "--fake-ai-container", "f"])
    with pytest.raises(SystemExit, match="embeddings was not applied"):
        bc.check_stack(emb)
    compose = (_ROOT / "deployment/docker-compose/docker-compose.bench.yml").read_text()
    assert "BENCH_FAKE_AI_ARGS" in compose.split("command:")[1].split("\n")[0]
    stats = fake.Config(fake.argparse.Namespace(base_ms=2000.0, per_kb_ms=2.0, fake_embeddings=True, embedding_dim=8,
                                                quota_concurrency=0, quota_rpm=0))
    assert (stats.base_ms, stats.fake_embeddings) == (2000.0, True)


# -- the tier runner ---------------------------------------------------------------------------------


def _stub_tree(tmp_path: Path, bench_rc: int = 0, fail_up_for: str = "") -> tuple[Path, Path, dict]:
    stub = tmp_path / "bin"
    stub.mkdir()
    log = tmp_path / "calls.log"
    (stub / "docker").write_text(
        f'#!/bin/sh\necho "docker $* [fake=$BENCH_FAKE_AI_ARGS]" >> {log}\n'
        f'case "$*" in *{fail_up_for or "NEVER-MATCHES"}*" up -d"*) exit 1;; esac\nexit 0\n')
    (stub / "curl").write_text(
        '#!/bin/sh\ncat >/dev/null\nfor a; do case "$a" in *health*) echo \'{"status":"healthy"}\';; esac; done\n'
        'case "$*" in *authenticate*) echo \'{"accessToken":"t"}\';; esac\nexit 0\n')
    (stub / "openssl").write_text("#!/bin/sh\necho abcdef\n")
    for f in stub.iterdir():
        f.chmod(0o755)
    tree = tmp_path / "tree"
    for d in ("integration-tests/perf", "deployment/docker-compose"):
        (tree / d).mkdir(parents=True)
    for name in ("run_tiers_e2e.sh", "tiers.py"):
        (tree / "integration-tests/perf" / name).write_text((_ROOT / "integration-tests/perf" / name).read_text())
    # The host's load is injected: a quiet reading, so these tests never read the real host and cannot go red
    # because the machine they run on is busy. The real reader is exercised by its own tests.
    tiers_copy = tree / "integration-tests/perf/tiers.py"
    real = "def host_usage() -> tuple[float, float]:"
    assert real in tiers_copy.read_text()
    tiers_copy.write_text(tiers_copy.read_text().replace(
        real, f"{real}\n    return 1.0, 1.0\n\n\ndef _real_host_usage() -> tuple[float, float]:"))
    (tree / "integration-tests/perf/bench_costclass.py").write_text(
        "import sys, pathlib\n"
        f"open({str(log)!r}, 'a').write('bench ' + ' '.join(sys.argv[1:]) + '\\n')\n"
        "a = sys.argv\n"
        "if '--output' in a and '--knee-from' not in a:\n"
        "    pathlib.Path(a[a.index('--output') + 1]).write_text('{}')\n"
        f"sys.exit({bench_rc})\n")
    env = {**os.environ, "PATH": f"{stub}:{os.environ['PATH']}", "BENCH_HOST_CPUS": "48", "BENCH_HOST_MEM_GB": "188",
           "BENCH_ORDER": ",".join(map(str, range(48))), "BENCH_QUIET_RETRIES": "1", "BENCH_QUIET_SLEEP": "0",
           "BENCH_OUT": str(tmp_path / "out"), "BENCH_PROJECT": "bench-stub"}
    return tree, log, env


def _runner(tree: Path, env: dict, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(tree / "integration-tests/perf/run_tiers_e2e.sh"), *argv], env=env,
                          capture_output=True, text=True, timeout=90, stdin=subprocess.DEVNULL, check=False)


def test_runner_tests_inject_the_host_load_and_never_read_the_real_host(tmp_path: Path) -> None:
    """The stub tree's tiers.py reports a fixed quiet host, so a busy machine cannot fail the runner tests."""
    tree, _log, env = _stub_tree(tmp_path)
    probe = ("import sys, inspect; sys.path.insert(0, sys.argv[1]); import tiers; "
             "src = inspect.getsource(tiers.host_usage); assert '/proc' not in src, src; print(tiers.host_usage())")
    out = subprocess.run([sys.executable, "-c", probe, str(tree / "integration-tests/perf")], env=env,
                         capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "(1.0, 1.0)"
    quiet = subprocess.run([sys.executable, str(tree / "integration-tests/perf/tiers.py"), "quiet", "--retries", "1",
                            "--sleep", "0"], env=env, capture_output=True, text=True, check=False)
    assert quiet.returncode == 0, quiet.stderr
    # the real reader still reads /proc (its own test): the injection did not replace the production file
    assert "/proc/stat" in (_ROOT / "integration-tests/perf/tiers.py").read_text()


def test_runner_exits_non_zero_and_says_why_when_every_tier_is_skipped(tmp_path: Path) -> None:
    tree, _log, env = _stub_tree(tmp_path)
    env.update({"BENCH_TIERS": "T4:4:16", "BENCH_HOST_CPUS": "4", "BENCH_HOST_MEM_GB": "16", "BENCH_ORDER": "0,1,2,3"})
    p = _runner(tree, env, "--scenario", "b05")
    assert "SKIP T4: needs" in p.stderr
    assert p.returncode != 0 and "nothing was measured" in p.stderr


def test_runner_runs_every_tier_even_after_one_fails_and_reports_each_skip(tmp_path: Path) -> None:
    tree, log, env = _stub_tree(tmp_path, bench_rc=3)
    env["BENCH_TIERS"] = "T2:2:8 T4:4:16 T48:48:192"
    p = _runner(tree, env, "--scenario", "b05")
    benches = [ln for ln in log.read_text().splitlines() if ln.startswith("bench")]
    assert len(benches) == 2 and p.returncode == 3, p.stderr[-400:]  # the failed tier did not stop the next
    assert "SKIP T48: needs" in p.stderr and "2 ran, 1 skipped" in p.stderr
    assert "--fake-ai-container" in benches[0] and "--siblings" in benches[0]


def test_runner_keeps_the_worst_exit_when_a_later_tier_passes(tmp_path: Path) -> None:
    """QA r2 probe: T2 invalid (3) then T4 passes; the runner exits 3 and the failure does not end the loop."""
    tree, _log, env = _stub_tree(tmp_path)
    (tree / "integration-tests/perf/bench_costclass.py").write_text(
        "import sys, pathlib\na = sys.argv\n"
        "if '--output' in a: pathlib.Path(a[a.index('--output') + 1]).write_text('{}')\n"
        "sys.exit(3 if 'local-T2' in ' '.join(a) else 0)\n")
    env["BENCH_TIERS"] = "T2:2:8 T4:4:16"
    p = _runner(tree, env, "--scenario", "b05")
    assert p.returncode == 3 and "2 ran, 0 skipped" in p.stderr, (p.returncode, p.stderr[-300:])


def test_runner_with_every_tier_refused_for_a_busy_host_is_non_zero(tmp_path: Path) -> None:
    """QA r2 probe: nothing ran because the host was not quiet: that is a failure, said so."""
    tree, _log, env = _stub_tree(tmp_path)
    env["BENCH_TIERS"] = "T2:2:8 T4:4:16"
    src = (_ROOT / "integration-tests/perf/tiers.py").read_text()
    anchor = "reason = wait_until_quiet(host_usage, args.retries, args.sleep)"
    assert anchor in src
    (tree / "integration-tests/perf/tiers.py").write_text(src.replace(anchor, 'reason = "cpu 99% (stub)"'))
    p = _runner(tree, env, "--scenario", "b05")
    assert p.returncode != 0 and "not quiet" in p.stderr, (p.returncode, p.stderr[-300:])


def test_runner_starts_a_stack_that_cannot_come_up_and_still_tries_the_next(tmp_path: Path) -> None:
    tree, log, env = _stub_tree(tmp_path, fail_up_for="bench-stub-t2")
    env["BENCH_TIERS"] = "T2:2:8 T4:4:16"
    p = _runner(tree, env, "--scenario", "b05")
    benches = [ln for ln in log.read_text().splitlines() if ln.startswith("bench")]
    assert len(benches) == 1 and "FAIL T2" in p.stderr and p.returncode != 0


def test_runner_hands_the_fake_flags_to_the_stack(tmp_path: Path) -> None:
    tree, log, env = _stub_tree(tmp_path)
    env["BENCH_TIERS"] = "T2:2:8"
    _runner(tree, env, "--scenario", "b05", "--", "--fake-ai-base-ms", "2000", "--fake-embeddings",
            "--chaos", "kill-app-at=5")
    up = next(ln for ln in log.read_text().splitlines() if " up " in ln)
    assert "fake=--base-ms 2000 --fake-embeddings" in up and "restart" not in up


def test_runner_computes_the_knee_after_the_tiers(tmp_path: Path) -> None:
    tree, log, env = _stub_tree(tmp_path)
    env["BENCH_TIERS"] = "T8:8:32 T12:12:48"
    _runner(tree, env, "--scenario", "b06-knee")
    calls = [ln for ln in log.read_text().splitlines() if ln.startswith("bench")]
    assert len(calls) == 3 and all("--scenario b06-knee" in c for c in calls[:2])
    assert calls[2].startswith("bench --knee-from") and calls[2].count("costclass-local-T") == 2
    (tmp_path / "b05").mkdir()
    tree2, log2, env2 = _stub_tree(tmp_path / "b05")
    env2["BENCH_TIERS"] = "T8:8:32"
    _runner(tree2, env2, "--scenario", "b05")
    assert not any("--knee-from" in ln for ln in log2.read_text().splitlines())  # only the knee scenario asks for one


# -- promoted from independent review: percentiles, fresh seeds, the verdict's edges ---------------------


@pytest.mark.parametrize("seed", [9137, 424242, 77])
def test_percentiles_match_numpy_at_small_n_and_with_ties(seed: int) -> None:
    import numpy as np

    rng = random.Random(seed)
    for n in (1, 2, 3, 5, 19, 20, 21, 100):
        values = [float(rng.choice([1, 2, 2, 3, 3, 3, 50])) for _ in range(n)]
        s = bc.summarise(values)
        for key, pct in (("p50", 50), ("p95", 95), ("p99", 99)):
            assert s[key] == pytest.approx(round(float(np.percentile(np.array(values), pct)), 3), abs=1e-3), (n, key)
        assert s["max"] == max(values) and s["n"] == n
    assert bc.summarise([1.0, 2.0])["p95"] == pytest.approx(1.95)
    assert bc.summarise([3.0, 3.0, 3.0, 10.0])["p95"] == pytest.approx(8.95)


@pytest.mark.parametrize("seed", [9137, 424242, 77])
def test_salts_at_other_seeds_parse_stay_unique_and_deterministic(seed: int, tmp_path: Path) -> None:
    import pypdfium2 as pdfium

    first = _CORPUS.generate(tmp_path / "a", profile="cost-standard", seed=seed, scale=0.5)
    again = _CORPUS.generate(tmp_path / "b", profile="cost-standard", seed=seed, scale=0.5)
    assert first.corpus_id == again.corpus_id
    for e in first.entries:
        raw = (tmp_path / "a" / e.path).read_bytes()
        assert raw == (tmp_path / "b" / e.path).read_bytes(), e.path
        tokens = [salt.salt_token(f"run{seed}", n) for n in range(5)]
        salted = [salt.salt_bytes(raw, e.ext, t) for t in tokens]
        assert len({hashlib.md5(x).hexdigest() for x in [raw, *salted]}) == 6, e.path
        assert salt.salt_bytes(raw, e.ext, tokens[0]) == salted[0]
        assert tokens[3] in _text_of(salted[3], e.ext), e.path
        if e.ext == "pdf":
            assert len(pdfium.PdfDocument(salted[3])) == e.pages, e.path


def test_run_verdict_edges() -> None:
    assert _verdict(rejected_n=1)["status"] == "pass"  # 1 % exactly
    assert _verdict(accepted=99, rejected_n=1)["status"] == "invalid"  # 1.01 %
    assert _verdict(accepted=0, rejected_n=0)["status"] == "invalid"
    rep = bc.sibling_report({"n": [(0, 0), (10, 28_000_000)], "q": [(0, 0), (10, 28_000_001)]}, {"n": 4, "q": 4})
    assert rep["saturated"] == ["q"]  # 70 % exactly is valid, a hair above is not
    probes = [_sample(n, md5="same", virtual_record_id=f"v{n}") for n in range(3)]  # a salt that failed
    acc, rej = bc.filter_samples(probes)
    assert [s.seq for s in acc] == [0] and len(rej["duplicate_md5"]) == 2


def test_workflow_is_valid_scheduled_and_wires_the_unit_tests() -> None:
    import re

    wf = yaml.safe_load((_ROOT / ".github/workflows/perf-costclass.yml").read_text())
    on = wf.get("on") or wf.get(True)
    assert {c["cron"] for c in on["schedule"]} == {"30 19 * * *", "0 22 * * 6"}
    assert "workflow_dispatch" in on and "pull_request" not in on
    assert all("uses" in s or "run" in s for s in wf["jobs"]["costclass"]["steps"])
    guards = yaml.safe_load((_ROOT / ".github/workflows/integration-suite-guards.yml").read_text())
    job = guards["jobs"]["integration-suite-guards"]
    run_step = next(s for s in job["steps"] if s.get("name") == "Run the integration-suite guards")
    assert "integration-tests/unit/test_perf_costclass.py" in run_step["env"]["GUARDS"]
    watched = re.compile(next(s for s in job["steps"] if s.get("id") == "scope")["env"]["WATCHED_PATHS"])
    for f in ("integration-tests/perf/salt.py", "integration-tests/perf/fake_ai/server.py", "integration-tests/perf/tiers.py",
              "integration-tests/perf/run_tiers_e2e.sh", "backend/python/tests/perf/parse_bench/corpus.py",
              "deployment/docker-compose/docker-compose.bench.yml", "integration-tests/unit/test_perf_costclass.py",
              "integration-tests/helper/local_auth.py", "integration-tests/perf/stack.py", "integration-tests/perf/compare.py",
              ".github/workflows/perf-costclass.yml"):  # this test reads it, so a change to it must run it
        assert watched.search(f), f


# -- the driver's accounting, the log oracle, the knee's attribution and the chaos kill ---------------------


def test_a_failed_copy_with_the_original_vrid_is_not_a_working_dedup(live_corpus: tuple) -> None:
    """The product found the twin and then failed the copy: the trust anchor must not pass."""
    _out, manifest, by_kind, _entries = live_corpus
    plan = _plan("small-text", [0.0], by_kind["small-text"][0], control=True)
    args = _args(inject_duplicate=True)
    run, t0 = _live(live_corpus, _FakeKB(index_s=0.2, copy_status="FAILED"), plan, args)
    result = _build(plan, manifest, run, args, t0)
    ctl = result["metrics"]["negative_control"]["controls"][0]
    assert ctl["control_status"] == "FAILED" and ctl["same_vrid"] is True
    assert ctl["outcome"] != "deduplicated" and result["verdict"]["status"] == "failed"


def test_a_control_whose_original_upload_failed_does_not_hold_the_run_to_its_timeout(live_corpus: tuple) -> None:
    """The original never gets a final status; the control must not wait out the whole timeout."""
    _out, manifest, by_kind, _entries = live_corpus
    tpl = by_kind["small-text"][0]
    plan = _plan("small-text", [0.0], tpl, control=True)
    args = _args(inject_duplicate=True, timeout=6.0)
    suffix = _entry_suffix(live_corpus, tpl)
    started = time.time()
    run, t0 = _live(live_corpus, _FakeKB(fail_upload_of=suffix), plan, args)
    wall = time.time() - started
    result = _build(plan, manifest, run, args, t0)
    assert result["verdict"]["status"] == "failed"
    ctl = result["metrics"]["negative_control"]["controls"][0]
    assert ctl["outcome"] == "upload_failed"
    assert "original's upload failed" in next(s for s in run.samples.values() if s.control).upload_error
    assert wall < 3.0, f"the run took {wall:.1f}s of a 6 s timeout with nothing left in flight"


def _entry_suffix(live_corpus: tuple, template: str) -> str:
    return live_corpus[3][template].path


@pytest.mark.parametrize("line", [
    "2026-11-15 10:00:00,123 - kb-service - \x1b[31mERROR\x1b[0m - [kb.controller.ts:88] - upload failed",
    "ERROR:    Exception in ASGI application",
    "2026-11-15 10:00:00,123 - app.indexing - CRITICAL - [main.py:1] - consumer loop died",
    '{"timestamp": "2026-11-15T10:00:00.000Z", "level": "critical", "message": "boom"}',
    "\x1b[1m\x1b[31mERROR\x1b[0m: coloured at the line start",
], ids=["node-text-coloured", "uvicorn-prefix", "python-critical", "python-json-critical", "coloured-line-start"])
def test_product_error_lines_in_every_format_the_stack_writes_are_caught(line: str) -> None:
    """The Node logger colours the level unconditionally (logger.service.ts:403-409)."""
    assert bc.scan_error_lines(line) == [bc._ANSI.sub("", line).strip()]
    assert bc.scan_error_lines("2026-11-15 10:00:00,123 - app.x - WARNING - [a.py:1] - error count 0 ERRORS none") == []
    assert bc.scan_error_lines("\x1b[33mWARNING\x1b[0m - retrying after an error") == []


def test_an_unreadable_or_empty_product_log_is_a_failed_check_not_a_clean_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Docker failing or timing out, or a log with nothing in it, must fail the run."""
    monkeypatch.setattr(bc, "docker", lambda *a, **k: None)
    assert bc.fetch_log_errors("app", "2026-11-15T00:00:00+00:00") is None
    monkeypatch.setattr(bc, "docker", lambda *a, **k: "  \n")
    assert bc.fetch_log_errors("app", "2026-11-15T00:00:00+00:00") is None
    monkeypatch.setattr(bc, "docker", lambda *a, **k: "INFO - all quiet\n")
    assert bc.fetch_log_errors("app", "2026-11-15T00:00:00+00:00") == []
    v = _verdict(log_errors=None, log_unreadable=True)
    assert v["status"] == "failed" and "could not be read" in v["reasons"][0]
    assert _verdict(log_errors=[], log_unreadable=False)["status"] == "pass"


def test_a_result_with_an_unreadable_log_fails_only_when_the_log_was_asked_for(live_corpus: tuple) -> None:
    """Through build_result, the way the run hands over fetch_log_errors' None."""
    _out, manifest, by_kind, _entries = live_corpus
    plan = _plan("small-text", [0.0], by_kind["small-text"][0])
    args = _args(fail_on_log_errors=True)
    run, t0 = _live(live_corpus, _FakeKB(), plan, args)
    result = _build(plan, manifest, run, args, t0, log_errors=None)
    assert result["verdict"]["status"] == "failed" and result["metrics"]["log_errors"]["unreadable"] is True
    assert _build(plan, manifest, run, args, t0, log_errors=[])["verdict"]["status"] == "pass"
    off = _args(fail_on_log_errors=False)
    assert _build(plan, manifest, run, off, t0, log_errors=None)["verdict"]["status"] == "pass"  # not asked for


def _series(idle_cores: float, busy_cores: float, idle_before: float, busy: float, idle_after: float, dt: float = 0.25):
    t = c = 0.0
    out = [(0.0, 0.0)]
    for span, cores in ((idle_before, idle_cores), (busy, busy_cores), (idle_after, idle_cores)):
        for _ in range(round(span / dt)):
            t += dt
            c += cores * dt
            out.append((round(t, 3), c))
    return out


def test_idle_cpu_outside_the_busy_intervals_is_not_counted_against_busy_time() -> None:
    """0.05 cores idling for 85 s plus 2 cores for 5 s is 2.0 cores while busy, not 2.85."""
    cpu, busy = bc.busy_and_cpu(_series(0.05, 2.0, 40, 5, 45))
    assert busy == pytest.approx(5.0) and cpu == pytest.approx(10.0)
    measured = bc.cores_per_unit(cpu, busy)
    assert measured == pytest.approx(2.0)
    assert not bc.weights_table({"docling": measured})["docling"]["plan_change"]


def test_peak_cores_is_the_busiest_stretch() -> None:
    assert bc.peak_cores(_series(0.05, 2.0, 40, 5, 45), window_s=4.0) == pytest.approx(2.0)
    assert bc.peak_cores([(0.0, 0.0)], window_s=4.0) is None


def _report(readings: int = 100, busy_s: float = 20.0, span_s: float = 100.0, cpu_s: float = 30.0, pids: int = 1) -> dict:
    return {"pids": pids, "readings": readings, "busy_s": busy_s, "span_s": span_s, "cpu_s": cpu_s, "peak_cores": 1.0}


def test_a_knee_point_says_which_component_capped_it() -> None:
    """Docling busy nearly all the burst is Docling-bound; slack in Docling is not; no data is unknown."""
    assert bc.attribute_knee_point(_report(busy_s=95.0), _report())["bound"] == "docling"
    assert bc.attribute_knee_point(_report(busy_s=40.0), _report())["bound"] == "not_docling"
    assert bc.attribute_knee_point(_report(pids=0), _report())["bound"] == "unknown"
    assert bc.attribute_knee_point(_report(), _report(readings=1))["bound"] == "unknown"
    assert bc.attribute_knee_point(None, None)["bound"] == "unknown"
    a = bc.attribute_knee_point(_report(busy_s=40.0), _report(busy_s=50.0, cpu_s=75.0))
    assert a["docling_busy_fraction"] == pytest.approx(0.4) and a["indexing_cores"] == pytest.approx(1.5)


def test_a_docling_bound_knee_reports_no_cell_cpus() -> None:
    good = [_knee_result(8, 10.0), _knee_result(12, 14.8), _knee_result(16, 19.0), _knee_result(24, 20.0)]
    assert bc.knee_from_results(good)["cell_cpus"] == 16
    capped = [_knee_result(8, 10.0), _knee_result(12, 14.8), _knee_result(16, 19.0, bound="docling"),
              _knee_result(24, 20.0, bound="docling")]
    out = bc.knee_from_results(capped)
    assert out["cell_cpus"] is None and out["bound"] == "docling" and out["docling_bound_tiers"] == [16, 24]
    assert "Docling-bound" in out["reason"] and out["record_as_decision"] is False
    assert out["attribution"][16]["bound"] == "docling"
    # no process CPU recorded: the point cannot be attributed, so it is not used
    unknown = [_knee_result(8, 10.0), _knee_result(12, 14.8, bound="unknown")]
    out = bc.knee_from_results(unknown)
    assert any("cannot be attributed" in e["why"] for e in out["excluded"]) and out["confidence"] == "low"


def test_the_knee_is_a_decision_only_at_high_confidence_and_on_the_legacy_profile(tmp_path: Path) -> None:
    """Only a high-confidence knee on the default capacity profile is a decision."""
    high = [_knee_result(8, 10.0), _knee_result(12, 14.8), _knee_result(16, 19.0), _knee_result(24, 20.0)]
    assert bc.knee_from_results(high)["record_as_decision"] is True
    medium = [_knee_result(8, 10.0), _knee_result(12, 15.0), _knee_result(16, 20.0), _knee_result(24, 30.0)]
    out = bc.knee_from_results(medium)
    assert out["confidence"] == "medium" and out["record_as_decision"] is False
    on_auto = [_knee_result(8, 10.0), _knee_result(12, 14.8, profile="auto"), _knee_result(16, 19.0, profile=None),
               _knee_result(24, 20.0)]
    out = bc.knee_from_results(on_auto)
    assert {e["tier"] for e in out["excluded"]} == {12, 16} and all("legacy" in e["why"] for e in out["excluded"])
    f = tmp_path / "r8.json"
    f.write_text(json.dumps(_knee_result(8, 10.0)))
    assert bc.main(["--knee-from", str(f), "--output", str(tmp_path / "k.json")]) == 0
    assert json.loads((tmp_path / "k.json").read_text())["record_as_decision"] is False  # T8 alone: a note


def test_check_stack_reads_the_capacity_profile_and_docling_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    env = {"INDEXING_UVICORN_WORKERS": "1", "PIPESHUB_CAPACITY_PROFILE": "AUTO", "DOCLING_UVICORN_WORKERS": "2",
           "OMP_NUM_THREADS": "2"}
    monkeypatch.setattr(bc, "container_env", lambda _c, name: env.get(name, ""))
    monkeypatch.setattr(bc, "docker", lambda *a, **k: "")
    stack = bc.check_stack(bc.parse_args(["--scenario", "b06-knee", "--label", "x", "--app-container", "app"]))
    assert stack["capacity_profile"] == "auto" and stack["docling_workers"] == 2 and stack["omp_threads"] == "2"
    env["PIPESHUB_CAPACITY_PROFILE"] = ""
    assert bc.check_stack(bc.parse_args(["--scenario", "b06-knee", "--label", "x", "--app-container", "app"]))[
        "capacity_profile"] == "legacy"


def test_one_process_check_does_not_read_a_docker_failure_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """A wrong container name or a docker timeout is an unreadable stack, not one process."""
    monkeypatch.setattr(bc, "docker", lambda *a, **k: None)
    assert bc.container_env("not-a-container", "INDEXING_UVICORN_WORKERS") is None
    assert bc.workers_from_container("not-a-container", "INDEXING_UVICORN_WORKERS") == -1
    with pytest.raises(SystemExit, match="one indexing process"):
        bc.check_stack(bc.parse_args(["--scenario", "b06-knee", "--label", "x", "--app-container", "not-a-container"]))
    monkeypatch.setattr(bc, "docker", lambda *a, **k: json.dumps(["PATH=/bin", "INDEXING_UVICORN_WORKERS=4"]))
    assert bc.container_env("app", "INDEXING_UVICORN_WORKERS") == "4" and bc.container_env("app", "NOPE") == ""
    monkeypatch.setattr(bc, "docker", lambda *a, **k: "not json")
    assert bc.container_env("app", "X") is None


def test_chaos_kills_the_indexing_process_and_recovery_is_a_new_one() -> None:
    """Docker never restarts what `docker kill` stopped, so the indexing process is killed inside the
    container and the process monitor's restart is detected by a new indexing pid."""
    tops = iter(["PID PPID ARGS\n20 1 python -m app.indexing_main\n"] +
                ["PID PPID ARGS\n"] * 2 + ["PID PPID ARGS\n31 1 python -m app.indexing_main\n"] * 10)
    killed: list[str] = []
    chaos = bc.ChaosKill("app", 0.01, kill=_kill_recorder(killed), top=lambda: next(tops))
    chaos.start()
    chaos.timer.join(timeout=10)
    assert not chaos.timer.is_alive()
    assert chaos.fired and chaos.before == {20}
    assert bc.ChaosKill("app", 1)._kill is bc.kill_indexing_process  # the default kill is the in-container one
    assert chaos.recovered(wait_s=5.0, poll_s=0.01) is True  # gone for two reads, then pid 31
    # the same pid still running, or nothing ever coming back, is not a recovery
    same = bc.ChaosKill("app", 0, kill=_kill_recorder([]), top=lambda: "PID PPID ARGS\n20 1 python -m app.indexing_main\n")
    same._fire()
    assert same.recovered(wait_s=0.1, poll_s=0.01) is False
    none = bc.ChaosKill("app", 0, kill=_kill_recorder([]), top=lambda: "PID PPID ARGS\n")
    none._fire()
    assert none.recovered(wait_s=0.1, poll_s=0.01) is False
    # a kill that hit nothing did not fire
    reads = iter(["PID PPID ARGS\n"] + ["PID PPID ARGS\n40 1 python -m app.indexing_main\n"] * 5)
    missed = bc.ChaosKill("app", 0, kill=_kill_recorder([], hit=[]), top=lambda: next(reads))
    missed._fire()
    assert missed.fired is False and missed.recovered(wait_s=0.0) is False  # a process nothing killed is no recovery


def test_the_kill_goes_into_the_container_and_never_to_pid_1(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(bc, "docker", lambda *a, **k: calls.append(a) or "20\n")
    assert bc.kill_indexing_process("app") == [20]
    cmd = calls[0]
    assert cmd[:3] == ("exec", "app", "sh") and "kill" not in cmd[:2]
    script = cmd[-1]
    assert "kill -9 $p" in script and "indexing_mai[n]" in script and "kill -9 1" not in script
    # the script must not match its own command line: run it for real against a stand-in process
    # The pattern is made unique to this process (the test's pid in the sleep argument), so the script run for
    # real can reach no other process of the host, whatever else is running `sleep`.
    uniq = str(os.getpid())
    p = subprocess.Popen(["sleep", f"30.{uniq}"])
    try:
        out = subprocess.run(["sh", "-c", script.replace("app[.]indexing_mai[n]", f"sleep.30[.]{uniq}")],
                             capture_output=True, text=True, timeout=30, check=False).stdout
        assert str(p.pid) in out.split()
        assert p.wait(timeout=10) == -9
    finally:
        p.kill()
    monkeypatch.setattr(bc, "docker", lambda *a, **k: None)
    assert bc.kill_indexing_process("app") is None


def test_knee_sampling_is_wired_for_the_knee_scenario_only() -> None:
    """A knee run watches Docling's process and the indexing process the whole burst."""
    knee = bc.knee_watchers(bc.parse_args(["--scenario", "b06-knee", "--label", "x", "--app-container", "app"]))
    assert {k: (w.marker, w.refresh_s) for k, w in knee.items()} == {"docling": ("docling_main", 15.0),
                                                                     "indexing": ("indexing_main", 15.0)}
    assert bc.knee_watchers(bc.parse_args(["--scenario", "b06", "--label", "x", "--app-container", "app"])) == {}
    assert bc.knee_watchers(bc.parse_args(["--scenario", "b06-knee", "--label", "x"])) == {}


_UNLIMITED_STEP_ALLOWANCE_MIN = 2  # a step without its own limit: checkout, setup, login, upload, teardown (generous)
_HEADROOM = 0.8  # a planned duration may use at most 80 percent of its limit
_CORPUS_BUILD_S = 300  # generating the corpus before the first upload


def _perf_workflow() -> dict:
    return yaml.safe_load((_ROOT / ".github/workflows/perf-costclass.yml").read_text())


def _weekly_legs() -> list[dict]:
    return json.loads((_ROOT / "integration-tests/perf/costclass_weekly_legs.json").read_text())["legs"]


def _planned_leg_seconds(leg: dict) -> float:
    """The longest a leg's bench run can take: its hard wall limit, after checking that its schedule ends first."""
    plan = bc.build_plan(leg["scenario"], tier_cpus=4, duration_s=float(leg["duration"]))
    last_arrival = max(a.at_s for a in plan.arrivals)
    assert last_arrival < float(leg["timeout"]), (leg["leg"], last_arrival, leg["timeout"])
    return float(leg["timeout"]) + _CORPUS_BUILD_S


def _job_steps(suite_step_prefix: str) -> tuple[list[dict], dict]:
    job = _perf_workflow()["jobs"]["costclass"]
    return job["steps"], next(s for s in job["steps"] if s.get("name", "").startswith(suite_step_prefix))


def _worst_case_job_minutes(steps: list[dict], suite: str, bench_step: dict) -> float:
    """The sum of the limits of every step that runs for the suite; steps without a limit count a fixed allowance."""
    total = 0.0
    for st in steps:
        cond = st.get("if", "")
        if f"env.SUITE != '{suite}'" in cond or ("env.SUITE ==" in cond and f"'{suite}'" not in cond):
            continue
        total += st.get("timeout-minutes", _UNLIMITED_STEP_ALLOWANCE_MIN)
    return total


def test_the_job_budgets_the_build_and_stack_start_apart_from_the_bench_run() -> None:
    """The build, the start and the bench run have limits of their own, and the job limit holds them all."""
    wf = _perf_workflow()
    job = wf["jobs"]["costclass"]
    steps = {s.get("name", ""): s for s in job["steps"]}
    build, start, wait = steps["Build the stack image"], steps["Start the stack (smoke and weekly)"], steps["Wait for the stack"]
    assert all("timeout-minutes" in s for s in (build, start, wait, steps["Smoke"], steps["Weekly run"], steps["Tiers"],
                                                 steps["Pull pre-built base images"], steps["Install dependencies"]))
    for suite, name in (("smoke", "Smoke"), ("weekly", "Weekly run"), ("tiers", "Tiers")):
        worst = _worst_case_job_minutes(job["steps"], suite, steps[name])
        assert worst <= job["timeout-minutes"] * _HEADROOM, (suite, worst, job["timeout-minutes"])


def test_the_smoke_run_fits_its_step_with_headroom() -> None:
    """The bench's own wall budget (--max-wall-s) plus the corpus build fits the smoke step with 20 percent to spare."""
    smoke = {s.get("name", ""): s for s in _perf_workflow()["jobs"]["costclass"]["steps"]}["Smoke"]
    wall = float(re.search(r"--max-wall-s (\d+)", smoke["run"]).group(1))
    timeout = float(re.search(r"--timeout (\d+)", smoke["run"]).group(1))
    assert timeout <= wall
    assert wall + _CORPUS_BUILD_S <= smoke["timeout-minutes"] * 60 * _HEADROOM


def test_every_weekly_leg_fits_its_step_with_headroom() -> None:
    """Each weekly run is a job of its own: its schedule (from build_plan), its wait and its corpus build fit the
    step with 20 percent to spare, and the step's limit is the one the workflow declares."""
    step = {s.get("name", ""): s for s in _perf_workflow()["jobs"]["costclass"]["steps"]}["Weekly run"]
    limit_s = step["timeout-minutes"] * 60
    legs = _weekly_legs()
    assert legs
    for leg in legs:
        planned = _planned_leg_seconds(leg)
        assert planned <= limit_s * _HEADROOM, (leg["leg"], planned, limit_s)


def test_the_fake_embedding_control_runs_first_and_alone() -> None:
    """A slow later run can never skip the control: it is the first leg, with its own job, and the matrix runs one at a time."""
    legs = _weekly_legs()
    assert legs[0]["fake_embeddings"] is True and legs[0]["scenario"] == "b05"
    assert [leg["leg"] for leg in legs if leg["fake_embeddings"]] == [legs[0]["leg"]]
    assert len({leg["leg"] for leg in legs}) == len(legs)
    job = _perf_workflow()["jobs"]["costclass"]
    assert job["strategy"]["max-parallel"] == 1 and job["strategy"]["fail-fast"] is False


def test_a_leg_that_cannot_fit_is_caught() -> None:
    """Negative control: a leg whose duration alone exceeds the step is refused by the planner arithmetic."""
    long_leg = {"leg": "x", "scenario": "b05", "duration": 3000, "timeout": 1500, "fake_embeddings": False}
    with pytest.raises(AssertionError):
        _planned_leg_seconds(long_leg)


def test_the_guards_watch_every_workflow_file_the_guard_tests_read() -> None:
    """test_guard_scope fails a guard that reads a file WATCHED_PATHS misses; so does this, before CI."""
    import re

    guards = yaml.safe_load((_ROOT / ".github/workflows/integration-suite-guards.yml").read_text())
    watched = re.compile(next(s for s in guards["jobs"]["integration-suite-guards"]["steps"]
                              if s.get("id") == "scope")["env"]["WATCHED_PATHS"])
    src = Path(__file__).read_text()
    read = sorted(set(re.findall(r'"(\.github/workflows/[\w.-]+\.yml)"', src)))
    assert ".github/workflows/perf-costclass.yml" in read
    assert [p for p in read if not watched.search(p)] == []


# -- review follow-ups: the knee decision, compare's workload inputs, stale tier files, the tiers dispatch -------------------


def test_a_knee_over_mixed_corpora_is_never_recorded_as_a_decision() -> None:
    same = bc.knee_from_results([_knee_result(8, 10.0), _knee_result(12, 14.8), _knee_result(16, 19.0),
                                 _knee_result(24, 20.0)])
    assert same["confidence"] == "high" and same["record_as_decision"] is True
    mixed = bc.knee_from_results([_knee_result(8, 10.0, corpus="a"), _knee_result(12, 14.8), _knee_result(16, 19.0),
                                  _knee_result(24, 20.0)])
    assert mixed["confidence"] == "low" and mixed["record_as_decision"] is False


def test_compare_refuses_runs_with_a_different_offered_load() -> None:
    def result(rate: float, duration: float = 600.0, burst: int | None = None, arrivals: int = 100) -> dict:
        r = _result()
        r["plan"] = {**r.get("plan", {}), "arrivals": arrivals,
                     "notes": {"rate_per_s": rate, "duration_s": duration, "burst_count": burst}}
        return r

    assert not compare.compare(result(0.2), result(0.2))[1]
    for other in (result(0.5), result(0.2, duration=300.0), result(0.2, burst=60), result(0.2, arrivals=90)):
        assert compare.compare(result(0.2), other)[1], other


def test_the_plan_records_the_workload_inputs_compare_checks() -> None:
    assert bc.build_plan("b05", rate_per_s=0.3, duration_s=120).notes["duration_s"] == 120
    assert bc.build_plan("b01", rate_per_s=0.3, duration_s=120).notes["duration_s"] == 120
    assert bc.build_plan("b03", rate_per_s=0.3, duration_s=120).notes["duration_s"] == 120
    assert bc.build_plan("b06", burst_count=7).notes["burst_count"] == 7


def test_the_process_cpu_total_survives_children_that_exit_and_start(tmp_path: Path) -> None:
    ticks: dict[int, int | None] = {10: 100}
    top = "PID PPID COMMAND\n10 1 python -m app.docling_main\n"

    def read(pid: int) -> str:
        if ticks.get(pid) is None:
            raise FileNotFoundError(pid)
        return f"{pid} (x) S " + " ".join(["0"] * 10 + [str(ticks[pid]), "0"])

    sampler = bc.ProcessCpuSampler("c", "docling_main", top=lambda: top, read_stat=read)
    sampler.tick = 100.0
    sampler.pids = [10]
    sampler._read_once()  # baseline
    ticks[10] = 200
    sampler._read_once()  # +1 s
    ticks.update({10: 250, 11: 5000})  # a child that already used 50 s when it was found
    sampler.pids = [10, 11]
    sampler._read_once()  # +0.5 s: the newcomer is a baseline, not a 50 s spike
    ticks[11] = 5100
    sampler._read_once()  # +1 s
    ticks[10] = None  # the parent exits: its CPU stays counted, the total never falls
    ticks[11] = 5100
    sampler._read_once()
    values = [v for _t, v in sampler.series]
    assert values == pytest.approx([0.0, 1.0, 1.5, 2.5, 2.5])
    assert all(b >= a for a, b in itertools.pairwise(values))


def test_execute_removes_the_generated_corpus_whatever_the_outcome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    seen: list[Path] = []

    def fake(_args: argparse.Namespace, _run_id: str, tmp: Path) -> dict:
        tmp.mkdir()
        (tmp / "doc.pdf").write_bytes(b"x")
        seen.append(tmp)
        if _args.boom:
            raise RuntimeError("boom")
        return {"ok": True}

    monkeypatch.setattr(bc, "_execute", fake)
    assert bc.execute(argparse.Namespace(boom=False)) == {"ok": True}
    with pytest.raises(RuntimeError):
        bc.execute(argparse.Namespace(boom=True))
    assert len(seen) == 2 and not any(p.exists() for p in seen) and list(tmp_path.iterdir()) == []


def test_runner_never_reuses_a_result_or_summary_an_earlier_run_left(tmp_path: Path) -> None:
    tree, log, env = _stub_tree(tmp_path, bench_rc=1)
    out = Path(env["BENCH_OUT"])
    out.mkdir()
    (out / "costclass-local-T8.json").write_text('{"stale": true}')
    (out / "summary-T8.md").write_text("STALE")
    # a bench that dies before writing anything: the stub writes the result, so make it exit first
    (tree / "integration-tests/perf/bench_costclass.py").write_text(
        f"import sys\nopen({str(log)!r}, 'a').write('bench ' + ' '.join(sys.argv[1:]) + '\\n')\nsys.exit(2)\n")
    env["BENCH_TIERS"] = "T8:8:32"
    p = _runner(tree, env, "--scenario", "b06-knee")
    assert p.returncode != 0
    assert not (out / "costclass-local-T8.json").exists() and not (out / "summary-T8.md").exists()
    assert "STALE" not in (out / "summary.md").read_text() if (out / "summary.md").exists() else True
    assert not any("--knee-from" in ln for ln in log.read_text().splitlines())  # nothing valid to find a knee in


def test_the_tiers_dispatch_needs_its_own_runner_and_fits_its_step() -> None:
    wf = _perf_workflow()
    job = wf["jobs"]["costclass"]
    validate = {s.get("name", ""): s for s in job["steps"]}["Validate inputs"]["run"]
    # the default 4-CPU runner cannot host any tier (tiers.fit), so a tiers dispatch must name another
    assert tiers.fit(tiers.Tier("T4", 4, 16), 4, 16, tiers.SIBLINGS_STANDARD)[0] is False
    assert 'SUITE" = tiers' in validate and "4cpu-linux-x64" in validate and "RUNNER_INPUT" in validate
    # at most three tiers, and the step covers three tiers' worst case with the bench's own --timeout
    pattern = re.search(r'\[\[ "\$TIERS_INPUT" =~ (\^.*\$) \]\]', validate).group(1)
    ok = re.compile(pattern)
    assert ok.match("T4:4:16 T8:8:32 T12:12:48") and not ok.match("T4:4:16 T8:8:32 T12:12:48 T16:16:64")
    step = {s.get("name", ""): s for s in job["steps"]}["Tiers"]
    bench_timeout = int(re.search(r"-- --timeout (\d+)", step["run"]).group(1))
    per_tier = tiers_quiet_wait_s() + 600 + bench_timeout + 180
    assert 3 * per_tier <= step["timeout-minutes"] * 60 <= job["timeout-minutes"] * 60 * _HEADROOM + 1


def tiers_quiet_wait_s() -> int:
    """The longest the runner waits for a quiet host before one tier: its default retries times its sleep."""
    text = (_ROOT / "integration-tests/perf/run_tiers_e2e.sh").read_text()
    return int(re.search(r"BENCH_QUIET_RETRIES:-(\d+)", text).group(1)) * int(
        re.search(r"BENCH_QUIET_SLEEP:-(\d+)", text).group(1))


def test_every_suite_fits_the_job_limit_with_headroom_and_no_long_step_is_unbounded() -> None:
    job = _perf_workflow()["jobs"]["costclass"]
    assert job["timeout-minutes"] == 240
    for suite, name in (("smoke", "Smoke"), ("weekly", "Weekly run"), ("tiers", "Tiers")):
        bench = next(s for s in job["steps"] if s.get("name") == name)
        assert _worst_case_job_minutes(job["steps"], suite, bench) <= job["timeout-minutes"] * _HEADROOM, suite
    # a step that talks to the network or installs packages has a limit of its own: the job limit is no bound for it
    for st in job["steps"]:
        if st.get("name") in ("Pull pre-built base images", "Install dependencies", "Install uv"):
            assert "timeout-minutes" in st or st["name"] == "Install uv", st["name"]


def test_the_docker_log_is_collected_before_the_artifact_is_uploaded() -> None:
    names = [s.get("name") for s in _perf_workflow()["jobs"]["costclass"]["steps"]]
    assert names.index("Dump docker logs") < names.index("Upload benchmark result")


def test_a_tier_list_with_a_repeated_name_is_refused_before_any_stack_starts(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate tier name"):
        tiers.parse_tiers("T4:4:16 T4:8:32")
    tree, log, env = _stub_tree(tmp_path)
    env["BENCH_TIERS"] = "T4:4:16 T4:8:32"
    p = _runner(tree, env, "--scenario", "b05")
    assert p.returncode != 0 and not log.exists()  # no docker, no bench


def test_each_tier_is_planned_again_after_its_quiet_check(tmp_path: Path) -> None:
    """The cores a tier gets are the idlest at its own start: the runner plans each tier again, once per tier."""
    tree, _log, env = _stub_tree(tmp_path)
    counter = tmp_path / "plans"
    perf = tree / "integration-tests/perf/tiers.py"
    perf.write_text(perf.read_text().replace(
        "def plan_lines(", f"def _count():\n    open({str(counter)!r}, 'a').write('x')\n\n\ndef plan_lines(", 1).replace(
        '    lines = plan_lines(parse_tiers', '    _count()\n    lines = plan_lines(parse_tiers', 1))
    env["BENCH_TIERS"] = "T8:8:32 T12:12:48"
    p = _runner(tree, env, "--scenario", "b05")
    assert p.returncode == 0, p.stderr[-300:]
    assert counter.read_text() == "xxx"  # the list once, then each of the two tiers


def test_a_baseline_from_fewer_repeats_than_planned_is_invalid() -> None:
    def run(rate: float = 0.2) -> dict:
        r = _result()
        r["plan"] = {**r.get("plan", {}), "arrivals": 50, "notes": {"rate_per_s": rate, "duration_s": 600.0}}
        r["environment"].setdefault("tier", {"cpus": 4, "mem_gb": 16})
        return r

    three = [run(), run(), run()]
    assert bc.baseline_from_results(three)["baseline_valid"] is True
    out = bc.baseline_from_results(three, expected_repeats=4)
    assert out["baseline_valid"] is False and any("planned repeat" in r for r in out["reasons"])
    assert bc.baseline_from_results(three, expected_repeats=3)["baseline_valid"] is True
    mixed = bc.baseline_from_results([run(0.1), run(0.2), run(0.4)])
    assert mixed["baseline_valid"] is False and len(mixed["groups"]) == 3  # different rates are not repeats
