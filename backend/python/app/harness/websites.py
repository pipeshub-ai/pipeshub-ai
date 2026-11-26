"""A run's allowed websites (WP-63): read from the org's live layer, checked again, and made egress destinations.

The org's live layer carries the ``websites`` member (the deployment's cap and, where something wrote them, an org's
and each agent's lists). A run's sites are what an edition's layers fold from it (``app.harness.org_network_layers``);
without them a run reaches no website, and the web is reached only through PipesHub's own tools. The member is read
again at each engine turn, so a site taken off stops the next turn reaching it.

Each site is checked again here before it becomes a destination (``site_destinations``): a value Node would refuse
never reaches the proxy, whatever wrote it. The data-flow rules then narrow it for the run
(``narrowed_for_flow``): a conversation the classification extension point denies web egress reaches no site, and one that
read private data holds every site, so each request that could carry the data waits for the person, as ``fetch_url``
does (SEC-A-01); a registry's own files still answer installs.

The narrowing is not only a turn's: when a gateway call's result makes the conversation hold more mid-turn, the
worker narrows the live sandbox's binding at the egress proxy before the engine sees that result (``LiveFlowHold``,
SEC-A-02), so no request of the same turn carries it out.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal, Protocol, cast

from app.harness.classification import classification_port, level_source
from app.harness.flow_destination import registrable_domain
from app.harness.org_network_layers import org_network_layers
from harness_core.effects.flow import holds_sends
from harness_core.logs import HarnessLog
from harness_core.sandbox.egress.policy import is_host_name
from harness_core.sandbox.egress.sites import (
    PRESET_HOSTS,
    SITE_PORT,
    git_host,
    held_for_flow,
    read_only_host,
    relay_host,
)
from harness_core.sandbox.egress.upstream_proxy import ENV_PRIVATE_ALLOW
from harness_core.sandbox.types import EgressDestination, SiteAccess, SiteMode

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.effects.flow import FlowRule
    from harness_core.security.classification import Classification
    from harness_core.security.labels import SourceLedger

_LOG = HarnessLog.named("app.harness.websites")
ACCESS_RANK: Final = {"read": 0, "read-write": 1}
_PATH: Final = re.compile(r"/[A-Za-z0-9._~!$&'()*+,;=:@-][A-Za-z0-9._~!$&'()*+,;=:@/-]{0,199}")
_PRIVATE_SUFFIXES: Final = (
    "localhost", "local", "internal", "intranet", "lan", "home", "corp", "private", "arpa", "test", "invalid",
    "example", "onion",
)
Site = Mapping[str, "JsonValue"]


def site_paths(site: Site) -> list[str] | None:
    """The paths a site is limited to; ``None``: the whole host."""
    paths = site.get("paths")
    return [p for p in paths if isinstance(p, str)] if isinstance(paths, list) else None


def live_sites(websites: Mapping[str, JsonValue] | None, agent_key: str | None) -> list[Site]:
    """The run's sites from the org's live layer ``websites`` member: the edition's fold of it, none without one."""
    layers = org_network_layers()
    if websites is None or layers is None:
        return []
    return layers.sites(websites, agent_key)


def _host_ok(host: str) -> bool:
    """The rules Node holds a save to, held again: a lower-case name of two labels or more (``*.`` and one for a
    wildcard), never an address, a private or internal name, or a public suffix."""
    name = host.removeprefix("*.")
    if host != host.lower() or "*" in name or not is_host_name(name) or name.split(".")[-1].isdigit():
        return False
    if len(name.split(".")) < 2 or any(name == s or name.endswith(f".{s}") for s in _PRIVATE_SUFFIXES):
        return False
    # A name that is its own registrable domain only because it is a public suffix (``co.uk``, ``github.io``).
    return registrable_domain(f"x.{name}") != f"x.{name}"


def site_destinations(
    sites: Sequence[Site], *, private_names: frozenset[str] = frozenset(), log: HarnessLog = _LOG
) -> tuple[EgressDestination, ...]:
    """Each site as the egress destination the proxy enforces; one that breaks a rule is dropped and logged once."""
    out: list[EgressDestination] = []
    for site in sites:
        host = str(site.get("host", ""))
        access = site.get("access")
        paths = tuple(site_paths(site) or ())
        opaque = site.get("opaque") is True
        ok = (
            (_host_ok(host) or host in private_names)
            and not git_host(host)
            and access in ACCESS_RANK
            and (access == "read" or site.get("readWriteConfirmed") is True)
            and all(_PATH.fullmatch(p) and ".." not in p.split("/") for p in paths)
            and not (access == "read-write" and read_only_host(host))
            # A relay's every request is an upload: listed read-only it would be a way out the entry does not name.
            and not (access == "read" and relay_host(host))
            and not (opaque and (access != "read-write" or host.startswith("*.") or paths or host in PRESET_HOSTS))
        )
        if not ok:
            log.warning("websites.site_dropped", reasonCode="site_invalid")
            continue
        mode = SiteMode.READ_WRITE if access == "read-write" else SiteMode.READ
        out.append(EgressDestination(host, (SITE_PORT,), site=SiteAccess(mode, paths, opaque)))
    return tuple(out)


