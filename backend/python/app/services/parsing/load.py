"""How busy a parsing service process is, and the saturation signal it gives its callers.

``ParseLoad`` counts the parse requests inside the route (waiting for a slot or parsing).
With ``PARSING_MAX_CONCURRENT`` set, a request that would exceed that count is refused at
once with 429 + Retry-After instead of queueing; the indexing side's client treats that as
backpressure and retries. With it unset nothing is refused here: the resource governor's
gate still queues and answers 429 after its own wait, exactly as before.

The counters are per process. With ``PARSING_UVICORN_WORKERS`` above 1 each worker keeps its own,
so a scrape sees one worker; run one worker per pod (the shipped default) so the figures describe the pod.
"""
from __future__ import annotations

import os
import threading
import time

PARSING_MAX_CONCURRENT_ENV = "PARSING_MAX_CONCURRENT"


def parsing_max_concurrent() -> int | None:
    """``PARSING_MAX_CONCURRENT`` as a positive int, or ``None`` (unset, empty, 0 or not a number)."""
    raw = (os.getenv(PARSING_MAX_CONCURRENT_ENV) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


class ParseLoad:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._in_flight = 0
        self._saturated_total = 0
        self._parses_total = 0

    def try_enter(self, max_concurrent: int | None) -> bool:
        """Count one request in, or count one saturation and refuse when *max_concurrent* is reached."""
        with self._lock:
            if max_concurrent is not None and self._in_flight >= max_concurrent:
                self._saturated_total += 1
                return False
            self._in_flight += 1
            self._parses_total += 1
            return True

    def leave(self) -> None:
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)

    def note_saturated(self) -> None:
        """A 429 the route gave for another reason (the gate's wait ran out, memory)."""
        with self._lock:
            self._saturated_total += 1

    def snapshot(self) -> dict[str, int | None]:
        with self._lock:
            return {
                "in_flight": self._in_flight,
                "max_concurrent": parsing_max_concurrent(),
                "saturated_total": self._saturated_total,
                "requests_total": self._parses_total,
            }


_load = ParseLoad()


class StuckConversions:
    """Parses whose conversion thread still runs after the parse ended: their gate permit is held until it returns.

    Read at scrape time, so the age grows between scrapes and a conversion that never returns is visible.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._since: dict[int, float] = {}

    def add(self, token: int, since: float) -> None:
        with self._lock:
            self._since[token] = since

    def remove(self, token: int) -> None:
        with self._lock:
            self._since.pop(token, None)

    def snapshot(self, now: float | None = None) -> tuple[int, float]:
        """(how many, the oldest one's age in seconds)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            return len(self._since), max((now - at for at in self._since.values()), default=0.0)


_stuck = StuckConversions()


def stuck_conversions() -> StuckConversions:
    return _stuck


def parse_load() -> ParseLoad:
    return _load


def capacity(default: int | None = None) -> int:
    """This pod's concurrent parses: ``PARSING_MAX_CONCURRENT`` when set, else *default* (the governor's
    heavy-parse ceiling, passed by the caller), else 0 for unknown."""
    return parsing_max_concurrent() or default or 0


def render_metrics(load: ParseLoad | None = None, default_capacity: int | None = None) -> str:
    """Prometheus text exposition (version 0.0.4) of the load counters."""
    snap = (load or _load).snapshot()
    stuck_count, stuck_oldest = _stuck.snapshot()
    lines = [
        "# HELP pipeshub_parsing_in_flight Parse requests in the service now, waiting for a slot or parsing.",
        "# TYPE pipeshub_parsing_in_flight gauge",
        f"pipeshub_parsing_in_flight {snap['in_flight']}",
        "# HELP pipeshub_parsing_capacity Concurrent parses this pod takes: PARSING_MAX_CONCURRENT, else its heavy-parse ceiling.",
        "# TYPE pipeshub_parsing_capacity gauge",
        f"pipeshub_parsing_capacity {capacity(default_capacity)}",
        "# HELP pipeshub_parsing_saturated_total Parse requests answered 429 because the service was at capacity.",
        "# TYPE pipeshub_parsing_saturated_total counter",
        f"pipeshub_parsing_saturated_total {snap['saturated_total']}",
        "# HELP pipeshub_parsing_requests_total Parse requests admitted into the route.",
        "# TYPE pipeshub_parsing_requests_total counter",
        f"pipeshub_parsing_requests_total {snap['requests_total']}",
        "# HELP pipeshub_parse_stuck_conversions Parses whose conversion threads still run after the parse ended; their permit is held.",
        "# TYPE pipeshub_parse_stuck_conversions gauge",
        f"pipeshub_parse_stuck_conversions {stuck_count}",
        "# HELP pipeshub_parse_stuck_oldest_seconds Age of the oldest conversion held after its parse ended.",
        "# TYPE pipeshub_parse_stuck_oldest_seconds gauge",
        f"pipeshub_parse_stuck_oldest_seconds {int(stuck_oldest)}",
    ]
    return "\n".join(lines) + "\n"
