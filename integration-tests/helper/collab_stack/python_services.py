"""The real Python services (query, connectors) for the lane's real-Python mode.

``PCC_E2E_REAL_PYTHON=1`` starts ``app.connectors_main`` and ``app.query_main`` from source on the host with the repo
venv, against the lane's own graph (Neo4j by default, ``PCC_E2E_GRAPH=arangodb`` for Arango), Mongo, Redis and Qdrant.
The Node API then calls them instead of the fake. The fake stays up for what is not started here (indexing) and serves the
OpenAI-compatible endpoint the query service uses as its LLM and embedding model.

Not started: indexing, docling, parsing, extraction, embedding. Nothing in these journeys indexes a document, and the
configured embedding model is a remote (OpenAI-compatible) one, so no local model is loaded.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import requests

from helper.collab_stack.infra import Endpoints, wait_until
from helper.collab_stack.node_api import SECRET_KEY, free_port

logger = logging.getLogger(__name__)

REAL_PYTHON_ENV = "PCC_E2E_REAL_PYTHON"
GRAPH_ENV = "PCC_E2E_GRAPH"
REPO_ROOT = Path(__file__).resolve().parents[3]
PYTHON_ROOT = REPO_ROOT / "backend" / "python"

NEO4J_PASSWORD = "pcc-e2e-neo4j-pass"
ARANGO_PASSWORD = "pcc-e2e-arango-pass"
ARANGO_DB = "pcc_e2e"
QDRANT_API_KEY = "pcc-e2e-qdrant-key"

BOOT_TIMEOUT_S = 240


def real_python_enabled() -> bool:
    return os.environ.get(REAL_PYTHON_ENV, "").lower() in ("1", "true", "yes")


def graph_backend() -> str:
    """``neo4j`` (default) or ``arangodb``."""
    value = os.environ.get(GRAPH_ENV, "neo4j").lower()
    if value in ("arango", "arangodb"):
        return "arangodb"
    if value == "neo4j":
        return "neo4j"
    raise ValueError(f"{GRAPH_ENV} must be neo4j or arangodb, not {value!r}")


def _main_checkout() -> Path | None:
    """The checkout that owns this worktree's git directory (the venv usually lives there, not in a linked worktree)."""
    try:
        common = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return (REPO_ROOT / common).resolve().parent


def python_binary() -> Path:
    """The interpreter that runs the services: ``PCC_E2E_SERVICES_PYTHON``, else the repo venv (``backend/python/venv``), also looked
    for in the main checkout when this is a linked worktree."""
    explicit = os.environ.get("PCC_E2E_SERVICES_PYTHON")
    if explicit:
        return Path(explicit)
    candidates = [PYTHON_ROOT / "venv" / "bin" / "python"]
    main = _main_checkout()
    if main is not None:
        candidates.append(main / "backend" / "python" / "venv" / "bin" / "python")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"no services venv: create backend/python/venv (CONTRIBUTING.md) or set PCC_E2E_SERVICES_PYTHON; looked in {candidates}")


def graph_env(graph: str, endpoints: Endpoints) -> dict[str, str]:
    """The graph and vector settings, given to the Python services and (for Arango and Qdrant) to Node too: Node writes
    them into the KV store at boot, and the Python services read the KV store before their environment."""
    env = {
        "DATA_STORE": graph,
        "VECTOR_DB_TYPE": "qdrant",
        "QDRANT_HOST": "127.0.0.1",
        "QDRANT_PORT": str(endpoints.qdrant_port),
        "QDRANT_GRPC_PORT": str(endpoints.qdrant_grpc_port),
        "QDRANT_API_KEY": QDRANT_API_KEY,
    }
    if graph == "neo4j":
        env.update(
            {
                "NEO4J_URI": f"bolt://127.0.0.1:{endpoints.graph_port}",
                "NEO4J_USERNAME": "neo4j",
                "NEO4J_PASSWORD": NEO4J_PASSWORD,
                "NEO4J_DATABASE": "neo4j",
            }
        )
    else:
        env.update(
            {
                "ARANGO_URL": f"http://127.0.0.1:{endpoints.graph_port}",
                "ARANGO_USERNAME": "root",
                "ARANGO_PASSWORD": ARANGO_PASSWORD,
                "ARANGO_DB_NAME": ARANGO_DB,
            }
        )
    return env


