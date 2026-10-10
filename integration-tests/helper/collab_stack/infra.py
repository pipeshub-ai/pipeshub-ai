"""Throwaway Mongo + Redis containers for the e2e lane.

Every container belongs to a compose project named ``pcc-e2e-<id>``. The project name is the only
handle used to stop things, so nothing outside it can be touched.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import redis
from pymongo import MongoClient

logger = logging.getLogger(__name__)

PROJECT_PREFIX = "pcc-e2e-"
COMPOSE_FILE = Path(__file__).resolve().parents[2] / "collaborative-chats" / "stack" / "docker-compose.yml"
_PROJECT_RE = re.compile(rf"^{PROJECT_PREFIX}[a-z0-9]+$")


def new_project_name() -> str:
    return f"{PROJECT_PREFIX}{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class Endpoints:
    mongo_port: int
    redis_port: int
    graph_port: int | None = None
    qdrant_port: int | None = None
    qdrant_grpc_port: int | None = None

    @property
    def mongo_uri(self) -> str:
        return f"mongodb://127.0.0.1:{self.mongo_port}/?replicaSet=rs0&directConnection=true"


class Infra:
    """Compose project holding Mongo 8 (single-node replica set) and Redis."""

    def __init__(self, project: str | None = None, graph: str | None = None) -> None:
        """``graph`` is ``"neo4j"`` or ``"arangodb"`` for the real-Python mode, which adds that graph and Qdrant to the project."""
        if graph not in (None, "neo4j", "arangodb"):
            raise ValueError(f"graph must be neo4j or arangodb, not {graph!r}")
        self.graph = graph
        self.project = project or os.environ.get("PCC_E2E_PROJECT") or new_project_name()
        if not _PROJECT_RE.match(self.project):
            raise ValueError(f"compose project must match {_PROJECT_RE.pattern}: {self.project!r}")
        self.endpoints: Endpoints | None = None
        self._mongo: MongoClient | None = None
        self._redis: redis.Redis | None = None

    def _compose(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["docker", "compose", "-p", self.project, "-f", str(COMPOSE_FILE), *self._profiles(), *args],
            check=check,
            capture_output=True,
            text=True,
            timeout=300,
        )

    def _profiles(self) -> list[str]:
        if not self.graph:
            return []
        return ["--profile", "real-python", "--profile", "neo4j" if self.graph == "neo4j" else "arango"]

    def up(self) -> Endpoints:
        logger.info("starting compose project %s", self.project)
        self._compose("up", "-d", "--wait")
        graph_service, graph_private = ("neo4j", 7687) if self.graph == "neo4j" else ("arango", 8529)
        self.endpoints = Endpoints(
            mongo_port=self._port("mongo", 27017),
            redis_port=self._port("redis", 6379),
            graph_port=self._port(graph_service, graph_private) if self.graph else None,
            qdrant_port=self._port("qdrant", 6333) if self.graph else None,
            qdrant_grpc_port=self._port("qdrant", 6334) if self.graph else None,
        )
        return self.endpoints

    def _port(self, service: str, private: int) -> int:
        out = self._compose("port", service, str(private)).stdout.strip()
        return int(out.rsplit(":", 1)[1])

    def down(self) -> None:
        self.close_clients()
        logger.info("removing compose project %s", self.project)
        self._compose("down", "-v", "--remove-orphans", check=False)

    def leftovers(self) -> list[str]:
        out = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={self.project}", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        return [n for n in out.split() if n]

    @property
    def mongo(self) -> MongoClient:
        assert self.endpoints, "infra is not up"
        if self._mongo is None:
            self._mongo = MongoClient(self.endpoints.mongo_uri, serverSelectionTimeoutMS=10_000, tz_aware=True)
        return self._mongo

    @property
    def redis(self) -> redis.Redis:
        assert self.endpoints, "infra is not up"
        if self._redis is None:
            self._redis = redis.Redis(host="127.0.0.1", port=self.endpoints.redis_port, decode_responses=True)
        return self._redis

    @contextmanager
    def failing_finds(self, db_name: str, collection: str, *, skip: int = 0, error_code: int = 2) -> Iterator[None]:
        """Mongo answers every ``find`` on one collection with an error (after ``skip`` successful ones) inside the block.

        Uses the ``failCommand`` fail point, which the compose file enables with ``enableTestCommands``. The error
        code is a non-retryable one, so the driver does not paper over it.
        """
        admin = self.mongo.admin
        data = {"failCommands": ["find"], "namespace": f"{db_name}.{collection}", "errorCode": error_code}
        admin.command("configureFailPoint", "failCommand", mode={"skip": skip} if skip else "alwaysOn", data=data)
        try:
            yield
        finally:
            admin.command("configureFailPoint", "failCommand", mode="off")

    def close_clients(self) -> None:
        if self._mongo is not None:
            self._mongo.close()
            self._mongo = None
        if self._redis is not None:
            self._redis.close()
            self._redis = None

    def wipe(self, db_name: str) -> None:
        """Drop the app database and every Redis key, as a fresh install would find them."""
        self.mongo.drop_database(db_name)
        self.redis.flushall()

    def describe(self) -> str:
        return json.dumps({"project": self.project, "endpoints": self.endpoints.__dict__ if self.endpoints else None})


def wait_until(predicate, timeout: float, interval: float = 0.25, message: str = "condition") -> None:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception as exc:  # noqa: BLE001 - polled until the deadline
            last_error = exc
        time.sleep(interval)
    raise TimeoutError(f"timed out after {timeout}s waiting for {message}" + (f" ({last_error})" if last_error else ""))
