"""Latency profiles on the fault-stack rig (QA-22), one per SLO row SLO.md gives a profile.

Each profile starts its own rig (``harness_rig.Rig``) with the scripted model at a fixed token rate, drives turns
through the product's chat routes as the web UI sends them (one ``POST …/stream``), and returns a result: samples,
statistics per metric (n, p50, p95, p99, max) and the highest one-minute load average seen while it ran.
``slo_gates.evaluate`` judges the results.

    python perf/bench_harness.py --profile modes --samples 200 --label <host> \\
        --mongo-uri 'mongodb://localhost:<mongo-port>/?replicaSet=rs0&directConnection=true' --redis-port <redis-port>

Model time is never estimated: the rig's model takes ``MODEL_DELAY_S`` to its first chunk and ``TOKEN_INTERVAL_S``
between chunks, so "minus the model" subtracts exactly what it took.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import statistics
import subprocess
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from commit_cpu import Window
from commit_cpu import metrics as commit_cpu_metrics
from harness_rig import (
    AGENT,
    FAULT_DIR,
    ORG,
    Infra,
    Rig,
    aborts_per_commit,
    commit_records,
    git_head,
    load1,
    new_user,
    node_events,
    stack,
)
from stack import percentile

MODEL_DELAY_S = 0.2
TOKEN_INTERVAL_S = 0.02
ANSWER_CHUNKS = 20
REPO = Path(__file__).resolve().parents[2]
PYTHON_ROOT = REPO / "backend" / "python"

# (route kind, chat mode) as the web UI sends them; "plain chat" is the built-in assistant's two durable modes.
MODES: tuple[tuple[str, str], ...] = (
    ("agent", "quick"), ("agent", "auto"), ("agent", "react"), ("agent", "deep"),
    ("chat", "internal_search"), ("chat", "web_search"),
)


def stats(values: list[float]) -> dict[str, float | int | None]:
    def r(v: float | None) -> float | None:
        return None if v is None else round(v, 1)

    return {"n": len(values), "p50": r(percentile(values, 50)), "p95": r(percentile(values, 95)),
            "p99": r(percentile(values, 99)), "max": r(max(values) if values else None)}


# ---- one turn as the web UI makes it ----


@dataclass
class Turn:
    """What the client saw of one turn; times in ms from the request (``t0``) and wall times for joins."""

    t0: float = field(default_factory=time.perf_counter)
    wall0: float = field(default_factory=time.time)
    status: int | None = None
    session_id: str | None = None
    run_id: str | None = None
    created_ms: float | None = None
    first_text_ms: float | None = None
    done_ms: float | None = None
    end: str | None = None
    last_id: str | None = None
    texts: list[tuple[float, str]] = field(default_factory=list)
    frames: list[dict[str, Any]] = field(default_factory=list)

    def ms(self) -> float:
        return (time.perf_counter() - self.t0) * 1000


def headers(user: str) -> dict[str, str]:
    return {"authorization": f"Bearer {ORG}.{user}", "content-type": "application/json"}


def stream_path(kind: str) -> str:
    return f"/api/v1/agents/{AGENT}/conversations/stream" if kind == "agent" else "/api/v1/conversations/stream"


def run_stream_path(kind: str, session_id: str, run_id: str) -> str:
    base = f"/api/v1/agents/{AGENT}/conversations" if kind == "agent" else "/api/v1/conversations"
    return f"{base}/{session_id}/runs/{run_id}/stream"


async def read_frames(
    response: httpx.Response, turn: Turn, on_frame: Callable[[dict[str, Any]], Awaitable[bool]] | None = None
) -> None:
    """Reads SSE frames into ``turn`` until the root run's terminal frame, or until ``on_frame`` says stop."""
    turn.status = response.status_code
    frame_id: str | None = None
    async for line in response.aiter_lines():
        if line.startswith("id: "):
            frame_id = line[4:]
            continue
        if not line.startswith("data: "):
            continue
        frame = json.loads(line[6:])
        now = turn.ms()
        turn.frames.append(frame)
        turn.last_id = frame_id or turn.last_id
        if frame.get("name") == "conversation_created" and turn.created_ms is None:
            turn.created_ms = now
            turn.session_id = frame["value"]["conversationId"]
            turn.run_id = frame["value"].get("runId")
        if frame.get("type") == "TEXT_MESSAGE_CONTENT" and frame.get("delta"):
            turn.texts.append((time.time(), str(frame["delta"])))
            if turn.first_text_ms is None:
                turn.first_text_ms = now
        if frame.get("type") in ("RUN_FINISHED", "RUN_ERROR") and not frame.get("parentRunId"):
            turn.done_ms, turn.end = now, frame["type"]
            return
        if on_frame is not None and await on_frame(frame):
            return


async def chat_turn(
    client: httpx.AsyncClient, kind: str, mode: str, query: str, user: str | None = None,
    on_frame: Callable[[dict[str, Any]], Awaitable[bool]] | None = None,
) -> Turn:
    turn = Turn()
    body = {"query": query, "chatMode": mode}
    async with client.stream("POST", stream_path(kind), json=body, headers=headers(user or new_user())) as response:
        await read_frames(response, turn, on_frame)
    return turn


# ---- profile plumbing ----

# PF-15: 0 -> 200 session creates in 60 s. 200 is also the release's active-runs cap per org
# (admission-quotas.ts), so every one is admitted and the worker's slots are what queues them.
BURST_SESSIONS = 200
BURST_WINDOW_S = 60.0
BURST_SEED = 15



@dataclass
class Options:
    infra: Infra
    out: Path
    samples: int
    think_s: float = 0.3
    # The soak's length (SLO #19: 24 h; a shorter one proves the job and its readings).
    soak_s: float = 24 * 3600.0
    # The burst's window (PF-15: 60 s); 0 starts every session in the same instant, which queues them on the slots.
    burst_window_s: float = BURST_WINDOW_S


@dataclass
class Result:
    profile: str
    metrics: dict[str, dict[str, Any]]
    samples: list[dict[str, Any]]
    load1_max: float
    extra: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {"profile": self.profile, "metrics": self.metrics, "load1_max": round(self.load1_max, 1),
                "samples": self.samples, **self.extra}


class Sampler:
    """Samples with the load average at each; ``metric`` collects a statistic's values."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.load_max = 0.0

    def add(self, row: dict[str, Any]) -> None:
        row["load1"] = round(load1(), 2)
        self.load_max = max(self.load_max, row["load1"])
        self.rows.append(row)

    def values(self, key: str, where: Callable[[dict[str, Any]], bool] = lambda _: True) -> list[float]:
        return [float(r[key]) for r in self.rows if r.get(key) is not None and where(r)]


def rig_for(options: Options, name: str, scenario: dict[str, Any]) -> Rig:
    base = {"modelDelayS": MODEL_DELAY_S, "tokenIntervalS": TOKEN_INTERVAL_S, "presentAnswer": True}
    return Rig(options.infra, options.out / name, {**base, **scenario})


def client_for(rig: Rig) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=rig.base_url, timeout=httpx.Timeout(120.0))


def model_time_ms(chunks: int) -> float:
    return (MODEL_DELAY_S + (chunks - 1) * TOKEN_INTERVAL_S) * 1000


def first_call_after(calls: list[dict[str, Any]], run_id: str, turn: int = 1) -> float | None:
    found = [c["t"] for c in calls if c["runId"] == run_id and c["turn"] == turn]
    return min(found) if found else None


def delta_latencies(turn: Turn, emitted: list[dict[str, Any]]) -> list[float]:
    """Each chunk's emission at the worker to the arrival of the frame that carried it (Node may coalesce)."""
    mine = [e for e in emitted if e["runId"] == turn.run_id]
    out: list[float] = []
    for event in mine:
        arrival = next((at for at, text in turn.texts if event["text"].strip() in text), None)
        if arrival is not None:
            out.append((arrival - event["t"]) * 1000)
    return out


