"""Allowed websites in the egress proxy (WP-63, C6.11): which sites a sandbox reaches, and what it may do there.

An allowed website is a destination with ``SiteAccess``, port 443 only. The proxy ends the sandbox's TLS with its
own authority (``interception.py``), so it sees every request and enforces:

- the name: the ``CONNECT`` host, the TLS name the client asked for and every request's ``Host`` are one (421
  otherwise), so a request cannot be fronted to another site behind the allowed one;
- read-only (``SiteMode.READ``): GET, HEAD and OPTIONS without a body; never a POST, PUT, PATCH or DELETE. The
  sandbox's own ``Authorization`` and cookies are dropped, so a token an attacker planted in the sandbox cannot
  write as the attacker;
- no git, in any mode: a website is never a git remote. Git's smart-HTTP (``info/refs?service=``,
  ``git-upload-pack``, ``git-receive-pack``) is refused at every site, and the release's git hosts (github.com,
  gitlab.com...) are never websites at all: a repository reaches a sandbox only as a bundle PipesHub fetched outside
  it after the person allowed the clone (``repository_fetch``);
- path prefixes, when the entry names them;
- the release's read-only hosts (gist, paste and storage sites): never written to, whatever an entry says;
- the release's relay hosts (a module proxy, a CORS proxy, a webhook catcher, a shortener): every request to one is
  an upload, whatever its method, since it fetches what the URL names or shows the request to anyone;
- the flow rule (SEC-A-01): after the run read private data a site is ``held``, and every request that could carry
  that data waits for the person, a GET's URL as much as a POST's body, as ``fetch_url`` does. Only a registry the
  release names (``registryHosts``) keeps answering reads, and only a path, never a query string: it serves its own
  files and fetches nothing, so a path reaches no one but the registry.

A host-level-only entry (``opaque``) is an uninspected tunnel: only its ``CONNECT`` name and the TLS name in its
ClientHello are checked. Every address is resolved once and checked before it is dialled, as for every destination.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING, Final, Literal

from harness_core.contracts.generated.policy_data import WEBSITE_RULES
from harness_core.sandbox.egress.errors import EgressRefusalReason as R
from harness_core.sandbox.egress.errors import EgressRefused
from harness_core.sandbox.egress.git import RECEIVE_PACK, UPLOAD_PACK
from harness_core.sandbox.types import SiteMode
from harness_core.security.token_patterns import redact_tokens

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from harness_core.sandbox.egress.types import ProxyRequest, Target
    from harness_core.sandbox.types import EgressDestination, EgressPolicy, SiteAccess

SITE_PORT: Final = 443
READ_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS"})
# What the sandbox may send of its own credentials to a read-only site: nothing.
CREDENTIAL_HEADERS: Final = frozenset({"authorization", "cookie", "proxy-authorization"})
# How much of a path an activity line shows.
MAX_SHOWN_PATH: Final = 120
_ENCODED_SEPARATOR: Final = re.compile(r"%(?:2f|5c)", re.IGNORECASE)
_GIT_SERVICES: Final = (f"/{UPLOAD_PACK}", f"/{RECEIVE_PACK}")
SiteUse = Literal["read", "write"]
# The release's data (contracts/harness/core/policy/website-rules.json): hosts never written to, git hosts (never a
# website), and every preset's.
READ_ONLY_HOSTS: Final = tuple(host.root for host in WEBSITE_RULES.read_only_hosts)
GIT_HOSTS: Final = tuple(host.root for host in WEBSITE_RULES.git_hosts)
PRESET_HOSTS: Final = frozenset(host.root for preset in WEBSITE_RULES.presets for host in preset.sites)
# SEC-A-01: registries that stay readable after a private read, and hosts every request to which is an upload.
REGISTRY_HOSTS: Final = frozenset(entry.host for entry in WEBSITE_RULES.registry_hosts)
RELAY_HOSTS: Final = tuple(host.root for host in WEBSITE_RULES.relay_hosts)


def host_covers(pattern: str, host: str) -> bool:
    """``pattern`` (``github.com``, ``*.example.com``) covers ``host``: equal, or a subdomain on a label boundary,
    never the apex (``*.example.com`` does not hold ``example.com``, nor ``evilexample.com``)."""
    if pattern == host:
        return True
    if not pattern.startswith("*."):
        return False
    return host.endswith(pattern[1:]) and len(host) > len(pattern) - 1


def read_only_host(host: str, hosts: Iterable[str] | None = None) -> bool:
    """Whether the release lists ``host`` as one never written to (anyone can read back what is sent to it)."""
    return any(host_covers(entry, host) for entry in (READ_ONLY_HOSTS if hosts is None else hosts))


def relay_host(host: str, hosts: Iterable[str] | None = None) -> bool:
    """Whether ``host`` is, is within, or holds one of the release's relay hosts (``*.workers.dev`` holds every
    Worker): a host that fetches what a request names or lets anyone read it, so any request to it is an upload."""
    return any(host_covers(entry, host) or host_covers(host, entry) for entry in (RELAY_HOSTS if hosts is None else hosts))


def registry_host(host: str) -> bool:
    """Whether the release names ``host`` as a fixed-content package registry (exact names only, never a relay)."""
    return host in REGISTRY_HOSTS and not relay_host(host)


def git_host(host: str) -> bool:
    """Whether ``host`` is, or is within, one of the release's git hosts, or holds one (``*.github.com``): never an
    allowed website."""
    return any(host_covers(entry, host) or host_covers(host, entry) for entry in GIT_HOSTS)


def site_destination(target: Target, policy: EgressPolicy) -> EgressDestination | None:
    """The allowed website ``target`` is, on port 443: an exact entry before a wildcard one."""
    if target.port != SITE_PORT:
        return None
    sites = [d for d in policy.destinations if d.site is not None and SITE_PORT in d.ports]
    exact = next((d for d in sites if d.host == target.host), None)
    return exact or next((d for d in sites if host_covers(d.host, target.host)), None)


def effective_mode(site: SiteAccess, host: str) -> SiteMode:
    """A read-write entry is still read-only for a host the release never lets be written to."""
    return SiteMode.READ if site.mode is SiteMode.READ or read_only_host(host) else SiteMode.READ_WRITE


def _path_of(target_path: str) -> str:
    return target_path.partition("?")[0] or "/"


def path_covered(limit: str, path: str) -> bool:
    """A path limit holds itself and what follows it after a slash (``/docs`` holds ``/docs/api``, not
    ``/docsx``); compared as sent, so an encoded spelling of a prefix is refused, not matched."""
    base = limit.removesuffix("/")
    return path == base or path.startswith(f"{base}/")


def check_site_path(site: SiteAccess, target: Target) -> None:
    path = _path_of(target.path)
    if _ENCODED_SEPARATOR.search(path):
        raise EgressRefused(R.SITE_PATH_NOT_ALLOWED, "an encoded separator in the path", host=target.host)
    if site.paths and not any(path_covered(limit, path) for limit in site.paths):
        raise EgressRefused(R.SITE_PATH_NOT_ALLOWED, "the site is allowed only under its paths", host=target.host)


def git_request(target: Target) -> bool:
    """Whether a request speaks git's smart-HTTP: a service discovery or an upload- or receive-pack."""
    path = _path_of(target.path)
    query = target.path.partition("?")[2]
    return path.endswith(_GIT_SERVICES) or any(part.startswith("service=git-") for part in query.split("&"))


