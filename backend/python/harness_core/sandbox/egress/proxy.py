"""The egress proxy's core: decide, inject, log. Transport-free, so the fake broker runs it unchanged."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, unquote, urlsplit

from harness_core.gateway.refusal_reports import (
    EgressAllowance,
    EgressVia,
    EgressVolumeAlert,
    RefusalReport,
    RefusalReporter,
    SecretHidden,
    SiteReport,
)
from harness_core.logs import HarnessLog
from harness_core.sandbox.egress.errors import (
    AddressRefused,
    EgressRefused,
    HostUnresolvable,
    TokenUnavailable,
)
from harness_core.sandbox.egress.errors import EgressRefusalReason as R
from harness_core.sandbox.egress.git import check_git_request, inspectable
from harness_core.sandbox.egress.policy import (
    MIN_SECRET_MATCH,
    basic_authorization,
    check_destination,
    check_request_content,
    check_tunnel_destination,
    credential_routes,
    exfiltration_suspect,
    normalize_host,
    outbound_headers,
    parse_target,
    parse_tunnel_target,
    public_addresses,
    redacted_host,
    reflections_of,
    routes_for,
    substitute,
)
from harness_core.sandbox.egress.push_grants import PushGrants
from harness_core.sandbox.egress.site_grants import SiteGrantInvalid, SiteGrants
from harness_core.sandbox.egress.sites import (
    check_site_request,
    effective_mode,
    held_for_flow,
    read_only_request,
    shown_path,
    site_destination,
    site_use,
)
from harness_core.sandbox.egress.tokens import DEFAULT_REFRESH_BEFORE_S, TokenCache
from harness_core.sandbox.egress.types import DatabaseForward, Forward, Target, Tunnel
from harness_core.sandbox.egress.upstream_proxy import UpstreamEgress, private_addresses
from harness_core.sandbox.egress.volume import (
    MODEL_GATEWAY_ALIAS,
    EgressVolume,
    alias_for,
    run_of,
)
from harness_core.sandbox.types import CredentialInjection, SiteMode
from harness_core.telemetry import security_signals

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Awaitable, Callable, Mapping

    from harness_core.clock import Clock
    from harness_core.ids import IdSource
    from harness_core.logs import Field
    from harness_core.sandbox.egress.bindings import InMemoryEgressBindings
    from harness_core.sandbox.egress.policy import Network
    from harness_core.sandbox.egress.ports import HostResolver, TokenSource
    from harness_core.sandbox.egress.types import EgressBinding, ProxyRequest
    from harness_core.sandbox.types import EgressDestination, GitAccess, TokenRoute

DEFAULT_MAX_BODY_BYTES = 32 * 1024 * 1024
HTTPS_PORT = 443
_LOG_NAME = "harness_core.sandbox.egress.proxy"
# Injections the proxy serves inside an intercepted TLS connection; a gateway (bearer) is reached over plain HTTP.
_INTERCEPTED = frozenset({CredentialInjection.BASIC, CredentialInjection.SUBSTITUTE})


@dataclass(frozen=True, slots=True)
class ProxyLimits:
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    # Cluster or deployment ranges refused on top of every non-public range.
    blocked_networks: tuple[Network, ...] = ()
    refresh_before_s: int = DEFAULT_REFRESH_BEFORE_S
    # P5.4b: opaque CONNECT tunnels to allowlisted, uncredentialed hosts (deployment flag, off by default).
    connect_tunnels: bool = False
    # Whether the proxy can end a sandbox's TLS to a credentialed host (it holds a signing certificate); without it
    # such a CONNECT is refused as before.
    intercepts: bool = False


class EgressProxy:
    """Authorizes one sandbox request at a time; every refusal is logged here, once."""

    def __init__(
        self,
        *,
        bindings: InMemoryEgressBindings,
        tokens: TokenSource,
        resolver: HostResolver,
        upstreams: Mapping[str, str],
        clock: Clock,
        ids: IdSource,
        limits: ProxyLimits | None = None,
        refusals: RefusalReporter | None = None,
        run_label: str = "runId",
        report_credential: str = "gateway:model",
        upstream_egress: UpstreamEgress | None = None,
        private_resolver: HostResolver | None = None,
        internal_networks: tuple[Network, ...] = (),
        volume: EgressVolume | None = None,
    ) -> None:
        """``refusals``: where a refusal of a sandbox bound to a run (its ``run_label``) is reported, with the token
        of the binding's ``report_credential`` route: the run's own gateway token, which the journal checks.

        G-21: ``upstream_egress`` is the deployment's upstream proxy and private entries; ``private_resolver`` answers
        the names a private entry may open (``resolver`` may refuse private answers itself); ``internal_networks``
        are PipesHub's own addresses, refused like ``limits.blocked_networks`` whatever an entry lists.

        C6.12: ``volume`` counts what each run moves (default: alert thresholds only, no cap, nothing kept); a
        request of a run past its cap is refused, and each alert goes to the run's journal like a refusal."""
        self.bindings = bindings
        self._refusals = refusals
        self._run_label = run_label
        self._report_credential = report_credential
        self.limits = limits or ProxyLimits()
        self._cache = TokenCache(
            tokens, clock, refresh_before_s=self.limits.refresh_before_s, on_issued=bindings.record_grant
        )
        for grant in bindings.restored_grants():
            self._cache.restore(grant.sandbox_id, grant.credential_ref, grant.token)
        bindings.on_unregister(self._binding_gone)
        bindings.on_rebound(self._rebound)
        # GIT-FLOW under WP-63's rule: the one push a person allowed, each good once (``push_grants``).
        self.push_grants = PushGrants(clock)
        # The upload card: held read-write websites the person released for a run's sandbox (``site_grants``).
        self.site_grants = SiteGrants()
        self._flow_held: list[Callable[[str], None]] = []
        self.run_label = run_label
        self._resolver = resolver
        self._private_resolver = private_resolver or resolver
        self.upstream_egress = upstream_egress or UpstreamEgress()
        self._internal_networks = tuple(internal_networks)
        self._upstreams = _normalized(upstreams)
        self._ids = ids
        self._log = HarnessLog.named(_LOG_NAME)
        self.volume = volume or EgressVolume(clock=clock, bindings=bindings)
        self.volume.on_alert(self._report_volume)

    def _binding_gone(self, sandbox_id: str) -> None:
        # The run's alerts are raised before its token goes, so the sends find it (``TokenCache.reporting_token``).
        _ = self.volume.binding_gone(sandbox_id)
        self._cache.drop(sandbox_id)
        self.push_grants.drop(sandbox_id)
        self.site_grants.drop(sandbox_id)

    def _rebound(self, sandbox_id: str) -> None:
        self._cache.forget_current(sandbox_id)
        self.push_grants.drop(sandbox_id)
        self.site_grants.drop(sandbox_id)

    def on_flow_held(self, callback: Callable[[str], None]) -> None:
        """``callback(sandbox_id)`` once a flow hold narrowed a live sandbox's websites: its open host-level tunnels,
        which no hold reaches, are cut."""
        self._flow_held.append(callback)

    async def hold_flow(self, sandbox_id: str, *, run_id: str, hold: str) -> bool:
        """SEC-A-02: narrows the live binding's websites as a turn's bind would after a private read (``held``) or
        under a level that forbids web egress (``forbidden``), and keeps it (a durable table writes it through).
        Only ever narrows: a site already held stays held, a released one stays released for this sandbox through
        its site grant. Whether anything changed. ``SiteGrantInvalid`` for a sandbox that is not bound, or serves
        another run."""
        if hold not in ("held", "forbidden"):
            raise SiteGrantInvalid("a flow hold is held or forbidden")
        binding = self.bindings.get(sandbox_id)
        if binding is None:
            raise SiteGrantInvalid("the sandbox is not bound")
        serving = binding.labels.get(self.run_label) or run_of(binding.labels)
        if not run_id or serving != run_id:
            raise SiteGrantInvalid("the sandbox does not serve that run")
        destinations = held_for_flow(binding.policy.destinations, forbidden=hold == "forbidden")
        if destinations == binding.policy.destinations:
            return False
        await self.bindings.register(replace(binding, policy=replace(binding.policy, destinations=destinations)))
        for callback in self._flow_held:
            callback(sandbox_id)
        return True

    def replace_upstreams(self, upstreams: Mapping[str, str]) -> bool:
        """Swap the gateway table (deployment config may arrive after start); whether it changed."""
        normalized = _normalized(upstreams)
        if normalized == self._upstreams:
            return False
        self._upstreams = normalized
        return True

    def replace_internal_networks(self, networks: tuple[Network, ...]) -> bool:
        """Swap PipesHub's own addresses (containers move); whether they changed."""
        if networks == self._internal_networks:
            return False
        self._internal_networks = networks
        return True

    @property
    def refused_networks(self) -> tuple[Network, ...]:
        """Every range refused as a destination on top of the non-public ones, whatever an entry lists."""
        return (*self.limits.blocked_networks, *self._internal_networks)

    def new_request_id(self) -> str:
        return self._ids.new_id("egr")

    def live_tokens(self) -> frozenset[str]:
        return self._cache.live()

    def binding_for(self, *, local: str, peer: str) -> EgressBinding:
        binding = self.bindings.for_local_address(local)
        if binding is None:
            raise EgressRefused(R.UNBOUND_SOURCE, "no sandbox is bound to this address")
        if binding.sandbox_address != peer:
            raise EgressRefused(R.SOURCE_MISMATCH, "connection does not come from the bound sandbox")
        return binding

    async def authorize(self, request: ProxyRequest, *, local: str, peer: str, request_id: str | None = None) -> Forward:
        request_id = request_id or self.new_request_id()
        binding: EgressBinding | None = None
        try:
            binding = self.binding_for(local=local, peer=peer)
            target = parse_target(request)
            forward = await self._forward(request, target, binding, request_id, secure=False)
        except EgressRefused as refused:
            self.record_refusal(refused, request_id=request_id, binding=binding, peer=peer)
            raise
        self._report_allowed(binding, target.host, "http")
        return forward

    async def authorize_intercepted(
        self, request: ProxyRequest, *, host: str, port: int, local: str, peer: str, request_id: str | None = None
    ) -> Forward:
        """A request inside a TLS connection the proxy ended (``Tunnel.intercept``): it may only go where the
        ``CONNECT`` said, and is forwarded over TLS to that host's own certificate."""
        request_id = request_id or self.new_request_id()
        binding: EgressBinding | None = None
        site: SiteReport | None = None
        try:
            binding = self.binding_for(local=local, peer=peer)
            allowed_site = site_destination(Target(host=host, port=port, path=""), binding.policy)
            target = _intercepted_target(request, host, port, site=allowed_site is not None)
            keep_authorization = False
            if allowed_site is not None and allowed_site.site is not None:
                # WP-63: the request as the site's entry allows it; a read-only site never gets the sandbox's own
                # credentials, so a token an attacker planted cannot write as the attacker.
                site = _site_report(target, effective_mode(allowed_site.site, host), site_use(request, target))
                released = allowed_site.site.held and self.site_grants.released(binding, host)
                mode, _ = check_site_request(request, target, allowed_site.site, released=released)
                if mode is SiteMode.READ:
                    request = read_only_request(request)
                else:
                    keep_authorization = True
            forward = await self._forward(
                request, target, binding, request_id, secure=True, keep_authorization=keep_authorization
            )
        except EgressRefused as refused:
            refused.site = site
            self.record_refusal(refused, request_id=request_id, binding=binding, peer=peer)
            raise
        self._report_allowed(binding, target.host, "intercepted", site)
        return forward

    async def _forward(
        self,
        request: ProxyRequest,
        target: Target,
        binding: EgressBinding,
        request_id: str,
        *,
        secure: bool,
        keep_authorization: bool = False,
    ) -> Forward:
        alias = alias_for(target.host, "intercepted" if secure else "http")
        capped = self.volume.check(binding, alias)
        git = _git_access(target, binding)
        inspected = inspectable(request) if git is not None else request
        # WP-103: only an engine's own model call, in the clear to the gateway, is a model context; a credential
        # shape in its body is hidden and the call goes on.
        checked = check_request_content(
            inspected, binding.policy, self._cache.live(), model_context=alias == MODEL_GATEWAY_ALIAS
        )
        if checked.hidden:
            request = checked.request
            self._report_hidden(binding, checked.hidden)
        destination = check_destination(target, binding.policy)
        granted = self.push_grants.admit(inspected, target, binding, git) if git is not None else None
        if git is not None:
            check_git_request(inspected, target.path, granted[0] if granted is not None else git)
        routes = (
            (granted[1],) if granted is not None else credential_routes(request, target, binding, self._foreign_to(binding))
        )
        credentials = [(route, await self._token(route, binding)) for route in routes]
        substitutions = [
            (route.placeholder, token) for route, token in credentials if route.injection is CredentialInjection.SUBSTITUTE
        ]
        authorization = token = None
        for route, value in credentials:
            if route.injection is CredentialInjection.BEARER:
                token = value
            elif route.injection is CredentialInjection.BASIC:
                authorization = basic_authorization(value)
        upstream_target = replace(target, path=substitute(target.path, substitutions))
        url, server_hostname, via_proxy = await self._upstream_url(upstream_target, destination, secure=secure)
        upstream_proxy = self.upstream_egress.upstream if via_proxy else None
        return Forward(
            request_id=request_id,
            sandbox_id=binding.sandbox_id,
            method=request.method.upper(),
            url=url,
            headers=outbound_headers(
                request,
                target,
                token=token,
                request_id=request_id,
                authorization=authorization,
                substitutions=substitutions,
                default_port=HTTPS_PORT if secure else 80,
                keep_authorization=keep_authorization,
                capped=capped and alias == MODEL_GATEWAY_ALIAS,
            ),
            body=request.body,
            reflections=reflections_of(
                (shown, route.placeholder) for route, value in credentials for shown in _shown(route, value)
            ),
            server_hostname=server_hostname,
            proxy=upstream_proxy.url if upstream_proxy is not None else None,
            proxy_headers=upstream_proxy.headers() if upstream_proxy is not None else (),
            meter=self.volume.meter(binding, alias),
        )

    async def authorize_database(self, *, local: str, peer: str, request_id: str | None = None) -> DatabaseForward:
        """A PostgreSQL connection from a sandbox: its one database route, the credential the proxy signs in with."""
        request_id = request_id or self.new_request_id()
        binding: EgressBinding | None = None
        try:
            binding = self.binding_for(local=local, peer=peer)
            _ = self.volume.check(binding)
            route = next(
                (r for r in binding.policy.token_routes if r.injection is CredentialInjection.POSTGRES), None
            )
            if route is None:
                raise EgressRefused(R.DESTINATION_NOT_ALLOWED, "no database is reachable from this sandbox")
            name = normalize_host(route.destination)
            destination = next(d for d in binding.policy.destinations if normalize_host(d.host) == name)
            upstream = urlsplit(destination.upstream or "")
            credential = _database_credential(await self._token(route, binding))
        except EgressRefused as refused:
            self.record_refusal(refused, request_id=request_id, binding=binding, peer=peer)
            raise
        self._report_allowed(binding, name, "database")
        sslmode = parse_qs(upstream.query).get("sslmode", ["prefer"])[-1]
        return DatabaseForward(
            request_id=request_id,
            sandbox_id=binding.sandbox_id,
            destination=name,
            host=upstream.hostname or "",
            port=upstream.port or 5432,
            sslmode=sslmode,
            user=credential[0] or unquote(upstream.username or ""),
            password=credential[1],
            labels=binding.labels,
            meter=self.volume.meter(binding, "database"),
        )

    async def authorize_tunnel(
        self, request: ProxyRequest, *, local: str, peer: str, request_id: str | None = None
    ) -> Tunnel:
        """A ``CONNECT``: allowlisted and uncredentialed, resolved once and checked, an opaque tunnel; or, to a host
        the sandbox holds a Basic or substitution route for and when this proxy ends TLS, an intercepted one.
        Refusals are logged once."""
        request_id = request_id or self.new_request_id()
        binding: EgressBinding | None = None
        intercept = False
        via_proxy = False
        opaque_site = False
        connect_to: tuple[tuple[str, int], ...] = ()
        try:
            binding = self.binding_for(local=local, peer=peer)
            _ = self.volume.check(binding)
            target = parse_tunnel_target(request)
            allowed_site = site_destination(target, binding.policy)
            if allowed_site is not None and allowed_site.site is not None:
                # WP-63: an allowed website is inspected (its TLS ended here) unless an admin marked it host-level
                # only, which is an opaque tunnel on the deployment's tunnel switch, its TLS name checked.
                opaque_site = allowed_site.site.opaque
                intercept = not opaque_site
                if intercept and not self.limits.intercepts:
                    raise EgressRefused(R.TUNNEL_REFUSED, "this proxy cannot inspect allowed websites", host=target.host)
            elif _git_access(target, binding) is not None:
                # A host reached only as one repository (its git rule, e.g. a cache's mirror fill): the rule is
                # checked on each request, so the TLS is ended here; opaque bytes could reach any repository.
                if not self.limits.intercepts:
                    raise EgressRefused(R.TUNNEL_REFUSED, "a repository's git rule needs this proxy to end TLS", host=target.host)
                intercept = True
            elif self.limits.intercepts:
                routes = routes_for(target, binding.policy)
                intercept = bool(routes) and routes[0].injection in _INTERCEPTED
            if not intercept and not self.limits.connect_tunnels:
                # The host is named so the run's activity says where the sandbox tried to go (WP-63).
                raise EgressRefused(R.TUNNEL_REFUSED, "CONNECT tunnels are not offered", host=target.host)
            check_request_content(request, binding.policy, self._cache.live())
            if intercept:
                _ = check_destination(target, binding.policy)
            else:
                destination = check_tunnel_destination(target, binding.policy, self._upstreams.keys())
                if destination.upstream is not None:
                    connect_to = (_authority_of(destination.upstream),)
                else:
                    addresses, private = await self._checked_addresses(target)
                    connect_to = tuple((address, target.port) for address in addresses)
                    via_proxy = self._via_upstream_proxy(target, addresses[0], private=private)
        except EgressRefused as refused:
            self.record_refusal(refused, request_id=request_id, binding=binding, peer=peer)
            raise
        # An intercepted tunnel's requests are each reported as they are forwarded; an opaque one only here.
        if not intercept:
            shown = SiteReport.of("/", "host-level", "write") if opaque_site else None
            self._report_allowed(binding, target.host, "tunnel", shown)
        return Tunnel(
            request_id=request_id,
            sandbox_id=binding.sandbox_id,
            host=target.host,
            port=target.port,
            connect_to=connect_to,
            labels=binding.labels,
            intercept=intercept,
            via_upstream_proxy=via_proxy,
            require_sni=opaque_site,
            # An intercepted tunnel's requests are each metered as they are forwarded.
            meter=None if intercept else self.volume.meter(binding, "tunnel"),
        )

    def record_refusal(
        self,
        refused: EgressRefused,
        *,
        request_id: str,
        binding: EgressBinding | None = None,
        peer: str | None = None,
    ) -> None:
        """Log a refusal once, with the sandbox's correlation labels; never content or credentials."""
        security_signals.egress_decided("refused", refused.reason.value)
        refused.request_id = request_id
        fields: dict[str, Field] = {}
        if binding is not None:
            fields.update({k: v for k, v in binding.labels.items() if k not in ("event", "exc")})
            fields["sandboxId"] = binding.sandbox_id
        else:
            fields["peer"] = peer
        fields.update(reason=refused.reason.value, proxyRequestId=request_id, status=refused.status)
        if refused.host is not None:
            suspect = exfiltration_suspect(refused.host)
            # The encoded data stays out of the logs and the run's activity: only the name's tail is kept.
            refused.host = redacted_host(refused.host)
            fields["host"] = refused.host
            if suspect:
                self._log.warning("egress.exfiltration_suspected", **fields)
        self._log.warning("egress.refused", **fields)
        if binding is not None:
            self._report(refused, binding)

    def _report(self, refused: EgressRefused, binding: EgressBinding) -> None:
        reporting = self._reporting(binding)
        report = RefusalReport.of(
            "egress", refused.reason.value, refused.status, host=refused.host, site=refused.site
        )
        if reporting is not None and report is not None and self._refusals is not None:
            run_id, token = reporting
            self._refusals.report(run_id, token, report)

    def _report_hidden(self, binding: EgressBinding, kinds: tuple[str, ...]) -> None:
        """WP-103: the kinds hidden from a model call, logged (never the value) and on the run's audit record."""
        fields: dict[str, Field] = {k: v for k, v in binding.labels.items() if k not in ("event", "exc")}
        self._log.warning("egress.secret_hidden", sandboxId=binding.sandbox_id, kinds=",".join(kinds), **fields)
        reporting = self._reporting(binding)
        if reporting is None or self._refusals is None:
            return
        run_id, token = reporting
        for kind in kinds:
            report = SecretHidden.of("egress", kind)
            if report is not None:
                self._refusals.hidden(run_id, token, report)

    def _report_allowed(
        self, binding: EgressBinding, host: str, via: EgressVia, site: SiteReport | None = None
    ) -> None:
        """OB-03: the destination a run's sandbox reached goes on the run's audit record, once per host and route;
        an allowed website's once per path and use too (WP-63), which the run's activity shows."""
        security_signals.egress_decided("allowed", via)
        reporting = self._reporting(binding)
        allowance = EgressAllowance.of(host, via, site)
        if reporting is not None and allowance is not None and self._refusals is not None:
            run_id, token = reporting
            self._refusals.allow(run_id, token, allowance)

    def _report_volume(self, binding: EgressBinding, alert: EgressVolumeAlert) -> asyncio.Future[bool] | None:
        """C6.12: an unusual volume goes on the run's audit record, once per kind and direction; the future answers
        whether the journal took it (``None``: there is nowhere to send it)."""
        reporting = self._reporting(binding)
        if reporting is not None and self._refusals is not None:
            run_id, token = reporting
            return self._refusals.volume(run_id, token, alert)
        return None

    def _reporting(self, binding: EgressBinding) -> tuple[str, Callable[[], Awaitable[str]]] | None:
        """The run a bound sandbox serves and its own gateway token, which the journal checks; ``None`` for a
        sandbox no run owns (a request's) or without that token."""
        # A sandbox kept for a conversation (SIGNIN-5) names the run it serves now under the bound label.
        run_id = binding.labels.get(self._run_label) or run_of(binding.labels)
        route = next(
            (r for r in binding.policy.token_routes if r.credential_ref == self._report_credential), None
        )
        if self._refusals is None or not run_id or route is None:
            return None
        cache = self._cache
        return run_id, lambda: cache.reporting_token(route, binding)

    def _foreign_to(self, binding: EgressBinding) -> _Foreign:
        return _Foreign(self.bindings, binding.sandbox_id)

    async def _upstream_url(
        self, target: Target, destination: EgressDestination, *, secure: bool
    ) -> tuple[str, str | None, bool]:
        """Where the request goes, the name a TLS upstream dialled by address must prove it is, and whether it goes
        through the deployment's forward proxy. A gateway or a destination's own upstream never does (NO_PROXY)."""
        # A sandbox's own upstream (its run's tool endpoint) wins over the deployment's table.
        own = destination.upstream
        upstream = own.rstrip("/") if own is not None else self._upstreams.get(target.host)
        if upstream is not None:
            return upstream + target.path, None, False
        addresses, private = await self._checked_addresses(target)
        address = addresses[0]
        via_proxy = self._via_upstream_proxy(target, address, private=private)
        literal = f"[{address}]" if ":" in address else address
        if secure:
            return f"https://{literal}:{target.port}{target.path}", target.host, via_proxy
        return f"http://{literal}:{target.port}{target.path}", None, via_proxy

    def _via_upstream_proxy(self, target: Target, address: str, *, private: bool) -> bool:
        """A public destination goes through the deployment's proxy unless NO_PROXY lists it; a private entry is the
        deployment's own network and is dialled direct."""
        upstream = self.upstream_egress.upstream
        return upstream is not None and not private and not upstream.bypassed(target.host, address)

    async def _checked_addresses(self, target: Target) -> tuple[tuple[str, ...], bool]:
        """The addresses to dial, and whether a private entry opened them. Public first; only when that refuses a
        private answer and an entry could open the target is the name looked up again for the entry's check."""
        try:
            return await self._public_addresses(target), False
        except EgressRefused as refused:
            entries = self.upstream_egress.entries_for(target)
            if refused.reason is not R.DESTINATION_PRIVATE_ADDRESS or not entries:
                raise
            try:
                addresses = await self._private_resolver.resolve(target.host)
            except HostUnresolvable:
                raise refused from None
            return private_addresses(target, addresses, entries, self.refused_networks), True

    async def _public_addresses(self, target: Target) -> tuple[str, ...]:
        """Resolved once; every answer checked; the caller dials only these (no second lookup to rebind)."""
        try:
            addresses = await self._resolver.resolve(target.host)
        except AddressRefused as exc:
            raise EgressRefused(
                R.DESTINATION_PRIVATE_ADDRESS, "destination resolves to a refused address", host=target.host
            ) from exc
        except HostUnresolvable as exc:
            raise EgressRefused(R.DESTINATION_UNRESOLVABLE, "destination has no address", host=target.host) from exc
        return public_addresses(target, addresses, self.refused_networks)

    async def _token(self, route: TokenRoute, binding: EgressBinding) -> str:
        try:
            return await self._cache.token(route, binding)
        except TokenUnavailable as exc:
            raise EgressRefused(R.TOKEN_UNAVAILABLE, "no token for this route") from exc


