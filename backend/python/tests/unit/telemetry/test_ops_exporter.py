"""The ops exporter's series, labels, carry and file bounds (no process or port involved)."""
from __future__ import annotations

import itertools
import json
import os
import random
import time
from typing import TYPE_CHECKING

from prometheus_client import CollectorRegistry, generate_latest

from app.telemetry import ops_exporter as ox
from tests.support.free_port import free_port

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

FORBIDDEN_LABELS = {"pid", "pod", "member", "memberId", "org", "orgId", "connector", "record", "host", "instance"}

# The pinned table: name -> (type, labels, fleet). Changing a series is a deliberate edit of this list.
PINNED = {
    "pipeshub_governor_pool_limit": ("gauge", ("service", "pool"), False),
    "pipeshub_governor_pool_ceiling": ("gauge", ("service", "pool"), False),
    "pipeshub_governor_pool_in_use": ("gauge", ("service", "pool"), False),
    "pipeshub_governor_pool_waiters": ("gauge", ("service", "pool"), False),
    "pipeshub_governor_pool_braked": ("gauge", ("service", "pool"), False),
    "pipeshub_governor_brakes_total": ("counter", ("service", "pool", "reason"), False),
    "pipeshub_governor_oldest_not_started_seconds": ("gauge", ("service",), False),
    "pipeshub_cgroup_memory_pressure_ratio": ("gauge", ("service",), False),
    "pipeshub_cgroup_cpu_utilisation_ratio": ("gauge", ("service",), False),
    "pipeshub_cgroup_memory_working_set_ratio": ("gauge", ("service",), False),
    "pipeshub_cgroup_psi_ratio": ("gauge", ("service", "resource", "kind", "window"), False),
    "pipeshub_cgroup_oom_kills_total": ("counter", ("service",), False),
    "pipeshub_downstream_events_total": ("counter", ("service", "dependency", "kind"), False),
    "pipeshub_worker_last_progress_timestamp_seconds": ("gauge", ("service",), False),
    "pipeshub_draining": ("gauge", ("service",), False),
    "pipeshub_load_outbox_overflow_total": ("counter", ("edge",), False),
    "pipeshub_shutdown_grace_exceeded_total": ("counter", (), False),
    "pipeshub_parse_units_total": ("counter", ("class", "outcome"), False),
    "pipeshub_parse_admission_running": ("gauge", ("pool", "class"), False),
    "pipeshub_parse_admission_waiting": ("gauge", ("pool", "class"), False),
    "pipeshub_parse_admission_grants_total": ("counter", ("pool", "class", "rule"), False),
    "pipeshub_parse_admission_capacity": ("gauge", ("pool", "class"), False),
    "pipeshub_parse_engine_limit": ("gauge", ("engine",), False),
    "pipeshub_parse_engine_in_use": ("gauge", ("engine",), False),
    "pipeshub_parse_engine_fallback_total": ("counter", ("reason",), False),
    "pipeshub_parse_unknown_messages_total": ("counter", ("action",), False),
    "pipeshub_parse_cost_upgrades_total": ("counter", ("from", "to", "mode"), False),
    "pipeshub_parse_admission_unscoped_total": ("counter", (), False),
    "pipeshub_parse_documents_total": ("counter", ("outcome",), False),
    "pipeshub_stage_schema_guard": ("gauge", ("state",), False),
    "pipeshub_queue_pending": ("gauge", ("queue", "class"), True),
    "pipeshub_queue_in_flight": ("gauge", ("queue", "class"), True),
    "pipeshub_queue_oldest_age_seconds": ("gauge", ("queue", "class"), True),
    "pipeshub_queue_pending_cost_units": ("gauge", ("queue", "class"), True),
    "pipeshub_load_state": ("gauge", ("kind", "state"), True),
    "pipeshub_load_notifications_total": ("counter", ("kind", "edge"), True),
    "pipeshub_load_evaluator_last_tick_timestamp_seconds": ("gauge", (), True),
    "pipeshub_load_clock_regressions_total": ("counter", (), True),
}


def test_series_contract() -> None:
    assert {n: (s.kind, s.labels, s.fleet) for n, s in ox.SERIES.items()} == PINNED
    for name, spec in ox.SERIES.items():
        assert (spec.kind == "counter") == name.endswith("_total"), name


def _snapshot(run: Path, pid: int, metrics: dict, *, at: float = 1000.0, nonce: str | None = None, start: str | None = "1",
              service: str = "indexing") -> Path:
    path = run / "signals" / f"{pid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid, "nonce": nonce or f"n{pid}", "start": start, "at": at,
                                "service": service, "signal": {}, "metrics": metrics}))
    return path