def site_use(request: ProxyRequest, target: Target) -> SiteUse:
    """What a request does at a site: reads (GET, HEAD, OPTIONS without a body), or writes (anything else, and every
    request to a relay host, whose GET is as much a send as a POST)."""
    method = request.method.upper()
    if relay_host(target.host):
        return "write"
    if method in READ_METHODS and not request.body and request.header("transfer-encoding") is None:
        return "read"
    return "write"


def fixed_content_read(target: Target) -> bool:
    """A read the flow rule lets through after a private read: a registry's own path, no query string. The path
    reaches no one but the registry (its logs are the residual, never an attacker's server); a query string is how
    a request carries what a program chose, and no install needs one."""
    return registry_host(target.host) and "?" not in target.path


def held_for_flow(
    destinations: Sequence[EgressDestination], *, forbidden: bool = False, released: frozenset[str] = frozenset()
) -> tuple[EgressDestination, ...]:
    """The flow rule on a sandbox's websites (SEC-A-01, the one narrowing the worker binds and the proxy applies
    live, SEC-A-02): a level that forbids web egress keeps no site; otherwise every inspected site is held, so each
    request that could carry what the run read waits for the person, unless they released its host (``released``),
    and a host-level tunnel, which the proxy cannot hold, is dropped. Destinations that are not websites (the
    gateways, git) are kept as they are."""
    out: list[EgressDestination] = []
    for d in destinations:
        site = d.site
        if site is None:
            out.append(d)
        elif forbidden or site.opaque:
            continue
        elif site.held or d.host in released:
            out.append(d)
        else:
            out.append(replace(d, site=replace(site, held=True)))
    return tuple(out)


