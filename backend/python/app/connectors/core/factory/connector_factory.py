"""Generic Connector Factory for creating and managing connectors"""

import logging
from typing import Dict, Optional, Type

from app.config.configuration_service import ConfigurationService
from app.connectors.core.base.connector.connector_service import BaseConnector

# from app.connectors.core.interfaces.data_store.data_store_provider import DataStoreProvider
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.core.registry.connector import (
    AirtableConnector,
    CalendarConnector,
    DocsConnector,
    FormsConnector,
    MeetConnector,
    SlackConnector,
    SlidesConnector,
    ZendeskConnector,
)
from app.connectors.sources.atlassian.confluence_cloud.connector import (
    ConfluenceConnector,
)
from app.connectors.sources.atlassian.jira_cloud.connector import JiraConnector
from app.connectors.sources.azure_blob.connector import AzureBlobConnector
from app.connectors.sources.azure_files.connector import AzureFilesConnector
from app.connectors.sources.bookstack.connector import BookStackConnector
from app.connectors.sources.box.connector import BoxConnector
from app.connectors.sources.dropbox.connector import DropboxConnector
from app.connectors.sources.dropbox_individual.connector import (
    DropboxIndividualConnector,
)
from app.connectors.sources.google.drive.individual.connector import (
    GoogleDriveIndividualConnector,
)
from app.connectors.sources.google.drive.team.connector import GoogleDriveTeamConnector
from app.connectors.sources.google.gmail.individual.connector import (
    GoogleGmailIndividualConnector,
)
from app.connectors.sources.google.gmail.team.connector import GoogleGmailTeamConnector
from app.connectors.sources.google_cloud_storage.connector import GCSConnector
from app.connectors.sources.linear.connector import LinearConnector
from app.connectors.sources.localKB.connector import KnowledgeBaseConnector
from app.connectors.sources.microsoft.onedrive.connector import OneDriveConnector
from app.connectors.sources.microsoft.outlook.connector import OutlookConnector
from app.connectors.sources.microsoft.sharepoint_online.connector import (
    SharePointConnector,
)
from app.connectors.sources.minio.connector import MinIOConnector
from app.connectors.sources.nextcloud.connector import NextcloudConnector
from app.connectors.sources.notion.connector import NotionConnector
from app.connectors.sources.rss.connector import RSSConnector
from app.connectors.sources.s3.connector import S3Connector
from app.connectors.sources.servicenow.servicenow.connector import ServiceNowConnector
from app.connectors.sources.web.connector import WebConnector
from app.connectors.sources.zammad.connector import ZammadConnector

from app.connectors.sources.snowflake.connector import SnowflakeConnector
from app.connectors.sources.postgres.connector import PostgreSQLConnector

