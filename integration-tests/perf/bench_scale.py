"""Scale run: index a large customer's worth of documents and watch for drift.

The weekly indexing benchmark answers "how fast is it right now" with a few
hundred files. This answers a different question: does the stack still behave
when the corpus is two orders of magnitude bigger? A run that starts at 90
records a minute and ends at 20, or whose memory climbs all run, passes every
average and is still a problem, so the result carries the shape of the run —
throughput and time-to-indexed per slice, memory start to end — next to the
totals.

Files are generated and uploaded a batch at a time and their bytes dropped once
uploaded, so 100,000 documents cost the same memory here as 500.

Needs the same environment as ``bench_indexing.py``: ``PIPESHUB_BASE_URL`` and
either ``CLIENT_ID``/``CLIENT_SECRET`` or an org admin's email and password,
plus the ``TEST_*`` provider keys when ``--ai-models seed`` (the default).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import platform
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PERF_DIR = Path(__file__).resolve().parent
_IT_DIR = _PERF_DIR.parent
for _p in (_PERF_DIR, _IT_DIR / "helper", _IT_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from corpus import Corpus, CorpusPlan, iter_corpus_batches, plan_corpus  # noqa: E402
from scale_metrics import (  # noqa: E402
    Sample,
    drift,
    latency_windows,
    rounded,
    throughput_windows,
)

SCHEMA_VERSION = 1


def _plumbing() -> Any:
    """The shared benchmark plumbing, wherever it lives.

    PR #3398 moves it out of ``bench_indexing`` into ``stack``; this works
    before and after that lands.
    """
    try:
        import stack

        return stack
    except ModuleNotFoundError:
        import bench_indexing as legacy

        return _Legacy(legacy)


class _Legacy:
    """``bench_indexing``'s private helpers under the names ``stack`` gives them."""

    _ALIASES = {
        "run_command": "_run",
        "load_env": "_load_env",
        "ensure_client_credentials": "_ensure_client_credentials",
        "describe_org_models": "_describe_org_models",
        "create_kb": "_create_kb",
        "create_folders": "_create_folders",
        "upload_file": "_upload",
        "poll_records_once": "_poll_once",
        "host_memory_gb": "_host_memory_gb",
    }

    def __init__(self, module: Any) -> None:
        self._module = module

    def __getattr__(self, name: str) -> Any:
        return getattr(self._module, self._ALIASES.get(name, name))


