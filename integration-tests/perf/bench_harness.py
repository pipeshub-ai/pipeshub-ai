"""Agent harness non-model overhead (SLO #2): durable route vs legacy route on one stack.

Both routes run against the same running stack, the same agent and the same
prompt. The org's harness Labs flag is switched between blocks of samples, so
runner noise lands on both routes alike. The model is a scripted
OpenAI-compatible server inside this process with a fixed latency per call, and
every call it serves is timed here, so "minus the model" is subtracted from what
the model actually took, not from a nominal figure.

``short-turn`` profile: one question, one read tool call (the toolset catalog,
which every agent has), one streamed answer. Per sample it records the time to
the first answer token (TTFT) and to the root ``RUN_FINISHED`` frame (turn
completed), each minus the model time spent before that point.

``saturate`` profile (SLO #20, G-45/GA-24): scheduled runs, each a webhook schedule fired once whose model asks
the read tool ``--background-steps`` times at ``--background-latency-ms`` a call, fill the stack; then
``--samples`` interactive turns are admitted one every ``--turn-interval`` seconds among them. Session ready is
admission to the turn's first model call. Gate: its p95 within ``--ready-slo-ms`` (5 s, the warm-pool target)
while every scheduled run completes. Start the stack with few slots and a provider limit so the load saturates it,
for example ``MODEL_GATEWAY_PROVIDER_CONCURRENCY=4`` and a worker of four slots; the result reports how many
scheduled runs the model saw at once.

    python perf/bench_harness.py --profile saturate --base-url http://localhost:3000 --label my-host \\
        --output reports/perf/harness-saturate.json

Gate (docs/agent-harness-v2/SLO.md #2): an absolute budget. Harness p50 may add
at most ``--ttft-budget-ms`` to legacy p50 at TTFT and ``--done-budget-ms`` at
turn completed; with ``--container``, every durable commit kind's p50 must also
stay within ``--commit-budget-ms``. The ratio is reported for information.

    python perf/bench_harness.py --base-url http://localhost:3000 --samples 200 \\
        --label my-laptop-eval --output reports/perf/harness-short-turn.json

The stack must run the harness processes (``docker-compose.agent-harness.yml``)
on a MongoDB replica set, and must reach this host at ``--model-url``.
``--container`` adds a breakdown of where durable time went, read from the
harness lifecycle logs of the runs this bench made.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

import requests

from stack import percentile

_IT_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_OUTPUT = _IT_DIR / "reports" / "perf" / "harness-short-turn.json"
LABS_FLAG = "ENABLE_AGENT_HARNESS"
SCHEDULED_RUNS_FLAG = "ENABLE_SCHEDULED_RUNS"
PROFILE = "short-turn"
SATURATE = "saturate"
SESSION_READY_SLO_MS = 5000.0  # SLO.md #20: the warm-pool session-ready target, design-plan PF-01
WEBHOOK_HEADER = "x-pipeshub-trigger-token"
ROUTES = ("legacy", "harness")
TAG = re.compile(r"bench-[0-9a-f]{12}")
READ_TOOL = "list_toolsets"
ANSWER = "The sum of 2 and 3 is 5."
ANSWER_CHUNKS = 6
# The fault-stack rig's profiles (harness_profiles.PROFILES), listed here so --help needs no backend imports.
RIG_PROFILES = ("approval", "burst-200", "cancel", "code-mode-5k", "commit-cpu", "commits-n16", "commits-n16-burst",
                "commits-n16-instep",
                "deep-wave", "engine-latency", "mcp-5-servers", "model-gateway", "modes", "reconnect", "resume", "sandbox", "soak",
                "tool-gateway")


# ---- scripted model ----


@dataclass
class ModelCall:
    tag: str | None
    started: float
    ended: float = 0.0
    first_content: float | None = None
    kind: str = "text"
    tools: list[str] = field(default_factory=list)
    stream: bool = False


class ScriptedModel:
    """OpenAI-compatible chat completions at a fixed latency to the first byte,
    then streamed text at a fixed interval per chunk.

    A request that carries a tool result is answered; one that offers the
    read tool asks for it once; anything else (titles, classifiers) gets the
    answer text. Every request is timed on this process's clock and filed under
    the sample tag found in its messages.

    A tag in ``background`` (the ``saturate`` profile's scheduled runs) asks
    for the read tool that many times, each call at ``background_latency_s``,
    before it answers. ``peak_in_flight`` is the most calls it served at once:
    what the stack's provider limit let through.
    """

    def __init__(self, host: str, port: int, latency_s: float, chunk_s: float,
                 background_latency_s: float | None = None) -> None:
        self.latency_s = latency_s
        self.chunk_s = chunk_s
        self.background_latency_s = latency_s if background_latency_s is None else background_latency_s
        self.background: dict[str, int] = {}
        self.calls: list[ModelCall] = []
        self.in_flight = 0
        self.peak_in_flight = 0
        self._lock = threading.Lock()
        model = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_: Any) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802
                body = json.dumps({"object": "list", "data": [{"id": "bench-model", "object": "model"}]})
                self._send(200, body.encode(), "application/json")

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("content-length") or 0)
                request = json.loads(self.rfile.read(length) or b"{}")
                model.serve(self, request)

            def _send(self, status: int, body: bytes, content_type: str) -> None:
                _reply(self, status, body, content_type)

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.daemon_threads = True
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def calls_for(self, tag: str) -> list[ModelCall]:
        with self._lock:
            return [c for c in self.calls if c.tag == tag and c.ended]

    def first_call(self, tag: str) -> float | None:
        """When the first call filed under ``tag`` started, on this process's clock."""
        with self._lock:
            started = [c.started for c in self.calls if c.tag == tag]
        return min(started) if started else None

    def answered(self, tag: str) -> bool:
        with self._lock:
            return any(c.tag == tag and c.kind == "answer" for c in self.calls)

    def plan(self, tag: str | None, request: dict[str, Any]) -> tuple[bool, float]:
        """Whether this call asks for the read tool, and how long it takes to its first byte."""
        read_tool = _read_tool(request.get("tools") or [])
        results = sum(1 for m in request.get("messages") or [] if m.get("role") == "tool")
        steps = self.background.get(tag or "")
        if steps is None:
            return bool(read_tool) and results == 0, self.latency_s
        return bool(read_tool) and results < steps, self.background_latency_s

    def serve(self, handler: BaseHTTPRequestHandler, request: dict[str, Any]) -> None:
        call = ModelCall(tag=_find_tag(request.get("messages")), started=time.perf_counter())
        with self._lock:
            self.in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            self._serve(handler, request, call)
        finally:
            with self._lock:
                self.in_flight -= 1

    def _serve(self, handler: BaseHTTPRequestHandler, request: dict[str, Any], call: ModelCall) -> None:
        call.stream = bool(request.get("stream"))
        tools = [t.get("function", {}).get("name", "") for t in request.get("tools") or []]
        call.tools = tools
        read_tool = _read_tool(request.get("tools") or [])
        has_result = any(m.get("role") == "tool" for m in request.get("messages") or [])
        wants_tool, latency_s = self.plan(call.tag, request)
        time.sleep(latency_s)
        if read_tool and wants_tool:
            call.kind = "tool_call"
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "type": "function",
                    "function": {"name": read_tool[0], "arguments": json.dumps(read_tool[1])},
                }],
            }
            finish = "tool_calls"
        else:
            call.kind = "answer" if has_result else "text"
            message = {"role": "assistant", "content": ANSWER}
            finish = "stop"
        if call.stream:
            try:
                call.first_content = _stream(handler, request, message, finish, self.chunk_s)
            except (BrokenPipeError, ConnectionResetError):
                call.kind = "abandoned"  # the caller stopped reading
        else:
            body = json.dumps({
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": request.get("model", "bench-model"),
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 10, "completion_tokens": ANSWER_CHUNKS, "total_tokens": 16},
            }).encode()
            _reply(handler, 200, body, "application/json")
        call.ended = time.perf_counter()
        with self._lock:
            self.calls.append(call)


