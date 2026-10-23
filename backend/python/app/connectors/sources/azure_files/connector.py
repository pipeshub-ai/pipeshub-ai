"""
Azure Files Connector

Connector for synchronizing data from Azure File Shares. This connector
supports real hierarchical directories (unlike S3/Azure Blob where directories
are virtual prefix-based paths).

Key differences from Azure Blob/S3:
- Directories are real entities, listed by a recursive walk
- Like S3/Azure Blob, folders are stored as FILE records (is_file=False,
  folder mimeType, internal) under the share's FILE_SHARE record group, not as
  record groups of their own; this is the model for file-storage connectors
- weburl is the file's REST endpoint, which a browser cannot open without a SAS,
  so it is hidden (hide_weburl=True) and citations open the file in PipesHub
- Access is APP_LEVEL: share-level RBAC and NTFS ACLs are not read
"""

import asyncio
import base64
import mimetypes
import uuid
from collections.abc import AsyncGenerator, Iterable
from datetime import datetime, timedelta, timezone
from logging import Logger
from typing import Any
from urllib.parse import quote, urlparse

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
    PermissionModel,
    ProgressStatus,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.connector_service import (
    BaseConnector,
    ConnectorInitError,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.error.stream_errors import (
    connector_not_ready,
    map_source_status,
    not_downloadable,
    to_stream_error,
)
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
    generate_record_sync_point_key,
)
from app.connectors.core.constants import IconPaths
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
    FilterCollection,
    FilterField,
    FilterOperator,
    FilterOption,
    FilterOptionsResponse,
    FilterType,
    IndexingFilterKey,
    MultiselectOperator,
    OptionSourceType,
    SyncFilterKey,
    included_names,
    load_connector_filters,
    name_passes_filter,
)
from app.connectors.core.registry.folder_scope import FolderScope
from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage
from app.connectors.sources.azure_files.common.apps import AzureFilesApp
from app.models.entities import (
    AppUser,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    User,
)
from app.models.permission import Permission
from app.services.graph_db.common.record_visibility import is_live_record
from app.services.notification.types import NotificationSeverity, NotificationType
from app.sources.client.azure.azure_files import AzureFilesClient, AzureFilesResponse
from app.sources.external.azure.azure_files import (
    CREDENTIALS_REJECTED,
    AzureFilesDataSource,
    describe_error,
    error_code_of,
)
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import datetime_to_epoch_ms, get_epoch_timestamp_in_ms
from app.utils.url_fetcher import FetchError, validate_public_http_url

# Default connector endpoint for signed URL generation
DEFAULT_CONNECTOR_ENDPOINT = "http://localhost:8000"
# Entries per directory listing page (the service's maximum).
LISTING_PAGE_SIZE = 5000
# Directories listed at once within a share, under the 50 requests/s limiter.
DIRECTORY_CONCURRENCY = 8
# Stale records removed per delete transaction.
DELETE_BATCH_SIZE = 500
# Long enough to start the download it is minted for.
SAS_URL_LIFETIME = timedelta(minutes=5)
# Azure error codes that mean a share is gone, not unreachable.
_SHARE_GONE_CODES = frozenset({"ShareNotFound"})
# Codes for a share, folder or file that does not exist.
_NOT_FOUND_CODES = _SHARE_GONE_CODES | {"ResourceNotFound", "ParentNotFound"}
# The credential itself was refused: a rotated account key, an expired SAS, a bad Entra ID secret.
_CREDENTIALS_REJECTED_CODES = frozenset({"AuthenticationFailed", "InvalidAuthenticationInfo", CREDENTIALS_REJECTED})
# Azure's answers for a credential that is valid but not allowed the call (a SAS or role
# without a permission, service or resource type); other failures name no permission.
_PERMISSION_CODES = frozenset({
    "AuthorizationPermissionMismatch", "AuthorizationFailure",
    "AuthorizationResourceTypeMismatch", "AuthorizationServiceMismatch",
})


async def _endpoint_problem(url: str) -> str | None:
    """Why the file endpoint must not be used, or None."""
    if not url:
        # The SDK builds the default *.file.core.windows.net endpoint from the account name.
        return None
    if urlparse(url).scheme != "https":
        return f"The file endpoint must use https: {url}"
    try:
        # Resolves the host: off the event loop.
        await asyncio.to_thread(validate_public_http_url, url)
    except FetchError as e:
        return f"The file endpoint is not a public address: {e}"
    return None


class CredentialsRejectedError(ConnectionError):
    """Azure refused the connector's credential, so nothing more can be read this run."""

SETUP_INFO = (
    "Use the least-privileged credential that can read your shares: Microsoft Entra ID "
    "with the Storage File Data Privileged Reader role, or a SAS connection string for "
    "the File service with only Read and List permissions (service, container and object "
    "resource types). An account key connection string gives full read, write and delete "
    "access to everything in the storage account. Share-level permissions and NTFS folder "
    "permissions are not applied: every synced file is visible to everyone with access to "
    "this connector, so for a team connector pick only shares the whole organisation may read "
    "(a team connector syncs nothing until File Share Names names at least one share)."
)


def _file_url(base_url: str, share_name: str, path: str = "") -> str:
    """URL of a share, folder or file under the account's file endpoint."""
    url = f"{base_url}/{quote(share_name)}"
    return f"{url}/{quote(path)}" if path else url


def get_file_extension(file_path: str) -> str | None:
    """Extracts the extension from a file path."""
    # From the name only: a dot in a folder name ("reports.2024/README") is not an extension.
    name = file_path.rsplit("/", 1)[-1]
    if "." in name:
        parts = name.split(".")
        if len(parts) > 1:
            return parts[-1].lower()
    return None


def get_parent_path(file_path: str) -> str | None:
    """Extracts the parent path from a file path.

    For a path like 'a/b/c/file.txt', returns 'a/b/c'
    For a path like 'a/b/c', returns 'a/b'
    For a root-level file 'file.txt', returns None
    """
    if not file_path:
        return None
    # Remove trailing slash if present
    normalized_path = file_path.rstrip("/")
    if "/" not in normalized_path:
        return None
    parent_path = "/".join(normalized_path.split("/")[:-1])
    return parent_path if parent_path else None


def get_mimetype_for_azure_files(file_path: str, *, is_directory: bool = False) -> str:
    """Determines the correct MimeTypes string value for an Azure file."""
    if is_directory:
        return MimeTypes.FOLDER.value

    mime_type_str, _ = mimetypes.guess_type(file_path)
    if mime_type_str:
        try:
            return MimeTypes(mime_type_str).value
        except ValueError:
            return MimeTypes.BIN.value
    return MimeTypes.BIN.value


def _share_last_modified_epoch_ms_from_list(
    shares_data: Iterable[Any] | None, target_names: set[str]
) -> dict[str, int]:
    """Map share name -> last_modified as epoch ms from list_shares data."""
    out: dict[str, int] = {}
    if not shares_data or not target_names:
        return out
    for share in shares_data:
        name: str | None = None
        lm: Any = None
        if isinstance(share, dict):
            name = share.get("name")
            lm = share.get("last_modified")
        else:
            name = getattr(share, "name", None)
            lm = getattr(share, "last_modified", None)
        if not name or name not in target_names:
            continue
        ts: int | None = None
        if lm is not None:
            if isinstance(lm, datetime):
                ts = datetime_to_epoch_ms(lm)
            else:
                ts = datetime_to_epoch_ms(str(lm))
        if ts is not None:
            out[name] = ts
    return out


class AzureFilesDataSourceEntitiesProcessor(DataSourceEntitiesProcessor):
    """Azure Files processor that handles directory placeholder records similar to other object storage connectors."""

    def __init__(
        self,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        account_name: str = "",
    ) -> None:
        super().__init__(logger, data_store_provider, config_service)
        self.account_name = account_name
        # The account's file endpoint, set by the connector from its client.
        self.account_url = ""

    def _create_placeholder_parent_record(
        self,
        parent_external_id: str,
        parent_record_type: RecordType,
        record: Record,
        record_name: str | None = None,
        record_group_type: str | None = None,
        external_record_group_id: str | None = None,
    ) -> Record:
        """Create a placeholder parent record with Azure Files-specific handling."""
        parent_record = super()._create_placeholder_parent_record(
            parent_external_id,
            parent_record_type,
            record,
            record_name=record_name,
            record_group_type=record_group_type,
            external_record_group_id=external_record_group_id,
        )

        if parent_record_type == RecordType.FILE and isinstance(parent_record, FileRecord):
            weburl = self._generate_directory_url(parent_external_id)
            path = self._extract_path_from_external_id(parent_external_id)
            parent_record.weburl = weburl
            parent_record.path = path
            # Match Azure Blob / GCS behavior: parent directory placeholders are internal and weburl is hidden
            parent_record.is_internal = True
            parent_record.hide_weburl = True

        return parent_record

    def _generate_directory_url(self, parent_external_id: str) -> str:
        """Generate URL for an Azure Files directory.

        Args:
            parent_external_id: External ID in format "share_name/path" or just "share_name"

        Returns:
            Azure Files URL for the directory
        """
        share_name, _, path = parent_external_id.partition("/")
        base_url = self.account_url or f"https://{self.account_name}.file.core.windows.net"
        return _file_url(base_url, share_name, path)

    def _extract_path_from_external_id(self, parent_external_id: str) -> str | None:
        """Extract path from external ID.

        Args:
            parent_external_id: External ID in format "share_name/path" or just "share_name"

        Returns:
            Path without share name prefix, or None for root
        """
        if "/" in parent_external_id:
            parts = parent_external_id.split("/", 1)
            return parts[1]
        return None


