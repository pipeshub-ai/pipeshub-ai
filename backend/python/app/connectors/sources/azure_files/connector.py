"""
Azure Files Connector

Connector for synchronizing data from Azure File Shares. This connector
supports real hierarchical directories (unlike S3/Azure Blob where directories
are virtual prefix-based paths).

Key differences from Azure Blob/S3:
- Directories are real entities with metadata, not internal placeholders
- Directory URLs are navigable (hide_weburl=False)
- Recursive directory traversal is required
"""

import mimetypes
import uuid
from datetime import datetime, timedelta, timezone
from logging import Logger
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

from aiolimiter import AsyncLimiter
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import config_node_constants
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
    FilterOption,
    FilterOptionsResponse,
    FilterType,
    IndexingFilterKey,
    ListOperator,
    MultiselectOperator,
    OptionSourceType,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.sources.azure_files.common.apps import AzureFilesApp
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
from app.sources.client.azure.azure_files import AzureFilesClient
from app.sources.external.azure.azure_files import AzureFilesDataSource
from app.utils.streaming import stream_content
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# Default connector endpoint for signed URL generation
DEFAULT_CONNECTOR_ENDPOINT = "http://localhost:8000"


def get_file_extension(file_path: str) -> Optional[str]:
    """Extracts the extension from a file path."""
    if "." in file_path:
        parts = file_path.split(".")
        if len(parts) > 1:
            return parts[-1].lower()
    return None


def get_parent_path(file_path: str) -> Optional[str]:
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


def get_mimetype_for_azure_files(file_path: str, is_directory: bool = False) -> str:
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


