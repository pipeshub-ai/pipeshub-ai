"""Whether tools are instance-scoped (``INSTANCE_TOOL_IDS``, F3 matrix): the one reader of the flag."""

from __future__ import annotations

from app.utils.env_flags import INSTANCE_TOOL_IDS, env_flag


def instance_tool_ids_enabled() -> bool:
    return env_flag(INSTANCE_TOOL_IDS)


__all__ = ["instance_tool_ids_enabled"]