def _memory_now(container: str | None, plumbing: Any) -> tuple[float | None, float | None]:
    """Container memory and the indexing process's memory, in MB. Best effort."""
    # These two parsers stay in bench_indexing either side of PR #3398.
    from bench_indexing import indexing_rss_bytes, parse_docker_mem

    if not container:
        return None, None
    stats = plumbing.run_command(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", container])
    used = parse_docker_mem(stats) if stats else None
    # docker top runs the host's ps, so this does not need procps in the image.
    ps = plumbing.run_command(["docker", "top", container, "-eo", "pid,ppid,rss,args"])
    rss = indexing_rss_bytes(ps) if ps else None
    return (used / 1e6 if used else None), (rss / 1e6 if rss else None)


def _shed_content(state: Any) -> None:
    """Forget the bytes of files already uploaded.

    The run state keeps a file per record so failures can be named. Holding the
    content too would make memory grow with the corpus, which is the one thing
    a scale run must not do.
    """
    with state.lock:
        for record_id, f in list(state.file_of.items()):
            if getattr(f, "content", b""):
                state.file_of[record_id] = dataclasses.replace(f, content=b"")


class _Uploader(threading.Thread):
    """Generates and uploads the corpus batch by batch, off the polling thread."""

    def __init__(self, args: argparse.Namespace, kb_client: Any, kb_id: str,
                 plan: CorpusPlan, folder_ids: dict, state: Any, plumbing: Any, salt: str) -> None:
        super().__init__(daemon=True)
        self.args = args
        self.kb_client = kb_client
        self.kb_id = kb_id
        self.plan = plan
        self.folder_ids = folder_ids
        self.state = state
        self.plumbing = plumbing
        self.salt = salt
        self.done = threading.Event()
        self.generated_bytes = 0
        self.batches = 0
        self.error: str = ""
        self.halted = False
        self.submitted = 0
        self._halt = threading.Event()
        self._counter = threading.Lock()

    def run(self) -> None:
        try:
            batches = iter_corpus_batches(
                len(self.plan.entries), self.args.batch_size,
                seed=self.plan.seed, salt=self.salt, plan=self.plan,
            )
            with ThreadPoolExecutor(max_workers=self.args.upload_workers) as pool:
                for batch in batches:
                    if self._halt.is_set():
                        self.halted = True
                        break
                    self.generated_bytes += batch.total_bytes
                    self.batches += 1
                    list(pool.map(self._upload_one, batch.files))
                    _shed_content(self.state)
        except Exception as exc:  # noqa: BLE001 - the run reports it rather than dying silently
            self.error = str(exc)[:300]
        finally:
            self.done.set()

    def _upload_one(self, f: Any) -> None:
        # Checked per file as well as per batch, so stopping does not have to
        # wait out a whole batch of uploads.
        if self._halt.is_set():
            self.halted = True
            return
        self.plumbing.upload_file(self.kb_client, self.kb_id, self.folder_ids.get(f.folder), f, self.state)
        # Counted whether the upload was accepted or refused: what matters here
        # is that the file was offered at all.
        with self._counter:
            self.submitted += 1

    def stop(self, timeout: float = 120.0) -> bool:
        """Ask it to stop uploading and wait for it. True if it actually stopped."""
        self._halt.set()
        if self.is_alive():
            self.join(timeout)
        return not self.is_alive()


def upload_shortfall(planned: int, submitted: int, error: str = "", halted: bool = False) -> str:
    """Why this run did not cover the corpus it was asked for, in plain words.

    Reaching the end of the wait loop only means nothing is still indexing. If
    uploading stopped part way — it raised, or it was cut short — the records
    that did finish are a prefix of the run, and calling that a complete result
    would be the same green tick a timeout used to give.
    """
    if error:
        return (
            f"the corpus was only partly uploaded: {submitted} of {planned} files were sent "
            f"before uploading stopped with an error ({error})"
        )
    if submitted < planned:
        why = "after being asked to stop" if halted else "before the end"
        return (
            f"the corpus was only partly uploaded: {submitted} of {planned} files were sent, "
            f"then uploading stopped {why}"
        )
    return ""


def note_incomplete(state: Any, reason: str) -> None:
    """Record a reason the run was incomplete, keeping any already there."""
    if not reason:
        return
    state.stopped_early = f"{state.stopped_early}; {reason}" if state.stopped_early else reason


def finish_run(uploader: Any, kb_client: Any, kb_id: str | None, keep_kb: bool,
               stop_timeout: float = 120.0) -> list[str]:
    """Stop uploading, then delete the knowledge base — in that order.

    A run that hits its time limit leaves the uploader mid-corpus. Deleting the
    knowledge base first would have it uploading into a knowledge base that no
    longer exists, and the result would be read while it was still changing.
    """
    warnings: list[str] = []
    if uploader is not None and not uploader.stop(stop_timeout):
        warnings.append(
            f"the uploader was still working {stop_timeout:.0f}s after being asked to stop; "
            "the numbers below cover what it had finished by then"
        )
    if kb_id and not keep_kb:
        try:
            kb_client.delete_kb(kb_id)
        except Exception as exc:  # noqa: BLE001 - cleanup must not hide the result
            warnings.append(f"could not delete the benchmark knowledge base {kb_id}: {exc}")
    return warnings


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    plumbing = _plumbing()
    from ai_models_setup import setup_test_indexing_models, teardown_test_indexing_models
    from helper.clients.kb_client import KBClient
    from pipeshub_client import PipeshubClient

    base_url = (args.base_url or os.getenv("PIPESHUB_BASE_URL", "")).rstrip("/")
    if not base_url:
        raise SystemExit("Set PIPESHUB_BASE_URL or pass --base-url")
    os.environ["PIPESHUB_BASE_URL"] = base_url
    plumbing.ensure_client_credentials(base_url)

    run_id = uuid.uuid4().hex[:8]
    salt = f"run {run_id}"
    kinds = tuple(args.kinds.split(",")) if args.kinds else None
    plan = plan_corpus(args.docs, args.seed, kinds)
    print(f"Planned {len(plan.entries)} files in {len(plan.folders)} folders; "
          f"generating and uploading {args.batch_size} at a time", flush=True)

    client = PipeshubClient(base_url=base_url, timeout_seconds=args.request_timeout)
    kb_client = KBClient(client)
    models = None
    org_models: dict[str, str] = {}
    state = plumbing.RunState()
    samples: list[Sample] = []
    kb_id: str | None = None
    started_at = datetime.now(timezone.utc)
    uploader: _Uploader | None = None
    t0 = time.perf_counter()
    try:
        # Seeded inside the try so a failure part way through still tears down
        # whatever was already added to the org's AI model config.
        if args.ai_models == "seed":
            models = setup_test_indexing_models(client)
        org_models = plumbing.describe_org_models(client)
        kb_id = plumbing.create_kb(kb_client, f"perf-scale-{run_id}")
        folder_ids = plumbing.create_folders(
            kb_client, kb_id, Corpus(seed=plan.seed, kinds=plan.kinds, folders=plan.folders, files=()))
        t0 = time.perf_counter()  # the clock starts once the KB is ready to receive files
        uploader = _Uploader(args, kb_client, kb_id, plan, folder_ids, state, plumbing, salt)
        uploader.start()
        deadline = t0 + args.timeout
        last_memory = 0.0
        while time.perf_counter() < deadline:
            plumbing.poll_records_once(kb_client, kb_id, state)
            now = time.perf_counter()
            container_mb = indexing_mb = None
            if now - last_memory >= args.memory_interval:
                container_mb, indexing_mb = _memory_now(args.container, plumbing)
                last_memory = now
            with state.lock:
                uploaded, finished = len(state.uploaded_at), len(state.finished_at)
            samples.append(Sample(now - t0, uploaded, finished, container_mb, indexing_mb))
            verdict = plumbing.should_stop_waiting(
                state, uploader.done.is_set(), now, args.not_listed_grace)
            if verdict:
                if verdict != "done":
                    state.stopped_early = verdict
                    print(f"Stopping early: {verdict}", file=sys.stderr, flush=True)
                break
            print(f"  {now - t0:6.0f}s  uploaded {uploaded}/{len(plan.entries)}  finished {finished}"
                  f"  waiting {max(0, uploaded - finished)}", flush=True)
            time.sleep(args.poll_interval)
        else:
            with state.lock:
                still_going = len(state.uploaded_at) - len(state.finished_at)
            state.stopped_early = (
                f"the run hit its {args.timeout:.0f}s limit with "
                f"{still_going} file(s) still indexing"
            )
        state.ended_at = time.perf_counter()
    finally:
        for warning in finish_run(uploader, kb_client, kb_id, args.keep_kb, args.upload_stop_timeout):
            print(f"warning: {warning}", file=sys.stderr)
        if models is not None:
            teardown_test_indexing_models(client, models)

    # Only now that uploading has stopped are the counts final.
    if uploader is not None:
        note_incomplete(state, upload_shortfall(
            len(plan.entries), uploader.submitted, uploader.error, uploader.halted))

    return build_result(args, plan, state, t0, samples, org_models, started_at,
                        uploader.generated_bytes if uploader else 0,
                        uploader.error if uploader else "")


def build_result(
    args: argparse.Namespace,
    plan: CorpusPlan,
    state: Any,
    t0: float,
    samples: list[Sample],
    org_models: dict[str, str],
    started_at: datetime,
    generated_bytes: int,
    uploader_error: str,
) -> dict[str, Any]:
    plumbing = _plumbing()
    success_status, percentile = plumbing.SUCCESS_STATUS, plumbing.percentile

    # One consistent picture of the run. A run that ended early can still have
    # an uploader finishing its last batch, and half-read totals would not add
    # up.
    with state.lock:
        uploaded_at = dict(state.uploaded_at)
        finished_at = dict(state.finished_at)
        statuses = dict(state.status)
        upload_failures = list(state.upload_failures)

    completed = [r for r in uploaded_at if statuses.get(r) == success_status]
    finished_events = [
        (finished_at[r] - t0, max(0.0, finished_at[r] - uploaded_at[r]))
        for r in completed if r in finished_at
    ]
    latencies = [taken for _, taken in finished_events]
    not_completed: dict[str, int] = {}
    unfinished = 0
    for record_id in uploaded_at:
        status = statuses.get(record_id, "NOT_LISTED")
        if status == success_status:
            continue
        if record_id in finished_at:
            not_completed[status] = not_completed.get(status, 0) + 1
        else:
            unfinished += 1

    upload_seconds = max(uploaded_at.values(), default=t0) - t0
    last = state.ended_at if unfinished else max(finished_at.values(), default=t0)
    wall = max(last - t0, upload_seconds)
    throughput = throughput_windows(samples, args.windows)
    latency = latency_windows(finished_events, args.windows)
    shape = drift(throughput, latency, samples)

    return {
        "schema_version": SCHEMA_VERSION,
        "benchmark": "scale",
        "label": args.label,
        "started_at": started_at.isoformat(timespec="seconds"),
        "command": " ".join(sys.argv),
        "environment": _environment(args, org_models),
        "corpus": {**plan.describe(), "generated_bytes": generated_bytes},
        "settings": {
            "docs": args.docs,
            "batch_size": args.batch_size,
            "upload_workers": args.upload_workers,
            "poll_interval_seconds": args.poll_interval,
            "timeout_seconds": args.timeout,
        },
        "metrics": {
            "wall_seconds": rounded(wall),
            "upload_seconds": rounded(upload_seconds),
            "records_uploaded": len(uploaded_at),
            "records_completed": len(completed),
            "records_per_minute": rounded(len(completed) / (wall / 60)) if wall > 0 else None,
            "time_to_indexed_seconds": {
                "p50": rounded(percentile(latencies, 50)),
                "p95": rounded(percentile(latencies, 95)),
                "p99": rounded(percentile(latencies, 99)),
                "max": rounded(max(latencies)) if latencies else None,
            },
            "failures": {
                "upload": len(upload_failures),
                "by_status": dict(sorted(not_completed.items())),
                "unfinished": unfinished,
                "total": len(upload_failures) + sum(not_completed.values()) + unfinished,
            },
            "peak_indexing_rss_mb": shape["memory"]["peak_mb"],
            "peak_container_memory_mb": rounded(
                max((s.container_memory_mb for s in samples if s.container_memory_mb), default=None)),
            "status_poll_errors": state.poll_errors,
            "stopped_early": state.stopped_early or None,
            "upload_error": uploader_error or None,
        },
        "trend": {
            "throughput_windows": throughput,
            "latency_windows": latency,
            **shape,
        },
        # The readings the trend above was worked out from, so anyone can check
        # it or plot it without rerunning four hours of indexing. One small row
        # per poll: a four-hour run at the default interval is about 1,400.
        "samples": [
            {
                "elapsed_seconds": rounded(s.elapsed_seconds),
                "uploaded": s.uploaded,
                "finished": s.finished,
                "backlog": s.backlog,
                "container_memory_mb": rounded(s.container_memory_mb),
                "indexing_memory_mb": rounded(s.indexing_memory_mb),
            }
            for s in samples
        ],
        "upload_failures": upload_failures[:50],
    }


def _environment(args: argparse.Namespace, org_models: dict[str, str]) -> dict[str, Any]:
    return {
        "label": args.label,
        "graph_db": args.graph_db,
        "message_broker": os.getenv("MESSAGE_BROKER", "redis"),
        "ai_models": org_models,
        "host_cpus": os.cpu_count(),
        "host_memory_gb": _host_memory_gb(),
        "host_platform": platform.platform(),
        "git_sha": os.getenv("GITHUB_SHA") or _plumbing().run_command(["git", "rev-parse", "HEAD"]) or "",
    }


def _host_memory_gb() -> float | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) / 1024**2, 1)
    except OSError:
        pass
    return None


