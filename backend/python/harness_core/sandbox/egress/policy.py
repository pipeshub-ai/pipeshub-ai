"""The egress proxy's decisions as pure functions (P5.4 §1.2). No I/O, no clock, no logging."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import math
import re
from dataclasses import dataclass, replace
from functools import lru_cache
from http import HTTPStatus
from typing import TYPE_CHECKING
from urllib.parse import quote, unquote, urlsplit

from harness_core.gateway.refusal_reports import POLICY_PATTERN_KIND
from harness_core.sandbox.egress.errors import EgressRefusalReason as R
from harness_core.sandbox.egress.errors import EgressRefused
from harness_core.sandbox.egress.git import branch_ok, repository_ok
from harness_core.sandbox.egress.sites import (
    PRESET_HOSTS,
    SITE_PORT,
    git_host,
    read_only_host,
    site_destination,
)
from harness_core.sandbox.egress.types import Target
from harness_core.sandbox.types import CredentialInjection, EgressDefault, SiteMode
from harness_core.security.token_patterns import (
    FORBIDDEN_PATTERN_SOURCES,
    FORBIDDEN_TOKEN_PATTERNS,
    HIDDEN_CREDENTIAL,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from harness_core.sandbox.egress.types import EgressBinding, ProxyRequest
    from harness_core.sandbox.types import EgressDestination, EgressPolicy, TokenRoute

    Network = ipaddress.IPv4Network | ipaddress.IPv6Network

# Vendor credential shapes refused in any request, whatever the policy adds. A tripwire for
# secrets pasted into tool arguments or prompts, not data-loss prevention: encodings hide them.
# One list for every enforcement point (the tool gateway and the replay scrubber use it too).
DEFAULT_FORBIDDEN_PATTERNS: tuple[str, ...] = FORBIDDEN_PATTERN_SOURCES
MIN_PLACEHOLDER_LENGTH = 16
# A credential shorter than this is neither scrubbed from responses nor treated as a replay: an operator's short
# password ("admin") would otherwise rewrite and refuse ordinary text.
MIN_SECRET_MATCH = 8
# Where a sandbox's PostgreSQL client connects: the destination's name resolves to the proxy, which listens here.
POSTGRES_PORT = 5432
POSTGRES_SCHEMES = frozenset({"postgres", "postgresql"})
DEFAULT_HTTP_PORT = 80
REQUEST_ID_HEADER = "X-Egress-Request-Id"
REFUSAL_HEADER = "X-Egress-Refusal"
# C6.12 (WP-61): on a Model Gateway request of a run past its egress cap, so the gateway ends the run with
# ``egress_volume_exceeded``. Only the proxy sets it: a sandbox's own is dropped.
EGRESS_CAPPED_HEADER = "X-Egress-Capped"
EGRESS_CAPPED_VOLUME = "volume"
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
# Set by the proxy on the way out, whatever the sandbox sent.
_REPLACED = frozenset(
    {"host", "authorization", "accept-encoding", "content-length", REQUEST_ID_HEADER.lower(), EGRESS_CAPPED_HEADER.lower()}
)


def normalize_host(host: str) -> str:
    return host.strip().lower().rstrip(".")


# SEC-EG-02: a refused name whose labels look like encoded data (a long label of letters and digits mixed with high
# character entropy: hex, base32, lower-cased base64; or a very long name) is how DNS exfiltration reads. The proxy
# refuses it like any other name and alerts on it; descriptive names (words and hyphens) are not suspects.
_SUSPECT_LABEL_LENGTH = 16
_SUSPECT_DIGIT_SHARE = 0.1
_SUSPECT_ENTROPY_BITS = 3.0
_SUSPECT_NAME_LENGTH = 100


def _entropy_bits(text: str) -> float:
    counts = {ch: text.count(ch) for ch in set(text)}
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def _encoded(label: str) -> bool:
    return (
        len(label) >= _SUSPECT_LABEL_LENGTH
        and sum(ch.isdigit() for ch in label) >= _SUSPECT_DIGIT_SHARE * len(label)
        and _entropy_bits(label) >= _SUSPECT_ENTROPY_BITS
    )


def exfiltration_suspect(host: str) -> bool:
    """Whether a host name carries what looks like encoded data in its labels (SEC-EG-02)."""
    return len(host) > _SUSPECT_NAME_LENGTH or any(_encoded(label) for label in normalize_host(host).split("."))


def redacted_host(host: str) -> str:
    """The host as it may be logged and reported: each label that looks encoded becomes ``…``, and a name too long
    to be anything but data keeps only its last two labels."""
    if not exfiltration_suspect(host):
        return host
    labels = ["…" if _encoded(label) else label for label in normalize_host(host).split(".")]
    if len(host) > _SUSPECT_NAME_LENGTH:
        labels = ["…", *labels[-2:]]
    return ".".join(labels)


_LABEL = re.compile(r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?")
_NUMBER = re.compile(r"0x[0-9a-f]*|[0-9]+")
_MAX_NAME = 253


def is_host_name(host: str) -> bool:
    """An ASCII host name or an IP literal, the one form the proxy matches, resolves and forwards to (SEC-EG-06).

    Not one: a character outside a name's letters, digits, ``-`` and ``_`` (NUL, ``%00``, a Unicode homoglyph or a
    fullwidth dot, which a later layer may map to another name than the one checked), an empty label, and a name
    whose last label is a number but is not an address (``127.1``, ``2130706433``, ``0x7f.1``), which a resolver reads
    as an address. An internationalized name is accepted in its ASCII form (``xn--``) only."""
    if not host.isascii():
        return False
    name = normalize_host(host)
    if _is_ip_literal(name.removeprefix("[").removesuffix("]")):
        return True
    if not name or len(name) > _MAX_NAME:
        return False
    labels = name.split(".")
    return all(_LABEL.fullmatch(label) for label in labels) and not _NUMBER.fullmatch(labels[-1])


def parse_target(request: ProxyRequest) -> Target:
    """Where the sandbox wants to go: absolute-form URL, or origin-form plus ``Host``."""
    if request.method.upper() == "CONNECT":
        raise EgressRefused(R.TUNNEL_REFUSED, "CONNECT tunnels are not offered")
    raw = request.target
    if raw.startswith("/"):
        host_header = request.header_values("host")
        if len(host_header) != 1:
            raise EgressRefused(R.MALFORMED_REQUEST, "origin-form request needs exactly one Host header")
        authority, path = host_header[0], raw
    else:
        try:
            parts = urlsplit(raw)
        except ValueError as exc:
            raise EgressRefused(R.MALFORMED_REQUEST, "request target is not a URL") from exc
        if parts.scheme.lower() != "http":
            raise EgressRefused(R.SCHEME_NOT_ALLOWED, "only plain http to the proxy is accepted")
        authority = parts.netloc
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    host, port = _authority(authority)
    if _has_dot_segment(path):
        raise EgressRefused(R.PATH_NOT_ALLOWED, "a dot segment in the request path")
    return Target(host=host, port=port, path=path)


def _has_dot_segment(path: str) -> bool:
    # The proxy appends this path to an upstream's base URL, and aiohttp removes `.`/`..` segments, percent-encoded
    # ones included, before it sends: such a segment would reach paths outside the gateway's base (C-26).
    return any(unquote(segment) in (".", "..") for segment in path.partition("?")[0].split("/"))


def parse_tunnel_target(request: ProxyRequest) -> Target:
    """A ``CONNECT`` request's authority-form target: a host name and an explicit port, never an address."""
    raw = request.target
    if not raw or not raw.isascii() or any(ch in raw for ch in "/@?#") or any(ch.isspace() for ch in raw):
        raise EgressRefused(R.MALFORMED_REQUEST, "CONNECT needs host:port")
    try:
        parts = urlsplit("//" + raw)
        port = parts.port
    except ValueError as exc:
        raise EgressRefused(R.MALFORMED_REQUEST, "malformed port") from exc
    host = normalize_host(parts.hostname or "")
    if not host or port is None:
        raise EgressRefused(R.MALFORMED_REQUEST, "CONNECT needs host:port")
    if not is_host_name(host):
        raise EgressRefused(R.MALFORMED_REQUEST, "the host is not an ASCII host name or an address")
    if _is_ip_literal(host):
        raise EgressRefused(R.TUNNEL_IP_LITERAL, "a tunnel is opened to a name the proxy resolves, not an address")
    return Target(host=host, port=port, path="")


