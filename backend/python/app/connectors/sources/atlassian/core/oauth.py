from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from app.config.configuration_service import ConfigurationService
from app.connectors.core.base.token_service.oauth_service import (
    OAuthConfig,
    OAuthProvider,
    OAuthToken,
)

OAUTH_CONFIG_PATH = "/services/connectors/atlassian/config"
OAUTH_CONFLUENCE_CREDENTIALS_PATH = "/services/connectors/atlassian/confluence/credentials"
OAUTH_JIRA_CREDENTIALS_PATH = "/services/connectors/jira/credentials"
OAUTH_CONFLUENCE_CONFIG_PATH = "/services/connectors/{connector_id}/config"
OAUTH_JIRA_CONFIG_PATH = "/services/connectors/{connector_id}/config"



class AtlassianScope(Enum):
    """Common Atlassian OAuth Scopes"""
    # Jira Scopes
    JIRA_WORK_READ = "read:jira-work"
    JIRA_WORK_WRITE = "write:jira-work"
    JIRA_USER_READ = "read:jira-user"
    USER_JIRA_READ = "read:user:jira"
    JIRA_GROUP_READ = "read:group:jira"
    JIRA_AVATAR_READ = "read:avatar:jira"
    JIRA_WEBHOOK_READ = "read:webhook:jira"
    JIRA_WEBHOOK_WRITE = "write:webhook:jira"
    JIRA_PROJECT_MANAGE = "manage:jira-project"
    JIRA_CONFIGURATION_MANAGE = "manage:jira-configuration"
    JIRA_DATA_PROVIDER_MANAGE = "manage:jira-data-provider"
    JIRA_USER_VIEW = "read:user:jira"
    JIRA_USER_COLUMNS = "read:user.columns:jira"

    JIRA_PROJECT_READ = "read:project:jira"
    JIRA_PROJECT_WRITE = "write:project:jira"
    JIRA_AUDIT_LOG_READ = "read:audit-log:jira"
    JIRA_APPLICATION_ROLE_READ = "read:application-role:jira"
    JIRA_PROJECT_ROLE_READ = "read:project-role:jira"
    JIRA_ISSUE_SECURITY_LEVEL_READ = "read:issue-security-level:jira"
    JIRA_FIELD_READ = "read:field:jira"

    # Confluence Scopes
    CONFLUENCE_CONTENT_READ = "read:confluence-content.all"
    CONFLUENCE_CONTENT_DETAILS_READ = "read:content-details:confluence"
    CONFLUENCE_AUDIT_LOG_READ = "read:audit-log:confluence"
    CONFLUENCE_PAGE_READ = "read:page:confluence"
    CONFLUENCE_ATTACHMENT_READ = "read:attachment:confluence"
    CONFLUENCE_BLOGPOST_READ = "read:blogpost:confluence"
    CONFLUENCE_COMMENT_READ = "read:comment:confluence"
    CONFLUENCE_GROUP_READ = "read:group:confluence"
    CONFLUENCE_USER_DETAILS_READ = "read:user:confluence"
    CONFLUENCE_SPACE_DETAILS_READ = "read:space:confluence"
    CONFLUENCE_PERMISSION_READ = "read:permission:confluence"
    CONFLUENCE_FOLDER_READ = "read:folder:confluence"
    CONFLUENCE_EMAIL_ADDRESS_READ = "read:email-address:confluence"
    # Write/Delete Scopes
    CONFLUENCE_CONTENT_WRITE = "write:confluence:content"
    CONFLUENCE_CONTENT_CREATE = "write:confluence-content"
    CONFLUENCE_CONTENT_DELETE = "delete:confluence-content"
    CONFLUENCE_CONTENT_PERMISSION = "read:confluence-content.permission"
    CONFLUENCE_SPACE_READ = "read:confluence-space.summary"
    CONFLUENCE_SPACE_READ_ALL = "read:space:confluence"
    CONFLUENCE_USER_READ = "read:user:confluence"
    CONFLUENCE_USER_READ_CLASSIC = "read:confluence-user"
    CONFLUENCE_GROUPS_READ = "read:group:confluence"
    CONFLUENCE_GROUPS_READ_CLASSIC = "read:confluence-groups"
    CONFLUENCE_PROPS_READ = "read:confluence-props"
    CONFLUENCE_PAGE_WRITE = "write:page:confluence"
    CONFLUENCE_ATTACHMENT_WRITE = "write:attachment:confluence"
    CONFLUENCE_BLOGPOST_WRITE = "write:blogpost:confluence"
    CONFLUENCE_COMMENT_WRITE = "write:comment:confluence"
    CONFLUENCE_SEARCH = "search:confluence"
    CONFLUENCE_EMAIL_READ = "read:email-address:confluence"
    CONFLUENCE_COMMENT_DELETE = "delete:comment:confluence"

    # Common Scopes
    ACCOUNT_READ = "read:account"
    ACCOUNT_EMAIL_READ = "read:me"
    OFFLINE_ACCESS = "offline_access"

    @classmethod
    def get_jira_basic(cls) -> List[str]:
        """Get essential Jira scopes (for minimal user/work access)"""
        return [
            cls.JIRA_WORK_READ.value,
            cls.JIRA_USER_READ.value,
            cls.ACCOUNT_READ.value,
            cls.OFFLINE_ACCESS.value,
        ]

    @classmethod
    def get_jira_read_access(cls) -> List[str]:
        """
        Get read-only access scopes for Jira connector.
        Uses classic scopes for broad access plus minimal granular scopes.
        """
        return [
            # Classic scopes (recommended for broad access)
            cls.JIRA_WORK_READ.value,           # Read project/issue data, search issues, attachments, worklogs
            cls.JIRA_USER_READ.value,           # View user information (usernames, emails, avatars)

            # Granular scopes (for specific API access)
            cls.USER_JIRA_READ.value,           # Granular: View user details
            cls.JIRA_GROUP_READ.value,          # Read groups and group members
            cls.JIRA_AVATAR_READ.value,         # Read user/project avatars
            cls.JIRA_AUDIT_LOG_READ.value,      # Read audit logs (for detecting deleted issues)
            cls.JIRA_APPLICATION_ROLE_READ.value,  # Read application roles
            cls.JIRA_PROJECT_ROLE_READ.value,   # Read project roles
            cls.JIRA_ISSUE_SECURITY_LEVEL_READ.value,   # Read issue security level members
            cls.JIRA_FIELD_READ.value,                  # Read fields

            # Common scopes
            cls.ACCOUNT_READ.value,             # Read Atlassian account info
            cls.OFFLINE_ACCESS.value,           # Refresh tokens
        ]

    @classmethod
    def get_confluence_basic(cls) -> List[str]:
        """Get basic Confluence scopes"""
        return [
            cls.CONFLUENCE_CONTENT_READ.value,
            cls.CONFLUENCE_SPACE_READ.value,
            cls.CONFLUENCE_SPACE_READ_ALL.value,
            cls.CONFLUENCE_USER_READ.value,
            cls.ACCOUNT_READ.value,
            cls.OFFLINE_ACCESS.value,
            cls.CONFLUENCE_PAGE_READ.value,
            cls.CONFLUENCE_COMMENT_READ.value,
        ]

    @classmethod
    def get_confluence_read_access(cls) -> List[str]:
        """Get read-only access scopes for Confluence connector"""
        return [
            cls.CONFLUENCE_USER_READ.value,
            cls.CONFLUENCE_USER_READ_CLASSIC.value,
            cls.CONFLUENCE_GROUPS_READ.value,
            cls.CONFLUENCE_SPACE_READ.value,
            cls.CONFLUENCE_SPACE_READ_ALL.value,
            cls.CONFLUENCE_PAGE_READ.value,
            cls.CONFLUENCE_BLOGPOST_READ.value,
            cls.CONFLUENCE_COMMENT_READ.value,
            cls.CONFLUENCE_ATTACHMENT_READ.value,
            cls.CONFLUENCE_PERMISSION_READ.value,
            cls.CONFLUENCE_FOLDER_READ.value,
            cls.OFFLINE_ACCESS.value,
            cls.CONFLUENCE_AUDIT_LOG_READ.value,
            cls.CONFLUENCE_SEARCH.value,
            cls.CONFLUENCE_CONTENT_DETAILS_READ.value,
            cls.CONFLUENCE_GROUPS_READ_CLASSIC.value,
            cls.CONFLUENCE_CONTENT_READ.value,
            cls.ACCOUNT_READ.value,
            cls.CONFLUENCE_CONTENT_PERMISSION.value,
            cls.CONFLUENCE_PROPS_READ.value,
            cls.CONFLUENCE_EMAIL_READ.value,
        ]

    @classmethod
    def get_full_access(cls) -> List[str]:
        """Get all common scopes for full access"""
        return [
            # Jira
            cls.JIRA_WORK_READ.value,
            cls.JIRA_WORK_WRITE.value,
            cls.JIRA_USER_READ.value,
            cls.USER_JIRA_READ.value,
            cls.JIRA_GROUP_READ.value,
            cls.JIRA_AVATAR_READ.value,
            cls.JIRA_CONFIGURATION_MANAGE.value,
            cls.JIRA_PROJECT_READ.value,
            cls.JIRA_PROJECT_WRITE.value,
            cls.JIRA_USER_VIEW.value,
            cls.JIRA_USER_COLUMNS.value,
            # Confluence
            cls.CONFLUENCE_CONTENT_READ.value,
            cls.CONFLUENCE_CONTENT_WRITE.value,
            cls.CONFLUENCE_SPACE_READ.value,
            cls.CONFLUENCE_USER_READ.value,
            cls.CONFLUENCE_SPACE_READ_ALL.value,
            cls.CONFLUENCE_PAGE_READ.value,
            cls.CONFLUENCE_PAGE_WRITE.value,
            cls.CONFLUENCE_CONTENT_DETAILS_READ.value,
            cls.CONFLUENCE_CONTENT_CREATE.value,
            cls.CONFLUENCE_CONTENT_DELETE.value,
            cls.CONFLUENCE_ATTACHMENT_READ.value,
            cls.CONFLUENCE_ATTACHMENT_WRITE.value,
            cls.CONFLUENCE_BLOGPOST_READ.value,
            cls.CONFLUENCE_BLOGPOST_WRITE.value,
            cls.CONFLUENCE_COMMENT_READ.value,
            cls.CONFLUENCE_COMMENT_WRITE.value,
            # Common
            cls.ACCOUNT_READ.value,
            cls.ACCOUNT_EMAIL_READ.value,
            cls.OFFLINE_ACCESS.value
        ]

