"""Sustained-load test: ramp simulated users through search and chat, hold, and gate the run.

The weekly query benchmark (``bench_query.py``) asks a fixed four users for five
minutes and reports. This one asks the question it cannot: does search and chat
keep working, and keep its speed, when many people use it at once for a while?

Against a running stack it seeds a small corpus (the same generator and seeding
as the query benchmark), then drives simulated users through **stages**, the way
k6's ``ramping-vus`` executor does: each stage moves the number of active users
in a straight line from the previous stage's count to its own, over its
duration. The default ramps to four users, then to eight, then holds eight for
fifteen minutes. Each user repeats a fixed mix of plain searches, searches
filtered to the seeded knowledge base, streaming chat turns and non-streaming
chat turns, and waits a random think time between them (Locust's ``between``).

Numbers are reported for the whole run, per stage, and for the **steady
window**, the stages that hold the peak user count. The comparison with a
baseline uses the steady window only, because the ramp stages mix different
loads and move with every change to the ramp.

The run then applies its own pass-or-fail checks, which need no baseline: the
error rate stays under ``--max-error-rate``, the steady window measured enough
operations, every kind of operation succeeded at least once, and plain and
filtered searches each found something. ``--fail-on-violation`` turns a failed check into a failing exit
code. If most recent operations are failing, the run stops early instead of
spending the rest of its budget on a broken stack (k6's ``abortOnFail``).

Needs the same environment as the query benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import random
import re
import sys
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

_PERF_DIR = Path(__file__).resolve().parent
_IT_DIR = _PERF_DIR.parent
for _p in (_PERF_DIR, _IT_DIR / "helper", _IT_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from bench_indexing import MemorySampler  # noqa: E402
from bench_query import (  # noqa: E402
    CHAT_MODE,
    CHAT_QUESTIONS,
    SEARCH_LIMIT,
    SEARCH_QUERIES,
    Sample,
    _error_label,
    check_seed_is_usable,
    operation_metrics,
    run_chat,
    run_search,
    seed_corpus,
    warm_up,
)
from stack import (  # noqa: E402
    SUCCESS_STATUS,
    describe_org_models,
    ensure_client_credentials,
    host_memory_gb,
    load_env,
    percentile,
    run_command,
)

SCHEMA_VERSION = 1
BENCHMARK = "load"

# ``chat`` is the streaming turn the query benchmark also times, so the two
# results name it the same way; ``chat_sync`` is the non-streaming
# ``POST /conversations/create``. Half the cycle is chat, because chat is what
# holds a worker and a provider call for seconds at a time.
LOAD_MIX: tuple[str, ...] = ("search", "chat", "search_filtered", "chat_sync", "search", "chat")

DEFAULT_STAGES = "120:4,120:8,900:8"
DEFAULT_THINK_TIME = "1:3"

# The abort check looks at this many of the most recent operations.
ABORT_WINDOW = 30
IDLE_POLL_SECONDS = 0.2
PROGRESS_SECONDS = 30.0


def question_set_id() -> str:
    """Short digest of the questions and this benchmark's mix."""
    material = json.dumps(
        {"search": SEARCH_QUERIES, "chat": CHAT_QUESTIONS, "mix": LOAD_MIX, "limit": SEARCH_LIMIT},
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------
# The load shape
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Stage:
    seconds: float
    users: int


# The same grammar as the workflow's "Validate inputs" step, which
# unit/test_perf_load.py checks against these parsers. No spaces, and no
# number form that float() would take but the step cannot, such as 1e3 or nan.
# Digit counts are capped so every value is finite and sane: a 400-digit
# duration would become inf, and 10,000 users would be 10,000 threads.
# Seconds up to 999999.999 (about 11 days), users up to 9999, docs up to 999999.
_NUMBER = r"[0-9]{1,6}(?:\.[0-9]{1,3})?"
_STAGE = re.compile(rf"({_NUMBER}):([0-9]{{1,4}})")
_THINK_TIME = re.compile(rf"({_NUMBER})(?::({_NUMBER}))?")


def parse_stages(text: str) -> tuple[Stage, ...]:
    """``"120:4,120:8,900:8"``: seconds and the user count to reach by the end of each."""
    stages = []
    for part in text.split(","):
        match = _STAGE.fullmatch(part)
        if not match:
            raise ValueError(
                f"a stage is seconds:users with no spaces, up to 999999.999 seconds and 9999 users, "
                f"like 120:4; got {part!r}"
            )
        seconds, users = float(match.group(1)), int(match.group(2))
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError(f"a stage must last longer than 0 seconds; got {part!r}")
        stages.append(Stage(seconds, users))
    if not stages:
        raise ValueError("at least one stage is needed")
    if max(s.users for s in stages) < 1:
        raise ValueError("at least one stage must have a user in it")
    return tuple(stages)


def parse_docs(text: str) -> int:
    """A whole number of at least 1, in plain ASCII digits: int() alone takes " 80", "+80" and "8_0"."""
    if not re.fullmatch(r"[0-9]{1,6}", text) or int(text) < 1:
        raise argparse.ArgumentTypeError(f"--docs must be a whole number from 1 to 999999; got {text!r}")
    return int(text)


def parse_think_time(text: str) -> tuple[float, float]:
    """``"1:3"`` is a random wait of one to three seconds; ``"2"`` is always two."""
    match = _THINK_TIME.fullmatch(text)
    if not match:
        raise ValueError(f"think time is seconds or min:max with no spaces, like 1:3; got {text!r}")
    low = float(match.group(1))
    high = float(match.group(2)) if match.group(2) is not None else low
    if not (math.isfinite(low) and math.isfinite(high)):
        raise ValueError(f"think time must be a finite number of seconds; got {text!r}")
    if high < low:
        raise ValueError(f"think time is seconds or min:max with min <= max, like 1:3; got {text!r}")
    return low, high


def total_seconds(stages: tuple[Stage, ...]) -> float:
    return sum(s.seconds for s in stages)


def target_users(stages: tuple[Stage, ...], elapsed: float) -> float:
    """How many users should be active ``elapsed`` seconds in: linear within each stage."""
    start_users = 0
    stage_start = 0.0
    for stage in stages:
        if elapsed < stage_start + stage.seconds:
            share = max(0.0, elapsed - stage_start) / stage.seconds
            return start_users + (stage.users - start_users) * share
        stage_start += stage.seconds
        start_users = stage.users
    return float(start_users)


def active_users(stages: tuple[Stage, ...], elapsed: float) -> int:
    # Rounded down, so a user joins once the ramp has reached them, not before.
    return int(math.floor(target_users(stages, elapsed) + 1e-9))


def stage_index(stages: tuple[Stage, ...], elapsed: float) -> int:
    stage_start = 0.0
    for index, stage in enumerate(stages):
        stage_start += stage.seconds
        if elapsed < stage_start:
            return index
    return len(stages) - 1


def steady_stages(stages: tuple[Stage, ...]) -> list[int]:
    """Stages that hold the peak user count from start to end.

    With no such stage (a pure ramp), the stage that ends at the peak stands in,
    so there is always a window to judge.
    """
    peak = max(s.users for s in stages)
    held = []
    previous = 0
    for index, stage in enumerate(stages):
        if previous == stage.users == peak:
            held.append(index)
        previous = stage.users
    if held:
        return held
    return [next(i for i, s in enumerate(stages) if s.users == peak)]


# --------------------------------------------------------------------------
# One non-streaming chat turn
# --------------------------------------------------------------------------


def run_chat_sync(conversations_client: Any, question: str, timeout: float) -> Sample:
    """``POST /conversations/create``: the whole answer in one response."""
    started = time.perf_counter()
    try:
        resp = conversations_client.create_conversation(
            json={"query": question, "chatMode": CHAT_MODE}, timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 - a failed request is a result
        return Sample("chat_sync", time.perf_counter() - started, False, error=_error_label(exc))
    seconds = time.perf_counter() - started
    if resp.status_code != 201:
        return Sample("chat_sync", seconds, False, error=f"HTTP {resp.status_code}")
    try:
        body = resp.json()
    except ValueError:
        return Sample("chat_sync", seconds, False, error="response was not JSON")
    conversation = body.get("conversation") if isinstance(body, dict) else None
    messages = conversation.get("messages") if isinstance(conversation, dict) else None
    last = messages[-1] if isinstance(messages, list) and messages else None
    # A 201 with no answer in it is a turn that failed quietly, and fast.
    if not isinstance(last, dict) or last.get("messageType") != "bot_response":
        return Sample("chat_sync", seconds, False, error="response carried no answer")
    if not str(last.get("content") or "").strip():
        return Sample("chat_sync", seconds, False, error="answer was empty")
    return Sample("chat_sync", seconds, True, with_sources=bool(last.get("citations")))


# --------------------------------------------------------------------------
# The load
# --------------------------------------------------------------------------


@dataclass
class Timed:
    """A sample, and where in the run it started."""

    started: float
    users: int
    stage: int
    sample: Sample
    user: int = -1


@dataclass
class LoadRecorder:
    entries: list[Timed] = field(default_factory=list)
    recent: deque = field(default_factory=lambda: deque(maxlen=ABORT_WINDOW))
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, entry: Timed) -> None:
        with self.lock:
            self.entries.append(entry)
            self.recent.append(entry.sample.ok)

    def snapshot(self) -> list[Timed]:
        with self.lock:
            return list(self.entries)

    def recent_error_share(self) -> float | None:
        with self.lock:
            if len(self.recent) < ABORT_WINDOW:
                return None
            return sum(1 for ok in self.recent if not ok) / len(self.recent)


def run_operation(operation: str, index: int, turn: int, args: argparse.Namespace,
                  kb_id: str, clients: dict[str, Any]) -> Sample:
    if operation == "chat":
        return run_chat(clients["conversations"], CHAT_QUESTIONS[(index + turn) % len(CHAT_QUESTIONS)],
                        args.chat_timeout)
    if operation == "chat_sync":
        return run_chat_sync(clients["conversations"], CHAT_QUESTIONS[(index + turn) % len(CHAT_QUESTIONS)],
                             args.chat_timeout)
    query = SEARCH_QUERIES[(index + turn) % len(SEARCH_QUERIES)]
    return run_search(clients["search"], query, kb_id if operation == "search_filtered" else None)


def drive_user(
    index: int,
    load_started: float,
    stages: tuple[Stage, ...],
    args: argparse.Namespace,
    kb_id: str,
    recorder: LoadRecorder,
    clients: dict[str, Any],
    halt: threading.Event,
    operation: Callable[..., Sample] = run_operation,
) -> None:
    """One simulated user: work while the ramp has it active, idle otherwise.

    A user the ramp drops finishes the operation it is in first, as k6 does
    with its graceful ramp-down, so no request is cut off to shape the load.
    """
    rng = random.Random(args.seed * 1000 + index)
    think_min, think_max = args.think_time_range
    end = total_seconds(stages)
    turn = 0
    while not halt.is_set():
        elapsed = time.perf_counter() - load_started
        if elapsed >= end:
            return
        users = active_users(stages, elapsed)
        if index >= users:
            halt.wait(IDLE_POLL_SECONDS)
            continue
        name = LOAD_MIX[(index + turn) % len(LOAD_MIX)]
        sample = operation(name, index, turn, args, kb_id, clients)
        recorder.add(Timed(round(elapsed, 3), users, stage_index(stages, elapsed), sample, index))
        turn += 1
        halt.wait(rng.uniform(think_min, think_max))


def run_load(
    args: argparse.Namespace,
    stages: tuple[Stage, ...],
    kb_id: str,
    clients: dict[str, Any],
    recorder: LoadRecorder,
    operation: Callable[..., Sample] = run_operation,
) -> tuple[float, str | None]:
    """Drive every stage. Returns the wall time and why the run stopped early, if it did."""
    peak = max(s.users for s in stages)
    halt = threading.Event()
    aborted: str | None = None
    load_started = time.perf_counter()
    end = load_started + total_seconds(stages)
    next_report = load_started + PROGRESS_SECONDS
    with ThreadPoolExecutor(max_workers=peak) as pool:
        users = [
            pool.submit(drive_user, i, load_started, stages, args, kb_id, recorder, clients, halt, operation)
            for i in range(peak)
        ]
        while not all(u.done() for u in users):
            now = time.perf_counter()
            share = recorder.recent_error_share()
            if aborted is None and share is not None and share >= args.abort_error_rate:
                aborted = (
                    f"{share:.0%} of the last {ABORT_WINDOW} operations failed, at or above "
                    f"--abort-error-rate {args.abort_error_rate:.0%}"
                )
                print(f"Stopping the load early: {aborted}", file=sys.stderr, flush=True)
                halt.set()
            if now >= next_report and now < end:
                print_progress(recorder, stages, now - load_started)
                next_report += PROGRESS_SECONDS
            time.sleep(0.5)
    wall = time.perf_counter() - load_started
    # A user thread that died takes its share of the load with it, which would
    # otherwise read as a quiet run rather than a broken one.
    for index, fut in enumerate(users):
        failure = fut.exception()
        if failure is not None:
            raise RuntimeError(f"simulated user {index} stopped early: {failure}") from failure
    return wall, aborted


def print_progress(recorder: LoadRecorder, stages: tuple[Stage, ...], elapsed: float) -> None:
    entries = recorder.snapshot()
    recent = [e.sample for e in entries if e.started >= elapsed - PROGRESS_SECONDS]
    errors = sum(1 for s in recent if not s.ok)
    p95 = percentile([s.seconds for s in recent if s.ok], 95)
    print(
        f"  {elapsed:6.0f}s  users {active_users(stages, elapsed)}  operations {len(entries)}"
        f"  last {PROGRESS_SECONDS:.0f}s: {len(recent)} done, {errors} failed, "
        f"p95 {'n/a' if p95 is None else f'{p95:.2f}s'}",
        flush=True,
    )


# --------------------------------------------------------------------------
# Numbers
# --------------------------------------------------------------------------


def _window_seconds(stages: tuple[Stage, ...], indexes: list[int]) -> float:
    return sum(stages[i].seconds for i in indexes)


def _window(entries: list[Timed], indexes: list[int], seconds: float) -> dict[str, Any]:
    wanted = set(indexes)
    samples = [e.sample for e in entries if e.stage in wanted]
    errors = sum(1 for s in samples if not s.ok)
    succeeded = len(samples) - errors
    return {
        "operations": {op: operation_metrics([s for s in samples if s.operation == op], seconds)
                       for op in sorted(set(LOAD_MIX))},
        "operations_total": len(samples),
        "operations_per_minute": round(len(samples) / (seconds / 60), 2) if seconds > 0 else None,
        # Throughput that counts only what worked, so a stack that fails fast
        # does not look busier than one that answers.
        "succeeded_per_minute": round(succeeded / (seconds / 60), 2) if seconds > 0 else None,
        "error_rate": round(errors / len(samples), 4) if samples else None,
    }


def stage_rows(entries: list[Timed], stages: tuple[Stage, ...]) -> list[dict[str, Any]]:
    rows = []
    previous = 0
    for index, stage in enumerate(stages):
        window = _window(entries, [index], stage.seconds)
        ops = window["operations"]
        chat_first = ops.get("chat", {}).get("first_answer_seconds") or {}
        rows.append({
            "stage": index + 1,
            "seconds": stage.seconds,
            "users_from": previous,
            "users_to": stage.users,
            "operations": window["operations_total"],
            "error_rate": window["error_rate"],
            "succeeded_per_minute": window["succeeded_per_minute"],
            "search_p95": ops["search"]["latency_seconds"]["p95"],
            "chat_p95": ops["chat"]["latency_seconds"]["p95"],
            "chat_first_answer_p95": chat_first.get("p95"),
            "chat_sync_p95": ops["chat_sync"]["latency_seconds"]["p95"],
        })
        previous = stage.users
    return rows


def evaluate_gate(metrics: dict[str, Any], max_error_rate: float, min_operations: int,
                  aborted: str | None) -> list[str]:
    """The run's own pass-or-fail checks, in plain words. Empty means it passed."""
    violations = []
    if aborted:
        violations.append(f"The load stopped early: {aborted}.")
    whole = metrics["whole_run"]
    rate = whole["error_rate"]
    if rate is None:
        violations.append("No operation finished, so nothing was measured.")
    elif rate > max_error_rate:
        violations.append(
            f"{rate:.1%} of all operations failed, above the {max_error_rate:.1%} this run allows "
            f"({whole['errors']} of {whole['operations_total']})."
        )
    if aborted:
        # The rest would only restate that little ran after the stop.
        return violations
    steady = metrics["operations_total"]
    if steady < min_operations:
        violations.append(
            f"The steady window measured {steady} operations, fewer than the {min_operations} "
            "needed for its percentiles to mean anything."
        )
    for name, op in metrics["operations"].items():
        if op["count"] and not op["succeeded"]:
            violations.append(f"Every {name} operation in the steady window failed.")
        elif not op["count"]:
            violations.append(f"No {name} operation ran in the steady window.")
    # Filtered searches get their own check: a knowledge-base filter that
    # stopped matching returns nothing while plain searches still find plenty.
    for name, what in (("search", "search"), ("search_filtered", "search filtered to the seeded knowledge base")):
        op = metrics["operations"].get(name, {})
        if op.get("succeeded") and not op.get("with_sources"):
            violations.append(
                f"No {what} found anything. An empty result is fast and counts as a success, so "
                "this run measured the not-found path; check that the seeded documents were indexed."
            )
    return violations


def build_result(
    args: argparse.Namespace,
    stages: tuple[Stage, ...],
    corpus: Any,
    seed_state: Any,
    recorder: LoadRecorder,
    wall: float,
    aborted: str | None,
    peak_container: float | None,
    org_models: dict[str, str],
    started_at: datetime,
) -> dict[str, Any]:
    entries = recorder.snapshot()
    held = steady_stages(stages)
    steady = _window(entries, held, _window_seconds(stages, held))
    everything = _window(entries, list(range(len(stages))), wall or total_seconds(stages))
    errors = [e.sample for e in entries if not e.sample.ok]
    by_error: dict[str, int] = {}
    for sample in errors:
        by_error[sample.error] = by_error.get(sample.error, 0) + 1
    indexed = sum(1 for status in seed_state.status.values() if status == SUCCESS_STATUS)
    held_from = sum(s.seconds for s in stages[: held[0]])

    metrics: dict[str, Any] = {
        # The steady window's numbers sit at the top level, in the same shape as
        # the query benchmark's, because they are the ones compare.py judges.
        **steady,
        "steady_window": {
            "stages": [i + 1 for i in held],
            "from_seconds": held_from,
            "seconds": _window_seconds(stages, held),
            "users": max(s.users for s in stages),
        },
        "whole_run": {
            "wall_seconds": round(wall, 2),
            "operations_total": everything["operations_total"],
            "errors": len(errors),
            "error_rate": everything["error_rate"],
            "succeeded_per_minute": everything["succeeded_per_minute"],
        },
        "stages": stage_rows(entries, stages),
        "errors_by_kind": dict(sorted(by_error.items(), key=lambda kv: -kv[1])[:10]),
        "aborted": aborted,
        "docs_uploaded": len(seed_state.uploaded_at),
        "docs_indexed": indexed,
        "seeding_stopped_early": seed_state.stopped_early or None,
        "peak_container_memory_mb": round(peak_container / 1e6, 2) if peak_container else None,
    }
    violations = evaluate_gate(metrics, args.max_error_rate, args.min_operations, aborted)
    return {
        "schema_version": SCHEMA_VERSION,
        "benchmark": BENCHMARK,
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
            "stages": [[s.seconds, s.users] for s in stages],
            "think_time_seconds": list(args.think_time_range),
            "warmup_operations": args.warmup,
            "question_set": question_set_id(),
            "mix": list(LOAD_MIX),
            "search_limit": SEARCH_LIMIT,
            "chat_mode": CHAT_MODE,
        },
        "metrics": metrics,
        "gate": {
            "max_error_rate": args.max_error_rate,
            "min_operations": args.min_operations,
            "passed": not violations,
            "violations": violations,
        },
    }


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------


