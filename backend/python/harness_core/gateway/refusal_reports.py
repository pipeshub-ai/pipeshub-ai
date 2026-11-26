"""A sandbox gateway's decisions as audit records (SEC2 O-2, OB-03): the egress proxy and the model gateway report a
refusal of a run's request to the run's journal (``gateway.refused``), next to the tool gateway's ``tool.denied``;
the egress proxy reports each destination it let the run's sandbox reach too (``egress.allowed``), and an unusual
amount of traffic (``egress.volume_alert``, C6.12). Node writes them on the run's stream, which chains them into the
org's audit stream (and so any export of it).

Only what the gateway can attribute is reported: a request that carried the run's own gateway token (the model
gateway) or came from a sandbox bound to a run attempt (the egress proxy). Reports are ids and codes; a
parameter is its field path, a host its bare name, never a path, a query or a value. The one exception is an
allowed website an admin named (WP-63, ``SiteReport``): its path, without the query and with tokens masked, is
what the run's activity shows ("Read github.com/onyx-dot-app/onyx").

``RefusalReporter`` sits on the request path, so it never waits: it remembers what it already reported per
run, gateway, reason and host (per run, host and route for an allowed destination), sends the rest in the background with at most ``MAX_IN_FLIGHT`` sends at once and
drops (and logs) what does not fit. A failed send is forgotten so a later refusal retries it; the failure is
logged once per outage. The sink (Node) dedupes and caps per run as well, so restarts and replicas agree.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from harness_core.logs import HarnessLog

GatewayName = Literal["egress", "model"]
RecordOutcome = Literal["recorded", "duplicate", "capped"]
# How the egress proxy let a request through: forwarded in the clear, inside a TLS connection it ended, as an
# opaque CONNECT tunnel, or as a PostgreSQL connection it signed in.
EgressVia = Literal["http", "intercepted", "tunnel", "database"]
EGRESS_VIAS: frozenset[str] = frozenset({"http", "intercepted", "tunnel", "database"})
# C6.12: what an egress volume alert is about, which way, and where most of the bytes went (``egress.volume_alert``).
VolumeAlertKind = Literal["run_threshold", "org_baseline", "run_cap"]
VolumeDirection = Literal["up", "down", "total"]
VOLUME_ALERT_KINDS: frozenset[str] = frozenset({"run_threshold", "org_baseline", "run_cap"})
VOLUME_DIRECTIONS: frozenset[str] = frozenset({"up", "down", "total"})
# ``repository``: what the worker fetched for a run outside its sandboxes (WP-63 part A, repository_fetch). Appended:
# a run's stored counts are by position, and a record kept before it was added still reads (``volume._restored_run``).
EGRESS_ALIASES: tuple[str, ...] = (
    "model-gateway", "tool-gateway", "tunnel", "intercepted", "database", "other", "repository",
)

# Node's cap (`MAX_GATEWAY_REFUSALS_PER_RUN`): the journal holds at most this many per run.
MAX_REFUSALS_PER_RUN = 32
# Node's cap (`MAX_EGRESS_ALLOWED_PER_RUN`): at most this many allowed destinations per run.
MAX_ALLOWED_PER_RUN = 64
# Node's cap (`MAX_EGRESS_VOLUME_ALERTS_PER_RUN`): every kind and direction an alert can have.
MAX_VOLUME_ALERTS_PER_RUN = 8
_MAX_BYTES = 2**53 - 1
_REASON = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_PARAM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_HOST = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$")
# A site's path as an activity line shows it: printable ASCII, no query or fragment, bounded.
_SITE_PATH = re.compile(r"/(?:(?![#?])[!-~]){0,120}")
# WP-63: how a site was reached and what the request did there.
SiteAccessShown = Literal["read", "read-write", "host-level"]
SiteUseShown = Literal["read", "write"]
SITE_ACCESSES: frozenset[str] = frozenset({"read", "read-write", "host-level"})
SITE_USES: frozenset[str] = frozenset({"read", "write"})
_LOG_NAME = "harness_core.gateway.refusal_reports"


class RefusalNotAccepted(Exception):
    """The sink refused the report itself (a token that is not this run's, a malformed report)."""


@dataclass(frozen=True, slots=True)
class SiteReport:
    """WP-63: what a run's sandbox did at an allowed website, as its activity line shows it. ``path`` has no query
    and its tokens masked (``harness_core.sandbox.egress.sites.shown_path``); ``use``: a read or a write."""

    path: str
    access: SiteAccessShown
    use: SiteUseShown

    @classmethod
    def of(cls, path: str, access: str, use: str) -> SiteReport | None:
        return cls(path, access, use) if _SITE_PATH.fullmatch(path) and access in SITE_ACCESSES and use in SITE_USES else None  # pyright: ignore[reportArgumentType]


@dataclass(frozen=True, slots=True)
class RefusalReport:
    gateway: GatewayName
    reason: str
    status: int
    host: str | None = None
    param: str | None = None
    site: SiteReport | None = None

    @classmethod
    def of(
        cls,
        gateway: GatewayName,
        reason: str,
        status: int,
        *,
        host: str | None = None,
        param: str | None = None,
        site: SiteReport | None = None,
    ) -> RefusalReport | None:
        """The report a gateway may send, or ``None`` when its reason or status is not a refusal's.

        A parameter keeps only its field path (a refusal names ``tools.type=<value>``: the value is the
        caller's); a host is kept only when it is a bare lower-case name."""
        if not _REASON.match(reason) or not 400 <= status <= 599:
            return None
        path = param.split("=", 1)[0] if param is not None else None
        name = host.lower() if host is not None else None
        kept = name if name is not None and _HOST.match(name) else None
        return cls(
            gateway,
            reason,
            status,
            host=kept,
            param=path if path is not None and _PARAM.match(path) else None,
            site=site if kept is not None and site is not None and SiteReport.of(site.path, site.access, site.use) == site else None,
        )


@dataclass(frozen=True, slots=True)
class EgressAllowance:
    """A destination the egress proxy let a run's sandbox reach: a bare host name and how, never a path or query."""

    host: str
    via: EgressVia
    # WP-63: set for an allowed website: one record per path and use, so the activity shows what was read.
    site: SiteReport | None = None

    @classmethod
    def of(cls, host: str, via: EgressVia, site: SiteReport | None = None) -> EgressAllowance | None:
        name = host.lower()
        if site is not None and SiteReport.of(site.path, site.access, site.use) != site:
            return None
        return cls(name, via, site) if _HOST.match(name) and via in EGRESS_VIAS else None


@dataclass(frozen=True, slots=True)
class EgressVolumeAlert:
    """C6.12: a run's sandboxes moved an unusual amount through the egress proxy. Counts and a fixed alias only."""

    kind: VolumeAlertKind
    direction: VolumeDirection
    bytes: int
    threshold_bytes: int
    alias: str
    baseline_bytes: int | None = None

    @classmethod
    def of(
        cls,
        kind: str,
        direction: str,
        bytes_: int,
        threshold_bytes: int,
        alias: str,
        *,
        baseline_bytes: int | None = None,
    ) -> EgressVolumeAlert | None:
        counts = (bytes_, threshold_bytes, *(() if baseline_bytes is None else (baseline_bytes,)))
        if (
            kind not in VOLUME_ALERT_KINDS
            or direction not in VOLUME_DIRECTIONS
            or alias not in EGRESS_ALIASES
            or any(type(n) is not int or not 0 <= n <= _MAX_BYTES for n in counts)
        ):
            return None
        return cls(kind, direction, bytes_, threshold_bytes, alias, baseline_bytes)  # pyright: ignore[reportArgumentType]


# Node's cap (`MAX_MODEL_FALLBACKS_PER_RUN`): a run moves at most twice (two alternates), each once per reason.
MAX_FALLBACKS_PER_RUN = 8
_MODEL_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,199}$")
_FALLBACK_REASONS = frozenset({"5xx", "timeout", "429_exhausted", "content_filter"})