# ---- profiles ----


async def profile_modes(options: Options) -> Result:
    """SLO #3, #4, #5 and the hand-off, per mode on the existing chat routes."""
    sampler = Sampler()
    with rig_for(options, "modes", {"deltaLog": True}) as rig:
        async with client_for(rig) as client:
            for kind, mode in MODES:
                for i in range(options.samples + 2):
                    await asyncio.sleep(options.think_s)
                    turn = await chat_turn(client, kind, mode, f"answer {ANSWER_CHUNKS}")
                    if i < 2:
                        continue
                    sampler.add({"mode": f"{kind}:{mode}", "run": turn.run_id, "wall0": turn.wall0,
                                 "created_ms": turn.created_ms, "first_text_ms": turn.first_text_ms,
                                 "done_ms": turn.done_ms, "end": turn.end, "texts": turn.texts})
        admissions = {e.get("runId"): e for e in node_events(rig.node_log(), "admission.accepted")}
        calls = rig.jsonl("model-calls.jsonl")
        emitted = rig.jsonl("deltas.jsonl")
    for row in sampler.rows:
        accepted = admissions.get(row["run"]) or {}
        row["admission_ms"] = accepted.get("ms")
        first = first_call_after(calls, row["run"]) if row["run"] else None
        row["handoff_ms"] = None if first is None or row["created_ms"] is None else (
            (first - row["wall0"]) * 1000 - row["created_ms"])
        if row["first_text_ms"] is not None:
            row["ttft_nonmodel_ms"] = row["first_text_ms"] - MODEL_DELAY_S * 1000
        if row["done_ms"] is not None:
            row["done_nonmodel_ms"] = row["done_ms"] - model_time_ms(ANSWER_CHUNKS)
        turn = Turn(run_id=row["run"], texts=[tuple(t) for t in row.pop("texts")])  # type: ignore[misc]
        row["delta_ms"] = delta_latencies(turn, emitted)
    metrics: dict[str, dict[str, Any]] = {}
    for key in ("created_ms", "admission_ms", "handoff_ms", "ttft_nonmodel_ms", "done_nonmodel_ms"):
        metrics[key] = stats(sampler.values(key))
        for kind, mode in MODES:
            name = f"{kind}:{mode}"
            metrics[f"{key}[{name}]"] = stats(sampler.values(key, lambda r, n=name: r["mode"] == n))
    deltas = [d for r in sampler.rows for d in r["delta_ms"]]
    metrics["delta_ms"] = stats(deltas)
    ends = sorted({str(r["end"]) for r in sampler.rows})
    return Result("modes", metrics, sampler.rows, sampler.load_max, {"ends": ends})


async def profile_cancel(options: Options) -> Result:
    """SLO #7 (Redis up): Stop at a random point of a long paced answer, and while a slow read runs."""
    sampler = Sampler()
    with rig_for(options, "cancel", {"slowToolS": 10, "tokenIntervalS": 0.05}) as rig:
        async with client_for(rig) as client:
            for i in range(options.samples + 2):
                for case, query, trigger in (("stream", "answer 200", "TEXT_MESSAGE_CONTENT"),
                                             ("slow_tool", "slow", "TOOL_CALL_START")):
                    await asyncio.sleep(options.think_s)
                    row = await _cancel_once(client, query, trigger, random.uniform(0.3, 3.0))
                    if i >= 2:
                        sampler.add({"case": case, **row})
    metrics = {"stop_ms": stats(sampler.values("stop_ms")), "ack_ms": stats(sampler.values("ack_ms"))}
    for case in ("stream", "slow_tool"):
        metrics[f"stop_ms[{case}]"] = stats(sampler.values("stop_ms", lambda r, c=case: r["case"] == c))
    return Result("cancel", metrics, sampler.rows, sampler.load_max)


async def _cancel_once(client: httpx.AsyncClient, query: str, trigger: str, after_s: float) -> dict[str, Any]:
    user = new_user()
    state: dict[str, Any] = {}
    task: asyncio.Task[None] | None = None
    turn = Turn()

    async def stop() -> None:
        await asyncio.sleep(after_s)
        state["t"] = time.perf_counter()
        path = f"/api/v1/agents/{AGENT}/conversations/{turn.session_id}/runs/{turn.run_id}/cancel"
        response = await client.post(path, json={"idempotencyKey": f"stop-{turn.run_id}"}, headers=headers(user))
        state["ack_ms"] = (time.perf_counter() - state["t"]) * 1000
        state["status"] = response.status_code

    async def on_frame(frame: dict[str, Any]) -> bool:
        nonlocal task
        if task is None and frame.get("type") == trigger:
            task = asyncio.create_task(stop())
        return False

    body = {"query": query, "chatMode": "quick"}
    async with client.stream("POST", stream_path("agent"), json=body, headers=headers(user)) as response:
        await read_frames(response, turn, on_frame)
    end = time.perf_counter()
    if task is not None:
        await task
    stopped = "t" in state and end >= state["t"]
    return {"run": turn.run_id, "end": turn.end, "after_s": round(after_s, 3), "ack_ms": state.get("ack_ms"),
            "cancel_status": state.get("status"), "stop_ms": (end - state["t"]) * 1000 if stopped else None}


async def profile_reconnect(options: Options) -> Result:
    """SLO #8: drop the stream mid-answer and reconnect with Last-Event-ID; a short run and a 500-call run."""
    sampler = Sampler()
    with rig_for(options, "reconnect", {"tokenIntervalS": 0.05}) as rig:
        async with client_for(rig) as client:
            for i in range(options.samples + 2):
                for case, query in (("short", "answer 100"), ("long", "reads 10 50 100")):
                    if case == "long" and i % 10:
                        continue
                    await asyncio.sleep(options.think_s)
                    row = await _reconnect_once(client, query, random.uniform(0.3, 2.5))
                    if i >= 2:
                        sampler.add({"case": case, **row})
    metrics = {"reconnect_ms": stats(sampler.values("reconnect_ms"))}
    for case in ("short", "long"):
        metrics[f"reconnect_ms[{case}]"] = stats(sampler.values("reconnect_ms", lambda r, c=case: r["case"] == c))
    return Result("reconnect", metrics, sampler.rows, sampler.load_max)


async def _reconnect_once(client: httpx.AsyncClient, query: str, after_s: float) -> dict[str, Any]:
    """The long case drops the stream ``after_s`` into its answer, once the 5,000 reads are journaled."""
    user = new_user()
    turn = Turn()
    started: float | None = None

    async def on_frame(frame: dict[str, Any]) -> bool:
        nonlocal started
        if frame.get("type") == "TEXT_MESSAGE_CONTENT" and started is None:
            started = time.perf_counter()
        return started is not None and time.perf_counter() - started >= after_s

    body = {"query": query, "chatMode": "quick"}
    async with client.stream("POST", stream_path("agent"), json=body, headers=headers(user)) as response:
        await read_frames(response, turn, on_frame)
    if turn.end is not None or turn.session_id is None or turn.run_id is None:
        return {"run": turn.run_id, "reconnect_ms": None, "note": f"ended before the drop ({turn.end})"}
    again = Turn()
    request_headers = {**headers(user), "last-event-id": turn.last_id or ""}
    path = run_stream_path("agent", turn.session_id, turn.run_id)
    first: float | None = None
    async with client.stream("GET", path, headers=request_headers) as response:
        async def first_frame(_: dict[str, Any]) -> bool:
            nonlocal first
            first = first if first is not None else again.ms()
            return False

        await read_frames(response, again, first_frame)
    return {"run": turn.run_id, "reconnect_ms": first, "end": again.end}


