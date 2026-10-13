"""Live collectors that run for the duration of a measured window.

Three of them, all started before the sync and stopped after it:

  ProbeCollector    constant-rate API latency, the metric that answers
                    "did the sync block the API"
  SampleCollector   per-process CPU/RSS inside the app container, plus
                    per-container stats for the stateful services
  ThroughputCollector  per-connector record counts over time

They write JSONL as they go rather than buffering, so a run that dies halfway
still leaves usable data behind.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

import requests

from .target import AGENT, Target


class _Loop(threading.Thread):
    """A stoppable, fixed-interval worker that appends JSONL to a file."""

    def __init__(self, path: Path, interval: float, name: str) -> None:
        super().__init__(name=name, daemon=True)
        self.path = path
        self.interval = interval
        # NOT `_stop`: threading.Thread has its own internal _stop() method, and
        # shadowing it with an Event makes Thread.join() raise once the thread
        # finishes ('Event' object is not callable).
        self._stopped = threading.Event()
        self.errors = 0

    def stop(self) -> None:
        self._stopped.set()

    def tick(self, out) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def run(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as out:
            while not self._stopped.is_set():
                started = time.monotonic()
                try:
                    self.tick(out)
                except Exception as exc:
                    self.errors += 1
                    _write(out, {"type": "collector_error", "t": time.time(), "error": str(exc)})
                delay = self.interval - (time.monotonic() - started)
                if delay > 0:
                    self._stopped.wait(delay)


def _write(out, payload: dict[str, Any]) -> None:
    out.write(json.dumps(payload) + "\n")
    out.flush()


class ProbeCollector(_Loop):
    """Constant-rate latency probe.

    Two probe classes on purpose:

      through  a route that traverses the connector service
      control  a route served by the Node API alone

    The control probe is the baseline: when `through` degrades and `control`
    does not, the stall is inside the connector service's event loop rather
    than in Node, the proxy hop, or the host.
    """

    def __init__(self, path: Path, rate: float, base_url: str, headers_fn: Callable[[], dict[str, str]]) -> None:
        super().__init__(path, 1.0 / max(rate, 0.01), "probe")
        self.base_url = base_url.rstrip("/")
        # Resolved per request, not captured once: a fleet run can outlive the
        # access token, and a probe that starts 401ing would otherwise look
        # like the API got dramatically faster.
        self.headers_fn = headers_fn
        self.probes: list[tuple[str, str]] = [
            ("through", "/api/v1/connectors/"),
            # Static JSON from Node, no downstream I/O (~2ms). NOT
            # /api/v1/health/services, which actively probes all five services
            # and measured 1.5s — that made the control slower than the thing
            # under test and added real load twice a second.
            ("control", "/api/v1/org/health"),
        ]
        self._next = 0
        self.session = requests.Session()

    def tick(self, out) -> None:
        kind, path = self.probes[self._next % len(self.probes)]
        self._next += 1
        started = time.monotonic()
        status: int | str
        try:
            resp = self.session.get(
                f"{self.base_url}{path}", headers=self.headers_fn(), timeout=120
            )
            status = resp.status_code
            # Read the body: a streamed response that is never consumed would
            # under-report the time the server actually spent on it.
            _ = resp.content
        except requests.RequestException as exc:
            status = type(exc).__name__
        elapsed_ms = (time.monotonic() - started) * 1000.0
        _write(
            out,
            {
                "type": "probe",
                "t": round(time.time(), 3),
                "probe": kind,
                "path": path,
                "status": status,
                "latency_ms": round(elapsed_ms, 2),
            },
        )


class ThroughputCollector(_Loop):
    """Poll per-connector record counts.

    Note the poll is itself API load against the same service under test. It is
    held at a fixed rate across every run so it acts as a constant offset rather
    than run-to-run noise; the report states the rate for that reason.
    """

    def __init__(
        self,
        path: Path,
        interval: float,
        stats_fn: Callable[[str], dict[str, Any]],
        connector_ids: list[str],
        status_fn: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(path, interval, "throughput")
        self.stats_fn = stats_fn
        # Optional: the connector doc carries `status`, which flips SYNCING ->
        # IDLE when the sync task actually ends. That is authoritative, whereas
        # a record count only tells you when records stopped arriving.
        self.status_fn = status_fn
        self.connector_ids = connector_ids
        #: latest total per connector, so the runner can wait for completion
        #: without opening a second poll loop against the service under test
        self.latest: dict[str, int] = {cid: 0 for cid in connector_ids}
        #: latest stored status per connector
        self.latest_status: dict[str, str] = {}
        #: connectors observed actually syncing at least once. A connector is
        #: only "finished" if it started first — otherwise the pre-sync IDLE
        #: would read as instant completion.
        self.seen_active: set[str] = set()

    def tick(self, out) -> None:
        for connector_id in self.connector_ids:
            try:
                data = self.stats_fn(connector_id)
            except Exception as exc:
                _write(
                    out,
                    {
                        "type": "stats_error",
                        "t": round(time.time(), 3),
                        "connector_id": connector_id,
                        "error": str(exc),
                    },
                )
                continue
            stats = (data or {}).get("stats") or {}
            by_status = stats.get("indexingStatus") or {}
            self.latest[connector_id] = stats.get("total", 0)
            status = None
            if self.status_fn is not None:
                try:
                    status = (self.status_fn(connector_id) or {}).get("status")
                except Exception:
                    status = None
                if status:
                    self.latest_status[connector_id] = status
                    if status.upper() in ("SYNCING", "FULL_SYNCING"):
                        self.seen_active.add(connector_id)
            _write(
                out,
                {
                    "type": "throughput",
                    "t": round(time.time(), 3),
                    "connector_id": connector_id,
                    "total": stats.get("total", 0),
                    # Guard rail: on a correctly configured connector-only run
                    # every record is AUTO_INDEX_OFF. Anything else means the
                    # manual-index filter did not take and the run is invalid.
                    "auto_index_off": by_status.get("AUTO_INDEX_OFF", 0),
                    "by_status": by_status,
                    "connector_status": status,
                },
            )


class SampleCollector(threading.Thread):
    """Per-process resource sampling inside the app container.

    Streams the agent's JSONL straight to disk. Runs as a plain thread rather
    than a _Loop because the cadence is owned by the in-container agent.
    """

    def __init__(self, path: Path, target: Target, interval: float, duration: float, token: str) -> None:
        super().__init__(name="sampler", daemon=True)
        self.path = path
        self.target = target
        self.interval = interval
        self.duration = duration
        self.stop_file = f"/tmp/lt-stop-{token}"
        self.proc: subprocess.Popen | None = None
        self.error: str | None = None

    def run(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        script = AGENT.read_text(encoding="utf-8")
        try:
            with self.path.open("w", encoding="utf-8") as out:
                self.proc = subprocess.Popen(
                    [
                        self.target.docker,
                        "exec",
                        "-i",
                        self.target.container,
                        "python3",
                        "-",
                        str(self.interval),
                        str(self.duration),
                        self.stop_file,
                    ],
                    stdin=subprocess.PIPE,
                    stdout=out,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                self.proc.stdin.write(script)
                self.proc.stdin.close()
                self.proc.wait()
        except Exception as exc:
            self.error = str(exc)

    def stop(self) -> None:
        # Touch the stop file first: killing the local `docker exec` client does
        # not stop the process it started inside the container, which would keep
        # sampling into the next iteration's file.
        self.target.exec(["touch", self.stop_file], check=False, timeout=20)
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.wait(timeout=self.interval * 3 + 5)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        self.target.exec(["rm", "-f", self.stop_file], check=False, timeout=20)


class ContainerStatsCollector(threading.Thread):
    """`docker stats` for the stateful sibling containers.

    The app container is deliberately excluded: it holds every service at once,
    so its aggregate number cannot be attributed and would only mislead.
    """

    def __init__(self, path: Path, target: Target, containers: list[str]) -> None:
        super().__init__(name="container-stats", daemon=True)
        self.path = path
        self.target = target
        self.containers = containers
        self.proc: subprocess.Popen | None = None

    def run(self) -> None:
        if not self.containers:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as out:
            self.proc = subprocess.Popen(
                [
                    self.target.docker,
                    "stats",
                    "--format",
                    "{{json .}}",
                    *self.containers,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            for line in self.proc.stdout:
                # Streaming `docker stats` repaints the terminal, so a line can
                # arrive as ESC[2J ESC[H {...} — stripping to the first brace
                # rather than requiring one at position 0.
                brace = line.find("{")
                if brace < 0:
                    continue
                try:
                    payload = json.loads(line[brace:].strip())
                except json.JSONDecodeError:
                    continue
                payload["t"] = round(time.time(), 3)
                payload["type"] = "container"
                _write(out, payload)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
