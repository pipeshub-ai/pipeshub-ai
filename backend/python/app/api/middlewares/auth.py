import os

from dependency_injector.wiring import inject
from fastapi import HTTPException, Request, status
from jose import JWTError, jwt

from app.config.constants.service import config_node_constants
from app.config.key_value_store import KeyValueStore


async def get_key_value_store(request: Request) -> KeyValueStore:
    container = request.app.container
    key_value_store = container.key_value_store()
    return key_value_store


# Authentication logic
@inject
async def isJwtTokenValid(request: Request) -> dict:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        logger = request.app.container.logger()
        logger.debug("🚀 Starting authentication")
        key_value_store = await get_key_value_store(request)
        secret_keys = await key_value_store.get_key(
            config_node_constants.SECRET_KEYS.value
        )
        jwt_secret = secret_keys.get("jwtSecret")
        algorithm = os.environ.get("JWT_ALGORITHM", "HS256")
        if not jwt_secret:
            raise ValueError("Missing SECRET_KEY in environment variables")
        # Get the Authorization header
        authorization: str = request.headers.get("Authorization")
        if not authorization or not authorization.startswith("Bearer "):
            raise credentials_exception
        # Extract the token
        token = authorization.split("Bearer ")[1]
        if not token:
            raise credentials_exception
        # Decode the JWT
        payload = jwt.decode(token, jwt_secret, algorithms=[algorithm])
        payload["user"] = token
        return payload
    except JWTError as e:
        logger.error(f"JWT Error: {e}")
        raise credentials_exception


# Dependency for injecting authentication
async def authMiddleware(request: Request) -> Request:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )
    try:  # Validate the token
        logger = request.app.container.logger()
        logger.debug("🚀 Starting authentication")
        payload = await isJwtTokenValid(request)
        # Attach the authenticated user information to the request state
        request.state.user = payload
    except HTTPException as e:
        raise e  # Re-raise HTTPException instances
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise credentials_exception
    return request