@dataclass(frozen=True, slots=True)
class FlowFacts:
    """What the run's conversation holds as the flow rule reads it: whether it read private data, and whether the
    classification extension point denies it web egress."""

    private_read: bool = False
    egress_forbidden: bool = False

    @property
    def hold(self) -> FlowHold | None:
        """What the proxy must hold the run's sites to: ``forbidden`` (none), ``held``, or nothing."""
        if self.egress_forbidden:
            return "forbidden"
        return "held" if self.private_read else None


FlowHold = Literal["held", "forbidden"]
_HOLD_RANK: Final[dict[FlowHold | None, int]] = {None: 0, "held": 1, "forbidden": 2}


def narrowed_for_flow(
    destinations: Sequence[EgressDestination], facts: FlowFacts, released: frozenset[str] = frozenset()
) -> tuple[EgressDestination, ...]:
    """The flow rule on allowed websites (``held_for_flow``): a level that forbids web egress reaches none; a
    conversation that read private data holds every site until a person allows it for the run (``released``): a
    URL carries data as a body does (FG2 holds ``fetch_url`` for it), so reads wait too, all but a registry's own
    files; a host-level tunnel, which the proxy cannot hold, is dropped."""
    if facts.hold is None:
        return tuple(destinations)
    return held_for_flow(destinations, forbidden=facts.hold == "forbidden", released=released)


def private_allow_names(env: Mapping[str, str] | None = None) -> frozenset[str]:
    """The host names ``EGRESS_PRIVATE_ALLOW`` lists (``host:port``): a private name a site may then be."""
    raw = (env if env is not None else os.environ).get(ENV_PRIVATE_ALLOW, "")
    names: set[str] = set()
    for entry in (part.strip().lower() for part in raw.split(",")):
        host = entry.rpartition(":")[0]
        if host and not entry.startswith("[") and "/" not in host and any(ch.isalpha() for ch in host):
            names.add(host)
    return frozenset(names)


class SitesSource(Protocol):
    async def allowed_sites(self) -> list[Site]: ...


