"""`create_agent_from_task`: Part A4's "promote to agent" -- a one-way copy
of a `TaskDefinition` into a standalone Agent Builder agent (Part A4:
"Creates agent graph node with task toolset, knowledge scope,
instructions... One-way copy, not a live link. Promoted agent evolves
independently.").

Delegates to `AgentProvisioningService` (app/services/agents/provisioning.py),
the canonical home for the nine-collection Arango transaction.  The original
doc on why this module doesn't import from api/routes still holds — but the
fix is calling the shared service, not duplicating the transaction here.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.tasks.domain.models import TaskDefinition

__all__ = ["create_agent_from_task"]


async def create_agent_from_task(
    task: "TaskDefinition", *, graph_provider: "IGraphDBProvider", config_service: "ConfigurationService",
) -> str:
    """Returns the new agent's `_key` (== `agentInstances` document id).
    Raises `PrerequisiteError` if the org has no AI model configured at all.
    """
    from app.services.agents.provisioning import AgentProvisioningService, AgentSpec

    service = AgentProvisioningService(graph_provider, config_service)
    spec = AgentSpec(
        name=task.title.strip() or f"Promoted task {task.task_id}",
        description=task.description.strip() or f"Promoted from scheduled task {task.task_id}",
        instructions=task.instructions.strip(),
        system_prompt=(
            "You are a workplace productivity assistant, promoted from a scheduled task. "
            "Help users with their connected work tools."
        ),
        org_id=task.org_id,
        created_by_user_id=task.created_by_user_id,
        is_service_account=task.principal.is_service_account,
        tool_names=list(task.tool_names),
        connector_ids=list(task.connector_ids),
        collection_ids=list(task.collection_ids),
        skill_names=list(task.skill_names),
        tags=["promoted-from-task"],
    )
    return await service.create(spec)
