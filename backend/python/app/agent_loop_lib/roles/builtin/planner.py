from __future__ import annotations

from app.agent_loop_lib.roles.base import Role

PLANNER_ROLE = Role(
    name="planner",
    description="Decomposes goals into ordered phases and tasks.",
    system_prompt="",  # stub — filled in implementation
    allowed_tools=["spawn_agent", "task_complete"],
    capabilities=["planning", "decomposition"],
)
