"""
Client factories for Jira.
"""

from typing import Any, Optional

from app.agents.tools.factories.base import ClientFactory, ToolsetAuthError
from app.modules.agents.qna.chat_state import ChatState
from app.sources.client.jira.jira import JiraClient
from app.sources.external.common.atlassian import (
    AtlassianMultiSiteError,
    resolve_preferred_site_with_fallback,
)

# ============================================================================
# Jira Client Factory
# ============================================================================

class JiraClientFactory(ClientFactory):
    """
    Factory for creating Jira clients.

    - Toolset-based authentication (new architecture): Uses toolset config from etcd
    """

    # Notification heading for setup failures (the OAuth callback passes it through).
    _AUTH_ERROR_TITLE = "Jira action: Resource-restricted OAuth required"

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
        try:
            return await JiraClient.build_from_toolset(
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
        """Reject an OAuth token that can reach multiple Jira sites when no specific
        site (``baseUrl``) is configured — the agent can't know which one to use."""
        base_url = ((auth_config or {}).get("baseUrl") or "").strip()
        try:
            await resolve_preferred_site_with_fallback(
                base_url, access_token, JiraClient.get_accessible_resources,
                logger, "Jira",
            )
        except AtlassianMultiSiteError as e:
            raise ToolsetAuthError(str(e), title=self._AUTH_ERROR_TITLE) from e


class JiraDataCenterClientFactory(JiraClientFactory):
    """Factory for creating Jira Data Center / Server clients.

    Shares ``JiraClient.build_from_toolset`` with the Cloud factory (the toolset
    config store, ``/services/toolsets/...``, is per-user — unlike the connector's
    ``build_from_services`` which reads ``/services/connectors/...``). The only
    Data-Center specialization is a guardrail that mirrors the DC *connector*'s
    ``init`` (connector.py: authType required, restricted to API_TOKEN/BASIC_AUTH):
    it rejects any auth type that would route through the Cloud OAuth / cloud-id
    proxy, so a DC instance always resolves to PAT→Bearer or username/password→Basic
    against the plain instance ``baseUrl``.
    """

    _SUPPORTED_AUTH_TYPES = {"API_TOKEN", "BASIC_AUTH"}

    async def create_client(
        self,
        config_service: object,
        logger: Optional[object],
        toolset_config: dict[str, Any],
        state: Optional[ChatState] = None
    ) -> JiraClient:
        auth_type = str(toolset_config.get("authType", "")).strip().upper()
        if auth_type not in self._SUPPORTED_AUTH_TYPES:
            raise ValueError(
                "Jira Data Center toolset requires authType API_TOKEN (Personal Access "
                f"Token) or BASIC_AUTH (username/password), got '{auth_type or 'missing'}'"
            )
        return await super().create_client(
            config_service=config_service,
            logger=logger,
            toolset_config=toolset_config,
            state=state,
        )
