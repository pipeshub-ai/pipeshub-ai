"""Decides, each tick, whether this process reports over v3 or the legacy endpoints, and sends v3.

No network or framework type in here: the collector is reached through
:class:`UsageHttpPort` and time through an injected clock.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

from app.plugins.telemetry.v3 import CONTRACT_ENV
from app.plugins.telemetry.v3 import edition as edition_from_env
from app.plugins.telemetry.v3.delta import DeltaTracker
from app.plugins.telemetry.v3.translate import (
    COUNTER,
    Point,
    Sample,
    translate_event,
    translate_metrics,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

CONTRACT_HEADER = "X-Usage-Contract"
CONTRACT = "3"

MAX_POINTS = 5000
MAX_EVENTS = 2000

NOT_V3_TTL_S = 24 * 60 * 60
BACKOFF_START_S = 30.0
BACKOFF_CAP_S = 30 * 60.0
RETRY_AFTER_CAP_S = 24 * 60 * 60.0
MAX_UNAUTHORISED = 3

VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}$")
PROCESS_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")

_NEVER_ACCEPTED = (400, 413)
_UNAUTHORISED = 401
_OK_MIN, _OK_MAX = 200, 299
_MAX_EVENT_CUTS = 5


class Mode(str, Enum):
    AUTO = "auto"
    V3 = "v3"
    LEGACY = "legacy"


def contract_mode(raw: str | None = None) -> Mode:
    """``USAGE_TELEMETRY_CONTRACT`` as a mode; unset or unrecognised is ``auto``."""
    value = (os.getenv(CONTRACT_ENV, "") if raw is None else raw).strip().lower()
    try:
        return Mode(value)
    except ValueError:
        return Mode.AUTO


class Route(str, Enum):
    """What a tick did. ``LEGACY`` asks the caller to send this tick over the legacy endpoints."""

    V3 = "v3"
    LEGACY = "legacy"
    HOLD = "hold"  # nothing sent; counts and events wait in the process


class Secret:
    """A credential that does not show itself in a repr, a format string or a log line."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret(***)"

    __str__ = __repr__


@dataclass(frozen=True)
class V3Target:
    """Where and as whom to push, as loaded from the config this tick."""

    metrics_url: str
    events_url: str
    install_id: str
    secret: Secret | None
    version: str | None = None


@dataclass(frozen=True)
class HttpAnswer:
    """The collector's answer. ``status`` is ``None`` when there was none (timeout, refused)."""

    status: int | None
    contract: str | None = None  # the X-Usage-Contract header
    retry_after_s: float | None = None
    detail: str = ""  # the first characters of the body, for a log line


class UsageHttpPort(Protocol):
    async def post(self, url: str, body: bytes, credential: str) -> HttpAnswer:
        """POST ``body`` (JSON) with ``Authorization: Bearer <credential>``. Never raises for a
        failed request: no answer is ``HttpAnswer(status=None)``."""
        ...


class LogPort(Protocol):
    def debug(self, message: str) -> None: ...
    def info(self, message: str) -> None: ...
    def warning(self, message: str) -> None: ...


class EventSource(Protocol):
    def take(self, limit: int) -> list[dict[str, Any]]:
        """Remove and return the oldest ``limit`` buffered events."""
        ...

    def requeue(self, events: list[dict[str, Any]]) -> None:
        """Put events back in front of the buffer."""
        ...


@dataclass
class _Pending:
    sequence: int
    body: bytes
    # The buffered legacy events this batch was made from, to hand back on a fall-back.
    originals: list[dict[str, Any]] = field(default_factory=list)


class _Outcome(Enum):
    DONE = "done"  # acknowledged, dropped for good, or nothing to send
    WAIT = "wait"  # kept; try again on a later tick
    NOT_V3 = "not_v3"