async def profile_mcp(options: Options) -> Result:
    """SLO #12: first token with 5 streamable-HTTP MCP servers discovered per run, against none."""
    sampler = Sampler()
    servers = _start_mcp_servers(options.out / "mcp", 5)
    try:
        # The MCP URL guard refuses loopback: the servers listen on the host's private address (WP88-MCP2).
        urls = [f"http://{stack.mcp_host()}:{port}/mcp" for port, _ in servers]
        for case, scenario in (("none", {}), ("five", {"mcpServers": urls})):
            with rig_for(options, f"mcp-{case}", scenario) as rig:
                async with client_for(rig) as client:
                    for i in range(options.samples + 2):
                        await asyncio.sleep(options.think_s)
                        turn = await chat_turn(client, "agent", "quick", "answer 4")
                        if i >= 2:
                            sampler.add({"case": case, "run": turn.run_id, "ttft_ms": turn.first_text_ms,
                                         "end": turn.end})
    finally:
        for _, process in servers:
            process.kill()
    none, five = (sampler.values("ttft_ms", lambda r, c=c: r["case"] == c) for c in ("none", "five"))
    metrics = {"ttft_ms[none]": stats(none), "ttft_ms[five]": stats(five)}
    added = {stat: (metrics["ttft_ms[five]"][stat] or 0) - (metrics["ttft_ms[none]"][stat] or 0)
             for stat in ("p50", "p95")}
    metrics["added_ttft_ms"] = {"n": min(len(none), len(five)), **{k: round(v, 1) for k, v in added.items()}}
    return Result("mcp-5-servers", metrics, sampler.rows, sampler.load_max)


def _start_mcp_servers(out: Path, count: int) -> list[tuple[int, subprocess.Popen[bytes]]]:
    import socket

    out.mkdir(parents=True, exist_ok=True)
    started: list[tuple[int, subprocess.Popen[bytes]]] = []
    for i in range(count):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        log = (out / f"mcp-{i}.log").open("ab")
        process = subprocess.Popen([sys.executable, str(FAULT_DIR / "mcp_server.py"), str(port), stack.mcp_host()],
                                   stdout=log,
                                   stderr=subprocess.STDOUT, env={**os.environ, "PYTHONPATH": str(PYTHON_ROOT)})
        started.append((port, process))
    for port, _ in started:
        _wait_port(port)
    return started


def _wait_port(port: int, timeout_s: float = 60.0) -> None:
    import socket

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            socket.create_connection((stack.mcp_host(), port), timeout=1).close()
            return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"MCP server on {port}")


async def profile_approval(options: Options) -> Result:
    """WRK-G2: "Allow" to the write running, the approval held in the run and from a suspended run."""
    sampler = Sampler()
    for case, hold in (("held", True), ("suspended", False)):
        with rig_for(options, f"approval-{case}", {"askWrites": True, "holdApprovals": hold}) as rig:
            async with client_for(rig) as client:
                for i in range(options.samples + 1):
                    await asyncio.sleep(options.think_s)
                    row = await _approve_once(client, rig)
                    if i >= 1:
                        sampler.add({"case": case, **row})
    metrics = {f"{case}_resume_ms": stats(sampler.values("resume_ms", lambda r, c=case: r["case"] == c))
               for case in ("held", "suspended")}
    return Result("approval", metrics, sampler.rows, sampler.load_max)


async def _approve_once(client: httpx.AsyncClient, rig: Rig) -> dict[str, Any]:
    user = new_user()
    state: dict[str, Any] = {}
    task: asyncio.Task[None] | None = None
    turn = Turn()

    async def decide(interaction: str) -> None:
        await asyncio.sleep(0.3)
        state["decided"] = time.time()
        path = (f"/api/v1/agents/{AGENT}/conversations/{turn.session_id}/runs/{turn.run_id}"
                f"/interactions/{interaction}/decision")
        response = await client.post(path, json={"decision": "approved", "idempotencyKey": uuid.uuid4().hex},
                                     headers=headers(user))
        state["status"] = response.status_code

    async def on_frame(frame: dict[str, Any]) -> bool:
        nonlocal task
        if frame.get("name") == "approval_requested" and task is None:
            task = asyncio.create_task(decide(frame["value"]["interactionId"]))
        return False

    async with client.stream("POST", stream_path("agent"), json={"query": "flow", "chatMode": "quick"},
                             headers=headers(user)) as response:
        await read_frames(response, turn, on_frame)
    if task is not None:
        await task
    if "decided" not in state:
        return {"run": turn.run_id, "resume_ms": None, "end": turn.end}
    # The write runs in the attempt that calls the model for turn 3 next; its saas effect is the "action running".
    effects = [e for e in rig.jsonl("saas.jsonl") if e["payload"].get("run") == turn.run_id]
    calls = [c["t"] for c in rig.jsonl("model-calls.jsonl") if c["runId"] == turn.run_id and c["turn"] == 3]
    ran = calls[0] if calls else None
    return {"run": turn.run_id, "end": turn.end, "decision_status": state.get("status"), "effects": len(effects),
            "resume_ms": None if ran is None else (ran - state["decided"]) * 1000}


async def profile_deep_wave(options: Options) -> Result:
    """CHD-G2: a deep run delegates 3 children in one wave; spawn → children, last child → parent."""
    sampler = Sampler()
    children = 3
    with rig_for(options, "deep-wave", {}) as rig:
        async with client_for(rig) as client:
            for i in range(options.samples + 2):
                await asyncio.sleep(options.think_s)
                turn = await chat_turn(client, "agent", "deep", f"wave {children}")
                if i >= 2:
                    sampler.add({"run": turn.run_id, "end": turn.end, "done_ms": turn.done_ms,
                                 "wall0": turn.wall0})
        calls = rig.jsonl("model-calls.jsonl")
    child_model_s = MODEL_DELAY_S + 3 * TOKEN_INTERVAL_S
    for row in sampler.rows:
        parent = [c for c in calls if c["runId"] == row["run"]]
        spawn = next((c["t"] for c in parent if c["turn"] == 1), None)
        resume = next((c["t"] for c in parent if c["turn"] == 2), None)
        kids = [c for c in calls if c["runId"] != row["run"] and spawn is not None and c["t"] > spawn
                and (resume is None or c["t"] < resume)]
        row["children"] = len({c["runId"] for c in kids})
        if spawn is not None and len({c["runId"] for c in kids}) == children and resume is not None:
            row["fanout_ms"] = (max(c["t"] for c in kids) - (spawn + MODEL_DELAY_S)) * 1000
            row["join_ms"] = (resume - (max(c["t"] for c in kids) + child_model_s)) * 1000
    metrics = {key: stats(sampler.values(key)) for key in ("fanout_ms", "join_ms")}
    return Result("deep-wave", metrics, sampler.rows, sampler.load_max)


