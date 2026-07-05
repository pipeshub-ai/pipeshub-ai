from __future__ import annotations

from app.agent_loop_lib.roles.base import Role

VERIFIER_ROLE = Role(
    name="verifier",
    description="Validates outputs against success criteria.",
    system_prompt="",
    allowed_tools=["task_complete"],
    capabilities=["verification", "testing"],
)
