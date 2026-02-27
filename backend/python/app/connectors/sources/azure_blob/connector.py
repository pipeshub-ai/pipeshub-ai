"""
Azure Blob Storage Connector

Connector for synchronizing data from Azure Blob Storage containers. This connector
uses the native Azure Blob Storage API with connection string authentication.
"""

import base64
import mimetypes
import uuid
from datetime import datetime, timedelta, timezone
from logging import Logger
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

from aiolimiter import AsyncLimiter
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    Connectors,
    MimeTypes,
    OriginTypes,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import (
    DataStoreProvider,
    TransactionStore,
)
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
    generate_record_sync_point_key,
)
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
    load_connector_filters,
)
from app.connectors.sources.azure_blob.common.apps import AzureBlobApp
from app.models.entities import (
    AppUser,
    FileRecord,
    IndexingStatus,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    User,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.azure.azure_blob import AzureBlobClient
from app.sources.external.azure.azure_blob import AzureBlobDataSource
from app.utils.streaming import create_stream_record_response, stream_content
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# Default connector endpoint for signed URL generation
DEFAULT_CONNECTOR_ENDPOINT = "http://localhost:8000"

# Base URL for Azure Portal
AZURE_PORTAL_BASE_URL = "https://portal.azure.com"


def get_file_extension(blob_name: str) -> Optional[str]:
    """Extracts the extension from a blob name."""
    if "." in blob_name:
        parts = blob_name.split(".")
        if len(parts) > 1:
            return parts[-1].lower()
    return None


def get_parent_path_from_blob_name(blob_name: str) -> Optional[str]:
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


def get_folder_path_segments_from_blob_name(blob_name: str) -> List[str]:
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
    segments = []
    for i in range(1, len(parts)):
        segments.append("/".join(parts[:i]))
    return segments


def get_mimetype_for_azure_blob(blob_name: str, is_folder: bool = False) -> str:
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


def parse_parent_external_id(parent_external_id: str) -> Tuple[str, Optional[str]]:
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


def get_parent_weburl_for_azure_blob(parent_external_id: str, account_name: str) -> str:
    """Generate webUrl for an Azure Blob directory based on parent external_id.

    Args:
        parent_external_id: External ID in format "container_name/path" or just "container_name"
        account_name: Azure storage account name

    Returns:
        Azure Portal URL for the directory
    """
    container_name, path = parse_parent_external_id(parent_external_id)
    # Azure Portal URL format for blob containers
    base_url = f"https://{account_name}.blob.core.windows.net/{container_name}"
    if path:
        return f"{base_url}/{path}"
    return base_url


def get_parent_path_for_azure_blob(parent_external_id: str) -> Optional[str]:
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


class AzureBlobDataSourceEntitiesProcessor(DataSourceEntitiesProcessor):
    """Azure Blob processor that extends the base processor with Azure-specific placeholder record logic."""

    def __init__(
        self,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        account_name: str = "",
    ) -> None:
        super().__init__(logger, data_store_provider, config_service)
        self.account_name = account_name

    def _create_placeholder_parent_record(
        self,
        parent_external_id: str,
        parent_record_type: RecordType,
        record: Record,
    ) -> Record:
        """
        Create a placeholder parent record with Azure-specific weburl and path.
        """
        parent_record = super()._create_placeholder_parent_record(
            parent_external_id, parent_record_type, record
        )

        if parent_record_type == RecordType.FILE and isinstance(parent_record, FileRecord):
            weburl = get_parent_weburl_for_azure_blob(parent_external_id, self.account_name)
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
    .with_auth([
        AuthBuilder.type(AuthType.CONNECTION_STRING).fields([
            AuthField(
                name="azureBlobConnectionString",
                display_name="Connection String",
                placeholder="DefaultEndpointsProtocol=https;AccountName=...",
                description="The Azure Blob Storage connection string from Azure Portal",
                field_type="PASSWORD",
                max_length=2000,
                is_secret=True
            ),
        ])
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/azureblob.svg")
        .add_documentation_link(DocumentationLink(
            "Azure Blob Storage Setup",
            "https://learn.microsoft.com/en-us/azure/storage/blobs/storage-quickstart-blobs-portal",
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
            description="Select specific Azure Blob containers to sync",
            option_source_type=OptionSourceType.DYNAMIC,
            default_value=[],
            default_operator=MultiselectOperator.IN.value
        ))
        .add_filter_field(CommonFields.file_extension_filter())
        .add_filter_field(CommonFields.modified_date_filter("Filter files and folders by modification date."))
        .add_filter_field(CommonFields.created_date_filter("Filter files and folders by creation date."))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
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
    ) -> None:
        super().__init__(
            app=AzureBlobApp(connector_id),
            logger=logger,
            data_entities_processor=data_entities_processor,
            data_store_provider=data_store_provider,
            config_service=config_service,
            connector_id=connector_id,
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

        self.data_source: Optional[AzureBlobDataSource] = None
        self.batch_size = 100
        self.rate_limiter = AsyncLimiter(50, 1)  # 50 requests per second
        self.container_name: Optional[str] = None
        self.connector_scope: Optional[str] = None
        self.created_by: Optional[str] = None
        self.creator_email: Optional[str] = None  # Cached to avoid repeated DB queries
        self.account_name: Optional[str] = None

        # Initialize filter collections
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

    def get_app_users(self, users: List[User]) -> List[AppUser]:
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
            return False

        auth_config = config.get("auth", {})
        connection_string = auth_config.get("azureBlobConnectionString")

        if not connection_string:
            self.logger.error("Azure Blob connection string not found in configuration.")
            return False

        # Container name is no longer stored in config - it's determined at sync time
        self.container_name = None

        # Get connector scope
        self.connector_scope = ConnectorScope.PERSONAL.value
        self.created_by = config.get("created_by")

        scope_from_config = config.get("scope")
        if scope_from_config:
            self.connector_scope = scope_from_config

        # Fetch creator email once to avoid repeated DB queries during sync
        if self.created_by and self.connector_scope != ConnectorScope.TEAM.value:
            try:
                async with self.data_store_provider.transaction() as tx_store:
                    user = await tx_store.get_user_by_id(self.created_by)
                    if user and user.get("email"):
                        self.creator_email = user.get("email")
            except Exception as e:
                self.logger.warning(f"Could not get user for created_by {self.created_by}: {e}")

        try:
            client = await AzureBlobClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id,
            )
            self.data_source = AzureBlobDataSource(client)

            # Extract account name from the client (derived from connection string)
            try:
                self.account_name = client.get_account_name()
            except Exception as e:
                self.logger.warning(f"Could not extract account name from connection string: {e}")
                self.account_name = None

            # Update the entities processor with the account name
            if isinstance(self.data_entities_processor, AzureBlobDataSourceEntitiesProcessor):
                self.data_entities_processor.account_name = self.account_name or ""

            # Load connector filters
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, self.filter_key, self.connector_id, self.logger
            )

            self.logger.info("Azure Blob client initialized successfully.")
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize Azure Blob client: {e}", exc_info=True)
            return False

    def _generate_web_url(self, container_name: str, blob_name: str) -> str:
        """Generate the web URL for an Azure blob."""
        # Azure Blob Storage URL format
        return f"https://{self.account_name}.blob.core.windows.net/{container_name}/{quote(blob_name)}"

    def _generate_parent_web_url(self, parent_external_id: str) -> str:
        """Generate the web URL for an Azure Blob parent folder/directory."""
        return get_parent_weburl_for_azure_blob(parent_external_id, self.account_name or "")

    def _extract_container_names(self, containers_data: Optional[Iterable[Any]]) -> List[str]:
        """Extract container names from list_containers response data.

        Handles both dict-based and ContainerProperties-like objects.
        """
        container_names: List[str] = []
        if not containers_data:
            return container_names

        for container in containers_data:
            container_name: Optional[str] = None

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

            # Reload sync and indexing filters to pick up configuration changes
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, self.filter_key, self.connector_id, self.logger
            )

            all_active_users = await self.data_entities_processor.get_all_active_users()
            app_users = self.get_app_users(all_active_users)
            await self.data_entities_processor.on_new_app_users(app_users)

            # Get sync filters
            sync_filters = self.sync_filters if hasattr(self, 'sync_filters') and self.sync_filters else FilterCollection()

            # Get container filter if specified
            container_filter = sync_filters.get("containers")
            selected_containers = container_filter.value if container_filter and container_filter.value else []

            # List all containers or use configured container
            containers_to_sync = []
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
                    return

                containers_data = containers_response.data
                if containers_data:
                    containers_to_sync = self._extract_container_names(containers_data)

                    if containers_to_sync:
                        self.logger.info(f"Found {len(containers_to_sync)} container(s) to sync: {containers_to_sync}")
                    else:
                        self.logger.warning("No valid container names found in response")
                        return
                else:
                    self.logger.warning("No containers found")
                    return

            # Create record groups for containers first
            await self._create_record_groups_for_containers(containers_to_sync)

            # Sync each container
            for container_name in containers_to_sync:
                if not container_name:
                    continue
                try:
                    self.logger.info(f"Syncing container: {container_name}")
                    await self._sync_container(container_name)
                except Exception as e:
                    self.logger.error(
                        f"Error syncing container {container_name}: {e}", exc_info=True
                    )
                    continue

            self.logger.info("Azure Blob full sync completed.")
        except Exception as ex:
            self.logger.error(f"Error in Azure Blob connector run: {ex}", exc_info=True)
            raise

    async def _create_record_groups_for_containers(self, container_names: List[str]) -> None:
        """Create record groups for containers with appropriate permissions.

        Uses cached creator_email from init() to avoid repeated database queries.
        """
        if not container_names:
            return

        record_groups = []
        for container_name in container_names:
            if not container_name:
                continue

            permissions = []
            if self.connector_scope == ConnectorScope.TEAM.value:
                permissions.append(
                    Permission(
                        type=PermissionType.READ,
                        entity_type=EntityType.ORG,
                        external_id=self.data_entities_processor.org_id
                    )
                )
            else:
                # Use cached creator_email from init() instead of querying DB
                if self.creator_email:
                    permissions.append(
                        Permission(
                            type=PermissionType.OWNER,
                            entity_type=EntityType.USER,
                            email=self.creator_email,
                            external_id=self.created_by
                        )
                    )

                if not permissions:
                    permissions.append(
                        Permission(
                            type=PermissionType.READ,
                            entity_type=EntityType.ORG,
                            external_id=self.data_entities_processor.org_id
                        )
                    )

            record_group = RecordGroup(
                name=container_name,
                external_group_id=container_name,
                group_type=RecordGroupType.BUCKET,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                description=f"Azure Blob Container: {container_name}",
            )
            record_groups.append((record_group, permissions))

        if record_groups:
            await self.data_entities_processor.on_new_record_groups(record_groups)
            self.logger.info(f"Created {len(record_groups)} record group(s) for containers")

    def _get_date_filters(self) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[int]]:
        """Extract date filter values from sync_filters."""
        modified_after_ms: Optional[int] = None
        modified_before_ms: Optional[int] = None
        created_after_ms: Optional[int] = None
        created_before_ms: Optional[int] = None

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
        blob: Dict,
        modified_after_ms: Optional[int] = None,
        modified_before_ms: Optional[int] = None,
        created_after_ms: Optional[int] = None,
        created_before_ms: Optional[int] = None
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

    def _pass_extension_filter(self, blob_name: str, is_folder: bool = False) -> bool:
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
        """Sync blobs from a specific container with incremental sync support.

        The Azure SDK's list_blobs method returns an AsyncItemPaged object which is an
        async iterator that handles pagination internally. We iterate directly over it
        rather than using manual pagination.
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

        modified_after_ms, modified_before_ms, created_after_ms, created_before_ms = self._get_date_filters()

        sync_point_key = generate_record_sync_point_key(
            RecordType.FILE.value, "container", container_name
        )
        sync_point = await self.record_sync_point.read_sync_point(sync_point_key)
        last_sync_time = sync_point.get("last_sync_time") if sync_point else None

        if last_sync_time:
            user_modified_after_ms = modified_after_ms
            if user_modified_after_ms:
                modified_after_ms = max(user_modified_after_ms, last_sync_time)
            else:
                modified_after_ms = last_sync_time

        batch_records = []
        max_timestamp = last_sync_time if last_sync_time else 0
        blob_count = 0

        try:
            async with self.rate_limiter:
                response = await self.data_source.list_blobs(
                    container_name=container_name,
                )

                if not response.success:
                    error_msg = response.error or "Unknown error"
                    self.logger.error(
                        f"Failed to list blobs in container {container_name}: {error_msg}"
                    )
                    return

                blobs_iterator = response.data
                if blobs_iterator is None:
                    self.logger.info(f"No blobs found in container {container_name}")
                    return

                # Azure SDK returns an AsyncItemPaged object which handles pagination internally.
                # We iterate directly over it using async for.
                async for blob in blobs_iterator:
                    try:
                        blob_count += 1
                        # Convert BlobProperties to dict for consistent handling
                        blob_dict = self._blob_properties_to_dict(blob)
                        blob_name = blob_dict.get("name", "")
                        if not blob_name:
                            continue

                        is_folder = blob_name.endswith("/")

                        # Check extension filter
                        if not self._pass_extension_filter(blob_name, is_folder):
                            self.logger.debug(
                                f"Skipping {blob_name}: does not pass extension filter"
                            )
                            continue

                        if not self._pass_date_filters(
                            blob_dict, modified_after_ms, modified_before_ms, created_after_ms, created_before_ms
                        ):
                            continue

                        # Track max timestamp for incremental sync
                        if not is_folder:
                            last_modified = blob_dict.get("last_modified")
                            if last_modified:
                                if isinstance(last_modified, datetime):
                                    obj_timestamp_ms = int(last_modified.timestamp() * 1000)
                                    max_timestamp = max(max_timestamp, obj_timestamp_ms)
                                elif isinstance(last_modified, str):
                                    try:
                                        obj_dt = datetime.fromisoformat(last_modified.replace('Z', '+00:00'))
                                        obj_timestamp_ms = int(obj_dt.timestamp() * 1000)
                                        max_timestamp = max(max_timestamp, obj_timestamp_ms)
                                    except ValueError:
                                        pass

                        # Ensure folder hierarchy exists from blob path (Azure Blob has no folder objects)
                        if not is_folder:
                            path_segments = get_folder_path_segments_from_blob_name(blob_name)
                            if path_segments:
                                await self._ensure_parent_folders_exist(container_name, path_segments)

                        record, permissions = await self._process_azure_blob(
                            blob_dict, container_name
                        )
                        if record:
                            batch_records.append((record, permissions))

                            if len(batch_records) >= self.batch_size:
                                await self.data_entities_processor.on_new_records(
                                    batch_records
                                )
                                batch_records = []
                    except Exception as e:
                        error_blob_name = blob_dict.get("name", "unknown") if "blob_dict" in locals() else "unknown"
                        self.logger.error(
                            f"Error processing blob {error_blob_name}: {e}",
                            exc_info=True,
                        )
                        continue

            self.logger.info(f"Processed {blob_count} blobs from container {container_name}")

        except Exception as e:
            self.logger.error(
                f"Error during container sync for {container_name}: {e}", exc_info=True
            )

        if batch_records:
            await self.data_entities_processor.on_new_records(batch_records)

        if max_timestamp > 0:
            await self.record_sync_point.update_sync_point(
                sync_point_key, {
                    "last_sync_time": max_timestamp,
                }
            )

    def _blob_properties_to_dict(self, blob) -> Dict:
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

        # Convert BlobProperties to dict
        return {
            "name": getattr(blob, "name", ""),
            "last_modified": getattr(blob, "last_modified", None),
            "creation_time": getattr(blob, "creation_time", None),
            "etag": getattr(blob, "etag", ""),
            "size": getattr(blob, "size", 0),
            "content_type": content_type,
            "content_md5": content_md5,
        }

    async def _remove_old_parent_relationship(
        self, record_id: str, tx_store: "TransactionStore"
    ) -> None:
        """Remove old PARENT_CHILD relationships for a record."""
        try:
            deleted_count = await tx_store.delete_parent_child_edge_to_record(record_id)
            if deleted_count > 0:
                self.logger.info(f"Removed {deleted_count} old parent relationship(s) for record {record_id}")
        except Exception as e:
            self.logger.warning(f"Error in _remove_old_parent_relationship: {e}")

    async def _ensure_parent_folders_exist(
        self, container_name: str, path_segments: List[str]
    ) -> None:
        """Ensure folder records exist for each path segment (root to leaf).

        Azure Blob Storage, like S3, represents folders implicitly via blob names.
        For each segment (e.g. 'a', 'a/b', 'a/b/c'), upsert a folder record and its edges.
        Always processes all segments so that edges are re-created after full sync.
        Process in order so parent exists before child. The processor handles existing
        records by external_record_id and re-creates edges without duplicating nodes.
        """
        if not path_segments:
            return
        timestamp_ms = get_epoch_timestamp_in_ms()
        for i, segment in enumerate(path_segments):
            external_id = f"{container_name}/{segment}"
            # Root folder: first segment has no parent. Others: parent is previous segment.
            parent_external_id = (
                f"{container_name}/{path_segments[i - 1]}" if i > 0 else None
            )
            parent_record_type = RecordType.FILE if parent_external_id else None
            record_name = segment.split("/")[-1] if segment else segment
            web_url = self._generate_web_url(container_name, segment + "/")
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
                source_created_at=timestamp_ms,
                source_updated_at=timestamp_ms,
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
            await self.data_entities_processor.on_new_records([(folder_record, permissions)])

    def _get_azure_blob_revision_id(self, blob: Dict) -> str:
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
        self, blob: Dict, container_name: str
    ) -> Tuple[Optional[FileRecord], List[Permission]]:
        """Process a single Azure blob and convert it to a FileRecord."""
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

            # PRIMARY: Try lookup by path (externalRecordId)
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id, external_id=external_record_id
                )

            is_move = False

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
                # Not found by path - FALLBACK: try revision-based lookup (for move/rename detection)
                async with self.data_store_provider.transaction() as tx_store:
                    existing_record = await tx_store.get_record_by_external_revision_id(
                        connector_id=self.connector_id, external_revision_id=current_revision_id
                    )

                if existing_record:
                    is_move = True
                    self.logger.info(
                        f"Move/rename detected: {normalized_name} - file moved from {existing_record.external_record_id} to {external_record_id}"
                    )
                else:
                    self.logger.debug(f"New document: {normalized_name}")
            else:
                self.logger.debug(f"New document: {normalized_name} (no revision available)")

            # Prepare record data
            record_type = RecordType.FOLDER if is_folder else RecordType.FILE
            extension = get_file_extension(normalized_name) if is_file else None
            mime_type = blob.get("content_type") or get_mimetype_for_azure_blob(normalized_name, is_folder)

            parent_path = get_parent_path_from_blob_name(normalized_name)
            parent_external_id = f"{container_name}/{parent_path}" if parent_path else None
            parent_record_type = RecordType.FILE if parent_path else None

            web_url = self._generate_web_url(container_name, normalized_name)

            record_id = existing_record.id if existing_record else str(uuid.uuid4())
            record_name = normalized_name.rstrip("/").split("/")[-1] or normalized_name.rstrip("/")

            # For moves/renames, remove old parent relationship
            if is_move and existing_record:
                async with self.data_store_provider.transaction() as tx_store:
                    await self._remove_old_parent_relationship(record_id, tx_store)

            if not existing_record:
                version = 0
            else:
                version = existing_record.version + 1

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
                record_type=record_type,
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

            if hasattr(self, 'indexing_filters') and self.indexing_filters:
                if not self.indexing_filters.is_enabled(IndexingFilterKey.FILES, default=True):
                    file_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

            permissions = await self._create_azure_blob_permissions(container_name, blob_name)

            return file_record, permissions

        except Exception as e:
            self.logger.error(f"Error processing Azure blob: {e}", exc_info=True)
            return None, []

    async def _create_azure_blob_permissions(
        self, container_name: str, blob_name: str
    ) -> List[Permission]:
        """Create permissions for an Azure blob based on connector scope.

        Uses cached creator_email from init() to avoid repeated database queries.
        """
        try:
            permissions = []

            if self.connector_scope == ConnectorScope.TEAM.value:
                permissions.append(
                    Permission(
                        type=PermissionType.READ,
                        entity_type=EntityType.ORG,
                        external_id=self.data_entities_processor.org_id
                    )
                )
            else:
                # Use cached creator_email instead of querying DB for each blob
                if self.creator_email:
                    permissions.append(
                        Permission(
                            type=PermissionType.OWNER,
                            entity_type=EntityType.USER,
                            email=self.creator_email,
                            external_id=self.created_by
                        )
                    )

                if not permissions:
                    permissions.append(
                        Permission(
                            type=PermissionType.READ,
                            entity_type=EntityType.ORG,
                            external_id=self.data_entities_processor.org_id
                        )
                    )

            return permissions
        except Exception as e:
            self.logger.warning(f"Error creating permissions for {blob_name}: {e}")
            return [
                Permission(
                    type=PermissionType.READ,
                    entity_type=EntityType.ORG,
                    external_id=self.data_entities_processor.org_id
                )
            ]

    async def test_connection_and_access(self) -> bool:
        """Test connection and access."""
        if not self.data_source:
            return False
        try:
            response = await self.data_source.list_containers()
            if response.success:
                self.logger.info("Azure Blob connection test successful.")
                return True
            else:
                self.logger.error(f"Azure Blob connection test failed: {response.error}")
                return False
        except Exception as e:
            self.logger.error(f"Azure Blob connection test failed: {e}", exc_info=True)
            return False

    async def get_signed_url(self, record: Record) -> Optional[str]:
        """Generate a SAS URL for an Azure blob."""
        if not self.data_source:
            return None
        try:
            container_name = record.external_record_group_id
            if not container_name:
                self.logger.warning(f"No container name found for record: {record.id}")
                return None

            external_record_id = record.external_record_id
            if not external_record_id:
                self.logger.warning(f"No external_record_id found for record: {record.id}")
                return None

            if external_record_id.startswith(f"{container_name}/"):
                blob_name = external_record_id[len(f"{container_name}/"):]
            else:
                blob_name = external_record_id.lstrip("/")

            from urllib.parse import unquote
            blob_name = unquote(blob_name)

            self.logger.debug(
                f"Generating SAS URL - Container: {container_name}, "
                f"Blob: {blob_name}, Record ID: {record.id}"
            )

            # Generate SAS URL with 24 hour expiry
            expiry = datetime.now(timezone.utc) + timedelta(hours=24)
            response = await self.data_source.generate_blob_sas_url(
                container_name=container_name,
                blob_name=blob_name,
                permission="r",  # Read permission
                expiry=expiry,
            )

            if response.success and response.data:
                return response.data.get("sas_url")
            else:
                self.logger.error(
                    f"Failed to generate SAS URL: {response.error} | "
                    f"Container: {container_name} | Blob: {blob_name}"
                )
                return None
        except Exception as e:
            self.logger.error(
                f"Error generating SAS URL for record {record.id}: {e}"
            )
            return None

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Stream Azure blob content."""
        if isinstance(record, FileRecord) and not record.is_file:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Cannot stream folder content",
            )

        signed_url = await self.get_signed_url(record)
        if not signed_url:
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail="File not found or access denied",
            )

        return create_stream_record_response(
            stream_content(signed_url, record_id=record.id, file_name=record.record_name),
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
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> FilterOptionsResponse:
        """Get dynamic filter options for filters."""
        if filter_key == "containers":
            return await self._get_container_options(page, limit, search)
        else:
            raise ValueError(f"Unsupported filter key: {filter_key}")

    async def _get_container_options(
        self,
        page: int,
        limit: int,
        search: Optional[str]
    ) -> FilterOptionsResponse:
        """Get list of available containers."""
        try:
            if not self.data_source:
                return FilterOptionsResponse(
                    success=False,
                    options=[],
                    page=page,
                    limit=limit,
                    has_more=False,
                    message="Azure Blob connector is not initialized"
                )

            response = await self.data_source.list_containers()
            if not response.success:
                return FilterOptionsResponse(
                    success=False,
                    options=[],
                    page=page,
                    limit=limit,
                    has_more=False,
                    message=f"Failed to list containers: {response.error}"
                )

            containers_data = response.data
            if not containers_data:
                return FilterOptionsResponse(
                    success=True,
                    options=[],
                    page=page,
                    limit=limit,
                    has_more=False
                )

            all_containers = [
                container.get("name") for container in containers_data
                if container.get("name")
            ]

            if search:
                search_lower = search.lower()
                all_containers = [
                    container for container in all_containers
                    if search_lower in container.lower()
                ]

            start_idx = (page - 1) * limit
            end_idx = start_idx + limit
            paginated_containers = all_containers[start_idx:end_idx]
            has_more = end_idx < len(all_containers)

            options = [
                FilterOption(id=container, label=container)
                for container in paginated_containers
            ]

            return FilterOptionsResponse(
                success=True,
                options=options,
                page=page,
                limit=limit,
                has_more=has_more
            )

        except Exception as e:
            self.logger.error(f"Error getting container options: {e}", exc_info=True)
            return FilterOptionsResponse(
                success=False,
                options=[],
                page=page,
                limit=limit,
                has_more=False,
                message=f"Error: {str(e)}"
            )

    def handle_webhook_notification(self, notification: Dict) -> None:
        """Handle webhook notifications from the source."""
        raise NotImplementedError("This method is not supported")

    async def reindex_records(self, record_results: List[Record]) -> None:
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
                except Exception as e:
                    self.logger.error(f"Error checking record {record.id} at source: {e}")
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
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Check if record has been updated at source and fetch updated data."""
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
                self.logger.warning(f"Blob {blob_name} not found in container {container_name}")
                return None

            blob_metadata = response.data
            if not blob_metadata:
                return None

            # Check etag
            current_etag = blob_metadata.get("etag", "").strip('"') if blob_metadata.get("etag") else ""
            stored_etag = record.external_revision_id

            if current_etag == stored_etag:
                self.logger.debug(f"Record {record.id}: etag unchanged")
                return None

            self.logger.debug(f"Record {record.id}: etag changed")

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
            mime_type = blob_metadata.get("content_type") or get_mimetype_for_azure_blob(blob_name, is_folder)

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
                record_type=RecordType.FOLDER if is_folder else RecordType.FILE,
                record_group_type=RecordGroupType.BUCKET.value,
                external_record_group_id=container_name,
                external_record_id=updated_external_record_id,
                external_revision_id=current_etag,
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

            if hasattr(self, 'indexing_filters') and self.indexing_filters:
                if not self.indexing_filters.is_enabled(IndexingFilterKey.FILES, default=True):
                    updated_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

            permissions = await self._create_azure_blob_permissions(container_name, blob_name)

            return updated_record, permissions

        except Exception as e:
            self.logger.error(f"Error checking record {record.id} at source: {e}")
            return None

    async def run_incremental_sync(self) -> None:
        """Run an incremental synchronization from containers."""
        try:
            self.logger.info("Starting Azure Blob incremental sync.")

            if not self.data_source:
                raise ConnectionError("Azure Blob connector is not initialized.")

            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, self.filter_key, self.connector_id, self.logger
            )

            sync_filters = self.sync_filters if hasattr(self, 'sync_filters') and self.sync_filters else FilterCollection()

            container_filter = sync_filters.get("containers")
            selected_containers = container_filter.value if container_filter and container_filter.value else []

            containers_to_sync = []
            if self.container_name:
                containers_to_sync = [self.container_name]
                self.logger.info(f"Using configured container: {self.container_name}")
            elif selected_containers:
                containers_to_sync = selected_containers
                self.logger.info(f"Using filtered containers: {containers_to_sync}")
            else:
                containers_response = await self.data_source.list_containers()
                if containers_response.success and containers_response.data:
                    containers_to_sync = self._extract_container_names(containers_response.data)

                    if not containers_to_sync:
                        self.logger.warning("No valid container names found in response")
                        return

            if not containers_to_sync:
                self.logger.warning("No containers to sync")
                return

            for container_name in containers_to_sync:
                if not container_name:
                    continue
                try:
                    self.logger.info(f"Incremental sync for container: {container_name}")
                    await self._sync_container(container_name)
                except Exception as e:
                    self.logger.error(
                        f"Error in incremental sync for container {container_name}: {e}", exc_info=True
                    )
                    continue

            self.logger.info("Azure Blob incremental sync completed.")
        except Exception as ex:
            self.logger.error(f"Error in Azure Blob incremental sync: {ex}", exc_info=True)
            raise

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        **kwargs,
    ) -> "AzureBlobConnector":
        """Factory method to create and initialize connector."""
        # Extract account name from connection string if available
        account_name = ""
        try:
            config = await config_service.get_config(
                f"/services/connectors/{connector_id}/config"
            )
            if config:
                auth_config = config.get("auth", {})
                connection_string = auth_config.get("azureBlobConnectionString", "")
                if connection_string:
                    # Extract account name from connection string
                    for part in connection_string.split(';'):
                        if part.startswith('AccountName='):
                            account_name = part.split('=', 1)[1]
                            break
        except Exception as e:
            logger.warning(f"Could not extract account name from connection string: {e}")

        data_entities_processor = AzureBlobDataSourceEntitiesProcessor(
            logger, data_store_provider, config_service, account_name=account_name
        )
        await data_entities_processor.initialize()

        connector = cls(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
        )

        return connector