async def profile_code_mode(options: Options) -> Result:
    """SLO #1, #9, #10 on one session's seq counter: 5,000 read calls, 10 runs of 10 turns of 50 parallel reads.

    Not one run: the loop's turn budget is 15 (``RUN_SPEC_BUDGETS``), and code mode's gateway RPC, which would make
    5,000 calls in one run, is not built (SLO #18). 50 reads a turn is one full commit (two events a call, 100 a
    commit); a wider turn is committed in parts since PERF-A (QA22-F1), and the shape is kept so the baselines stay
    comparable. Each sample is one such session."""
    runs, turns, calls_per_turn = 10, 10, 50
    sampler = Sampler()
    with rig_for(options, "code-mode-5k", {"modelDelayS": 0.0, "tokenIntervalS": 0.0}) as rig:
        async with client_for(rig) as client:
            for _ in range(max(1, options.samples)):
                user, session = new_user(), None
                for _ in range(runs):
                    turn = await session_turn(client, f"reads {turns} {calls_per_turn}", user, session)
                    session = turn.session_id
                    sampler.add({"run": turn.run_id, "session": session, "end": turn.end, "done_ms": turn.done_ms,
                                 "calls": turns * calls_per_turn})
                events = _journal(client_base=rig.base_url, session_id=session)
                for row in sampler.rows:
                    if row["session"] == session:
                        row.update(_journal_size([e for e in events if e.get("runId") == row["run"]]))
        commits = commit_records(rig.worker_log())
    return _commit_result("code-mode-5k", sampler, commits,
                          {"runs_per_session": runs, "turns": turns, "calls_per_turn": calls_per_turn})


async def session_turn(client: httpx.AsyncClient, query: str, user: str, session: str | None) -> Turn:
    """A turn of the agent's conversation ``session`` (a new one when ``None``), as the web UI sends a follow-up."""
    if session is None:
        return await chat_turn(client, "agent", "quick", query, user=user)
    turn = Turn()
    path = f"/api/v1/agents/{AGENT}/conversations/{session}/messages/stream"
    async with client.stream("POST", path, json={"query": query, "chatMode": "quick"}, headers=headers(user)) as response:
        await read_frames(response, turn)
    turn.session_id = turn.session_id or session
    return turn


N16_SESSIONS = 16
# Turns every N = 16 profile runs first and records nowhere (PERFC-F5).
N16_WARM_UP_TURNS = 1
# The corpus's read, write, answer turn as the fault worker scripts it (``flow``): seven commits a run.
FLOW_COMMITS = 7


def n16_turn_period_s(commits_per_turn: int = FLOW_COMMITS) -> float:
    """How often each session starts a turn for the rig to commit at the capacity model's rate.

    SLO #1 is stated "at the capacity model's N = 16 rate": N concurrent runs of a recording commit N x its commits
    over its duration (capacity-baseline.md). The busiest recording is taken, not the corpus mean, so the gate is
    measured at the highest rate the model has for N; the period does not depend on N."""
    sys.path.insert(0, str(PYTHON_ROOT))
    from tests.harness_replay.capacity import load_corpus

    busiest = max(load_corpus(), key=lambda p: len(p.commits) / p.duration_s)
    return commits_per_turn * busiest.duration_s / len(busiest.commits)


# Fixed, so two runs of the profile meet the same arrivals.
N16_ARRIVAL_SEED = 22


def n16_turn_starts(turns: int, period_s: float, seed: int = N16_ARRIVAL_SEED, *,
                    in_step: bool = False) -> list[list[float]]:
    """When each session starts each of its turns, in seconds from the profile's start.

    ``period_s`` > 0: sessions out of phase by chance (each a random phase of the period) and every turn off its
    slot by up to a quarter of the period, as independent users are; some turns arrive together, most do not.
    ``in_step``: every session starts its n-th turn at the same instant, a period apart (an open loop at the
    paced rate: scheduled runs firing in the same minute).
    0: every session back to back from the same instant, in step for the whole run, as fast as the rig answers."""
    if period_s <= 0:
        return [[0.0] * turns for _ in range(N16_SESSIONS)]
    if in_step:
        return [[turn * period_s for turn in range(turns)] for _ in range(N16_SESSIONS)]
    rng = random.Random(seed)
    starts: list[list[float]] = []
    for _ in range(N16_SESSIONS):
        phase = rng.uniform(0.0, period_s)
        starts.append([max(0.0, phase + turn * period_s + rng.uniform(-0.25, 0.25) * period_s) for turn in range(turns)])
    return starts


async def _n16_sessions(client: httpx.AsyncClient, sampler: Sampler, turns: int, period_s: float, *,
                        in_step: bool = False) -> None:
    """``N16_SESSIONS`` users, each a new conversation a turn, starting their turns at ``n16_turn_starts`` (a turn
    whose slot passed while the one before it ran starts at once)."""
    clock = asyncio.get_running_loop().time
    began = clock()

    async def session(starts: list[float]) -> None:
        user = new_user()
        for start in starts:
            wait = began + start - clock()
            if wait > 0:
                await asyncio.sleep(wait)
            turn = await chat_turn(client, "agent", "quick", "flow", user=user)
            sampler.add({"run": turn.run_id, "end": turn.end, "done_ms": turn.done_ms})

    await asyncio.gather(*(session(starts) for starts in n16_turn_starts(turns, period_s, in_step=in_step)))


async def n16_warm_up(client: httpx.AsyncClient, rig: Rig) -> tuple[frozenset[str], dict[str, int] | None]:
    """``N16_WARM_UP_TURNS`` turns of every session that no statistic counts (PERFC-F5): the org's first admissions
    create its data key, grow the connection pool and compile the path. Returns the warm-up's runs and MongoDB's
    transaction counts once it is over, which is where a profile's own counts begin."""
    warm_up = Sampler()
    await _n16_sessions(client, warm_up, N16_WARM_UP_TURNS, 0.0)
    return frozenset(str(row["run"]) for row in warm_up.rows), rig.transactions()


def n16_capacity_rate(period_s: float) -> float:
    """Commits a second of ``N16_SESSIONS`` sessions at the capacity model's pace."""
    return N16_SESSIONS * FLOW_COMMITS / period_s


def run_accounting(commits: list[dict[str, Any]], runs: set[str], expected: int,
                   unrecorded: frozenset[str] = frozenset()) -> dict[str, dict[str, Any]]:
    """Whether every turn became exactly one run that ended. Lost: turns asked for with no ended run of their own.
    Duplicated: a run that ended twice, or an ended run no turn asked for (``unrecorded``: the warm-up's)."""
    ended = [c["runId"] for c in commits if c["kind"] == "complete" and c["runId"] not in unrecorded]
    lost = expected - len(set(ended) & runs)
    duplicated = (len(ended) - len(set(ended))) + len(set(ended) - runs)
    return {"runs_lost": stats([float(max(0, lost))]), "runs_duplicated": stats([float(duplicated)])}


