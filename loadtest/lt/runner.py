"""Run orchestration.

One run = warmup iterations (discarded) + `repeat` measured iterations +
optionally one profiled iteration. Each iteration is:

    seed corpus -> create connectors -> start collectors -> trigger sync
    -> poll to completion -> stop collectors -> tear connectors down

Seeding happens once per run, not per iteration: the corpus is deterministic,
so re-uploading it between iterations would only add wall-clock and a fresh
chance for the object store to behave differently.
"""

from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import metrics, profiler
from .client import Client
from .collect import (
    ContainerStatsCollector,
    ProbeCollector,
    SampleCollector,
    ThroughputCollector,
)
from .scenario import Scenario
from .sources import LoadSource, Unit
from .target import Target


class RunError(RuntimeError):
    pass


@dataclass
class Iteration:
    index: int
    kind: str  # warmup | measured | profiled
    directory: Path


def _log(message: str) -> None:
    print(f"[loadtest] {message}", flush=True)


class Runner:
    def __init__(
        self,
        scenario: Scenario,
        source: LoadSource,
        client: Client,
        target: Target,
        out_root: Path,
        label: str | None = None,
    ) -> None:
        self.scenario = scenario
        self.source = source
        self.client = client
        self.target = target
        self.run_id = label or f"{scenario.name}-{time.strftime('%Y%m%d-%H%M%S')}"
        self.dir = out_root / self.run_id
        self.units: list[Unit] = []

    # -- top level ---------------------------------------------------------

    def run(self) -> dict[str, Any]:
        self.dir.mkdir(parents=True, exist_ok=True)
        _log(f"run {self.run_id} -> {self.dir}")

        problems = self.source.preflight() + self.target.preflight()
        if problems:
            raise RunError("preflight failed:\n  - " + "\n  - ".join(problems))

        self._purge_leftover_connectors()

        _log(f"seeding {self.scenario.units} unit(s) x {self.scenario.scale} records")
        seed_started = time.monotonic()
        self.units = self.source.seed(self.scenario.units, self.scenario.scale, self.scenario.seed)
        _log(
            f"seeded in {time.monotonic() - seed_started:.1f}s "
            f"({self.expected_total} records expected: "
            f"{[u.expected_records for u in self.units]})"
        )

        iterations: list[dict[str, Any]] = []
        profiles: list[dict[str, Any]] = []
        profiled_iteration: dict[str, Any] | None = None
        try:
            for i in range(self.scenario.warmup):
                _log(f"warmup {i + 1}/{self.scenario.warmup} (discarded)")
                self._iteration(Iteration(i, "warmup", self.dir / f"warmup-{i}"), profile=False)

            for i in range(self.scenario.repeat):
                _log(f"measured {i + 1}/{self.scenario.repeat}")
                result = self._iteration(
                    Iteration(i, "measured", self.dir / f"iter-{i}"), profile=False
                )
                iterations.append(result)
                _log(
                    f"  -> {result['throughput']['records_per_s']:.2f} rec/s, "
                    f"api p99 {result['latency']['through']['p99']:.0f}ms, "
                    f"conf avg {result['latency']['confluence']['mean']:.1f}ms, "
                    f"neo4j avg {result['latency']['neo4j']['mean']:.1f}ms, "
                    f"max gap {result['latency']['through']['max_gap_s']:.1f}s"
                )

            if self.scenario.profile:
                _log("profiled iteration (excluded from the measured numbers)")
                # Never let the optional profiling pass destroy a completed run:
                # the measured iterations are already done at this point, and
                # losing them to a profiler hiccup is the worst trade available.
                try:
                    result = self._iteration(
                        Iteration(0, "profiled", self.dir / "profiled"), profile=True
                    )
                    profiles = result.get("profiles", [])
                    # Keep the whole profiled iteration, not just its flame
                    # graphs: its CPU/RSS series is the only resource picture a
                    # profile-only run produces.
                    profiled_iteration = result
                except Exception as exc:
                    _log(f"  profiled iteration failed ({exc}); measured results kept")
        finally:
            self._teardown_units()

        summary = metrics.aggregate(iterations) if iterations else {}
        document = {
            "run_id": self.run_id,
            "created_at": time.time(),
            "scenario": self.scenario.as_dict(),
            "source": self.source.describe(),
            "target": {"base_url": self.client.base_url, "container": self.target.container},
            "expected_total": self.expected_total,
            "iterations": iterations,
            "summary": summary,
            "profiles": profiles,
            "profiled_iteration": profiled_iteration,
        }
        (self.dir / "result.json").write_text(json.dumps(document, indent=2), encoding="utf-8")
        return document

    # -- one iteration ------------------------------------------------------

    def _iteration(self, iteration: Iteration, *, profile: bool) -> dict[str, Any]:
        iteration.directory.mkdir(parents=True, exist_ok=True)
        connector_ids = self._create_connectors(iteration)

        expected_total = self.expected_total
        # Generous ceiling: the sampler must outlive the sync, and it stops the
        # moment we ask it to.
        agent_duration = self.scenario.sync_timeout + 120

        probe = ProbeCollector(
            iteration.directory / "probe.jsonl",
            self.scenario.probe_rate,
            self.client.base_url,
            self.client.auth_headers,
        )
        throughput = ThroughputCollector(
            iteration.directory / "throughput.jsonl",
            self.scenario.poll_interval,
            self.client.stats,
            connector_ids,
            status_fn=self.client.status,
        )
        sampler = SampleCollector(
            iteration.directory / "samples.jsonl",
            self.target,
            self.scenario.sample_interval,
            agent_duration,
            token=f"{self.run_id}-{iteration.kind}-{iteration.index}",
        )
        containers = ContainerStatsCollector(
            iteration.directory / "containers.jsonl",
            self.target,
            self.target.stateful_containers(),
        )

        collectors = [probe, throughput, sampler, containers]
        for c in collectors:
            c.start()
        # Drop leftover timings from the previous iteration / worker warmup.
        self.target.reset_backend_timing()
        # Let the samplers establish a baseline delta before the sync starts;
        # the first CPU sample of any /proc-based sampler is meaningless.
        time.sleep(3)

        started_at = time.time()
        profile_results: list[dict[str, Any]] = []
        try:
            trigger_times = self._trigger_syncs(connector_ids)
            spread = max(trigger_times.values()) - min(trigger_times.values())
            _log(
                f"  sync triggered for {len(connector_ids)} connector(s) "
                f"(spread {spread:.2f}s)"
            )

            if profile:
                profile_results = self._profile()

            self._await_completion(throughput, expected_total)
        finally:
            for c in collectors:
                c.stop()
            for c in collectors:
                c.join(timeout=20)
            self.target.collect_backend_timing(iteration.directory / "backend.jsonl")
            self._delete_connectors(connector_ids)

        return self._summarise(
            iteration, started_at, expected_total, connector_ids, profile_results, trigger_times
        )

    # -- steps -------------------------------------------------------------

    def _create_connectors(self, iteration: Iteration) -> list[str]:
        ids: list[str] = []
        for unit in self.units:
            name = f"lt-{self.scenario.source}-{unit.index}-{uuid.uuid4().hex[:8]}"
            connector_id = self.client.create(
                self.source.connector_type,
                name,
                unit.auth,
                self.source.scope,
                self.source.auth_type,
            )
            self.client.set_filters(connector_id, unit.filters_payload(), unit.sync_config)
            self._assert_filters_applied(connector_id)
            ids.append(connector_id)
        (iteration.directory / "connectors.json").write_text(
            json.dumps(
                [{"connector_id": cid, "unit": u.label} for cid, u in zip(ids, self.units)],
                indent=2,
            ),
            encoding="utf-8",
        )
        return ids

    def _assert_filters_applied(self, connector_id: str) -> None:
        """Fail at setup, not at the report, if the manual-index filter vanished.

        Costs one GET per connector and turns the harness's worst failure mode —
        a plausible-looking run that silently measured connector *and* indexing —
        into an error before any measurement happens.
        """
        try:
            stored = self.client.stored_filters(connector_id)
        except Exception as exc:
            raise RunError(f"could not read back filters for {connector_id}: {exc}") from exc

        indexing = (stored.get("indexing") or {}).get("values") or {}
        if "enable_manual_sync" not in indexing:
            raise RunError(
                f"enable_manual_sync was not stored for connector {connector_id} — "
                f"the run would measure indexing too. Stored filters: {stored}"
            )

    @property
    def expected_total(self) -> int:
        """Records the seeded units actually promise.

        Not `units * scale`: a live source such as Confluence syncs whatever
        the space holds, so `scale` is meaningless there."""
        return sum(u.expected_records for u in self.units)

    def _trigger_syncs(self, connector_ids: list[str]) -> dict[str, float]:
        """Start every connector's sync as close to simultaneously as possible.

        A sequential loop staggered the starts by ~0.55s each — 16s across 30
        connectors, which measurably lowers peak concurrency on exactly the
        runs where concurrency is the thing being measured. Issuing the toggles
        from a thread pool collapses that to well under a second.

        With `chaos` on, every trigger is fired twice a short gap apart. That
        is a deliberate attempt to make one connector sync twice at once — the
        exact condition that produced duplicate records at N=120. Exclusion is
        only proven if the record counts come out identical anyway; without
        this, a passing run might just mean the race never happened to fire.

        Returns {connector_id: wall-clock time the first trigger returned}.
        """
        times: dict[str, float] = {}

        def _start(connector_id: str) -> None:
            self.client.start_sync(connector_id)
            times[connector_id] = time.time()
            if not self.scenario.chaos:
                return
            time.sleep(self.scenario.chaos_gap_s)
            try:
                self.client.start_sync(connector_id)
            except Exception as e:  # noqa: BLE001 - a refused duplicate is a pass
                _log(f"chaos: second trigger for {connector_id} refused ({e})")

        with ThreadPoolExecutor(max_workers=min(len(connector_ids), 32)) as pool:
            list(pool.map(_start, connector_ids))
        return times

    def _await_completion(self, throughput: ThroughputCollector, expected_total: int) -> None:
        """Wait on the throughput collector's own polling rather than opening a
        second poll loop. /stats runs a full graph aggregation per call, and
        that cost lands on the service under test — one poller keeps it a fixed
        offset across every run instead of a variable the harness adds.

        Finishes when nothing is SYNCING any more — never on record counts
        alone.

        Counting was the original signal and it was wrong: a connector delivers
        its records slightly before its sync task finishes, so returning on the
        count deletes connectors out from under running syncs. That produced
        1008 "Confluence configuration not found" errors and Neo4j
        EntityNotFound rollbacks in one 30-minute window, and it made record
        counts differ between iterations of the same run (modal 245 vs 172),
        which the divergence check then flagged as duplicated work. Neither was
        real; both were the harness cutting the thing it was measuring.

        The count still matters as a liveness fallback: a sync so short that
        polling never catches it SYNCING would otherwise leave `seen_active`
        incomplete and spin until sync_timeout.
        """
        deadline = time.time() + self.scenario.sync_timeout
        quiet_polls = 0
        targets = {
            cid: unit.expected_records
            for cid, unit in zip(throughput.connector_ids, self.units)
        }
        while time.time() < deadline:
            time.sleep(self.scenario.poll_interval)
            counts = dict(throughput.latest)
            if not counts:
                continue

            still_running = [
                cid for cid in counts
                if (throughput.latest_status.get(cid, "") or "").upper()
                # QUEUED too: the connector is past the concurrency limit with a
                # sync still owed, so ending here measures a run that has not
                # finished and reports the queued connectors as 0 records.
                in ("SYNCING", "FULL_SYNCING", "QUEUED")
            ]
            if still_running:
                quiet_polls = 0
                continue  # a sync is still finalising; deleting now would cut it

            if not throughput.seen_active:
                continue  # triggers have not landed yet; everything is idle by default

            # Two consecutive quiet polls, not one: a single quiet poll can land
            # in the gap between the trigger returning and the status flipping to
            # SYNCING, which would end the run before it started.
            quiet_polls += 1
            if quiet_polls < 2:
                continue

            # Deliberately not "every connector reached its target" or "every
            # connector was seen SYNCING". A sync that dies early — two did, with
            # httpcore.ReadError against Confluence — reaches neither, and
            # requiring them turned a premature exit into a 3600s hang with all
            # 50 connectors already IDLE and no records moving.
            short = {
                cid: (v, targets.get(cid, 0))
                for cid, v in counts.items()
                if v < targets.get(cid, 0)
            }
            if short:
                _log(
                    f"  all syncs finished ({sum(counts.values())} records); "
                    f"{len(short)} connector(s) ended below target: "
                    + ", ".join(f"{c[:8]}={got}/{want}" for c, (got, want) in list(short.items())[:5])
                )
            else:
                _log(f"  all syncs finished ({sum(counts.values())} records, none short)")
            return
        _log(
            f"  TIMEOUT after {self.scenario.sync_timeout}s "
            f"(reached {sum(throughput.latest.values())}/{expected_total})"
        )

    def _profile(self) -> list[dict[str, Any]]:
        binary = profiler.ensure_py_spy(self.target)
        if not binary:
            _log("  py-spy unavailable inside the container; skipping flame graphs")
            return []
        try:
            pids = self.target.discover_processes()
        except Exception as exc:
            _log(f"  process discovery failed: {exc}")
            return []

        wanted = set(self.scenario.profile_services)
        by_service: dict[str, list[int]] = {}
        for pid, service in pids.items():
            if service in wanted:
                by_service.setdefault(service, []).append(pid)

        # Uvicorn --workers: parent is idle; children (spawn_main) own the sync
        # work and HTTP is round-robined across them. Profile every worker, not
        # one arbitrary PID — otherwise most syncs never appear in the flame.
        selected: list[tuple[int, str]] = []
        for service, plist in by_service.items():
            workers = [
                pid for pid in sorted(plist) if self._is_worker_child(pid)
            ]
            picks = workers or plist
            if workers and len(plist) > len(workers):
                _log(
                    f"  {service}: profiling {len(workers)} worker pid(s), "
                    f"skipping uvicorn parent"
                )
            for pid in picks:
                selected.append((pid, service))

        # Connector first, and all its workers in parallel, so the capture
        # window overlaps the sync. Indexing used to run first and burned
        # ~60–120s (speedscope + native SVG) before py-spy ever attached to a
        # connector worker — short syncs finished as API-poll noise only.
        # Longer window for big N: py-spy native SVG runs first inside record().
        duration = min(90, self.scenario.sync_timeout)
        out_dir = self.dir / "profiles"
        connector_jobs = [(pid, svc) for pid, svc in selected if svc == "connector"]
        other_jobs = [(pid, svc) for pid, svc in selected if svc != "connector"]

        results: list[dict[str, Any]] = []
        if connector_jobs:
            _log(
                f"  profiling {len(connector_jobs)} connector worker(s) "
                f"in parallel for {duration}s (speedscope + py-spy flamegraph)"
            )
            results.extend(
                self._profile_parallel(
                    connector_jobs, binary, duration, out_dir, native_flamegraph=True
                )
            )
        for pid, service in other_jobs:
            _log(f"  profiling {service} (pid {pid})")
            results.append(
                self._profile_one(
                    service, pid, binary, duration, out_dir, native_flamegraph=False
                )
            )
        profiler.kill_leftovers(self.target)
        return results

    def _is_worker_child(self, pid: int) -> bool:
        try:
            cmd = self.target.exec(
                ["bash", "-lc", f"tr '\\0' ' ' < /proc/{pid}/cmdline"],
                check=False,
            )
        except Exception:
            return False
        return "spawn_main" in cmd or "fork_main" in cmd

    def _profile_one(
        self,
        service: str,
        pid: int,
        binary: str,
        duration: int,
        out_dir: Path,
        *,
        native_flamegraph: bool = False,
    ) -> dict[str, Any]:
        try:
            result = profiler.record(
                self.target,
                service,
                pid,
                duration=duration,
                rate=self.scenario.profile_rate,
                out_dir=out_dir,
                binary=binary,
                native_flamegraph=native_flamegraph,
            )
        except Exception as exc:
            _log(f"  profiling {service} pid {pid} failed: {exc}")
            return {
                "service": service,
                "pid": pid,
                "flamegraph": None,
                "speedscope": None,
                "blocking": [],
                "error": str(exc),
            }
        row = {
            "service": result.service,
            "pid": result.pid,
            "flamegraph": (
                str(result.flamegraph.relative_to(self.dir)) if result.flamegraph else None
            ),
            "pyspy_flamegraph": (
                str(result.pyspy_flamegraph.relative_to(self.dir))
                if result.pyspy_flamegraph
                else None
            ),
            "speedscope": (
                str(result.speedscope.relative_to(self.dir)) if result.speedscope else None
            ),
            "blocking": result.blocking or [],
            "modules": result.modules or {},
            "hot_frames": result.hot_frames or [],
            "busy_s": result.busy_s,
            "error": result.error,
        }
        if result.modules:
            top = ", ".join(f"{m} {p:.0f}%" for m, p in list(result.modules.items())[:4])
            _log(f"    {service} pid {pid}: busy {result.busy_s:.0f}s | {top}")
        if result.pyspy_flamegraph:
            _log(f"    {service} pid {pid}: py-spy flamegraph ok")
        return row

    def _profile_parallel(
        self,
        jobs: list[tuple[int, str]],
        binary: str,
        duration: int,
        out_dir: Path,
        *,
        native_flamegraph: bool = False,
    ) -> list[dict[str, Any]]:
        # One docker-exec py-spy per worker, overlapped so every worker sees
        # the same slice of the sync.
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futs = {
                pool.submit(
                    self._profile_one,
                    service,
                    pid,
                    binary,
                    duration,
                    out_dir,
                    native_flamegraph=native_flamegraph,
                ): (pid, service)
                for pid, service in jobs
            }
            out: list[dict[str, Any]] = []
            for fut in futs:
                out.append(fut.result())
            return out

    def _purge_leftover_connectors(self) -> None:
        """Delete harness connectors left by a run that did not finish.

        `_delete_connectors` only runs on the happy path, so a killed or failed
        run leaves its connectors behind. They are not inert: every service
        restart resyncs all of them, and once enough accumulate the API spends
        so long serving that flood that creating a connector times out — runs
        then fail for reasons that have nothing to do with what they measure.
        506 of them had piled up before this was noticed, and every number taken
        alongside them was worthless.

        The `lt-` prefix is harness-owned by construction (`_create_connectors`
        names them `lt-<source>-<index>-<hex8>`), so this can never touch a
        human's connector. Concurrent runs against one target are not supported,
        so anything already present is by definition stale.
        """
        try:
            leftovers = [
                c for c in self.client.list_instances()
                if str(c.get("name") or c.get("instanceName") or "").startswith("lt-")
            ]
        except Exception as exc:  # noqa: BLE001 - never block a run on cleanup
            _log(f"warning: could not list connectors to purge leftovers: {exc}")
            return

        if not leftovers:
            return

        _log(f"purging {len(leftovers)} leftover harness connector(s) from an earlier run")
        purged = 0
        for connector in leftovers:
            cid = connector.get("_key") or connector.get("id") or connector.get("_id")
            if not cid:
                continue
            try:
                try:
                    self.client.stop_sync(cid)
                except Exception:
                    pass
                self.client.delete(cid)
                purged += 1
            except Exception as exc:  # noqa: BLE001
                _log(f"  warning: could not delete leftover {cid}: {exc}")
        _log(f"  purged {purged}/{len(leftovers)}")
        # Same reason _delete_connectors waits: deletion cascades through the
        # graph asynchronously, and seeding mid-cascade charges its cost here.
        time.sleep(20)

    def _delete_connectors(self, connector_ids: list[str]) -> None:
        if self.scenario.reset == "none":
            return
        for connector_id in connector_ids:
            try:
                self.client.stop_sync(connector_id)
            except Exception:
                pass
            try:
                self.client.delete(connector_id)
            except Exception as exc:
                _log(f"  warning: failed to delete connector {connector_id}: {exc}")
        # Deletion cascades through the graph asynchronously; starting the next
        # iteration mid-cascade would charge its cost to that iteration.
        time.sleep(20)

    def _teardown_units(self) -> None:
        try:
            self.source.teardown(self.units)
        except Exception as exc:
            _log(f"warning: source teardown failed: {exc}")

    # -- results -----------------------------------------------------------

    def _summarise(
        self,
        iteration: Iteration,
        started_at: float,
        expected_total: int,
        connector_ids: list[str],
        profile_results: list[dict[str, Any]],
        trigger_times: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        probe_rows = metrics.read_jsonl(iteration.directory / "probe.jsonl")
        tp_rows = metrics.read_jsonl(iteration.directory / "throughput.jsonl")
        sample_rows = metrics.read_jsonl(iteration.directory / "samples.jsonl")
        container_rows = metrics.read_jsonl(iteration.directory / "containers.jsonl")
        backend_rows = metrics.read_jsonl(iteration.directory / "backend.jsonl")

        throughput = metrics.throughput_stats(tp_rows, started_at, expected_total)
        resources = metrics.resource_stats(sample_rows, started_at)
        containers = metrics.container_stats(container_rows, started_at)
        # Upstream throttling flattens the CPU curve, which is indistinguishable
        # from saturation unless you look for it.
        window = max(int(time.time() - started_at) + 10, 1)
        # A bare "429" matches content IDs and request IDs (Confluence has a
        # page 429195277), so the number must be anchored to status-like
        # context or the signal is pure noise.
        throttled = self.target.count_log_matches(
            r"429 too many requests"
            r"|too many requests"
            r"|\brate.?limit(?:ed|ing|s)?\b"
            r"|retry-after"
            r"|status(?:_code)?[\"'=: ]{1,4}429\b"
            r"|HTTP[/ ][\d.]* ?429\b",
            window,
        )
        if throttled > 0:
            _log(f"  WARNING: {throttled} rate-limit signals in the source's responses")
        return {
            "rate_limit_signals": throttled,
            "kind": iteration.kind,
            "index": iteration.index,
            "directory": iteration.directory.name,
            "connector_ids": connector_ids,
            "started_at": started_at,
            "connectors": metrics.connector_timeline(
                tp_rows, started_at, trigger_times or {}
            ),
            "throughput": throughput.as_dict(),
            "latency": {
                "through": metrics.latency_stats(probe_rows, "through").as_dict(),
                "control": metrics.latency_stats(probe_rows, "control").as_dict(),
                "confluence": metrics.call_latency_stats(backend_rows, "confluence").as_dict(),
                "neo4j": metrics.call_latency_stats(backend_rows, "neo4j").as_dict(),
            },
            "resources": resources.as_dict(),
            "containers": containers.as_dict(),
            "profiles": profile_results,
        }
