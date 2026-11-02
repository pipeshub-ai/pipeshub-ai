"""
MinIO S3-Compatible Connector

Connector for synchronizing data from MinIO object storage. MinIO is an S3-compatible
object storage server, so this connector shares most functionality with the S3Connector
through the S3CompatibleBaseConnector base class.
"""

from contextlib import suppress
from logging import Logger
from urllib.parse import quote, urlparse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors, PermissionModel
from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.constants import CONNECTOR_MINIO_INFO, IconPaths
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
from app.sources.client.minio.minio import (
    MinIOClient,
    check_endpoint_address,
    resolve_endpoint_url,
)
from app.sources.external.minio.minio import MinIODataSource

# Entities processor for MinIO
MinIODataSourceEntitiesProcessor = S3CompatibleDataSourceEntitiesProcessor

# MinIO's documented layout (`minio server --console-address :9001`).
MINIO_DEFAULT_API_PORT = 9000
MINIO_DEFAULT_CONSOLE_PORT = 9001


@ConnectorBuilder("MinIO")\
    .in_group("MinIO")\
    .with_description("Sync files and folders from MinIO S3-compatible storage")\
    .with_categories(["Storage"])\
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
    .with_permission_model(PermissionModel.APP_LEVEL)\
    .with_auth([
        AuthBuilder.type(AuthType.ACCESS_KEY).fields([
            AuthField(
                name="endpointUrl",
                display_name="Endpoint URL",
                placeholder="http://localhost:9000 or https://minio.example.com",
                description="The address of the MinIO S3 API (e.g., http://localhost:9000)",
                field_type="TEXT",
                max_length=2000,
                is_secret=False
            ),
            AuthField(
                name="consoleUrl",
                display_name="Console URL",
                placeholder="http://localhost:9001 or https://console.minio.example.com",
                description="The address of the MinIO Console, used for links to files. "
                "Leave empty when the endpoint is on port 9000 and the Console on port 9001 of the same host.",
                field_type="TEXT",
                required=False,
                min_length=0,
                max_length=2000,
                is_secret=False
            ),
            AuthField(
                name="accessKey",
                display_name="Access Key",
                placeholder="Enter your MinIO Access Key",
                description="Access key of a key with read-only access to the buckets to sync",
                field_type="PASSWORD",
                max_length=2000,
                is_secret=True
            ),
            AuthField(
                name="secretKey",
                display_name="Secret Key",
                placeholder="Enter your MinIO Secret Key",
                description="Secret key of a key with read-only access to the buckets to sync",
                field_type="PASSWORD",
                max_length=2000,
                is_secret=True
            ),
            AuthField(
                name="useSsl",
                display_name="Use SSL/TLS",
                placeholder="",
                description="Connect over HTTPS when the endpoint URL has no http:// or https:// (recommended for production)",
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
    .with_info(CONNECTOR_MINIO_INFO)\
    .with_resilience_config(rate_limit=50, max_retries=3)\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.MINIO.value))
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
            description="Select the MinIO buckets to sync. Leave empty to sync every bucket the key can list.",
            option_source_type=OptionSourceType.DYNAMIC,
            default_operator=MultiselectOperator.IN.value
        ))
        .add_filter_field(FilterField(
            name="file_extensions",
            display_name="File Extensions",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            description="Filter files by extension (e.g., pdf, docx, txt). Leave empty to sync all files.",
            option_source_type=OptionSourceType.MANUAL,
            default_operator=ListOperator.IN.value
        ))
        .add_filter_field(CommonFields.folder_paths_filter("bucket"))
        .add_filter_field(CommonFields.modified_date_filter("Filter files and folders by modification date."))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(True)
    )\
    .build_decorator()
