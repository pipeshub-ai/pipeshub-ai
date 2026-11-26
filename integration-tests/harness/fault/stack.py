"""Real processes for the L4 fault suite: the Docker infrastructure, Node and the worker.

Node is ``backend/nodejs/apps/tests/support/harness/fault-stack-server.ts`` (the
production harness composition) under ts-node; the worker is ``worker.py``
(``app.harness_main.run_worker`` with a scripted runtime). Both read the same
environment the product reads. Crashes are SIGKILLs of the process group.
"""

from __future__ import annotations

import collections
import contextlib
import itertools
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
NODE_APP = REPO / "backend" / "nodejs" / "apps"
PYTHON_ROOT = REPO / "backend" / "python"
NODE_SERVER = NODE_APP / "tests" / "support" / "harness" / "fault-stack-server.ts"
# What the image starts Node with (deployment/process_monitor.sh): the rig and the crash catalog run Node as it ships.
NODE_FLAGS = "--experimental-async-context-frame"
PROJECT = "harness-fault"
MONGO_PORT, REDIS_PORT, KAFKA_PORT, NODE_PORT = 37017, 36379, 39092, 38000
NODE_URL = f"http://127.0.0.1:{NODE_PORT}"
# The `degraded` profile's ports and the degraded module's own (degraded.Ports): from here up, below Linux's
# ephemeral range (32768-60999), where a fake binding late can find its port taken by an outgoing connection.
DEGRADED_BASE = 29130


def configure(port_base: int | None) -> None:
    """Host ports from ``port_base`` (mongo, redis, kafka, node, then free for more Nodes), and a project per base."""
    global PROJECT, MONGO_PORT, REDIS_PORT, KAFKA_PORT, NODE_PORT, NODE_URL, DEGRADED_BASE
    if port_base is None:
        return
    # FAULT_PROJECT: a name of the caller's own, so its containers are told apart from a shared stack's.
    PROJECT = os.environ.get("FAULT_PROJECT") or f"harness-fault-{port_base}"
    MONGO_PORT, REDIS_PORT, KAFKA_PORT, NODE_PORT = port_base, port_base + 1, port_base + 2, port_base + 3
    NODE_URL = f"http://127.0.0.1:{NODE_PORT}"
    DEGRADED_BASE = port_base + 30


def mcp_host() -> str:
    """An address of this host a run may reach an MCP server on. The product's MCP URL guard refuses loopback in
    every mode (``app.agents.mcp.url_guard``) and allows a private address: the host's own, on the interface its
    default route leaves by (no packet is sent). A host with no private address cannot run an MCP scenario."""
    import ipaddress

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(("10.255.255.255", 1))
        address = str(probe.getsockname()[0])
    if not ipaddress.ip_address(address).is_private or ipaddress.ip_address(address).is_loopback:
        raise RuntimeError(f"this host's address {address} is not a private one: an MCP server URL needs one")
    return address


def extra_port(n: int) -> int:
    """The n-th port after Node's (a second Node, a balancer)."""
    return NODE_PORT + n
ORG = "66f6a6a6a6a6a6a6a6a6a6a1"
AGENT = "agent-1"
USER = "66f6a6a6a6a6a6a6a6a6a6b1"
SECRET_KEY = "harness-fault-suite-throwaway-secret-0123456789"
REDIS_KV_PREFIX = "pipeshub:kv:"
WORKER_CREDENTIAL_PATH = "/services/agentHarness/workerCredential"


