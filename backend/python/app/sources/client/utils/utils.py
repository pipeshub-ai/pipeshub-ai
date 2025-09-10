from typing import List, Optional


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
