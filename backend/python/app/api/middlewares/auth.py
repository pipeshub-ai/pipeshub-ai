import os
from collections.abc import Callable, Coroutine
from typing import Any, Optional

import httpx
from dependency_injector.wiring import inject
from fastapi import HTTPException, Request, status
from jose import JWTError, jwt

from app.config.configuration_service import ConfigurationService
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import DefaultEndpoints, config_node_constants

_ADMIN_CHECK_TIMEOUT_SECONDS = 5.0


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

# Authentication logic
@inject
async def isJwtTokenValid(request: Request) -> dict:
    """
    Validate JWT token using either regular JWT secret (for frontend) or scoped JWT secret (for internal services).

    This function maintains backward compatibility by trying regular JWT first (the original behavior),
    then falls back to scoped JWT for internal service calls. This ensures existing frontend calls
    continue to work without any changes.

    Args:
        request: FastAPI request object

    Returns:
        dict: Decoded JWT payload with token metadata:
            - All original payload fields
            - "user": The original token string (for backward compatibility)
            - "token_type": Either "regular" or "scoped"

    Raises:
        HTTPException: If token validation fails with both secrets
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
            config_node_constants.SECRET_KEYS.value
        )

        if not secret_keys:
            raise ValueError("Secret keys configuration not found")

        # Get both JWT secrets
        regular_jwt_secret = secret_keys.get("jwtSecret")
        scoped_jwt_secret = secret_keys.get("scopedJwtSecret")
        algorithm = os.environ.get("JWT_ALGORITHM", "HS256")
        # Validate required secrets exist
        if not regular_jwt_secret:
            raise ValueError("Missing jwtSecret in configuration")

        # scoped_jwt_secret is optional - if missing, we'll only try regular JWT
        if not scoped_jwt_secret:
            logger.warning("scopedJwtSecret not found in configuration - scoped JWT validation disabled")

        # Extract token from Authorization header
        authorization_header = request.headers.get("Authorization")
        token = extract_bearer_token(authorization_header)

        def _normalize_payload(payload: dict, token_type: str) -> dict:
            """Add metadata and normalize OAuth tokens for scope enforcement."""
            payload["user"] = token
            payload["token_type"] = token_type
            # Detect OAuth tokens and normalize for require_scopes()
            if payload.get("tokenType") == "oauth":
                payload["isOAuth"] = True
                payload["oauthScopes"] = payload.get("scope", "").split(" ")
                payload["oauthClientId"] = payload.get("client_id")
                if payload.get("userId") == payload.get("client_id") and payload.get("createdBy"):
                    payload["userId"] = payload["createdBy"]

            payload["role"] = normalize_auth_role(payload.get("role"))
            return payload

        # Try regular JWT first (maintains backward compatibility)
        try:
            payload = jwt.decode(token, regular_jwt_secret, algorithms=[algorithm])
            logger.debug("✅ Validated token using regular JWT secret")
            return _normalize_payload(payload, "regular")
        except JWTError as regular_jwt_error:
            # If scoped JWT secret is available, try it as fallback
            if scoped_jwt_secret:
                try:
                    payload = jwt.decode(token, scoped_jwt_secret, algorithms=[algorithm])
                    logger.debug("✅ Validated token using scoped JWT secret (fallback)")
                    return _normalize_payload(payload, "scoped")
                except JWTError as scoped_jwt_error:
                    # Both failed - log and raise
                    logger.warning(
                        f"Token validation failed with both secrets. "
                        f"Regular JWT error: {type(regular_jwt_error).__name__}, "
                        f"Scoped JWT error: {type(scoped_jwt_error).__name__}"
                    )
                    raise credentials_exception
            else:
                # No scoped secret available, regular JWT failed
                logger.warning(f"Token validation failed with regular JWT: {type(regular_jwt_error).__name__}")
                raise credentials_exception

    except HTTPException:
        # Re-raise HTTP exceptions as-is (including credentials_exception)
        raise
    except ValueError as e:
        # Configuration errors should be 500, not 401
        logger.error(f"Configuration error during authentication: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication configuration error",
        )
    except Exception as e:
        # Catch-all for unexpected errors
        logger.error(f"Unexpected error during authentication: {e}", exc_info=True)
        raise credentials_exception


# Dependency for injecting authentication
async def authMiddleware(request: Request) -> Request:
    """
    FastAPI middleware dependency for authenticating requests.

    Validates JWT token and attaches authenticated user information to request.state.user.
    Supports both regular JWT (frontend) and scoped JWT (internal services).

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


def normalize_auth_role(role: Any) -> str:
    """Map a JWT role claim to admin|member. Unknown/missing → member (fail closed)."""
    if isinstance(role, str) and role.strip().lower() == "admin":
        return "admin"
    return "member"


def is_request_admin(request: Request) -> bool:
    """Org-admin from JWT role on request.state.user."""
    user = getattr(request.state, "user", None) or {}
    getter = getattr(user, "get", None)
    role = getter("role") if callable(getter) else None
    return normalize_auth_role(role) == "admin"


async def resolve_request_role(request: Request, payload: dict[str, Any]) -> str:
    """Session JWT carries role. OAuth/PAT does not — look up via Node adminCheck."""
    role = normalize_auth_role(payload.get("role"))
    if role == "admin":
        return role
    if payload.get("isOAuth"):
        user_id = payload.get("userId")
        if user_id and await _lookup_oauth_admin(request, str(user_id)):
            return "admin"
    return "member"


async def _lookup_oauth_admin(request: Request, user_id: str) -> bool:
    """Verify OAuth caller is org admin via Node (JWT has no role claim). Fail closed."""
    logger = request.app.container.logger()
    try:
        config_service = await get_config_service(request)
        try:
            endpoints = await config_service.get_config("/services/endpoints", use_cache=False)
            nodejs_url = (
                endpoints.get("nodejs", {}).get("endpoint")
                if isinstance(endpoints, dict)
                else None
            ) or DefaultEndpoints.NODEJS_ENDPOINT.value
        except Exception:
            nodejs_url = DefaultEndpoints.NODEJS_ENDPOINT.value

        auth_headers: dict[str, str] = {}
        for header_name in ("authorization", "Authorization"):
            val = request.headers.get(header_name)
            if val:
                auth_headers["authorization"] = val
                break

        async with httpx.AsyncClient(timeout=_ADMIN_CHECK_TIMEOUT_SECONDS) as client:
            resp = await client.get(
                f"{nodejs_url}/api/v1/users/{user_id}/adminCheck",
                headers=auth_headers,
            )
            return resp.status_code == HttpStatusCode.OK.value
    except Exception as exc:
        logger.warning(
            "OAuth admin lookup failed for user %s: %s. Defaulting to member.",
            user_id,
            exc,
        )
        return False


def require_scopes(*required_scopes: str) -> Callable[..., Coroutine[Any, Any, None]]:
    """
    FastAPI dependency factory that enforces OAuth scope validation.
    Args:
        *required_scopes: One or more scope strings. The token must have
                        at least one of them (OR logic).
    """
    async def _check_scopes(request: Request) -> None:
        user = getattr(request.state, "user", None)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Only enforce scopes for OAuth tokens
        if not user.get("isOAuth", False):
            return

        token_scopes = user.get("oauthScopes", [])
        if not any(scope in token_scopes for scope in required_scopes):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient scope. Required: {' or '.join(required_scopes)}",
            )

    return _check_scopes