def run_benchmark(args: argparse.Namespace, stages: tuple[Stage, ...]) -> dict[str, Any]:
    from ai_models_setup import (
        setup_test_indexing_models,
        teardown_test_indexing_models,
    )
    from pipeshub_client import PipeshubClient

    from helper.clients.conversations_client import ConversationsClient
    from helper.clients.kb_client import KBClient
    from helper.clients.search_client import SearchClient

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
    recorder = LoadRecorder()
    kb_id: str | None = None
    started_at = datetime.now(timezone.utc)
    wall, aborted = 0.0, None
    try:
        kb_id, corpus, seed_state = seed_corpus(args, kb_client, run_id, kb_prefix="perf-load")
        check_seed_is_usable(seed_state, corpus, args.require_indexed)
        warm_up(args, kb_id, clients)
        print(f"Load: {', '.join(f'{s.users} users by {s.seconds:g}s' for s in stages)}", flush=True)
        sampler.start()
        wall, aborted = run_load(args, stages, kb_id, clients, recorder)
    finally:
        sampler.stop()
        if kb_id and not args.keep_kb:
            try:
                kb_client.delete_kb(kb_id)
            except Exception as exc:  # noqa: BLE001 - cleanup must not hide the result
                print(f"warning: could not delete KB {kb_id}: {exc}", file=sys.stderr)
        if models is not None:
            teardown_test_indexing_models(client, models)

    return build_result(args, stages, corpus, seed_state, recorder, wall, aborted,
                        sampler.peak_container, org_models, started_at)


