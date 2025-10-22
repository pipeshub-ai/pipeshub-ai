import base64
import json
from datetime import datetime, timedelta, timezone

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


async def generate_jwt(config_service: ConfigurationService, token_payload: dict) -> str:
    """
    Generate a JWT token using the jose library.

    Args:
        token_payload (dict): The payload to include in the JWT

    Returns:
        str: The generated JWT token
    """
    # Get the JWT secret from environment variable
    secret_keys = await config_service.get_config(
        config_node_constants.SECRET_KEYS.value
    )
    if not secret_keys:
        raise ValueError("SECRET_KEYS environment variable is not set")
    scoped_jwt_secret = secret_keys.get("scopedJwtSecret") # type: ignore
    if not scoped_jwt_secret:
        raise ValueError("SCOPED_JWT_SECRET environment variable is not set")

    # Add standard claims if not present
    if "exp" not in token_payload:
        # Set expiration to 1 hour from now
        token_payload["exp"] = datetime.now(timezone.utc) + timedelta(hours=1)

    if "iat" not in token_payload:
        # Set issued at to current time
        token_payload["iat"] = datetime.now(timezone.utc)

    # Generate the JWT token using jose
    token = jwt.encode(token_payload, scoped_jwt_secret, algorithm="HS256")

    return token