def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    # REDIS_PASSWORD: the base file's redis service reads it; this project does not run that service.
    env = {
        **os.environ,
        "REDIS_PASSWORD": "",
        "FAULT_MONGO_PORT": str(MONGO_PORT),
        "FAULT_REDIS_PORT": str(REDIS_PORT),
        "FAULT_KAFKA_PORT": str(KAFKA_PORT),
        "FAULT_TOXI_PORT": str(DEGRADED_BASE),
        "FAULT_REDIS_REPLICA_PORT": str(DEGRADED_BASE + 1),
        "FAULT_NEO4J_PORT": str(DEGRADED_BASE + 2),
        "FAULT_QDRANT_PORT": str(DEGRADED_BASE + 3),
        "FAULT_NETWORK": network_name(),
    }
    argv = ["docker", "compose", "-p", PROJECT, "-f", str(HERE / "docker-compose.yml"), *args]
    done = subprocess.run(argv, env=env, check=False, capture_output=True, text=True)
    if check and done.returncode != 0:
        raise RuntimeError(f"docker compose {' '.join(args)} exited {done.returncode}: {done.stderr.strip()[-2000:]}")
    return done


def network_name() -> str:
    return f"{PROJECT}-rig"


def infrastructure_up(broker: str) -> None:
    sys.path.insert(0, str(PYTHON_ROOT))
    import docker

    from tests.support.docker_networks import create_rig_network

    create_rig_network(docker.from_env(), network_name(), labels={"pipeshub.harness.owner": PROJECT})
    profiles = ["--profile", "kafka"] if broker == "kafka" else []
    compose(*profiles, "up", "-d", "--wait", "--wait-timeout", "240")


def infrastructure_down() -> None:
    compose("--profile", "kafka", "--profile", "degraded", "down", "-v", "--remove-orphans", check=False)
    subprocess.run(["docker", "network", "rm", network_name()], check=False, capture_output=True)


def stack_env(broker: str, database: str) -> dict[str, str]:
    return {
        **os.environ,
        "MESSAGE_BROKER": broker,
        "KAFKA_BROKERS": f"127.0.0.1:{KAFKA_PORT}",
        "MONGO_URI": f"mongodb://admin:password@127.0.0.1:{MONGO_PORT}/?authSource=admin&directConnection=true&w=majority",
        "MONGO_DB_NAME": database,
        "SECRET_KEY": SECRET_KEY,
        "KV_STORE_TYPE": "redis",
        "REDIS_HOST": "127.0.0.1",
        "REDIS_PORT": str(REDIS_PORT),
        "REDIS_KV_PREFIX": REDIS_KV_PREFIX,
        "REPLICA_SET_AVAILABLE": "true",
        "AGENT_HARNESS_RELAY_ENABLED": "true",
        "AGENT_HARNESS_WORKER_ENABLED": "true",
        "AGENT_HARNESS_STATE_URL": NODE_URL,
        "LOG_LEVEL": "debug",
        "PORT": str(NODE_PORT),
        "TS_NODE_PROJECT": str(NODE_APP / "tsconfig.test.json"),
        "TS_NODE_TRANSPILE_ONLY": "true",
        "PYTHONPATH": str(PYTHON_ROOT),
    }


