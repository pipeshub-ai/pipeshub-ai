"""The Neo4j + Qdrant integration lane (C13.01's store leg, G-49), on throwaway containers.

    cd backend/python && python -m tests.integration.store_lane [--only graph|vector] [--report DIR]

Starts ``pxh-storelane-<id>-{neo4j,qdrant,redis}`` (labelled ``pxh.lane=storelane``, published on loopback ports
the host picks), runs what backend-matrix runs against them, and removes them however the run ends:

- graph: every file under tests/integration that reads ``NEO4J_IT_URI`` or ``ARANGO_IT_URL``, or the harness
  suites' ``NEO4J_TEST_URI`` or ``ARANGO_TEST_URL``, or connects through ``tests/integration/real_graph.py``, its
  ``neo4j`` cases (the seeded-tenant access parity, the
  harness's agent, toolset, knowledge and record-access suites among them), with Redis and Qdrant for the cases that
  also index or publish;
- vector: tests/integration/vector_db and the multimodal indexing suite, their ``qdrant`` cases.

As in CI, a case that skips fails the lane: the service was there, so a skip is a case that did not run. Exit 0 only
when every step ran with no failure and no skip. The images must already be on the host (nothing is pulled).
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

PYTHON_ROOT = Path(__file__).resolve().parents[2]
LABEL = "pxh.lane"
NEO4J_PASSWORD = "ensure-it-pass"
# The images deployment/docker-compose/docker-compose.integration.*.yml pin, then what a host commonly has instead.
IMAGES = {
    "neo4j": ("neo4j:5.26.0", "neo4j:5.26"),
    "qdrant": ("qdrant/qdrant:v1.15",),
    "redis": ("redis:7.2-alpine", "redis:7-alpine", "redis:7.4-alpine", "redis:7"),
}
# backend-matrix's graph jobs' ``discover``.
DISCOVER = re.compile(r"NEO4J_IT_URI|ARANGO_IT_URL|NEO4J_TEST_URI|ARANGO_TEST_URL|tests\.integration\.real_graph")


@dataclass
class Box:
    name: str
    container: Any
    ports: dict[str, int] = field(default_factory=dict[str, int])


def _image(client: Any, kind: str) -> str:  # noqa: ANN401 - docker.DockerClient
    for tag in IMAGES[kind]:
        with contextlib.suppress(Exception):
            client.images.get(tag)
            return tag
    raise SystemExit(f"store lane: none of {IMAGES[kind]} is on this host (the lane pulls nothing)")


def _start(client: Any, run: str, kind: str, ports: list[str], **kwargs: Any) -> Box:  # noqa: ANN401
    name = f"pxh-storelane-{run}-{kind}"
    container = client.containers.run(
        _image(client, kind), name=name, detach=True, labels={LABEL: "storelane", "pxh.run": run},
        ports=dict.fromkeys(ports, ("127.0.0.1", None)), **kwargs,
    )
    container.reload()
    published = {
        p: int(container.attrs["NetworkSettings"]["Ports"][p][0]["HostPort"]) for p in ports
    }
    return Box(name, container, published)


def _wait(what: str, check: Any, timeout_s: float = 180) -> None:  # noqa: ANN401 - a no-arg callable
    deadline = time.monotonic() + timeout_s
    last: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except Exception as exc:
            last = exc
        time.sleep(1)
    raise SystemExit(f"store lane: {what} did not come up in {timeout_s:.0f} s ({last!r})")


def _tcp(port: int) -> bool:
    with socket.create_connection(("127.0.0.1", port), timeout=2):
        return True


def _neo4j_ready(port: int) -> bool:
    from neo4j import GraphDatabase

    with GraphDatabase.driver(f"bolt://127.0.0.1:{port}", auth=("neo4j", NEO4J_PASSWORD)) as driver:
        driver.verify_connectivity()
    return True


def _qdrant_ready(port: int) -> bool:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz", timeout=2) as response:
        return response.status == 200


@contextlib.contextmanager
def stores(run: str) -> Iterator[dict[str, Box]]:
    import docker

    client = docker.from_env()
    boxes: dict[str, Box] = {}
    try:
        boxes["neo4j"] = _start(
            client, run, "neo4j", ["7687/tcp"],
            environment={"NEO4J_AUTH": f"neo4j/{NEO4J_PASSWORD}", "NEO4J_db_lock_acquisition_timeout": "10s"},
            ulimits=[docker.types.Ulimit(name="nofile", soft=60000, hard=60000)],
        )
        boxes["qdrant"] = _start(client, run, "qdrant", ["6333/tcp"])
        boxes["redis"] = _start(client, run, "redis", ["6379/tcp"], command=["redis-server", "--save", ""])
        _wait("redis", lambda: _tcp(boxes["redis"].ports["6379/tcp"]))
        _wait("qdrant", lambda: _qdrant_ready(boxes["qdrant"].ports["6333/tcp"]))
        _wait("neo4j", lambda: _neo4j_ready(boxes["neo4j"].ports["7687/tcp"]))
        yield boxes
    finally:
        for box in boxes.values():
            with contextlib.suppress(Exception):
                box.container.remove(force=True, v=True)
        client.close()


def _env(boxes: dict[str, Box]) -> dict[str, str]:
    neo4j, qdrant, redis = boxes["neo4j"].ports["7687/tcp"], boxes["qdrant"].ports["6333/tcp"], boxes["redis"].ports["6379/tcp"]
    return {
        **os.environ,
        "PYTHONPATH": str(PYTHON_ROOT),
        "NEO4J_IT_URI": f"bolt://127.0.0.1:{neo4j}",
        "NEO4J_IT_PASSWORD": NEO4J_PASSWORD,
        "NEO4J_TEST_URI": f"bolt://127.0.0.1:{neo4j}",
        "NEO4J_TEST_PASSWORD": NEO4J_PASSWORD,
        "REDIS_IT_HOST": "127.0.0.1",
        "REDIS_IT_PORT": str(redis),
        "QDRANT_IT_HOST": "127.0.0.1",
        "QDRANT_IT_PORT": str(qdrant),
        # The vector suite's own names; REST, so no fixed gRPC port is needed on the host.
        "QDRANT_HOST": "127.0.0.1",
        "QDRANT_PORT": str(qdrant),
        "QDRANT_PREFER_GRPC": "false",
        "QDRANT_HTTP_PORT": str(qdrant),
    }


def discovered(pattern: re.Pattern[str] = DISCOVER) -> list[str]:
    return [
        path.relative_to(PYTHON_ROOT).as_posix()
        for path in sorted((PYTHON_ROOT / "tests" / "integration").rglob("test_*.py"))
        if pattern.search(path.read_text(encoding="utf-8", errors="replace"))
    ]


@dataclass
class Step:
    name: str
    paths: list[str]
    select: str


def steps(only: str | None) -> list[Step]:
    planned = [
        Step("graph", discovered(), "neo4j"),
        Step("vector", ["tests/integration/vector_db", "tests/integration/test_multimodal_indexing.py"], "qdrant"),
    ]
    return [s for s in planned if only in (None, s.name)]


def _counts(report: Path) -> dict[str, int]:
    root = ET.parse(report).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    assert suite is not None
    return {k: int(suite.get(k, 0)) for k in ("tests", "failures", "errors", "skipped")}


def run_step(step: Step, env: dict[str, str], report_dir: Path) -> tuple[bool, str]:
    report = report_dir / f"store-lane-{step.name}.xml"
    started = time.monotonic()
    code = subprocess.call(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *step.paths, "-m", "integration",
         "-k", step.select, "-rs", "--timeout=300", f"--junitxml={report}"],
        cwd=PYTHON_ROOT, env=env,
    )
    took = time.monotonic() - started
    counts = _counts(report) if report.exists() else {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    ok = code == 0 and counts["tests"] > 0 and counts["skipped"] == 0
    return ok, f"{step.name}: exit {code}, {counts}, {took:.0f} s"


def _interrupted(signum: int, _frame: object) -> None:
    raise KeyboardInterrupt(f"signal {signum}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", choices=["graph", "vector"])
    parser.add_argument("--report", type=Path, default=Path(tempfile.gettempdir()) / "pxh-store-lane")
    args = parser.parse_args(argv)
    # Teardown runs on Ctrl-C and on a kill by pid alike.
    signal.signal(signal.SIGTERM, _interrupted)
    args.report.mkdir(parents=True, exist_ok=True)
    run = uuid.uuid4().hex[:8]
    started = time.monotonic()
    results: list[tuple[bool, str]] = []
    with stores(run) as boxes:
        print(f"store lane {run}: " + ", ".join(f"{k} 127.0.0.1:{next(iter(b.ports.values()))}" for k, b in boxes.items()),
              flush=True)
        env = _env(boxes)
        for step in steps(args.only):
            results.append(run_step(step, env, args.report))
            print(results[-1][1], flush=True)
    print(f"store lane {run}: {'PASS' if all(ok for ok, _ in results) else 'FAIL'} in {time.monotonic() - started:.0f} s "
          "(containers removed)")
    return 0 if results and all(ok for ok, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
