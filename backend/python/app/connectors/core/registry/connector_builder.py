from copy import deepcopy
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Type, Union

from app.config.constants.arangodb import ExtensionTypes
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    OAuthConfig,
)
from app.connectors.core.registry.auth_utils import (
    auth_field_to_dict,
    auto_add_oauth_fields,
)
from app.connectors.core.registry.filters import (
    FilterCategory,
    FilterField,
    FilterOption,
    FilterType,
    MultiselectOperator,
    OptionSourceType,
)
from app.connectors.core.registry.oauth_config_registry import get_oauth_config_registry
from app.connectors.core.registry.types import AuthField, CustomField, DocumentationLink


class ConnectorScope(str, Enum):
    """Connector scope types."""
    PERSONAL = "personal"
    TEAM = "team"


class ConnectorConfigBuilder:
    """Generic builder for creating connector configurations"""

    def __init__(self) -> None:
        self._reset()

    def _reset(self) -> 'ConnectorConfigBuilder':
        """Reset the builder to default state"""
        self.config = {
            "iconPath": "/assets/icons/connectors/default.svg",
            "supportsRealtime": False,
            "supportsSync": True,
            "supportsAgent": True,
            "documentationLinks": [],
            "auth": {
                "supportedAuthTypes": ["OAUTH"],
                "schemas": {},  # Per-auth-type schemas: {"OAUTH": {"fields": []}, "API_TOKEN": {"fields": []}}
                "oauthConfigs": {},  # OAuth URLs and scopes per auth type (temporary, used during OAuth flow)
                "values": {},
                "customFields": [],
                "customValues": {},
                "conditionalDisplay": {}
            },
            "sync": {
                "supportedStrategies": ["MANUAL"],
                "selectedStrategy": "MANUAL",
                "webhookConfig": {
                    "supported": False,
                    "webhookUrl": "",
                    "events": [],
                    "verificationToken": "",
                    "secretKey": ""
                },
                "scheduledConfig": {
                    "intervalMinutes": 60,
                    "cronExpression": "",
                    "timezone": "UTC",
                    "startTime": 0,
                    "nextTime": 0,
                    "endTime": 0,
                    "maxRepetitions": 0,
                    "repetitionCount": 0
                },
                "realtimeConfig": {
                    "supported": False,
                    "connectionType": "WEBSOCKET"
                },
                "customFields": [],
                "customValues": {},
                "values": {}
            },
            "filters": {
                "sync": {
                    "schema": {"fields": []},
                    "values": {}
                },
                "indexing": {
                    "schema": {"fields": []},
                    "values": {}
                }
            }
        }
        return self

    def with_icon(self, icon_path: str) -> 'ConnectorConfigBuilder':
        """Set the icon path"""
        self.config["iconPath"] = icon_path
        return self

    def with_realtime_support(self, supported: bool = True, connection_type: str = "WEBSOCKET") -> 'ConnectorConfigBuilder':
        """Enable or disable realtime support"""
        self.config["supportsRealtime"] = supported
        self.config["sync"]["realtimeConfig"]["supported"] = supported
        self.config["sync"]["realtimeConfig"]["connectionType"] = connection_type
        return self

    def with_sync_support(self, supported: bool = True) -> 'ConnectorConfigBuilder':
        """Enable or disable sync support"""
        self.config["supportsSync"] = supported
        return self

    def with_agent_support(self, supported: bool = True) -> 'ConnectorConfigBuilder':
        """Enable or disable agent support"""
        self.config["supportsAgent"] = supported
        return self

    def add_documentation_link(self, link: DocumentationLink) -> 'ConnectorConfigBuilder':
        """Add documentation link"""
        self.config["documentationLinks"].append({
            "title": link.title,
            "url": link.url,
            "type": link.doc_type
        })
        return self

    def with_supported_auth_types(self, auth_types: Union[str, List[str]]) -> 'ConnectorConfigBuilder':
        """Set supported authentication types - user will select one during connector creation"""
        if isinstance(auth_types, str):
            self.config["auth"]["supportedAuthTypes"] = [auth_types]
        elif isinstance(auth_types, list):
            if not auth_types:
                raise ValueError("auth_types list cannot be empty")
            self.config["auth"]["supportedAuthTypes"] = auth_types
        else:
            raise ValueError(f"auth_types must be str or List[str], got {type(auth_types)}")
        return self

    def add_supported_auth_type(self, auth_type: str) -> 'ConnectorConfigBuilder':
        """Add an additional supported authentication type"""
        if "supportedAuthTypes" not in self.config["auth"]:
            self.config["auth"]["supportedAuthTypes"] = ["OAUTH"]
        if auth_type not in self.config["auth"]["supportedAuthTypes"]:
            self.config["auth"]["supportedAuthTypes"].append(auth_type)
        return self

    def add_auth_field(self, field: AuthField, auth_type: str) -> 'ConnectorConfigBuilder':
        """
        Add an authentication field to a specific auth type's schema.

        Args:
            field: AuthField instance to add
            auth_type: Authentication type to add the field to (required)
        """
        if not auth_type:
            raise ValueError("auth_type is required when adding auth fields")

        if "schemas" not in self.config["auth"]:
            self.config["auth"]["schemas"] = {}
        if auth_type not in self.config["auth"]["schemas"]:
            self.config["auth"]["schemas"][auth_type] = {"fields": []}

        target_schema = self.config["auth"]["schemas"][auth_type]
        field_config = auth_field_to_dict(field)
        target_schema["fields"].append(field_config)
        return self

    def with_oauth_config(
        self,
        oauth_config: OAuthConfig,
        auth_type: Optional[str] = None,
        auto_add_common_fields: bool = True
    ) -> 'ConnectorConfigBuilder':
        """
        Use OAuth configuration from registry.

        This method:
        1. Populates OAuth URLs, scopes, and redirect URI in oauthConfigs (for OAuth flow)
        2. Stores redirect URI in the auth type's schema (for form display)
        3. Automatically adds all OAuth fields from the config to the schema

        All OAuth fields should be defined in the OAuthConfig.auth_fields list.
        This ensures a single source of truth - define fields once in OAuth config, use everywhere.

        Args:
            oauth_config: OAuthConfig instance with all fields defined
            auth_type: Optional specific auth type (defaults to first supported auth type)
            auto_add_common_fields: If True, automatically adds all fields from oauth_config.auth_fields
        """
        # Determine auth type
        if not auth_type:
            supported_auth_types = self.config["auth"].get("supportedAuthTypes", ["OAUTH"])
            auth_type = supported_auth_types[0] if supported_auth_types else "OAUTH"

        # Initialize schemas structure if needed
        if "schemas" not in self.config["auth"]:
            self.config["auth"]["schemas"] = {}
        if auth_type not in self.config["auth"]["schemas"]:
            self.config["auth"]["schemas"][auth_type] = {"fields": []}

        # Store redirect URI in the auth type's schema (needed for form display)
        if oauth_config.redirect_uri:
            self.config["auth"]["schemas"][auth_type]["redirectUri"] = oauth_config.redirect_uri
            self.config["auth"]["schemas"][auth_type]["displayRedirectUri"] = True

        # Store OAuth URLs and scopes in oauthConfigs (used during OAuth flow)
        all_scopes = oauth_config.scopes.get_all_scopes()
        self.config["auth"]["oauthConfigs"][auth_type] = {
            "authorizeUrl": oauth_config.authorize_url,
            "tokenUrl": oauth_config.token_url,
            "scopes": all_scopes,
            "redirectUri": oauth_config.redirect_uri
        }

        # Auto-add all OAuth fields from config if requested
        if auto_add_common_fields:
            auto_add_oauth_fields(self.config, oauth_config, auth_type)

        return self

    def with_sync_strategies(self, strategies: List[str], selected: str = "MANUAL") -> 'ConnectorConfigBuilder':
        """Configure sync strategies"""
        self.config["sync"]["supportedStrategies"] = strategies
        self.config["sync"]["selectedStrategy"] = selected
        return self

    def with_webhook_config(self, supported: bool = True, events: Optional[List[str]] = None) -> 'ConnectorConfigBuilder':
        """Configure webhook support"""
        self.config["sync"]["webhookConfig"]["supported"] = supported
        if events:
            self.config["sync"]["webhookConfig"]["events"] = events
        if supported and "WEBHOOK" not in self.config["sync"]["supportedStrategies"]:
            self.config["sync"]["supportedStrategies"].append("WEBHOOK")
        return self

    def with_scheduled_config(self, supported: bool = True, interval_minutes: int = 60) -> 'ConnectorConfigBuilder':
        """Configure scheduled sync"""
        if supported:
            self.config["sync"]["scheduledConfig"]["intervalMinutes"] = interval_minutes
            if "SCHEDULED" not in self.config["sync"]["supportedStrategies"]:
                self.config["sync"]["supportedStrategies"].append("SCHEDULED")
        return self

    def add_sync_custom_field(self, field: CustomField) -> 'ConnectorConfigBuilder':
        """Add a custom field to sync configuration"""
        field_config = {
            "name": field.name,
            "displayName": field.display_name,
            "description": field.description,
            "fieldType": field.field_type,
            "required": field.required,
            "defaultValue": field.default_value,
            "validation": {},
            "isSecret": field.is_secret
        }

        if field.options:
            field_config["options"] = field.options

        if field.min_length is not None:
            field_config["validation"]["minLength"] = field.min_length
        if field.max_length is not None:
            field_config["validation"]["maxLength"] = field.max_length

        self.config["sync"]["customFields"].append(field_config)
        return self

    def add_filter_field(self, field: FilterField) -> 'ConnectorConfigBuilder':
        """
        Add a filter field to the connector schema.

        The field will be added to either sync or indexing category
        based on field.category.

        Args:
            field: FilterField definition with type, operators, category
        """
        schema_dict = field.to_schema_dict()
        category = field.category.value  # "sync" or "indexing"

        # Add to appropriate category schema
        self.config["filters"][category]["schema"]["fields"].append(schema_dict)

        return self

    def add_conditional_display(self, field_name: str, show_when_field: str, operator: str, value: Union[str, bool, int, float]) -> 'ConnectorConfigBuilder':
        """Add conditional display logic for auth fields"""
        self.config["auth"]["conditionalDisplay"][field_name] = {
            "showWhen": {
                "field": show_when_field,
                "operator": operator,
                "value": value
            }
        }
        return self

    def build(self) -> Dict[str, Any]:
        """Build and return the final configuration"""
        result = deepcopy(self.config)
        self._reset()
        return result


