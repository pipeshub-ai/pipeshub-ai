"""RFC 7591 Dynamic Client Registration + RFC 9728/8414 OAuth metadata discovery +
PKCE helpers for MCP OAuth.

Discovery chain (`discover_oauth_metadata`) probes, in order:
  1. RFC 9728 protected-resource metadata on the MCP server itself. If present and it lists
     `authorization_servers`, those hosts become the authorization-server (AS) candidates
     instead of the MCP server's own host — e.g. GitHub's MCP server delegates to
     `github.com/login/oauth`, which shares no host with `api.githubcopilot.com`.
  2. RFC 8414 AS metadata (falling back to OIDC discovery) on each AS candidate, trying the
     RFC 8414 section 3.1 path-inserted well-known URL first, then the bare authority root —
     some real MCP servers (Atlassian's) only serve metadata at the root.

The result tells the authorize flow both the real `authorization_endpoint`/`token_endpoint`
to use (which can differ from an admin-typed or template-default URL — Notion's and
Atlassian's own catalog templates originally pointed at their *direct* OAuth endpoints, not
the ones that front their MCP servers) and whether dynamic client registration is available,
so a static admin-configured OAuth app is only ever required — never silently ignored — when
the server genuinely needs one.

Used when a custom or catalog MCP server advertises OAuth metadata but the admin has not
manually registered a static OAuth app for it — PipesHub registers its own client on the fly
and reuses it for every subsequent authorization.

All timestamps are timezone-aware UTC (`datetime.now(timezone.utc)`), never naive `datetime.now()`.
"""
import asyncio
import base64
import hashlib
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

import httpx

from app.agents.mcp.errors import MCPUrlBlockedError
from app.agents.mcp.models import (
    DCRClient,
    DiscoveredOAuthMetadata,
    TokenEndpointAuthMethod,
    is_web_url,
)
from app.agents.mcp.url_guard import (
    MCPUrlPolicy,
    PerOriginGuardedTransport,
    check_mcp_url,
)
from app.agents.mcp.www_authenticate import bearer_challenge, challenge_scopes
from mcp.shared.auth_utils import check_resource_allowed, resource_url_from_server_url

logger = logging.getLogger(__name__)

DISCOVERY_REQUEST_TIMEOUT_SECONDS = 5.0
DISCOVERY_TOTAL_TIMEOUT_SECONDS = 20.0
DCR_REQUEST_TIMEOUT_SECONDS = 15.0

WELL_KNOWN_OAUTH_METADATA_PATH = "/.well-known/oauth-authorization-server"
WELL_KNOWN_OIDC_PATH = "/.well-known/openid-configuration"
WELL_KNOWN_PROTECTED_RESOURCE_PATH = "/.well-known/oauth-protected-resource"
# Bounds total requests if a malicious/misconfigured protected-resource document lists an
# excessive number of authorization_servers — each candidate costs up to 4 requests below.
MAX_AS_ISSUER_CANDIDATES = 3


class InvalidOAuthEndpointError(ValueError):
    """An OAuth endpoint isn't an http(s) URL; it must never reach the browser."""


def _endpoints_are_web_urls(as_metadata: dict[str, Any]) -> bool:
    """Authorization-server metadata is usable only if every endpoint it names is a web URL;
    a server answering `javascript:` is treated like one that published nothing."""
    endpoints = [as_metadata.get(key) for key in ("authorization_endpoint", "token_endpoint", "registration_endpoint")]
    return all(is_web_url(url) for url in endpoints if url is not None)


class DCRError(Exception):
    """Raised when dynamic client registration or metadata discovery fails."""


class DiscoveryBlockedError(DCRError):
    """Raised when a discovery/registration target resolves to a disallowed (private,
    loopback, or otherwise internal) address — an SSRF guard for admin-supplied URLs."""


def generate_code_verifier(n: int = 64) -> str:
    return base64.urlsafe_b64encode(os.urandom(n)).decode().rstrip("=")


def generate_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def generate_state() -> str:
    return secrets.token_urlsafe(32)