async def profile_commits_n16_instep(options: Options) -> Result:
    """SLO #1 and SLO #4 with the sessions in step: an open loop at the capacity model's N = 16 rate in which all
    16 sessions of one org start every turn in the same instant (scheduled runs firing in the same minute). The
    scripted model answers at fixed times, so their commits arrive together too. One unrecorded turn first: the
    org's first admissions create its data key and grow the connection pool, which no SLO describes."""
    sampler = Sampler()
    period_s = n16_turn_period_s()
    turns = max(1, options.samples)
    with rig_for(options, "commits-n16-instep", {}) as rig:
        async with client_for(rig) as client:
            unrecorded, before = await n16_warm_up(client, rig)
            started = time.time()
            await _n16_sessions(client, sampler, turns, period_s, in_step=True)
            wall_s = time.time() - started
            aborts = aborts_per_commit(before, rig.transactions())
        commits = commit_records(rig.worker_log())
        admitted = node_events(rig.node_log(), "admission.accepted")
    result = _commit_result("commits-n16-instep", sampler, commits,
                            {"sessions": N16_SESSIONS, "turn_period_s": round(period_s, 2)})
    runs = {row["run"] for row in sampler.rows}
    result.metrics["admission_ms"] = stats([float(e["ms"]) for e in admitted if e.get("runId") in runs and "ms" in e])
    result.metrics.update(run_accounting(commits, runs, N16_SESSIONS * turns, unrecorded))
    if aborts is not None:
        result.metrics["aborts_per_commit"] = stats([aborts])
    result.extra["commits_per_s"] = round(result.metrics["commit_ms"]["n"] / wall_s, 1)
    return result


async def profile_commits_n16(options: Options) -> Result:
    """SLO #1 at the capacity model's N = 16 rate: 16 sessions of the corpus's read, write, answer turn, paced."""
    sampler = Sampler()
    period_s = n16_turn_period_s()
    with rig_for(options, "commits-n16", {}) as rig:
        async with client_for(rig) as client:
            started = time.time()
            await _n16_sessions(client, sampler, max(1, options.samples), period_s)
            wall_s = time.time() - started
        commits = commit_records(rig.worker_log())
    result = _commit_result("commits-n16", sampler, commits,
                            {"sessions": N16_SESSIONS, "turn_period_s": round(period_s, 2)})
    result.extra["commits_per_s"] = round(result.metrics["commit_ms"]["n"] / wall_s, 1)
    return result


async def profile_commits_n16_burst(options: Options) -> Result:
    """Overload: 16 sessions of one org started together and run back to back with no think time, a closed loop
    that drives one Node process as fast as it answers (several times the capacity model's N = 16 rate). Its
    latency is queueing at saturation, which SLO #1 and SLO #4 do not describe (SLO.md); it is held to what
    overload must not do: transactions aborting each other (``aborts_per_commit``; PERFB-F1 was 13 to 15 a commit),
    throughput collapsing (``capacity_multiple``), admission taking longer than a user waits, a run lost or
    duplicated. ``N16_WARM_UP_TURNS`` unrecorded turn first, as the in-step profile has (PERFC-F5): the org's first
    admissions create its data key, grow the connection pool and compile the path, which is start-up, not
    overload. Nothing of the warm-up is in any statistic, the throughput and the transaction counts included."""
    sampler = Sampler()
    turns = max(1, options.samples)
    with rig_for(options, "commits-n16-burst", {}) as rig:
        async with client_for(rig) as client:
            unrecorded, before = await n16_warm_up(client, rig)
            started = time.time()
            await _n16_sessions(client, sampler, turns, 0.0)
            wall_s = time.time() - started
            aborts = aborts_per_commit(before, rig.transactions())
        commits = commit_records(rig.worker_log())
        admitted = node_events(rig.node_log(), "admission.accepted")
    result = _commit_result("commits-n16-burst", sampler, commits,
                            {"sessions": N16_SESSIONS, "warm_up_turns": N16_WARM_UP_TURNS})
    runs = {row["run"] for row in sampler.rows}
    result.metrics["admission_ms"] = stats([float(e["ms"]) for e in admitted if e.get("runId") in runs and "ms" in e])
    if aborts is not None:
        result.metrics["aborts_per_commit"] = stats([aborts])
    result.metrics.update(run_accounting(commits, runs, N16_SESSIONS * turns, unrecorded))
    commits_per_s = result.metrics["commit_ms"]["n"] / wall_s
    result.metrics["capacity_multiple"] = stats([commits_per_s / n16_capacity_rate(n16_turn_period_s())])
    result.extra["commits_per_s"] = round(commits_per_s, 1)
    return result


def burst_starts(sessions: int = BURST_SESSIONS, window_s: float = BURST_WINDOW_S, seed: int = BURST_SEED) -> list[float]:
    """When each session is created, in seconds from the burst's start: independent arrivals over the window, the
    first at 0, the same every run."""
    rng = random.Random(seed)
    starts = sorted(rng.uniform(0.0, window_s) for _ in range(sessions - 1))
    return [0.0, *starts]


def server_errors(statuses: list[int | None]) -> int:
    """Answers the server failed (5xx). A quota's 429 is admission saying no, which PF-15 allows; a turn with no
    answer at all is a run lost, counted there."""
    return sum(1 for status in statuses if status is not None and status >= 500)


def burst_client(rig: Rig) -> httpx.AsyncClient:
    """Every session's stream open at once: more connections than ``client_for``'s pool has."""
    return httpx.AsyncClient(base_url=rig.base_url, timeout=httpx.Timeout(300.0),
                             limits=httpx.Limits(max_connections=BURST_SESSIONS + 20,
                                                 max_keepalive_connections=BURST_SESSIONS + 20))


async def profile_burst(options: Options) -> Result:
    """PF-15 (C7.12): 200 users each start a conversation within one minute, each the corpus's read, write, answer
    turn. Held to what a burst must not do: admission slower than SLO #4's target at p95, a 5xx, a turn whose run
    never ended or ended twice. The wait for a worker slot (``queue_ms``: the turn's conversation created to its
    first model call) and the throughput are reported, not gated. ``N16_WARM_UP_TURNS`` unrecorded turns first."""
    sampler = Sampler()
    with rig_for(options, "burst-200", {}) as rig:
        async with burst_client(rig) as client:
            unrecorded, _ = await n16_warm_up(client, rig)
            began = asyncio.get_running_loop().time()

            async def session(start: float) -> None:
                await asyncio.sleep(max(0.0, began + start - asyncio.get_running_loop().time()))
                try:
                    turn = await chat_turn(client, "agent", "quick", "flow")
                except httpx.HTTPError as exc:
                    sampler.add({"run": None, "end": None, "status": None, "error": type(exc).__name__})
                    return
                sampler.add({"run": turn.run_id, "end": turn.end, "status": turn.status, "wall0": turn.wall0,
                             "created_ms": turn.created_ms, "done_ms": turn.done_ms})

            started = time.time()
            await asyncio.gather(*(session(start) for start in burst_starts(window_s=options.burst_window_s)))
            wall_s = time.time() - started
        commits = commit_records(rig.worker_log())
        admitted = {e.get("runId"): e for e in node_events(rig.node_log(), "admission.accepted")}
        calls = rig.jsonl("model-calls.jsonl")
    runs = {str(r["run"]) for r in sampler.rows if r["run"]}
    for row in sampler.rows:
        first = first_call_after(calls, row["run"]) if row["run"] else None
        row["queue_ms"] = None if first is None or row.get("created_ms") is None else (
            (first - row["wall0"]) * 1000 - row["created_ms"])
        row["admission_ms"] = (admitted.get(row["run"]) or {}).get("ms")
    metrics: dict[str, dict[str, Any]] = {
        "admission_ms": stats(sampler.values("admission_ms")),
        "created_ms": stats(sampler.values("created_ms")),
        "queue_ms": stats(sampler.values("queue_ms")),
        "done_ms": stats(sampler.values("done_ms")),
        "server_errors": stats([float(server_errors([r["status"] for r in sampler.rows]))]),
    }
    metrics.update(run_accounting(commits, runs, BURST_SESSIONS, unrecorded))
    ends = sorted({str(r["end"]) for r in sampler.rows})
    return Result("burst-200", metrics, sampler.rows, sampler.load_max,
                  {"sessions": BURST_SESSIONS, "window_s": options.burst_window_s, "wall_s": round(wall_s, 1),
                   "runs_per_s": round(len(runs) / wall_s, 2), "ends": ends})


