"""
Client factories for Jira.
"""

from typing import Any, Optional

from app.agents.tools.factories.base import ClientFactory
from app.modules.agents.qna.chat_state import ChatState
from app.sources.client.jira.jira import JiraClient

# ============================================================================
# Jira Client Factory
# ============================================================================

class JiraClientFactory(ClientFactory):
    """
    Factory for creating Jira clients.

    - Toolset-based authentication (new architecture): Uses toolset config from etcd
    """

    async def create_client(
        self,
        config_service: object,
        logger: Optional[object],
        toolset_config: dict[str, Any],
        state: Optional[ChatState] = None
    ) -> JiraClient:
        """
        Create Jira client instance from toolset configuration.

        Args:
            config_service: Configuration service instance
            logger: Logger instance
            state: Chat state (optional)
            toolset_config: Toolset configuration from etcd (REQUIRED)

        Returns:
            JiraClient instance
        """
        return await JiraClient.build_from_toolset(
            toolset_config=toolset_config,
            logger=logger,
            config_service=config_service,
        )
