"""
Google Cloud Storage Connector

Connector for synchronizing data from Google Cloud Storage buckets. This connector
uses the native GCS API with service account authentication.
"""

import asyncio
import mimetypes
import uuid
from datetime import datetime, timezone
from itertools import accumulate
from logging import Logger
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote

from aiolimiter import AsyncLimiter
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    AppGroups,
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
    FilterOption,
    FilterOptionsResponse,
    FilterType,
    IndexingFilterKey,
    MultiselectOperator,
    OptionSourceType,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.sources.google_cloud_storage.common.apps import GCSApp
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
from app.sources.client.gcs.gcs import GCSClient
from app.sources.external.gcs.gcs import GCSDataSource
from app.utils.streaming import create_stream_record_response, stream_content
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# Default connector endpoint for signed URL generation
DEFAULT_CONNECTOR_ENDPOINT = "http://localhost:8000"

# Base URL for Google Cloud Console
GCS_CONSOLE_BASE_URL = "https://console.cloud.google.com/storage/browser"


def get_file_extension(key: str) -> Optional[str]:
    """Extracts the extension from a GCS key."""
    if "." in key:
        parts = key.split(".")
        if len(parts) > 1:
            return parts[-1].lower()
    return None


def get_parent_path_from_key(key: str) -> Optional[str]:
    """Extracts the parent path from a GCS key (without leading slash).

    For a key like 'a/b/c/file.txt', returns 'a/b/c'
    For a key like 'a/b/c/', returns 'a/b'
    """
    if not key:
        return None
    # Remove leading slash and trailing slash (if present)
    normalized_key = key.lstrip("/").rstrip("/")
    if not normalized_key or "/" not in normalized_key:
        return None
    parent_path = "/".join(normalized_key.split("/")[:-1])
    return parent_path if parent_path else None


def get_folder_path_segments_from_key(key: str) -> List[str]:
    """Derives folder path segments from a GCS key for hierarchy creation.

    GCS, like S3, represents folders implicitly via object keys.
    For each file key (e.g. a/b/c/file.txt), this returns the folder
    path segments that must exist:

    Example:
        'a/b/c/file.txt' -> ['a', 'a/b', 'a/b/c']
        'file.txt'       -> []
    """
    if not key:
        return []

    normalized = key.lstrip("/").rstrip("/")
    if not normalized or "/" not in normalized:
        return []

    parts = normalized.split("/")
    # The last part is the file (or folder key), so we only want to accumulate the directory parts.
    if len(parts) <= 1:
        return []
    return list(accumulate(parts[:-1], lambda acc, part: f"{acc}/{part}"))


def get_mimetype_for_gcs(key: str, is_folder: bool = False) -> str:
    """Determines the correct MimeTypes string value for a GCS object."""
    if is_folder:
        return MimeTypes.FOLDER.value

    mime_type_str, _ = mimetypes.guess_type(key)
    if mime_type_str:
        try:
            return MimeTypes(mime_type_str).value
        except ValueError:
            return MimeTypes.BIN.value
    return MimeTypes.BIN.value


def parse_parent_external_id(parent_external_id: str) -> Tuple[str, Optional[str]]:
    """Parse parent_external_id to extract bucket_name and normalized path.

    Args:
        parent_external_id: External ID in format "bucket_name/path" or just "bucket_name"

    Returns:
        A tuple of (bucket_name, normalized_path) where normalized_path is None
        if parent_external_id contains only a bucket name.
    """
    if "/" in parent_external_id:
        parts = parent_external_id.split("/", 1)
        bucket_name = parts[0]
        path = parts[1]
        path = path.lstrip("/")
        if path and not path.endswith("/"):
            path = path + "/"
        return bucket_name, path
    else:
        bucket_name = parent_external_id
        return bucket_name, None


def get_parent_weburl_for_gcs(parent_external_id: str) -> str:
    """Generate webUrl for a GCS directory based on parent external_id.

    Args:
        parent_external_id: External ID in format "bucket_name/path" or just "bucket_name"

    Returns:
        Console URL for the directory
    """
    bucket_name, path = parse_parent_external_id(parent_external_id)
    if path:
        return f"{GCS_CONSOLE_BASE_URL}/{bucket_name}/{path}"
    else:
        return f"{GCS_CONSOLE_BASE_URL}/{bucket_name}"


def get_parent_path_for_gcs(parent_external_id: str) -> Optional[str]:
    """Extract directory path from GCS parent external_id.

    Args:
        parent_external_id: External ID in format "bucket_name/path" or just "bucket_name"

    Returns:
        Directory path without bucket name prefix, or None for root directories
    """
    if "/" in parent_external_id:
        parts = parent_external_id.split("/", 1)
        directory_path = parts[1]
        if directory_path and not directory_path.endswith("/"):
            directory_path = directory_path + "/"
        return directory_path
    else:
        return None


