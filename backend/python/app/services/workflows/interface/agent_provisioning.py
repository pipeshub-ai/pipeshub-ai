"""`IAgentProvisioning` port -- minting an Agent Builder agent.

Kept behind a port for the same reason as the other collaborators here: the
broker's AGENT_CREATE handler and the task executor's promote-to-agent path
both need "create an agent from this spec" and nothing more, and typing them
against the concrete `AgentProvisioningService` drags its graph provider and
config service into every caller's dependency graph.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.services.agents.provisioning import AgentSpec

__all__ = ["IAgentProvisioning"]


class IAgentProvisioning(Protocol):
    async def create(self, spec: "AgentSpec") -> str:
        """Create the agent described by `spec` and return its id.

        Raises on failure -- callers decide whether an agent that could not be
        created is fatal to their operation.
        """
        ...