async def assert_discovery_target_allowed(url: str, *, allow_private: bool) -> None:
    """Fail fast with a clear error before any request, under the same policy as the MCP
    connection itself (`url_guard`); callers pass `MCPUrlPolicy.private_ok(url)`. The
    request is then pinned by `PerOriginGuardedTransport`, so a DNS answer that changes
    between this check and the connect can't move it."""
    try:
        await check_mcp_url(url, allow_private=allow_private)
    except MCPUrlBlockedError as e:
        raise DiscoveryBlockedError(str(e)) from e


def _discovery_client(timeout: float, policy: MCPUrlPolicy) -> httpx.AsyncClient:
    # Redirects stay off (the httpx default): each hop would need its own check.
    return httpx.AsyncClient(timeout=timeout, trust_env=False, transport=PerOriginGuardedTransport(policy))


def _authority_without_userinfo(base_url: str) -> Optional[str]:
    """Scheme + host[:port] only — strips URL userinfo so embedded credentials never reach
    discovery requests or logs."""
    parsed = urlparse(base_url)
    if not parsed.scheme or not parsed.netloc:
        return None
    # Drop userinfo (`user:pass@`) while keeping host/port, including bracketed IPv6.
    netloc = parsed.netloc.rsplit("@", 1)[-1]
    return f"{parsed.scheme}://{netloc}"


def _well_known_candidate_urls(base_url: str, well_known_path: str) -> list[str]:
    """RFC 8414 section 3.1 candidate well-known URLs for `base_url`, in probe order — the
    suffix is inserted *between* the authority and the path, never appended after the full
    URL (e.g. for `https://mcp.atlassian.com/v1/sse` the candidate is
    `https://mcp.atlassian.com/.well-known/oauth-authorization-server/v1/sse`, not
    `https://mcp.atlassian.com/v1/sse/.well-known/oauth-authorization-server`). Falls back to
    the bare authority root, since some real servers only serve metadata there.
    """
    authority = _authority_without_userinfo(base_url)
    if authority is None:
        return []
    path = urlparse(base_url).path.rstrip("/")

    urls = []
    if path:
        urls.append(f"{authority}{well_known_path}{path}")
    urls.append(f"{authority}{well_known_path}")
    return urls


def _authorization_server_metadata_urls(issuer: str) -> list[str]:
    """Where an authorization server's metadata may be, in the order the MCP authorization
    spec gives for an issuer with a path: RFC 8414 and OpenID discovery with the well-known
    path inserted before the issuer's path, then OpenID discovery appended after it (Entra ID,
    Okta and Keycloak serve it there), then RFC 8414 appended (Okta serves both). The bare
    host comes last: on Okta it is the org's server, not the custom one the MCP server uses."""
    authority = _authority_without_userinfo(issuer)
    if authority is None:
        return []
    path = urlparse(issuer).path.rstrip("/")
    urls: list[str] = []
    if path:
        urls += [
            f"{authority}{WELL_KNOWN_OAUTH_METADATA_PATH}{path}",
            f"{authority}{WELL_KNOWN_OIDC_PATH}{path}",
            f"{authority}{path}{WELL_KNOWN_OIDC_PATH}",
            f"{authority}{path}{WELL_KNOWN_OAUTH_METADATA_PATH}",
        ]
    urls += [f"{authority}{WELL_KNOWN_OAUTH_METADATA_PATH}", f"{authority}{WELL_KNOWN_OIDC_PATH}"]
    return urls


async def _fetch_well_known_json(
    client: httpx.AsyncClient, candidate_urls: list[str], policy: MCPUrlPolicy,
) -> Optional[dict[str, Any]]:
    for url in candidate_urls:
        try:
            await assert_discovery_target_allowed(url, allow_private=policy.private_ok(url))
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.debug("No metadata at well-known endpoint (%s)", type(e).__name__)
    return None


_MAX_METADATA_URL_CHARS = 2048
# What a probe sends: a request any MCP endpoint answers, without credentials.
_PROBE_PROTOCOL_VERSION = "2025-11-25"
_PROBE_BODY = {
    "jsonrpc": "2.0", "id": 0, "method": "initialize",
    "params": {"protocolVersion": _PROBE_PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "PipesHub", "version": "0"}},
}


