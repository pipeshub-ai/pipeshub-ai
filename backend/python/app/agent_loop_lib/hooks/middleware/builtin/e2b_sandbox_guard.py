"""``e2b_sandbox_guard``: backward-compatible alias for
``metered_sandbox_guard`` — kept so existing imports and test references
continue to work.  New code should use ``metered_sandbox_guard`` directly.

See ``metered_sandbox_guard.py`` for the full docstring."""

from __future__ import annotations

from app.agent_loop_lib.hooks.middleware.builtin.metered_sandbox_guard import (
    metered_sandbox_guard,
)

__all__ = ["e2b_sandbox_guard"]


def e2b_sandbox_guard(
    max_timeout: float = 120.0,
    max_cumulative_s: float | None = None,
    default_timeout: float = 30.0,
):
    """Thin alias — delegates to ``metered_sandbox_guard``."""
    return metered_sandbox_guard(
        max_timeout=max_timeout,
        max_cumulative_s=max_cumulative_s,
        default_timeout=default_timeout,
    )