def _reply(handler: BaseHTTPRequestHandler, status: int, body: bytes, content_type: str) -> None:
    # No keep-alive: a pooled connection the Docker host proxy has dropped
    # would hang the stack's next call until its client timeout.
    handler.send_response(status)
    handler.send_header("content-type", content_type)
    handler.send_header("content-length", str(len(body)))
    handler.send_header("connection", "close")
    handler.end_headers()
    handler.wfile.write(body)
    handler.close_connection = True


def _find_tag(messages: Any) -> str | None:
    match = TAG.search(json.dumps(messages or []))
    return match.group(0) if match else None


def _read_tool(tools: list[dict[str, Any]]) -> tuple[str, dict[str, Any]] | None:
    """The toolset catalog: a read tool both routes give an agent with no toolsets."""
    names = {(t.get("function") or {}).get("name") for t in tools}
    return (READ_TOOL, {}) if READ_TOOL in names else None


def _stream(
    handler: BaseHTTPRequestHandler, request: dict[str, Any], message: dict[str, Any], finish: str, chunk_s: float
) -> float | None:
    """Streams ``message``; returns when its first text chunk was written."""
    handler.send_response(200)
    handler.send_header("content-type", "text/event-stream")
    handler.send_header("cache-control", "no-cache")
    handler.send_header("connection", "close")
    handler.end_headers()
    handler.close_connection = True
    base = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": request.get("model", "bench-model"),
    }

    def send(delta: dict[str, Any], reason: str | None = None, usage: dict[str, int] | None = None) -> None:
        chunk = {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": reason}]}
        if usage:
            chunk["usage"] = usage
        handler.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        handler.wfile.flush()

    first: float | None = None
    send({"role": "assistant", "content": ""})
    if message.get("tool_calls"):
        send({"tool_calls": [{"index": 0, **message["tool_calls"][0]}]})
    else:
        text = message["content"]
        step = max(1, len(text) // ANSWER_CHUNKS)
        for i in range(0, len(text), step):
            if first is not None:
                time.sleep(chunk_s)
            send({"content": text[i : i + step]})
            first = first or time.perf_counter()
    send({}, finish, {"prompt_tokens": 10, "completion_tokens": ANSWER_CHUNKS, "total_tokens": 16})
    handler.wfile.write(b"data: [DONE]\n\n")
    handler.wfile.flush()
    return first


# ---- stack ----


class QuotaRefused(Exception):
    """The admission quota (30 runs a minute per user) refused a run."""

    def __init__(self, retry_after_s: float) -> None:
        super().__init__(f"retry after {retry_after_s}s")
        self.retry_after_s = retry_after_s


class Stack:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.http = requests.Session()
        self.token = ""
        self.agent_key = ""

    def call(self, method: str, path: str, body: Any = None, **kw: Any) -> requests.Response:
        headers = {"content-type": "application/json", **kw.pop("headers", {})}
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        resp = self.http.request(method, self.base + path, json=body, headers=headers,
                                 timeout=kw.pop("timeout", self.timeout), **kw)
        if resp.status_code == 429:
            raise QuotaRefused(float(resp.headers.get("retry-after") or 5))
        if resp.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {resp.status_code} {resp.text[:300]}")
        return resp

    def login(self, email: str, password: str, bootstrap: bool) -> None:
        if bootstrap and not self.call("GET", "/api/v1/org/exists").json().get("exists"):
            self.call("POST", "/api/v1/org", {"accountType": "individual", "contactEmail": email,
                                              "adminFullName": "Harness Bench", "password": password})
        session = self.call("POST", "/api/v1/userAccount/initAuth", {"email": email}).headers["x-session-token"]
        auth = self.call("POST", "/api/v1/userAccount/authenticate",
                         {"method": "password", "email": email, "credentials": {"password": password}},
                         headers={"x-session-token": session}).json()
        self.token = auth["accessToken"]

    def use_model(self, endpoint: str) -> None:
        self.call("POST", "/api/v1/configurationManager/ai-models/providers", {
            "modelType": "llm", "provider": "openAICompatible",
            "configuration": {"model": "bench-model", "apiKey": "bench", "endpoint": endpoint},
            "isMultimodal": False, "isReasoning": False, "isDefault": True,
        })
        try:
            self.call("PUT", "/api/v1/org/onboarding-status", {"status": "configured"})
        except RuntimeError:
            pass

    def create_agent(self, wait_s: float) -> None:
        # A fresh org reaches the graph through the entity-event consumer, so the
        # first create can race it.
        deadline = time.monotonic() + wait_s
        while True:
            try:
                created = self.call("POST", "/api/v1/agents/create", {
                    "name": f"harness-bench-{uuid.uuid4().hex[:6]}", "description": "SLO #2 bench",
                    "models": [], "toolsets": [], "knowledge": [],
                }).json()
                break
            except RuntimeError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(5)
        agent = created.get("agent", created)
        self.agent_key = agent.get("_key") or agent.get("agentKey") or ""
        if not self.agent_key:
            raise RuntimeError("agent create answered no key")

    def set_flag(self, on: bool) -> None:
        self.set_flags({LABS_FLAG: on})

    def set_flags(self, changed: dict[str, bool]) -> None:
        current = self.call("GET", "/api/v1/configurationManager/platform/settings").json()
        settings = current.get("settings", current)
        flags = {**dict(settings.get("featureFlags") or {}), **changed}
        self.call("POST", "/api/v1/configurationManager/platform/settings", {
            "fileUploadMaxSizeBytes": settings.get("fileUploadMaxSizeBytes", 30 * 1024 * 1024),
            "featureFlags": flags,
        })


def sse_frames(resp: requests.Response, deadline: float) -> Iterator[dict[str, Any]]:
    data: list[str] = []
    for raw in resp.iter_lines(chunk_size=1, decode_unicode=True):
        # Heartbeats keep the socket alive, so the read timeout alone never fires.
        if time.perf_counter() > deadline:
            raise TimeoutError("turn timeout")
        line = raw or ""
        if line.startswith("data:"):
            data.append(line[5:].strip())
        elif line == "" and data:
            payload, data = "\n".join(data), []
            try:
                yield json.loads(payload)
            except json.JSONDecodeError:
                continue


# ---- one sample ----


@dataclass
class Sample:
    route: str
    tag: str
    at: float = field(default_factory=time.time)
    ok: bool = False
    error: str | None = None
    run_id: str | None = None
    admit_s: float | None = None
    first_frame_s: float | None = None
    ttft_s: float | None = None
    done_s: float | None = None
    model_calls: int = 0
    model_before_ttft_s: float = 0.0
    model_before_done_s: float = 0.0
    tools_offered: list[str] = field(default_factory=list)
    tool_called: bool = False

    @property
    def ttft_nonmodel_s(self) -> float | None:
        return None if self.ttft_s is None else self.ttft_s - self.model_before_ttft_s

    @property
    def done_nonmodel_s(self) -> float | None:
        return None if self.done_s is None else self.done_s - self.model_before_done_s


def model_busy(calls: list[ModelCall], start: float, until: float, *, to_first_token: bool = False) -> float:
    """Length of the union of the model's call intervals inside [start, until].

    ``to_first_token`` ends a streamed answer's interval at its first text chunk:
    what follows is the harness's time to deliver that chunk, not the model's.
    """
    spans = sorted(
        (max(c.started, start), min(c.first_content if to_first_token and c.first_content else c.ended, until))
        for c in calls
    )
    busy, reach = 0.0, start
    for lo, hi in spans:
        lo = max(lo, reach)
        if hi > lo:
            busy += hi - lo
            reach = hi
    return busy


def follow(sample: Sample, resp: requests.Response, t0: float, timeout: float) -> None:
    for frame in sse_frames(resp, t0 + timeout):
        kind = frame.get("type")
        if sample.first_frame_s is None:
            sample.first_frame_s = time.perf_counter() - t0
        if kind == "TEXT_MESSAGE_CONTENT" and sample.ttft_s is None and frame.get("delta"):
            sample.ttft_s = time.perf_counter() - t0
        elif kind == "RUN_ERROR":
            sample.error = f"RUN_ERROR {str(frame.get('message') or frame.get('code'))[:120]}"
            return
        elif kind == "RUN_FINISHED" and not frame.get("parentRunId"):
            sample.done_s = time.perf_counter() - t0
            return


def run_sample(stack: Stack, model: ScriptedModel, route: str, timeout: float) -> Sample:
    """One turn; a run the admission quota refused is waited out and asked again."""
    while True:
        try:
            return _run_sample(stack, model, route, timeout)
        except QuotaRefused as refused:
            time.sleep(refused.retry_after_s)


def _run_sample(stack: Stack, model: ScriptedModel, route: str, timeout: float) -> Sample:
    tag = f"bench-{uuid.uuid4().hex[:12]}"
    sample = Sample(route=route, tag=tag)
    query = f"What is 2 plus 3? ({tag})"
    sse = {"accept": "text/event-stream"}
    t0 = time.perf_counter()
    try:
        if route == "legacy":
            resp = stack.call("POST", f"/api/v1/agents/{stack.agent_key}/conversations/stream",
                              {"query": query, "chatMode": "quick", "recordIds": [], "filters": {},
                               "protocol": "agui"}, headers=sse, stream=True, timeout=timeout)
        else:
            admitted = stack.call("POST", f"/api/v1/agents/{stack.agent_key}/runs", {
                "idempotencyKey": tag, "query": query, "chatMode": "quick",
                "recordIds": [], "filters": {}, "attachments": [],
            }).json().get("admission")
            if admitted is None:
                raise RuntimeError("admission routed to legacy: is the Labs flag on and the harness running?")
            sample.run_id = admitted["run"]["runId"]
            sample.admit_s = time.perf_counter() - t0
            resp = stack.call("GET", f"/api/v1/agents/{stack.agent_key}/conversations/"
                              f"{admitted['sessionId']}/runs/{sample.run_id}/stream",
                              headers=sse, stream=True, timeout=timeout)
        with resp:
            follow(sample, resp, t0, timeout)
    except (requests.RequestException, RuntimeError, KeyError, TimeoutError) as exc:
        sample.error = f"{type(exc).__name__}: {str(exc)[:160]}"
    # A title or summary call can still be running after the terminal frame.
    time.sleep(0.05)
    calls = model.calls_for(tag)
    sample.model_calls = len(calls)
    sample.tool_called = any(c.kind == "tool_call" for c in calls)
    sample.tools_offered = sorted({t for c in calls for t in c.tools})
    if sample.ttft_s is not None:
        sample.model_before_ttft_s = model_busy(calls, t0, t0 + sample.ttft_s, to_first_token=True)
    if sample.done_s is not None:
        sample.model_before_done_s = model_busy(calls, t0, t0 + sample.done_s)
    sample.ok = sample.error is None and sample.done_s is not None and sample.ttft_s is not None
    if sample.ok and not sample.tool_called:
        sample.ok, sample.error = False, "no read tool call: the route did not offer it"
    return sample


# ---- schedule and result ----


def run_blocks(stack: Stack, model: ScriptedModel, args: argparse.Namespace) -> list[Sample]:
    samples: list[Sample] = []
    done = {r: 0 for r in ROUTES}
    first = True
    while min(done.values()) < args.samples:
        for route in ROUTES:
            stack.set_flag(route == "harness")
            for _ in range(args.warmup if first else 0):
                run_sample(stack, model, route, args.turn_timeout)
            n = min(args.block, args.samples - done[route])
            for _ in range(n):
                sample = run_sample(stack, model, route, args.turn_timeout)
                samples.append(sample)
                done[route] += 1
                if args.think_time:
                    time.sleep(args.think_time)
            ok = [s for s in samples if s.route == route and s.ok]
            print(f"{route}: {done[route]}/{args.samples} ({len(ok)} ok)", file=sys.stderr, flush=True)
        first = False
    stack.set_flag(args.leave_flag == "on")
    return samples


# ---- saturate: SLO #20, interactive session ready under saturation (G-45, GA-24) ----


@dataclass
class Turn:
    """One interactive turn admitted while the stack is saturated: session ready is admission to its first model
    call, as SLO #20 (and GA-24's virtual-time test) defines it."""

    tag: str
    at: float = field(default_factory=time.time)
    admitted_at: float | None = None
    first_call_at: float | None = None
    done_s: float | None = None
    error: str | None = None
    run_id: str | None = None

    @property
    def ready_s(self) -> float | None:
        if self.admitted_at is None or self.first_call_at is None:
            return None
        return self.first_call_at - self.admitted_at


@dataclass
class BackgroundJob:
    tag: str
    schedule_id: str = ""
    error: str | None = None


def fire_background(stack: Stack, model: ScriptedModel, steps: int) -> BackgroundJob:
    """A scheduled run (class ``background``): its own webhook schedule, fired once, asking ``steps`` tool calls."""
    job = BackgroundJob(tag=f"bench-{uuid.uuid4().hex[:12]}")
    model.background[job.tag] = steps
    while True:
        try:
            created = stack.call("POST", "/api/v1/agent-harness/schedules", {
                "name": f"saturate {job.tag}", "agentKey": stack.agent_key,
                "input": {"query": f"Background job ({job.tag})", "chatMode": "quick"},
                # A token webhook (TR-04's choice for a caller that cannot sign): the bench measures the firing, not
                # the signature, and a new webhook is signed unless asked otherwise.
                "trigger": {"kind": "webhook", "auth": "token"},
            }).json()
            job.schedule_id = str(created["schedule"]["id"])
            stack.call("POST", f"/api/v1/agent-harness/triggers/{job.schedule_id}/webhook",
                       headers={WEBHOOK_HEADER: str(created["webhookToken"])})
            return job
        except QuotaRefused as refused:
            time.sleep(refused.retry_after_s)
        except (requests.RequestException, RuntimeError, KeyError) as exc:
            job.error = f"{type(exc).__name__}: {str(exc)[:160]}"
            return job


def interactive_turn(stack: Stack, model: ScriptedModel, timeout: float) -> Turn:
    turn = Turn(tag=f"bench-{uuid.uuid4().hex[:12]}")
    query = f"What is 2 plus 3? ({turn.tag})"
    sse = {"accept": "text/event-stream"}
    try:
        while True:
            try:
                admitted = stack.call("POST", f"/api/v1/agents/{stack.agent_key}/runs", {
                    "idempotencyKey": turn.tag, "query": query, "chatMode": "quick",
                    "recordIds": [], "filters": {}, "attachments": [],
                }).json().get("admission")
                break
            except QuotaRefused as refused:
                time.sleep(refused.retry_after_s)
        turn.admitted_at = time.perf_counter()
        if admitted is None:
            raise RuntimeError("admission routed to legacy: is the Labs flag on and the harness running?")
        turn.run_id = admitted["run"]["runId"]
        resp = stack.call("GET", f"/api/v1/agents/{stack.agent_key}/conversations/"
                          f"{admitted['sessionId']}/runs/{turn.run_id}/stream",
                          headers=sse, stream=True, timeout=timeout)
        sample = Sample(route="harness", tag=turn.tag)
        with resp:
            follow(sample, resp, turn.admitted_at, timeout)
        turn.done_s, turn.error = sample.done_s, sample.error
    except (requests.RequestException, RuntimeError, KeyError, TimeoutError) as exc:
        turn.error = f"{type(exc).__name__}: {str(exc)[:160]}"
    turn.first_call_at = model.first_call(turn.tag)
    return turn


def saturate_gate(turns: list[Turn], jobs: list[BackgroundJob], answered: set[str], *,
                  ready_slo_ms: float) -> dict[str, Any]:
    """SLO #20: interactive session ready p95 within the warm-pool target while every scheduled run completes."""
    ready = [t.ready_s for t in turns if t.ready_s is not None]
    p95 = percentile(ready, 95) if ready else None
    fired = [j for j in jobs if j.error is None]
    completed = [j for j in fired if j.tag in answered]
    measured = bool(ready) and len(ready) == len(turns)
    passed = measured and p95 is not None and p95 * 1000 <= ready_slo_ms and len(completed) == len(jobs)
    return {
        "slo": "#20",
        "session_ready": stats(ready),
        "target_p95_ms": ready_slo_ms,
        "turns": len(turns),
        "turns_measured": len(ready),
        "background": {"fired": len(fired), "of": len(jobs), "completed": len(completed)},
        "pass": passed,
    }


def run_saturate(stack: Stack, model: ScriptedModel, args: argparse.Namespace) -> dict[str, Any]:
    """Fills the stack with scheduled runs, then admits interactive turns among them and times each one's session
    ready. The stack decides how much saturates it (its worker slots, ``MODEL_GATEWAY_PROVIDER_CONCURRENCY``):
    ``background_peak`` says how many scheduled runs the model saw at once, so a run that never saturated reads so."""
    # The durable route and scheduled runs (Labs ENABLE_SCHEDULED_RUNS) for the org.
    stack.set_flags({LABS_FLAG: True, SCHEDULED_RUNS_FLAG: True})
    jobs: list[BackgroundJob] = []
    turns: list[Turn] = []
    try:
        jobs = [fire_background(stack, model, args.background_steps) for _ in range(args.background)]
        # Saturated: as many scheduled runs started as the stack will run at once (none more for a few seconds).
        settle = time.monotonic() + args.settle_s
        started, steady_since = 0, time.monotonic()
        while time.monotonic() < settle:
            now_started = sum(1 for j in jobs if model.first_call(j.tag) is not None)
            if now_started != started:
                started, steady_since = now_started, time.monotonic()
            elif started > 0 and time.monotonic() - steady_since >= 5:
                break
            time.sleep(0.5)
        # Each turn on its own thread, one every --turn-interval: a slow turn does not hold back the next one.
        running: list[tuple[threading.Thread, list[Turn]]] = []
        for _ in range(args.samples):
            box: list[Turn] = []
            thread = threading.Thread(
                target=lambda box=box: box.append(interactive_turn(stack, model, args.turn_timeout)), daemon=True
            )
            thread.start()
            running.append((thread, box))
            time.sleep(args.turn_interval)
        for thread, box in running:
            thread.join(args.turn_timeout + 30)
            turns.extend(box)
        deadline = time.monotonic() + args.saturate_timeout
        while time.monotonic() < deadline and not all(model.answered(j.tag) for j in jobs if j.error is None):
            time.sleep(1)
    finally:
        for job in jobs:
            if job.schedule_id:
                try:
                    stack.call("DELETE", f"/api/v1/agent-harness/schedules/{job.schedule_id}")
                except (requests.RequestException, RuntimeError):
                    pass
        stack.set_flag(args.leave_flag == "on")
    answered = {j.tag for j in jobs if model.answered(j.tag)}
    return {
        "gate": saturate_gate(turns, jobs, answered, ready_slo_ms=args.ready_slo_ms),
        "model_peak_in_flight": model.peak_in_flight,
        "background_peak": background_peak(model, {j.tag for j in jobs}),
        "turns": [asdict(t) | {"ready_s": t.ready_s} for t in turns],
        "background": [asdict(j) | {"completed": j.tag in answered} for j in jobs],
    }


def background_peak(model: ScriptedModel, tags: set[str]) -> int:
    """The most scheduled runs the model served calls for at one moment."""
    with model._lock:  # noqa: SLF001
        spans = [(c.started, c.ended) for c in model.calls if c.tag in tags]
    edges = sorted([(s, 1) for s, _ in spans] + [(e, -1) for _, e in spans])
    peak = running = 0
    for _, step in edges:
        running += step
        peak = max(peak, running)
    return peak


def render_saturate(result: dict[str, Any]) -> str:
    g = result["saturate"]["gate"]
    ready = g["session_ready"]
    bg = g["background"]
    return "\n".join([
        f"# Interactive session ready under saturation (SLO #20, {result['environment']['label']})", "",
        f"Session ready (admission to first model call): p50 {ready['p50_ms']} ms, p95 {ready['p95_ms']} ms "
        f"over {g['turns_measured']}/{g['turns']} turns; target p95 <= {g['target_p95_ms']} ms.",
        f"Scheduled runs: {bg['completed']}/{bg['of']} completed ({bg['fired']} fired); at most "
        f"{result['saturate']['background_peak']} at once; the model served at most "
        f"{result['saturate']['model_peak_in_flight']} calls at once.",
        f"-> {'PASS' if g['pass'] else 'FAIL'}",
    ]) + "\n"


def stats(values: list[float]) -> dict[str, float | int | None]:
    def ms(v: float | None) -> float | None:
        return None if v is None else round(v * 1000, 1)

    return {"n": len(values), "p50_ms": ms(percentile(values, 50)), "p95_ms": ms(percentile(values, 95)),
            "mean_ms": ms(statistics.fmean(values)) if values else None}


def route_metrics(samples: list[Sample]) -> dict[str, Any]:
    ok = [s for s in samples if s.ok]
    errors: dict[str, int] = {}
    for s in samples:
        if not s.ok:
            errors[s.error or "unknown"] = errors.get(s.error or "unknown", 0) + 1
    return {
        "samples": len(samples),
        "ok": len(ok),
        "errors": errors,
        "ttft_nonmodel": stats([s.ttft_nonmodel_s for s in ok if s.ttft_nonmodel_s is not None]),
        "done_nonmodel": stats([s.done_nonmodel_s for s in ok if s.done_nonmodel_s is not None]),
        "admission": stats([s.admit_s for s in ok if s.admit_s is not None]),
        "first_frame": stats([s.first_frame_s for s in ok if s.first_frame_s is not None]),
        "ttft_raw": stats([s.ttft_s for s in ok if s.ttft_s is not None]),
        "done_raw": stats([s.done_s for s in ok if s.done_s is not None]),
        "model_calls_p50": percentile([float(s.model_calls) for s in ok], 50),
        "model_time_p50_ms": round((percentile([s.model_before_done_s for s in ok], 50) or 0) * 1000, 1),
        "tools_offered": sorted({t for s in ok for t in s.tools_offered}),
    }


# SLO #2 budgets (milliseconds added by the durable route at p50). The commit
# rules put five majority-acknowledged transactions before the first answer
# token, so the budget is absolute rather than a ratio to a fake model's speed.
TTFT_BUDGET_MS = 75.0
DONE_BUDGET_MS = 100.0
COMMIT_BUDGET_MS = 25.0


def gate(
    metrics: dict[str, Any],
    *,
    ttft_budget_ms: float = TTFT_BUDGET_MS,
    done_budget_ms: float = DONE_BUDGET_MS,
    commit_budget_ms: float = COMMIT_BUDGET_MS,
    durable_breakdown: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "budget_ms": {"ttft_nonmodel": ttft_budget_ms, "done_nonmodel": done_budget_ms,
                      "commit_p50": commit_budget_ms},
    }
    passed = True
    for metric, budget in (("ttft_nonmodel", ttft_budget_ms), ("done_nonmodel", done_budget_ms)):
        legacy = metrics["legacy"][metric]["p50_ms"]
        harness = metrics["harness"][metric]["p50_ms"]
        if legacy is None or harness is None:
            out[metric] = {"added_ms": None, "ratio": None, "pass": False}
            passed = False
            continue
        added = round(harness - legacy, 1)
        out[metric] = {"added_ms": added, "ratio": round(harness / legacy, 3) if legacy else None,
                       "pass": added <= budget}
        passed = passed and added <= budget
    if durable_breakdown is not None and "events" in durable_breakdown:
        commits = {
            label: {"p50_ms": row["ms_p50"], "pass": row["ms_p50"] is not None and row["ms_p50"] <= commit_budget_ms}
            for label, row in durable_breakdown["events"].items()
            if label.startswith("journal.commit")
        }
        out["commits"] = commits
        passed = passed and all(c["pass"] for c in commits.values())
    out["pass"] = passed
    return out


# ---- breakdown from the harness lifecycle logs ----

_RECORD = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3}) - ")
_FIELD = re.compile(r"\b([A-Za-z]+)=([^\s]+)")


