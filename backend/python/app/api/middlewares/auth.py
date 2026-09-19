import os
from collections.abc import Callable, Coroutine, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Optional

from dependency_injector.wiring import inject
from fastapi import HTTPException, Request, status
from jose import JWTError, jwt

from app.api.middlewares.caller_role import (
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

# Marks the dependency that states which token classes a route accepts; the route
# inventory test requires one on every route so no route admits service tokens by omission.
AUTH_POLICY_ATTR = "__auth_policy__"

# Set only by the regular path from a verified OAuth access token; a service token that
# arrives carrying them must not be able to pose as an OAuth client.
_OAUTH_DERIVED_CLAIMS = ("isOAuth", "oauthScopes", "oauthClientId")


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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header is missing",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not authorization_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must start with 'Bearer '",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Extract token after "Bearer "
    token = authorization_header[7:].strip()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is missing in Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return token


def _normalize_regular_payload(payload: dict[str, Any], token: str) -> dict[str, Any]:
    """Add metadata and normalize OAuth tokens for scope enforcement."""
    payload["user"] = token
    payload["token_type"] = AuthTokenType.REGULAR.value
    if payload.get("tokenType") == "oauth":
        payload["isOAuth"] = True
        payload["oauthScopes"] = payload.get("scope", "").split(" ")
        payload["oauthClientId"] = payload.get("client_id")
        if payload.get("userId") == payload.get("client_id") and payload.get("createdBy"):
            payload["userId"] = payload["createdBy"]

    payload["role"] = normalize_auth_role(payload.get("role"))
    return payload


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
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    logger = request.app.container.logger()
    try:
        logger.debug("🚀 Starting JWT token validation")

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

        if not scoped_jwt_secret:
            logger.warning("scopedJwtSecret not found in configuration - service tokens are disabled")

        authorization_header = request.headers.get("Authorization")
        token = extract_bearer_token(authorization_header)

        try:
            payload = jwt.decode(token, regular_jwt_secret, algorithms=[algorithm])
            logger.debug("✅ Validated token using regular JWT secret")
            return _normalize_regular_payload(payload, token)
        except JWTError as regular_jwt_error:
            regular_error_name = type(regular_jwt_error).__name__

        if not scoped_jwt_secret:
            logger.warning(f"Token validation failed with regular JWT: {regular_error_name}")
            raise credentials_exception

        try:
            payload = jwt.decode(token, scoped_jwt_secret, algorithms=[algorithm])
        except JWTError as scoped_jwt_error:
            logger.warning(
                f"Token validation failed with both secrets. "
                f"Regular JWT error: {regular_error_name}, "
                f"Scoped JWT error: {type(scoped_jwt_error).__name__}"
            )
            raise credentials_exception

        scopes = token_scopes(payload)
        if not scopes & ACCEPTED_SERVICE_SCOPES:
            logger.warning(
                "Rejected service token: scopes %s are not accepted by this service",
                sorted(scopes),
            )
            raise credentials_exception

        logger.debug("✅ Validated service token with scopes %s", sorted(scopes))
        return _normalize_scoped_payload(payload, token)

    except HTTPException:
        raise
    except ValueError as e:
        # Configuration errors should be 500, not 401
        logger.error(f"Configuration error during authentication: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication configuration error",
        )
    except Exception as e:
        logger.error(f"Unexpected error during authentication: {e}", exc_info=True)
        raise credentials_exception


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
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )

    logger = request.app.container.logger()
    try:
        logger.debug("🚀 Starting authentication middleware")

        payload = await isJwtTokenValid(request)
        payload["role"] = await resolve_request_role(request, payload)

        # Attach the authenticated user information to the request state
        request.state.user = payload

        logger.debug(f"✅ Authentication successful. Token type: {payload.get('token_type', 'unknown')}")

    except HTTPException as e:
        # Re-raise HTTP exceptions as-is to preserve status codes and details
        raise e
    except Exception as e:
        logger.error(f"Unexpected error in authentication middleware: {e}", exc_info=True)
        raise credentials_exception

    return request


def is_request_admin(request: Request) -> bool:
    """Org-admin from JWT role on request.state.user."""
    user = getattr(request.state, "user", None) or {}
    getter = getattr(user, "get", None)
    role = getter("role") if callable(getter) else None
    return normalize_auth_role(role) == "admin"


async def resolve_request_role(request: Request, payload: dict[str, Any]) -> str:
    """Org role for the authenticated caller.

    Session JWTs carry a role claim. OAuth/PAT tokens do not, and only Node knows
    whether one has been revoked or its user deleted, so their role comes from Node. A
    token Node refuses is refused here, and so is one Node could not confirm.
    """
    if not payload.get("isOAuth"):
        return normalize_auth_role(payload.get("role"))

    caller = await fetch_caller_role(request, await get_config_service(request))
    if caller.status is CallerRoleStatus.REJECTED:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is no longer valid",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if caller.status is CallerRoleStatus.UNKNOWN:
        # Without Node's answer a revoked token looks exactly like a valid one.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not verify the access token; try again shortly",
        )
    return caller.role


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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
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
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This route does not accept service tokens",
            )

        if not user.get("isOAuth", False):
            return

        token_scopes = user.get("oauthScopes", [])
        if not any(scope in token_scopes for scope in required_scopes):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient scope. Required: {' or '.join(required_scopes)}",
            )

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
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This route requires a service token",
            )
        return user

    return _tag_auth_policy(_check_service_token, "service", scopes)


async def deny_service_tokens(request: Request) -> None:
    """FastAPI dependency for routes open to any authenticated user but never to service tokens."""
    if is_service_token(_authenticated_user(request)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This route does not accept service tokens",
        )


_tag_auth_policy(deny_service_tokens, "deny_service")
