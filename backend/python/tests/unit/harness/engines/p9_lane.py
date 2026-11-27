"""The P9 lane: E1's durable lane with the dependencies a production deployment has, each one breakable.

* ``MongoNode``: the journal HTTP server in its Mongo mode (production Mongo adapters on a replica set),
  a process the lane can SIGKILL and start again on the same port and database (FM-6, FM-8, the BR drill);
  optionally relaying Node's outbox to Redis Streams (FM-1).
* ``BrokerService``: the sandbox broker as its own process behind the broker HTTP API (FM-2).
* ``RedisBox``: a lane-owned Redis container (the message broker of FM-1, the interaction wakeup of FM-7).
* ``MongoReplicaSet``: three ``mongod`` containers in one replica set, for a primary step-down (FM-8).

Everything it starts carries ``pipeshub.harness.owner=x-lead``, the ``pxh-p9-`` prefix and the lane's suffix,
and is removed by them at teardown.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from tests.unit.harness.engines.durable_stack import PYTHON_ROOT, durable_lane
from tests.unit.harness.engines.real_stack import OWNER

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from tests.unit.harness.engines.durable_stack import DurableLane

NODE_APP = PYTHON_ROOT.parent / "nodejs" / "apps"
SERVER = "tests/modules/agent-harness/support/journal-http-server.ts"
PROCESS_MONITOR = PYTHON_ROOT.parents[1] / "deployment" / "process_monitor.sh"


def shipped_node_flags() -> str:
    """The flags that make a host's Node run as the image's does (WP88-PF05-4; PERF-E3-5's async context frame is
    about 6% of Node's event-loop CPU a turn). The image's Node 24 (the Dockerfile's ``node:24``) has it by default and
    refuses the flag, so ``process_monitor.sh`` passes none; an older Node on the host needs it."""
    found = re.search(r"^\s*node ((?:--[\w-]+ )+)dist/index\.js", PROCESS_MONITOR.read_text(), re.M)
    flags = found.group(1).strip() if found else ""
    major = re.search(r"^FROM node:(\d+)", (PROCESS_MONITOR.parents[1] / "Dockerfile").read_text(), re.M)
    if major and int(major.group(1)) >= 24 and "--experimental-async-context-frame" not in flags.split():
        flags = f"{flags} --experimental-async-context-frame".strip()
    return flags


def journal_server_env(host: str, port: int, uri: str, database: str, state_dir: Path,
                       broker_redis: str | None = None) -> dict[str, str]:
    """The environment ``MongoNode`` starts Node's journal server with."""
    env = {
        **os.environ,
        "TS_NODE_PROJECT": "tsconfig.test.json",
        "JOURNAL_HTTP_HOST": host,
        "JOURNAL_HTTP_PORT": str(port),
        "JOURNAL_MONGO_URI": uri,
        "JOURNAL_MONGO_DB": database,
        "JOURNAL_STATE_DIR": str(state_dir),
        "REPLICA_SET_AVAILABLE": "true",
        "NODE_OPTIONS": f"{os.environ.get('NODE_OPTIONS', '')} {shipped_node_flags()}".strip(),
    }
    if broker_redis:
        env["JOURNAL_BROKER_REDIS"] = broker_redis
    return env
# What the journal server's Mongo mode admits runs to (object ids, as the product's admission takes).
ORG_ID = "66f6a6a6a6a6a6a6a6a6a6a1"
MONGO_URI_ENV = "MONGO_IT_URI"
REDIS_IMAGE = "redis:7.4-bookworm"
MONGO_IMAGE = "mongo:8.0.17"


def object_session(run_id: str) -> str:
    """A session id Mongo admission accepts, stable per run."""
    return hashlib.sha256(f"p9-session/{run_id}".encode()).hexdigest()[:24]


def mongo_uri() -> str | None:
    """The replica set the lanes use (the Node itests' ``MONGO_IT_URI``, without its database path)."""
    raw = os.environ.get(MONGO_URI_ENV, "").strip()
    if not raw:
        return None
    head, _, query = raw.partition("?")
    base = head.split("/", 3)
    return f"{base[0]}//{base[2]}/" + (f"?{query}" if query else "")


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class MongoNode:
    """Node's journal server in Mongo mode: start, SIGKILL, start again (same port, database, authority)."""

    host: str
    uri: str
    database: str
    state_dir: Path
    log: Path
    broker_redis: str | None = None
    port: int = 0
    credential: str = ""
    # ADR-CRED-01: the egress proxy's credential, the one a gateway token is minted with.
    proxy_credential: str = ""
    process: subprocess.Popen[bytes] | None = None
    control: httpx.Client = field(init=False)

    def __post_init__(self) -> None:
        if not self.port:
            import socket

            with socket.socket() as sock:
                sock.bind((self.host, 0))
                self.port = int(sock.getsockname()[1])
        self.control = httpx.Client(base_url=f"{self.base_url}/__control", timeout=30)
        self.start()

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self, timeout_s: float = 180) -> None:
        env = journal_server_env(self.host, self.port, self.uri, self.database, self.state_dir, self.broker_redis)
        seen = self.log.read_text(errors="replace").count("READY ") if self.log.exists() else 0
        with self.log.open("ab") as out:
            self.process = subprocess.Popen(
                ["npx", "ts-node", "--transpile-only", SERVER],
                cwd=NODE_APP,
                env=env,
                stdin=subprocess.PIPE,
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            lines = [line for line in self.log.read_text(errors="replace").splitlines() if line.startswith("READY ")]
            if len(lines) > seen:
                ready = lines[-1].split()
                self.credential = ready[2]
                self.proxy_credential = ready[3] if len(ready) > 3 else ready[2]
                return
            if self.process.poll() is not None:
                break
            time.sleep(0.3)
        raise RuntimeError(f"Node (Mongo mode) did not start: {self.log.read_text(errors='replace')[-2000:]}")

    def kill9(self) -> None:
        """A crash: the process group gets SIGKILL, nothing of it runs after."""
        if self.process is not None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGKILL)
            _ = self.process.wait()

    def stop(self) -> None:
        with contextlib.suppress(Exception):
            _ = httpx.post(f"{self.base_url}/__control/drop-database", timeout=30)
        if self.process is not None and self.process.poll() is None:
            if self.process.stdin is not None:
                self.process.stdin.close()
            try:
                _ = self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.kill9()


@dataclass
class BrokerService:
    """The sandbox broker's process (``p9_broker``), killable and restartable on its port."""

    config: dict[str, Any]
    workdir: Path
    process: subprocess.Popen[bytes] | None = None

    @property
    def url(self) -> str:
        return f"http://{self.config['host']}:{self.config['port']}"

    @property
    def token(self) -> str:
        return str(self.config["token"])

    def start(self, timeout_s: float = 90) -> None:
        path = self.workdir / "broker.json"
        path.write_text(json.dumps(self.config))
        log = self.workdir / "broker.log"
        seen = log.read_text(errors="replace").count("BROKER READY") if log.exists() else 0
        with log.open("ab") as out:
            self.process = subprocess.Popen(
                [sys.executable, "-m", "tests.unit.harness.engines.p9_broker", str(path)],
                cwd=PYTHON_ROOT,
                env={**os.environ, "PYTHONPATH": str(PYTHON_ROOT)},
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if log.read_text(errors="replace").count("BROKER READY") > seen:
                return
            if self.process.poll() is not None:
                break
            time.sleep(0.2)
        raise RuntimeError(f"the broker service did not start: {log.read_text(errors='replace')[-2000:]}")

    def kill9(self) -> None:
        if self.process is not None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGKILL)
            _ = self.process.wait()


@dataclass
class RedisBox:
    """A Redis container of the lane's own on a loopback port."""

    client: Any  # docker.DockerClient
    suffix: str
    port: int = field(default_factory=_free_port)
    name: str = field(init=False)

    def __post_init__(self) -> None:
        self.name = f"pxh-p9-redis-{self.suffix}-{uuid.uuid4().hex[:4]}"
        _ = self.client.containers.run(
            REDIS_IMAGE,
            ["redis-server", "--save", "", "--appendonly", "no"],
            name=self.name,
            detach=True,
            labels={**OWNER, "pipeshub.agent": "P9", "pxh.lane": self.suffix},
            ports={"6379/tcp": ("127.0.0.1", self.port)},
            mem_limit="128m",
        )
        self.wait_ready()

    @property
    def url(self) -> str:
        return f"redis://127.0.0.1:{self.port}/0"

    @property
    def address(self) -> str:
        return f"127.0.0.1:{self.port}"

    def container(self) -> Any:  # noqa: ANN401
        return self.client.containers.get(self.name)

    def wait_ready(self, timeout_s: float = 30) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with contextlib.suppress(Exception):
                if self.container().exec_run(["redis-cli", "ping"]).output.strip() == b"PONG":
                    return
            time.sleep(0.2)
        raise TimeoutError("lane Redis never answered")

    def pause(self) -> None:
        self.container().pause()

    def unpause(self) -> None:
        self.container().unpause()

    def stop(self) -> None:
        self.container().stop(timeout=1)

    def start(self) -> None:
        self.container().start()
        self.wait_ready()

    def remove(self) -> None:
        with contextlib.suppress(Exception):
            self.container().remove(force=True, v=True)


@dataclass
class MongoReplicaSet:
    """Three ``mongod`` members on loopback ports (host network, tmpfs data: no volume), one replica set."""

    client: Any  # docker.DockerClient
    suffix: str
    ports: tuple[int, int, int] = field(default_factory=lambda: (_free_port(), _free_port(), _free_port()))
    names: list[str] = field(default_factory=list[str])

    def __post_init__(self) -> None:
        for i, port in enumerate(self.ports):
            name = f"pxh-p9-mongo-{self.suffix}-{i}"
            _ = self.client.containers.run(
                MONGO_IMAGE,
                ["mongod", "--replSet", "p9rs", "--port", str(port), "--bind_ip", "127.0.0.1",
                 "--wiredTigerCacheSizeGB", "0.25", "--quiet"],
                name=name,
                detach=True,
                network_mode="host",
                # mongod refuses index builds with under 500 MB free on its dbpath; tmpfs pages are used only as written.
                tmpfs={"/data/db": "size=1g", "/data/configdb": "size=16m"},
                labels={**OWNER, "pipeshub.agent": "P9", "pxh.lane": self.suffix},
                mem_limit="768m",
            )
            self.names.append(name)
        members = [{"_id": i, "host": f"127.0.0.1:{p}", "priority": 2 if i == 0 else 1} for i, p in enumerate(self.ports)]
        config = json.dumps({"_id": "p9rs", "members": members})
        deadline = time.monotonic() + 60
        while True:
            out = self._eval(0, f"try {{ rs.initiate({config}).ok }} catch (e) {{ e.codeName }}")
            if "1" in out or "AlreadyInitialized" in out:
                break
            if time.monotonic() > deadline:
                raise TimeoutError(f"replica set never initiated: {out}")
            time.sleep(1)
        self.wait_primary()

    @property
    def uri(self) -> str:
        hosts = ",".join(f"127.0.0.1:{p}" for p in self.ports)
        return f"mongodb://{hosts}/?replicaSet=p9rs"

    def _eval(self, member: int, script: str) -> str:
        result = self.client.containers.get(self.names[member]).exec_run(
            ["mongosh", "--quiet", "--port", str(self.ports[member]), "--eval", script]
        )
        return str(result.output.decode(errors="replace"))

    def primary(self) -> int | None:
        for i in range(3):
            with contextlib.suppress(Exception):
                if self._eval(i, "db.hello().isWritablePrimary").strip() == "true":
                    return i
        return None

    def wait_primary(self, timeout_s: float = 90) -> int:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            found = self.primary()
            if found is not None:
                return found
            time.sleep(0.5)
        raise TimeoutError("no primary")

    def step_down(self) -> tuple[int, int]:
        """Steps the primary down (it stays out for 30 s); returns (old primary, new primary)."""
        old = self.wait_primary()
        _ = self._eval(old, "try { db.adminCommand({replSetStepDown: 30, secondaryCatchUpPeriodSecs: 10}) } catch (e) { e.codeName }")
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            found = self.primary()
            if found is not None and found != old:
                return old, found
            time.sleep(0.5)
        raise TimeoutError("no new primary after the step-down")

    def remove(self) -> None:
        for name in self.names:
            with contextlib.suppress(Exception):
                self.client.containers.get(name).remove(force=True, v=True)


@dataclass
class P9Lane:
    lane: DurableLane
    node: MongoNode
    suffix: str
    broker: BrokerService | None = None

    def broker_service(self) -> BrokerService:
        """Starts the sandbox broker as a service on the lane's namespace; workers then use it."""
        stack = self.lane.stack
        self.broker = BrokerService(
            {
                "host": "127.0.0.1",
                "port": _free_port(),
                "token": secrets.token_urlsafe(16),
                "namespace": self.lane.namespace,
                "prefix": self.lane.prefix,
                "proxy": stack.proxy,
                "control_url": self.lane.control_url,
                "control_token": stack.control_token,
            },
            self.lane.workdir,
        )
        self.broker.start()
        return self.broker

    def worker_overrides(self) -> dict[str, Any]:
        if self.broker is None:
            return {}
        return {"broker_url": self.broker.url, "broker_token": self.broker.token}


@contextlib.asynccontextmanager
async def p9_lane(
    workdir: Path,
    *,
    uri: str,
    broker_redis: str | None = None,
    e3_forwarder: bool = False,
    egress_state: bool = False,
) -> AsyncIterator[P9Lane]:
    """The durable lane on a Mongo-backed Node (database ``pxh_p9_<suffix>``, dropped at the end)."""
    suffix = uuid.uuid4().hex[:8]
    workdir.mkdir(parents=True, exist_ok=True)
    holder: dict[str, MongoNode] = {}

    def node(host: str) -> MongoNode:
        holder["node"] = MongoNode(
            host, uri, f"pxh_p9_{suffix}", workdir / "node-state", workdir / "node.log", broker_redis=broker_redis
        )
        return holder["node"]

    async with durable_lane(
        workdir / "lane",
        node_factory=node,
        org=ORG_ID,
        session_for=object_session,
        e3_forwarder=e3_forwarder,
        egress_state=egress_state,
    ) as lane:
        p9 = P9Lane(lane, holder["node"], suffix)
        try:
            yield p9
        finally:
            if p9.broker is not None:
                p9.broker.kill9()
            # Before the lane removes its network: Node listens on that network's address.
            with contextlib.suppress(Exception):
                async with httpx.AsyncClient(timeout=30) as client:
                    _ = await client.post(f"{p9.node.base_url}/__control/drop-database")


__all__ = [
    "MONGO_URI_ENV",
    "ORG_ID",
    "BrokerService",
    "MongoNode",
    "MongoReplicaSet",
    "P9Lane",
    "RedisBox",
    "mongo_uri",
    "object_session",
    "p9_lane",
]