# ---- PF-14 / SLO #19: the soak ----

# 60% of the capacity model's N = 16 (PF-14 "at 60% capacity").
SOAK_SESSIONS = 10
SOAK_SAMPLE_S = 30.0
# Every n-th turn of a session is a slow read the user stops part way (the cancel path in the mix); a streamed
# answer is no use for it, the rig presents its answer whole at the end (``presentAnswer``).
SOAK_CANCEL_EVERY = 10
SOAK_SLOW_TOOL_S = 10.0
_SOAK_STATE = ("const d = db.getSiblingDB('{db}'); const now = Date.now(); print(JSON.stringify({{"
               "orphanRuns: d.agentRuns.countDocuments({{status: {{$nin: ['completed', 'failed', 'cancelled']}}}}), "
               "ownerlessLeases: d.agentRuns.countDocuments({{status: {{$in: ['running', 'cancelling']}}, "
               "$or: [{{leaseExpiresAt: null}}, {{leaseExpiresAt: {{$lt: now}}}}]}})}}))")


def growth(samples: list[float]) -> float | None:
    """How much a process's reading grew over a soak: the median of the last tenth of the samples over the median
    of the tenth after the first (the first is start-up: imports, pools and caches filling). None: too few."""
    tenth = len(samples) // 10
    if tenth < 1:
        return None
    baseline = statistics.median(samples[tenth : 2 * tenth])
    end = statistics.median(samples[-tenth:])
    return (end - baseline) / baseline if baseline else None


def soak_state(printed: str, outbox: dict[str, int]) -> dict[str, int] | None:
    """What a drained soak left: runs not ended, worker-owned runs whose lease nobody holds, outbox rows not
    published (``_SOAK_STATE``'s line and Node's outbox counts). None when MongoDB could not be read."""
    for line in reversed(printed.strip().splitlines()):
        try:
            state = json.loads(line)
        except ValueError:
            continue
        if isinstance(state, dict) and {"orphanRuns", "ownerlessLeases"} <= state.keys():
            stuck = sum(int(outbox.get(k, 0)) for k in ("pending", "publishing", "failed"))
            return {"orphan_runs": int(state["orphanRuns"]), "ownerless_leases": int(state["ownerlessLeases"]),
                    "stuck_outbox": stuck}
    return None


def _held(pid: int) -> dict[str, float] | None:
    """A process's resident set (MiB) and open files, from /proc; None once it is gone. The worker marks itself
    non-dumpable (``mark_process_non_dumpable``), so its open files are not listable by another process: its
    reading has no ``fds``."""
    try:
        status = Path(f"/proc/{pid}/status").read_text()
    except OSError:
        return None
    rss_kb = next(int(line.split()[1]) for line in status.splitlines() if line.startswith("VmRSS:"))
    reading = {"rss_mb": rss_kb / 1024}
    with contextlib.suppress(OSError):
        reading["fds"] = float(len(os.listdir(f"/proc/{pid}/fd")))
    return reading


async def profile_soak(options: Options) -> Result:
    """SLO #19 / PF-14 (C7.11): ``SOAK_SESSIONS`` users for ``options.soak_s``, each paced at the capacity model's
    N = 16 turn period, mixing the read, write, answer turn, a streamed answer and a stopped long answer. Node's
    and the worker's resident set and open files are read every ``SOAK_SAMPLE_S``. Then the load stops, the
    worker's leases run out (two lease lengths), and what is left is counted: runs not ended, worker-owned runs
    whose lease nobody holds, outbox rows not published. The rig's runs are loop runs (no sandbox): orphaned
    sandboxes are the broker reaper's, gated by its own suite."""
    sampler = Sampler()
    period_s = n16_turn_period_s()
    held: dict[str, list[dict[str, float]]] = {"node": [], "worker": []}
    lease_s = 30.0
    with rig_for(options, "soak", {"leaseMs": int(lease_s * 1000), "slowToolS": SOAK_SLOW_TOOL_S}) as rig:
        async with client_for(rig) as client:
            unrecorded, _ = await n16_warm_up(client, rig)
            loop = asyncio.get_running_loop()
            began = loop.time()
            deadline = began + options.soak_s
            rng = random.Random(N16_ARRIVAL_SEED)

            async def session(phase: float) -> None:
                user = new_user()
                n = 0
                next_at = began + phase
                while next_at < deadline:
                    await asyncio.sleep(max(0.0, next_at - loop.time()))
                    n += 1
                    if n % SOAK_CANCEL_EVERY == 0:
                        row = await _cancel_once(client, "slow", "TOOL_CALL_START", rng.uniform(0.3, 2.0))
                        sampler.add({"kind": "cancel", **row})
                    else:
                        query = "flow" if n % 2 else f"answer {ANSWER_CHUNKS}"
                        turn = await chat_turn(client, "agent", "quick", query, user=user)
                        sampler.add({"kind": query.split()[0], "run": turn.run_id, "end": turn.end,
                                     "status": turn.status, "done_ms": turn.done_ms})
                    next_at += period_s

            async def watch() -> None:
                while loop.time() < deadline:
                    for name, pid in rig.process_ids().items():
                        reading = _held(pid)
                        if reading is not None:
                            held[name].append({"t": round(loop.time() - began, 1), **reading})
                    await asyncio.sleep(SOAK_SAMPLE_S)

            await asyncio.gather(watch(), *(session(rng.uniform(0.0, period_s)) for _ in range(SOAK_SESSIONS)))
            await asyncio.sleep(2 * lease_s)
            outbox = (await client.get("/__fault/outbox")).json()["backlog"]
        printed = rig.mongo_eval(_SOAK_STATE.format(db=rig.database))
        left = soak_state(printed or "", outbox)
        commits = commit_records(rig.worker_log())
    answered = [r for r in sampler.rows if r["kind"] != "cancel"]
    runs = {str(r["run"]) for r in answered if r.get("run")}
    metrics: dict[str, dict[str, Any]] = {
        "done_ms": stats(sampler.values("done_ms")),
        "server_errors": stats([float(server_errors([r.get("status") for r in answered]))]),
    }
    stops = [r for r in sampler.rows if r["kind"] == "cancel"]
    stopped = frozenset(str(r["run"]) for r in stops if r.get("run"))
    # Reported, not gated (SLO #7 is the cancel profile's): that the mix's stops stopped their runs.
    metrics["stop_ms"] = stats([float(r["stop_ms"]) for r in stops if r.get("stop_ms") is not None])
    metrics["stops_refused"] = stats([float(sum(1 for r in stops if r.get("cancel_status") != 200))])
    metrics.update(run_accounting(commits, runs, len(answered), unrecorded | stopped))
    if left is not None:
        metrics.update({key: stats([float(value)]) for key, value in left.items()})
    for name, readings in held.items():
        for key, label in (("rss_mb", "rss"), ("fds", "fd")):
            values = [r[key] for r in readings if key in r]
            if not values:
                continue
            change = growth(values)
            # In percent: ``stats`` keeps one decimal, which a fraction's 0.14 would round to the bound.
            metrics[f"{label}_growth[{name}]"] = stats([] if change is None else [change * 100])
            metrics[f"{key}[{name}]"] = stats(values)
    return Result("soak", metrics, sampler.rows, sampler.load_max,
                  {"sessions": SOAK_SESSIONS, "soak_s": options.soak_s, "turn_period_s": round(period_s, 2),
                   "held": held, "outbox": outbox, "left": left})