@dataclass
class LogRecord:
    at: float
    event: str
    fields: dict[str, Any]


def parse_logs(text: str) -> list[LogRecord]:
    """Lifecycle records from the app container's log: the Python form
    (``<event> key=value ...``) and the Node form (``<event>`` then a
    ``metadata: {...}`` block on the following lines)."""
    records: list[LogRecord] = []
    head: str | None = None
    body: list[str] = []

    def close() -> None:
        if head is None:
            return
        stamp = _RECORD.match(head)
        if stamp is None or "] - " not in head:
            return
        rest = head.rsplit("] - ", 1)[1].strip()
        event, _, tail = rest.partition(" ")
        fields: dict[str, Any] = dict(_FIELD.findall(tail))
        if body and body[0].startswith("metadata:"):
            try:
                fields.update(json.loads("\n".join([body[0][len("metadata:"):], *body[1:]])))
            except json.JSONDecodeError:
                pass
        at = datetime.strptime(stamp.group(1), "%Y-%m-%d %H:%M:%S,%f").timestamp()
        records.append(LogRecord(at, event, fields))

    for line in text.splitlines():
        if _RECORD.match(line):
            close()
            head, body = line, []
        elif head is not None:
            body.append(line)
    close()
    return records


def _label(record: LogRecord) -> str:
    if record.event == "journal.commit":
        kind = str(record.fields.get("commandId", "")).split(":")
        return f"journal.commit[{kind[1] if len(kind) > 2 else '?'}]"
    return record.event


