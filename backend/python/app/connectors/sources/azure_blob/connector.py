"""
Azure Blob Storage Connector

Connector for synchronizing data from Azure Blob Storage containers. This connector
uses the native Azure Blob Storage API with connection string authentication.
"""

import asyncio
import base64
import mimetypes
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Container, Iterable
from datetime import datetime, timedelta, timezone
from logging import Logger
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

if TYPE_CHECKING:
    from azure.storage.blob import BlobProperties  # type: ignore[import-untyped]

from azure.core.async_paging import AsyncItemPaged  # type: ignore[import-untyped]
from azure.core.exceptions import AzureError  # type: ignore[import-untyped]
from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    Connectors,
    MimeTypes,
    OriginTypes,
    PermissionModel,
    ProgressStatus,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.constants import IconPaths
from app.connectors.core.base.connector.connector_service import BaseConnector, ConnectorInitError
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.sync_point.sync_point import (
    FailedItems,
    SyncDataPointType,
    SyncPoint,
    generate_record_sync_point_key,
    listing_checkpoint,
)
from app.connectors.core.registry.auth_builder import AuthBuilder, AuthType
from app.connectors.core.registry.folder_scope import (
    FolderScope,
    clean_up_scope,
    listed_record_ids,
    path_in_container,
    recorded_record_ids,
    remove_container,
    remove_deselected_containers,
    remove_recorded_not_listed,
    stored_containers,
)
from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage
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
from app.connectors.sources.azure_blob.common.apps import AzureBlobApp
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
from app.sources.client.azure.azure_blob import AzureBlobClient
from app.sources.external.azure.azure_blob import AzureBlobDataSource, azure_error_code, azure_error_text
from app.connectors.core.base.error.stream_errors import (
    connector_not_ready,
    not_downloadable,
    not_found_at_source,
    to_stream_error,
)
from app.services.notification.types import NotificationSeverity, NotificationType
from app.utils.streaming import create_stream_record_response, stream_content
from app.utils.time_conversion import datetime_to_epoch_ms, get_epoch_timestamp_in_ms
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

# Default connector endpoint for signed URL generation
DEFAULT_CONNECTOR_ENDPOINT = "http://localhost:8000"

# Download links are only used server-side, right away; a leaked one should die quickly.
_SAS_LIFETIME = timedelta(minutes=15)

# Containers listed at once in one sync.
_CONTAINER_CONCURRENCY = 3

# Azure error codes that mean the container is gone, not unreachable.
_CONTAINER_GONE_CODES = frozenset({"ContainerNotFound", "ResourceNotFound"})

# Azure error codes that mean the credentials are wrong, expired or lack a permission.
_AUTH_ERROR_CODES = frozenset({
    "AuthenticationFailed", "InvalidAuthenticationInfo", "KeyBasedAuthenticationNotPermitted",
    "AuthorizationFailure", "AuthorizationPermissionMismatch", "AuthorizationResourceTypeMismatch",
    "AuthorizationServiceMismatch", "AuthorizationProtocolMismatch", "AuthorizationSourceIPMismatch",
})


class _NotReindexable(Exception):
    """The blob cannot be read now: gone, refused, or in the Archive tier."""

_SETUP_INFO = (
    "The connection string under Access keys in the Azure portal holds the account key, which "
    "has full control of the whole storage account. Prefer a SAS with only Read and List "
    "permissions, or the Microsoft Entra ID option with the Storage Blob Data Reader role. "
    "Azure permissions (role assignments, SAS scope, access policies) are not applied in "
    "PipesHub: everyone with access to this connector can read every file it syncs, and a team "
    "connector shares them with the whole organisation. With no containers picked under "
    "Container Names, it syncs every container the credentials can list, so for a team "
    "connector pick only containers the whole organisation may read. Citations open files in "
    "PipesHub's viewer, since blob URLs of a private container cannot be opened in a browser."
)

# When sync targets explicit container names without a prior list response, fetch each
# container's properties instead of listing all containers (avoids heavy calls on large accounts).
_MAX_CONTAINERS_FOR_PER_PROPERTY_TIMESTAMP_FETCH = 128


def get_file_extension(blob_name: str) -> str | None:
    """Extracts the extension from a blob name."""
    if "." in blob_name:
        parts = blob_name.split(".")
        if len(parts) > 1:
            return parts[-1].lower()
    return None


def get_parent_path_from_blob_name(blob_name: str) -> str | None:
    """Extracts the parent path from a blob name (without leading slash).

    For a blob like 'a/b/c/file.txt', returns 'a/b/c'
    For a blob like 'a/b/c/', returns 'a/b'
    """
    if not blob_name:
        return None
    # Remove leading slash and trailing slash (if present)
    normalized_name = blob_name.lstrip("/").rstrip("/")
    if not normalized_name or "/" not in normalized_name:
        return None
    parent_path = "/".join(normalized_name.split("/")[:-1])
    return parent_path if parent_path else None


def get_folder_path_segments_from_blob_name(blob_name: str) -> list[str]:
    """Derives folder path segments from a blob name for hierarchy creation.

    Azure Blob Storage, like S3, represents folders implicitly via blob names.
    For each blob name (e.g. a/b/c/file.txt), returns the folder path segments
    that must exist:

    Example:
        'a/b/c/file.txt' -> ['a', 'a/b', 'a/b/c']
        'file.txt'       -> []
    """
    if not blob_name:
        return []
    normalized = blob_name.lstrip("/").rstrip("/")
    if not normalized or "/" not in normalized:
        return []
    parts = normalized.split("/")
    # Last part is the file (or folder blob); segments are the folder path prefix
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


def get_mimetype_for_azure_blob(blob_name: str, *, is_folder: bool = False) -> str:
    """Determines the correct MimeTypes string value for an Azure blob."""
    if is_folder:
        return MimeTypes.FOLDER.value

    mime_type_str, _ = mimetypes.guess_type(blob_name)
    if mime_type_str:
        try:
            return MimeTypes(mime_type_str).value
        except ValueError:
            return MimeTypes.BIN.value
    return MimeTypes.BIN.value


def parse_parent_external_id(parent_external_id: str) -> tuple[str, str | None]:
    """Parse parent_external_id to extract container_name and normalized path.

    Args:
        parent_external_id: External ID in format "container_name/path" or just "container_name"

    Returns:
        A tuple of (container_name, normalized_path) where normalized_path is None
        if parent_external_id contains only a container name.
    """
    if "/" in parent_external_id:
        parts = parent_external_id.split("/", 1)
        container_name = parts[0]
        path = parts[1]
        path = path.lstrip("/")
        if path and not path.endswith("/"):
            path = path + "/"
        return container_name, path
    else:
        container_name = parent_external_id
        return container_name, None


def get_parent_weburl_for_azure_blob(parent_external_id: str, account_url: str) -> str:
    """Generate webUrl for an Azure Blob directory based on parent external_id.

    Args:
        parent_external_id: External ID in format "container_name/path" or just "container_name"
        account_url: The account's blob endpoint, e.g. https://<account>.blob.core.windows.net

    Returns:
        Blob endpoint URL for the directory
    """
    container_name, path = parse_parent_external_id(parent_external_id)
    base_url = f"{account_url.rstrip('/')}/{container_name}"
    if path:
        return f"{base_url}/{path}"
    return base_url


def get_parent_path_for_azure_blob(parent_external_id: str) -> str | None:
    """Extract directory path from Azure Blob parent external_id.

    Args:
        parent_external_id: External ID in format "container_name/path" or just "container_name"

    Returns:
        Directory path without container name prefix, or None for root directories
    """
    if "/" in parent_external_id:
        parts = parent_external_id.split("/", 1)
        directory_path = parts[1]
        if directory_path and not directory_path.endswith("/"):
            directory_path = directory_path + "/"
        return directory_path
    else:
        return None


def _container_last_modified_epoch_ms_from_list(
    containers_data: Iterable[Any] | None, target_names: set[str]
) -> dict[str, int]:
    """Map container name -> last_modified as epoch ms from list_containers data.

    The Azure data source returns dicts with ``name`` and ISO ``last_modified``.
    List API does not expose a separate creation time; we use last_modified for
    both source_created_at and source_updated_at on the record group.
    """
    out: dict[str, int] = {}
    if not containers_data or not target_names:
        return out
    for container in containers_data:
        name: str | None = None
        lm: Any = None
        if isinstance(container, dict):
            name = container.get("name")
            lm = container.get("last_modified")
        else:
            name = getattr(container, "name", None)
            lm = getattr(container, "last_modified", None)
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