class MinIOConnector(S3CompatibleBaseConnector):
    """
    Connector for synchronizing data from MinIO S3-compatible object storage.

    MinIO is a high-performance, S3-compatible object storage server. This connector
    uses the same S3 API through aiobotocore but connects to a MinIO server instead
    of AWS S3.
    """

    @staticmethod
    def _parse_console_url(endpoint_url: str, console_url: str | None = None, *, use_ssl: bool = True) -> str:
        """The MinIO Console's base URL, which file links open.

        The Console is a separate server: the S3 API port reads ``/browser/...`` as a bucket
        named "browser" and answers 403. Without a configured Console URL, MinIO's default
        layout is assumed: API on 9000, Console on 9001 of the same host.

        Raises:
            ValueError: the configured Console URL cannot be linked to. It lands in every
                citation, so it is held to the endpoint's rules rather than replaced by a guess.
        """
        if console_url and console_url.strip():
            return resolve_endpoint_url(console_url, use_ssl=use_ssl, field="Console URL")
        parsed = urlparse(endpoint_url)
        netloc = parsed.netloc
        try:
            port = parsed.port
        except ValueError:
            port = None
        if port == MINIO_DEFAULT_API_PORT and parsed.hostname:
            host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
            netloc = f"{host}:{MINIO_DEFAULT_CONSOLE_PORT}"
        return f"{parsed.scheme}://{netloc}"

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
        endpoint_url: str = "http://localhost:9000",
        console_url: str | None = None,
    ) -> None:
        try:
            base_console_url = self._parse_console_url(endpoint_url, console_url)
        except ValueError:
            # init() refuses it with the reason; links need a placeholder until then.
            base_console_url = self._parse_console_url(endpoint_url)

        super().__init__(
            app=MinIOApp(connector_id),
            logger=logger,
            data_entities_processor=data_entities_processor,
            data_store_provider=data_store_provider,
            config_service=config_service,
            connector_id=connector_id,
            scope=scope,
            created_by=created_by,
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
        self.bucket_name = auth_config.get("bucket")

        if not access_key or not secret_key:
            self.logger.error("MinIO access key or secret key not found in configuration.")
            return False

        if not auth_config.get("endpointUrl"):
            self.logger.error("MinIO endpoint URL not found in configuration.")
            return False

        use_ssl = auth_config.get("useSsl", True)
        try:
            endpoint_url = resolve_endpoint_url(auth_config["endpointUrl"], use_ssl=use_ssl)
            await check_endpoint_address(endpoint_url)
            base_console_url = self._parse_console_url(endpoint_url, auth_config.get("consoleUrl"), use_ssl=use_ssl)
        except ValueError as e:
            self.logger.error(f"MinIO connection settings refused: {e}")
            raise ConnectorInitError(f"{self.display_name}: {e}") from e

        self.endpoint_url = endpoint_url
        self.base_console_url = base_console_url
        # Keep data_entities_processor in sync with updated console URL
        self.data_entities_processor.base_console_url = self.base_console_url

        try:
            client = self._with_retry_budget(await MinIOClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id,
            ))
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
        client = self._with_retry_budget(await MinIOClient.build_from_services(
            logger=self.logger,
            config_service=self.config_service,
            connector_instance_id=self.connector_id,
        ))
        return MinIODataSource(client)

    def _generate_web_url(self, bucket_name: str, normalized_key: str) -> str:
        """Generate the web URL for a MinIO object.

        MinIO console uses a different URL format than AWS S3 console.
        Format: {endpoint}/browser/{bucket}/{path}
        """
        return f"{self.base_console_url}/browser/{quote(bucket_name, safe='')}/{quote(normalized_key, safe='')}"

    def _generate_parent_web_url(self, parent_external_id: str) -> str:
        """Generate the web URL for a MinIO parent folder/directory.

        MinIO console uses a different URL format than AWS S3 console.
        Format: {endpoint}/browser/{bucket}/{path}
        """
        bucket_name, path = parse_parent_external_id(parent_external_id)
        if path:
            return f"{self.base_console_url}/browser/{quote(bucket_name, safe='')}/{quote(path, safe='')}"
        else:
            return f"{self.base_console_url}/browser/{quote(bucket_name, safe='')}"

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
        scope: str,
        created_by: str,
        data_entities_processor,
        **kwargs,
    ) -> "MinIOConnector":
        """Factory method to create and initialize connector."""
        org_id = kwargs.pop("org_id", None)
        # Get endpoint URL from config for initial console URL setup.
        # Note: The actual config will be loaded again in init() to set up the client,
        # but we need endpoint_url here to initialize data_entities_processor correctly.
        config = await config_service.get_config(
            f"/services/connectors/{connector_id}/config"
        )
        endpoint_url = "http://localhost:9000"
        console_url = None
        if config:
            auth_config = config.get("auth", {})
            console_url = auth_config.get("consoleUrl")
            # init() refuses a bad one with the reason; the processor only needs a placeholder until then.
            with suppress(ValueError):
                endpoint_url = resolve_endpoint_url(
                    auth_config.get("endpointUrl", endpoint_url), use_ssl=auth_config.get("useSsl", True)
                )

        try:
            base_console_url = cls._parse_console_url(endpoint_url, console_url)
        except ValueError:
            base_console_url = cls._parse_console_url(endpoint_url)

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
            endpoint_url=endpoint_url,
            console_url=console_url,
        )

        # Update processor with connector-specific URL generator
        data_entities_processor.parent_url_generator = lambda parent_external_id: connector._generate_parent_web_url(parent_external_id)

        return connector