def _site_report(target: Target, mode: SiteMode, use: str) -> SiteReport | None:
    return SiteReport.of(shown_path(target.path), mode.value, use)


def _intercepted_target(request: ProxyRequest, host: str, port: int, *, site: bool = False) -> Target:
    """What a request inside an intercepted connection asks for; it may not name another host than the CONNECT
    (an allowed website's is answered 421, WP-63: the name it reached is the one checked)."""
    proxied = parse_target(
        replace(request, target=request.target.replace("https://", "http://", 1))
        if request.target.lower().startswith("https://")
        else request
    )
    if proxied.host != host or proxied.port not in (port, 80):
        reason = R.HOST_MISMATCH if site else R.DESTINATION_NOT_ALLOWED
        raise EgressRefused(reason, "request names another host than its connection", host=host)
    return Target(host=host, port=port, path=proxied.path)


def _git_access(target: Target, binding: EgressBinding) -> GitAccess | None:
    """The git rule of the destination a request goes to (checked before its body, which a git client may encode)."""
    for destination in binding.policy.destinations:
        if normalize_host(destination.host) == target.host and target.port in destination.ports:
            return destination.git
    return None


def _shown(route: TokenRoute, credential: str) -> tuple[str, ...]:
    """The parts of an injected credential that could come back in a response. A Basic credential is also sent
    whole (``user:password``, base64 in the header), which a destination may echo as it received it."""
    if route.injection is CredentialInjection.BASIC:
        password = credential.partition(":")[2]
        return (credential, password) if len(password) >= MIN_SECRET_MATCH else (credential,)
    return (credential,)