class ConnectorBuilder:
    """Main builder for creating connectors with the decorator"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.app_group = ""
        self.supported_auth_types: List[str] = ["OAUTH"]  # Supported auth types (user selects one during creation)
        self.app_description = ""
        self.app_categories = []
        self.config_builder = ConnectorConfigBuilder()
        self.connector_scopes: List[ConnectorScope] = []
        self._oauth_configs: Dict[str, OAuthConfig] = {}  # Store OAuth configs for auto-registration
        self.connector_info: Optional[str] = None

    def in_group(self, app_group: str) -> 'ConnectorBuilder':
        """Set the app group"""
        self.app_group = app_group
        return self

    def with_scopes(self, scopes: List[ConnectorScope]) -> 'ConnectorBuilder':
        """Set the connector scopes"""
        self.connector_scopes = scopes
        return self

    def with_auth(self, auth_builders: List[AuthBuilder]) -> 'ConnectorBuilder':
        """
        Configure authentication types using AuthBuilder pattern (preferred method).

        Args:
            auth_builders: List of AuthBuilder instances, each configuring one auth type

        Example:
            with_auth([
                AuthBuilder.type(AuthType.OAUTH).oauth(...),
                AuthBuilder.type(AuthType.API_TOKEN).fields([...])
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

    def with_supported_auth_types(self, auth_types: Union[str, List[str]]) -> 'ConnectorBuilder':
        """
        Set the supported authentication types - user will select one during connector creation.

        For OAuth connectors, prefer using with_auth() with AuthBuilder for better configuration.
        This method is for simple cases where you just need to set the auth types without OAuth config.

        Args:
            auth_types: Single auth type string or list of auth type strings
        """
        if isinstance(auth_types, str):
            self.supported_auth_types = [auth_types]
        elif isinstance(auth_types, list):
            if not auth_types:
                raise ValueError("auth_types list cannot be empty")
            self.supported_auth_types = auth_types
        else:
            raise ValueError(f"auth_types must be str or List[str], got {type(auth_types)}")

        # Update config builder with supported auth types
        self.config_builder.with_supported_auth_types(auth_types)
        return self

    def with_description(self, description: str) -> 'ConnectorBuilder':
        """Set the app description"""
        self.app_description = description
        return self

    def with_categories(self, categories: List[str]) -> 'ConnectorBuilder':
        """Set the app categories"""
        self.app_categories = categories
        return self

    def with_info(self, info: str) -> 'ConnectorBuilder':
        """Set connector info that will be displayed on the frontend connector page"""
        self.connector_info = info
        return self

    def configure(self, config_func: Callable[[ConnectorConfigBuilder], ConnectorConfigBuilder]) -> 'ConnectorBuilder':
        """Configure the connector using a configuration function"""
        self.config_builder = config_func(self.config_builder)
        return self


    def build_decorator(self) -> Callable[[Type], Type]:
        """Build the final connector decorator"""
        from app.connectors.core.registry.connector_registry import Connector

        config = self.config_builder.build()

        # Auto-register all OAuth configs with final connector name
        oauth_registry = get_oauth_config_registry()
        for auth_type, oauth_config in self._oauth_configs.items():
            # Ensure connector name matches final builder name
            if oauth_config.connector_name != self.name:
                # Remove old registration if name changed and it's the same object
                old_config = oauth_registry.get_config(oauth_config.connector_name)
                if old_config is oauth_config:
                    del oauth_registry._configs[oauth_config.connector_name]
                oauth_config.connector_name = self.name

            # Auto-populate metadata from connector builder if not already set
            # This makes OAuth configs self-contained with metadata
            # Note: app_description should be OAuth-specific (about the OAuth app), not connector sync description
            if not oauth_config.icon_path or oauth_config.icon_path == "/assets/icons/connectors/default.svg":
                oauth_config.icon_path = config.get("iconPath", oauth_config.icon_path)
            if not oauth_config.app_group:
                oauth_config.app_group = self.app_group
            # app_description is intentionally NOT auto-populated from connector description
            # It should describe the OAuth app itself, not what the connector syncs
            # If not provided in oauth() method, it remains empty or uses a generic OAuth description
            if not oauth_config.app_description:
                # Generate OAuth-specific description if not provided
                oauth_config.app_description = f"OAuth application for {self.name} integration"
            if not oauth_config.app_categories:
                oauth_config.app_categories = self.app_categories

            # Auto-populate documentation links from connector config if not already set
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

        return Connector(
            name=self.name,
            app_group=self.app_group,
            supported_auth_types=self.supported_auth_types,  # All supported types (user selects one during creation)
            app_description=self.app_description,
            app_categories=self.app_categories,
            config=config,
            connector_scopes=self.connector_scopes,
            connector_info=self.connector_info
        )

    def _validate_required_auth_fields(self, config: Dict[str, Any]) -> None:
        """
        Validate that required auth fields are properly defined.

        This checks that if a field is marked as required=True, it exists
        in the schema. It does NOT validate that values are provided (that
        happens at runtime during configuration).

        Note: Auth types like "NONE" may not have schemas, which is allowed.
        """
        auth_config = config.get("auth", {})
        schemas = auth_config.get("schemas", {})

        # Check each supported auth type that has a schema
        # Some auth types like "NONE" don't need schemas
        for auth_type in self.supported_auth_types:
            if auth_type in schemas:
                schema_fields = schemas[auth_type].get("fields", [])

                # Check for required fields without names
                for i, field in enumerate(schema_fields):
                    if isinstance(field, dict):
                        if field.get("required", False) and not field.get("name"):
                            raise ValueError(
                                f"Connector '{self.name}' (auth_type: {auth_type}): "
                                f"Required field at index {i} is missing a 'name'"
                            )

    def _validate_oauth_requirements(self, config: Dict[str, Any], auth_type: str = "OAUTH") -> None:
        """Ensure required OAuth infrastructure is provided for OAuth connectors.

        Required OAuth infrastructure:
        - authorizeUrl
        - tokenUrl
        - redirectUri
        - scopes (non-empty list)

        Note: Auth fields (clientId, clientSecret, tenantId, etc.) are flexible
        and should be added by the user via add_auth_field(). We only validate
        that required fields (marked with required=True) are present.

        Args:
            config: Configuration dictionary
            auth_type: Specific auth type to validate (default: "OAUTH")
        """
        auth_config = config.get("auth", {})
        missing_items = []

        # Check for OAuth configs per auth type
        oauth_configs = auth_config.get("oauthConfigs", {})
        if auth_type in oauth_configs:
            oauth_config = oauth_configs[auth_type]
            required_urls = ["authorizeUrl", "tokenUrl"]
            for url_key in required_urls:
                if not oauth_config.get(url_key):
                    missing_items.append(f"{auth_type}.{url_key}")
            scopes = oauth_config.get("scopes", [])
            if not isinstance(scopes, list) or not scopes:
                missing_items.append(f"{auth_type}.scopes")
        else:
            missing_items.append(f"{auth_type}.oauthConfigs (missing OAuth configuration)")

        # Check redirectUri in schema (required for OAuth flow)
        schemas = auth_config.get("schemas", {})
        if auth_type in schemas:
            redirect_uri = schemas[auth_type].get("redirectUri")
            if not redirect_uri:
                missing_items.append(f"{auth_type}.redirectUri")
        else:
            missing_items.append(f"{auth_type}.schemas (missing schema)")

        # Validate that required auth fields are present
        if auth_type in schemas:
            schema_fields = schemas[auth_type].get("fields", [])

            # Check for required fields (fields marked with required=True)
            missing_required_fields = []
            for field in schema_fields:
                if isinstance(field, dict) and field.get("required", False):
                    field_name = field.get("name")
                    if not field_name:
                        missing_required_fields.append("unnamed required field")

            if missing_required_fields:
                missing_items.append(f"auth.schemas.{auth_type}.fields: missing required field definitions")

        if missing_items:
            details = ", ".join(missing_items)
            raise ValueError(
                f"OAuth configuration incomplete for connector '{self.name}' (auth_type: {auth_type}): missing {details}"
            )


# Common field definitions that can be reused
class CommonFields:
    """Reusable field definitions"""

    @staticmethod
    def client_id(provider: str = "OAuth Provider") -> AuthField:
        """Standard OAuth client ID field"""
        return AuthField(
            name="clientId",
            display_name="Client ID",
            placeholder="Enter your Client ID",
            description=f"The OAuth2 client ID from {provider}"
        )

    @staticmethod
    def client_secret(provider: str = "OAuth Provider") -> AuthField:
        """Standard OAuth client secret field"""
        return AuthField(
            name="clientSecret",
            display_name="Client Secret",
            placeholder="Enter your Client Secret",
            description=f"The OAuth2 client secret from {provider}",
            field_type="PASSWORD",
            is_secret=True
        )

    @staticmethod
    def api_token(token_name: str = "API Token", placeholder: str = "") -> AuthField:
        """Standard API token field"""
        return AuthField(
            name="apiToken",
            display_name=token_name,
            placeholder=placeholder or f"Enter your {token_name}",
            description=f"The {token_name} from your application settings",
            field_type="PASSWORD",
            max_length=2000,
            is_secret=True
        )

    @staticmethod
    def bearer_token(token_name: str = "Bearer Token", placeholder: str = "") -> AuthField:
        """Standard Bearer token field"""
        return AuthField(
            name="bearerToken",
            display_name=token_name,
            placeholder=placeholder or f"Enter your {token_name}",
            description=f"The {token_name} from your application settings",
            field_type="PASSWORD",
            max_length=8000,
            is_secret=True
        )

    @staticmethod
    def username() -> AuthField:
        """Standard username field"""
        return AuthField(
            name="username",
            display_name="Username",
            placeholder="Enter your username",
            description="Your account username or email",
            min_length=3
        )

    @staticmethod
    def password() -> AuthField:
        """Standard password field"""
        return AuthField(
            name="password",
            display_name="Password",
            placeholder="Enter your password",
            description="Your account password",
            field_type="PASSWORD",
            min_length=8,
            max_length=2000,
            is_secret=True
        )

    @staticmethod
    def base_url(service_name: str = "service") -> AuthField:
        """Standard base URL field"""
        return AuthField(
            name="baseUrl",
            display_name="Base URL",
            placeholder=f"https://your-{service_name}.com",
            description=f"The base URL of your {service_name} instance",
            field_type="URL",
            max_length=2000
        )

    @staticmethod
    def file_extension_filter(
        description: Optional[str] = None,
        display_name: str = "File Extensions",
        options_endpoint: Optional[str] = None,
    ) -> FilterField:
        """
        Standard file extension filter (static multiselect).

        Note: options_endpoint is reserved for a future dynamic-options implementation.
        """
        return FilterField(
            name="file_extensions",
            display_name=display_name,
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            description=description
            or "Filter files by extension (e.g., pdf, docx, txt). Leave empty to sync all files.",
            default_value=[],
            default_operator=MultiselectOperator.IN.value,
            option_source_type=OptionSourceType.STATIC,
            options=[
                FilterOption(id=ext.value, label=f".{ext.value}")
                for ext in ExtensionTypes
            ]
        )

    @staticmethod
    def folders_filter(options_endpoint: Optional[str] = None) -> FilterField:
        """Standard folders filter"""
        return FilterField(
            name="folders",
            display_name="Folders",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            description="Select folders to sync from",
        )

    @staticmethod
    def channels_filter(options_endpoint: Optional[str] = None) -> FilterField:
        """Standard channels filter"""
        return FilterField(
            name="channels",
            display_name="Channels",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            description="Select channels to sync messages from",
        )

    @staticmethod
    def modified_date_filter(description: Optional[str] = None) -> FilterField:
        """Standard modified date filter with operator selection"""
        return FilterField(
            name="modified",
            display_name="Modified Date",
            filter_type=FilterType.DATETIME,
            category=FilterCategory.SYNC,
            description=description or "Filter content by modification date."
        )

    @staticmethod
    def created_date_filter(description: Optional[str] = None) -> FilterField:
        """Standard created date filter with operator selection"""
        return FilterField(
            name="created",
            display_name="Created Date",
            filter_type=FilterType.DATETIME,
            category=FilterCategory.SYNC,
            description=description or "Filter content by creation date."
        )

    @staticmethod
    def enable_manual_sync_filter() -> FilterField:
        """Standard manual sync control filter (master switch for indexing)"""
        return FilterField(
            name="enable_manual_sync",
            display_name="Enable Manual Sync",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Disable automatic indexing for all synced records.",
            default_value=False
        )

    @staticmethod
    def batch_size_field() -> CustomField:
        """Standard batch size sync field"""
        return CustomField(
            name="batchSize",
            display_name="Batch Size",
            description="Number of items to process in each batch",
            field_type="SELECT",
            default_value="50",
            options=["25", "50", "100"]
        )


