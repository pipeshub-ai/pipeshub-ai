"""Comprehensive coverage tests for app.connectors.sources.atlassian.core.oauth.

Covers:
- AtlassianScope enum values and classmethods (all branches)
- AtlassianCloudResource dataclass
- AtlassianOAuthProvider initialization, static/instance name, identity, callback
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.atlassian.core.oauth import (
    AtlassianCloudResource,
    AtlassianOAuthProvider,
    AtlassianScope,
    OAUTH_CONFIG_PATH,
    OAUTH_CONFLUENCE_CONFIG_PATH,
    OAUTH_CONFLUENCE_CREDENTIALS_PATH,
    OAUTH_JIRA_CONFIG_PATH,
    OAUTH_JIRA_CREDENTIALS_PATH,
)


# =============================================================================
# AtlassianScope Enum
# =============================================================================


class TestAtlassianScopeEnum:
    """Verify every enum member and classmethod for full coverage."""

    def test_jira_basic_returns_list_with_required_scopes(self):
        scopes = AtlassianScope.get_jira_basic()
        assert isinstance(scopes, list)
        assert AtlassianScope.JIRA_WORK_READ.value in scopes
        assert AtlassianScope.JIRA_USER_READ.value in scopes
        assert AtlassianScope.ACCOUNT_READ.value in scopes
        assert AtlassianScope.OFFLINE_ACCESS.value in scopes
        assert len(scopes) == 4

    def test_jira_read_access(self):
        scopes = AtlassianScope.get_jira_read_access()
        assert isinstance(scopes, list)
        # Check all expected values present
        expected = [
            "read:jira-work", "read:jira-user", "read:user:jira",
            "read:group:jira", "read:avatar:jira", "read:audit-log:jira",
            "read:application-role:jira", "read:project-role:jira",
            "read:account", "offline_access",
        ]
        for s in expected:
            assert s in scopes

    def test_confluence_basic(self):
        scopes = AtlassianScope.get_confluence_basic()
        assert isinstance(scopes, list)
        assert "read:confluence-content.all" in scopes
        assert "offline_access" in scopes
        assert "read:page:confluence" in scopes
        assert "read:comment:confluence" in scopes

    def test_confluence_read_access(self):
        scopes = AtlassianScope.get_confluence_read_access()
        assert isinstance(scopes, list)
        assert "read:page:confluence" in scopes
        assert "read:blogpost:confluence" in scopes
        assert "read:attachment:confluence" in scopes
        assert "read:permission:confluence" in scopes
        assert "read:folder:confluence" in scopes
        assert "search:confluence" in scopes
        assert "read:content-details:confluence" in scopes
        assert "read:confluence-groups" in scopes
        assert "read:confluence-content.permission" in scopes
        assert "read:confluence-props" in scopes
        assert "read:email-address:confluence" in scopes

    def test_full_access_contains_jira_and_confluence_and_common(self):
        scopes = AtlassianScope.get_full_access()
        # Jira write scopes
        assert "write:jira-work" in scopes
        assert "manage:jira-configuration" in scopes
        # Confluence write scopes
        assert "write:confluence:content" in scopes
        assert "write:page:confluence" in scopes
        assert "delete:confluence-content" in scopes
        # Common
        assert "read:me" in scopes
        assert "offline_access" in scopes

    def test_scope_values_are_strings(self):
        """Every enum member value should be a string."""
        for member in AtlassianScope:
            assert isinstance(member.value, str)
            assert len(member.value) > 0

    def test_individual_scope_values(self):
        assert AtlassianScope.JIRA_WEBHOOK_READ.value == "read:webhook:jira"
        assert AtlassianScope.JIRA_WEBHOOK_WRITE.value == "write:webhook:jira"
        assert AtlassianScope.JIRA_PROJECT_MANAGE.value == "manage:jira-project"
        assert AtlassianScope.JIRA_DATA_PROVIDER_MANAGE.value == "manage:jira-data-provider"
        assert AtlassianScope.JIRA_USER_VIEW.value == "read:user:jira"
        assert AtlassianScope.JIRA_USER_COLUMNS.value == "read:user.columns:jira"
        assert AtlassianScope.JIRA_PROJECT_READ.value == "read:project:jira"
        assert AtlassianScope.JIRA_PROJECT_WRITE.value == "write:project:jira"
        assert AtlassianScope.CONFLUENCE_CONTENT_WRITE.value == "write:confluence:content"
        assert AtlassianScope.CONFLUENCE_CONTENT_CREATE.value == "write:confluence-content"
        assert AtlassianScope.CONFLUENCE_COMMENT_DELETE.value == "delete:comment:confluence"
        assert AtlassianScope.CONFLUENCE_SPACE_READ_ALL.value == "read:space:confluence"
        assert AtlassianScope.CONFLUENCE_USER_READ_CLASSIC.value == "read:confluence-user"
        assert AtlassianScope.CONFLUENCE_EMAIL_READ.value == "read:email-address:confluence"
        assert AtlassianScope.CONFLUENCE_AUDIT_LOG_READ.value == "read:audit-log:confluence"
        assert AtlassianScope.CONFLUENCE_BLOGPOST_WRITE.value == "write:blogpost:confluence"
        assert AtlassianScope.CONFLUENCE_ATTACHMENT_WRITE.value == "write:attachment:confluence"
        assert AtlassianScope.CONFLUENCE_COMMENT_WRITE.value == "write:comment:confluence"


# =============================================================================
# AtlassianCloudResource
# =============================================================================


class TestAtlassianCloudResource:
    def test_creation_with_all_fields(self):
        resource = AtlassianCloudResource(
            id="r-123", name="MySite", url="https://mysite.atlassian.net",
            scopes=["read:jira-work"], avatar_url="https://img/avatar.png",
        )
        assert resource.id == "r-123"
        assert resource.name == "MySite"
        assert resource.url == "https://mysite.atlassian.net"
        assert resource.scopes == ["read:jira-work"]
        assert resource.avatar_url == "https://img/avatar.png"

    def test_creation_without_optional_avatar(self):
        resource = AtlassianCloudResource(
            id="r-456", name="Site2", url="https://site2.atlassian.net",
            scopes=[],
        )
        assert resource.avatar_url is None


# =============================================================================
# AtlassianOAuthProvider
# =============================================================================


class TestAtlassianOAuthProvider:
    """Tests for AtlassianOAuthProvider covering init branches and methods."""

    @patch("app.connectors.sources.atlassian.core.oauth.OAuthProvider.__init__", return_value=None)
    def test_init_with_default_scopes(self, mock_parent_init):
        """When scopes is None, get_full_access() is used."""
        config_service = MagicMock()
        provider = AtlassianOAuthProvider(
            client_id="cid", client_secret="csec",
            redirect_uri="https://callback",
            configuration_service=config_service,
            credentials_path="/creds",
            scopes=None,
        )
        # Parent was called with OAuthConfig whose scope contains full access scopes
        call_args = mock_parent_init.call_args
        oauth_config = call_args[0][0]
        assert "read:jira-work" in oauth_config.scope
        assert "offline_access" in oauth_config.scope
        assert provider._accessible_resources is None

    @patch("app.connectors.sources.atlassian.core.oauth.OAuthProvider.__init__", return_value=None)
    def test_init_with_custom_scopes(self, mock_parent_init):
        """When scopes is provided, they are used instead of defaults."""
        config_service = MagicMock()
        provider = AtlassianOAuthProvider(
            client_id="cid", client_secret="csec",
            redirect_uri="https://cb",
            configuration_service=config_service,
            credentials_path="/creds",
            scopes=["offline_access", "read:me"],
        )
        call_args = mock_parent_init.call_args
        oauth_config = call_args[0][0]
        assert oauth_config.scope == "offline_access read:me"
        # Check additional params
        assert oauth_config.additional_params == {"audience": "api.atlassian.com", "prompt": "consent"}

    def test_get_name_static(self):
        assert AtlassianOAuthProvider.get_name() == "atlassian"

    @patch("app.connectors.sources.atlassian.core.oauth.OAuthProvider.__init__", return_value=None)
    def test_get_provider_name(self, mock_parent_init):
        provider = AtlassianOAuthProvider(
            client_id="x", client_secret="y",
            redirect_uri="z", configuration_service=MagicMock(),
            credentials_path="/p",
        )
        assert provider.get_provider_name() == "atlassian"

    @pytest.mark.asyncio
    @patch("app.connectors.sources.atlassian.core.oauth.OAuthProvider.__init__", return_value=None)
    async def test_get_identity(self, mock_parent_init):
        """get_identity makes a GET to /me with the access token."""
        provider = AtlassianOAuthProvider(
            client_id="x", client_secret="y",
            redirect_uri="z", configuration_service=MagicMock(),
            credentials_path="/p",
        )

        mock_resp = AsyncMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={"account_id": "abc", "email": "u@e.com"})

        mock_session = AsyncMock()
        mock_session.closed = False
        mock_session.get = MagicMock(return_value=mock_resp)
        # Make the context manager work
        mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.get.return_value.__aexit__ = AsyncMock(return_value=False)

        # session is an async @property; use backing field used by OAuthProvider.session
        provider._session = mock_session

        from app.connectors.core.base.token_service.oauth_service import OAuthToken
        token = MagicMock(spec=OAuthToken)
        token.access_token = "test-token"

        identity = await provider.get_identity(token)
        assert identity == {"account_id": "abc", "email": "u@e.com"}

    @pytest.mark.asyncio
    @patch("app.connectors.sources.atlassian.core.oauth.OAuthProvider.__init__", return_value=None)
    @patch("app.connectors.sources.atlassian.core.oauth.OAuthProvider.handle_callback")
    async def test_handle_callback_delegates_to_parent(self, mock_parent_callback, mock_parent_init):
        """handle_callback calls super().handle_callback() and returns the token."""
        from app.connectors.core.base.token_service.oauth_service import OAuthToken
        mock_token = MagicMock(spec=OAuthToken)
        mock_token.access_token = "tok"
        mock_parent_callback.return_value = mock_token

        provider = AtlassianOAuthProvider(
            client_id="x", client_secret="y",
            redirect_uri="z", configuration_service=MagicMock(),
            credentials_path="/p",
        )

        result = await provider.handle_callback("code123", "state456")
        assert result == mock_token
        mock_parent_callback.assert_called_once_with("code123", "state456")


# =============================================================================
# Module-level constants
# =============================================================================


class TestModuleConstants:
    def test_oauth_config_path(self):
        assert OAUTH_CONFIG_PATH == "/services/connectors/atlassian/config"

    def test_credential_paths(self):
        assert OAUTH_CONFLUENCE_CREDENTIALS_PATH == "/services/connectors/atlassian/confluence/credentials"
        assert OAUTH_JIRA_CREDENTIALS_PATH == "/services/connectors/jira/credentials"

    def test_config_paths_have_placeholder(self):
        assert "{connector_id}" in OAUTH_CONFLUENCE_CONFIG_PATH
        assert "{connector_id}" in OAUTH_JIRA_CONFIG_PATH

    def test_oauth_endpoint_urls(self):
        assert AtlassianOAuthProvider.AUTHORIZE_URL == "https://auth.atlassian.com/authorize"
        assert AtlassianOAuthProvider.TOKEN_URL == "https://auth.atlassian.com/oauth/token"
        assert AtlassianOAuthProvider.RESOURCE_URL == "https://api.atlassian.com/oauth/token/accessible-resources"
