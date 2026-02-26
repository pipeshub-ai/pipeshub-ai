"""
Auth Builder for configuring authentication types and fields.

This module provides:
- OAuth configuration classes (OAuthConfig, OAuthScopeConfig, OAuthScopeType)
- AuthBuilder for creating authentication configurations
- Uniform fluent API for all authentication types

Note: OAuthConfigRegistry is in a separate file (oauth_config_registry.py)
for better separation of concerns - registry infrastructure vs configuration classes.

Usage:
    with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="ExampleConnector",
            authorize_url="https://example.com/oauth/authorize",
            token_url="https://example.com/oauth/token",
            redirect_uri="connectors/oauth/callback/ExampleConnector",
            scopes=OAuthScopeConfig(...),
            fields=[...]
        ),
        AuthBuilder.type(AuthType.API_TOKEN).fields([api_token_field])
    ])
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from app.connectors.core.registry.types import AuthField, DocumentationLink

# ============================================================================
# OAuth Configuration Classes
# ============================================================================

class OAuthScopeType(str, Enum):
    """OAuth scope types for different use cases"""
    PERSONAL_SYNC = "personal_sync"
    TEAM_SYNC = "team_sync"
    AGENT = "agent"


@dataclass
class OAuthScopeConfig:
    """Configuration for OAuth scopes by use case"""
    personal_sync: List[str] = field(default_factory=list)
    team_sync: List[str] = field(default_factory=list)
    agent: List[str] = field(default_factory=list)

    def get_scopes_for_type(self, scope_type: OAuthScopeType) -> List[str]:
        """Get scopes for a specific scope type"""
        if scope_type == OAuthScopeType.PERSONAL_SYNC:
            return self.personal_sync
        elif scope_type == OAuthScopeType.TEAM_SYNC:
            return self.team_sync
        elif scope_type == OAuthScopeType.AGENT:
            return self.agent
        return []

    def get_all_scopes(self) -> List[str]:
        """Get all unique scopes across all types"""
        all_scopes = set(self.personal_sync + self.team_sync + self.agent)
        return sorted(list(all_scopes))

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "personal_sync": self.personal_sync,
            "team_sync": self.team_sync,
            "agent": self.agent
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OAuthScopeConfig':
        """Create from dictionary"""
        return cls(
            personal_sync=data.get("personal_sync", []),
            team_sync=data.get("team_sync", []),
            agent=data.get("agent", [])
        )


@dataclass
class OAuthConfig:
    """
    Complete OAuth configuration for a connector or toolset.

    This is the single source of truth for OAuth configuration including:
    - OAuth infrastructure (URLs, scopes, redirect URI)
    - All auth fields (clientId, clientSecret, tenantId, etc.)
    - Display metadata (iconPath, appGroup, appDescription, appCategories)
    - Documentation links for OAuth setup

    OAuth configs are generic and can be used for both connectors and toolsets.
    Metadata is stored directly in the config to avoid dependency on connector/toolset registries.

    When used in builders, all fields are automatically added.
    """
    connector_name: str
    authorize_url: str
    token_url: str
    redirect_uri: str
    scopes: OAuthScopeConfig = field(default_factory=OAuthScopeConfig)
    auth_fields: List[AuthField] = field(default_factory=list)
    token_access_type: Optional[str] = None
    additional_params: Dict[str, Any] = field(default_factory=dict)
    scope_parameter_name: str = "scope"  # Parameter name for scopes in authorization URL (e.g., "scope", "user_scope", "resource")
    token_response_path: Optional[str] = None  # Optional: path to extract token from nested response (e.g., "authed_user" for Slack)
    # Display metadata - stored in OAuth config to make it self-contained
    icon_path: str = "/assets/icons/connectors/default.svg"
    app_group: str = ""
    app_description: str = ""
    app_categories: List[str] = field(default_factory=list)
    documentation_links: List[DocumentationLink] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "connector_name": self.connector_name,
            "authorize_url": self.authorize_url,
            "token_url": self.token_url,
            "redirect_uri": self.redirect_uri,
            "scopes": self.scopes.to_dict(),
            "auth_fields": [
                {
                    "name": field.name,
                    "display_name": field.display_name,
                    "field_type": field.field_type,
                    "placeholder": field.placeholder,
                    "description": field.description,
                    "required": field.required,
                    "default_value": field.default_value,
                    "min_length": field.min_length,
                    "max_length": field.max_length,
                    "is_secret": field.is_secret
                }
                for field in self.auth_fields
            ],
            "token_access_type": self.token_access_type,
            "additional_params": self.additional_params,
            "scope_parameter_name": self.scope_parameter_name,
            "token_response_path": self.token_response_path,
            "icon_path": self.icon_path,
            "app_group": self.app_group,
            "app_description": self.app_description,
            "app_categories": self.app_categories,
            "documentation_links": [
                {
                    "title": link.title,
                    "url": link.url,
                    "type": link.doc_type
                }
                for link in self.documentation_links
            ]
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OAuthConfig':
        """Create from dictionary"""
        auth_fields = []
        for field_data in data.get("auth_fields", []):
            auth_fields.append(AuthField(
                name=field_data["name"],
                display_name=field_data["display_name"],
                field_type=field_data.get("field_type", "TEXT"),
                placeholder=field_data.get("placeholder", ""),
                description=field_data.get("description", ""),
                required=field_data.get("required", True),
                default_value=field_data.get("default_value", ""),
                min_length=field_data.get("min_length", 1),
                max_length=field_data.get("max_length", 1000),
                is_secret=field_data.get("is_secret", False)
            ))

        documentation_links = []
        for link_data in data.get("documentation_links", []):
            documentation_links.append(DocumentationLink(
                title=link_data.get("title", ""),
                url=link_data.get("url", ""),
                doc_type=link_data.get("type", link_data.get("doc_type", ""))
            ))

        return cls(
            connector_name=data["connector_name"],
            authorize_url=data["authorize_url"],
            token_url=data["token_url"],
            redirect_uri=data.get("redirect_uri", ""),
            scopes=OAuthScopeConfig.from_dict(data.get("scopes", {})),
            auth_fields=auth_fields,
            token_access_type=data.get("token_access_type"),
            additional_params=data.get("additional_params", {}),
            scope_parameter_name=data.get("scope_parameter_name", "scope"),
            token_response_path=data.get("token_response_path") or data.get("tokenResponsePath"),
            icon_path=data.get("icon_path", "/assets/icons/connectors/default.svg"),
            app_group=data.get("app_group", ""),
            app_description=data.get("app_description", ""),
            app_categories=data.get("app_categories", []),
            documentation_links=documentation_links
        )


# ============================================================================
# Auth Builder Classes
# ============================================================================

class AuthType:
    """Auth type constants"""
    OAUTH = "OAUTH"
    API_TOKEN = "API_TOKEN"
    BEARER_TOKEN = "BEARER_TOKEN"
    BASIC_AUTH = "BASIC_AUTH"
    ACCESS_KEY = "ACCESS_KEY"
    ACCOUNT_KEY = "ACCOUNT_KEY"
    CONNECTION_STRING = "CONNECTION_STRING"
    OAUTH_ADMIN_CONSENT = "OAUTH_ADMIN_CONSENT"
    CUSTOM = "CUSTOM"


class AuthBuilder:
    """
    Builder for configuring a single authentication type with its fields.

    Provides a uniform API for all auth types - OAuth config is created and
    registered within the builder automatically.

    Usage:
        # OAuth - create config within builder (auto-registered)
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="ExampleConnector",
            authorize_url="https://example.com/oauth/authorize",
            token_url="https://example.com/oauth/token",
            redirect_uri="connectors/oauth/callback/ExampleConnector",
            scopes=OAuthScopeConfig(...),
            fields=[client_id_field, client_secret_field]
        )

        # API Token - just specify fields
        AuthBuilder.type(AuthType.API_TOKEN).fields([api_token_field])

        # OAuth with existing config (backward compatibility)
        AuthBuilder.type(AuthType.OAUTH).oauth_config(oauth_config)
    """

    def __init__(self, auth_type: str) -> None:
        self.auth_type = auth_type.upper()
        self._fields: List[AuthField] = []
        self._oauth_config: Optional[OAuthConfig] = None

    @classmethod
    def type(cls, auth_type: Union[str, AuthType]) -> 'AuthBuilder':
        """
        Create a new AuthBuilder for a specific auth type.

        Args:
            auth_type: Auth type string or AuthType constant
        """
        auth_type_str = auth_type.value if hasattr(auth_type, 'value') else str(auth_type)
        return cls(auth_type_str)

    def oauth(
        self,
        connector_name: str,
        authorize_url: str,
        token_url: str,
        redirect_uri: str,
        scopes: Optional[OAuthScopeConfig] = None,
        fields: Optional[List[AuthField]] = None,
        token_access_type: Optional[str] = None,
        additional_params: Optional[Dict[str, Any]] = None,
        scope_parameter_name: Optional[str] = None,
        token_response_path: Optional[str] = None,
        icon_path: Optional[str] = None,
        app_group: Optional[str] = None,
        app_description: Optional[str] = None,
        app_categories: Optional[List[str]] = None,
        documentation_links: Optional[List[DocumentationLink]] = None
    ) -> 'AuthBuilder':
        """
        Configure OAuth authentication - creates and registers OAuth config internally.

        This is the preferred way to configure OAuth - all OAuth configuration
        is done in one place within the builder.

        Args:
            connector_name: Name of the connector/toolset
            authorize_url: OAuth authorization URL
            token_url: OAuth token URL
            redirect_uri: OAuth redirect URI
            scopes: OAuth scope configuration (defaults to empty)
            fields: List of auth fields (defaults to empty)
            token_access_type: Optional token access type (e.g., "offline")
            additional_params: Optional additional OAuth parameters
            scope_parameter_name: Optional parameter name for scopes in authorization URL
                                  (defaults to "scope", use "user_scope" for Slack user scopes)
            token_response_path: Optional path to extract token from nested response
                                 (e.g., "authed_user" for Slack OAuth v2 user tokens)
            icon_path: Optional icon path for display
            app_group: Optional app group name
            app_description: Optional app description
            app_categories: Optional list of app categories
            documentation_links: Optional list of documentation links for OAuth setup
        """
        # Create OAuth config within builder
        # Note: Registration will happen in build_decorator() with final connector name
        self._oauth_config = OAuthConfig(
            connector_name=connector_name,
            authorize_url=authorize_url,
            token_url=token_url,
            redirect_uri=redirect_uri,
            scopes=scopes or OAuthScopeConfig(),
            auth_fields=fields or [],
            token_access_type=token_access_type,
            additional_params=additional_params or {},
            scope_parameter_name=scope_parameter_name or "scope",
            token_response_path=token_response_path,
            icon_path=icon_path or "/assets/icons/connectors/default.svg",
            app_group=app_group or "",
            app_description=app_description or "",
            app_categories=app_categories or [],
            documentation_links=documentation_links or []
        )
        return self

    def oauth_config(self, oauth_config: OAuthConfig) -> 'AuthBuilder':
        """
        Use existing OAuth config for this auth type (backward compatibility).
        All fields from oauth_config.auth_fields will be used.

        Args:
            oauth_config: OAuthConfig instance with fields defined
        """
        self._oauth_config = oauth_config
        return self

    def fields(self, fields: List[AuthField]) -> 'AuthBuilder':
        """
        Specify auth fields for this auth type.

        For OAuth, fields can be specified here or in oauth() method.
        If both are specified, fields from oauth() take precedence.

        Args:
            fields: List of AuthField instances
        """
        self._fields = fields
        return self

    def add_field(self, field: AuthField) -> 'AuthBuilder':
        """
        Add a single auth field.

        Args:
            field: AuthField instance
        """
        self._fields.append(field)
        return self

    def build(self) -> Dict[str, Any]:
        """
        Build the auth configuration for this auth type.

        Returns:
            Dictionary with auth_type, fields, and optional oauth_config
        """
        result: Dict[str, Any] = {
            "auth_type": self.auth_type,
            "fields": self._fields.copy()
        }

        if self._oauth_config:
            result["oauth_config"] = self._oauth_config
            # If oauth_config is provided, use its fields (they take precedence)
            if self._oauth_config.auth_fields:
                result["fields"] = self._oauth_config.auth_fields.copy()

        return result

    def get_auth_type(self) -> str:
        """Get the auth type"""
        return self.auth_type

    def get_fields(self) -> List[AuthField]:
        """Get all fields (from oauth_config if available, otherwise manual fields)"""
        if self._oauth_config and self._oauth_config.auth_fields:
            return self._oauth_config.auth_fields
        return self._fields.copy()

    def get_oauth_config(self) -> Optional[OAuthConfig]:
        """Get the OAuth config if set"""
        return self._oauth_config