class AzureBlobDataSourceEntitiesProcessor(DataSourceEntitiesProcessor):
    """Azure Blob processor that extends the base processor with Azure-specific placeholder record logic."""

    def __init__(
        self,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        account_url: str = "",
    ) -> None:
        super().__init__(logger, data_store_provider, config_service)
        self.account_url = account_url

    def _create_placeholder_parent_record(
        self,
        parent_external_id: str,
        parent_record_type: RecordType,
        record: Record,
        record_name: str | None = None,
        record_group_type: str | None = None,
        external_record_group_id: str | None = None,
    ) -> Record:
        """
        Create a placeholder parent record with Azure-specific weburl and path.
        """
        parent_record = super()._create_placeholder_parent_record(
            parent_external_id,
            parent_record_type,
            record,
            record_name=record_name,
            record_group_type=record_group_type,
            external_record_group_id=external_record_group_id,
        )

        if parent_record_type == RecordType.FILE and isinstance(parent_record, FileRecord):
            weburl = get_parent_weburl_for_azure_blob(parent_external_id, self.account_url)
            path = get_parent_path_for_azure_blob(parent_external_id)
            parent_record.weburl = weburl
            parent_record.path = path
            parent_record.is_internal = True
            parent_record.hide_weburl = True

        return parent_record


