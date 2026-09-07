import asyncio
import io
import os
import tempfile
import uuid
from datetime import datetime
from logging import Logger
from pathlib import Path
from typing import TYPE_CHECKING, AsyncGenerator, Dict, List, Optional, Tuple

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    Connectors,
    ExtensionTypes,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.constants import IconPaths
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
)
from app.connectors.core.registry.auth_builder import AuthType, OAuthScopeConfig
from app.connectors.core.registry.connector_builder import (
    AuthBuilder,
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
    SyncStrategy,
)
from app.connectors.core.constants import CONNECTOR_EMAIL_IDENTITY_INFO
from app.connectors.core.registry.filters import (
    FilterCategory,
    FilterCollection,
    FilterField,
    FilterOperator,
    FilterOption,
    FilterOptionsResponse,
    FilterType,
    IndexingFilterKey,
    OptionSourceType,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.sources.google.common.apps import GoogleDriveApp
from app.connectors.sources.google.common.connector_google_exceptions import (
    GoogleDriveError,
)
from app.connectors.sources.google.common.datasource_refresh import (
    refresh_google_datasource_credentials,
)
from app.connectors.sources.google.common.drive_file_fields import (
    DRIVE_PERSONAL_SYNC_FILE_RESOURCE_FIELDS,
    DRIVE_PERSONAL_SYNC_FILES_LIST_FIELDS,
)
from app.connectors.sources.google.drive.utils.folder_filter_utils import (
    ANCESTOR_FETCH_CONCURRENCY,
    PLACEHOLDER_SWEEP_SAFETY_MAX,
    build_tracked_folder_ids,
    fetch_ancestor_metadata,
    fetch_folder_children,
    has_entered_scope,
    has_exited_scope,
    is_retryable_403,
    pass_folder_filter,
    probe_can_list_children,
)
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
from app.models.entities import (
    AppUser,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.google.google import GoogleClient
from app.sources.external.google.drive.drive import GoogleDriveDataSource
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import get_epoch_timestamp_in_ms, parse_timestamp

if TYPE_CHECKING:
    from app.connectors.core.thread_pool import ThreadPoolLease

# Maximum concurrent borrows from the shared connector thread pool.
_DRIVE_INDIVIDUAL_MAX_CONCURRENCY = 4

# Bytes fetched per MediaIoBaseDownload.next_chunk() call. The library default is
# 100 MB, which buffers a whole slice in memory before any of it reaches the
# client and keeps one executor thread busy for that entire transfer.
_DRIVE_DOWNLOAD_CHUNK_SIZE = 4 * 1024 * 1024


@ConnectorBuilder("Drive")\
    .in_group("Google Workspace")\
    .with_description("Sync files and folders from Google Drive")\
    .with_categories(["Storage"])\
    .with_scopes([ConnectorScope.PERSONAL.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Drive",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            redirect_uri="connectors/oauth/callback/Drive",
            scopes=OAuthScopeConfig(
                personal_sync=[
                    "https://www.googleapis.com/auth/drive.readonly",
                ],
                team_sync=[],
                agent=[]
            ),
            fields=[
                CommonFields.client_id("Google Cloud Console"),
                CommonFields.client_secret("Google Cloud Console")
            ],
            icon_path=IconPaths.connector_icon(Connectors.GOOGLE_DRIVE.value),
            app_group="Google Workspace",
            app_description="OAuth application for accessing Google Drive API and related Google Workspace services",
            app_categories=["Storage"],
            additional_params={
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true"
            }
        )
    ])\
    .with_info(CONNECTOR_EMAIL_IDENTITY_INFO)\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.GOOGLE_DRIVE.value))
        .with_realtime_support(True)
        .add_documentation_link(DocumentationLink(
            "Google Drive API Setup",
            "https://developers.google.com/workspace/guides/auth-overview",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/google-workspace/drive/drive',
            'pipeshub'
        ))
        .add_filter_field(FilterField(
            name=SyncFilterKey.FOLDER_IDS.value,
            display_name="Folder IDs",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            description=(
                """
                To find a folder ID: Open the folder in Google Drive. The folder ID is the last segment of the URL: 
                `drive.google.com/drive/folders/<FOLDER_ID>`.
                """),
            option_source_type=OptionSourceType.MANUAL,
            allowed_operators=[FilterOperator.IN],
        ))
        .add_filter_field(CommonFields.modified_date_filter("Filter files and folders by modification date."))
        .add_filter_field(CommonFields.created_date_filter("Filter files and folders by creation date."))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        # .add_filter_field(CommonFields.file_extension_filter())
        .add_filter_field(FilterField(
            name="file_extensions",
            display_name="Sync Files with Extensions",
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            description="Sync files with specific extensions",
            option_source_type=OptionSourceType.STATIC,
            options=[
                FilterOption(id=MimeTypes.GOOGLE_DOCS.value, label="google docs"),
                FilterOption(id=MimeTypes.GOOGLE_SHEETS.value, label="google sheets"),
                FilterOption(id=MimeTypes.GOOGLE_SLIDES.value, label="google slides"),
            ] + [
                FilterOption(id=ext.value, label=f".{ext.value}")
                for ext in ExtensionTypes
            ]
        ))
        .add_filter_field(FilterField(
            name="shared",
            display_name="Index Shared Items",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of shared items",
            default_value=True
        ))
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(True)
    )\
    .build_decorator()
