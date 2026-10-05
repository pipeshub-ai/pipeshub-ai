"""SMB 2.0.2–3.1.1 connector.

Dialects are negotiated by smbprotocol. SMB1 is rejected by the library.
Permissions are APP_LEVEL; inherit_permissions is set on records so a later
RECORD_LEVEL ACL pass can attach without a graph rewrite.

Limits: one server per instance; directory symlinks are not walked; file id 0
disables rename detection; no change notify; the connectors host must reach
TCP 445.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config.constants.arangodb import Connectors, PermissionModel
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
)
from app.connectors.core.constants import ConfigPaths, IconPaths
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
    FilterCollection,
    FilterField,
    FilterOptionsResponse,
    FilterType,
    MultiselectOperator,
    OptionSourceType,
    load_connector_filters,
)
from app.connectors.sources.network_share.entities_processor import (
    NetworkShareEntitiesProcessor,
)
from app.connectors.sources.network_share.errors import (
    DirectoryListingError,
    NetworkShareAuthError,
    ShareListingError,
)
from app.connectors.sources.network_share.operations import (
    create_share_groups,
    reindex_records,
    resolve_shares,
    share_filter_options,
    stream_file,
    walk_shares,
)
from app.connectors.sources.network_share.record_mapper import RecordMapper
from app.connectors.sources.smb.common.apps import SmbApp
from app.models.entities import AppUser, Record, User
from app.services.notification.types import NotificationSeverity, NotificationType
from app.sources.client.smb.smb import SmbClient
from app.sources.external.smb.smb import SmbDataSource

if TYPE_CHECKING:
    from logging import Logger

    from fastapi.responses import StreamingResponse

    from app.config.configuration_service import ConfigurationService
    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )
    from app.connectors.core.base.data_store.data_store import DataStoreProvider
    from app.connectors.sources.network_share.protocol import INetworkShareDataSource


@ConnectorBuilder("SMB")\
    .in_group("SMB")\
    .with_description("Sync files and folders from SMB 2/3 network shares")\
    .with_categories(["Storage"])\
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
    .with_permission_model(PermissionModel.APP_LEVEL)\
    .with_auth([
        AuthBuilder.type(AuthType.BASIC_AUTH).fields([
            AuthField(
                name="server",
                display_name="Server",
                placeholder="fileserver.example.com",
                description="Hostname or IP of the SMB server",
                field_type="TEXT",
                max_length=500,
            ),
            AuthField(
                name="port",
                display_name="Port",
                placeholder="445",
                description="SMB port. Default is 445.",
                field_type="NUMBER",
                default_value="445",
            ),
            AuthField(
                name="domain",
                display_name="Domain",
                placeholder="CORP",
                description="Optional AD / NetBIOS domain. Leave empty for a local account.",
                field_type="TEXT",
                max_length=200,
                required=False,
                min_length=0,
            ),
            AuthField(
                name="username",
                display_name="Username",
                placeholder="Enter username",
                description="Account with at least read access to the share",
                field_type="TEXT",
                max_length=200,
            ),
            AuthField(
                name="password",
                display_name="Password",
                placeholder="Enter password",
                description="Password for the SMB account",
                field_type="PASSWORD",
                max_length=500,
                is_secret=True,
            ),
            AuthField(
                name="share",
                display_name="Share",
                placeholder="departments",
                description="Share name to crawl when the shares filter is empty",
                field_type="TEXT",
                max_length=200,
            ),
        ])
    ])\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.SMB.value))
        .add_documentation_link(DocumentationLink(
            "SMB protocol",
            "https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-smb2/56066a30-52a3-438d-8538-ba3a5050002a",
            "setup",
        ))
        .add_documentation_link(DocumentationLink(
            "PipesHub Documentation",
            "https://docs.pipeshub.com/connectors/smb/smb",
            "pipeshub",
        ))
        .add_filter_field(FilterField(
            name="shares",
            display_name="Share Names",
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            description="Select specific SMB shares to sync",
            option_source_type=OptionSourceType.DYNAMIC,
            default_value=[],
            default_operator=MultiselectOperator.IN.value,
        ))
        .add_filter_field(CommonFields.folder_paths_filter("share"))
        .add_filter_field(CommonFields.file_extension_filter())
        .add_filter_field(CommonFields.modified_date_filter("Filter files by modification date."))
        .add_filter_field(CommonFields.created_date_filter("Filter files by creation date. Uses last-write time when the server omits created time."))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(True)
    )\
    .build_decorator()
class SmbConnector(BaseConnector):
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
            app=SmbApp(connector_id),
            logger=logger,
            data_entities_processor=data_entities_processor,
            data_store_provider=data_store_provider,
            config_service=config_service,
            connector_id=connector_id,
            scope=scope,
            created_by=created_by,
        )
        self.connector_name = Connectors.SMB
        self.filter_key = "smb"
        self.data_source: INetworkShareDataSource | None = None
        self.configured_share: str | None = None
        self.batch_size = 100
        self.mapper = RecordMapper()
        self.sync_filters = FilterCollection()
        self.indexing_filters = FilterCollection()
        self.record_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=self.data_entities_processor.org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=self.data_store_provider,
        )

    def get_app_users(self, users: list[User]) -> list[AppUser]:
        return [
            AppUser(
                app_name=self.connector_name,
                connector_id=self.connector_id,
                source_user_id=user.source_user_id or user.id or user.email,
                org_id=user.org_id or self.data_entities_processor.org_id,
                email=user.email,
                full_name=user.full_name or user.email,
                is_active=user.is_active if user.is_active is not None else True,
                title=user.title,
            )
            for user in users
            if user.email
        ]

    async def init(self) -> bool:
        try:
            config = await self.config_service.get_config(
                ConfigPaths.CONNECTOR_CONFIG.format(connector_id=self.connector_id)
            )
            if not config:
                await self._auth_error("Configuration not found", "SMB configuration not found.")
                return False
            auth = config.get("auth") or {}
            if not auth.get("password"):
                await self._auth_error("Credentials missing", "SMB password is required.")
                return False
            self.configured_share = (auth.get("share") or "").strip() or None
            await self._load_creator_email()
            client = await SmbClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id,
            )
            self.data_source = SmbDataSource(client)
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, self.filter_key, self.connector_id, self.logger
            )
            return True
        except NetworkShareAuthError as exc:
            await self._auth_error("Authentication failed", str(exc))
            return False
        except Exception as exc:
            self.logger.exception("SMB init failed")
            await self._auth_error("Connection failed", str(exc))
            return False

    async def test_connection_and_access(self) -> bool:
        if not self.data_source:
            return False
        try:
            share = self.configured_share
            if share:
                await self.data_source.list_directory(share, "")
            else:
                await self.data_source.list_shares()
            return True
        except (NetworkShareAuthError, DirectoryListingError, ShareListingError, OSError, FileNotFoundError) as exc:
            await self._auth_error("Connection test failed", str(exc))
            return False

    async def get_signed_url(self, record: Record) -> str | None:
        return None

    async def stream_record(
        self, record: Record, user_id: str | None = None, convertTo: str | None = None
    ) -> StreamingResponse:
        return await stream_file(
            data_source=self.data_source,
            record=record,
            display_name=self.display_name,
        )

    async def run_sync(self) -> None:
        await self._sync(prune=True)

    async def run_incremental_sync(self) -> None:
        # The scheduler calls run_sync. There is no change journal, and a
        # rename keeps timestamps, so this is the same complete listing.
        await self.run_sync()

    async def _sync(self, *, prune: bool) -> None:
        if not self.data_source:
            raise ConnectionError("SMB connector is not initialized.")
        self.sync_filters, self.indexing_filters = await load_connector_filters(
            self.config_service, self.filter_key, self.connector_id, self.logger
        )
        await self._ensure_scope_edges()
        shares = await resolve_shares(self.sync_filters, self.configured_share)
        if not shares:
            self.logger.warning("No SMB shares to sync")
            return
        await create_share_groups(
            share_names=shares,
            processor=self.data_entities_processor,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            scope=self.scope,
            created_by=self.created_by,
            creator_email=self.creator_email,
            description_prefix="SMB share",
        )
        await walk_shares(
            data_source=self.data_source,
            processor=self.data_entities_processor,
            mapper=self.mapper,
            logger=self.logger,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            batch_size=self.batch_size,
            scope=self.scope,
            created_by=self.created_by,
            creator_email=self.creator_email,
            shares=shares,
            sync_filters=self.sync_filters,
            indexing_filters=self.indexing_filters,
            record_sync_point=self.record_sync_point,
            prune=prune,
        )

    def handle_webhook_notification(self, notification: dict) -> None:
        raise NotImplementedError("SMB change notify is not a webhook strategy")

    async def cleanup(self) -> None:
        if self.data_source:
            await self.data_source.close()
        self.data_source = None
        await self._release_thread_lease()

    async def reindex_records(self, record_results: list[Record]) -> None:
        if not self.data_source:
            raise ConnectionError("SMB connector is not initialized.")
        await reindex_records(
            data_source=self.data_source,
            processor=self.data_entities_processor,
            mapper=self.mapper,
            records=record_results,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            scope=self.scope,
            created_by=self.created_by,
            creator_email=self.creator_email,
            indexing_filters=self.indexing_filters,
            logger=self.logger,
        )

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: str | None = None,
        cursor: str | None = None,
    ) -> FilterOptionsResponse:
        if filter_key != "shares":
            raise ValueError(f"Unsupported filter key: {filter_key}")
        return await share_filter_options(
            data_source=self.data_source,
            configured_share=self.configured_share,
            page=page,
            limit=limit,
            search=search,
        )

    async def _ensure_scope_edges(self) -> None:
        if self.scope == ConnectorScope.TEAM.value:
            await self.data_entities_processor.ensure_team_app_edge(self.connector_id)
            return
        if not self.created_by:
            return
        creator = await self.data_entities_processor.get_user_by_user_id(self.created_by)
        if creator and getattr(creator, "email", None):
            await self.data_entities_processor.on_new_app_users(self.get_app_users([creator]))

    async def _auth_error(self, title: str, message: str) -> None:
        await self.notify(
            type=NotificationType.CONNECTOR_AUTH_ERROR,
            severity=NotificationSeverity.ERROR,
            title=title,
            message=message,
            payload={"connectorId": self.connector_id, "connectorName": Connectors.SMB.value},
        )

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        data_entities_processor: DataSourceEntitiesProcessor,
        **kwargs: object,
    ) -> SmbConnector:
        processor = NetworkShareEntitiesProcessor(logger, data_store_provider, config_service)
        org_id = kwargs.get("org_id")
        if org_id:
            processor.org_id = org_id
        await processor.initialize()
        return cls(
            logger,
            processor,
            data_store_provider,
            config_service,
            connector_id,
            kwargs.get("scope") or ConnectorScope.PERSONAL.value,
            kwargs.get("created_by") or "",
        )