def check_tunnel_destination(target: Target, policy: EgressPolicy, gateway_hosts: Iterable[str]) -> EgressDestination:
    """The allowlisted destination a tunnel may reach; credentialed hosts and gateways are never tunnelled."""
    destination = check_destination(target, policy)
    if route_for(target, policy) is not None or target.host in gateway_hosts:
        raise EgressRefused(R.TUNNEL_REFUSED, "credentialed and gateway destinations are not tunnelled")
    return destination


def _authority(authority: str) -> tuple[str, int]:
    # Checked before ``urlsplit``, whose lower-casing maps some non-ASCII letters to ASCII ones (KELVIN SIGN to k).
    if not authority or "@" in authority or not authority.isascii() or any(ch.isspace() for ch in authority):
        raise EgressRefused(R.MALFORMED_REQUEST, "malformed authority")
    try:
        parts = urlsplit("//" + authority)
        port = parts.port
    except ValueError as exc:
        raise EgressRefused(R.MALFORMED_REQUEST, "malformed port") from exc
    host = normalize_host(parts.hostname or "")
    if not host:
        raise EgressRefused(R.MALFORMED_REQUEST, "request has no host")
    if not is_host_name(host):
        raise EgressRefused(R.MALFORMED_REQUEST, "the host is not an ASCII host name or an address")
    return host, port if port is not None else DEFAULT_HTTP_PORT


