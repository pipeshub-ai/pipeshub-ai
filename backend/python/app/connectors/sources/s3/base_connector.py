"""
Base connector for S3-compatible storage systems (AWS S3, MinIO, etc.)

This module provides shared functionality for connectors that work with S3-compatible
object storage systems. The base classes and utilities here are used by both
S3Connector and MinIOConnector to avoid code duplication.
"""

import mimetypes
import uuid
from abc import abstractmethod
from datetime import datetime, timezone
from logging import Logger
from typing import Any, Callable, Dict, List, Optional, Tuple

from aiolimiter import AsyncLimiter
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
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
from app.connectors.core.interfaces.connector.apps import App
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOption,
    FilterOptionsResponse,
    IndexingFilterKey,
    SyncFilterKey,
    load_connector_filters,
)
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
from app.utils.streaming import create_stream_record_response, stream_content
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# Default connector endpoint for signed URL generation
DEFAULT_CONNECTOR_ENDPOINT = "http://localhost:8000"


def get_file_extension(key: str) -> Optional[str]:
    """Extracts the extension from an S3 key."""
    if "." in key:
        parts = key.split(".")
        if len(parts) > 1:
            return parts[-1].lower()
    return None


def get_parent_path_from_key(key: str) -> Optional[str]:
    """Extracts the parent path from an S3 key (without leading slash).

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
    """Derives folder path segments from an S3 key for hierarchy creation.

    S3 list_objects only returns object keys (files); there are no separate folder objects.
    For each file key (e.g. a/b/c/file.txt), returns the folder path segments that must exist:
    e.g. ['a', 'a/b', 'a/b/c']. For a root-level key (e.g. file.txt) returns [].

    Args:
        key: S3 object key (may have leading/trailing slashes)

    Returns:
        List of cumulative path segments (folders only), root to leaf, no duplicates.
    """
    if not key:
        return []
    normalized = key.lstrip("/").rstrip("/")
    if not normalized or "/" not in normalized:
        return []
    parts = normalized.split("/")
    # Last part is the file (or folder key); segments are the folder path prefix
    segments = []
    for i in range(1, len(parts)):
        segments.append("/".join(parts[:i]))
    return segments


def get_parent_weburl_for_s3(parent_external_id: str, base_console_url: str = "https://s3.console.aws.amazon.com") -> str:
    """Generate webUrl for an S3/MinIO directory based on parent external_id.

    Args:
        parent_external_id: External ID in format "bucket_name/path" or just "bucket_name"
        base_console_url: Base URL for the console (different for S3 vs MinIO)

    Returns:
        Console URL for the directory
    """
    if "/" in parent_external_id:
        parts = parent_external_id.split("/", 1)
        bucket_name = parts[0]
        path = parts[1]
        path = path.lstrip("/")
        if path and not path.endswith("/"):
            path = path + "/"
        return f"{base_console_url}/s3/object/{bucket_name}?prefix={path}"
    else:
        bucket_name = parent_external_id
        return f"{base_console_url}/s3/buckets/{bucket_name}"


def get_parent_path_for_s3(parent_external_id: str) -> Optional[str]:
    """Extract directory path from S3 parent external_id.

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