class GoogleDriveIndividualConnector(BaseConnector):
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
            GoogleDriveApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )

        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_id=self.connector_id,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider
            )

        # Initialize sync points
        self.drive_delta_sync_point = _create_sync_point(SyncDataPointType.RECORDS)
        self.connector_id = connector_id

        # Batch processing configuration
        self.batch_size = 100

        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

        # Folder-scope filter state, rebuilt per sync run in run_sync().
        # Empty _tracked_folder_ids means no folder filter, so everything syncs.
        self._folder_seed_ids: set = set()
        self._tracked_folder_ids: set = set()
        self._blocked_folder_ids: set = set()

        # Google Drive client and data source (initialized in init())
        self.google_client: Optional[GoogleClient] = None
        self.drive_data_source: Optional[GoogleDriveDataSource] = None
        self.config: Optional[Dict] = None
        # Serializes credential refresh so concurrent callers can't swap the client's
        # credentials out from under each other.
        self._datasource_refresh_lock = asyncio.Lock()

        # Acquired in init(), once the factory has injected the shared pool.
        self._drive_executor: ThreadPoolLease | None = None

    async def init(self) -> bool:
        """Initialize the Google Drive connector with credentials and services."""
        try:
            self._drive_executor = self._thread_lease(_DRIVE_INDIVIDUAL_MAX_CONCURRENCY)

            # Load connector config
            config = await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config"
            )
            if not config:
                self.logger.error("Google Drive config not found")
                return False

            self.config = {"credentials": config}

            auth_config = config.get("auth") or {}
            oauth_config_id = auth_config.get("oauthConfigId")

            if not oauth_config_id:
                self.logger.error("Dropbox oauthConfigId not found in auth configuration.")
                return False

            oauth_config = await self._fetch_oauth_config_by_id(
                oauth_config_id=oauth_config_id,
                connector_type=Connectors.GOOGLE_DRIVE.value,
                auth_config=auth_config,
            )

            if not oauth_config:
                self.logger.error(f"OAuth config {oauth_config_id} not found for Dropbox connector.")
                return False

            oauth_config_data = oauth_config.get("config", {})

            client_id = oauth_config_data.get("clientId")
            client_secret = oauth_config_data.get("clientSecret")

            if not all((client_id, client_secret)):
                self.logger.error(
                    "Incomplete Google Drive config. Ensure clientId and clientSecret are configured."
                )
                raise ValueError(
                    "Incomplete Google Drive credentials. Ensure clientId and clientSecret are configured."
                )

            # Extract credentials (tokens)
            credentials_data = config.get("credentials", {})
            access_token = credentials_data.get("access_token")
            refresh_token = credentials_data.get("refresh_token")

            if not access_token and not refresh_token:
                self.logger.warning(
                    "No access token or refresh token found. Connector may need OAuth flow completion."
                )

            # Initialize Google Client using build_from_services
            # This will handle token management and credential refresh automatically
            try:
                self.google_client = await GoogleClient.build_from_services(
                    service_name="drive",
                    logger=self.logger,
                    config_service=self.config_service,
                    is_individual=True,  # This is an individual connector
                    version="v3",
                    connector_instance_id=self.connector_id
                )

                # Create Google Drive Data Source from the client
                self.drive_data_source = GoogleDriveDataSource(
                    self.google_client.get_client(),
                    executor=self._drive_executor,
                )

                self.logger.info(
                    "✅ Google Drive client and data source initialized successfully"
                )
            except Exception as e:
                self.logger.error(
                    f"❌ Failed to initialize Google Drive client: {e}",
                    exc_info=True
                )
                raise ValueError(f"Failed to initialize Google Drive client: {e}") from e

            self.logger.info("✅ Google Drive connector initialized successfully")
            return True

        except Exception as ex:
            self.logger.error(f"❌ Error initializing Google Drive connector: {ex}", exc_info=True)
            raise

    async def _get_fresh_datasource(self) -> None:
        """
        Ensure drive_data_source has ALWAYS-FRESH OAuth credentials.

        Creates a new Credentials object when credentials change.
        After calling this, use self.drive_data_source directly.

        The datasource wraps a Google client by reference, so replacing
        the client's credentials automatically updates the datasource.
        """

        if not self.google_client or not self.drive_data_source:
            raise GoogleDriveError("Google client or drive data source not initialized. Call init() first.")

        async with self._datasource_refresh_lock:
            await refresh_google_datasource_credentials(
                google_client=self.google_client,
                data_source=self.drive_data_source,
                config_service=self.config_service,
                connector_id=self.connector_id,
                logger=self.logger,
                service_name="Drive"
            )

    async def _fresh_drive_data_source(self) -> GoogleDriveDataSource:
        """Refresh credentials and hand back the datasource, as a provider callable."""
        await self._get_fresh_datasource()
        return self.drive_data_source

    async def _process_drive_item(
        self,
        metadata: dict,
        user_id: str,
        user_email: str,
        drive_id: str,
        *,
        bypass_folder_filter: bool = False,
        permission_type: PermissionType = PermissionType.OWNER,
    ) -> Optional[RecordUpdate]:
        """
        Process a single Google Drive file and detect changes.

        Args:
            metadata: Google Drive file metadata dictionary
            user_id: The user's account ID
            user_email: The user's email
            drive_id: The drive ID
            bypass_folder_filter: Skip the folder-scope check. Only the placeholder
                sweep sets this: the ancestors it backfills are by definition
                outside the tracked subtree and would otherwise be rejected.
            permission_type: Access level to record for user_email on this item.
                Defaults to OWNER (My Drive); callers seeding items the user does
                not own (e.g. sharedWithMe) must pass the correct grant.

        Returns:
            RecordUpdate object or None if entry should be skipped
        """
        try:

            file_id = metadata.get("id")
            if not file_id:
                return None

            # Apply Folder Filter
            if not bypass_folder_filter and not pass_folder_filter(metadata, self._tracked_folder_ids):
                self.logger.debug(f"Skipping item {metadata.get('name', 'unknown')} (ID: {file_id}) due to folder filter.")
                return None  # Skip this item

            # Apply Date Filters
            if not self._pass_date_filters(metadata):
                self.logger.debug(f"Skipping item {metadata.get('name', 'unknown')} (ID: {file_id}) due to date filters.")
                return None  # Skip this item

            # Apply Extension Filters
            if not self._pass_extension_filter(metadata):
                self.logger.debug(f"Skipping item {metadata.get('name', 'unknown')} (ID: {file_id}) due to extension filters.")
                return None  # Skip this item

            org_id = self.data_entities_processor.org_id

            existing_record = await self.data_entities_processor.get_record_by_external_id(
                self.connector_id, file_id
            )

            # Detect changes
            is_new = existing_record is None
            is_updated = False
            metadata_changed = False
            content_changed = False
            permissions_changed = False

            if existing_record:
                if existing_record.record_name != metadata.get("name", "Untitled"):
                    metadata_changed = True
                    is_updated = True

                external_revision_id = metadata.get("headRevisionId") or metadata.get("version")
                if existing_record.external_revision_id != external_revision_id:
                    content_changed = True
                    is_updated = True

                if existing_record and drive_id != existing_record.external_record_group_id:
                    is_updated = True
                    metadata_changed = True

                parent_external_record_id = (metadata.get("parents") or [None])[0]
                if existing_record and parent_external_record_id != existing_record.parent_external_record_id:
                    is_updated = True
                    metadata_changed = True

            # Determine if it's a file or folder
            mime_type = metadata.get("mimeType", "")
            is_file = mime_type != MimeTypes.GOOGLE_DRIVE_FOLDER.value

            # Determine indexing status - shared files are not indexed by default
            is_shared = metadata.get("shared", False)

            # Get timestamps
            created_time = metadata.get("createdTime")
            modified_time = metadata.get("modifiedTime")
            timestamp_ms = get_epoch_timestamp_in_ms()
            source_created_at = int(parse_timestamp(created_time)) if created_time else timestamp_ms
            source_updated_at = int(parse_timestamp(modified_time)) if modified_time else timestamp_ms

            # Get file extension
            file_extension = metadata.get("fileExtension", None)
            if not file_extension:
                file_name = metadata.get("name", "")
                if "." in file_name:
                    file_extension = file_name.rsplit(".", 1)[-1].lower()

            parent_external_record_id = (metadata.get("parents") or [None])[0]

            # Create FileRecord directly
            file_record = FileRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                org_id=org_id,
                record_name=str(metadata.get("name", "Untitled")),
                record_type=RecordType.FILE,
                record_group_type=RecordGroupType.DRIVE.value,
                external_record_group_id=drive_id,
                external_record_id=str(file_id),
                external_revision_id=metadata.get("headRevisionId") or metadata.get("version", None),
                parent_external_record_id=parent_external_record_id if parent_external_record_id != drive_id else None,
                parent_record_type=RecordType.FILE if parent_external_record_id != drive_id else None,
                version=0 if is_new else (existing_record.version + 1 if existing_record else 0),
                origin=OriginTypes.CONNECTOR.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                created_at=timestamp_ms,
                updated_at=timestamp_ms,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
                weburl=metadata.get("webViewLink", None),
                mime_type=mime_type if mime_type else MimeTypes.UNKNOWN.value,
                is_file=is_file,
                size_in_bytes=int(metadata.get("size", 0) or 0),
                extension=file_extension,
                path=metadata.get("path", None),
                etag=metadata.get("etag", None),
                ctag=metadata.get("ctag", None),
                quick_xor_hash=metadata.get("quickXorHash", None),
                crc32_hash=metadata.get("crc32Hash", None),
                sha1_hash=metadata.get("sha1Checksum", None),
                sha256_hash=metadata.get("sha256Checksum", None),
                md5_hash=metadata.get("md5Checksum", None),
                is_shared=is_shared,
            )

            if existing_record and not content_changed:
                self.logger.debug(f"No content change for file {file_record.record_name} setting indexing status as prev value")
                file_record.parsing_status = existing_record.parsing_status
                file_record.indexing_status = existing_record.indexing_status
                file_record.extraction_status = existing_record.extraction_status

            # Handle Permissions
            new_permissions = [
                Permission(
                    external_id=user_id,
                    email=user_email,
                    type=permission_type,
                    entity_type=EntityType.USER
                )
            ]

            # Compare permissions
            old_permissions = []

            return RecordUpdate(
                record=file_record,
                is_new=is_new,
                is_updated=is_updated,
                is_deleted=False,
                metadata_changed=metadata_changed,
                content_changed=content_changed,
                permissions_changed=permissions_changed,
                old_permissions=old_permissions,
                new_permissions=new_permissions,
                external_record_id=file_id
            )

        except Exception as ex:
            self.logger.error(f"Error processing Google Drive file {metadata.get('id', 'unknown')}: {ex}", exc_info=True)
            return None

    def _parse_datetime(self, dt_obj) -> Optional[int]:
        """Parse datetime object or string to epoch timestamp in milliseconds."""
        if not dt_obj:
            return None
        try:
            if isinstance(dt_obj, str):
                dt = datetime.fromisoformat(dt_obj.replace('Z', '+00:00'))
            else:
                dt = dt_obj
            return int(dt.timestamp() * 1000)
        except Exception:
            return None

    def _pass_date_filters(self, metadata: dict) -> bool:
        """
        Checks if the Google Drive file passes the configured CREATED and MODIFIED date filters.
        Relies on client-side filtering since Google Drive API does not support date filtering.
        """
        # 1. ALWAYS Allow Folders
        # We must sync folders regardless of date to ensure the directory structure
        # exists for any new files that might be inside them.
        mime_type = metadata.get("mimeType", "")
        if mime_type == MimeTypes.GOOGLE_DRIVE_FOLDER.value:
            return True

        # 2. Check Created Date Filter
        created_filter = self.sync_filters.get(SyncFilterKey.CREATED)
        if created_filter:
            created_after_iso, created_before_iso = created_filter.get_datetime_iso()

            # Use _parse_datetime to get millisecond timestamps for easy comparison
            created_time = metadata.get("createdTime")
            item_ts = self._parse_datetime(created_time) if created_time else None
            start_ts = self._parse_datetime(created_after_iso)
            end_ts = self._parse_datetime(created_before_iso)

            if item_ts is not None:
                if start_ts and item_ts < start_ts:
                    return False
                if end_ts and item_ts > end_ts:
                    return False

        # 3. Check Modified Date Filter
        modified_filter = self.sync_filters.get(SyncFilterKey.MODIFIED)
        if modified_filter:
            modified_after_iso, modified_before_iso = modified_filter.get_datetime_iso()

            # Use _parse_datetime to get millisecond timestamps
            modified_time = metadata.get("modifiedTime")
            item_ts = self._parse_datetime(modified_time) if modified_time else None
            start_ts = self._parse_datetime(modified_after_iso)
            end_ts = self._parse_datetime(modified_before_iso)

            if item_ts is not None:
                if start_ts and item_ts < start_ts:
                    return False
                if end_ts and item_ts > end_ts:
                    return False

        return True

    def _pass_extension_filter(self, metadata: dict) -> bool:
        """
        Checks if the Google Drive file passes the configured file extensions filter.

        For MULTISELECT filters:
        - Operator IN: Only allow files with extensions in the selected list
        - Operator NOT_IN: Allow files with extensions NOT in the selected list

        Google-specific docs (Docs, Sheets, Slides) are filtered by mimeType,
        while other files are filtered by file extension.

        Folders always pass this filter to maintain directory structure.
        """
        # 1. ALWAYS Allow Folders
        mime_type = metadata.get("mimeType", "")
        if mime_type == MimeTypes.GOOGLE_DRIVE_FOLDER.value:
            return True

        # 2. Get the extensions filter
        extensions_filter = self.sync_filters.get(SyncFilterKey.FILE_EXTENSIONS)

        # If no filter configured or filter is empty, allow all files
        if extensions_filter is None or extensions_filter.is_empty():
            return True

        # 3. Get the list of allowed values from the filter
        allowed_values = extensions_filter.value
        if not isinstance(allowed_values, list):
            return True  # Invalid filter value, allow the file

        # 4. Check if this is a Google-specific doc (Docs, Sheets, Slides)
        google_doc_mime_types = [
            MimeTypes.GOOGLE_DOCS.value,
            MimeTypes.GOOGLE_SHEETS.value,
            MimeTypes.GOOGLE_SLIDES.value,
        ]

        if mime_type in google_doc_mime_types:
            # Filter Google docs by mimeType
            operator = extensions_filter.get_operator()
            operator_str = operator.value if hasattr(operator, 'value') else str(operator)

            if operator_str == FilterOperator.IN:
                # Only allow if mimeType is in the allowed list
                return mime_type in allowed_values
            elif operator_str == FilterOperator.NOT_IN:
                # Allow if mimeType is NOT in the excluded list
                return mime_type not in allowed_values
            return True

        # 5. For non-Google docs, filter by file extension
        # Get the file extension from the metadata
        # Try fileExtension field first, then extract from name
        file_extension = metadata.get("fileExtension", None)
        if not file_extension:
            file_name = metadata.get("name", "")
            if "." in file_name:
                file_extension = file_name.rsplit(".", 1)[-1].lower()
            else:
                file_extension = None

        # Files without extensions: behavior depends on operator
        # If using IN operator and file has no extension, it won't match any allowed extensions
        # If using NOT_IN operator and file has no extension, it passes (not in excluded list)
        if file_extension is None:
            operator = extensions_filter.get_operator()
            operator_str = operator.value if hasattr(operator, 'value') else str(operator)
            if operator_str == FilterOperator.NOT_IN:
                return True
            return False

        # Normalize extension (lowercase, without dots)
        file_extension = file_extension.lower().lstrip(".")

        # Normalize extensions (lowercase, without dots) - only for extension values
        # The allowed_values list contains both mimeType values and extension values
        # We need to check extensions separately
        normalized_extensions = [
            ext.lower().lstrip(".")
            for ext in allowed_values
            if not ext.startswith("application/vnd.google-apps")
        ]

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

    async def _process_drive_items_generator(
        self,
        files: List[dict],
        user_id: str,
        user_email: str,
        drive_id: str,
        *,
        bypass_folder_filter: bool = False,
        permission_type: PermissionType = PermissionType.OWNER,
    ) -> AsyncGenerator[Tuple[Optional[FileRecord], List[Permission], RecordUpdate], None]:
        """
        Process Google Drive files and yield records with their permissions.
        Generator for non-blocking processing of large datasets.

        Args:
            files: List of Google Drive file metadata
            user_id: The user's account ID
            user_email: The user's email
            drive_id: The drive ID
            bypass_folder_filter: Forwarded to `_process_drive_item`; set only by
                the placeholder sweep.
            permission_type: Forwarded to `_process_drive_item`; the access level
                to record for user_email on these items.
        """
        import asyncio

        for file_metadata in files:
            try:
                record_update = await self._process_drive_item(
                    file_metadata,
                    user_id,
                    user_email,
                    drive_id,
                    bypass_folder_filter=bypass_folder_filter,
                    permission_type=permission_type,
                )
                if record_update and record_update.record:
                    files_disabled = not self.indexing_filters.is_enabled(IndexingFilterKey.FILES, default=True)
                    shared_disabled = record_update.record.is_shared and not self.indexing_filters.is_enabled(IndexingFilterKey.SHARED, default=True)
                    if files_disabled or shared_disabled:
                        record_update.record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

                    yield (record_update.record, record_update.new_permissions or [], record_update)
                await asyncio.sleep(0)
            except Exception as e:
                self.logger.error(f"Error processing item in generator: {e}", exc_info=True)
                continue

    async def _handle_record_updates(self, record_update: RecordUpdate) -> None:
        """Handle different types of record updates (new, updated, deleted)."""
        try:
            if record_update.is_deleted:
                existing_record = await self.data_entities_processor.get_record_by_external_id(
                    self.connector_id, record_update.external_record_id
                )
                if existing_record is None:
                    self.logger.debug(
                        f"Received delete for untracked external id {record_update.external_record_id}; nothing to delete"
                    )
                    return
                self.logger.info("Deleting record: %s", existing_record.record_name)
                await self.data_entities_processor.on_record_deleted(
                    record_id=existing_record.id
                )
            elif record_update.is_new:
                self.logger.info(f"New record detected: {record_update.record.record_name}")
            elif record_update.is_updated:
                if record_update.metadata_changed:
                    self.logger.info(f"Metadata changed for record: {record_update.record.record_name}")
                    await self.data_entities_processor.on_record_metadata_update(record_update.record)

                if record_update.permissions_changed:
                    self.logger.info(f"Permissions changed for record: {record_update.record.record_name}")
                    await self.data_entities_processor.on_updated_record_permissions(
                        record_update.record,
                        record_update.new_permissions
                    )

                if record_update.content_changed:
                    self.logger.info(f"Content changed for record: {record_update.record.record_name}")
                    await self.data_entities_processor.on_record_content_update(record_update.record)

        except Exception as e:
            self.logger.error(f"Error handling record updates: {e}", exc_info=True)
            raise

    async def _apply_folder_scope_to_change(
        self, file_metadata: dict, changes_ids: set
    ) -> List[dict]:
        """
        Resolve one changed item against the folder scope, returning the metadata to sync.

        Empty when the item is out of scope (any record it left behind is deleted),
        the item alone when it is in scope, or the item plus its descendants when a
        folder just moved into scope — changes_list reports the folder itself but
        nothing inside it.
        """
        file_id = file_metadata.get("id")
        file_name = file_metadata.get("name")

        if not pass_folder_filter(file_metadata, self._tracked_folder_ids):
            await self._delete_on_scope_exit(file_id, file_name)
            return []

        items = [file_metadata]

        is_folder = file_metadata.get("mimeType") == MimeTypes.GOOGLE_DRIVE_FOLDER.value
        if (
            self._tracked_folder_ids
            and is_folder
            and file_id not in self._folder_seed_ids
            and await has_entered_scope(
                self.data_entities_processor,
                self.connector_id,
                file_id,
                self._tracked_folder_ids,
            )
        ):
            self.logger.info(
                f"📁 Folder {file_name} entered folder-filter scope; fetching descendants"
            )
            async for child_batch in fetch_folder_children(
                file_id,
                changes_ids,
                self._fresh_drive_data_source,
                fields=DRIVE_PERSONAL_SYNC_FILES_LIST_FIELDS,
            ):
                items.extend(child_batch)

        return items

    async def _delete_on_scope_exit(
        self, file_id: str, file_name: Optional[str]
    ) -> None:
        """
        Delete the record left behind by an item that moved out of the tracked folder
        scope. A folder takes its whole subtree with it.
        """
        exited_scope, existing_record = await has_exited_scope(
            self.data_entities_processor,
            self.connector_id,
            file_id,
            self._tracked_folder_ids,
        )
        if not exited_scope:
            self.logger.debug(
                f"Item {file_name} outside folder filter and has no tracked record; nothing to do"
            )
            return

        if existing_record.mime_type == MimeTypes.GOOGLE_DRIVE_FOLDER.value:
            self.logger.info(
                "📁 Folder %s exited folder-filter scope; deleting folder and descendants",
                existing_record.record_name,
            )
            result = await self.data_entities_processor.on_records_deleted_cascade(
                [existing_record.id], self.connector_id
            )
            total_deleted = len((result or {}).get("deleted_records") or [])
            self.logger.info(
                "Deleted folder %s and %d descendant(s)",
                existing_record.record_name,
                max(total_deleted - 1, 0),
            )
        else:
            self.logger.info(
                "File %s exited folder-filter scope; deleting record",
                existing_record.record_name,
            )
            await self.data_entities_processor.on_record_deleted(
                record_id=existing_record.id
            )

    async def _expand_folder_scope(self) -> None:
        """
        Resolve the configured seed folders into the full set of folder IDs to sync
        (seeds plus every descendant subfolder this account can enumerate).

        A seed the account cannot see, or cannot list the children of, contributes
        nothing to the scope, so it is reported rather than silently narrowing the
        sync to nothing.
        """
        if not self._folder_seed_ids:
            return

        frontier: List[str] = []
        for folder_id in self._folder_seed_ids:
            probe = await probe_can_list_children(
                folder_id, self._fresh_drive_data_source, self.logger
            )
            if probe is None:
                self.logger.warning(
                    f"📁 Seed folder {folder_id} is not visible to this account; "
                    "its subtree will not be synced"
                )
                continue
            if not probe.can_list_children:
                self._blocked_folder_ids.add(folder_id)
                self.logger.warning(
                    f"📁 Seed folder {folder_id} cannot be listed by this account; "
                    "its subtree will not be synced"
                )
                continue
            frontier.append(folder_id)

        if not frontier:
            return

        expansion = await build_tracked_folder_ids(
            frontier, self._fresh_drive_data_source, self.logger
        )

        self._tracked_folder_ids |= expansion.tracked
        self._blocked_folder_ids |= expansion.blocked

        self.logger.info(
            f"📁 Folder filter active: {len(self._tracked_folder_ids)} tracked folder(s)"
        )
        if self._blocked_folder_ids:
            self.logger.warning(
                f"📁 {len(self._blocked_folder_ids)} folder(s) cannot be listed by this "
                f"account, so their subtrees are not in scope: "
                f"{sorted(self._blocked_folder_ids)}"
            )

    async def _sweep_placeholder_records(
        self, user_id: str, user_email: str, drive_id: str
    ) -> None:
        """Backfill the ancestor breadcrumb for placeholder stubs left unreconciled.

        The folder_ids filter doesn't respect hierarchy: a selected folder is synced
        while its Drive ancestors are filtered out, leaving stubs keyed by the
        ancestors' file ids with no real name, url or permissions.

        This is an ancestor-closure walk over child->parent pointers, implemented as a
        frontier BFS with a ``visited`` set (dedup + cycle guard) and a boundary that
        stops at ancestors already materialized as real records or at My Drive root:

          - seed the frontier from the stubs this sync left behind (one DB query);
          - fetch each frontier level from source (bounded concurrency) and sync it as
            a normal folder record — only folders can be parents in Drive, and folders
            carry no content, so nothing out-of-scope becomes indexable;
          - expand to each fetched record's parent, skipping ones already visited or
            already real in the graph, until the frontier drains.

        Reconciliation is keyed by external id and idempotent, so an interrupted sweep
        is completed by the next sync's sweep.
        """
        visited: set = set()
        seeds = await self.data_entities_processor.get_placeholder_records(self.connector_id)
        frontier: List[Record] = []
        for stub in seeds:
            if stub.external_record_id not in visited:
                visited.add(stub.external_record_id)
                frontier.append(stub)

        total = 0
        while frontier:
            self.logger.info(f"📁 Placeholder sweep: backfilling {len(frontier)} ancestor(s)")
            metadata_by_id = await fetch_ancestor_metadata(
                frontier,
                self._fresh_drive_data_source,
                self.logger,
                fields=DRIVE_PERSONAL_SYNC_FILE_RESOURCE_FIELDS,
                concurrency=ANCESTOR_FETCH_CONCURRENCY,
            )

            backfills: List[Tuple[Record, List[Permission]]] = []
            async for record, permissions, _update in self._process_drive_items_generator(
                list(metadata_by_id.values()),
                user_id,
                user_email,
                drive_id,
                bypass_folder_filter=True,
            ):
                record.is_placeholder = False
                backfills.append((record, permissions))

            for stub in frontier:
                if stub.external_record_id in metadata_by_id:
                    continue
                # Source fetch failed (inaccessible/deleted). Re-submit the persisted
                # stub anyway so its structural edges — record group (BELONGS_TO) and
                # parent (PARENT_CHILD) — which a full sync deletes are still restored.
                # Keeps it a stub; access fails closed (inherits the record group's perms).
                stub.is_placeholder = True
                backfills.append((stub, []))

            if backfills:
                # Creates/updates the ancestors and, via _handle_parent_record, materializes
                # the next level's parent stubs so we can pick them up below.
                await self.data_entities_processor.on_new_records(backfills)

            next_frontier: List[Record] = []
            for record, _permissions in backfills:
                parent_ext_id = record.parent_external_record_id
                if not parent_ext_id or not record.parent_record_type:
                    continue  # boundary: My Drive root
                if parent_ext_id in visited:
                    continue
                visited.add(parent_ext_id)
                parent_record = await self.data_entities_processor.get_record_by_external_id(
                    self.connector_id, parent_ext_id
                )
                if parent_record is None:
                    continue  # stub should exist after on_new_records; skip defensively
                if not parent_record.is_placeholder:
                    continue  # boundary: parent already synced in scope — nothing to backfill
                next_frontier.append(parent_record)

            total += len(frontier)
            if total > PLACEHOLDER_SWEEP_SAFETY_MAX:
                self.logger.error(
                    f"Placeholder sweep exceeded safety bound ({PLACEHOLDER_SWEEP_SAFETY_MAX}); aborting"
                )
                break
            frontier = next_frontier

    async def _sync_user_personal_drive(self, drive_id: str) -> None:
        """
        Sync user's personal Google Drive.

        If sync point doesn't exist, performs full sync using files_list.
        If sync point exists, performs incremental sync using changes_list.
        """
        if not self.drive_data_source:
            self.logger.error("Drive data source not initialized")
            return

        # Get user info
        fields = 'user(displayName,emailAddress,permissionId)'
        await self._get_fresh_datasource()
        user_about = await self.drive_data_source.about_get(fields=fields)
        user_id = user_about.get('user', {}).get('permissionId')
        user_email = user_about.get('user', {}).get('emailAddress')

        if not user_id or not user_email:
            self.logger.error("Failed to get user information")
            return

        sync_point_key = "personal_drive"
        org_id = self.data_entities_processor.org_id

        # Resolve the folder scope on every sync (full and incremental) so new and
        # deleted subfolders at the source are reflected before items are filtered below.
        await self._expand_folder_scope()

        # Check if sync point exists
        sync_point_data = await self.drive_delta_sync_point.read_sync_point(sync_point_key)
        page_token = sync_point_data.get("pageToken") if sync_point_data else None

        if not page_token:
            # Full sync: no sync point exists
            self.logger.info("🆕 Starting full sync for Google Drive (no sync point found)")
            await self._perform_full_sync(sync_point_key, org_id, user_id, user_email, drive_id)
        else:
            # Incremental sync: sync point exists
            self.logger.info("🔄 Starting incremental sync for Google Drive")
            await self._perform_incremental_sync(sync_point_key, org_id, user_id, user_email, page_token, drive_id)

        # Backfill placeholder ancestors that out-of-scope sync filters left unreconciled.
        await self._sweep_placeholder_records(user_id, user_email, drive_id)

    async def _perform_full_sync(self, sync_point_key: str, org_id: str, user_id: str, user_email: str, drive_id: str) -> None:
        """
        Perform full sync by fetching all files using files_list.

        Args:
            sync_point_key: Key for storing sync point
            org_id: Organization ID
            user_id: User's account ID
            user_email: User's email
            drive_id: Drive ID
        """
        try:
            # Get start page token for future incremental syncs
            await self._get_fresh_datasource()
            start_token_response = await self.drive_data_source.changes_get_start_page_token()
            start_page_token = start_token_response.get("startPageToken")

            if not start_page_token:
                self.logger.error("Failed to get start page token")
                return

            self.logger.info(f"📋 Start page token: {start_page_token[:20]}...")

            # Fetch all files with pagination
            page_token = None
            total_files = 0
            batch_records = []
            batch_size = self.batch_size

            while True:
                # Prepare files_list parameters
                # Using fields that match the working example from drive_user_service.py
                # Note: etag, ctag, quickXorHash, crc32Hash are not available in files.list() - they require files.get()
                list_params = {
                    "q": "trashed=false",
                    "fields": DRIVE_PERSONAL_SYNC_FILES_LIST_FIELDS,
                }

                if page_token:
                    list_params["pageToken"] = page_token

                # Fetch files
                self.logger.info(f"📥 Fetching files page (token: {page_token[:20] if page_token else 'initial'}...)")
                await self._get_fresh_datasource()
                files_response = await self.drive_data_source.files_list(**list_params)

                files = files_response.get("files", [])

                if not files:
                    self.logger.info("No more files to process")
                    break

                # Process files using generator
                async for record, perms, update in self._process_drive_items_generator(
                    files,
                    user_id,
                    user_email,
                    drive_id
                ):
                    if update.is_deleted:
                        await self._handle_record_updates(update)
                        continue
                    elif update.is_updated:
                        await self._handle_record_updates(update)
                        continue
                    else:
                        batch_records.append((record, perms))
                        total_files += 1

                        # Process in batches
                        if len(batch_records) >= batch_size:
                            self.logger.info(f"💾 Processing batch of {len(batch_records)} records")
                            await self.data_entities_processor.on_new_records(batch_records)
                            batch_records = []
                            await asyncio.sleep(0)

                # Check for next page
                page_token = files_response.get("nextPageToken")
                if not page_token:
                    break

            # Process remaining records
            if batch_records:
                self.logger.info(f"💾 Processing final batch of {len(batch_records)} records")
                await self.data_entities_processor.on_new_records(batch_records)

            # Seed shared drive items shared individually with this user. Runs before the
            # page token is stored so a failure here replays on the next run instead of
            # being skipped for good; afterwards changes_list carries the deltas.
            total_files += await self._sync_shared_with_me(user_id, user_email, drive_id)

            # Save start page token to sync point for future incremental syncs
            await self.drive_delta_sync_point.update_sync_point(
                sync_point_key,
                {"pageToken": start_page_token}
            )

            self.logger.info(f"✅ Full sync completed. Processed {total_files} files. Saved page token: {start_page_token[:20]}...")

        except Exception as e:
            self.logger.error(f"❌ Error during full sync: {e}", exc_info=True)
            raise

    async def _sync_shared_with_me(self, user_id: str, user_email: str, drive_id: str) -> int:
        """
        Seed items that live in a shared drive and were shared individually with this user.

        The full sync above runs with Drive's defaults, which drop every shared drive item
        regardless of how access was granted. Scoping this to `sharedWithMe` rather than
        setting includeItemsFromAllDrives on that listing keeps the seed to individual
        grants: shared drive membership leaves sharedWithMeTime unset, so drives the user
        belongs to are not enumerated here.

        Returns:
            Number of records queued for processing.
        """
        self.logger.info("Syncing shared with me items")

        batch_records = []
        total_files = 0
        page_token = None
        # Persists across pages so a subtree already pulled in behind one shared folder
        # is not re-walked when an overlapping/nested share surfaces on a later page.
        seen_ids: set = set()

        while True:
            list_params = {
                "q": "sharedWithMe = true and trashed = false",
                "supportsAllDrives": True,
                "includeItemsFromAllDrives": True,
                "fields": DRIVE_PERSONAL_SYNC_FILES_LIST_FIELDS,
            }

            if page_token:
                list_params["pageToken"] = page_token

            await self._get_fresh_datasource()
            files_response = await self.drive_data_source.files_list(**list_params)

            # Items without a driveId are personal-drive shares, which the full sync
            # listing already returned. Already-seen ids are dropped too, in case an
            # earlier page's folder expansion already pulled this item in.
            files = [
                file_metadata
                for file_metadata in files_response.get("files", [])
                if file_metadata.get("driveId")
                and file_metadata.get("id") not in seen_ids
            ]

            # A shared folder arrives without its contents: sharedWithMeTime marks only
            # the item actually shared, and Drive has no recursive parent operator.
            # drive_scoped=False keeps the walk on the user corpus, since corpora=drive
            # needs shared drive membership and this path does not have it.
            seen_ids.update(file_id for f in files if (file_id := f.get("id")))
            for folder in [f for f in files if f.get("mimeType") == MimeTypes.GOOGLE_DRIVE_FOLDER.value]:
                found = []
                try:
                    async for child_batch in fetch_folder_children(
                        folder["id"],
                        seen_ids,
                        self._fresh_drive_data_source,
                        fields=DRIVE_PERSONAL_SYNC_FILES_LIST_FIELDS,
                        drive_scoped=False,
                    ):
                        found.extend(child_batch)
                except HttpError as e:
                    if (
                        e.resp.status in (HttpStatusCode.FORBIDDEN.value, HttpStatusCode.NOT_FOUND.value)
                        and not is_retryable_403(e)
                    ):
                        # Folder genuinely gone or access revoked since it was listed above;
                        # there is nothing to replay, so this is safe to skip permanently.
                        self.logger.warning(
                            f"Shared folder {folder['id']} no longer accessible (HTTP {e.resp.status}); skipping"
                        )
                        continue
                    # Anything else (rate limiting -- including a 403 with a
                    # rateLimitExceeded/userRateLimitExceeded reason -- transient 5xx,
                    # etc.) must not be swallowed: the sync-point save below would then
                    # permanently skip this folder's descendants since incremental sync
                    # never replays them.
                    self.logger.error(
                        f"Failed to list children of shared folder {folder['id']}: {e}"
                    )
                    raise
                except Exception as e:
                    self.logger.error(
                        f"Failed to list children of shared folder {folder['id']}: {e}"
                    )
                    raise

                seen_ids.update(c["id"] for c in found if c.get("id"))
                files.extend(found)

            if files:
                async for record, perms, update in self._process_drive_items_generator(
                    files,
                    user_id,
                    user_email,
                    drive_id,
                    permission_type=PermissionType.READ,
                ):
                    if update.is_deleted or update.is_updated:
                        await self._handle_record_updates(update)
                        continue

                    batch_records.append((record, perms))
                    total_files += 1

                    if len(batch_records) >= self.batch_size:
                        self.logger.info(f"💾 Processing batch of {len(batch_records)} shared with me records")
                        await self.data_entities_processor.on_new_records(batch_records)
                        batch_records = []
                        await asyncio.sleep(0)

            # Paging is driven by the token alone: an empty page here only means everything
            # on it was filtered out, not that the listing is exhausted.
            page_token = files_response.get("nextPageToken")
            if not page_token:
                break

        if batch_records:
            self.logger.info(f"💾 Processing final batch of {len(batch_records)} shared with me records")
            await self.data_entities_processor.on_new_records(batch_records)

        self.logger.info(f"✅ Synced {total_files} shared with me item(s)")
        return total_files

    async def _perform_incremental_sync(self, sync_point_key: str, org_id: str, user_id: str, user_email: str, page_token: str, drive_id: str) -> None:
        """
        Perform incremental sync by fetching changes using changes_list.

        Args:
            sync_point_key: Key for storing sync point
            org_id: Organization ID
            user_id: User's account ID
            user_email: User's email
            page_token: Page token from sync point
            drive_id: Drive ID
        """
        try:
            current_page_token = page_token
            total_changes = 0
            batch_records = []
            batch_size = self.batch_size

            while True:
                # Prepare changes_list parameters
                changes_params = {
                    "pageToken": current_page_token,
                    "pageSize": 1000,
                    "includeRemoved": True,
                    "restrictToMyDrive": False,  # Include shared files
                    "supportsAllDrives": True,
                    "includeItemsFromAllDrives": True,
                    # Specify fields to retrieve
                    "fields": "nextPageToken, newStartPageToken, changes(fileId, removed, file(id, name, mimeType, size, createdTime, modifiedTime, webViewLink, fileExtension, headRevisionId, version, shared, trashed, md5Checksum, sha1Checksum, sha256Checksum, parents, owners, permissions))",
                }

                # Fetch changes
                self.logger.info(f"📥 Fetching changes page (token: {current_page_token[:20]}...)")
                await self._get_fresh_datasource()
                changes_response = await self.drive_data_source.changes_list(**changes_params)

                self.logger.info(f"changes_response keys: {changes_response.keys()}")

                changes = changes_response.get("changes", [])

                # All non-removed file ids in this changes page. Used to dedupe
                # against recursively-fetched folder children below: a newly
                # created subtree reports every node in changes_list, so each
                # descendant must be skipped during recursion to avoid processing
                # it twice.
                changes_ids = {
                    change["file"]["id"]
                    for change in changes
                    if not change.get("removed") and change.get("file") and change["file"].get("id")
                }

                # Extract files from changes
                files = []
                for change in changes:
                    is_removed = change.get("removed", False)
                    file_metadata = change.get("file")
                    file_name = (file_metadata or {}).get("name")
                    is_trashed = bool(file_metadata and file_metadata.get("trashed"))

                    if is_removed or is_trashed:
                        # Trashing is treated the same as a permanent delete: Drive
                        # reports moves to Trash as a normal (removed=False) change
                        # carrying trashed=true on the file, not as a `removed` change.
                        file_id = change.get("fileId") or (file_metadata or {}).get("id")
                        if file_id:
                            if is_trashed and not is_removed:
                                self.logger.info("🗑️ Item (%s) is trashed", file_name if file_name else file_id)
                            if is_removed:
                                self.logger.info("🗑️ Item (%s) is deleted", file_id)
                            deleted_update = RecordUpdate(
                                record=None,
                                is_new=False,
                                is_updated=False,
                                is_deleted=True,
                                metadata_changed=False,
                                content_changed=False,
                                permissions_changed=False,
                                external_record_id=file_id
                            )
                            await self._handle_record_updates(deleted_update)
                        continue

                    if file_metadata:
                        if not file_metadata.get("id"):
                            self.logger.warning(
                                "Skipping Drive change whose file metadata has no id"
                            )
                            continue

                        files.extend(
                            await self._apply_folder_scope_to_change(
                                file_metadata, changes_ids
                            )
                        )

                # Process files using generator (only if there are files)
                if files:
                    async for record, perms, update in self._process_drive_items_generator(
                        files,
                        user_id,
                        user_email,
                        drive_id
                    ):
                        if update.is_deleted:
                            await self._handle_record_updates(update)
                            continue
                        elif update.is_updated:
                            self.logger.info(f"📝 Record updated: {record.record_name}")
                            await self._handle_record_updates(update)
                            continue
                        else:
                            batch_records.append((record, perms))
                            total_changes += 1

                            # Process in batches
                            if len(batch_records) >= batch_size:
                                self.logger.info(f"💾 Processing batch of {len(batch_records)} records")
                                await self.data_entities_processor.on_new_records(batch_records)
                                batch_records = []
                                await asyncio.sleep(0)

                # Get next page token
                next_page_token = changes_response.get("nextPageToken")
                new_start_page_token = changes_response.get("newStartPageToken")

                if next_page_token:
                    # More pages to fetch
                    current_page_token = next_page_token
                    self.logger.info(f"📄 More pages available, continuing with token: {current_page_token[:20]}...")
                elif new_start_page_token:
                    # Sync complete, save the new start token for next sync
                    current_page_token = new_start_page_token
                    self.logger.info(f"✅ Sync complete, new start token: {current_page_token[:20]}...")
                    break
                else:
                    self.logger.warning("⚠️ No nextPageToken or newStartPageToken found")
                    break

            # Process remaining records
            if batch_records:
                self.logger.info(f"💾 Processing final batch of {len(batch_records)} records")
                await self.data_entities_processor.on_new_records(batch_records)

            # Update sync point with latest page token
            if current_page_token and current_page_token != page_token:
                self.logger.info(f"💾 Updating sync point from {page_token[:20]}... to {current_page_token[:20]}...")
                await self.drive_delta_sync_point.update_sync_point(
                    sync_point_key,
                    {"pageToken": current_page_token}
                )
                self.logger.info(f"✅ Incremental sync completed. Processed {total_changes} changes.")
            else:
                self.logger.warning("⚠️ Sync point not updated (token unchanged or invalid)")

        except Exception as e:
            self.logger.error(f"❌ Error during incremental sync: {e}", exc_info=True)
            raise


    async def test_connection_and_access(self) -> bool:
        """Test connection and access to Google Drive."""
        try:
            self.logger.info("Testing connection and access to Google Drive")
            if not self.drive_data_source:
                self.logger.error("Drive data source not initialized. Call init() first.")
                return False

            if not self.google_client:
                self.logger.error("Google client not initialized. Call init() first.")
                return False

            # Try to make a simple API call to test connection
            # For now, just check if client is initialized
            if self.google_client.get_client() is None:
                self.logger.warning("Google Drive API client not initialized")
                return False

            return True
        except Exception as e:
            self.logger.error(f"❌ Error testing connection and access to Google Drive: {e}")
            return False

    async def _stream_google_api_request(self, request, error_context: str = "download") -> AsyncGenerator[bytes, None]:
        """
        Helper function to stream data from a Google API request.

        Args:
            request: Google API request object (from files().get_media() or files().export_media())
            error_context: Context string for error messages (e.g., "PDF export", "file export")
        Yields:
            bytes: File content from the request
        """
        buffer = io.BytesIO()
        try:
            downloader = MediaIoBaseDownload(buffer, request, chunksize=_DRIVE_DOWNLOAD_CHUNK_SIZE)
            done = False

            while not done:
                try:
                    # next_chunk() performs the HTTP range request synchronously, so
                    # calling it here would freeze the event loop for the whole
                    # round-trip and stall every other request in the process.
                    _, done = await self.drive_data_source.execute(
                        downloader.next_chunk
                    )
                except HttpError as http_error:
                    self.logger.error(f"HTTP error during {error_context}: {str(http_error)}")
                    raise HTTPException(
                        status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                        detail=f"Error during {error_context}: {str(http_error)}",
                    )
                except Exception as chunk_error:
                    self.logger.error(f"Error during {error_context}: {str(chunk_error)}")
                    raise HTTPException(
                        status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                        detail=f"Error during {error_context}",
                    )

                buffer.seek(0)
                content = buffer.read()
                if content:
                    yield content

                # Clear buffer for next chunk
                buffer.seek(0)
                buffer.truncate(0)
        except HTTPException:
            raise
        except Exception as stream_error:
            self.logger.error(f"Error in {error_context} stream: {str(stream_error)}")
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail=f"Error setting up {error_context} stream",
            )
        finally:
            buffer.close()

    async def _convert_to_pdf(self, file_path: str, temp_dir: str) -> str:
        """Helper function to convert file to PDF"""
        pdf_path = os.path.join(temp_dir, f"{Path(file_path).stem}.pdf")

        try:
            conversion_cmd = [
                "soffice",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                temp_dir,
                file_path,
            ]
            process = await asyncio.create_subprocess_exec(
                *conversion_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Add timeout to communicate
            try:
                conversion_output, conversion_error = await asyncio.wait_for(
                    process.communicate(), timeout=30.0
                )
            except asyncio.TimeoutError:
                # Make sure to terminate the process if it times out
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    process.kill()  # Force kill if termination takes too long
                self.logger.error("LibreOffice conversion timed out after 30 seconds")
                raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="PDF conversion timed out")

            if process.returncode != 0:
                error_msg = f"LibreOffice conversion failed: {conversion_error.decode('utf-8', errors='replace')}"
                self.logger.error(error_msg)
                raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Failed to convert file to PDF")

            if os.path.exists(pdf_path):
                return pdf_path
            else:
                raise HTTPException(
                    status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="PDF conversion failed - output file not found"
                )
        except asyncio.TimeoutError:
            # This catch is for any other timeout that might occur
            self.logger.error("Timeout during PDF conversion")
            raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="PDF conversion timed out")
        except Exception as conv_error:
            self.logger.error(f"Error during conversion: {str(conv_error)}")
            raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail="Error converting file to PDF")

    async def _get_file_metadata_from_drive(self, file_id: str) -> Dict:
        """
        Get file metadata from Google Drive API via ``files.get``.

        Args:
            file_id: Google Drive file ID

        Returns:
            Dictionary with file metadata from Drive
        """
        try:
            drive_service = self.google_client.get_client()
            metadata_request = drive_service.files().get(
                fileId=file_id,
                fields=DRIVE_PERSONAL_SYNC_FILE_RESOURCE_FIELDS,
                supportsAllDrives=True,
            )
            return await self.drive_data_source.execute(metadata_request.execute)
        except HttpError as http_error:
            self.logger.error(f"Error fetching file metadata from Drive: {str(http_error)}")
            if http_error.resp.status == HttpStatusCode.NOT_FOUND.value:
                raise HTTPException(
                    status_code=HttpStatusCode.NOT_FOUND.value,
                    detail="File not found in Google Drive"
                )
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail=f"Error fetching file metadata: {str(http_error)}"
            )
        except Exception as e:
            self.logger.error(f"Error getting file metadata: {str(e)}")
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail=f"Error getting file metadata: {str(e)}"
            )

    def get_signed_url(self, record: Record) -> Optional[str]:
        """Get a signed URL for a specific record."""
        raise NotImplementedError("get_signed_url is not yet implemented for Google Drive")

    async def stream_record(self, record: Record, convertTo: Optional[str] = None) -> StreamingResponse:
        """
        Stream a record from Google Drive.

        Args:
            record: Record object containing file information
            convertTo: Optional format to convert to (e.g., "application/pdf")

        Returns:
            StreamingResponse with file content
        """
        try:
            # Extract file information from record
            file_id = record.external_record_id
            file_name = record.record_name

            if not file_id:
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_REQUEST.value,
                    detail="File ID not found in record"
                )

            self.logger.info(f"Streaming Drive file: {file_id}, convertTo: {convertTo}")

            # Get drive service
            drive_service = self.google_client.get_client()

            # Get file metadata from Drive API
            file_metadata = await self._get_file_metadata_from_drive(file_id)
            mime_type = file_metadata.get("mimeType", "application/octet-stream")

            # Handle Google Workspace files (they need to be exported, not downloaded)
            # Map Google Workspace mime types to export formats
            google_workspace_export_formats = {
                "application/vnd.google-apps.spreadsheet": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # Excel format
                "application/vnd.google-apps.document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # Word format
                "application/vnd.google-apps.presentation": "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # PowerPoint format
            }

            # Check if PDF conversion is requested for Google Workspace files
            if convertTo == MimeTypes.PDF.value and mime_type in google_workspace_export_formats:
                self.logger.info(f"Exporting Google Workspace file ({mime_type}) directly to PDF")

                request = drive_service.files().export_media(fileId=file_id, mimeType="application/pdf")
                return create_stream_record_response(
                    self._stream_google_api_request(request, error_context="PDF export"),
                    filename=file_name,
                    mime_type="application/pdf",
                    fallback_filename=f"record_{record.id}",
                )

            # Regular export for Google Workspace files (not PDF conversion)
            if mime_type in google_workspace_export_formats:
                export_mime_type = google_workspace_export_formats[mime_type]
                self.logger.info(f"Exporting Google Workspace file ({mime_type}) to {export_mime_type}")

                # Export and stream the file
                request = drive_service.files().export_media(fileId=file_id, mimeType=export_mime_type)

                # Determine the appropriate file extension and media type for the response
                export_media_types = {
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
                }

                response_media_type, file_ext = export_media_types.get(export_mime_type, (export_mime_type, ""))
                file_name if file_name.endswith(file_ext) else f"{file_name}{file_ext}"

                return create_stream_record_response(
                    self._stream_google_api_request(request, error_context="Google Workspace file export"),
                    filename=file_name,
                    mime_type=response_media_type,
                    fallback_filename=f"record_{record.id}",
                )

            # Check if PDF conversion is requested (for regular files only, Google Workspace handled above)
            if convertTo == MimeTypes.PDF.value:
                self.logger.info(f"Converting file to PDF: {file_name}")
                # For regular files, download and convert to PDF
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_file_path = os.path.join(temp_dir, file_name)

                    # Download file to temp directory
                    try:
                        with open(temp_file_path, "wb") as f:
                            request = drive_service.files().get_media(
                                fileId=file_id,
                                supportsAllDrives=True,
                            )
                            downloader = MediaIoBaseDownload(f, request, chunksize=_DRIVE_DOWNLOAD_CHUNK_SIZE)

                            done = False
                            while not done:
                                status, done = await self.drive_data_source.execute(
                                    downloader.next_chunk
                                )
                                self.logger.info(
                                    f"Download {int(status.progress() * 100)}%."
                                )
                    except HttpError as http_error:
                        # Check if this is a Google Workspace file that can't be downloaded directly
                        if http_error.resp.status == HttpStatusCode.FORBIDDEN.value:
                            error_details = http_error.error_details if hasattr(http_error, 'error_details') else []
                            for detail in error_details:
                                if detail.get('reason') == 'fileNotDownloadable':
                                    self.logger.error(
                                        f"Google Workspace file cannot be downloaded for PDF conversion: {str(http_error)}"
                                    )
                                    raise HTTPException(
                                        status_code=HttpStatusCode.BAD_REQUEST.value,
                                        detail="Google Workspace files (Sheets, Docs, Slides) cannot be converted to PDF using direct download. Please use the file's native export functionality.",
                                    )
                        raise

                    # Convert to PDF
                    pdf_path = await self._convert_to_pdf(temp_file_path, temp_dir)
                    self.logger.info(f"PDF file converted: {pdf_path}")
                    # Create async generator to properly handle file cleanup
                    async def file_iterator() -> AsyncGenerator[bytes, None]:
                        try:
                            with open(pdf_path, "rb") as pdf_file:
                                yield await asyncio.to_thread(pdf_file.read)
                        except Exception as e:
                            self.logger.error(f"Error reading PDF file: {str(e)}")
                            raise HTTPException(
                                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                                detail="Error reading converted PDF file",
                            )

                    return create_stream_record_response(
                        file_iterator(),
                        filename=file_name,
                        mime_type="application/pdf",
                        fallback_filename=f"record_{record.id}",
                    )

            # Regular file download without conversion
            # StreamingResponse will handle chunking automatically
            request = drive_service.files().get_media(
                fileId=file_id,
                supportsAllDrives=True,
            )
            return create_stream_record_response(
                self._stream_google_api_request(request, error_context="file download"),
                filename=file_name,
                mime_type=mime_type,
                fallback_filename=f"record_{record.id}",
            )

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Error streaming record: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail=f"Error streaming file: {str(e)}"
            )

    async def _create_personal_record_group(self, user_id: str, user_email: str, display_name: str, drive_id: str) -> RecordGroup:
        """Create a personal record group for the user."""
        # Fetch root drive info to get the actual drive ID

        record_group = RecordGroup(
            name=display_name,
            group_type=RecordGroupType.DRIVE.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            external_group_id=drive_id,
        )

        permissions = [Permission(external_id=user_id, email=user_email, type=PermissionType.OWNER, entity_type=EntityType.USER)]
        await self.data_entities_processor.on_new_record_groups([(record_group, permissions)])
        return record_group

    async def _create_app_user(self, user_about: Dict) -> None:
        try:

            user = AppUser(
                email=user_about.get('user').get('emailAddress'),
                full_name=user_about.get('user').get('displayName'),
                source_user_id=user_about.get('user').get('permissionId'),
                app_name=self.connector_name,
                connector_id=self.connector_id
            )
            await self.data_entities_processor.on_new_app_users([user])
        except Exception as e:
            self.logger.error(f"❌ Error creating app user: {e}", exc_info=True)
            raise

    async def run_sync(self) -> None:

        self.logger.info("Starting sync for Google Drive Individual")

        self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "drive", self.connector_id, self.logger
            )

        # Reset the folder scope on every run so filter edits, folder moves and new
        # subfolders at the source are re-resolved rather than served from last run.
        folder_ids_filter = self.sync_filters.get_value(SyncFilterKey.FOLDER_IDS)
        self._folder_seed_ids = set(folder_ids_filter or ())
        self._tracked_folder_ids = set(self._folder_seed_ids)
        self._blocked_folder_ids = set()
        if self._folder_seed_ids:
            self.logger.info(
                f"📁 Folder filter active with {len(self._folder_seed_ids)} seed folder(s)"
            )

        # Fetch app user
        fields = 'user(displayName,emailAddress,permissionId),storageQuota(limit,usage,usageInDrive)'
        await self._get_fresh_datasource()
        user_about = await self.drive_data_source.about_get(fields=fields)
        await self._create_app_user(user_about)

        # Create user personal drive
        display_name = f"Google Drive - {user_about.get('user').get('emailAddress')}"
        drive_info = await self.drive_data_source.files_get(
            fileId="root",
            supportsAllDrives=True,
            fields=DRIVE_PERSONAL_SYNC_FILE_RESOURCE_FIELDS,
        )
        drive_id = drive_info.get("id")

        if not drive_id:
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail="Failed to get drive ID"
            )
        await self._create_personal_record_group(
            user_about.get('user').get('permissionId'),
            user_about.get('user').get('emailAddress'),
            display_name,
            drive_id
        )

        # Sync user's personal drive
        await self._sync_user_personal_drive(drive_id=drive_id)

        self.logger.info("Sync completed for Google Drive Individual")

    async def run_incremental_sync(self) -> None:
        """Run incremental sync for Google Drive."""
        self.logger.info("run_incremental_sync not implemented for Google Drive")

    def handle_webhook_notification(self, notification: Dict) -> None:
        """Handle webhook notifications from Google Drive."""
        raise NotImplementedError("handle_webhook_notification is not yet implemented for Google Drive")

    async def cleanup(self) -> None:
        """Cleanup resources when shutting down the connector."""
        try:
            self.logger.info("Cleaning up Google Drive connector resources")

            await self._release_thread_lease()

            # Clear client and data source references
            if hasattr(self, 'drive_data_source') and self.drive_data_source:
                self.drive_data_source = None

            if hasattr(self, 'google_client') and self.google_client:
                self.google_client = None

            # Clear config
            self.config = None

            self.logger.info("Google Drive connector cleanup completed")

        except Exception as e:
            self.logger.error(f"❌ Error during cleanup: {e}")

    async def reindex_records(self, records: List[Record]) -> None:
        """Reindex records for Google Drive."""
        try:
            if not records:
                self.logger.info("No records to reindex")
                return

            self.logger.info(f"Starting reindex for {len(records)} Google Drive records")

            if not self.drive_data_source:
                self.logger.error("Drive data source not initialized. Call init() first.")
                raise Exception("Drive data source not initialized. Call init() first.")

            # Get user information
            fields = 'user(displayName,emailAddress,permissionId)'
            await self._get_fresh_datasource()
            user_about = await self.drive_data_source.about_get(fields=fields)
            user_id = user_about.get('user', {}).get('permissionId')
            user_email = user_about.get('user', {}).get('emailAddress')

            if not user_id or not user_email:
                self.logger.error("Failed to get user information")
                raise Exception("Failed to get user information")

            # Check records at source for updates
            org_id = self.data_entities_processor.org_id
            updated_records = []
            non_updated_records = []
            for record in records:
                try:
                    updated_record_data = await self._check_and_fetch_updated_record(org_id, record, user_id, user_email)
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

            # Publish reindex events for non updated records
            if non_updated_records:
                await self.data_entities_processor.reindex_existing_records(non_updated_records)
                self.logger.info(f"Published reindex events for {len(non_updated_records)} non updated records")
        except Exception as e:
            self.logger.error(f"Error during Google Drive reindex: {e}", exc_info=True)
            raise

    async def _check_and_fetch_updated_record(
        self, org_id: str, record: Record, user_id: str, user_email: str
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch record from Google Drive and return data for reindexing if changed."""
        try:
            file_id = record.external_record_id
            record_group_id = record.external_record_group_id

            if not file_id:
                self.logger.warning(f"Missing file_id for record {record.id}")
                return None

            # Use record_group_id if available, otherwise use user_id (for personal drive)
            if not record_group_id:
                record_group_id = user_id

            # Fetch fresh file from Google Drive API
            try:
                await self._get_fresh_datasource()
                file_metadata = await self.drive_data_source.files_get(
                    fileId=file_id,
                    supportsAllDrives=True,
                    fields=DRIVE_PERSONAL_SYNC_FILE_RESOURCE_FIELDS,
                )
            except HttpError as e:
                if e.resp.status == HttpStatusCode.NOT_FOUND.value:
                    self.logger.warning(f"File {file_id} not found at source")
                    return None
                raise

            if not file_metadata:
                self.logger.warning(f"File {file_id} not found at source")
                return None

            # Use existing logic to detect changes and transform to FileRecord
            record_update = await self._process_drive_item(
                file_metadata,
                user_id,
                user_email,
                record_group_id
            )

            if not record_update or record_update.is_deleted:
                return None

            # Only return data if there's an actual update (metadata, content, or permissions)
            if record_update.is_updated:
                self.logger.info(f"Record {file_id} has changed at source. Updating.")
                # Ensure we keep the internal DB ID
                record_update.record.id = record.id
                return (record_update.record, record_update.new_permissions)

            return None

        except Exception as e:
            self.logger.error(f"Error checking Google Drive record {record.id} at source: {e}")
            return None

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> FilterOptionsResponse:
        """Google Drive connector does not support dynamic filter options."""
        raise NotImplementedError("Google Drive connector does not support dynamic filter options")

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
        **kwargs,
    ) -> BaseConnector:
        """Create a new instance of the Google Drive connector."""
        return cls(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
