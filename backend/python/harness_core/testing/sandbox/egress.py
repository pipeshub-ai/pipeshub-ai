"""Request-level egress for programs in a fake sandbox.

``FakeEgress`` is deny-all except explicit routes: fake engines send their
model and tool requests through it, and a request matching no route is
refused and recorded, which is how tests prove "no egress outside the
gateways". ``FakeEgressProxy`` is the egress proxy for a ``FakeSandboxBroker``:
the real ``EgressProxy`` core and binding table over an in-memory transport,
so a fake sandbox with an allowlist is refused, bound, credentialed and
logged exactly as a Docker sandbox behind the real proxy (P5.4 §1.5).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, cast, override
from urllib.parse import urlsplit

from harness_core.engines.attempt_labels import bound_attempt
from harness_core.engines.types import JsonValue
from harness_core.sandbox.egress.addresses import SubnetPool
from harness_core.sandbox.egress.admission import binding_of
from harness_core.sandbox.egress.bindings import InMemoryEgressBindings
from harness_core.sandbox.egress.errors import EgressRefused, TokenUnavailable
from harness_core.sandbox.egress.proxy import EgressProxy
from harness_core.sandbox.egress.resolver import StaticResolver
from harness_core.sandbox.egress.sealed import (
    CredentialSealer,
    RoutedTokenSource,
    SealedTokenSource,
)
from harness_core.sandbox.egress.server import is_model_call, refusal_response
from harness_core.sandbox.egress.tokens import (
    DEFAULT_TOKEN_TTL_S,
    TOKEN_PREFIX,
    InMemoryTokenSource,
)
from harness_core.sandbox.egress.types import IssuedToken, ProxyRequest
from harness_core.sandbox.errors import BrokerUnavailable
from harness_core.testing.ids import SeededIds

if TYPE_CHECKING:
    from harness_core.clock import Clock
    from harness_core.ids import IdSource
    from harness_core.sandbox.egress.addresses import EgressSubnet
    from harness_core.sandbox.egress.ports import TokenSource
    from harness_core.sandbox.egress.types import EgressBinding, Forward
    from harness_core.sandbox.types import SandboxSpec, TokenRoute


@dataclass(frozen=True, slots=True)
class EgressRequest:
    url: str
    method: str
    headers: Mapping[str, str]
    body: JsonValue


EgressHandler = Callable[[EgressRequest], Awaitable[JsonValue]]


class EgressDenied(ConnectionError):
    """``words``: the refusal body's message, as the sandbox's client reads it (``refusal_response``)."""

    def __init__(
        self, message: str, *, reason: str | None = None, status: int | None = None, words: str | None = None
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.status = status
        self.words = words


@dataclass
class FakeEgress:
    """Deny-all egress with explicit routes (URL prefix → handler). Records every request."""

    routes: dict[str, EgressHandler] = field(default_factory=dict[str, EgressHandler])
    requests: list[EgressRequest] = field(default_factory=list[EgressRequest])
    denied: list[EgressRequest] = field(default_factory=list[EgressRequest])

    def route(self, prefix: str, handler: EgressHandler) -> None:
        self.routes[prefix.rstrip("/")] = handler

    async def request(
        self, url: str, *, method: str = "POST", headers: Mapping[str, str], body: JsonValue
    ) -> JsonValue:
        req = EgressRequest(
            url=url, method=method, headers={k.lower(): v for k, v in headers.items()}, body=body
        )
        for prefix, handler in self.routes.items():
            if url == prefix or url.startswith(prefix + "/"):
                self.requests.append(req)
                return await handler(req)
        self.denied.append(req)
        raise EgressDenied(f"egress to {url} is not allowed")


_UPSTREAM = "http://fake-upstream.{host}"


class AttemptTokenSource:
    """Tokens as Node's gateway-token authority issues them (E2): only for the run attempt the sandbox's binding
    names (``TokenUnavailable`` otherwise, as ``NodeGatewayTokenSource`` refuses), each attempt its own. The
    in-memory source ignores the labels, which is how a binding naming no attempt went unseen (SIGNFIX)."""

    def __init__(self, secret: bytes, clock: Clock, *, ttl_s: int = DEFAULT_TOKEN_TTL_S) -> None:
        self._secret = secret
        self._clock = clock
        self._ttl_s = ttl_s
        self._attempts: dict[str, tuple[str, str]] = {}
        # (run id, attempt id, credential ref) of every token issued, in order.
        self.issued: list[tuple[str, str, str]] = []
        self.refused = 0

    async def issue(self, route: TokenRoute, binding: EgressBinding) -> IssuedToken:
        named = bound_attempt(binding.labels)
        if named is None:
            self.refused += 1
            raise TokenUnavailable("the sandbox's binding names no run attempt")
        run_id, attempt_id = named
        self.issued.append((run_id, attempt_id, route.credential_ref))
        message = f"{run_id}\x00{attempt_id}\x00{route.credential_ref}\x00{len(self.issued)}".encode()
        value = TOKEN_PREFIX + hmac.new(self._secret, message, hashlib.sha256).hexdigest()[:48]
        self._attempts[value] = named
        return IssuedToken(value, self._clock.now() + timedelta(seconds=self._ttl_s))

    def attempt_of(self, token: str) -> tuple[str, str] | None:
        """The (run, attempt) a token was issued for, as the gateway reads its claims."""
        return self._attempts.get(token)


class FakeEgressProxy:
    """The egress proxy a ``FakeSandboxBroker`` binds sandboxes to.

    ``gateways`` maps a gateway host (as the sandbox names it) to the handler
    that plays the gateway; ``seen`` records what each gateway received, with
    the injected token, so a test can check the token arrived and the sandbox
    never held it.
    """

    def __init__(
        self,
        *,
        clock: Clock,
        gateways: Mapping[str, EgressHandler],
        ids: IdSource | None = None,
        secret: bytes = b"fake-egress-secret",
        resolver: StaticResolver | None = None,
        pool: SubnetPool | None = None,
        tokens: TokenSource | None = None,
    ) -> None:
        """``tokens``: unset, an ``InMemoryTokenSource`` on ``secret`` (``expected_token`` reads it) for run-bound
        routes, and ``sealer`` (on the same secret) opening sealed ones, as the real proxy does."""
        self.secret = secret
        self.sealer = CredentialSealer(secret)
        self.bindings = InMemoryEgressBindings()
        self.tokens = tokens or RoutedTokenSource(InMemoryTokenSource(secret, clock), SealedTokenSource(self.sealer, clock))
        self._gateways = {host.lower(): handler for host, handler in gateways.items()}
        self.proxy = EgressProxy(
            bindings=self.bindings,
            tokens=self.tokens,
            resolver=resolver or StaticResolver(),
            upstreams={host: _UPSTREAM.format(host=host) for host in self._gateways},
            clock=clock,
            ids=ids or SeededIds(),
        )
        self._pool = pool or SubnetPool()
        self._subnets: dict[str, EgressSubnet] = {}
        self.seen: list[EgressRequest] = []
        self._upstream_routes: dict[str, EgressHandler] = {}

    def route_upstream(self, base_url: str, handler: EgressHandler) -> None:
        """Answer forwards to ``base_url`` (a sandbox's own ``EgressDestination.upstream``) with ``handler``."""
        self._upstream_routes[base_url.rstrip("/")] = handler

    def expected_token(self, sandbox_id: str, credential_ref: str, generation: int = 0) -> str:
        return InMemoryTokenSource.derive(self.secret, sandbox_id, credential_ref, generation)

    async def bind(self, sandbox_id: str, spec: SandboxSpec) -> ProxiedEgress:
        """A sandbox already bound keeps its subnet and is bound again (a re-acquire, as the Docker broker does)."""
        subnet = self._subnets.get(sandbox_id) or self._pool.allocate({s.cidr for s in self._subnets.values()})
        if subnet is None:
            raise BrokerUnavailable("the egress subnet pool is exhausted")
        binding = binding_of(sandbox_id, subnet, spec)
        await self.bindings.register(binding)
        self._subnets[sandbox_id] = subnet
        return ProxiedEgress(self, binding)

    def unbind(self, sandbox_id: str) -> None:
        self.bindings.discard(sandbox_id)
        _ = self._subnets.pop(sandbox_id, None)

    async def dispatch(self, forward: Forward, method: str) -> JsonValue:
        parts = urlsplit(forward.url)
        host = (parts.hostname or "").removeprefix("fake-upstream.")
        handler = next(
            (h for base, h in self._upstream_routes.items() if forward.url.startswith(base + "/")),
            self._gateways.get(host),
        )
        if handler is None:
            raise EgressDenied("no upstream answers", reason="upstream_unavailable", status=502)
        request = EgressRequest(
            url=forward.url,
            method=method,
            headers={k.lower(): v for k, v in forward.headers},
            body=json.loads(forward.body) if forward.body else None,
        )
        self.seen.append(request)
        result = await handler(request)
        text = json.dumps(result)
        for token, placeholder in forward.reflections:
            text = text.replace(token, placeholder)
        return json.loads(text)


class ProxiedEgress(FakeEgress):
    """One fake sandbox's egress: every request goes through the proxy core with this sandbox's addresses."""

    def __init__(self, proxy: FakeEgressProxy, binding: EgressBinding) -> None:
        super().__init__()
        self._proxy = proxy
        self.binding = binding

    @override
    async def request(
        self, url: str, *, method: str = "POST", headers: Mapping[str, str], body: JsonValue
    ) -> JsonValue:
        req = EgressRequest(url=url, method=method, headers={k.lower(): v for k, v in headers.items()}, body=body)
        encoded = json.dumps(body).encode() if body is not None else b""
        proxied = ProxyRequest(method=method, target=url, headers=tuple(headers.items()), body=encoded)
        try:
            forward = await self._proxy.proxy.authorize(
                proxied, local=self.binding.proxy_address, peer=self.binding.sandbox_address
            )
        except EgressRefused as refused:
            self.denied.append(req)
            model_call = is_model_call(method, url, proxied.headers)
            error = json.loads(cast("bytes", refusal_response(refused, model_call=model_call).body))["error"]
            raise EgressDenied(
                str(refused), reason=refused.reason.value, status=refused.status, words=error.get("message")
            ) from refused
        self.requests.append(req)
        return await self._proxy.dispatch(forward, method)


__all__ = ["AttemptTokenSource", "EgressDenied", "EgressHandler", "EgressRequest", "FakeEgress", "FakeEgressProxy", "ProxiedEgress"]
