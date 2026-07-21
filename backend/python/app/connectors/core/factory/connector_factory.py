"""Generic Connector Factory for creating and managing connectors"""

import logging
from typing import Optional

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
from app.connectors.sources.rss.connector import RSSConnector
from app.connectors.sources.s3.connector import S3Connector
from app.connectors.sources.servicenow.servicenow.connector import ServiceNowConnector
from app.connectors.sources.slack.team.connector import SlackConnector
from app.connectors.sources.web.connector import WebConnector
from app.connectors.sources.zammad.connector import ZammadConnector
from app.connectors.sources.zoom.connector import ZoomConnector
from app.connectors.sources.salesforce.connector import SalesforceConnector
from app.connectors.sources.slack.individual.connector import SlackIndividualConnector
from app.connectors.sources.slack.team.connector import SlackConnector

from app.connectors.sources.gitlab.connector import GitLabConnector
from app.connectors.sources.gitlab_personal.connector import GitLabPersonalConnector

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
        "zammad": ZammadConnector,
        "zoom": ZoomConnector,
        "salesforce": SalesforceConnector,
        "gitlab": GitLabConnector,
        "gitlabpersonal": GitLabPersonalConnector,
        "mariadb": MariaDBConnector,
        "slackworkspace": SlackConnector,
        # "slack": SlackIndividualConnector,
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
        **kwargs,
    ) -> BaseConnector | None:
        """Create a connector instance.

        ``org_id`` is bound here (not forwarded to the connector) and applied to the
        connector's entities processor after creation. The processor's initialize()
        resolves an arbitrary orgs[0] fallback; connectors read
        ``self.data_entities_processor.org_id`` live at sync time, so overriding it
        here makes every record/edge use the connector's actual org (multi-org fix).
        """
        connector_class = cls.get_connector_class(name)
        if not connector_class:
            logger.error(f"Unknown connector type: {name} {connector_id}")
            return None

        try:
            notification_service = kwargs.pop("notification_service", None)
            connector = await connector_class.create_connector(
                logger=logger,
                data_store_provider=data_store_provider,
                config_service=config_service,
                connector_id=connector_id,
                scope=scope,
                created_by=created_by,
                **kwargs,
            )
            if connector is not None:
                if org_id and getattr(connector, "data_entities_processor", None) is not None:
                    connector.data_entities_processor.org_id = org_id
                if notification_service is not None:
                    connector._notification_service = notification_service
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
        org_id: str | None = None,
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
                        connector_id,
                        cls._run_startup_sync(
                            connector, connector_id, org_id, logger, config_service
                        ),
                    )
                    logger.info(f"Started sync for {name} {connector_id} connector")
                return connector
            except Exception as e:
                logger.error(
                    f"❌ Failed to start sync for {name} {connector_id} connector: {str(e)}"
                )
                return None

        return None

    @classmethod
    async def _run_startup_sync(
        cls,
        connector: BaseConnector,
        connector_id: str,
        org_id: str | None,
        logger: logging.Logger,
        config_service: ConfigurationService,
    ) -> None:
        """Wrap run_sync() for the startup-resume path.

        Kafka-driven syncs set apps.status via EventService; startup-resumed
        syncs bypass that, so mirror the same SYNCING -> IDLE transition and the
        run-scoped progress lifecycle here to avoid the UI showing IDLE mid-sync.
        """
        from app.config.constants.arangodb import AppStatus, CollectionNames
        from app.connectors.services.sync_progress_store import (
            get_connector_sync_progress_store,
        )
        from app.utils.time_conversion import get_epoch_timestamp_in_ms

        data_store_provider = getattr(connector, "data_store_provider", None)
        graph_provider = getattr(data_store_provider, "graph_provider", None)

        async def _set_status(status: str) -> None:
            if not graph_provider:
                return
            try:
                await graph_provider.batch_upsert_nodes(
                    [{
                        "id": connector_id,
                        "status": status,
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                    }],
                    CollectionNames.APPS.value,
                )
            except Exception as e:
                logger.debug(f"Failed to set status {status} for {connector_id}: {e}")

        store = None
        try:
            store = await get_connector_sync_progress_store(logger, config_service)
        except Exception:
            store = None

        await _set_status(AppStatus.SYNCING.value)
        run_id: Optional[str] = None
        if store and org_id:
            run_id = await store.start_run(org_id, connector_id, full_sync=False)
        try:
            from app.connectors.services.sync_run_context import (
                reset_sync_run_id,
                set_sync_run_id,
            )

            token = set_sync_run_id(run_id)
            try:
                await connector.run_sync()
            finally:
                reset_sync_run_id(token)
        finally:
            # A manual (Kafka-driven) sync can supersede this startup-resume run;
            # in that case leave status/progress to the newer run rather than
            # clobbering it back to IDLE. See EventService._run_sync_and_clear_status.
            if store and org_id and not await store.is_current_run(org_id, connector_id, run_id):
                logger.info(
                    f"Startup sync for connector {connector_id} was superseded by a newer run; "
                    "leaving status and progress to the newer run"
                )
            else:
                if store and org_id:
                    await store.close_discovery(org_id, connector_id, expected_run_id=run_id)
                await _set_status(AppStatus.IDLE.value)