# One session at a time, with a model that answers at once: the Node process is then busy for about half the
# wall time, which keeps its caches warm without two requests' work overlapping more than a worker's own does.
CPU_WARM_TURNS = 60
# Fewer turns than this do not average a turn's CPU out; the other profiles' ``--samples`` are often 10.
CPU_MIN_TURNS = 60


async def _calibration_us(client: httpx.AsyncClient, spent: dict[str, float]) -> float:
    """One reading of the calibration unit; its own CPU and time are added to ``spent``, for they are no turn's."""
    before = (await client.get("/__fault/cpu")).json()
    reading = float((await client.get("/__fault/cpu/calibration")).json()["us"])
    after = (await client.get("/__fault/cpu")).json()
    for key in spent:
        spent[key] += after[key] - before[key]
    return reading


async def _cpu_window(options: Options, name: str, turns: int, *, attribute: bool) -> Window:
    """``turns`` turns of the corpus's read, write, answer turn after a warm-up, as the Node's ledger counted them.
    The plain window reads the calibration unit before every turn and keeps the mean: one reading is as noisy as
    the host is at that moment, and the turns are spread over the same moments. The attributed window reads none,
    since its callbacks would be charged to the background."""
    rig = Rig(options.infra, options.out / name, {"modelDelayS": 0.0, "tokenIntervalS": 0.0, "presentAnswer": True},
              node_env={"FAULT_CPU_ATTRIBUTE": "true"} if attribute else {})
    with rig:
        async with client_for(rig) as client:
            for _ in range(CPU_WARM_TURNS):
                await chat_turn(client, "agent", "quick", "flow")
            await client.post("/__fault/cpu/reset")
            await asyncio.sleep(5)
            idle = (await client.get("/__fault/cpu")).json()
            await client.post("/__fault/cpu/reset")
            calibrations: list[float] = []
            spent = {"loopUs": 0.0, "wallMs": 0.0}
            for _ in range(turns):
                if not attribute:
                    calibrations.append(await _calibration_us(client, spent))
                await chat_turn(client, "agent", "quick", "flow")
            run = (await client.get("/__fault/cpu")).json()
    for key, value in spent.items():
        run[key] -= value
    return Window.of(turns, idle, run, sum(calibrations) / len(calibrations) if calibrations else 0.0)


async def profile_commit_cpu(options: Options) -> Result:
    """PERFC-F1: the event-loop CPU of one admission, one step commit and one ending commit on one Node process,
    and the store commands each sends (``commit_cpu.py``). Two runs: one plain for the total, one in which Node
    charges every callback to its request. No latency is read: the numbers hold on a loaded host, up to what the
    calibration unit takes out."""
    sampler = Sampler()
    turns = max(CPU_MIN_TURNS, options.samples)
    sampler.add({"window": "plain"})
    total = await _cpu_window(options, "commit-cpu", turns, attribute=False)
    sampler.add({"window": "attributed"})
    attributed = await _cpu_window(options, "commit-cpu-attributed", turns, attribute=True)
    sampler.add({"window": "end"})
    return Result("commit-cpu", commit_cpu_metrics(total, attributed), sampler.rows, sampler.load_max,
                  {"turns": turns, "warm_up_turns": CPU_WARM_TURNS})


def _journal(client_base: str, session_id: str | None) -> list[dict[str, Any]]:
    if session_id is None:
        return []
    response = httpx.get(f"{client_base}/__fault/journal/{ORG}/{session_id}", headers=headers(new_user()),
                         timeout=120)
    return list(response.json().get("events") or [])


def _journal_size(events: list[dict[str, Any]]) -> dict[str, Any]:
    sizes = [len(json.dumps(e, separators=(",", ":")).encode()) for e in events]
    return {"journal_events": len(events), "inline_bytes": sum(sizes),
            "largest_event_bytes": max(sizes) if sizes else 0}


def commit_excess(commits: list[dict[str, Any]], tool_calls_per_turn: int) -> list[float]:
    """Per run: its commits minus SLO #9's allowance, 2 + 2 x tools for each model turn that called tools."""
    by_run: dict[str, list[dict[str, Any]]] = {}
    for commit in commits:
        by_run.setdefault(commit["runId"], []).append(commit)
    out: list[float] = []
    for rows in by_run.values():
        tool_turns = sum(1 for c in rows if c["kind"] == "model") - 1
        allowance = max(1, tool_turns) * (2 + 2 * tool_calls_per_turn) + 2
        out.append(float(len(rows) - allowance))
    return out


def _commit_result(name: str, sampler: Sampler, commits: list[dict[str, Any]], extra: dict[str, Any]) -> Result:
    runs = {r["run"] for r in sampler.rows}
    mine = [c for c in commits if c["runId"] in runs]
    metrics: dict[str, dict[str, Any]] = {"commit_ms": stats([c["ms"] for c in mine])}
    for kind in sorted({c["kind"] for c in mine}):
        metrics[f"commit_ms[{kind}]"] = stats([c["ms"] for c in mine if c["kind"] == kind])
    per_turn = int(extra.get("calls_per_turn", 2))
    metrics["commit_excess"] = stats(commit_excess(mine, per_turn))
    metrics["commits_per_run"] = stats([float(sum(1 for c in mine if c["runId"] == r)) for r in runs])
    if any("inline_bytes" in r for r in sampler.rows):
        metrics["inline_kb"] = stats([r["inline_bytes"] / 1024 for r in sampler.rows])
        metrics["largest_event_kb"] = stats([r["largest_event_bytes"] / 1024 for r in sampler.rows])
        projected = _projected_inline_kb(int(extra["turns"]), per_turn)
        metrics["inline_ratio"] = stats([r["inline_bytes"] / 1024 / projected for r in sampler.rows])
        extra = {**extra, "projected_inline_kb": round(projected, 1)}
    return Result(name, metrics, sampler.rows, sampler.load_max, extra)


def _projected_inline_kb(turns: int, calls: int) -> float:
    """capacity.py's projection of the same run (SLO #10's baseline for the scenario)."""
    sys.path.insert(0, str(PYTHON_ROOT))
    from tests.harness_replay.capacity import project_loop_reads

    return project_loop_reads(turns, calls).inline_bytes / 1024


async def profile_sandbox(options: Options) -> Result:
    """SLO #17 tier 1: the Docker broker (runc, hardened), acquire to the first command finished, cold and warm."""
    report = options.out / "sandbox" / "report.jsonl"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.unlink(missing_ok=True)
    loads: list[float] = [load1()]
    env = {**os.environ, "QA22_SANDBOX_SAMPLES": str(options.samples), "QA22_SANDBOX_REPORT": str(report),
           "PYTHONPATH": str(PYTHON_ROOT)}
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-m", "docker",
                           "tests/unit/harness/sandbox_broker/test_qa22_sandbox_start.py"],
                          cwd=PYTHON_ROOT, env=env, capture_output=True, text=True, check=False)
    loads.append(load1())
    rows = [json.loads(line) for line in report.read_text().splitlines()] if report.exists() else []
    metrics = {f"{r['lane']}_ms": stats([float(v) for v in r["ready_ms"]]) for r in rows}
    loads += [float(v) for r in rows for v in r.get("load1", [])]
    return Result("sandbox", metrics, rows, max(loads),
                  {"pytest": done.stdout[-2000:] if done.returncode else "passed"})


