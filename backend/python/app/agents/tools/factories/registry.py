"""
Registry for managing client factories.
"""

from typing import Optional

from app.agents.tools.factories.base import ClientFactory
from app.agents.tools.factories.clickup import ClickUpClientFactory
from app.agents.tools.factories.confluence import (
    ConfluenceClientFactory,
    ConfluenceDataCenterClientFactory,
)
from app.agents.tools.factories.dropbox import DropboxClientFactory
from app.agents.tools.factories.github import GitHubClientFactory
from app.agents.tools.factories.google import GoogleClientFactory
from app.agents.tools.factories.jira import (
    JiraClientFactory,
    JiraDataCenterClientFactory,
)
from app.agents.tools.factories.linear import LinearClientFactory
from app.agents.tools.factories.lumos import LumosClientFactory
from app.agents.tools.factories.mariadb import MariaDBClientFactory
from app.agents.tools.factories.microsoft import MSGraphClientFactory
from app.agents.tools.factories.notion import NotionClientFactory
from app.agents.tools.factories.redshift import RedshiftClientFactory
from app.agents.tools.factories.salesforce import SalesforceClientFactory
from app.agents.tools.factories.slack import SlackClientFactory
from app.agents.tools.factories.zoom import ZoomClientFactory


class ClientFactoryRegistry:
    """
    Registry for managing client factories.

    Provides centralized access to client factories and automatic initialization.
    """

    _factories: dict[str, ClientFactory] = {}
    _initialized: bool = False

    @classmethod
    def register(cls, app_name: str, factory: ClientFactory) -> None:
        cls._factories[app_name] = factory

    @classmethod
    def get_factory(cls, app_name: str) -> Optional[ClientFactory]:
        if not cls._initialized:
            cls.initialize_default_factories()
        return cls._factories.get(app_name)

    @classmethod
    def unregister(cls, app_name: str) -> None:
        if app_name in cls._factories:
            del cls._factories[app_name]

    @classmethod
    def list_factories(cls) -> list[str]:
        if not cls._initialized:
            cls.initialize_default_factories()
        return list(cls._factories.keys())

    @classmethod
    def initialize_default_factories(cls) -> None:
        if cls._initialized:
            return

        # Jira
        cls.register("jira", JiraClientFactory())
        cls.register("jiradatacenter", JiraDataCenterClientFactory())

        # Confluence
        cls.register("confluence", ConfluenceClientFactory())
        cls.register("confluencedatacenter", ConfluenceDataCenterClientFactory())

        # Slack
        cls.register("slack", SlackClientFactory())

        # Notion
        cls.register("notion", NotionClientFactory())

        # ClickUp
        cls.register("clickup", ClickUpClientFactory())

        # Google sub-services
        _google_services = {
            "gmail": ("gmail", "v1"),
            "calendar": ("calendar", "v3"),
            "drive": ("drive", "v3"),
            "meet": ("meet", "v2"),
        }
        for subdir, (service_name, version) in _google_services.items():
            factory = GoogleClientFactory(service_name, version)
            cls.register(f"google{subdir}", factory)
            cls.register(subdir, factory)

        # Microsoft sub-services
        for subdir in ("one_drive", "sharepoint"):
            cls.register(subdir, MSGraphClientFactory(subdir))
        cls.register("outlook", MSGraphClientFactory("outlook"))
        cls.register("teams", MSGraphClientFactory("teams"))
        cls.register("onedrive", MSGraphClientFactory("onedrive"))

        # Linear
        cls.register("linear", LinearClientFactory())

        # MariaDB
        cls.register("mariadb", MariaDBClientFactory())

        # Redshift
        cls.register("redshift", RedshiftClientFactory())

        # Dropbox
        cls.register("dropbox", DropboxClientFactory())

        # GitHub
        cls.register("github", GitHubClientFactory())

        # Lumos
        cls.register("lumos", LumosClientFactory())

        # Zoom
        cls.register("zoom", ZoomClientFactory())

        # Salesforce
        cls.register("salesforce", SalesforceClientFactory())

        cls._initialized = True

    @classmethod
    def reset(cls) -> None:
        """Reset the registry (mainly for testing)"""
        cls._factories.clear()
        cls._initialized = False


ClientFactoryRegistry.initialize_default_factories()
