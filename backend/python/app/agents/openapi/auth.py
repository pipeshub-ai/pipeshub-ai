"""Puts a toolset's stored credentials on each request, the way its chosen auth scheme says.

One `AuthApplier` per scheme type. OAuth2 tokens come from `OAuthTokenSource`, which gets
a new token when the current one is missing, expiring, or was just rejected.
"""
from __future__ import annotations

import asyncio
import base64
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Optional
from urllib.parse import urljoin

from app.agents.mcp.oauth_client import (
    MCPOAuthError,
    refresh_access_token,
    request_client_credentials_token,
)
from app.agents.openapi.models import ApiToolsetCredentials, AuthScheme, AuthType

if TYPE_CHECKING:
    import httpx

    from app.agents.mcp.models import OAuthTokens

# A token this close to expiry is replaced before use, so it can't expire in flight.
EXPIRY_MARGIN_SECONDS = 30

TokensSaver = Callable[[ApiToolsetCredentials], Awaitable[None]]
TokensLoader = Callable[[], Awaitable[Optional[ApiToolsetCredentials]]]

# One lock per stored credential, shared by every request in this process, so two chats on one
# credential don't both spend its refresh token (many providers rotate it, and revoke both on reuse).
# shortcut: per process; separate workers can still race, a store-level claim would close that.
_REFRESH_LOCKS: dict[str, asyncio.Lock] = {}


class ApiToolsetAuthError(Exception):
    """Credentials are missing, expired or rejected. The message is safe to show."""


class AuthApplier(ABC):
    @abstractmethod
    async def apply(self, request: httpx.Request, *, force_refresh: bool = False) -> None:
        """Add credentials to `request`. `force_refresh` asks for fresh ones after a 401."""

    @property
    def can_refresh(self) -> bool:
        """Whether a 401 is worth one retry with `force_refresh`."""
        return False


class NoAuth(AuthApplier):
    async def apply(self, request: httpx.Request, *, force_refresh: bool = False) -> None:
        return None


class ApiKeyAuth(AuthApplier):
    def __init__(self, location: str, param_name: str, api_key: str) -> None:
        self._location = location
        self._param_name = param_name
        self._api_key = api_key

    async def apply(self, request: httpx.Request, *, force_refresh: bool = False) -> None:
        if self._location == "query":
            request.url = request.url.copy_add_param(self._param_name, self._api_key)
        elif self._location == "cookie":
            cookie = f"{self._param_name}={self._api_key}"
            existing = request.headers.get("Cookie")
            request.headers["Cookie"] = f"{existing}; {cookie}" if existing else cookie
        else:
            request.headers[self._param_name] = self._api_key


class BearerAuth(AuthApplier):
    def __init__(self, token: str) -> None:
        self._token = token

    async def apply(self, request: httpx.Request, *, force_refresh: bool = False) -> None:
        request.headers["Authorization"] = f"Bearer {self._token}"


class BasicAuth(AuthApplier):
    def __init__(self, username: str, password: str) -> None:
        self._header = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()

    async def apply(self, request: httpx.Request, *, force_refresh: bool = False) -> None:
        request.headers["Authorization"] = self._header


