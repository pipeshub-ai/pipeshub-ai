"""
Robust & fast URL fetcher with multi-strategy fallback.

Fallback chain:
  1. curl_cffi with browser impersonation (rotates profiles)
  2. curl_cffi with HTTP/1.1 forced (some sites reject HTTP/2 fingerprints)
  3. cloudscraper (JS challenge solver, if installed)
  4. Plain requests with stealth headers (last resort)

Install:
  pip install curl_cffi
  pip install cloudscraper requests   # optional fallbacks
"""

import ipaddress
import random
import re
import socket
import time
from dataclasses import dataclass, replace
from functools import partial
from typing import TYPE_CHECKING, Literal, override
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit

from app.utils.logger import create_logger

if TYPE_CHECKING:
    from curl_cffi import CurlOpt
    from requests import PreparedRequest

    # requests' stubs name the return type of the method it documents for overriding only privately.
    from requests.adapters import (
        HTTPAdapter,
        _HostParams,  # pyright: ignore[reportPrivateUsage]
        _PoolKwargs,  # pyright: ignore[reportPrivateUsage]
    )

logger = create_logger(__name__)

# ---------------------------------------------------------------------------
# HTTP status constants
# ---------------------------------------------------------------------------
HTTP_STATUS_OK = 200
HTTP_STATUS_MULTIPLE_CHOICES = 300
HTTP_STATUS_BAD_REQUEST = 400
HTTP_STATUS_FORBIDDEN = 403
HTTP_STATUS_CLIENT_ERROR_MAX = 500


# ---------------------------------------------------------------------------
# Response wrapper (unified across all strategies)
# ---------------------------------------------------------------------------

@dataclass
class FetchResult:
    status_code: int
    text: str
    content: bytes
    headers: dict
    url: str
    strategy: str  # which strategy succeeded




class FetchError(Exception):
    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


# Hostnames that must never be fetched (SSRF / metadata endpoints).
_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "metadata.google.internal",
    }
)


# RFC 6052 NAT64 "Well-Known Prefix" — DNS64 resolvers synthesize addresses in this range to
# carry real IPv4 destinations over IPv6-only networks (common on NAT64/DNS64 VPNs and carrier
# networks). Python's `IPv6Address.is_reserved` flags the entire `::/8` superblock — which
# contains this prefix — as reserved, which would otherwise block every public IPv4-only host
# resolved from such a network.
_NAT64_WELL_KNOWN_PREFIX = ipaddress.IPv6Network("64:ff9b::/96")


_DEFAULT_PORTS = {"http": 80, "https": 443}

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


@dataclass(frozen=True)
class PublicTarget:
    """A validated http(s) URL target: every address in ``addresses`` passed the SSRF policy."""

    scheme: str
    host: str
    port: int
    addresses: tuple[IPAddress, ...]

    @property
    def pinned_address(self) -> IPAddress:
        """The validated address every transport connects to for this hop."""
        return self.addresses[0]


# Cloud metadata / platform endpoints that no range rule catches: Alibaba Cloud's metadata
# service sits in CGNAT space (allowed when block_non_global is False) and Azure's WireServer
# uses a public address. Link-local metadata (169.254.169.254, ...) and AWS's IPv6 IMDS
# (fd00:ec2::254) are already covered by is_link_local / is_private.
_CLOUD_METADATA_ADDRESSES = frozenset(
    {
        ipaddress.ip_address("100.100.100.200"),
        ipaddress.ip_address("168.63.129.16"),
    }
)


