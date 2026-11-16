"""The cost-class driver against a fake knowledge base with fault modes: how it accounts for stuck, failed,
missing, crashed and doubled uploads, what it reports for each, and the result file's schema.

Pure and fast (no stack): the guards job runs this on every pull request.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "integration-tests" / "perf"))

import bench_costclass as bc
import compare

CC = bc.load_cost_corpus_module()


class KB:
    """A knowledge base fake. ``stuck``: name suffixes whose record never leaves IN_PROGRESS; ``failing``: suffixes
    whose record ends FAILED; ``created`` records are made per upload, ``reported`` of them are in the upload's
    answer and ``listed`` of them show in the listing; ``detail_status`` is what the record read says."""

    def __init__(self, index_s: float = 0.05, stuck: tuple[str, ...] = (), failing: tuple[str, ...] = (),
                 created: int = 1, reported: int | None = None, listed: int | None = None,
                 list_fails: bool = False, detail_status: str | None = None) -> None:
        self.index_s, self.stuck, self.failing = index_s, stuck, failing
        self.created, self.reported, self.listed = created, reported, listed
        self.list_fails, self.detail_status = list_fails, detail_status
        self.lock = threading.Lock()
        self.records: dict[str, dict] = {}

    def upload_file(self, kb_id: str, name: str, data: bytes, mimetype: str = "") -> dict:
        now = time.time() * 1000
        md5 = hashlib.md5(data).hexdigest()
        ids = []
        with self.lock:
            twin = next((r for r in self.records.values() if r["md5"] == md5 and r["done"] <= now), None)
            for i in range(self.created):
                rid = f"r-{name}-{i}"
                self.records[rid] = {
                    "id": rid, "name": name, "created": now, "md5": md5, "done": now + self.index_s * 1000,
                    "vrid": twin["vrid"] if twin else f"v-{rid}", "shown": self.listed is None or i < self.listed,
                    "stuck": any(name.endswith(x) for x in self.stuck),
                    "failing": any(name.endswith(x) for x in self.failing)}
                ids.append(rid)
        shown = ids if self.reported is None else ids[: self.reported]
        return {"records": [{"recordId": r} for r in shown]}

    def list_records(self, kb_id: str, page: int = 1, limit: int = 200) -> dict:
        if self.list_fails:
            raise RuntimeError("list down")
        now = time.time() * 1000
        with self.lock:
            items = [{"id": r["id"], "name": r["name"],
                      "indexingStatus": "FAILED" if r["failing"] and now >= r["done"]
                      else "IN_PROGRESS" if r["stuck"] or now < r["done"] else "COMPLETED"}
                     for r in self.records.values() if r["shown"]]
        return {"items": items, "pagination": {"totalPages": 1}}

    def get_record(self, rid: str) -> dict:
        r = self.records[rid]
        status = self.detail_status or ("FAILED" if r["failing"] else "COMPLETED")
        return {"record": {"indexingStatus": status, "lastIndexTimestamp": r["done"],
                           "createdAtTimestamp": r["created"], "virtualRecordId": r["vrid"]}}


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, object, dict, dict]:
    out = tmp_path_factory.mktemp("costclass-driver")
    manifest = CC.generate(out, profile="cost-smoke", seed=31)
    by_kind, entries = bc.corpus_templates(manifest, out)
    return out, manifest, by_kind, entries


def _args(**kw: object) -> argparse.Namespace:
    base = {"upload_workers": 4, "timeout": 3.0, "poll_interval": 0.05, "inject_duplicate": False, "label": "t",
            "tier_cpus": 4, "tier_mem_gb": 16, "scenario": "b05", "seed": 1, "neo4j_connection_budget": None,
            "pg_max_connections": None, "fake_embeddings": False}
    base.update(kw)
    return argparse.Namespace(**base)


def _plan(template: str, n: int, control: bool = False, kind: str = "small-text") -> bc.Plan:
    arrivals = [bc.Arrival(i, kind, i * 0.02, "probe", template) for i in range(n)]
    if control:
        arrivals.append(bc.Arrival(n, kind, 0.01, "control", template, control_of=0))
    return bc.Plan("t", arrivals, "cost-smoke")


def _go(corpus: tuple, kb: object, plan: bc.Plan, args: argparse.Namespace, pad_s: float = 0.0,
        entries: dict | None = None) -> tuple[dict, float]:
    out, manifest, _by, base_entries = corpus
    run, t0 = bc.Run(), time.time()
    bc._run_open_loop(plan, kb, "kb", run, out, entries or base_entries, "t", t0, args)
    end = time.time()
    sampler = bc.Sampler("http://127.0.0.1:1", None, {}, 60, None, None, None)
    result = bc.build_result(args, plan, manifest, run, sampler, {}, {}, bc.datetime.now(bc.UTC), t0,
                             end + pad_s, 0, [])
    return result, end - t0


def _tpl(corpus: tuple) -> str:
    return corpus[2]["small-text"][0]


# -- failed, timed out and missing are `failed` (exit 1), never `invalid` (exit 3) ---------------------------------------


def test_a_probe_that_ends_failed_fails_the_run_not_invalidates_it(corpus: tuple) -> None:
    kb = KB(failing=(f"00002-{corpus[2]['small-text'][0]}",))
    result, _ = _go(corpus, kb, _plan(_tpl(corpus), 6), _args())
    assert result["verdict"]["status"] == "failed" and result["verdict"]["exit_code"] == 1
    assert result["metrics"]["failures"]["failed_status"] == 1
    assert any("FAILED probes" in r for r in result["verdict"]["reasons"])


def test_a_stuck_record_ends_the_run_at_its_timeout_as_failed_not_invalid(corpus: tuple) -> None:
    ok, _ = _go(corpus, KB(), _plan(_tpl(corpus), 6), _args())
    assert ok["verdict"]["status"] == "pass", ok["verdict"]
    result, wall = _go(corpus, KB(stuck=(f"00003-{_tpl(corpus)}",)), _plan(_tpl(corpus), 6), _args(timeout=2.0))
    assert result["verdict"]["status"] == "failed" and result["verdict"]["exit_code"] == 1
    assert result["metrics"]["failures"]["timed_out"] == 1
    assert any("timed-out probes" in r for r in result["verdict"]["reasons"])
    assert 1.9 <= wall <= 3.5, wall


def test_a_probe_with_no_record_and_no_error_is_missing_and_fails_the_run(corpus: tuple) -> None:
    out, manifest, _by, entries = corpus
    plan = _plan(_tpl(corpus), 4)
    run, t0 = bc.Run(), time.time()
    bc._run_open_loop(plan, KB(), "kb", run, out, entries, "t", t0, _args())
    run.samples[1].record_id, run.samples[1].status = None, None  # sent, then no record came back and nothing said why
    sampler = bc.Sampler("http://127.0.0.1:1", None, {}, 60, None, None, None)
    result = bc.build_result(_args(), plan, manifest, run, sampler, {}, {}, bc.datetime.now(bc.UTC), t0,
                             time.time(), 0, [])
    assert result["metrics"]["failures"]["missing"] == 1
    assert result["verdict"]["status"] == "failed" and result["verdict"]["exit_code"] == 1
    assert any("missing records" in r for r in result["verdict"]["reasons"])


def test_a_control_whose_original_is_stuck_does_not_hold_the_run_far_past_its_timeout(corpus: tuple) -> None:
    kb = KB(stuck=(f"00000-{_tpl(corpus)}",))
    result, wall = _go(corpus, kb, _plan(_tpl(corpus), 3, control=True), _args(timeout=2.0, inject_duplicate=True))
    assert result["verdict"]["status"] == "failed"
    assert wall <= 2.0 + 1.5, wall


def test_a_status_listing_that_never_answers_fails_the_run_and_counts_the_poll_errors(corpus: tuple) -> None:
    result, _ = _go(corpus, KB(list_fails=True), _plan(_tpl(corpus), 3), _args(timeout=1.0))
    assert result["verdict"]["status"] == "failed"
    assert result["metrics"]["status_poll_errors"] > 0 and result["metrics"]["failures"]["timed_out"] == 3


# -- one record per upload ------------------------------------------------------------------------------------------------


def test_two_records_the_upload_answer_reports_are_rejected(corpus: tuple) -> None:
    """The answer to the upload lists two records, the listing shows one: the answer alone is enough."""
    result, _ = _go(corpus, KB(created=2, reported=2, listed=1), _plan(_tpl(corpus), 3), _args())
    assert result["metrics"]["rejected"].get("multiple_records") == 3 and result["verdict"]["status"] != "pass"


def test_two_records_only_the_listing_shows_are_rejected(corpus: tuple) -> None:
    """The answer to the upload lists one record, the listing shows two under the name: the listing alone is enough."""
    result, _ = _go(corpus, KB(created=2, reported=1, listed=2), _plan(_tpl(corpus), 3), _args())
    assert result["metrics"]["rejected"].get("multiple_records") == 3 and result["verdict"]["status"] != "pass"


def test_a_detail_read_that_disagrees_with_the_listing_is_rejected_not_accepted(corpus: tuple) -> None:
    result, _ = _go(corpus, KB(detail_status="IN_PROGRESS"), _plan(_tpl(corpus), 3), _args())
    assert result["metrics"]["accepted"] == 0 and result["verdict"]["status"] != "pass"


# -- an exception inside an upload is an error with its cause, not "never started" ----------------------------------------


def _broken_entries(corpus: tuple) -> dict:
    _out, _m, _by, entries = corpus
    tpl = _tpl(corpus)
    e = entries[tpl]
    broken = dict(entries)
    broken[tpl] = type("E", (), {"path": e.path, "ext": "bin", "mime": e.mime, "pages": e.pages})()  # salt refuses "bin"
    return broken


def test_an_upload_that_crashes_in_the_harness_is_reported_with_its_cause(corpus: tuple) -> None:
    out, _manifest, _by, _entries = corpus
    run, t0 = bc.Run(), time.time()
    bc._run_open_loop(_plan(_tpl(corpus), 1), KB(), "kb", run, out, _broken_entries(corpus), "t", t0, _args(timeout=1.0))
    error = run.samples[0].upload_error
    assert error and "not started" not in error
    assert "harness failed this upload" in error and "Error" in error


def test_a_crash_in_the_harness_fails_the_run_and_names_the_cause_in_the_result(corpus: tuple) -> None:
    result, _ = _go(corpus, KB(), _plan(_tpl(corpus), 2), _args(timeout=1.0), entries=_broken_entries(corpus))
    assert result["verdict"]["status"] == "failed"
    assert result["metrics"]["failures"]["upload"] == 2
    reasons = result["metrics"]["failures"]["reasons"]
    assert all("not started" not in (r["error"] or "") and "harness failed" in r["error"] for r in reasons)


def test_a_control_does_not_wait_for_the_bytes_of_an_original_that_crashed(corpus: tuple) -> None:
    result, wall = _go(corpus, KB(), _plan(_tpl(corpus), 1, control=True),
                       _args(timeout=30.0, inject_duplicate=True), entries=_broken_entries(corpus))
    assert wall < 10.0, wall  # not the 30 s the control would wait for bytes that never come
    assert result["verdict"]["status"] == "failed"


# -- throughput is over the span of the run, not the wall time -------------------------------------------------------------


def test_throughput_is_records_over_the_time_to_the_last_index_not_the_wall_time(corpus: tuple) -> None:
    result, _ = _go(corpus, KB(), _plan(_tpl(corpus), 6), _args(), pad_s=1000.0)  # a long tail of waiting after the last record
    assert result["metrics"]["accepted"] == 6
    assert result["metrics"]["records_per_minute"] > 60.0, result["metrics"]["records_per_minute"]


# -- the result file ------------------------------------------------------------------------------------------------------

REQUIRED_TOP = {"schema_version", "benchmark", "label", "started_at", "environment", "knee_attribution", "plan",
                "metrics", "samples", "resources", "verdict", "not_measurable_here"}


@pytest.mark.parametrize("mode", ["pass", "all_failed", "nothing_ran"])
def test_result_is_strict_json_with_its_keys_in_every_outcome(corpus: tuple, mode: str) -> None:
    kb = KB(list_fails=mode == "all_failed")
    result, _ = _go(corpus, kb, _plan(_tpl(corpus), 0 if mode == "nothing_ran" else 3), _args(timeout=1.0))
    back = json.loads(json.dumps(result, allow_nan=False))  # Infinity or NaN would break jq and JavaScript readers
    assert REQUIRED_TOP <= set(back)
    assert back["schema_version"] == bc.SCHEMA_VERSION == 2 and back["benchmark"] == "costclass"
    assert back["not_measurable_here"] and back["not_measurable_here"] == list(bc.NOT_MEASURABLE_HERE)
    status = {"pass": "pass", "all_failed": "failed", "nothing_ran": "invalid"}[mode]
    assert back["verdict"]["status"] == status
    assert back["verdict"]["exit_code"] == {"pass": 0, "failed": 1, "invalid": 3}[status]
    rows, mismatches = compare.compare(back, back)
    if mode == "pass":
        assert not mismatches and not any(r.regressed for r in rows)
    else:
        assert mismatches  # a failed or invalid run is never judged as a number


def test_compare_flags_a_kind_regression_between_two_harness_results(corpus: tuple) -> None:
    base, _ = _go(corpus, KB(), _plan(_tpl(corpus), 4), _args())
    cur = json.loads(json.dumps(base))
    k = cur["metrics"]["by_kind"]["small-text"]
    k["p95"] = (k["p95"] or 0) * 1.5 + 2
    rows, mismatches = compare.compare(base, cur)
    assert not mismatches and any(r.regressed and r.name == "small-text p95" for r in rows)


# -- compare: a missing metric is tolerated for a corpus kind only ---------------------------------------------------------


def test_compare_tolerates_a_missing_kind_but_not_a_missing_metric_of_another_benchmark() -> None:
    kind = compare._kind_check("small-pdf", "p95")
    assert kind.optional
    row = compare._check_row(kind, {"by_kind": {}}, {"by_kind": {"small-pdf": {"p95": 5.0}}})
    assert row.regressed is False and "not measured" in row.note
    for check in compare.INDEXING_CHECKS:
        assert not check.optional
    with pytest.raises((KeyError, TypeError)):
        compare._check_row(compare.INDEXING_CHECKS[1], {"time_to_indexed_seconds": {"p95": 10.0}},
                           {"time_to_indexed_seconds": None})
    with pytest.raises((KeyError, TypeError)):
        compare._check_row(compare.INDEXING_CHECKS[1], {}, {"time_to_indexed_seconds": {"p95": 10.0}})


# -- log oracle, chaos recovery, sampler, stack reading, acceptance edges (each pins a rule a mutant removed) --------------


def test_the_log_oracle_allow_list_silences_only_the_lines_it_matches() -> None:
    text = "x - ERROR - known harmless thing\nx - ERROR - something broke\nx - INFO - fine"
    assert bc.scan_error_lines(text, allow=((r"known harmless", "why"),)) == ["x - ERROR - something broke"]
    assert len(bc.scan_error_lines(text, allow=())) == 2


def test_the_log_is_read_with_stderr_merged(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake_docker(*cmd: str, timeout: int = 30, merge_stderr: bool = False) -> str:
        seen.update(cmd=cmd, merge=merge_stderr)
        return "a - ERROR - x\n"

    monkeypatch.setattr(bc, "docker", fake_docker)
    assert bc.fetch_log_errors("app", "2026-01-01T00:00:00") == ["a - ERROR - x"]
    assert seen["merge"] is True
    monkeypatch.setattr(bc, "docker", lambda *a, **k: "  \n")
    assert bc.fetch_log_errors("app", "2026-01-01T00:00:00") is None  # an empty log is not a clean one


def test_chaos_counts_a_restart_only_after_waiting_for_the_new_process() -> None:
    """The recovery check polls for its wait: a new indexing process that shows up a poll later still counts."""
    def top(pid: int | None) -> str:
        return "PID PPID ARGS\n" + (f"{pid} 1 python -m app.indexing_main\n" if pid else "")

    reads = iter([top(20), top(None), top(None), top(31), top(31)])  # before the kill, two empty polls, then a new pid
    chaos = bc.ChaosKill("app", 0.0, kill=lambda _c: [20], top=lambda: next(reads))
    chaos._fire()
    assert chaos.fired and chaos.before == {20}
    slept: list[float] = []
    assert bc.chaos_restart_count(chaos, sleep=slept.append) == 1 and len(slept) == 2
    unfired = bc.ChaosKill("app", 0.0, kill=lambda _c: [], top=lambda: top(None))
    unfired._fire()
    assert bc.chaos_restart_count(unfired, sleep=lambda _s: None) == 0 and bc.chaos_restart_count(None) == 0


def test_the_sampler_takes_a_last_sample_when_it_stops() -> None:
    s = bc.Sampler("http://127.0.0.1:1", None, {}, 60, None, None, None)
    calls: list[float] = []
    s.sample = lambda: calls.append(time.time())  # type: ignore[method-assign]
    s.start()
    deadline = time.time() + 5
    while not calls and time.time() < deadline:
        time.sleep(0.01)
    s.stop()
    assert len(calls) == 2


def test_an_unreadable_capacity_profile_is_unreadable_not_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    args = bc.parse_args(["--scenario", "b05", "--label", "x", "--app-container", "app"])
    values = {"PIPESHUB_CAPACITY_PROFILE": None}
    monkeypatch.setattr(bc, "container_env", lambda _c, name: values.get(name, "1"))
    monkeypatch.setattr(bc, "docker", lambda *a, **k: "")
    assert bc.check_stack(args)["capacity_profile"] == "unreadable"
    values["PIPESHUB_CAPACITY_PROFILE"] = ""
    assert bc.check_stack(args)["capacity_profile"] == "legacy"
    values["PIPESHUB_CAPACITY_PROFILE"] = "AUTO"
    assert bc.check_stack(args)["capacity_profile"] == "auto"


def test_a_record_indexed_long_before_it_was_created_is_a_skewed_clock() -> None:
    skewed = bc.Sample(seq=9, kind="small-pdf", role="probe", name="9.pdf", md5="m9", scheduled_arrival_ms=1_000_000.0,
                       send_ms=1_000_000.0, status="COMPLETED", last_index_ms=1_005_000.0, created_ms=1_020_000.0,
                       virtual_record_id="v9")
    accepted, rejected = bc.filter_samples([skewed], now_ms=1_100_000.0)
    assert accepted == [] and [s.seq for s in rejected["skewed_timestamp"]] == [9]


# -- review follow-ups: background load, detail reads, and what counts as an unfinished probe -------------------------------


def _load_plan(template: str, probes: int, kind: str = "small-text") -> bc.Plan:
    arrivals = [bc.Arrival(0, kind, 0.0, "load", template)]
    arrivals += [bc.Arrival(i, kind, i * 0.02, "probe", template) for i in range(1, probes + 1)]
    return bc.Plan("t", arrivals, "cost-smoke")


def test_background_load_still_indexing_neither_fails_nor_holds_the_run(corpus: tuple) -> None:
    kb = KB(stuck=(f"00000-{_tpl(corpus)}",))  # the load file never finishes
    result, wall = _go(corpus, kb, _load_plan(_tpl(corpus), 5), _args(timeout=6.0))
    assert result["verdict"]["status"] == "pass", result["verdict"]
    assert result["metrics"]["failures"]["timed_out"] == 0 and result["metrics"]["rejected"] == {}
    assert wall < 3.0, wall  # the probes decide the end; the run does not wait for the load


def test_background_load_that_ends_failed_or_fails_to_upload_still_fails_the_run(corpus: tuple) -> None:
    failed, _ = _go(corpus, KB(failing=(f"00000-{_tpl(corpus)}",)), _load_plan(_tpl(corpus), 4), _args())
    assert failed["verdict"]["status"] == "failed" and failed["metrics"]["failures"]["failed_status"] == 1
    _out, _manifest, _by, entries = corpus
    broken = {**entries}
    broken[_tpl(corpus)] = None  # the harness cannot read the template: an upload error, on the load file too
    result, _ = _go(corpus, KB(), _load_plan(_tpl(corpus), 2), _args(), entries=broken)
    assert result["verdict"]["status"] == "failed"


def test_a_probe_the_listing_calls_completed_but_the_record_calls_in_progress_is_a_timeout(corpus: tuple) -> None:
    result, _ = _go(corpus, KB(detail_status="IN_PROGRESS"), _plan(_tpl(corpus), 3), _args(timeout=1.5))
    assert result["verdict"]["status"] == "failed" and result["verdict"]["exit_code"] == 1
    assert result["metrics"]["failures"]["timed_out"] == 3


class FlakyDetailKB(KB):
    """The first record read of every record raises; the next one answers."""

    def __init__(self) -> None:
        super().__init__()
        self.reads: dict[str, int] = {}

    def get_record(self, rid: str) -> dict:
        self.reads[rid] = self.reads.get(rid, 0) + 1
        if self.reads[rid] == 1:
            raise RuntimeError("transient")
        return super().get_record(rid)


def test_a_failed_detail_read_is_retried_not_finalised_without_its_details(corpus: tuple) -> None:
    kb = FlakyDetailKB()
    result, _ = _go(corpus, kb, _plan(_tpl(corpus), 4), _args())
    assert result["verdict"]["status"] == "pass", result["verdict"]
    assert result["metrics"]["accepted"] == 4 and result["metrics"]["rejected"] == {}
    assert all(n >= 2 for n in kb.reads.values())


class DetailFailsAfterAnInProgressRead(KB):
    """The first record read says IN_PROGRESS; every later one raises. The listing says COMPLETED throughout."""

    def __init__(self) -> None:
        super().__init__()
        self.reads: dict[str, int] = {}

    def get_record(self, rid: str) -> dict:
        self.reads[rid] = self.reads.get(rid, 0) + 1
        if self.reads[rid] > 1:
            raise RuntimeError("detail read down")
        self.detail_status = "IN_PROGRESS"
        try:
            return super().get_record(rid)
        finally:
            self.detail_status = None


def test_a_listing_never_stands_in_for_a_detail_read_that_failed(corpus: tuple) -> None:
    result, _ = _go(corpus, DetailFailsAfterAnInProgressRead(), _plan(_tpl(corpus), 3), _args(timeout=1.5))
    assert result["verdict"]["status"] == "failed", result["verdict"]
    assert result["metrics"]["failures"]["timed_out"] == 3 and result["metrics"]["accepted"] == 0


class LateUnitKB(KB):
    """The first unit finishes (in the listing) only after ``late_after`` seconds from its upload."""

    def __init__(self, late_after: float) -> None:
        super().__init__(index_s=0.01)
        self.late_after, self.uploads = late_after, []

    def upload_file(self, kb_id: str, name: str, data: bytes, mimetype: str = "") -> dict:
        self.uploads.append(name)
        out = super().upload_file(kb_id, name, data, mimetype)
        if len(self.uploads) == 1:
            rec = self.records[out["records"][0]["recordId"]]
            rec["done"] = time.time() * 1000 + self.late_after * 1000
        return out


def test_calibration_stops_when_a_unit_outlives_its_deadline(corpus: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bc, "docker", lambda *a, **k: None)
    out, _manifest, _by, entries = corpus
    name = _tpl(corpus)
    arrivals = [bc.Arrival(i, "small-text", 0.0, "unit:embed", name) for i in range(3)]
    plan = bc.Plan("calibrate", arrivals, "cost-smoke", notes={"sequential": True})
    kb = LateUnitKB(late_after=1.2)
    run, t0 = bc.Run(), time.time()
    args = _args(timeout=0.5, app_container="")
    log = bc._run_sequential(plan, kb, "kb", run, out, entries, "t", t0, args)
    assert len(log) == 1 and len(kb.uploads) == 1  # the second unit was never uploaded
    assert {s.seq: s.upload_error for s in run.samples.values() if s.upload_error} == {
        1: "not started before the deadline", 2: "not started before the deadline"}
    time.sleep(1.0)  # even when the open unit finishes later, nothing else ran alongside it
    assert len(kb.uploads) == 1
