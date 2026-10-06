"""The real Node API, started from source on the host."""

from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import time
from pathlib import Path

import requests

from helper.collab_stack.infra import Endpoints, wait_until

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_NODE_ROOT = REPO_ROOT / "backend" / "nodejs" / "apps"

DB_NAME = "pcc_e2e"
# Fixed test secrets: the API reads these env names before it generates its own, so the
# tests can mint the same tokens its middleware verifies.
SECRET_KEY = "pcc-e2e-secret-key-0123456789abcdef"
JWT_SECRET = "pcc-e2e-jwt-secret-0123456789abcdef-0123456789"
SCOPED_JWT_SECRET = "pcc-e2e-scoped-jwt-secret-0123456789abcdef-01"
COOKIE_SECRET = "pcc-e2e-cookie-secret-0123456789abcdef-0123"

BOOT_TIMEOUT_S = 180
MIGRATION_TIMEOUT_S = 90
# Written by `app.ts` when the post-start migrations have finished. Stopping the API before this line races them
# against the Mongo disconnect and leaves the completion flags unwritten.
MIGRATIONS_DONE = "Migration completed successfully"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def node_binary_dir() -> Path:
    return Path(os.environ.get("PCC_E2E_NODE_BIN", Path.home() / ".local" / "node" / "bin"))


class NodeApi:
    """One Express process. ``restart`` keeps the port, so clients stay valid."""

    def __init__(
        self,
        endpoints: Endpoints,
        fake_url: str,
        log_dir: Path,
        node_root: Path | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self.endpoints = endpoints
        self.fake_url = fake_url
        self.node_root = Path(node_root or os.environ.get("PCC_E2E_NODE_ROOT") or DEFAULT_NODE_ROOT)
        self.log_dir = log_dir
        self.extra_env = extra_env or {}
        self.port = free_port()
        self.proc: subprocess.Popen[bytes] | None = None
        self._boots = 0
        self._log_offset = 0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def log_path(self) -> Path:
        return self.log_dir / f"node-{self.port}.log"

    def env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("MONGO", "REDIS", "KAFKA", "ETCD", "NEO4J", "ARANGO"))}
        env["PATH"] = f"{node_binary_dir()}:{env.get('PATH', '')}"
        env.update(
            {
                "PORT": str(self.port),
                "NODE_ENV": "test",
                "LOG_LEVEL": "info",
                "SECRET_KEY": SECRET_KEY,
                "JWT_SECRET": JWT_SECRET,
                "SCOPED_JWT_SECRET": SCOPED_JWT_SECRET,
                "COOKIE_SECRET": COOKIE_SECRET,
                "KV_STORE_TYPE": "redis",
                "MESSAGE_BROKER": "redis",
                "DATA_STORE": "neo4j",
                "REDIS_HOST": "127.0.0.1",
                "REDIS_PORT": str(self.endpoints.redis_port),
                "REDIS_URL": f"redis://127.0.0.1:{self.endpoints.redis_port}",
                "MONGO_URI": self.endpoints.mongo_uri,
                "MONGO_DB_NAME": DB_NAME,
                "REPLICA_SET_AVAILABLE": os.environ.get("PCC_E2E_REPLICA_SET_AVAILABLE", "false"),
                "QUERY_BACKEND": self.fake_url,
                "CONNECTOR_BACKEND": self.fake_url,
                "INDEXING_BACKEND": self.fake_url,
                "FRONTEND_PUBLIC_URL": self.base_url,
                "MAX_REQUESTS_PER_MINUTE": "1000000",
                "MAX_AUTH_REQUESTS_PER_MINUTE": "1000000",
                "MAX_OAUTH_CLIENT_REQUESTS_PER_MINUTE": "1000000",
                "TZ": "UTC",
            }
        )
        env.update(self.extra_env)
        return env

    def start(self) -> None:
        assert self.proc is None or self.proc.poll() is not None, "node api already running"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._boots += 1
        log = open(self.log_path, "ab")  # noqa: SIM115 - handed to the child process
        log.write(f"\n===== boot {self._boots} =====\n".encode())
        log.flush()
        self._log_offset = self.log_path.stat().st_size
        self.proc = subprocess.Popen(
            [str(self.node_root / "node_modules" / ".bin" / "ts-node-transpile-only"), "src/index.ts"],
            cwd=self.node_root,
            env=self.env(),
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        log.close()
        self.wait_ready()

    def wait_ready(self) -> None:
        def ready() -> bool:
            assert self.proc is not None
            if self.proc.poll() is not None:
                raise RuntimeError(f"node api exited with {self.proc.returncode}; see {self.log_path}")
            return requests.get(f"{self.base_url}/api/v1/health", timeout=2).status_code == 200

        wait_until(ready, BOOT_TIMEOUT_S, interval=0.5, message=f"node api on :{self.port}")
        wait_until(self._migrations_done, MIGRATION_TIMEOUT_S, interval=0.25, message="the post-start migrations to finish")

    def _migrations_done(self) -> bool:
        assert self.proc is not None
        if self.proc.poll() is not None:
            raise RuntimeError(f"node api exited with {self.proc.returncode}; see {self.log_path}")
        with open(self.log_path, "rb") as f:
            f.seek(self._log_offset)
            return MIGRATIONS_DONE.encode() in f.read()

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