def _ip_is_blocked(ip: IPAddress, *, block_non_global: bool = True) -> bool:
    """True if the address must not be contacted by the generic HTTP fetcher.

    NAT64 well-known-prefix addresses are unwrapped to their embedded IPv4 address and
    re-checked against the same rules, instead of trusting `is_reserved` — so a NAT64-routed
    private/loopback/metadata IPv4 address is still blocked, but a NAT64-routed public one
    (e.g. a legitimate SaaS host resolved from an IPv6-only network) is not.

    ``block_non_global`` also rejects addresses that are not globally routable but are not
    flagged private either, e.g. CGNAT ``100.64.0.0/10``. Known cloud metadata addresses are
    rejected regardless of it.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip in _NAT64_WELL_KNOWN_PREFIX:
        embedded_ipv4 = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        return _ip_is_blocked(embedded_ipv4, block_non_global=block_non_global)
    if ip in _CLOUD_METADATA_ADDRESSES:
        return True
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or (block_non_global and not ip.is_global)
    )


def _hostname_is_blocked(hostname: str) -> bool:
    hn = hostname.lower().removesuffix(".")
    if hn in _BLOCKED_HOSTNAMES:
        return True
    if hn.endswith(".localhost") or hn.endswith(".local"):
        return True
    return False


def resolve_public_http_target(url: str, *, block_non_global: bool = True) -> PublicTarget:
    """Resolve ``url`` and reject it if it could reach loopback, RFC1918, link-local,
    cloud metadata or other non-public addresses. Every resolved address must pass,
    so one private A record among public ones is enough to reject the URL.

    This is the single blocked-host policy: ``validate_public_http_url``, ``fetch_url``
    (per redirect hop) and ``app.utils.public_http`` all go through it.

    Raises:
        FetchError: if the URL's scheme/hostname/port/resolved address is disallowed.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _DEFAULT_PORTS:
        raise FetchError(f"Only HTTP/HTTPS URLs are allowed, got scheme {parsed.scheme!r}")

    hostname = parsed.hostname
    if not hostname:
        raise FetchError("URL has no hostname")

    try:
        port = parsed.port or _DEFAULT_PORTS[parsed.scheme]
    except ValueError as e:
        raise FetchError("URL has an invalid port") from e

    if _hostname_is_blocked(hostname):
        raise FetchError(f"Blocked unsafe URL hostname: {hostname}")

    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        if _ip_is_blocked(literal_ip, block_non_global=block_non_global):
            raise FetchError(f"Blocked unsafe URL address: {literal_ip}")
        return PublicTarget(parsed.scheme, hostname, port, (literal_ip,))

    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise FetchError(f"Could not resolve hostname {hostname!r}: {e}") from e

    addresses: list[IPAddress] = []
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if _ip_is_blocked(ip, block_non_global=block_non_global):
            raise FetchError(f"Blocked unsafe URL: hostname {hostname!r} resolves to {ip}")
        if ip not in addresses:
            addresses.append(ip)

    if not addresses:
        raise FetchError(f"No addresses resolved for hostname {hostname!r}")
    return PublicTarget(parsed.scheme, hostname, port, tuple(addresses))


def validate_public_http_url(url: str, *, block_non_global: bool = True) -> None:
    """Public entry point for the SSRF guard above — reused by callers outside this module
    (e.g. MCP OAuth metadata discovery) so there is exactly one blocked-host policy for
    fetching admin-supplied URLs, instead of a second copy drifting out of sync.

    Raises:
        FetchError: if the URL's scheme/hostname/resolved address is disallowed.
    """
    resolve_public_http_target(url, block_non_global=block_non_global)


_MAX_REDIRECTS = 5
_FOLLOWED_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def _is_redirect_status(status_code: int) -> bool:
    return HTTP_STATUS_MULTIPLE_CHOICES <= status_code < HTTP_STATUS_BAD_REQUEST


def _redirect_location(result: FetchResult) -> str | None:
    if result.status_code not in _FOLLOWED_REDIRECT_STATUSES:
        return None
    headers: dict[str, str] = result.headers
    return next((v for k, v in headers.items() if k.lower() == "location"), None)


# Hostname and userinfo characters that curl reads exactly as urllib does.
_CURL_SAFE_HOSTNAME = re.compile(r"[a-z0-9_-]+(?:\.[a-z0-9_-]+)*\.?")
_CURL_SAFE_USERINFO = re.compile(r"[A-Za-z0-9._~!$&'()*+,;=:%-]*")