def _without_userinfo(url: str) -> Optional[str]:
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return urlunsplit(parsed._replace(netloc=parsed.netloc.rsplit("@", 1)[-1], fragment=""))


async def _challenge_hints(
    client: httpx.AsyncClient, server_url: str, policy: MCPUrlPolicy, *, sse: bool,
) -> tuple[Optional[str], list[str]]:
    """The protected-resource metadata URL and the scopes a 401 from the server names.

    The MCP spec has a client use these before the well-known addresses. One request without
    credentials (a legacy SSE endpoint takes only GET); only the status and headers are read,
    so a server that answers with an open stream can't hold discovery up."""
    url = _without_userinfo(server_url)
    if url is None:
        return None, []
    try:
        await assert_discovery_target_allowed(url, allow_private=policy.private_ok(url))
        if sse:
            request = client.build_request("GET", url, headers={"Accept": "text/event-stream"})
        else:
            request = client.build_request("POST", url, json=_PROBE_BODY, headers={
                "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": _PROBE_PROTOCOL_VERSION,
            })
        response = await client.send(request, stream=True)
        try:
            status, values = response.status_code, response.headers.get_list("www-authenticate")
        finally:
            await response.aclose()
    except Exception as e:
        logger.debug("No 401 challenge from the MCP server (%s)", type(e).__name__)
        return None, []
    if status != 401:
        return None, []
    params = bearer_challenge(values)
    metadata = params.get("resource_metadata")
    if metadata is not None and (len(metadata) > _MAX_METADATA_URL_CHARS or not is_web_url(metadata)):
        metadata = None
    return metadata, challenge_scopes(params)


def _resource_matches(protected_resource: dict[str, Any], server_url: str) -> bool:
    """RFC 9728 §3.3: metadata is for the server only if its `resource` covers the server's URL.
    Without the check, a server could name another one's metadata and get a token minted for it."""
    declared = protected_resource.get("resource")
    if not isinstance(declared, str) or not is_web_url(declared):
        return False
    server = _without_userinfo(server_url)
    return server is not None and check_resource_allowed(resource_url_from_server_url(server), declared)


def canonical_resource_uri(server_url: str) -> Optional[str]:
    """RFC 8707's identifier for an MCP server: scheme and host lowercased, no userinfo,
    query or fragment, and no trailing slash."""
    if not is_web_url(server_url):
        return None
    parsed = urlparse(server_url.strip())
    netloc = parsed.netloc.rsplit("@", 1)[-1].lower()
    return f"{parsed.scheme.lower()}://{netloc}{parsed.path.rstrip('/')}"


def _resource_identity(protected_resource: dict[str, Any], server_base_url: str) -> tuple[Optional[str], list[str]]:
    """The `resource` to send and the scopes to request, from RFC 9728 metadata. A `resource`
    that isn't a web address falls back to the server's own URL."""
    declared = protected_resource.get("resource")
    resource = canonical_resource_uri(declared) if isinstance(declared, str) else None
    scopes = protected_resource.get("scopes_supported")
    return (
        resource or canonical_resource_uri(server_base_url),
        [s for s in scopes if isinstance(s, str) and s] if isinstance(scopes, list) else [],
    )


