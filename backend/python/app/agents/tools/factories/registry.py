"""
Registry for managing client factories.
"""

from typing import Dict, Optional

from app.agents.tools.config import ToolDiscoveryConfig
from app.agents.tools.factories.base import ClientFactory
from app.agents.tools.factories.confluence import ConfluenceClientFactory
from app.agents.tools.factories.dropbox import DropboxClientFactory
from app.agents.tools.factories.google import GoogleClientFactory
from app.agents.tools.factories.jira import JiraClientFactory
from app.agents.tools.factories.linear import LinearClientFactory
from app.agents.tools.factories.microsoft import MSGraphClientFactory
from app.agents.tools.factories.notion import NotionClientFactory
from app.agents.tools.factories.slack import SlackClientFactory

# from app.agents.tools.factories.linkedin import LinkedInClientFactory
# from app.agents.tools.factories.freshdesk import FreshDeskClientFactory
# from app.agents.tools.factories.zendesk import ZendeskClientFactory
# from app.agents.tools.factories.posthog import PostHogClientFactory
# from app.agents.tools.factories.box import BoxClientFactory
# from app.agents.tools.factories.bookstack import BookStackClientFactory
# from app.agents.tools.factories.azureblob import AzureBlobClientFactory
# from app.agents.tools.factories.airtable import AirtableClientFactory
# from app.agents.tools.factories.evernote import EvernoteClientFactory
# from app.agents.tools.factories.s3 import S3ClientFactory
# from app.agents.tools.factories.github import GitHubClientFactory
# from app.agents.tools.factories.gitlab import GitLabClientFactory


class ClientFactoryRegistry:
    """
    Registry for managing client factories.

    Provides centralized access to client factories and automatic initialization.
    """

    _factories: Dict[str, ClientFactory] = {}
    _initialized: bool = False

    @classmethod
    def register(cls, app_name: str, factory: ClientFactory) -> None:
        """
        Register a client factory.

        Args:
            app_name: Name of the application
            factory: ClientFactory instance
        """
        cls._factories[app_name] = factory

    @classmethod
    def get_factory(cls, app_name: str) -> Optional[ClientFactory]:
        """
        Get a client factory by app name.

        Args:
            app_name: Name of the application

        Returns:
            ClientFactory if found, None otherwise
        """
        if not cls._initialized:
            cls.initialize_default_factories()

        return cls._factories.get(app_name)

    @classmethod
    def unregister(cls, app_name: str) -> None:
        """
        Unregister a client factory.

        Args:
            app_name: Name of the application
        """
        if app_name in cls._factories:
            del cls._factories[app_name]

    @classmethod
    def list_factories(cls) -> list[str]:
        """
        List all registered factory names.

        Returns:
            List of registered app names
        """
        if not cls._initialized:
            cls.initialize_default_factories()

        return list(cls._factories.keys())

    @classmethod
    def initialize_default_factories(cls) -> None:
        """
        Initialize default client factories based on configuration.
        This is called automatically on first access.
        """
        if cls._initialized:
            return

        for app_name, config in ToolDiscoveryConfig.APP_CONFIGS.items():
            if not config.client_builder:
                continue

            if app_name == "google":
                # Register factories for Google sub-services
                for subdir, service_config in config.service_configs.items():
                    cls.register(
                        subdir,
                        GoogleClientFactory(
                            service_config["service_name"],
                            service_config["version"]
                        )
                    )

            elif app_name == "jira":
                cls.register(app_name, JiraClientFactory())

            elif app_name == "confluence":
                cls.register(app_name, ConfluenceClientFactory())

            elif app_name == "slack":
                cls.register(app_name, SlackClientFactory())

            elif app_name == "notion":
                cls.register(app_name, NotionClientFactory())

            elif app_name == "microsoft":
                # Register factories for Microsoft sub-services
                for subdir in config.subdirectories:
                    cls.register(subdir, MSGraphClientFactory(subdir))

            elif app_name == "linear":
                cls.register(app_name, LinearClientFactory())

            # elif app_name == "linkedin":
            #     cls.register(app_name, LinkedInClientFactory())

            elif app_name == "dropbox":
                cls.register(app_name, DropboxClientFactory())

            # elif app_name == "freshdesk":
            #     cls.register(app_name, FreshDeskClientFactory())

            # elif app_name == "zendesk":
            #     cls.register(app_name, ZendeskClientFactory())

            # elif app_name == "posthog":
            #     cls.register(app_name, PostHogClientFactory())

            # elif app_name == "box":
            #     cls.register(app_name, BoxClientFactory())

            # elif app_name == "bookstack":
            #     cls.register(app_name, BookStackClientFactory())

            # elif app_name == "azureblob":
            #     cls.register(app_name, AzureBlobClientFactory())

            # elif app_name == "airtable":
            #     cls.register(app_name, AirtableClientFactory())

            # elif app_name == "evernote":
            #     cls.register(app_name, EvernoteClientFactory())

            # elif app_name == "s3":
            #     cls.register(app_name, S3ClientFactory())

            # elif app_name == "github":
            #     cls.register(app_name, GitHubClientFactory())

            # elif app_name == "gitlab":
            #     cls.register(app_name, GitLabClientFactory())

        cls._initialized = True

    @classmethod
    def reset(cls) -> None:
        """Reset the registry (mainly for testing)"""
        cls._factories.clear()
        cls._initialized = False


# Initialize factories on import
ClientFactoryRegistry.initialize_default_factories()
