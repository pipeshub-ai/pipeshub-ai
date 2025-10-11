from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from app.config.key_value_store import KeyValueStore
from app.connectors.core.base.token_service.oauth_service import (
    OAuthConfig,
    OAuthProvider,
    OAuthToken,
)

OAUTH_CONFIG_PATH = "/services/connectors/atlassian/config"
OAUTH_CONFLUENCE_CREDENTIALS_PATH = "/services/connectors/atlassian/confluence/credentials"
OAUTH_JIRA_CREDENTIALS_PATH = "/services/connectors/jira/credentials"
OAUTH_CONFLUENCE_CONFIG_PATH = "/services/connectors/confluence/config"
OAUTH_JIRA_CONFIG_PATH = "/services/connectors/jira/config"


class AtlassianScope(Enum):
    """Common Atlassian OAuth Scopes"""
    # Jira Scopes
    JIRA_WORK_READ = "read:jira-work"
    JIRA_WORK_WRITE = "write:jira-work"
    JIRA_USER_READ = "read:jira-user"
    JIRA_WEBHOOK_READ = "read:webhook:jira"
    JIRA_WEBHOOK_WRITE = "write:webhook:jira"
    JIRA_PROJECT_MANAGE = "manage:jira-project"
    JIRA_CONFIGURATION_MANAGE = "manage:jira-configuration"
    JIRA_DATA_PROVIDER_MANAGE = "manage:jira-data-provider"
    JIRA_PROJECT_READ = "read:jira-project"
    JIRA_PROJECT_WRITE = "write:jira-project"

    # Confluence Scopes
    CONFLUENCE_CONTENT_READ = "read:content.all"
    CONFLUENCE_CONTENT_DETAILS_READ = "read:content-details:confluence"
    CONFLUENCE_CONTENT_WRITE = "write:confluence:content"
    CONFLUENCE_CONTENT_CREATE = "write:confluence-content"
    CONFLUENCE_CONTENT_DELETE = "delete:confluence-content"
    CONFLUENCE_SPACE_READ = "read:confluence-space.summary"
    CONFLUENCE_SPACE_READ_ALL = "read:space:confluence"
    CONFLUENCE_USER_READ = "read:confluence-user"
    CONFLUENCE_GROUPS_READ = "read:confluence-groups"
    CONFLUENCE_PROPS_WRITE = "write:confluence-props"
    CONFLUENCE_PAGE_READ = "read:page:confluence"
    CONFLUENCE_PAGE_WRITE = "write:page:confluence"
    CONFLUENCE_ATTACHMENT_READ = "read:attachment:confluence"
    CONFLUENCE_ATTACHMENT_WRITE = "write:attachment:confluence"
    CONFLUENCE_BLOGPOST_READ = "read:blogpost:confluence"
    CONFLUENCE_BLOGPOST_WRITE = "write:blogpost:confluence"
    CONFLUENCE_COMMENT_READ = "read:comment:confluence"
    CONFLUENCE_COMMENT_WRITE = "write:comment:confluence"

    # Common Scopes
    ACCOUNT_READ = "read:account"
    ACCOUNT_EMAIL_READ = "read:me"
    OFFLINE_ACCESS = "offline_access"

    @classmethod
    def get_jira_basic(cls) -> List[str]:
        """Get basic Jira scopes"""
        return [
            cls.JIRA_WORK_READ.value,
            cls.JIRA_USER_READ.value,
            cls.ACCOUNT_READ.value,
            cls.OFFLINE_ACCESS.value,
            cls.JIRA_PROJECT_READ.value,
            cls.JIRA_PROJECT_WRITE.value,
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
    def get_full_access(cls) -> List[str]:
        """Get all common scopes for full access"""
        return [
            # Jira
            cls.JIRA_WORK_READ.value,
            cls.JIRA_WORK_WRITE.value,
            cls.JIRA_USER_READ.value,
            cls.JIRA_PROJECT_READ.value,
            cls.JIRA_PROJECT_WRITE.value,
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
        key_value_store: KeyValueStore,
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
            key_value_store: Key-value store implementation
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

        super().__init__(config, key_value_store, credentials_path)
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
        # identity = await self.get_identity(token)
        # email = identity.get('email')
        # if not email:
        #     raise Exception("User email not found in Atlassian identity response")
        # user = await self.base_arango_service.get_user_by_email(email)


        return token


