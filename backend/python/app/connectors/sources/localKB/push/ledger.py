"""In-memory accounting of the pushes that hurt the process, per caller.

A strike is a death by SIGSEGV/SIGABRT/SIGBUS, or a budget kill of a process that was CPU-bound for the time it
held it. Strikes are kept per (caller, request key); the same key twice refuses that request for ten minutes, and
enough distinct keys suspend the caller: three crashing bodies (all pushes) or four crashing image requests (image
pushes only). A death that is not a strike (a shared process that an earlier task had left in a bad state) is only
counted as a suspect event, and shared-worker seconds are charged per caller whatever the outcome, so a body that
finishes just under the kill is not free either. A budget kill with low CPU (a slow dependency) refuses the same
request for two minutes and nothing more, so a storage brownout cannot suspend an honest caller.

State lives in this process: a restart clears it, two replicas double every limit, and integrations that share one
identity share one budget. Checks never create or promote an entry, so a flood of unknown callers cannot evict a
suspended one.
"""

from __future__ import annotations

import logging
import time
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .contract import PushRefusal

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

WINDOW_S = 600.0
PAIR_REFUSAL_S = 600.0
OVERRUN_REFUSAL_S = 120.0
TEXT_DISTINCT = 3
IMAGE_DISTINCT = 4
SUSPECT_LIMIT = 4
LEASE_BUDGET_S = 150.0
MAX_USERS = 4096
MAX_ROWS = 64
#: Lease charges past MAX_ROWS are merged within a slot this long. The window spans at most MAX_ROWS - 1 slots
#: (one to spare against float rounding at a slot edge).
LEASE_SLOT_S = WINDOW_S / (MAX_ROWS - 2)

TEXT = "text"
IMAGE = "image"

#: Counters for operators: strikes by scope, suspects, refusals, and so on. Never labelled with a body or a token.
EVENTS: Counter[str] = Counter()

User = tuple[str, str]


@dataclass
class _Row:
    strikes: deque[tuple[float, str, str]] = field(
        default_factory=deque
    )  # (time, key, scope)
    suspects: deque[tuple[float, str]] = field(default_factory=deque)  # (time, scope)
    lease: deque[tuple[float, float]] = field(
        default_factory=deque
    )  # (end time, seconds)
    pairs: dict[str, tuple[float, str]] = field(
        default_factory=dict
    )  # key -> (until, answer)


def _retry(seconds: float) -> int:
    return max(1, int(seconds) + (0 if seconds == int(seconds) else 1))


def _until(times: list[float], limit: int) -> float:
    """When the count of events (oldest first) falls below ``limit``."""
    return times[len(times) - limit] + WINDOW_S