@dataclass
class AtlassianCloudResource:
    """Represents an Atlassian Cloud resource (site)"""
    id: str
    name: str
    url: str
    scopes: List[str]
    avatar_url: Optional[str] = None

class AtlassianOAuthProvider(OAuthProvider):
    """Atlassian OAuth Provider for Confluence and Jira"""

    # Atlassian OAuth endpoints
    AUTHORIZE_URL = "https://auth.atlassian.com/authorize"
    TOKEN_URL = "https://auth.atlassian.com/oauth/token"
    RESOURCE_URL = "https://api.atlassian.com/oauth/token/accessible-resources"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        configuration_service: ConfigurationService,
        credentials_path: str,
        scopes: Optional[List[str]] = None,
    ) -> None:
        """
        Initialize Atlassian OAuth Provider
        Args:
            client_id: OAuth 2.0 client ID from Atlassian
            client_secret: OAuth 2.0 client secret
            redirect_uri: Callback URL registered with Atlassian
            scopes: List of scopes to request (uses basic scopes if not provided)
            configuration_service: Configuration service instance
        """
        if scopes is None:
            scopes = AtlassianScope.get_full_access()

        config = OAuthConfig(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            authorize_url=self.AUTHORIZE_URL,
            token_url=self.TOKEN_URL,
            scope=" ".join(scopes),
            additional_params={
                "audience": "api.atlassian.com",
                "prompt": "consent"
            }
        )

        super().__init__(config, configuration_service, credentials_path)
        self._accessible_resources: Optional[List[AtlassianCloudResource]] = None

    @staticmethod
    def get_name() -> str:
        return "atlassian"

    def get_provider_name(self) -> str:
        return "atlassian"

    async def get_identity(self, token: OAuthToken) -> Dict[str, Any]:
        session = await self.session
        async with session.get(
            "https://api.atlassian.com/me",
            headers={"Authorization": f"Bearer {token.access_token}"}
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def handle_callback(self, code: str, state: str) -> OAuthToken:
        token = await super().handle_callback(code, state)

        return token


