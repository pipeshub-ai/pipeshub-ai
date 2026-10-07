"""Stress run: push harder than the stack can keep up with, then check nothing was lost.

This is not a speed measurement. Files are uploaded far faster than they can be
indexed so that work piles up, and the questions afterwards are: was every
upload either accepted or refused with an error; did every accepted file appear
in the knowledge base; did every one of them finish, one way or another; and did
the backlog clear once the load stopped.

Refusing an upload under load is a fine answer — the caller is told and can
retry. Accepting one and quietly losing it is not, and that is what this run is
built to catch.

Needs the same environment as ``bench_indexing.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
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

from bench_scale import _memory_now, _plumbing, _shed_content  # noqa: E402
from corpus import Corpus, iter_corpus_batches, plan_corpus  # noqa: E402
from scale_metrics import (  # noqa: E402
    Sample,
    Verdict,
    overload_verdicts,
    render_verdicts,
    rounded,
    throughput_windows,
)

SCHEMA_VERSION = 1
# Upload errors that mean the stack asked us to slow down rather than broke.
BACKPRESSURE_MARKERS = ("429", "503", "too many requests", "service unavailable", "rate limit")


def counted_rejections(upload_failures: list[dict[str, str]]) -> int:
    """Upload failures that were the stack pushing back, not falling over."""
    return sum(
        1 for f in upload_failures
        if any(marker in str(f.get("error", "")).lower() for marker in BACKPRESSURE_MARKERS)
    )


def recovery_seconds(samples: list[Sample], uploads_finished_at: float | None) -> float | None:
    """How long after the last upload the backlog reached zero, or None if it never did.

    Only samples from after the last upload count, which is what keeps the
    run's first, empty sample out of it. A run where every upload was refused
    has no backlog to clear and recovers immediately — that is an answer, not a
    failure to drain.
    """
    if uploads_finished_at is None:
        return None
    for s in sorted(samples, key=lambda s: s.elapsed_seconds):
        if s.elapsed_seconds >= uploads_finished_at and s.backlog == 0:
            return s.elapsed_seconds - uploads_finished_at
    return None


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
    kinds = tuple(args.kinds.split(",")) if args.kinds else None
    plan = plan_corpus(args.docs, args.seed, kinds)
    client = PipeshubClient(base_url=base_url, timeout_seconds=args.request_timeout)
    kb_client = KBClient(client)
    models = None
    org_models: dict[str, str] = {}
    state = plumbing.RunState()
    samples: list[Sample] = []
    kb_id: str | None = None
    started_at = datetime.now(timezone.utc)
    attempted = 0
    uploads_finished_at: float | None = None
    memory_clock = [0.0]
    t0 = time.perf_counter()
    try:
        # Seeded inside the try so a failure part way through still tears down
        # whatever was already added to the org's AI model config.
        if args.ai_models == "seed":
            models = setup_test_indexing_models(client)
        org_models = plumbing.describe_org_models(client)
        kb_id = plumbing.create_kb(kb_client, f"perf-stress-{run_id}")
        folder_ids = plumbing.create_folders(
            kb_client, kb_id, Corpus(seed=plan.seed, kinds=plan.kinds, folders=plan.folders, files=()))
        t0 = time.perf_counter()

        # The burst: everything at once, no pacing, so the queue builds up. Each
        # batch is followed by one status read, which is what makes the backlog
        # figures real rather than an upper bound.
        print(f"Uploading {args.docs} files with {args.upload_workers} workers, as fast as they go",
              flush=True)
        with ThreadPoolExecutor(max_workers=args.upload_workers) as pool:
            for batch in iter_corpus_batches(args.docs, args.batch_size, seed=plan.seed,
                                             salt=f"run {run_id}", plan=plan):
                attempted += len(batch.files)
                list(pool.map(
                    lambda f: plumbing.upload_file(kb_client, kb_id, folder_ids.get(f.folder), f, state),
                    batch.files,
                ))
                _shed_content(state)
                # Read the statuses before sampling: without it nothing would be
                # seen finishing during the burst, so the backlog would look
                # like everything uploaded so far and the slices would show no
                # progress until the drain began.
                plumbing.poll_records_once(kb_client, kb_id, state)
                _record_sample(args, plumbing, state, samples, t0, memory_clock, force_memory=True)
        uploads_finished_at = time.perf_counter() - t0
        peak = max((s.backlog for s in samples), default=0)
        print(f"Uploads done after {uploads_finished_at:.0f}s; {peak} file(s) waiting at the worst point",
              flush=True)

        # The drain: stop pushing and watch the backlog clear.
        deadline = time.perf_counter() + args.recovery_timeout
        while time.perf_counter() < deadline:
            plumbing.poll_records_once(kb_client, kb_id, state)
            sample = _record_sample(args, plumbing, state, samples, t0, memory_clock)
            print(f"  {sample.elapsed_seconds:6.0f}s  finished {sample.finished}/{sample.uploaded}"
                  f"  waiting {sample.backlog}", flush=True)
            # No backlog means drained, including when nothing was accepted at
            # all: uploading has already stopped by this point.
            if sample.backlog == 0:
                break
            time.sleep(args.poll_interval)
        else:
            state.stopped_early = (
                f"the backlog had not cleared {args.recovery_timeout:.0f}s after the last upload"
            )
        state.ended_at = time.perf_counter()
    finally:
        if kb_id and not args.keep_kb:
            try:
                kb_client.delete_kb(kb_id)
            except Exception as exc:  # noqa: BLE001 - cleanup must not hide the result
                print(f"warning: could not delete KB {kb_id}: {exc}", file=sys.stderr)
        if models is not None:
            teardown_test_indexing_models(client, models)

    return build_result(args, plan, state, t0, samples, org_models, started_at,
                        attempted, uploads_finished_at)


def _record_sample(args: argparse.Namespace, plumbing: Any, state: Any, samples: list[Sample],
                   t0: float, memory_clock: list[float], force_memory: bool = False) -> Sample:
    """One look at the run. Memory is read at most every ``--memory-interval``,
    because asking docker costs about a second."""
    now = time.perf_counter()
    container_mb = indexing_mb = None
    if force_memory or now - memory_clock[0] >= args.memory_interval:
        container_mb, indexing_mb = _memory_now(args.container, plumbing)
        memory_clock[0] = now
    with state.lock:
        uploaded, finished = len(state.uploaded_at), len(state.finished_at)
    sample = Sample(now - t0, uploaded, finished, container_mb, indexing_mb)
    samples.append(sample)
    return sample


def build_result(
    args: argparse.Namespace,
    plan: Any,
    state: Any,
    t0: float,
    samples: list[Sample],
    org_models: dict[str, str],
    started_at: datetime,
    attempted: int,
    uploads_finished_at: float | None,
) -> dict[str, Any]:
    plumbing = _plumbing()
    success_status, percentile = plumbing.SUCCESS_STATUS, plumbing.percentile

    # One consistent picture of the run, taken under the lock.
    with state.lock:
        uploaded_at = dict(state.uploaded_at)
        finished_at = dict(state.finished_at)
        statuses = dict(state.status)
        upload_failures = list(state.upload_failures)

    uploaded = len(uploaded_at)
    listed = len([r for r in uploaded_at if r in statuses])
    terminal = len([r for r in uploaded_at if r in finished_at])
    completed = [r for r in uploaded_at if statuses.get(r) == success_status]
    latencies = [max(0.0, finished_at[r] - uploaded_at[r])
                 for r in completed if r in finished_at]
    peak_backlog = max((s.backlog for s in samples), default=0)
    recovered = recovery_seconds(samples, uploads_finished_at)
    rejections = counted_rejections(upload_failures)
    verdicts = overload_verdicts(
        attempted=attempted,
        uploaded=uploaded,
        upload_failures=len(upload_failures),
        listed=listed,
        terminal=terminal,
        peak_backlog=peak_backlog,
        recovery_seconds=recovered,
        rejected=rejections,
    )
    by_status: dict[str, int] = {}
    for record_id in uploaded_at:
        status = statuses.get(record_id, "NOT_LISTED")
        by_status[status] = by_status.get(status, 0) + 1

    return {
        "schema_version": SCHEMA_VERSION,
        "benchmark": "stress",
        "label": args.label,
        "started_at": started_at.isoformat(timespec="seconds"),
        "command": " ".join(sys.argv),
        "environment": {
            "label": args.label,
            "graph_db": args.graph_db,
            "message_broker": os.getenv("MESSAGE_BROKER", "redis"),
            "ai_models": org_models,
            "host_cpus": os.cpu_count(),
            "host_platform": platform.platform(),
            "git_sha": os.getenv("GITHUB_SHA") or plumbing.run_command(["git", "rev-parse", "HEAD"]) or "",
        },
        "corpus": plan.describe(),
        "settings": {
            "docs": args.docs,
            "upload_workers": args.upload_workers,
            "batch_size": args.batch_size,
            "recovery_timeout_seconds": args.recovery_timeout,
        },
        "metrics": {
            "uploads_attempted": attempted,
            "uploads_accepted": uploaded,
            "uploads_refused": len(upload_failures),
            "refused_as_backpressure": rejections,
            "records_listed": listed,
            "records_terminal": terminal,
            "records_completed": len(completed),
            "records_by_status": dict(sorted(by_status.items())),
            "peak_backlog": peak_backlog,
            "upload_seconds": rounded(uploads_finished_at),
            "recovery_seconds": rounded(recovered),
            "time_to_indexed_seconds": {
                "p50": rounded(percentile(latencies, 50)),
                "p95": rounded(percentile(latencies, 95)),
            },
            "peak_indexing_rss_mb": rounded(
                max((s.indexing_memory_mb for s in samples if s.indexing_memory_mb), default=None)),
            "status_poll_errors": state.poll_errors,
            "stopped_early": state.stopped_early or None,
            "held_up": all(v.passed for v in verdicts),
            "failures": {"total": sum(1 for v in verdicts if not v.passed)},
        },
        "verdicts": [{"check": v.check, "passed": v.passed, "detail": v.detail} for v in verdicts],
        "backlog_windows": throughput_windows(samples, args.windows),
        # The readings behind the backlog figures above.
        "samples": [
            {
                "elapsed_seconds": rounded(s.elapsed_seconds),
                "uploaded": s.uploaded,
                "finished": s.finished,
                "backlog": s.backlog,
                "indexing_memory_mb": rounded(s.indexing_memory_mb),
            }
            for s in samples
        ],
        "upload_failures": upload_failures[:50],
    }


def render_summary(result: dict[str, Any]) -> str:
    m = result["metrics"]
    verdicts = [Verdict(v["check"], v["passed"], v["detail"]) for v in result["verdicts"]]

    def show(value: Any, unit: str = "") -> str:
        return "n/a" if value is None else f"{value}{unit}"

    headline = (
        "Nothing was lost under overload."
        if m["held_up"]
        else "**Something did not survive the overload — see the table.**"
    )
    lines = [
        f"### Stress run — {result['label']}",
        "",
        f"{m['uploads_attempted']} files uploaded as fast as the API would take them "
        f"({result['settings']['upload_workers']} at a time). {headline}",
        "",
        render_verdicts(verdicts),
        "",
        "| Measure | Value |",
        "| --- | --- |",
        f"| Files accepted / refused | {m['uploads_accepted']} / {m['uploads_refused']} "
        f"({m['refused_as_backpressure']} asked to slow down) |",
        f"| Worst backlog | {m['peak_backlog']} files waiting |",
        f"| Time to upload them all | {show(m['upload_seconds'], ' s')} |",
        f"| Time to clear the backlog afterwards | {show(m['recovery_seconds'], ' s')} |",
        f"| Time to indexed p50 / p95 | {show(m['time_to_indexed_seconds']['p50'])} / "
        f"{show(m['time_to_indexed_seconds']['p95'])} s |",
        f"| Peak indexing memory | {show(m['peak_indexing_rss_mb'], ' MB')} |",
    ]
    if m.get("stopped_early"):
        lines += ["", f"**Stopped early:** {m['stopped_early']}."]
    if not m["held_up"]:
        lines += ["", "What to do next: open the failing row above, then the run's JSON artifact, "
                      "which lists the files involved."]
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--docs", type=int, default=400,
                        help="files to fire at the stack as fast as it will take them")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--upload-workers", type=int, default=24,
                        help="parallel uploads; well past what the indexer can keep up with")
    parser.add_argument("--seed", type=int, default=4242)
    parser.add_argument("--kinds", default=None)
    parser.add_argument("--label", required=True, help="where this ran, e.g. ci-stress-neo4j-4cpu")
    parser.add_argument("--graph-db", default=os.getenv("TEST_GRAPH_DB_TYPE", "neo4j"))
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--ai-models", choices=["seed", "existing"], default="seed")
    parser.add_argument("--container", default=None)
    parser.add_argument("--poll-interval", type=float, default=10.0)
    parser.add_argument("--memory-interval", type=float, default=30.0)
    parser.add_argument("--windows", type=int, default=6)
    parser.add_argument("--recovery-timeout", type=float, default=3600,
                        help="how long to wait after the last upload for the backlog to clear")
    parser.add_argument("--request-timeout", type=int, default=180)
    parser.add_argument("--keep-kb", action="store_true")
    parser.add_argument("--output", type=Path, default=_IT_DIR / "reports" / "perf" / "stress.json")
    parser.add_argument("--summary", type=Path, default=None)
    parser.add_argument("--fail-on-violation", action="store_true",
                        help="exit non-zero when something was lost under overload")
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
    return 0 if result["metrics"]["held_up"] or not args.fail_on_violation else 1


if __name__ == "__main__":
    sys.exit(main())
