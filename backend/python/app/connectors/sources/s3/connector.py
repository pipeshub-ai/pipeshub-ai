import asyncio
import mimetypes
import uuid
from datetime import datetime, timezone
from logging import Logger
from typing import Dict, List, Optional, Tuple

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
    FilterOptionsResponse,
    FilterOption,
    OptionSourceType,
)
from app.connectors.sources.s3.common.apps import S3App
from app.models.entities import FileRecord, IndexingStatus, Record
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

            # Get sync filters
            sync_filters = self.sync_filters if hasattr(self, 'sync_filters') else {}
            
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

    async def _sync_bucket(self, bucket_name: str) -> None:
        """Sync objects from a specific bucket with pagination support and incremental sync."""
        if not self.data_source:
            raise ConnectionError("S3 connector is not initialized.")

        # Get sync filters
        sync_filters = self.sync_filters if hasattr(self, 'sync_filters') else {}
        
        # Get date filters
        modified_filter = sync_filters.get("modified")
        created_filter = sync_filters.get("created")
        file_extensions_filter = sync_filters.get("file_extensions")
        
        modified_after = None
        modified_before = None
        if modified_filter and modified_filter.value:
            date_range = modified_filter.value
            if isinstance(date_range, tuple) and len(date_range) == 2:
                modified_after, modified_before = date_range
        
        created_after = None
        created_before = None
        if created_filter and created_filter.value:
            date_range = created_filter.value
            if isinstance(date_range, tuple) and len(date_range) == 2:
                created_after, created_before = date_range

        # Get file extensions filter
        allowed_extensions = []
        if file_extensions_filter and file_extensions_filter.value:
            allowed_extensions = [ext.lower().lstrip('.') for ext in file_extensions_filter.value]

        sync_point_key = generate_record_sync_point_key(
            RecordType.FILE.value, "bucket", bucket_name
        )
        sync_point = await self.record_sync_point.read_sync_point(sync_point_key)
        continuation_token = sync_point.get("continuation_token") if sync_point else None
        last_sync_time = sync_point.get("last_sync_time") if sync_point else None

        # Use last_sync_time for incremental sync if available
        if last_sync_time and not modified_after:
            # Convert last_sync_time (epoch ms) to datetime for comparison
            modified_after = last_sync_time

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
                        self.logger.error(
                            f"Failed to list objects in bucket {bucket_name}: {response.error}"
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
                            # Apply filters
                            key = obj.get("Key", "")
                            
                            # Filter by extension
                            if allowed_extensions:
                                ext = get_file_extension(key)
                                if not ext or ext not in allowed_extensions:
                                    continue
                            
                            # Filter by date
                            last_modified = obj.get("LastModified")
                            if last_modified:
                                if isinstance(last_modified, datetime):
                                    obj_timestamp_ms = int(last_modified.timestamp() * 1000)
                                else:
                                    obj_timestamp_ms = get_epoch_timestamp_in_ms()
                                
                                # Check modified date filter
                                if modified_after and obj_timestamp_ms < modified_after:
                                    continue
                                if modified_before and obj_timestamp_ms > modified_before:
                                    continue
                                
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
        """Process a single S3 object and convert it to a FileRecord."""
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
            
            # Create FileRecord
            file_record = FileRecord(
                id=record_id,
                record_name=record_name,
                record_type=record_type,
                record_group_type=RecordGroupType.DRIVE.value,
                external_record_group_id=bucket_name,
                external_record_id=external_record_id,  # bucket_name/path format: my-bucket/a/b/c/file.txt
                external_revision_id=obj.get("ETag", "").strip('"'),
                version=0 if not existing_record else existing_record.version + 1,
                origin=OriginTypes.CONNECTOR.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                source_created_at=timestamp_ms,
                source_updated_at=timestamp_ms,
                weburl=web_url,
                signed_url=None,  # Will be generated on demand
                fetch_signed_url=signed_url_route,  # Route for Kafka to fetch signed URL
                parent_external_record_id=parent_external_id,  # bucket_name/parent_path format: my-bucket/a/b/c, or bucket_name if root
                parent_record_type=RecordType.FILE,
                size_in_bytes=obj.get("Size", 0) if is_file else 0,
                is_file=is_file,
                extension=extension,
                path=normalized_key,
                mime_type=mime_type,
                etag=obj.get("ETag", "").strip('"'),
            )

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

            # Generate presigned URL (valid for 1 hour)
            response = await self.data_source.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": bucket_name, "Key": key},
                ExpiresIn=86400,  # 24 hours in seconds
            )

            if response.success:
                return response.data
            else:
                self.logger.error(f"Failed to generate presigned URL: {response.error}")
                return None
        except Exception as e:
            self.logger.error(f"Error generating signed URL for record {record.id}: {e}")
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
            stream_content(signed_url),
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
                message=f"Error getting bucket options: {str(e)}"
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

            self.logger.info(f"Starting reindex for {len(record_results)} S3 records")

            if not self.data_source:
                self.logger.error("S3 connector is not initialized.")
                raise Exception("S3 connector is not initialized.")

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

            # Update DB only for records that changed at source
            if updated_records:
                await self.data_entities_processor.on_new_records(updated_records)
                self.logger.info(f"Updated {len(updated_records)} records in DB that changed at source")

            # Publish reindex events for non-updated records
            if non_updated_records:
                await self.data_entities_processor.reindex_existing_records(non_updated_records)
                self.logger.info(f"Published reindex events for {len(non_updated_records)} non-updated records")

        except Exception as e:
            self.logger.error(f"Error during S3 reindex: {e}", exc_info=True)
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

            # Get object metadata from S3
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

            # Get ETag (revision ID)
            current_etag = obj_metadata.get("ETag", "").strip('"')
            
            # Check if ETag has changed (content or metadata changed)
            if current_etag == record.external_revision_id:
                # No changes detected
                return None

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
                parent_external_record_id=parent_external_id,  # bucket_name/parent_path format: my-bucket/a/b/c, or bucket_name if root
                parent_record_type=RecordType.FILE ,
                size_in_bytes=obj_metadata.get("ContentLength", 0) if is_file else 0,
                is_file=is_file,
                extension=extension,
                path=normalized_key,
                mime_type=mime_type,
                etag=current_etag,
            )

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

            # Get sync filters
            sync_filters = self.sync_filters if hasattr(self, 'sync_filters') else {}
            
            # Get bucket filter if specified
            bucket_filter = sync_filters.get("buckets")
            selected_buckets = bucket_filter.value if bucket_filter and bucket_filter.value else []

            # List all buckets or use configured bucket
            buckets_to_sync = []
            if self.bucket_name:
                buckets_to_sync = [self.bucket_name]
            elif selected_buckets:
                buckets_to_sync = selected_buckets
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
        data_entities_processor = DataSourceEntitiesProcessor(
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
