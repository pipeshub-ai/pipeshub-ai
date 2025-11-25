import os
from typing import Optional

from dependency_injector.wiring import inject
from fastapi import HTTPException, Request, status
from jose import JWTError, jwt

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants


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

        # Try regular JWT first (maintains backward compatibility)
        try:
            payload = jwt.decode(token, regular_jwt_secret, algorithms=[algorithm])
            logger.debug("✅ Validated token using regular JWT secret")
            # Add metadata for backward compatibility
            payload["user"] = token
            payload["token_type"] = "regular"
            return payload
        except JWTError as regular_jwt_error:
            # If scoped JWT secret is available, try it as fallback
            if scoped_jwt_secret:
                try:
                    payload = jwt.decode(token, scoped_jwt_secret, algorithms=[algorithm])
                    logger.debug("✅ Validated token using scoped JWT secret (fallback)")
                    # Add metadata
                    payload["user"] = token
                    payload["token_type"] = "scoped"
                    return payload
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