@ConnectorBuilder("Azure Files")\
    .in_group("Azure")\
    .with_description("Sync files and folders from Azure File Shares")\
    .with_categories(["Storage"])\
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
    .with_permission_model(PermissionModel.APP_LEVEL)\
    .with_auth([
        AuthBuilder.type(AuthType.CONNECTION_STRING).fields([
            AuthField(
                name="connectionString",
                display_name="Connection String",
                placeholder="FileEndpoint=https://<account>.file.core.windows.net/;SharedAccessSignature=sv=...",
                description=(
                    "Prefer a SAS connection string (Storage account > Shared access signature: "
                    "File service, Read and List permissions, Service, Container and Object resource "
                    "types). An account key connection string (Access keys) also works but gives full "
                    "write and delete access to the whole storage account."
                ),
                field_type="PASSWORD",
                max_length=2000,
                is_secret=True
            ),
        ]),
        AuthBuilder.type(AuthType.ENTRA_ID).fields([
            AuthField(
                name="accountUrl",
                display_name="File Endpoint",
                placeholder="https://<account>.file.core.windows.net",
                description="The storage account's file endpoint",
                field_type="URL",
                max_length=2000,
                is_secret=False
            ),
            AuthField(
                name="tenantId",
                display_name="Tenant ID",
                placeholder="00000000-0000-0000-0000-000000000000",
                description="Directory (tenant) ID of the Entra ID app registration",
                field_type="TEXT",
                max_length=100,
                is_secret=False
            ),
            AuthField(
                name="clientId",
                display_name="Client ID",
                placeholder="00000000-0000-0000-0000-000000000000",
                description="Application (client) ID; give the app the Storage File Data Privileged Reader role",
                field_type="TEXT",
                max_length=100,
                is_secret=False
            ),
            AuthField(
                name="clientSecret",
                display_name="Client Secret",
                placeholder="Client secret value",
                description="A client secret of the app registration",
                field_type="PASSWORD",
                max_length=500,
                is_secret=True
            ),
        ]),
    ])\
    .with_info(SETUP_INFO)\
    .with_resilience_config(rate_limit=50, max_retries=3)\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.AZURE_FILES.value))
        .add_documentation_link(DocumentationLink(
            "Azure Files Setup",
            "https://learn.microsoft.com/en-us/azure/storage/files/storage-files-introduction",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/azure-files/azure-files',
            'pipeshub'
        ))
        .add_filter_field(FilterField(
            name="shares",
            display_name="File Share Names",
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            description="Select the Azure File Shares to sync. Required for a team connector: everything synced is visible to the whole organisation.",
            option_source_type=OptionSourceType.DYNAMIC,
            default_value=[],
            default_operator=MultiselectOperator.IN.value
        ))
        .add_filter_field(CommonFields.folder_paths_filter("share"))
        .add_filter_field(CommonFields.file_extension_filter())
        .add_filter_field(CommonFields.modified_date_filter("Filter files and folders by modification date."))
        .add_filter_field(CommonFields.created_date_filter("Filter files and folders by creation date."))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(True)
    )\
    .build_decorator()