def render_summary(result: dict[str, Any]) -> str:
    m = result["metrics"]
    p = result["profile"]
    env = result["environment"]
    gate = result["gate"]
    steady = m["steady_window"]
    whole = m["whole_run"]

    def show(value: Any, unit: str = "") -> str:
        return "n/a" if value is None else f"{value}{unit}"

    shape = ", then ".join(f"{int(u)} users by {s:g}s" for s, u in p["stages"])
    lines = [
        f"### Sustained load — {result['label']}",
        "",
        f"**{'Passed' if gate['passed'] else 'Failed'}.** {shape}, over {m['docs_indexed']} indexed "
        f"documents, thinking {p['think_time_seconds'][0]:g}–{p['think_time_seconds'][1]:g}s between "
        f"operations (question set {p['question_set']}).",
        "",
    ]
    if gate["violations"]:
        lines += [*[f"- {v}" for v in gate["violations"]], ""]
    lines += [
        f"#### Steady window: {steady['users']} users for {steady['seconds']:g}s (stage "
        f"{', '.join(str(s) for s in steady['stages'])})",
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
    first = m["operations"].get("chat", {}).get("first_answer_seconds") or {}
    lines += [
        "",
        f"{show(m['operations_per_minute'])} operations/min, {show(m['succeeded_per_minute'])} of them "
        f"successful; error rate {show(m['error_rate'])}. Streaming chat started answering in "
        f"{show(first.get('p50'), ' s')} (p50) / {show(first.get('p95'), ' s')} (p95).",
        "",
        "#### By stage",
        "",
        "| Stage | Users | Seconds | Operations | Error rate | Succeeded/min | Search p95 | "
        "Chat p95 | First answer p95 | Non-streaming chat p95 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in m["stages"]:
        lines.append(
            f"| {row['stage']} | {row['users_from']}→{row['users_to']} | {row['seconds']:g} | "
            f"{row['operations']} | {show(row['error_rate'])} | {show(row['succeeded_per_minute'])} | "
            f"{show(row['search_p95'], ' s')} | {show(row['chat_p95'], ' s')} | "
            f"{show(row['chat_first_answer_p95'], ' s')} | {show(row['chat_sync_p95'], ' s')} |"
        )
    lines += [
        "",
        f"Whole run: {whole['operations_total']} operations in {whole['wall_seconds']}s, "
        f"{whole['errors']} failed (error rate {show(whole['error_rate'])}, allowed "
        f"{gate['max_error_rate']}). Peak app container memory "
        f"{show(m['peak_container_memory_mb'], ' MB')}.",
        "",
        f"Graph DB {env['graph_db']}, broker {env['message_broker']}, LLM {env['ai_models']['llm']}, "
        f"embedding {env['ai_models']['embedding']}, host {env['host_cpus']} CPUs / {env['host_memory_gb']} GB.",
    ]
    if m["errors_by_kind"]:
        listed = "; ".join(f"{kind} ×{count}" for kind, count in m["errors_by_kind"].items())
        lines += ["", f"**Errors:** {listed}."]
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--docs", type=parse_docs, default=80, help="files to seed before the load")
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--kinds", default="txt,md,html,docx,pdf",
                        help="file kinds to seed, comma-separated. Spreadsheets are left out by default: "
                             "they index slowest and this measures questions, not indexing")
    parser.add_argument("--stages", default=DEFAULT_STAGES,
                        help="seconds:users for each stage, comma-separated; users move linearly "
                             "from the previous stage's count")
    parser.add_argument("--think-time", default=DEFAULT_THINK_TIME,
                        help="seconds a user waits between operations: a fixed number, or min:max for a random wait")
    parser.add_argument("--warmup", type=int, default=2, help="unrecorded searches before the load")
    parser.add_argument("--max-error-rate", type=float, default=0.02,
                        help="share of failed operations, over the whole run, above which the run fails")
    parser.add_argument("--min-operations", type=int, default=50,
                        help="fewest operations the steady window must measure")
    parser.add_argument("--abort-error-rate", type=float, default=0.5,
                        help=f"stop early when this share of the last {ABORT_WINDOW} operations failed")
    parser.add_argument("--fail-on-violation", action="store_true",
                        help="exit 1 when one of the run's own checks fails (the result is written first)")
    parser.add_argument("--label", required=True,
                        help="where this ran, e.g. ci-load-neo4j-4cpu; baselines are compared per label")
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
                        help="share of the corpus that must be indexed before the load starts")
    parser.add_argument("--request-timeout", type=int, default=120)
    parser.add_argument("--chat-timeout", type=float, default=300, help="seconds to wait for one chat turn")
    parser.add_argument("--keep-kb", action="store_true", help="leave the benchmark KB in place afterwards")
    parser.add_argument("--output", type=Path, default=_IT_DIR / "reports" / "perf" / "load.json")
    parser.add_argument("--summary", type=Path, default=None, help="also write the Markdown summary here")
    args = parser.parse_args(argv)
    args.kinds_tuple = tuple(args.kinds.split(",")) if args.kinds else None
    try:
        args.stage_list = parse_stages(args.stages)
        args.think_time_range = parse_think_time(args.think_time)
    except ValueError as exc:
        parser.error(str(exc))
    if args.min_operations < 0:
        parser.error("--min-operations cannot be negative")
    if not 0 <= args.max_error_rate < 1:
        parser.error("--max-error-rate is a share from 0 up to (not including) 1")
    if not 0 < args.abort_error_rate <= 1:
        parser.error("--abort-error-rate is a share above 0 and at most 1")
    if not 0 < args.require_indexed <= 1:
        parser.error("--require-indexed must be a share above 0 and at most 1")
    return args


def write_outputs(result: dict[str, Any], args: argparse.Namespace) -> str:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = render_summary(result)
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(summary, encoding="utf-8")
    return summary


def main() -> int:
    args = parse_args()
    load_env()
    result = run_benchmark(args, args.stage_list)
    summary = write_outputs(result, args)
    print(summary)
    print(f"Result written to {args.output}")
    if not result["gate"]["passed"] and args.fail_on_violation:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
