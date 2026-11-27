"""A stand-in for the usage collector's /v3 endpoints, behind the sender's HTTP port."""

from __future__ import annotations

import json
from collections import deque
from typing import Any

from app.plugins.telemetry.v3.sender import HttpAnswer

V3 = {"contract": "3"}
METRICS_URL = "https://collector.test/v3/metrics"
EVENTS_URL = "https://collector.test/v3/events"

# Scripted answers that are not a plain status.
TIMEOUT = "timeout"  # the request never arrives
ANSWER_LOST = "answer-lost"  # the collector applies the batch, the answer never arrives


class FakeCollector:
    """Applies a ``(install, kind, processId, sequence)`` once and adds counter deltas, like the real one."""

    def __init__(self, installs: dict[str, str] | None = None, *, v3: bool = True) -> None:
        self.installs = dict(installs or {})
        self.v3 = v3
        self.script: deque[Any] = deque()
        self.requests: list[tuple[str, bytes, str]] = []
        self.totals: dict[str, float] = {}
        self.gauges: dict[str, float] = {}
        self.events: list[dict[str, Any]] = []
        self._seen: set[tuple[str, str, str, int]] = set()

    def bodies(self, url: str | None = None) -> list[dict[str, Any]]:
        return [json.loads(body) for u, body, _ in self.requests if url is None or u == url]

    @staticmethod
    def key(point: dict[str, Any]) -> str:
        labels = ",".join(f"{k}={v}" for k, v in sorted(point.get("labels", {}).items()))
        return f"{point['name']}{{{labels}}}"

    async def post(self, url: str, body: bytes, credential: str) -> HttpAnswer:
        self.requests.append((url, body, credential))
        scripted = self.script.popleft() if self.script else None
        if scripted == TIMEOUT:
            return HttpAnswer(status=None, detail="TimeoutError")
        if isinstance(scripted, HttpAnswer):
            return scripted
        if not self.v3:
            return HttpAnswer(status=404, detail="<html>Not found</html>")
        install_id, _, secret = credential.partition(".")
        if self.installs.get(install_id) != secret:
            return HttpAnswer(status=401, **V3)
        doc = json.loads(body)
        kind = "metrics" if url.endswith("/metrics") else "events"
        allowed = {"processId", "sequence", "service", "version", "edition", "points" if kind == "metrics" else "events"}
        if set(doc) - allowed or not isinstance(doc.get("sequence"), int) or doc["sequence"] < 1:
            return HttpAnswer(status=400, detail="Invalid payload", **V3)
        seen = (install_id, kind, doc["processId"], doc["sequence"])
        if seen not in self._seen:
            self._seen.add(seen)
            if kind == "metrics":
                for point in doc["points"]:
                    if point["type"] == "counter":
                        self.totals[self.key(point)] = self.totals.get(self.key(point), 0) + point["value"]
                    else:
                        self.gauges[self.key(point)] = point["value"]
            else:
                self.events.extend(doc["events"])
        if scripted == ANSWER_LOST:
            return HttpAnswer(status=None, detail="TimeoutError")
        return HttpAnswer(status=200, **V3)


class Clock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds
