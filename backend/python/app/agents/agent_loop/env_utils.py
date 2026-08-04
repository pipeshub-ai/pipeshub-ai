"""Shared environment-variable helpers for the agent-loop adapter layer.

Promotes the module-private ``_env_bool`` from
``skills/manager_factory.py`` so other modules can reuse the same
idiom without duplicating it.
"""

from __future__ import annotations

import os


def env_bool(name: str, default: bool) -> bool:
    """Return ``True`` iff the env var ``name`` is ``"true"`` (case-insensitive).

    Absent → ``default``.  Any non-``"true"`` non-empty value → ``False``.
    This is the dominant pattern in the codebase:

        os.getenv("PIPESHUB_*", "true").strip().lower() == "true"
    """
    return os.getenv(name, str(default)).strip().lower() == "true"


__all__ = ["env_bool"]