@ConnectorBuilder("Azure Blob")\
    .in_group("Azure")\
    .with_description("Sync files and folders from Azure Blob Storage")\
    .with_categories(["Storage"])\
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
    .with_permission_model(PermissionModel.APP_LEVEL)\
    .with_auth([
        AuthBuilder.type(AuthType.CONNECTION_STRING).fields([
            AuthField(
                name="azureBlobConnectionString",
                display_name="Connection String",
                placeholder="BlobEndpoint=https://<account>.blob.core.windows.net/;SharedAccessSignature=...",
                description=(
                    "A SAS connection string with only Read and List permissions is enough. The "
                    "portal's Access keys connection string holds the account key, which has full "
                    "control of the storage account."
                ),
                field_type="PASSWORD",
                max_length=2000,
                is_secret=True
            ),
        ]),
        AuthBuilder.type(AuthType.ENTRA_ID).fields([
            AuthField(
                name="accountUrl",
                display_name="Blob Endpoint",
                placeholder="https://<account>.blob.core.windows.net",
                description="The storage account's blob endpoint",
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
                description="Application (client) ID; give the app the Storage Blob Data Reader role",
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
    .with_info(_SETUP_INFO)\
    .with_resilience_config(rate_limit=50, max_retries=3)\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.AZURE_BLOB.value))
        .add_documentation_link(DocumentationLink(
            "Create a read-only SAS",
            "https://learn.microsoft.com/en-us/azure/storage/common/storage-sas-overview",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            "Storage Blob Data Reader role",
            "https://learn.microsoft.com/en-us/azure/storage/blobs/assign-azure-role-data-access",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/azure/azureblob',
            'pipeshub'
        ))
        .add_filter_field(FilterField(
            name="containers",
            display_name="Container Names",
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            description="Containers to sync; leave empty to sync every container the credentials can list. Everything a team connector syncs is visible to the whole organisation.",
            option_source_type=OptionSourceType.DYNAMIC,
            default_value=[],
            default_operator=MultiselectOperator.IN.value
        ))
        .add_filter_field(CommonFields.folder_paths_filter("container"))
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
class AzureBlobConnector(BaseConnector):
    """
    Connector for synchronizing data from Azure Blob Storage containers.
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
            app=AzureBlobApp(connector_id),
            logger=logger,
            data_entities_processor=data_entities_processor,
            data_store_provider=data_store_provider,
            config_service=config_service,
            connector_id=connector_id,
            scope=scope,
            created_by=created_by,
        )

        self.connector_name = Connectors.AZURE_BLOB
        self.connector_id = connector_id
        self.filter_key = "azureblob"

        # Initialize sync point for tracking record changes
        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_id=self.connector_id,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider,
            )

        self.record_sync_point = _create_sync_point(SyncDataPointType.RECORDS)

        self.data_source: AzureBlobDataSource | None = None
        self.batch_size = 100
        self._reset_run_state()
        self.container_name: str | None = None
        self.creator_email: str | None = None  # Cached to avoid repeated DB queries
        self.account_name: str | None = None
        self.account_url: str = ""

        # Initialize filter collections
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

    def _reset_run_state(self) -> None:
        """Per-sync bookkeeping, shared by the containers a sync lists at once."""
        # Records moved during this sync: two new copies of a deleted blob
        # must not both take its record before the batch naming it is saved.
        self._moved_record_ids: set[str] = set()
        # Claim and presence check run as one step: containers are listed side by side.
        self._move_claim_lock = asyncio.Lock()
        # Folder ids already saved this sync: every file under a/b/c would save a, a/b and a/b/c again.
        self._folders_written: set[str] = set()
        # Record ids saved this sync, which the removal pass must keep.
        self._written_ids: set[str] = set()
        # Removals waiting for every container of the sync to be listed; None outside one.
        self._queued_removals: list[tuple[str, Callable[[], Awaitable[None]]]] | None = None
        # Part of the source was not read (a container, a listing or a blob failed).
        self._sync_incomplete = False
        # Blobs this sync could not process, how many of them Azure refused for the credentials and
        # its reason; and its reason when it refused the credentials for a container or a listing.
        self._failed_blob_count = 0
        self._refused_blob_count = 0
        self._blob_auth_error: str | None = None
        self._auth_error: str | None = None

    def full_sync_coverage(self) -> FullSyncCoverage:
        """A sync that skipped a container, a listing or a blob after an error did not read
        everything, so a full sync then unlinks nothing it did not see."""
        return FullSyncCoverage(complete=not self._sync_incomplete)

    async def _save(self, records: list[tuple[Record, list[Permission]]]) -> None:
        await self.data_entities_processor.on_new_records(records)
        # The processor gives a record the id it is stored under.
        self._written_ids.update(record.id for record, _ in records)

    async def _notify_error(self, kind: NotificationType, title: str, message: str) -> None:
        await self.notify(
            type=kind, severity=NotificationSeverity.ERROR, title=f"{self.display_name}: {title}", message=message,
        )

    def _log_failure(self, what: str, error: BaseException, *, blob: bool = False) -> str:
        """Log a failed call and return its reason. An Azure error is logged by status, code and
        message only: its own text, which a traceback prints, can hold the request signature.

        A refused ``blob`` is recorded apart from a refused container or listing, so one file's
        refusal does not name a container that failed for another reason."""
        azure = error if isinstance(error, AzureError) else error.__cause__
        if isinstance(azure, AzureError):
            reason = azure_error_text(azure)
            if azure_error_code(azure) in _AUTH_ERROR_CODES:
                if blob:
                    self._refused_blob_count += 1
                    self._blob_auth_error = reason
                else:
                    self._auth_error = reason
            self.logger.error(f"{what}: {reason}")
            return reason
        self.logger.error(f"{what}: {error}", exc_info=True)
        return str(error)

    def get_app_users(self, users: list[User]) -> list[AppUser]:
        """Convert User objects to AppUser objects for Azure Blob connector."""
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
        """Initializes the Azure Blob client using connection string from the config service."""
        config = await self.config_service.get_config(
            f"/services/connectors/{self.connector_id}/config"
        )
        if not config:
            self.logger.error("Azure Blob configuration not found.")
            await self._notify_error(
                NotificationType.CONNECTOR_AUTH_ERROR, "Configuration not found",
                "The Azure Blob configuration of this connector is missing.",
            )
            return False

        auth_config = config.get("auth", {})
        if auth_config.get("authType") != AuthType.ENTRA_ID and not auth_config.get("azureBlobConnectionString"):
            self.logger.error("Azure Blob connection string not found in configuration.")
            await self._notify_error(
                NotificationType.CONNECTOR_AUTH_ERROR, "Credentials missing",
                "The Azure Blob connection string is missing.",
            )
            return False

        # Container name is no longer stored in config - it's determined at sync time
        self.container_name = None

        # Load creator email if needed (for personal scope permission creation)
        await self._load_creator_email()

        try:
            client = await AzureBlobClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id,
            )
            self.data_source = AzureBlobDataSource(client)

            try:
                self.account_name = client.get_account_name()
                self.account_url = client.get_account_url()
            except Exception as e:
                self.logger.warning(f"Could not read the account name and endpoint from the credentials: {e}")
                self.account_name = None
                self.account_url = ""

            if isinstance(self.data_entities_processor, AzureBlobDataSourceEntitiesProcessor):
                self.data_entities_processor.account_url = self.account_url

            # Load connector filters
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, self.filter_key, self.connector_id, self.logger
            )

            self.logger.info("Azure Blob client initialized successfully.")
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize Azure Blob client: {e}", exc_info=True)
            await self._notify_error(
                NotificationType.CONNECTOR_AUTH_ERROR, "Could not connect",
                f"Failed to initialize the Azure Blob client: {e}",
            )
            return False

    def _generate_web_url(self, container_name: str, blob_name: str) -> str:
        """Generate the web URL for an Azure blob."""
        return f"{self.account_url}/{container_name}/{quote(blob_name)}"

    def _generate_parent_web_url(self, parent_external_id: str) -> str:
        """Generate the web URL for an Azure Blob parent folder/directory."""
        return get_parent_weburl_for_azure_blob(parent_external_id, self.account_url)

    def _extract_container_names(self, containers_data: Iterable[Any] | None) -> list[str]:
        """Extract container names from list_containers response data.

        Handles both dict-based and ContainerProperties-like objects.
        """
        container_names: list[str] = []
        if not containers_data:
            return container_names

        for container in containers_data:
            container_name: str | None = None

            # Handle both dict and object formats for robustness
            if isinstance(container, dict):
                container_name = container.get("name")
            else:
                # Fallback for ContainerProperties objects
                container_name = getattr(container, "name", None)

            if container_name:
                container_names.append(container_name)

        return container_names

    async def run_sync(self) -> None:
        """Runs a full synchronization from containers."""
        try:
            self.logger.info("Starting Azure Blob full sync.")

            if not self.data_source:
                raise ConnectionError("Azure Blob connector is not initialized.")
            self._reset_run_state()

            # Reload sync and indexing filters to pick up configuration changes
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, self.filter_key, self.connector_id, self.logger
            )

            # Get sync filters
            sync_filters = self.sync_filters if hasattr(self, 'sync_filters') and self.sync_filters else FilterCollection()

            # Get container filter if specified
            selected_containers = included_names(sync_filters, "containers")
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

            if not self.container_name:
                await remove_deselected_containers(
                    self.data_entities_processor, self.config_service, self.connector_id, "containers", sync_filters, self.logger
                )

            # List all containers or use configured container
            containers_to_sync: list[str] = []
            containers_list_payload: list[Any] | None = None
            if self.container_name:
                containers_to_sync = [self.container_name]
                self.logger.info(f"Using configured container: {self.container_name}")
            elif selected_containers:
                containers_to_sync = selected_containers
                self.logger.info(f"Using filtered containers: {containers_to_sync}")
            else:
                self.logger.info("Listing all containers...")
                containers_response = await self.data_source.list_containers()
                if not containers_response.success:
                    self.logger.error(f"Failed to list containers: {containers_response.error}")
                    if getattr(containers_response, "error_code", None) in _AUTH_ERROR_CODES:
                        await self._notify_error(
                            NotificationType.CONNECTOR_AUTH_ERROR, "Credentials rejected",
                            f"Azure refused the connector's credentials while listing containers: "
                            f"{containers_response.error}. Update the credentials or their permissions; "
                            "nothing was removed.",
                        )
                    else:
                        await self._notify_error(
                            NotificationType.CONNECTOR_SYNC_ERROR, "Could not list containers",
                            f"Failed to list Azure Blob containers: {containers_response.error}. "
                            "Check the credentials and their List permission.",
                        )
                    raise ConnectionError(f"Failed to list Azure Blob containers: {containers_response.error}")
                containers_list_payload = containers_response.data or []
                containers_to_sync = [
                    name for name in self._extract_container_names(containers_list_payload)
                    if name_passes_filter(sync_filters, "containers", name)
                ]
                self.logger.info(f"Found {len(containers_to_sync)} container(s) to sync: {containers_to_sync}")

            container_ts_ms, gone = await self._container_timestamps_and_gone(
                containers_to_sync, containers_list_payload
            )
            if gone:
                self.logger.info(f"Containers deleted in Azure: {sorted(gone)}")
                for name in sorted(gone):
                    await remove_container(
                        self.data_entities_processor, self.connector_id, name,
                        f"now that {name} is deleted in Azure", self.logger,
                    )
                containers_to_sync = [n for n in containers_to_sync if n not in gone]

            # Create record groups for containers first
            await self._create_record_groups_for_containers(containers_to_sync, container_ts_ms)

            failed = await self._sync_containers(containers_to_sync)
            files = self._failed_files_text()
            if failed:
                self._sync_incomplete = True
                names = ", ".join(sorted(failed))
                if self._auth_error:
                    await self._notify_error(
                        NotificationType.CONNECTOR_AUTH_ERROR, "Credentials rejected",
                        f"Azure refused the connector's credentials while syncing {names}: {self._auth_error}. "
                        f"Update the credentials or their permissions; nothing was removed. {files}".rstrip(),
                    )
                else:
                    first = min(failed)
                    await self._notify_error(
                        NotificationType.CONNECTOR_SYNC_ERROR, "Some containers failed to sync",
                        f"Could not sync {len(failed)} container(s): {names}. {first}: {failed[first]}. "
                        f"The next sync retries them. {files}".rstrip(),
                    )
                raise RuntimeError(f"Azure Blob sync failed for container(s): {names}")
            if self._refused_blob_count:
                await self._notify_error(NotificationType.CONNECTOR_AUTH_ERROR, "Credentials rejected", files)
            elif self._failed_blob_count:
                await self._notify_error(NotificationType.CONNECTOR_SYNC_ERROR, "Some files failed to sync", files)

            self.logger.info("Azure Blob full sync completed.")
        except Exception as ex:
            self.logger.error(f"Error in Azure Blob connector run: {ex}", exc_info=True)
            raise

    def _failed_files_text(self) -> str:
        """What this sync's failed files come to, for its closing notice; "" when none failed."""
        text = []
        if self._refused_blob_count:
            text.append(
                f"Azure refused the connector's credentials for {self._refused_blob_count} file(s): "
                f"{self._blob_auth_error}. Update the credentials or their permissions."
            )
        others = self._failed_blob_count - self._refused_blob_count
        if others > 0:
            text.append(
                f"{others} {'other ' if self._refused_blob_count else ''}file(s) could not be synced. "
                "The next sync retries them; the connector's log names each one."
            )
        return " ".join(text)

    async def _sync_containers(self, container_names: list[str]) -> dict[str, str]:
        """Sync containers a few at a time, so one large container does not hold up the rest.
        Returns the reason each failed container failed; the others are synced either way."""
        semaphore = asyncio.Semaphore(_CONTAINER_CONCURRENCY)
        failed: dict[str, str] = {}

        async def sync_one(container_name: str) -> None:
            async with semaphore:
                try:
                    self.logger.info(f"Syncing container: {container_name}")
                    await self._sync_container(container_name)
                except Exception as e:
                    failed[container_name] = self._log_failure(f"Error syncing container {container_name}", e)

        self._queued_removals = []
        try:
            await asyncio.gather(*(sync_one(name) for name in container_names if name))
            queued, self._queued_removals = self._queued_removals, None
            # Only once every container is listed and every move is saved. After a failed
            # container nothing is removed: a blob moved into it was never claimed.
            if failed:
                self.logger.info(f"Not removing unlisted records: container(s) {sorted(failed)} failed to sync")
            for container_name, remove in queued:
                try:
                    await remove(keep_unlisted=bool(failed))
                except Exception as e:
                    failed[container_name] = self._log_failure(f"Error removing unlisted records in {container_name}", e)
        finally:
            self._queued_removals = None
        return failed

    async def _container_timestamps_and_gone(
        self, containers_to_sync: list[str], containers_list_payload: list[Any] | None,
    ) -> tuple[dict[str, int], set[str]]:
        """Container last-modified times, and the stored or named containers that no longer exist.

        A container is gone only when Azure says so (ContainerNotFound): an auth or network
        error must not delete its files. With every container listed, a stored one the listing
        lacks is checked; with names from the filter, each named one is.
        """
        target_names = {n for n in containers_to_sync if n}
        container_ts_ms = _container_last_modified_epoch_ms_from_list(containers_list_payload, target_names)
        if containers_list_payload is not None:
            listed = set(self._extract_container_names(containers_list_payload))
            try:
                to_check = await stored_containers(self.data_entities_processor, self.connector_id) - listed
            except Exception as e:  # checked again next sync
                self.logger.warning(f"Could not read the stored containers to find deleted ones: {e}")
                to_check = set()
        elif len(target_names) > _MAX_CONTAINERS_FOR_PER_PROPERTY_TIMESTAMP_FETCH:
            # One listing instead of a call per named container; only the names it lacks are checked.
            extra = await self.data_source.list_containers()
            if extra.success:
                container_ts_ms = _container_last_modified_epoch_ms_from_list(extra.data, target_names)
                to_check = target_names - set(self._extract_container_names(extra.data))
            else:
                self.logger.warning(f"Could not list containers to find deleted ones: {extra.error}")
                to_check = set()
        else:
            to_check = target_names
        gone: set[str] = set()
        for cname in sorted(to_check):
            prop = await self.data_source.get_container_properties(cname)
            if not prop.success:
                if getattr(prop, "error_code", None) in _CONTAINER_GONE_CODES:
                    gone.add(cname)
                else:
                    self.logger.warning(f"Could not read container {cname}; keeping its records: {prop.error}")
                continue
            lm = getattr(prop.data, "last_modified", None)
            if cname in target_names and lm is not None:
                container_ts_ms[cname] = datetime_to_epoch_ms(lm if isinstance(lm, datetime) else str(lm))
        return container_ts_ms, gone

    async def _create_record_groups_for_containers(
        self,
        container_names: list[str],
        container_last_modified_epoch_ms: dict[str, int] | None = None,
    ) -> None:
        """Create record groups for containers with appropriate permissions.

        Uses cached creator_email from init() to avoid repeated database queries.

        Args:
            container_names: Containers to represent as record groups.
            container_last_modified_epoch_ms: Optional map from container name to
                Azure ``last_modified`` (epoch ms) from list_containers. Used for
                source_created_at / source_updated_at so the knowledge hub shows
                real dates (Azure list does not expose separate creation time).
        """
        if not container_names:
            return

        ts_map = container_last_modified_epoch_ms or {}
        record_groups = []
        for container_name in container_names:
            if not container_name:
                continue

            lm_ms = ts_map.get(container_name)
            record_group = RecordGroup(
                name=container_name,
                external_group_id=container_name,
                group_type=RecordGroupType.BUCKET,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                description=f"Azure Blob Container: {container_name}",
                web_url=get_parent_weburl_for_azure_blob(container_name, self.account_url),
                source_created_at=lm_ms,
                source_updated_at=lm_ms,
                inherit_permissions=True,
            )
            record_groups.append((record_group, []))

        if record_groups:
            await self.data_entities_processor.on_new_record_groups(record_groups)
            self.logger.info(f"Created {len(record_groups)} record group(s) for containers")

    def _get_date_filters(self) -> tuple[int | None, int | None, int | None, int | None]:
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
                before_dt = datetime.fromisoformat(before_iso).replace(tzinfo=timezone.utc)
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
                before_dt = datetime.fromisoformat(before_iso).replace(tzinfo=timezone.utc)
                created_before_ms = int(before_dt.timestamp() * 1000)
                self.logger.info(f"Applying created date filter: before {before_dt}")

        return modified_after_ms, modified_before_ms, created_after_ms, created_before_ms

    def _pass_date_filters(
        self,
        blob: dict,
        modified_after_ms: int | None = None,
        modified_before_ms: int | None = None,
        created_after_ms: int | None = None,
        created_before_ms: int | None = None
    ) -> bool:
        """Returns True if Azure blob PASSES date filters (should be kept)."""
        blob_name = blob.get("name", "")
        is_folder = blob_name.endswith("/")
        if is_folder:
            return True

        if not any([modified_after_ms, modified_before_ms, created_after_ms, created_before_ms]):
            return True

        last_modified = blob.get("last_modified")
        if not last_modified:
            return True

        # Parse datetime
        if isinstance(last_modified, datetime):
            obj_timestamp_ms = int(last_modified.timestamp() * 1000)
        elif isinstance(last_modified, str):
            try:
                obj_dt = datetime.fromisoformat(last_modified.replace('Z', '+00:00'))
                obj_timestamp_ms = int(obj_dt.timestamp() * 1000)
            except ValueError:
                return True
        else:
            return True

        if modified_after_ms and obj_timestamp_ms < modified_after_ms:
            self.logger.debug(f"Skipping {blob_name}: modified {obj_timestamp_ms} before cutoff {modified_after_ms}")
            return False
        if modified_before_ms and obj_timestamp_ms > modified_before_ms:
            self.logger.debug(f"Skipping {blob_name}: modified {obj_timestamp_ms} after cutoff {modified_before_ms}")
            return False

        # Check creation time
        creation_time = blob.get("creation_time")
        if creation_time:
            if isinstance(creation_time, datetime):
                created_timestamp_ms = int(creation_time.timestamp() * 1000)
            elif isinstance(creation_time, str):
                try:
                    created_dt = datetime.fromisoformat(creation_time.replace('Z', '+00:00'))
                    created_timestamp_ms = int(created_dt.timestamp() * 1000)
                except ValueError:
                    created_timestamp_ms = None
            else:
                created_timestamp_ms = None

            if created_timestamp_ms:
                if created_after_ms and created_timestamp_ms < created_after_ms:
                    self.logger.debug(f"Skipping {blob_name}: created {created_timestamp_ms} before cutoff {created_after_ms}")
                    return False
                if created_before_ms and created_timestamp_ms > created_before_ms:
                    self.logger.debug(f"Skipping {blob_name}: created {created_timestamp_ms} after cutoff {created_before_ms}")
                    return False

        return True

    def _pass_extension_filter(self, blob_name: str, *, is_folder: bool = False) -> bool:
        """
        Checks if the Azure blob passes the configured file extensions filter.

        For MULTISELECT filters:
        - Operator IN: Only allow files with extensions in the selected list
        - Operator NOT_IN: Allow files with extensions NOT in the selected list

        Folders always pass this filter to maintain directory structure.

        Args:
            blob_name: The name of the blob
            is_folder: Whether this is a folder (ends with "/")

        Returns:
            True if the blob passes the filter (should be kept), False otherwise
        """
        # 1. ALWAYS Allow Folders
        # We must sync folders regardless of extension to ensure the directory structure
        # exists for any files that might be inside them.
        if is_folder:
            return True

        # 2. Get the extensions filter
        extensions_filter = self.sync_filters.get(SyncFilterKey.FILE_EXTENSIONS)

        # If no filter configured or filter is empty, allow all files
        if extensions_filter is None or extensions_filter.is_empty():
            return True

        # 3. Get the file extension from the blob name
        file_extension = get_file_extension(blob_name)

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

    async def _sync_container(self, container_name: str) -> None:
        """Sync a container, or only the folders the folder filter names.

        While ``_sync_containers`` lists several containers, the removal of what this listing
        lacks is queued in ``self._queued_removals`` instead of run: a record moved into a
        container still listing is not yet saved under its new name, and removing it now
        would delete it with its old one.
        """
        sync_filters = self.sync_filters if hasattr(self, 'sync_filters') and self.sync_filters else FilterCollection()
        scope = FolderScope.from_filters(sync_filters)
        if not scope.is_everything:
            self.logger.info(f"Folder filter for container {container_name}: {scope.describe()}")
        recorded = await recorded_record_ids(self.data_entities_processor, self.connector_id, container_name)
        listed: set[str] = set()
        unrecorded: list[dict] = []
        complete = True
        for prefix in scope.list_prefixes:
            prefix_listed, prefix_complete = await self._sync_container_prefix(container_name, prefix, scope, recorded, unrecorded)
            listed |= prefix_listed
            complete = complete and prefix_complete
        # One pass after every prefix: a rename into a folder listed later is
        # still stored under its old path until that folder is processed.
        if not complete:
            self._sync_incomplete = True
            self.logger.info(f"Not removing records in container {container_name}: a listing did not cover every blob")
        elif not await self._claim_records_of_gone_copies(container_name, unrecorded, listed, recorded):
            complete = False
            self.logger.info(f"Not removing records in container {container_name}: a blob's content could not be looked up")

        # Only the unlisted records wait for the other containers, not the whole listing.
        unlisted = {ext: rid for ext, rid in recorded.items() if ext not in listed} if complete else {}
        del recorded, listed, unrecorded

        async def remove(*, keep_unlisted: bool = False) -> None:
            if complete and not keep_unlisted:
                await remove_recorded_not_listed(
                    self.data_entities_processor, container_name, scope.list_prefixes, unlisted, set(),
                    self._written_ids | self._moved_record_ids, self.logger,
                )
            await clean_up_scope(
                self.data_entities_processor, self.record_sync_point, self.connector_id, container_name, scope, self.logger
            )

        if self._queued_removals is None:
            await remove()
        else:
            self._queued_removals.append((container_name, remove))

    async def _claim_records_of_gone_copies(
        self, container_name: str, unrecorded: list[dict], listed: set[str], recorded: Container[str] | None = None,
    ) -> bool:
        """Process the unchanged blobs that have no record of their own, when that is safe.

        The connectors take equal content at a new key for a move, so one record can
        stand for two keys. Before the removal pass deletes a record whose key is gone,
        a listed copy of its content takes it over. A copy whose content is held by a
        key that is still there gets a record of its own (``_process_azure_blob`` tells
        a copy from a move), so the record does not move back and forth between two
        live keys and the copy is still indexed.

        Returns False when the record holding some blob's content could not be looked
        up: any unlisted record may be that one, so none may be removed.
        """
        holders_known = True
        for blob_dict in unrecorded:
            blob_name = blob_dict.get("name", "")
            holder = None
            looked_up = False
            try:
                revision = self._get_azure_blob_revision_id(blob_dict)
                holder = (
                    await self.data_entities_processor.get_record_by_external_revision_id(self.connector_id, revision)
                    if revision else None
                )
                looked_up = True
                segments = get_folder_path_segments_from_blob_name(blob_name)
                if segments:
                    await self._ensure_parent_folders_exist(container_name, segments, recorded, blob_dict)
                record, permissions = await self._process_azure_blob(blob_dict, container_name)
                if record:
                    await self._save([(record, permissions)])
                    continue
                # The processor returns no record for a real name only on an error.
                failed = bool(blob_name.lstrip("/"))
            except Exception as e:
                self.logger.error(f"Error giving {blob_name} the record of its content: {e}", exc_info=True)
                failed = True
            if failed:
                self._failed_blob_count += 1
                # Else a full sync would unlink what this one could not read, the record kept below included.
                self._sync_incomplete = True
            if holder is not None:
                # Keep the record rather than delete content that is still listed.
                listed.add(holder.external_record_id or "")
            elif not looked_up:
                holders_known = False
        return holders_known

    async def _sync_container_prefix(
        self, container_name: str, prefix: str, scope: FolderScope, recorded: Container[str], unrecorded: list[dict],
    ) -> tuple[set[str], bool]:
        """Sync blobs under one prefix of a container ("" for all of it), with incremental sync support.

        The Azure SDK's list_blobs method returns an AsyncItemPaged object which is an
        async iterator that handles pagination internally. We iterate directly over it
        rather than using manual pagination.

        Returns the record ids the listing keeps, and whether it covered every blob.
        """
        if not self.data_source:
            raise ConnectionError("Azure Blob connector is not initialized.")

        sync_filters = self.sync_filters if hasattr(self, 'sync_filters') and self.sync_filters else FilterCollection()

        # Log extension filter status if configured
        extensions_filter = sync_filters.get(SyncFilterKey.FILE_EXTENSIONS)
        if extensions_filter and not extensions_filter.is_empty():
            filter_value = extensions_filter.value
            operator = extensions_filter.get_operator()
            operator_str = operator.value if hasattr(operator, 'value') else str(operator)
            self.logger.info(
                f"File extensions filter active for container {container_name}: "
                f"operator={operator_str}, extensions={filter_value}"
            )

        user_date_filters = self._get_date_filters()
        modified_after_ms, modified_before_ms, created_after_ms, created_before_ms = user_date_filters

        # Each listed prefix keeps its own last sync time.
        sync_point_key = generate_record_sync_point_key(
            RecordType.FILE.value, "container", f"{container_name}/{prefix}" if prefix else container_name
        )
        sync_point = await self.record_sync_point.read_sync_point(sync_point_key)
        last_sync_time = sync_point.get("last_sync_time") if sync_point else None
        listing_started_at = get_epoch_timestamp_in_ms()

        if last_sync_time:
            user_modified_after_ms = modified_after_ms
            if user_modified_after_ms:
                modified_after_ms = max(user_modified_after_ms, last_sync_time)
            else:
                modified_after_ms = last_sync_time

        batch_records = []
        max_timestamp = last_sync_time if last_sync_time else 0
        blob_count = 0
        listing_failed: Exception | None = None
        failed = FailedItems()
        listed: set[str] = set()
        unjudged = False

        try:
            response = await self.data_source.list_blobs(
                container_name=container_name,
                prefix=prefix or None,
                # ADLS Gen2 marks directories only in metadata (hdi_isfolder).
                include=["metadata"],
            )

            if not response.success:
                raise ConnectionError(response.error or "Unknown error")

            if response.data is None:
                self.logger.info(f"No blobs found in container {container_name}")
                return listed, False

            async for blob in self._paced(response.data):
                obj_timestamp_ms = None
                judged = False
                try:
                    blob_count += 1
                    # Convert BlobProperties to dict for consistent handling
                    blob_dict = self._blob_properties_to_dict(blob)
                    blob_name = blob_dict.get("name", "")
                    if not blob_name:
                        continue

                    if self._is_folder_blob(blob_dict):
                        # A folder marker ('a/b/') or an ADLS Gen2 directory ('a/b') is the same
                        # folder record that the paths of its files imply (container/a/b).
                        folder = blob_name.strip("/")
                        if folder and scope.includes_folder(f"{folder}/"):
                            listed |= listed_record_ids(container_name, folder)
                            judged = True
                            # An ADLS directory unchanged since the last sync already has its folder.
                            if not (last_sync_time and not self._pass_date_filters(
                                blob_dict, modified_after_ms, modified_before_ms, created_after_ms, created_before_ms
                            )):
                                await self._ensure_parent_folders_exist(
                                    container_name, [*get_folder_path_segments_from_blob_name(folder), folder],
                                    recorded, blob_dict,
                                )
                        continue

                    if not scope.includes_file(blob_name):
                        continue

                    # Check extension filter
                    if not self._pass_extension_filter(blob_name):
                        self.logger.debug(
                            f"Skipping {blob_name}: does not pass extension filter"
                        )
                        continue

                    if not self._pass_date_filters(blob_dict, *user_date_filters):
                        continue

                    # Kept before processing: a blob that fails to process still exists.
                    listed |= listed_record_ids(container_name, blob_name)
                    judged = True

                    if last_sync_time and not self._pass_date_filters(
                        blob_dict, modified_after_ms, modified_before_ms, created_after_ms, created_before_ms
                    ) and not self._tier_changed_since(blob_dict, last_sync_time):
                        if f"{container_name}/{blob_name.lstrip('/')}" not in recorded:
                            # Its content may be held by another key's record; decided once every prefix is listed.
                            unrecorded.append(blob_dict)
                        continue

                    # Track max timestamp for incremental sync
                    last_modified = blob_dict.get("last_modified")
                    if last_modified:
                        if isinstance(last_modified, datetime):
                            obj_timestamp_ms = int(last_modified.timestamp() * 1000)
                        elif isinstance(last_modified, str):
                            try:
                                obj_dt = datetime.fromisoformat(last_modified.replace('Z', '+00:00'))
                                obj_timestamp_ms = int(obj_dt.timestamp() * 1000)
                            except ValueError:
                                pass
                    tier_changed = blob_dict.get("blob_tier_change_time")
                    if isinstance(tier_changed, datetime):
                        obj_timestamp_ms = max(obj_timestamp_ms or 0, datetime_to_epoch_ms(tier_changed))
                    if obj_timestamp_ms:
                        max_timestamp = max(max_timestamp, obj_timestamp_ms)

                    # Ensure folder hierarchy exists from blob path (Azure Blob has no folder objects)
                    path_segments = get_folder_path_segments_from_blob_name(blob_name)
                    if path_segments:
                        await self._ensure_parent_folders_exist(container_name, path_segments, recorded, blob_dict)

                    record, permissions = await self._process_azure_blob(
                        blob_dict, container_name, recorded
                    )
                    if record:
                        batch_records.append((record, permissions))

                        if len(batch_records) >= self.batch_size:
                            await self._save(batch_records)
                            batch_records = []
                    elif blob_name.lstrip("/"):
                        # The processor returns no record for a real name only on an error.
                        failed.add(obj_timestamp_ms)
                except Exception as e:
                    error_blob_name = blob_dict.get("name", "unknown") if "blob_dict" in locals() else "unknown"
                    self._log_failure(
                        f"Error processing blob {error_blob_name} in container {container_name}", e, blob=True,
                    )
                    failed.add(obj_timestamp_ms)
                    # An object that errored before it was judged may still be kept.
                    unjudged = unjudged or not judged
                    continue

            self.logger.info(f"Processed {blob_count} blobs from container {container_name}")

        except Exception as e:
            self._log_failure(f"Error listing blobs in container {container_name}", e)
            listing_failed = e

        if batch_records:
            await self._save(batch_records)

        # Blobs are listed by name, not time, so a checkpoint after a partial
        # listing would skip the older blobs it never reached.
        if failed.count:
            self._sync_incomplete = True
            self._failed_blob_count += failed.count
            self.logger.warning(
                f"{failed.count} blobs in container {container_name} failed to process; "
                "the next sync retries them"
            )
        if listing_failed:
            reason = azure_error_text(listing_failed) if isinstance(listing_failed, AzureError) else listing_failed
            raise ConnectionError(f"Listing blobs in {container_name} failed: {reason}") from listing_failed
        checkpoint = listing_checkpoint(failed.checkpoint(max_timestamp), listing_started_at)
        if checkpoint and checkpoint > 0:
            await self.record_sync_point.update_sync_point(
                sync_point_key, {
                    "last_sync_time": checkpoint,
                }
            )
        return listed, not unjudged

    def _blob_properties_to_dict(self, blob: "BlobProperties | dict[str, Any]") -> dict[str, Any]:
        """Convert Azure BlobProperties object to a dictionary.

        The Azure SDK returns BlobProperties objects from the async iterator.
        This method converts them to dictionaries for consistent handling.
        """
        # If it's already a dict, return as-is
        if isinstance(blob, dict):
            return blob

        # Extract content_settings properties safely
        content_settings = getattr(blob, "content_settings", None)
        content_type = None
        content_md5 = None
        if content_settings:
            content_type = getattr(content_settings, "content_type", None)
            content_md5 = getattr(content_settings, "content_md5", None)

        blob_tier = getattr(blob, "blob_tier", None)
        # Convert BlobProperties to dict
        return {
            "name": getattr(blob, "name", ""),
            "last_modified": getattr(blob, "last_modified", None),
            "creation_time": getattr(blob, "creation_time", None),
            "etag": getattr(blob, "etag", ""),
            "size": getattr(blob, "size", 0),
            "content_type": content_type,
            "content_md5": content_md5,
            "metadata": getattr(blob, "metadata", None) or {},
            # The listing gives the generated AccessTier enum, whose str() is "AccessTier.ARCHIVE".
            "blob_tier": getattr(blob_tier, "value", blob_tier),
            "blob_tier_change_time": getattr(blob, "blob_tier_change_time", None),
        }

    @staticmethod
    def _tier_changed_since(blob: dict, since_ms: int) -> bool:
        """Whether the blob's tier changed after ``since_ms``. Setting a tier, as rehydrating
        from Archive does, changes neither its Last-Modified nor its etag."""
        changed = blob.get("blob_tier_change_time")
        return isinstance(changed, datetime) and datetime_to_epoch_ms(changed) > since_ms

    @staticmethod
    def _is_folder_blob(blob: dict) -> bool:
        """A folder marker ('a/b/') or an ADLS Gen2 directory (metadata hdi_isfolder=true)."""
        metadata = blob.get("metadata") or {}
        is_dir = any(k.lower() == "hdi_isfolder" and str(v).lower() == "true" for k, v in metadata.items())
        return is_dir or blob.get("name", "").endswith("/")

    async def _ensure_parent_folders_exist(
        self, container_name: str, path_segments: list[str],
        recorded: Container[str] | None = None, blob: dict | None = None,
    ) -> None:
        """Ensure folder records exist for each path segment (root to leaf).

        Azure Blob Storage, like S3, represents folders implicitly via blob names.
        For each segment (e.g. 'a', 'a/b', 'a/b/c'), upsert a folder record and its edges.
        Always processes all segments so that edges are re-created after full sync.
        Process in order so parent exists before child. The processor handles existing
        records by external_record_id and re-creates edges without duplicating nodes.

        Azure keeps no folder times. A new folder is dated by ``blob``, the blob that implies
        it; a folder in ``recorded`` is sent without times, so the processor keeps the stored
        ones instead of moving them to every write.
        """
        if not path_segments:
            return
        last_modified = (blob or {}).get("last_modified")
        timestamp_ms = (
            datetime_to_epoch_ms(last_modified) if isinstance(last_modified, datetime) else get_epoch_timestamp_in_ms()
        )
        for i, segment in enumerate(path_segments):
            external_id = f"{container_name}/{segment}"
            if external_id in self._folders_written:
                continue
            # Root folder: first segment has no parent. Others: parent is previous segment.
            parent_external_id = (
                f"{container_name}/{path_segments[i - 1]}" if i > 0 else None
            )
            parent_record_type = RecordType.FILE if parent_external_id else None
            record_name = segment.split("/")[-1] if segment else segment
            web_url = self._generate_web_url(container_name, segment + "/")
            stored = recorded is not None and external_id in recorded
            folder_record = FileRecord(
                id=str(uuid.uuid4()),
                record_name=record_name,
                record_type=RecordType.FILE,
                record_group_type=RecordGroupType.BUCKET.value,
                external_record_group_id=container_name,
                external_record_id=external_id,
                external_revision_id=None,
                version=0,
                origin=OriginTypes.CONNECTOR.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                source_created_at=None if stored else timestamp_ms,
                source_updated_at=None if stored else timestamp_ms,
                weburl=web_url,
                signed_url=None,
                hide_weburl=True,
                is_internal=True,
                parent_external_record_id=parent_external_id,
                parent_record_type=parent_record_type,
                size_in_bytes=0,
                is_file=False,
                extension=None,
                path=segment,
                mime_type=MimeTypes.FOLDER.value,
                etag=None,
            )
            permissions = await self._create_azure_blob_permissions(container_name, segment + "/")
            await self._save([(folder_record, permissions)])
            self._folders_written.add(external_id)

    def _get_azure_blob_revision_id(self, blob: dict) -> str:
        """
        Determines a stable revision ID for an Azure Blob object.

        It prioritizes the content_md5 hash as a content fingerprint, which is stable
        across renames/copies. If not available, it falls back to the etag.

        Note: Unlike S3's ETag (which is content-based), Azure Blob's etag changes on
        every modification including metadata changes and renames. Therefore, content_md5
        must be used for reliable move/rename detection.

        Args:
            blob: Azure Blob metadata dictionary

        Returns:
            Revision ID string (content_md5 or etag)
        """
        content_md5 = blob.get("content_md5")
        if content_md5:
            if isinstance(content_md5, (bytes, bytearray)):
                return base64.b64encode(bytes(content_md5)).decode('utf-8')
            elif isinstance(content_md5, str):
                return content_md5

        # Fall back to etag if no MD5 available
        # Note: etag-based move detection won't work as Azure etag changes on copy
        etag = blob.get("etag")
        if etag:
            return etag.strip('"')

        return ""

    async def _process_azure_blob(
        self, blob: dict, container_name: str, recorded: Container[str] | None = None,
    ) -> tuple[FileRecord | None, list[Permission]]:
        """Process a single Azure blob and convert it to a FileRecord.

        A blob at a new name whose content matches a stored record is a move only
        when that record's blob is gone; otherwise it is a copy with its own record.
        ``recorded``: the container's record ids read before the listing; a blob not
        in it has no record under its name, so that lookup is skipped.
        """
        try:
            blob_name = blob.get("name", "")
            if not blob_name:
                return None, []

            is_folder = blob_name.endswith("/")
            is_file = not is_folder

            normalized_name = blob_name.lstrip("/")
            if not normalized_name:
                return None, []

            # Parse timestamps
            last_modified = blob.get("last_modified")
            if last_modified:
                if isinstance(last_modified, datetime):
                    timestamp_ms = int(last_modified.timestamp() * 1000)
                elif isinstance(last_modified, str):
                    try:
                        obj_dt = datetime.fromisoformat(last_modified.replace('Z', '+00:00'))
                        timestamp_ms = int(obj_dt.timestamp() * 1000)
                    except ValueError:
                        timestamp_ms = get_epoch_timestamp_in_ms()
                else:
                    timestamp_ms = get_epoch_timestamp_in_ms()
            else:
                timestamp_ms = get_epoch_timestamp_in_ms()

            # Parse created time
            creation_time = blob.get("creation_time")
            if creation_time:
                if isinstance(creation_time, datetime):
                    created_timestamp_ms = int(creation_time.timestamp() * 1000)
                elif isinstance(creation_time, str):
                    try:
                        created_dt = datetime.fromisoformat(creation_time.replace('Z', '+00:00'))
                        created_timestamp_ms = int(created_dt.timestamp() * 1000)
                    except ValueError:
                        created_timestamp_ms = timestamp_ms
                else:
                    created_timestamp_ms = timestamp_ms
            else:
                created_timestamp_ms = timestamp_ms

            external_record_id = f"{container_name}/{normalized_name}"

            # Use a stable "content fingerprint" first (similar to GCS Md5Hash/S3 ETag usage).
            # - `content_md5` changes when content changes and is stable across renames/copies when content is identical
            # - fall back to etag when no md5 is available (note: etag-based move detection won't work in Azure)
            current_revision_id = self._get_azure_blob_revision_id(blob)

            existing_record = (
                None if recorded is not None and external_record_id not in recorded
                else await self.data_entities_processor.get_record_by_external_id(self.connector_id, external_record_id)
            )


            if existing_record:
                stored_revision = existing_record.external_revision_id or ""

                # Content changed or missing revision - sync properly from Azure Blob
                if current_revision_id and stored_revision and current_revision_id != stored_revision:
                    self.logger.info(
                        f"Content change detected: {normalized_name} - externalRevisionId changed from {stored_revision} to {current_revision_id}"
                    )
                elif not current_revision_id or not stored_revision:
                    if not current_revision_id:
                        self.logger.warning(
                            f"Current revision missing for {normalized_name}, processing record"
                        )
                    if not stored_revision:
                        self.logger.debug(
                            f"Stored revision missing for {normalized_name}, processing record"
                        )
            elif current_revision_id:
                existing_record = await self.data_entities_processor.get_record_by_external_revision_id(
                    self.connector_id, current_revision_id
                )
                taken_by = None
                if existing_record:
                    async with self._move_claim_lock:
                        if existing_record.id in self._moved_record_ids or await self._still_in_container(existing_record):
                            existing_record, taken_by = None, existing_record.external_record_id
                        else:
                            self._moved_record_ids.add(existing_record.id)
                if taken_by:
                    self.logger.info(f"New document: {normalized_name} is a copy of {taken_by}, not a move")

                if existing_record:
                    self.logger.info(
                        f"Move/rename detected: {normalized_name} - file moved from {existing_record.external_record_id} to {external_record_id}"
                    )
                else:
                    self.logger.debug(f"New document: {normalized_name}")
            else:
                self.logger.debug(f"New document: {normalized_name} (no revision available)")

            # Prepare record data
            extension = get_file_extension(normalized_name) if is_file else None
            # A folder marker's own content type is not a folder mimeType.
            mime_type = (MimeTypes.FOLDER.value if is_folder
                         else blob.get("content_type") or get_mimetype_for_azure_blob(normalized_name))

            parent_path = get_parent_path_from_blob_name(normalized_name)
            parent_external_id = f"{container_name}/{parent_path}" if parent_path else None
            parent_record_type = RecordType.FILE if parent_path else None

            web_url = self._generate_web_url(container_name, normalized_name)

            record_id = existing_record.id if existing_record else str(uuid.uuid4())
            record_name = normalized_name.rstrip("/").split("/")[-1] or normalized_name.rstrip("/")


            version = 0 if not existing_record else existing_record.version + 1

            # Get content MD5 hash for md5_hash field
            content_md5 = blob.get("content_md5")
            if content_md5:
                if isinstance(content_md5, (bytes, bytearray)):
                    # Convert bytes/bytearray to base64 string
                    content_md5 = base64.b64encode(bytes(content_md5)).decode('utf-8')
                elif not isinstance(content_md5, str):
                    # If it's some other type, convert to string
                    content_md5 = str(content_md5)
                # If it's already a string, use it as-is

            # Get raw etag for the etag field (separate from revision ID)
            raw_etag = blob.get("etag", "").strip('"') if blob.get("etag") else ""

            file_record = FileRecord(
                id=record_id,
                record_name=record_name,
                record_type=RecordType.FILE,
                record_group_type=RecordGroupType.BUCKET.value,
                external_record_group_id=container_name,
                external_record_id=external_record_id,
                external_revision_id=current_revision_id,
                version=version,
                origin=OriginTypes.CONNECTOR.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                source_created_at=existing_record.source_created_at if existing_record else created_timestamp_ms,
                source_updated_at=timestamp_ms,
                weburl=web_url,
                signed_url=None,
                hide_weburl=True,
                is_internal=True if is_folder else False,
                parent_external_record_id=parent_external_id,
                parent_record_type=parent_record_type,
                size_in_bytes=blob.get("size", 0) if is_file else 0,
                is_file=is_file,
                extension=extension,
                path=normalized_name,
                mime_type=mime_type,
                md5_hash=content_md5,
                etag=raw_etag,
            )

            if (
                hasattr(self, 'indexing_filters')
                and self.indexing_filters
                and not self.indexing_filters.is_enabled(IndexingFilterKey.FILES, default=True)
            ) or str(blob.get("blob_tier") or "").lower() == "archive":
                # An Archive-tier blob cannot be read until it is rehydrated.
                file_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

            permissions = await self._create_azure_blob_permissions(container_name, blob_name)

            return file_record, permissions

        except Exception as e:
            self._log_failure(f"Error processing blob {blob.get('name')} in container {container_name}", e, blob=True)
            return None, []

    async def _paced(self, blobs: "AsyncItemPaged | AsyncIterator[dict]") -> AsyncIterator["BlobProperties | dict"]:
        """Blobs of an SDK listing, taking a rate-limit token for each page it requests."""
        if not isinstance(blobs, AsyncItemPaged):
            async for blob in blobs:
                yield blob
            return
        pages = blobs.by_page()
        while True:
            async with self.resilience.guard():
                try:
                    page = await pages.__anext__()
                except StopAsyncIteration:
                    return
            async for blob in page:
                yield blob

    async def _still_in_container(self, record: Record) -> bool:
        """Whether ``record``'s blob is still listed. A check that fails counts as yes:
        taking a copy for a move would lose the original's record."""
        container_name = record.external_record_group_id
        blob_name = path_in_container(container_name, record.external_record_id)
        if not container_name or not blob_name:
            return True
        try:
            async with self.resilience.guard():
                response = await self.data_source.list_blobs(container_name=container_name, prefix=blob_name)
                if not response.success or response.data is None:
                    self.logger.warning(
                        f"Could not check whether {container_name}/{blob_name} still exists: {response.error}"
                    )
                    return True
                # Names list in lexicographic order, so the name itself comes first among those it prefixes.
                async for blob in response.data:
                    return self._blob_properties_to_dict(blob).get("name") == blob_name
                return False
        except Exception as e:
            reason = azure_error_text(e) if isinstance(e, AzureError) else e
            self.logger.warning(f"Could not check whether {container_name}/{blob_name} still exists: {reason}")
            return True

    async def _create_azure_blob_permissions(
        self, container_name: str, blob_name: str
    ) -> list[Permission]:
        """Blobs inherit their parent folder or the container. No permission edges."""
        return []

    async def test_connection_and_access(self) -> bool:
        """Check what a sync needs: listing containers (unless the filter names them),
        listing blobs, and producing a download link. A problem raises ConnectorInitError
        naming it, which the user sees instead of a generic failure."""
        if not self.data_source:
            return False
        try:
            problem = await self._connection_problem()
        except Exception as e:
            problem = azure_error_text(e)
        if problem:
            self.logger.error(f"Azure Blob connection test failed: {problem}")
            await self._notify_error(NotificationType.CONNECTOR_AUTH_ERROR, "Connection test failed", problem)
            raise ConnectorInitError(f"{self.display_name}: {problem}")
        self.logger.info("Azure Blob connection test successful.")
        return True

    async def _connection_problem(self) -> str | None:
        """What the credentials cannot do that a sync needs, or None."""
        names = included_names(self.sync_filters, "containers") if self.sync_filters else []
        if not names:
            response = await self.data_source.list_containers()
            if not response.success:
                return f"Cannot list containers: {response.error}"
            names = self._extract_container_names(response.data)
        if not names:
            return None
        for container_name in names:
            response = await self.data_source.list_blobs(container_name=container_name, maxresults=1)
            if not response.success:
                return f"Cannot list blobs in {container_name}: {response.error}"
            try:
                async for _ in response.data.by_page():
                    break
            except Exception as e:
                # A deleted container is not a credential problem; the next sync removes it.
                if getattr(e, "error_code", None) in _CONTAINER_GONE_CODES:
                    continue
                return f"Cannot list blobs in {container_name}: {azure_error_text(e)}"
            break
        # Signing never fetches the blob, so any name will do.
        signed = await self.data_source.generate_blob_sas_url(
            container_name=container_name, blob_name="pipeshub-connection-test", permission="r",
            expiry=datetime.now(timezone.utc) + _SAS_LIFETIME,
        )
        if not signed.success:
            return f"Cannot create download links for files: {signed.error}"
        return None

    async def get_signed_url(self, record: Record) -> str | None:
        """A short-lived SAS URL for one blob, or None when the credentials can only reuse
        the connection string's own SAS (its lifetime and scope are the admin's)."""
        url, minted = await self._download_url(record)
        return url if minted else None

    async def _download_url(self, record: Record) -> tuple[str | None, bool]:
        """A URL that reads the blob, and whether it is a SAS minted here (15 minutes, this blob)."""
        if not self.data_source:
            raise connector_not_ready(self.display_name)
        try:
            container_name = record.external_record_group_id
            if not container_name:
                self.logger.warning(f"No container name found for record: {record.id}")
                raise not_downloadable(
                    "This item is missing the container it belongs to and cannot be "
                    "downloaded.",
                    connector=self.display_name,
                )

            external_record_id = record.external_record_id
            if not external_record_id:
                self.logger.warning(f"No external_record_id found for record: {record.id}")
                raise not_downloadable(
                    "This item is missing its blob name and cannot be downloaded.",
                    connector=self.display_name,
                )

            # Sync stores the raw blob name; '%' in it is part of the name.
            if external_record_id.startswith(f"{container_name}/"):
                blob_name = external_record_id[len(f"{container_name}/"):]
            else:
                blob_name = external_record_id.lstrip("/")

            self.logger.debug(
                f"Generating SAS URL - Container: {container_name}, "
                f"Blob: {blob_name}, Record ID: {record.id}"
            )

            response = await self.data_source.generate_blob_sas_url(
                container_name=container_name,
                blob_name=blob_name,
                permission="r",  # Read permission
                expiry=datetime.now(timezone.utc) + _SAS_LIFETIME,
                # Azurite and private endpoints may serve plain HTTP.
                protocol="https" if self.account_url.startswith("https://") else None,
            )

            if response.success and response.data:
                return response.data.get("sas_url"), response.data.get("expiry") is not None

            # A failure here means the credential can't sign (no account key, no
            # Entra ID delegation, no SAS) — the connector is misconfigured, the blob is fine.
            self.logger.error(
                f"Failed to generate SAS URL: {response.error} | "
                f"Container: {container_name} | Blob: {blob_name}"
            )
            raise connector_not_ready(self.display_name)
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(
                f"Error generating SAS URL for record {record.id}: {e}", exc_info=True
            )
            raise to_stream_error(e, connector=self.display_name) from e

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Stream Azure blob content."""
        if isinstance(record, FileRecord) and not record.is_file:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Cannot stream folder content",
            )

        # Streamed server-side, so a reused connection-string SAS never leaves PipesHub.
        signed_url, _ = await self._download_url(record)
        if not signed_url:
            raise not_found_at_source(self.display_name)

        return create_stream_record_response(
            stream_content(
                signed_url,
                record_id=record.id,
                file_name=record.record_name,
                connector=self.display_name,
            ),
            filename=record.record_name,
            mime_type=record.mime_type if record.mime_type else "application/octet-stream",
            fallback_filename=f"record_{record.id}"
        )

    async def cleanup(self) -> None:
        """Clean up resources used by the connector."""
        self.logger.info("Cleaning up Azure Blob connector resources.")
        if self.data_source:
            await self.data_source.close_async_client()
        self.data_source = None

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: str | None = None,
        cursor: str | None = None
    ) -> FilterOptionsResponse:
        """Get dynamic filter options for filters."""
        if filter_key == "containers":
            return await self._get_container_options(page, limit, search, cursor)
        else:
            raise ValueError(f"Unsupported filter key: {filter_key}")

    async def _get_container_options(
        self,
        page: int,
        limit: int,
        search: str | None,
        cursor: str | None = None,
    ) -> FilterOptionsResponse:
        """One page of containers, filtered by Azure (name starts with ``search``) and paged with its marker."""
        def failure(message: str) -> FilterOptionsResponse:
            return FilterOptionsResponse(
                success=False, options=[], page=page, limit=limit, has_more=False, message=message
            )

        try:
            if not self.data_source:
                return failure("Azure Blob connector is not initialized")
            response = await self.data_source.list_containers_page(
                prefix=search or None, results_per_page=limit, continuation_token=cursor or None
            )
            if not response.success:
                return failure(f"Failed to list containers: {response.error}")
            next_cursor = response.data.get("continuation_token")
            return FilterOptionsResponse(
                success=True,
                options=[FilterOption(id=name, label=name) for name in response.data.get("names", [])],
                page=page,
                limit=limit,
                has_more=bool(next_cursor),
                cursor=next_cursor,
            )
        except Exception as e:
            self.logger.error(f"Error getting container options: {e}", exc_info=True)
            return failure(f"Error: {str(e)}")

    def handle_webhook_notification(self, notification: dict) -> None:
        """Handle webhook notifications from the source."""
        raise NotImplementedError("This method is not supported")

    async def reindex_records(self, record_results: list[Record]) -> None:
        """Reindex records by checking for updates at source and publishing reindex events."""
        try:
            if not record_results:
                self.logger.info("No records to reindex")
                return

            self.logger.info(f"Starting reindex for {len(record_results)} Azure Blob records")

            if not self.data_source:
                self.logger.error("Azure Blob connector is not initialized.")
                raise Exception("Azure Blob connector is not initialized.")

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
                except _NotReindexable as e:
                    self.logger.warning(f"Not reindexing record {record.id}: {e}")
                    continue
                except Exception as e:
                    self.logger.error(f"Error checking record {record.id} at source: {e}", exc_info=True)
                    continue

            if updated_records:
                await self.data_entities_processor.on_new_records(updated_records)
                self.logger.info(f"Updated {len(updated_records)} records in DB")

            if non_updated_records:
                await self.data_entities_processor.reindex_existing_records(non_updated_records)
                self.logger.info(f"Published reindex events for {len(non_updated_records)} records")

        except Exception as e:
            self.logger.error(f"Error during Azure Blob reindex: {e}", exc_info=True)
            raise

    async def _check_and_fetch_updated_record(
        self, org_id: str, record: Record
    ) -> tuple[Record, list[Permission]] | None:
        """Check if record has been updated at source and fetch updated data."""
        if isinstance(record, FileRecord) and not record.is_file:
            # Folders are implied by blob paths; an ADLS directory blob is not a file to refetch.
            return None
        try:
            container_name = record.external_record_group_id
            external_record_id = record.external_record_id

            if not container_name or not external_record_id:
                self.logger.warning(f"Missing container or external_record_id for record {record.id}")
                return None

            if external_record_id.startswith(f"{container_name}/"):
                blob_name = external_record_id[len(f"{container_name}/"):]
            else:
                blob_name = external_record_id.lstrip("/")

            if not blob_name:
                self.logger.warning(f"Invalid blob name for record {record.id}")
                return None

            response = await self.data_source.get_blob_properties(
                container_name=container_name,
                blob_name=blob_name
            )

            if not response.success:
                # Raised, so reindex_records publishes nothing for a blob that is gone or unreadable.
                raise _NotReindexable(f"Blob {blob_name} in container {container_name} cannot be read: {response.error}")

            if not response.data:
                return None
            blob_metadata = self._blob_properties_to_dict(response.data)
            if str(blob_metadata.get("blob_tier") or "").lower() == "archive":
                # Raised like a missing blob: indexing could not read it until it is rehydrated.
                raise _NotReindexable(f"Blob {blob_name} in container {container_name} is in the Archive tier")

            # The same revision sync stores (Content-MD5, else etag).
            current_revision = self._get_azure_blob_revision_id(blob_metadata)
            current_etag = (blob_metadata.get("etag") or "").strip('"')
            if current_revision == record.external_revision_id:
                self.logger.debug(f"Record {record.id}: revision unchanged")
                return None

            self.logger.debug(f"Record {record.id}: revision changed")

            # Parse timestamps
            last_modified = blob_metadata.get("last_modified")
            if last_modified:
                if isinstance(last_modified, datetime):
                    timestamp_ms = int(last_modified.timestamp() * 1000)
                elif isinstance(last_modified, str):
                    try:
                        obj_dt = datetime.fromisoformat(last_modified.replace('Z', '+00:00'))
                        timestamp_ms = int(obj_dt.timestamp() * 1000)
                    except ValueError:
                        timestamp_ms = get_epoch_timestamp_in_ms()
                else:
                    timestamp_ms = get_epoch_timestamp_in_ms()
            else:
                timestamp_ms = get_epoch_timestamp_in_ms()

            is_folder = blob_name.endswith("/")
            is_file = not is_folder

            extension = get_file_extension(blob_name) if is_file else None
            # A folder marker's own content type is not a folder mimeType.
            mime_type = (MimeTypes.FOLDER.value if is_folder
                         else blob_metadata.get("content_type") or get_mimetype_for_azure_blob(blob_name))

            parent_path = get_parent_path_from_blob_name(blob_name)
            parent_external_id = f"{container_name}/{parent_path}" if parent_path else None
            parent_record_type = RecordType.FILE if parent_path else None

            web_url = self._generate_web_url(container_name, blob_name)


            record_name = blob_name.rstrip("/").split("/")[-1] or blob_name.rstrip("/")

            updated_external_record_id = f"{container_name}/{blob_name}"

            # Get content MD5 hash
            content_md5 = blob_metadata.get("content_md5")
            if content_md5:
                if isinstance(content_md5, (bytes, bytearray)):
                    # Convert bytes/bytearray to base64 string
                    content_md5 = base64.b64encode(bytes(content_md5)).decode('utf-8')
                elif not isinstance(content_md5, str):
                    # If it's some other type, convert to string
                    content_md5 = str(content_md5)
                # If it's already a string, use it as-is

            updated_record = FileRecord(
                id=record.id,
                record_name=record_name,
                record_type=RecordType.FILE,
                record_group_type=RecordGroupType.BUCKET.value,
                external_record_group_id=container_name,
                external_record_id=updated_external_record_id,
                external_revision_id=current_revision,
                version=record.version + 1,
                origin=OriginTypes.CONNECTOR.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                source_created_at=record.source_created_at,
                source_updated_at=timestamp_ms,
                weburl=web_url,
                signed_url=None,
                hide_weburl=True,
                is_internal=True if is_folder else False,
                parent_external_record_id=parent_external_id,
                parent_record_type=parent_record_type,
                size_in_bytes=blob_metadata.get("size", 0) if is_file else 0,
                is_file=is_file,
                extension=extension,
                path=blob_name,
                mime_type=mime_type,
                md5_hash=content_md5,
                etag=current_etag,
            )

            if (
                hasattr(self, 'indexing_filters')
                and self.indexing_filters
                and not self.indexing_filters.is_enabled(IndexingFilterKey.FILES, default=True)
            ):
                updated_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

            permissions = await self._create_azure_blob_permissions(container_name, blob_name)

            return updated_record, permissions

        except _NotReindexable:
            raise
        except Exception as e:
            self.logger.error(f"Error checking record {record.id} at source: {e}", exc_info=True)
            return None

    async def run_incremental_sync(self) -> None:
        """Every sync is incremental per prefix (each keeps its own last sync time)."""
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
    ) -> "AzureBlobConnector":
        """Factory method to create and initialize connector."""
        org_id = kwargs.pop("org_id", None)
        # init() sets the account endpoint from the credentials.
        data_entities_processor = AzureBlobDataSourceEntitiesProcessor(
            logger, data_store_provider, config_service
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