def _collector(run: Path, *, alive: set[int] | None = None, now: float = 1000.0, fleet=None) -> ox.SignalCollector:
    return ox.SignalCollector(run, clock=lambda: now, is_alive=lambda pid, start: alive is None or pid in alive, fleet_source=fleet)


def _text(collector: ox.SignalCollector) -> str:
    registry = CollectorRegistry(auto_describe=False)
    registry.register(collector)  # type: ignore[arg-type]
    return generate_latest(registry).decode()


def _value(text: str, series: str) -> float:
    for line in text.splitlines():
        if line.startswith(series + " "):
            return float(line.split()[1])
    raise KeyError(series)


def test_live_gauges_aggregate_by_their_rule(tmp_path: Path) -> None:
    for pid, used, ratio in ((1, 3, 0.5), (2, 5, 0.9)):
        _snapshot(tmp_path, pid, {
            "pipeshub_governor_pool_in_use": [[["indexing", "heavy_parse"], used]],
            "pipeshub_cgroup_memory_pressure_ratio": [[["indexing"], ratio]],
        })
    text = _text(_collector(tmp_path))
    assert _value(text, 'pipeshub_governor_pool_in_use{pool="heavy_parse",service="indexing"}') == 8
    assert _value(text, 'pipeshub_cgroup_memory_pressure_ratio{service="indexing"}') == 0.9


def test_a_vanished_process_has_no_gauges_and_its_file_is_deleted(tmp_path: Path) -> None:
    path = _snapshot(tmp_path, 7, {"pipeshub_governor_pool_in_use": [[["indexing", "heavy_parse"], 4]]})
    text = _text(_collector(tmp_path, alive=set()))
    assert "pool_in_use" not in text and not path.exists()


def test_counter_carry_is_not_inflated_by_a_restart(tmp_path: Path) -> None:
    """A dies at 50 and its replacement counts 0 then 1: increase() is 1, not 51 (the naive sum)."""
    series = 'pipeshub_governor_brakes_total{pool="heavy_parse",reason="memory_soft",service="indexing"}'
    metric = lambda n: {"pipeshub_governor_brakes_total": [[["indexing", "heavy_parse", "memory_soft"], n]]}  # noqa: E731
    _snapshot(tmp_path, 1, metric(50))
    _snapshot(tmp_path, 2, metric(50))
    seen: list[float] = []

    def scrape(alive: set[int]) -> float:
        text = _text(_collector(tmp_path, alive=alive))
        return _value(text, series)

    seen.append(scrape({1, 2}))
    seen.append(scrape({2}))  # 1 is gone: folded once
    _snapshot(tmp_path, 3, metric(0))
    seen.append(scrape({2, 3}))
    _snapshot(tmp_path, 3, metric(1))
    seen.append(scrape({2, 3}))
    assert seen == [100, 100, 100, 101]
    increase = sum((b - a) if b >= a else b for a, b in zip(seen, seen[1:], strict=False))
    assert increase == 1


def test_fold_is_idempotent_when_two_holders_overlap(tmp_path: Path) -> None:
    metric = {"pipeshub_parse_units_total": [[["small", "ok"], 10]]}
    path = _snapshot(tmp_path, 1, metric)
    first, second = _collector(tmp_path, alive=set()), _collector(tmp_path, alive=set())
    first._fold(json.loads(path.read_text()))
    second._fold(json.loads(path.read_text()))
    carry = json.loads((tmp_path / ox.CARRY_FILE).read_text())
    assert list(carry["values"].values()) == [10]


def test_a_reused_pid_with_a_different_start_time_is_not_alive(tmp_path: Path) -> None:
    path = _snapshot(tmp_path, os.getpid(), {"pipeshub_parse_units_total": [[["small", "ok"], 3]]}, start="1", at=time_now())
    collector = ox.SignalCollector(tmp_path)  # the real /proc check
    assert collector.scan() == [] and not path.exists()
    assert list(json.loads((tmp_path / ox.CARRY_FILE).read_text())["values"].values()) == [3]


def time_now() -> float:
    import time

    return time.time()


def test_a_live_process_with_a_matching_start_time_is_served(tmp_path: Path) -> None:
    _snapshot(tmp_path, os.getpid(), {}, start=ox._proc_start(os.getpid()), at=time_now())
    assert len(ox.SignalCollector(tmp_path).scan()) == 1


def test_oom_kills_use_the_cgroup_value_without_a_carry(tmp_path: Path) -> None:
    for pid in (1, 2):
        _snapshot(tmp_path, pid, {"pipeshub_cgroup_oom_kills_total": [[["indexing"], 4]]})
    collector = _collector(tmp_path, alive={1})
    text = _text(collector)
    assert _value(text, 'pipeshub_cgroup_oom_kills_total{service="indexing"}') == 4  # not 8, and the dead one is not carried
    assert not (tmp_path / ox.CARRY_FILE).exists() or json.loads((tmp_path / ox.CARRY_FILE).read_text())["values"] == {}