class OAuthTokenSource:
    """The current OAuth2 access token for one credential, obtained or refreshed on demand.

    New tokens are handed to `save` so the next request starts from them.
    """

    def __init__(
        self,
        scheme: AuthScheme,
        credentials: ApiToolsetCredentials,
        *,
        token_url: str,
        allow_private: bool,
        scopes: Optional[list[str]] = None,
        save: Optional[TokensSaver] = None,
        load: Optional[TokensLoader] = None,
        lock_key: Optional[str] = None,
    ) -> None:
        self._scheme = scheme
        self._credentials = credentials
        self._token_url = token_url
        self._allow_private = allow_private
        self._scopes = scopes
        self._save = save
        self._load = load
        self._lock = _REFRESH_LOCKS.setdefault(lock_key, asyncio.Lock()) if lock_key else asyncio.Lock()

    async def access_token(self, *, force_refresh: bool = False) -> str:
        async with self._lock:
            tokens = self._credentials.oauth
            if tokens is not None and not force_refresh and not _expires_soon(tokens):
                return tokens.access_token
            newer = await self._newer_stored_tokens(tokens)
            if newer is not None:
                # Another request refreshed while this one waited for the lock: take its tokens, and
                # its access token too unless that is about to expire or is the one just refused.
                self._credentials = self._credentials.model_copy(update={"oauth": newer})
                rejected = force_refresh and tokens is not None and newer.access_token == tokens.access_token
                if not rejected and not _expires_soon(newer):
                    return newer.access_token
                tokens = newer
            try:
                fresh = await self._obtain(tokens)
            except MCPOAuthError as e:
                raise ApiToolsetAuthError(
                    "Couldn't get an access token from the API's OAuth server. Check the API action's "
                    "OAuth settings, or sign in again."
                ) from e
            self._credentials = self._credentials.model_copy(update={"oauth": fresh})
            if self._save is not None:
                await self._save(self._credentials)
            return fresh.access_token

    async def _newer_stored_tokens(self, held: Optional[OAuthTokens]) -> Optional[OAuthTokens]:
        if self._load is None:
            return None
        stored = await self._load()
        tokens = stored.oauth if stored is not None else None
        # The refresh token too: a server may rotate it and hand back the same access token.
        if tokens is None or (held is not None and (tokens.access_token, tokens.refresh_token)
                              == (held.access_token, held.refresh_token)):
            return None
        return tokens

    async def _obtain(self, tokens: Optional[OAuthTokens]) -> OAuthTokens:
        creds = self._credentials
        if self._scheme.type is AuthType.OAUTH2_CLIENT_CREDENTIALS:
            if not creds.client_id:
                raise ApiToolsetAuthError("No OAuth client ID is stored for this API action.")
            return await request_client_credentials_token(
                self._token_url, creds.client_id, creds.client_secret,
                allow_private=self._allow_private, scopes=self._scopes,
                auth_method=tokens.token_endpoint_auth_method if tokens else None,
            )
        if tokens is None or not tokens.refresh_token:
            raise ApiToolsetAuthError("The sign-in for this API action has expired. Sign in again.")
        return await refresh_access_token(
            tokens.token_url or self._token_url, tokens.client_id or creds.client_id or "", creds.client_secret,
            tokens.refresh_token, allow_private=self._allow_private, auth_method=tokens.token_endpoint_auth_method,
        )


class OAuth2Auth(AuthApplier):
    def __init__(self, tokens: OAuthTokenSource) -> None:
        self._tokens = tokens

    async def apply(self, request: httpx.Request, *, force_refresh: bool = False) -> None:
        token = await self._tokens.access_token(force_refresh=force_refresh)
        request.headers["Authorization"] = f"Bearer {token}"

    @property
    def can_refresh(self) -> bool:
        return True


def build_auth(
    scheme: AuthScheme,
    credentials: ApiToolsetCredentials,
    *,
    base_url: str,
    allow_private: bool,
    scopes: Optional[list[str]] = None,
    save_tokens: Optional[TokensSaver] = None,
    load_tokens: Optional[TokensLoader] = None,
    lock_key: Optional[str] = None,
) -> AuthApplier:
    """The applier for `scheme`, holding `credentials`. Raises if a needed credential is missing."""
    if scheme.type is AuthType.NONE:
        return NoAuth()
    if scheme.type is AuthType.API_KEY:
        return ApiKeyAuth(scheme.location or "header", scheme.param_name or "X-API-Key", _need(credentials.api_key, "API key"))
    if scheme.type is AuthType.HTTP_BEARER:
        return BearerAuth(_need(credentials.token, "bearer token"))
    if scheme.type is AuthType.HTTP_BASIC:
        return BasicAuth(_need(credentials.username, "username"), _need(credentials.password, "password"))
    token_url = urljoin(base_url.rstrip("/") + "/", scheme.token_url or "")
    return OAuth2Auth(OAuthTokenSource(
        scheme, credentials, token_url=token_url, allow_private=allow_private, scopes=scopes, save=save_tokens,
        load=load_tokens, lock_key=lock_key,
    ))


def _need(value: Optional[str], what: str) -> str:
    if not value:
        raise ApiToolsetAuthError(f"No {what} is stored for this API action.")
    return value


def _expires_soon(tokens: OAuthTokens) -> bool:
    expires_at = tokens.expires_at_epoch
    return expires_at is not None and expires_at - EXPIRY_MARGIN_SECONDS <= time.time()