def _curl_pinned_request(
    url: str, pin: PublicTarget
) -> "tuple[str, dict[CurlOpt, str | list[str]]]":
    """The URL and curl options that confine a curl_cffi request to ``pin``'s address.

    CURLOPT_RESOLVE is keyed on the hostname as curl reads it, and curl resolves a name that
    misses the entry by itself. curl reads some hosts differently from urllib (IDNA,
    percent-escapes, a backslash in the userinfo), so the URL is rebuilt around exactly the
    host the entry names, and anything curl could read another way is refused.
    """
    from curl_cffi import CurlOpt

    parts = urlsplit(url)
    # libcurl reads *_proxy from the environment by itself; a proxy would resolve the name again.
    curl_options: dict[CurlOpt, str | list[str]] = {CurlOpt.PROXY: ""}
    try:
        literal = ipaddress.ip_address(pin.host)
    except ValueError:
        literal = None
    if literal is not None:
        host = f"[{literal}]" if literal.version == 6 else str(literal)
    else:
        try:
            host = pin.host.encode("idna").decode("ascii")
        except UnicodeError as e:
            raise FetchError(f"Invalid hostname {pin.host!r}") from e
        if not _CURL_SAFE_HOSTNAME.fullmatch(host):
            raise FetchError(f"Unsupported characters in hostname {pin.host!r}")
        address = pin.pinned_address
        pinned = f"[{address}]" if address.version == 6 else str(address)
        curl_options[CurlOpt.RESOLVE] = [f"{host}:{pin.port}:{pinned}"]

    userinfo, at, _ = parts.netloc.rpartition("@")
    if at and not _CURL_SAFE_USERINFO.fullmatch(userinfo):
        raise FetchError("Unsupported characters in the URL's credentials")
    port = f":{parts.port}" if parts.port is not None else ""
    request_url = urlunsplit(
        (pin.scheme, f"{userinfo}{at}{host}{port}", parts.path, parts.query, "")
    )
    return request_url, curl_options


def _require_pinned_peer(peer_ip: str, pin: PublicTarget) -> None:
    """Backstop for the pin: never hand back a response that came from another address."""
    try:
        peer = ipaddress.ip_address(peer_ip)
    except ValueError:
        peer = None
    if peer != pin.pinned_address:
        raise FetchError(f"Connected to {peer_ip!r}, not the validated address for {pin.host!r}")


def _pinned_requests_adapter(pin: PublicTarget) -> "HTTPAdapter":
    """A requests adapter that connects to ``pin``'s address, keeping the URL's host for the
    Host header, SNI and certificate verification."""
    from requests.adapters import HTTPAdapter

    class PinnedAddressAdapter(HTTPAdapter):
        @override
        def build_connection_pool_key_attributes(
            self,
            request: "PreparedRequest",
            verify: bool | str,
            cert: tuple[str, str] | str | None = None,
        ) -> "tuple[_HostParams, _PoolKwargs]":
            host_params, pool_kwargs = super().build_connection_pool_key_attributes(request, verify, cert)
            if host_params["scheme"] == "https":
                pool_kwargs["server_hostname"] = host_params["host"].rstrip(".")  # pyright: ignore[reportGeneralTypeIssues]
            host_params["host"] = str(pin.pinned_address)
            return host_params, pool_kwargs

        @override
        def add_headers(self, request: "PreparedRequest", **kwargs: object) -> None:
            request.headers["Host"] = urlsplit(request.url or "").netloc.rpartition("@")[2]

    return PinnedAddressAdapter()


# ---------------------------------------------------------------------------
# Shared headers
# ---------------------------------------------------------------------------

def _build_headers(url: str, referer: str | None, extra: dict | None) -> dict:
    parsed = urlparse(url)
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Referer": referer or f"{parsed.scheme}://{parsed.netloc}/",
    }
    if extra:
        headers.update(extra)
    return headers


# ---------------------------------------------------------------------------
# Strategy 1: curl_cffi with impersonation
# ---------------------------------------------------------------------------