def test_cardinality_budget(tmp_path: Path) -> None:
    """Hostile snapshots: unknown series, free-text labels, wrong arity, forbidden label values, a flood."""
    rng = random.Random(3)
    rows = lambda name, n, label: [[[label(i)], float(i)] for i in range(n)]  # noqa: E731
    for pid in range(1, 40):
        _snapshot(tmp_path, pid, {
            "pipeshub_governor_pool_in_use": [[["indexing", p], 1] for p in ("index_heavy", "heavy_parse", "org-1", f"p{pid}")],
            "pipeshub_cgroup_memory_pressure_ratio": rows("", 500, lambda i: f"svc-{i}") + [[["org-123 name"], 1]],  # noqa: RUF005
            "pipeshub_downstream_events_total": [[["indexing", f"org-{i}", "throttle"], 1] for i in range(50)],
            "pipeshub_parse_units_total": [[["small", f"o{rng.random()}"], 1], [["small"], 1]],
            "not_a_series": [[[], 1]],
            "pipeshub_queue_pending": [[["parse", "small"], 9]],  # a fleet series from a process: refused
        })
    text = _text(_collector(tmp_path))
    samples = [line for line in text.splitlines() if line and not line.startswith("#")]
    assert len(samples) <= ox.MAX_SERIES
    assert "not_a_series" not in text and "pipeshub_queue_pending" not in text
    assert "org-" not in text and "org-123" not in text
    for line in samples:
        labels = line[line.find("{") + 1 : line.find("}")] if "{" in line else ""
        assert not ({kv.split("=")[0] for kv in labels.split(",") if kv} & FORBIDDEN_LABELS)
    assert not {s.split("{")[0] for s in samples} - {n.removesuffix("_total") + ("_total" if ox.SERIES[n].kind == "counter" else "") for n in ox.SERIES}


def test_every_series_label_is_a_closed_set_or_the_service() -> None:
    for name, spec in ox.SERIES.items():
        for label in spec.labels:
            assert label in ox.CLOSED_LABELS or label in {"service", "dependency"}, (name, label)
        assert not set(spec.labels) & FORBIDDEN_LABELS


def test_fleet_series_come_only_from_the_fleet_source(tmp_path: Path) -> None:
    assert "pipeshub_queue_pending" not in _text(_collector(tmp_path))
    fleet = lambda: [  # noqa: E731
        ("pipeshub_queue_pending", ("parse", "small"), 12.0),
        ("pipeshub_governor_pool_limit", ("indexing", "heavy_parse"), 9.0),  # not a fleet series: refused
        ("pipeshub_queue_pending", ("parse", "org-9"), 1.0),  # open label: refused
    ]
    text = _text(_collector(tmp_path, fleet=fleet))
    assert _value(text, 'pipeshub_queue_pending{class="small",queue="parse"}') == 12
    assert "pool_limit" not in text and "org-9" not in text
    assert "pipeshub_queue_pending" not in _text(_collector(tmp_path, fleet=lambda: None))

    def broken():  # noqa: ANN202
        raise RuntimeError("state store down")

    assert _text(_collector(tmp_path, fleet=broken)) == ""