@lru_cache(maxsize=256)
def compile_patterns(extra: tuple[str, ...] = ()) -> tuple[re.Pattern[bytes], ...]:
    return tuple(re.compile(p.encode("utf-8")) for p in (*DEFAULT_FORBIDDEN_PATTERNS, *extra))


def _surfaces(request: ProxyRequest) -> Iterable[bytes]:
    yield request.target.encode("utf-8", "surrogateescape")
    for key, value in request.headers:
        yield f"{key}: {value}".encode("utf-8", "surrogateescape")
    yield request.body


def forbidden_pattern_index(request: ProxyRequest, patterns: Sequence[re.Pattern[bytes]]) -> int | None:
    """The first pattern found in the target, a header or the body; ``None`` if clean."""
    for surface in _surfaces(request):
        for index, pattern in enumerate(patterns):
            if pattern.search(surface):
                return index
    return None


def contains_any(request: ProxyRequest, secrets: Iterable[str]) -> bool:
    needles = [s.encode("utf-8") for s in secrets if s]
    return any(needle in surface for surface in _surfaces(request) for needle in needles)


# WP-103: bytes a replacement inside a JSON string may not span (a quote, a backslash, a control character): a match
# holding one is refused, never rewritten, so the body the gateway parses is the document the engine sent.
_JSON_SYNTAX = re.compile(rb'["\\\x00-\x1f]')
_HIDDEN = HIDDEN_CREDENTIAL.encode("ascii")
_DEFAULT_KINDS: tuple[str, ...] = tuple(p.kind for p in FORBIDDEN_TOKEN_PATTERNS)


@dataclass(frozen=True, slots=True)
class Inspected:
    """A request the content checks passed, as it may leave: ``hidden`` names the credential shapes replaced in its
    body (a forbidden kind, or ``policy_pattern`` for the policy's own), in pattern order."""

    request: ProxyRequest
    hidden: tuple[str, ...] = ()


def hide_in_body(body: bytes, patterns: Sequence[re.Pattern[bytes]]) -> tuple[bytes, tuple[str, ...]]:
    """``body`` with each match of ``patterns`` replaced by ``HIDDEN_CREDENTIAL`` where the match holds no JSON
    syntax (a match that does is left for the scan after, which refuses it), and the kinds replaced."""
    hidden: list[str] = []
    for index, pattern in enumerate(patterns):
        replaced = False

        def mask(match: re.Match[bytes]) -> bytes:
            nonlocal replaced
            if _JSON_SYNTAX.search(match.group(0)):
                return match.group(0)
            replaced = True
            return _HIDDEN

        body = pattern.sub(mask, body)
        if replaced:
            kind = _DEFAULT_KINDS[index] if index < len(_DEFAULT_KINDS) else POLICY_PATTERN_KIND
            if kind not in hidden:
                hidden.append(kind)
    return body, tuple(hidden)


def check_request_content(
    request: ProxyRequest, policy: EgressPolicy, live_tokens: Iterable[str], *, model_context: bool = False
) -> Inspected:
    """Refuse a request carrying a credential shape or a live gateway token anywhere. ``model_context``: the request
    is an engine's call to the Model Gateway, whose body is the run's own context going to the org's model; a shape
    there is hidden and the call goes on (WP-103: public pages the run read carry documentation examples of these
    shapes), while its target and headers are checked as any request's."""
    if request.header("content-encoding") not in (None, "", "identity"):
        raise EgressRefused(R.BODY_ENCODED, "encoded request bodies cannot be inspected")
    patterns = compile_patterns(tuple(policy.forbidden_patterns))
    hidden: tuple[str, ...] = ()
    if model_context:
        body, hidden = hide_in_body(request.body, patterns)
        request = replace(request, body=body)
    index = forbidden_pattern_index(request, patterns)
    if index is not None:
        raise EgressRefused(R.FORBIDDEN_PATTERN, f"request matches forbidden pattern #{index}")
    if contains_any(request, (token for token in live_tokens if len(token) >= MIN_SECRET_MATCH)):
        raise EgressRefused(R.TOKEN_REPLAYED, "request carries a gateway token")
    return Inspected(request, hidden)


