"""Value types of the egress proxy (P5.4)."""

from __future__ import annotations

from collections.abc import (
    Mapping,  # noqa: TC003 - the wire codec resolves it at runtime
)
from dataclasses import dataclass, field
from datetime import datetime  # noqa: TC003 - the wire codec resolves it at runtime
from typing import TYPE_CHECKING, ClassVar

from harness_core.sandbox.types import STRICT_WIRE, EgressPolicy

if TYPE_CHECKING:
    from pydantic import ConfigDict

    from harness_core.sandbox.egress.volume import VolumeMeter

# The one port a sandbox uses for every gateway, on Docker (the proxy's address on the
# sandbox's network) and on K8s (the sidecar on loopback), so engine configuration never changes.
EGRESS_PROXY_PORT = 8097
EGRESS_CONTROL_PORT = 8098
# The name a sandbox's hosts file gives the proxy (absolute-form requests via HTTP_PROXY).
EGRESS_PROXY_HOST = "egress-proxy"
# Where a bound sandbox reads the certificate the proxy signs intercepted connections with (not a secret).
EGRESS_CA_PATH = "/.well-known/pipeshub-egress-ca.pem"


@dataclass(frozen=True, slots=True)
class EgressBinding:
    """One sandbox as the proxy knows it: which connection is it, and what may it do."""

    __pydantic_config__: ClassVar[ConfigDict] = STRICT_WIRE
    sandbox_id: str
    proxy_address: str
    sandbox_address: str
    policy: EgressPolicy = field(default_factory=EgressPolicy)
    # The spec's opaque correlation labels (org, session, run, attempt for PipesHub).
    labels: Mapping[str, str] = field(default_factory=lambda: dict[str, str]())
    # The broker's proof of the run attempt the labels name; the proxy forwards it, uninterpreted, when it asks for
    # that run's gateway token, and cannot make one itself (R38IDENT-FU-7).
    attestation: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "labels", dict(self.labels))

    def placeholders(self) -> frozenset[str]:
        return frozenset(route.placeholder for route in self.policy.token_routes)


@dataclass(frozen=True, slots=True)
class IssuedToken:
    value: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ProxyRequest:
    """A request as the sandbox sent it; ``target`` is the raw request-target."""

    method: str
    target: str
    headers: tuple[tuple[str, str], ...]
    body: bytes = b""

    def header(self, name: str) -> str | None:
        lowered = name.lower()
        for key, value in self.headers:
            if key.lower() == lowered:
                return value
        return None

    def header_values(self, name: str) -> list[str]:
        lowered = name.lower()
        return [value for key, value in self.headers if key.lower() == lowered]


@dataclass(frozen=True, slots=True)
class Target:
    host: str
    port: int
    path: str


@dataclass(frozen=True, slots=True)
class Forward:
    """What the transport sends upstream for an authorized request."""

    request_id: str
    sandbox_id: str
    method: str
    url: str
    headers: tuple[tuple[str, str], ...]
    body: bytes
    # (credential, placeholder) pairs to scrub from the response, one per credential injected.
    reflections: tuple[tuple[str, str], ...] = ()
    # Set when the upstream is dialled by address over TLS: the name its certificate must carry.
    server_hostname: str | None = None
    # G-21: the deployment's forward proxy this request goes through (``http://host:port``), and the headers only
    # that proxy receives (its ``Proxy-Authorization``); ``None`` goes direct.
    proxy: str | None = None
    proxy_headers: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    # C6.12: counts what this request moves on its run; ``None`` counts nothing.
    meter: VolumeMeter | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class Tunnel:
    """An authorized ``CONNECT``: where it may go, already resolved and checked (or a trusted upstream)."""

    request_id: str
    sandbox_id: str
    host: str
    port: int
    # (address, port) pairs to try in order; nothing else is ever dialled for this tunnel.
    connect_to: tuple[tuple[str, int], ...]
    labels: Mapping[str, str] = field(default_factory=lambda: dict[str, str]())
    # A credentialed destination: the proxy ends the sandbox's TLS itself and serves each request inside as an
    # authorized forward, so the credential is put on it where the sandbox never sees it (OWD-1).
    intercept: bool = False
    # G-21: dial ``connect_to`` through the deployment's forward proxy (a ``CONNECT`` to the checked address).
    via_upstream_proxy: bool = False
    meter: VolumeMeter | None = field(default=None, repr=False, compare=False)
    # WP-63: a host-level-only allowed website: the TLS name in the client's first message must be ``host``.
    require_sni: bool = False


@dataclass(frozen=True, slots=True)
class DatabaseForward:
    """An authorized PostgreSQL connection: where the proxy signs in, as whom; the password is never shown."""

    request_id: str
    sandbox_id: str
    destination: str
    host: str
    port: int
    sslmode: str
    user: str
    password: str = field(repr=False)
    labels: Mapping[str, str] = field(default_factory=lambda: dict[str, str]())
    meter: VolumeMeter | None = field(default=None, repr=False, compare=False)


__all__ = [
    "EGRESS_CA_PATH",
    "EGRESS_CONTROL_PORT",
    "EGRESS_PROXY_HOST",
    "EGRESS_PROXY_PORT",
    "DatabaseForward",
    "EgressBinding",
    "Forward",
    "IssuedToken",
    "ProxyRequest",
    "Target",
    "Tunnel",
]