class AzureFilesDataSourceEntitiesProcessor(DataSourceEntitiesProcessor):
    """Azure Files processor that handles real directory records.

    Unlike S3/Azure Blob where folders are virtual (internal placeholders),
    Azure Files has real directories that should be visible to users.
    """

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
        Create a placeholder parent record with Azure Files-specific handling.

        Unlike S3/Azure Blob, Azure Files directories are REAL entities,
        so they should NOT be marked as internal placeholders.
        """
        parent_record = super()._create_placeholder_parent_record(
            parent_external_id, parent_record_type, record
        )

        if parent_record_type == RecordType.FILE and isinstance(
            parent_record, FileRecord
        ):
            # Azure Files directories are REAL, not placeholders
            # They have navigable URLs in Azure Portal
            weburl = self._generate_directory_url(parent_external_id)
            path = self._extract_path_from_external_id(parent_external_id)
            parent_record.weburl = weburl
            parent_record.path = path
            # Key difference: directories are NOT internal in Azure Files
            parent_record.is_internal = False
            parent_record.hide_weburl = False

        return parent_record

    def _generate_directory_url(self, parent_external_id: str) -> str:
        """Generate URL for an Azure Files directory.

        Args:
            parent_external_id: External ID in format "share_name/path" or just "share_name"

        Returns:
            Azure Files URL for the directory
        """
        if "/" in parent_external_id:
            parts = parent_external_id.split("/", 1)
            share_name = parts[0]
            path = parts[1]
            return f"https://{self.account_name}.file.core.windows.net/{share_name}/{quote(path)}"
        else:
            share_name = parent_external_id
            return f"https://{self.account_name}.file.core.windows.net/{share_name}"

    def _extract_path_from_external_id(self, parent_external_id: str) -> Optional[str]:
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
    .with_auth([
        AuthBuilder.type(AuthType.ACCOUNT_KEY).fields([
            AuthField(
                name="accountName",
                display_name="Account Name",
                placeholder="mystorageaccount",
                description="The Azure Storage account name",
                field_type="TEXT",
                max_length=2000,
                is_secret=False
            ),
            AuthField(
                name="accountKey",
                display_name="Account Key",
                placeholder="Your account key",
                description="The Azure Storage account key",
                field_type="PASSWORD",
                max_length=2000,
                is_secret=True
            ),
            AuthField(
                name="shareName",
                display_name="Share Name",
                placeholder="my-file-share",
                description="Optional: specific file share to sync. Leave empty to sync all shares.",
                field_type="TEXT",
                max_length=2000,
                is_secret=False
            ),
            AuthField(
                name="endpointProtocol",
                display_name="Endpoint Protocol",
                placeholder="https",
                description="The Endpoint Protocol (default: https)",
                field_type="TEXT",
                max_length=2000,
                is_secret=False
            ),
            AuthField(
                name="endpointSuffix",
                display_name="Endpoint Suffix",
                placeholder="core.windows.net",
                description="The Endpoint Suffix (default: core.windows.net)",
                field_type="TEXT",
                max_length=2000,
                is_secret=False
            ),
        ])
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/azurefiles.svg")
        .add_documentation_link(DocumentationLink(
            "Azure Files Setup",
            "https://learn.microsoft.com/en-us/azure/storage/files/storage-files-introduction",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/azure/azurefiles',
            'pipeshub'
        ))
        .add_filter_field(FilterField(
            name="shares",
            display_name="File Share Names",
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            description="Select specific Azure File Shares to sync",
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
    ) -> None:
        super().__init__(
            app=AzureFilesApp(connector_id),
            logger=logger,
            data_entities_processor=data_entities_processor,
            data_store_provider=data_store_provider,
            config_service=config_service,
            connector_id=connector_id,
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

        self.data_source: Optional[AzureFilesDataSource] = None
        self.batch_size = 100
        self.rate_limiter = AsyncLimiter(50, 1)  # 50 requests per second
        self.share_name: Optional[str] = None
        self.connector_scope: Optional[str] = None
        self.created_by: Optional[str] = None
        self.account_name: Optional[str] = None

        # Initialize filter collections
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

    def get_app_users(self, users: List[User]) -> List[AppUser]:
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
        """Initializes the Azure Files client using credentials from the config service."""
        config = await self.config_service.get_config(
            f"/services/connectors/{self.connector_id}/config"
        )
        if not config:
            self.logger.error("Azure Files configuration not found.")
            return False

        auth_config = config.get("auth", {})
        account_name = auth_config.get("accountName")
        account_key = auth_config.get("accountKey")
        self.share_name = auth_config.get("shareName")

        if not account_name or not account_key:
            self.logger.error(
                "Azure Files account name or account key not found in configuration."
            )
            return False

        self.account_name = account_name

        # Get connector scope
        self.connector_scope = ConnectorScope.PERSONAL.value
        self.created_by = config.get("created_by")

        scope_from_config = config.get("scope")
        if scope_from_config:
            self.connector_scope = scope_from_config

        try:
            client = await AzureFilesClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id,
            )
            self.data_source = AzureFilesDataSource(client)

            # Update the entities processor with the account name
            if isinstance(
                self.data_entities_processor, AzureFilesDataSourceEntitiesProcessor
            ):
                self.data_entities_processor.account_name = self.account_name

            # Load connector filters
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, self.filter_key, self.connector_id, self.logger
            )

            self.logger.info("Azure Files client initialized successfully.")
            return True
        except Exception as e:
            self.logger.error(
                f"Failed to initialize Azure Files client: {e}", exc_info=True
            )
            return False

    def _generate_web_url(self, share_name: str, file_path: str) -> str:
        """Generate the web URL for an Azure file."""
        return f"https://{self.account_name}.file.core.windows.net/{share_name}/{quote(file_path)}"

    def _generate_directory_url(self, share_name: str, dir_path: str) -> str:
        """Generate the web URL for an Azure Files directory."""
        if dir_path:
            return f"https://{self.account_name}.file.core.windows.net/{share_name}/{quote(dir_path)}"
        return f"https://{self.account_name}.file.core.windows.net/{share_name}"

    async def run_sync(self) -> None:
        """Runs a full synchronization from file shares."""
        try:
            self.logger.info("Starting Azure Files full sync.")

            if not self.data_source:
                raise ConnectionError("Azure Files connector is not initialized.")

            # Reload sync and indexing filters to pick up configuration changes
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, self.filter_key, self.connector_id, self.logger
            )

            all_active_users = await self.data_entities_processor.get_all_active_users()
            app_users = self.get_app_users(all_active_users)
            await self.data_entities_processor.on_new_app_users(app_users)

            # Get sync filters
            sync_filters = (
                self.sync_filters
                if hasattr(self, "sync_filters") and self.sync_filters
                else FilterCollection()
            )

            # Get share filter if specified
            share_filter = sync_filters.get("shares")
            selected_shares = (
                share_filter.value if share_filter and share_filter.value else []
            )

            # List all shares or use configured share
            shares_to_sync = []
            if self.share_name:
                shares_to_sync = [self.share_name]
                self.logger.info(f"Using configured share: {self.share_name}")
            elif selected_shares:
                shares_to_sync = selected_shares
                self.logger.info(f"Using filtered shares: {shares_to_sync}")
            else:
                self.logger.info("Listing all shares...")
                shares_response = await self.data_source.list_shares()
                if not shares_response.success:
                    self.logger.error(
                        f"Failed to list shares: {shares_response.error}"
                    )
                    return

                shares_data = shares_response.data
                if shares_data:
                    shares_to_sync = [
                        share.get("name")
                        for share in shares_data
                        if share.get("name")
                    ]
                    self.logger.info(f"Found {len(shares_to_sync)} share(s) to sync")
                else:
                    self.logger.warning("No shares found")
                    return

            # Create record groups for shares first
            await self._create_record_groups_for_shares(shares_to_sync)

            # Sync each share
            for share_name in shares_to_sync:
                if not share_name:
                    continue
                try:
                    self.logger.info(f"Syncing share: {share_name}")
                    await self._sync_share(share_name)
                except Exception as e:
                    self.logger.error(
                        f"Error syncing share {share_name}: {e}", exc_info=True
                    )
                    continue

            self.logger.info("Azure Files full sync completed.")
        except Exception as ex:
            self.logger.error(f"Error in Azure Files connector run: {ex}", exc_info=True)
            raise

    async def _create_record_groups_for_shares(
        self, share_names: List[str]
    ) -> None:
        """Create record groups for shares with appropriate permissions."""
        if not share_names:
            return

        # Get user info once upfront to avoid repeated transactions
        creator_email = None
        if self.created_by and self.connector_scope != ConnectorScope.TEAM.value:
            try:
                async with self.data_store_provider.transaction() as tx_store:
                    user = await tx_store.get_user_by_id(self.created_by)
                    if user and user.get("email"):
                        creator_email = user.get("email")
            except Exception as e:
                self.logger.warning(
                    f"Could not get user for created_by {self.created_by}: {e}"
                )

        record_groups = []
        for share_name in share_names:
            if not share_name:
                continue

            permissions = []
            if self.connector_scope == ConnectorScope.TEAM.value:
                permissions.append(
                    Permission(
                        type=PermissionType.READ,
                        entity_type=EntityType.ORG,
                        external_id=self.data_entities_processor.org_id,
                    )
                )
            else:
                if creator_email:
                    permissions.append(
                        Permission(
                            type=PermissionType.OWNER,
                            entity_type=EntityType.USER,
                            email=creator_email,
                            external_id=self.created_by,
                        )
                    )

                if not permissions:
                    permissions.append(
                        Permission(
                            type=PermissionType.READ,
                            entity_type=EntityType.ORG,
                            external_id=self.data_entities_processor.org_id,
                        )
                    )

            record_group = RecordGroup(
                name=share_name,
                external_group_id=share_name,
                group_type=RecordGroupType.FILE_SHARE,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                description=f"Azure File Share: {share_name}",
            )
            record_groups.append((record_group, permissions))

        if record_groups:
            await self.data_entities_processor.on_new_record_groups(record_groups)
            self.logger.info(
                f"Created {len(record_groups)} record group(s) for shares"
            )

    def _get_date_filters(
        self,
    ) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[int]]:
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
        item: Dict,
        modified_after_ms: Optional[int] = None,
        modified_before_ms: Optional[int] = None,
        created_after_ms: Optional[int] = None,
        created_before_ms: Optional[int] = None,
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
            obj_timestamp_ms = int(last_modified.timestamp() * 1000)
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
                created_timestamp_ms = int(creation_time.timestamp() * 1000)
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

    async def _get_signed_url_route(self, record_id: str) -> str:
        """Generate the signed URL route for a record."""
        endpoints = await self.config_service.get_config(
            config_node_constants.ENDPOINTS.value
        )
        connector_endpoint = endpoints.get("connectors", {}).get(
            "endpoint", DEFAULT_CONNECTOR_ENDPOINT
        )
        return f"{connector_endpoint}/api/v1/internal/stream/record/{record_id}"

    async def _sync_share(self, share_name: str) -> None:
        """Sync files and directories from a specific share with recursive traversal."""
        if not self.data_source:
            raise ConnectionError("Azure Files connector is not initialized.")

        sync_filters = (
            self.sync_filters
            if hasattr(self, "sync_filters") and self.sync_filters
            else FilterCollection()
        )

        file_extensions_filter = sync_filters.get("file_extensions")
        allowed_extensions = []
        if file_extensions_filter and not file_extensions_filter.is_empty():
            filter_value = file_extensions_filter.value
            if isinstance(filter_value, list):
                allowed_extensions = [
                    ext.lower().lstrip(".") for ext in filter_value if ext
                ]
            elif isinstance(filter_value, str):
                allowed_extensions = [filter_value.lower().lstrip(".")]

        if allowed_extensions:
            self.logger.info(
                f"File extensions filter active for share {share_name}: {allowed_extensions}"
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

        if last_sync_time:
            user_modified_after_ms = modified_after_ms
            if user_modified_after_ms:
                modified_after_ms = max(user_modified_after_ms, last_sync_time)
            else:
                modified_after_ms = last_sync_time

        batch_records: List[Tuple[FileRecord, List[Permission]]] = []
        max_timestamp = last_sync_time if last_sync_time else 0

        # Recursive directory traversal
        async def traverse_directory(directory_path: str) -> None:
            nonlocal batch_records, max_timestamp

            try:
                async with self.rate_limiter:
                    response = await self.data_source.list_directories_and_files(
                        share_name=share_name,
                        directory_path=directory_path,
                    )

                    if not response.success:
                        error_msg = response.error or "Unknown error"
                        self.logger.error(
                            f"Failed to list items in {share_name}/{directory_path}: {error_msg}"
                        )
                        return

                    items = response.data or []
                    self.logger.debug(
                        f"Processing {len(items)} items from {share_name}/{directory_path or 'root'}"
                    )

                    for item in items:
                        try:
                            item_name = item.get("name", "")
                            is_directory = item.get("is_directory", False)
                            item_path = item.get("path", item_name)

                            # Skip file extension filter for directories
                            if not is_directory and allowed_extensions:
                                ext = get_file_extension(item_name)
                                if not ext:
                                    self.logger.debug(
                                        f"Skipping {item_path}: no file extension found"
                                    )
                                    continue
                                if ext not in allowed_extensions:
                                    self.logger.debug(
                                        f"Skipping {item_path}: extension '{ext}' not in allowed extensions"
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

                            # Track max timestamp for incremental sync
                            last_modified = item.get("last_modified")
                            if last_modified:
                                if isinstance(last_modified, datetime):
                                    obj_timestamp_ms = int(
                                        last_modified.timestamp() * 1000
                                    )
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

                            record, permissions = await self._process_azure_files_item(
                                item, share_name
                            )
                            if record:
                                batch_records.append((record, permissions))

                                if len(batch_records) >= self.batch_size:
                                    await self.data_entities_processor.on_new_records(
                                        batch_records
                                    )
                                    batch_records = []

                            # Recurse into subdirectories
                            if is_directory:
                                await traverse_directory(item_path)

                        except Exception as e:
                            self.logger.error(
                                f"Error processing item {item.get('name', 'unknown')}: {e}",
                                exc_info=True,
                            )
                            continue

            except Exception as e:
                self.logger.error(
                    f"Error during directory traversal for {share_name}/{directory_path}: {e}",
                    exc_info=True,
                )

        # Start traversal from root
        await traverse_directory("")

        # Process remaining records
        if batch_records:
            await self.data_entities_processor.on_new_records(batch_records)

        if max_timestamp > 0:
            await self.record_sync_point.update_sync_point(
                sync_point_key, {"last_sync_time": max_timestamp}
            )

    async def _remove_old_parent_relationship(
        self, record_id: str, tx_store: "TransactionStore"
    ) -> None:
        """Remove old PARENT_CHILD relationships for a record."""
        try:
            record_key = f"{CollectionNames.RECORDS.value}/{record_id}"
            deleted_count = await tx_store.delete_parent_child_edges_to(to_key=record_key)
            if deleted_count > 0:
                self.logger.info(
                    f"Removed {deleted_count} old parent relationship(s) for record {record_id}"
                )
        except Exception as e:
            self.logger.warning(f"Error in _remove_old_parent_relationship: {e}")

    async def _process_azure_files_item(
        self, item: Dict, share_name: str
    ) -> Tuple[Optional[FileRecord], List[Permission]]:
        """Process a single Azure Files item (file or directory) and convert it to a FileRecord.

        Key difference from S3/Blob: Directories are REAL entities, not placeholders.
        """
        try:
            item_name = item.get("name", "")
            if not item_name:
                return None, []

            is_directory = item.get("is_directory", False)
            is_file = not is_directory
            item_path = item.get("path", item_name)

            # Parse timestamps
            last_modified = item.get("last_modified")
            if last_modified:
                if isinstance(last_modified, datetime):
                    timestamp_ms = int(last_modified.timestamp() * 1000)
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
                    created_timestamp_ms = int(creation_time.timestamp() * 1000)
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

            external_record_id = f"{share_name}/{item_path}"
            current_etag = item.get("etag", "").strip('"') if item.get("etag") else ""

            # Check for existing record
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id, external_id=external_record_id
                )

            is_move = False

            if existing_record:
                stored_etag = existing_record.external_revision_id or ""
                if current_etag and stored_etag and current_etag == stored_etag:
                    self.logger.debug(f"Skipping {item_path}: revision unchanged")
                    return None, []

                if current_etag != stored_etag:
                    self.logger.info(f"Content change detected: {item_path}")
            elif current_etag:
                # Try lookup by revision ID for move detection
                async with self.data_store_provider.transaction() as tx_store:
                    existing_record = await tx_store.get_record_by_external_revision_id(
                        connector_id=self.connector_id, external_revision_id=current_etag
                    )

                if existing_record:
                    is_move = True
                    self.logger.info(f"Move/rename detected: {item_path}")
                else:
                    self.logger.debug(f"New item: {item_path}")
            else:
                self.logger.debug(f"New item: {item_path}")

            # Prepare record data
            record_type = RecordType.FOLDER if is_directory else RecordType.FILE
            extension = get_file_extension(item_path) if is_file else None
            mime_type = (
                item.get("content_type")
                or get_mimetype_for_azure_files(item_path, is_directory)
            )

            parent_path = get_parent_path(item_path)
            parent_external_id = (
                f"{share_name}/{parent_path}" if parent_path else share_name
            )

            if is_directory:
                web_url = self._generate_directory_url(share_name, item_path)
            else:
                web_url = self._generate_web_url(share_name, item_path)

            record_id = existing_record.id if existing_record else str(uuid.uuid4())
            signed_url_route = await self._get_signed_url_route(record_id)
            record_name = item_name

            # For moves/renames, remove old parent relationship
            if is_move and existing_record:
                async with self.data_store_provider.transaction() as tx_store:
                    await self._remove_old_parent_relationship(record_id, tx_store)

            if not existing_record:
                version = 0
            else:
                version = existing_record.version + 1

            # Get content MD5 hash
            content_md5 = item.get("content_md5")
            if content_md5 and isinstance(content_md5, bytes):
                import base64

                content_md5 = base64.b64encode(content_md5).decode("utf-8")

            file_record = FileRecord(
                id=record_id,
                record_name=record_name,
                record_type=record_type,
                record_group_type=RecordGroupType.FILE_SHARE.value,
                external_record_group_id=share_name,
                external_record_id=external_record_id,
                external_revision_id=current_etag,
                version=version,
                origin=OriginTypes.CONNECTOR.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                source_created_at=existing_record.source_created_at
                if existing_record
                else created_timestamp_ms,
                source_updated_at=timestamp_ms,
                weburl=web_url,
                signed_url=None,
                fetch_signed_url=signed_url_route if is_file else None,
                # KEY DIFFERENCE: Azure Files directories are NOT internal
                # They are real entities with navigable URLs
                hide_weburl=False,
                is_internal=False,
                parent_external_record_id=parent_external_id,
                parent_record_type=RecordType.FILE,
                size_in_bytes=item.get("size", 0) or item.get("content_length", 0)
                if is_file
                else 0,
                is_file=is_file,
                extension=extension,
                path=item_path,
                mime_type=mime_type,
                md5_hash=content_md5,
                etag=current_etag,
            )

            if hasattr(self, "indexing_filters") and self.indexing_filters:
                if not self.indexing_filters.is_enabled(
                    IndexingFilterKey.FILES, default=True
                ):
                    file_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

            permissions = await self._create_azure_files_permissions(
                share_name, item_path
            )

            return file_record, permissions

        except Exception as e:
            self.logger.error(f"Error processing Azure Files item: {e}", exc_info=True)
            return None, []

    async def _create_azure_files_permissions(
        self, share_name: str, item_path: str
    ) -> List[Permission]:
        """Create permissions for an Azure Files item based on connector scope."""
        try:
            permissions = []

            if self.connector_scope == ConnectorScope.TEAM.value:
                permissions.append(
                    Permission(
                        type=PermissionType.READ,
                        entity_type=EntityType.ORG,
                        external_id=self.data_entities_processor.org_id,
                    )
                )
            else:
                if self.created_by:
                    try:
                        async with self.data_store_provider.transaction() as tx_store:
                            user = await tx_store.get_user_by_id(self.created_by)
                            if user and user.get("email"):
                                permissions.append(
                                    Permission(
                                        type=PermissionType.OWNER,
                                        entity_type=EntityType.USER,
                                        email=user.get("email"),
                                        external_id=self.created_by,
                                    )
                                )
                    except Exception as e:
                        self.logger.warning(
                            f"Could not get user for created_by {self.created_by}: {e}"
                        )

                if not permissions:
                    permissions.append(
                        Permission(
                            type=PermissionType.READ,
                            entity_type=EntityType.ORG,
                            external_id=self.data_entities_processor.org_id,
                        )
                    )

            return permissions
        except Exception as e:
            self.logger.warning(f"Error creating permissions for {item_path}: {e}")
            return [
                Permission(
                    type=PermissionType.READ,
                    entity_type=EntityType.ORG,
                    external_id=self.data_entities_processor.org_id,
                )
            ]

    async def test_connection_and_access(self) -> bool:
        """Test connection and access."""
        if not self.data_source:
            return False
        try:
            response = await self.data_source.list_shares()
            if response.success:
                self.logger.info("Azure Files connection test successful.")
                return True
            else:
                self.logger.error(
                    f"Azure Files connection test failed: {response.error}"
                )
                return False
        except Exception as e:
            self.logger.error(
                f"Azure Files connection test failed: {e}", exc_info=True
            )
            return False

    async def get_signed_url(self, record: Record) -> Optional[str]:
        """Generate a SAS URL for an Azure file."""
        if not self.data_source:
            return None
        try:
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

            if external_record_id.startswith(f"{share_name}/"):
                file_path = external_record_id[len(f"{share_name}/") :]
            else:
                file_path = external_record_id.lstrip("/")

            from urllib.parse import unquote

            file_path = unquote(file_path)

            self.logger.debug(
                f"Generating SAS URL - Share: {share_name}, "
                f"File: {file_path}, Record ID: {record.id}"
            )

            # Generate SAS URL with 24 hour expiry
            expiry = datetime.now(timezone.utc) + timedelta(hours=24)
            response = await self.data_source.generate_file_sas_url(
                share_name=share_name,
                file_path=file_path,
                permission="r",
                expiry=expiry,
            )

            if response.success and response.data:
                return response.data.get("sas_url")
            else:
                self.logger.error(
                    f"Failed to generate SAS URL: {response.error} | "
                    f"Share: {share_name} | File: {file_path}"
                )
                return None
        except Exception as e:
            self.logger.error(
                f"Error generating SAS URL for record {record.id}: {e}"
            )
            return None

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Stream Azure file content."""
        if isinstance(record, FileRecord) and not record.is_file:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Cannot stream directory content",
            )

        signed_url = await self.get_signed_url(record)
        if not signed_url:
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail="File not found or access denied",
            )

        return StreamingResponse(
            stream_content(
                signed_url, record_id=record.id, file_name=record.record_name
            ),
            media_type=record.mime_type if record.mime_type else "application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename={record.record_name}"},
        )

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
        search: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> FilterOptionsResponse:
        """Get dynamic filter options for filters."""
        if filter_key == "shares":
            return await self._get_share_options(page, limit, search)
        else:
            raise ValueError(f"Unsupported filter key: {filter_key}")

    async def _get_share_options(
        self, page: int, limit: int, search: Optional[str]
    ) -> FilterOptionsResponse:
        """Get list of available file shares."""
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

            response = await self.data_source.list_shares()
            if not response.success:
                return FilterOptionsResponse(
                    success=False,
                    options=[],
                    page=page,
                    limit=limit,
                    has_more=False,
                    message=f"Failed to list shares: {response.error}",
                )

            shares_data = response.data
            if not shares_data:
                return FilterOptionsResponse(
                    success=True,
                    options=[],
                    page=page,
                    limit=limit,
                    has_more=False,
                )

            all_shares = [
                share.get("name") for share in shares_data if share.get("name")
            ]

            if search:
                search_lower = search.lower()
                all_shares = [
                    share for share in all_shares if search_lower in share.lower()
                ]

            start_idx = (page - 1) * limit
            end_idx = start_idx + limit
            paginated_shares = all_shares[start_idx:end_idx]
            has_more = end_idx < len(all_shares)

            options = [
                FilterOption(id=share, label=share) for share in paginated_shares
            ]

            return FilterOptionsResponse(
                success=True,
                options=options,
                page=page,
                limit=limit,
                has_more=has_more,
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

    def handle_webhook_notification(self, notification: Dict) -> None:
        """Handle webhook notifications from the source."""
        raise NotImplementedError("This method is not supported")

    async def reindex_records(self, record_results: List[Record]) -> None:
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
                except Exception as e:
                    self.logger.error(
                        f"Error checking record {record.id} at source: {e}"
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
    ) -> Optional[Tuple[Record, List[Permission]]]:
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
                self.logger.warning(
                    f"Item {item_path} not found in share {share_name}"
                )
                return None

            item_metadata = response.data
            if not item_metadata:
                return None

            # Check etag
            current_etag = (
                item_metadata.get("etag", "").strip('"')
                if item_metadata.get("etag")
                else ""
            )
            stored_etag = record.external_revision_id

            if current_etag == stored_etag:
                self.logger.debug(
                    f"Record {record.id}: etag unchanged ({current_etag})"
                )
                return None

            self.logger.debug(f"Record {record.id}: etag changed")

            # Parse timestamps
            last_modified = item_metadata.get("last_modified")
            if last_modified:
                if isinstance(last_modified, datetime):
                    timestamp_ms = int(last_modified.timestamp() * 1000)
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

            is_directory = item_metadata.get("is_directory", False)

            extension = get_file_extension(item_path) if is_file else None
            mime_type = (
                item_metadata.get("content_type")
                or get_mimetype_for_azure_files(item_path, is_directory)
            )

            parent_path = get_parent_path(item_path)
            parent_external_id = (
                f"{share_name}/{parent_path}" if parent_path else share_name
            )

            if is_directory:
                web_url = self._generate_directory_url(share_name, item_path)
            else:
                web_url = self._generate_web_url(share_name, item_path)

            signed_url_route = await self._get_signed_url_route(record.id)

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
                record_type=RecordType.FOLDER if is_directory else RecordType.FILE,
                record_group_type=RecordGroupType.FILE_SHARE.value,
                external_record_group_id=share_name,
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
                fetch_signed_url=signed_url_route if is_file else None,
                hide_weburl=False,
                is_internal=False,
                parent_external_record_id=parent_external_id,
                parent_record_type=RecordType.FILE,
                size_in_bytes=item_metadata.get("size", 0) if is_file else 0,
                is_file=is_file,
                extension=extension,
                path=item_path,
                mime_type=mime_type,
                md5_hash=content_md5,
                etag=current_etag,
            )

            if hasattr(self, "indexing_filters") and self.indexing_filters:
                if not self.indexing_filters.is_enabled(
                    IndexingFilterKey.FILES, default=True
                ):
                    updated_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

            permissions = await self._create_azure_files_permissions(
                share_name, item_path
            )

            return updated_record, permissions

        except Exception as e:
            self.logger.error(f"Error checking record {record.id} at source: {e}")
            return None

    async def run_incremental_sync(self) -> None:
        """Run an incremental synchronization from shares."""
        try:
            self.logger.info("Starting Azure Files incremental sync.")

            if not self.data_source:
                raise ConnectionError("Azure Files connector is not initialized.")

            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, self.filter_key, self.connector_id, self.logger
            )

            sync_filters = (
                self.sync_filters
                if hasattr(self, "sync_filters") and self.sync_filters
                else FilterCollection()
            )

            share_filter = sync_filters.get("shares")
            selected_shares = (
                share_filter.value if share_filter and share_filter.value else []
            )

            shares_to_sync = []
            if self.share_name:
                shares_to_sync = [self.share_name]
                self.logger.info(f"Using configured share: {self.share_name}")
            elif selected_shares:
                shares_to_sync = selected_shares
                self.logger.info(f"Using filtered shares: {shares_to_sync}")
            else:
                shares_response = await self.data_source.list_shares()
                if shares_response.success and shares_response.data:
                    shares_to_sync = [
                        share.get("name")
                        for share in shares_response.data
                        if share.get("name")
                    ]

            if not shares_to_sync:
                self.logger.warning("No shares to sync")
                return

            for share_name in shares_to_sync:
                if not share_name:
                    continue
                try:
                    self.logger.info(f"Incremental sync for share: {share_name}")
                    await self._sync_share(share_name)
                except Exception as e:
                    self.logger.error(
                        f"Error in incremental sync for share {share_name}: {e}",
                        exc_info=True,
                    )
                    continue

            self.logger.info("Azure Files incremental sync completed.")
        except Exception as ex:
            self.logger.error(
                f"Error in Azure Files incremental sync: {ex}", exc_info=True
            )
            raise

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        **kwargs,
    ) -> "AzureFilesConnector":
        """Factory method to create and initialize connector."""
        # Get account name from config for entities processor
        config = await config_service.get_config(
            f"/services/connectors/{connector_id}/config"
        )
        account_name = ""
        if config:
            auth_config = config.get("auth", {})
            account_name = auth_config.get("accountName", "")

        data_entities_processor = AzureFilesDataSourceEntitiesProcessor(
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
