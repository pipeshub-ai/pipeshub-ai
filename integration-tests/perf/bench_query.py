"""Query benchmark: seed a knowledge base, then measure search and chat under load.

Against a running stack this uploads a synthetic corpus (the same generator the
indexing benchmark uses), waits until every record is indexed, and then runs a
fixed profile: N simulated users asking the same questions for a fixed number
of seconds. It writes a JSON result and a Markdown summary; ``compare.py``
judges the JSON against a baseline.

Each user repeats a fixed mix of operations — plain searches, searches filtered
to the seeded knowledge base, and chat turns — starting at its own offset in
the question list so the users are not asking the same thing at the same moment.
A chat turn is timed from the request to the terminal ``RUN_FINISHED`` frame,
and its first frame is timed separately: a turn that starts answering quickly
and finishes slowly feels very different from one that stalls at the start.

Needs the same environment as the integration tests: ``PIPESHUB_BASE_URL`` and
either ``CLIENT_ID``/``CLIENT_SECRET`` or ``PIPESHUB_TEST_USER_EMAIL``/
``PIPESHUB_TEST_USER_PASSWORD`` of an org admin. With ``--ai-models seed`` (the
default) it also needs the ``TEST_*`` provider keys that ``ai_models_setup``
reads, and removes the models it seeded when it finishes. Chat needs an LLM:
with none configured every turn fails.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PERF_DIR = Path(__file__).resolve().parent
_IT_DIR = _PERF_DIR.parent
for _p in (_PERF_DIR, _IT_DIR / "helper", _IT_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from bench_indexing import MemorySampler  # noqa: E402
from corpus import generate_corpus  # noqa: E402
from stack import (  # noqa: E402
    SUCCESS_STATUS,
    RunState,
    create_folders,
    create_kb,
    describe_org_models,
    ensure_client_credentials,
    host_memory_gb,
    load_env,
    percentile,
    poll_records_once,
    run_command,
    should_stop_waiting,
    upload_file,
)

SCHEMA_VERSION = 1

# The corpus vocabulary is a fixed word list (corpus.py), so these phrases are
# in the documents and a search for them has something to find. Change them and
# you change what is being measured: the question set's id goes into the result
# and compare.py refuses to judge runs whose sets differ.
SEARCH_QUERIES: tuple[str, ...] = (
    "quarterly revenue report",
    "operating costs stayed flat",
    "search indexing connectors",
    "customer contract renewal",
    "incident retrospective",
    "latency throughput",
    "budget forecast",
    "security review",
)

CHAT_QUESTIONS: tuple[str, ...] = (
    "What do these documents say about quarterly revenue?",
    "Summarise what the notes say about operating costs.",
    "Which documents mention a security review?",
    "What is said about latency and throughput?",
)

# One user's repeating cycle. Searches are cheap and chat turns are not, so the
# mix decides how much of the run is spent in each.
OPERATION_MIX: tuple[str, ...] = ("search", "search", "search_filtered", "chat")

SEARCH_LIMIT = 20
CHAT_MODE = "internal_search"


def question_set_id() -> str:
    """Short digest of the questions and the mix, so a changed set is visible."""
    material = json.dumps(
        {"search": SEARCH_QUERIES, "chat": CHAT_QUESTIONS, "mix": OPERATION_MIX, "limit": SEARCH_LIMIT},
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


@dataclass
class Sample:
    operation: str
    seconds: float
    ok: bool
    # Time to the first frame carrying answer text. Not the first frame of the
    # stream: Node flushes CUSTOM{conversation_created} as soon as it has
    # written the conversation, before the query service is asked anything, so
    # timing that would miss every change in retrieval and prompt assembly.
    first_answer_seconds: float | None = None
    with_sources: bool = False
    error: str = ""


@dataclass
class Recorder:
    samples: list[Sample] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, sample: Sample) -> None:
        with self.lock:
            self.samples.append(sample)

    def of(self, operation: str) -> list[Sample]:
        with self.lock:
            return [s for s in self.samples if s.operation == operation]


def _error_label(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:160]}"


def search_hits(body: Any) -> list[Any]:
    """The hits in a search response.

    The search routes wrap them: ``searchResponse.searchResults`` carries the
    hits and ``searchResponse.records`` the documents they came from. The
    unwrapped keys are accepted too, so a route that answers flat still counts.
    """
    if not isinstance(body, dict):
        return []
    for container in (body.get("searchResponse"), body):
        if not isinstance(container, dict):
            continue
        for key in ("searchResults", "records", "results"):
            value = container.get(key)
            if isinstance(value, list) and value:
                return value
    return []


def run_search(search_client: Any, query: str, kb_id: str | None) -> Sample:
    """One search. ``kb_id`` filters to the seeded knowledge base."""
    payload: dict[str, Any] = {"limit": SEARCH_LIMIT}
    if kb_id:
        payload["filters"] = {"kb": [kb_id]}
    operation = "search_filtered" if kb_id else "search"
    started = time.perf_counter()
    try:
        resp = search_client.search(query, **payload)
    except Exception as exc:  # noqa: BLE001 - a failed request is a result
        return Sample(operation, time.perf_counter() - started, False, error=_error_label(exc))
    seconds = time.perf_counter() - started
    if resp.status_code != 200:
        return Sample(operation, seconds, False, error=f"HTTP {resp.status_code}")
    try:
        body = resp.json()
    except ValueError:
        return Sample(operation, seconds, False, error="response was not JSON")
    return Sample(operation, seconds, True, with_sources=bool(search_hits(body)))


def _carries_answer(event: str, payload: Any, state_delta_answer: Any, agui: Any) -> bool:
    """True for a frame carrying answer text.

    Retrieval and prompt assembly happen before the first of these, so this is
    the frame worth timing. Node's ``CUSTOM{conversation_created}`` is flushed
    as soon as the conversation row exists and says nothing about either.
    """
    if event == agui.TEXT_MESSAGE_CONTENT:
        return True
    return event == agui.STATE_DELTA and state_delta_answer(payload) is not None


def run_chat(conversations_client: Any, question: str, timeout: float) -> Sample:
    """One chat turn, timed to the terminal frame of its stream."""
    from helper.agui_sse import (
        AGUI,
        is_root_error,
        is_root_finished,
        iter_sse_envelopes,
        run_error_message,
        run_finished_result,
        state_delta_answer,
    )

    started = time.perf_counter()
    first_answer: float | None = None
    try:
        resp = conversations_client.stream_conversation(
            json={"query": question, "chatMode": CHAT_MODE},
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        return Sample("chat", time.perf_counter() - started, False, error=_error_label(exc))

    if resp.status_code != 200:
        resp.close()
        return Sample("chat", time.perf_counter() - started, False, error=f"HTTP {resp.status_code}")

    with_sources = False
    try:
        for envelope in iter_sse_envelopes(resp):
            try:
                payload = json.loads(envelope["data"]) if envelope.get("data") else {}
            except ValueError:
                continue
            event = envelope.get("event", "")
            if first_answer is None and _carries_answer(event, payload, state_delta_answer, AGUI):
                first_answer = time.perf_counter() - started
            if is_root_error(event, payload):
                return Sample(
                    "chat", time.perf_counter() - started, False, first_answer,
                    error=f"stream error: {run_error_message(payload)[:160]}",
                )
            if is_root_finished(event, payload):
                result = run_finished_result(payload)
                with_sources = bool(result.get("recordsUsed"))
                return Sample("chat", time.perf_counter() - started, True, first_answer, with_sources)
    except Exception as exc:  # noqa: BLE001 - a cut stream is a result
        return Sample("chat", time.perf_counter() - started, False, first_answer, error=_error_label(exc))
    finally:
        resp.close()
    return Sample("chat", time.perf_counter() - started, False, first_answer, error="stream ended with no answer")


def drive_user(
    index: int,
    deadline: float,
    args: argparse.Namespace,
    kb_id: str,
    recorder: Recorder,
    clients: dict[str, Any],
) -> None:
    """One simulated user: repeat the mix until the clock runs out."""
    turn = 0
    while time.perf_counter() < deadline:
        operation = OPERATION_MIX[(index + turn) % len(OPERATION_MIX)]
        if operation == "chat":
            question = CHAT_QUESTIONS[(index + turn) % len(CHAT_QUESTIONS)]
            sample = run_chat(clients["conversations"], question, args.chat_timeout)
        else:
            query = SEARCH_QUERIES[(index + turn) % len(SEARCH_QUERIES)]
            sample = run_search(clients["search"], query, kb_id if operation == "search_filtered" else None)
        recorder.add(sample)
        turn += 1
        if args.think_time:
            time.sleep(args.think_time)


def warm_up(args: argparse.Namespace, kb_id: str, clients: dict[str, Any]) -> None:
    """A few unrecorded operations, so first-request costs do not land in the numbers."""
    for i in range(args.warmup):
        run_search(clients["search"], SEARCH_QUERIES[i % len(SEARCH_QUERIES)], kb_id)
    if args.warmup:
        run_chat(clients["conversations"], CHAT_QUESTIONS[0], args.chat_timeout)


def seed_corpus(args: argparse.Namespace, kb_client: Any, run_id: str) -> tuple[str, Any, RunState]:
    """Upload the corpus and wait for the indexer, so questions have something to find."""
    corpus = generate_corpus(args.docs, args.seed, salt=f"run {run_id}", kinds=args.kinds_tuple)
    print(f"Seeding {len(corpus.files)} files ({corpus.total_bytes / 1e6:.1f} MB)", flush=True)
    kb_id = create_kb(kb_client, f"perf-query-{run_id}")
    folder_ids = create_folders(kb_client, kb_id, corpus)
    state = RunState()
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.upload_workers) as pool:
        futures: list[Future] = [
            pool.submit(upload_file, kb_client, kb_id, folder_ids.get(f.folder), f, state)
            for f in corpus.files
        ]
        deadline = t0 + args.index_timeout
        while time.perf_counter() < deadline:
            uploads_done = all(fut.done() for fut in futures)
            poll_records_once(kb_client, kb_id, state)
            verdict = should_stop_waiting(state, uploads_done, time.perf_counter(), args.not_listed_grace)
            if verdict:
                if verdict != "done":
                    state.stopped_early = verdict
                    print(f"Stopped waiting for the indexer: {verdict}", file=sys.stderr, flush=True)
                break
            indexed = sum(1 for r in state.status.values() if r == SUCCESS_STATUS)
            print(f"  {time.perf_counter() - t0:6.0f}s  uploaded {len(state.uploaded_at)}/{len(corpus.files)}"
                  f"  indexed {indexed}", flush=True)
            time.sleep(args.poll_interval)
        else:
            state.stopped_early = f"indexing did not finish within {args.index_timeout:.0f}s"
            pool.shutdown(wait=True, cancel_futures=True)
    return kb_id, corpus, state


def check_seed_is_usable(state: RunState, corpus: Any, required_share: float) -> None:
    """Refuse to ask questions over a corpus that is not there.

    Empty results are fast and count as successes, so a run over a half-seeded
    knowledge base produces better-looking numbers than a healthy one. The
    comparison already refuses to judge such a run; stopping here also saves the
    five minutes of load and the provider spend behind it.
    """
    indexed = sum(1 for status in state.status.values() if status == SUCCESS_STATUS)
    wanted = len(corpus.files)
    if state.stopped_early:
        raise SystemExit(
            f"Stopping before the questions: seeding did not finish ({state.stopped_early}). "
            f"{indexed} of {wanted} documents were indexed. Look at the indexing benchmark or the "
            "app container's log for why, then run this again."
        )
    if indexed >= wanted * required_share:
        return
    by_status: dict[str, int] = {}
    for status in state.status.values():
        if status != SUCCESS_STATUS:
            by_status[status] = by_status.get(status, 0) + 1
    detail = ", ".join(f"{status} {count}" for status, count in sorted(by_status.items())) or "none"
    raise SystemExit(
        f"Stopping before the questions: only {indexed} of {wanted} documents were indexed, and this "
        f"run needs at least {required_share:.0%}. Records that did not finish: {detail}. "
        f"Uploads that failed: {len(state.upload_failures)}. "
        "Questions over a corpus this incomplete answer from nothing, which measures the "
        "not-found path and looks fast. Pass --require-indexed to accept a lower share on purpose."
    )


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    from ai_models_setup import setup_test_indexing_models, teardown_test_indexing_models
    from helper.clients.conversations_client import ConversationsClient
    from helper.clients.kb_client import KBClient
    from helper.clients.search_client import SearchClient
    from pipeshub_client import PipeshubClient

    base_url = (args.base_url or os.getenv("PIPESHUB_BASE_URL", "")).rstrip("/")
    if not base_url:
        raise SystemExit("Set PIPESHUB_BASE_URL or pass --base-url")
    os.environ["PIPESHUB_BASE_URL"] = base_url
    ensure_client_credentials(base_url)

    run_id = uuid.uuid4().hex[:8]
    client = PipeshubClient(base_url=base_url, timeout_seconds=args.request_timeout)
    kb_client = KBClient(client)
    clients = {"search": SearchClient(client), "conversations": ConversationsClient(client)}
    models = setup_test_indexing_models(client) if args.ai_models == "seed" else None
    org_models = describe_org_models(client)
    sampler = MemorySampler(args.container, args.memory_interval)
    recorder = Recorder()
    kb_id: str | None = None
    started_at = datetime.now(timezone.utc)
    wall = 0.0
    try:
        kb_id, corpus, seed_state = seed_corpus(args, kb_client, run_id)
        check_seed_is_usable(seed_state, corpus, args.require_indexed)
        warm_up(args, kb_id, clients)
        sampler.start()
        load_started = time.perf_counter()
        deadline = load_started + args.duration
        with ThreadPoolExecutor(max_workers=args.users) as pool:
            users = [
                pool.submit(drive_user, user, deadline, args, kb_id, recorder, clients)
                for user in range(args.users)
            ]
        wall = time.perf_counter() - load_started
        # A user thread that died takes its share of the load with it, which
        # would otherwise read as a quiet run rather than a broken one.
        for user_index, fut in enumerate(users):
            failure = fut.exception()
            if failure is not None:
                raise RuntimeError(f"simulated user {user_index} stopped early: {failure}") from failure
    finally:
        sampler.stop()
        if kb_id and not args.keep_kb:
            try:
                kb_client.delete_kb(kb_id)
            except Exception as exc:  # noqa: BLE001 - cleanup must not hide the result
                print(f"warning: could not delete KB {kb_id}: {exc}", file=sys.stderr)
        if models is not None:
            teardown_test_indexing_models(client, models)

    return build_result(args, corpus, seed_state, recorder, wall, sampler, org_models, started_at)


def operation_metrics(samples: list[Sample], wall: float) -> dict[str, Any]:
    latencies = [s.seconds for s in samples if s.ok]
    firsts = [s.first_answer_seconds for s in samples if s.ok and s.first_answer_seconds is not None]
    errors = [s for s in samples if not s.ok]

    def rounded(value: float | None) -> float | None:
        return None if value is None else round(value, 3)

    metrics: dict[str, Any] = {
        "count": len(samples),
        "succeeded": len(latencies),
        "errors": len(errors),
        "error_rate": round(len(errors) / len(samples), 4) if samples else None,
        "per_minute": round(len(samples) / (wall / 60), 2) if wall > 0 else None,
        "latency_seconds": {
            "p50": rounded(percentile(latencies, 50)),
            "p95": rounded(percentile(latencies, 95)),
            "p99": rounded(percentile(latencies, 99)),
            "max": rounded(max(latencies)) if latencies else None,
        },
        "with_sources": sum(1 for s in samples if s.ok and s.with_sources),
        # Of the requests that succeeded, the share that actually found
        # something: a search with no hits and a chat answer citing nothing are
        # both fast, so a rate that falls while latency improves is a warning.
        "with_sources_rate": (
            round(sum(1 for s in samples if s.ok and s.with_sources) / len(latencies), 4)
            if latencies else None
        ),
    }
    if firsts:
        metrics["first_answer_seconds"] = {
            "p50": rounded(percentile(firsts, 50)),
            "p95": rounded(percentile(firsts, 95)),
        }
    return metrics


def build_result(
    args: argparse.Namespace,
    corpus: Any,
    seed_state: RunState,
    recorder: Recorder,
    wall: float,
    sampler: MemorySampler,
    org_models: dict[str, str],
    started_at: datetime,
) -> dict[str, Any]:
    samples = recorder.samples
    operations = {op: operation_metrics(recorder.of(op), wall) for op in sorted(set(OPERATION_MIX))}
    errors = [s for s in samples if not s.ok]
    by_error: dict[str, int] = {}
    for sample in errors:
        by_error[sample.error] = by_error.get(sample.error, 0) + 1
    indexed = sum(1 for status in seed_state.status.values() if status == SUCCESS_STATUS)

    return {
        "schema_version": SCHEMA_VERSION,
        "benchmark": "query",
        "label": args.label,
        "started_at": started_at.isoformat(timespec="seconds"),
        "environment": {
            "label": args.label,
            "graph_db": args.graph_db,
            "message_broker": os.getenv("MESSAGE_BROKER", "redis"),
            "ai_models": org_models,
            "host_cpus": os.cpu_count(),
            "host_memory_gb": host_memory_gb(),
            "host_platform": platform.platform(),
            "git_sha": os.getenv("GITHUB_SHA") or run_command(["git", "rev-parse", "HEAD"]) or "",
        },
        "corpus": corpus.describe(),
        "profile": {
            "users": args.users,
            "duration_seconds": args.duration,
            "think_time_seconds": args.think_time,
            "warmup_operations": args.warmup,
            "question_set": question_set_id(),
            "mix": list(OPERATION_MIX),
            "search_limit": SEARCH_LIMIT,
            "chat_mode": CHAT_MODE,
        },
        "metrics": {
            "wall_seconds": round(wall, 2),
            "operations": operations,
            "operations_total": len(samples),
            "operations_per_minute": round(len(samples) / (wall / 60), 2) if wall > 0 else None,
            "error_rate": round(len(errors) / len(samples), 4) if samples else None,
            "errors_by_kind": dict(sorted(by_error.items(), key=lambda kv: -kv[1])[:10]),
            "docs_uploaded": len(seed_state.uploaded_at),
            "docs_indexed": indexed,
            "seeding_stopped_early": seed_state.stopped_early or None,
            "peak_container_memory_mb": (
                round(sampler.peak_container / 1e6, 2) if sampler.peak_container else None
            ),
        },
    }


def render_summary(result: dict[str, Any]) -> str:
    m = result["metrics"]
    p = result["profile"]
    env = result["environment"]

    def show(value: Any, unit: str = "") -> str:
        return "n/a" if value is None else f"{value}{unit}"

    lines = [
        f"### Query benchmark — {result['label']}",
        "",
        f"{p['users']} simulated users for {p['duration_seconds']}s over "
        f"{m['docs_indexed']} indexed documents (question set {p['question_set']}).",
        "",
        "| Operation | Count | Errors | p50 | p95 | p99 | Per minute | Found something |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, op in m["operations"].items():
        lat = op["latency_seconds"]
        rate = op.get("with_sources_rate")
        lines.append(
            f"| {name} | {op['count']} | {op['errors']} | {show(lat['p50'], ' s')} | "
            f"{show(lat['p95'], ' s')} | {show(lat['p99'], ' s')} | {show(op['per_minute'])} | "
            f"{'n/a' if rate is None else f'{rate:.0%}'} |"
        )
    chat = m["operations"].get("chat", {})
    first = chat.get("first_answer_seconds") or {}
    lines += [
        "",
        f"Overall {show(m['operations_per_minute'])} operations/min, "
        f"error rate {show(m['error_rate'])}, "
        f"peak app container memory {show(m['peak_container_memory_mb'], ' MB')}.",
    ]
    if first:
        lines.append(
            f"Chat started answering in {show(first.get('p50'), ' s')} (p50) / "
            f"{show(first.get('p95'), ' s')} (p95); "
            f"{chat.get('with_sources', 0)} of {chat.get('succeeded', 0)} answers cited a document."
        )
    searches = m["operations"].get("search", {})
    lines.append(
        f"{searches.get('with_sources', 0)} of {searches.get('succeeded', 0)} searches found a hit."
    )
    lines += [
        "",
        f"Graph DB {env['graph_db']}, broker {env['message_broker']}, LLM {env['ai_models']['llm']}, "
        f"embedding {env['ai_models']['embedding']}, host {env['host_cpus']} CPUs / {env['host_memory_gb']} GB.",
    ]
    if m["errors_by_kind"]:
        listed = "; ".join(f"{kind} ×{count}" for kind, count in m["errors_by_kind"].items())
        lines += ["", f"**Errors:** {listed}."]
    if m.get("seeding_stopped_early"):
        lines += ["", f"**Seeding stopped early:** {m['seeding_stopped_early']}. "
                      "The questions were asked over fewer documents than usual, so treat the numbers as "
                      "not comparable and look at the indexing benchmark for why."]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--docs", type=int, default=120, help="files to seed before asking anything")
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--kinds", default=None,
                        help="only these file kinds, comma-separated (default: the full mix)")
    parser.add_argument("--users", type=int, default=4, help="simulated users asking at the same time")
    parser.add_argument("--duration", type=float, default=300, help="seconds of load, after warm-up")
    parser.add_argument("--think-time", type=float, default=1.0, help="seconds a user waits between turns")
    parser.add_argument("--warmup", type=int, default=2, help="unrecorded searches before the run")
    parser.add_argument("--label", required=True,
                        help="where this ran, e.g. ci-query-neo4j-4cpu; baselines are compared per label")
    parser.add_argument("--graph-db", default=os.getenv("TEST_GRAPH_DB_TYPE", "neo4j"))
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--ai-models", choices=["seed", "existing"], default="seed",
                        help="seed: add the test LLM and embedding models for the run; existing: use the org's")
    parser.add_argument("--container", default=None, help="app container name or id, for peak memory via docker")
    parser.add_argument("--upload-workers", type=int, default=4)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--memory-interval", type=float, default=5.0)
    parser.add_argument("--index-timeout", type=float, default=1800,
                        help="seconds to wait for the seeded corpus to finish indexing")
    parser.add_argument("--not-listed-grace", type=float, default=300)
    parser.add_argument("--require-indexed", type=float, default=1.0,
                        help="share of the corpus that must be indexed before the questions start")
    parser.add_argument("--request-timeout", type=int, default=120)
    parser.add_argument("--chat-timeout", type=float, default=300, help="seconds to wait for one chat turn")
    parser.add_argument("--keep-kb", action="store_true", help="leave the benchmark KB in place afterwards")
    parser.add_argument("--output", type=Path, default=_IT_DIR / "reports" / "perf" / "query.json")
    parser.add_argument("--summary", type=Path, default=None, help="also write the Markdown summary here")
    args = parser.parse_args()
    args.kinds_tuple = tuple(args.kinds.split(",")) if args.kinds else None
    if args.users < 1:
        raise SystemExit("--users must be at least 1")
    if not 0 < args.require_indexed <= 1:
        raise SystemExit("--require-indexed must be a share above 0 and at most 1")

    load_env()
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
