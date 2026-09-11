"""Generic Connector Factory for creating and managing connectors"""

import logging

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
    SlidesConnector,
    ZendeskConnector,
)
from app.connectors.core.registry.connector_builder import SyncStrategy
from app.connectors.core.sync.task_manager import sync_task_manager
from app.connectors.core.thread_pool import get_shared_connector_thread_pool
from app.connectors.sources.atlassian.confluence_cloud.connector import (
    ConfluenceConnector,
)
from app.connectors.sources.atlassian.confluence_datacenter.connector import (
    ConfluenceDataCenterConnector,
)
from app.connectors.sources.atlassian.confluence_datacenter_personal.connector import (
    ConfluenceDataCenterPersonalConnector,
)
from app.connectors.sources.atlassian.jira_cloud.connector import JiraConnector
from app.connectors.sources.atlassian.jira_cloud_personal.connector import (
    JiraCloudPersonalConnector,
)
from app.connectors.sources.atlassian.jira_data_center.connector import (
    JiraDataCenterConnector,
)
from app.connectors.sources.atlassian.jira_data_center_personal.connector import (
    JiraDataCenterPersonalConnector,
)
from app.connectors.sources.azure_blob.connector import AzureBlobConnector
from app.connectors.sources.azure_files.connector import AzureFilesConnector
from app.connectors.sources.bookstack.connector import BookStackConnector
from app.connectors.sources.box.connector import BoxConnector
from app.connectors.sources.dropbox.connector import DropboxConnector
from app.connectors.sources.dropbox_individual.connector import (
    DropboxIndividualConnector,
)
from app.connectors.sources.local_fs.connector import LocalFsConnector
from app.connectors.sources.github.connector import GithubConnector
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
from app.connectors.sources.microsoft.outlook_individual.connector import (
    OutlookIndividualConnector,
)
from app.connectors.sources.microsoft.sharepoint_online.connector import (
    SharePointConnector,
)
from app.connectors.sources.minio.connector import MinIOConnector
from app.connectors.sources.nextcloud.connector import NextcloudConnector
from app.connectors.sources.notion.connector import NotionConnector
from app.connectors.sources.notion_personal.connector import NotionPersonalConnector
from app.connectors.sources.rss.connector import RSSConnector
from app.connectors.sources.s3.connector import S3Connector
from app.connectors.sources.servicenow.servicenow.connector import ServiceNowConnector
from app.connectors.sources.web.connector import WebConnector
from app.connectors.sources.zammad.connector import ZammadConnector
from app.connectors.sources.zoom.connector import ZoomConnector
from app.connectors.sources.salesforce.connector import SalesforceConnector
from app.connectors.sources.slack.individual.connector import SlackIndividualConnector
from app.connectors.sources.slack.team.connector import SlackConnector

from app.connectors.sources.gitlab.connector import GitLabConnector
from app.connectors.sources.gitlab_personal.connector import GitLabPersonalConnector

from app.connectors.sources.github_teams.connector import GitHubTeamsConnector

from app.connectors.sources.snowflake.connector import SnowflakeConnector
from app.connectors.sources.postgres.connector import PostgreSQLConnector
from app.connectors.sources.mariadb.connector import MariaDBConnector

