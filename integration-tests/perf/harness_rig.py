"""The L4 fault stack as a latency rig (QA-22; LAT1's rig, committed).

Node is ``fault-stack-server.ts`` (the production harness composition: admission, the run-ready notice, the Redis
LiveBus, the outbox, the stream routes) and the worker is ``integration-tests/harness/fault/worker.py``
(``run_worker``: the production claimer, driver and journal client) with a scripted model at a fixed token rate.
Both are started from ``stack.py``, so the rig and the crash catalog run the same processes.

Infrastructure: by default the fault compose project (MongoDB replica set, Redis) on ``--port-base``; with
``--mongo-uri`` and ``--redis-port`` an already running replica set and a Redis DB of the rig's own
(``--redis-db``): the rig refuses a DB that already holds keys, and clears only that DB when it is done, never
another (a FLUSHALL on the host's shared Redis wiped another stack's config: redisiso).
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

FAULT_DIR = Path(__file__).resolve().parents[1] / "harness" / "fault"


def _fault_stack() -> ModuleType:
    """The fault suite's ``stack.py`` under its own name: this directory has a ``stack`` module too."""
    spec = importlib.util.spec_from_file_location("fault_stack", FAULT_DIR / "stack.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["fault_stack"] = module
    spec.loader.exec_module(module)
    return module


stack = _fault_stack()

ORG = stack.ORG
AGENT = stack.AGENT
USER_PREFIX = "66f6a6a6a6a6a6a6a6a6"


@dataclass
class Infra:
    """Where the rig's Node and worker keep state."""

    mongo_uri: str | None = None
    redis_port: int | None = None
    redis_db: int = 0
    # The replica set's container, for dropping the rig's database (the backend venv has no Mongo driver).
    mongo_container: str = "pxh-mongo-rs"
    port_base: int = 31400
    broker: str = "redis"

    @property
    def managed(self) -> bool:
        return self.mongo_uri is None


def new_user() -> str:
    """A user id of the rig's org; a new one per sample keeps the admission quota and history out of the numbers."""
    return USER_PREFIX + uuid.uuid4().hex[:4]


def load1() -> float:
    return os.getloadavg()[0]


@dataclass
class Rig:
    """One Node and one worker on their own database; ``up`` returns when admission sees the worker live."""

    infra: Infra
    out: Path
    scenario: dict[str, Any]
    database: str = field(default_factory=lambda: f"pxh_qa22_{uuid.uuid4().hex[:8]}")
    # Node's commit timings (read, prepare, write ms) are debug lines.
    log_level: str = "info"
    # More of Node's environment for one profile (the CPU ledger's attribution, PERF-D).
    node_env: dict[str, str] = field(default_factory=dict)
    node: Any = None
    worker: Any = None
    claimed_redis: bool = False

    @property
    def base_url(self) -> str:
        return stack.NODE_URL

    def env(self) -> dict[str, str]:
        env = stack.stack_env(self.infra.broker, self.database)
        env["LOG_LEVEL"] = self.log_level
        if self.infra.mongo_uri is not None:
            env["MONGO_URI"] = self.infra.mongo_uri
        if self.infra.redis_port is not None:
            env["REDIS_PORT"] = str(self.infra.redis_port)
            env["REDIS_DB"] = str(self.infra.redis_db)
        return {**env, **self.node_env}

    def up(self) -> Rig:
        stack.configure(self.infra.port_base)
        if self.infra.managed:
            stack.infrastructure_up(self.infra.broker)
        self.out.mkdir(parents=True, exist_ok=True)
        # The processes append to their logs (a restart within a rig keeps its history); a rig starts on none, or a
        # second run of a profile into the same directory reads the first one's commits as its own (WP-88).
        for name in ("model-calls.jsonl", "deltas.jsonl", "dispositions.jsonl", "saas.jsonl", "worker.log", "node.log"):
            (self.out / name).unlink(missing_ok=True)
        scenario = {"leaseMs": 30_000, "dispositions": False, **self.scenario}
        (self.out / "scenario.json").write_text(json.dumps(scenario))
        self._claim_redis()
        env = self.env()
        self.node = stack.node_process(env, self.out / "node.log")
        self.node.start()
        self.worker = stack.start_live_worker(env, self.out)
        return self

    def down(self) -> None:
        for process in (self.worker, self.node):
            if process is not None:
                process.kill9()
        self._drop_database()
        self._release_redis()
        if self.infra.managed:
            stack.infrastructure_down()

    def __enter__(self) -> Rig:
        return self.up()

    def __exit__(self, *_: object) -> None:
        self.down()

    def _redis(self) -> Any:  # noqa: ANN401
        import redis

        return redis.Redis(host="127.0.0.1", port=int(self.infra.redis_port or 0), db=self.infra.redis_db)

    def _claim_redis(self) -> None:
        if self.infra.managed:
            # The fault compose project's own Redis.
            stack.compose("exec", "-T", "redis", "redis-cli", "FLUSHALL")
            return
        held = self._redis().dbsize()
        if held:
            raise RuntimeError(f"Redis 127.0.0.1:{self.infra.redis_port} DB {self.infra.redis_db} holds {held} keys: "
                               "the rig runs only on a DB it finds empty (pass an empty --redis-db)")
        self.claimed_redis = True

    def _release_redis(self) -> None:
        """Clears the DB the rig found empty, so the next run finds it empty too; nothing else."""
        if not self.infra.managed and self.claimed_redis:
            self._redis().flushdb()
            self.claimed_redis = False

    def _drop_database(self) -> None:
        if self.infra.mongo_uri is None:
            return
        _ = self.mongo_eval(f"db.getSiblingDB('{self.database}').dropDatabase()")

    def _uri_in_container(self) -> str:
        """``--mongo-uri`` as mongosh inside its container reaches the server: the host's published port is not the
        port mongod listens on in the container (a replica set on host networking keeps its own)."""
        uri = str(self.infra.mongo_uri)
        published = subprocess.run(["docker", "port", self.infra.mongo_container], capture_output=True, text=True,
                                   check=False, timeout=30).stdout
        return container_uri(uri, published)

    def mongo_eval(self, script: str) -> str | None:
        """What ``script`` printed in mongosh on the rig's replica set; None when it cannot be asked."""
        if self.infra.managed:
            argv = ["exec", "-T", "mongodb", "mongosh", "--quiet", "-u", "admin", "-p", "password",
                    "--authenticationDatabase", "admin", "admin", "--eval", script]
            done = stack.compose(*argv, check=False)
        else:
            done = subprocess.run(["docker", "exec", self.infra.mongo_container, "mongosh", "--quiet",
                                   self._uri_in_container(), "--eval", script],
                                  capture_output=True, text=True, check=False, timeout=60)
        return done.stdout if done.returncode == 0 else None

    def transactions(self) -> dict[str, int] | None:
        """MongoDB's own count of the transactions it committed and aborted so far; None when it cannot be asked."""
        printed = self.mongo_eval(_TRANSACTIONS)
        return None if printed is None else transaction_counts(printed)

    def process_ids(self) -> dict[str, int]:
        """The rig's own processes by role, for what they hold (RSS, open files) over a soak."""
        return {name: process.proc.pid for name, process in (("node", self.node), ("worker", self.worker))
                if process is not None and process.proc is not None and process.proc.poll() is None}

    def jsonl(self, name: str) -> list[dict[str, Any]]:
        return stack.jsonl(self.out / name)

    def worker_log(self) -> str:
        return (self.out / "worker.log").read_text(errors="replace")

    def node_log(self) -> str:
        return (self.out / "node.log").read_text(errors="replace")


def container_uri(uri: str, published: str) -> str:
    """``uri`` with its host port replaced by the container port ``docker port`` (``published``) maps it from."""
    host_port = re.search(r"@?[\w.\-]+:(\d+)/", uri)
    if host_port is None:
        return uri
    for line in published.splitlines():
        found = re.match(r"(\d+)/tcp -> [^:]*:(\d+)$", line.strip())
        if found and found.group(2) == host_port.group(1):
            start, end = host_port.span(1)
            return uri[:start] + found.group(1) + uri[end:]
    return uri


# ---- what MongoDB counted ----

_TRANSACTIONS = ("const t = db.serverStatus().transactions; "
                 "print(JSON.stringify({committed: Number(t.totalCommitted), aborted: Number(t.totalAborted)}))")


def transaction_counts(output: str) -> dict[str, int] | None:
    """The counters ``_TRANSACTIONS`` printed (the last JSON line of mongosh's output)."""
    for line in reversed(output.strip().splitlines()):
        try:
            counts = json.loads(line)
        except ValueError:
            continue
        if isinstance(counts, dict) and {"committed", "aborted"} <= counts.keys():
            return {"committed": int(counts["committed"]), "aborted": int(counts["aborted"])}
    return None


def aborts_per_commit(before: dict[str, int] | None, after: dict[str, int] | None) -> float | None:
    """Aborted transactions for each committed one between two readings: PERFB-F1's storm was 13 to 15, a run
    whose transactions do not abort each other is near 0. None when a reading is missing or nothing committed."""
    if before is None or after is None:
        return None
    committed = after["committed"] - before["committed"]
    return (after["aborted"] - before["aborted"]) / committed if committed > 0 else None


# ---- what the processes logged ----

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_KV = re.compile(r"\b([A-Za-z]+)=([^\s,]+)")


def commit_records(worker_log: str) -> list[dict[str, Any]]:
    """The worker's ``journal.commit`` lines: run, command kind, events and the round trip in ms."""
    out: list[dict[str, Any]] = []
    for line in worker_log.splitlines():
        if " journal.commit " not in line and not line.rstrip().endswith(" journal.commit"):
            continue
        fields = dict(_KV.findall(line))
        if "ms" not in fields or "runId" not in fields:
            continue
        command = fields.get("commandId", "")
        parts = command.split(":")
        out.append({
            "runId": fields["runId"],
            "kind": parts[1] if len(parts) > 2 else (parts[-1] if parts else "?"),
            "events": int(fields.get("events", "0") or 0),
            "ms": float(fields["ms"]),
        })
    return out


def node_events(node_log: str, event: str) -> list[dict[str, Any]]:
    """Node's lifecycle records named ``event``, with the ``metadata`` block that follows each."""
    text = _ANSI.sub("", node_log)
    found: list[dict[str, Any]] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not re.search(rf"\b{re.escape(event)}\b", line) or not re.match(r"^\d{4}-\d\d-\d\d ", line):
            continue
        body: list[str] = []
        for follow in lines[i + 1 :]:
            if re.match(r"^\d{4}-\d\d-\d\d ", follow):
                break
            body.append(follow)
        joined = "\n".join(body).strip()
        if joined.startswith("metadata:"):
            joined = joined[len("metadata:") :]
        try:
            found.append(json.loads(joined))
        except json.JSONDecodeError:
            found.append(dict(_KV.findall(line)))
    return found


def wait_until(predicate: Any, timeout_s: float, what: str) -> None:  # noqa: ANN401 - a zero-argument callable
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise TimeoutError(what)


def git_head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True,
                              cwd=FAULT_DIR).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


__all__ = ["AGENT", "ORG", "Infra", "Rig", "commit_records", "git_head", "load1", "new_user", "node_events",
           "wait_until"]
