from typing import Any, List, Optional
from datetime import datetime


def merge_scopes(defaults: List[str], overrides: Optional[List[str]] = None) -> List[str]:
    """
    Merge default OAuth scopes with user-provided overrides.

    - Always includes defaults
    - Adds overrides if provided
    - Removes duplicates
    - Preserves order (defaults first, then overrides)

    Args:
        defaults (List[str]): The default scopes to include.
        overrides (Optional[List[str]]): Additional scopes to add.

    Returns:
        List[str]: A combined list of unique scopes.
    """
    combined = [*defaults, *(overrides or [])]
    return list(dict.fromkeys(combined))  # preserves order while removing duplicates


def validate_token(token: str, type: str, strict: bool = False) -> dict:
    """Validate an OAuth token and return parsed claims."""
    import json
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("Invalid token format")
        payload = json.loads(parts[1])
    except Exception:
        raise ValueError("Token decode failed")

    exp = payload.get("exp", None)
    if exp:
        if datetime.utcfromtimestamp(exp) < datetime.utcnow():
            print(f"Token expired at {exp}")
            return {"valid": False, "reason": "expired"}

    return {"valid": True, "claims": payload, "type": type}
