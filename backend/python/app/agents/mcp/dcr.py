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
from urllib.parse import urlencode, urlparse

import httpx

from app.agents.mcp.models import DCRClient, DiscoveredOAuthMetadata
from app.utils.url_fetcher import FetchError as UrlFetchError
from app.utils.url_fetcher import validate_public_http_url

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


async def assert_discovery_target_allowed(url: str) -> None:
    """Reject scheme/hosts that would let an admin-supplied MCP/discovery URL reach
    cluster-internal services — delegates to the same SSRF policy `app.utils.url_fetcher`
    already enforces for connector-triggered fetches, so there is exactly one blocked-host
    list to maintain.

    `httpx.AsyncClient` never follows redirects unless `follow_redirects=True` is passed —
    it never is, anywhere in this module — so a single resolve-before-connect check here is
    sufficient; there is no redirect hop that could land on a different, unchecked host.
    """
    try:
        await asyncio.to_thread(validate_public_http_url, url)
    except UrlFetchError as e:
        raise DiscoveryBlockedError(str(e)) from e


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


async def _fetch_well_known_json(client: httpx.AsyncClient, candidate_urls: list[str]) -> Optional[dict[str, Any]]:
    for url in candidate_urls:
        try:
            await assert_discovery_target_allowed(url)
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.debug("No metadata at well-known endpoint (%s)", type(e).__name__)
    return None


async def _discover_oauth_metadata_inner(server_base_url: str) -> Optional[DiscoveredOAuthMetadata]:
    async with httpx.AsyncClient(timeout=DISCOVERY_REQUEST_TIMEOUT_SECONDS) as client:
        as_issuer_candidates = [server_base_url]
        protected_resource = await _fetch_well_known_json(
            client, _well_known_candidate_urls(server_base_url, WELL_KNOWN_PROTECTED_RESOURCE_PATH)
        )
        if isinstance(protected_resource, dict):
            servers = protected_resource.get("authorization_servers")
            if isinstance(servers, list):
                candidates = [s for s in servers if isinstance(s, str) and s]
                if candidates:
                    as_issuer_candidates = candidates[:MAX_AS_ISSUER_CANDIDATES]

        for issuer in as_issuer_candidates:
            as_metadata = await _fetch_well_known_json(
                client, _well_known_candidate_urls(issuer, WELL_KNOWN_OAUTH_METADATA_PATH)
            )
            if as_metadata is None:
                as_metadata = await _fetch_well_known_json(
                    client, _well_known_candidate_urls(issuer, WELL_KNOWN_OIDC_PATH)
                )
            if isinstance(as_metadata, dict) and (
                as_metadata.get("authorization_endpoint") or as_metadata.get("token_endpoint")
            ):
                return DiscoveredOAuthMetadata(
                    authorization_endpoint=as_metadata.get("authorization_endpoint"),
                    token_endpoint=as_metadata.get("token_endpoint"),
                    registration_endpoint=as_metadata.get("registration_endpoint"),
                    scopes_supported=as_metadata.get("scopes_supported") or [],
                    issuer=as_metadata.get("issuer") or issuer,
                )
    return None


async def discover_oauth_metadata(server_base_url: str) -> Optional[DiscoveredOAuthMetadata]:
    """RFC 9728 protected-resource discovery chained into RFC 8414 (OIDC-fallback)
    authorization-server metadata for `server_base_url`.

    Best-effort: returns `None` (not an error) on any failure — network error, timeout, or a
    target blocked by the SSRF guard — never raises. Callers fall back to the instance's
    configured `authorizationUrl`/`tokenUrl` in that case.
    """
    try:
        return await asyncio.wait_for(
            _discover_oauth_metadata_inner(server_base_url), timeout=DISCOVERY_TOTAL_TIMEOUT_SECONDS
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
) -> DCRClient:
    """RFC 7591 dynamic client registration against `registration_endpoint`.

    `authorization_url`/`token_url` come from the caller's metadata discovery (or the
    instance's static config) — they are stored on the resulting `DCRClient` so background
    token refresh never needs to re-discover metadata.
    """
    await assert_discovery_target_allowed(registration_endpoint)

    payload = {
        "client_name": client_name,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post",
    }
    try:
        async with httpx.AsyncClient(timeout=DCR_REQUEST_TIMEOUT_SECONDS) as client:
            resp = await client.post(registration_endpoint, json=payload)
            if resp.status_code >= 400:
                raise DCRError(f"DCR registration failed with status {resp.status_code}: {resp.text}")
            data = resp.json()
    except DCRError:
        raise
    except Exception as e:
        raise DCRError(f"DCR registration request failed: {e}") from e

    client_id = data.get("client_id")
    if not client_id:
        raise DCRError("DCR response missing client_id")

    return DCRClient(
        client_id=client_id,
        client_secret=data.get("client_secret"),
        registration_access_token=data.get("registration_access_token"),
        registration_client_uri=data.get("registration_client_uri"),
        authorization_url=authorization_url,
        token_url=token_url,
        registered_at=int(datetime.now(timezone.utc).timestamp() * 1000),
    )


def build_authorization_url(
    authorization_url: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    scopes: Optional[list[str]] = None,
    code_challenge: Optional[str] = None,
) -> str:
    """Build the full `GET {authorizationUrl}?...` redirect target, with PKCE if provided."""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
    }
    if scopes:
        params["scope"] = " ".join(scopes)
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    return f"{authorization_url}?{urlencode(params)}"
