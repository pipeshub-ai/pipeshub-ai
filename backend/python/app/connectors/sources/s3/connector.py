"""
AWS S3 Connector

Connector for synchronizing data from AWS S3 buckets. This connector uses the
shared S3CompatibleBaseConnector to handle common functionality.
"""

from logging import Logger

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors, PermissionModel
from app.connectors.core.constants import CONNECTOR_S3_INFO, IconPaths
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
)
from app.connectors.core.registry.connector_builder import (
    AuthField,
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
    SyncStrategy,
)
from app.connectors.core.registry.filters import (
    FilterCategory,
    FilterField,
    FilterType,
    MultiselectOperator,
    OptionSourceType,
    load_connector_filters,
)

# Re-export utility functions for backward compatibility
from app.connectors.sources.s3.base_connector import (
    S3CompatibleBaseConnector,
    S3CompatibleDataSourceEntitiesProcessor,
    parse_parent_external_id,
)
from app.connectors.sources.s3.common.apps import S3App
from app.sources.client.s3.s3 import S3Client
from app.sources.external.s3.s3 import S3DataSource
from app.services.notification.types import (
    NotificationSeverity,
    NotificationType,
    NotificationRecipientRole,
)

# Re-export the entities processor for backward compatibility
S3DataSourceEntitiesProcessor = S3CompatibleDataSourceEntitiesProcessor


@ConnectorBuilder("S3")\
    .in_group("S3")\
    .with_description("Sync files and folders from S3")\
    .with_categories(["Storage"])\
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
    .with_permission_model(PermissionModel.APP_LEVEL)\
    .with_auth([
        AuthBuilder.type(AuthType.ACCESS_KEY).fields([
            AuthField(
                name="accessKey",
                display_name="Access Key",
                placeholder="Enter your Access Key",
                description="Access key of a key with read-only access to the buckets to sync",
                field_type="PASSWORD",
                max_length=2000,
                is_secret=True
            ),
            AuthField(
                name="secretKey",
                display_name="Secret Key",
                placeholder="Enter your Secret Key",
                description="Secret key of a key with read-only access to the buckets to sync",
                field_type="PASSWORD",
                max_length=2000,
                is_secret=True
            )
        ]),
        AuthBuilder.type(AuthType.IAM_ROLE).fields([
            AuthField(
                name="accessKey",
                display_name="Access Key",
                placeholder="Enter the Access Key that may call sts:AssumeRole",
                description="Used only to call STS AssumeRole. Give it no S3 access.",
                field_type="PASSWORD",
                max_length=2000,
                is_secret=True
            ),
            AuthField(
                name="secretKey",
                display_name="Secret Key",
                placeholder="Enter the Secret Key for that Access Key",
                description="Used only to call STS AssumeRole.",
                field_type="PASSWORD",
                max_length=2000,
                is_secret=True
            ),
            AuthField(
                name="roleArn",
                display_name="Role ARN",
                placeholder="arn:aws:iam::123456789012:role/pipeshub-s3-reader",
                description="The IAM role in the bucket's account that PipesHub assumes to read S3",
                field_type="TEXT",
                max_length=2048,
            ),
            AuthField(
                name="externalId",
                display_name="External ID",
                placeholder="Enter the External ID from the role's trust policy",
                description="Optional. Must match sts:ExternalId in the role's trust policy.",
                field_type="TEXT",
                required=False,
                max_length=1224,
            ),
        ])
    ])\
    .with_info(CONNECTOR_S3_INFO)\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.S3.value))
        .add_documentation_link(DocumentationLink(
            "S3 Read-Only Policy Examples",
            "https://docs.aws.amazon.com/AmazonS3/latest/userguide/example-policies-s3.html",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            "S3 IAM Role (AssumeRole) Setup",
            "https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_common-scenarios_third-party.html",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/s3/s3',
            'pipeshub'
        ))
        .add_filter_field(FilterField(
            name="buckets",
            display_name="Bucket Names",
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            description="Select the S3 buckets to sync. Leave empty to sync every bucket the key can list.",
            option_source_type=OptionSourceType.DYNAMIC,
            default_operator=MultiselectOperator.IN.value
        ))
        .add_filter_field(CommonFields.folder_paths_filter("bucket"))
        .add_filter_field(CommonFields.file_extension_filter())
        .add_filter_field(CommonFields.modified_date_filter("Filter files and folders by modification date."))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(True)
    )\
    .build_decorator()