async def _discover_oauth_metadata_inner(
    server_base_url: str, policy: MCPUrlPolicy, *, sse: bool = False,
) -> Optional[DiscoveredOAuthMetadata]:
    async with _discovery_client(DISCOVERY_REQUEST_TIMEOUT_SECONDS, policy) as client:
        as_issuer_candidates = [server_base_url]
        resource: Optional[str] = None
        resource_scopes: list[str] = []
        hinted_url, challenge_scopes = await _challenge_hints(client, server_base_url, policy, sse=sse)
        protected_resource: Optional[dict[str, Any]] = None
        if hinted_url:
            hinted = await _fetch_well_known_json(client, [hinted_url], policy)
            if isinstance(hinted, dict) and _resource_matches(hinted, server_base_url):
                protected_resource = hinted
            elif hinted is not None:
                logger.warning("Ignoring the protected-resource metadata a 401 named: it is for another resource")
        if protected_resource is None:
            protected_resource = await _fetch_well_known_json(
                client, _well_known_candidate_urls(server_base_url, WELL_KNOWN_PROTECTED_RESOURCE_PATH), policy,
            )
            if isinstance(protected_resource, dict) and not _resource_matches(protected_resource, server_base_url):
                # Kept, as before: servers whose well-known `resource` is slightly off still sign in.
                logger.info("The server's protected-resource metadata names a different resource")
        if isinstance(protected_resource, dict):
            resource, resource_scopes = _resource_identity(protected_resource, server_base_url)
            servers = protected_resource.get("authorization_servers")
            if isinstance(servers, list):
                candidates = [s for s in servers if isinstance(s, str) and s]
                if candidates:
                    as_issuer_candidates = candidates[:MAX_AS_ISSUER_CANDIDATES]

        for issuer in as_issuer_candidates:
            as_metadata = await _fetch_well_known_json(client, _authorization_server_metadata_urls(issuer), policy)
            if isinstance(as_metadata, dict) and not _endpoints_are_web_urls(as_metadata):
                logger.warning("Ignoring an authorization server's OAuth metadata: an endpoint is not an http(s) URL")
                continue
            if isinstance(as_metadata, dict) and (
                as_metadata.get("authorization_endpoint") or as_metadata.get("token_endpoint")
            ):
                return DiscoveredOAuthMetadata(
                    authorization_endpoint=as_metadata.get("authorization_endpoint"),
                    token_endpoint=as_metadata.get("token_endpoint"),
                    registration_endpoint=as_metadata.get("registration_endpoint"),
                    scopes_supported=as_metadata.get("scopes_supported") or [],
                    issuer=as_metadata.get("issuer") or issuer,
                    resource=resource,
                    resource_scopes=resource_scopes,
                    challenge_scopes=challenge_scopes,
                    token_endpoint_auth_methods_supported=_string_list_or_none(
                        as_metadata.get("token_endpoint_auth_methods_supported"),
                    ),
                    code_challenge_methods_supported=_string_list_or_none(
                        as_metadata.get("code_challenge_methods_supported"),
                    ),
                    client_id_metadata_document_supported=as_metadata.get("client_id_metadata_document_supported") is True,
                )
    return None


def _string_list_or_none(value: object) -> Optional[list[str]]:
    return [item for item in value if isinstance(item, str) and item] if isinstance(value, list) else None


def preferred_token_auth_method(supported: Optional[list[str]]) -> str:
    """`client_secret_post`, as PipesHub always used, unless the authorization server lists
    methods without it: then `client_secret_basic`, or `none` for one that only has public
    clients. RFC 8414 makes an unlisted method mean `client_secret_basic`; that isn't followed,
    because servers that list nothing take a posted secret in practice."""
    if not supported or TokenEndpointAuthMethod.CLIENT_SECRET_POST.value in supported:
        return TokenEndpointAuthMethod.CLIENT_SECRET_POST.value
    for method in (TokenEndpointAuthMethod.CLIENT_SECRET_BASIC.value, TokenEndpointAuthMethod.NONE.value):
        if method in supported:
            return method
    return TokenEndpointAuthMethod.CLIENT_SECRET_POST.value


async def discover_oauth_metadata(
    server_base_url: str, *, policy: MCPUrlPolicy, sse: bool = False,
) -> Optional[DiscoveredOAuthMetadata]:
    """RFC 9728 protected-resource discovery chained into RFC 8414 (OIDC-fallback)
    authorization-server metadata for `server_base_url`.

    Best-effort: returns `None` (not an error) on any failure — network error, timeout, or a
    target blocked by the SSRF guard — never raises. Callers fall back to the instance's
    configured `authorizationUrl`/`tokenUrl` in that case.
    """
    try:
        return await asyncio.wait_for(
            _discover_oauth_metadata_inner(server_base_url, policy, sse=sse),
            timeout=DISCOVERY_TOTAL_TIMEOUT_SECONDS,
        )
    except Exception as e:
        logger.debug("OAuth metadata discovery failed (%s)", type(e).__name__)
        return None