def check_site_request(
    request: ProxyRequest, target: Target, site: SiteAccess, *, released: bool = False
) -> tuple[SiteMode, SiteUse]:
    """The request as the site's entry allows it, or ``EgressRefused``: never git, then its path, then its method.
    A held site (the flow rule after a private read) answers only a registry's fixed-content read until
    ``released`` (the person allowed the site for the run); every other request waits for them."""
    if git_request(target) or git_host(target.host):
        raise EgressRefused(R.SITE_GIT_REFUSED, "a website is never a git remote", host=target.host)
    check_site_path(site, target)
    mode = effective_mode(site, target.host)
    use = site_use(request, target)
    if use == "write" and mode is SiteMode.READ:
        if relay_host(target.host):
            raise EgressRefused(
                R.SITE_RELAY, "this site fetches or shows what it is sent, so every request is an upload",
                host=target.host,
            )
        raise EgressRefused(R.SITE_READ_ONLY, "the site is allowed read-only", host=target.host)
    if site.held and not released:
        if use == "write":
            raise EgressRefused(
                R.SITE_UPLOAD_HELD,
                "uploads to this site wait for the person to allow them: this conversation read private data",
                host=target.host,
            )
        if not fixed_content_read(target):
            raise EgressRefused(
                R.SITE_REQUEST_HELD,
                "requests to this site wait for the person to allow them: this conversation read private data",
                host=target.host,
            )
        return SiteMode.READ, use
    return mode, use


def read_only_request(request: ProxyRequest) -> ProxyRequest:
    """``request`` without the sandbox's own credentials, for a read-only site."""
    return replace(request, headers=tuple((k, v) for k, v in request.headers if k.lower() not in CREDENTIAL_HEADERS))


def shown_path(target_path: str) -> str:
    """The path an activity line shows: no query, tokens masked, cut short."""
    path = _path_of(target_path).rstrip("/") or "/"
    path = redact_tokens(path)
    return path if len(path) <= MAX_SHOWN_PATH else path[: MAX_SHOWN_PATH - 1] + "…"


TLS_HANDSHAKE: Final = 0x16
CLIENT_HELLO: Final = 0x01
SERVER_NAME_EXTENSION: Final = 0x0000
MAX_RECORD: Final = 16384 + 5


def client_hello_sni(data: bytes) -> str | None:
    """The host name a TLS ClientHello asks for (its ``server_name`` extension), or ``None``: not a ClientHello,
    cut short, or naming none. Only the first record is read; a hello spread over several records names none."""

    def u16(at: int) -> int:
        return int.from_bytes(data[at : at + 2], "big")

    if len(data) < 9 or data[0] != TLS_HANDSHAKE or data[5] != CLIENT_HELLO:
        return None
    end = min(len(data), 5 + u16(3))
    at = 9 + 2 + 32
    if at >= end:
        return None
    at += 1 + data[at]
    at += 2 + u16(at)
    if at >= end:
        return None
    at += 1 + data[at]
    extensions_end = min(end, at + 2 + u16(at))
    at += 2
    while at + 4 <= extensions_end:
        kind, size = u16(at), u16(at + 2)
        body = at + 4
        if kind == SERVER_NAME_EXTENSION and body + 5 <= extensions_end and data[body + 2] == 0:
            length = u16(body + 3)
            name = data[body + 5 : body + 5 + length]
            if len(name) != length or not name.isascii():
                return None
            return name.decode("ascii").lower().rstrip(".")
        at = body + size
    return None


__all__ = [
    "CREDENTIAL_HEADERS",
    "GIT_HOSTS",
    "PRESET_HOSTS",
    "READ_ONLY_HOSTS",
    "REGISTRY_HOSTS",
    "RELAY_HOSTS",
    "READ_METHODS",
    "SITE_PORT",
    "SiteUse",
    "check_site_path",
    "client_hello_sni",
    "check_site_request",
    "effective_mode",
    "fixed_content_read",
    "git_host",
    "held_for_flow",
    "git_request",
    "host_covers",
    "path_covered",
    "read_only_host",
    "read_only_request",
    "registry_host",
    "relay_host",
    "shown_path",
    "site_destination",
    "site_use",
]