def check_destination(target: Target, policy: EgressPolicy) -> EgressDestination:
    for destination in policy.destinations:
        if (
            destination.site is None
            and normalize_host(destination.host) == target.host
            and target.port in destination.ports
        ):
            return destination
    site = site_destination(target, policy)
    if site is not None:
        return site
    raise EgressRefused(R.DESTINATION_NOT_ALLOWED, "destination is not allowlisted", host=target.host)


def _bearer(value: str) -> str | None:
    scheme, _, token = value.strip().partition(" ")
    return token.strip() or None if scheme.lower() == "bearer" else None


def basic_password(value: str) -> str | None:
    """The password of an ``Authorization: Basic`` value, ``None`` when it is not one."""
    scheme, _, encoded = value.strip().partition(" ")
    if scheme.lower() != "basic":
        return None
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
    except (binascii.Error, ValueError):
        return None
    _, sep, password = decoded.partition(":")
    return password if sep else None


def basic_authorization(credential: str) -> str:
    """``Authorization`` for a ``user:password`` credential."""
    return "Basic " + base64.b64encode(credential.encode("utf-8")).decode("ascii")


def route_for(target: Target, policy: EgressPolicy) -> TokenRoute | None:
    for route in policy.token_routes:
        if normalize_host(route.destination) == target.host:
            return route
    return None


def routes_for(target: Target, policy: EgressPolicy) -> tuple[TokenRoute, ...]:
    return tuple(route for route in policy.token_routes if normalize_host(route.destination) == target.host)


def _substitutable(policy: EgressPolicy) -> frozenset[str]:
    return frozenset(r.placeholder for r in policy.token_routes if r.injection is CredentialInjection.SUBSTITUTE)


def _texts(request: ProxyRequest) -> Iterable[str]:
    yield request.target
    for _, value in request.headers:
        yield value


def _present(request: ProxyRequest, placeholders: Iterable[str]) -> frozenset[str]:
    texts = list(_texts(request))
    return frozenset(p for p in placeholders if any(p in text for text in texts))


def credential_routes(
    request: ProxyRequest,
    target: Target,
    binding: EgressBinding,
    is_foreign: Callable[[str], bool],
) -> tuple[TokenRoute, ...]:
    """Every route whose credential this request gets; ``()`` for an uncredentialed host (or a substituting host the
    request names no placeholder for). A placeholder sent anywhere it is not routed is refused."""
    routes = routes_for(target, binding.policy)
    substitutable = _substitutable(binding.policy)
    if not routes or routes[0].injection is not CredentialInjection.SUBSTITUTE:
        route = check_credentials(request, target, binding, is_foreign)
        # A substituting placeholder (a program's own secret) must not leave for a host it is not meant for.
        if _present(request, substitutable):
            raise EgressRefused(R.PLACEHOLDER_WRONG_DESTINATION, "placeholder sent to a host it is not for")
        return () if route is None else (route,)
    routed = {r.placeholder for r in routes}
    if _present(request, binding.placeholders() - routed):
        raise EgressRefused(R.PLACEHOLDER_WRONG_DESTINATION, "placeholder sent to a host it is not for")
    for value in request.header_values("authorization"):
        presented = _bearer(value) or basic_password(value) or value.strip()
        if presented in binding.placeholders() - routed:
            raise EgressRefused(R.PLACEHOLDER_WRONG_DESTINATION, "placeholder sent to a host it is not for")
        if presented not in routed and is_foreign(presented):
            raise EgressRefused(R.PLACEHOLDER_FOREIGN, "placeholder belongs to another sandbox")
    present = _present(request, routed)
    return tuple(r for r in routes if r.placeholder in present)


def substitute(text: str, values: Sequence[tuple[str, str]]) -> str:
    for placeholder, value in values:
        text = text.replace(placeholder, value)
    return text