def _get_supported_profiles() -> list[str]:
    try:
        from curl_cffi.requests import Session
    except ImportError:
        return []

    candidates = [
        "chrome131", "chrome124", "chrome120", "chrome119", "chrome116",
        "chrome110", "chrome107", "chrome104", "chrome101", "chrome100",
        "chrome99", "chrome"
    ]
    supported = []
    for p in candidates:
        try:
            s = Session(impersonate=p)
            s.close()
            supported.append(p)
        except Exception:
            continue
    return supported


_PROFILES: list[str] | None = None


def _get_profiles() -> list[str]:
    global _PROFILES
    if _PROFILES is None:
        _PROFILES = _get_supported_profiles()
    return _PROFILES


def _try_curl_cffi(
    url: str,
    headers: dict,
    timeout: int,
    use_http2: bool = True,
    profiles: list[str] | None = None,
    *,
    follow_redirects: bool = True,
    pin: PublicTarget | None = None,
) -> FetchResult | None:
    """Try curl_cffi with rotating profiles or an explicit profile list.

    With ``follow_redirects=False`` a 3xx is returned as-is so the caller can validate the
    next hop. With ``pin`` the request goes only to that validated address.
    """
    try:
        from curl_cffi import CurlOpt
        from curl_cffi.requests import Session
    except ImportError:
        return None

    request_url, curl_options = (url, None) if pin is None else _curl_pinned_request(url, pin)

    if profiles is None:
        available = _get_profiles()
        if not available:
            return None
        profiles_to_try = random.sample(available, min(3, len(available)))
    else:
        # Constrained mode: callsites can force a single profile.
        profiles_to_try = profiles

    if not profiles_to_try:
        return None

    for profile in profiles_to_try:
        try:
            with Session(
                impersonate=profile, timeout=timeout, trust_env=pin is None, curl_options=curl_options
            ) as session:
                # Force HTTP/1.1 if requested (bypasses HTTP/2 fingerprinting)
                if not use_http2:
                    try:
                        session.curl.setopt(CurlOpt.HTTP_VERSION, 2)  # CURL_HTTP_VERSION_1_1
                    except Exception:
                        pass

                resp = session.get(request_url, headers=headers, allow_redirects=follow_redirects)
                if pin is not None:
                    _require_pinned_peer(resp.primary_ip, pin)

                if resp.status_code == HTTP_STATUS_OK or (
                    not follow_redirects and _is_redirect_status(resp.status_code)
                ):
                    return FetchResult(
                        status_code=resp.status_code,
                        text=resp.text,
                        content=resp.content,
                        headers=dict(resp.headers),
                        url=str(resp.url),
                        strategy=f"curl_cffi({profile}, h2={use_http2})",
                    )

                # 403 → try next profile
                if resp.status_code == HTTP_STATUS_FORBIDDEN:
                    logger.debug("403 for %s with profile %s", url, profile)
                    continue

                # Other non-retryable errors
                if HTTP_STATUS_BAD_REQUEST <= resp.status_code < HTTP_STATUS_CLIENT_ERROR_MAX:
                    return FetchResult(
                        status_code=resp.status_code,
                        text=resp.text,
                        content=resp.content,
                        headers=dict(resp.headers),
                        url=str(resp.url),
                        strategy=f"curl_cffi({profile})",
                    )

        except FetchError:
            raise
        except Exception:
            logger.debug("Exception for %s with profile %s", url, profile, exc_info=True)
            continue

    return None


# ---------------------------------------------------------------------------
# Strategy 2: cloudscraper
# ---------------------------------------------------------------------------

def _try_cloudscraper(
    url: str, headers: dict, timeout: int, *, follow_redirects: bool = True
) -> FetchResult | None:
    try:
        import cloudscraper
    except ImportError:
        return None

    try:
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        resp = scraper.get(
            url, headers=headers, timeout=timeout, allow_redirects=follow_redirects
        )

        if resp.status_code == HTTP_STATUS_OK or (
            not follow_redirects and _is_redirect_status(resp.status_code)
        ):
            return FetchResult(
                status_code=resp.status_code,
                text=resp.text,
                content=resp.content,
                headers=dict(resp.headers),
                url=resp.url,
                strategy="cloudscraper",
            )
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Strategy 3: plain requests with stealth UA
# ---------------------------------------------------------------------------

