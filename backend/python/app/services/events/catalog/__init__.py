"""App-event catalog.

Importing this package registers every built-in provider descriptor. Each
`catalog/<provider>.py` self-registers at import time, and nothing imported
them -- so the global catalog was empty and `validate_filter` accepted any
event type, including a typo'd one that could then never match a real event.

`load_builtin_descriptors()` is the supported entry point; it is idempotent
because `EventCatalog.register` keys on `event_type`.
"""
from __future__ import annotations

__all__ = ["load_builtin_descriptors"]


def load_builtin_descriptors() -> None:
    """Import the provider catalog modules for their registration side effect.

    Repeat calls are free: `sys.modules` caches the import, so the descriptors
    register exactly once per process.
    """
    from app.services.events.catalog import (  # noqa: F401
        confluence,
        github,
        jira,
        slack,
    )
