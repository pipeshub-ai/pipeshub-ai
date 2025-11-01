from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Type, Union


@dataclass
class AuthField:
    """Represents an authentication field"""
    name: str
    display_name: str
    field_type: str = "TEXT"
    placeholder: str = ""
    description: str = ""
    required: bool = True
    default_value: Any = ""
    min_length: int = 10
    max_length: int = 1000
    is_secret: bool = False


@dataclass
class FilterField:
    """Represents a filter field"""
    name: str
    display_name: str
    field_type: str = "MULTISELECT"
    description: str = ""
    required: bool = False
    default_value: Any = field(default_factory=list)
    options: List[str] = field(default_factory=list)
    operators: List[str] = field(default_factory=lambda: ["IN", "NOT_IN"])


@dataclass
class CustomField:
    """Represents a custom field for sync configuration"""
    name: str
    display_name: str
    field_type: str
    description: str = ""
    required: bool = False
    default_value: Any = ""
    options: List[str] = field(default_factory=list)
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    is_secret: bool = False


@dataclass
class DocumentationLink:
    """Represents a documentation link"""
    title: str
    url: str
    doc_type: str


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
            "documentationLinks": [],
            "auth": {
                "type": "OAUTH",
                "displayRedirectUri": True,
                "redirectUri": "",
                "schema": {"fields": []},
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
                "schema": {"fields": []},
                "values": {},
                "customFields": [],
                "customValues": {},
                "endpoints": {}
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

    def add_documentation_link(self, link: DocumentationLink) -> 'ConnectorConfigBuilder':
        """Add documentation link"""
        self.config["documentationLinks"].append({
            "title": link.title,
            "url": link.url,
            "type": link.doc_type
        })
        return self

    def with_auth_type(self, auth_type: str) -> 'ConnectorConfigBuilder':
        """Set authentication type"""
        self.config["auth"]["type"] = auth_type
        return self

    def with_redirect_uri(self, redirect_uri: str, display: bool = True) -> 'ConnectorConfigBuilder':
        """Set redirect URI configuration"""
        self.config["auth"]["redirectUri"] = redirect_uri
        self.config["auth"]["displayRedirectUri"] = display
        return self

    def add_auth_field(self, field: AuthField) -> 'ConnectorConfigBuilder':
        """Add an authentication field"""
        field_config = {
            "name": field.name,
            "displayName": field.display_name,
            "placeholder": field.placeholder,
            "description": field.description,
            "fieldType": field.field_type,
            "required": field.required,
            "defaultValue": field.default_value,
            "validation": {
                "minLength": field.min_length,
                "maxLength": field.max_length,
            },
            "isSecret": field.is_secret
        }
        self.config["auth"]["schema"]["fields"].append(field_config)
        return self

    def with_oauth_urls(self, authorize_url: str, token_url: str, scopes: Optional[List[str]] = None) -> 'ConnectorConfigBuilder':
        """Set OAuth URLs and scopes for OAuth connectors"""
        self.config["auth"]["authorizeUrl"] = authorize_url
        self.config["auth"]["tokenUrl"] = token_url
        if scopes:
            self.config["auth"]["scopes"] = scopes
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

    def add_filter_field(self, field: FilterField, endpoint: str = "static") -> 'ConnectorConfigBuilder':
        """Add a filter field"""
        field_config = {
            "name": field.name,
            "displayName": field.display_name,
            "description": field.description,
            "fieldType": field.field_type,
            "required": field.required,
            "defaultValue": field.default_value,
            "options": field.options,
            "operators": field.operators
        }

        self.config["filters"]["schema"]["fields"].append(field_config)
        self.config["filters"]["endpoints"][field.name] = endpoint
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
        self.auth_type = "OAUTH"
        self.app_description = ""
        self.app_categories = []
        self.config_builder = ConnectorConfigBuilder()

    def in_group(self, app_group: str) -> 'ConnectorBuilder':
        """Set the app group"""
        self.app_group = app_group
        return self

    def with_auth_type(self, auth_type: str) -> 'ConnectorBuilder':
        """Set the authentication type"""
        self.auth_type = auth_type
        return self

    def with_description(self, description: str) -> 'ConnectorBuilder':
        """Set the app description"""
        self.app_description = description
        return self

    def with_categories(self, categories: List[str]) -> 'ConnectorBuilder':
        """Set the app categories"""
        self.app_categories = categories
        return self

    def configure(self, config_func: Callable[[ConnectorConfigBuilder], ConnectorConfigBuilder]) -> 'ConnectorBuilder':
        """Configure the connector using a configuration function"""
        self.config_builder = config_func(self.config_builder)
        return self

    def build_decorator(self) -> Callable[[Type], Type]:
        """Build the final connector decorator"""
        from app.connectors.core.registry.connector_registry import Connector

        config = self.config_builder.build()

        # Validate OAuth requirements when applicable
        if self.auth_type and self.auth_type.upper() == "OAUTH":
            self._validate_oauth_requirements(config)

        return Connector(
            name=self.name,
            app_group=self.app_group,
            auth_type=self.auth_type,
            app_description=self.app_description,
            app_categories=self.app_categories,
            config=config
        )

    def _validate_oauth_requirements(self, config: Dict[str, Any]) -> None:
        """Ensure required OAuth fields are provided for OAuth connectors.

        Required:
        - authorizeUrl
        - tokenUrl
        - redirectUri
        - scopes (non-empty list)
        - auth schema includes fields: clientId, clientSecret
        """
        auth_config = config.get("auth", {})

        missing_items = []

        required_urls = ["authorizeUrl", "tokenUrl", "redirectUri"]
        for url_key in required_urls:
            if not auth_config.get(url_key):
                missing_items.append(url_key)

        scopes = auth_config.get("scopes")
        if not isinstance(scopes, list) or not scopes:
            missing_items.append("scopes")

        # Validate presence of clientId and clientSecret in auth schema fields
        schema_fields = auth_config.get("schema", {}).get("fields", [])
        field_names = {f.get("name") for f in schema_fields if isinstance(f, dict)}
        required_schema_fields = {"clientId", "clientSecret"}
        missing_fields = required_schema_fields - field_names
        for field_name in sorted(list(missing_fields)):
            missing_items.append(f"auth.schema.fields: {field_name}")

        if missing_items:
            details = ", ".join(missing_items)
            raise ValueError(
                f"OAuth configuration incomplete for connector '{self.name}': missing {details}"
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
    def file_types_filter() -> FilterField:
        """Standard file types filter"""
        return FilterField(
            name="fileTypes",
            display_name="File Types",
            description="Select the types of files to sync",
            options=["document", "spreadsheet", "presentation", "pdf", "image", "video"]
        )

    @staticmethod
    def folders_filter() -> FilterField:
        """Standard folders filter"""
        return FilterField(
            name="folders",
            display_name="Folders",
            description="Select folders to sync from"
        )

    @staticmethod
    def channels_filter() -> FilterField:
        """Standard channels filter"""
        return FilterField(
            name="channels",
            display_name="Channels",
            description="Select channels to sync messages from"
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