class UploadCards(Protocol):
    """``app.harness.site_uploads.SiteUploads`` as the turn's sites read it."""

    async def refresh(self) -> None: ...

    def released(self) -> frozenset[str]: ...

    def released_for(self, hosts: Sequence[str]) -> frozenset[str]: ...

    async def ask(self, hosts: Sequence[str]) -> int: ...

    def watch_held(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RunSites:
    """An engine run's allowed websites as each turn's sandbox gets them: the live fold, checked, then narrowed by
    the flow rule and the classification extension point's answer as they stand at that turn."""

    policy: SitesSource
    classification: Classification
    ledger: SourceLedger | None
    run_id: str
    flow_rules: tuple[FlowRule, ...] = ()
    log: HarnessLog = _LOG
    # The run's site cards: what the person allowed, and the ask for each newly held site.
    uploads: UploadCards | None = None

    def facts(self) -> tuple[FlowFacts, str]:
        """What the conversation holds now, as the flow rule and the classification extension point read it, with
        the extension point's ``egress`` answer. Cheap: the ledger and the level, no fold of the sites."""
        held = self.ledger is not None and holds_sends(self.flow_rules, self.ledger, self.run_id)
        egress = "allow"
        if self.classification.configured:
            egress = classification_port().egress(
                self.classification, level_source(self.classification, self.ledger, self.run_id)()
            )
        return FlowFacts(private_read=held or egress == "ask", egress_forbidden=egress == "deny"), egress

    async def __call__(self) -> tuple[EgressDestination, ...]:
        destinations = site_destinations(
            await self.policy.allowed_sites(), private_names=private_allow_names(), log=self.log
        )
        if not destinations:
            return ()
        facts, egress = self.facts()
        released: frozenset[str] = frozenset()
        if self.uploads is not None and facts.private_read and not facts.egress_forbidden:
            await self.uploads.refresh()
            released = self.uploads.released_for([d.host for d in destinations])
        narrowed = narrowed_for_flow(destinations, facts, released)
        held = [d for d in narrowed if d.site is not None and d.site.held]
        if self.uploads is not None and held:
            # A read-write site is an upload place the admin chose: the person is asked as the turn starts. A read
            # site is asked about only when the sandbox first needs it, so a list of docs sites raises no cards.
            _ = await self.uploads.ask([d.host for d in held if d.site is not None and d.site.mode is SiteMode.READ_WRITE])
            self.uploads.watch_held()
        if narrowed != tuple(destinations):
            self.log.info(
                "websites.narrowed_for_flow", sites=len(destinations), kept=len(narrowed), held=len(held),
                levelEgress=egress,
            )
        return narrowed


class HoldsFlow(Protocol):
    """The egress proxy's control plane as the live flow hold uses it (``POST /v1/flow-holds``)."""

    async def hold_flow(self, *, sandbox_id: str, run_id: str, hold: FlowHold) -> None: ...


class LiveSandbox(Protocol):
    def sandbox_id(self) -> str | None: ...


async def _hold_through_broker(sandbox: LiveSandbox, run_id: str, hold: FlowHold) -> bool | None:
    """R38EGRESS-FU-1: the hold through the sandbox's broker, which narrows the binding it gives a restarted proxy
    back and then tells the proxy; ``None`` where the sandbox or its broker cannot."""
    hold_flow = getattr(sandbox, "hold_flow", None)
    if hold_flow is None:
        return None
    return cast("bool | None", await hold_flow(run_id=run_id, hold=hold))


class EgressFlowUnenforced(Exception):
    """The proxy could not be told that the run now holds more: the gateway result that raised it is withheld."""

    reason_code = "egress_rebind_failed"


@dataclass
class LiveFlowHold:
    """SEC-A-02: the run's sites follow the flow rule within a turn, not only from one turn to the next.

    ``sites`` is what the kernel binds each turn's sandbox with (``RunSites``), remembered here; ``narrow`` runs
    after every gateway call, before its result reaches the engine. When the conversation now holds more than the
    sandbox's binding was narrowed to (a private or untrusted read, a classification raise), the proxy is told at
    once (``hold_flow``), so no request of the same turn carries what that call returned. The proxy's narrowing is
    the same as the bind's (``held_for_flow``) and can only narrow. A failure raises ``EgressFlowUnenforced``.
    Where the sandbox's broker takes it, the hold goes through the broker (R38EGRESS-FU-1): its copy of the binding
    is what a proxy that restarted without its state is given back, so the hold survives that too.
    """

    run_sites: RunSites
    control: HoldsFlow | None
    sandbox: LiveSandbox
    log: HarnessLog = _LOG
    _bound: tuple[EgressDestination, ...] = field(default=(), init=False)
    _hold: dict[str, FlowHold | None] = field(default_factory=lambda: dict[str, "FlowHold | None"](), init=False)
    _bound_hold: FlowHold | None = field(default=None, init=False)

    async def sites(self) -> tuple[EgressDestination, ...]:
        self._bound_hold = self.run_sites.facts()[0].hold
        self._bound = await self.run_sites()
        return self._bound

    async def narrow(self) -> None:
        if not self._bound:
            return
        sandbox_id = self.sandbox.sandbox_id()
        if sandbox_id is None:
            return
        hold = self.run_sites.facts()[0].hold
        held = self._hold.get(sandbox_id, self._bound_hold)
        if _HOLD_RANK[hold] <= _HOLD_RANK[held]:
            return
        assert hold is not None
        try:
            through_broker = await _hold_through_broker(self.sandbox, self.run_sites.run_id, hold)
            if through_broker is None:
                if self.control is None:
                    raise EgressFlowUnenforced("no egress control plane to narrow the sandbox's websites")
                await self.control.hold_flow(sandbox_id=sandbox_id, run_id=self.run_sites.run_id, hold=hold)
        except EgressFlowUnenforced:
            raise
        except Exception as exc:
            self.log.warning("websites.flow_hold_failed", hold=hold, errorType=type(exc).__name__)
            raise EgressFlowUnenforced("the egress proxy could not narrow the sandbox's websites") from exc
        self._hold[sandbox_id] = hold
        self.log.info("websites.flow_held", hold=hold, sites=len(self._bound))
        uploads = self.run_sites.uploads
        if uploads is not None and hold == "held":
            uploads.watch_held()


def sites_instructions(destinations: Sequence[EgressDestination]) -> str:
    """What the engine is told of its allowed websites: one line each, how to reach them, and what is refused."""
    if not destinations:
        return ""
    lines = []
    for d in destinations:
        site = d.site
        if site is None:
            continue
        how = (
            "read and write, host-level only"
            if site.opaque
            else "read-only for now: uploads wait for the person to allow them, which they are asked"
            if site.held
            else "read and write"
            if site.mode is SiteMode.READ_WRITE
            else "read-only: GET, HEAD and OPTIONS"
        )
        under = f", only under {', '.join(site.paths)}" if site.paths else ""
        lines.append(f"- https://{d.host} ({how}{under})")
    return (
        "\n\nAllowed websites: your shell reaches these over https through the PipesHub egress proxy (HTTPS_PROXY is "
        "set and its certificate is trusted for curl, git, pip, npm and Python). Every other site is refused, "
        "and every request is shown in the run's activity. Git hosts are never reachable from the shell: "
        "clone a repository with `ph git clone <https url>`, which asks the person first.\n" + "\n".join(lines)
    )


__all__ = [
    "ACCESS_RANK",
    "EgressFlowUnenforced",
    "FlowFacts",
    "FlowHold",
    "HoldsFlow",
    "LiveFlowHold",
    "RunSites",
    "SitesSource",
    "live_sites",
    "narrowed_for_flow",
    "private_allow_names",
    "site_destinations",
    "site_paths",
    "sites_instructions",
]