def _try_requests(
    url: str,
    headers: dict[str, str],
    timeout: int,
    *,
    follow_redirects: bool = True,
    pin: PublicTarget | None = None,
) -> FetchResult | None:
    try:
        import requests as req
    except ImportError:
        return None

    try:
        session = req.Session()
        if pin is not None:
            session.trust_env = False  # no environment proxies: a proxy would resolve the name again
            session.mount(f"{pin.scheme}://", _pinned_requests_adapter(pin))
        session.headers.update(headers)

        # Add a realistic User-Agent (requests doesn't set one by default)
        ua_list = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        ]
        session.headers["User-Agent"] = random.choice(ua_list)

        resp = session.get(url, timeout=timeout, allow_redirects=follow_redirects)

        if resp.status_code == HTTP_STATUS_OK or (
            not follow_redirects and _is_redirect_status(resp.status_code)
        ):
            return FetchResult(
                status_code=resp.status_code,
                text=resp.text,
                content=resp.content,
                headers=dict(resp.headers),
                url=resp.url,
                strategy="requests",
            )
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Main fetch function
# ---------------------------------------------------------------------------
MAX_RETRIES = 0
def fetch_url(
    url: str,
    *,
    headers: dict | None = None,
    referer: str | None = None,
    timeout: int = 15,
    max_retries: int = MAX_RETRIES,
    strategy: Literal["curl_cffi_h2", "curl_cffi_h1", "cloudscraper", "requests"] | None = None,
    profile: str | None = None,
    verbose: bool = False,
    block_private_hosts: bool = True,
) -> FetchResult:
    """
    Fetch a URL using a multi-strategy fallback chain.

    Tries (in order):
      1. curl_cffi with HTTP/2 impersonation (3 profiles)
      2. curl_cffi with HTTP/1.1 forced (3 profiles)
      3. cloudscraper (if installed)
      4. Plain requests

    Each top-level strategy is retried up to max_retries times with backoff.

    Args:
        url:         Target URL.
        headers:     Extra headers (optional).
        referer:     Referer header (auto-generated if None).
        timeout:     Request timeout in seconds.
        max_retries: Retries per strategy (the whole chain runs once).
        strategy:    Optional single strategy to run (no fallback chain).
        profile:     Optional curl_cffi profile to use (e.g. "chrome120").
        verbose:     Print which strategy is being tried.
        block_private_hosts: When True (default), refuse loopback, RFC1918,
            link-local, CGNAT, metadata-style hosts, and related SSRF-prone targets
            before any network I/O, re-validate every redirect hop (at most
            ``_MAX_REDIRECTS``), and connect each hop only to its validated address,
            without environment proxies or the cloudscraper strategy. Set False only
            for URLs an operator configured, never for URLs a user, a model or a
            third-party document supplied.

    Returns:
        FetchResult with .text, .content, .status_code, .strategy, etc.

    Raises:
        FetchError: If the URL or a redirect hop fails SSRF validation (when
            ``block_private_hosts`` is True), on too many redirects, if all strategies
            fail, or for unknown ``strategy`` values.
    """
    run = partial(
        _run_strategies,
        headers=headers,
        referer=referer,
        timeout=timeout,
        max_retries=max_retries,
        strategy=strategy,
        profile=profile,
        verbose=verbose,
    )
    if not block_private_hosts:
        return run(url, follow_redirects=True, pin=None)

    # Each hop is resolved once and pinned to that answer, so a second DNS answer
    # (rebinding) cannot move the connection somewhere the check never saw.
    current_url = url
    for _ in range(_MAX_REDIRECTS + 1):
        _reject_http_userinfo(current_url)
        target = resolve_public_http_target(current_url)
        result = _run_pinned_with_failover(run, current_url, target)
        location = _redirect_location(result)
        if location is None:
            return result
        current_url = urljoin(current_url, location)
    raise FetchError(f"Too many redirects (more than {_MAX_REDIRECTS})")


