"""Indexing benchmark: upload a synthetic corpus into a fresh knowledge base and
time how long the stack takes to index it.

Against a running stack this creates a KB, recreates the corpus's folder tree,
uploads every file through the public upload API, then polls the KB's record
list until each record reaches a final indexing status (or the timeout passes).
It writes a JSON result and a Markdown summary; ``compare.py`` judges the JSON
against a baseline.

Time-to-indexed is measured from the moment a file's upload call returned to
the first poll that saw its record finished, so it is accurate to within one
poll interval (``--poll-interval``, 2 s by default).

Needs the same environment as the integration tests: ``PIPESHUB_BASE_URL`` and
either ``CLIENT_ID``/``CLIENT_SECRET`` or ``PIPESHUB_TEST_USER_EMAIL``/
``PIPESHUB_TEST_USER_PASSWORD`` of an org admin. With ``--ai-models seed`` (the
default) it also needs the ``TEST_*`` provider keys that ``ai_models_setup``
reads, and removes the models it seeded when it finishes. ``--ai-models
existing`` uses the org's own models instead; indexing fails without an LLM.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

_PERF_DIR = Path(__file__).resolve().parent
_IT_DIR = _PERF_DIR.parent
for _p in (_PERF_DIR, _IT_DIR / "helper", _IT_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from corpus import Corpus, CorpusFile, generate_corpus  # noqa: E402

SCHEMA_VERSION = 1
SUCCESS_STATUS = "COMPLETED"
# Every status a record can rest in once the indexer is done with it
# (docs/indexing-service.md, section 2.1). The rest are still in flight.
FINAL_STATUSES = frozenset({
    "COMPLETED",
    "FAILED",
    "EMPTY",
    "FILE_TYPE_NOT_SUPPORTED",
    "AUTO_INDEX_OFF",
    "ENABLE_MULTIMODAL_MODELS",
})
RECORDS_PAGE_LIMIT = 200  # the most the Knowledge Hub listing accepts


@dataclass
class RunState:
    uploaded_at: dict[str, float] = field(default_factory=dict)
    file_of: dict[str, CorpusFile] = field(default_factory=dict)
    finished_at: dict[str, float] = field(default_factory=dict)
    status: dict[str, str] = field(default_factory=dict)
    reason: dict[str, str] = field(default_factory=dict)
    poll_errors: int = 0
    ended_at: float = 0.0
    stopped_early: str = ""
    upload_failures: list[dict[str, str]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


def percentile(values: list[float], pct: float) -> float | None:
    """Linear-interpolated percentile (the same definition as numpy's default)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * pct / 100
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


_SIZE_UNITS = {
    "b": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9,
    "kib": 1024, "mib": 1024**2, "gib": 1024**3,
}


def parse_docker_mem(text: str) -> float | None:
    """Bytes in use from ``docker stats`` MemUsage, e.g. ``805.9MiB / 10GiB``."""
    match = re.match(r"\s*([\d.]+)\s*([a-zA-Z]+)", text)
    if not match:
        return None
    factor = _SIZE_UNITS.get(match.group(2).lower())
    return float(match.group(1)) * factor if factor else None


def indexing_rss_bytes(ps_output: str) -> int | None:
    """RSS of the indexing service's process tree, from ``docker top <c> -eo pid,ppid,rss,args``."""
    procs: dict[int, tuple[int, int, str]] = {}
    for line in ps_output.splitlines()[1:]:
        parts = line.split(None, 3)
        if len(parts) == 4 and parts[0].isdigit():
            procs[int(parts[0])] = (int(parts[1]), int(parts[2]), parts[3])
    # Only a python process counts as the root: a shell wrapper whose command
    # line mentions indexing_main would otherwise pull in every service.
    roots = {
        pid for pid, (_, _, args) in procs.items()
        if "indexing_main" in args and Path(args.split()[0]).name.startswith("python")
    }
    if not roots:
        return None
    tree = set(roots)
    grew = True
    while grew:
        children = {pid for pid, (ppid, _, _) in procs.items() if ppid in tree} - tree
        tree |= children
        grew = bool(children)
    return sum(procs[pid][1] for pid in tree) * 1024


class MemorySampler(threading.Thread):
    """Peak memory of the app container and of the indexing process inside it.

    Best effort: with no container name, or no docker CLI, it records nothing.
    """

    def __init__(self, container: str | None, interval: float) -> None:
        super().__init__(daemon=True)
        self.container = container
        self.interval = interval
        self.peak_container: float | None = None
        self.peak_indexing: int | None = None
        self._halt = threading.Event()

    def run(self) -> None:
        if not self.container:
            return
        while not self._halt.is_set():
            self._sample()
            self._halt.wait(self.interval)
        self._sample()

    def stop(self) -> None:
        self._halt.set()
        if self.is_alive():
            self.join(timeout=30)

    def _sample(self) -> None:
        stats = _run(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", self.container])
        used = parse_docker_mem(stats) if stats else None
        if used is not None:
            self.peak_container = max(self.peak_container or 0, used)
        # docker top runs the host's ps, so this does not depend on the image having procps.
        ps = _run(["docker", "top", self.container, "-eo", "pid,ppid,rss,args"])
        rss = indexing_rss_bytes(ps) if ps else None
        if rss is not None:
            self.peak_indexing = max(self.peak_indexing or 0, rss)


def _run(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout


def _load_env() -> None:
    from dotenv import load_dotenv

    load_dotenv(_IT_DIR / ".env", override=False)
    if os.getenv("PIPESHUB_TEST_ENV", "").strip().lower() == "local":
        load_dotenv(_IT_DIR / ".env.local", override=False)


def _ensure_client_credentials(base_url: str) -> None:
    if os.getenv("CLIENT_ID") and os.getenv("CLIENT_SECRET"):
        return
    from local_auth import obtain_local_oauth_credentials

    client_id, client_secret = obtain_local_oauth_credentials(base_url)
    os.environ["CLIENT_ID"] = client_id
    os.environ["CLIENT_SECRET"] = client_secret


def _folder_id(payload: dict[str, Any]) -> str:
    for container in (payload, payload.get("folder") or {}, payload.get("data") or {}):
        if isinstance(container, dict):
            for key in ("id", "folderId", "_key"):
                if container.get(key):
                    return str(container[key])
    raise RuntimeError(f"No folder id in the create-folder response: {payload}")


def _describe_org_models(client: Any) -> dict[str, str]:
    """The org's default LLM and embedding as ``provider/model``, for the result's label."""
    described = {}
    for model_type, when_missing in (("llm", "none"), ("embedding", "none (built-in local model)")):
        try:
            resp = client.request("GET", f"/api/v1/configurationManager/ai-models/{model_type}")
            models = resp.json().get("models") or []
        except Exception:  # noqa: BLE001 - a label, not a measurement
            described[model_type] = "unknown"
            continue
        chosen = next((m for m in models if m.get("isDefault")), models[0] if models else None)
        described[model_type] = (
            f"{chosen.get('provider')}/{(chosen.get('configuration') or {}).get('model')}"
            if chosen else when_missing
        )
    return described


def _create_kb(kb_client: Any, name: str, wait_seconds: float = 120) -> str:
    """Create the benchmark KB, waiting out a just-created org.

    A new admin reaches the graph through an async event, and until it lands
    the create answers 404 "User not found". CI runs this minutes after
    creating the org, so the wait is expected there, not a fault.
    """
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            return kb_client.create_kb(name)["id"]
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404 or time.monotonic() > deadline:
                raise
            time.sleep(5)


def _create_folders(kb_client: Any, kb_id: str, corpus: Corpus) -> dict[tuple[str, ...], str]:
    ids: dict[tuple[str, ...], str] = {}
    for path in corpus.folders:  # parents always precede their children
        parent = ids.get(path[:-1]) if len(path) > 1 else None
        ids[path] = _folder_id(kb_client.create_folder(kb_id, path[-1], parent_id=parent))
    return ids


def _upload(kb_client: Any, kb_id: str, folder_id: str | None, f: CorpusFile, state: RunState) -> None:
    try:
        result = kb_client.upload_file(kb_id, f.name, f.content, folder_id=folder_id, mimetype=f.mimetype)
    except Exception as exc:  # noqa: BLE001 - one bad upload is a result, not a crash
        with state.lock:
            state.upload_failures.append({"file": f.rel_path, "error": str(exc)[:300]})
        return
    done = time.perf_counter()
    records = result.get("records") or []
    with state.lock:
        for rec in records:
            record_id = rec.get("recordId") or rec.get("id")
            if record_id:
                state.uploaded_at[record_id] = done
                state.file_of[record_id] = f
                # A poll saw it finish before this call returned; time it from now,
                # or a run that stops before the next poll has no finish time for it.
                if state.status.get(record_id) in FINAL_STATUSES and record_id not in state.finished_at:
                    state.finished_at[record_id] = done
        if not records or result.get("failed"):
            state.upload_failures.append({"file": f.rel_path, "error": json.dumps(result.get("failed"))[:300]})


def _poll_once(kb_client: Any, kb_id: str, state: RunState) -> None:
    """Read every record's status once. A failed read is counted and retried on
    the next poll: a slow gateway under load is part of what is being measured,
    not a reason to throw the run away."""
    page = 1
    while True:
        try:
            body = kb_client.list_records(kb_id, page=page, limit=RECORDS_PAGE_LIMIT)
        except Exception as exc:  # noqa: BLE001
            with state.lock:
                state.poll_errors += 1
            print(f"  poll failed, will retry: {str(exc)[:200]}", file=sys.stderr, flush=True)
            return
        for rec in body.get("items") or []:
            record_id = rec.get("id")
            if not record_id:
                continue
            status = rec.get("indexingStatus") or "UNKNOWN"
            with state.lock:
                state.status[record_id] = status
                if rec.get("reason"):
                    state.reason[record_id] = str(rec["reason"])[:300]
                # A record can finish before its upload call returns. It is timed
                # from the first poll after that, never from before its upload.
                if (
                    status in FINAL_STATUSES
                    and record_id in state.uploaded_at
                    and record_id not in state.finished_at
                ):
                    state.finished_at[record_id] = time.perf_counter()
        total_pages = (body.get("pagination") or {}).get("totalPages") or 1
        if page >= total_pages:
            return
        page += 1


def unlisted_records(state: RunState, now: float, grace: float) -> list[str]:
    """Uploaded records the listing has never shown, ``grace`` seconds after their upload."""
    with state.lock:
        return [r for r, at in state.uploaded_at.items() if r not in state.status and now - at > grace]


def should_stop_waiting(state: RunState, uploads_done: bool, now: float, grace: float) -> str:
    """Why to stop polling, or ``""`` to keep going.

    Stops when every record is finished, and also when all that is left are
    records the listing has never shown: a status endpoint that has stopped
    returning them would otherwise look like slow indexing until the timeout.
    """
    if not uploads_done:
        return ""
    with state.lock:
        pending = {r for r in state.uploaded_at if r not in state.finished_at}
    if not pending:
        return "done"
    unlisted = set(unlisted_records(state, now, grace))
    if pending <= unlisted:
        return (
            f"{len(unlisted)} uploaded record(s) never appeared in the knowledge base's record "
            f"listing within {grace:.0f}s of upload; the listing endpoint is not reporting them"
        )
    return ""


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    from ai_models_setup import setup_test_indexing_models, teardown_test_indexing_models
    from helper.clients.kb_client import KBClient
    from pipeshub_client import PipeshubClient

    base_url = (args.base_url or os.getenv("PIPESHUB_BASE_URL", "")).rstrip("/")
    if not base_url:
        raise SystemExit("Set PIPESHUB_BASE_URL or pass --base-url")
    os.environ["PIPESHUB_BASE_URL"] = base_url
    _ensure_client_credentials(base_url)

    run_id = uuid.uuid4().hex[:8]
    gen_started = time.perf_counter()
    kinds = tuple(args.kinds.split(",")) if args.kinds else None
    corpus = generate_corpus(args.docs, args.seed, salt=f"run {run_id}", kinds=kinds)
    print(f"Generated {len(corpus.files)} files ({corpus.total_bytes / 1e6:.1f} MB) "
          f"in {time.perf_counter() - gen_started:.1f}s", flush=True)

    client = PipeshubClient(base_url=base_url, timeout_seconds=args.request_timeout)
    kb_client = KBClient(client)
    models = setup_test_indexing_models(client) if args.ai_models == "seed" else None
    org_models = _describe_org_models(client)
    sampler = MemorySampler(args.container, args.memory_interval)
    state = RunState()
    kb_id: str | None = None
    started_at = datetime.now(timezone.utc)
    try:
        kb_id = _create_kb(kb_client, f"perf-indexing-{run_id}")
        folder_ids = _create_folders(kb_client, kb_id, corpus)
        sampler.start()
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=args.upload_workers) as pool:
            futures: list[Future] = [
                pool.submit(_upload, kb_client, kb_id, folder_ids.get(f.folder), f, state)
                for f in corpus.files
            ]
            deadline = t0 + args.timeout
            while time.perf_counter() < deadline:
                uploads_done = all(fut.done() for fut in futures)
                _poll_once(kb_client, kb_id, state)
                verdict = should_stop_waiting(state, uploads_done, time.perf_counter(), args.not_listed_grace)
                if verdict:
                    if verdict != "done":
                        state.stopped_early = verdict
                        print(f"Stopping early: {verdict}", file=sys.stderr, flush=True)
                    break
                print(f"  {time.perf_counter() - t0:6.0f}s  uploaded {len(state.uploaded_at)}/{len(corpus.files)}"
                      f"  finished {len(state.finished_at)}", flush=True)
                time.sleep(args.poll_interval)
            else:
                pool.shutdown(wait=True, cancel_futures=True)
            state.ended_at = time.perf_counter()
    finally:
        sampler.stop()
        if kb_id and not args.keep_kb:
            try:
                kb_client.delete_kb(kb_id)
            except Exception as exc:  # noqa: BLE001 - cleanup must not hide the result
                print(f"warning: could not delete KB {kb_id}: {exc}", file=sys.stderr)
        if models is not None:
            teardown_test_indexing_models(client, models)

    return build_result(args, corpus, state, t0, sampler, org_models, started_at)


def build_result(
    args: argparse.Namespace,
    corpus: Corpus,
    state: RunState,
    t0: float,
    sampler: MemorySampler,
    org_models: dict[str, str],
    started_at: datetime,
) -> dict[str, Any]:
    completed = [r for r in state.uploaded_at if state.status.get(r) == SUCCESS_STATUS]
    not_completed: dict[str, int] = {}
    timed_out: list[str] = []
    not_listed: list[str] = []
    for record_id in state.uploaded_at:
        status = state.status.get(record_id, "NOT_LISTED")
        if status == SUCCESS_STATUS:
            continue
        if record_id in state.finished_at:
            not_completed[status] = not_completed.get(status, 0) + 1
        elif status == "NOT_LISTED":
            not_listed.append(record_id)
        else:
            timed_out.append(record_id)

    latencies = [max(0.0, state.finished_at[r] - state.uploaded_at[r]) for r in completed]
    upload_seconds = max(state.uploaded_at.values(), default=t0) - t0
    # With records still unfinished, the run lasted until the timeout gave up.
    last = state.ended_at if timed_out or not_listed else max(state.finished_at.values(), default=t0)
    wall = max(last - t0, upload_seconds)
    failed_files = [
        {
            "file": state.file_of[r].rel_path,
            "kind": state.file_of[r].kind,
            "status": state.status.get(r, "NOT_LISTED"),
            "reason": state.reason.get(r, ""),
        }
        for r in state.uploaded_at if r not in completed
    ]

    def rounded(value: float | None) -> float | None:
        return None if value is None else round(value, 2)

    return {
        "schema_version": SCHEMA_VERSION,
        "benchmark": "indexing",
        "label": args.label,
        "started_at": started_at.isoformat(timespec="seconds"),
        "environment": {
            "label": args.label,
            "graph_db": args.graph_db,
            "message_broker": os.getenv("MESSAGE_BROKER", "redis"),
            "ai_models": org_models,
            "host_cpus": os.cpu_count(),
            "host_memory_gb": _host_memory_gb(),
            "host_platform": platform.platform(),
            "git_sha": os.getenv("GITHUB_SHA") or _run(["git", "rev-parse", "HEAD"]) or "",
        },
        "corpus": corpus.describe(),
        "settings": {
            "upload_workers": args.upload_workers,
            "poll_interval_seconds": args.poll_interval,
            "timeout_seconds": args.timeout,
        },
        "metrics": {
            "wall_seconds": rounded(wall),
            "upload_seconds": rounded(upload_seconds),
            "records_uploaded": len(state.uploaded_at),
            "records_completed": len(completed),
            "records_per_minute": rounded(len(completed) / (wall / 60) if wall > 0 else None),
            "time_to_indexed_seconds": {
                "p50": rounded(percentile(latencies, 50)),
                "p95": rounded(percentile(latencies, 95)),
                "p99": rounded(percentile(latencies, 99)),
                "max": rounded(max(latencies, default=None) if latencies else None),
            },
            "failures": {
                "upload": len(state.upload_failures),
                "by_status": dict(sorted(not_completed.items())),
                "timed_out": len(timed_out),
                "not_listed": len(not_listed),
                "total": (
                    len(state.upload_failures) + sum(not_completed.values()) + len(timed_out) + len(not_listed)
                ),
            },
            "stopped_early": state.stopped_early or None,
            "peak_container_memory_mb": rounded(sampler.peak_container / 1e6 if sampler.peak_container else None),
            "status_poll_errors": state.poll_errors,
            "peak_indexing_rss_mb": rounded(sampler.peak_indexing / 1e6 if sampler.peak_indexing else None),
        },
        "failed_files": failed_files[:50],
        "upload_failures": state.upload_failures[:50],
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
    m = result["metrics"]
    c = result["corpus"]
    ttl = m["time_to_indexed_seconds"]
    fails = m["failures"]
    env = result["environment"]

    def show(value: Any, unit: str = "") -> str:
        return "n/a" if value is None else f"{value}{unit}"

    by_status = ", ".join(f"{k} {v}" for k, v in fails["by_status"].items()) or "none"
    lines = [
        f"### Indexing benchmark — {result['label']}",
        "",
        f"{c['docs']} files ({c['total_bytes'] / 1e6:.1f} MB, seed {c['seed']}, folders {c['folders']}): "
        + ", ".join(f"{k} {v}" for k, v in c["by_kind"].items()),
        "",
        "| Measure | Value |",
        "| --- | --- |",
        f"| Wall time, first upload to last record finished | {show(m['wall_seconds'], ' s')} |",
        f"| Upload time | {show(m['upload_seconds'], ' s')} |",
        f"| Records indexed | {m['records_completed']} of {m['records_uploaded']} uploaded |",
        f"| Throughput | {show(m['records_per_minute'])} records/min |",
        f"| Time to indexed p50 / p95 / p99 | {show(ttl['p50'])} / {show(ttl['p95'])} / {show(ttl['p99'])} s |",
        f"| Failures | {fails['total']} (upload {fails['upload']}; final status {by_status}; "
        f"timed out {fails['timed_out']}; never listed {fails['not_listed']}) |",
        f"| Peak memory, app container | {show(m['peak_container_memory_mb'], ' MB')} |",
        f"| Peak memory, indexing process | {show(m['peak_indexing_rss_mb'], ' MB')} |",
        "",
        f"Graph DB {env['graph_db']}, broker {env['message_broker']}, LLM {env['ai_models']['llm']}, embedding {env['ai_models']['embedding']}, "
        f"host {env['host_cpus']} CPUs / {env['host_memory_gb']} GB.",
    ]
    if m.get("stopped_early"):
        lines += ["", f"**Stopped early:** {m['stopped_early']}."]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--docs", type=int, default=500)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--kinds", default=None,
                        help="only these file kinds, comma-separated (default: the full mix)")
    parser.add_argument("--label", required=True, help="where this ran, e.g. ci-neo4j-4cpu; baselines are compared per label")
    parser.add_argument("--graph-db", default=os.getenv("TEST_GRAPH_DB_TYPE", "neo4j"))
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--ai-models", choices=["seed", "existing"], default="seed",
                        help="seed: add the test LLM and embedding models for the run; existing: use what the org has")
    parser.add_argument("--container", default=None, help="app container name or id, for peak memory via docker")
    parser.add_argument("--upload-workers", type=int, default=4)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--memory-interval", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=3600, help="seconds to wait for every record to finish")
    parser.add_argument("--not-listed-grace", type=float, default=300,
                        help="stop early if records are still missing from the listing this long after upload")
    parser.add_argument("--request-timeout", type=int, default=120)
    parser.add_argument("--keep-kb", action="store_true", help="leave the benchmark KB in place afterwards")
    parser.add_argument("--output", type=Path, default=_IT_DIR / "reports" / "perf" / "indexing.json")
    parser.add_argument("--summary", type=Path, default=None, help="also write the Markdown summary here")
    args = parser.parse_args()

    _load_env()
    result = run_benchmark(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = render_summary(result)
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(summary, encoding="utf-8")
    print(summary)
    print(f"Result written to {args.output}")


if __name__ == "__main__":
    main()