async def register_dynamic_client(
    registration_endpoint: str,
    redirect_uri: str,
    authorization_url: str,
    token_url: str,
    client_name: str = "PipesHub",
    *,
    policy: MCPUrlPolicy,
    auth_methods_supported: Optional[list[str]] = None,
) -> DCRClient:
    """RFC 7591 dynamic client registration against `registration_endpoint`.

    `authorization_url`/`token_url` come from the caller's metadata discovery (or the
    instance's static config) — they are stored on the resulting `DCRClient` so background
    token refresh never needs to re-discover metadata. `auth_methods_supported` is the
    authorization server's `token_endpoint_auth_methods_supported`; the provider may register
    the client for another method, which is kept if PipesHub can use it.
    """
    await assert_discovery_target_allowed(registration_endpoint, allow_private=policy.private_ok(registration_endpoint))

    requested_method = preferred_token_auth_method(auth_methods_supported)
    payload = {
        "client_name": client_name,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": requested_method,
        # A server-side web application, as the MCP authorization spec asks clients to declare.
        "application_type": "web",
    }
    try:
        async with _discovery_client(DCR_REQUEST_TIMEOUT_SECONDS, policy) as client:
            resp = await client.post(registration_endpoint, json=payload)
            if resp.status_code >= 400:
                raise DCRError(f"DCR registration failed with status {resp.status_code}: {resp.text}")
            data = resp.json()
    except DCRError:
        raise
    except Exception as e:
        raise DCRError(f"DCR registration request failed: {e}") from e

    client_id = data.get("client_id") if isinstance(data, dict) else None
    if not client_id:
        raise DCRError("DCR response missing client_id")
    registered_method = data.get("token_endpoint_auth_method") or requested_method
    if not isinstance(registered_method, str) or registered_method not in {m.value for m in TokenEndpointAuthMethod}:
        raise DCRError(
            f"The provider registered the client for token endpoint authentication {registered_method!r}, "
            "which PipesHub doesn't support",
        )
    expires_at = data.get("client_secret_expires_at")

    return DCRClient(
        client_id=client_id,
        client_secret=data.get("client_secret"),
        registration_access_token=data.get("registration_access_token"),
        registration_client_uri=data.get("registration_client_uri"),
        authorization_url=authorization_url,
        token_url=token_url,
        registered_at=int(datetime.now(timezone.utc).timestamp() * 1000),
        redirect_uri=redirect_uri,
        client_secret_expires_at=expires_at if isinstance(expires_at, int) else None,
        token_endpoint_auth_method=registered_method,
    )


RESERVED_AUTHORIZE_PARAMS = frozenset(
    {"client_id", "redirect_uri", "response_type", "state", "scope", "code_challenge", "code_challenge_method", "resource"}
)


def build_authorization_url(
    authorization_url: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    scopes: Optional[list[str]] = None,
    code_challenge: Optional[str] = None,
    extra_params: Optional[dict[str, str]] = None,
    resource: Optional[str] = None,
) -> str:
    """Build the full `GET {authorizationUrl}?...` redirect target, with PKCE if provided.

    `extra_params` carries provider-specific additions from the catalog template; the
    protocol parameters below are dropped from it so catalog metadata can never redirect
    the flow or weaken PKCE. A query already on the endpoint is kept (RFC 6749 §3.1), minus
    any protocol parameter, so none appears twice.

    Raises:
        InvalidOAuthEndpointError: `authorization_url` is not an http(s) URL.
    """
    if not is_web_url(authorization_url):
        raise InvalidOAuthEndpointError("The authorization endpoint is not an http(s) URL.")
    endpoint = urlsplit(authorization_url.strip())
    params = {k: v for k, v in parse_qsl(endpoint.query, keep_blank_values=True) if k not in RESERVED_AUTHORIZE_PARAMS}
    params.update({k: v for k, v in (extra_params or {}).items() if k not in RESERVED_AUTHORIZE_PARAMS})
    params.update({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
    })
    if scopes:
        params["scope"] = " ".join(scopes)
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    if resource:
        params["resource"] = resource
    # RFC 6749 §3.1: the endpoint carries no fragment.
    return urlunsplit(endpoint._replace(query=urlencode(params), fragment=""))