async def profile_resume(options: Options) -> Result:
    """PF-03 (C7.06): a run's next attempt resumed from its sandbox snapshot on the release engine image, end to end
    (acquire to the engine's session open, and to its first tool use), cold and with a warm pool
    (``test_pf03_resume_e2e.py``; ``--samples`` resumes per lane, 20 at least). Reported; not a gate while CI has no
    engine image to run it on."""
    report = options.out / "resume" / "report.jsonl"
    rows, pytest_tail, loads = _gate_lane(
        "tests/unit/harness/engines/test_pf03_resume_e2e.py", {"PF03_REPORT": str(report),
                                                               "PF03_SAMPLES": str(max(20, options.samples))}, report
    )
    metrics: dict[str, dict[str, Any]] = {}
    for row in rows:
        metrics[f"ready_ms[{row['lane']}]"] = stats([float(v) for v in row["ready_ms"]])
        metrics[f"first_tool_ms[{row['lane']}]"] = stats([float(v) for v in row["first_tool_ms"]])
    return Result("resume", metrics, [{k: v for k, v in r.items() if not k.endswith("_ms")} for r in rows],
                  max(loads), {"pytest": pytest_tail})


def _gate_lane(test: str, env: dict[str, str], report: Path) -> tuple[list[dict[str, Any]], str, list[float]]:
    """A ``slow`` pytest gate run of the backend (its JSON-line report, pytest's tail if it failed, the loads)."""
    report.parent.mkdir(parents=True, exist_ok=True)
    report.unlink(missing_ok=True)
    loads = [load1()]
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-m", "slow", test],
                          cwd=PYTHON_ROOT, env={**os.environ, **env, "PYTHONPATH": str(PYTHON_ROOT)},
                          capture_output=True, text=True, check=False)
    loads.append(load1())
    rows = [json.loads(line) for line in report.read_text().splitlines()] if report.exists() else []
    loads += [float(r["load1_max"]) for r in rows if "load1_max" in r]
    return rows, done.stdout[-2000:] if done.returncode else "passed", loads


@contextlib.contextmanager
def _gate_mongo_uri(options: Options) -> Iterator[str]:
    """The replica set the rig's Node keeps its journal on: the given one, or the fault project's for the lane."""
    if options.infra.mongo_uri is not None:
        yield options.infra.mongo_uri
        return
    stack.configure(options.infra.port_base)
    stack.infrastructure_up(options.infra.broker)
    try:
        yield str(stack.stack_env(options.infra.broker, "unused")["MONGO_URI"])
    finally:
        stack.infrastructure_down()


async def profile_tool_gateway(options: Options) -> Result:
    """PF-05: the tool gateway's overhead (auth, grant, policy, audit; a null tool) at 50 calls a second over one
    worker's engine runs, the audit on Node's MongoDB journal (``test_pf05_tool_gateway_overhead.py``)."""
    report = options.out / "tool-gateway" / "report.jsonl"
    with _gate_mongo_uri(options) as uri:
        rows, pytest_tail, loads = _gate_lane(
            "tests/unit/harness/perf/test_pf05_tool_gateway_overhead.py",
            {"PF05_REPORT": str(report), "PF05_MONGO_URI": uri},
            report,
        )
    metrics: dict[str, dict[str, Any]] = {}
    for row in rows:
        metrics["overhead_ms"] = stats([float(v) for v in row["samples"]])
        metrics["calls_per_s"] = {"n": row["n"], "mean": row["rate"]}
    return Result("tool-gateway", metrics, [{k: v for k, v in r.items() if k != "samples"} for r in rows], max(loads),
                  {"pytest": pytest_tail})


async def profile_model_gateway(options: Options) -> Result:
    """PF-07: what the model gateway adds to an engine's time to first token, per engine wire, its budget
    reservation a round trip to Node's ledger (``test_pf07_model_gateway_ttft.py``)."""
    report = options.out / "model-gateway" / "report.jsonl"
    rows, pytest_tail, loads = _gate_lane(
        "tests/unit/harness/perf/test_pf07_model_gateway_ttft.py", {"PF07_REPORT": str(report)}, report
    )
    metrics = {f"added_ms[{r['lane']}]": stats([float(v) for v in r["samples"]]) for r in rows}
    return Result("model-gateway", metrics, [{k: v for k, v in r.items() if k != "samples"} for r in rows],
                  max(loads), {"pytest": pytest_tail})


async def profile_engine_latency(options: Options) -> Result:
    """DP8-4, DP8-8, DP8-9: per engine on its real binary, a turn's first event, a sandbox event's lag to the
    controller, an interrupt's stop and a stalled engine's kill (``test_dp8_engine_latency.py``; ``--samples``
    turns of each, 20 at least). Reported; not a gate while CI has no engine image to run it on."""
    report = options.out / "engine-latency" / "report.jsonl"
    rows, pytest_tail, loads = _gate_lane(
        "tests/unit/harness/engines/test_dp8_engine_latency.py",
        {"DP8_REPORT": str(report), "DP8_SAMPLES": str(max(20, options.samples))}, report,
    )
    measured = ("first_event_ms", "lag_ms", "stop_ms", "kill_stop_ms", "kill_ms")
    metrics = {f"{name}[{row['lane']}]": stats([float(v) for v in row[name]]) for row in rows for name in measured}
    return Result("engine-latency", metrics, [{k: v for k, v in r.items() if k not in measured} for r in rows],
                  max(loads), {"pytest": pytest_tail})


PROFILES: dict[str, Callable[[Options], Awaitable[Result]]] = {
    "code-mode-5k": profile_code_mode,
    "commits-n16": profile_commits_n16,
    "commits-n16-burst": profile_commits_n16_burst,
    "commits-n16-instep": profile_commits_n16_instep,
    "commit-cpu": profile_commit_cpu,
    "modes": profile_modes,
    "cancel": profile_cancel,
    "reconnect": profile_reconnect,
    "mcp-5-servers": profile_mcp,
    "approval": profile_approval,
    "deep-wave": profile_deep_wave,
    "sandbox": profile_sandbox,
    "resume": profile_resume,
    "tool-gateway": profile_tool_gateway,
    "burst-200": profile_burst,
    "soak": profile_soak,
    "model-gateway": profile_model_gateway,
    "engine-latency": profile_engine_latency,
}


def environment(label: str) -> dict[str, Any]:
    import platform

    return {"label": label, "host": platform.platform(), "cpus": os.cpu_count(), "head": git_head(),
            "model_delay_ms": MODEL_DELAY_S * 1000, "token_interval_ms": TOKEN_INTERVAL_S * 1000,
            "at": datetime.now(timezone.utc).isoformat()}


async def run_profiles(names: list[str], options: Options) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for name in names:
        started = time.time()
        result = await PROFILES[name](options)
        results[name] = {**result.as_json(), "wall_s": round(time.time() - started, 1)}
        print(f"{name}: done in {results[name]['wall_s']} s, load max {result.load1_max}", file=sys.stderr, flush=True)
    return results


__all__ = ["BURST_SESSIONS", "BURST_WINDOW_S", "MODES", "PROFILES", "burst_starts", "growth", "server_errors",
           "soak_state", "Options", "Result", "commit_excess", "delta_latencies", "environment",
           "n16_warm_up", "run_profiles", "stats"]
