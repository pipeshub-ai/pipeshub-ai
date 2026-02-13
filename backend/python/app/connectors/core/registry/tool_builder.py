"""
Tool Builder and Registry
"""

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Type,
    Union,
)

from pydantic import BaseModel

from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    OAuthConfig,
)
from app.connectors.core.registry.auth_utils import (
    auth_field_to_dict,
    auto_add_oauth_fields,
)
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.oauth_config_registry import get_oauth_config_registry
from app.connectors.core.registry.types import AuthField, DocumentationLink


class ToolsetCategory(str, Enum):
    """Categories for toolsets"""
    APP = "app"  # Application-specific toolsets (Jira, Slack, etc.)
    FILE = "file"  # File-related toolsets
    FILE_STORAGE = "file_storage"  # File storage toolsets (Drive, Dropbox, etc.)
    WEB_SEARCH = "web_search"  # Web search toolsets
    SEARCH = "search"  # Search toolsets (internal knowledge, etc.)
    RESEARCH = "research"  # Research-related toolsets
    UTILITY = "utility"  # Utility toolsets
    COMMUNICATION = "communication"  # Communication toolsets (Slack, Gmail, etc.)
    PRODUCTIVITY = "productivity"  # Productivity toolsets
    DATABASE = "database"  # Database toolsets
    CALENDAR = "calendar"  # Calendar toolsets
    PROJECT_MANAGEMENT = "project_management"  # Project management toolsets (Jira, etc.)
    DOCUMENTATION = "documentation"  # Documentation toolsets (Confluence, etc.)


