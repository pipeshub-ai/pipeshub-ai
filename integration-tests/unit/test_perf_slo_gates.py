"""QA-22: the SLO gate table, its verdicts, and the rig's readings that feed them.

The rig itself needs real processes; these pin what would quietly produce a wrong verdict: an SLO row with neither
a gate nor a reason, a gate looser than its SLO, a loaded host's number taken as a result, a log line read as the
wrong run's commit, a coalesced SSE frame dropping its chunks' latencies, and a workflow that stops running a gated
profile.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "perf"))

from bench_harness import RIG_PROFILES  # noqa: E402
from harness_profiles import (  # noqa: E402
    BURST_SESSIONS,
    BURST_WINDOW_S,
    FLOW_COMMITS,
    N16_SESSIONS,
    PROFILES,
    Turn,
    burst_starts,
    commit_excess,
    delta_latencies,
    growth,
    n16_capacity_rate,
    n16_turn_period_s,
    n16_turn_starts,
    run_accounting,
    server_errors,
    soak_state,
    stats,
)
from harness_rig import aborts_per_commit, commit_records, node_events, transaction_counts  # noqa: E402
from slo_gates import SLO_GATES, SLO_ROWS, Gate, Reason, evaluate, gates, passed, profiles_gated  # noqa: E402

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]


def _slo_md_rows() -> list[str]:
    text = (REPO / "docs/agent-harness-v2/SLO.md").read_text()
    summary = text.split("## Summary", 1)[1].split("\n## ", 1)[0]
    return [f"#{m}" for m in re.findall(r"^\| (\d+) \|", summary, re.M)]


def test_qa22_15_every_slo_row_has_a_gate_or_a_written_reason() -> None:
    assert _slo_md_rows() == list(SLO_ROWS)
    covered = {row.slo for row in SLO_GATES}
    assert set(SLO_ROWS) <= covered
    for row in SLO_GATES:
        if isinstance(row, Reason):
            assert row.owner and len(row.why) > 20, row


def test_qa22_15_no_gate_is_looser_than_its_slo_target() -> None:
    for gate in gates():
        assert gate.threshold <= gate.target, gate


def _gate(threshold: float = 100.0) -> Gate:
    return Gate("#1", "code-mode-5k", "commit_ms", "p99", 100.0, threshold)


@pytest.mark.parametrize(
    ("value", "load", "status"),
    [
        (80.0, 3.0, "pass"),
        (100.0, 3.0, "pass"),
        (100.1, 3.0, "fail"),
        (80.0, 33.0, "rerun"),
        (None, 3.0, "missing"),
    ],
)
def test_qa22_16_verdicts(value: float | None, load: float, status: str) -> None:
    metrics = {} if value is None else {"commit_ms": {"p99": value}}
    results = {"code-mode-5k": {"metrics": metrics, "load1_max": load}}
    assert [v.status for v in evaluate(results, cores=32, rows=(_gate(),))] == [status]


def test_qa22_16_a_waived_miss_is_reported_as_its_finding_and_does_not_fail_the_job() -> None:
    gate = Gate("#1", "code-mode-5k", "commit_ms", "p99", 100.0, 100.0, waived_by="QA22-F2")
    results = {"code-mode-5k": {"metrics": {"commit_ms": {"p99": 240.0}}, "load1_max": 3.0}}
    verdict = next(v for v in evaluate(results, cores=32, rows=(gate,)))
    assert (verdict.status, verdict.detail) == ("waived", "QA22-F2")
    assert passed([verdict]) is True


def test_qa22_15_every_waiver_names_a_finding_and_an_expiry() -> None:
    waived = [gate for gate in gates() if gate.waived_by]
    for gate in waived:
        assert re.fullmatch(r"(QA22|PERFB|PERFC)-F\d+", gate.waived_by), gate
        assert gate.waiver_expires, f"{gate.waived_by}'s waiver of {gate.slo} names no expiry"
    # No row is waived (SLO.md, decision of 2026-10-12): sessions in step are held to SLO #1 and SLO #4 at their
    # own bounds on the open loop, and the closed loop is held to overload bounds, not to those SLOs.
    assert waived == []
    held = {(g.slo, g.profile, g.metric, g.stat, g.threshold) for g in gates() if g.slo in ("#1", "#4")}
    assert held >= {
        ("#1", "commits-n16-instep", "commit_ms", "p99", 100.0),
        ("#4", "commits-n16-instep", "admission_ms", "p95", 150.0),
    }
    assert not [g for g in gates() if g.profile == "commits-n16-burst" and g.slo.startswith("#")]


def test_perfb_19_a_waived_verdict_names_its_finding_and_its_expiry() -> None:
    gate = Gate("#1", "commits-n16-burst", "commit_ms", "p99", 100.0, 100.0, waived_by="PERFB-F1",
                waiver_expires="before the PR")
    results = {"commits-n16-burst": {"metrics": {"commit_ms": {"p99": 288.0}}, "load1_max": 3.0}}
    verdict = next(v for v in evaluate(results, cores=32, rows=(gate,)))
    assert (verdict.status, verdict.detail) == ("waived", "PERFB-F1, expires before the PR")


def test_perfc_50_overload_is_held_to_what_it_must_not_do() -> None:
    burst = {(g.metric, g.stat, g.threshold, g.at_least) for g in gates() if g.profile == "commits-n16-burst"}
    assert burst == {
        ("aborts_per_commit", "max", 1.0, False),
        ("capacity_multiple", "max", 2.0, True),
        ("admission_ms", "p95", 1000.0, False),
        ("runs_lost", "max", 0.0, False),
        ("runs_duplicated", "max", 0.0, False),
    }
    floor = next(g for g in gates() if g.metric == "capacity_multiple")
    at = lambda value: next(  # noqa: E731
        v.status for v in evaluate({"commits-n16-burst": {"metrics": {"capacity_multiple": stats([value])},
                                                          "load1_max": 3.0}}, cores=32, rows=(floor,)))
    # A floor: PERFB-F1's storm ran at 1.2 to 1.3 times the model's rate; the rig answers at 3.4 and more.
    assert [at(3.4), at(2.0), at(1.3)] == ["pass", "pass", "fail"]


def test_perfc_50_runs_lost_and_duplicated_are_counted_from_the_runs_that_ended() -> None:
    def ended(*run_ids: str) -> list[dict[str, object]]:
        return [{"runId": run_id, "kind": "complete", "ms": 1.0, "events": 1} for run_id in run_ids]

    def count(commits: list[dict[str, object]], runs: set[str], expected: int, **more: frozenset[str]) -> tuple[float, float]:
        result = run_accounting(commits, runs, expected, **more)  # type: ignore[arg-type]
        return result["runs_lost"]["max"], result["runs_duplicated"]["max"]

    assert count(ended("a", "b"), {"a", "b"}, 2) == (0.0, 0.0)
    assert count(ended("a"), {"a", "b"}, 2) == (1.0, 0.0)
    assert count(ended("a"), {"a"}, 2) == (1.0, 0.0), "a turn that never came back is a run lost"
    assert count(ended("a", "a", "b"), {"a", "b"}, 2) == (0.0, 1.0)
    assert count(ended("a", "b", "x"), {"a", "b"}, 2) == (0.0, 1.0), "a run no turn asked for"
    assert count(ended("a", "b", "warm"), {"a", "b"}, 2, unrecorded=frozenset({"warm"})) == (0.0, 0.0)
    other_kinds = [{"runId": "a", "kind": "model", "ms": 1.0, "events": 1}]
    assert count(other_kinds, {"a"}, 1) == (1.0, 0.0)


def test_perfc_50_the_in_step_profile_starts_every_session_together_a_period_apart() -> None:
    period = n16_turn_period_s()
    starts = n16_turn_starts(turns=3, period_s=period, in_step=True)
    assert starts == [[0.0, period, 2 * period]] * N16_SESSIONS
    # The open loop's pace is the capacity model's: the same commits a second as the out-of-step profile.
    assert n16_capacity_rate(period) == pytest.approx(N16_SESSIONS * FLOW_COMMITS / period)


def test_perfc_50_the_burst_is_gated_on_the_aborts_mongodb_counted_with_no_waiver() -> None:
    gate = next(g for g in gates() if g.metric == "aborts_per_commit" and g.profile == "commits-n16-burst")
    assert (gate.profile, gate.stat, gate.threshold, gate.waived_by) == ("commits-n16-burst", "max", 1.0, "")

    def verdict(aborts: float | None) -> str:
        metrics = {} if aborts is None else {"aborts_per_commit": stats([aborts])}
        results = {"commits-n16-burst": {"metrics": metrics, "load1_max": 3.0}}
        return next(v.status for v in evaluate(results, cores=32, rows=(gate,)))

    # PERFB-F1's storm (13 aborted transactions a commit) fails the job; a reading that is not there does too.
    assert [verdict(0.1), verdict(13.6), verdict(None)] == ["pass", "fail", "missing"]


def test_perfc_50_aborts_per_commit_is_read_from_mongodb_s_own_counters() -> None:
    noisy = 'Warning: something mongosh prints\n{"committed":1505,"aborted":22051}\n'
    assert transaction_counts(noisy) == {"committed": 1505, "aborted": 22051}
    assert transaction_counts("") is None
    assert transaction_counts('{"ok":1}\nnot json') is None

    before = {"committed": 100, "aborted": 40}
    assert aborts_per_commit(before, {"committed": 1605, "aborted": 22091}) == pytest.approx(14.65, abs=0.01)
    assert aborts_per_commit(before, {"committed": 1900, "aborted": 190}) == pytest.approx(0.083, abs=0.001)
    # No reading, or nothing committed between the two: no number, so the gate reports it missing.
    assert aborts_per_commit(None, before) is None
    assert aborts_per_commit(before, None) is None
    assert aborts_per_commit(before, before) is None


def test_qa22_16_only_pass_passes_and_profiles_not_run_are_not_judged() -> None:
    results = {"modes": {"metrics": {}, "load1_max": 40.0}}
    verdicts = evaluate(results, cores=32)
    assert {v.row.profile for v in verdicts} == {"modes"}
    assert {v.status for v in verdicts} == {"rerun"}
    assert passed(verdicts) is False
    assert passed([]) is True


def test_qa22_17_the_weekly_and_release_jobs_run_every_gated_rig_profile() -> None:
    assert set(PROFILES) == set(RIG_PROFILES)
    assert profiles_gated() <= set(RIG_PROFILES)
    workflow = yaml.safe_load((REPO / ".github/workflows/perf-harness.yml").read_text())
    triggers = workflow.get("on") or workflow.get(True)
    assert "schedule" in triggers and "release" in triggers
    job = workflow["jobs"]["harness-latency"]
    script = "\n".join(step.get("run", "") for step in job["steps"])
    assert "--profile rig" in script and "--fail-on-gate" in script


def test_perfb_19_commits_n16_is_paced_at_the_capacity_models_n16_rate() -> None:
    """SLO #1 is stated at the capacity model's N = 16 rate; the profile's pace is that rate, read from the model."""
    table = (REPO / "docs/agent-harness-v2/capacity-baseline.md").read_text()
    row = next(line for line in table.splitlines() if line.startswith("| busiest recording") and "commits/s" in line)
    published = float(row.split("|")[5])
    period = n16_turn_period_s()
    assert round(N16_SESSIONS * FLOW_COMMITS / period, 1) == published
    # Twice the commits a turn at the same rate: half as many turns.
    assert n16_turn_period_s(2 * FLOW_COMMITS) == pytest.approx(2 * period)


