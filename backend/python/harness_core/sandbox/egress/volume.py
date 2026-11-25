"""Egress volume per run (C6.12, SEC-EG-14): what each run's sandboxes sent and received through the proxy, by
destination alias, an alert when that is unusual, and an optional hard per-run cap.

Two kinds of traffic are told apart (WP-61). Outside traffic (``tunnel``, ``intercepted``, ``database``, ``other``)
is what can carry a run's data off the deployment: the alerts, the org baseline and the cap judge it alone.
PipesHub's own gateways (``model-gateway``, ``tool-gateway``) are counted for the metrics only: a cap set to stop a
bulk upload must never cut a model stream, and a baseline of model traffic would hide an upload behind it.

The hot path is a ``VolumeMeter`` per forwarded request, tunnel or database connection: one list add, one integer
add and one comparison per chunk, nothing written, sent or timed. ``EgressVolume.flush`` (every
``VolumeLimits.flush_s``, from the server) does the rest: metric deltas, the alerts, a run that has ended folded
into its org's baseline, and one sealed record per run or org that changed in the proxy's state store, so a restart
keeps the cap and the baseline. An alert goes to the run's journal once per kind and direction (``on_alert``, which
the proxy sends with the run's own gateway token), so the audit chain and any export of it carry it; one the journal
did not take is raised again at the next flush. A run is the binding's ``runId``, or for a sandbox kept for a
conversation (SIGNIN-5) the run it is bound to now (``pipeshub.bound.runId``).

Alerts, each once per run:

- ``run_threshold``: bytes up past ``alert_up_bytes``, or down past ``alert_down_bytes``;
- ``org_baseline``: bytes up past ``baseline_factor`` times the org's usual run (the mean of its ended runs for the
  first 50, then a moving average that gives each new run a weight of 1/50; armed after ``baseline_min_runs`` of
  them, never below ``baseline_floor_bytes``);
- ``run_cap``: outside bytes up and down together past ``max_run_bytes`` (0: no cap). An outside meter answers
  ``False`` from then on, the transports stop the run's outside streams and tunnels, and every new outside request of
  the run is refused ``volume_cap_exceeded``. A gateway meter never answers ``False``: a model stream in flight
  finishes, and the run's next Model Gateway request goes on marked (``EGRESS_CAPPED_HEADER``) so the gateway ends
  the run with ``egress_volume_exceeded`` in its own wire's error, which every engine reports. The cap is kept at
  once, not at the next flush; the alerts of a binding that goes are raised as it goes (``binding_gone``), while its
  token can still send them.

Totals are per proxy process: what a run moved in the last ``flush_s`` before a crash is lost. The
DNS half of C6.12 is moot: no DNS reaches a sandbox (C6.07), and a refused name with encoded labels already raises
``egress.exfiltration_suspected``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, cast

from harness_core.engines.attempt_labels import BOUND_RUN_LABEL
from harness_core.gateway.refusal_reports import EGRESS_ALIASES, EgressVolumeAlert
from harness_core.logs import HarnessLog
from harness_core.sandbox.egress.errors import EgressRefusalReason as R
from harness_core.sandbox.egress.errors import EgressRefused

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from harness_core.clock import Clock
    from harness_core.gateway.refusal_reports import (
        EgressVia,
        VolumeAlertKind,
        VolumeDirection,
    )
    from harness_core.sandbox.egress.bindings import InMemoryEgressBindings
    from harness_core.sandbox.egress.ports import EgressStateStore
    from harness_core.sandbox.egress.state import StateSealer
    from harness_core.sandbox.egress.types import EgressBinding

MIB = 1024 * 1024
UP, DOWN = 0, 1
RUN_LABEL = "runId"
ORG_LABEL = "orgId"
# The proxy's state store holds these beside its bindings; ``DurableEgressBindings.load`` leaves them to this module.
VOLUME_PREFIX = "volume/"
RUN_PREFIX = VOLUME_PREFIX + "run/"
ORG_PREFIX = VOLUME_PREFIX + "org/"
RUN_RECORD_TTL_S = 24 * 3600
ORG_RECORD_TTL_S = 90 * 24 * 3600
# Ended runs kept in memory (so a late request still meets the cap); the oldest ended ones go first.
MAX_RUNS = 10_000
# The baseline averages every ended run until it has 1 / this many, then moves by this fraction per run.
BASELINE_MIN_WEIGHT = 0.02
MODEL_GATEWAY_ALIAS = "model-gateway"
GATEWAY_ALIASES = frozenset({MODEL_GATEWAY_ALIAS, "tool-gateway"})
_VIA_ALIAS: dict[str, str] = {"http": "other", "intercepted": "intercepted", "tunnel": "tunnel", "database": "database"}
_INDEX = {alias: i for i, alias in enumerate(EGRESS_ALIASES)}
_SLOTS = 2 * len(EGRESS_ALIASES)
# The outside aliases (alerts name the busiest of them) and their slots.
_OUTSIDE_ALIASES = tuple(alias for alias in EGRESS_ALIASES if alias not in GATEWAY_ALIASES)
_OUTSIDE_SLOTS = tuple(2 * _INDEX[alias] + d for alias in _OUTSIDE_ALIASES for d in (UP, DOWN))
GATEWAY_SCOPE = "gateway"
OUTSIDE_SCOPE = "outside"
_LOG = HarnessLog.named("harness_core.sandbox.egress.volume")
ENV_ALERT_UP_MB = "EGRESS_VOLUME_ALERT_UP_MB"
ENV_ALERT_DOWN_MB = "EGRESS_VOLUME_ALERT_DOWN_MB"
ENV_BASELINE_FACTOR = "EGRESS_VOLUME_BASELINE_FACTOR"
ENV_BASELINE_MIN_RUNS = "EGRESS_VOLUME_BASELINE_MIN_RUNS"
ENV_BASELINE_FLOOR_MB = "EGRESS_VOLUME_BASELINE_FLOOR_MB"
ENV_RUN_MAX_MB = "EGRESS_RUN_MAX_MB"


def scope_of(alias: str) -> str:
    """``gateway`` for PipesHub's own gateways, ``outside`` for the traffic the cap and the alerts judge."""
    return GATEWAY_SCOPE if alias in GATEWAY_ALIASES else OUTSIDE_SCOPE


