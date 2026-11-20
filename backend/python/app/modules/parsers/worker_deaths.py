"""Which records have already lost a parse worker, so a death is retried once and not for ever."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict

# Longer than a record's redelivery backoff, short enough that an unrelated death much later starts afresh.
WINDOW_SECONDS = 1800.0
MAX_RECORDS = 1024


class WorkerDeaths:
    """A bounded, per-process memory of records whose parse worker was killed.

    Per process: a redelivery that lands on another process reads as a first death there, which is bounded
    by the broker's own attempt limit. Fail closed: a death with no record to name is never a first one.
    """

    def __init__(self, *, window: float = WINDOW_SECONDS, capacity: int = MAX_RECORDS) -> None:
        self._window = window
        self._capacity = capacity
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()

    def is_first(self, record_id: str | None, *, now: float | None = None) -> bool:
        """Note a death of ``record_id``'s worker; True only if it is the first within the window."""
        if not record_id:
            return False
        moment = time.monotonic() if now is None else now
        with self._lock:
            while self._seen and next(iter(self._seen.values())) < moment - self._window:
                self._seen.popitem(last=False)
            first = record_id not in self._seen
            self._seen[record_id] = moment
            self._seen.move_to_end(record_id)
            while len(self._seen) > self._capacity:
                self._seen.popitem(last=False)
            return first


deaths = WorkerDeaths()