def test_perfb_19_paced_sessions_arrive_out_of_step_at_the_same_rate_every_run() -> None:
    period = 6.0
    starts = n16_turn_starts(turns=10, period_s=period)
    assert len(starts) == N16_SESSIONS and all(len(session) == 10 for session in starts)
    assert n16_turn_starts(turns=10, period_s=period) == starts
    for session in starts:
        gaps = [later - earlier for earlier, later in zip(session, session[1:], strict=False)]
        # Each turn within a quarter period of its slot: consecutive turns half a period apart at least.
        assert all(period * 0.5 <= gap <= period * 1.5 for gap in gaps)
    first = sorted(session[0] for session in starts)
    assert len(set(first)) == N16_SESSIONS, "sessions do not start together"
    assert max(first) < period * 1.25
    span = max(session[-1] for session in starts) - min(first)
    assert N16_SESSIONS * 10 / span == pytest.approx(N16_SESSIONS / period, rel=0.15)


def test_perfb_19_the_burst_profile_starts_every_session_together_back_to_back() -> None:
    assert n16_turn_starts(turns=3, period_s=0.0) == [[0.0, 0.0, 0.0]] * N16_SESSIONS


WORKER_LOG = """\
2026-10-11 06:46:01,996 - app.harness.journal_node - INFO - [logs.py:108] - journal.commit orgId=o runId=run-1 attemptId=a1 fence=1 seq=7 commandId=a1:model:1 events=4 firstSeq=4 ms=32 replayed=False
2026-10-11 06:46:02,001 - app.harness.journal_node - WARNING - [logs.py:108] - journal.commit_failed orgId=o runId=run-1 commandId=a1:outcomes:1 ms=900
2026-10-11 06:46:02,058 - app.harness.journal_node - INFO - [logs.py:108] - journal.commit orgId=o runId=run-2 attemptId=a2 fence=1 seq=10 commandId=a2:complete:2 events=2 firstSeq=8 ms=60 replayed=False transition=complete
"""