class ConnectorFactory:
    """Generic factory for creating and managing connectors"""

    # Registry of available connectors
    _connector_registry: dict[str, type[BaseConnector]] = {
        "onedrive": OneDriveConnector,
        "sharepointonline": SharePointConnector,
        "outlook": OutlookConnector,
        "outlookpersonal": OutlookIndividualConnector,
        "confluence": ConfluenceConnector,
        "confluencedatacenter": ConfluenceDataCenterConnector,
        "confluencedatacenterpersonal": ConfluenceDataCenterPersonalConnector,
        "jira": JiraConnector,
        "jiracloudpersonal": JiraCloudPersonalConnector,
        "jiradatacenter": JiraDataCenterConnector,
        "jiradatacenterpersonal": JiraDataCenterPersonalConnector,
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
        "localfs": LocalFsConnector,
        "bookstack": BookStackConnector,
        "github": GithubConnector,
        "s3": S3Connector,
        "minio": MinIOConnector,
        "gcs": GCSConnector,
        "kb": KnowledgeBaseConnector,
        "azureblob": AzureBlobConnector,
        "azurefiles": AzureFilesConnector,
        "postgresql": PostgreSQLConnector,
        "linear": LinearConnector,
        "notion": NotionConnector,
        "notionpersonal": NotionPersonalConnector,
        "zammad": ZammadConnector,
        "zoom": ZoomConnector,
        "salesforce": SalesforceConnector,
        "gitlab": GitLabConnector,
        "gitlabpersonal": GitLabPersonalConnector,
        "githubteams": GitHubTeamsConnector,
        "mariadb": MariaDBConnector,
        "slackworkspace": SlackConnector,
        "slack": SlackIndividualConnector,
    }

    # Beta connector definitions - single source of truth
    # Maps registry key to connector class
    _beta_connector_definitions: dict[str, type[BaseConnector]] = {
        'calendar': CalendarConnector,
        'meet': MeetConnector,
        'forms': FormsConnector,
        'slides': SlidesConnector,
        'docs': DocsConnector,
        'zendesk': ZendeskConnector,
        'airtable': AirtableConnector,
    }

    @classmethod
    def register_connector(
        cls, name: str, connector_class: type[BaseConnector]
    ) -> None:
        """Register a new connector type"""
        cls._connector_registry[name.lower()] = connector_class


    @classmethod
    def unregister_connectors(cls, names: list[str]) -> None:
        """Remove multiple connectors from the registry."""
        for name in names:
            cls._connector_registry.pop(name.lower(), None)

    @classmethod
    def initialize_beta_connector_registry(cls) -> None:
        """Initialize connectors based on feature flags"""
        for name, connector in cls._beta_connector_definitions.items():
            cls.register_connector(name.lower(), connector)

    @classmethod
    def list_beta_connectors(cls) -> dict[str, type[BaseConnector]]:
        """
        Get the dictionary of beta connectors.

        This dynamically extracts app names from connector metadata,
        making it the single source of truth for beta connector identification.

        Returns:
            Dictionary of beta connectors
        """
        return cls._beta_connector_definitions.copy()

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
        data_store_provider: GraphDataStore,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
        org_id: str | None = None,
        data_entities_processor_cls: type | None = None,
        **kwargs,
    ) -> BaseConnector | None:
        """Create a connector instance.

        The processor is created and initialized here using
        ``data_entities_processor_cls``.
        The ready instance is forwarded to the connector so individual connectors

        ``org_id`` is applied to the processor after creation so every
        record/edge uses the connector's actual org.
        """
        connector_class = cls.get_connector_class(name)
        if not connector_class:
            logger.error(f"Unknown connector type: {name} {connector_id}")
            return None

        try:
            notification_service = kwargs.pop("notification_service", None)
            connector_instance_name = kwargs.pop("connector_instance_name", None)
            last_synced_by = kwargs.pop("last_synced_by", None)
            from app.connectors.core.base.data_processor.data_source_entities_processor import DataSourceEntitiesProcessor
            processor_cls = data_entities_processor_cls or DataSourceEntitiesProcessor
            data_entities_processor = processor_cls(logger, data_store_provider, config_service)
            if org_id:
                data_entities_processor.org_id = org_id
            await data_entities_processor.initialize()

            thread_pool = kwargs.pop("thread_pool", None)

            connector = await connector_class.create_connector(
                logger=logger,
                data_store_provider=data_store_provider,
                config_service=config_service,
                connector_id=connector_id,
                scope=scope,
                created_by=created_by,
                data_entities_processor=data_entities_processor,
                org_id=org_id,
                **kwargs,
            )
            if connector is not None:
                if notification_service is not None:
                    connector._notification_service = notification_service
                # Must be set here rather than passed to create_connector: no
                # concrete connector accepts **kwargs, so an extra kwarg would
                # TypeError and be swallowed into a None return below.
                connector._shared_thread_pool = (
                    thread_pool or get_shared_connector_thread_pool()
                )
                if connector_instance_name:
                    connector.connector_instance_name = connector_instance_name
                if last_synced_by:
                    connector.last_synced_by = last_synced_by
            logger.info(f"Created {name} {connector_id} connector successfully")
            return connector
        except Exception as e:
            logger.error(
                f"❌ Failed to create {name} {connector_id} connector: {str(e)}"
            )
            return None

    @classmethod
    async def initialize_connector(
        cls,
        name: str,
        logger: logging.Logger,
        data_store_provider: GraphDataStore,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
        **kwargs,
    ) -> BaseConnector | None:
        """Create and initialize a connector"""
        connector = await cls.create_connector(
            name=name,
            logger=logger,
            data_store_provider=data_store_provider,
            config_service=config_service,
            connector_id=connector_id,
            scope=scope,
            created_by=created_by,
            **kwargs,
        )

        if connector:
            try:
                success = await connector.init()
                if not success:
                    logger.error(
                        f"❌ Failed to initialize {name} {connector_id} connector"
                    )
                    return None
                logger.info(f"Initialized {name} {connector_id} connector successfully")
                return connector
            except Exception as e:
                logger.error(
                    f"❌ Failed to initialize {name} {connector_id} connector: {str(e)}"
                )
                return None

        return None

    @staticmethod
    async def _run_sync_and_invalidate(connector: BaseConnector, connector_id: str) -> None:
        """Run a sync started outside `EventService`, then drop the connector's
        cached accessible-record map the same way that path does."""
        from app.services.cache.invalidation_hooks import notify_connector_sync_completed

        try:
            await connector.run_sync()
        finally:
            processor = getattr(connector, "data_entities_processor", None)
            org_id = getattr(processor, "org_id", None) if processor is not None else None
            await notify_connector_sync_completed(connector_id, org_id)

    @classmethod
    async def create_and_start_sync(
        cls,
        name: str,
        logger: logging.Logger,
        data_store_provider: GraphDataStore,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
        **kwargs,
    ) -> BaseConnector | None:
        """Create, initialize, and start sync for a connector"""
        connector = await cls.initialize_connector(
            name=name,
            logger=logger,
            data_store_provider=data_store_provider,
            config_service=config_service,
            connector_id=connector_id,
            scope=scope,
            created_by=created_by,
            **kwargs,
        )

        config = await config_service.get_config(
            f"/services/connectors/{connector_id}/config"
        )
        sync_strategy = (config or {}).get("sync", {}).get("selectedStrategy")
        if connector:
            try:
                if sync_strategy == SyncStrategy.MANUAL.value:
                    logger.info(
                        f"Skipping sync for {name} {connector_id} connector because selected strategy is MANUAL"
                    )
                else:
                    await sync_task_manager.start_sync(
                        connector_id, cls._run_sync_and_invalidate(connector, connector_id)
                    )
                    logger.info(f"Started sync for {name} {connector_id} connector")
                return connector
            except Exception as e:
                logger.error(
                    f"❌ Failed to start sync for {name} {connector_id} connector: {str(e)}"
                )
                return None

        return None
