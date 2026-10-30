import asyncio
import mimetypes
import uuid
from datetime import datetime, timezone
from logging import Logger
from typing import AsyncGenerator, Dict, List, NoReturn, Optional, Tuple, Union

from aiolimiter import AsyncLimiter

# Dropbox SDK specific types
from dropbox.users import FullAccount
from dropbox.files import (
    DeletedMetadata,
    FileMetadata,
    FolderMetadata,
)
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

# Base connector and service imports
from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    PermissionModel,
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.core.base.connector.connector_service import (
    BaseConnector,
    oauth_app_not_linked_error,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
    generate_record_sync_point_key,
)
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import (
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
    SyncStrategy,
)
from app.connectors.core.constants import CONNECTOR_EMAIL_IDENTITY_INFO
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperator,
    IndexingFilterKey,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.core.constants import (
    IconPaths,
)
# App-specific Dropbox client imports
from app.connectors.sources.dropbox_individual.common.apps import DropboxIndividualApp
from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage
from app.connectors.sources.dropbox.deletes import (
    DeleteOutcome,
    apply_deleted_entry,
    apply_out_of_scope_entry,
    apply_replaced_entry,
    item_still_exists,
    recheck_owed_deletes,
)
from app.connectors.sources.dropbox.errors import is_auth_error, is_cursor_reset
from app.connectors.sources.dropbox.owed import OwedDeletes
from app.connectors.sources.dropbox.parents import ParentResolver
from app.connectors.sources.dropbox.paths import repath_descendants
from app.connectors.sources.dropbox.reconcile import remove_unlisted
from app.connectors.sources.dropbox.sync_health import SyncHealth
from app.connectors.sources.dropbox.sync_notices import SyncNotices
from app.connectors.sources.dropbox.weblinks import WebLinks
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate

# Model imports
from app.models.entities import (
    AppUser,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import Permission
from app.sources.client.dropbox.dropbox_ import (
    DropboxClient,
    DropboxResponse,
    DropboxTokenConfig,
)
from app.sources.client.dropbox.endpoint import dropbox_api_base_url
from app.sources.external.dropbox.dropbox_ import DropboxDataSource
from app.connectors.core.base.error.stream_errors import (
    connector_not_ready,
    not_downloadable,
    not_found_at_source,
    raise_for_stream_fetch,
    to_stream_error,
)
from app.utils.streaming import create_stream_record_response, stream_content


# Helper functions (reused from team connector)
def get_parent_path_from_path(path: str) -> Optional[str]:
    """Extracts the parent path from a file/folder path."""
    if not path or path == "/" or "/" not in path.lstrip("/"):
        return None # Root directory has no parent path in this context
    parent_path = "/".join(path.strip("/").split("/")[:-1])
    return f"/{parent_path}" if parent_path else "/"


def get_file_extension(filename: str) -> Optional[str]:
    """Extracts the extension from a filename."""
    if "." in filename:
        parts = filename.split(".")
        if len(parts) > 1:
            return parts[-1].lower()
    return None


def get_mimetype_enum_for_dropbox(entry: Union[FileMetadata, FolderMetadata]) -> MimeTypes:
    """
    Determines the correct MimeTypes enum member for a Dropbox API entry.

    Args:
        entry: A FileMetadata or FolderMetadata object from the Dropbox SDK.

    Returns:
        The corresponding MimeTypes enum member.
    """
    # 1. Handle folders directly
    if isinstance(entry, FolderMetadata):
        return MimeTypes.FOLDER

    # 2. Handle files by guessing the type from the filename
    if isinstance(entry, FileMetadata):
        # The '.paper' extension is a special Dropbox file type. We can handle it explicitly
        # or let it fall through to the default binary type if not in the enum.
        if entry.name.endswith('.paper'):
            # Assuming .paper files are a form of web content.
            return MimeTypes.HTML

        # Use the mimetypes library to guess from the extension

        mime_type_str, _ = mimetypes.guess_type(entry.name)

        if mime_type_str:
            try:
                # 3. Attempt to convert the guessed string into our MimeTypes enum
                return MimeTypes(mime_type_str)
            except ValueError:
                # 4. If the guessed type is not in our enum (e.g., 'application/zip'),
                # fall back to the generic binary type.
                return MimeTypes.BIN

    # 5. Fallback for any unknown file type or if mimetypes fails
    return MimeTypes.BIN

# @dataclass
# class RecordUpdate:
#     """Track updates to a record"""
#     record: Optional[FileRecord]
#     is_new: bool
#     is_updated: bool
#     is_deleted: bool
#     metadata_changed: bool
#     content_changed: bool
#     permissions_changed: bool
#     old_permissions: Optional[List[Permission]] = None
#     new_permissions: Optional[List[Permission]] = None
#     external_record_id: Optional[str] = None

@ConnectorBuilder("Dropbox Personal")\
    .in_group("Cloud Storage")\
    .with_description("Sync files and folders from Dropbox Personal account")\
    .with_categories(["Storage"])\
    .with_scopes([ConnectorScope.PERSONAL.value])\
    .with_permission_model(PermissionModel.APP_LEVEL)\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Dropbox Personal",
            authorize_url="https://www.dropbox.com/oauth2/authorize",
            token_url="https://api.dropboxapi.com/oauth2/token",
            redirect_uri="connectors/oauth/callback/Dropbox%20Personal",
            scopes=OAuthScopeConfig(
                personal_sync=[
                    "account_info.read",
                    "files.content.read",
                    "files.metadata.read",
                    "sharing.read",
                    "sharing.write"
                ],
                team_sync=[],
                agent=[]
            ),
            fields=[
                CommonFields.client_id("Dropbox App Console"),
                CommonFields.client_secret("Dropbox App Console")
            ],
            icon_path=IconPaths.connector_icon(Connectors.DROPBOX.value),
            app_group="Cloud Storage",
            app_description="OAuth application for accessing Dropbox Personal account API",
            app_categories=["Storage"],
            token_access_type="offline"
        )
    ])\
    .with_info(CONNECTOR_EMAIL_IDENTITY_INFO)\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.DROPBOX.value))
        .with_realtime_support(True)
        .add_documentation_link(DocumentationLink(
            "Dropbox App Setup",
            "https://developers.dropbox.com/oauth-guide",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/dropbox/dropbox_personal',
            'pipeshub'
        ))
        .add_filter_field(CommonFields.modified_date_filter("Filter files and folders by modification date."))
        .add_filter_field(CommonFields.created_date_filter("Filter files and folders by creation date."))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .add_filter_field(CommonFields.file_extension_filter())
        .with_webhook_config(True, ["file.added", "file.modified", "file.deleted"])
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .add_sync_custom_field(CommonFields.batch_size_field())
        .with_sync_support(True)
        .with_agent_support(False)
    )\
    .build_decorator()