def check_credentials(
    request: ProxyRequest,
    target: Target,
    binding: EgressBinding,
    is_foreign: Callable[[str], bool],
) -> TokenRoute | None:
    """The route whose token replaces the sandbox's placeholder, or ``None`` for an uncredentialed host."""
    values = request.header_values("authorization")
    presented = [_bearer(v) or basic_password(v) or v.strip() for v in values]
    own = binding.placeholders()
    route = route_for(target, binding.policy)
    if route is not None and route.injection is CredentialInjection.POSTGRES:
        raise EgressRefused(R.SCHEME_NOT_ALLOWED, "a database destination is reached over its own protocol")
    if route is None:
        for value in presented:
            if value in own:
                raise EgressRefused(R.PLACEHOLDER_WRONG_DESTINATION, "placeholder sent to a host it is not for")
            if is_foreign(value):
                raise EgressRefused(R.PLACEHOLDER_FOREIGN, "placeholder belongs to another sandbox")
        return None
    if not values:
        raise EgressRefused(R.PLACEHOLDER_MISSING, "credentialed destination needs the placeholder")
    if len(values) == 1 and route.injection is CredentialInjection.BEARER and _bearer(values[0]) == route.placeholder:
        return route
    if len(values) == 1 and route.injection is CredentialInjection.BASIC and basic_password(values[0]) == route.placeholder:
        return route
    for value in presented:
        if value in own and value != route.placeholder:
            raise EgressRefused(R.PLACEHOLDER_WRONG_DESTINATION, "placeholder sent to a host it is not for")
        if is_foreign(value):
            raise EgressRefused(R.PLACEHOLDER_FOREIGN, "placeholder belongs to another sandbox")
    raise EgressRefused(R.CALLER_AUTHORIZATION, "caller-supplied credentials to a credentialed host")