class UsageV3Sender:
    """One process's v3 sender.

    No loss, no double counting: there is at most one unacknowledged metrics
    batch and one unacknowledged events batch. While a batch waits it is sent
    again byte for byte under the same ``sequence`` (the collector applies a
    ``(processId, sequence)`` once), and no new batch is cut: new counts stay in
    the registry and new events in the buffer until the next cut. Whenever a
    tick goes over the legacy endpoints instead, which carry cumulative values,
    the counter baseline moves to the current values, so a later switch to v3
    does not send that history again as a delta.

    ``process_id`` is made once per process and ``sequence`` starts at 1 per
    process and per kind (metrics, events), going up only when a batch is cut.
    A restarted process has a new id and starts at 1 again; the collector adds
    its deltas to the install's totals like any other process's. The one batch
    a process had not got acknowledged when it died is lost: at most one push
    interval of counts and 2000 events. Every uvicorn worker is its own
    process with its own id, and the collector adds their deltas together.

    The secret is used for the ``Authorization`` header only and is never
    logged; anything that is logged has it masked first.
    """

    def __init__(
        self,
        *,
        mode: Mode,
        service: str,
        process_id: str,
        http: UsageHttpPort,
        snapshot: Callable[[], Iterable[Sample]],
        events: EventSource,
        logger: LogPort,
        clock: Callable[[], float] = time.time,
        edition: str | None = None,
    ) -> None:
        if not PROCESS_ID_RE.match(process_id):
            raise ValueError("process_id does not fit the usage contract")
        self._mode = mode
        self._service = service
        self._process_id = process_id
        self._http = http
        self._snapshot = snapshot
        self._events = events
        self._logger = logger
        self._clock = clock
        self._edition = edition if edition in ("oss", "ee") else edition_from_env()

        self._tracker = DeltaTracker()
        self._sequence = {"metrics": 0, "events": 0}
        self._pending_metrics: _Pending | None = None
        self._pending_events: _Pending | None = None
        self._not_v3_until = 0.0
        self._confirmed = False  # the collector answered as v3 since the last "not v3" decision
        self._failures = 0
        self._retry_at = 0.0
        self._unauthorised = 0

    @property
    def mode(self) -> Mode:
        return self._mode

    @property
    def process_id(self) -> str:
        return self._process_id

    async def tick(self, target: V3Target) -> Route:
        if self._mode is Mode.LEGACY:
            return Route.LEGACY
        auto = self._mode is Mode.AUTO
        if target.secret is None or not target.secret.reveal():
            # Node has not registered the install (yet), or the collector is an old one.
            return self._legacy() if auto else Route.HOLD
        now = self._clock()
        if auto and now < self._not_v3_until:
            return self._legacy()
        if now < self._retry_at:
            return Route.HOLD

        for send in (self._send_metrics, self._send_events):
            outcome = await send(target)
            if outcome is _Outcome.NOT_V3:
                return self._legacy() if auto else Route.HOLD
            if outcome is _Outcome.WAIT:
                return Route.HOLD
        return Route.V3

    # -- legacy fall-back -------------------------------------------------

    def _legacy(self) -> Route:
        """This tick goes over the legacy endpoints: hand it everything v3 was holding."""
        if self._pending_events is not None:
            self._events.requeue(self._pending_events.originals)
            self._pending_events = None
        # The legacy push is cumulative, so it carries what a pending batch held.
        self._pending_metrics = None
        self._tracker.rebase(self._counters(translate_metrics(self._snapshot())))
        return Route.LEGACY

    # -- batches ----------------------------------------------------------

    @staticmethod
    def _counters(points: list[Point]) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
        return {(p.name, p.labels): p.value for p in points if p.type == COUNTER}

    def _envelope(self, kind: str, target: V3Target) -> dict[str, Any]:
        self._sequence[kind] += 1
        envelope: dict[str, Any] = {
            "processId": self._process_id,
            "sequence": self._sequence[kind],
            "service": self._service,
        }
        if isinstance(target.version, str) and VERSION_RE.match(target.version):
            envelope["version"] = target.version
        envelope["edition"] = self._edition
        return envelope

    @staticmethod
    def _number(value: float) -> int | float:
        return int(value) if float(value).is_integer() else value

    def _cut_metrics(self, target: V3Target) -> _Pending | None:
        points = translate_metrics(self._snapshot())
        gauges = [p for p in points if p.type != COUNTER]
        deltas = self._tracker.cut(self._counters(points), limit=MAX_POINTS)
        out: list[dict[str, Any]] = [
            {"name": name, "type": COUNTER, "labels": dict(labels), "value": self._number(value)}
            for (name, labels), value in deltas.items()
        ]
        if len(out) >= MAX_POINTS:
            self._logger.warning(
                f"Usage telemetry: a batch holds {MAX_POINTS} counter points; the rest go in the next one"
            )
        room = MAX_POINTS - len(out)
        ts = int(self._clock() * 1000)
        out.extend(
            {"name": p.name, "type": p.type, "labels": dict(p.labels), "value": self._number(p.value), "ts": ts}
            for p in gauges[:room]
        )
        if len(gauges) > room:
            self._logger.warning(
                f"Usage telemetry: {len(gauges) - room} gauge points left out of a batch of {MAX_POINTS}"
            )
        # With nothing to report, a batch is still sent until the collector has answered as
        # v3 once: otherwise a process with no catalogued signal would never find out
        # whether it should be using the legacy endpoints.
        if not out and self._confirmed:
            return None
        body = {**self._envelope("metrics", target), "points": out}
        return _Pending(self._sequence["metrics"], self._encode(body))

    def _cut_events(self, target: V3Target) -> _Pending | None:
        for _ in range(_MAX_EVENT_CUTS):
            originals = self._events.take(MAX_EVENTS)
            if not originals:
                return None
            translated = [e for e in map(translate_event, originals) if e is not None]
            if not translated:
                continue  # none of these has a v3 counterpart
            body = {**self._envelope("events", target), "events": translated}
            return _Pending(self._sequence["events"], self._encode(body), originals)
        return None

    @staticmethod
    def _encode(body: Mapping[str, Any]) -> bytes:
        return json.dumps(body, separators=(",", ":"), allow_nan=False).encode("utf-8")

    async def _send_metrics(self, target: V3Target) -> _Outcome:
        if self._pending_metrics is None:
            self._pending_metrics = self._cut_metrics(target)
        if self._pending_metrics is None:
            return _Outcome.DONE
        outcome = await self._post("metrics", target.metrics_url, self._pending_metrics, target)
        if outcome is _Outcome.DONE:
            self._pending_metrics = None
        return outcome

    async def _send_events(self, target: V3Target) -> _Outcome:
        if self._pending_events is None:
            self._pending_events = self._cut_events(target)
        if self._pending_events is None:
            return _Outcome.DONE
        outcome = await self._post("events", target.events_url, self._pending_events, target)
        if outcome is _Outcome.DONE:
            self._pending_events = None
        return outcome

    # -- one request and what its answer means ------------------------------

    async def _post(self, kind: str, url: str, pending: _Pending, target: V3Target) -> _Outcome:
        secret = target.secret.reveal() if target.secret else ""
        try:
            answer = await self._http.post(url, pending.body, f"{target.install_id}.{secret}")
        except Exception as error:  # the port should not raise; a failure is "no answer"
            answer = HttpAnswer(status=None, detail=type(error).__name__)
        now = self._clock()
        detail = self._masked(answer.detail, secret)

        if answer.status is None:
            return self._retry_later(kind, pending, "no answer", answer, now)

        if answer.contract != CONTRACT:
            # Not a v3 collector, whatever the status says: an older collector, a proxy, a login page.
            self._confirmed = False
            self._unauthorised = 0
            if self._mode is Mode.AUTO:
                self._not_v3_until = now + NOT_V3_TTL_S
                self._logger.info(
                    "Usage telemetry: the collector did not answer as a v3 collector "
                    f"(status {answer.status}); using the legacy endpoints for 24 hours"
                )
                return _Outcome.NOT_V3
            self._retry_later(kind, pending, f"not a v3 collector (status {answer.status})", answer, now)
            return _Outcome.NOT_V3

        self._confirmed = True
        if answer.status == _UNAUTHORISED:
            return self._unauthorised_answer(kind, now)
        self._unauthorised = 0

        if _OK_MIN <= answer.status <= _OK_MAX:
            self._failures = 0
            self._retry_at = 0.0
            self._logger.debug(f"Usage telemetry: v3 {kind} batch {pending.sequence} acknowledged")
            return _Outcome.DONE
        if answer.status in _NEVER_ACCEPTED:
            self._logger.warning(
                f"Usage telemetry: the collector refused v3 {kind} batch {pending.sequence} "
                f"for good (status {answer.status}); dropping it: {detail}"
            )
            return _Outcome.DONE
        return self._retry_later(kind, pending, f"status {answer.status}", answer, now)

    def _unauthorised_answer(self, kind: str, now: float) -> _Outcome:
        # Node may have rotated the secret between our config read and this push: keep the
        # batch and try again next tick with the secret as it is then.
        self._unauthorised += 1
        if self._unauthorised < MAX_UNAUTHORISED:
            self._logger.debug(f"Usage telemetry: v3 {kind} credential not accepted; retrying next tick")
            return _Outcome.WAIT
        if self._unauthorised == MAX_UNAUTHORISED:
            self._logger.warning(
                f"Usage telemetry: the collector refused this install's v3 credential "
                f"{MAX_UNAUTHORISED} times in a row"
                + ("; using the legacy endpoints for 24 hours" if self._mode is Mode.AUTO else "")
            )
        if self._mode is Mode.AUTO:
            self._unauthorised = 0
            self._confirmed = False
            self._not_v3_until = now + NOT_V3_TTL_S
            return _Outcome.NOT_V3
        return _Outcome.WAIT

    def _retry_later(self, kind: str, pending: _Pending, why: str, answer: HttpAnswer, now: float) -> _Outcome:
        self._failures += 1
        delay = min(BACKOFF_START_S * (2 ** min(self._failures - 1, 16)), BACKOFF_CAP_S)
        if answer.retry_after_s is not None and answer.retry_after_s > delay:
            delay = min(answer.retry_after_s, RETRY_AFTER_CAP_S)
        self._retry_at = now + delay
        self._logger.debug(
            f"Usage telemetry: v3 {kind} batch {pending.sequence} not delivered ({why}); "
            f"same batch again in {int(delay)}s"
        )
        return _Outcome.WAIT

    @staticmethod
    def _masked(text: str, secret: str) -> str:
        text = text[:200]
        return text.replace(secret, "***") if secret else text
