"""In-memory external service that records every committed effect.

Fault switches model the failure modes the harness must survive:

* ``lose_ack_after_commit`` - the effect commits, the caller sees a timeout.
* ``rate_limit`` - N calls per window, then ``RateLimited`` with retry-after.
* ``lookup_lag_s`` - a committed effect is invisible to ``lookup`` for a while
  (eventual consistency, which makes naive reconciliation unsafe).

``log_path`` appends every committed effect to a JSON-lines file (fsynced) and
reloads it on construction, so effects made by a SIGKILLed process stay visible.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harness_core.clock import SystemClock
from harness_core.effects.reconcile import refuse_write_while_reconciling

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from pydantic import JsonValue

    from harness_core.clock import Clock


class AckLost(TimeoutError):
    """The effect committed but its acknowledgement never reached the caller."""


class RateLimited(Exception):
    def __init__(self, retry_after_s: float) -> None:
        super().__init__(f"rate limited; retry after {retry_after_s:.3f}s")
        self.retry_after_s = retry_after_s


class IdempotencyConflict(Exception):
    """Same idempotency key reused with a different operation or payload."""


@dataclass(frozen=True)
class RateLimit:
    calls: int
    window_s: float


@dataclass(frozen=True)
class Effect:
    seq: int
    op: str
    payload: Mapping[str, JsonValue]
    idempotency_key: str | None
    committed_at: float
    receipt_id: str


@dataclass
class _Window:
    started_at: float
    used: int = 0


@dataclass
class FakeSaaS:
    clock: Clock = field(default_factory=SystemClock)
    lose_ack_after_commit: bool = False
    rate_limit: RateLimit | None = None
    lookup_lag_s: float = 0.0
    log_path: Path | None = None
    effects: list[Effect] = field(default_factory=list[Effect], init=False)
    _by_key: dict[str, Effect] = field(default_factory=dict[str, Effect], init=False, repr=False)
    _window: _Window | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self._read_log()

    def _read_log(self) -> None:
        """Effects other processes sharing ``log_path`` committed since this one last looked."""
        if self.log_path is None or not self.log_path.exists():
            return
        known = {(e.receipt_id, e.committed_at) for e in self.effects}
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            doc = json.loads(line)
            if (doc["receipt_id"], doc["committed_at"]) in known:
                continue
            effect = Effect(
                seq=doc["seq"],
                op=doc["op"],
                payload=doc["payload"],
                idempotency_key=doc["idempotency_key"],
                committed_at=doc["committed_at"],
                receipt_id=doc["receipt_id"],
            )
            self.effects.append(effect)
            if effect.idempotency_key:
                self._by_key[effect.idempotency_key] = effect

    def call(
        self,
        op: str,
        payload: Mapping[str, JsonValue],
        *,
        idempotency_key: str | None = None,
    ) -> Effect:
        """Perform ``op``; a repeated ``idempotency_key`` replays the original receipt."""
        refuse_write_while_reconciling(op)
        with self._lock:
            self._admit()
            # Two live workers (a rolling deploy) share one SaaS: dedupe against what the other committed.
            self._read_log()
            existing = self._by_key.get(idempotency_key) if idempotency_key else None
            if existing is not None:
                if existing.op != op or dict(existing.payload) != dict(payload):
                    raise IdempotencyConflict(idempotency_key)
                effect = existing
            else:
                effect = self._commit(op, payload, idempotency_key)
            lose = self.lose_ack_after_commit
        if lose:
            raise AckLost(f"{op} committed as {effect.receipt_id}; ack lost")
        return effect

    def lookup(self, idempotency_key: str) -> Effect | None:
        """Reconciliation read; lags commits by ``lookup_lag_s``."""
        with self._lock:
            effect = self._by_key.get(idempotency_key)
        if effect is None:
            return None
        visible = self.clock.suspend_aware() - effect.committed_at >= self.lookup_lag_s
        return effect if visible else None

    def effects_for(self, op: str) -> list[Effect]:
        return [e for e in self.effects if e.op == op]

    def assert_exactly_once(self, idempotency_key: str) -> Effect:
        matches = [e for e in self.effects if e.idempotency_key == idempotency_key]
        if len(matches) != 1:
            raise AssertionError(f"{idempotency_key!r} committed {len(matches)} times")
        return matches[0]

    def assert_none(self) -> None:
        if self.effects:
            raise AssertionError(f"expected no effects, found {len(self.effects)}")

    def _admit(self) -> None:
        limit = self.rate_limit
        if limit is None:
            return
        now = self.clock.suspend_aware()
        window = self._window
        if window is None or now - window.started_at >= limit.window_s:
            window = self._window = _Window(started_at=now)
        if window.used >= limit.calls:
            raise RateLimited(window.started_at + limit.window_s - now)
        window.used += 1

    def _commit(
        self, op: str, payload: Mapping[str, JsonValue], idempotency_key: str | None
    ) -> Effect:
        seq = len(self.effects) + 1
        effect = Effect(
            seq=seq,
            op=op,
            payload=dict(payload),
            idempotency_key=idempotency_key,
            committed_at=self.clock.suspend_aware(),
            receipt_id=f"{op}-{seq}",
        )
        if self.log_path is not None:
            line = json.dumps(
                {
                    "seq": seq,
                    "op": op,
                    "payload": dict(payload),
                    "idempotency_key": idempotency_key,
                    "committed_at": effect.committed_at,
                    "receipt_id": effect.receipt_id,
                },
                sort_keys=True,
            )
            with self.log_path.open("a", encoding="utf-8") as fh:
                _ = fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        self.effects.append(effect)
        if idempotency_key:
            self._by_key[idempotency_key] = effect
        return effect


__all__ = [
    "AckLost",
    "Effect",
    "FakeSaaS",
    "IdempotencyConflict",
    "RateLimit",
    "RateLimited",
]
