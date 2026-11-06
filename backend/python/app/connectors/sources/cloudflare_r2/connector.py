"""Cloudflare R2 connector. R2 speaks the S3 API, so the sync itself comes from S3CompatibleBaseConnector."""

from logging import Logger
from urllib.parse import quote

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors, PermissionModel
from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.constants import CONNECTOR_CLOUDFLARE_R2_INFO, IconPaths
from app.connectors.core.registry.auth_builder import AuthBuilder, AuthType
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
from app.connectors.sources.cloudflare_r2.common.apps import CloudflareR2App
from app.connectors.sources.s3.base_connector import (
    S3CompatibleBaseConnector,
    parse_parent_external_id,
)
from app.sources.client.cloudflare_r2.cloudflare_r2 import R2_REGION, CloudflareR2Client
from app.sources.external.cloudflare_r2.cloudflare_r2 import CloudflareR2DataSource

CLOUDFLARE_DASHBOARD_URL = "https://dash.cloudflare.com"

_TOKEN_HINT = "Check that the R2 API token can list buckets and read objects. An Admin Read only token can do both."


@ConnectorBuilder("Cloudflare R2")\
    .in_group("Cloudflare R2")\
    .with_description("Sync files and folders from Cloudflare R2 buckets")\
    .with_categories(["Storage"])\
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
    .with_permission_model(PermissionModel.APP_LEVEL)\
    .with_auth([
        AuthBuilder.type(AuthType.ACCESS_KEY).fields([
            AuthField(
                name="accountId",
                display_name="Account ID",
                placeholder="32 hexadecimal characters",
                description="Your Cloudflare account ID, shown on the R2 overview page of the dashboard",
                field_type="TEXT",
                max_length=64,
                is_secret=False
            ),
            AuthField(
                name="accessKey",
                display_name="Access Key ID",
                placeholder="Enter the R2 token's Access Key ID",
                description="Access Key ID of a read-only R2 API token",
                field_type="PASSWORD",
                max_length=2000,
                is_secret=True
            ),
            AuthField(
                name="secretKey",
                display_name="Secret Access Key",
                placeholder="Enter the R2 token's Secret Access Key",
                description="Secret Access Key of a read-only R2 API token",
                field_type="PASSWORD",
                max_length=2000,
                is_secret=True
            ),
            AuthField(
                name="jurisdiction",
                display_name="Jurisdiction",
                placeholder="eu, fedramp or us",
                description="Only for buckets created in a jurisdiction. Otherwise leave empty.",
                field_type="TEXT",
                required=False,
                min_length=0,
                max_length=16,
                is_secret=False
            ),
        ])
    ])\
    .with_info(CONNECTOR_CLOUDFLARE_R2_INFO)\
    .with_resilience_config(rate_limit=50, max_retries=3)\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.CLOUDFLARE_R2.value))
        .add_documentation_link(DocumentationLink(
            "R2 API Tokens",
            "https://developers.cloudflare.com/r2/api/tokens/",
            "setup"
        ))
        .add_filter_field(FilterField(
            name="buckets",
            display_name="Bucket Names",
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            description="Select the R2 buckets to sync. Leave empty to sync every bucket the token can list.",
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
class CloudflareR2Connector(S3CompatibleBaseConnector):
    """Syncs Cloudflare R2 buckets."""

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
            app=CloudflareR2App(connector_id),
            logger=logger,
            data_entities_processor=data_entities_processor,
            data_store_provider=data_store_provider,
            config_service=config_service,
            connector_id=connector_id,
            scope=scope,
            created_by=created_by,
            connector_name=Connectors.CLOUDFLARE_R2,
            filter_key="cloudflarer2",
            base_console_url=CLOUDFLARE_DASHBOARD_URL,
        )

    async def init(self) -> bool:
        """Build the client from the saved settings; settings it refuses reach the admin with the reason."""
        try:
            self.data_source = await self._build_data_source()
        except ValueError as e:
            self.logger.error(f"Cloudflare R2 connection settings refused: {e}")
            raise ConnectorInitError(f"{self.display_name}: {e}") from e

        config = await self.config_service.get_config(f"/services/connectors/{self.connector_id}/config")
        auth = config.get("auth") or {}
        # The client has already validated both values.
        account = auth["accountId"].strip().lower()
        jurisdiction = (auth.get("jurisdiction") or "").strip().lower() or "default"
        self.base_console_url = f"{CLOUDFLARE_DASHBOARD_URL}/{account}/r2/{jurisdiction}/buckets"

        self.sync_filters, self.indexing_filters = await load_connector_filters(
            self.config_service, self.filter_key, self.connector_id, self.logger
        )
        return True

    async def _build_data_source(self) -> CloudflareR2DataSource:
        client = await CloudflareR2Client.build_from_services(
            logger=self.logger,
            config_service=self.config_service,
            connector_instance_id=self.connector_id,
        )
        return CloudflareR2DataSource(self._with_retry_budget(client))

    def _generate_web_url(self, bucket_name: str, normalized_key: str) -> str:
        # Cloudflare documents neither link format; both were written without a copied dashboard link to check against.
        if normalized_key.endswith("/"):
            return self._generate_parent_web_url(f"{bucket_name}/{normalized_key}")
        return f"{self.base_console_url}/{quote(bucket_name, safe='')}/objects/{quote(normalized_key, safe='')}/details"

    def _generate_parent_web_url(self, parent_external_id: str) -> str:
        bucket_name, path = parse_parent_external_id(parent_external_id)
        url = f"{self.base_console_url}/{quote(bucket_name, safe='')}"
        return f"{url}?prefix={quote(path, safe='/')}" if path else url

    async def _get_bucket_region(self, bucket_name: str) -> str:
        # R2 signs for "auto" whatever location a bucket reports, so asking would only spend a request.
        return R2_REGION

    async def _access_problem(self) -> str | None:
        problem = await super()._access_problem()
        # The base class words its hint as an AWS IAM policy, which an R2 token does not have.
        if problem and " The key needs " in problem:
            reason = problem.split(" The key needs ", 1)[0]
            # R2 answers 403 to a wrong key too, and that reason already says which field to fix.
            return reason if "SignatureDoesNotMatch" in reason or "InvalidAccessKeyId" in reason else f"{reason} {_TOKEN_HINT}"
        return problem

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
        data_entities_processor: DataSourceEntitiesProcessor,
        **kwargs: object,
    ) -> "CloudflareR2Connector":
        org_id = kwargs.pop("org_id", None)
        data_entities_processor = DataSourceEntitiesProcessor(logger, data_store_provider, config_service)
        await data_entities_processor.initialize(org_id=org_id)
        return cls(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
