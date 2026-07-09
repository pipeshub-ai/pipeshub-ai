"""
Client factories for Confluence.
"""

from typing import Any, Optional

from app.agents.tools.factories.base import ClientFactory
from app.modules.agents.qna.chat_state import ChatState
from app.sources.client.confluence.confluence import ConfluenceClient

# ============================================================================
# Confluence Client Factory
# ============================================================================

class ConfluenceClientFactory(ClientFactory):
    """
    Factory for creating Confluence clients.
    """

    async def create_client(
        self,
        config_service: object,
        logger: Optional[object],
        toolset_config: dict[str, Any],
        state: Optional[ChatState] = None
    ) -> ConfluenceClient:
        """
        Create Confluence client instance from toolset configuration.

        Args:
            config_service: Configuration service instance
            logger: Logger instance
            state: Chat state (optional)
            toolset_config: Toolset configuration from etcd (REQUIRED)

        Returns:
            ConfluenceClient instance
        """
        return await ConfluenceClient.build_from_toolset(
            toolset_config=toolset_config,
            logger=logger,
            config_service=config_service,
        )


class ConfluenceDataCenterClientFactory(ConfluenceClientFactory):
    """Factory for creating Confluence Data Center / Server clients.

    Shares ``ConfluenceClient.build_from_toolset`` with the Cloud factory. The only
    Data-Center specialization is a guardrail that mirrors the DC *connector*'s init
    (restricted to API_TOKEN/BASIC_AUTH): it rejects any auth type that would route
    through the Cloud OAuth / cloud-id proxy, so a DC instance always resolves to
    PAT→Bearer or username/password→Basic against the plain instance ``baseUrl``.
    Mirrors ``JiraDataCenterClientFactory``.
    """

    _SUPPORTED_AUTH_TYPES = {"API_TOKEN", "BASIC_AUTH"}

    async def create_client(
        self,
        config_service: object,
        logger: Optional[object],
        toolset_config: dict[str, Any],
        state: Optional[ChatState] = None
    ) -> ConfluenceClient:
        auth_type = str(toolset_config.get("authType", "")).strip().upper()
        if auth_type not in self._SUPPORTED_AUTH_TYPES:
            raise ValueError(
                "Confluence Data Center toolset requires authType API_TOKEN (Personal "
                "Access Token) or BASIC_AUTH (username/password), got "
                f"'{auth_type or 'missing'}'"
            )
        return await super().create_client(
            config_service=config_service,
            logger=logger,
            toolset_config=toolset_config,
            state=state,
        )