class PushLedger:
    def __init__(
        self, clock: Callable[[], float] = time.monotonic, max_users: int = MAX_USERS
    ) -> None:
        self.clock = clock
        self.max_users = max_users
        self._rows: OrderedDict[User, _Row] = OrderedDict()

    # ---- writes (create or promote the entry) ------------------------------------------------------

    def _row(self, user: User) -> _Row:
        row = self._rows.get(user)
        if row is None:
            row = self._rows[user] = _Row()
            while len(self._rows) > self.max_users:
                self._rows.popitem(last=False)
        else:
            self._rows.move_to_end(user)
        return row

    def _trim(self, row: _Row, now: float) -> None:
        horizon = now - WINDOW_S
        for rows in (row.strikes, row.suspects, row.lease):
            while rows and rows[0][0] <= horizon:
                rows.popleft()
        for rows in (row.strikes, row.suspects):
            while len(rows) > MAX_ROWS:
                rows.popleft()
        # Seconds are never dropped: two charges in the same LEASE_SLOT_S slot become one at the later time, so no
        # charge expires early and none is counted more than one slot late. The window spans at most MAX_ROWS slots,
        # so such a pair always exists; the oldest pair is only a fallback for a clock that went backwards.
        while len(row.lease) > MAX_ROWS:
            i = next(
                (j for j in range(len(row.lease) - 1)
                 if row.lease[j][0] // LEASE_SLOT_S == row.lease[j + 1][0] // LEASE_SLOT_S),
                0,
            )
            (t1, first), (t2, second) = row.lease[i], row.lease[i + 1]
            del row.lease[i]
            row.lease[i] = (max(t1, t2), first + second)
        for key in [k for k, (until, _) in row.pairs.items() if until <= now]:
            del row.pairs[key]

    def strike(self, user: User, key: str, scope: str) -> None:
        now = self.clock()
        row = self._row(user)
        self._trim(row, now)
        row.strikes.append((now, key, scope))
        self._trim(row, now)
        EVENTS[f"strike_{scope}"] += 1
        if sum(1 for _, k, _s in row.strikes if k == key) >= 2:
            row.pairs[key] = (
                now + PAIR_REFUSAL_S,
                "complexity" if key.startswith("b:") else "timeout",
            )
            EVENTS["pair_refused"] += 1
        if self._suspension(row, now) is not None:
            EVENTS["suspended"] += 1
            logger.warning(
                "pushes of one caller are suspended after repeated process crashes (%s)",
                scope,
            )

    def suspect(self, user: User, scope: str) -> None:
        now = self.clock()
        row = self._row(user)
        self._trim(row, now)
        row.suspects.append((now, scope))
        self._trim(row, now)
        EVENTS[f"suspect_{scope}"] += 1

    def lease(self, user: User, seconds: float) -> None:
        """Charge shared-worker seconds. Fast pushes (well under a second) are charged too; they never add up to the budget."""
        if seconds <= 0:
            return
        now = self.clock()
        row = self._row(user)
        self._trim(row, now)
        row.lease.append((now, seconds))
        self._trim(row, now)

    def refuse_pair(
        self, user: User, key: str, seconds: float = OVERRUN_REFUSAL_S
    ) -> None:
        now = self.clock()
        row = self._row(user)
        self._trim(row, now)
        row.pairs[key] = (
            max(now + seconds, row.pairs.get(key, (0.0, ""))[0]),
            "timeout",
        )
        EVENTS["overrun_refused"] += 1

    # ---- reads (never create or promote) -----------------------------------------------------------

    def _distinct_until(
        self, row: _Row, scope: str, limit: int, now: float
    ) -> float | None:
        latest = {
            key: t for t, key, s in row.strikes if s == scope and t > now - WINDOW_S
        }  # later strikes overwrite
        if len(latest) < limit:
            return None
        return _until(sorted(latest.values()), limit)

    def _suspension(self, row: _Row, now: float) -> tuple[float, str] | None:
        until = self._distinct_until(row, TEXT, TEXT_DISTINCT, now)
        if until is not None:
            return until, TEXT
        until = self._distinct_until(row, IMAGE, IMAGE_DISTINCT, now)
        return (until, IMAGE) if until is not None else None

    def _suspect_until(self, row: _Row, scope: str, now: float) -> float | None:
        times = sorted(t for t, s in row.suspects if s == scope and t > now - WINDOW_S)
        return _until(times, SUSPECT_LIMIT) if len(times) >= SUSPECT_LIMIT else None

    def _lease_until(self, row: _Row, now: float) -> float | None:
        live = [(t, s) for t, s in row.lease if t > now - WINDOW_S]
        total = sum(s for _, s in live)
        if total < LEASE_BUDGET_S:
            return None
        for t, s in sorted(
            live
        ):  # drop the oldest charges until the rest is under the budget
            total -= s
            if total < LEASE_BUDGET_S:
                return t + WINDOW_S
        return None

    def check_all(self, user: User) -> None:
        """Refuse every push of this caller: text-scope suspension, the shared-worker budget, repeated worker deaths."""
        row = self._rows.get(user)
        if row is None:
            return
        now = self.clock()
        until = self._distinct_until(row, TEXT, TEXT_DISTINCT, now)
        if until is not None:
            EVENTS["refused_suspended"] += 1
            raise PushRefusal(
                429,
                "PUSH_SUSPENDED",
                "Pushes are suspended after repeated failures; retry later.",
                extra={"retryAfter": _retry(until - now)},
            )
        for until, what in (
            (self._lease_until(row, now), "lease"),
            (self._suspect_until(row, TEXT, now), "suspect"),
        ):
            if until is not None:
                EVENTS[f"refused_{what}"] += 1
                raise PushRefusal(
                    503,
                    "PUSH_TIMEOUT",
                    "Too much processing time was used by recent pushes; retry later.",
                    extra={"retryAfter": _retry(until - now)},
                )

    def check_images(self, user: User) -> None:
        """Refuse this caller's pushes that carry images."""
        row = self._rows.get(user)
        if row is None:
            return
        now = self.clock()
        until = self._distinct_until(row, IMAGE, IMAGE_DISTINCT, now)
        if until is not None:
            EVENTS["refused_suspended"] += 1
            raise PushRefusal(
                429,
                "PUSH_SUSPENDED",
                "Image pushes are suspended after repeated failures; retry later.",
                extra={"retryAfter": _retry(until - now)},
            )
        until = self._suspect_until(row, IMAGE, now)
        if until is not None:
            EVENTS["refused_suspect"] += 1
            raise PushRefusal(
                503,
                "PUSH_TIMEOUT",
                "Recent image pushes stopped the image worker; retry later.",
                extra={"retryAfter": _retry(until - now)},
            )

    def check_pair(self, user: User, key: str) -> None:
        row = self._rows.get(user)
        entry = row.pairs.get(key) if row else None
        if entry is None:
            return
        until, answer = entry
        now = self.clock()
        if until <= now:
            return
        EVENTS["refused_pair"] += 1
        retry = {"retryAfter": _retry(until - now)}
        if answer == "complexity":
            raise PushRefusal(
                413,
                "PAYLOAD_TOO_LARGE",
                "The body is too complex to parse.",
                extra={"limit": "complexity", **retry},
            )
        raise PushRefusal(
            503,
            "PUSH_TIMEOUT",
            "This request recently took too long to process; retry later.",
            extra=retry,
        )

    def users(self) -> int:
        return len(self._rows)

    # ---- what a child's supervisor reports ---------------------------------------------------------

    def accountant(
        self, user: User, key: str, scope: str
    ) -> Callable[[str, float], None]:
        """The callback a worker or materialiser calls: ``("strike" | "suspect" | "overrun" | "lease", seconds)``."""

        def account(event: str, value: float = 0.0) -> None:
            if event == "strike":
                self.strike(user, key, scope)
            elif event == "suspect":
                self.suspect(user, scope)
                if (
                    scope == IMAGE
                ):  # a replayed text body that survived is not held against its key
                    self.refuse_pair(user, key)
            elif event == "overrun":
                self.refuse_pair(user, key)
            elif event == "lease":
                self.lease(user, value)

        return account