def test_commit_records_read_each_commit_its_run_and_kind() -> None:
    assert commit_records(WORKER_LOG) == [
        {"runId": "run-1", "kind": "model", "events": 4, "ms": 32.0},
        {"runId": "run-2", "kind": "complete", "events": 2, "ms": 60.0},
    ]


def test_node_events_read_the_metadata_block_of_each_record() -> None:
    log = (
        "2026-10-11 06:46:01,500 - nodejs_service - INFO - [run-admission-service.ts:485] - admission.accepted\n"
        'metadata: {\n  "runId": "r1",\n  "ms": 71\n}\n'
        "2026-10-11 06:46:01,519 - nodejs_service - INFO - [stream-service.ts:506] - projection.subscribe\n"
        'metadata: {\n  "runId": "r1"\n}\n'
    )
    assert node_events(log, "admission.accepted") == [{"runId": "r1", "ms": 71}]


def test_commit_excess_allows_two_plus_two_per_tool_for_each_tool_turn() -> None:
    run = [{"runId": "r", "kind": k} for k in ("prerun", "model", "intent", "outcomes", "model", "complete")]
    # One tool turn of one call: 2 + 2 x 1, plus the run's own two.
    assert commit_excess(run, tool_calls_per_turn=1) == [0.0]
    assert commit_excess([*run, {"runId": "r", "kind": "outcomes"}], tool_calls_per_turn=1) == [1.0]