def _database_credential(material: str) -> tuple[str, str]:
    try:
        value = json.loads(material)
        user, password = value.get("user") or "", value["password"]
    except (ValueError, KeyError, AttributeError, TypeError):
        raise EgressRefused(R.TOKEN_UNAVAILABLE, "the database credential is unreadable") from None
    if not isinstance(user, str) or not isinstance(password, str):
        raise EgressRefused(R.TOKEN_UNAVAILABLE, "the database credential is unreadable")
    return user, password


def _authority_of(upstream: str) -> tuple[str, int]:
    """A destination's trusted upstream base URL as the (host, port) a tunnel dials."""
    parts = urlsplit(upstream)
    host = parts.hostname or ""
    return host, parts.port or (443 if parts.scheme == "https" else 80)


def _normalized(upstreams: Mapping[str, str]) -> dict[str, str]:
    return {normalize_host(host): url.rstrip("/") for host, url in upstreams.items()}


class _Foreign:
    def __init__(self, bindings: InMemoryEgressBindings, sandbox_id: str) -> None:
        self._bindings = bindings
        self._sandbox_id = sandbox_id

    def __call__(self, value: str) -> bool:
        owner = self._bindings.placeholder_owner(value)
        return owner is not None and owner != self._sandbox_id


__all__ = ["DEFAULT_MAX_BODY_BYTES", "EgressProxy", "ProxyLimits"]