class S3Connector(S3CompatibleBaseConnector):
    """
    Connector for synchronizing data from AWS S3 buckets.
    """

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
    ) -> None:
        super().__init__(
            app=S3App(connector_id),
            logger=logger,
            data_entities_processor=data_entities_processor,
            data_store_provider=data_store_provider,
            config_service=config_service,
            connector_id=connector_id,
            scope=scope,
            created_by=created_by,
            connector_name=Connectors.S3,
            filter_key="s3",
            base_console_url="https://s3.console.aws.amazon.com",
        )

    async def init(self) -> bool:
        """Initializes the S3 client using credentials from the config service."""
        config = await self.config_service.get_config(
            f"/services/connectors/{self.connector_id}/config"
        )
        if not config:
            self.logger.error("S3 configuration not found.")
            payload = {
                "connectorId": self.connector_id,
                "connectorName": self.connector_name.value,
            }
            await self.notify(
                type=NotificationType.CONNECTOR_AUTH_ERROR, 
                severity=NotificationSeverity.ERROR, 
                title=self._notification_title("Configuration not found"),
                message="S3 configuration not found for this connector.", 
                payload=payload,
            )
            return False

        auth_config = config.get("auth", {})
        access_key = auth_config.get("accessKey")
        secret_key = auth_config.get("secretKey")
        self.bucket_name = auth_config.get("bucket")

        if not access_key or not secret_key:
            self.logger.error("S3 access key or secret key not found in configuration.")
            await self.notify(
                type=NotificationType.CONNECTOR_AUTH_ERROR,
                severity=NotificationSeverity.ERROR,
                title=self._notification_title("Credentials missing"),
                message=(
                    "S3 access key or secret key is missing in connector configuration."
                ),
            )
            return False

        try:
            client = await S3Client.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id,
            )
            self.data_source = S3DataSource(client)

            # Load connector filters
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "s3", self.connector_id, self.logger
            )

            self.logger.info("S3 client initialized successfully.")
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize S3 client: {e}", exc_info=True)
            await self.notify(
                type=NotificationType.CONNECTOR_SYNC_ERROR,
                severity=NotificationSeverity.ERROR,
                title=self._notification_title("Failed to initialize S3 client"),
                message=f"Failed to initialize S3 client: {e}",
            )
            return False

    async def _build_data_source(self) -> S3DataSource:
        """Build the S3DataSource."""
        client = await S3Client.build_from_services(
            logger=self.logger,
            config_service=self.config_service,
            connector_instance_id=self.connector_id,
        )
        return S3DataSource(client)

    def _generate_web_url(self, bucket_name: str, normalized_key: str) -> str:
        """Generate the web URL for an S3 object."""
        return f"{self.base_console_url}/s3/object/{bucket_name}?prefix={normalized_key}"

    def _generate_parent_web_url(self, parent_external_id: str) -> str:
        """Generate the web URL for an S3 parent folder/directory."""
        bucket_name, path = parse_parent_external_id(parent_external_id)
        if path:
            return f"{self.base_console_url}/s3/object/{bucket_name}?prefix={path}"
        else:
            return f"{self.base_console_url}/s3/buckets/{bucket_name}"

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
        data_entities_processor,
        **kwargs,
    ) -> "S3Connector":
        """Factory method to create and initialize connector."""
        org_id = kwargs.pop("org_id", None)
        base_console_url = "https://s3.console.aws.amazon.com"

        # Create processor with default S3 URL generation (will be updated after connector creation)
        data_entities_processor = S3CompatibleDataSourceEntitiesProcessor(
            logger, data_store_provider, config_service,
            base_console_url=base_console_url
        )
        await data_entities_processor.initialize(org_id=org_id)

        connector = cls(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )

        # Update processor with connector-specific URL generator
        data_entities_processor.parent_url_generator = lambda parent_external_id: connector._generate_parent_web_url(parent_external_id)

        return connector
