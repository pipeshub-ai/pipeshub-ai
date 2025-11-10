"""Generic Connector Factory for creating and managing connectors"""

import logging

from app.config.configuration_service import ConfigurationService
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_store.arango_data_store import ArangoDataStore
from app.connectors.core.registry.connector import (
    AirtableConnector,
    AzureBlobConnector,
    CalendarConnector,
    DocsConnector,
    FormsConnector,
    LinearConnector,
    MeetConnector,
    NotionConnector,
    S3Connector,
    SlackConnector,
    SlidesConnector,
    ZendeskConnector,
)
from app.connectors.core.registry.connector import (
    ServiceNowConnector as ServiceNowConnectorAgent,
)
from app.connectors.sources.atlassian.confluence_cloud.connector import (
    ConfluenceConnector,
)
from app.connectors.sources.atlassian.jira_cloud.connector import JiraConnector
from app.connectors.sources.bookstack.connector import BookStackConnector
from app.connectors.sources.dropbox.connector import DropboxConnector
from app.connectors.sources.microsoft.onedrive.connector import OneDriveConnector
from app.connectors.sources.microsoft.outlook.connector import OutlookConnector
from app.connectors.sources.microsoft.sharepoint_online.connector import (
    SharePointConnector,
)
from app.connectors.sources.servicenow.servicenow.connector import (
    ServiceNowConnector,
)
from app.connectors.sources.web.connector import WebConnector
from app.services.featureflag.config.config import CONFIG
from app.services.featureflag.featureflag import FeatureFlagService


class ConnectorFactory:
    """Generic factory for creating and managing connectors"""

    # Registry of available connectors
    _connector_registry: dict[str, type[BaseConnector]] = {
        "onedrive": OneDriveConnector,
        "sharepointonline": SharePointConnector,
        "outlook": OutlookConnector,
        "confluence": ConfluenceConnector,
        "jira": JiraConnector,
        "dropbox": DropboxConnector,
        "servicenow": ServiceNowConnector,
        "web": WebConnector,
        "bookstack": BookStackConnector,
    }

    @classmethod
    def register_connector(
        cls, name: str, connector_class: type[BaseConnector]
    ) -> None:
        """Register a new connector type"""
        cls._connector_registry[name.lower()] = connector_class

    @classmethod
    def initialize_connectors(cls, feature_flag_service: FeatureFlagService) -> None:
        """Initialize connectors based on feature flags"""
        # Only one flag for beta connectors
        if feature_flag_service.is_feature_enabled(CONFIG.ENABLE_BETA_CONNECTORS):
            beta_connectors = {
                "slack": SlackConnector,
                "calendar": CalendarConnector,
                "meet": MeetConnector,
                "forms": FormsConnector,
                "slides": SlidesConnector,
                "docs": DocsConnector,
                "servicenow": ServiceNowConnectorAgent,
                "zendesk": ZendeskConnector,
                "linear": LinearConnector,
                "s3": S3Connector,
                "notion": NotionConnector,
                "airtable": AirtableConnector,
                "bookstack": BookStackConnector,
                "azureblob": AzureBlobConnector,
            }

            for name, connector in beta_connectors.items():
                cls.register_connector(name, connector)
        # No need to check per app; only production connectors are pre-registered

    @classmethod
    def get_connector_class(cls, name: str) -> type[BaseConnector] | None:
        """Get connector class by name"""
        return cls._connector_registry.get(name.lower())

    @classmethod
    def list_connectors(cls) -> dict[str, type[BaseConnector]]:
        """List all registered connectors"""
        return cls._connector_registry.copy()

    @classmethod
    async def create_connector(
        cls,
        name: str,
        logger: logging.Logger,
        data_store_provider: ArangoDataStore,
        config_service: ConfigurationService,
        **kwargs,
    ) -> BaseConnector | None:
        """Create a connector instance"""
        connector_class = cls.get_connector_class(name)
        if not connector_class:
            logger.error(f"Unknown connector type: {name}")
            return None

        try:
            connector = await connector_class.create_connector(
                logger=logger,
                data_store_provider=data_store_provider,
                config_service=config_service,
                **kwargs,
            )
            logger.info(f"Created {name} connector successfully")
            return connector
        except Exception as e:
            logger.error(f"❌ Failed to create {name} connector: {e!s}")
            return None

    @classmethod
    async def initialize_connector(
        cls,
        name: str,
        logger: logging.Logger,
        data_store_provider: ArangoDataStore,
        config_service: ConfigurationService,
        **kwargs,
    ) -> BaseConnector | None:
        """Create and initialize a connector"""
        connector = await cls.create_connector(
            name=name,
            logger=logger,
            data_store_provider=data_store_provider,
            config_service=config_service,
            **kwargs,
        )

        if connector:
            try:
                await connector.init()
                logger.info(f"Initialized {name} connector successfully")
                return connector
            except Exception as e:
                logger.error(f"❌ Failed to initialize {name} connector: {e!s}")
                return None

        return None

    @classmethod
    async def create_and_start_sync(
        cls,
        name: str,
        logger: logging.Logger,
        data_store_provider: ArangoDataStore,
        config_service: ConfigurationService,
        **kwargs,
    ) -> BaseConnector | None:
        """Create, initialize, and start sync for a connector"""
        connector = await cls.initialize_connector(
            name=name,
            logger=logger,
            data_store_provider=data_store_provider,
            config_service=config_service,
            **kwargs,
        )

        if connector:
            try:
                import asyncio

                asyncio.create_task(connector.run_sync())
                logger.info(f"Started sync for {name} connector")
                return connector
            except Exception as e:
                logger.error(f"❌ Failed to start sync for {name} connector: {e!s}")
                return None

        return None