def test_run_dir_unwritable_serves_only_the_fleet_series(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    run = blocker / "run"  # a path under a regular file cannot be created
    writer = ox.SnapshotWriter(run, "indexing", lambda: ({}, {}))
    with caplog.at_level("WARNING"):
        assert writer.write() is False and writer.write() is False
    assert len([r for r in caplog.records if "cannot write the snapshot" in r.message]) == 1
    text = _text(ox.SignalCollector(run, fleet_source=lambda: [("pipeshub_queue_pending", ("parse", "small"), 1.0)]))
    assert text.count("pipeshub_queue_pending{") == 1 and "governor" not in text


def test_gc_bound_under_a_crash_loop(tmp_path: Path) -> None:
    """One restart per 20 s for 30 minutes: the live file count stays small, every dead file is folded once and deleted."""
    now = [0.0]
    alive: set[int] = set()
    collector = ox.SignalCollector(tmp_path, clock=lambda: now[0], is_alive=lambda pid, start: pid in alive)
    peak = 0
    for i in range(90):
        pid = 1000 + i
        alive.discard(pid - 1)
        alive.add(pid)
        _snapshot(tmp_path, pid, {"pipeshub_parse_units_total": [[["small", "failed"], 1]]}, at=now[0])
        collector.gc()
        peak = max(peak, len(list((tmp_path / "signals").glob("*.json"))))
        now[0] += 20.0
    assert peak <= ox.MAX_FILES and peak <= 2
    carry = json.loads((tmp_path / ox.CARRY_FILE).read_text())
    assert sum(carry["values"].values()) == 89 and len(carry["folded"]) == 89
    assert _value(_text(collector), 'pipeshub_parse_units_total{class="small",outcome="failed"}') == 90


def test_the_folded_list_is_bounded(tmp_path: Path) -> None:
    collector = _collector(tmp_path, alive=set())
    for pid in range(1, ox._FOLDED_KEPT + 50):
        collector._fold({"pid": pid, "nonce": "n", "metrics": {}})
    assert len(json.loads((tmp_path / ox.CARRY_FILE).read_text())["folded"]) == ox._FOLDED_KEPT


def test_derive_metrics_from_a_projected_signal() -> None:
    signal = {
        "pools": {"heavy_parse": {"limit": 4, "ceiling": 8, "inUse": 3, "waiters": 2, "state": "braked"}},
        "waiting": {"oldestNotStartedS": 12.5},
        "cpu": {"util": 0.4, "psiSomeAvg10": 0.1, "psiSomeAvg60": None},
        "mem": {"pressureRaw": 0.8, "workingSetRatio": 0.8, "psiSomeAvg60": 0.02, "psiFullAvg60": 0.01, "oomKillsTotal": 2},
        "draining": False,
    }
    out = ox.derive_metrics("indexing", signal)
    assert out["pipeshub_governor_pool_braked"] == [[["indexing", "heavy_parse"], 1.0]]
    assert out["pipeshub_governor_oldest_not_started_seconds"] == [[["indexing"], 12.5]]
    kinds = {tuple(r[0][1:]) for r in out["pipeshub_cgroup_psi_ratio"]}
    assert kinds == {("cpu", "some", "avg10"), ("memory", "some", "avg60"), ("memory", "full", "avg60")}
    assert out["pipeshub_draining"] == [[["indexing"], 0.0]]
    for name in out:
        assert name in ox.SERIES


class _FakeGovernor:
    def __init__(self, stats: dict) -> None:
        self.value = stats

    def stats(self) -> dict:
        return self.value


def _admission(rule: str, seq: int, totals: dict | None = None, brakes: dict | None = None) -> dict:
    return {"sampleSeq": seq, "brakeEntries": brakes or {}, "feedback": {"window": {"seq": seq}, "totals": totals or {}}, "pools": {"heavy_parse": {"ceiling": 8, "limit": 4, "inUse": 4, "waiting": 3, "rule": rule, "ruleAgeSec": 20.0}}}


def test_governor_source_reads_pools_from_the_admission_block_and_counts_brakes_once() -> None:
    stats = {"worker_count": 1, "cpu_quota": 2.0, "cpu_utilisation": 0.5, "mem_pressure_raw": 0.4,
             "downstream_feedback": {"throttles": {"qdrant": 2}},
             "admission": _admission("grow", 1, {"throttles": {"qdrant": 2}})}
    gov = _FakeGovernor(stats)
    source = ox.GovernorSource(gov, "indexing")
    _signal, metrics = source()
    assert metrics["pipeshub_governor_pool_waiters"] == [[["indexing", "heavy_parse"], 3.0]]
    assert metrics["pipeshub_governor_pool_braked"] == [[["indexing", "heavy_parse"], 0.0]]
    assert metrics["pipeshub_governor_brakes_total"] == []
    stats["admission"] = _admission("mem_soft", 2, {"throttles": {"qdrant": 2}}, {"heavy_parse": {"memory_soft": 1}})
    source()
    _signal, metrics = source()  # the same rule on the next read is not a second brake; the same totals are not counted twice
    assert metrics["pipeshub_governor_brakes_total"] == [[["indexing", "heavy_parse", "memory_soft"], 1.0]]
    assert metrics["pipeshub_downstream_events_total"] == [[["indexing", "qdrant", "throttle"], 2.0]]
    stats["admission"] = _admission("cpu", 3, None, {"heavy_parse": {"memory_soft": 1, "cpu": 1}})
    _signal, metrics = source()
    assert {tuple(r[0]): r[1] for r in metrics["pipeshub_governor_brakes_total"]} == {
        ("indexing", "heavy_parse", "memory_soft"): 1.0, ("indexing", "heavy_parse", "cpu"): 1.0}


def test_brake_entries_between_two_reads_are_all_exported_and_the_series_never_falls() -> None:
    """The governor counted three entries (enter, leave, enter, leave, enter) while the exporter did not
    read; the next read exports exactly three, whatever rule the pool shows now."""
    stats = {"worker_count": 1, "cpu_quota": 2.0, "cpu_utilisation": 0.5, "mem_pressure_raw": 0.4,
             "admission": _admission("grow", 1, None, {"heavy_parse": {"memory_soft": 0}})}
    source = ox.GovernorSource(_FakeGovernor(stats), "indexing")
    source()
    stats["admission"] = _admission("grow", 4, None, {"heavy_parse": {"memory_soft": 3}})  # the pool shows no brake now
    _signal, metrics = source()
    assert {tuple(r[0]): r[1] for r in metrics["pipeshub_governor_brakes_total"]}[("indexing", "heavy_parse", "memory_soft")] == 3.0
    stats["admission"] = _admission("grow", 5, None, {"heavy_parse": {"memory_soft": 2}})  # a reading that falls is not exported
    _signal, metrics = source()
    assert {tuple(r[0]): r[1] for r in metrics["pipeshub_governor_brakes_total"]}[("indexing", "heavy_parse", "memory_soft")] == 3.0


def test_the_brake_series_does_not_fall_across_a_holder_change(tmp_path: Path) -> None:
    """Process one counted 3 entries and left; process two starts at 0 and counts 1: 3, then 4, never less."""
    first = {"worker_count": 1, "cpu_utilisation": 0.5, "mem_pressure_raw": 0.4,
             "admission": _admission("grow", 1, None, {"heavy_parse": {"memory_soft": 3}})}
    ox.SnapshotWriter(tmp_path, "indexing", ox.GovernorSource(_FakeGovernor(first), "indexing")).write()
    collector = ox.SignalCollector(tmp_path, cache_ttl=0, is_alive=lambda *_: False)
    seen = [_value(_text(collector), BRAKES)]  # the first process is gone: its three are carried
    second = {"worker_count": 1, "cpu_utilisation": 0.5, "mem_pressure_raw": 0.4,
              "admission": _admission("grow", 1, None, {"heavy_parse": {"memory_soft": 1}})}
    ox.SnapshotWriter(tmp_path, "indexing", ox.GovernorSource(_FakeGovernor(second), "indexing")).write()
    live = ox.SignalCollector(tmp_path, cache_ttl=0, is_alive=lambda *_: True)
    seen.append(_value(_text(live), BRAKES))
    assert seen == [3.0, 4.0]


def test_governor_source_without_the_admission_block_exports_no_pool_series() -> None:
    gov = _FakeGovernor({"worker_count": 1, "cpu_quota": 2.0, "cpu_utilisation": 0.5, "mem_pressure_raw": 0.4,
                         "downstream_feedback": {"throttles": {"qdrant": 2}}})
    signal, metrics = ox.GovernorSource(gov, "indexing")()
    assert signal["pools"] == {}
    assert not any(name.startswith(("pipeshub_governor_pool", "pipeshub_governor_oldest")) for name in metrics)
    assert metrics["pipeshub_downstream_events_total"] == []  # no admission block: unknown, not counted
    assert metrics["pipeshub_cgroup_memory_pressure_ratio"] == [[["indexing"], 0.4]]


def test_downstream_counter_follows_the_totals_across_skipped_and_repeated_samples() -> None:
    """A sample that lands between two reads of stats(), or two samples inside one tick, must not be counted
    twice or dropped: the counter is the governor's running total, whatever the sequence does."""
    stats = {"worker_count": 1, "cpu_quota": 2.0, "cpu_utilisation": 0.5, "mem_pressure_raw": 0.4,
             # a stale window next to newer totals: the pre-fix pairing would add this window again
             "downstream_feedback": {"throttles": {"qdrant": 2}}}
    source = ox.GovernorSource(_FakeGovernor(stats), "indexing")

    def read(seq: int, throttles: int) -> float:
        stats["admission"] = _admission("grow", seq, {"throttles": {"qdrant": throttles}, "timeouts": {"qdrant": 1}})
        _signal, metrics = source()
        return {tuple(r[0]): r[1] for r in metrics["pipeshub_downstream_events_total"]}[("indexing", "qdrant", "throttle")]

    assert read(1, 2) == 2.0
    assert read(1, 5) == 5.0  # the sequence did not move but the totals did (a sample landed mid-read)
    assert read(3, 9) == 9.0  # two samples inside one tick: nothing dropped
    assert read(3, 9) == 9.0  # repeated: nothing added
    assert read(4, 7) == 9.0  # a counter never falls


# ---------------------------------------------------------------------------
# Aggregation: container-wide values are not summed over the processes
# ---------------------------------------------------------------------------

OOM = 'pipeshub_cgroup_oom_kills_total{service="indexing"}'
BRAKES = 'pipeshub_governor_brakes_total{pool="heavy_parse",reason="memory_soft",service="indexing"}'


def _brakes(n: float) -> dict:
    return {"pipeshub_governor_brakes_total": [[["indexing", "heavy_parse", "memory_soft"], n]]}


def _oom(n: float) -> dict:
    return {"pipeshub_cgroup_oom_kills_total": [[["indexing"], n]]}


def _scrape(run: Path, alive: set[int], now: float = 1000.0) -> dict[str, float]:
    text = _text(_collector(run, alive=alive, now=now))
    return {ln.rsplit(" ", 1)[0]: float(ln.rsplit(" ", 1)[1]) for ln in text.splitlines() if ln and not ln.startswith("#")}


def _increase(seen: list[float]) -> float:
    """Prometheus ``increase()``: a drop is a counter reset and the new value counts from zero."""
    return sum((b - a) if b >= a else b for a, b in zip(seen, seen[1:], strict=False))


def test_every_cgroup_series_combines_by_max() -> None:
    """Each process reads the same container-wide cgroup files, so a sum would multiply the value by the process count."""
    cgroup = {n: s for n, s in ox.SERIES.items() if n.startswith("pipeshub_cgroup_")}
    assert cgroup and {n: s.agg for n, s in cgroup.items()} == dict.fromkeys(cgroup, "max")


def test_oom_kills_with_several_live_processes_is_the_cgroup_value(tmp_path: Path) -> None:
    for pid in (1, 2, 3, 4):
        _snapshot(tmp_path, pid, _oom(2))
    assert _scrape(tmp_path, {1, 2, 3, 4})[OOM] == 2


def test_oom_kills_increase_is_zero_over_a_restart_without_an_oom(tmp_path: Path) -> None:
    for pid in (1, 2, 3):
        _snapshot(tmp_path, pid, _oom(2))
    seen = [_scrape(tmp_path, {1, 2, 3})[OOM]]
    seen.append(_scrape(tmp_path, {2, 3})[OOM])  # one exits
    _snapshot(tmp_path, 4, _oom(2))  # its replacement reads the same cgroup value
    seen.append(_scrape(tmp_path, {2, 3, 4})[OOM])
    seen.append(_scrape(tmp_path, {4})[OOM])  # two more exit
    assert seen == [2, 2, 2, 2] and _increase(seen) == 0


def test_a_real_oom_kill_is_counted_once_not_per_process(tmp_path: Path) -> None:
    for pid in (1, 2, 3):
        _snapshot(tmp_path, pid, _oom(2))
    seen = [_scrape(tmp_path, {1, 2, 3})[OOM]]
    for pid in (1, 2, 3):
        _snapshot(tmp_path, pid, _oom(3))  # the kernel killed one more process
    seen.append(_scrape(tmp_path, {1, 2, 3})[OOM])
    assert seen == [2, 3] and _increase(seen) == 1


def test_a_live_but_late_process_keeps_its_counters_and_hides_its_gauges(tmp_path: Path) -> None:
    """A writer stalled past the stale window (CPU throttling, a long GC) must not make a counter fall and rise."""
    metrics = lambda n: {**_brakes(n), "pipeshub_governor_pool_in_use": [[["indexing", "heavy_parse"], 2]]}  # noqa: E731
    _snapshot(tmp_path, 1, metrics(50), at=1000.0)
    _snapshot(tmp_path, 2, metrics(30), at=1000.0)
    seen = [_scrape(tmp_path, {1, 2}, now=1000.0)]
    _snapshot(tmp_path, 2, metrics(30), at=1030.0)  # process 1 is alive, its file is 30 s old
    seen.append(_scrape(tmp_path, {1, 2}, now=1030.0))
    _snapshot(tmp_path, 1, metrics(50), at=1035.0)
    seen.append(_scrape(tmp_path, {1, 2}, now=1035.0))
    counters = [s[BRAKES] for s in seen]
    assert counters == [80, 80, 80] and _increase(counters) == 0
    in_use = 'pipeshub_governor_pool_in_use{pool="heavy_parse",service="indexing"}'
    assert [s[in_use] for s in seen] == [4, 2, 4]  # the late process's gauge is hidden, not its counter


def test_a_late_process_that_then_dies_is_folded_once(tmp_path: Path) -> None:
    _snapshot(tmp_path, 1, _brakes(50), at=1000.0)
    _snapshot(tmp_path, 2, _brakes(30), at=1030.0)
    seen = [_scrape(tmp_path, {1, 2}, now=1030.0)[BRAKES]]
    seen.append(_scrape(tmp_path, {2}, now=1031.0)[BRAKES])  # 1 died while late
    seen.append(_scrape(tmp_path, {2}, now=1032.0)[BRAKES])
    assert seen == [80, 80, 80]


def test_pid_reuse_cannot_overwrite_a_dead_processes_counters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The file name carries the start time: a new process with a dead one's pid writes another file."""
    monkeypatch.setattr(ox.os, "getpid", lambda: 7)
    monkeypatch.setattr(ox, "_proc_start", lambda pid: "100")
    old = ox.SnapshotWriter(tmp_path, "indexing", lambda: ({}, _brakes(50)))
    assert old.write()
    monkeypatch.setattr(ox, "_proc_start", lambda pid: "200")
    new = ox.SnapshotWriter(tmp_path, "indexing", lambda: ({}, _brakes(0)))
    assert new.write()
    assert old.path != new.path and old.path.exists() and new.path.exists()
    alive = lambda pid, start: start == "200"  # noqa: E731
    text = _text(ox.SignalCollector(tmp_path, is_alive=alive, cache_ttl=0))
    assert _value(text, BRAKES) == 50  # the dead process's 50 is carried, the new one counts from 0


def test_the_restart_of_every_process_with_a_kept_run_dir_does_not_double_count(tmp_path: Path) -> None:
    for pid in (1, 2):
        _snapshot(tmp_path, pid, _brakes(10))
    seen = [_scrape(tmp_path, {1, 2})[BRAKES]]
    for pid in (11, 12):
        _snapshot(tmp_path, pid, _brakes(0))
    seen += [_scrape(tmp_path, {11, 12})[BRAKES], _scrape(tmp_path, {11, 12})[BRAKES]]
    _snapshot(tmp_path, 11, _brakes(1))
    seen.append(_scrape(tmp_path, {11, 12})[BRAKES])
    assert seen == [20, 20, 20, 21] and _increase(seen) == 1


# ---------------------------------------------------------------------------
# Cardinality: the full closed-label product, against fixed numbers
# ---------------------------------------------------------------------------

# The most series one service of a container can ever serve: every series times every value of every
# label it is allowed to carry. It is a literal on purpose: raising it is a deliberate edit that must
# also raise MAX_SERIES in the exporter, which the docs and the Helm values promise.
FULL_PRODUCT_PER_SERVICE = 295
MAX_SERIES_PROMISED = 300


def _population(*, services: int = 1, fleet: bool = True) -> dict[str, int]:
    out: dict[str, int] = {}
    for name, spec in ox.SERIES.items():
        if spec.fleet and not fleet:
            continue
        count = 1
        for label in spec.labels:
            if label == "service":
                count *= services
                continue
            allowed = ox.allowed_values(spec, label)
            assert allowed is not None, (name, label)
            count *= len(allowed)
        out[name] = count
    return out


def test_the_full_closed_label_product_fits_the_budget_for_one_service() -> None:
    per_family = _population(services=1)
    total = sum(per_family.values())
    assert total == FULL_PRODUCT_PER_SERVICE, f"the closed-label product changed: {total}; largest {sorted(per_family.items(), key=lambda kv: -kv[1])[:4]}"
    assert total <= MAX_SERIES_PROMISED
    assert ox.MAX_SERIES == MAX_SERIES_PROMISED


def test_a_series_label_can_only_narrow_its_closed_set() -> None:
    for name, spec in ox.SERIES.items():
        for label, values in spec.allowed:
            assert label in spec.labels and values <= ox.CLOSED_LABELS[label], (name, label)


def test_what_is_cut_first_when_a_container_runs_more_services_than_the_budget_holds(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Two services double the service-labelled families. Over the budget the tail of the table is cut first
    (fleet, then parse), the governor and cgroup families at the head stay, and a warning names what was cut."""
    two = sum(_population(services=2).values())
    assert two > ox.MAX_SERIES  # the honest limit: this is why the cut order matters
    for pid, service in ((1, "indexing"), (2, "docling")):  # each process names its own service
        metrics: dict[str, list] = {}
        for name, spec in ox.SERIES.items():
            if spec.fleet:
                continue
            axes = [[service] if label == "service" else sorted(ox.allowed_values(spec, label) or ()) for label in spec.labels]
            metrics[name] = [[list(c), 1.0] for c in itertools.product(*axes)]
        _snapshot(tmp_path, pid, metrics, service=service)
    fleet = lambda: [("pipeshub_queue_pending", ("parse", "small"), 1.0)]  # noqa: E731
    collector = ox.SignalCollector(tmp_path, clock=lambda: 1000.0, is_alive=lambda *_: True, fleet_source=fleet, cache_ttl=0)
    with caplog.at_level("WARNING"):
        text = _text(collector)
    samples = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    assert len(samples) == ox.MAX_SERIES
    assert "pipeshub_governor_pool_limit" in text and "pipeshub_cgroup_oom_kills_total" in text  # the head is kept
    assert "pipeshub_queue_pending" not in text  # the fleet tail goes first
    warned = [r.message for r in caplog.records if "were cut" in r.message]
    assert len(warned) == 1 and "pipeshub_queue_pending" in warned[0]


def test_under_the_budget_nothing_is_cut_and_nothing_is_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _snapshot(tmp_path, 1, _oom(1))
    with caplog.at_level("WARNING"):
        assert _text(_collector(tmp_path))
    assert not [r for r in caplog.records if "were cut" in r.message]


def test_series_are_served_in_the_order_of_the_table(tmp_path: Path) -> None:
    _snapshot(tmp_path, 1, {**_oom(1), "pipeshub_governor_pool_in_use": [[["indexing", "heavy_parse"], 1]]})
    names = [ln.split(" ")[2] for ln in _text(_collector(tmp_path)).splitlines() if ln.startswith("# TYPE")]
    assert names == [n for n in ox.SERIES if n in {"pipeshub_governor_pool_in_use", "pipeshub_cgroup_oom_kills_total"}]


def test_help_texts_tell_pressure_ratio_from_working_set_ratio() -> None:
    """The admission view stands in for the governor's figure until reserved bytes are exposed; the working
    set is what the kernel sees. The two series must not read as one."""
    pressure = ox.SERIES["pipeshub_cgroup_memory_pressure_ratio"].help
    working_set = ox.SERIES["pipeshub_cgroup_memory_working_set_ratio"].help
    assert pressure != working_set
    assert "stands in" in pressure and "reserved bytes" in pressure
    assert "kernel" in working_set


# ---------------------------------------------------------------------------
# The scrape cache, temp files, retire
# ---------------------------------------------------------------------------


def test_a_scrape_inside_the_ttl_does_not_read_the_files_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for pid in range(1, 9):
        _snapshot(tmp_path, pid, _brakes(1))
    collector = ox.SignalCollector(tmp_path, clock=lambda: 1000.0, is_alive=lambda *_: True, cache_ttl=60.0)
    scans = []
    original = collector._scan_locked
    monkeypatch.setattr(collector, "_scan_locked", lambda: scans.append(1) or original())
    first = _text(collector)
    for _ in range(20):
        assert _text(collector) == first
    assert len(scans) == 1
    collector.gc()  # a fold or a delete invalidates the cache
    _text(collector)
    assert len(scans) == 3


def test_a_zero_ttl_reads_every_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    collector = ox.SignalCollector(tmp_path, clock=lambda: 1000.0, is_alive=lambda *_: True, cache_ttl=0)
    scans = []
    original = collector._scan_locked
    monkeypatch.setattr(collector, "_scan_locked", lambda: scans.append(1) or original())
    _text(collector)
    _text(collector)
    assert len(scans) == 2


def _dead_pid() -> int:
    import subprocess
    import sys

    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_a_temp_file_of_a_dead_writer_is_collected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sig = tmp_path / "signals"
    sig.mkdir()
    dead = _dead_pid()
    monkeypatch.setattr(ox, "_pid_gone", lambda pid: pid == dead)  # the answer must not depend on /proc
    leftovers = [sig / f".{dead}-1.json.{dead}.tmp", tmp_path / f".counters-carry.json.{dead}.tmp"]
    for leftover in leftovers:
        leftover.write_text('{"pid": 1')
    ox.SignalCollector(tmp_path).gc()
    assert not any(p.exists() for p in leftovers)


def test_a_temp_file_of_a_live_writer_is_kept_until_it_is_old(tmp_path: Path) -> None:
    sig = tmp_path / "signals"
    sig.mkdir()
    mine = sig / f".x.json.{os.getpid()}.tmp"
    mine.write_text("{")
    ox.SignalCollector(tmp_path).gc()
    assert mine.exists()  # a writer in the middle of its rename
    old = time.time() - ox.TMP_MAX_AGE_S - 5
    os.utime(mine, (old, old))
    ox.SignalCollector(tmp_path).gc()
    assert not mine.exists()


def test_stop_folds_this_processes_counters_and_removes_its_file(tmp_path: Path) -> None:
    exporter = ox.OpsExporter(service="indexing", run_dir=tmp_path, port=free_port(), source=lambda: ({}, _brakes(7)))
    assert exporter.writer.write() and exporter.writer.path.exists()
    exporter.stop()
    assert not exporter.writer.path.exists()
    carry = json.loads((tmp_path / ox.CARRY_FILE).read_text())
    assert list(carry["values"].values()) == [7]
    # a process that stays up reads the same total, once
    _snapshot(tmp_path, 4242, _brakes(3))
    assert _scrape(tmp_path, {4242})[BRAKES] == 10


def test_stop_does_not_make_a_live_neighbours_counter_fall(tmp_path: Path) -> None:
    """One process leaves while a holder keeps scraping: the total never dips and never double counts."""
    _snapshot(tmp_path, 4242, _brakes(3))
    exporter = ox.OpsExporter(service="indexing", run_dir=tmp_path, port=free_port(), source=lambda: ({}, _brakes(7)))
    exporter.writer.write()
    collector = ox.SignalCollector(tmp_path, is_alive=lambda pid, start: pid == 4242 or start == exporter.writer._start, cache_ttl=0)
    before = _value(_text(collector), BRAKES)
    exporter.stop()
    after = _value(_text(collector), BRAKES)
    assert before == after == 10
