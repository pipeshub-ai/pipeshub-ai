import base64
import json
from datetime import datetime


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