@dataclass
class ToolDefinition:
    """Definition of a single tool within a toolset"""
    name: str
    description: str
    function: Optional[Callable] = None
    args_schema: Optional[Type[BaseModel]] = None  # NEW: Pydantic schema for validation
    parameters: List[Dict[str, Any]] = field(default_factory=list)  # DEPRECATED: Use args_schema instead
    returns: Optional[str] = None
    examples: List[Dict] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict, converting schema to parameters for frontend compatibility"""
        result = {
            "name": self.name,
            "description": self.description,
            "parameters": self._schema_to_parameters() if self.args_schema else self.parameters,
            "returns": self.returns,
            "examples": self.examples,
            "tags": self.tags
        }
        return result

    def _schema_to_parameters(self) -> List[Dict[str, Any]]:
        """Convert Pydantic schema to parameter dict format for frontend API compatibility"""
        if not self.args_schema:
            return self.parameters

        try:
            from typing import get_args, get_origin

            parameters = []
            schema_fields = self.args_schema.model_fields

            for field_name, field_info in schema_fields.items():
                # Get field description
                description = field_info.description or f"Parameter {field_name}"

                # Determine type - handle Optional, Union, etc.
                field_type = field_info.annotation
                param_type = "string"  # default

                # Handle Optional types (Union[T, None])
                origin = get_origin(field_type) if hasattr(field_type, '__origin__') or hasattr(field_type, '__args__') else None
                if origin is Union:
                    args = get_args(field_type)
                    # Filter out None type
                    non_none_args = [arg for arg in args if arg is not type(None)]
                    if non_none_args:
                        field_type = non_none_args[0]
                        origin = get_origin(field_type) if hasattr(field_type, '__origin__') else None

                # Determine base type
                if field_type is int:
                    param_type = "integer"
                elif field_type is float:
                    param_type = "number"
                elif field_type is bool:
                    param_type = "boolean"
                elif origin is list:
                    param_type = "array"
                elif origin is dict:
                    param_type = "object"

                # Check if required (not Optional and no default)
                required = field_info.is_required() and field_info.default is ...

                param_dict = {
                    "name": field_name,
                    "type": param_type,
                    "description": description,
                    "required": required
                }

                # Add default if present
                if field_info.default is not ...:
                    param_dict["default"] = field_info.default

                parameters.append(param_dict)

            return parameters
        except Exception:
            # Fallback to legacy parameters if conversion fails
            return self.parameters


class ToolsetConfigBuilder:
    """Builder for creating toolset configurations"""

    def __init__(self) -> None:
        self._reset()

    def _reset(self) -> 'ToolsetConfigBuilder':
        """Reset the builder to default state"""
        self.config = {
            "iconPath": "/assets/icons/toolsets/default.svg",
            "documentationLinks": [],
            "auth": {
                "supportedAuthTypes": ["API_TOKEN"],  # List of supported auth types (user selects one during creation)
                "displayRedirectUri": False,
                "redirectUri": "",
                "schema": {"fields": []},  # Default schema (for backward compatibility)
                "schemas": {},  # Per-auth-type schemas: {"API_TOKEN": {"fields": []}, "OAUTH": {"fields": []}}
                "values": {},
                "customFields": [],
                "customValues": {},
                "conditionalDisplay": {}
            },
            "tools": []  # List of tool definitions
        }
        return self

    def with_icon(self, icon_path: str) -> 'ToolsetConfigBuilder':
        """Set the icon path"""
        self.config["iconPath"] = icon_path
        return self

    def add_documentation_link(self, link: DocumentationLink) -> 'ToolsetConfigBuilder':
        """Add documentation link"""
        self.config["documentationLinks"].append({
            "title": link.title,
            "url": link.url,
            "type": link.doc_type
        })
        return self

    def with_supported_auth_types(self, auth_types: Union[str, List[str]]) -> 'ToolsetConfigBuilder':
        """Set supported authentication types - user will select one during toolset creation"""
        if isinstance(auth_types, str):
            self.config["auth"]["supportedAuthTypes"] = [auth_types]
        elif isinstance(auth_types, list):
            if not auth_types:
                raise ValueError("auth_types list cannot be empty")
            self.config["auth"]["supportedAuthTypes"] = auth_types
        else:
            raise ValueError(f"auth_types must be str or List[str], got {type(auth_types)}")
        return self

    def add_supported_auth_type(self, auth_type: str) -> 'ToolsetConfigBuilder':
        """Add an additional supported authentication type"""
        if "supportedAuthTypes" not in self.config["auth"]:
            self.config["auth"]["supportedAuthTypes"] = ["API_TOKEN"]
        if auth_type not in self.config["auth"]["supportedAuthTypes"]:
            self.config["auth"]["supportedAuthTypes"].append(auth_type)
        return self

    def with_redirect_uri(self, redirect_uri: str, display: bool = False) -> 'ToolsetConfigBuilder':
        """Set redirect URI configuration (for OAuth toolsets)"""
        self.config["auth"]["redirectUri"] = redirect_uri
        self.config["auth"]["displayRedirectUri"] = display
        return self


    def add_auth_field(self, field: AuthField, auth_type: Optional[str] = None) -> 'ToolsetConfigBuilder':
        """
        Add an authentication field.

        If auth_type is provided, adds to that specific auth type's schema.
        Otherwise, adds to the default schema.
        """
        if auth_type:
            if "schemas" not in self.config["auth"]:
                self.config["auth"]["schemas"] = {}
            if auth_type not in self.config["auth"]["schemas"]:
                self.config["auth"]["schemas"][auth_type] = {"fields": []}
            target_schema = self.config["auth"]["schemas"][auth_type]
        else:
            target_schema = self.config["auth"]["schema"]

        field_config = auth_field_to_dict(field)
        target_schema["fields"].append(field_config)
        return self

    def with_oauth_config(
        self,
        oauth_config: OAuthConfig,
        auth_type: Optional[str] = None,
        auto_add_common_fields: bool = True
    ) -> 'ToolsetConfigBuilder':
        """
        Use OAuth configuration from registry (avoids duplication).

        This method:
        1. Registers the OAuth config
        2. Populates OAuth URLs, scopes, and redirect URI
        3. Automatically adds ALL OAuth fields from the config (clientId, clientSecret, tenantId, etc.)

        All OAuth fields should be defined in the OAuthConfig.auth_fields list.
        This ensures a single source of truth - define fields once in OAuth config, use everywhere.

        Args:
            oauth_config: OAuthConfig instance with all fields defined
            auth_type: Optional specific auth type (defaults to primary auth type)
            auto_add_common_fields: If True, automatically adds all fields from oauth_config.auth_fields
        """
        # Note: OAuth config will be registered during build_decorator() to ensure
        # connector/toolset name is finalized. We just store it here for now.

        # Determine auth type
        if not auth_type:
            auth_type = self.config["auth"].get("type", "OAUTH")

        # Initialize schemas structure if needed
        if "schemas" not in self.config["auth"]:
            self.config["auth"]["schemas"] = {}
        if auth_type not in self.config["auth"]["schemas"]:
            self.config["auth"]["schemas"][auth_type] = {"fields": []}

        # Add redirect URI and displayRedirectUri to the auth type's schema (needed for form display)
        if oauth_config.redirect_uri:
            self.config["auth"]["schemas"][auth_type]["redirectUri"] = oauth_config.redirect_uri
            self.config["auth"]["schemas"][auth_type]["displayRedirectUri"] = True
            # Also set at top level for backward compatibility (will be removed in schema response)
            self.config["auth"]["redirectUri"] = oauth_config.redirect_uri
            self.config["auth"]["displayRedirectUri"] = True

        # Populate OAuth URLs and scopes at top level (for backward compatibility, will be cleaned in schema response)
        self.config["auth"]["authorizeUrl"] = oauth_config.authorize_url
        self.config["auth"]["tokenUrl"] = oauth_config.token_url

        # Use all scopes from the config (combine all scope types)
        all_scopes = oauth_config.scopes.get_all_scopes()
        self.config["auth"]["scopes"] = all_scopes

        # Store in oauthConfigs for the specific auth type (for backward compatibility, will be cleaned in schema response)
        if "oauthConfigs" not in self.config["auth"]:
            self.config["auth"]["oauthConfigs"] = {}
        self.config["auth"]["oauthConfigs"][auth_type] = {
            "authorizeUrl": oauth_config.authorize_url,
            "tokenUrl": oauth_config.token_url,
            "scopes": all_scopes
        }

        # Store reference to OAuth config for later use (internal, will be removed in schema response)
        if "_oauth_configs" not in self.config:
            self.config["_oauth_configs"] = {}
        self.config["_oauth_configs"][auth_type] = oauth_config

        # Auto-add all OAuth fields from config if requested and not already present
        if auto_add_common_fields:
            auto_add_oauth_fields(self.config, oauth_config, auth_type)

        return self

    def with_oauth_urls(
        self,
        authorize_url: str,
        token_url: str,
        scopes: Optional[List[str]] = None
    ) -> 'ToolsetConfigBuilder':
        """
        Set OAuth URLs and scopes for OAuth toolsets.

        DEPRECATED: Prefer using with_oauth_config() with OAuthConfig from registry
        to avoid duplication and enable scope-based configuration.
        """
        self.config["auth"]["authorizeUrl"] = authorize_url
        self.config["auth"]["tokenUrl"] = token_url
        if scopes:
            self.config["auth"]["scopes"] = scopes
        return self

    def add_tool(self, tool: ToolDefinition) -> 'ToolsetConfigBuilder':
        """Add a tool to the toolset"""
        tool_dict = {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
            "returns": tool.returns,
            "examples": tool.examples,
            "tags": tool.tags
        }
        self.config["tools"].append(tool_dict)
        return self

    def add_tools(self, tools: List[ToolDefinition]) -> 'ToolsetConfigBuilder':
        """Add multiple tools to the toolset"""
        for tool in tools:
            self.add_tool(tool)
        return self

    def build(self) -> Dict[str, Any]:
        """Build and return the final configuration"""
        result = deepcopy(self.config)
        self._reset()
        return result


class ToolsetBuilder:
    """Main builder for creating toolsets with the decorator"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.app_group = ""
        self.supported_auth_types: List[str] = ["API_TOKEN"]  # Supported auth types (user selects one during creation)
        self.description = ""
        self.category = ToolsetCategory.APP
        self.config_builder = ToolsetConfigBuilder()
        self.tools: List[ToolDefinition] = []
        self._oauth_configs: Dict[str, OAuthConfig] = {}  # Store OAuth configs for auto-registration
        self.is_internal: bool = False  # Internal toolsets are backend-only, not sent to frontend

    def in_group(self, app_group: str) -> 'ToolsetBuilder':
        """Set the app group"""
        self.app_group = app_group
        return self

    def with_auth(self, auth_builders: List[AuthBuilder]) -> 'ToolsetBuilder':
        """
        Configure authentication types using AuthBuilder pattern.

        This is the preferred way to configure auth - it allows specifying
        auth types and their fields in one place for better readability.

        Args:
            auth_builders: List of AuthBuilder instances, each configuring one auth type

        Example:
            with_auth([
                AuthBuilder.type("OAUTH").oauth_config(oauth_config),
                AuthBuilder.type("API_TOKEN").fields([api_token_field])
            ])
        """
        if not auth_builders:
            raise ValueError("auth_builders list cannot be empty")

        # Extract supported auth types and configure
        supported_auth_types = [builder.get_auth_type() for builder in auth_builders]
        self.supported_auth_types = supported_auth_types

        # Update config builder with supported auth types
        self.config_builder.with_supported_auth_types(supported_auth_types)

        # Configure each auth type with its fields/oauth config
        for builder in auth_builders:
            auth_type = builder.get_auth_type()
            oauth_config = builder.get_oauth_config()
            fields = builder.get_fields()

            # If OAuth config is provided, use it
            if oauth_config:
                self._oauth_configs[auth_type] = oauth_config
                self.config_builder.with_oauth_config(oauth_config, auth_type)
            else:
                # Otherwise, add fields manually
                for field in fields:
                    self.config_builder.add_auth_field(field, auth_type)

        return self

    def with_supported_auth_types(self, auth_types: Union[str, List[str]]) -> 'ToolsetBuilder':
        """
        Set the supported authentication types - user will select one during toolset creation.

        DEPRECATED: Prefer using with_auth() with AuthBuilder for better readability.
        """
        if isinstance(auth_types, str):
            self.supported_auth_types = [auth_types]
        elif isinstance(auth_types, list):
            if not auth_types:
                raise ValueError("auth_types list cannot be empty")
            self.supported_auth_types = auth_types
        else:
            raise ValueError(f"auth_types must be str or List[str], got {type(auth_types)}")

        self.config_builder.with_supported_auth_types(auth_types)
        return self

    def add_supported_auth_type(self, auth_type: str) -> 'ToolsetBuilder':
        """Add an additional supported authentication type"""
        if auth_type not in self.supported_auth_types:
            self.supported_auth_types.append(auth_type)
        self.config_builder.add_supported_auth_type(auth_type)
        return self

    def with_description(self, description: str) -> 'ToolsetBuilder':
        """Set the toolset description"""
        self.description = description
        return self

    def with_category(self, category: ToolsetCategory) -> 'ToolsetBuilder':
        """Set the toolset category"""
        self.category = category
        return self

    def as_internal(self) -> 'ToolsetBuilder':
        """Mark this toolset as internal (backend-only, not sent to frontend)"""
        self.is_internal = True
        return self

    def configure(
        self,
        config_func: Callable[[ToolsetConfigBuilder], ToolsetConfigBuilder]
    ) -> 'ToolsetBuilder':
        """Configure the toolset using a configuration function"""
        self.config_builder = config_func(self.config_builder)
        return self

    def with_tools(self, tools: List[ToolDefinition]) -> 'ToolsetBuilder':
        """
        Set the tools for this toolset (optional).

        DEPRECATED: Tools will be auto-discovered from @tool decorators if not provided.
        This method is kept for backward compatibility during migration.
        """
        self.tools = tools
        # Only add to config if provided (for backward compat)
        if tools:
            for tool in tools:
                self.config_builder.add_tool(tool)
        return self

    def with_oauth_config(
        self,
        oauth_config: OAuthConfig,
        auth_type: Optional[str] = None,
        auto_add_common_fields: bool = True
    ) -> 'ToolsetBuilder':
        """
        Use OAuth configuration from registry (avoids duplication).

        This registers the OAuth config and configures the builder to use it.
        Common OAuth fields (clientId, clientSecret) are automatically added.

        Args:
            oauth_config: OAuthConfig instance
            auth_type: Optional specific auth type (defaults to primary)
            auto_add_common_fields: If True, automatically adds clientId/clientSecret fields
        """
        if not auth_type:
            # Use first supported auth type as default if not specified
            auth_type = self.supported_auth_types[0] if self.supported_auth_types else "API_TOKEN"

        # Store for auto-registration during build
        self._oauth_configs[auth_type] = oauth_config

        # Configure the config builder (will auto-add common fields)
        self.config_builder.with_oauth_config(oauth_config, auth_type, auto_add_common_fields)

        return self

    def build_decorator(self) -> Callable[[Type], Type]:
        """Build the final toolset decorator"""
        from app.agents.registry.toolset_registry import Toolset

        config = self.config_builder.build()

        # Auto-register all OAuth configs with final toolset name
        oauth_registry = get_oauth_config_registry()
        for auth_type, oauth_config in self._oauth_configs.items():
            # Ensure connector/toolset name matches final builder name
            if oauth_config.connector_name != self.name:
                # Remove old registration if name changed
                old_config = oauth_registry.get_config(oauth_config.connector_name)
                if old_config == oauth_config:
                    # Only remove if it's the same object (not just same name)
                    del oauth_registry._configs[oauth_config.connector_name]
                oauth_config.connector_name = self.name

            # Auto-populate metadata from toolset builder if not already set
            # This makes OAuth configs self-contained with metadata
            # Note: app_description should be OAuth-specific (about the OAuth app), not toolset description
            if not oauth_config.icon_path or oauth_config.icon_path == "/assets/icons/connectors/default.svg":
                oauth_config.icon_path = config.get("iconPath", oauth_config.icon_path)
            if not oauth_config.app_group:
                oauth_config.app_group = self.app_group
            # app_description is intentionally NOT auto-populated from toolset description
            # It should describe the OAuth app itself, not what the toolset does
            # If not provided in oauth() method, it remains empty or uses a generic OAuth description
            if not oauth_config.app_description:
                # Generate OAuth-specific description if not provided
                oauth_config.app_description = f"OAuth application for {self.name} integration"
            if not oauth_config.app_categories:
                # Use category as a category for toolsets
                oauth_config.app_categories = [self.category.value] if hasattr(self.category, 'value') else [str(self.category)] if self.category else []

            # Auto-populate documentation links from toolset config if not already set
            # This allows OAuth configs to have setup documentation links
            if not oauth_config.documentation_links and config.get("documentationLinks"):
                from app.connectors.core.registry.types import DocumentationLink
                oauth_config.documentation_links = [
                    DocumentationLink(
                        title=link.get("title", ""),
                        url=link.get("url", ""),
                        doc_type=link.get("type", "")
                    )
                    for link in config.get("documentationLinks", [])
                ]

            # Register with final name (overwrites if already registered - allows sharing between connector/toolset)
            oauth_registry.register(oauth_config)

        # Validate OAuth requirements for all OAuth supported auth types
        for auth_type in self.supported_auth_types:
            if auth_type and auth_type.upper() == "OAUTH":
                self._validate_oauth_requirements(config, auth_type)

        # Validate that required auth fields are present
        self._validate_required_auth_fields(config)

        return Toolset(
            name=self.name,
            app_group=self.app_group,
            supported_auth_types=self.supported_auth_types,  # All supported types (user selects one during creation)
            description=self.description,
            category=self.category,
            config=config,
            tools=self.tools,
            internal=self.is_internal
        )

    def _validate_oauth_requirements(self, config: Dict[str, Any], auth_type: str = "OAUTH") -> None:
        """Ensure required OAuth infrastructure is provided for OAuth toolsets."""
        auth_config = config.get("auth", {})
        missing_items = []

        oauth_configs = auth_config.get("oauthConfigs", {})
        if auth_type in oauth_configs:
            oauth_config = oauth_configs[auth_type]
            required_urls = ["authorizeUrl", "tokenUrl"]
            for url_key in required_urls:
                if not oauth_config.get(url_key):
                    missing_items.append(f"{auth_type}.{url_key}")
            # Scopes are optional - some OAuth providers (like Notion) don't use explicit scopes
            scopes = oauth_config.get("scopes", [])
            if scopes is not None and not isinstance(scopes, list):
                missing_items.append(f"{auth_type}.scopes (must be a list)")
        else:
            required_urls = ["authorizeUrl", "tokenUrl"]
            for url_key in required_urls:
                if not auth_config.get(url_key):
                    missing_items.append(url_key)
            # Scopes are optional - some OAuth providers (like Notion) don't use explicit scopes
            scopes = auth_config.get("scopes")
            if scopes is not None and not isinstance(scopes, list):
                missing_items.append("scopes (must be a list)")

        redirect_uri = auth_config.get("redirectUri")
        if not redirect_uri:
            missing_items.append("redirectUri")

        if missing_items:
            details = ", ".join(missing_items)
            raise ValueError(
                f"OAuth configuration incomplete for toolset '{self.name}' (auth_type: {auth_type}): missing {details}"
            )

    def _validate_required_auth_fields(self, config: Dict[str, Any]) -> None:
        """Validate that required auth fields are properly defined."""
        auth_config = config.get("auth", {})
        schemas = auth_config.get("schemas", {})
        default_schema = auth_config.get("schema", {})

        for auth_type in self.supported_auth_types:
            # Skip validation for "NONE" auth type (internal toolsets don't need auth fields)
            if auth_type.upper() == "NONE":
                continue

            if auth_type in schemas:
                schema_fields = schemas[auth_type].get("fields", [])
            else:
                schema_fields = default_schema.get("fields", [])

            for i, field_item in enumerate(schema_fields):
                if isinstance(field_item, dict):
                    if field_item.get("required", False) and not field_item.get("name"):
                        raise ValueError(
                            f"Toolset '{self.name}' (auth_type: {auth_type}): "
                            f"Required field at index {i} is missing a 'name'"
                        )


# Common field definitions for toolsets (reuse from connector_builder)
class ToolsetCommonFields:
    """Reusable field definitions for toolsets"""

    @staticmethod
    def api_token(token_name: str = "API Token", placeholder: str = "", field_name: Optional[str] = None) -> AuthField:
        """Standard API token field

        Args:
            token_name: Display name for the token field
            placeholder: Placeholder text for the input field
            field_name: Optional custom field name (defaults to "apiToken")
        """
        return CommonFields.api_token(token_name, placeholder, field_name)

    @staticmethod
    def bearer_token(token_name: str = "Bearer Token", placeholder: str = "") -> AuthField:
        """Standard Bearer token field"""
        return CommonFields.bearer_token(token_name, placeholder)

    @staticmethod
    def client_id(provider: str = "OAuth Provider") -> AuthField:
        """Standard OAuth client ID field"""
        return CommonFields.client_id(provider)

    @staticmethod
    def client_secret(provider: str = "OAuth Provider") -> AuthField:
        """Standard OAuth client secret field"""
        return CommonFields.client_secret(provider)

