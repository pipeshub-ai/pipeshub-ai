import asyncio
import mimetypes
import uuid
from datetime import datetime, timezone
from logging import Logger
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote

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
from app.models.entities import (
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import config_node_constants
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
    generate_record_sync_point_key,
)
from app.connectors.core.registry.connector_builder import (
    AuthField,
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
)
from app.connectors.core.registry.filters import (
    FilterField,
    FilterType,
    FilterCategory,
    OptionSourceType,
    MultiselectOperator,
    ListOperator,
    SyncFilterKey,
    IndexingFilterKey,
    FilterCollection,
    load_connector_filters,
    FilterOptionsResponse,
    FilterOption,
)
from app.connectors.sources.s3.common.apps import S3App
from app.models.entities import FileRecord, IndexingStatus, Record, RecordType
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.s3.s3 import S3Client
from app.sources.external.s3.s3 import S3DataSource
from app.utils.streaming import logger, stream_content
from app.utils.time_conversion import get_epoch_timestamp_in_ms


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


def get_parent_weburl_for_s3(parent_external_id: str) -> str:
    """Generate webUrl for an S3 directory based on parent external_id.
    
    Args:
        parent_external_id: External ID in format "bucket_name/path" or just "bucket_name"
        
    Returns:
        S3 console URL for the directory
        
    Examples:
        - "my-bucket" -> "https://s3.console.aws.amazon.com/s3/buckets/my-bucket"
        - "my-bucket/a/b/c" -> "https://s3.console.aws.amazon.com/s3/object/my-bucket?prefix=a/b/c/"
    """
    if "/" in parent_external_id:
        # Has path: "bucket_name/path/" or "bucket_name/path"
        parts = parent_external_id.split("/", 1)
        bucket_name = parts[0]
        path = parts[1]
        # Normalize: ensure path doesn't start with / but keeps trailing /
        path = path.lstrip("/")
        # For directories, ensure trailing slash is present
        if path and not path.endswith("/"):
            path = path + "/"
        return f"https://s3.console.aws.amazon.com/s3/object/{bucket_name}?prefix={path}"
    else:
        # Just bucket name (root level) - use bucket browsing URL format
        bucket_name = parent_external_id
        return f"https://s3.console.aws.amazon.com/s3/buckets/{bucket_name}"


def get_parent_path_for_s3(parent_external_id: str) -> Optional[str]:
    """Extract directory path from S3 parent external_id.
    
    Args:
        parent_external_id: External ID in format "bucket_name/path" or just "bucket_name"
        
    Returns:
        Directory path without bucket name prefix, or None for root directories
        
    Examples:
        - "my-bucket" -> None
        - "my-bucket/a/b/c" -> "a/b/c/"
    """
    if "/" in parent_external_id:
        # Has path: extract just the path part (without bucket name)
        parts = parent_external_id.split("/", 1)
        directory_path = parts[1]
        # Ensure trailing slash for directories
        if directory_path and not directory_path.endswith("/"):
            directory_path = directory_path + "/"
        return directory_path
    else:
        # Root directory (bucket level) - path should be None
        return None


def get_mimetype_for_s3(key: str, is_folder: bool = False) -> MimeTypes:
    """Determines the correct MimeTypes enum member for an S3 object."""
    if is_folder:
        return MimeTypes.FOLDER.value

    mime_type_str, _ = mimetypes.guess_type(key)
    if mime_type_str:
        try:
            return MimeTypes(mime_type_str)
        except ValueError:
            return MimeTypes.BIN.value
    return MimeTypes.BIN.value


class S3DataSourceEntitiesProcessor(DataSourceEntitiesProcessor):
    """S3-specific processor that extends the base processor with S3-specific placeholder record logic."""
    
    def _create_placeholder_parent_record(
        self,
        parent_external_id: str,
        parent_record_type: RecordType,
        record: Record,
    ) -> Record:
        """
        Create a placeholder parent record with S3-specific weburl and path.
        
        Overrides the base implementation to use S3 helper functions for generating
        weburl and path when creating placeholder parent records.
        """
        # Call the base implementation first
        parent_record = super()._create_placeholder_parent_record(
            parent_external_id, parent_record_type, record
        )
        
        # For S3 FILE records (directories), update weburl and path using S3 helper functions
        if parent_record_type == RecordType.FILE and isinstance(parent_record, FileRecord):
            weburl = get_parent_weburl_for_s3(parent_external_id)
            path = get_parent_path_for_s3(parent_external_id)
            parent_record.weburl = weburl
            parent_record.path = path
            parent_record.is_internal = True  # Mark S3 folder placeholder records as internal
        
        return parent_record