@dataclass
class PythonService:
    """One uvicorn process running a service module from source. ``restart`` keeps the port."""

    name: str
    module: str
    port: int
    env: dict[str, str]
    log_dir: Path
    proc: subprocess.Popen[bytes] | None = None
    boots: int = 0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def log_path(self) -> Path:
        return self.log_dir / f"{self.name}-{self.port}.log"

    def start(self) -> None:
        assert self.proc is None or self.proc.poll() is not None, f"{self.name} already running"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.boots += 1
        log = open(self.log_path, "ab")  # noqa: SIM115 - handed to the child process
        log.write(f"\n===== boot {self.boots} =====\n".encode())
        log.flush()
        self.proc = subprocess.Popen(
            [str(python_binary()), "-m", "uvicorn", f"{self.module}:app", "--host", "127.0.0.1", "--port", str(self.port), "--log-level", "info"],
            cwd=PYTHON_ROOT,
            env=self.env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        log.close()
        self.wait_ready()

    def wait_ready(self, timeout: float = BOOT_TIMEOUT_S) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            assert self.proc is not None
            if self.proc.poll() is not None:
                raise RuntimeError(f"{self.name} exited with {self.proc.returncode}; see {self.log_path}\n{self.log_tail(40)}")
            try:
                if requests.get(f"{self.base_url}/health", timeout=2).status_code == 200:
                    return
            except requests.RequestException:
                pass
            time.sleep(1.0)
        raise TimeoutError(f"{self.name} not healthy on :{self.port} after {timeout}s\n{self.log_tail(40)}")

    def stop(self, timeout: float = 30) -> None:
        proc, self.proc = self.proc, None
        if proc is None or proc.poll() is not None:
            return
        os.killpg(proc.pid, signal.SIGTERM)
        deadline = time.monotonic() + timeout
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.2)
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)

    def restart(self) -> None:
        self.stop()
        self.start()

    def log_tail(self, lines: int = 60) -> str:
        try:
            return "\n".join(self.log_path.read_text(errors="replace").splitlines()[-lines:])
        except OSError:
            return ""


class PythonServices:
    """The connectors and query services of one lane."""

    def __init__(self, endpoints: Endpoints, graph: str, run_dir: Path, tmpdir: str) -> None:
        self.endpoints = endpoints
        self.graph_backend = graph
        self.connectors_port = free_port()
        self.query_port = free_port()
        self.run_dir = run_dir
        self.tmpdir = tmpdir
        self.connectors: PythonService | None = None
        self.query: PythonService | None = None

    @property
    def connectors_url(self) -> str:
        return f"http://127.0.0.1:{self.connectors_port}"

    @property
    def query_url(self) -> str:
        return f"http://127.0.0.1:{self.query_port}"

    def env(self, name: str) -> dict[str, str]:
        keep = ("PATH", "HOME", "LANG", "LC_ALL", "USER")
        env = {k: v for k, v in os.environ.items() if k in keep}
        env.update(
            {
                "PYTHONPATH": str(PYTHON_ROOT),
                "PYTHONUNBUFFERED": "1",
                "TMPDIR": self.tmpdir,
                "TZ": "UTC",
                "LOG_LEVEL": "info",
                "SECRET_KEY": SECRET_KEY,
                "KV_STORE_TYPE": "redis",
                "REDIS_HOST": "127.0.0.1",
                "REDIS_PORT": str(self.endpoints.redis_port),
                "REDIS_URL": f"redis://127.0.0.1:{self.endpoints.redis_port}",
                "MESSAGE_BROKER": "redis",
                "MONGO_URI": self.endpoints.mongo_uri,
                "MONGO_DB_NAME": "pcc_e2e",
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "ANONYMIZED_TELEMETRY": "False",
                "OPIK_API_KEY": "",
                "SERVICE_NAME": name,
            }
        )
        env.update(graph_env(self.graph_backend, self.endpoints))
        return env

    def start_connectors(self) -> None:
        self.connectors = PythonService("connectors", "app.connectors_main", self.connectors_port, self.env("connectors"), self.run_dir)
        self.connectors.start()

    def start_query(self) -> None:
        self.query = PythonService("query", "app.query_main", self.query_port, self.env("query"), self.run_dir)
        self.query.start()

    def graph(self, *ops: dict) -> list:
        """Run graph operations (``graph_seed.py``) through the product's own provider; returns one result per operation."""
        spec_path = self.run_dir / f"graph-seed-{uuid.uuid4().hex[:8]}.json"
        spec_path.write_text(json.dumps({"ops": list(ops)}))
        done = subprocess.run(
            [str(python_binary()), str(Path(__file__).with_name("graph_seed.py")), str(spec_path)],
            cwd=PYTHON_ROOT,
            env=self.env("graph-seed"),
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        marker = "GRAPH_SEED_RESULT "
        line = next((ln for ln in done.stdout.splitlines() if ln.startswith(marker)), None)
        if done.returncode != 0 or line is None:
            raise RuntimeError(f"graph seed failed ({done.returncode}):\n{done.stdout[-1500:]}\n{done.stderr[-2500:]}")
        return json.loads(line[len(marker):])

    def stop(self) -> None:
        for service in (self.query, self.connectors):
            if service is not None:
                try:
                    service.stop()
                except Exception:  # noqa: BLE001 - keep tearing down
                    logger.exception("stopping %s failed", service.name)

    def log_tail(self, lines: int = 40) -> str:
        return "\n".join(f"--- {s.name} ---\n{s.log_tail(lines)}" for s in (self.connectors, self.query) if s is not None)