@dataclass(frozen=True, slots=True)
class ModelFallbackReport:
    """G-48 (WP-100): the model gateway moved a run's call to an alternate model its pin names, for ``reason``;
    Node journals it as the run's ``model.fallback``, so the chat says which model answered. Names only."""

    from_provider: str
    from_model: str
    to_provider: str
    to_model: str
    reason: str

    @classmethod
    def of(cls, from_provider: str, from_model: str, to_provider: str, to_model: str, reason: str) -> ModelFallbackReport | None:
        names = (from_provider, from_model, to_provider, to_model)
        if reason not in _FALLBACK_REASONS or not all(_MODEL_PART.match(n) for n in names):
            return None
        return cls(from_provider, from_model, to_provider, to_model, reason)


# Node's cap (`MAX_SECRETS_HIDDEN_PER_RUN`): one per gateway and kind, every forbidden kind and the policy's own.
MAX_HIDDEN_PER_RUN = 16
# WP-103: the kind an operator's own egress pattern is reported under (its regex is never sent).
POLICY_PATTERN_KIND = "policy_pattern"


@dataclass(frozen=True, slots=True)
class SecretHidden:
    """WP-103: a gateway hid a credential shape from a run's model context and let the call go on. Node journals it
    as the run's ``gateway.secret_hidden`` (audit), so an operator sees what was hidden, by kind; never the value."""

    gateway: GatewayName
    kind: str

    @classmethod
    def of(cls, gateway: str, kind: str) -> SecretHidden | None:
        if gateway not in ("egress", "model") or not _REASON.match(kind):
            return None
        return cls(gateway, kind)  # pyright: ignore[reportArgumentType]


