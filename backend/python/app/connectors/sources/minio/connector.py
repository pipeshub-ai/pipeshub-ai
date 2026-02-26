"""
MinIO S3-Compatible Connector

Connector for synchronizing data from MinIO object storage. MinIO is an S3-compatible
object storage server, so this connector shares most functionality with the S3Connector
through the S3CompatibleBaseConnector base class.
"""

from logging import Logger
from urllib.parse import urlparse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors
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
)
from app.connectors.core.registry.filters import (
    FilterCategory,
    FilterField,
    FilterType,
    ListOperator,
    MultiselectOperator,
    OptionSourceType,
    load_connector_filters,
)
from app.connectors.sources.minio.common.apps import MinIOApp

# Re-export utility functions for convenience
from app.connectors.sources.s3.base_connector import (
    S3CompatibleBaseConnector,
    S3CompatibleDataSourceEntitiesProcessor,
    parse_parent_external_id,
)
from app.sources.client.minio.minio import MinIOClient
from app.sources.external.minio.minio import MinIODataSource

# Entities processor for MinIO
MinIODataSourceEntitiesProcessor = S3CompatibleDataSourceEntitiesProcessor


@ConnectorBuilder("MinIO")\
    .in_group("MinIO")\
    .with_description("Sync files and folders from MinIO S3-compatible storage")\
    .with_categories(["Storage"])\
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.ACCESS_KEY).fields([
            AuthField(
                name="endpointUrl",
                display_name="Endpoint URL",
                placeholder="http://localhost:9000 or https://minio.example.com",
                description="The MinIO server endpoint URL (e.g., http://localhost:9000)",
                field_type="TEXT",
                max_length=2000,
                is_secret=False
            ),
            AuthField(
                name="accessKey",
                display_name="Access Key",
                placeholder="Enter your MinIO Access Key",
                description="The Access Key from MinIO server",
                field_type="PASSWORD",
                max_length=2000,
                is_secret=True
            ),
            AuthField(
                name="secretKey",
                display_name="Secret Key",
                placeholder="Enter your MinIO Secret Key",
                description="The Secret Key from MinIO server",
                field_type="PASSWORD",
                max_length=2000,
                is_secret=True
            ),
            AuthField(
                name="useSsl",
                display_name="Use SSL/TLS",
                placeholder="",
                description="Enable SSL/TLS for secure connections (recommended for production)",
                field_type="BOOLEAN",
                max_length=0,
                is_secret=False,
                default_value=True
            ),
            AuthField(
                name="verifySsl",
                display_name="Verify SSL Certificate",
                placeholder="",
                description="Verify SSL certificate (disable for self-signed certificates)",
                field_type="BOOLEAN",
                max_length=0,
                is_secret=False,
                default_value=True
            )
        ])
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/minio.svg")
        .add_documentation_link(DocumentationLink(
            "MinIO Documentation",
            "https://min.io/docs/minio/linux/index.html",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/minio/minio',
            'pipeshub'
        ))
        .add_filter_field(FilterField(
            name="buckets",
            display_name="Bucket Names",
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            description="Select specific MinIO buckets to sync",
            option_source_type=OptionSourceType.DYNAMIC,
            default_value=[],
            default_operator=MultiselectOperator.IN.value
        ))
        .add_filter_field(FilterField(
            name="file_extensions",
            display_name="File Extensions",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            description="Filter files by extension (e.g., pdf, docx, txt). Leave empty to sync all files.",
            option_source_type=OptionSourceType.MANUAL,
            default_value=[],
            default_operator=ListOperator.IN.value
        ))
        .add_filter_field(CommonFields.modified_date_filter("Filter files and folders by modification date."))
        .add_filter_field(CommonFields.created_date_filter("Filter files and folders by creation date."))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(True)
    )\
    .build_decorator()