class AzureFilesConnector(BaseConnector):
    """
    Connector for synchronizing data from Azure File Shares.

    Key features:
    - Supports real hierarchical directories (not virtual like S3)
    - Recursive directory traversal
    - SAS URL generation for file access
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
            app=AzureFilesApp(connector_id),
            logger=logger,
            data_entities_processor=data_entities_processor,
            data_store_provider=data_store_provider,
            config_service=config_service,
            connector_id=connector_id,
            scope=scope,
            created_by=created_by,
        )

        self.connector_name = Connectors.AZURE_FILES
        self.connector_id = connector_id
        self.filter_key = "azurefiles"

        # Initialize sync point for tracking record changes
        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_id=self.connector_id,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider,
            )

        self.record_sync_point = _create_sync_point(SyncDataPointType.RECORDS)

        self.data_source: AzureFilesDataSource | None = None
        self.batch_size = 100
        self.share_name: str | None = None
        self.creator_email: str | None = None  # Cached to avoid repeated DB queries
        self.account_name: str | None = None
        # The account's file endpoint (sovereign clouds and custom endpoints included).
        self.account_url: str = ""
        # Set when the last run could not read a share, folder or item.
        self._sync_incomplete = False
        # Shares whose root could not be listed in the current run.
        self._unread_shares: set[str] = set()
        # Shares found deleted this run, whether before or during their walk.
        self._gone_shares: set[str] = set()

        # Initialize filter collections
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

    def get_app_users(self, users: list[User]) -> list[AppUser]:
        """Convert User objects to AppUser objects for Azure Files connector."""
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
        """Initializes the Azure Files client using connection string from the config service."""
        config = await self.config_service.get_config(
            f"/services/connectors/{self.connector_id}/config"
        )
        if not config:
            self.logger.error("Azure Files configuration not found.")
            await self._notify_problem(
                NotificationType.CONNECTOR_AUTH_ERROR, "Configuration not found",
                "The connector's configuration could not be read; open it and save its credentials again.",
            )
            return False

        auth_config = config.get("auth", {})
        if auth_config.get("authType") != AuthType.ENTRA_ID and not auth_config.get("connectionString"):
            self.logger.error("Azure Files connectionString not found in configuration.")
            await self._notify_problem(
                NotificationType.CONNECTOR_AUTH_ERROR, "Credentials missing",
                "The Azure Files connection string is missing.",
            )
            return False

        # Load creator email if needed (for personal scope permission creation)
        await self._load_creator_email()

        try:
            client = await AzureFilesClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id,
            )
            try:
                self.account_name = client.get_account_name()
            except Exception as e:
                self.logger.warning(f"Could not read the account name from the credentials: {e}")
            # Every request goes to this endpoint; an admin-supplied one must not reach internal hosts.
            # Read outside the try above: an endpoint that cannot be read cannot be checked.
            try:
                self.account_url = client.get_account_url()
            except Exception as e:
                raise ConnectorInitError(f"{self.display_name}: could not read the file endpoint from the credentials") from e
            problem = await _endpoint_problem(self.account_url)
            if problem:
                raise ConnectorInitError(f"{self.display_name}: {problem}")
            self.data_source = AzureFilesDataSource(client)

            if isinstance(
                self.data_entities_processor, AzureFilesDataSourceEntitiesProcessor
            ):
                self.data_entities_processor.account_name = self.account_name or ""
                self.data_entities_processor.account_url = self.account_url

            # Load connector filters
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, self.filter_key, self.connector_id, self.logger
            )

            self.logger.info("Azure Files client initialized successfully.")
            return True
        except ConnectorInitError as e:
            self.logger.error(str(e))
            await self._notify_problem(NotificationType.CONNECTOR_AUTH_ERROR, "Endpoint refused", str(e))
            raise
        except Exception as e:
            self.logger.error(
                f"Failed to initialize Azure Files client: {e}", exc_info=True
            )
            await self._notify_problem(
                NotificationType.CONNECTOR_AUTH_ERROR, "Could not connect",
                f"Failed to initialize the Azure Files client: {e}",
            )
            return False

    @staticmethod
    def _extract_account_name_from_connection_string(
        connection_string: str,
    ) -> str | None:
        """Extract account name from an Azure Storage connection string."""
        for part in connection_string.split(";"):
            if not part or "=" not in part:
                continue
            key, value = part.split("=", 1)
            if key == "AccountName":
                return value or None
        return None

    def _base_url(self) -> str:
        return self.account_url or f"https://{self.account_name}.file.core.windows.net"

    def _generate_web_url(self, share_name: str, file_path: str) -> str:
        """Generate the web URL for an Azure file."""
        return _file_url(self._base_url(), share_name, file_path)

    def _generate_directory_url(self, share_name: str, dir_path: str) -> str:
        """Generate the web URL for an Azure Files directory."""
        return _file_url(self._base_url(), share_name, dir_path)

    async def _notify_problem(self, notification_type: NotificationType, title: str, message: str) -> None:
        """Tell the connector's owner, naming the instance; never fails the caller."""
        try:
            await self.notify(
                type=notification_type,
                severity=NotificationSeverity.ERROR,
                title=f"{self.display_name}: {title}",
                message=f"{self.display_name}: {message}",
            )
        except Exception as e:
            self.logger.warning(f"Could not send the notification \"{title}\": {e}")

    def full_sync_coverage(self) -> FullSyncCoverage:
        """A run that could not read a share, a folder or an item did not see all of the
        source, so the links of what it did not write must stay."""
        return FullSyncCoverage(complete=not self._sync_incomplete)

    async def run_sync(self) -> None:
        """Runs a full synchronization from file shares."""
        self._sync_incomplete = False
        self._unread_shares = set()
        self._gone_shares = set()
        try:
            self.logger.info("Starting Azure Files full sync.")

            if not self.data_source:
                raise ConnectionError("Azure Files connector is not initialized.")

            # Reload sync and indexing filters to pick up configuration changes
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, self.filter_key, self.connector_id, self.logger
            )

            # Get sync filters
            sync_filters = (
                self.sync_filters
                if hasattr(self, "sync_filters") and self.sync_filters
                else FilterCollection()
            )

            selected_shares = included_names(sync_filters, "shares")
            if self.scope == ConnectorScope.TEAM.value and not selected_shares:
                # Every synced file is readable by the whole organisation, so a team
                # connector must not pick up every share the credential can list, nor
                # every share but a few (Not in), which takes in shares created later.
                message = (
                    "A team connector syncs only the shares named in File Share Names (operator In), "
                    "since everything it syncs is visible to the whole organisation. Select the "
                    "shares to sync in the connector's filters."
                )
                await self._notify_problem(NotificationType.CONNECTOR_SYNC_ERROR, "No file shares selected", message)
                raise ValueError(message)

            if self.scope == ConnectorScope.TEAM.value:
                await self.data_entities_processor.ensure_team_app_edge(
                    self.connector_id
                )
            else:
                # Personal: create user-app edge only for the creator
                if self.created_by:
                    creator_user = await self.data_entities_processor.get_user_by_user_id(self.created_by)
                    if creator_user and getattr(creator_user, "email", None):
                        app_users = self.get_app_users([creator_user])
                        await self.data_entities_processor.on_new_app_users(app_users)
                    else:
                        self.logger.warning(
                            "Creator user not found or has no email for created_by %s; skipping user-app edges.",
                            self.created_by,
                        )
                else:
                    self.logger.warning(
                        "Personal connector has no created_by; skipping user-app edges."
                    )

            # List all shares or use configured share
            shares_to_sync: list[str] = []
            shares_list_payload: list[Any] | None = None
            share_ts_ms: dict[str, int] = {}
            if selected_shares:
                share_ts_ms, self._gone_shares = await self._selected_share_timestamps_and_gone(selected_shares)
                shares_to_sync = [n for n in selected_shares if n not in self._gone_shares]
                self.logger.info(f"Using filtered shares: {shares_to_sync}")
            else:
                self.logger.info("Listing all shares...")
                shares_response = await self.data_source.list_shares()
                if not shares_response.success:
                    self._sync_incomplete = True
                    if shares_response.error_code in _CREDENTIALS_REJECTED_CODES:
                        raise CredentialsRejectedError(f"Listing file shares was refused: {shares_response.error_code}")
                    await self._notify_problem(
                        NotificationType.CONNECTOR_SYNC_ERROR, "Could not list file shares",
                        f"Listing the storage account's file shares failed: {shares_response.error}. "
                        "Nothing was removed; the next sync tries again.",
                    )
                    raise ConnectionError(f"Failed to list shares: {shares_response.error}")

                # An empty account still runs the cleanup below for shares that are gone.
                shares_list_payload = shares_response.data or []
                shares_to_sync = [
                    share.get("name")
                    for share in shares_list_payload
                    if share.get("name") and name_passes_filter(sync_filters, "shares", share.get("name"))
                ]
                self.logger.info(f"Found {len(shares_to_sync)} share(s) to sync")
                share_ts_ms = _share_last_modified_epoch_ms_from_list(
                    shares_list_payload, set(shares_to_sync)
                )

            target_names = {n for n in shares_to_sync if n}

            # Create record groups for shares first
            await self._create_record_groups_for_shares(shares_to_sync, share_ts_ms)

            # Sync each share. Deletes are decided per share, so a share that
            # cannot be read holds back only its own.
            failed_shares: list[str] = []
            for share_name in dict.fromkeys(n for n in shares_to_sync if n):
                try:
                    self.logger.info(f"Syncing share: {share_name}")
                    share_seen, share_complete = await self._sync_share(share_name)
                except CredentialsRejectedError:
                    raise
                except Exception as e:
                    share_seen, share_complete = set(), False
                    self._unread_shares.add(share_name)
                    self.logger.error(
                        f"Error syncing share {share_name}: {e}", exc_info=True
                    )
                if share_complete:
                    await self._remove_records_not_seen(share_name, share_seen)
                else:
                    failed_shares.append(share_name)

            removed = await self._remove_unsynced_shares(target_names - self._gone_shares)
            for share_name in sorted(removed & self._gone_shares):
                await self._notify_problem(
                    NotificationType.CONNECTOR_SYNC_ERROR, f"File share {share_name} not found",
                    f"The file share {share_name} selected in File Share Names no longer exists, so its "
                    "files were removed. Remove it from the filter if it is gone for good.",
                )

            if failed_shares:
                self._sync_incomplete = True
                self.logger.warning(
                    f"Some listings failed in {failed_shares}; not removing records of files "
                    "in them that were not seen this sync"
                )
                await self._notify_problem(
                    NotificationType.CONNECTOR_SYNC_ERROR, "Some file shares could not be read",
                    f"Could not read all of {', '.join(failed_shares)}. Files deleted there stay "
                    "searchable until a sync reads them in full; see the connector logs.",
                )
                if self._unread_shares >= target_names:
                    raise RuntimeError(f"No share could be read: {', '.join(sorted(self._unread_shares))}")

            self.logger.info("Azure Files full sync completed.")
        except CredentialsRejectedError as ex:
            self._sync_incomplete = True
            self.logger.error(f"Azure Files sync stopped: {ex}")
            await self._notify_problem(
                NotificationType.CONNECTOR_AUTH_ERROR, "Credentials rejected",
                "Azure refused the connector's credentials, so the sync stopped and nothing was removed. "
                "An expired SAS, a rotated account key or a changed Entra ID client secret causes this; "
                "update the connector's credentials.",
            )
            raise
        except Exception as ex:
            self._sync_incomplete = True
            self.logger.error(f"Error in Azure Files connector run: {ex}", exc_info=True)
            raise

    async def _selected_share_timestamps_and_gone(self, share_names: list[str]) -> tuple[dict[str, int], set[str]]:
        """Last-modified times of the selected shares, and those that no longer exist.

        A share is gone only when Azure says so (ShareNotFound): an auth or network
        error must not delete its files.
        """
        timestamps: dict[str, int] = {}
        gone: set[str] = set()
        for share_name in dict.fromkeys(share_names):
            props = await self.data_source.get_share_properties(share_name)
            if props.success:
                last_modified = (props.data or {}).get("last_modified")
                if isinstance(last_modified, datetime):
                    timestamps[share_name] = datetime_to_epoch_ms(last_modified)
            elif props.error_code in _SHARE_GONE_CODES:
                self.logger.warning(f"Share {share_name} named in the share filter no longer exists")
                gone.add(share_name)
            elif props.error_code in _CREDENTIALS_REJECTED_CODES:
                raise CredentialsRejectedError(f"Reading share {share_name} was refused: {props.error_code}")
            else:
                # Read again by the walk; its listing decides whether the share counts as read.
                self.logger.warning(f"Could not read the properties of share {share_name}: {props.error}")
        return timestamps, gone

    async def _remove_records_not_seen(self, share_name: str, seen: set[str]) -> None:
        """Delete the records of a share's files and directories this sync did not find.

        Covers deletions, items now excluded by the sync filters, and a file edited
        and then renamed between two syncs, which is found at its new path under a
        new revision rather than moved. Only called after the share was read in full.
        """
        try:
            stored = await self.data_entities_processor.get_nodes_by_filters(
                CollectionNames.RECORDS.value,
                {"connectorId": self.connector_id, "externalGroupId": share_name},
                return_fields=["id", "_key", "externalRecordId"],
                raise_on_error=True,
            )
        except Exception as e:
            self.logger.warning(f"Not removing deleted files of {share_name}: its records could not be read: {e}")
            return
        stale = [
            node.get("_key") or node.get("id")
            for node in stored
            if node.get("externalRecordId") not in seen and (node.get("_key") or node.get("id"))
        ]
        if not stale:
            return
        self.logger.info(f"Removing {len(stale)} records of items no longer in share {share_name}")
        await self._delete_records(stale)

    async def _delete_records(self, record_ids: list[str]) -> None:
        for start in range(0, len(record_ids), DELETE_BATCH_SIZE):
            batch = record_ids[start:start + DELETE_BATCH_SIZE]
            try:
                # Each stale record is listed, so no subtree walk is needed.
                result = await self.data_entities_processor.on_records_deleted_cascade(
                    batch, self.connector_id, cascade_children=False, include_trashed_roots=True,
                )
                if not (result or {}).get("success", False):
                    self.logger.warning(f"Failed to delete {len(batch)} records: {(result or {}).get('reason')}")
            except Exception as e:
                self.logger.warning(f"Failed to delete {len(batch)} records: {e}")

    async def _remove_unsynced_shares(self, synced: set[str]) -> set[str]:
        """Remove the shares, with their records, that this sync no longer covers:
        taken out of the share filter, or no longer in the account. Returns those removed."""
        try:
            groups = await self.data_entities_processor.get_nodes_by_filters(
                CollectionNames.RECORD_GROUPS.value,
                {"connectorId": self.connector_id, "groupType": RecordGroupType.FILE_SHARE.value},
                return_fields=["externalGroupId"],
                raise_on_error=True,
            )
        except Exception as e:
            self.logger.warning(f"Not removing shares that are no longer synced: they could not be read: {e}")
            return set()
        removed: set[str] = set()
        for share_name in sorted({g.get("externalGroupId") for g in groups} - synced - {None, ""}):
            self.logger.info(f"Removing share {share_name}, which is no longer synced")
            if await self.data_entities_processor.on_record_group_deleted(share_name, self.connector_id):
                removed.add(share_name)
            else:
                self.logger.warning(f"Could not remove share {share_name}; retrying next sync")
        return removed

    async def _create_record_groups_for_shares(
        self,
        share_names: list[str],
        share_last_modified_epoch_ms: dict[str, int] | None = None,
    ) -> None:
        """Create record groups for shares with appropriate permissions.

        Args:
            share_names: File shares to represent as record groups.
            share_last_modified_epoch_ms: Optional map from share name to Azure
                ``last_modified`` (epoch ms) from list_shares, for hub Created/Updated.
        """
        if not share_names:
            return

        ts_map = share_last_modified_epoch_ms or {}
        record_groups = []
        for share_name in share_names:
            if not share_name:
                continue

            lm_ms = ts_map.get(share_name)
            record_group = RecordGroup(
                name=share_name,
                external_group_id=share_name,
                group_type=RecordGroupType.FILE_SHARE,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                description=f"Azure File Share: {share_name}",
                web_url=self._generate_directory_url(share_name, ""),
                source_created_at=lm_ms,
                source_updated_at=lm_ms,
                inherit_permissions=True,
            )
            record_groups.append((record_group, []))

        if record_groups:
            await self.data_entities_processor.on_new_record_groups(record_groups)
            self.logger.info(
                f"Created {len(record_groups)} record group(s) for shares"
            )

    def _get_date_filters(
        self,
    ) -> tuple[int | None, int | None, int | None, int | None]:
        """Extract date filter values from sync_filters."""
        modified_after_ms: int | None = None
        modified_before_ms: int | None = None
        created_after_ms: int | None = None
        created_before_ms: int | None = None

        modified_date_filter = self.sync_filters.get(SyncFilterKey.MODIFIED)
        if modified_date_filter and not modified_date_filter.is_empty():
            after_iso, before_iso = modified_date_filter.get_datetime_iso()
            if after_iso:
                after_dt = datetime.fromisoformat(after_iso).replace(tzinfo=timezone.utc)
                modified_after_ms = int(after_dt.timestamp() * 1000)
                self.logger.info(f"Applying modified date filter: after {after_dt}")
            if before_iso:
                before_dt = datetime.fromisoformat(before_iso).replace(
                    tzinfo=timezone.utc
                )
                modified_before_ms = int(before_dt.timestamp() * 1000)
                self.logger.info(f"Applying modified date filter: before {before_dt}")

        created_date_filter = self.sync_filters.get(SyncFilterKey.CREATED)
        if created_date_filter and not created_date_filter.is_empty():
            after_iso, before_iso = created_date_filter.get_datetime_iso()
            if after_iso:
                after_dt = datetime.fromisoformat(after_iso).replace(tzinfo=timezone.utc)
                created_after_ms = int(after_dt.timestamp() * 1000)
                self.logger.info(f"Applying created date filter: after {after_dt}")
            if before_iso:
                before_dt = datetime.fromisoformat(before_iso).replace(
                    tzinfo=timezone.utc
                )
                created_before_ms = int(before_dt.timestamp() * 1000)
                self.logger.info(f"Applying created date filter: before {before_dt}")

        return modified_after_ms, modified_before_ms, created_after_ms, created_before_ms

    def _pass_date_filters(
        self,
        item: dict,
        modified_after_ms: int | None = None,
        modified_before_ms: int | None = None,
        created_after_ms: int | None = None,
        created_before_ms: int | None = None,
    ) -> bool:
        """Returns True if item PASSES date filters (should be kept)."""
        is_directory = item.get("is_directory", False)
        if is_directory:
            return True

        if not any(
            [modified_after_ms, modified_before_ms, created_after_ms, created_before_ms]
        ):
            return True

        last_modified = item.get("last_modified")
        if not last_modified:
            return True

        # Parse datetime
        if isinstance(last_modified, datetime):
            obj_timestamp_ms = datetime_to_epoch_ms(last_modified)
        elif isinstance(last_modified, str):
            try:
                obj_dt = datetime.fromisoformat(last_modified.replace("Z", "+00:00"))
                obj_timestamp_ms = int(obj_dt.timestamp() * 1000)
            except ValueError:
                return True
        else:
            return True

        item_name = item.get("name", "")
        if modified_after_ms and obj_timestamp_ms < modified_after_ms:
            self.logger.debug(
                f"Skipping {item_name}: modified {obj_timestamp_ms} before cutoff {modified_after_ms}"
            )
            return False
        if modified_before_ms and obj_timestamp_ms > modified_before_ms:
            self.logger.debug(
                f"Skipping {item_name}: modified {obj_timestamp_ms} after cutoff {modified_before_ms}"
            )
            return False

        # Check creation time if available
        creation_time = item.get("creation_time")
        if creation_time:
            if isinstance(creation_time, datetime):
                created_timestamp_ms = datetime_to_epoch_ms(creation_time)
            elif isinstance(creation_time, str):
                try:
                    created_dt = datetime.fromisoformat(
                        creation_time.replace("Z", "+00:00")
                    )
                    created_timestamp_ms = int(created_dt.timestamp() * 1000)
                except ValueError:
                    created_timestamp_ms = None
            else:
                created_timestamp_ms = None

            if created_timestamp_ms:
                if created_after_ms and created_timestamp_ms < created_after_ms:
                    self.logger.debug(
                        f"Skipping {item_name}: created {created_timestamp_ms} before cutoff {created_after_ms}"
                    )
                    return False
                if created_before_ms and created_timestamp_ms > created_before_ms:
                    self.logger.debug(
                        f"Skipping {item_name}: created {created_timestamp_ms} after cutoff {created_before_ms}"
                    )
                    return False

        return True

    def _pass_extension_filter(self, item_path: str, *, is_directory: bool = False) -> bool:
        """
        Checks if the Azure Files item passes the configured file extensions filter.

        For MULTISELECT filters:
        - Operator IN: Only allow files with extensions in the selected list
        - Operator NOT_IN: Allow files with extensions NOT in the selected list

        Folders always pass this filter to maintain directory structure.

        Args:
            item_path: The path of the file or directory
            is_directory: Whether this is a directory

        Returns:
            True if the item passes the filter (should be kept), False otherwise
        """
        # 1. ALWAYS Allow Folders
        # We must sync folders regardless of extension to ensure the directory structure
        # exists for any files that might be inside them.
        if is_directory:
            return True

        # 2. Get the extensions filter
        extensions_filter = self.sync_filters.get(SyncFilterKey.FILE_EXTENSIONS)

        # If no filter configured or filter is empty, allow all files
        if extensions_filter is None or extensions_filter.is_empty():
            return True

        # 3. Get the file extension from the item path
        file_extension = get_file_extension(item_path)

        # 4. Handle files without extensions
        if file_extension is None:
            operator = extensions_filter.get_operator()
            operator_str = operator.value if hasattr(operator, 'value') else str(operator)
            # If using NOT_IN operator, files without extensions pass (not in excluded list)
            # If using IN operator, files without extensions fail (not in allowed list)
            return operator_str == FilterOperator.NOT_IN

        # 5. Get the list of extensions from the filter value
        allowed_extensions = extensions_filter.value
        if not isinstance(allowed_extensions, list):
            return True  # Invalid filter value, allow the file

        # Normalize extensions (lowercase, without dots)
        normalized_extensions = [ext.lower().lstrip(".") for ext in allowed_extensions]

        # 6. Apply the filter based on operator
        operator = extensions_filter.get_operator()
        operator_str = operator.value if hasattr(operator, 'value') else str(operator)

        if operator_str == FilterOperator.IN:
            # Only allow files with extensions in the list
            return file_extension in normalized_extensions
        elif operator_str == FilterOperator.NOT_IN:
            # Allow files with extensions NOT in the list
            return file_extension not in normalized_extensions

        # Unknown operator, default to allowing the file
        return True

    async def _sync_share(self, share_name: str) -> tuple[set[str], bool]:
        """Sync files and directories from a specific share with recursive traversal.

        Returns the external ids found and whether the share was read in full.
        """
        if not self.data_source:
            raise ConnectionError("Azure Files connector is not initialized.")

        sync_filters = (
            self.sync_filters
            if hasattr(self, "sync_filters") and self.sync_filters
            else FilterCollection()
        )

        # Log extension filter status if configured
        extensions_filter = sync_filters.get(SyncFilterKey.FILE_EXTENSIONS)
        if extensions_filter and not extensions_filter.is_empty():
            filter_value = extensions_filter.value
            operator = extensions_filter.get_operator()
            operator_str = operator.value if hasattr(operator, 'value') else str(operator)
            self.logger.info(
                f"File extensions filter active for share {share_name}: "
                f"operator={operator_str}, extensions={filter_value}"
            )

        (
            modified_after_ms,
            modified_before_ms,
            created_after_ms,
            created_before_ms,
        ) = self._get_date_filters()

        sync_point_key = generate_record_sync_point_key(
            RecordType.FILE.value, "share", share_name
        )
        sync_point = await self.record_sync_point.read_sync_point(sync_point_key)
        last_sync_time = sync_point.get("last_sync_time") if sync_point else None
        # last_sync_time is not used to skip items modified before it: a rename
        # keeps the file's timestamps, so skipping would hide moves, and removing
        # records of deleted files needs every item seen on every sync.

        # The share itself is the parent of its top-level items (a placeholder
        # record may exist for it), and no listing returns it.
        seen: set[str] = {share_name}
        # Directories listed in full; only a path under one of them can be known to be gone.
        listed: set[str] = set()
        complete = True
        share_gone = False
        rejected_code: str | None = None

        scope = FolderScope.from_filters(sync_filters)
        if not scope.is_everything:
            self.logger.info(f"Folder filter for share {share_name}: {scope.describe()}")
        list_roots = {p.rstrip("/") for p in scope.list_prefixes}
        chosen_folders = {p for p in list_roots if p}
        batch_records: list[tuple[FileRecord, list[Permission]]] = []
        max_timestamp = last_sync_time if last_sync_time else 0

        # A full sync writes every record, which also restores links an earlier
        # run lost; other runs leave a record whose revision is unchanged alone.
        full_sync = self._full_sync_running()
        stored_revisions = await self._stored_revisions(share_name)
        # A move is looked for among this share's records only: FileIds repeat across shares.
        paths_by_revision: dict[str, list[str]] | None = None
        if stored_revisions is not None:
            paths_by_revision = {}
            for stored_path, revision in stored_revisions.items():
                paths_by_revision.setdefault(revision, []).append(stored_path)

        move_candidates: list[tuple[str, FileRecord, list[Permission]]] = []
        directory_slots = asyncio.Semaphore(DIRECTORY_CONCURRENCY)
        write_lock = asyncio.Lock()

        async def flush(*, force: bool = False) -> None:
            nonlocal batch_records
            if not batch_records or (len(batch_records) < self.batch_size and not force):
                return
            to_write, batch_records = batch_records, []
            # One writer at a time, in listing order, so parents land before children.
            async with write_lock:
                await self.data_entities_processor.on_new_records(to_write)

        written_folders: set[str] = set()

        async def write_folder_above_the_walk(path: str) -> None:
            """Write a chosen folder or one above it, which the walk never lists, from its
            properties: otherwise it is only a stub named by its full path."""
            external_id = f"{share_name}/{path}"
            props = await self.data_source.get_directory_properties(share_name, path)
            if props.success and props.data:
                item = {**props.data, "name": path.rsplit("/", 1)[-1], "path": path, "is_directory": True}
                if not full_sync and (stored_revisions or {}).get(external_id) == self._get_azure_files_revision_id(item):
                    return
                # Looked up by path only: these folders are not candidates for a move.
                record, permissions = await self._process_azure_files_item(item, share_name, None, {})
                if record:
                    batch_records.append((record, permissions))
                    return
            else:
                self.logger.warning(f"Could not read folder {external_id} above the folder filter: {props.error}")
            # Not written by this walk: noted as kept, or the unlink after a full
            # sync cuts the links of everything below it to the app.
            stored = await self.data_entities_processor.get_record_by_external_id(self.connector_id, external_id)
            if stored:
                self.data_entities_processor.note_kept_records(self.connector_id, [stored.id])

        async def share_is_gone() -> bool:
            props = await self.data_source.get_share_properties(share_name)
            return not props.success and props.error_code in _SHARE_GONE_CODES

        async def traverse_directory(directory_path: str) -> None:
            nonlocal max_timestamp, complete, share_gone, rejected_code
            subdirectories: list[str] = []

            async with directory_slots:
                if rejected_code:
                    # Every further call would be refused the same way.
                    return
                try:
                    token: str | None = None
                    # Page by page, so a directory with millions of entries is never held whole.
                    while True:
                        first_page = token is None
                        async with self.resilience.guard():
                            response = await self.data_source.list_directories_and_files(
                                share_name=share_name,
                                directory_path=directory_path,
                                results_per_page=LISTING_PAGE_SIZE,
                                continuation_token=token,
                            )

                        if not response.success:
                            error_msg = response.error or "Unknown error"
                            if response.error_code in _CREDENTIALS_REJECTED_CODES:
                                rejected_code = response.error_code
                                complete = False
                                return
                            if first_page and directory_path in list_roots and response.error_code in _NOT_FOUND_CODES:
                                if await share_is_gone():
                                    share_gone = True
                                    return
                                if directory_path in chosen_folders:
                                    # A chosen folder that doesn't exist has nothing to sync;
                                    # a typo must not block removal for the whole share.
                                    self.logger.warning(
                                        f"Folder {share_name}/{directory_path} named in the folder filter does not exist"
                                    )
                                    return
                            complete = False
                            if directory_path in list_roots:
                                self._unread_shares.add(share_name)
                            self.logger.error(
                                f"Failed to list items in {share_name}/{directory_path} ({response.error_code}): {error_msg}"
                            )
                            return

                        if first_page and directory_path in chosen_folders:
                            # With Include the walk starts inside each chosen folder, so that
                            # folder and those above it are never listed. Only once it is known
                            # to exist do they count as part of the tree.
                            parts = directory_path.split("/")
                            ancestors = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
                            seen.update(f"{share_name}/{path}" for path in ancestors)
                            # Claimed before any await: two chosen folders share their parents.
                            to_write = [path for path in ancestors if path not in written_folders]
                            written_folders.update(to_write)
                            for path in to_write:
                                await write_folder_above_the_walk(path)

                        items = response.data or []
                        listed.add(f"{share_name}/{directory_path}" if directory_path else share_name)
                        self.logger.debug(
                            f"Processing {len(items)} items from {share_name}/{directory_path or 'root'}"
                        )

                        for item in items:
                            try:
                                item_name = item.get("name", "")
                                is_directory = item.get("is_directory", False)
                                item_path = item.get("path", item_name)

                                in_scope = (
                                    scope.includes_folder(item_path) if is_directory
                                    else scope.includes_file(item_path)
                                )
                                if not in_scope:
                                    continue

                                # Check extension filter
                                if not self._pass_extension_filter(item_path, is_directory=is_directory):
                                    self.logger.debug(
                                        f"Skipping {item_path}: does not pass extension filter"
                                    )
                                    continue

                                if not self._pass_date_filters(
                                    item,
                                    modified_after_ms,
                                    modified_before_ms,
                                    created_after_ms,
                                    created_before_ms,
                                ):
                                    continue

                                # Seen before processing: an item that fails to
                                # process still exists and must not be removed.
                                # Only slashes are trimmed: spaces are part of the name.
                                external_record_id = f"{share_name}/{(item_path or item_name).strip('/')}"
                                seen.add(external_record_id)

                                # Track max timestamp for incremental sync
                                last_modified = item.get("last_modified")
                                if last_modified:
                                    if isinstance(last_modified, datetime):
                                        obj_timestamp_ms = datetime_to_epoch_ms(last_modified)
                                        max_timestamp = max(max_timestamp, obj_timestamp_ms)
                                    elif isinstance(last_modified, str):
                                        try:
                                            obj_dt = datetime.fromisoformat(
                                                last_modified.replace("Z", "+00:00")
                                            )
                                            obj_timestamp_ms = int(
                                                obj_dt.timestamp() * 1000
                                            )
                                            max_timestamp = max(
                                                max_timestamp, obj_timestamp_ms
                                            )
                                        except ValueError:
                                            pass

                                if is_directory:
                                    subdirectories.append(item_path)

                                stored_revision = (stored_revisions or {}).get(external_record_id)
                                if (
                                    not full_sync
                                    and stored_revision
                                    and stored_revision == self._get_azure_files_revision_id(item)
                                ):
                                    continue

                                # Per item: directories are walked concurrently.
                                moved_from: dict[str, str] = {}
                                record, permissions = await self._process_azure_files_item(
                                    item, share_name, moved_from, paths_by_revision
                                )
                                if not record:
                                    if item_name:
                                        # Not written, so the unlink after a full sync would cut it off.
                                        complete = False
                                    continue
                                old_external_id = moved_from.get(record.id)
                                if old_external_id:
                                    move_candidates.append((old_external_id, record, permissions))
                                    continue
                                batch_records.append((record, permissions))
                                await flush()

                            except Exception as e:
                                complete = False
                                self.logger.error(
                                    f"Error processing item {item.get('name', 'unknown')}: {e}",
                                    exc_info=True,
                                )
                                continue


                        token = response.continuation_token
                        if not token:
                            break

                except Exception as e:
                    complete = False
                    self.logger.error(
                        f"Error during directory traversal for {share_name}/{directory_path}: {e}",
                        exc_info=True,
                    )
                    return

            # Outside the slot: a parent waiting on its children must not hold one.
            if subdirectories:
                await asyncio.gather(*(traverse_directory(path) for path in subdirectories))

        # Start traversal from root
        await asyncio.gather(*(traverse_directory(path) for path in sorted(list_roots)))

        if rejected_code:
            raise CredentialsRejectedError(f"Listing share {share_name} was refused: {rejected_code}")

        if share_gone:
            self.logger.warning(f"Share {share_name} no longer exists; removing its records")
            # run_sync removes its group too and tells the owner once.
            self._gone_shares.add(share_name)
            return set(), True

        try:
            await self._write_moves(share_name, move_candidates, seen, listed, paths_by_revision)
            await flush(force=True)
        except Exception as e:
            complete = False
            self.logger.error(f"Error writing records of share {share_name}: {e}", exc_info=True)

        if max_timestamp > 0:
            await self.record_sync_point.update_sync_point(
                sync_point_key, {"last_sync_time": max_timestamp}
            )

        return seen, complete

    def _full_sync_running(self) -> bool:
        running = getattr(self.data_entities_processor, "full_sync_running", None)
        return bool(running(self.connector_id)) if callable(running) else True

    async def _stored_revisions(self, share_name: str) -> dict[str, str] | None:
        """External id -> stored revision of the share's live records, read once per walk;
        None when they could not be read."""
        try:
            nodes = await self.data_entities_processor.get_nodes_by_filters(
                CollectionNames.RECORDS.value,
                {"connectorId": self.connector_id, "externalGroupId": share_name},
                return_fields=["externalRecordId", "externalRevisionId", "isDeleted"],
                raise_on_error=True,
            )
        except Exception as e:
            self.logger.warning(f"Could not read the stored records of {share_name}; writing every item: {e}")
            return None
        return {
            node["externalRecordId"]: node["externalRevisionId"]
            for node in nodes
            if node.get("externalRecordId") and node.get("externalRevisionId") and node.get("isDeleted") is not True
        }

    async def _write_moves(
        self,
        share_name: str,
        candidates: list[tuple[str, FileRecord, list[Permission]]],
        seen: set[str],
        listed: set[str],
        paths_by_revision: dict[str, list[str]] | None = None,
    ) -> None:
        """Write the items found under a stored record's FileId at a new path.

        Only once the share is listed is it known which stored paths are gone: a
        path is gone when the directory above it was listed and did not hold it, or
        is gone itself (a directory whose listing failed proves nothing). Each new path takes one of
        those with its revision (the record moves, keeping its index when the
        content did not change); a new path left with none is a copy that kept its
        FileId (AzCopy, robocopy) and gets a record of its own.
        """

        def is_gone(external_id: str) -> bool:
            # Also gone: anything under a directory that is itself gone (a renamed folder's files).
            if external_id in seen:
                return False
            parent = get_parent_path(external_id[len(share_name) + 1:])
            parent_id = f"{share_name}/{parent}" if parent else share_name
            if parent_id in listed:
                return True
            return parent_id != share_name and is_gone(parent_id)

        moves: list[tuple[str, FileRecord, list[Permission]]] = []
        copies: list[tuple[FileRecord, list[Permission]]] = []
        taken: set[str] = set()
        for old_external_id, record, permissions in candidates:
            stored_paths = (
                [old_external_id] if paths_by_revision is None
                else paths_by_revision.get(record.external_revision_id or "", [])
            )
            gone = [path for path in stored_paths if path not in taken and is_gone(path)]
            old_path = old_external_id if old_external_id in gone else next(iter(gone), None)
            stored = None
            if old_path and old_path != old_external_id:
                # Paired with another stored path of the same revision than the one picked
                # while the share was still being listed: take that record's identity.
                stored = await self.data_entities_processor.get_record_by_external_id(self.connector_id, old_path)
                if stored is None:
                    old_path = None
            if old_path is None:
                record.id = str(uuid.uuid4())
                record.version = 0
                copies.append((record, permissions))
                continue
            if stored is not None:
                record.id = stored.id
                record.source_created_at = stored.source_created_at
            taken.add(old_path)
            moves.append((old_path, record, permissions))
        for start in range(0, len(moves), self.batch_size):
            await self.data_entities_processor.on_records_moved(moves[start:start + self.batch_size])
        for start in range(0, len(copies), self.batch_size):
            await self.data_entities_processor.on_new_records(copies[start:start + self.batch_size])

    def _get_azure_files_revision_id(self, item: dict) -> str:
        """
        Determines a stable revision ID for an Azure Files item.

        Prefers file_id (SMB FileId from list API) when available, as it is stable
        across renames. Then content_md5 (stable across renames), then etag.

        The FileId names the file, not its contents: an edit leaves it unchanged,
        so on its own it hid every content change. For a file it is combined with
        the size and last-write time, which change when the contents do and, like
        the FileId, survive a rename.

        Note: Azure Files etag changes on every modification including metadata changes
        and renames. Therefore file_id or content_md5 must be used for reliable
        move/rename detection when available.

        Args:
            item: Azure Files item metadata dictionary

        Returns:
            Revision ID string (file_id[:size:last_write_time], content_md5, or etag)
        """
        file_id = item.get("file_id")
        if file_id is not None:
            if item.get("is_directory"):
                return str(file_id)
            written = item.get("last_write_time") or item.get("last_modified") or item.get("etag")
            if not written:
                return str(file_id)
            if isinstance(written, datetime):
                # A listing gives an aware time, get_file_properties a naive one in UTC.
                if written.tzinfo is None:
                    written = written.replace(tzinfo=timezone.utc)
                written = written.astimezone(timezone.utc).isoformat()
            return f"{file_id}:{item.get('size')}:{str(written).strip(chr(34))}"

        content_md5 = item.get("content_md5")
        if content_md5:
            if isinstance(content_md5, (bytes, bytearray)):
                return base64.b64encode(bytes(content_md5)).decode("utf-8")
            elif isinstance(content_md5, str):
                return content_md5

        # Fall back to etag if no file_id or MD5 available
        # Note: etag-based move detection may be unreliable as Azure etag changes on copy
        etag = item.get("etag")
        if etag:
            return etag.strip('"')

        return ""

    async def _process_azure_files_item(
        self,
        item: dict,
        share_name: str,
        moved_from: dict[str, str] | None = None,
        paths_by_revision: dict[str, list[str]] | None = None,
    ) -> tuple[FileRecord | None, list[Permission]]:
        """Process a single Azure Files item (file or directory) and convert it to a FileRecord.

        Key difference from S3/Blob: Directories are REAL entities, not placeholders.

        An item found by its FileId under another path of the same share reuses that
        record; ``moved_from`` then maps the record id to the stored external id, for
        the caller to write it as a move. ``paths_by_revision`` (stored revision ->
        external ids, this share's records) is where that record is looked for.
        """
        try:
            item_name = item.get("name", "")
            if not item_name:
                return None, []

            is_directory = item.get("is_directory", False)
            is_file = not is_directory
            # Only slashes are trimmed: leading and trailing spaces are part of the name.
            normalized_path = (item.get("path") or item_name).strip("/")

            # Parse timestamps
            last_modified = item.get("last_modified")
            if last_modified:
                if isinstance(last_modified, datetime):
                    timestamp_ms = datetime_to_epoch_ms(last_modified)
                elif isinstance(last_modified, str):
                    try:
                        obj_dt = datetime.fromisoformat(
                            last_modified.replace("Z", "+00:00")
                        )
                        timestamp_ms = int(obj_dt.timestamp() * 1000)
                    except ValueError:
                        timestamp_ms = get_epoch_timestamp_in_ms()
                else:
                    timestamp_ms = get_epoch_timestamp_in_ms()
            else:
                timestamp_ms = get_epoch_timestamp_in_ms()

            # Parse created time
            creation_time = item.get("creation_time")
            if creation_time:
                if isinstance(creation_time, datetime):
                    created_timestamp_ms = datetime_to_epoch_ms(creation_time)
                elif isinstance(creation_time, str):
                    try:
                        created_dt = datetime.fromisoformat(
                            creation_time.replace("Z", "+00:00")
                        )
                        created_timestamp_ms = int(created_dt.timestamp() * 1000)
                    except ValueError:
                        created_timestamp_ms = timestamp_ms
                else:
                    created_timestamp_ms = timestamp_ms
            else:
                created_timestamp_ms = timestamp_ms

            external_record_id = f"{share_name}/{normalized_path}"
            current_revision_id = self._get_azure_files_revision_id(item)
            raw_etag = item.get("etag", "").strip('"') if item.get("etag") else ""

            existing_record = await self.data_entities_processor.get_record_by_external_id(
                self.connector_id, external_record_id
            )

            is_move = False

            if existing_record:
                stored_revision = existing_record.external_revision_id or ""

                # Content changed or missing revision - sync properly from Azure Files
                if current_revision_id and stored_revision and current_revision_id != stored_revision:
                    self.logger.info(
                        f"Content change detected: {normalized_path} - externalRevisionId changed from {stored_revision} to {current_revision_id}"
                    )
                elif not current_revision_id or not stored_revision:
                    if not current_revision_id:
                        self.logger.warning(
                            f"Current revision missing for {normalized_path}, processing record"
                        )
                    if not stored_revision:
                        self.logger.debug(
                            f"Stored revision missing for {normalized_path}, processing record"
                        )
            elif current_revision_id:
                if paths_by_revision is not None:
                    old_path = next(
                        (p for p in paths_by_revision.get(current_revision_id, ()) if p != external_record_id), None
                    )
                    existing_record = (
                        await self.data_entities_processor.get_record_by_external_id(self.connector_id, old_path)
                        if old_path else None
                    )
                else:
                    existing_record = await self.data_entities_processor.get_record_by_external_revision_id(
                        self.connector_id, current_revision_id
                    )
                    if existing_record is not None and not is_live_record(existing_record):
                        # A trashed record is not moved, so its new path would get no record.
                        existing_record = None
                if existing_record and existing_record.external_record_group_id != share_name:
                    # FileIds are unique only within a share; new shares hand out the same ones.
                    existing_record = None

                if existing_record:
                    is_move = True
                    self.logger.info(
                        f"Move/rename detected: {normalized_path} - file moved from {existing_record.external_record_id} to {external_record_id}"
                    )
                else:
                    self.logger.debug(f"New item: {normalized_path}")
            else:
                self.logger.debug(f"New item: {normalized_path} (no revision available)")

            # Prepare record data
            # Use RecordType.FILE for both files and directories; directories are distinguished via is_file flag.
            record_type = RecordType.FILE
            extension = get_file_extension(normalized_path) if is_file else None
            # From the name, as a reindex does, so the two agree.
            mime_type = get_mimetype_for_azure_files(normalized_path, is_directory=is_directory)

            parent_path = get_parent_path(normalized_path)
            parent_external_id = (
                f"{share_name}/{parent_path}" if parent_path else share_name
            )
            # Match Azure Blob / GCS behavior: parent is always treated as a FILE record
            parent_record_type = RecordType.FILE

            if is_directory:
                web_url = self._generate_directory_url(share_name, normalized_path)
            else:
                web_url = self._generate_web_url(share_name, normalized_path)

            record_id = existing_record.id if existing_record else str(uuid.uuid4())
            record_name = normalized_path.rstrip("/").split("/")[-1] or normalized_path.rstrip("/")

            if is_move and moved_from is not None:
                moved_from[record_id] = existing_record.external_record_id


            # Get content MD5 hash for md5_hash field (same handling as Azure Blob)
            content_md5 = item.get("content_md5")
            if content_md5:
                if isinstance(content_md5, (bytes, bytearray)):
                    content_md5 = base64.b64encode(bytes(content_md5)).decode("utf-8")
                elif not isinstance(content_md5, str):
                    content_md5 = str(content_md5)

            file_record = FileRecord(
                id=record_id,
                record_name=record_name,
                record_type=record_type,
                record_group_type=RecordGroupType.FILE_SHARE.value,
                external_record_group_id=share_name,
                external_record_id=external_record_id,
                external_revision_id=current_revision_id,
                # The processor raises the stored version when the revision changed.
                version=0,
                origin=OriginTypes.CONNECTOR.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                source_created_at=existing_record.source_created_at
                if existing_record
                else created_timestamp_ms,
                source_updated_at=timestamp_ms,
                weburl=web_url,
                signed_url=None,
                # Match Azure Blob / GCS behavior: directories are internal placeholders with hidden weburl
                hide_weburl=True,
                is_internal=True if is_directory else False,
                parent_external_record_id=parent_external_id,
                parent_record_type=parent_record_type,
                size_in_bytes=item.get("size", 0) or item.get("content_length", 0)
                if is_file
                else 0,
                is_file=is_file,
                extension=extension,
                path=normalized_path,
                mime_type=mime_type,
                md5_hash=content_md5,
                etag=raw_etag,
            )

            # Root-level items: do not link to the share as a parent
            if (
                file_record.parent_external_record_id
                and file_record.external_record_group_id
                and file_record.parent_external_record_id == file_record.external_record_group_id
            ):
                file_record.parent_external_record_id = None
                file_record.parent_record_type = None

            if (
                hasattr(self, "indexing_filters")
                and self.indexing_filters
                and not self.indexing_filters.is_enabled(
                    IndexingFilterKey.FILES, default=True
                )
            ):
                file_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

            permissions = await self._create_azure_files_permissions(
                share_name, normalized_path
            )

            return file_record, permissions

        except Exception as e:
            self.logger.error(f"Error processing Azure Files item: {e}", exc_info=True)
            return None, []

    async def _create_azure_files_permissions(
        self, share_name: str, item_path: str
    ) -> list[Permission]:
        """Files inherit their parent folder or the share. No permission edges."""
        return []

    async def test_connection_and_access(self) -> bool:
        """Test that the credential can do what a sync needs: list the shares to sync,
        list their roots, and read a file's properties. A credential that cannot
        raises ConnectorInitError naming what is missing."""
        if not self.data_source:
            return False
        try:
            problem = await self._access_problem()
        except Exception as e:
            self.logger.error(f"Azure Files connection test failed: {e}", exc_info=True)
            return False
        if problem is None:
            self.logger.info("Azure Files connection test successful.")
            return True
        self.logger.error(f"Azure Files connection test failed: {problem}")
        await self._notify_problem(NotificationType.CONNECTOR_AUTH_ERROR, "Connection test failed", problem)
        raise ConnectorInitError(f"{self.display_name}: {problem}")

    @staticmethod
    def _refusal(action: str, response: AzureFilesResponse, missing: str) -> str:
        """Why a check failed, worded for the admin who set the credential up."""
        if response.error_code in _CREDENTIALS_REJECTED_CODES:
            return (
                f"Azure rejected the credentials while trying to {action} ({response.error_code}). "
                "Check the connection string or Entra ID details; a SAS may have expired or an account key been rotated."
            )
        if response.error_code in _PERMISSION_CODES:
            return f"Could not {action}: the credential needs the {missing} permission. Azure said: {response.error}"
        return f"Could not {action}. Azure said: {response.error}"

    async def _access_problem(self) -> str | None:
        """Why the credential cannot sync, or None."""
        shares = included_names(self.sync_filters, "shares")
        if not shares:
            response = await self.data_source.list_shares()
            if not response.success:
                return self._refusal("list the file shares", response, "List (service level)")
            shares = [s.get("name") for s in response.data or [] if s.get("name")][:1]

        # Listing each selected root also works for a share SAS, which cannot list shares.
        readable: tuple[str, list[dict]] | None = None
        for share_name in shares:
            listing = await self.data_source.list_directories_and_files(share_name=share_name)
            if listing.success:
                readable = readable or (share_name, listing.data or [])
            elif listing.error_code in _NOT_FOUND_CODES:
                # Not a credential problem: the sync removes a gone share's files and
                # tells the owner, which it could not do if the connector never started.
                self.logger.warning(f"Azure Files connection test: selected share {share_name} not found")
            else:
                return self._refusal(f"list share {share_name}", listing, "List")

        if readable:
            share_name, items = readable
            # Reading properties needs the Read permission that listing does not.
            item = next((i for i in items if not i.get("is_directory") and i.get("path")), None) or next(
                (i for i in items if i.get("path")), None
            )
            if item:
                props = await (
                    self.data_source.get_directory_properties(share_name, item["path"])
                    if item.get("is_directory")
                    else self.data_source.get_file_properties(share_name, item["path"])
                )
                if not props.success:
                    return self._refusal(f"read {share_name}/{item['path']}", props, "Read")
        return None

    def _extract_file_path_info(self, record: Record) -> tuple[str, str] | None:
        """Extract share name and file path from record.

        Returns:
            Tuple of (share_name, file_path) if successful, None otherwise
        """
        share_name = record.external_record_group_id
        if not share_name:
            self.logger.warning(f"No share name found for record: {record.id}")
            return None

        external_record_id = record.external_record_id
        if not external_record_id:
            self.logger.warning(
                f"No external_record_id found for record: {record.id}"
            )
            return None

        # The stored path is the file's real name; "%" in it is a literal character.
        if external_record_id.startswith(f"{share_name}/"):
            file_path = external_record_id[len(f"{share_name}/") :]
        else:
            file_path = external_record_id.lstrip("/")

        return (share_name, file_path)

    async def get_signed_url(self, record: Record) -> str | None:
        """Generate a short-lived SAS URL for an Azure file, if the credentials carry an account key."""
        if not self.data_source:
            return None
        try:
            path_info = self._extract_file_path_info(record)
            if not path_info:
                return None

            share_name, file_path = path_info

            self.logger.debug(
                f"Generating SAS URL - Share: {share_name}, "
                f"File: {file_path}, Record ID: {record.id}"
            )

            response = await self.data_source.generate_file_sas_url(
                share_name=share_name,
                file_path=file_path,
                permission="r",
                expiry=datetime.now(timezone.utc) + SAS_URL_LIFETIME,
            )

            if response.success and response.data:
                return response.data.get("sas_url")
            if "account key required" in (response.error or "").lower():
                # A SAS connection string or Entra ID carries no key to sign with.
                self.logger.debug(f"No account key to sign a SAS URL for record {record.id}")
            else:
                self.logger.error(
                    f"Failed to generate SAS URL: {response.error} | "
                    f"Share: {share_name} | File: {file_path}"
                )
            return None
        except Exception as e:
            # Returning None is deliberate: callers fall back to streaming the
            # file. Only the diagnostic must survive.
            self.logger.error(
                f"Error generating SAS URL for record {record.id}: {e}", exc_info=True
            )
            return None

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Stream Azure file content, chunk by chunk from the SDK, whatever the credential."""
        if isinstance(record, FileRecord) and not record.is_file:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Cannot stream directory content",
            )

        if not self.data_source:
            raise connector_not_ready(self.display_name)

        # Extract file path information
        path_info = self._extract_file_path_info(record)
        if not path_info:
            raise not_downloadable(
                "This item is missing the share and path it belongs to and cannot be "
                "downloaded.",
                connector=self.display_name,
            )

        share_name, file_path = path_info

        try:
            # Azure failures arrive as HttpResponseError here, before any byte is
            # sent, and are mapped below (403 on the share vs a deleted file).
            downloader = await self.data_source.download_file_stream(
                share_name=share_name,
                file_path=file_path,
            )
        except HTTPException:
            raise
        except Exception as e:
            # describe_error, and no traceback: str() of an Azure error carries the response body.
            self.logger.error(f"Error downloading file for record {record.id}: {describe_error(e)}")
            raise self._stream_error(e) from e

        async def chunks() -> AsyncGenerator[bytes, None]:
            try:
                async for chunk in downloader.chunks():
                    yield chunk
            except Exception as e:
                # A later range request can fail too (a file deleted mid-download).
                self.logger.error(f"Error streaming file for record {record.id}: {describe_error(e)}")
                raise self._stream_error(e) from e

        return create_stream_record_response(
            chunks(),
            filename=record.record_name,
            mime_type=record.mime_type if record.mime_type else "application/octet-stream",
            fallback_filename=f"record_{record.id}"
        )

    def _stream_error(self, exc: Exception) -> HTTPException:
        # Azure refuses a bad or expired credential with 403 AuthenticationFailed (Entra ID with
        # no status at all); the user must reconnect, which is what a source 401 means.
        if error_code_of(exc) in _CREDENTIALS_REJECTED_CODES:
            return map_source_status(HttpStatusCode.UNAUTHORIZED.value, connector=self.display_name)
        return to_stream_error(exc, connector=self.display_name)

    async def cleanup(self) -> None:
        """Clean up resources used by the connector."""
        self.logger.info("Cleaning up Azure Files connector resources.")
        if self.data_source:
            await self.data_source.close_async_client()
        self.data_source = None

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: str | None = None,
        cursor: str | None = None,
    ) -> FilterOptionsResponse:
        """Get dynamic filter options for filters."""
        if filter_key == "shares":
            return await self._get_share_options(page, limit, search, cursor)
        else:
            raise ValueError(f"Unsupported filter key: {filter_key}")

    async def _get_share_options(
        self, page: int, limit: int, search: str | None, cursor: str | None = None
    ) -> FilterOptionsResponse:
        """One page of file shares, paged by Azure's continuation token.

        Share names are lowercase, and the service filters by prefix, so a search
        matches shares whose name starts with it.
        """
        try:
            if not self.data_source:
                return FilterOptionsResponse(
                    success=False,
                    options=[],
                    page=page,
                    limit=limit,
                    has_more=False,
                    message="Azure Files connector is not initialized",
                )

            response = await self.data_source.list_shares_page(
                results_per_page=limit,
                continuation_token=cursor or None,
                name_starts_with=search.strip().lower() if search and search.strip() else None,
            )
            if not response.success:
                return FilterOptionsResponse(
                    success=False,
                    options=[],
                    page=page,
                    limit=limit,
                    has_more=False,
                    message=f"Failed to list shares: {response.error}",
                )

            data = response.data or {}
            next_cursor = data.get("next")
            options = [
                FilterOption(id=share["name"], label=share["name"])
                for share in data.get("shares") or []
                if share.get("name")
            ]

            return FilterOptionsResponse(
                success=True,
                options=options,
                page=page,
                limit=limit,
                has_more=bool(next_cursor),
                cursor=next_cursor,
            )

        except Exception as e:
            self.logger.error(f"Error getting share options: {e}", exc_info=True)
            return FilterOptionsResponse(
                success=False,
                options=[],
                page=page,
                limit=limit,
                has_more=False,
                message=f"Error: {str(e)}",
            )

    def handle_webhook_notification(self, notification: dict) -> None:
        """Handle webhook notifications from the source."""
        raise NotImplementedError("This method is not supported")

    async def reindex_records(self, record_results: list[Record]) -> None:
        """Reindex records by checking for updates at source and publishing reindex events."""
        try:
            if not record_results:
                self.logger.info("No records to reindex")
                return

            self.logger.info(
                f"Starting reindex for {len(record_results)} Azure Files records"
            )

            if not self.data_source:
                self.logger.error("Azure Files connector is not initialized.")
                raise Exception("Azure Files connector is not initialized.")

            org_id = self.data_entities_processor.org_id
            updated_records = []
            non_updated_records = []

            for record in record_results:
                try:
                    updated_record_data = await self._check_and_fetch_updated_record(
                        org_id, record
                    )
                    if updated_record_data:
                        updated_record, permissions = updated_record_data
                        updated_records.append((updated_record, permissions))
                    else:
                        non_updated_records.append(record)
                except FileNotFoundError as e:
                    self.logger.warning(f"Not reindexing record {record.id}: {e}")
                    continue
                except Exception as e:
                    self.logger.error(
                        f"Error checking record {record.id} at source: {e}", exc_info=True
                    )
                    continue

            if updated_records:
                await self.data_entities_processor.on_new_records(updated_records)
                self.logger.info(f"Updated {len(updated_records)} records in DB")

            if non_updated_records:
                await self.data_entities_processor.reindex_existing_records(
                    non_updated_records
                )
                self.logger.info(
                    f"Published reindex events for {len(non_updated_records)} records"
                )

        except Exception as e:
            self.logger.error(f"Error during Azure Files reindex: {e}", exc_info=True)
            raise

    async def _check_and_fetch_updated_record(
        self, org_id: str, record: Record
    ) -> tuple[Record, list[Permission]] | None:
        """Check if record has been updated at source and fetch updated data."""
        try:
            share_name = record.external_record_group_id
            external_record_id = record.external_record_id

            if not share_name or not external_record_id:
                self.logger.warning(
                    f"Missing share or external_record_id for record {record.id}"
                )
                return None

            if external_record_id.startswith(f"{share_name}/"):
                item_path = external_record_id[len(f"{share_name}/") :]
            else:
                item_path = external_record_id.lstrip("/")

            if not item_path:
                self.logger.warning(f"Invalid path for record {record.id}")
                return None

            # Check if it's a file or directory
            is_file = isinstance(record, FileRecord) and record.is_file

            if is_file:
                response = await self.data_source.get_file_properties(
                    share_name=share_name, file_path=item_path
                )
            else:
                response = await self.data_source.get_directory_properties(
                    share_name=share_name, directory_path=item_path
                )

            if not response.success:
                if response.error_code in _NOT_FOUND_CODES:
                    # Re-queueing it would only fail in indexing; the next sync removes the record.
                    raise FileNotFoundError(f"{share_name}/{item_path} no longer exists")
                self.logger.warning(
                    f"Could not read {share_name}/{item_path} to reindex record {record.id}: {response.error}"
                )
                return None

            item_metadata = response.data
            if not item_metadata:
                return None

            # Same revision the sync writes; comparing with the etag instead made
            # this path and the sync overwrite each other's value every run.
            current_etag = (
                item_metadata.get("etag", "").strip('"')
                if item_metadata.get("etag")
                else ""
            )
            current_revision = self._get_azure_files_revision_id(
                {**item_metadata, "is_directory": not is_file}
            )

            if current_revision == record.external_revision_id:
                self.logger.debug(
                    f"Record {record.id}: revision unchanged ({current_revision})"
                )
                return None

            self.logger.debug(f"Record {record.id}: revision changed")

            # Parse timestamps
            last_modified = item_metadata.get("last_modified")
            if last_modified:
                if isinstance(last_modified, datetime):
                    timestamp_ms = datetime_to_epoch_ms(last_modified)
                elif isinstance(last_modified, str):
                    try:
                        obj_dt = datetime.fromisoformat(
                            last_modified.replace("Z", "+00:00")
                        )
                        timestamp_ms = int(obj_dt.timestamp() * 1000)
                    except ValueError:
                        timestamp_ms = get_epoch_timestamp_in_ms()
                else:
                    timestamp_ms = get_epoch_timestamp_in_ms()
            else:
                timestamp_ms = get_epoch_timestamp_in_ms()

            # Directory properties carry no is_directory flag; the stored record says what it is.
            is_directory = not is_file

            extension = get_file_extension(item_path) if is_file else None
            # From the name, as the sync does: a listing has no content type, and the two must agree.
            mime_type = MimeTypes.FOLDER.value if is_directory else get_mimetype_for_azure_files(item_path)

            parent_path = get_parent_path(item_path)
            parent_external_id = (
                f"{share_name}/{parent_path}" if parent_path else share_name
            )
            # Match Azure Blob / GCS behavior: parent is always treated as a FILE record
            parent_record_type = RecordType.FILE

            if is_directory:
                web_url = self._generate_directory_url(share_name, item_path)
            else:
                web_url = self._generate_web_url(share_name, item_path)


            record_name = item_path.split("/")[-1] if "/" in item_path else item_path

            updated_external_record_id = f"{share_name}/{item_path}"

            # Get content MD5 hash
            content_md5 = item_metadata.get("content_md5")
            if content_md5 and isinstance(content_md5, bytes):
                import base64

                content_md5 = base64.b64encode(content_md5).decode("utf-8")

            updated_record = FileRecord(
                id=record.id,
                record_name=record_name,
                # Use RecordType.FILE for both files and directories; directories are distinguished via is_file flag.
                record_type=RecordType.FILE,
                record_group_type=RecordGroupType.FILE_SHARE.value,
                external_record_group_id=share_name,
                external_record_id=updated_external_record_id,
                external_revision_id=current_revision,
                version=0,
                origin=OriginTypes.CONNECTOR.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                source_created_at=record.source_created_at,
                source_updated_at=timestamp_ms,
                weburl=web_url,
                signed_url=None,
                # Match Azure Blob / GCS behavior: directories are internal placeholders with hidden weburl
                hide_weburl=True,
                is_internal=True if is_directory else False,
                parent_external_record_id=parent_external_id,
                parent_record_type=parent_record_type,
                size_in_bytes=item_metadata.get("size", 0) if is_file else 0,
                is_file=is_file,
                extension=extension,
                path=item_path,
                mime_type=mime_type,
                md5_hash=content_md5,
                etag=current_etag,
            )

            # Root-level items: do not link to the share as a parent
            if (
                updated_record.parent_external_record_id
                and updated_record.external_record_group_id
                and updated_record.parent_external_record_id == updated_record.external_record_group_id
            ):
                updated_record.parent_external_record_id = None
                updated_record.parent_record_type = None

            if (
                hasattr(self, "indexing_filters")
                and self.indexing_filters
                and not self.indexing_filters.is_enabled(
                    IndexingFilterKey.FILES, default=True
                )
            ):
                updated_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

            permissions = await self._create_azure_files_permissions(
                share_name, item_path
            )

            return updated_record, permissions

        except FileNotFoundError:
            raise
        except Exception as e:
            self.logger.error(f"Error checking record {record.id} at source: {e}", exc_info=True)
            return None

    async def run_incremental_sync(self) -> None:
        """Azure Files has no change feed: an incremental sync is a full walk."""
        await self.run_sync()

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
        **kwargs: object,
    ) -> "AzureFilesConnector":
        """Factory method to create and initialize connector."""
        org_id = kwargs.pop("org_id", None)
        # Get account name from config for entities processor
        config = await config_service.get_config(
            f"/services/connectors/{connector_id}/config"
        )
        account_name = ""
        if config:
            auth_config = config.get("auth", {})
            connection_string = auth_config.get("connectionString", "")
            if connection_string:
                extracted = cls._extract_account_name_from_connection_string(
                    connection_string
                )
                account_name = extracted or ""

        data_entities_processor = AzureFilesDataSourceEntitiesProcessor(
            logger, data_store_provider, config_service, account_name=account_name
        )
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