def alias_for(host: str, via: EgressVia) -> str:
    """The bounded name metrics and alerts use for a destination: a gateway by its name, anything else by route."""
    if via == "http" and host in GATEWAY_ALIASES:
        return host
    return _VIA_ALIAS[via]


@dataclass(frozen=True, slots=True)
class VolumeLimits:
    """0 turns a bound off (``baseline_min_runs`` 0: the baseline arms from the org's first ended run, as 1 does).
    Defaults alert on a run far past what a long coding run moves and cap nothing."""

    alert_up_bytes: int = 256 * MIB
    alert_down_bytes: int = 2048 * MIB
    baseline_factor: float = 10.0
    baseline_min_runs: int = 20
    baseline_floor_bytes: int = 32 * MIB
    max_run_bytes: int = 0
    flush_s: float = 10.0
    # A run no live binding names, and that moved nothing for this long, has ended: its total joins the baseline.
    ended_after_s: float = 600.0


class VolumeLimitsInvalid(ValueError):
    """A volume setting that is not a number in range: the proxy does not start on it."""


def volume_limits_from_env(env: Mapping[str, str] | None = None) -> VolumeLimits:
    """The deployment's bounds (MiB, a factor, a count; unset or blank is the default, 0 turns one off)."""
    source = env if env is not None else {
        ENV_ALERT_UP_MB: os.getenv(ENV_ALERT_UP_MB, ""),
        ENV_ALERT_DOWN_MB: os.getenv(ENV_ALERT_DOWN_MB, ""),
        ENV_BASELINE_FACTOR: os.getenv(ENV_BASELINE_FACTOR, ""),
        ENV_BASELINE_MIN_RUNS: os.getenv(ENV_BASELINE_MIN_RUNS, ""),
        ENV_BASELINE_FLOOR_MB: os.getenv(ENV_BASELINE_FLOOR_MB, ""),
        ENV_RUN_MAX_MB: os.getenv(ENV_RUN_MAX_MB, ""),
    }
    default = VolumeLimits()

    def mib(name: str, fallback: int) -> int:
        return _whole(source, name, fallback // MIB) * MIB

    factor_text = (source.get(ENV_BASELINE_FACTOR) or "").strip()
    try:
        factor = float(factor_text) if factor_text else default.baseline_factor
    except ValueError:
        raise VolumeLimitsInvalid(f"{ENV_BASELINE_FACTOR}: {factor_text!r} is not a number") from None
    if not math.isfinite(factor) or factor < 0 or 0 < factor < 1:
        raise VolumeLimitsInvalid(f"{ENV_BASELINE_FACTOR}: {factor_text!r} must be 0 (off) or at least 1")
    return VolumeLimits(
        alert_up_bytes=mib(ENV_ALERT_UP_MB, default.alert_up_bytes),
        alert_down_bytes=mib(ENV_ALERT_DOWN_MB, default.alert_down_bytes),
        baseline_factor=factor,
        baseline_min_runs=max(1, _whole(source, ENV_BASELINE_MIN_RUNS, default.baseline_min_runs)),
        baseline_floor_bytes=mib(ENV_BASELINE_FLOOR_MB, default.baseline_floor_bytes),
        max_run_bytes=mib(ENV_RUN_MAX_MB, default.max_run_bytes),
    )


def _whole(source: Mapping[str, str], name: str, fallback: int) -> int:
    text = (source.get(name) or "").strip()
    if not text:
        return fallback
    if not text.isdigit() or len(text) > 9:
        raise VolumeLimitsInvalid(f"{name}: {text!r} is not a whole number from 0 to 999999999")
    return int(text)


class VolumeMetrics(Protocol):
    """Where the counts go as metrics. Labels are bounded: an alias, a scope (``scope_of``), a direction, an alert
    kind; never an id."""

    def transferred(self, alias: str, direction: str, count: int) -> None: ...

    def alerted(self, kind: str, direction: str) -> None: ...

    def run_ended(self, scope: str, direction: str, count: int) -> None: ...


class NoVolumeMetrics:
    def transferred(self, alias: str, direction: str, count: int) -> None:
        pass

    def alerted(self, kind: str, direction: str) -> None:
        pass

    def run_ended(self, scope: str, direction: str, count: int) -> None:
        pass


@dataclass(slots=True)
class _Run:
    key: str
    org_id: str
    run_id: str
    # Bytes per alias and direction: slot ``2 * alias index + direction``.
    slots: list[int] = field(default_factory=lambda: [0] * _SLOTS)
    # Every byte (what the flush watches for activity), and the outside bytes the cap judges.
    total: int = 0
    outside: int = 0
    capped: bool = False
    exported: list[int] = field(default_factory=lambda: [0] * _SLOTS)
    flushed_total: int = 0
    last_active: float = 0.0
    # Delivered (or with nowhere to go); ``sending``: handed to the journal, not yet taken; ``counted``: in the
    # metrics and the log already, so a resend after a failed one is not counted twice.
    alerted: set[tuple[str, str]] = field(default_factory=set[tuple[str, str]])
    sending: set[tuple[str, str]] = field(default_factory=set[tuple[str, str]])
    counted: set[tuple[str, str]] = field(default_factory=set[tuple[str, str]])
    folded: bool = False
    dirty: bool = False
    binding: EgressBinding | None = None

    def moved(self, direction: int) -> int:
        """Every byte that way, the gateways' included."""
        return sum(self.slots[direction::2])

    def outside_moved(self, direction: int) -> int:
        """The outside bytes that way: what the alerts and the baseline judge."""
        return sum(self.slots[i] for i in _OUTSIDE_SLOTS if i % 2 == direction)

    def top_alias(self, direction: int | None) -> str:
        """The outside alias with the most bytes that way (both ways for ``None``)."""
        def amount(alias: str) -> int:
            i = _INDEX[alias]
            if direction is None:
                return self.slots[2 * i] + self.slots[2 * i + 1]
            return self.slots[2 * i + direction]

        return max(_OUTSIDE_ALIASES, key=amount)


@dataclass(slots=True)
class _Baseline:
    runs: int = 0
    mean_up: float = 0.0
    dirty: bool = False

    def fold(self, up: int) -> None:
        self.runs += 1
        self.mean_up += max(1.0 / self.runs, BASELINE_MIN_WEIGHT) * (up - self.mean_up)
        self.dirty = True


@dataclass(frozen=True, slots=True)
class RunVolume:
    """What a run moved, as a test or the health route reads it."""

    up: int
    down: int
    by_alias: dict[str, tuple[int, int]]
    capped: bool
    # What the cap and the alerts judge: the bytes to outside destinations only.
    outside_up: int = 0
    outside_down: int = 0


@dataclass(frozen=True, slots=True)
class RunCharge:
    """What a charge left of a run's cap (``EgressVolume.charge``)."""

    allowed: bool
    remaining: int | None


class VolumeMeter:
    """One stream's counter on its run. ``add`` answers whether the stream may go on: ``False`` for an outside stream
    once its run is capped, never for a gateway's (counted, never cut)."""

    __slots__ = ("_base", "_cap", "_outside", "_run", "_tripped")

    def __init__(self, run: _Run, alias: str, cap: int, tripped: Callable[[_Run], None] | None = None) -> None:
        self._run = run
        self._base = 2 * _INDEX[alias]
        self._cap = cap
        self._outside = alias not in GATEWAY_ALIASES
        self._tripped = tripped

    def add(self, direction: int, count: int) -> bool:
        run = self._run
        if not self._outside:
            run.slots[self._base + direction] += count
            run.total += count
            return True
        if run.capped:
            # Nothing moves outside past the cap, so nothing more is counted.
            return False
        run.slots[self._base + direction] += count
        run.total += count
        run.outside += count
        if self._cap and run.outside > self._cap:
            run.capped = True
            if self._tripped is not None:
                self._tripped(run)
        return not run.capped

    def sent(self, count: int) -> bool:
        return self.add(UP, count)

    def received(self, count: int) -> bool:
        return self.add(DOWN, count)

    @property
    def stopped(self) -> bool:
        return self._outside and self._run.capped


class EgressVolume:
    """The proxy's per-run byte counts, alerts, org baselines and cap. ``store`` and ``sealer`` (both or neither)
    keep runs and baselines across a restart; ``load`` before serving, ``flush`` on a timer and at stop."""

    def __init__(
        self,
        limits: VolumeLimits | None = None,
        *,
        clock: Clock,
        bindings: InMemoryEgressBindings | None = None,
        metrics: VolumeMetrics | None = None,
        store: EgressStateStore | None = None,
        sealer: StateSealer | None = None,
    ) -> None:
        self.limits = limits or VolumeLimits()
        self._clock = clock
        self._bindings = bindings
        self._metrics = metrics or NoVolumeMetrics()
        self._store = store if sealer is not None else None
        self._sealer = sealer
        self._runs: OrderedDict[str, _Run] = OrderedDict()
        self._orgs: dict[str, _Baseline] = {}
        self._handlers: list[Callable[[EgressBinding, EgressVolumeAlert], Awaitable[bool] | None]] = []
        self._save_failing = False
        self._saving: set[asyncio.Task[None]] = set()

    def on_alert(self, handler: Callable[[EgressBinding, EgressVolumeAlert], Awaitable[bool] | None]) -> None:
        """``handler(binding, alert)`` for each new alert of a run a binding names (the proxy journals it). A handler
        that answers a future is taken at its word: ``False`` (or a failure) raises the alert again at the next
        flush; ``None`` counts as taken."""
        self._handlers.append(handler)

    # --- hot path -----------------------------------------------------------------------------------------------

    def meter(self, binding: EgressBinding, alias: str) -> VolumeMeter:
        run = self._run_for(binding)
        run.binding = binding
        return VolumeMeter(run, alias, self.limits.max_run_bytes, self._tripped)

    def _tripped(self, run: _Run) -> None:
        """The chunk that crossed the cap: the cap is kept now, so a proxy that restarts before the next flush still
        refuses the run (its alert follows at that flush, or when the cut streams release the binding)."""
        run.dirty = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._save_run(run))
        self._saving.add(task)
        task.add_done_callback(self._saving.discard)

    def binding_gone(self, sandbox_id: str) -> int:
        """A binding is about to go (its token with it): what its runs crossed since the last flush is raised now,
        while it can still be sent. The alerts raised."""
        raised = 0
        for run in list(self._runs.values()):
            if run.binding is not None and run.binding.sandbox_id == sandbox_id:
                raised += self._decide(run)
        return raised

    def check(self, binding: EgressBinding, alias: str | None = None) -> bool:
        """Refuse an outside request (any ``alias`` but a gateway's; ``None`` is outside) of a run already past the cap.
        A gateway request is never refused here; the answer says whether its run is capped, which the proxy tells the
        Model Gateway so the run ends with its reason."""
        run = self._runs.get(_key(binding))
        capped = run is not None and run.capped
        if capped and alias not in GATEWAY_ALIASES:
            raise EgressRefused(R.VOLUME_CAP_EXCEEDED, "the run moved more than the deployment's egress cap")
        return capped

    def charge(self, *, org_id: str, run_id: str, received: int) -> RunCharge:
        """Bytes a run received outside its sandboxes, counted as the proxy counts its own (WP-63 part A: a repository
        the worker fetched for it), under the alias ``repository``. ``received=0`` only reads what is left. A run past the
        cap is ``allowed=False``; ``remaining`` is what it may still move, ``None`` without a cap."""
        key = _run_key(org_id, run_id)
        run = self._runs.get(key)
        if run is None:
            run = _Run(key, org_id, run_id, last_active=self._clock.monotonic())
            self._runs[key] = run
        if received > 0 and not run.capped:
            run.dirty = True
            _ = VolumeMeter(run, "repository", self.limits.max_run_bytes, self._tripped).received(received)
        cap = self.limits.max_run_bytes
        remaining = None if not cap else max(0, cap - run.total)
        return RunCharge(allowed=not run.capped, remaining=remaining)

    # --- reads --------------------------------------------------------------------------------------------------

    def volume(self, *, org_id: str, run_id: str) -> RunVolume | None:
        run = self._runs.get(_run_key(org_id, run_id))
        if run is None:
            return None
        return RunVolume(
            up=run.moved(UP),
            down=run.moved(DOWN),
            by_alias={a: (run.slots[2 * i], run.slots[2 * i + 1]) for i, a in enumerate(EGRESS_ALIASES)},
            capped=run.capped,
            outside_up=run.outside_moved(UP),
            outside_down=run.outside_moved(DOWN),
        )

    def baseline(self, org_id: str) -> tuple[int, int] | None:
        """(ended runs, usual bytes up per run) of an org, or ``None`` before its first ended run."""
        base = self._orgs.get(org_id)
        return None if base is None else (base.runs, int(base.mean_up))

    @property
    def runs_metered(self) -> int:
        return len(self._runs)

    # --- flush --------------------------------------------------------------------------------------------------

    async def flush(self) -> int:
        """Export, decide, fold and save; the number of alerts raised."""
        now = self._clock.monotonic()
        live = self._live_keys()
        raised = 0
        for run in list(self._runs.values()):
            self._export(run)
            if run.total != run.flushed_total:
                run.flushed_total = run.total
                run.last_active = now
                run.dirty = True
            raised += self._decide(run)
            if not run.folded and run.key not in live and now - run.last_active >= self.limits.ended_after_s:
                if not run.run_id:
                    # A sandbox no run owns has gone: nothing of it is kept or folded, and its entry goes now.
                    self._ended_metrics(run)
                    del self._runs[run.key]
                    continue
                self._end(run)
            if run.dirty:
                await self._save_run(run)
        for org_id, base in self._orgs.items():
            if base.dirty:
                await self._save_org(org_id, base)
        self._evict()
        return raised

    def _export(self, run: _Run) -> None:
        for i in range(_SLOTS):
            delta = run.slots[i] - run.exported[i]
            if delta:
                self._metrics.transferred(EGRESS_ALIASES[i // 2], _DIRECTIONS[i % 2], delta)
                run.exported[i] = run.slots[i]

    def _decide(self, run: _Run) -> int:
        limits = self.limits
        up, down = run.outside_moved(UP), run.outside_moved(DOWN)
        raised = 0
        if limits.alert_up_bytes and up > limits.alert_up_bytes:
            raised += self._alert(run, "run_threshold", "up", up, limits.alert_up_bytes, run.top_alias(UP))
        if limits.alert_down_bytes and down > limits.alert_down_bytes:
            raised += self._alert(run, "run_threshold", "down", down, limits.alert_down_bytes, run.top_alias(DOWN))
        base = self._orgs.get(run.org_id) if run.run_id else None
        if limits.baseline_factor > 0 and base is not None and base.runs >= limits.baseline_min_runs:
            threshold = max(limits.baseline_floor_bytes, int(base.mean_up * limits.baseline_factor))
            if up > threshold:
                raised += self._alert(
                    run, "org_baseline", "up", up, threshold, run.top_alias(UP), baseline=int(base.mean_up)
                )
        if run.capped and limits.max_run_bytes:
            raised += self._alert(run, "run_cap", "total", run.outside, limits.max_run_bytes, run.top_alias(None))
        return raised

    def _alert(
        self,
        run: _Run,
        kind: VolumeAlertKind,
        direction: VolumeDirection,
        moved: int,
        threshold: int,
        alias: str,
        *,
        baseline: int | None = None,
    ) -> int:
        key = (kind, direction)
        if key in run.alerted or key in run.sending:
            return 0
        first = key not in run.counted
        binding = run.binding
        alert = EgressVolumeAlert.of(kind, direction, moved, threshold, alias, baseline_bytes=baseline)
        pending = [
            outcome
            for handler in (self._handlers if alert is not None and binding is not None and run.run_id else ())
            if (outcome := handler(binding, alert)) is not None  # type: ignore[arg-type]
        ]
        if pending:
            run.sending.add(key)
            for outcome in pending:
                asyncio.ensure_future(outcome).add_done_callback(lambda done: self._delivered(run, key, done))
        else:
            run.alerted.add(key)
            run.dirty = True
        if not first:
            return 0
        run.counted.add(key)
        self._metrics.alerted(kind, direction)
        fields: dict[str, str | None] = (
            {k: v for k, v in binding.labels.items() if k not in ("event", "exc")} if binding is not None else {}
        )
        fields.update(orgId=run.org_id, runId=run.run_id or None)
        _LOG.warning(
            "egress.volume_alert",
            **fields,
            kind=kind,
            direction=direction,
            bytes=moved,
            thresholdBytes=threshold,
            baselineBytes=baseline,
            alias=alias,
        )
        return 1

    @staticmethod
    def _delivered(run: _Run, key: tuple[str, str], done: asyncio.Future[bool]) -> None:
        run.sending.discard(key)
        if not done.cancelled() and done.exception() is None and done.result():
            run.alerted.add(key)
            run.dirty = True

    def _end(self, run: _Run) -> None:
        run.folded = True
        run.dirty = True
        self._ended_metrics(run)
        if run.org_id:
            self._orgs.setdefault(run.org_id, _Baseline()).fold(run.outside_moved(UP))

    def _ended_metrics(self, run: _Run) -> None:
        for direction in (UP, DOWN):
            outside = run.outside_moved(direction)
            self._metrics.run_ended(OUTSIDE_SCOPE, _DIRECTIONS[direction], outside)
            self._metrics.run_ended(GATEWAY_SCOPE, _DIRECTIONS[direction], run.moved(direction) - outside)

    def _evict(self) -> None:
        excess = len(self._runs) - MAX_RUNS
        if excess <= 0:
            return
        for key in [k for k, run in self._runs.items() if run.folded][:excess]:
            del self._runs[key]

    def _live_keys(self) -> frozenset[str]:
        if self._bindings is None:
            return frozenset()
        return frozenset(_key(binding) for binding in self._bindings.held())

    def _run_for(self, binding: EgressBinding) -> _Run:
        key = _key(binding)
        run = self._runs.get(key)
        if run is None:
            run = _Run(
                key,
                binding.labels.get(ORG_LABEL, ""),
                run_of(binding.labels),
                last_active=self._clock.monotonic(),
            )
            self._runs[key] = run
        return run

    # --- state --------------------------------------------------------------------------------------------------

    async def load(self) -> int:
        """Restore runs and baselines from the store; what does not open is dropped. The records restored."""
        if self._store is None or self._sealer is None:
            return 0
        restored = 0
        now = self._clock.monotonic()
        for key, value in await self._store.entries():
            if not key.startswith(VOLUME_PREFIX):
                continue
            try:
                body = json.loads(self._sealer.open(key, value))
                if key.startswith(RUN_PREFIX):
                    run = _restored_run(body, now)
                    if _digest(run.key) != key.removeprefix(RUN_PREFIX):
                        raise ValueError("record names another run")
                    self._runs[run.key] = run
                elif key.startswith(ORG_PREFIX):
                    org_id = str(body["orgId"])
                    if _digest(org_id) != key.removeprefix(ORG_PREFIX):
                        raise ValueError("record names another org")
                    self._orgs[org_id] = _Baseline(int(body["runs"]), float(body["meanUp"]))
                else:
                    raise ValueError("unknown record")
            except Exception as exc:
                _LOG.warning("egress.volume_state_unverified", errorType=type(exc).__name__)
                await self._store.delete(key)
                continue
            restored += 1
        _LOG.info("egress.volume_restored", records=restored, runs=len(self._runs), orgs=len(self._orgs))
        return restored

    async def _save_run(self, run: _Run) -> None:
        if not run.run_id or self._store is None or self._sealer is None:
            run.dirty = False
            return
        key = RUN_PREFIX + _digest(run.key)
        body = {
            "orgId": run.org_id,
            "runId": run.run_id,
            "slots": run.slots,
            "capped": run.capped,
            "alerted": sorted(f"{k}:{d}" for k, d in run.alerted),
            "folded": run.folded,
        }
        if await self._put(key, body, RUN_RECORD_TTL_S):
            run.dirty = False

    async def _save_org(self, org_id: str, base: _Baseline) -> None:
        if self._store is None or self._sealer is None:
            base.dirty = False
            return
        body = {"orgId": org_id, "runs": base.runs, "meanUp": base.mean_up}
        if await self._put(ORG_PREFIX + _digest(org_id), body, ORG_RECORD_TTL_S):
            base.dirty = False

    async def _put(self, key: str, body: Mapping[str, object], ttl_s: int) -> bool:
        assert self._store is not None and self._sealer is not None
        try:
            await self._store.put(key, self._sealer.seal(key, json.dumps(body).encode("utf-8")), ttl_s=ttl_s)
        except Exception as exc:
            # Kept dirty: the next flush writes it again. Logged once per outage.
            if not self._save_failing:
                self._save_failing = True
                _LOG.warning("egress.volume_save_failed", errorType=type(exc).__name__)
            return False
        if self._save_failing:
            self._save_failing = False
            _LOG.info("egress.volume_save_recovered")
        return True


_DIRECTIONS = ("up", "down")


def run_of(labels: Mapping[str, str]) -> str:
    """The run a sandbox's binding serves: its own ``runId``, or the run a kept sandbox is bound to now."""
    return labels.get(RUN_LABEL) or labels.get(BOUND_RUN_LABEL) or ""


def _key(binding: EgressBinding) -> str:
    run_id = run_of(binding.labels)
    if run_id:
        return _run_key(binding.labels.get(ORG_LABEL, ""), run_id)
    # A sandbox no run owns (a standard chat's code sandbox) is metered and capped on its own.
    return f"sandbox\x00{binding.sandbox_id}"


def _run_key(org_id: str, run_id: str) -> str:
    return f"run\x00{org_id}\x00{run_id}"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _restored_run(body: Mapping[str, object], now: float) -> _Run:
    org_id, run_id = str(body["orgId"]), str(body["runId"])
    raw_slots = body["slots"]
    if not isinstance(raw_slots, list):
        raise ValueError("malformed slots")
    values = cast("list[object]", raw_slots)
    # A record written before the ``repository`` alias was appended holds one alias (two slots) fewer.
    if len(values) == _SLOTS - 2:
        values = [*values, 0, 0]
    if len(values) != _SLOTS or any(not isinstance(n, int) or isinstance(n, bool) or n < 0 for n in values):
        raise ValueError("malformed slots")
    alerted_raw = body.get("alerted", [])
    if not isinstance(alerted_raw, list):
        raise ValueError("malformed alerts")
    alerted = [str(a).split(":", 1) for a in cast("list[object]", alerted_raw)]
    counts = [cast("int", n) for n in values]
    total = sum(counts)
    return _Run(
        _run_key(org_id, run_id),
        org_id,
        run_id,
        slots=counts,
        total=total,
        outside=sum(counts[i] for i in _OUTSIDE_SLOTS),
        capped=bool(body.get("capped")),
        exported=list(counts),
        flushed_total=total,
        last_active=now,
        alerted={(a[0], a[1]) for a in alerted if len(a) == 2},
        folded=bool(body.get("folded")),
    )


__all__ = [
    "DOWN",
    "ENV_ALERT_DOWN_MB",
    "ENV_ALERT_UP_MB",
    "ENV_BASELINE_FACTOR",
    "ENV_BASELINE_FLOOR_MB",
    "ENV_BASELINE_MIN_RUNS",
    "ENV_RUN_MAX_MB",
    "GATEWAY_ALIASES",
    "GATEWAY_SCOPE",
    "MODEL_GATEWAY_ALIAS",
    "ORG_PREFIX",
    "OUTSIDE_SCOPE",
    "RUN_PREFIX",
    "UP",
    "VOLUME_PREFIX",
    "EgressVolume",
    "NoVolumeMetrics",
    "RunVolume",
    "VolumeLimits",
    "VolumeLimitsInvalid",
    "VolumeMeter",
    "VolumeMetrics",
    "alias_for",
    "run_of",
    "scope_of",
    "volume_limits_from_env",
]