class DropboxIndividualConnector(BaseConnector):
    """
    Connector for synchronizing data from a Dropbox Individual account.
    Simplified version without team-specific features.
    """

    current_user_id: Optional[str] = None
    current_user_email: Optional[str] = None

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

        """Initialize the Dropbox Individual connector."""

        super().__init__(
            DropboxIndividualApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )

        self.connector_name = Connectors.DROPBOX_PERSONAL
        self.connector_id = connector_id

        # Sync point (Only RECORDS needed for individual)
        # We inline the logic here because we only create one.
        self.dropbox_cursor_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=self.data_entities_processor.org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=self.data_store_provider
        )

        # NOTE: We do NOT initialize user_sync_point or user_group_sync_point
        # because individual accounts do not sync a member directory.

        self.data_source: Optional[DropboxDataSource] = None
        self.batch_size = 100
        self.rate_limiter = AsyncLimiter(50, 1)  # 50 requests per second

        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()
        self._health = SyncHealth()
        self._owed = OwedDeletes(self.dropbox_cursor_sync_point)
        self._web_links: WebLinks | None = None
        self._moved_folders: list[tuple[str, str, str]] = []

    async def init(self) -> bool:
        """
        Initializes the Dropbox client using credentials from the config service.
        Sets up client for individual account (is_team=False).
        """
        config = await self.config_service.get_config(
            f"/services/connectors/{self.connector_id}/config"
        )
        if not config:
            self.logger.error("Dropbox Individual access token not found in configuration.")
            return False

        credentials_config = config.get("credentials")
        access_token = credentials_config.get("access_token")
        refresh_token = credentials_config.get("refresh_token")

        auth_config = config.get("auth") or {}
        oauth_config_id = auth_config.get("oauthConfigId")

        if not oauth_config_id:
            self.logger.error(
                "Dropbox Individual has no oauthConfigId in its auth config; it reads its "
                "client ID and secret only from a linked OAuth app."
            )
            raise oauth_app_not_linked_error()

        oauth_config = await self._fetch_oauth_config_by_id(
            oauth_config_id=oauth_config_id,
            connector_type=Connectors.DROPBOX_PERSONAL.value,
            auth_config=auth_config,
        )

        if not oauth_config:
            self.logger.error(f"Linked OAuth app not found for Dropbox Individual connector {self.connector_id}.")
            return False

        # Use credentials from OAuth config
        oauth_config_data = oauth_config.get("config", {})
        app_key = oauth_config_data.get("clientId") or oauth_config_data.get("client_id")
        app_secret = oauth_config_data.get("clientSecret") or oauth_config_data.get("client_secret")
        self.logger.info(f"Using linked OAuth app for Dropbox Individual connector {self.connector_id}")

        try:
            config = DropboxTokenConfig(
                token=access_token,
                refresh_token=refresh_token,
                app_key=app_key,
                app_secret=app_secret
            )
            client = await DropboxClient.build_with_config(config, is_team=False)
            self.data_source = DropboxDataSource(client)
            if dropbox_api_base_url():
                self.logger.warning(
                    "Dropbox calls for this connector go to %s (DROPBOX_API_BASE_URL), not to Dropbox: "
                    "only a test deployment should set it.", dropbox_api_base_url()
                )
            self.logger.info("Dropbox Individual client initialized successfully.")
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize Dropbox Individual client: {e}", exc_info=True)
            return False


    async def _get_current_user_info(self) -> Tuple[str, str]:
        """
        Fetches the current user's account information.
        Returns:
            Tuple of (account_id, email)
        """
        # Check if we already have the current user info
        if self.current_user_id and self.current_user_email:
            return self.current_user_id, self.current_user_email

        # Fetch the current user info from the Dropbox API
        response = await self.data_source.users_get_current_account()

        if not response:
            raise ValueError("Failed to retrieve account information (empty response).")

        if not response.success:
            error_detail = response.error or "Unknown Dropbox API error"
            self.logger.error("Dropbox API rejected users_get_current_account: %s", error_detail)
            raise ValueError(f"Failed to retrieve account information: {error_detail}")

        if not response.data:
            raise ValueError("Failed to retrieve account information (no payload).")

        self.current_user_id = response.data.account_id
        self.current_user_email = response.data.email

        return self.current_user_id, self.current_user_email

    def _get_current_user_as_app_user(self, account_data) -> AppUser:
        """
        Converts the current user's Dropbox account data to an AppUser.

        Args:
            account_data: The FullAccount object from Dropbox API

        Returns:
            AppUser object
        """
        # Extract display name from account data
        # FullAccount has a 'name' attribute with display_name
        full_name = getattr(account_data.name, 'display_name', None) if hasattr(account_data, 'name') else None
        if not full_name:
            # Fallback to email if name is not available
            full_name = account_data.email.split('@')[0]

        return AppUser(
            app_name=self.connector_name,
            connector_id=self.connector_id,
            source_user_id=account_data.account_id,
            full_name=full_name,
            email=account_data.email,
            is_active=True,  # Individual accounts are always active
            title=None  # Individual accounts don't have roles
        )


    async def _process_dropbox_entry(
        self,
        entry: Union[FileMetadata, FolderMetadata, DeletedMetadata],
        user_id: str,
        user_email: str,
        record_group_id: str,
        modified_after: Optional[datetime] = None,
        modified_before: Optional[datetime] = None,
        created_after: Optional[datetime] = None,
        created_before: datetime | None = None,
        *,
        parents: ParentResolver | None = None,
    ) -> Optional[RecordUpdate]:
        """
        Turn one listed entry into the record to write, and say what changed. A delete is applied here and gives
        None. Records of a personal connector carry no grants: the connector's creator reaches all of them.
        """
        try:
            # A delete has no id, dates or extension to filter on.
            if isinstance(entry, DeletedMetadata):
                outcome = await apply_deleted_entry(
                    self.data_entities_processor, self.connector_id, entry.path_lower,
                    self._item_still_exists, self.logger, record_group_id=record_group_id,
                )
                self._note_delete_outcome(outcome)
                if parents is not None:
                    parents.note_gone(entry.path_lower)
                return None

            if not (self._pass_date_filters(entry, modified_after, modified_before, created_after, created_before)
                    and self._pass_extension_filter(entry)):
                self.logger.debug(f"Skipping item {entry.name} (ID: {entry.id}): outside the sync filters.")
                self._note_delete_outcome(await apply_out_of_scope_entry(
                    self.data_entities_processor, self.connector_id, entry.id, self.logger,
                ))
                return None

            existing_record = await self.data_entities_processor.get_record_by_external_id(
                self.connector_id, entry.id
            )
            is_file = isinstance(entry, FileMetadata)
            if existing_record is None:
                # Another item stored at this path was replaced (deleted, and this one made in its place).
                self._note_delete_outcome(await apply_replaced_entry(
                    self.data_entities_processor, self.connector_id, entry.path_lower, entry.id, record_group_id,
                    self._item_still_exists, self.logger,
                ))
            elif not is_file:
                stored_file = await self.data_entities_processor.get_file_record_by_id(existing_record.id)
                old_path = getattr(stored_file, "path", None)
                if old_path and old_path != entry.path_lower:
                    self._moved_folders.append((entry.id, old_path, entry.path_lower))

            if is_file:
                timestamp_ms = int(entry.server_modified.timestamp() * 1000)
                created_ms = int((getattr(entry, "client_modified", None) or entry.server_modified).timestamp() * 1000)
            elif existing_record is not None:
                # Folders carry no dates; keep the stored ones instead of moving them on every sync.
                timestamp_ms = existing_record.source_updated_at or existing_record.updated_at
                created_ms = existing_record.source_created_at or existing_record.created_at
            else:
                timestamp_ms = created_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

            if parents is not None:
                parent_external_record_id = await parents.parent_id(entry.path_lower)
            else:
                parent_external_record_id = await self._ask_parent_id(get_parent_path_from_path(entry.path_lower))

            weburl = await self._links().link_for(entry, stored=existing_record.weburl if existing_record else None)

            file_record = FileRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                record_name=entry.name,
                record_type=RecordType.FILE,
                record_group_type=RecordGroupType.DRIVE.value,
                external_record_group_id=record_group_id, # This is the User's personal drive ID
                external_record_id=entry.id,
                external_revision_id=entry.rev if is_file else None,
                # The processor sets the version: it moves on a content change only.
                version=0,
                origin=OriginTypes.CONNECTOR.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                created_at=created_ms,
                updated_at=timestamp_ms,
                source_created_at=created_ms,
                source_updated_at=timestamp_ms,
                weburl=weburl,
                parent_external_record_id=parent_external_record_id,
                parent_record_type=RecordType.FILE if parent_external_record_id else None,
                size_in_bytes=entry.size if is_file else 0,
                is_file=is_file,
                preview_renderable=is_file,
                extension=get_file_extension(entry.name) if is_file else None,
                path=entry.path_lower,
                mime_type=get_mimetype_enum_for_dropbox(entry),
                sha256_hash=entry.content_hash if is_file and hasattr(entry, 'content_hash') else None,
            )

            file_record.rewrite_permissions = True
            file_record.inherit_permissions = True

            content_changed = bool(existing_record) and is_file and existing_record.external_revision_id != entry.rev
            return RecordUpdate(
                record=file_record,
                is_new=existing_record is None,
                is_updated=existing_record is not None,
                is_deleted=False,
                metadata_changed=bool(existing_record) and existing_record.record_name != entry.name,
                content_changed=content_changed,
                permissions_changed=True,
                old_permissions=[],
                new_permissions=[],
                external_record_id=entry.id
            )

        except Exception as ex:
            self.logger.error(f"Error processing Dropbox entry {getattr(entry, 'id', entry.path_lower)}: {ex}", exc_info=True)
            self._health.skipped(f"entry {getattr(entry, 'path_lower', '?')}", ex)
            return None

    def _note_delete_outcome(self, outcome: DeleteOutcome) -> None:
        """A delete Dropbox could not confirm is owed: the cursor moves on, and Dropbox lists the change only once."""
        if outcome.unsure_external_ids:
            self._owed.add(outcome.unsure_external_ids)
        if outcome.failed:
            self._health.skipped("a delete", "could not be written; it is tried again on the next run")

    def _links(self) -> WebLinks:
        if self._web_links is None or self._web_links.data_source is not self.data_source:
            self._web_links = WebLinks(self.data_source, self.logger)
        return self._web_links

    async def _stored_id_at(self, path_lower: str, record_group_id: str) -> str | None:
        for record in await self.data_entities_processor.get_records_by_file_path(self.connector_id, path_lower):
            if record.external_record_group_id == record_group_id:
                return record.external_record_id
        return None

    async def _ask_parent_id(self, parent_path: str | None) -> str | None:
        if not parent_path or parent_path == "/":
            return None
        metadata = await self.data_source.files_get_metadata(parent_path)
        found = item_still_exists(metadata)
        if found is None:
            # Unknown is not "no parent": the entry would be written at the root and Dropbox lists it once.
            raise RuntimeError(f"could not read the parent folder {parent_path}: {metadata.error}")
        return metadata.data.id if found else None

    async def _item_still_exists(self, record: Record) -> bool | None:
        """Whether a deleted path's record still resolves by id (a move also reports a delete at the old path)."""
        return item_still_exists(await self.data_source.files_get_metadata(path=record.external_record_id))

    async def _process_dropbox_items_generator(
        self,
        entries: List[Union[FileMetadata, FolderMetadata, DeletedMetadata]],
        user_id: str,
        user_email: str,
        record_group_id: str,
        modified_after: Optional[datetime] = None,
        modified_before: Optional[datetime] = None,
        created_after: Optional[datetime] = None,
        created_before: datetime | None = None,
        *,
        parents: ParentResolver | None = None,
    ) -> AsyncGenerator[Tuple[Optional[FileRecord], List[Permission], RecordUpdate], None]:
        """
        Process Dropbox entries and yield records with their permissions.
        Generator for non-blocking processing of large datasets.

        Args:
            entries: List of Dropbox file/folder metadata entries
            user_id: The user's account ID
            user_email: The user's email
            record_group_id: The record group ID (user's drive ID)
            modified_after: Only include files modified after this date
            modified_before: Only include files modified before this date
            created_after: Only include files created after this date
            created_before: Only include files created before this date
        """
        if parents is not None:
            # A page may list a child before its folder.
            for entry in entries:
                if isinstance(entry, FolderMetadata):
                    parents.note_folder(entry.path_lower, entry.id)
        for entry in entries:
            try:
                record_update = await self._process_dropbox_entry(
                    entry,
                    user_id,
                    user_email,
                    record_group_id,
                    modified_after=modified_after,
                    modified_before=modified_before,
                    created_after=created_after,
                    created_before=created_before,
                    parents=parents,
                )
                if record_update and record_update.record:
                    if not self.indexing_filters.is_enabled(IndexingFilterKey.FILES, default=True):
                        record_update.record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                    if record_update.record.is_shared and not self.indexing_filters.is_enabled(IndexingFilterKey.SHARED, default=True):
                        record_update.record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

                    yield (record_update.record, record_update.new_permissions or [], record_update)
                await asyncio.sleep(0)
            except Exception as e:
                self.logger.error(f"Error processing item in generator: {e}", exc_info=True)
                self._health.skipped(f"entry {getattr(entry, 'path_lower', '?')}", e)
                continue

    def _pass_date_filters(
        self,
        entry: Union[FileMetadata, FolderMetadata, DeletedMetadata],
        modified_after: Optional[datetime] = None,
        modified_before: Optional[datetime] = None,
        created_after: Optional[datetime] = None,
        created_before: Optional[datetime] = None
    ) -> bool:
        """
        Returns True if entry PASSES filters (should be kept)

        Note: Dropbox only provides `server_modified` (modification date) for files.
        For `client_modified` (creation/upload date), we use it as the "created" date.
        Folders don't have date metadata, so they are never filtered out.

        Args:
            entry: The Dropbox file/folder metadata
            modified_after: Skip files modified before this date
            modified_before: Skip files modified after this date
            created_after: Skip files created before this date
            created_before: Skip files created after this date

        Returns:
            False if the entry should be skipped, True otherwise
        """
        # Folders don't have date metadata - never filter them out
        if not isinstance(entry, FileMetadata):
            return True

        # No filters applied
        if not any([modified_after, modified_before, created_after, created_before]):
            return True

        # Get the dates from the entry
        # server_modified = last time the file was modified on Dropbox
        # client_modified = modification time set by the desktop client when file was added
        server_modified = entry.server_modified
        client_modified = getattr(entry, 'client_modified', None)

        # Ensure timezone awareness for comparison
        if server_modified and server_modified.tzinfo is None:
            server_modified = server_modified.replace(tzinfo=timezone.utc)
        if client_modified and client_modified.tzinfo is None:
            client_modified = client_modified.replace(tzinfo=timezone.utc)

        # Apply modified date filters (using server_modified)
        if server_modified:
            if modified_after and server_modified < modified_after:
                self.logger.debug(f"Skipping {entry.name}: modified {server_modified} before cutoff {modified_after}")
                return False
            if modified_before and server_modified > modified_before:
                self.logger.debug(f"Skipping {entry.name}: modified {server_modified} after cutoff {modified_before}")
                return False

        # Apply created date filters (using client_modified as proxy for creation date)
        # If client_modified is not available, fall back to server_modified
        created_date = client_modified or server_modified
        if created_date:
            if created_after and created_date < created_after:
                self.logger.debug(f"Skipping {entry.name}: created {created_date} before cutoff {created_after}")
                return False
            if created_before and created_date > created_before:
                self.logger.debug(f"Skipping {entry.name}: created {created_date} after cutoff {created_before}")
                return False

        return True

    def _pass_extension_filter(self, entry: Union[FileMetadata, FolderMetadata, DeletedMetadata]) -> bool:
        """
        Checks if the Dropbox entry passes the configured file extensions filter.

        For MULTISELECT filters:
        - Operator IN: Only allow files with extensions in the selected list
        - Operator NOT_IN: Allow files with extensions NOT in the selected list

        Folders and deleted items always pass this filter to maintain directory structure.

        Args:
            entry: The Dropbox file/folder/deleted metadata

        Returns:
            True if the entry passes the filter (should be kept), False otherwise
        """
        # 1. ALWAYS Allow Folders and Deleted items
        # We must sync folders regardless of extension to ensure the directory structure
        # exists for any files that might be inside them.
        # Deleted items should pass through so deletions are processed.
        if not isinstance(entry, FileMetadata):
            return True

        # 2. Get the extensions filter
        extensions_filter = self.sync_filters.get(SyncFilterKey.FILE_EXTENSIONS)

        # If no filter configured or filter is empty, allow all files
        if extensions_filter is None or extensions_filter.is_empty():
            return True

        # 3. Get the file extension from the entry name
        # The extension is stored without the dot (e.g., "pdf", "docx")
        file_extension = None
        if entry.name and "." in entry.name:
            file_extension = entry.name.rsplit(".", 1)[-1].lower()

        # 4. Handle files without extensions
        if file_extension is None:
            operator = extensions_filter.get_operator()
            operator_str = operator.value if hasattr(operator, 'value') else str(operator)
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

    def _get_date_filters(self) -> Tuple[Optional[datetime], Optional[datetime], Optional[datetime], Optional[datetime]]:
        """
        Extract date filter values from sync_filters.

        Returns:
            Tuple of (modified_after, modified_before, created_after, created_before)
        """
        modified_after: Optional[datetime] = None
        modified_before: Optional[datetime] = None
        created_after: Optional[datetime] = None
        created_before: Optional[datetime] = None

        # Get modified date filter
        modified_date_filter = self.sync_filters.get(SyncFilterKey.MODIFIED)
        if modified_date_filter and not modified_date_filter.is_empty():
            after_iso, before_iso = modified_date_filter.get_datetime_iso()
            if after_iso:
                modified_after = datetime.fromisoformat(after_iso).replace(tzinfo=timezone.utc)
                self.logger.info(f"Applying modified date filter: after {modified_after}")
            if before_iso:
                modified_before = datetime.fromisoformat(before_iso).replace(tzinfo=timezone.utc)
                self.logger.info(f"Applying modified date filter: before {modified_before}")

        # Get created date filter
        created_date_filter = self.sync_filters.get(SyncFilterKey.CREATED)
        if created_date_filter and not created_date_filter.is_empty():
            after_iso, before_iso = created_date_filter.get_datetime_iso()
            if after_iso:
                created_after = datetime.fromisoformat(after_iso).replace(tzinfo=timezone.utc)
                self.logger.info(f"Applying created date filter: after {created_after}")
            if before_iso:
                created_before = datetime.fromisoformat(before_iso).replace(tzinfo=timezone.utc)
                self.logger.info(f"Applying created date filter: before {created_before}")

        return modified_after, modified_before, created_after, created_before

    async def _run_sync_with_cursor(self, user_id: str, user_email: str) -> None:
        """
        List the account from its cursor and write what changed.

        A page is written before its cursor is saved, so a run cut off part way resumes there. A cursor Dropbox
        reset is dropped and the account listed from the start; once that listing has finished, what it no longer
        shows is checked and removed. A page that cannot be read ends the listing for this run and is recorded,
        so the run is not reported as a success and a full sync removes nothing.
        """
        # _v2: a listing written by an earlier version is read once more from the start, and what it no longer
        # shows is removed, to repair what that version stored (paths left by moves, records of replaced files).
        sync_point_key = generate_record_sync_point_key(RecordType.DRIVE.value, "personal_drive_v2", user_id)
        sync_point = await self.dropbox_cursor_sync_point.read_sync_point(sync_point_key)
        cursor = sync_point.get('cursor')
        older = cursor is None and bool((await self.dropbox_cursor_sync_point.read_sync_point(
            generate_record_sync_point_key(RecordType.DRIVE.value, "personal_drive", user_id))).get('cursor'))

        self.logger.info(f"Starting sync for {user_email}. Cursor exists: {bool(cursor)}")

        modified_after, modified_before, created_after, created_before = self._get_date_filters()
        parents = ParentResolver(
            stored_id_at=lambda path: self._stored_id_at(path, user_id),
            ask_dropbox=self._ask_parent_id,
        )
        problems_before = len(self._health.problems)
        # A listing from the start (a full sync) shows what exists, not what was deleted since the last cursor,
        # which Dropbox reports only once: what it does not show is checked once it ends.
        relisting = older or cursor is None
        listed: set[str] = set()
        held = restart = False

        while True:
            try:
                async with self.rate_limiter:
                    if cursor:
                        result = await self.data_source.files_list_folder_continue(cursor)
                    else:
                        result = await self.data_source.files_list_folder(path="", recursive=True)
            except Exception as e:  # the data source normally answers failures, it does not raise
                result = DropboxResponse(success=False, error=str(e))

            if not result.success:
                if cursor and is_cursor_reset(result.error):
                    self.logger.warning(f"Dropbox reset the cursor of {user_email}; listing it again from the start")
                    self._health.relisted.append(f"Dropbox of {user_email}")
                    relisting, cursor = True, None
                    listed.clear()
                    continue
                if is_auth_error(result.error):
                    self._health.auth_failed(result.error)
                    return
                self.logger.error(f"Dropbox List Folder failed: {result.error}")
                self._health.skipped(f"listing of {user_email}'s Dropbox", result.error)
                return

            entries = result.data.entries
            page_problems = len(self._health.problems)
            updates = []
            async for _record, _perms, update in self._process_dropbox_items_generator(
                entries,
                user_id,
                user_email,
                user_id,
                modified_after=modified_after,
                modified_before=modified_before,
                created_after=created_after,
                created_before=created_before,
                parents=parents,
            ):
                updates.append(update)
            if relisting:
                listed.update(getattr(e, "id", None) for e in entries if not isinstance(e, DeletedMetadata))
            # In listing order, in one batch: a moved file and the folder made for it land together.
            if updates:
                await self.data_entities_processor.on_new_records([(u.record, []) for u in updates])
            moved, self._moved_folders = self._moved_folders, []
            for external_id, old_path, new_path in moved:
                _, done = await repath_descendants(self.data_entities_processor, self.connector_id, external_id,
                                                   old_path, new_path, self.logger)
                if not done:
                    # Listed again from the start, which writes every path below it. Saved now: replayed from an
                    # older cursor, the move would no longer show.
                    self._health.skipped(f"paths below {new_path}", "could not all be rewritten; listed again next run")
                    restart = True
                    await self.dropbox_cursor_sync_point.update_sync_point(sync_point_key, {'cursor': None})

            cursor = result.data.cursor
            # Dropbox lists a change once: after a page with a failed entry the saved cursor stays before it, and
            # the next run reads it again (its writes are idempotent). The pages after it are still written.
            held = held or len(self._health.problems) > page_problems
            if not (held or restart):
                await self.dropbox_cursor_sync_point.update_sync_point(sync_point_key, {'cursor': cursor})
            if result.data.has_more is not True:
                break

        if relisting and len(self._health.problems) == problems_before:
            self._note_delete_outcome(await remove_unlisted(
                self.data_entities_processor, self.connector_id, user_id, listed, self._item_still_exists,
                self.logger,
            ))

    async def _handle_record_updates(self, record_update: RecordUpdate) -> None:
        """Write one re-read record (a reindex that found it changed), or delete one."""
        if record_update.is_deleted:
            # The update carries Dropbox's id; records are deleted by their key.
            existing = await self.data_entities_processor.get_record_by_external_id(
                self.connector_id, record_update.external_record_id
            )
            if existing:
                await self.data_entities_processor.on_record_deleted(record_id=existing.id)
            return
        if record_update.record is not None:
            await self.data_entities_processor.on_new_records([(record_update.record, [])])

    def full_sync_coverage(self) -> FullSyncCoverage:
        """A run that skipped part of Dropbox did not see everything, so a full sync must remove nothing."""
        return FullSyncCoverage(complete=self._health.complete)

    async def run_sync(self) -> None:
        """
        Runs a sync of the Dropbox individual account, from its cursor (or in full after a full-sync request).

        A run that could not read part of Dropbox, or whose credentials Dropbox refused, raises at the end: it is
        recorded as failed, a full sync removes nothing, and the creator is told once.
        """
        self._health = SyncHealth()
        try:
            await self._sync_steps()
        except Exception as ex:
            self.logger.error(f"❌ Error in Dropbox Individual connector run: {ex}", exc_info=True)
            if is_auth_error(ex):
                self._health.auth_failed(ex)
            else:
                self._health.skipped("the sync", ex)
            raise
        finally:
            self.last_sync_summary = {"skipped": len(self._health.problems), "relisted": len(self._health.relisted)}
            await SyncNotices(self).report(self._health)
        self._health.raise_if_incomplete()

    async def _sync_steps(self) -> None:
        self.logger.info("🚀 Starting Dropbox Individual Sync")

        self.sync_filters, self.indexing_filters = await load_connector_filters(
            self.config_service, "dropboxpersonal", self.connector_id, self.logger
        )

        # 1. The connected account, and who may read it: the connector's creator only.
        response = await self.data_source.users_get_current_account()
        if not response or not response.success or not response.data:
            error = getattr(response, "error", None) or "no account information"
            if is_auth_error(error):
                self._health.auth_failed(error)
                return
            raise ValueError(f"Failed to retrieve account information for user sync: {error}")
        await self._grant_creator(response.data)
        self.current_user_id, self.current_user_email = response.data.account_id, response.data.email
        user_id, user_email = self.current_user_id, self.current_user_email
        self.logger.info(f"Identified current user: {user_email} ({user_id})")

        # 2. Create the 'Drive' (Record Group)
        display_name = f"Dropbox - {user_email}"
        await self._create_personal_record_group(user_id, user_email, display_name)

        # Deletes earlier runs could not confirm.
        owed = await self._owed.load()
        if owed:
            self._owed.settle(await recheck_owed_deletes(
                self.data_entities_processor, self.connector_id, owed, self._item_still_exists, self.logger,
            ))

        # 3. The files.
        self.logger.info("Starting file traversal...")
        try:
            await self._run_sync_with_cursor(user_id, user_email)
        finally:
            # Deletes owed so far are kept even when this run stops part way.
            await self._owed.save()
        self.logger.info("✅ Dropbox Individual Sync Completed")

    async def _grant_creator(self, account_data: FullAccount) -> None:
        """Let the connector's creator, and nobody else, reach the account's records.

        The account's email may be another PipesHub user's (someone connects a Dropbox account under a colleague's
        address). Saving it as the connector's user would give that colleague the whole account, so it is saved
        only when it is the creator's own; the creator is always linked, and any other user an earlier version
        linked through the account's email is unlinked.
        """
        await self._load_creator_email()
        creator = (self.creator_email or "").lower()
        account_email = (account_data.email or "").lower()
        if creator and account_email == creator:
            await self.data_entities_processor.on_new_app_users([self._get_current_user_as_app_user(account_data)])
        await self.ensure_creator_user_app_relation()
        await self.register_authenticated_source_user(account_data.email, account_data.account_id)
        if creator:
            stored = await self.data_entities_processor.get_all_app_users(self.connector_id, raise_on_error=True)
            others = [u for u in stored if (u.email or "").lower() != creator]
            if others:
                self.logger.info(f"Unlinking {len(others)} user(s) who are not this personal connector's creator")
                await self.data_entities_processor.withdraw_app_user_access(self.connector_id, others)

    async def _create_personal_record_group(self, user_id: str, user_email: str, display_name: str) -> RecordGroup:
        """
        Create a single record group for the individual user's root folder.
        Returns:
            RecordGroup for the user's personal Dropbox
        """
        record_group = RecordGroup(
            id=str(uuid.uuid4()),
            name=display_name,
            group_type=RecordGroupType.DRIVE.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            external_group_id=user_id,
            inherit_permissions=True,
        )
        await self.data_entities_processor.on_new_record_groups([(record_group, [])])
        return record_group

    async def run_incremental_sync(self) -> None:
        """Every run is incremental already: the account is listed from its cursor."""
        await self.run_sync()

    async def get_signed_url(self, record: Record) -> Optional[str]:
        """
        Generate a temporary signed URL for downloading a file.
        Simplified for individual accounts.
        """
        if not self.data_source:
            raise connector_not_ready(self.display_name)
        try:
            # Dropbox uses path or file ID for temporary links. ID is more robust.
            target_identifier = record.external_record_id
            if not target_identifier:
                # Fallback: Use path if ID is somehow missing
                target_identifier = getattr(record, 'path', None)
            if not target_identifier:
                self.logger.warning(f"Cannot generate signed URL: Record {record.id} missing external_id")
                raise not_downloadable(
                    "This item is missing its Dropbox path and cannot be downloaded.",
                    connector=self.display_name,
                )
            response = await self.data_source.files_get_temporary_link(
                path=target_identifier, raise_on_error=True
            )
            if not response.success or not response.data:
                self.logger.error(
                    f"Failed to get temporary link for record {record.id}: {response.error}"
                )
            raise_for_stream_fetch(
                success=response.success,
                has_payload=bool(response.data),
                connector=self.display_name,
                message=response.error,
            )
            return response.data.link
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(
                f"Error creating signed URL for record {record.id}: {e}", exc_info=True
            )
            raise to_stream_error(e, connector=self.display_name) from e

    async def stream_record(self, record: Record) -> StreamingResponse:
        signed_url = await self.get_signed_url(record)
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
            mime_type=record.mime_type,
            fallback_filename=f"record_{record.id}"
        )

    async def test_connection_and_access(self) -> bool:
        """Whether Dropbox accepts the token: the data source reports a refusal in its answer, it does not raise."""
        if not self.data_source:
            return False
        try:
            response = await self.data_source.users_get_current_account()
        except Exception as e:
            self.logger.error(f"Dropbox connection test failed: {e}", exc_info=True)
            return False
        if not response.success:
            self.logger.error(f"Dropbox connection test failed: {response.error}")
            return False
        self.logger.info("Dropbox connection test successful.")
        return True

    async def reindex_records(self, records: List[Record]) -> None:
        """
        Reindex records from Dropbox Individual account.

        This method checks each record at the source for updates:
        - If the record has changed (metadata, content, or permissions), it updates the DB
        - If the record hasn't changed, it publishes a reindex event for the existing record
        """
        try:
            if not records:
                self.logger.info("No records to reindex")
                return

            self.logger.info(f"Starting reindex for {len(records)} Dropbox Individual records")

            # Ensure Dropbox client is initialized
            if not self.data_source:
                self.logger.error("Dropbox client not initialized. Call init() first.")
                raise Exception("Dropbox client not initialized. Call init() first.")

            # Get current user info (needed for processing entries)
            user_id, user_email = await self._get_current_user_info()

            # Check records at source for updates
            org_id = self.data_entities_processor.org_id
            updated_records = []
            non_updated_records = []

            for record in records:
                try:
                    updated_record_data = await self._check_and_fetch_updated_record(
                        org_id, record, user_id, user_email
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
                # A folder that moved is stored at its new path now, so no later listing sees the move: the
                # paths below it are rewritten here.
                moved, self._moved_folders = self._moved_folders, []
                for external_id, old_path, new_path in moved:
                    _, done = await repath_descendants(self.data_entities_processor, self.connector_id,
                                                       external_id, old_path, new_path, self.logger)
                    if not done:
                        self.logger.warning(f"Paths below {new_path} could not all be rewritten; a full sync "
                                            "writes them again")
                self.logger.info(f"Updated {len(updated_records)} records in DB that changed at source")

            # Publish reindex events for non-updated records
            if non_updated_records:
                await self.data_entities_processor.reindex_existing_records(non_updated_records)
                self.logger.info(f"Published reindex events for {len(non_updated_records)} non-updated records")

        except Exception as e:
            self.logger.error(f"Error during Dropbox Individual reindex: {e}", exc_info=True)
            raise

    async def _check_and_fetch_updated_record(
        self, org_id: str, record: Record, user_id: str, user_email: str
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """
        Fetch record from Dropbox and return data for reindexing if changed.

        Args:
            org_id: The organization ID
            record: The record to check for updates
            user_id: The current user's account ID
            user_email: The current user's email

        Returns:
            Tuple of (updated_record, permissions) if the record has changed, None otherwise
        """
        try:
            external_id = record.external_record_id
            record_group_id = record.external_record_group_id

            if not external_id:
                self.logger.warning(f"Missing external_record_id for record {record.id}")
                return None

            # Fetch fresh metadata from Dropbox using the file ID
            metadata_result = await self.data_source.files_get_metadata(path=external_id)

            if not metadata_result or not metadata_result.success:
                self.logger.warning(f"Could not fetch metadata for record {record.id}: {metadata_result.error if metadata_result else 'No response'}")
                return None

            entry = metadata_result.data

            # Check if deleted
            if isinstance(entry, DeletedMetadata):
                self.logger.info(f"Record {record.id} has been deleted at source")
                return None

            # Unchanged content and place: only a re-index is wanted.
            stored = await self.data_entities_processor.get_file_record_by_id(record.id)
            if entry.name == record.record_name and getattr(entry, "rev", None) == record.external_revision_id \
                    and entry.path_lower == getattr(stored, "path", entry.path_lower):
                return None

            # For individual accounts, record_group_id is the user's account ID
            record_update = await self._process_dropbox_entry(
                entry=entry,
                user_id=user_id,
                user_email=user_email,
                record_group_id=record_group_id or user_id
            )

            if not record_update or record_update.is_deleted:
                return None

            self.logger.info(f"Record {external_id} has changed at source. Updating.")
            record_update.record.id = record.id
            return (record_update.record, record_update.new_permissions or [])

        except Exception as e:
            self.logger.error(f"Error checking Dropbox Individual record {record.id} at source: {e}", exc_info=True)
            return None

    def handle_webhook_notification(self, notification: Dict) -> None:
        """Handle webhook notifications by triggering incremental sync."""
        self.logger.info("Dropbox webhook received. Triggering incremental sync.")
        asyncio.create_task(self.run_incremental_sync())

    async def cleanup(self) -> None:
        self.logger.info("Cleaning up Dropbox Individual connector resources.")
        self.data_source = None

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> NoReturn:
        """Dropbox Individual connector does not support dynamic filter options."""
        raise NotImplementedError("Dropbox Individual connector does not support dynamic filter options")

    @classmethod
    async def create_connector(
        cls,
        logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
        data_entities_processor,
        **kwargs,
    ) -> "BaseConnector":
        return cls(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
