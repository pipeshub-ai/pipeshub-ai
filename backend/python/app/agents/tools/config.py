"""
Configuration module for tool discovery and management.
Centralizes all configuration to make the system easier to maintain and extend.
"""

from enum import Enum
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field


class ToolCategory(Enum):
    """Categories for organizing tools"""
    COMMUNICATION = "communication"
    PROJECT_MANAGEMENT = "project_management"
    DOCUMENTATION = "documentation"
    CALENDAR = "calendar"
    FILE_STORAGE = "file_storage"
    CODE_MANAGEMENT = "code_management"
    UTILITY = "utility"
    SEARCH = "search"


class ToolMetadata(BaseModel):
    """
    Metadata for a tool.

    Attributes:
        app_name: Name of the application the tool belongs to
        tool_name: Name of the specific tool
        description: Description of what the tool does
        category: Category for organization
        is_essential: Whether the tool is essential (always loaded)
        requires_auth: Whether the tool requires authentication
        dependencies: List of tool dependencies
        tags: Tags for categorization and search
    """
    app_name: str
    tool_name: str
    description: str
    category: ToolCategory
    is_essential: bool = False
    requires_auth: bool = True
    dependencies: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)


class AppConfiguration(BaseModel):
    """
    Configuration for an application and its tools.

    Attributes:
        app_name: Name of the application
        enabled: Whether the app is enabled for discovery
        subdirectories: List of subdirectories for nested structures
        client_builder: Name of the client class (e.g., "GoogleClient")
        service_configs: Additional service-specific configuration
    """
    app_name: str
    enabled: bool = True
    subdirectories: List[str] = Field(default_factory=list)
    client_builder: Optional[str] = None
    service_configs: Dict[str, Any] = Field(default_factory=dict)


class ToolDiscoveryConfig:
    """
    Centralized configuration for tool discovery.
    All app configurations and discovery rules are defined here.
    """

    # Application configurations
    APP_CONFIGS: Dict[str, AppConfiguration] = {
        "confluence": AppConfiguration(
            app_name="confluence",
            client_builder="ConfluenceClient",
        ),
        "jira": AppConfiguration(
            app_name="jira",
            client_builder="JiraClient",
        ),
        "slack": AppConfiguration(
            app_name="slack",
            client_builder="SlackClient",
        ),
        "notion": AppConfiguration(
            app_name="notion",
            client_builder="NotionClient",
        ),
        # Simple utility apps
        "calculator": AppConfiguration(
            app_name="calculator",
        ),
        "utility": AppConfiguration(
            app_name="utility",
        ),
        "google": AppConfiguration(
            app_name="google",
            subdirectories=["gmail", "calendar", "drive", "meet"],
            client_builder="GoogleClient",
            service_configs={
                "gmail": {"service_name": "gmail", "version": "v1"},
                "calendar": {"service_name": "calendar", "version": "v3"},
                "drive": {"service_name": "drive", "version": "v3"},
                "meet": {"service_name": "meet", "version": "v2"},
            }
        ),
        "microsoft": AppConfiguration(
            app_name="microsoft",
            subdirectories=["one_drive", "sharepoint"],
            client_builder="MSGraphClient",
        ),
        # "discord": AppConfiguration(
        #     app_name="discord",
        #     client_builder="DiscordClient",
        # ),
        # "freshdesk": AppConfiguration(
        #     app_name="freshdesk",
        #     client_builder="FreshDeskClient",
        # ),
        # "evernote": AppConfiguration(
        #     app_name="evernote",
        #     client_builder="EvernoteClient",
        # ),
        "linear": AppConfiguration(
            app_name="linear",
            client_builder="LinearClient",
        ),
        # "linkedin": AppConfiguration(
        #     app_name="linkedin",
        #     client_builder="LinkedInClient",
        # ),
        # "posthog": AppConfiguration(
        #     app_name="posthog",
        #     client_builder="PostHogClient",
        # ),
        # "s3": AppConfiguration(
        #     app_name="s3",
        #     client_builder="S3Client",
        # ),
        # "servicenow": AppConfiguration(
        #     app_name="servicenow",
        #     client_builder="ServiceNowClient",
        # ),
        # "box": AppConfiguration(
        #     app_name="box",
        #     client_builder="BoxClient",
        # ),
        "dropbox": AppConfiguration(
            app_name="dropbox",
            client_builder="DropboxClient",
        ),
        # "github": AppConfiguration(
        #     app_name="github",
        #     client_builder="GitHubClient",
        # ),
        # "gitlab": AppConfiguration(
        #     app_name="gitlab",
        #     client_builder="GitLabClient",
        # ),
        # "airtable": AppConfiguration(
        #     app_name="airtable",
        #     client_builder="AirtableClient",
        # ),
        # "bookstack": AppConfiguration(
        #     app_name="bookstack",
        #     client_builder="BookStackClient",
        # ),
        # "azure_blob": AppConfiguration(
        #     app_name="azure_blob",
        #     client_builder="AzureBlobClient",
        # ),
        # "zendesk": AppConfiguration(
        #     app_name="zendesk",
        #     client_builder="ZendeskClient",
        # )

    }

    # Essential tools that should always be loaded
    ESSENTIAL_TOOL_PATTERNS: Set[str] = {
        "calculator.",
        "web_search",
        "get_current_datetime",
    }

    # Files to skip during discovery
    SKIP_FILES: Set[str] = {"__init__.py", "config.py", "base.py"}

    @classmethod
    def get_app_config(cls, app_name: str) -> Optional[AppConfiguration]:
        """
        Get configuration for a specific app.

        Args:
            app_name: Name of the application

        Returns:
            AppConfiguration if found, None otherwise
        """
        return cls.APP_CONFIGS.get(app_name)

    @classmethod
    def is_essential_tool(cls, tool_name: str) -> bool:
        """
        Check if a tool is essential and should always be loaded.

        Args:
            tool_name: Full name of the tool

        Returns:
            True if tool is essential, False otherwise
        """
        return any(pattern in tool_name for pattern in cls.ESSENTIAL_TOOL_PATTERNS)

    @classmethod
    def add_app_config(cls, config: AppConfiguration) -> None:
        """
        Add or update an app configuration.
        Useful for dynamically adding new apps.

        Args:
            config: AppConfiguration to add
        """
        cls.APP_CONFIGS[config.app_name] = config

    @classmethod
    def disable_app(cls, app_name: str) -> None:
        """
        Disable an app from discovery.

        Args:
            app_name: Name of the app to disable
        """
        if app_name in cls.APP_CONFIGS:
            cls.APP_CONFIGS[app_name].enabled = False

    @classmethod
    def enable_app(cls, app_name: str) -> None:
        """
        Enable an app for discovery.

        Args:
            app_name: Name of the app to enable
        """
        if app_name in cls.APP_CONFIGS:
            cls.APP_CONFIGS[app_name].enabled = True

    @classmethod
    def get_enabled_apps(cls) -> List[str]:
        """
        Get list of enabled app names.

        Returns:
            List of enabled app names
        """
        return [
            name for name, config in cls.APP_CONFIGS.items()
            if config.enabled
        ]