def test_a_coalesced_frame_carries_each_of_its_chunks_latency() -> None:
    turn = Turn(run_id="run-abc", texts=[(10.050, "abc-0000 "), (10.100, "abc-0001 abc-0002 ")])
    emitted = [{"runId": "run-abc", "text": t, "t": at}
               for t, at in (("abc-0000 ", 10.0), ("abc-0001 ", 10.02), ("abc-0002 ", 10.04))]
    emitted.append({"runId": "other", "text": "abc-0000 ", "t": 0.0})
    assert [round(v) for v in delta_latencies(turn, emitted)] == [50, 80, 60]


def test_stats_reports_the_percentiles_the_gates_read() -> None:
    assert stats([float(v) for v in range(1, 101)]) == {"n": 100, "p50": 50.5, "p95": 95.0, "p99": 99.0, "max": 100.0}
    assert stats([])["p95"] is None


# ---- WP-88: PF-15's burst and PF-14's soak (SLO #19) ----


def test_wp88_b1_the_burst_creates_200_sessions_within_its_minute_the_same_way_every_run() -> None:
    starts = burst_starts()
    assert len(starts) == BURST_SESSIONS == 200
    assert starts == sorted(starts) == burst_starts()
    assert starts[0] == 0.0 and starts[-1] <= BURST_WINDOW_S == 60.0
    # Arrivals cover the minute rather than bunching at one end of it.
    assert sum(1 for s in starts if s < BURST_WINDOW_S / 2) == pytest.approx(BURST_SESSIONS / 2, rel=0.2)


def test_wp88_b2_the_burst_is_held_to_pf15_admission_and_no_run_lost_or_refused() -> None:
    burst = {(g.slo, g.metric, g.stat, g.threshold) for g in gates() if g.profile == "burst-200"}
    assert burst == {
        ("PF-15", "admission_ms", "p95", 150.0),
        ("PF-15", "server_errors", "max", 0.0),
        ("PF-15", "runs_lost", "max", 0.0),
        ("PF-15", "runs_duplicated", "max", 0.0),
    }
    assert "burst-200" in profiles_gated(), "the weekly and release rig runs the burst"


def test_wp88_b3_a_5xx_is_counted_as_a_server_error_and_a_quota_refusal_is_not() -> None:
    assert server_errors([200, 200, 503, 500, 429, None]) == 2


