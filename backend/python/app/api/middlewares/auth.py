import os
from collections.abc import Callable, Coroutine, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Optional

import jwt
from dependency_injector.wiring import inject
from fastapi import HTTPException, Request, status
from jwt import PyJWTError

from app.api.middlewares.caller_role import (
    CallerRole,
    CallerRoleCache,
    CallerRoleStatus,
    fetch_caller_role,
    normalize_auth_role,
)
from app.api.middlewares.token_policy import (
    ACCEPTED_SERVICE_SCOPES,
    AuthTokenType,
    ScopeLike,
    has_service_scope,
    is_service_token,
    scope_value,
    token_scopes,
)
from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.core.org_context import OrgContext, set_request_context
from app.utils.logging_fields import log_once
from app.utils.request_context import get_context as get_request_context
from app.utils.request_context import set_context_org

# Marks the dependency that states which token classes a route accepts; the route
# inventory test requires one on every route so no route admits service tokens by omission.
AUTH_POLICY_ATTR = "__auth_policy__"

# Set only by the regular path from a verified OAuth access token; a service token that
# arrives carrying them must not be able to pose as an OAuth client.
_OAUTH_DERIVED_CLAIMS = ("isOAuth", "oauthScopes", "oauthClientId", "knowledgeLimit")

#: What the user let a connected app see (``apps`` / ``recordGroups`` /
#: ``records``), signed into the app's OAuth or personal access token by Node.
KNOWLEDGE_LIMIT_CLAIM = "knowledgeLimit"
_KNOWLEDGE_LIMIT_KEYS = ("apps", "recordGroups", "records")

DEFAULT_SESSION_CHECK_TTL_SECONDS = 30.0


def session_check_ttl_seconds() -> float:
    """How long Node's answer about a session token is reused (``SESSION_CHECK_CACHE_SECONDS``).

    This bounds both how late signing out, a password change or a lockout takes effect
    here and how often each session costs Node a lookup.
    """
    raw = os.environ.get("SESSION_CHECK_CACHE_SECONDS", "").strip()
    try:
        ttl = float(raw) if raw else DEFAULT_SESSION_CHECK_TTL_SECONDS
    except ValueError:
        return DEFAULT_SESSION_CHECK_TTL_SECONDS
    return ttl if 0 <= ttl < float("inf") else DEFAULT_SESSION_CHECK_TTL_SECONDS


_session_checks = CallerRoleCache(ttl_seconds=session_check_ttl_seconds(), max_entries=10_000)