class MinIOConnector(S3CompatibleBaseConnector):
    """
    Connector for synchronizing data from MinIO S3-compatible object storage.

    MinIO is a high-performance, S3-compatible object storage server. This connector
    uses the same S3 API through aioboto3 but connects to a MinIO server instead
    of AWS S3.
    """

    @staticmethod
    def _parse_console_url(endpoint_url: str) -> str:
        """Parse endpoint URL to extract the console base URL.

        Args:
            endpoint_url: The MinIO endpoint URL (e.g., http://localhost:9000)

        Returns:
            The base console URL (e.g., http://localhost:9000)
        """
        parsed = urlparse(endpoint_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        endpoint_url: str = "http://localhost:9000",
    ) -> None:
        base_console_url = self._parse_console_url(endpoint_url)

        super().__init__(
            app=MinIOApp(connector_id),
            logger=logger,
            data_entities_processor=data_entities_processor,
            data_store_provider=data_store_provider,
            config_service=config_service,
            connector_id=connector_id,
            connector_name=Connectors.MINIO,
            filter_key="minio",
            base_console_url=base_console_url,
        )

        self.endpoint_url = endpoint_url

    async def init(self) -> bool:
        """Initializes the MinIO client using credentials from the config service."""
        config = await self.config_service.get_config(
            f"/services/connectors/{self.connector_id}/config"
        )
        if not config:
            self.logger.error("MinIO configuration not found.")
            return False

        auth_config = config.get("auth", {})
        access_key = auth_config.get("accessKey")
        secret_key = auth_config.get("secretKey")
        endpoint_url = auth_config.get("endpointUrl")
        self.bucket_name = auth_config.get("bucket")

        if not access_key or not secret_key:
            self.logger.error("MinIO access key or secret key not found in configuration.")
            return False

        if not endpoint_url:
            self.logger.error("MinIO endpoint URL not found in configuration.")
            return False

        # Update endpoint URL and console URL from config
        self.endpoint_url = endpoint_url
        self.base_console_url = self._parse_console_url(endpoint_url)
        # Keep data_entities_processor in sync with updated console URL
        self.data_entities_processor.base_console_url = self.base_console_url

        # Get connector scope and creator (support both camelCase and snake_case for compatibility)
        self.connector_scope = ConnectorScope.PERSONAL.value
        self.created_by = config.get("createdBy") or config.get("created_by")

        scope_from_config = config.get("scope")
        if scope_from_config:
            self.connector_scope = scope_from_config

        try:
            client = await MinIOClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id,
            )
            self.data_source = MinIODataSource(client)

            # Load connector filters
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "minio", self.connector_id, self.logger
            )

            self.logger.info("MinIO client initialized successfully.")
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize MinIO client: {e}", exc_info=True)
            return False

    async def _build_data_source(self) -> MinIODataSource:
        """Build the MinIODataSource."""
        client = await MinIOClient.build_from_services(
            logger=self.logger,
            config_service=self.config_service,
            connector_instance_id=self.connector_id,
        )
        return MinIODataSource(client)

    def _generate_web_url(self, bucket_name: str, normalized_key: str) -> str:
        """Generate the web URL for a MinIO object.

        MinIO console uses a different URL format than AWS S3 console.
        Format: {endpoint}/browser/{bucket}/{path}
        """
        # MinIO console browser URL format
        return f"{self.base_console_url}/browser/{bucket_name}/{normalized_key}"

    def _generate_parent_web_url(self, parent_external_id: str) -> str:
        """Generate the web URL for a MinIO parent folder/directory.

        MinIO console uses a different URL format than AWS S3 console.
        Format: {endpoint}/browser/{bucket}/{path}
        """
        bucket_name, path = parse_parent_external_id(parent_external_id)
        if path:
            return f"{self.base_console_url}/browser/{bucket_name}/{path}"
        else:
            return f"{self.base_console_url}/browser/{bucket_name}"

    async def _get_bucket_region(self, bucket_name: str) -> str:
        """Get the region for a bucket.

        For MinIO, regions are typically not used in the same way as AWS S3.
        MinIO servers are usually single-region deployments, so we return a
        default region value that satisfies the boto3 API requirements.

        Args:
            bucket_name: The bucket name (unused, but kept for API compatibility)

        Returns:
            A default region string ("us-east-1") as MinIO doesn't use regions.
        """
        # Check cache first for compatibility with parent class behavior
        if bucket_name in self.bucket_regions:
            return self.bucket_regions[bucket_name]

        # MinIO doesn't use regions like AWS S3; return default region for boto3 compatibility
        # and cache it to avoid repeated checks.
        region = "us-east-1"
        self.bucket_regions[bucket_name] = region
        return region

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        **kwargs,
    ) -> "MinIOConnector":
        """Factory method to create and initialize connector."""
        # Get endpoint URL from config for initial console URL setup.
        # Note: The actual config will be loaded again in init() to set up the client,
        # but we need endpoint_url here to initialize data_entities_processor correctly.
        config = await config_service.get_config(
            f"/services/connectors/{connector_id}/config"
        )
        endpoint_url = "http://localhost:9000"
        if config:
            auth_config = config.get("auth", {})
            endpoint_url = auth_config.get("endpointUrl", endpoint_url)

        base_console_url = cls._parse_console_url(endpoint_url)

        data_entities_processor = S3CompatibleDataSourceEntitiesProcessor(
            logger, data_store_provider, config_service,
            base_console_url=base_console_url
        )
        await data_entities_processor.initialize()

        connector = cls(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            endpoint_url=endpoint_url,
        )

        # Update processor with connector-specific URL generator
        data_entities_processor.parent_url_generator = lambda parent_external_id: connector._generate_parent_web_url(parent_external_id)

        return connector
