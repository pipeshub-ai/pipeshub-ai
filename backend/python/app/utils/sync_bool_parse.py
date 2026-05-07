"""Parse boolean sync flags from etcd / JSON (strings or bools)."""

from typing import Any


def parse_sync_bool(raw: Any, default: bool) -> bool:
    """Parse boolean sync config values from etcd / JSON (strings or bools)."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return default