class Process:
    """A process group started with its output appended to ``log``, ready when ``ready`` is printed."""

    def __init__(self, name: str, argv: list[str], cwd: Path, env: dict[str, str], log: Path, ready: str) -> None:
        self.name, self.argv, self.cwd, self.env, self.log, self.ready = name, argv, cwd, env, log, ready
        self.proc: subprocess.Popen[bytes] | None = None

    def start(self, *, may_die: bool = False, timeout: float = 180) -> None:
        seen = self.log.read_text(errors="replace").count(self.ready) if self.log.exists() else 0
        with self.log.open("ab") as out:
            self.proc = subprocess.Popen(
                self.argv, cwd=self.cwd, env=self.env, stdout=out, stderr=subprocess.STDOUT, start_new_session=True
            )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.log.read_text(errors="replace").count(self.ready) > seen:
                return
            if self.proc.poll() is not None:
                if may_die:
                    return
                raise RuntimeError(f"{self.name} exited {self.proc.returncode}; see {self.log}")
            time.sleep(0.2)
        raise TimeoutError(f"{self.name} not ready in {timeout}s; see {self.log}")

    def term(self, timeout: float) -> float | None:
        """SIGTERM (a rolling deploy's stop); seconds until it exited, None if it outlived ``timeout``."""
        if self.proc is None or self.proc.poll() is not None:
            return 0.0
        started = time.monotonic()
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            return None
        return time.monotonic() - started

    def kill9(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait()

    def wait_dead(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                return True
            time.sleep(0.2)
        return False


@dataclass
class FaultStack:
    """One broker's session: Node running on the shared infrastructure, and where its files go."""

    broker: str
    env: dict[str, str]
    node: Process
    node_log: Path
    out: Path


def node_process(env: dict[str, str], log: Path, port: int | None = None) -> Process:
    """A Node on ``port`` (default the suite's), sharing ``env`` otherwise."""
    port = NODE_PORT if port is None else port
    ts_node = NODE_APP / "node_modules" / ".bin" / "ts-node"
    options = f"{env.get('NODE_OPTIONS', '')} {NODE_FLAGS}".strip()
    return Process(
        "node", [str(ts_node), str(NODE_SERVER)], NODE_APP, {**env, "PORT": str(port), "NODE_OPTIONS": options}, log,
        f"FAULT NODE READY {port}"
    )


def worker_process(env: dict[str, str], run_dir: Path) -> Process:
    argv = [sys.executable, str(HERE / "worker.py"), str(run_dir)]
    return Process("worker", argv, PYTHON_ROOT, env, run_dir / "worker.log", "worker.started")


def start_live_worker(env: dict[str, str], run_dir: Path, *, may_die: bool = False, base: str | None = None) -> Process:
    """A worker Node's admission sees: a new conversation goes to legacy while no worker was seen (LAT1)."""
    worker = worker_process(env, run_dir)
    worker.start(may_die=may_die)
    try:
        wait_for(lambda: worker_live(base=base), 60, "admission seeing a live worker")
    except BaseException:
        worker.kill9()
        raise
    return worker


def advertise_worker(env: dict[str, str], run_dir: Path, *, base: str | None = None) -> None:
    """Admission sees a live worker for its window, with none left running to claim (a scenario starts its own)."""
    start_live_worker(env, run_dir, base=base).kill9()


def worker_live(*, base: str | None = None) -> bool:
    return bool(http("GET", "/__fault/liveness/chat", base=base)["live"])


def http(
    method: str, path: str, body: object | None = None, *, base: str | None = None, user: str = USER
) -> dict[str, Any]:
    request = urllib.request.Request(
        (base or NODE_URL) + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"authorization": f"Bearer {ORG}.{user}", "content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def admit(
    query: str, key: str, *, route: str = "agent", base: str | None = None, user: str = USER
) -> dict[str, Any]:
    """An agent run (``quick``), or a built-in assistant run (``internal_search``)."""
    chat = route == "chat"
    body = {
        "idempotencyKey": key,
        "query": query,
        "recordIds": [],
        "filters": {},
        "attachments": [],
        "chatMode": "internal_search" if chat else "quick",
    }
    path = "/api/v1/conversations/runs" if chat else f"/api/v1/agents/{AGENT}/runs"
    return http("POST", path, body, base=base, user=user)["admission"]


def steer(session_id: str, query: str, key: str) -> dict[str, Any]:
    """A message sent to the agent run the session is running (P1b T2): queued into it, never a new run."""
    body = {"idempotencyKey": key, "query": query, "chatMode": "quick", "steer": True}
    return http("POST", f"/api/v1/agents/agent-1/conversations/{session_id}/runs", body)["admission"]


# Each surface's legacy stream route: MCP marks its requests, Slack is known by its internal route.
SURFACE_ROUTES = {
    "mcp": ("/api/v1/conversations/stream", {"x-pipeshub-surface": "mcp"}),
    "slack": ("/api/v1/conversations/internal/stream", {}),
}


class SurfaceTurn:
    """An external surface's chat turn on its legacy stream route, read line by line on a thread (P1b C-5, C-20)."""

    def __init__(self, query: str, surface: str) -> None:
        self.status: int | None = None
        self.lines: list[str] = []
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._read, args=(query, surface), daemon=True)
        self.thread.start()

    def _read(self, query: str, surface: str) -> None:
        path, headers = SURFACE_ROUTES[surface]
        request = urllib.request.Request(
            NODE_URL + path,
            method="POST",
            data=json.dumps({"query": query, "chatMode": "internal_search"}).encode(),
            headers={"authorization": f"Bearer {ORG}.{USER}", "content-type": "application/json", **headers},
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                self.status = response.status
                for raw in response:
                    self.lines.append(raw.decode())
        except BaseException as error:  # noqa: BLE001 - reported to the checker
            self.error = error

    def frames(self) -> list[dict[str, Any]]:
        return [json.loads(line[len("data: "):]) for line in "".join(self.lines).splitlines() if line.startswith("data: ")]

    def session_id(self) -> str | None:
        created = next((f for f in self.frames() if f.get("name") == "conversation_created"), None)
        return None if created is None else created["value"]["conversationId"]


class Balancer:
    """A TCP balancer in front of several Nodes, as a deployment's service is (RU-4).

    Each connection goes to the next backend that accepts it; a connection to a
    backend that goes away breaks, as it would behind a real balancer.
    """

    def __init__(self, port: int, backends: list[int]) -> None:
        self.port, self.backends = port, backends
        self._turn = itertools.cycle(range(len(backends)))
        self._server = socket.create_server(("127.0.0.1", port), reuse_port=False)
        self._stopped = False
        threading.Thread(target=self._accept, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def route_to(self, backends: list[int]) -> None:
        """New connections go to ``backends`` from now on, as when a deployment's service drops a pod."""
        self.backends = backends
        self._turn = itertools.cycle(range(len(backends)))

    def _backend(self) -> socket.socket | None:
        for _ in self.backends:
            port = self.backends[next(self._turn)]
            try:
                return socket.create_connection(("127.0.0.1", port), timeout=2)
            except OSError:
                continue
        return None

    def _accept(self) -> None:
        while not self._stopped:
            try:
                client, _ = self._server.accept()
            except OSError:
                return
            backend = self._backend()
            if backend is None:
                client.close()
                continue
            backend.settimeout(None)
            for source, sink in ((client, backend), (backend, client)):
                threading.Thread(target=self._pump, args=(source, sink), daemon=True).start()

    @staticmethod
    def _pump(source: socket.socket, sink: socket.socket) -> None:
        try:
            while chunk := source.recv(65536):
                sink.sendall(chunk)
        except OSError:
            pass  # the other side went away; closing both ends below tells the peer
        finally:
            for end in (source, sink):
                try:
                    end.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass  # already closed
                end.close()

    def close(self) -> None:
        self._stopped = True
        # A close alone leaves the port listening while the accept thread is blocked on it (the next broker's
        # session then cannot bind it); a shutdown wakes the accept.
        with contextlib.suppress(OSError):
            self._server.shutdown(socket.SHUT_RDWR)
        self._server.close()


class ResumingStream:
    """A browser's owner stream of one agent run: reconnects with Last-Event-ID whenever its connection breaks (RU-4).

    ``arrivals`` holds the arrival time of every frame; the longest gap between
    two is what the user saw as a stall.
    """

    def __init__(self, base: str, session_id: str, run_id: str, deadline_s: float, *, user: str = USER) -> None:
        self.base, self.session_id, self.run_id, self.user = base, session_id, run_id, user
        self.started = time.monotonic()
        self.arrivals: list[float] = []
        self.frames: list[dict[str, Any]] = []
        self.reconnects = 0
        self.finished = False
        self.error: str | None = None
        self._deadline = self.started + deadline_s
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def max_gap_s(self) -> float:
        times = [self.started, *self.arrivals]
        return max((b - a for a, b in itertools.pairwise(times)), default=0.0)

    def _run(self) -> None:
        last_id: str | None = None
        while not self.finished and time.monotonic() < self._deadline:
            headers = {"authorization": f"Bearer {ORG}.{self.user}", "accept": "text/event-stream"}
            if last_id is not None:
                headers["last-event-id"] = last_id
            request = urllib.request.Request(
                f"{self.base}/api/v1/agents/{AGENT}/conversations/{self.session_id}/runs/{self.run_id}/stream",
                headers=headers,
            )
            frame_id: str | None = None
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    for raw in response:
                        line = raw.decode().rstrip("\n")
                        if line.startswith("id: "):
                            frame_id = line[len("id: "):]
                        elif line.startswith("data: "):
                            frame = json.loads(line[len("data: "):])
                            self.arrivals.append(time.monotonic())
                            self.frames.append(frame)
                            last_id = frame_id
                            if frame.get("type") in ("RUN_FINISHED", "RUN_ERROR"):
                                self.finished = True
            except (OSError, urllib.error.URLError, ValueError) as error:
                self.error = type(error).__name__
            if not self.finished:
                self.reconnects += 1
                time.sleep(0.2)


def replayed_frames(session_id: str, run_id: str, *, base: str | None = None) -> list[dict[str, Any]]:
    """The owner's stream of a finished run, replayed whole from the journal."""
    request = urllib.request.Request(
        f"{base or NODE_URL}/api/v1/conversations/{session_id}/runs/{run_id}/stream",
        headers={"authorization": f"Bearer {ORG}.{USER}"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        body = response.read().decode()
    return [json.loads(line[len("data: "):]) for line in body.splitlines() if line.startswith("data: ")]


def run_document(run_id: str, *, base: str | None = None) -> dict[str, Any] | None:
    return http("GET", f"/__fault/run/{run_id}", base=base)["run"]


def journal(session_id: str, *, base: str | None = None) -> list[dict[str, Any]]:
    return http("GET", f"/__fault/journal/{ORG}/{session_id}", base=base)["events"]


def conversation(session_id: str, *, base: str | None = None) -> dict[str, Any]:
    return http("GET", f"/__fault/conversation/{session_id}", base=base)


def cancel(session_id: str, run_id: str, *, base: str | None = None) -> dict[str, Any]:
    body = {"idempotencyKey": f"cancel-{run_id}"}
    return http("POST", f"/api/v1/agents/{AGENT}/conversations/{session_id}/runs/{run_id}/cancel", body, base=base)


def wait_for(predicate: Callable[[], bool], timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except OSError:
            pass  # Node restarting
        time.sleep(0.25)
    raise TimeoutError(what)


def jsonl(path: Path) -> list[dict[str, Any]]:
    """The records of a file a process may be appending to: a last line without its newline is still being written."""
    if not path.exists():
        return []
    complete = path.read_text().rpartition("\n")[0]
    return [json.loads(line) for line in complete.splitlines() if line.strip()]


def responses_per_turn(events: list[dict[str, Any]]) -> dict[int, int]:
    """``model.response`` events per turn of one run's journal (K-12 b: one each)."""
    responses: collections.Counter[int] = collections.Counter()
    turn = 0
    for event in events:
        if event["type"] == "turn.started":
            turn = int(event["data"]["turn"])
        elif event["type"] == "model.response":
            responses[turn] += 1
    return dict(responses)


def effects(run_dir: Path, run_id: str) -> list[dict[str, Any]]:
    """The run's external effects, from the fsynced fake-SaaS log."""
    return [e for e in jsonl(run_dir / "saas.jsonl") if e["payload"].get("run") == run_id]


def duplicate_effects(made: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
    """Effects made more than once, by operation and idempotency key (or invocation, for a write without one)."""
    keys = collections.Counter((e["op"], e["idempotency_key"] or e["payload"].get("invocation")) for e in made)
    return {k: n for k, n in keys.items() if n > 1}


# Text a run carries that must never reach a log line (with the tool result and the credential).
SENTINELS = (
    "SENTINEL-PROMPT", "SENTINEL-TOOLARG", "SENTINEL-ANSWER", "SENTINEL-STEER", "x" * 64, "eyJ", "Bearer ", SECRET_KEY,
)


def worker_credential(kv_prefix: str = REDIS_KV_PREFIX, *, redis_port: int | None = None) -> str:
    """The credential Node provisioned, decrypted as the worker reads it (for the log check).

    ``redis_port``: read where Node writes (the degraded rig's proxy, which a failover moves to the replica)."""
    sys.path.insert(0, str(PYTHON_ROOT))
    import hashlib

    from app.utils.encryption.encryption_service import EncryptionService

    path = kv_prefix + WORKER_CREDENTIAL_PATH
    if redis_port is None:
        stored = compose("exec", "-T", "redis", "redis-cli", "--raw", "GET", path).stdout.strip()
    else:
        import redis

        stored = (redis.Redis(host="127.0.0.1", port=redis_port).get(path) or b"").decode()
    key = hashlib.sha256(SECRET_KEY.encode()).hexdigest()
    return json.loads(EncryptionService("aes-256-gcm", key, None).decrypt(stored))["current"]


# Node's and the worker's records both start `<date> <time> - <logger> - <LEVEL> - `, then
# optional bracketed context (`[req:… task:…] [logs.py:108]`), then ` - <event>`.
_RECORD = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[,.]\d{3} - (\S+) - ([A-Z]+) - (?:(?:\[[^\]]*\] ?)+- )?(\S*)", re.M)


@dataclass(frozen=True)
class LogRecord:
    source: str
    logger: str
    level: str
    event: str
    text: str


_ANSI = re.compile(r"\x1b\[[0-9;]*m")  # Node colours its level names


def log_records(source: str, text: str) -> list[LogRecord]:
    text = _ANSI.sub("", text)
    starts = list(_RECORD.finditer(text))
    records = []
    for i, match in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        records.append(LogRecord(source, match.group(1), match.group(2), match.group(3), text[match.start() : end]))
    return records


# How an attempt ended without ending the run: a drained attempt records one of these before the last attempt does.
HANDED_BACK = {"yielded", "released", "lost"}


def log_mark(log: Path) -> int:
    """Where ``log`` ends now, for reading only what a test adds to a log its session shares."""
    return log.stat().st_size if log.exists() else 0


def log_since(source: str, log: Path, mark: int) -> list[LogRecord]:
    with log.open("rb") as f:
        f.seek(mark)
        return log_records(source, f.read().decode(errors="replace"))


def stream_groups(stream: str) -> list[str]:
    """The consumer groups of a Redis stream; none when the stream does not exist."""
    if compose("exec", "-T", "redis", "redis-cli", "--raw", "EXISTS", stream).stdout.strip() != "1":
        return []
    # One line per field name and per value, a group's fields starting with `name`.
    lines = compose("exec", "-T", "redis", "redis-cli", "--raw", "XINFO", "GROUPS", stream).stdout.splitlines()
    return [value for field, value in zip(lines, lines[1:], strict=False) if field == "name"]


def kafka_groups(topic: str, *, members: bool = False) -> list[str]:
    """The consumer groups named after ``topic`` (each subscriber's own); ``members``: only those a process is in."""
    listed = compose(
        "--profile", "kafka", "exec", "-T", "kafka", "/opt/kafka/bin/kafka-consumer-groups.sh",
        "--bootstrap-server", f"127.0.0.1:{KAFKA_PORT}", "--list", "--state",
    ).stdout
    rows = [line.split() for line in listed.splitlines() if line.strip()]
    named = [(row[0], row[-1]) for row in rows if row[0].replace("_", "-").startswith(topic)]
    return [name for name, state in named if not members or state not in ("Empty", "Dead")]