class AuthRejected(HTTPException):
    """An auth refusal: the client gets the same status, detail and headers as from an ``HTTPException``;
    ``reason`` is never sent, it goes on the request's ``http.request.end`` line."""

    def __init__(self, status_code: int, detail: str, *, reason: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.reason = reason


_BEARER = {"WWW-Authenticate": "Bearer"}


def _invalid_credentials(reason: str) -> AuthRejected:
    return AuthRejected(status.HTTP_401_UNAUTHORIZED, "Could not validate credentials", reason=reason, headers=_BEARER)


async def get_config_service(request: Request) -> ConfigurationService:
    """Get configuration service from request container."""
    container = request.app.container
    config_service = container.config_service()
    return config_service


def extract_bearer_token(authorization_header: Optional[str]) -> str:
    """
    Extract JWT token from Authorization header.

    Args:
        authorization_header: The Authorization header value (e.g., "Bearer <token>")

    Returns:
        str: The extracted JWT token

    Raises:
        HTTPException: If Authorization header is missing or malformed
    """
    if not authorization_header:
        raise AuthRejected(
            status.HTTP_401_UNAUTHORIZED, "Authorization header is missing", reason="missing_header", headers=_BEARER,
        )

    if not authorization_header.startswith("Bearer "):
        raise AuthRejected(
            status.HTTP_401_UNAUTHORIZED, "Authorization header must start with 'Bearer '", reason="bad_scheme",
            headers=_BEARER,
        )

    # Extract token after "Bearer "
    token = authorization_header[7:].strip()

    if not token:
        raise AuthRejected(
            status.HTTP_401_UNAUTHORIZED, "Token is missing in Authorization header", reason="missing_token",
            headers=_BEARER,
        )

    return token


def _normalize_regular_payload(payload: dict[str, Any], token: str) -> dict[str, Any]:
    """Add metadata and normalize OAuth tokens for scope enforcement."""
    payload["user"] = token
    payload["token_type"] = AuthTokenType.REGULAR.value
    limit = payload.pop(KNOWLEDGE_LIMIT_CLAIM, None)
    if payload.get("tokenType") == "oauth":
        # "".split(" ") is [""]. A token with no scope names is not a grant.
        scopes = str(payload.get("scope") or "").split()
        if not scopes:
            raise _invalid_credentials("no_scopes")
        payload["isOAuth"] = True
        payload["oauthScopes"] = scopes
        payload["oauthClientId"] = payload.get("client_id")
        if isinstance(limit, dict):
            payload[KNOWLEDGE_LIMIT_CLAIM] = {
                key: [i for i in limit.get(key) or [] if isinstance(i, str) and i]
                if isinstance(limit.get(key), list) else []
                for key in _KNOWLEDGE_LIMIT_KEYS
            }
        # A client_credentials token has no caller of its own: its userId is
        # the client id. The `createdBy` claim carries the identity it acts
        # as, which Node resolves when minting the token — the application's
        # chosen service account where one has been set, and its creator
        # otherwise. The claim keeps its original name for tokens already in
        # circulation; it means "the identity", not "who made the app".
        #
        # Pointing an application at a different identity revokes the tokens
        # it has already issued, so a claim reaching here is never stale.
        if payload.get("userId") == payload.get("client_id") and payload.get("createdBy"):
            payload["userId"] = payload["createdBy"]

    payload["role"] = normalize_auth_role(payload.get("role"))
    return payload


def connection_limit(user: Mapping[str, Any] | None) -> dict[str, list[str]] | None:
    """What the connected app making this request may see, or None when it may
    see everything its user can (a session, or an app given no limit)."""
    limit = (user or {}).get(KNOWLEDGE_LIMIT_CLAIM)
    return limit if isinstance(limit, dict) else None


def _normalize_scoped_payload(payload: dict[str, Any], token: str) -> dict[str, Any]:
    for claim in _OAUTH_DERIVED_CLAIMS:
        payload.pop(claim, None)
    payload["user"] = token
    payload["token_type"] = AuthTokenType.SCOPED.value
    # A service token never carries org-admin rights, whatever its claims say.
    payload["role"] = "member"
    return payload


# Authentication logic
@inject
async def isJwtTokenValid(request: Request) -> dict:
    """
    Authenticate the bearer token and classify it.

    Regular tokens (``jwtSecret``) are user sessions and OAuth/PAT access tokens.
    Service tokens (``scopedJwtSecret``) are accepted only when they carry one of
    ``ACCEPTED_SERVICE_SCOPES``; see ``token_policy`` for why the signature alone
    is not enough.

    Args:
        request: FastAPI request object

    Returns:
        dict: Decoded JWT payload with token metadata:
            - All original payload fields
            - "user": The original token string
            - "token_type": Either "regular" or "scoped"

    Raises:
        HTTPException: 401 if the token is invalid or its class is not accepted,
            500 if the secrets are not configured
    """
    logger = request.app.container.logger()
    try:
        config_service = await get_config_service(request)
        secret_keys = await config_service.get_config(
            config_node_constants.SECRET_KEYS.value, use_cache=True,
        )

        if not secret_keys:
            raise ValueError("Secret keys configuration not found")

        regular_jwt_secret = secret_keys.get("jwtSecret")
        scoped_jwt_secret = secret_keys.get("scopedJwtSecret")
        algorithm = os.environ.get("JWT_ALGORITHM", "HS256")
        if not regular_jwt_secret:
            raise ValueError("Missing jwtSecret in configuration")

        if not scoped_jwt_secret and log_once("auth.scoped_jwt_secret_missing", 3600):
            logger.warning("scopedJwtSecret not found in configuration - service tokens are disabled")

        authorization_header = request.headers.get("Authorization")
        token = extract_bearer_token(authorization_header)

        try:
            # The audience is checked where it means something, at /mcp in the Node API; PyJWT
            # would otherwise refuse every token that names one.
            payload = jwt.decode(
                token, regular_jwt_secret, algorithms=[algorithm], options={"verify_aud": False}
            )
            logger.debug("Validated token using regular JWT secret")
            return _normalize_regular_payload(payload, token)
        except jwt.ExpiredSignatureError:
            regular_reason = "expired"
        except PyJWTError:
            regular_reason = "invalid_token"

        if not scoped_jwt_secret:
            raise _invalid_credentials(regular_reason)

        try:
            payload = jwt.decode(token, scoped_jwt_secret, algorithms=[algorithm])
        except jwt.ExpiredSignatureError:
            raise _invalid_credentials("expired") from None
        except PyJWTError:
            raise _invalid_credentials(regular_reason) from None

        scopes = token_scopes(payload)
        if not scopes & ACCEPTED_SERVICE_SCOPES:
            logger.warning(
                "Rejected service token: scopes %s are not accepted by this service",
                sorted(scopes),
            )
            raise _invalid_credentials("scope_not_accepted")

        logger.debug("Validated service token with scopes %s", sorted(scopes))
        return _normalize_scoped_payload(payload, token)

    except HTTPException:
        raise
    except ValueError as e:
        # Configuration errors are 500, not 401; the request's line logs this with the ValueError's stack.
        raise AuthRejected(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Authentication configuration error", reason="auth_config",
        ) from e
    except Exception as e:
        logger.error(f"Unexpected error during authentication: {e}", exc_info=True)
        raise _invalid_credentials("auth_error") from e


# Dependency for injecting authentication
async def authMiddleware(request: Request) -> Request:
    """
    FastAPI middleware dependency for authenticating requests.

    Validates the bearer token and attaches the authenticated identity to
    request.state.user. Accepts regular tokens and service tokens with an
    accepted scope; routes then decide which of those they admit.

    Args:
        request: FastAPI request object
    Returns:
        Request: The request object with authenticated user info in request.state.user
    Raises:
        HTTPException: If authentication fails
    """
    logger = request.app.container.logger()
    try:
        payload = await isJwtTokenValid(request)
        payload["role"] = await resolve_request_role(request, payload)

        # Attach the authenticated user information to the request state
        request.state.user = payload
        _bind_org_context(request, payload)

        logger.debug(f"Authentication successful. Token type: {payload.get('token_type', 'unknown')}")

    except HTTPException as e:
        # Re-raise HTTP exceptions as-is to preserve status codes and details
        raise e
    except Exception as e:
        logger.error(f"Unexpected error in authentication middleware: {e}", exc_info=True)
        raise AuthRejected(status.HTTP_401_UNAUTHORIZED, "Not authenticated", reason="auth_error", headers=_BEARER) from e

    return request


# Pre-org routes: the only paths that accept a service token with no ``orgId``. Any other
# route receiving one answers 401 (same as ``service_token_org``). Keep in sync with docs/org-context.md.
PRE_ORG_SERVICE_ROUTES = frozenset({"/api/v1/parse/providers"})


def _bind_org_context(request: Request, payload: dict[str, Any]) -> None:
    """Bind the verified token's org for the rest of the request (never a client field)."""
    scoped = payload.get("token_type") == AuthTokenType.SCOPED.value
    org_id = payload.get("orgId")
    if not org_id:
        if scoped and getattr(getattr(request, "url", None), "path", None) not in PRE_ORG_SERVICE_ROUTES:
            raise AuthRejected(status.HTTP_401_UNAUTHORIZED, "Token missing orgId", reason="missing_org")
        return  # stays unbound: current() fails closed
    rc = get_request_context()
    set_request_context(OrgContext(
        org_id=org_id,
        user_id=payload.get("userId"),
        kind="service" if scoped else "user",
        source="scoped_jwt" if scoped else "jwt",
        request_id=rc.root_id if rc else "-",
    ))
    # Context middleware runs before auth, so this is the first point the org is known.
    set_context_org(org_id)


def is_request_admin(request: Request) -> bool:
    """Org-admin from JWT role on request.state.user."""
    user = getattr(request.state, "user", None) or {}
    getter = getattr(user, "get", None)
    role = getter("role") if callable(getter) else None
    return normalize_auth_role(role) == "admin"


async def resolve_request_role(request: Request, payload: dict[str, Any]) -> str:
    """Org role for the authenticated caller, once Node has confirmed the token is live.

    A signature proves only that a token was issued. Whether a session has since
    ended (signed out, password changed, account locked or deleted) or an OAuth/PAT
    token been revoked is known to Node alone, so both are checked there, and a token
    Node refuses, or could not confirm, is refused here. Service tokens are not.

    A session's role is its claim, which is current because Node ends every session
    when a role changes. OAuth/PAT tokens carry none, so theirs is Node's.
    """
    if is_service_token(payload):
        return normalize_auth_role(payload.get("role"))

    config_service = await get_config_service(request)
    if payload.get("isOAuth"):
        caller = await fetch_caller_role(request, config_service)
        _refuse_unconfirmed(caller, "Token is no longer valid", "token_revoked")
        return caller.role

    caller = await _session_checks.get(
        payload["user"], lambda: fetch_caller_role(request, config_service)
    )
    _refuse_unconfirmed(caller, "Your session has ended. Please sign in again.", "session_ended")
    return normalize_auth_role(payload.get("role"))


def _refuse_unconfirmed(caller: CallerRole, rejected_detail: str, rejected_reason: str) -> None:
    if caller.status is CallerRoleStatus.REJECTED:
        raise AuthRejected(status.HTTP_401_UNAUTHORIZED, rejected_detail, reason=rejected_reason, headers=_BEARER)
    if caller.status is CallerRoleStatus.UNKNOWN:
        # Without Node's answer a revoked token looks exactly like a valid one.
        raise AuthRejected(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "We couldn't confirm your sign-in just now. Please try again in a few seconds.",
            reason="unconfirmed",
            headers={"Retry-After": "5"},
        )


@dataclass(frozen=True)
class AuthPolicy:
    """What a route's auth dependency admits; attached to it under ``AUTH_POLICY_ATTR``."""

    kind: Literal["scopes", "service", "deny_service"]
    service_scopes: frozenset[str] = frozenset()
    oauth_scopes: frozenset[str] = frozenset()


def _tag_auth_policy(
    dependency: Callable[..., Any],
    kind: Literal["scopes", "service", "deny_service"],
    service_scopes: Iterable[ScopeLike] = (),
    oauth_scopes: Iterable[ScopeLike] = (),
) -> Callable[..., Any]:
    policy = AuthPolicy(
        kind,
        frozenset(scope_value(scope) for scope in service_scopes),
        frozenset(scope_value(scope) for scope in oauth_scopes),
    )
    setattr(dependency, AUTH_POLICY_ATTR, policy)
    return dependency


def _authenticated_user(request: Request) -> Mapping[str, Any]:
    user = getattr(request.state, "user", None)
    if user is None:
        raise AuthRejected(status.HTTP_401_UNAUTHORIZED, "Authentication required", reason="unauthenticated",
                           headers=_BEARER)
    return user


def require_scopes(
    *required_scopes: str,
    service_scopes: Iterable[ScopeLike] = (),
) -> Callable[..., Coroutine[Any, Any, None]]:
    """
    FastAPI dependency factory stating which tokens a user-facing route accepts.

    Args:
        *required_scopes: OAuth scopes; an OAuth/PAT token needs at least one (OR logic).
            Session tokens are not scope-restricted.
        service_scopes: Service-token scopes this route admits. Empty (the default)
            rejects every service token with 403.
    """
    admitted_service_scopes = tuple(service_scopes)

    async def _check_scopes(request: Request) -> None:
        user = _authenticated_user(request)

        if is_service_token(user):
            if has_service_scope(user, *admitted_service_scopes):
                return
            raise AuthRejected(status.HTTP_403_FORBIDDEN, "This route does not accept service tokens",
                               reason="service_token_not_allowed")

        if not user.get("isOAuth", False):
            return

        token_scopes = user.get("oauthScopes", [])
        if not any(scope in token_scopes for scope in required_scopes):
            raise AuthRejected(status.HTTP_403_FORBIDDEN, f"Insufficient scope. Required: {' or '.join(required_scopes)}",
                               reason="insufficient_scope")

    return _tag_auth_policy(_check_scopes, "scopes", admitted_service_scopes, required_scopes)


def require_service_token(*scopes: ScopeLike) -> Callable[..., Coroutine[Any, Any, Mapping[str, Any]]]:
    """
    FastAPI dependency factory for internal service-to-service routes.

    Only a service token carrying one of ``scopes`` passes; session and OAuth/PAT
    tokens get 403. Returns the validated claims so handlers never re-decode the JWT.
    """
    if not scopes:
        raise ValueError("require_service_token needs at least one scope")

    async def _check_service_token(request: Request) -> Mapping[str, Any]:
        user = _authenticated_user(request)
        if not has_service_scope(user, *scopes):
            raise AuthRejected(status.HTTP_403_FORBIDDEN, "This route requires a service token",
                               reason="service_token_required")
        return user

    return _tag_auth_policy(_check_service_token, "service", scopes)


def service_token_org(claims: Mapping[str, Any], requested_org_id: str | None = None) -> str:
    """Org a service token acts for; a caller-supplied org may only repeat it."""
    org_id = claims.get("orgId")
    if not org_id:
        raise AuthRejected(status.HTTP_401_UNAUTHORIZED, "Token missing orgId", reason="missing_org")
    if requested_org_id and requested_org_id != org_id:
        raise AuthRejected(status.HTTP_403_FORBIDDEN, "org_id does not match the service token", reason="org_mismatch")
    return org_id


async def deny_service_tokens(request: Request) -> None:
    """FastAPI dependency for routes open to any authenticated user but never to service tokens."""
    if is_service_token(_authenticated_user(request)):
        raise AuthRejected(status.HTTP_403_FORBIDDEN, "This route does not accept service tokens",
                           reason="service_token_not_allowed")


_tag_auth_policy(deny_service_tokens, "deny_service")
