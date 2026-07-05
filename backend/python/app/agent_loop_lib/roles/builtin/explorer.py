from __future__ import annotations

from app.agent_loop_lib.roles.base import Role

EXPLORER_ROLE = Role(
    name="explorer",
    description="Navigates and reads files and codebases.",
    system_prompt="",
    allowed_tools=["task_complete"],
    capabilities=["exploration", "reading"],
)
