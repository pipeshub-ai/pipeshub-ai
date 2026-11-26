"""The degraded-dependency rig (QA-17): toxiproxy in front of every dependency, and the checks every scenario makes.

``Rig`` is one broker's session: the `degraded` compose profile, toxiproxy's
proxies, the scripted provider, the MCP server, the sandbox broker, and a Node
of its own whose Mongo and Redis are reached through toxiproxy. The worker each
scenario starts reaches all of them through toxiproxy too.

The checks (C-1..C-5) look at what the user's stream
carried and what the stack persisted, never at how the code got there.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import stack

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

LEASE_MS = 10_000
# Its own KV documents: the crash catalog's Node shares this Redis in one session.
KV_PREFIX = "pipeshub:kv-degraded:"
SANDBOX_TOKEN = "fault-suite-broker-token"
GRAPH_PASSWORD = "fault-suite-graph"
RUN_ERROR_CODES = json.loads((stack.REPO / "contracts" / "harness" / "vectors" / "run-error-codes.json").read_text())


@dataclass(frozen=True)
class Ports:
    """The `degraded` profile's host ports (compose), the rig's own, and one toxiproxy listener per dependency."""

    base: int

    @property
    def toxiproxy(self) -> int:
        return self.base

    @property
    def redis_replica(self) -> int:
        return self.base + 1

    @property
    def neo4j(self) -> int:
        return self.base + 2

    @property
    def qdrant(self) -> int:
        return self.base + 3

    @property
    def llm(self) -> int:
        return self.base + 4

    @property
    def mcp(self) -> int:
        return self.base + 5

    @property
    def sandbox(self) -> int:
        return self.base + 6

    @property
    def node(self) -> int:
        return self.base + 7

    PROXIES = ("mongo", "redis", "llm", "embedding", "graph", "vector", "mcp", "sandbox")

    def proxy(self, name: str) -> int:
        return self.base + 10 + self.PROXIES.index(name)


class Toxiproxy:
    """Toxiproxy's HTTP API: a proxy per dependency, taken down, slowed or black-holed by a scenario."""

    def __init__(self, port: int) -> None:
        self.url = f"http://127.0.0.1:{port}"

    def _call(self, method: str, path: str, body: object | None = None) -> object:
        request = urllib.request.Request(
            self.url + path, method=method, data=json.dumps(body).encode() if body is not None else None,
            headers={"content-type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read()
        return json.loads(raw) if raw else None

    def ready(self) -> bool:
        try:
            self._call("GET", "/version")
        except OSError:
            return False
        return True

    def create(self, name: str, listen: int, upstream: int, listen_host: str = "127.0.0.1") -> None:
        with contextlib.suppress(OSError):
            self._call("DELETE", f"/proxies/{name}")
        self._call("POST", "/proxies", {
            "name": name, "listen": f"{listen_host}:{listen}", "upstream": f"127.0.0.1:{upstream}", "enabled": True,
        })

    def reset(self) -> None:
        """Every proxy enabled, every toxic removed."""
        self._call("POST", "/reset")

    def enabled(self, name: str, *, on: bool) -> None:
        self._call("POST", f"/proxies/{name}", {"enabled": on})

    def upstream(self, name: str, port: int) -> None:
        self._call("POST", f"/proxies/{name}", {"upstream": f"127.0.0.1:{port}"})

    @contextlib.contextmanager
    def down(self, name: str) -> Iterator[None]:
        """Connections refused and the open ones cut, until the block ends."""
        self.enabled(name, on=False)
        try:
            yield
        finally:
            self.enabled(name, on=True)

    @contextlib.contextmanager
    def toxic(self, name: str, kind: str, stream: str = "downstream", **attributes: int) -> Iterator[None]:
        toxic = f"{kind}-{stream}"
        self._call("POST", f"/proxies/{name}/toxics", {
            "name": toxic, "type": kind, "stream": stream, "toxicity": 1.0, "attributes": attributes,
        })
        try:
            yield
        finally:
            self._call("DELETE", f"/proxies/{name}/toxics/{toxic}")


@dataclass
class Run:
    """One admitted run, followed as the user's browser follows it."""

    run_id: str
    session_id: str
    run_dir: Path
    stream: stack.ResumingStream
    worker: stack.Process
    workers: list[stack.Process] = field(default_factory=list)


@dataclass(frozen=True)
class Expect:
    status: str
    # The failure the user is told of: `RUN_ERROR.code`, or the error answer's `errorCode` (C-1).
    code: str | None = None
    # A scenario without a write leaves no external effect.
    writes: bool = True
    # A failure the worker answers (a model call the port classified) ends `RUN_FINISHED`; one Node ends, `RUN_ERROR`.
    ending: str = "RUN_FINISHED"


@dataclass
class Rig:
    broker: str
    ports: Ports
    toxi: Toxiproxy
    env: dict[str, str]
    node: stack.Process
    node_log: Path
    out: Path
    processes: dict[str, stack.Process]

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.ports.node}"

    # ---- a scenario's run ----

    def scenario(self, name: str, **knobs: object) -> Path:
        run_dir = self.out / name
        run_dir.mkdir(parents=True, exist_ok=True)
        knobs = {"leaseMs": LEASE_MS, "presentAnswer": True, "dependencies": self.dependencies(), **knobs}
        (run_dir / "scenario.json").write_text(json.dumps(knobs))
        return run_dir

    def dependencies(self) -> dict[str, Any]:
        proxy = self.ports.proxy
        return {
            "graph": {"uri": f"bolt://127.0.0.1:{proxy('graph')}", "password": GRAPH_PASSWORD},
            "vector": {"host": "127.0.0.1", "port": proxy("vector")},
            "embedding": {"url": f"http://127.0.0.1:{proxy('embedding')}/v1"},
            "mcp": {"url": f"http://{stack.mcp_host()}:{proxy('mcp')}/mcp"},
            "sandbox": {"url": f"http://127.0.0.1:{proxy('sandbox')}", "token": SANDBOX_TOKEN},
        }

    def model(self, **stream_timeouts: float) -> dict[str, Any]:
        """``scenario.json``'s ``model``: the run's model on the wire, through the `llm` proxy."""
        config: dict[str, Any] = {"url": f"http://127.0.0.1:{self.ports.proxy('llm')}/v1"}
        if stream_timeouts:
            config["streamTimeouts"] = stream_timeouts
        return config

    def plan_llm(self, steps: list[dict[str, Any]], then: dict[str, Any] | str = "ok") -> None:
        stack.http("POST", "/__fault/plan", {"steps": steps, "then": then}, base=f"http://127.0.0.1:{self.ports.llm}")

    def start_worker(self, run_dir: Path) -> stack.Process:
        return stack.start_live_worker(self.env, run_dir, base=self.base)

    def admit(self, run_dir: Path, query: str, worker: stack.Process) -> Run:
        admission = stack.admit(f"{query} SENTINEL-PROMPT-{run_dir.name}", f"degraded-{uuid.uuid4()}", base=self.base)
        assert admission is not None and admission.get("run"), f"not admitted durable: {admission}"
        run_id, session_id = admission["run"]["runId"], admission["sessionId"]
        stream = stack.ResumingStream(self.base, session_id, run_id, deadline_s=RUN_DEADLINE_S)
        return Run(run_id, session_id, run_dir, stream, worker, [worker])

    def gate_reached(self, run: Run, turn: int) -> None:
        stack.wait_for(lambda: (run.run_dir / f"gate-{turn}.reached").exists(), 60, f"turn {turn} reached its gate")

    def open_gate(self, run: Run, turn: int) -> None:
        (run.run_dir / f"gate-{turn}.open").write_text(run.run_id)

    def status(self, run: Run) -> str | None:
        return (stack.run_document(run.run_id, base=self.base) or {}).get("status")

    def wait_terminal(self, run: Run, timeout: float = 180) -> None:
        stack.wait_for(lambda: self.status(run) in TERMINAL, timeout, f"{run.run_id} terminal")
        stack.wait_for(lambda: run.stream.finished or not run.stream.thread.is_alive(), 60, "the user's stream ended")

    def finish(self, run: Run) -> None:
        for worker in run.workers:
            worker.kill9()

    # ---- the checks ----

    def problems(self, run: Run, expect: Expect) -> dict[str, list[str]]:
        events = [e for e in stack.journal(run.session_id, base=self.base) if e.get("runId") == run.run_id]
        session = stack.journal(run.session_id, base=self.base)
        found = {
            "C-1 user": user_state_problems(run.stream, expect),
            "C-2 run": run_problems(self.status(run), expect),
            "C-3 effects": effect_problems(run, events, expect),
            "C-4 history": history_problems(session),
            "C-5 logs": log_problems(run, self.node_log, self.ports.proxy("redis")),
        }
        (run.run_dir / "result.json").write_text(json.dumps({
            "broker": self.broker, "runId": run.run_id, "problems": found, "reconnects": run.stream.reconnects,
            "frames": run.stream.frames,
        }, indent=1) + "\n")
        (run.run_dir / "journal.json").write_text(json.dumps(session, indent=1, default=str) + "\n")
        return {k: v for k, v in found.items() if v}

    def assert_clean(self, run: Run, expect: Expect) -> None:
        found = self.problems(run, expect)
        assert found == {}, json.dumps(found, indent=1)


TERMINAL = {"completed", "failed", "cancelled", "waiting_approval"}
RUN_DEADLINE_S = 300

# What a user must never read: an exception's name, a stack, an address of ours, a provider's error body.
RAW = re.compile(
    r"Traceback|\b[A-Z][A-Za-z]*(?:Error|Exception)\b|\b(?:127\.0\.0\.1|localhost|0\.0\.0\.0):\d+|bolt://"
    r"|Error code: \d{3}|rate_limit_exceeded|ServiceUnavailable|ClientConnector"
)


def user_state_problems(stream: stack.ResumingStream, expect: Expect) -> list[str]:
    """C-1: the stream ended in the state the run ended in, in our own words, and carried nothing raw."""
    from harness_core.contracts.generated.enums import TOOL_ERROR_CODES
    problems: list[str] = []
    if not stream.finished:
        problems.append(f"the user's stream never ended (reconnects {stream.reconnects}, last error {stream.error})")
    terminals = [f for f in stream.frames if f.get("type") in ("RUN_FINISHED", "RUN_ERROR") and not f.get("parentRunId")]
    kinds = [f["type"] for f in terminals]
    if expect.status in ("completed", "failed", "cancelled") and kinds != [expect.ending]:
        problems.append(f"terminal frames {kinds}, expected [{expect.ending}]")
    for frame in terminals:
        if frame["type"] == "RUN_ERROR":
            if frame.get("code") != expect.code:
                problems.append(f"RUN_ERROR code {frame.get('code')} != {expect.code}")
            if frame.get("code") not in RUN_ERROR_CODES["codes"]:
                problems.append(f"RUN_ERROR code {frame.get('code')} has no words of the web app's own (run-error-codes.json)")
    if expect.ending == "RUN_FINISHED" and expect.code is not None:
        problems += error_answer_problems(stream.frames, expect.code)
    for frame in stream.frames:
        raw = {m.group(0) for m in RAW.finditer(json.dumps(frame))}
        problems += [f"{frame.get('type')} carries raw text {text!r}" for text in sorted(raw)]
    failed = [f for f in stream.frames if f.get("type") == "TOOL_CALL_RESULT" and f.get("status") == "failed"]
    problems += [f"failed call {f.get('toolCallId')} has no code the web app words (ERR-UX)" for f in failed
                 if f.get("errorCode") not in TOOL_ERROR_CODES]
    return problems


def error_answer_problems(frames: list[dict[str, Any]], code: str) -> list[str]:
    """A failed model call's answer is the product's own words for its code, never the provider's."""
    from app.agents.agent_loop.error_classification import (
        _USER_MESSAGES,  # pyright: ignore[reportPrivateUsage]
    )

    finals = [f["snapshot"] for f in frames if f.get("type") == "STATE_SNAPSHOT" and (f.get("snapshot") or {}).get("final")]
    if not finals:
        return ["no final answer reached the user"]
    from app.harness.contracts.generated.pipeshub.enums import ANSWER_ERROR_CODES

    final = finals[-1]
    problems = [] if final.get("answer") == _USER_MESSAGES[code] else [
        f"the error answer is not the product's words for {code}: {final.get('answer')!r}"]
    if final.get("errorCode") != code or code not in ANSWER_ERROR_CODES:
        problems.append(f"the error answer's code {final.get('errorCode')!r} is not {code!r}, worded by the web app")
    return problems


def run_problems(status: str | None, expect: Expect) -> list[str]:
    """C-2: the run resumed and ended, or failed, cleanly."""
    return [] if status == expect.status else [f"run status {status} != {expect.status}"]


def effect_problems(run: Run, events: list[dict[str, Any]], expect: Expect) -> list[str]:
    """C-3: each external effect once, one journaled model response per turn."""
    problems: list[str] = []
    made = stack.effects(run.run_dir, run.run_id)
    if expect.writes and expect.status == "completed" and not made:
        problems.append("the run made no external effect")
    if duplicates := stack.duplicate_effects(made):
        problems.append(f"duplicate external effects {duplicates}")
    responses = stack.responses_per_turn(events)
    if any(n != 1 for n in responses.values()):
        problems.append(f"journaled responses per turn {responses}")
    return problems


def history_problems(session: list[dict[str, Any]]) -> list[str]:
    """C-4: the session's journal is a valid history (QA-13's H1..H6)."""
    from harness_core.journal.types import JournalEvent
    from harness_core.testing.history import journal_history_violations

    return [str(v) for v in journal_history_violations(JournalEvent.from_envelope(e) for e in session)]


def log_problems(run: Run, node_log: Path, redis_port: int) -> list[str]:
    """C-5: no log line holds content or a secret, and the run can be followed by its id."""
    credential = stack.worker_credential(KV_PREFIX, redis_port=redis_port)
    node = stack.log_records("node", node_log.read_text(errors="replace"))
    worker = stack.log_records("worker", (run.run_dir / "worker.log").read_text(errors="replace"))
    problems = [
        f"{r.source} {r.event} logs content or a secret"
        for r in [*node, *worker]
        if any(s in r.text for s in (*stack.SENTINELS, credential))
    ]
    ours = {(r.source, r.event) for r in [*node, *worker] if run.run_id in r.text}
    problems += [f"no {s} {e} line names the run" for s, e in (("node", "admission.accepted"), ("worker", "run.claimed"))
                 if (s, e) not in ours]
    return problems


DEGRADED_SERVICES = ("toxiproxy", "redis-replica", "neo4j", "qdrant")


def start_rig(broker: str, out: Path) -> Rig:
    """The `degraded` profile up, a proxy per dependency, the fakes, and a Node reaching Mongo and Redis through toxiproxy."""
    ports = Ports(stack.DEGRADED_BASE)
    stack.compose("--profile", "degraded", "up", "-d", "--wait", "--wait-timeout", "240", *DEGRADED_SERVICES)
    toxi = Toxiproxy(ports.toxiproxy)
    stack.wait_for(toxi.ready, 60, "toxiproxy's API")
    # A failover in an earlier session promoted the replica: it follows the primary again.
    stack.compose("exec", "-T", "redis-replica", "redis-cli", "REPLICAOF", "redis", "6379")
    stack.wait_for(_replica_linked, 60, "the replica following the primary")
    upstreams = {
        "mongo": stack.MONGO_PORT, "redis": stack.REDIS_PORT, "llm": ports.llm, "embedding": ports.llm,
        "graph": ports.neo4j, "vector": ports.qdrant, "mcp": ports.mcp, "sandbox": ports.sandbox,
    }
    for name, upstream in upstreams.items():
        # The MCP URL guard refuses loopback: the run reaches the MCP server's proxy on the host's private address.
        toxi.create(name, ports.proxy(name), upstream, stack.mcp_host() if name == "mcp" else "127.0.0.1")
    processes = {
        "llm": fake_process("fake_llm.py", [str(ports.llm)], out / "fake-llm.log", "FAKE LLM READY"),
        "mcp": fake_process("mcp_server.py", [str(ports.mcp)], out / "mcp-server.log", "MCP SERVER READY"),
        "sandbox": fake_process("sandbox_broker.py", [str(ports.sandbox), SANDBOX_TOKEN], out / "sandbox-broker.log",
                                "SANDBOX BROKER READY"),
    }
    for name, process in processes.items():
        process.start()
        wait_port(getattr(ports, name))
    node_url = f"http://127.0.0.1:{ports.node}"
    env = {
        **stack.stack_env(broker, f"harness_degraded_{broker}_{int(time.time())}"),
        "MONGO_URI": f"mongodb://admin:password@127.0.0.1:{ports.proxy('mongo')}/?authSource=admin&directConnection=true&w=majority",
        "REDIS_PORT": str(ports.proxy("redis")),
        "REDIS_KV_PREFIX": KV_PREFIX,
        "AGENT_HARNESS_STATE_URL": node_url,
    }
    node_log = out / "node.log"
    node = stack.node_process(env, node_log, port=ports.node)
    node.start()
    return Rig(broker, ports, toxi, env, node, node_log, out, processes)


def _replica_linked() -> bool:
    info = stack.compose("exec", "-T", "redis-replica", "redis-cli", "info", "replication", check=False).stdout
    return "master_link_status:up" in info


def stop_rig(rig: Rig) -> None:
    rig.node.kill9()
    for process in rig.processes.values():
        process.kill9()
    with contextlib.suppress(OSError):
        rig.toxi.reset()


def fake_process(script: str, args: list[str], log: Path, ready: str) -> stack.Process:
    import sys

    return stack.Process(script, [sys.executable, str(stack.HERE / script), *args], stack.PYTHON_ROOT,
                         {**stack.stack_env("redis", "unused")}, log, ready)


def wait_port(port: int, timeout: float = 60) -> None:
    import socket

    def accepting() -> bool:
        with contextlib.closing(socket.socket()) as probe:
            probe.settimeout(1)
            return probe.connect_ex(("127.0.0.1", port)) == 0

    stack.wait_for(accepting, timeout, f"127.0.0.1:{port} listening")


def elapsed_since(start: float) -> float:
    return time.monotonic() - start