@ConnectorBuilder("S3")\
    .in_group("S3")\
    .with_auth_type("ACCESS_KEY")\
    .with_description("Sync files and folders from S3")\
    .with_categories(["Storage"])\
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/s3.svg")
        .add_documentation_link(DocumentationLink(
            "S3 Access Key Setup",
            "https://docs.aws.amazon.com/general/latest/gr/aws-sec-cred-types.html#access-keys-and-secret-access-keys",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/s3/s3',
            'pipeshub'
        ))
        .with_redirect_uri("", False)
        .add_auth_field(AuthField(
            name="accessKey",
            display_name="Access Key",
            placeholder="Enter your Access Key",
            description="The Access Key from S3 instance",
            field_type="PASSWORD",
            max_length=2000,
            is_secret=True
        ))
        .add_auth_field(AuthField(
            name="secretKey",
            display_name="Secret Key",
            placeholder="Enter your Secret Key",
            description="The Secret Key from S3 instance",
            field_type="PASSWORD",
            max_length=2000,
            is_secret=True
        ))
        .add_auth_field(AuthField(
            name="region",
            display_name="Region",
            placeholder="Enter your Region Name",
            description="The Region from S3 instance",
            field_type="TEXT",
            max_length=2000
        ))
        .add_filter_field(FilterField(
            name="buckets",
            display_name="Bucket Names",
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            description="Select specific S3 buckets to sync",
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
class S3Connector(BaseConnector):
    """
    Connector for synchronizing data from AWS S3 buckets.
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
            S3App(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
        )

        self.connector_name = Connectors.S3
        self.connector_id = connector_id

        # Initialize sync point for tracking record changes
        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_id=self.connector_id,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider,
            )

        self.record_sync_point = _create_sync_point(SyncDataPointType.RECORDS)

        self.data_source: Optional[S3DataSource] = None
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

    async def init(self) -> bool:
        """Initializes the S3 client using credentials from the config service."""
        config = await self.config_service.get_config(
            f"/services/connectors/{self.connector_id}/config"
        )
        if not config:
            self.logger.error("S3 configuration not found.")
            return False

        auth_config = config.get("auth", {})
        access_key = auth_config.get("accessKey")
        secret_key = auth_config.get("secretKey")
        self.region = auth_config.get("region", "us-east-1")
        self.bucket_name = auth_config.get("bucket")

        if not access_key or not secret_key:
            self.logger.error("S3 access key or secret key not found in configuration.")
            return False

        # Get connector instance metadata to determine scope
        # Try to get scope from config or default to PERSONAL
        # Note: Scope is typically stored in the connector instance document in the database
        # For now, we'll default to PERSONAL and can be overridden if needed
        self.connector_scope = ConnectorScope.PERSONAL.value
        self.created_by = None
        
        # Try to get scope from a config field if available
        scope_from_config = config.get("scope")
        if scope_from_config:
            self.connector_scope = scope_from_config

        try:
            client = await S3Client.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id,
            )
            self.data_source = S3DataSource(client)
            
            # Load connector filters
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "s3", self.connector_id, self.logger
            )
            
            self.logger.info("S3 client initialized successfully.")
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize S3 client: {e}", exc_info=True)
            return False

    async def run_sync(self) -> None:
        """Runs a full synchronization from S3 buckets."""
        try:
            self.logger.info("Starting S3 full sync.")

            if not self.data_source:
                raise ConnectionError("S3 connector is not initialized.")

            # Reload sync and indexing filters to pick up configuration changes
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "s3", self.connector_id, self.logger
            )

            # Get sync filters
            sync_filters = self.sync_filters if hasattr(self, 'sync_filters') and self.sync_filters else FilterCollection()
            
            # Get bucket filter if specified
            bucket_filter = sync_filters.get("buckets")
            selected_buckets = bucket_filter.value if bucket_filter and bucket_filter.value else []

            # List all buckets or use configured bucket
            buckets_to_sync = []
            if self.bucket_name:
                # Use configured bucket
                buckets_to_sync = [self.bucket_name]
                self.logger.info(f"Using configured bucket: {self.bucket_name}")
            elif selected_buckets:
                # Use buckets from filter
                buckets_to_sync = selected_buckets
                self.logger.info(f"Using filtered buckets: {buckets_to_sync}")
            else:
                # List all buckets
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

            self.logger.info("S3 full sync completed.")
        except Exception as ex:
            self.logger.error(f"❌ Error in S3 connector run: {ex}", exc_info=True)
            raise

    async def _create_record_groups_for_buckets(self, bucket_names: List[str]) -> None:
        """Create record groups for S3 buckets with appropriate permissions."""
        if not bucket_names:
            return

        record_groups = []
        for bucket_name in bucket_names:
            if not bucket_name:
                continue

            # Create permissions based on connector scope
            permissions = []
            if self.connector_scope == ConnectorScope.TEAM.value:
                # For Teams: permission with ORG (Anyone in org)
                permissions.append(
                    Permission(
                        type=PermissionType.READ,
                        entity_type=EntityType.ORG,
                        external_id=self.data_entities_processor.org_id
                    )
                )
            else:
                # For Personal: permission with individual user (creator)
                if self.created_by:
                    # Try to get user email from created_by user_id
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
                
                # Fallback to ORG permission if user not found
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
                group_type=RecordGroupType.DRIVE,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                description=f"S3 Bucket: {bucket_name}",
            )
            record_groups.append((record_group, permissions))

        if record_groups:
            await self.data_entities_processor.on_new_record_groups(record_groups)
            self.logger.info(f"Created {len(record_groups)} record group(s) for buckets")

    def _get_date_filters(self) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[int]]:
        """
        Extract date filter values from sync_filters.
        
        Returns date ranges as epoch milliseconds for comparison with S3 LastModified timestamps.
        Returns tuple of (modified_after_ms, modified_before_ms, created_after_ms, created_before_ms)
        """
        modified_after_ms: Optional[int] = None
        modified_before_ms: Optional[int] = None
        created_after_ms: Optional[int] = None
        created_before_ms: Optional[int] = None

        # Get modified date filter
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

        # Get created date filter
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
        """
        Returns True if S3 object PASSES date filters (should be kept).
        
        Note: S3 uses LastModified for both created and modified dates.
        For "created" filter, we use LastModified as proxy (S3 doesn't track separate creation time).
        Folders always pass through date filters to ensure directory structure exists.
        
        Args:
            obj: S3 object metadata dictionary
            modified_after_ms: Skip files modified before this timestamp (epoch ms)
            modified_before_ms: Skip files modified after this timestamp (epoch ms)
            created_after_ms: Skip files created before this timestamp (epoch ms)
            created_before_ms: Skip files created after this timestamp (epoch ms)
        
        Returns:
            False if the object should be filtered out, True otherwise
        """
        # Folders always pass through date filters
        key = obj.get("Key", "")
        is_folder = key.endswith("/")
        if is_folder:
            return True

        # No filters applied
        if not any([modified_after_ms, modified_before_ms, created_after_ms, created_before_ms]):
            return True

        # Get LastModified timestamp from S3 object
        last_modified = obj.get("LastModified")
        if not last_modified:
            return True  # If no timestamp, allow through
        
        # Convert to epoch milliseconds
        if isinstance(last_modified, datetime):
            obj_timestamp_ms = int(last_modified.timestamp() * 1000)
        else:
            return True  # If invalid format, allow through

        # Apply modified date filters (using LastModified)
        if modified_after_ms and obj_timestamp_ms < modified_after_ms:
            self.logger.debug(f"Skipping {key}: modified {obj_timestamp_ms} before cutoff {modified_after_ms}")
            return False
        if modified_before_ms and obj_timestamp_ms > modified_before_ms:
            self.logger.debug(f"Skipping {key}: modified {obj_timestamp_ms} after cutoff {modified_before_ms}")
            return False

        # Apply created date filters (using LastModified as proxy for creation time)
        if created_after_ms and obj_timestamp_ms < created_after_ms:
            self.logger.debug(f"Skipping {key}: created {obj_timestamp_ms} before cutoff {created_after_ms}")
            return False
        if created_before_ms and obj_timestamp_ms > created_before_ms:
            self.logger.debug(f"Skipping {key}: created {obj_timestamp_ms} after cutoff {created_before_ms}")
            return False

        return True

    async def _get_bucket_region(self, bucket_name: str) -> str:
        """Get the region for a bucket, using cache if available.
        
        Args:
            bucket_name: Name of the S3 bucket
            
        Returns:
            Region name (e.g., 'us-east-1', 'us-west-2'). Falls back to configured region if fetch fails.
        """
        # Check cache first
        if bucket_name in self.bucket_regions:
            return self.bucket_regions[bucket_name]
        
        # Fetch region if not in cache
        if not self.data_source:
            self.logger.warning(f"Cannot fetch region for bucket {bucket_name}: data_source not initialized")
            return self.region or "us-east-1"
        
        try:
            response = await self.data_source.get_bucket_location(Bucket=bucket_name)
            if response.success and response.data:
                # AWS returns None or empty string for us-east-1 (the default region)
                location = response.data.get("LocationConstraint")
                if location is None or location == "":
                    region = "us-east-1"
                else:
                    region = location
                # Cache the result
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
        
        # Fallback to configured region
        fallback_region = self.region or "us-east-1"
        return fallback_region

    async def _sync_bucket(self, bucket_name: str) -> None:
        """Sync objects from a specific bucket with pagination support and incremental sync."""
        if not self.data_source:
            raise ConnectionError("S3 connector is not initialized.")

        # Get sync filters
        sync_filters = self.sync_filters if hasattr(self, 'sync_filters') and self.sync_filters else FilterCollection()
        
        # Get file extensions filter (use string key for consistency with buckets filter)
        file_extensions_filter = sync_filters.get("file_extensions")
        allowed_extensions = []
        if file_extensions_filter and not file_extensions_filter.is_empty():
            filter_value = file_extensions_filter.value
            # Handle both list and string values (for backward compatibility)
            if isinstance(filter_value, list):
                allowed_extensions = [ext.lower().lstrip('.') for ext in filter_value if ext]
            elif isinstance(filter_value, str):
                # If it's a string, convert to list
                allowed_extensions = [filter_value.lower().lstrip('.')]
            else:
                self.logger.warning(
                    f"Unexpected file_extensions filter value type: {type(filter_value)}. "
                    f"Expected list or string, got {filter_value}"
                )
        
        if allowed_extensions:
            self.logger.info(
                f"File extensions filter active for bucket {bucket_name}: {allowed_extensions} (only files with these extensions will be synced)"
            )
        else:
            self.logger.debug(
                f"No file extensions filter for bucket {bucket_name} - syncing all file types"
            )

        # Get date filters
        modified_after_ms, modified_before_ms, created_after_ms, created_before_ms = self._get_date_filters()

        sync_point_key = generate_record_sync_point_key(
            RecordType.FILE.value, "bucket", bucket_name
        )
        sync_point = await self.record_sync_point.read_sync_point(sync_point_key)
        continuation_token = sync_point.get("continuation_token") if sync_point else None
        last_sync_time = sync_point.get("last_sync_time") if sync_point else None

        batch_records = []
        has_more = True
        max_timestamp = last_sync_time if last_sync_time else 0

        while has_more:
            try:
                async with self.rate_limiter:
                    # List objects with pagination
                    response = await self.data_source.list_objects_v2(
                        Bucket=bucket_name,
                        MaxKeys=self.batch_size,
                        ContinuationToken=continuation_token,
                    )

                    if not response.success:
                        error_msg = response.error or "Unknown error"
                        # Check if it's a permissions error
                        if "AccessDenied" in error_msg or "not authorized" in error_msg:
                            self.logger.error(
                                f"Access denied when listing objects in bucket {bucket_name}: {error_msg}. "
                                f"The IAM user may not have s3:ListBucket permission. "
                                f"Streaming individual files (s3:GetObject) may still work."
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

                    # Process each object
                    for obj in objects:
                        try:
                            key = obj.get("Key", "")
                            
                            # Determine if it's a folder (S3 uses keys ending with / for folders)
                            is_folder = key.endswith("/")
                            
                            # Apply file extensions filter (only for files, not folders)
                            if not is_folder and allowed_extensions:
                                ext = get_file_extension(key)
                                if not ext:
                                    self.logger.debug(
                                        f"Skipping {key}: no file extension found (allowed extensions: {allowed_extensions})"
                                    )
                                    continue
                                if ext not in allowed_extensions:
                                    self.logger.debug(
                                        f"Skipping {key}: extension '{ext}' not in allowed extensions {allowed_extensions}"
                                    )
                                    continue
                                # File passed extension filter
                                self.logger.debug(
                                    f"File {key} passed extension filter (extension: '{ext}' matches allowed extensions: {allowed_extensions})"
                                )
                            
                            # Apply date filters
                            if not self._pass_date_filters(
                                obj, modified_after_ms, modified_before_ms, created_after_ms, created_before_ms
                            ):
                                continue
                            
                            # Update max_timestamp for incremental sync tracking
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
                                # For folders, update max_timestamp if available
                                last_modified = obj.get("LastModified")
                                if last_modified:
                                    if isinstance(last_modified, datetime):
                                        obj_timestamp_ms = int(last_modified.timestamp() * 1000)
                                        max_timestamp = max(max_timestamp, obj_timestamp_ms)
                            
                            record, permissions = await self._process_s3_object(
                                obj, bucket_name
                            )
                            if record:
                                batch_records.append((record, permissions))

                                # Process in batches
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

                    # Check for more pages
                    has_more = objects_data.get("IsTruncated", False)
                    continuation_token = objects_data.get("NextContinuationToken")

                    # Update sync point with continuation token
                    if continuation_token:
                        await self.record_sync_point.update_sync_point(
                            sync_point_key, {"continuation_token": continuation_token}
                        )

            except Exception as e:
                self.logger.error(
                    f"Error during bucket sync for {bucket_name}: {e}", exc_info=True
                )
                has_more = False

        # Process remaining records
        if batch_records:
            await self.data_entities_processor.on_new_records(batch_records)

        # Update sync point with last sync time
        if max_timestamp > 0:
            await self.record_sync_point.update_sync_point(
                sync_point_key, {
                    "last_sync_time": max_timestamp,
                    "continuation_token": None  # Reset continuation token after full sync
                }
            )

    async def _process_s3_object(
        self, obj: Dict, bucket_name: str
    ) -> Tuple[Optional[FileRecord], List[Permission]]:
        """Process a single S3 object and convert it to a FileRecord.
        
        When run_sync runs again, this method fetches updated documents by checking
        if the external_revision_id (ETag) has changed. If changed, the document
        is updated; if unchanged, processing is skipped for efficiency.
        """
        try:
            key = obj.get("Key", "")
            if not key:
                return None, []

            # Determine if it's a folder (S3 uses keys ending with / for folders)
            # Check before normalization to preserve folder indicator
            is_folder = key.endswith("/")
            is_file = not is_folder

            # Normalize key by removing only leading slashes for external_id
            # Keep trailing slash for folders to maintain folder identification
            normalized_key = key.lstrip("/")
            if not normalized_key:
                return None, []

            # Get timestamps
            last_modified = obj.get("LastModified")
            if last_modified:
                if isinstance(last_modified, datetime):
                    timestamp_ms = int(last_modified.timestamp() * 1000)
                else:
                    # Handle string format if needed
                    timestamp_ms = get_epoch_timestamp_in_ms()
            else:
                timestamp_ms = get_epoch_timestamp_in_ms()

            # Get existing record if it exists (use bucket_name/path format for external_id)
            external_record_id = f"{bucket_name}/{normalized_key}"
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id, external_id=external_record_id
                )

            # Get current ETag (external_revision_id) from S3 object
            current_etag = obj.get("ETag", "").strip('"')
            
            # Check if document has been updated (ETag/external_revision_id changed)
            # When run_sync runs again, fetch updated documents if external_revision_id has changed
            etag_changed = False
            if existing_record:
                stored_etag = existing_record.external_revision_id or ""
                
                # Compare ETags - both must be non-empty strings for reliable comparison
                if current_etag and stored_etag:
                    if current_etag == stored_etag:
                        # External revision ID unchanged - document not updated, skip processing
                        self.logger.debug(
                            f"Skipping {normalized_key}: external_revision_id unchanged ({current_etag})"
                        )
                        return None, []
                    else:
                        # External revision ID changed - document updated, fetch and update
                        etag_changed = True
                        self.logger.info(
                            f"Document updated: {normalized_key} - external_revision_id changed from {stored_etag} to {current_etag}"
                        )
                elif not current_etag or not stored_etag:
                    # One or both ETags are missing - process defensively (treat as potentially changed)
                    if not current_etag:
                        self.logger.warning(
                            f"Current ETag missing for {normalized_key}, processing record"
                        )
                    if not stored_etag:
                        self.logger.debug(
                            f"Stored ETag missing for {normalized_key}, processing record"
                        )
                    etag_changed = True
            else:
                # New record - no existing record, so ETag comparison not applicable
                self.logger.debug(f"New document: {normalized_key}")

            # Determine record type
            record_type = RecordType.FOLDER if is_folder else RecordType.FILE

            # Get file extension and MIME type (use original key for mimetype detection)
            extension = get_file_extension(normalized_key) if is_file else None
            mime_type = get_mimetype_for_s3(normalized_key, is_folder)

            # Get parent path (returns path without leading slash)
            parent_path = get_parent_path_from_key(normalized_key)
            parent_external_id = f"{bucket_name}/{parent_path}" if parent_path else bucket_name

            # Generate web URL (S3 console URL format)
            web_url = f"https://s3.console.aws.amazon.com/s3/object/{bucket_name}?prefix={normalized_key}"

            # Create FileRecord first to get the ID
            record_id = existing_record.id if existing_record else str(uuid.uuid4())
            
            # Generate signed URL route for Kafka events
            # Get connector endpoint from config
            endpoints = await self.config_service.get_config(
                config_node_constants.ENDPOINTS.value
            )
            connector_endpoint = endpoints.get("connectors", {}).get("endpoint", "http://localhost:8000")
            signed_url_route = (
                f"{connector_endpoint}/api/v1/internal/stream/record/{record_id}"
            )

            # Extract record name (remove trailing slash for folders)
            record_name = normalized_key.rstrip("/").split("/")[-1] or normalized_key.rstrip("/")
            
            # Determine version - only increment for new records or when ETag changed
            # (Note: If we reached here with existing_record, ETag must have changed or one was missing)
            if not existing_record:
                version = 0
            else:
                # Existing record with ETag change - increment version
                version = existing_record.version + 1
            
            # Create FileRecord
            file_record = FileRecord(
                id=record_id,
                record_name=record_name,
                record_type=record_type,
                record_group_type=RecordGroupType.DRIVE.value,
                external_record_group_id=bucket_name,
                external_record_id=external_record_id,  # bucket_name/path format: my-bucket/a/b/c/file.txt
                external_revision_id=current_etag,  # Use current ETag from S3
                version=version,
                origin=OriginTypes.CONNECTOR.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                source_created_at=timestamp_ms,
                source_updated_at=timestamp_ms,
                weburl=web_url,
                signed_url=None,  # Will be generated on demand
                fetch_signed_url=signed_url_route,  # Route for Kafka to fetch signed URL
                hide_weburl=True,  # Hide web URL for S3 documents
                is_internal=True if is_folder else False,  # Mark S3 folder records as internal
                parent_external_record_id=parent_external_id,  # bucket_name/parent_path format: my-bucket/a/b/c, or bucket_name if root
                parent_record_type=RecordType.FILE,
                size_in_bytes=obj.get("Size", 0) if is_file else 0,
                is_file=is_file,
                extension=extension,
                path=normalized_key,
                mime_type=mime_type,
                etag=current_etag,  # Use current ETag from S3
            )

            # Set indexing status based on enable_manual_sync filter
            # The is_enabled method automatically handles enable_manual_sync - if it's enabled, indexing is disabled
            if hasattr(self, 'indexing_filters') and self.indexing_filters:
                if not self.indexing_filters.is_enabled(IndexingFilterKey.FILES, default=True):
                    file_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

            # Create permissions (default: owner permission)
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
                # For Teams: permission with ORG (Anyone in org)
                permissions.append(
                    Permission(
                        type=PermissionType.READ,
                        entity_type=EntityType.ORG,
                        external_id=self.data_entities_processor.org_id
                    )
                )
            else:
                # For Personal: permission with individual user (creator)
                # Since we may not have created_by easily accessible, default to ORG permission
                # In a real implementation, you would fetch the creator from the connector instance
                permissions.append(
                    Permission(
                        type=PermissionType.READ,
                        entity_type=EntityType.ORG,
                        external_id=self.data_entities_processor.org_id
                    )
                )

            # TODO: Fetch object ACL from S3 if needed
            # response = await self.data_source.get_object_acl(Bucket=bucket_name, Key=key)
            # if response.success:
            #     # Parse ACL and convert to Permission objects
            #     pass

            return permissions
        except Exception as e:
            self.logger.warning(f"Error creating permissions for {key}: {e}")
            # Fallback to ORG permission
            return [
                Permission(
                    type=PermissionType.READ,
                    entity_type=EntityType.ORG,
                    external_id=self.data_entities_processor.org_id
                )
            ]

    async def test_connection_and_access(self) -> bool:
        """Test S3 connection and access."""
        if not self.data_source:
            return False
        try:
            # Test by listing buckets
            response = await self.data_source.list_buckets()
            if response.success:
                self.logger.info("S3 connection test successful.")
                return True
            else:
                self.logger.error(f"S3 connection test failed: {response.error}")
                return False
        except Exception as e:
            self.logger.error(f"S3 connection test failed: {e}", exc_info=True)
            return False

    async def get_signed_url(self, record: Record) -> Optional[str]:
        """Generate a presigned URL for an S3 object."""
        if not self.data_source:
            return None
        try:
            # Get bucket name from record group
            bucket_name = record.external_record_group_id
            if not bucket_name:
                self.logger.warning(f"No bucket name found for record: {record.id}")
                return None

            external_record_id = record.external_record_id
            if not external_record_id:
                self.logger.warning(f"No external_record_id found for record: {record.id}")
                return None

            # Extract the actual key from external_record_id (format: bucket_name/path)
            # Remove the bucket prefix to get the actual S3 key
            if external_record_id.startswith(f"{bucket_name}/"):
                key = external_record_id[len(f"{bucket_name}/"):]
            else:
                # Fallback: if format is unexpected, use as-is
                key = external_record_id.lstrip("/")

            # URL-decode the key if it's encoded (AWS expects raw keys)
            # This handles cases where external_record_id might be URL-encoded
            key = unquote(key)

            # Get bucket-specific region (will use cache if available, or fetch on-demand)
            bucket_region = await self._get_bucket_region(bucket_name)

            # Log the key being used for debugging
            self.logger.debug(
                f"Generating presigned URL - Bucket: {bucket_name}, "
                f"Region: {bucket_region}, Key: {key}, Record ID: {record.id}"
            )

            # Generate presigned URL (valid for 24 hours) using bucket-specific region
            response = await self.data_source.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": bucket_name, "Key": key},
                ExpiresIn=86400,  # 24 hours in seconds
                region_name=bucket_region
            )

            if response.success:
                return response.data
            else:
                error_msg = response.error or "Unknown error"
                # Distinguish between access denied and other errors (like invalid key due to encoding)
                if "AccessDenied" in error_msg or "not authorized" in error_msg or "Forbidden" in error_msg:
                    self.logger.error(
                        f"❌ ACCESS DENIED: Failed to generate presigned URL due to permissions issue. "
                        f"Error: {error_msg} | Bucket: {bucket_name} | Key: {key} | Record ID: {record.id} | "
                        f"File: {record.record_name if hasattr(record, 'record_name') else 'unknown'}"
                    )
                elif "NoSuchKey" in error_msg or "NotFound" in error_msg:
                    self.logger.error(
                        f"❌ KEY NOT FOUND: The S3 key may be incorrect (possibly encoding issue with special characters). "
                        f"Error: {error_msg} | Bucket: {bucket_name} | Key: {key} | Record ID: {record.id} | "
                        f"File: {record.record_name if hasattr(record, 'record_name') else 'unknown'} | "
                        f"Original external_record_id: {external_record_id}"
                    )
                else:
                    self.logger.error(
                        f"❌ FAILED: Failed to generate presigned URL. "
                        f"Error: {error_msg} | Bucket: {bucket_name} | Key: {key} | Record ID: {record.id}"
                    )
                return None
        except Exception as e:
            self.logger.error(
                f"Error generating signed URL for record {record.id}: {e} | "
                f"Bucket: {bucket_name if 'bucket_name' in locals() else 'unknown'} | "
                f"Key: {key if 'key' in locals() else 'unknown'}"
            )
            return None

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Stream S3 object content."""
        # Check if record is a file (not a folder)
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

        return StreamingResponse(
            stream_content(signed_url, record_id=record.id, file_name=record.record_name),
            media_type=record.mime_type if record.mime_type else "application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename={record.record_name}"},
        )

    async def cleanup(self) -> None:
        """Clean up resources used by the connector."""
        raise NotImplementedError("This method should be implemented by the subclass")

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> FilterOptionsResponse:
        """Get dynamic filter options for S3 filters."""
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
        """Get list of available S3 buckets."""
        try:
            if not self.data_source:
                return FilterOptionsResponse(
                    success=False,
                    options=[],
                    page=page,
                    limit=limit,
                    has_more=False,
                    message="S3 connector is not initialized"
                )

            # List all buckets
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

            # Extract bucket names
            all_buckets = [
                bucket.get("Name") for bucket in buckets_data["Buckets"]
                if bucket.get("Name")
            ]

            # Fetch and cache regions for all buckets
            for bucket_name in all_buckets:
                if bucket_name:
                    await self._get_bucket_region(bucket_name)

            # Apply search filter if provided
            if search:
                search_lower = search.lower()
                all_buckets = [
                    bucket for bucket in all_buckets
                    if search_lower in bucket.lower()
                ]

            # Apply pagination
            start_idx = (page - 1) * limit
            end_idx = start_idx + limit
            paginated_buckets = all_buckets[start_idx:end_idx]
            has_more = end_idx < len(all_buckets)

            # Convert to FilterOption objects
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
        """Reindex records by checking for updates at source and publishing reindex events.
        
        For manual sync reindex:
        1. Fetch from S3 to check if external_revision_id (ETag) has changed
        2. If changed: fetch updated data and reindex it
        3. If not changed: reindex only (without updating DB)
        """
        try:
            if not record_results:
                self.logger.info("No records to reindex")
                return

            self.logger.info(f"Starting reindex for {len(record_results)} S3 records - checking S3 for external_revision_id changes")

            if not self.data_source:
                self.logger.error("S3 connector is not initialized.")
                raise Exception("S3 connector is not initialized.")

            org_id = self.data_entities_processor.org_id
            updated_records = []
            non_updated_records = []

            for record in record_results:
                try:
                    # Fetch from S3 and check if external_revision_id has changed
                    updated_record_data = await self._check_and_fetch_updated_record(
                        org_id, record
                    )
                    if updated_record_data:
                        # External revision ID changed - fetch updated data and reindex
                        updated_record, permissions = updated_record_data
                        updated_records.append((updated_record, permissions))
                    else:
                        # External revision ID unchanged - reindex only (without updating DB)
                        non_updated_records.append(record)
                except Exception as e:
                    self.logger.error(f"Error checking record {record.id} at source: {e}")
                    continue

            # Update DB only for records that changed at source (external_revision_id changed)
            if updated_records:
                await self.data_entities_processor.on_new_records(updated_records)
                self.logger.info(f"Updated {len(updated_records)} records in DB that changed at source (external_revision_id changed)")

            # Publish reindex events for non-updated records (external_revision_id unchanged)
            if non_updated_records:
                await self.data_entities_processor.reindex_existing_records(non_updated_records)
                self.logger.info(f"Published reindex events for {len(non_updated_records)} records with unchanged external_revision_id (reindex only)")

        except Exception as e:
            self.logger.error(f"Error during S3 reindex: {e}", exc_info=True)
            raise

    async def _check_and_fetch_updated_record(
        self, org_id: str, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Check if record has been updated at source and fetch updated data.
        
        Fetches from S3 to check if external_revision_id (ETag) has changed.
        Returns updated record data if changed, None if unchanged.
        """
        try:
            bucket_name = record.external_record_group_id
            external_record_id = record.external_record_id

            if not bucket_name or not external_record_id:
                self.logger.warning(f"Missing bucket or external_record_id for record {record.id}")
                return None

            # Extract the actual key from external_record_id (format: bucket_name/path)
            # Remove the bucket prefix to get the actual S3 key
            if external_record_id.startswith(f"{bucket_name}/"):
                normalized_key = external_record_id[len(f"{bucket_name}/"):]
            else:
                # Fallback: if format is unexpected, use as-is
                normalized_key = external_record_id.lstrip("/")
            
            if not normalized_key:
                self.logger.warning(f"Invalid key for record {record.id}")
                return None

            # Fetch from S3 to get object metadata (including ETag/external_revision_id)
            response = await self.data_source.head_object(
                Bucket=bucket_name,
                Key=normalized_key
            )

            if not response.success:
                # Object might have been deleted
                self.logger.warning(f"Object {normalized_key} not found in bucket {bucket_name}")
                return None

            obj_metadata = response.data
            if not obj_metadata:
                return None

            # Get current ETag (external_revision_id) from S3
            current_etag = obj_metadata.get("ETag", "").strip('"')
            stored_etag = record.external_revision_id
            
            # Check if external_revision_id (ETag) has changed
            if current_etag == stored_etag:
                # External revision ID unchanged - return None to trigger reindex only
                self.logger.debug(f"Record {record.id}: external_revision_id unchanged ({current_etag}), will reindex only")
                return None
            
            # External revision ID changed - fetch updated data
            self.logger.debug(f"Record {record.id}: external_revision_id changed from {stored_etag} to {current_etag}, fetching updated data")

            # Get LastModified timestamp
            last_modified = obj_metadata.get("LastModified")
            if last_modified:
                if isinstance(last_modified, datetime):
                    timestamp_ms = int(last_modified.timestamp() * 1000)
                else:
                    timestamp_ms = get_epoch_timestamp_in_ms()
            else:
                timestamp_ms = get_epoch_timestamp_in_ms()

            # Determine if it's a folder (check original key format)
            is_folder = normalized_key.endswith("/")
            is_file = not is_folder

            # Get file extension and MIME type
            extension = get_file_extension(normalized_key) if is_file else None
            mime_type = get_mimetype_for_s3(normalized_key, is_folder)

            # Get parent path (returns path without leading slash)
            parent_path = get_parent_path_from_key(normalized_key)
            parent_external_id = f"{bucket_name}/{parent_path}" if parent_path else bucket_name

            # Generate web URL
            web_url = f"https://s3.console.aws.amazon.com/s3/object/{bucket_name}?prefix={normalized_key}"

            # Generate signed URL route
            endpoints = await self.config_service.get_config(
                config_node_constants.ENDPOINTS.value
            )
            connector_endpoint = endpoints.get("connectors", {}).get("endpoint", "http://localhost:8000")
            signed_url_route = (
                f"{connector_endpoint}/api/v1/internal/stream/record/{record.id}"
            )

            # Extract record name (remove trailing slash for folders)
            record_name = normalized_key.rstrip("/").split("/")[-1] or normalized_key.rstrip("/")
            
            # Create updated FileRecord with bucket_name/path format
            updated_external_record_id = f"{bucket_name}/{normalized_key}"
            
            # Create updated FileRecord
            updated_record = FileRecord(
                id=record.id,
                record_name=record_name,
                record_type=RecordType.FOLDER if is_folder else RecordType.FILE,
                record_group_type=RecordGroupType.DRIVE.value,
                external_record_group_id=bucket_name,
                external_record_id=updated_external_record_id,  # bucket_name/path format: my-bucket/a/b/c/file.txt
                external_revision_id=current_etag,
                version=record.version + 1,
                origin=OriginTypes.CONNECTOR.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                source_created_at=record.source_created_at,
                source_updated_at=timestamp_ms,
                weburl=web_url,
                signed_url=None,
                fetch_signed_url=signed_url_route,
                hide_weburl=True,  # Hide web URL for S3 documents
                is_internal=True if is_folder else False,  # Mark S3 folder records as internal
                parent_external_record_id=parent_external_id,  # bucket_name/parent_path format: my-bucket/a/b/c, or bucket_name if root
                parent_record_type=RecordType.FILE ,
                size_in_bytes=obj_metadata.get("ContentLength", 0) if is_file else 0,
                is_file=is_file,
                extension=extension,
                path=normalized_key,
                mime_type=mime_type,
                etag=current_etag,
            )

            # Set indexing status based on enable_manual_sync filter
            # The is_enabled method automatically handles enable_manual_sync - if it's enabled, indexing is disabled
            if hasattr(self, 'indexing_filters') and self.indexing_filters:
                if not self.indexing_filters.is_enabled(IndexingFilterKey.FILES, default=True):
                    updated_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

            # Get permissions
            permissions = await self._create_s3_permissions(bucket_name, normalized_key)

            return updated_record, permissions

        except Exception as e:
            self.logger.error(f"Error checking record {record.id} at source: {e}")
            return None

    async def run_incremental_sync(self) -> None:
        """Run an incremental synchronization from S3 buckets."""
        try:
            self.logger.info("Starting S3 incremental sync.")

            if not self.data_source:
                raise ConnectionError("S3 connector is not initialized.")

            # Reload sync and indexing filters to pick up configuration changes
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "s3", self.connector_id, self.logger
            )

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
                # Use buckets from filter
                buckets_to_sync = selected_buckets
                self.logger.info(f"Using filtered buckets: {buckets_to_sync}")
            else:
                # List all buckets
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

            # Fetch and cache regions for all buckets
            self.logger.info(f"Fetching regions for {len(buckets_to_sync)} bucket(s)...")
            for bucket_name in buckets_to_sync:
                if bucket_name:
                    await self._get_bucket_region(bucket_name)

            # Sync each bucket (incremental sync uses last_sync_time from sync point)
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

            self.logger.info("S3 incremental sync completed.")
        except Exception as ex:
            self.logger.error(f"❌ Error in S3 incremental sync: {ex}", exc_info=True)
            raise

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        **kwargs,
    ) -> "S3Connector":
        """Factory method to create and initialize connector."""
        data_entities_processor = S3DataSourceEntitiesProcessor(
            logger, data_store_provider, config_service
        )
        await data_entities_processor.initialize()

        return cls(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
        )