def parse_parent_external_id(parent_external_id: str) -> Tuple[str, Optional[str]]:
    """Parse parent_external_id to extract bucket_name and normalized path.

    This helper method extracts the common parsing logic for parent_external_id
    used by both S3 and MinIO connectors to generate parent web URLs.

    Args:
        parent_external_id: External ID in format "bucket_name/path" or just "bucket_name"

    Returns:
        A tuple of (bucket_name, normalized_path) where normalized_path is None
        if parent_external_id contains only a bucket name, otherwise it's the
        path normalized (leading slashes removed, trailing slash added if not present).
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


def make_s3_composite_revision(bucket_name: str, normalized_key: str, raw_etag: Optional[str]) -> str:
    """Build external_revision_id for S3. Uses bucket/etag so move/rename can be detected
    (same etag in same bucket). When etag is missing, falls back to bucket/key for uniqueness."""
    if raw_etag:
        return f"{bucket_name}/{raw_etag}"
    return f"{bucket_name}/{normalized_key}|"


def get_mimetype_for_s3(key: str, is_folder: bool = False) -> str:
    """Determines the correct MimeTypes string value for an S3 object."""
    if is_folder:
        return MimeTypes.FOLDER.value

    mime_type_str, _ = mimetypes.guess_type(key)
    if mime_type_str:
        try:
            return MimeTypes(mime_type_str).value
        except ValueError:
            return MimeTypes.BIN.value
    return MimeTypes.BIN.value


class S3CompatibleDataSourceEntitiesProcessor(DataSourceEntitiesProcessor):
    """S3-compatible processor that extends the base processor with S3-specific placeholder record logic."""

    def __init__(
        self,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        base_console_url: str = "https://s3.console.aws.amazon.com",
        parent_url_generator: Optional[Callable[[str], str]] = None,
    ) -> None:
        super().__init__(logger, data_store_provider, config_service)
        self.base_console_url = base_console_url
        # Default to S3 format if no generator provided (for backward compatibility)
        self.parent_url_generator = parent_url_generator or (
            lambda parent_external_id: get_parent_weburl_for_s3(parent_external_id, base_console_url)
        )


class S3CompatibleBaseConnector(BaseConnector):
    """
    Base connector for S3-compatible storage systems.

    This abstract base class provides common functionality for syncing data from
    S3-compatible object storage (AWS S3, MinIO, etc.). Subclasses must implement
    the abstract methods to provide storage-specific behavior.
    """

    def __init__(
        self,
        app: App,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        connector_name: str,
        filter_key: str,
        base_console_url: str = "https://s3.console.aws.amazon.com",
    ) -> None:
        super().__init__(
            app,
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
        )

        self.connector_name = connector_name
        self.connector_id = connector_id
        self.filter_key = filter_key
        self.base_console_url = base_console_url

        # Initialize sync point for tracking record changes
        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_id=self.connector_id,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider,
            )

        self.record_sync_point = _create_sync_point(SyncDataPointType.RECORDS)

        self.data_source: Optional[Any] = None  # Will be S3DataSource or MinIODataSource
        self.batch_size = 100
        self.rate_limiter = AsyncLimiter(50, 1)  # 50 requests per second
        self.bucket_name: Optional[str] = None
        self.region: Optional[str] = None
        self.connector_scope: Optional[str] = None
        self.created_by: Optional[str] = None
        self.bucket_regions: Dict[str, str] = {}  # Cache for bucket-to-region mapping

        # Initialize filter collections
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

    def get_app_users(self, users: List[User]) -> List[AppUser]:
        """Convert User objects to AppUser objects for S3-compatible connectors."""
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

    @abstractmethod
    async def init(self) -> bool:
        """Initialize the connector. Must be implemented by subclasses."""
        pass

    @abstractmethod
    async def _build_data_source(self) -> object:
        """Build the data source (S3DataSource or MinIODataSource). Must be implemented by subclasses."""
        pass

    @abstractmethod
    def _generate_web_url(self, bucket_name: str, normalized_key: str) -> str:
        """Generate the web URL for an object. Must be implemented by subclasses."""
        pass

    @abstractmethod
    def _generate_parent_web_url(self, parent_external_id: str) -> str:
        """Generate the web URL for a parent folder/directory. Must be implemented by subclasses."""
        pass

    async def run_sync(self) -> None:
        """Runs a full synchronization from buckets."""
        try:
            self.logger.info(f"Starting {self.connector_name} full sync.")

            if not self.data_source:
                raise ConnectionError(f"{self.connector_name} connector is not initialized.")

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
                        bucket.get("Name") for bucket in buckets_data["Buckets"]
                    ]
                    self.logger.info(f"Found {len(buckets_to_sync)} bucket(s) to sync")
                else:
                    self.logger.warning("No buckets found")
                    return

            # Fetch and cache regions for all buckets
            self.logger.info(f"Fetching regions for {len(buckets_to_sync)} bucket(s)...")
            for bucket_name in buckets_to_sync:
                if bucket_name:
                    await self._get_bucket_region(bucket_name)

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

            self.logger.info(f"{self.connector_name} full sync completed.")
        except Exception as ex:
            self.logger.error(f"❌ Error in {self.connector_name} connector run: {ex}", exc_info=True)
            raise

    async def _create_record_groups_for_buckets(self, bucket_names: List[str]) -> None:
        """Create or upsert record groups for buckets with appropriate permissions.
        Always processes all buckets so that edges (e.g. recordGroup->app) are
        re-created after full sync when only edges were deleted.
        """
        if not bucket_names:
            return

        record_groups = []
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

            record_group = RecordGroup(
                name=bucket_name,
                external_group_id=bucket_name,
                group_type=RecordGroupType.BUCKET,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                description=f"{self.connector_name} Bucket: {bucket_name}",
            )
            record_groups.append((record_group, permissions))

        if record_groups:
            await self.data_entities_processor.on_new_record_groups(record_groups)
            self.logger.info(f"Created {len(record_groups)} record group(s) for buckets")

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
        obj: Dict,
        modified_after_ms: Optional[int] = None,
        modified_before_ms: Optional[int] = None,
        created_after_ms: Optional[int] = None,
        created_before_ms: Optional[int] = None
    ) -> bool:
        """Returns True if S3 object PASSES date filters (should be kept)."""
        key = obj.get("Key", "")
        is_folder = key.endswith("/")
        if is_folder:
            return True

        if not any([modified_after_ms, modified_before_ms, created_after_ms, created_before_ms]):
            return True

        last_modified = obj.get("LastModified")
        if not last_modified:
            return True

        if isinstance(last_modified, datetime):
            obj_timestamp_ms = int(last_modified.timestamp() * 1000)
        else:
            return True

        if modified_after_ms and obj_timestamp_ms < modified_after_ms:
            self.logger.debug(f"Skipping {key}: modified {obj_timestamp_ms} before cutoff {modified_after_ms}")
            return False
        if modified_before_ms and obj_timestamp_ms > modified_before_ms:
            self.logger.debug(f"Skipping {key}: modified {obj_timestamp_ms} after cutoff {modified_before_ms}")
            return False

        if created_after_ms and obj_timestamp_ms < created_after_ms:
            self.logger.debug(f"Skipping {key}: created {obj_timestamp_ms} before cutoff {created_after_ms}")
            return False
        if created_before_ms and obj_timestamp_ms > created_before_ms:
            self.logger.debug(f"Skipping {key}: created {obj_timestamp_ms} after cutoff {created_before_ms}")
            return False

        return True

    async def _get_bucket_region(self, bucket_name: str) -> str:
        """Get the region for a bucket, using cache if available."""
        if bucket_name in self.bucket_regions:
            return self.bucket_regions[bucket_name]

        if not self.data_source:
            self.logger.warning(f"Cannot fetch region for bucket {bucket_name}: data_source not initialized")
            return self.region or "us-east-1"

        try:
            response = await self.data_source.get_bucket_location(Bucket=bucket_name)
            if response.success and response.data:
                location = response.data.get("LocationConstraint")
                if location is None or location == "":
                    region = "us-east-1"
                else:
                    region = location
                self.bucket_regions[bucket_name] = region
                self.logger.debug(f"Cached region for bucket {bucket_name}: {region}")
                return region
            else:
                self.logger.warning(
                    f"Failed to get region for bucket {bucket_name}: {response.error}. "
                    f"Using configured region {self.region or 'us-east-1'}"
                )
        except Exception as e:
            self.logger.warning(
                f"Error fetching region for bucket {bucket_name}: {e}. "
                f"Using configured region {self.region or 'us-east-1'}"
            )

        fallback_region = self.region or "us-east-1"
        return fallback_region

    async def _sync_bucket(self, bucket_name: str) -> None:
        """Sync objects from a specific bucket with pagination support and incremental sync."""
        if not self.data_source:
            raise ConnectionError(f"{self.connector_name} connector is not initialized.")

        sync_filters = self.sync_filters if hasattr(self, 'sync_filters') and self.sync_filters else FilterCollection()

        file_extensions_filter = sync_filters.get("file_extensions")
        allowed_extensions = []
        if file_extensions_filter and not file_extensions_filter.is_empty():
            filter_value = file_extensions_filter.value
            if isinstance(filter_value, list):
                allowed_extensions = [ext.lower().lstrip('.') for ext in filter_value if ext]
            elif isinstance(filter_value, str):
                allowed_extensions = [filter_value.lower().lstrip('.')]
            else:
                self.logger.warning(
                    f"Unexpected file_extensions filter value type: {type(filter_value)}. "
                    f"Expected list or string, got {filter_value}"
                )

        if allowed_extensions:
            self.logger.info(
                f"File extensions filter active for bucket {bucket_name}: {allowed_extensions}"
            )

        modified_after_ms, modified_before_ms, created_after_ms, created_before_ms = self._get_date_filters()

        sync_point_key = generate_record_sync_point_key(
            RecordType.FILE.value, "bucket", bucket_name
        )
        sync_point = await self.record_sync_point.read_sync_point(sync_point_key)
        continuation_token = sync_point.get("continuation_token") if sync_point else None
        last_sync_time = sync_point.get("last_sync_time") if sync_point else None

        if last_sync_time:
            user_modified_after_ms = modified_after_ms
            if user_modified_after_ms:
                modified_after_ms = max(user_modified_after_ms, last_sync_time)
                self.logger.debug(
                    f"Merging modified_after filter for incremental sync: {modified_after_ms}"
                )
            else:
                modified_after_ms = last_sync_time
                self.logger.debug(f"Using last_sync_time for incremental sync: {modified_after_ms}")

        batch_records = []
        has_more = True
        max_timestamp = last_sync_time if last_sync_time else 0

        while has_more:
            try:
                async with self.rate_limiter:
                    response = await self.data_source.list_objects_v2(
                        Bucket=bucket_name,
                        MaxKeys=self.batch_size,
                        ContinuationToken=continuation_token,
                    )

                    if not response.success:
                        error_msg = response.error or "Unknown error"
                        if "AccessDenied" in error_msg or "not authorized" in error_msg:
                            self.logger.warning(
                                f"Access denied when listing objects in bucket {bucket_name}: {error_msg}."
                            )
                            self.logger.warning(
                                f"Please verify your IAM policy includes the following permissions for bucket '{bucket_name}':\n"
                                f"  - s3:ListBucket on arn:aws:s3:::{bucket_name}\n"
                                f"  - s3:GetBucketLocation on arn:aws:s3:::{bucket_name}\n"
                                f"  - s3:ListBucketVersions on arn:aws:s3:::{bucket_name} (if versioning is enabled)\n"
                                f"Also check if there's a bucket policy that might be blocking access."
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

                            if not is_folder:
                                last_modified = obj.get("LastModified")
                                if last_modified:
                                    if isinstance(last_modified, datetime):
                                        obj_timestamp_ms = int(last_modified.timestamp() * 1000)
                                        max_timestamp = max(max_timestamp, obj_timestamp_ms)
                                    else:
                                        obj_timestamp_ms = get_epoch_timestamp_in_ms()
                                        max_timestamp = max(max_timestamp, obj_timestamp_ms)
                            else:
                                last_modified = obj.get("LastModified")
                                if last_modified:
                                    if isinstance(last_modified, datetime):
                                        obj_timestamp_ms = int(last_modified.timestamp() * 1000)
                                        max_timestamp = max(max_timestamp, obj_timestamp_ms)

                            # Ensure folder hierarchy exists from file path (S3 has no folder objects)
                            if not is_folder:
                                path_segments = get_folder_path_segments_from_key(key)
                                if path_segments:
                                    await self._ensure_parent_folders_exist(bucket_name, path_segments)

                            record, permissions = await self._process_s3_object(
                                obj, bucket_name
                            )
                            if record:
                                batch_records.append((record, permissions))

                                if len(batch_records) >= self.batch_size:
                                    await self.data_entities_processor.on_new_records(
                                        batch_records
                                    )
                                    batch_records = []
                        except Exception as e:
                            self.logger.error(
                                f"Error processing object {obj.get('Key', 'unknown')}: {e}",
                                exc_info=True,
                            )
                            continue

                    has_more = objects_data.get("IsTruncated", False)
                    continuation_token = objects_data.get("NextContinuationToken")

                    if continuation_token:
                        await self.record_sync_point.update_sync_point(
                            sync_point_key, {"continuation_token": continuation_token}
                        )

            except Exception as e:
                self.logger.error(
                    f"Error during bucket sync for {bucket_name}: {e}", exc_info=True
                )
                has_more = False

        if batch_records:
            await self.data_entities_processor.on_new_records(batch_records)

        if max_timestamp > 0:
            await self.record_sync_point.update_sync_point(
                sync_point_key, {
                    "last_sync_time": max_timestamp,
                    "continuation_token": None
                }
            )

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
        self, bucket_name: str, path_segments: List[str]
    ) -> None:
        """Ensure folder records exist for each path segment (root to leaf).

        S3 list_objects only returns object keys; there are no separate folder objects.
        For each segment (e.g. 'a', 'a/b', 'a/b/c'), upsert a folder record and its edges.
        Always processes all segments so that edges are re-created after full sync.
        Process in order so parent exists before child. Aligns with Box _ensure_parent_folders_exist pattern.
        """
        if not path_segments:
            return
        timestamp_ms = get_epoch_timestamp_in_ms()
        for i, segment in enumerate(path_segments):
            external_id = f"{bucket_name}/{segment}"
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
                etag=None,
            )
            permissions = await self._create_s3_permissions(bucket_name, segment + "/")
            await self.data_entities_processor.on_new_records([(folder_record, permissions)])

    async def _process_s3_object(
        self, obj: Dict, bucket_name: str
    ) -> Tuple[Optional[FileRecord], List[Permission]]:
        """Process a single S3 object and convert it to a FileRecord.

        Logic:
        1. Extract path and etag from S3 object
        2. Try lookup by path (externalRecordId) - PRIMARY
           ├─ Found → Compare etags
           │   ├─ Different → Content change → Update record
           │   └─ Same → Skip (no changes)
           └─ Not Found → Try lookup by etag (externalRevisionId) - FALLBACK
               ├─ Found → Move/rename detected
               │   ├─ Extract old path from existing record
               │   ├─ Remove old parent relationship
               │   ├─ Update externalRecordId, path, recordName
               │   └─ Update record via data_entities_processor
               └─ Not Found → New file → Create new record
        """
        try:
            # 1. Extract path and etag from S3 object
            key = obj.get("Key", "")
            if not key:
                return None, []

            is_folder = key.endswith("/")
            is_file = not is_folder

            normalized_key = key.lstrip("/")
            if not normalized_key:
                return None, []

            last_modified = obj.get("LastModified")
            if last_modified:
                if isinstance(last_modified, datetime):
                    timestamp_ms = int(last_modified.timestamp() * 1000)
                else:
                    timestamp_ms = get_epoch_timestamp_in_ms()
            else:
                timestamp_ms = get_epoch_timestamp_in_ms()

            external_record_id = f"{bucket_name}/{normalized_key}"
            current_etag = obj.get("ETag", "").strip('"')
            composite_revision = make_s3_composite_revision(bucket_name, normalized_key, current_etag or None)

            # 2. PRIMARY: Try lookup by path (externalRecordId)
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id, external_id=external_record_id
                )

            is_move = False

            if existing_record:
                # Found by path - check if revision (composite) changed (content change)
                stored_revision = existing_record.external_revision_id or ""

                # Content changed or missing - sync properly from S3
                if composite_revision and stored_revision and composite_revision != stored_revision:
                    self.logger.info(
                        f"Content change detected: {normalized_key} - externalRevisionId changed from {stored_revision} to {composite_revision}"
                    )
                elif not composite_revision or not stored_revision:
                    if not current_etag:
                        self.logger.warning(
                            f"Current ETag missing for {normalized_key}, processing record"
                        )
                    if not stored_revision:
                        self.logger.debug(
                            f"Stored revision missing for {normalized_key}, processing record"
                        )
            else:
                # Not found by path - FALLBACK: try composite revision lookup (for move/rename detection)
                async with self.data_store_provider.transaction() as tx_store:
                    existing_record = await tx_store.get_record_by_external_revision_id(
                        connector_id=self.connector_id, external_revision_id=composite_revision
                    )

                if existing_record:
                    # Same composite can only match same key; if path differs it's a move/rename (key changed, etag same)
                    if existing_record.external_record_id != external_record_id:
                        is_move = True
                        self.logger.info(
                            f"Move/rename detected: {normalized_key} - file moved from {existing_record.external_record_id} to {external_record_id}"
                        )
                else:
                    self.logger.debug(f"New document: {normalized_key}")

            # Prepare record data: all items are RecordType.FILE; folders have is_file=False
            record_type = RecordType.FILE

            extension = get_file_extension(normalized_key) if is_file else None
            mime_type = get_mimetype_for_s3(normalized_key, is_folder)

            parent_path = get_parent_path_from_key(normalized_key)
            parent_external_id = (f"{bucket_name}/{parent_path}" if parent_path else None)
            parent_record_type = RecordType.FILE if parent_path else None
            # Root-level items: parent must be null, not the bucket
            if parent_external_id == bucket_name:
                parent_external_id = None
                parent_record_type = None

            web_url = self._generate_web_url(bucket_name, normalized_key)

            record_id = existing_record.id if existing_record else str(uuid.uuid4())

            record_name = normalized_key.rstrip("/").split("/")[-1] or normalized_key.rstrip("/")

            # For moves/renames, remove old parent relationship before processing
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
                external_revision_id=composite_revision,
                version=version,
                origin=OriginTypes.CONNECTOR.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                source_created_at=existing_record.source_created_at if existing_record else timestamp_ms,
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
                etag=current_etag,
            )

            # Root-level items: do not link to the bucket as a parent
            if (
                file_record.parent_external_record_id
                and file_record.external_record_group_id
                and file_record.parent_external_record_id == file_record.external_record_group_id
            ):
                file_record.parent_external_record_id = None
                file_record.parent_record_type = None

            if hasattr(self, 'indexing_filters') and self.indexing_filters:
                if not self.indexing_filters.is_enabled(IndexingFilterKey.FILES, default=True):
                    file_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

            permissions = await self._create_s3_permissions(bucket_name, key)

            return file_record, permissions

        except Exception as e:
            self.logger.error(f"Error processing S3 object: {e}", exc_info=True)
            return None, []

    async def _create_s3_permissions(
        self, bucket_name: str, key: str
    ) -> List[Permission]:
        """Create permissions for an S3 object based on connector scope."""
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
                self.logger.info(f"{self.connector_name} connection test successful.")
                return True
            else:
                self.logger.error(f"{self.connector_name} connection test failed: {response.error}")
                return False
        except Exception as e:
            self.logger.error(f"{self.connector_name} connection test failed: {e}", exc_info=True)
            return False

    async def get_signed_url(self, record: Record) -> Optional[str]:
        """Generate a presigned URL for an S3 object."""
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

            from urllib.parse import unquote
            key = unquote(key)

            bucket_region = await self._get_bucket_region(bucket_name)

            self.logger.debug(
                f"Generating presigned URL - Bucket: {bucket_name}, "
                f"Region: {bucket_region}, Key: {key}, Record ID: {record.id}"
            )

            response = await self.data_source.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": bucket_name, "Key": key},
                ExpiresIn=86400,
                region_name=bucket_region
            )

            if response.success:
                return response.data
            else:
                error_msg = response.error or "Unknown error"
                if "AccessDenied" in error_msg or "not authorized" in error_msg or "Forbidden" in error_msg:
                    self.logger.error(
                        f"❌ ACCESS DENIED: Failed to generate presigned URL. "
                        f"Error: {error_msg} | Bucket: {bucket_name} | Key: {key} | Record ID: {record.id}"
                    )
                elif "NoSuchKey" in error_msg or "NotFound" in error_msg:
                    self.logger.error(
                        f"❌ KEY NOT FOUND: The key may be incorrect. "
                        f"Error: {error_msg} | Bucket: {bucket_name} | Key: {key} | Record ID: {record.id}"
                    )
                else:
                    self.logger.error(
                        f"❌ FAILED: Failed to generate presigned URL. "
                        f"Error: {error_msg} | Bucket: {bucket_name} | Key: {key} | Record ID: {record.id}"
                    )
                return None
        except Exception as e:
            self.logger.error(
                f"Error generating signed URL for record {record.id}: {e}"
            )
            return None

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Stream S3 object content."""
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
            fallback_filename=f"record_{record.id}",
        )


    async def cleanup(self) -> None:
        """Clean up resources used by the connector."""
        self.logger.info(f"Cleaning up {self.connector_name} connector resources.")
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
                    message=f"{self.connector_name} connector is not initialized"
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
                bucket.get("Name") for bucket in buckets_data["Buckets"]
                if bucket.get("Name")
            ]

            for bucket_name in all_buckets:
                if bucket_name:
                    await self._get_bucket_region(bucket_name)

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

            self.logger.info(f"Starting reindex for {len(record_results)} {self.connector_name} records")

            if not self.data_source:
                self.logger.error(f"{self.connector_name} connector is not initialized.")
                raise Exception(f"{self.connector_name} connector is not initialized.")

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
                self.logger.info(f"Published reindex events for {len(non_updated_records)} records with unchanged external_revision_id")

        except Exception as e:
            self.logger.error(f"Error during {self.connector_name} reindex: {e}", exc_info=True)
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

            response = await self.data_source.head_object(
                Bucket=bucket_name,
                Key=normalized_key
            )

            if not response.success:
                self.logger.warning(f"Object {normalized_key} not found in bucket {bucket_name}")
                return None

            obj_metadata = response.data
            if not obj_metadata:
                return None

            current_etag = obj_metadata.get("ETag", "").strip('"')
            composite_revision = make_s3_composite_revision(bucket_name, normalized_key, current_etag or None)
            stored_revision = record.external_revision_id

            if composite_revision and stored_revision and composite_revision == stored_revision:
                self.logger.debug(f"Record {record.id}: external_revision_id unchanged ({composite_revision})")
                return None

            self.logger.debug(f"Record {record.id}: external_revision_id changed from {stored_revision} to {composite_revision}")

            last_modified = obj_metadata.get("LastModified")
            if last_modified:
                if isinstance(last_modified, datetime):
                    timestamp_ms = int(last_modified.timestamp() * 1000)
                else:
                    timestamp_ms = get_epoch_timestamp_in_ms()
            else:
                timestamp_ms = get_epoch_timestamp_in_ms()

            is_folder = normalized_key.endswith("/")
            is_file = not is_folder

            extension = get_file_extension(normalized_key) if is_file else None
            mime_type = get_mimetype_for_s3(normalized_key, is_folder)

            parent_path = get_parent_path_from_key(normalized_key)
            parent_external_id = (f"{bucket_name}/{parent_path}" if parent_path else None)
            parent_record_type = RecordType.FILE if parent_path else None
            # Root-level items: parent must be null, not the bucket
            if parent_external_id == bucket_name:
                parent_external_id = None
                parent_record_type = None

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
                external_revision_id=composite_revision,
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
                etag=current_etag,
            )

            # Root-level items: do not link to the bucket as a parent
            if (
                updated_record.parent_external_record_id
                and updated_record.external_record_group_id
                and updated_record.parent_external_record_id == updated_record.external_record_group_id
            ):
                updated_record.parent_external_record_id = None
                updated_record.parent_record_type = None

            if hasattr(self, 'indexing_filters') and self.indexing_filters:
                if not self.indexing_filters.is_enabled(IndexingFilterKey.FILES, default=True):
                    updated_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

            permissions = await self._create_s3_permissions(bucket_name, normalized_key)

            return updated_record, permissions

        except Exception as e:
            self.logger.error(f"Error checking record {record.id} at source: {e}")
            return None

    async def run_incremental_sync(self) -> None:
        """Run an incremental synchronization from buckets."""
        try:
            self.logger.info(f"Starting {self.connector_name} incremental sync.")

            if not self.data_source:
                raise ConnectionError(f"{self.connector_name} connector is not initialized.")

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
                            bucket.get("Name") for bucket in buckets_data["Buckets"]
                        ]

            if not buckets_to_sync:
                self.logger.warning("No buckets to sync")
                return

            self.logger.info(f"Fetching regions for {len(buckets_to_sync)} bucket(s)...")
            for bucket_name in buckets_to_sync:
                if bucket_name:
                    await self._get_bucket_region(bucket_name)

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

            self.logger.info(f"{self.connector_name} incremental sync completed.")
        except Exception as ex:
            self.logger.error(f"❌ Error in {self.connector_name} incremental sync: {ex}", exc_info=True)
            raise