def test_wp88_s1_growth_is_the_last_tenths_median_over_the_baseline_after_warm_up() -> None:
    flat = [100.0] * 50
    assert growth(flat) == 0.0
    # The first tenth (start-up, caches filling) is not the baseline.
    assert growth([10.0] * 5 + [100.0] * 45) == 0.0
    leaking = [100.0 + i for i in range(50)]
    assert growth(leaking) == pytest.approx((147.0 - 107.0) / 107.0, abs=0.02)
    assert growth([]) is None and growth([1.0, 2.0]) is None


def test_wp88_s2_slo19_is_gated_on_the_soak_with_none_left_and_growth_under_ten_percent() -> None:
    soak = {(g.slo, g.metric, g.stat, g.threshold) for g in gates() if g.profile == "soak"}
    assert soak == {
        ("#19", "orphan_runs", "max", 0.0),
        ("#19", "ownerless_leases", "max", 0.0),
        ("#19", "stuck_outbox", "max", 0.0),
        ("#19", "rss_growth[node]", "max", 10.0),
        ("#19", "rss_growth[worker]", "max", 10.0),
        ("#19", "fd_growth[node]", "max", 10.0),
    }
    assert not any(isinstance(r, Reason) and r.slo == "#19" for r in SLO_GATES)
    # 24 h: not in the weekly rig set; its own job runs it.
    assert "soak" not in profiles_gated()


def test_wp88_s3_the_soak_job_runs_the_soak_profile_for_24_hours_and_fails_on_its_gate() -> None:
    workflow = yaml.safe_load((REPO / ".github/workflows/perf-harness.yml").read_text())
    job = workflow["jobs"]["harness-soak"]
    script = "\n".join(step.get("run", "") for step in job["steps"])
    assert "--profile soak" in script and "--fail-on-gate" in script
    assert "SOAK_MINUTES" in script
    assert workflow["env"]["SOAK_MINUTES_INPUT"].endswith("|| '1440' }}")
    assert job["timeout-minutes"] >= 1440 + 60
    assert "soak" in RIG_PROFILES


def test_wp88_s4_the_soak_state_reads_runs_leases_and_outbox_left_after_the_drain() -> None:
    printed = 'noise\n{"orphanRuns": 0, "ownerlessLeases": 1}\n'
    assert soak_state(printed, {"pending": 2, "publishing": 0, "published": 90, "failed": 1}) == {
        "orphan_runs": 0, "ownerless_leases": 1, "stuck_outbox": 3}
    assert soak_state("", {"pending": 0}) is None


def test_wp88_r1_a_rig_starts_on_no_earlier_run_s_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A second run of a profile into the same directory read the first run's commits as its own: the burst counted
    the earlier burst's 216 runs as duplicated."""
    import harness_rig
    from harness_rig import Infra, Rig

    out = tmp_path / "burst-200"
    out.mkdir()
    for name in ("worker.log", "node.log", "model-calls.jsonl"):
        (out / name).write_text("journal.commit runId=earlier commandId=a:complete:1 ms=1\n")
    started: list[str] = []

    class _Process:
        def start(self) -> None:
            started.append("node")

    monkeypatch.setattr(harness_rig.stack, "configure", lambda _base: None)
    monkeypatch.setattr(harness_rig.stack, "stack_env", lambda _broker, _db: {})
    monkeypatch.setattr(harness_rig.stack, "node_process", lambda _env, _log: _Process())
    monkeypatch.setattr(harness_rig.stack, "start_live_worker", lambda _env, _out: started.append("worker"))
    monkeypatch.setattr(Rig, "_claim_redis", lambda _self: None)
    Rig(Infra(mongo_uri="mongodb://unused", redis_port=1), out, {}).up()
    assert started == ["node", "worker"]
    assert not any((out / name).exists() for name in ("worker.log", "node.log", "model-calls.jsonl"))


def test_wp88_r2_the_rig_asks_mongodb_in_its_container_on_the_port_it_listens_on() -> None:
    """The soak's left-state read and the rig's database drop ran mongosh in the container with the host's published
    port (ECONNREFUSED): the soak's three SLO #19 readings came back missing and every rig database was left."""
    from harness_rig import container_uri

    uri = "mongodb://admin:password@127.0.0.1:47100/?authSource=admin&directConnection=true"
    published = "27017/tcp -> 0.0.0.0:47100\n27017/tcp -> [::]:47100\n"
    assert container_uri(uri, published) == uri.replace(":47100/", ":27017/")
    # Host networking publishes nothing: mongod listens on the port the URI names.
    assert container_uri("mongodb://localhost:47200/?replicaSet=rs0", "") == "mongodb://localhost:47200/?replicaSet=rs0"