class GCSDataSourceEntitiesProcessor(DataSourceEntitiesProcessor):
    """GCS processor that extends the base processor with GCS-specific placeholder record logic."""

    def __init__(
        self,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
    ) -> None:
        super().__init__(logger, data_store_provider, config_service)

    def _create_placeholder_parent_record(
        self,
        parent_external_id: str,
        parent_record_type: RecordType,
        record: Record,
    ) -> Record:
        """
        Create a placeholder parent record with GCS-specific weburl and path.
        """
        parent_record = super()._create_placeholder_parent_record(
            parent_external_id, parent_record_type, record
        )

        if parent_record_type == RecordType.FILE and isinstance(parent_record, FileRecord):
            weburl = get_parent_weburl_for_gcs(parent_external_id)
            path = get_parent_path_for_gcs(parent_external_id)
            parent_record.weburl = weburl
            parent_record.path = path
            parent_record.is_internal = True
            parent_record.hide_weburl = True

        return parent_record


@ConnectorBuilder("GCS")\
    .in_group(AppGroups.GOOGLE_CLOUD.value)\
    .with_description("Sync files and folders from Google Cloud Storage")\
    .with_categories(["Storage"])\
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.ACCESS_KEY).fields([
            AuthField(
                name="serviceAccountJson",
                display_name="Service Account JSON",
                placeholder="Click to upload service account JSON file",
                description="Upload your Service Account JSON key file from Google Cloud Console. Go to IAM & Admin > Service Accounts > Keys to create one.",
                field_type="FILE",
                is_secret=True
            ),
        ])
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/gcs.svg")
        .add_documentation_link(DocumentationLink(
            "GCS Service Account Setup",
            "https://cloud.google.com/iam/docs/service-accounts-create",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/gcs/gcs',
            'pipeshub'
        ))
        .add_filter_field(FilterField(
            name="buckets",
            display_name="Bucket Names",
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            description="Select specific GCS buckets to sync",
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
class GCSConnector(BaseConnector):
    """
    Connector for synchronizing data from Google Cloud Storage buckets.
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
            app=GCSApp(connector_id),
            logger=logger,
            data_entities_processor=data_entities_processor,
            data_store_provider=data_store_provider,
            config_service=config_service,
            connector_id=connector_id,
        )

        self.connector_name = Connectors.GCS
        self.connector_id = connector_id
        self.filter_key = "gcs"

        # Initialize sync point for tracking record changes
        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_id=self.connector_id,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider,
            )

        self.record_sync_point = _create_sync_point(SyncDataPointType.RECORDS)

        self.data_source: Optional[GCSDataSource] = None
        self.batch_size = 100
        self.rate_limiter = AsyncLimiter(50, 1)  # 50 requests per second
        self.bucket_name: Optional[str] = None
        self.connector_scope: Optional[str] = None
        self.created_by: Optional[str] = None
        self.project_id: Optional[str] = None

        # Initialize filter collections
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

    def get_app_users(self, users: List[User]) -> List[AppUser]:
        """Convert User objects to AppUser objects for GCS connector."""
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
        """Initializes the GCS client using credentials from the config service."""
        config = await self.config_service.get_config(
            f"/services/connectors/{self.connector_id}/config"
        )
        if not config:
            self.logger.error("GCS configuration not found.")
            return False

        auth_config = config.get("auth", {})
        service_account_json = auth_config.get("serviceAccountJson")
        self.bucket_name = auth_config.get("bucket")

        if not service_account_json:
            self.logger.error("GCS service account JSON not found in configuration.")
            return False

        # Get connector scope
        self.connector_scope = ConnectorScope.PERSONAL.value
        self.created_by = config.get("created_by")

        scope_from_config = config.get("scope")
        if scope_from_config:
            self.connector_scope = scope_from_config

        try:
            client = await GCSClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id,
            )
            self.data_source = GCSDataSource(client)
            self.project_id = client.get_project_id()

            # Load connector filters
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "gcs", self.connector_id, self.logger
            )

            self.logger.info("GCS client initialized successfully.")
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize GCS client: {e}", exc_info=True)
            return False

    def _generate_web_url(self, bucket_name: str, normalized_key: str) -> str:
        """Generate the web URL for a GCS object."""
        # URL encode the key for the console URL
        return f"{GCS_CONSOLE_BASE_URL}/{bucket_name}/{normalized_key}"

    def _generate_parent_web_url(self, parent_external_id: str) -> str:
        """Generate the web URL for a GCS parent folder/directory."""
        return get_parent_weburl_for_gcs(parent_external_id)

    async def run_sync(self) -> None:
        """Runs a full synchronization from buckets."""
        try:
            self.logger.info("Starting GCS full sync.")

            if not self.data_source:
                raise ConnectionError("GCS connector is not initialized.")

            # Reload sync and indexing filters to pick up configuration changes
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, self.filter_key, self.connector_id, self.logger
            )

            all_active_users = await self.data_entities_processor.get_all_active_users()
            app_users = self.get_app_users(all_active_users)
            await self.data_entities_processor.on_new_app_users(app_users)

            # Get sync filters
            sync_filters = self.sync_filters if hasattr(self, 'sync_filters') and self.sync_filters else FilterCollection()

            # Get bucket filter if specified
            bucket_filter = sync_filters.get("buckets")
            selected_buckets = bucket_filter.value if bucket_filter and bucket_filter.value else []

            # List all buckets or use configured bucket
            buckets_to_sync = []
            if self.bucket_name:
                buckets_to_sync = [self.bucket_name]
                self.logger.info(f"Using configured bucket: {self.bucket_name}")
            elif selected_buckets:
                buckets_to_sync = selected_buckets
                self.logger.info(f"Using filtered buckets: {buckets_to_sync}")
            else:
                self.logger.info("Listing all buckets...")
                buckets_response = await self.data_source.list_buckets()
                if not buckets_response.success:
                    self.logger.error(f"Failed to list buckets: {buckets_response.error}")
                    return

                buckets_data = buckets_response.data
                if buckets_data and "Buckets" in buckets_data:
                    buckets_to_sync = [
                        bucket.get("name") for bucket in buckets_data["Buckets"]
                    ]
                    self.logger.info(f"Found {len(buckets_to_sync)} bucket(s) to sync")
                else:
                    self.logger.warning("No buckets found")
                    return

            # Create record groups for buckets first
            await self._create_record_groups_for_buckets(buckets_to_sync)

            # Sync each bucket
            for bucket_name in buckets_to_sync:
                if not bucket_name:
                    continue
                try:
                    self.logger.info(f"Syncing bucket: {bucket_name}")
                    await self._sync_bucket(bucket_name)
                except Exception as e:
                    self.logger.error(
                        f"Error syncing bucket {bucket_name}: {e}", exc_info=True
                    )
                    continue

            self.logger.info("GCS full sync completed.")
        except Exception as ex:
            self.logger.error(f"❌ Error in GCS connector run: {ex}", exc_info=True)
            raise

    async def _create_record_groups_for_buckets(self, bucket_names: List[str]) -> None:
        """Create record groups for buckets with appropriate permissions.

        Processes buckets one at a time to avoid database lock contention issues.
        Includes retry logic with exponential backoff for transient errors.
        """
        if not bucket_names:
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
                self.logger.warning(f"Could not get user for created_by {self.created_by}: {e}")

        successful_count = 0
        failed_buckets = []

        # Process each bucket individually to avoid lock contention
        for bucket_name in bucket_names:
            if not bucket_name:
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
                if creator_email:
                    permissions.append(
                        Permission(
                            type=PermissionType.OWNER,
                            entity_type=EntityType.USER,
                            email=creator_email,
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
                name=bucket_name,
                external_group_id=bucket_name,
                group_type=RecordGroupType.BUCKET,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                description=f"GCS Bucket: {bucket_name}",
            )

            # Process each record group with retry logic
            max_retries = 3
            base_delay = 1.0  # seconds

            for attempt in range(max_retries):
                try:
                    await self.data_entities_processor.on_new_record_groups([(record_group, permissions)])
                    successful_count += 1
                    break
                except Exception as e:
                    error_str = str(e)
                    is_lock_timeout = "timeout waiting to lock" in error_str.lower() or "status=409" in error_str

                    if is_lock_timeout and attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)  # Exponential backoff
                        self.logger.warning(
                            f"Lock timeout for bucket {bucket_name}, retrying in {delay}s "
                            f"(attempt {attempt + 1}/{max_retries})"
                        )
                        await asyncio.sleep(delay)
                    else:
                        self.logger.error(
                            f"Failed to create record group for bucket {bucket_name} "
                            f"after {attempt + 1} attempts: {e}"
                        )
                        failed_buckets.append(bucket_name)
                        break

        if successful_count > 0:
            self.logger.info(f"Created {successful_count} record group(s) for buckets")

        if failed_buckets:
            self.logger.warning(f"Failed to create record groups for {len(failed_buckets)} bucket(s): {failed_buckets}")

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

    async def _process_records_with_retry(
        self,
        records_with_permissions: List[Tuple[FileRecord, List[Permission]]],
        max_retries: int = 3,
        base_delay: float = 1.0
    ) -> None:
        """Process records with retry logic for transient database errors.

        Args:
            records_with_permissions: List of (record, permissions) tuples to process
            max_retries: Maximum number of retry attempts
            base_delay: Base delay in seconds for exponential backoff
        """
        for attempt in range(max_retries):
            try:
                await self.data_entities_processor.on_new_records(records_with_permissions)
                return
            except Exception as e:
                error_str = str(e)
                is_lock_timeout = "timeout waiting to lock" in error_str.lower() or "status=409" in error_str

                if is_lock_timeout and attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)  # Exponential backoff
                    self.logger.warning(
                        f"Lock timeout processing {len(records_with_permissions)} records, "
                        f"retrying in {delay}s (attempt {attempt + 1}/{max_retries})"
                    )
                    await asyncio.sleep(delay)
                else:
                    self.logger.error(
                        f"Failed to process {len(records_with_permissions)} records "
                        f"after {attempt + 1} attempts: {e}"
                    )
                    raise

    def _pass_date_filters(
        self,
        obj: Dict,
        modified_after_ms: Optional[int] = None,
        modified_before_ms: Optional[int] = None,
        created_after_ms: Optional[int] = None,
        created_before_ms: Optional[int] = None
    ) -> bool:
        """Returns True if GCS object PASSES date filters (should be kept)."""
        key = obj.get("Key", "")
        is_folder = key.endswith("/")
        if is_folder:
            return True

        if not any([modified_after_ms, modified_before_ms, created_after_ms, created_before_ms]):
            return True

        last_modified = obj.get("LastModified")
        if not last_modified:
            return True

        # Parse ISO format timestamp
        if isinstance(last_modified, str):
            try:
                obj_dt = datetime.fromisoformat(last_modified.replace('Z', '+00:00'))
                obj_timestamp_ms = int(obj_dt.timestamp() * 1000)
            except ValueError:
                return True
        else:
            return True

        if modified_after_ms and obj_timestamp_ms < modified_after_ms:
            self.logger.debug(f"Skipping {key}: modified {obj_timestamp_ms} before cutoff {modified_after_ms}")
            return False
        if modified_before_ms and obj_timestamp_ms > modified_before_ms:
            self.logger.debug(f"Skipping {key}: modified {obj_timestamp_ms} after cutoff {modified_before_ms}")
            return False

        # For GCS, we can also check TimeCreated
        time_created = obj.get("TimeCreated")
        if time_created and isinstance(time_created, str):
            try:
                created_dt = datetime.fromisoformat(time_created.replace('Z', '+00:00'))
                created_timestamp_ms = int(created_dt.timestamp() * 1000)

                if created_after_ms and created_timestamp_ms < created_after_ms:
                    self.logger.debug(f"Skipping {key}: created {created_timestamp_ms} before cutoff {created_after_ms}")
                    return False
                if created_before_ms and created_timestamp_ms > created_before_ms:
                    self.logger.debug(f"Skipping {key}: created {created_timestamp_ms} after cutoff {created_before_ms}")
                    return False
            except ValueError:
                pass

        return True

    async def _sync_bucket(self, bucket_name: str) -> None:
        """Sync objects from a specific bucket with pagination support and incremental sync."""
        if not self.data_source:
            raise ConnectionError("GCS connector is not initialized.")

        sync_filters = self.sync_filters if hasattr(self, 'sync_filters') and self.sync_filters else FilterCollection()

        file_extensions_filter = sync_filters.get("file_extensions")
        allowed_extensions = []
        if file_extensions_filter and not file_extensions_filter.is_empty():
            filter_value = file_extensions_filter.value
            if isinstance(filter_value, list):
                allowed_extensions = [ext.lower().lstrip('.') for ext in filter_value if ext]
            elif isinstance(filter_value, str):
                allowed_extensions = [filter_value.lower().lstrip('.')]

        if allowed_extensions:
            self.logger.info(
                f"File extensions filter active for bucket {bucket_name}: {allowed_extensions}"
            )

        modified_after_ms, modified_before_ms, created_after_ms, created_before_ms = self._get_date_filters()

        sync_point_key = generate_record_sync_point_key(
            RecordType.FILE.value, "bucket", bucket_name
        )
        sync_point = await self.record_sync_point.read_sync_point(sync_point_key)
        page_token = sync_point.get("page_token") if sync_point else None
        last_sync_time = sync_point.get("last_sync_time") if sync_point else None

        if last_sync_time:
            user_modified_after_ms = modified_after_ms
            if user_modified_after_ms:
                modified_after_ms = max(user_modified_after_ms, last_sync_time)
            else:
                modified_after_ms = last_sync_time

        batch_records = []
        has_more = True
        max_timestamp = last_sync_time if last_sync_time else 0

        while has_more:
            try:
                async with self.rate_limiter:
                    response = await self.data_source.list_blobs(
                        bucket_name=bucket_name,
                        max_results=self.batch_size,
                        page_token=page_token,
                    )

                    if not response.success:
                        error_msg = response.error or "Unknown error"
                        if any(
                            phrase in str(error_msg)
                            for phrase in ["403", "denied", "permission", "PermissionDenied"]
                        ):
                            self.logger.error(
                                f"Access denied when listing objects in bucket {bucket_name}: {error_msg}."
                            )
                            self.logger.error(
                                "Please verify that the service account has at least the following roles:\n"
                                f"  - storage.objects.list on bucket '{bucket_name}'\n"
                                f"  - storage.buckets.get on project '{self.project_id}' (if applicable)\n"
                                "Also check if there is a bucket-level IAM or ACL policy blocking access."
                            )
                        else:
                            self.logger.error(
                                f"Failed to list objects in bucket {bucket_name}: {error_msg}"
                            )
                        has_more = False
                        continue

                    objects_data = response.data
                    if not objects_data or "Contents" not in objects_data:
                        self.logger.info(f"No objects found in bucket {bucket_name}")
                        has_more = False
                        continue

                    objects = objects_data["Contents"]
                    self.logger.info(
                        f"Processing {len(objects)} objects from bucket {bucket_name}"
                    )

                    for obj in objects:
                        try:
                            key = obj.get("Key", "")

                            is_folder = key.endswith("/")

                            if not is_folder and allowed_extensions:
                                ext = get_file_extension(key)
                                if not ext:
                                    self.logger.debug(
                                        f"Skipping {key}: no file extension found"
                                    )
                                    continue
                                if ext not in allowed_extensions:
                                    self.logger.debug(
                                        f"Skipping {key}: extension '{ext}' not in allowed extensions"
                                    )
                                    continue

                            if not self._pass_date_filters(
                                obj, modified_after_ms, modified_before_ms, created_after_ms, created_before_ms
                            ):
                                continue

                            # Track max timestamp for incremental sync
                            if not is_folder:
                                last_modified = obj.get("LastModified")
                                if last_modified and isinstance(last_modified, str):
                                    try:
                                        obj_dt = datetime.fromisoformat(last_modified.replace('Z', '+00:00'))
                                        obj_timestamp_ms = int(obj_dt.timestamp() * 1000)
                                        max_timestamp = max(max_timestamp, obj_timestamp_ms)
                                    except ValueError:
                                        pass

                            # Ensure folder hierarchy exists from file path (GCS has no real folder objects)
                            if not is_folder:
                                path_segments = get_folder_path_segments_from_key(key)
                                if path_segments:
                                    await self._ensure_parent_folders_exist(bucket_name, path_segments)

                            record, permissions = await self._process_gcs_object(
                                obj, bucket_name
                            )
                            if record:
                                batch_records.append((record, permissions))

                                if len(batch_records) >= self.batch_size:
                                    await self._process_records_with_retry(batch_records)
                                    batch_records = []
                        except Exception as e:
                            self.logger.error(
                                f"Error processing object {obj.get('Key', 'unknown')}: {e}",
                                exc_info=True,
                            )
                            continue

                    has_more = objects_data.get("IsTruncated", False)
                    page_token = objects_data.get("NextContinuationToken")

                    if page_token:
                        await self.record_sync_point.update_sync_point(
                            sync_point_key, {"page_token": page_token}
                        )

            except Exception as e:
                self.logger.error(
                    f"Error during bucket sync for {bucket_name}: {e}", exc_info=True
                )
                has_more = False

        if batch_records:
            await self._process_records_with_retry(batch_records)

        if max_timestamp > 0:
            await self.record_sync_point.update_sync_point(
                sync_point_key, {
                    "last_sync_time": max_timestamp,
                    "page_token": None
                }
            )

    async def _remove_old_parent_relationship(
        self, record_id: str, tx_store: "TransactionStore"
    ) -> None:
        """Remove old PARENT_CHILD relationships for a record."""
        try:
            deleted_count = await tx_store.delete_parent_child_edges_to(record_id=record_id)
            if deleted_count > 0:
                self.logger.info(f"Removed {deleted_count} old parent relationship(s) for record {record_id}")
        except Exception as e:
            self.logger.warning(f"Error in _remove_old_parent_relationship: {e}")

    async def _ensure_parent_folders_exist(
        self, bucket_name: str, path_segments: List[str]
    ) -> None:
        """Ensure folder records exist for each path segment (root to leaf). No duplicates.

        GCS, like S3, represents folders implicitly via object keys.
        For each segment (e.g. 'a', 'a/b', 'a/b/c'), create a folder record if one does not
        already exist (by external_id = bucket_name/segment). Process in order so parent
        exists before child.
        """
        if not path_segments:
            return

        timestamp_ms = get_epoch_timestamp_in_ms()
        external_ids = [f"{bucket_name}/{segment}" for segment in path_segments]

        async with self.data_store_provider.transaction() as tx_store:
            results = await asyncio.gather(
                *[
                    tx_store.get_record_by_external_id(
                        connector_id=self.connector_id, external_id=eid
                    )
                    for eid in external_ids
                ]
            )

        existing_external_ids = {
            eid for eid, rec in zip(external_ids, results) if rec is not None
        }

        for i, segment in enumerate(path_segments):
            external_id = f"{bucket_name}/{segment}"
            if external_id in existing_external_ids:
                continue

            # Root folder: first segment has no parent. Others: parent is previous segment.
            parent_external_id = (
                f"{bucket_name}/{path_segments[i - 1]}" if i > 0 else None
            )
            parent_record_type = RecordType.FILE if parent_external_id else None
            record_name = segment.split("/")[-1] if segment else segment
            web_url = self._generate_web_url(bucket_name, segment + "/")

            folder_record = FileRecord(
                id=str(uuid.uuid4()),
                record_name=record_name,
                record_type=RecordType.FILE,
                record_group_type=RecordGroupType.BUCKET.value,
                external_record_group_id=bucket_name,
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
                md5_hash=None,
                crc32_hash=None,
            )

            permissions = await self._create_gcs_permissions(bucket_name, segment + "/")
            await self._process_records_with_retry([(folder_record, permissions)])

    def _get_gcs_revision_id(self, obj: Dict) -> str:
        """
        Determines a stable revision ID for a GCS object.

        It prioritizes the MD5 hash as a content fingerprint, which is stable
        across renames/copies. If not available, it falls back to the
        generation/metageneration numbers.

        Args:
            obj: GCS object metadata dictionary

        Returns:
            Revision ID string (Md5Hash, "generation:metageneration", or "")
        """
        md5_hash = obj.get("Md5Hash")
        if md5_hash:
            return md5_hash

        generation = obj.get("Generation")
        if generation:
            metageneration = obj.get("Metageneration")
            return f"{generation}:{metageneration}" if metageneration else str(generation)

        return ""

    async def _process_gcs_object(
        self, obj: Dict, bucket_name: str
    ) -> Tuple[Optional[FileRecord], List[Permission]]:
        """Process a single GCS object and convert it to a FileRecord.

        Logic mirrors S3:
        1. Extract path and revision fingerprint
        2. Try lookup by path (externalRecordId) - PRIMARY
           ├─ Found → Compare revisions
           │   ├─ Different → Content change → Update record
           │   └─ Same → Skip (no changes)
           └─ Not Found → Try lookup by revision (externalRevisionId) - FALLBACK
               ├─ Found → Move/rename detected
               │   ├─ Remove old parent relationship
               │   └─ Update record
               └─ Not Found → New file → Create new record
        """
        try:
            key = obj.get("Key", "")
            if not key:
                return None, []

            is_folder = key.endswith("/")
            is_file = not is_folder

            normalized_key = key.lstrip("/")
            if not normalized_key:
                return None, []

            # Parse timestamps
            last_modified = obj.get("LastModified")
            if last_modified and isinstance(last_modified, str):
                try:
                    obj_dt = datetime.fromisoformat(last_modified.replace('Z', '+00:00'))
                    timestamp_ms = int(obj_dt.timestamp() * 1000)
                except ValueError:
                    timestamp_ms = get_epoch_timestamp_in_ms()
            else:
                timestamp_ms = get_epoch_timestamp_in_ms()

            # Parse created time
            time_created = obj.get("TimeCreated")
            if time_created and isinstance(time_created, str):
                try:
                    created_dt = datetime.fromisoformat(time_created.replace('Z', '+00:00'))
                    created_timestamp_ms = int(created_dt.timestamp() * 1000)
                except ValueError:
                    created_timestamp_ms = timestamp_ms
            else:
                created_timestamp_ms = timestamp_ms

            external_record_id = f"{bucket_name}/{normalized_key}"

            # Use a stable "content fingerprint" first (similar to S3 ETag usage).
            # - `Md5Hash` changes when content changes and is stable across renames/copies when content is identical
            # - fall back to generation/metageneration when no md5 is available (e.g., composite objects)
            current_revision_id = self._get_gcs_revision_id(obj)

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
                        f"Skipping {normalized_key}: externalRecordId and externalRevisionId unchanged"
                    )
                    return None, []

                # Content changed or missing revision - sync properly from GCS
                if current_revision_id and stored_revision and current_revision_id != stored_revision:
                    self.logger.info(
                        f"Content change detected: {normalized_key} - externalRevisionId changed from {stored_revision} to {current_revision_id}"
                    )
                elif not current_revision_id or not stored_revision:
                    if not current_revision_id:
                        self.logger.warning(
                            f"Current revision missing for {normalized_key}, processing record"
                        )
                    if not stored_revision:
                        self.logger.debug(
                            f"Stored revision missing for {normalized_key}, processing record"
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
                        f"Move/rename detected: {normalized_key} - file moved from {existing_record.external_record_id} to {external_record_id}"
                    )
                else:
                    self.logger.debug(f"New document: {normalized_key}")
            else:
                self.logger.debug(f"New document: {normalized_key} (no revision available)")

            # Prepare record data: all items are RecordType.FILE; folders have is_file=False
            record_type = RecordType.FILE
            extension = get_file_extension(normalized_key) if is_file else None
            mime_type = obj.get("ContentType") or get_mimetype_for_gcs(normalized_key, is_folder)

            parent_path = get_parent_path_from_key(normalized_key)
            parent_external_id = f"{bucket_name}/{parent_path}" if parent_path else None
            parent_record_type = RecordType.FILE if parent_path else None

            web_url = self._generate_web_url(bucket_name, normalized_key)

            record_id = existing_record.id if existing_record else str(uuid.uuid4())
            record_name = normalized_key.rstrip("/").split("/")[-1] or normalized_key.rstrip("/")

            # For moves/renames, remove old parent relationship
            if is_move and existing_record:
                async with self.data_store_provider.transaction() as tx_store:
                    await self._remove_old_parent_relationship(record_id, tx_store)

            if not existing_record:
                version = 0
            else:
                version = existing_record.version + 1

            file_record = FileRecord(
                id=record_id,
                record_name=record_name,
                record_type=record_type,
                record_group_type=RecordGroupType.BUCKET.value,
                external_record_group_id=bucket_name,
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
                size_in_bytes=obj.get("Size", 0) if is_file else 0,
                is_file=is_file,
                extension=extension,
                path=normalized_key,
                mime_type=mime_type,
                md5_hash=obj.get("Md5Hash"),
                crc32_hash=obj.get("Crc32c"),
            )

            if hasattr(self, 'indexing_filters') and self.indexing_filters:
                if not self.indexing_filters.is_enabled(IndexingFilterKey.FILES, default=True):
                    file_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

            permissions = await self._create_gcs_permissions(bucket_name, key)

            return file_record, permissions

        except Exception as e:
            self.logger.error(f"Error processing GCS object: {e}", exc_info=True)
            return None, []

    async def _create_gcs_permissions(
        self, bucket_name: str, key: str
    ) -> List[Permission]:
        """Create permissions for a GCS object based on connector scope."""
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
                                        external_id=self.created_by
                                    )
                                )
                    except Exception as e:
                        self.logger.warning(f"Could not get user for created_by {self.created_by}: {e}")

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
            self.logger.warning(f"Error creating permissions for {key}: {e}")
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
            response = await self.data_source.list_buckets()
            if response.success:
                self.logger.info("GCS connection test successful.")
                return True
            else:
                self.logger.error(f"GCS connection test failed: {response.error}")
                return False
        except Exception as e:
            self.logger.error(f"GCS connection test failed: {e}", exc_info=True)
            return False

    async def get_signed_url(self, record: Record) -> Optional[str]:
        """Generate a signed URL for a GCS object."""
        if not self.data_source:
            return None
        try:
            bucket_name = record.external_record_group_id
            if not bucket_name:
                self.logger.warning(f"No bucket name found for record: {record.id}")
                return None

            external_record_id = record.external_record_id
            if not external_record_id:
                self.logger.warning(f"No external_record_id found for record: {record.id}")
                return None

            if external_record_id.startswith(f"{bucket_name}/"):
                key = external_record_id[len(f"{bucket_name}/"):]
            else:
                key = external_record_id.lstrip("/")

            key = unquote(key)

            self.logger.debug(
                f"Generating signed URL - Bucket: {bucket_name}, "
                f"Key: {key}, Record ID: {record.id}"
            )

            response = await self.data_source.generate_signed_url(
                bucket_name=bucket_name,
                blob_name=key,
                expiration=86400,  # 24 hours
            )

            if response.success and response.data:
                return response.data.get("url")
            else:
                error_msg = response.error or "Unknown error"
                error_str = str(error_msg)
                if any(
                    phrase in error_str
                    for phrase in ["403", "denied", "permission", "PermissionDenied", "Forbidden"]
                ):
                    self.logger.error(
                        f"❌ ACCESS DENIED: Failed to generate signed URL. "
                        f"Error: {error_msg} | Bucket: {bucket_name} | Key: {key} | Record ID: {record.id}"
                    )
                elif any(
                    phrase in error_str
                    for phrase in ["404", "NotFound", "NoSuchKey", "not found"]
                ):
                    self.logger.error(
                        f"❌ KEY NOT FOUND: The object may not exist or the key is incorrect. "
                        f"Error: {error_msg} | Bucket: {bucket_name} | Key: {key} | Record ID: {record.id}"
                    )
                else:
                    self.logger.error(
                        f"❌ FAILED: Failed to generate signed URL. "
                        f"Error: {error_msg} | Bucket: {bucket_name} | Key: {key} | Record ID: {record.id}"
                    )
                return None
        except Exception as e:
            self.logger.error(
                f"Error generating signed URL for record {record.id}: {e}"
            )
            return None

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Stream GCS object content."""
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
        self.logger.info("Cleaning up GCS connector resources.")
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
        if filter_key == "buckets":
            return await self._get_bucket_options(page, limit, search)
        else:
            raise ValueError(f"Unsupported filter key: {filter_key}")

    async def _get_bucket_options(
        self,
        page: int,
        limit: int,
        search: Optional[str]
    ) -> FilterOptionsResponse:
        """Get list of available buckets."""
        try:
            if not self.data_source:
                return FilterOptionsResponse(
                    success=False,
                    options=[],
                    page=page,
                    limit=limit,
                    has_more=False,
                    message="GCS connector is not initialized"
                )

            response = await self.data_source.list_buckets()
            if not response.success:
                return FilterOptionsResponse(
                    success=False,
                    options=[],
                    page=page,
                    limit=limit,
                    has_more=False,
                    message=f"Failed to list buckets: {response.error}"
                )

            buckets_data = response.data
            if not buckets_data or "Buckets" not in buckets_data:
                return FilterOptionsResponse(
                    success=True,
                    options=[],
                    page=page,
                    limit=limit,
                    has_more=False
                )

            all_buckets = [
                bucket.get("name") for bucket in buckets_data["Buckets"]
                if bucket.get("name")
            ]

            if search:
                search_lower = search.lower()
                all_buckets = [
                    bucket for bucket in all_buckets
                    if search_lower in bucket.lower()
                ]

            start_idx = (page - 1) * limit
            end_idx = start_idx + limit
            paginated_buckets = all_buckets[start_idx:end_idx]
            has_more = end_idx < len(all_buckets)

            options = [
                FilterOption(id=bucket, label=bucket)
                for bucket in paginated_buckets
            ]

            return FilterOptionsResponse(
                success=True,
                options=options,
                page=page,
                limit=limit,
                has_more=has_more
            )

        except Exception as e:
            self.logger.error(f"Error getting bucket options: {e}", exc_info=True)
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

            self.logger.info(f"Starting reindex for {len(record_results)} GCS records")

            if not self.data_source:
                self.logger.error("GCS connector is not initialized.")
                raise Exception("GCS connector is not initialized.")

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
            self.logger.error(f"Error during GCS reindex: {e}", exc_info=True)
            raise

    async def _check_and_fetch_updated_record(
        self, org_id: str, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Check if record has been updated at source and fetch updated data."""
        try:
            bucket_name = record.external_record_group_id
            external_record_id = record.external_record_id

            if not bucket_name or not external_record_id:
                self.logger.warning(f"Missing bucket or external_record_id for record {record.id}")
                return None

            if external_record_id.startswith(f"{bucket_name}/"):
                normalized_key = external_record_id[len(f"{bucket_name}/"):]
            else:
                normalized_key = external_record_id.lstrip("/")

            if not normalized_key:
                self.logger.warning(f"Invalid key for record {record.id}")
                return None

            response = await self.data_source.head_blob(
                bucket_name=bucket_name,
                blob_name=normalized_key
            )

            if not response.success:
                self.logger.warning(f"Object {normalized_key} not found in bucket {bucket_name}")
                return None

            obj_metadata = response.data
            if not obj_metadata:
                return None

            # Check revision ID
            current_revision_id = self._get_gcs_revision_id(obj_metadata)
            stored_revision = record.external_revision_id

            if current_revision_id and stored_revision and current_revision_id == stored_revision:
                self.logger.debug(f"Record {record.id}: external_revision_id unchanged ({current_revision_id})")
                return None

            if current_revision_id and stored_revision and current_revision_id != stored_revision:
                self.logger.debug(
                    f"Record {record.id}: external_revision_id changed from {stored_revision} to {current_revision_id}"
                )
            else:
                if not current_revision_id:
                    self.logger.warning(
                        f"Record {record.id}: current revision missing for {normalized_key}, processing record"
                    )
                if not stored_revision:
                    self.logger.debug(
                        f"Record {record.id}: stored revision missing for {normalized_key}, processing record"
                    )

            # Parse timestamps
            last_modified = obj_metadata.get("LastModified")
            if last_modified and isinstance(last_modified, str):
                try:
                    obj_dt = datetime.fromisoformat(last_modified.replace('Z', '+00:00'))
                    timestamp_ms = int(obj_dt.timestamp() * 1000)
                except ValueError:
                    timestamp_ms = get_epoch_timestamp_in_ms()
            else:
                timestamp_ms = get_epoch_timestamp_in_ms()

            is_folder = normalized_key.endswith("/")
            is_file = not is_folder

            extension = get_file_extension(normalized_key) if is_file else None
            mime_type = obj_metadata.get("ContentType") or get_mimetype_for_gcs(normalized_key, is_folder)

            parent_path = get_parent_path_from_key(normalized_key)
            parent_external_id = f"{bucket_name}/{parent_path}" if parent_path else None
            parent_record_type = RecordType.FILE if parent_path else None

            web_url = self._generate_web_url(bucket_name, normalized_key)


            record_name = normalized_key.rstrip("/").split("/")[-1] or normalized_key.rstrip("/")

            updated_external_record_id = f"{bucket_name}/{normalized_key}"

            # All items are RecordType.FILE; folders have is_file=False
            updated_record = FileRecord(
                id=record.id,
                record_name=record_name,
                record_type=RecordType.FILE,
                record_group_type=RecordGroupType.BUCKET.value,
                external_record_group_id=bucket_name,
                external_record_id=updated_external_record_id,
                external_revision_id=current_revision_id,
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
                size_in_bytes=obj_metadata.get("ContentLength", 0) if is_file else 0,
                is_file=is_file,
                extension=extension,
                path=normalized_key,
                mime_type=mime_type,
                md5_hash=obj_metadata.get("Md5Hash"),
                crc32_hash=obj_metadata.get("Crc32c"),
            )

            if hasattr(self, 'indexing_filters') and self.indexing_filters:
                if not self.indexing_filters.is_enabled(IndexingFilterKey.FILES, default=True):
                    updated_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

            permissions = await self._create_gcs_permissions(bucket_name, normalized_key)

            return updated_record, permissions

        except Exception as e:
            self.logger.error(f"Error checking record {record.id} at source: {e}")
            return None

    async def run_incremental_sync(self) -> None:
        """Run an incremental synchronization from buckets."""
        try:
            self.logger.info("Starting GCS incremental sync.")

            if not self.data_source:
                raise ConnectionError("GCS connector is not initialized.")

            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, self.filter_key, self.connector_id, self.logger
            )

            sync_filters = self.sync_filters if hasattr(self, 'sync_filters') and self.sync_filters else FilterCollection()

            bucket_filter = sync_filters.get("buckets")
            selected_buckets = bucket_filter.value if bucket_filter and bucket_filter.value else []

            buckets_to_sync = []
            if self.bucket_name:
                buckets_to_sync = [self.bucket_name]
                self.logger.info(f"Using configured bucket: {self.bucket_name}")
            elif selected_buckets:
                buckets_to_sync = selected_buckets
                self.logger.info(f"Using filtered buckets: {buckets_to_sync}")
            else:
                buckets_response = await self.data_source.list_buckets()
                if buckets_response.success and buckets_response.data:
                    buckets_data = buckets_response.data
                    if "Buckets" in buckets_data:
                        buckets_to_sync = [
                            bucket.get("name") for bucket in buckets_data["Buckets"]
                        ]

            if not buckets_to_sync:
                self.logger.warning("No buckets to sync")
                return

            for bucket_name in buckets_to_sync:
                if not bucket_name:
                    continue
                try:
                    self.logger.info(f"Incremental sync for bucket: {bucket_name}")
                    await self._sync_bucket(bucket_name)
                except Exception as e:
                    self.logger.error(
                        f"Error in incremental sync for bucket {bucket_name}: {e}", exc_info=True
                    )
                    continue

            self.logger.info("GCS incremental sync completed.")
        except Exception as ex:
            self.logger.error(f"❌ Error in GCS incremental sync: {ex}", exc_info=True)
            raise

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        **kwargs,
    ) -> "GCSConnector":
        """Factory method to create and initialize connector."""
        data_entities_processor = GCSDataSourceEntitiesProcessor(
            logger, data_store_provider, config_service
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
