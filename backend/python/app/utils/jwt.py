import base64
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import jwt  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants


def is_jwt_expired(token: str) -> bool:
    """
    Check if JWT token is expired
    Args:
        token: JWT token string
    Returns:
        True if token is expired, False otherwise
    """
    if not token:
        return True

    # Split the JWT token into its parts
    TOKEN_PARTS = 3
    parts = token.split('.')
    if len(parts) != TOKEN_PARTS:
        return True

    # Decode the payload (second part)
    payload = parts[1]

    # Add padding if necessary
    padding = len(payload) % 4
    if padding:
        payload += '=' * (4 - padding)

    # Decode base64
    decoded_payload = base64.urlsafe_b64decode(payload)
    payload_data = json.loads(decoded_payload)

    # Check if 'exp' claim exists
    if 'exp' not in payload_data:
        return True

    # Get current timestamp
    current_time = datetime.utcnow().timestamp()

    # Check if token is expired
    return payload_data['exp'] < current_time


SERVICE_TOKEN_TTL = timedelta(hours=1)


def mint_service_token(
    scoped_jwt_secret: str,
    claims: dict[str, Any],
    ttl: timedelta = SERVICE_TOKEN_TTL,
) -> str:
    """Sign a service-to-service JWT with the scoped secret.

    ``iat``/``exp`` are always stamped here, overriding any in ``claims``, so no
    service token can be minted without an expiry.
    """
    issued_at = datetime.now(timezone.utc)
    payload = {**claims, "iat": issued_at, "exp": issued_at + ttl}
    return jwt.encode(payload, scoped_jwt_secret, algorithm="HS256")


async def generate_jwt(config_service: ConfigurationService, token_payload: dict) -> str:
    """
    Mint a service token signed with the scoped JWT secret from configuration.

    Args:
        token_payload (dict): The claims to include in the JWT

    Returns:
        str: The generated JWT token
    """
    secret_keys = await config_service.get_config(
        config_node_constants.SECRET_KEYS.value
    )
    if not secret_keys:
        raise ValueError("SECRET_KEYS environment variable is not set")
    scoped_jwt_secret = secret_keys.get("scopedJwtSecret") # type: ignore
    if not scoped_jwt_secret:
        raise ValueError("SCOPED_JWT_SECRET environment variable is not set")

    return mint_service_token(scoped_jwt_secret, token_payload)