def is_public_address(address: str, blocked: Sequence[Network] = ()) -> bool:
    """Whether an upstream address is outside every private, local and reserved range."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return False
    return not any(ip.version == net.version and ip in net for net in blocked)


def parse_networks(cidrs: Iterable[str]) -> tuple[Network, ...]:
    return tuple(ipaddress.ip_network(c.strip(), strict=False) for c in cidrs if c.strip())


def public_addresses(target: Target, addresses: Sequence[str], blocked: Sequence[Network] = ()) -> tuple[str, ...]:
    """The addresses that may be dialled, in order; every one must be public (one private answer refuses all)."""
    if not addresses:
        raise EgressRefused(R.DESTINATION_UNRESOLVABLE, "destination has no address", host=target.host)
    if not all(is_public_address(a, blocked) for a in addresses):
        raise EgressRefused(
            R.DESTINATION_PRIVATE_ADDRESS, "destination resolves to a non-public address", host=target.host
        )
    return tuple(addresses)


def check_addresses(target: Target, addresses: Sequence[str], blocked: Sequence[Network] = ()) -> str:
    """The address to connect to; every resolved address must be public (no rebinding to a private one)."""
    return public_addresses(target, addresses, blocked)[0]


def _connection_tokens(headers: Iterable[tuple[str, str]]) -> frozenset[str]:
    named = (v for k, v in headers if k.lower() == "connection")
    return frozenset(part.strip().lower() for value in named for part in value.split(",") if part.strip())


def outbound_headers(
    request: ProxyRequest,
    target: Target,
    *,
    token: str | None,
    request_id: str,
    authorization: str | None = None,
    substitutions: Sequence[tuple[str, str]] = (),
    default_port: int = DEFAULT_HTTP_PORT,
    keep_authorization: bool = False,
    capped: bool = False,
) -> tuple[tuple[str, str], ...]:
    """The headers sent upstream. ``token``: a bearer token; ``authorization``: the whole value instead (Basic);
    ``substitutions``: (placeholder, credential) pairs replaced in every header the sandbox sent. With
    substitutions the sandbox's own ``Authorization`` is kept (it may carry a placeholder), and with
    ``keep_authorization`` (a read-write allowed website, WP-63, whose admin let the sandbox sign in as itself);
    otherwise it is dropped. ``capped``: the run is past its egress cap (``EGRESS_CAPPED_HEADER``)."""
    replaced = _REPLACED - {"authorization"} if substitutions or keep_authorization else _REPLACED
    dropped = _HOP_BY_HOP | replaced | _connection_tokens(request.headers)
    kept = [(k, substitute(v, substitutions)) for k, v in request.headers if k.lower() not in dropped]
    host = target.host if target.port == default_port else f"{target.host}:{target.port}"
    added = [("Host", host), ("Accept-Encoding", "identity"), (REQUEST_ID_HEADER, request_id)]
    if authorization is None and token is not None:
        authorization = f"Bearer {token}"
    if authorization is not None:
        added.append(("Authorization", authorization))
    if capped:
        added.append((EGRESS_CAPPED_HEADER, EGRESS_CAPPED_VOLUME))
    return (*kept, *added)


def reflections_of(pairs: Iterable[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    """(secret, stand-in) pairs worth scrubbing from a response: long enough not to match ordinary text."""
    return tuple((secret, standin) for secret, standin in pairs if len(secret) >= MIN_SECRET_MATCH)


def _base64_cores(secret: bytes, encode: Callable[[bytes], bytes]) -> set[str]:
    """The base64 characters fixed by ``secret`` alone, at each of the three byte alignments it can take inside a
    longer encoded payload: padded, unpadded and embedded (``user:token`` in a Basic header, a token inside an
    encoded JSON document) all contain one of them."""
    cores: set[str] = set()
    for offset in range(3):
        encoded = encode(b"\0" * offset + secret).decode("ascii")
        core = encoded[-(-4 * offset // 3) : (offset + len(secret)) * 8 // 6]
        if len(core) >= MIN_SECRET_MATCH:
            cores.add(core)
    return cores


def reflection_variants(reflections: Sequence[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    """SEC-C-15: every (shape of a secret, stand-in) pair a response is scrubbed of. A destination may echo the
    swapped credential encoded rather than as sent: URL-encoded (either hex case), base64 (standard or URL-safe, any
    alignment) or hex. Longest first, so a shape that contains another is replaced whole."""
    variants: dict[str, str] = {}
    for secret, standin in reflections:
        variants.setdefault(secret, standin)
        if len(secret) < MIN_SECRET_MATCH:
            # A short secret is scrubbed only as given: its encodings would match ordinary text.
            continue
        raw = secret.encode("utf-8")
        shapes = {raw.hex(), raw.hex().upper()}
        quoted = quote(secret, safe="")
        shapes |= {quoted, re.sub(r"%[0-9A-F]{2}", lambda m: m.group(0).lower(), quoted)}
        shapes |= _base64_cores(raw, base64.b64encode) | _base64_cores(raw, base64.urlsafe_b64encode)
        for shape in shapes:
            if len(shape) >= MIN_SECRET_MATCH:
                variants.setdefault(shape, standin)
    return tuple(sorted(variants.items(), key=lambda pair: -len(pair[0])))


def response_headers(
    headers: Iterable[tuple[str, str]], reflections: Sequence[tuple[str, str]] = ()
) -> tuple[tuple[str, str], ...]:
    return ResponseScrubber(reflections).headers(headers)


class ReflectionScrubber:
    """Replaces a secret in a byte stream with a stand-in, also across chunk boundaries."""

    def __init__(self, secret: str, replacement: str) -> None:
        if not secret:
            raise ValueError("secret is empty")
        self._secret = secret.encode("utf-8")
        self._replacement = replacement.encode("utf-8")
        self._tail = b""

    def feed(self, chunk: bytes) -> bytes:
        data = (self._tail + chunk).replace(self._secret, self._replacement)
        # Hold back only a suffix that could start the secret, so streamed events are not delayed.
        held = self._held(data)
        self._tail = data[len(data) - held :] if held else b""
        return data[: len(data) - held]

    def _held(self, data: bytes) -> int:
        for size in range(min(len(data), len(self._secret) - 1), 0, -1):
            if data.endswith(self._secret[:size]):
                return size
        return 0

    def finish(self) -> bytes:
        tail, self._tail = self._tail, b""
        return tail


class ReflectionScrubbers:
    """Every (secret, stand-in) pair scrubbed from one stream, each across chunk boundaries."""

    def __init__(self, reflections: Sequence[tuple[str, str]]) -> None:
        self._chain = [ReflectionScrubber(secret, standin) for secret, standin in reflections]

    def feed(self, chunk: bytes) -> bytes:
        for scrubber in self._chain:
            chunk = scrubber.feed(chunk)
        return chunk

    def finish(self) -> bytes:
        out = b""
        for scrubber in self._chain:
            out = scrubber.feed(out) + scrubber.finish()
        return out


class ResponseScrubber:
    """SEC-C-15: the one place an upstream response is made safe for the sandbox, the credential swap in reverse.
    Every field the proxy forwards goes through it: the status line, each header's name and value, and the body."""

    def __init__(self, reflections: Sequence[tuple[str, str]]) -> None:
        self._forms = reflection_variants(reflections)

    def text(self, value: str) -> str:
        return substitute(value, self._forms)

    @staticmethod
    def reason(status: int) -> str:
        """The standard phrase for ``status``, never the upstream's own: a reason phrase is free text the
        destination chooses, so nothing of it is passed on."""
        try:
            return HTTPStatus(status).phrase
        except ValueError:
            return ""

    def headers(self, headers: Iterable[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
        pairs = list(headers)
        dropped = _HOP_BY_HOP | {"content-length"} | _connection_tokens(pairs)
        return tuple((self.text(k), self.text(v)) for k, v in pairs if k.lower() not in dropped)

    def body(self) -> ReflectionScrubbers | None:
        """A fresh streaming scrubber for one body; ``None`` when nothing was swapped in."""
        return ReflectionScrubbers(self._forms) if self._forms else None


def policy_errors(policy: EgressPolicy) -> list[str]:
    """Why a spec's egress policy cannot be bound; checked by the broker before anything is created."""
    errors: list[str] = []
    if not policy.needs_proxy:
        return errors
    if policy.default is EgressDefault.ALLOW:
        errors.append("an allowlist cannot be combined with unrestricted egress")
    hosts = [normalize_host(d.host) for d in policy.destinations]
    errors.extend(
        f"destination {d.host!r} is malformed" for d in policy.destinations if d.site is None and not _host_ok(d.host)
    )
    errors.extend(
        f"destination {d.host!r} has a port outside 1..65535"
        for d in policy.destinations
        if not d.ports or any(not 0 < p < 65536 for p in d.ports)
    )
    errors.extend(
        f"destination {d.host!r} has an upstream that is not a plain http(s) or postgres base URL"
        for d in policy.destinations
        if d.upstream is not None and not _upstream_ok(d.upstream)
    )
    errors.extend(_git_errors(policy))
    errors.extend(_site_errors(policy))
    for pattern in policy.forbidden_patterns:
        try:
            _ = re.compile(pattern.encode("utf-8"))
        except re.error:
            errors.append(f"forbidden pattern #{policy.forbidden_patterns.index(pattern)} does not compile")
    seen_placeholders: dict[str, TokenRoute] = {}
    seen_destinations: dict[str, TokenRoute] = {}
    for index, route in enumerate(policy.token_routes):
        errors.extend(_route_errors(index, route, hosts))
        destination = normalize_host(route.destination)
        earlier = seen_placeholders.get(route.placeholder)
        # One secret may go to several hosts only as a substitution of the same credential.
        if earlier is not None and not (
            route.injection is earlier.injection is CredentialInjection.SUBSTITUTE
            and route.credential_ref == earlier.credential_ref
            and destination != normalize_host(earlier.destination)
        ):
            errors.append(f"token route #{index} reuses a placeholder")
        sharing = seen_destinations.get(destination)
        if sharing is not None and not (route.injection is sharing.injection is CredentialInjection.SUBSTITUTE):
            errors.append(f"token route #{index} is a second route for its destination")
        seen_placeholders.setdefault(route.placeholder, route)
        seen_destinations.setdefault(destination, route)
    errors.extend(_postgres_errors(policy))
    return errors


def _git_errors(policy: EgressPolicy) -> list[str]:
    errors: list[str] = []
    for d in policy.destinations:
        if d.git is None:
            continue
        if not repository_ok(d.git.repository):
            errors.append(f"git destination {d.host!r} names a malformed repository")
        if d.git.push_branch is not None and not branch_ok(d.git.push_branch):
            errors.append(f"git destination {d.host!r} names a malformed push branch")
    return errors


_SITE_PATH = re.compile(r"/[A-Za-z0-9._~!$&'()*+,;=:@-][A-Za-z0-9._~!$&'()*+,;=:@/-]{0,199}")


def _site_host_ok(host: str) -> bool:
    """A site's host: a name (never an address), or ``*.`` and a name of at least two labels."""
    name = host.removeprefix("*.")
    return (
        host == normalize_host(host)
        and "*" not in name
        and not _is_ip_literal(name)
        and is_host_name(name)
        and len(name.split(".")) >= 2
    )


def _site_errors(policy: EgressPolicy) -> list[str]:
    """WP-63: what a broker refuses of an allowed website before anything is created, whatever its caller checked."""
    errors: list[str] = []
    for d in policy.destinations:
        site = d.site
        if site is None:
            continue
        if not _site_host_ok(d.host):
            errors.append(f"site {d.host!r} is not a host name or a *. wildcard of one")
        if git_host(d.host):
            errors.append(f"site {d.host!r} is a git host, never a website")
        if tuple(d.ports) != (SITE_PORT,):
            errors.append(f"site {d.host!r} is reached on port {SITE_PORT} only")
        if d.git is not None or d.upstream is not None:
            errors.append(f"site {d.host!r} cannot also be a repository or upstream destination")
        if any(not _SITE_PATH.fullmatch(p) or any(seg in (".", "..") for seg in p.split("/")) for p in site.paths):
            errors.append(f"site {d.host!r} has a malformed path limit")
        if site.opaque and (
            site.mode is not SiteMode.READ_WRITE
            or d.host.startswith("*.")
            or bool(site.paths)
            or d.host in PRESET_HOSTS
            or read_only_host(d.host)
        ):
            errors.append(f"site {d.host!r} cannot be host-level only")
        if any(normalize_host(r.destination) == d.host for r in policy.token_routes):
            errors.append(f"site {d.host!r} carries no credential route")
    return errors


def _postgres_errors(policy: EgressPolicy) -> list[str]:
    routes = [r for r in policy.token_routes if r.injection is CredentialInjection.POSTGRES]
    if not routes:
        return []
    errors: list[str] = []
    if len(routes) > 1:
        errors.append("a sandbox reaches at most one database through the proxy")
    for route in routes:
        destination = next(
            (d for d in policy.destinations if normalize_host(d.host) == normalize_host(route.destination)), None
        )
        if destination is None:
            continue
        if tuple(destination.ports) != (POSTGRES_PORT,):
            errors.append(f"database destination {destination.host!r} must use port {POSTGRES_PORT} only")
        if destination.upstream is None or urlsplit(destination.upstream).scheme not in POSTGRES_SCHEMES:
            errors.append(f"database destination {destination.host!r} needs a postgres:// upstream")
    return errors


def _upstream_ok(upstream: str) -> bool:
    try:
        parts = urlsplit(upstream)
        _ = parts.port
    except ValueError:
        return False
    if parts.scheme in POSTGRES_SCHEMES:
        # The database's user name is not a secret; its password only ever travels sealed in a route.
        query = [pair.partition("=")[0] for pair in parts.query.split("&") if pair]
        return bool(parts.hostname) and parts.password is None and not parts.fragment and set(query) <= {"sslmode"}
    return (
        parts.scheme in ("http", "https")
        and bool(parts.hostname)
        and parts.username is None
        and parts.password is None
        and not parts.query
        and not parts.fragment
    )


def _route_errors(index: int, route: TokenRoute, hosts: Sequence[str]) -> list[str]:
    errors: list[str] = []
    placeholder = route.placeholder
    if len(placeholder) < MIN_PLACEHOLDER_LENGTH or not placeholder.isprintable() or " " in placeholder:
        errors.append(f"token route #{index} placeholder is shorter than {MIN_PLACEHOLDER_LENGTH} or not printable")
    if any(p.search(placeholder.encode("utf-8")) for p in compile_patterns()):
        errors.append(f"token route #{index} placeholder looks like a vendor credential")
    if normalize_host(route.destination) not in hosts:
        errors.append(f"token route #{index} destination is not allowlisted")
    if not route.credential_ref:
        errors.append(f"token route #{index} has no credential ref")
    return errors


def _host_ok(host: str) -> bool:
    return is_host_name(host)


def _is_ip_literal(host: str) -> bool:
    try:
        _ = ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


__all__ = [
    "Inspected",
    "DEFAULT_FORBIDDEN_PATTERNS",
    "DEFAULT_HTTP_PORT",
    "EGRESS_CAPPED_HEADER",
    "EGRESS_CAPPED_VOLUME",
    "MIN_PLACEHOLDER_LENGTH",
    "MIN_SECRET_MATCH",
    "POSTGRES_PORT",
    "POSTGRES_SCHEMES",
    "REFUSAL_HEADER",
    "REQUEST_ID_HEADER",
    "ReflectionScrubber",
    "ReflectionScrubbers",
    "ResponseScrubber",
    "basic_authorization",
    "basic_password",
    "check_addresses",
    "check_credentials",
    "check_destination",
    "check_request_content",
    "hide_in_body",
    "check_tunnel_destination",
    "compile_patterns",
    "contains_any",
    "credential_routes",
    "exfiltration_suspect",
    "forbidden_pattern_index",
    "is_host_name",
    "is_public_address",
    "normalize_host",
    "outbound_headers",
    "parse_networks",
    "parse_target",
    "parse_tunnel_target",
    "policy_errors",
    "public_addresses",
    "redacted_host",
    "reflection_variants",
    "reflections_of",
    "response_headers",
    "route_for",
    "routes_for",
    "substitute",
]