class RefusalSink(Protocol):
    """Where a gateway's decisions on a run's requests are recorded (Node's run journal).

    Both calls authenticate with ``token``, the run's own gateway token: the sink accepts only a token issued
    for ``run_id`` (``RefusalNotAccepted`` otherwise). ``record``: one record per run, gateway, reason and host
    (``duplicate`` after that), at most ``MAX_REFUSALS_PER_RUN`` per run (``capped`` after that). ``allowed``:
    one per run, host and route, at most ``MAX_ALLOWED_PER_RUN``. ``volume``: one per run, kind and direction, at most
``MAX_VOLUME_ALERTS_PER_RUN``. Raise on a transport failure."""

    async def record(self, run_id: str, token: str, report: RefusalReport) -> RecordOutcome: ...

    async def allowed(self, run_id: str, token: str, allowance: EgressAllowance) -> RecordOutcome: ...

    async def volume(self, run_id: str, token: str, alert: EgressVolumeAlert) -> RecordOutcome: ...

    async def fallback(self, run_id: str, token: str, report: ModelFallbackReport) -> RecordOutcome:
        """One per run, route moved from, to and reason, at most ``MAX_FALLBACKS_PER_RUN`` (WP-100)."""
        ...

    async def hidden(self, run_id: str, token: str, report: SecretHidden) -> RecordOutcome:
        """One per run, gateway and kind, at most ``MAX_HIDDEN_PER_RUN`` (WP-103)."""
        ...


@dataclass(frozen=True, slots=True)
class RecordedRefusal:
    run_id: str
    token: str = field(repr=False)
    report: RefusalReport


class InMemoryRefusalSink:
    """The sink as Node keeps it; ``authorize`` (when given) answers the run a token was issued for."""

    def __init__(
        self,
        *,
        authorize: Callable[[str], Awaitable[str]] | None = None,
        cap_per_run: int = MAX_REFUSALS_PER_RUN,
    ) -> None:
        self._authorize = authorize
        self._cap = cap_per_run
        self.calls: list[RecordedRefusal] = []
        self.records: list[RecordedRefusal] = []
        self.allowances: list[tuple[str, EgressAllowance]] = []
        self.volume_alerts: list[tuple[str, EgressVolumeAlert]] = []
        self.fallbacks: list[tuple[str, ModelFallbackReport]] = []
        self.hidden_secrets: list[tuple[str, SecretHidden]] = []

    async def record(self, run_id: str, token: str, report: RefusalReport) -> RecordOutcome:
        if self._authorize is not None and await self._authorize(token) != run_id:
            raise RefusalNotAccepted("the token is not this run's")
        if (
            RefusalReport.of(
                report.gateway, report.reason, report.status, host=report.host, param=report.param, site=report.site
            )
            != report
        ):
            raise RefusalNotAccepted("the report is malformed")
        entry = RecordedRefusal(run_id, token, report)
        self.calls.append(entry)
        own = [r for r in self.records if r.run_id == run_id]
        if any(_refusal_key(r.report) == _refusal_key(report) for r in own):
            return "duplicate"
        if len(own) >= self._cap:
            return "capped"
        self.records.append(entry)
        return "recorded"

    async def allowed(self, run_id: str, token: str, allowance: EgressAllowance) -> RecordOutcome:
        if self._authorize is not None and await self._authorize(token) != run_id:
            raise RefusalNotAccepted("the token is not this run's")
        if EgressAllowance.of(allowance.host, allowance.via, allowance.site) != allowance:
            raise RefusalNotAccepted("the report is malformed")
        own = [a for r, a in self.allowances if r == run_id]
        if allowance in own:
            return "duplicate"
        if len(own) >= MAX_ALLOWED_PER_RUN:
            return "capped"
        self.allowances.append((run_id, allowance))
        return "recorded"

    async def volume(self, run_id: str, token: str, alert: EgressVolumeAlert) -> RecordOutcome:
        if self._authorize is not None and await self._authorize(token) != run_id:
            raise RefusalNotAccepted("the token is not this run's")
        checked = EgressVolumeAlert.of(
            alert.kind, alert.direction, alert.bytes, alert.threshold_bytes, alert.alias, baseline_bytes=alert.baseline_bytes
        )
        if checked != alert:
            raise RefusalNotAccepted("the report is malformed")
        own = [a for r, a in self.volume_alerts if r == run_id]
        if any(a.kind == alert.kind and a.direction == alert.direction for a in own):
            return "duplicate"
        if len(own) >= MAX_VOLUME_ALERTS_PER_RUN:
            return "capped"
        self.volume_alerts.append((run_id, alert))
        return "recorded"

    async def fallback(self, run_id: str, token: str, report: ModelFallbackReport) -> RecordOutcome:
        if self._authorize is not None and await self._authorize(token) != run_id:
            raise RefusalNotAccepted("the token is not this run's")
        if ModelFallbackReport.of(report.from_provider, report.from_model, report.to_provider, report.to_model, report.reason) != report:
            raise RefusalNotAccepted("the report is malformed")
        own = [f for r, f in self.fallbacks if r == run_id]
        if report in own:
            return "duplicate"
        if len(own) >= MAX_FALLBACKS_PER_RUN:
            return "capped"
        self.fallbacks.append((run_id, report))
        return "recorded"

    async def hidden(self, run_id: str, token: str, report: SecretHidden) -> RecordOutcome:
        if self._authorize is not None and await self._authorize(token) != run_id:
            raise RefusalNotAccepted("the token is not this run's")
        if SecretHidden.of(report.gateway, report.kind) != report:
            raise RefusalNotAccepted("the report is malformed")
        own = [h for r, h in self.hidden_secrets if r == run_id]
        if report in own:
            return "duplicate"
        if len(own) >= MAX_HIDDEN_PER_RUN:
            return "capped"
        self.hidden_secrets.append((run_id, report))
        return "recorded"


