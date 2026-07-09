"""
Client factories for Confluence.
"""

from typing import Any, Optional

from app.agents.tools.factories.base import ClientFactory, ToolsetAuthError
from app.modules.agents.qna.chat_state import ChatState
from app.sources.client.confluence.confluence import ConfluenceClient
from app.sources.external.common.atlassian import (
    AtlassianMultiSiteError,
    resolve_preferred_site_with_fallback,
)

# ============================================================================
# Confluence Client Factory
# ============================================================================

class ConfluenceClientFactory(ClientFactory):
    """
    Factory for creating Confluence clients.
    """

    # Notification heading for setup failures (the OAuth callback passes it through).
    _AUTH_ERROR_TITLE = "Confluence action: Resource-restricted OAuth required"

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
        try:
            return await ConfluenceClient.build_from_toolset(
                toolset_config=toolset_config,
                logger=logger,
                config_service=config_service,
            )
        except AtlassianMultiSiteError as e:
            # Translate the provider-specific error to the generic toolset error the
            # wrapper handles, so no Atlassian knowledge leaks into generic code.
            raise ToolsetAuthError(str(e), title=self._AUTH_ERROR_TITLE) from e

    async def test_connection(
        self,
        *,
        access_token: str,
        auth_config: dict,
        config_service: object,
        logger: Optional[object],
    ) -> None:
        """Reject an OAuth token that can reach multiple Confluence sites when no
        specific site (``baseUrl``) is configured."""
        base_url = ((auth_config or {}).get("baseUrl") or "").strip()
        try:
            await resolve_preferred_site_with_fallback(
                base_url, access_token, ConfluenceClient.get_accessible_resources,
                logger, "Confluence",
            )
        except AtlassianMultiSiteError as e:
            raise ToolsetAuthError(str(e), title=self._AUTH_ERROR_TITLE) from e


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