def render_summary(result: dict[str, Any]) -> str:
    m, c, t = result["metrics"], result["corpus"], result["trend"]
    ttl = m["time_to_indexed_seconds"]
    fails = m["failures"]
    env = result["environment"]

    def show(value: Any, unit: str = "") -> str:
        return "n/a" if value is None else f"{value}{unit}"

    lines = [
        f"### Scale run — {result['label']}",
        "",
        f"{c['docs']} files ({m['records_completed']} indexed) in {show(m['wall_seconds'], ' s')}.",
        "",
        "| Measure | Value |",
        "| --- | --- |",
        f"| Throughput over the whole run | {show(m['records_per_minute'])} records/min |",
        f"| Time to indexed p50 / p95 / p99 | {show(ttl['p50'])} / {show(ttl['p95'])} / {show(ttl['p99'])} s |",
        f"| Failed or unfinished | {fails['total']} of {m['records_uploaded']} uploaded |",
        f"| Indexing memory, start → end | {show(t['memory']['start_mb'], ' MB')} → "
        f"{show(t['memory']['end_mb'], ' MB')} (peak {show(t['memory']['peak_mb'], ' MB')}) |",
        "",
        "**Did it hold up as the corpus grew?**",
        "",
    ]
    if t["steady"]:
        lines.append("Yes: throughput, time-to-indexed and memory all held steady from start to finish.")
    else:
        lines += [f"- {note}" for note in t["notes"]]
    lines += [
        "",
        "| Slice of the run | Records finished | Records/min | Waiting at end | Median time to index |",
        "| --- | --- | --- | --- | --- |",
    ]
    for tp, lat in zip(t["throughput_windows"], t["latency_windows"] or t["throughput_windows"]):
        lines.append(
            f"| {tp['from_seconds']:.0f}–{tp['to_seconds']:.0f}s | {tp['records_finished']} | "
            f"{show(tp['records_per_minute'])} | {tp['backlog_at_end']} | "
            f"{show(lat.get('p50_seconds'), ' s')} |"
        )
    lines += [
        "",
        f"Graph DB {env['graph_db']}, broker {env['message_broker']}, LLM {env['ai_models']['llm']}, "
        f"host {env['host_cpus']} CPUs / {env['host_memory_gb']} GB.",
    ]
    if m.get("stopped_early"):
        lines += ["", f"**Stopped early:** {m['stopped_early']}."]
    if m.get("upload_error"):
        lines += ["", f"**Uploading stopped:** {m['upload_error']}."]
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--docs", type=int, default=5000,
                        help="how many documents to index (the plan targets 100000 on a bigger machine)")
    parser.add_argument("--batch-size", type=int, default=250,
                        help="documents generated and uploaded at once; keeps memory flat")
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--kinds", default=None, help="only these file kinds, comma-separated")
    parser.add_argument("--label", required=True, help="where this ran, e.g. ci-scale-neo4j-4cpu")
    parser.add_argument("--graph-db", default=os.getenv("TEST_GRAPH_DB_TYPE", "neo4j"))
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--ai-models", choices=["seed", "existing"], default="seed")
    parser.add_argument("--container", default=None, help="app container name, for memory over time")
    parser.add_argument("--upload-workers", type=int, default=4)
    parser.add_argument("--poll-interval", type=float, default=10.0)
    parser.add_argument("--memory-interval", type=float, default=30.0)
    parser.add_argument("--windows", type=int, default=4, help="slices the run is cut into for the trend")
    parser.add_argument("--timeout", type=float, default=21600, help="seconds to wait for every record")
    parser.add_argument("--not-listed-grace", type=float, default=600)
    parser.add_argument("--upload-stop-timeout", type=float, default=120,
                        help="how long to wait for uploading to stop when the run ends early")
    parser.add_argument("--request-timeout", type=int, default=180)
    parser.add_argument("--keep-kb", action="store_true")
    parser.add_argument("--output", type=Path, default=_IT_DIR / "reports" / "perf" / "scale.json")
    parser.add_argument("--summary", type=Path, default=None)
    parser.add_argument("--fail-if-incomplete", action="store_true",
                        help="exit non-zero if the run ended before every file finished")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    _plumbing().load_env()
    result = run_benchmark(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = render_summary(result)
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(summary, encoding="utf-8")
    print(summary)
    print(f"Result written to {args.output}")
    # A run that ran out of time measured part of a corpus, and its numbers are
    # not the ones the baseline holds. Saying so out loud beats a green tick.
    incomplete = result["metrics"].get("stopped_early")
    if incomplete and args.fail_if_incomplete:
        print(f"This run did not finish: {incomplete}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
