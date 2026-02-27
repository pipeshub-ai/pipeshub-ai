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

import base64
import mimetypes
import uuid
from datetime import datetime, timedelta, timezone
from logging import Logger
from typing import AsyncGenerator, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote

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
from app.utils.streaming import create_stream_record_response, stream_content
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

    def _create_placeholder_parent_record(
        self,
        parent_external_id: str,
        parent_record_type: RecordType,
        record: Record,
    ) -> Record:
        """Create a placeholder parent record with Azure Files-specific handling."""
        parent_record = super()._create_placeholder_parent_record(
            parent_external_id, parent_record_type, record
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
        AuthBuilder.type(AuthType.CONNECTION_STRING).fields([
            AuthField(
                name="connectionString",
                display_name="Connection String",
                placeholder="DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net",
                description="The Azure Storage connection string from Azure Portal (Storage account > Access keys)",
                field_type="PASSWORD",
                max_length=2000,
                is_secret=True
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
        self.creator_email: Optional[str] = None  # Cached to avoid repeated DB queries
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
        """Initializes the Azure Files client using connection string from the config service."""
        config = await self.config_service.get_config(
            f"/services/connectors/{self.connector_id}/config"
        )
        if not config:
            self.logger.error("Azure Files configuration not found.")
            return False

        auth_config = config.get("auth", {})
        connection_string = auth_config.get("connectionString")

        if not connection_string:
            self.logger.error("Azure Files connectionString not found in configuration.")
            return False

        # Derive account name for web URL generation
        self.account_name = self._extract_account_name_from_connection_string(
            connection_string
        )

        # Get connector scope
        self.connector_scope = ConnectorScope.PERSONAL.value
        self.created_by = config.get("createdBy") or config.get("created_by")

        scope_from_config = config.get("scope")
        if scope_from_config:
            self.connector_scope = scope_from_config

        # Fetch creator email once to avoid repeated DB queries during sync
        if self.created_by and self.connector_scope != ConnectorScope.TEAM.value:
            try:
                async with self.data_store_provider.transaction() as tx_store:
                    user = await tx_store.get_user_by_user_id(self.created_by)
                    if user and user.get("email"):
                        self.creator_email = user.get("email")
            except Exception as e:
                self.logger.warning(
                    f"Could not get user for created_by {self.created_by}: {e}"
                )

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
                self.data_entities_processor.account_name = self.account_name or ""

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

    @staticmethod
    def _extract_account_name_from_connection_string(
        connection_string: str,
    ) -> Optional[str]:
        """Extract account name from an Azure Storage connection string."""
        for part in connection_string.split(";"):
            if not part or "=" not in part:
                continue
            key, value = part.split("=", 1)
            if key == "AccountName":
                return value or None
        return None

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

            if self.connector_scope == ConnectorScope.TEAM.value:
                async with self.data_store_provider.transaction() as tx_store:
                    await tx_store.ensure_team_app_edge(
                        self.connector_id,
                        self.data_entities_processor.org_id,
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
            if selected_shares:
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
                # Use cached creator_email from init() instead of querying DB again
                if self.creator_email:
                    permissions.append(
                        Permission(
                            type=PermissionType.OWNER,
                            entity_type=EntityType.USER,
                            email=self.creator_email,
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

    def _pass_extension_filter(self, item_path: str, is_directory: bool = False) -> bool:
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

    async def _sync_share(self, share_name: str) -> None:
        """Sync files and directories from a specific share with recursive traversal."""
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

                            # Check extension filter
                            if not self._pass_extension_filter(item_path, is_directory):
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
            deleted_count = await tx_store.delete_parent_child_edge_to_record(record_id)
            if deleted_count > 0:
                self.logger.info(
                    f"Removed {deleted_count} old parent relationship(s) for record {record_id}"
                )
        except Exception as e:
            self.logger.warning(f"Error in _remove_old_parent_relationship: {e}")

    def _get_azure_files_revision_id(self, item: Dict) -> str:
        """
        Determines a stable revision ID for an Azure Files item.

        Prefers file_id (SMB FileId from list API) when available, as it is stable
        across renames. Then content_md5 (stable across renames), then etag.

        Note: Azure Files etag changes on every modification including metadata changes
        and renames. Therefore file_id or content_md5 must be used for reliable
        move/rename detection when available.

        Args:
            item: Azure Files item metadata dictionary

        Returns:
            Revision ID string (file_id, content_md5, or etag)
        """
        file_id = item.get("file_id")
        if file_id is not None:
            return str(file_id)

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
            raw_path = (item.get("path") or item_name).strip()
            normalized_path = raw_path.lstrip("/").rstrip("/") if raw_path else ""

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

            external_record_id = f"{share_name}/{normalized_path}"
            current_revision_id = self._get_azure_files_revision_id(item)
            raw_etag = item.get("etag", "").strip('"') if item.get("etag") else ""

            # PRIMARY: Try lookup by path (externalRecordId)
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id, external_id=external_record_id
                )

            is_move = False

            if existing_record:
                stored_revision = existing_record.external_revision_id or ""
                if current_revision_id and stored_revision and current_revision_id == stored_revision:
                    self.logger.debug(
                        f"Skipping {normalized_path}: externalRecordId and externalRevisionId unchanged"
                    )
                    return None, []

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
                # Not found by path - FALLBACK: try revision-based lookup (for move/rename detection)
                async with self.data_store_provider.transaction() as tx_store:
                    existing_record = await tx_store.get_record_by_external_revision_id(
                        connector_id=self.connector_id, external_revision_id=current_revision_id
                    )

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
            mime_type = (
                item.get("content_type")
                or get_mimetype_for_azure_files(normalized_path, is_directory)
            )

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

            # For moves/renames, remove old parent relationship
            if is_move and existing_record:
                async with self.data_store_provider.transaction() as tx_store:
                    await self._remove_old_parent_relationship(record_id, tx_store)

            if not existing_record:
                version = 0
            else:
                version = existing_record.version + 1

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

            if hasattr(self, "indexing_filters") and self.indexing_filters:
                if not self.indexing_filters.is_enabled(
                    IndexingFilterKey.FILES, default=True
                ):
                    file_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

            permissions = await self._create_azure_files_permissions(
                share_name, normalized_path
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
            permissions: List[Permission] = []

            if self.connector_scope == ConnectorScope.TEAM.value:
                permissions.append(
                    Permission(
                        type=PermissionType.READ,
                        entity_type=EntityType.ORG,
                        external_id=self.data_entities_processor.org_id,
                    )
                )
            else:
                # Use cached creator_email from init() instead of querying DB for each item
                if self.creator_email:
                    permissions.append(
                        Permission(
                            type=PermissionType.OWNER,
                            entity_type=EntityType.USER,
                            email=self.creator_email,
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

    def _extract_file_path_info(self, record: Record) -> Optional[Tuple[str, str]]:
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

        if external_record_id.startswith(f"{share_name}/"):
            file_path = external_record_id[len(f"{share_name}/") :]
        else:
            file_path = external_record_id.lstrip("/")

        file_path = unquote(file_path)
        return (share_name, file_path)

    async def get_signed_url(self, record: Record) -> Optional[str]:
        """Generate a SAS URL for an Azure file."""
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

    async def _stream_file_content(
        self, content: bytes, chunk_size: int = 8192
    ) -> AsyncGenerator[bytes, None]:
        """Stream file content in chunks.

        Args:
            content: The file content as bytes
            chunk_size: Size of each chunk to yield (default: 8KB)

        Yields:
            Chunks of bytes
        """
        offset = 0
        while offset < len(content):
            chunk = content[offset : offset + chunk_size]
            yield chunk
            offset += len(chunk)

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Stream Azure file content."""
        if isinstance(record, FileRecord) and not record.is_file:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Cannot stream directory content",
            )

        if not self.data_source:
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail="Data source not initialized",
            )

        # Extract file path information
        path_info = self._extract_file_path_info(record)
        if not path_info:
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail="File not found or invalid record",
            )

        share_name, file_path = path_info

        # Try to generate SAS URL first
        signed_url = await self.get_signed_url(record)

        if signed_url:
            # Use SAS URL streaming (existing behavior)
            return create_stream_record_response(
                stream_content(signed_url, record_id=record.id, file_name=record.record_name),
                filename=record.record_name,
                mime_type=record.mime_type if record.mime_type else "application/octet-stream",
                fallback_filename=f"record_{record.id}"
            )

        # Fallback: Download file directly when SAS URL generation fails
        # This handles cases where account key is missing from connection string
        self.logger.info(
            f"SAS URL generation failed, falling back to direct download - "
            f"Share: {share_name} | File: {file_path} | Record ID: {record.id}"
        )

        try:
            download_response = await self.data_source.download_file(
                share_name=share_name,
                file_path=file_path,
            )

            if not download_response.success:
                error_msg = download_response.error or "Unknown error"
                if "not found" in error_msg.lower():
                    raise HTTPException(
                        status_code=HttpStatusCode.NOT_FOUND.value,
                        detail=f"File not found: {share_name}/{file_path}",
                    )
                else:
                    raise HTTPException(
                        status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                        detail=f"Failed to download file: {error_msg}",
                    )

            # Get file content from response
            file_content = download_response.data.get("content")
            if not file_content:
                raise HTTPException(
                    status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                    detail="Downloaded file has no content",
                )

            # Stream the content in chunks
            return create_stream_record_response(
                self._stream_file_content(file_content),
                filename=record.record_name,
                mime_type=record.mime_type if record.mime_type else "application/octet-stream",
                fallback_filename=f"record_{record.id}"
            )

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(
                f"Error downloading file directly for record {record.id}: {e}",
                exc_info=True
            )
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail=f"Failed to stream file: {str(e)}",
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
                external_revision_id=current_etag,
                version=record.version + 1,
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
            if selected_shares:
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
            connection_string = auth_config.get("connectionString", "")
            if connection_string:
                extracted = cls._extract_account_name_from_connection_string(
                    connection_string
                )
                account_name = extracted or ""

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