TokenSupply = str | Callable[[], Awaitable[str]]
_Report = RefusalReport | EgressAllowance | EgressVolumeAlert | ModelFallbackReport | SecretHidden


class RefusalReporter:
    MAX_IN_FLIGHT = 32
    MAX_REMEMBERED = 4096

    def __init__(self, sink: RefusalSink, *, log: HarnessLog | None = None) -> None:
        self._sink = sink
        self._log = log or HarnessLog.named(_LOG_NAME)
        self._reported: OrderedDict[tuple[str, ...], None] = OrderedDict()
        self._tasks: set[asyncio.Task[bool]] = set()
        self._failing = False
        self._dropping = False

    @property
    def in_flight(self) -> int:
        return len(self._tasks)

    def report(self, run_id: str, token: TokenSupply, report: RefusalReport, *, log: HarnessLog | None = None) -> None:
        """Never awaits: called on the refusal path, before the gateway answers."""
        _ = self._start((run_id, *_refusal_key(report)), run_id, token, report, log or self._log)

    def allow(self, run_id: str, token: TokenSupply, allowance: EgressAllowance, *, log: HarnessLog | None = None) -> None:
        """Never awaits: called as the proxy forwards a request (OB-03, one record per run, host and route)."""
        site = allowance.site
        key = (run_id, "allowed", allowance.host, allowance.via, *(() if site is None else (site.path, site.use)))
        _ = self._start(key, run_id, token, allowance, log or self._log)

    def volume(
        self, run_id: str, token: TokenSupply, alert: EgressVolumeAlert, *, log: HarnessLog | None = None
    ) -> asyncio.Future[bool]:
        """Never awaits: called from the proxy's flush (C6.12, one record per run, kind and direction). The future
        answers whether the journal took it (recorded, a duplicate or past the run's bound), so a send that failed
        is raised again rather than lost."""
        key = (run_id, "volume", alert.kind, alert.direction)
        return self._start(key, run_id, token, alert, log or self._log)

    def fallback(self, run_id: str, token: TokenSupply, report: ModelFallbackReport, *, log: HarnessLog | None = None) -> None:
        """Never awaits: called as the model gateway moves a call to an alternate (WP-100, one record per run, route
        moved from, to and reason)."""
        key = (run_id, "fallback", report.from_provider, report.from_model, report.to_provider, report.to_model, report.reason)
        _ = self._start(key, run_id, token, report, log or self._log)

    def hidden(self, run_id: str, token: TokenSupply, report: SecretHidden, *, log: HarnessLog | None = None) -> None:
        """Never awaits: called as a gateway forwards a model call it hid a credential shape from (WP-103, one record
        per run, gateway and kind)."""
        _ = self._start((run_id, "hidden", report.gateway, report.kind), run_id, token, report, log or self._log)

    def _start(
        self, key: tuple[str, ...], run_id: str, token: TokenSupply, report: _Report, log: HarnessLog
    ) -> asyncio.Future[bool]:
        loop = asyncio.get_running_loop()
        if key in self._reported:
            return _settled(loop, value=True)
        if len(self._tasks) >= self.MAX_IN_FLIGHT:
            if not self._dropping:
                self._dropping = True
                log.warning("refusal_report.dropped", reason=_reason(report), gateway=_gateway(report), inFlight=len(self._tasks))
            return _settled(loop, value=False)
        self._dropping = False
        self._remember(key)
        task = loop.create_task(self._send(key, run_id, token, report, log))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self) -> None:
        """Waits for the sends in flight (tests, shutdown)."""
        while self._tasks:
            _ = await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def close(self) -> None:
        for task in list(self._tasks):
            _ = task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            _ = await asyncio.gather(*list(self._tasks), return_exceptions=True)
        self._tasks.clear()

    def _remember(self, key: tuple[str, ...]) -> None:
        self._reported[key] = None
        while len(self._reported) > self.MAX_REMEMBERED:
            _ = self._reported.popitem(last=False)

    async def _send(
        self,
        key: tuple[str, ...],
        run_id: str,
        token: TokenSupply,
        report: _Report,
        log: HarnessLog,
    ) -> bool:
        try:
            value = token if isinstance(token, str) else await token()
            if isinstance(report, RefusalReport):
                outcome = await self._sink.record(run_id, value, report)
            elif isinstance(report, EgressVolumeAlert):
                outcome = await self._sink.volume(run_id, value, report)
            elif isinstance(report, ModelFallbackReport):
                outcome = await self._sink.fallback(run_id, value, report)
            elif isinstance(report, SecretHidden):
                outcome = await self._sink.hidden(run_id, value, report)
            else:
                outcome = await self._sink.allowed(run_id, value, report)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _ = self._reported.pop(key, None)
            if not self._failing:
                self._failing = True
                log.warning(
                    "refusal_report.failed", reason=_reason(report), gateway=_gateway(report), errorType=type(exc).__name__
                )
            return False
        if self._failing:
            self._failing = False
            log.info("refusal_report.recovered", gateway=_gateway(report))
        log.info("refusal_report.sent", reason=_reason(report), gateway=_gateway(report), outcome=outcome)
        return True