class ConnectorFactory:
    """Generic factory for creating and managing connectors"""

    # Registry of available connectors
    _connector_registry: Dict[str, Type[BaseConnector]] = {
        "onedrive": OneDriveConnector,
        "sharepointonline": SharePointConnector,
        "outlook": OutlookConnector,
        "confluence": ConfluenceConnector,
        "jira": JiraConnector,
        "box": BoxConnector,
        "drive": GoogleDriveIndividualConnector,
        "driveworkspace": GoogleDriveTeamConnector,
        "gmail": GoogleGmailIndividualConnector,
        "gmailworkspace": GoogleGmailTeamConnector,
        "dropbox": DropboxConnector,
        "dropboxpersonal": DropboxIndividualConnector,
        "nextcloud": NextcloudConnector,
        "servicenow": ServiceNowConnector,
        "web": WebConnector,
        "rss": RSSConnector,
        "bookstack": BookStackConnector,
        "s3": S3Connector,
        "minio": MinIOConnector,
        "gcs": GCSConnector,
        "kb": KnowledgeBaseConnector,
        "azureblob": AzureBlobConnector,
        "azurefiles": AzureFilesConnector,
        "postgresql": PostgreSQLConnector,
        "linear": LinearConnector,
        "notion": NotionConnector,
        "zammad": ZammadConnector,
    }

    # Beta connector definitions - single source of truth
    # Maps registry key to connector class
    _beta_connector_definitions: Dict[str, Type[BaseConnector]] = {
        'slack': SlackConnector,
        'calendar': CalendarConnector,
        'meet': MeetConnector,
        'forms': FormsConnector,
        'slides': SlidesConnector,
        'docs': DocsConnector,
        'zendesk': ZendeskConnector,
        'airtable': AirtableConnector,
    }


    @classmethod
    def register_connector(cls, name: str, connector_class: Type[BaseConnector]) -> None:
        """Register a new connector type"""
        cls._connector_registry[name.lower()] = connector_class

    @classmethod
    def initialize_beta_connector_registry(cls) -> None:
        """Initialize connectors based on feature flags"""
        for name, connector in cls._beta_connector_definitions.items():
            cls.register_connector(name.lower(), connector)

    @classmethod
    def list_beta_connectors(cls) -> Dict[str, Type[BaseConnector]]:
        """
        Get the dictionary of beta connectors.

        This dynamically extracts app names from connector metadata,
        making it the single source of truth for beta connector identification.

        Returns:
            Dictionary of beta connectors
        """
        return cls._beta_connector_definitions.copy()

    @classmethod
    def get_connector_class(cls, name: str) -> Optional[Type[BaseConnector]]:
        """Get connector class by name"""
        return cls._connector_registry.get(name.lower())

    @classmethod
    def list_connectors(cls) -> Dict[str, Type[BaseConnector]]:
        """List all registered connectors"""
        return cls._connector_registry.copy()

    @classmethod
    async def create_connector(
        cls,
        name: str,
        logger: logging.Logger,
        data_store_provider: GraphDataStore,
        config_service: ConfigurationService,
        connector_id: str,
        **kwargs
    ) -> Optional[BaseConnector]:
        """Create a connector instance"""
        connector_class = cls.get_connector_class(name)
        if not connector_class:
            logger.error(f"Unknown connector type: {name} {connector_id}")
            return None

        try:
            connector = await connector_class.create_connector(
                logger=logger,
                data_store_provider=data_store_provider,
                config_service=config_service,
                connector_id=connector_id,
                **kwargs
            )
            logger.info(f"Created {name} {connector_id} connector successfully")
            return connector
        except Exception as e:
            logger.error(f"❌ Failed to create {name} {connector_id} connector: {str(e)}")
            return None

    @classmethod
    async def initialize_connector(
        cls,
        name: str,
        logger: logging.Logger,
        data_store_provider: GraphDataStore,
        config_service: ConfigurationService,
        connector_id: str,
        **kwargs
    ) -> Optional[BaseConnector]:
        """Create and initialize a connector"""
        connector = await cls.create_connector(
            name=name,
            logger=logger,
            data_store_provider=data_store_provider,
            config_service=config_service,
            connector_id=connector_id,
            **kwargs
        )

        if connector:
            try:
                success = await connector.init()
                if not success:
                    logger.error(f"❌ Failed to initialize {name} {connector_id} connector")
                    return None
                logger.info(f"Initialized {name} {connector_id} connector successfully")
                return connector
            except Exception as e:
                logger.error(f"❌ Failed to initialize {name} {connector_id} connector: {str(e)}")
                return None

        return None

    @classmethod
    async def create_and_start_sync(
        cls,
        name: str,
        logger: logging.Logger,
        data_store_provider: GraphDataStore,
        config_service: ConfigurationService,
        connector_id: str,
        **kwargs
    ) -> Optional[BaseConnector]:
        """Create, initialize, and start sync for a connector"""
        connector = await cls.initialize_connector(
            name=name,
            logger=logger,
            data_store_provider=data_store_provider,
            config_service=config_service,
            connector_id=connector_id,
            **kwargs
        )

        if connector:
            try:
                import asyncio
                asyncio.create_task(connector.run_sync())
                logger.info(f"Started sync for {name} {connector_id} connector")
                return connector
            except Exception as e:
                logger.error(f"❌ Failed to start sync for {name} {connector_id} connector: {str(e)}")
                return None

        return None