def _ms(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def breakdown(records: list[LogRecord], run_ids: set[str]) -> dict[str, Any]:
    """Where durable time went, per run: each lifecycle event's own ``ms``
    field, and each event's offset from the run's ``admission.accepted``."""
    by_run: dict[str, list[LogRecord]] = {}
    for record in records:
        run = record.fields.get("runId")
        if run in run_ids:
            by_run.setdefault(run, []).append(record)
    own: dict[str, list[float]] = {}
    per_run: dict[str, list[float]] = {}
    counts: dict[str, list[float]] = {}
    offsets: dict[str, list[float]] = {}
    for run, rows in by_run.items():
        rows.sort(key=lambda r: r.at)
        start = next((r.at for r in rows if r.event == "admission.accepted"), rows[0].at)
        seen: dict[str, int] = {}
        totals: dict[str, float] = {}
        for record in rows:
            label = _label(record)
            seen[label] = seen.get(label, 0) + 1
            offsets.setdefault(f"{label}#{seen[label]}", []).append((record.at - start) * 1000)
            ms = _ms(record.fields.get("ms"))
            if ms is not None:
                own.setdefault(label, []).append(ms)
                totals[label] = totals.get(label, 0.0) + ms
        for label, n in seen.items():
            counts.setdefault(label, []).append(float(n))
        for label, total in totals.items():
            per_run.setdefault(label, []).append(total)

    def p(values: list[float], q: float) -> float | None:
        v = percentile(values, q)
        return None if v is None else round(v, 1)

    events = {
        label: {"per_run": p(counts[label], 50), "ms_p50": p(own[label], 50),
                "ms_p95": p(own[label], 95), "ms_per_run_p50": p(per_run[label], 50)}
        for label in sorted(own)
    }
    timeline = sorted(((p(v, 50) or 0.0, k) for k, v in offsets.items() if len(v) * 2 >= len(by_run)))
    return {"runs": len(by_run), "events": events,
            "timeline_ms_from_admission_p50": [{"event": k, "at_ms": at} for at, k in timeline]}


def container_logs(container: str, since: str) -> str | None:
    try:
        out = subprocess.run(["docker", "logs", "--since", since, container],
                             capture_output=True, text=True, timeout=300, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout + out.stderr


# ---- entry point ----


def environment(args: argparse.Namespace, stack: Stack) -> dict[str, Any]:
    health = {}
    try:
        health = stack.call("GET", "/api/v1/health").json().get("deployment") or {}
    except RuntimeError:
        pass
    return {"label": args.label, "host": platform.platform(), "cpus": os.cpu_count(),
            "deployment": health, "model_latency_ms": args.model_latency_ms,
            "chunk_interval_ms": args.chunk_interval_ms}


def render(result: dict[str, Any]) -> str:
    lines = [f"# Harness overhead ({result['profile']}, {result['environment']['label']})", "",
             "| Route | ok | TTFT p50 | TTFT p95 | turn done p50 | turn done p95 | model calls |",
             "|---|---|---|---|---|---|---|"]
    for route in ROUTES:
        m = result["metrics"][route]
        lines.append(f"| {route} | {m['ok']}/{m['samples']} | {m['ttft_nonmodel']['p50_ms']} | "
                     f"{m['ttft_nonmodel']['p95_ms']} | {m['done_nonmodel']['p50_ms']} | "
                     f"{m['done_nonmodel']['p95_ms']} | {m['model_calls_p50']} |")
    g = result["gate"]
    lines += ["", "Milliseconds, model time subtracted.",
              f"Gate (harness p50 adds at most {g['budget_ms']['ttft_nonmodel']} ms at TTFT and "
              f"{g['budget_ms']['done_nonmodel']} ms at turn done): TTFT +{g['ttft_nonmodel']['added_ms']} ms "
              f"({g['ttft_nonmodel']['ratio']}x), turn done +{g['done_nonmodel']['added_ms']} ms "
              f"({g['done_nonmodel']['ratio']}x) -> {'PASS' if g['pass'] else 'FAIL'}"]
    for label, commit in g.get("commits", {}).items():
        if not commit["pass"]:
            lines.append(f"Commit over budget ({g['budget_ms']['commit_p50']} ms p50): {label} {commit['p50_ms']} ms")
    for route in ROUTES:
        if result["metrics"][route]["errors"]:
            lines.append(f"{route} errors: {result['metrics'][route]['errors']}")
    return "\n".join(lines) + "\n"


def run_rig(args: argparse.Namespace) -> int:
    """The fault-stack rig's profiles (QA-22), judged by ``slo_gates``."""
    import asyncio

    from harness_profiles import Options, environment, run_profiles
    from harness_rig import Infra
    from slo_gates import evaluate, passed, profiles_gated, render

    names = sorted(profiles_gated()) if args.profile == "rig" else [args.profile]
    infra = Infra(mongo_uri=args.mongo_uri, redis_port=args.redis_port, redis_db=args.redis_db,
                  mongo_container=args.mongo_container, port_base=args.port_base, broker=args.broker)
    options = Options(infra=infra, out=args.rig_out, samples=args.samples, soak_s=args.soak_minutes * 60,
                      burst_window_s=args.burst_window_s)
    results = asyncio.run(run_profiles(names, options))
    verdicts = evaluate(results, os.cpu_count() or 1)
    output = {"benchmark": "harness-latency", "environment": environment(args.label), "results": results,
              "verdicts": [{"slo": v.row.slo, "profile": v.row.profile, "metric": v.row.metric, "stat": v.row.stat,
                            "target": v.row.target, "threshold": v.row.threshold, "value": v.value,
                            "status": v.status, "detail": v.detail} for v in verdicts]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=1) + "\n", encoding="utf-8")
    summary = f"# Harness latency ({', '.join(names)}, {args.label})\n\n" + render(verdicts)
    if args.summary:
        args.summary.write_text(summary, encoding="utf-8")
    print(summary)
    print(f"Result written to {args.output}")
    return 1 if args.fail_on_gate and not passed(verdicts) else 0


def saturate_main(args: argparse.Namespace) -> int:
    if not args.email or not args.password:
        raise SystemExit("pass --email/--password or set PIPESHUB_TEST_USER_EMAIL/PASSWORD")
    if args.samples > 60 or args.background > 60:
        raise SystemExit("saturate: --samples and --background stay within the admission quota (30 a minute)")
    model = ScriptedModel(args.model_host, args.model_port, args.model_latency_ms / 1000,
                          args.chunk_interval_ms / 1000, background_latency_s=args.background_latency_ms / 1000)
    model.start()
    stack = Stack(args.base_url, timeout=60)
    started = time.time()
    try:
        stack.login(args.email, args.password, args.bootstrap)
        stack.use_model(args.model_url or f"http://host.docker.internal:{args.model_port}/v1")
        stack.create_agent(wait_s=300)
        saturate = run_saturate(stack, model, args)
    finally:
        model.stop()
    result: dict[str, Any] = {
        "benchmark": "harness",
        "profile": SATURATE,
        "started_at": datetime.fromtimestamp(started, timezone.utc).isoformat(),
        "wall_s": round(time.time() - started, 1),
        "environment": environment(args, stack),
        "config": {"samples": args.samples, "background": args.background, "background_steps": args.background_steps,
                   "background_latency_ms": args.background_latency_ms, "turn_interval_s": args.turn_interval},
        "saturate": saturate,
    }
    output = args.output if args.output != _DEFAULT_OUTPUT else _IT_DIR / "reports" / "perf" / "harness-saturate.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    summary = render_saturate(result)
    if args.summary:
        args.summary.write_text(summary, encoding="utf-8")
    print(summary)
    print(f"Result written to {output}")
    return 1 if args.fail_on_gate and not saturate["gate"]["pass"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=os.getenv("PIPESHUB_BASE_URL", "http://localhost:3000"))
    parser.add_argument("--email", default=os.getenv("PIPESHUB_TEST_USER_EMAIL"))
    parser.add_argument("--password", default=os.getenv("PIPESHUB_TEST_USER_PASSWORD"))
    parser.add_argument("--bootstrap", action="store_true", help="create the org with these credentials if none exists")
    parser.add_argument("--profile", choices=[PROFILE, SATURATE, *RIG_PROFILES, "rig"], default=PROFILE,
                        help="short-turn: the eval stack, both routes (SLO #2); saturate: interactive session "
                             "ready among scheduled runs on that stack (SLO #20); any other: the fault-stack rig "
                             "(harness_profiles.py); rig: every profile a gate reads")
    parser.add_argument("--samples", type=int, default=None,
                        help="recorded samples per route (default 200); saturate: interactive turns (default 12)")
    parser.add_argument("--block", type=int, default=20, help="samples per route before switching the flag")
    parser.add_argument("--warmup", type=int, default=5, help="unrecorded samples per route before the first block")
    parser.add_argument("--think-time", type=float, default=0.2, help="seconds between samples")
    parser.add_argument("--model-latency-ms", type=float, default=200, help="scripted model time to first byte per call")
    parser.add_argument("--chunk-interval-ms", type=float, default=20, help="scripted model time between answer chunks")
    parser.add_argument("--model-host", default="0.0.0.0")
    parser.add_argument("--model-port", type=int, default=38910)
    parser.add_argument("--model-url", default=None,
                        help="the scripted model's /v1 URL as the stack sees it "
                             "(default http://host.docker.internal:<port>/v1)")
    parser.add_argument("--turn-timeout", type=float, default=60, help="seconds to the terminal frame")
    parser.add_argument("--ttft-budget-ms", type=float, default=TTFT_BUDGET_MS)
    parser.add_argument("--done-budget-ms", type=float, default=DONE_BUDGET_MS)
    parser.add_argument("--commit-budget-ms", type=float, default=COMMIT_BUDGET_MS)
    parser.add_argument("--fail-on-gate", action="store_true", help="exit 1 when the gate fails")
    parser.add_argument("--leave-flag", choices=["on", "off"], default="off")
    parser.add_argument("--container", default=None, help="app container, for the durable time breakdown from its logs")
    parser.add_argument("--label", required=True, help="where this ran; baselines compare per label")
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=None)
    parser.add_argument("--mongo-uri", default=None, help="rig: a running replica set (default: the fault compose)")
    parser.add_argument("--mongo-container", default="pxh-mongo-rs",
                        help="rig: the container of --mongo-uri, where the rig drops its database")
    parser.add_argument("--redis-port", type=int, default=None, help="rig: a Redis of the rig's own")
    parser.add_argument("--redis-db", type=int, default=0,
                        help="rig: the DB of --redis-port it uses; it must be empty, and only it is cleared")
    parser.add_argument("--port-base", type=int, default=31400, help="rig: Node's port is this + 3")
    parser.add_argument("--broker", choices=["redis", "kafka"], default="redis")
    parser.add_argument("--background", type=int, default=16, help="saturate: scheduled runs fired first")
    parser.add_argument("--background-steps", type=int, default=20, help="saturate: tool calls each one asks")
    parser.add_argument("--background-latency-ms", type=float, default=1000,
                        help="saturate: scripted model time to first byte per scheduled-run call")
    parser.add_argument("--turn-interval", type=float, default=3.0, help="saturate: seconds between turns")
    parser.add_argument("--settle-s", type=float, default=60, help="saturate: at most this long to fill the stack")
    parser.add_argument("--saturate-timeout", type=float, default=900,
                        help="saturate: seconds after the last turn for every scheduled run to complete")
    parser.add_argument("--ready-slo-ms", type=float, default=SESSION_READY_SLO_MS)
    parser.add_argument("--burst-window-s", type=float, default=60.0,
                        help="burst-200: seconds the 200 creates are spread over (PF-15: 60); 0: all at once")
    parser.add_argument("--soak-minutes", type=float, default=24 * 60,
                        help="soak: minutes of load before the drain and the count of what is left (SLO #19: 24 h)")
    parser.add_argument("--rig-out", type=Path, default=_IT_DIR / "reports" / "perf" / "rig",
                        help="rig: process logs and run files")
    args = parser.parse_args()
    if args.samples is None:
        args.samples = 12 if args.profile == SATURATE else 200
    if args.profile == SATURATE:
        return saturate_main(args)
    if args.profile != PROFILE:
        return run_rig(args)
    if not args.email or not args.password:
        raise SystemExit("pass --email/--password or set PIPESHUB_TEST_USER_EMAIL/PASSWORD")
    if args.samples < 1 or args.block < 1:
        raise SystemExit("--samples and --block must be at least 1")

    model = ScriptedModel(args.model_host, args.model_port, args.model_latency_ms / 1000, args.chunk_interval_ms / 1000)
    model.start()
    stack = Stack(args.base_url, timeout=60)
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    started = time.time()
    try:
        stack.login(args.email, args.password, args.bootstrap)
        stack.use_model(args.model_url or f"http://host.docker.internal:{args.model_port}/v1")
        stack.create_agent(wait_s=300)
        samples = run_blocks(stack, model, args)
    finally:
        model.stop()

    metrics = {route: route_metrics([s for s in samples if s.route == route]) for route in ROUTES}
    result: dict[str, Any] = {
        "benchmark": "harness",
        "profile": args.profile,
        "started_at": datetime.fromtimestamp(started, timezone.utc).isoformat(),
        "wall_s": round(time.time() - started, 1),
        "environment": environment(args, stack),
        "config": {"samples": args.samples, "block": args.block, "warmup": args.warmup,
                   "think_time_s": args.think_time, "chat_mode": "quick"},
        "metrics": metrics,
        "samples": [asdict(s) for s in samples],
    }
    if args.container:
        text = container_logs(args.container, since)
        run_ids = {s.run_id for s in samples if s.run_id and s.ok}
        result["breakdown"] = (breakdown(parse_logs(text), run_ids) if text is not None
                               else {"error": "docker logs failed"})
    result["gate"] = gate(
        metrics,
        ttft_budget_ms=args.ttft_budget_ms,
        done_budget_ms=args.done_budget_ms,
        commit_budget_ms=args.commit_budget_ms,
        durable_breakdown=result.get("breakdown"),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    summary = render(result)
    if args.summary:
        args.summary.write_text(summary, encoding="utf-8")
    print(summary)
    print(f"Result written to {args.output}")
    return 1 if args.fail_on_gate and not result["gate"]["pass"] else 0


if __name__ == "__main__":
    sys.exit(main())