def _reject_http_userinfo(url: str) -> None:
    """Refuse ``user:pass@`` in a plain-HTTP URL: basic-auth credentials would cross the
    network in the clear. https keeps them (TLS-encrypted). Applies only to the untrusted
    (SSRF-guarded) fetch path."""
    parts = urlsplit(url)
    if parts.scheme == "http" and (parts.username or parts.password):
        raise FetchError("Credentials in the URL are not allowed over plain HTTP")


def _run_pinned_with_failover(
    run: "partial[FetchResult]", url: str, target: PublicTarget
) -> FetchResult:
    """Run the strategy chain against each validated address until one answers.

    A dual-stack host can return an unreachable address first (e.g. IPv6 with no route);
    the check validated every address, so any is safe to try. Retry only covers transport
    failure (the chain raising ``FetchError`` with no HTTP response); once any address
    returns a response it is used, per the redirect/response contract.
    """
    last_error: FetchError | None = None
    for address in target.addresses:
        try:
            return run(url, follow_redirects=False, pin=replace(target, addresses=(address,)))
        except FetchError as e:
            last_error = e
    raise last_error or FetchError(f"No reachable address for {url}")


def _run_strategies(
    url: str,
    *,
    headers: dict[str, str] | None,
    referer: str | None,
    timeout: int,
    max_retries: int,
    strategy: str | None,
    profile: str | None,
    verbose: bool,
    follow_redirects: bool,
    pin: PublicTarget | None,
) -> FetchResult:
    req_headers = _build_headers(url, referer, headers)
    selected_profiles = [profile] if profile else None

    strategy_map = {
        "curl_cffi_h2": (
            "curl_cffi (HTTP/2)",
            lambda: _try_curl_cffi(
                url,
                req_headers,
                timeout,
                use_http2=True,
                profiles=selected_profiles,
                follow_redirects=follow_redirects,
                pin=pin,
            ),
        ),
        "curl_cffi_h1": (
            "curl_cffi (HTTP/1.1)",
            lambda: _try_curl_cffi(
                url,
                req_headers,
                timeout,
                use_http2=False,
                profiles=selected_profiles,
                follow_redirects=follow_redirects,
                pin=pin,
            ),
        ),
        "cloudscraper": (
            "cloudscraper",
            lambda: _try_cloudscraper(
                url, req_headers, timeout, follow_redirects=follow_redirects
            ),
        ),
        "requests": (
            "requests",
            lambda: _try_requests(
                url, req_headers, timeout, follow_redirects=follow_redirects, pin=pin
            ),
        ),
    }

    if strategy is not None and strategy not in strategy_map:
        raise FetchError(f"Unknown fetch strategy: {strategy}")
    if pin is not None:
        # cloudscraper answers Cloudflare challenges by requesting URLs the challenge names
        # (cloudscraper/cloudflare.py follows its Location), past any hop check or pin.
        if strategy == "cloudscraper":
            raise FetchError("The cloudscraper strategy cannot fetch untrusted URLs")
        del strategy_map["cloudscraper"]
    strategies = [strategy_map[strategy]] if strategy is not None else list(strategy_map.values())

    errors: list[str] = []

    for name, strategy_fn in strategies:
        for attempt in range(max_retries + 1):
            if verbose:
                logger.debug("[%s] attempt %d/%d…", name, attempt + 1, max_retries + 1)

            try:
                result = strategy_fn()
                if result is not None:
                    if verbose:
                        logger.debug("Success via %s", result.strategy)
                    return result
            except Exception as e:
                errors.append(f"{e}")

            # Small backoff between retries of same strategy
            if attempt < max_retries:
                time.sleep(0.5 * (attempt + 1) + random.uniform(0, 0.3))

        if verbose:
            logger.debug("%s exhausted", name)

    raise FetchError(
        errors[0] if errors else "No error details."
    )