def _refusal_key(report: RefusalReport) -> tuple[str, ...]:
    """What makes a refusal a new record: its gateway, reason and host, and for an allowed website its path."""
    site = report.site
    return (report.gateway, report.reason, report.host or "", *(() if site is None else (site.path,)))


def _settled(loop: asyncio.AbstractEventLoop, *, value: bool) -> asyncio.Future[bool]:
    future: asyncio.Future[bool] = loop.create_future()
    future.set_result(value)
    return future


def _reason(report: _Report) -> str:
    if isinstance(report, RefusalReport | ModelFallbackReport):
        return report.reason
    if isinstance(report, SecretHidden):
        return "secret_hidden"
    return "volume" if isinstance(report, EgressVolumeAlert) else "allowed"


def _gateway(report: _Report) -> str:
    if isinstance(report, ModelFallbackReport):
        return "model"
    return report.gateway if isinstance(report, RefusalReport | SecretHidden) else "egress"


__all__ = [
    "EGRESS_ALIASES",
    "EGRESS_VIAS",
    "MAX_ALLOWED_PER_RUN",
    "MAX_FALLBACKS_PER_RUN",
    "MAX_HIDDEN_PER_RUN",
    "MAX_REFUSALS_PER_RUN",
    "MAX_VOLUME_ALERTS_PER_RUN",
    "POLICY_PATTERN_KIND",
    "SITE_ACCESSES",
    "SITE_USES",
    "VOLUME_ALERT_KINDS",
    "VOLUME_DIRECTIONS",
    "EgressAllowance",
    "EgressVia",
    "EgressVolumeAlert",
    "GatewayName",
    "InMemoryRefusalSink",
    "ModelFallbackReport",
    "RecordOutcome",
    "RecordedRefusal",
    "RefusalNotAccepted",
    "RefusalReport",
    "RefusalReporter",
    "RefusalSink",
    "SecretHidden",
    "SiteAccessShown",
    "SiteReport",
    "SiteUseShown",
    "VolumeAlertKind",
    "VolumeDirection",
]
