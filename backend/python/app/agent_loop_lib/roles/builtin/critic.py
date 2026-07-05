from __future__ import annotations

from app.agent_loop_lib.roles.base import Role

CRITIC_ROLE = Role(
    name="critic",
    description="Reviews plans and outputs for flaws.",
    system_prompt="",
    allowed_tools=["task_complete"],
    capabilities=["critique", "review"],
)
