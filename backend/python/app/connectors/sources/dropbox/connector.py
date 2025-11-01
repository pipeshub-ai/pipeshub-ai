import asyncio
import mimetypes
import uuid

# from datetime import datetime
from datetime import datetime, timezone
from logging import Logger
from typing import AsyncGenerator, Dict, List, Optional, Tuple, Union

from aiolimiter import AsyncLimiter
from dropbox.exceptions import ApiError

# Dropbox SDK specific types
from dropbox.files import (
    DeletedMetadata,
    FileMetadata,
    FolderMetadata,
    ListFolderResult,
)
from dropbox.sharing import AccessLevel, LinkAudience, SharedLinkSettings
from dropbox.team import UserSelectorArg
from dropbox.team_log import EventCategory
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

# Base connector and service imports
from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
)
from app.config.constants.http_status_code import HttpStatusCode
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
    CommonFields,
    ConnectorBuilder,
    DocumentationLink,
)

# App-specific Dropbox client imports
from app.connectors.sources.dropbox.common.apps import DropboxApp
from app.connectors.sources.microsoft.common.msgraph_client import (
    RecordUpdate,
)

# Model imports
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    User,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.dropbox.dropbox_ import (
    DropboxClient,
    DropboxResponse,
    DropboxTokenConfig,
)
from app.sources.external.dropbox.dropbox_ import DropboxDataSource
from app.utils.streaming import stream_content
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# from dropbox.team import GroupSelector

# Add these helper functions at the top of the file
def get_parent_path_from_path(path: str) -> Optional[str]:
    """Extracts the parent path from a file/folder path."""
    if not path or path == "/" or "/" not in path.lstrip("/"):
        return None  # Root directory has no parent path in this context
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

@ConnectorBuilder("Dropbox")\
    .in_group("Cloud Storage")\
    .with_auth_type("OAUTH")\
    .with_description("Sync files and folders from Dropbox")\
    .with_categories(["Storage"])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/dropbox.svg")
        .with_realtime_support(True)
        .add_documentation_link(DocumentationLink(
            "Dropbox App Setup",
            "https://developers.dropbox.com/oauth-guide",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/dropbox/dropbox',
            'pipeshub'
        ))
        .with_redirect_uri("connectors/oauth/callback/Dropbox", True)
        .with_oauth_urls(
            "https://www.dropbox.com/oauth2/authorize",
            "https://api.dropboxapi.com/oauth2/token",
            [
                "account_info.read",
                "files.content.read",
                "files.metadata.read",
                "file_requests.read",
                "groups.read",
                "members.read",
                "sharing.read",
                "sharing.write",
                "team_data.member",
                "team_data.team_space",
                "team_info.read",
                "events.read"
            ]
        )
        .add_auth_field(CommonFields.client_id("Dropbox App Console"))
        .add_auth_field(CommonFields.client_secret("Dropbox App Console"))
        .with_webhook_config(True, ["file.added", "file.modified", "file.deleted"])
        .with_scheduled_config(True, 60)
        .add_sync_custom_field(CommonFields.batch_size_field())
        .add_filter_field(CommonFields.file_types_filter(), "static")
        .add_filter_field(CommonFields.folders_filter(),
                          "https://api.dropboxapi.com/2/files/list_folder")
    )\
    .build_decorator()
class DropboxConnector(BaseConnector):
    """
    Connector for synchronizing data from a Dropbox account.
    """

    current_user_id: Optional[str] = None

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
    ) -> None:

        super().__init__(DropboxApp(), logger, data_entities_processor, data_store_provider, config_service)

        self.connector_name = Connectors.DROPBOX



        # Initialize sync point for tracking record changes
        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_name=self.connector_name,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider
            )
        # Initialize sync points
        self.dropbox_cursor_sync_point = _create_sync_point(SyncDataPointType.RECORDS)
        self.user_sync_point = _create_sync_point(SyncDataPointType.USERS)
        self.user_group_sync_point = _create_sync_point(SyncDataPointType.GROUPS)

        self.data_source: Optional[DropboxDataSource] = None
        self.batch_size = 100
        self.max_concurrent_batches = 5
        self.rate_limiter = AsyncLimiter(50, 1)  # 50 requests per second

    async def init(self) -> bool:
        """Initializes the Dropbox client using credentials from the config service."""
        config = await self.config_service.get_config(
            "/services/connectors/dropbox/config"
        )
        if not config:
            self.logger.error("Dropbox access token not found in configuration.")
            return False

        credentials_config = config.get("credentials")
        access_token = credentials_config.get("access_token")
        refresh_token = credentials_config.get("refresh_token")
        is_team = credentials_config.get("isTeam", True)

        auth_config = config.get("auth")
        app_key = auth_config.get("clientId")
        app_secret = auth_config.get("clientSecret")

        try:
            config = DropboxTokenConfig(
                token=access_token,
                refresh_token=refresh_token,
                app_key=app_key,
                app_secret=app_secret
            )
            client = await DropboxClient.build_with_config(config, is_team=is_team)
            self.data_source = DropboxDataSource(client)
            self.logger.info("Dropbox client initialized successfully.")
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize Dropbox client: {e}", exc_info=True)
            return False

    async def _process_dropbox_entry(
        self, entry: Union[FileMetadata, FolderMetadata, DeletedMetadata],
         user_id: str, user_email: str,
          record_group_id: str,
          is_person_folder: bool
    ) -> Optional[RecordUpdate]:
        """
        Process a single Dropbox entry and detect changes.

        Returns:
            RecordUpdate object containing the record and change information.
        """
        try:
            # 1. Handle Deleted Items (Deletion from db not implemented yet)
            if isinstance(entry, DeletedMetadata):
                pass
                # return None
                # self.logger.info(f"Item at path '{entry.path_lower}' has been deleted.")


                # async with self.data_store_provider.transaction() as tx_store:
                #     record = await tx_store.get_record_by_path(
                #         connector_name=self.connector_name,
                #         path=entry.path_lower,
                #     )

                # print("GOING TO RUN ON_RECORD_DELETED 1: ", record["_key"], record["name"])
                # await self.data_entities_processor.on_record_deleted(
                #     record_id=record["_key"]
                # )


                # return RecordUpdate(
                #     record=None,
                #     external_record_id=entry.id,
                #     is_new=False,
                #     is_updated=False,
                #     is_deleted=True,
                #     metadata_changed=False,
                #     content_changed=False,
                #     permissions_changed=False
                # )


            # 2. Get existing record from the database
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_name=self.connector_name,
                    external_id=entry.id
                )

            # 3. Detect changes
            is_new = existing_record is None
            is_updated = False
            metadata_changed = False
            content_changed = False
            permissions_changed = False
            is_file = isinstance(entry, FileMetadata)

            if existing_record:
                if existing_record.record_name != entry.name:
                    metadata_changed = True
                    is_updated = True
                if is_file and existing_record.external_revision_id != entry.rev:

                    content_changed = True
                    is_updated = True

            # 4. Create or Update the FileRecord object
            record_type = RecordType.FILE


            # Conditionally get timestamp: files have it, folders do not.
            if is_file:
                timestamp_ms = int(entry.server_modified.timestamp() * 1000)
            else:
                # Use current time for folders as a fallback.
                timestamp_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

            # 5. Get signed URL for files
            signed_url = None
            if is_file:
                temp_link_result = await self.data_source.files_get_temporary_link(
                    entry.path_lower,
                    team_member_id=user_id,
                    team_folder_id=record_group_id if not is_person_folder else None
                )
                if temp_link_result.success:
                    signed_url = temp_link_result.data.link

            #5.5 Get preview URL
            self.logger.info("=" * 50)
            self.logger.info("Processing weburl for path: %s", entry.path_lower)
            self.logger.info("=" * 50)

            preview_url = None
            link_settings = SharedLinkSettings(
                audience=LinkAudience('no_one'),
                allow_download=True
            )

            # First call - try to create link with settings
            shared_link_result = await self.data_source.sharing_create_shared_link_with_settings(
                path=entry.path_lower,
                team_member_id=user_id,
                team_folder_id=record_group_id if not is_person_folder else None,
                settings=link_settings
            )

            self.logger.info("Result 1: %s", shared_link_result)

            if shared_link_result.success:
                # Successfully created new link
                preview_url = shared_link_result.data.url
                self.logger.info("Successfully created new link: %s", preview_url)
            else:
                # First call failed - check if link already exists
                error_str = str(shared_link_result.error)
                self.logger.info("First call failed with error type")

                if 'shared_link_already_exists' in error_str:
                    self.logger.info("Link already exists, making second call to retrieve it")

                    # Make second call with settings=None to get the existing link
                    second_result = await self.data_source.sharing_create_shared_link_with_settings(
                        path=entry.path_lower,
                        team_member_id=user_id,
                        team_folder_id=record_group_id if not is_person_folder else None,
                        settings=None
                    )

                    self.logger.info("Result 2 received")

                    if second_result.success:
                        # Unexpectedly succeeded
                        preview_url = second_result.data.url
                        self.logger.info("Second call succeeded: %s", preview_url)
                    else:
                        # Expected to fail - extract URL from error string
                        second_error_str = str(second_result.error)

                        if 'shared_link_already_exists' in second_error_str:
                            # Extract URL using regex
                            import re
                            # Pattern to match url='...' in the error string
                            url_pattern = r"url='(https://[^']+)'"
                            url_match = re.search(url_pattern, second_error_str)

                            if url_match:
                                preview_url = url_match.group(1)
                                self.logger.info("Successfully extracted URL from error: %s", preview_url)
                            else:
                                self.logger.error("Could not extract URL from second error string")
                                self.logger.debug("Error string: %s", second_error_str[:500])  # Log first 500 chars
                        else:
                            self.logger.error("Unexpected error on second call (not shared_link_already_exists)")
                else:
                    self.logger.error("Unexpected error type on first call (not shared_link_already_exists)")

            # Final check
            if preview_url is None:
                self.logger.error("Failed to retrieve preview URL for %s", entry.path_lower)
            else:
                self.logger.info("Final preview_url: %s", preview_url)

            # 6. Get parent record ID
            parent_path = None
            parent_external_record_id = None
            if entry.path_display != '/':
                parent_path = get_parent_path_from_path(entry.path_lower)
            parent_metadata = None
            if parent_path:
                parent_metadata = await self.data_source.files_get_metadata(
                    parent_path,
                    team_member_id=user_id,
                    team_folder_id=record_group_id if not is_person_folder else None,
                )
                if parent_metadata.success:
                    parent_external_record_id = parent_metadata.data.id

            file_record = FileRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                record_name=entry.name,
                record_type=record_type,
                record_group_type=RecordGroupType.DRIVE.value,
                external_record_group_id=record_group_id, # Use the passed-in folder_id or user_id
                external_record_id=entry.id,
                external_revision_id=entry.rev if is_file else None,
                version=0 if is_new else existing_record.version + 1,
                origin=OriginTypes.CONNECTOR.value,
                connector_name=self.connector_name,
                created_at=timestamp_ms,
                updated_at=timestamp_ms,
                source_created_at=timestamp_ms,
                source_updated_at=timestamp_ms,
                weburl=preview_url,
                signed_url=signed_url,
                parent_external_record_id=parent_external_record_id,
                size_in_bytes=entry.size if is_file else 0,
                is_file=is_file,
                extension=get_file_extension(entry.name) if is_file else None,
                path=entry.path_lower,
                mime_type=get_mimetype_enum_for_dropbox(entry),
                sha256_hash=entry.content_hash if is_file and hasattr(entry, 'content_hash') else None,
            )

            # async with self.data_store_provider.transaction() as tx_store:
            #     user = await tx_store.get_user_by_id(user_id=user_id)

            # 5. Handle Permissions
            new_permissions = []

            try:
                # Determine if this is a shared file/folder
                shared_folder_id = None
                if hasattr(entry, 'shared_folder_id') and entry.shared_folder_id:
                    shared_folder_id = entry.shared_folder_id

                # Fetch permissions from Dropbox
                new_permissions = await self._convert_dropbox_permissions_to_permissions(
                    file_or_folder_id=entry.id,
                    is_file=is_file,
                    team_member_id=user_id,
                    shared_folder_id=shared_folder_id
                )

                # If no explicit permissions were found (e.g., personal file),
                # add the owner's permission
                if not new_permissions:
                    #in case of personal file/folder, add owner permission
                    new_permissions = [
                        Permission(
                            external_id=user_id,
                            email=user_email,
                            type=PermissionType.OWNER,
                            entity_type=EntityType.USER
                        )
                    ]
                else:
                    #in all other cases atleast add user permission
                    user_already_has_permission = any(
                        perm.email == user_email
                        for perm in new_permissions
                    )
                    if not user_already_has_permission:
                        new_permissions.append(
                            Permission(
                                external_id=user_id,
                                email=user_email,
                                type=PermissionType.WRITE,
                                entity_type=EntityType.USER
                            )
                        )

            except Exception as perm_ex:
                self.logger.warning(f"Could not fetch permissions for {entry.name}: {perm_ex}")
                # Fallback to owner permission
                new_permissions = [
                    Permission(
                        external_id=user_id,
                        email=user_email,
                        type=PermissionType.OWNER,
                        entity_type=EntityType.USER
                    )
                ]

            # Compare permissions if record exists
            old_permissions = []
            if existing_record:
                # Since you mentioned we can't get old_permissions from existing_record yet,
                # we'll leave this empty. When you implement it, you can fetch them here:
                # old_permissions = await tx_store.get_permissions_for_record(existing_record.id) or []

                # For now, if there's an existing record and we have new permissions,
                # we'll assume permissions might have changed
                if new_permissions:
                    permissions_changed = True
                    is_updated = True

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
                external_record_id=entry.id
            )
        except Exception as ex:
            self.logger.error(f"Error processing Dropbox entry {getattr(entry, 'id', entry.path_lower)}: {ex}", exc_info=True)
            return None

    async def _process_dropbox_items_generator(
        self, entries: List[Union[FileMetadata, FolderMetadata, DeletedMetadata]], user_id: str, user_email: str, record_group_id: str, is_person_folder: bool
    ) -> AsyncGenerator[Tuple[Optional[FileRecord], List[Permission], RecordUpdate], None]:
        """
        Process Dropbox entries and yield records with their permissions.
        This allows non-blocking processing of large datasets.
        """
        for entry in entries:
            try:
                record_update = await self._process_dropbox_entry(entry, user_id, user_email, record_group_id, is_person_folder)
                if record_update:
                    yield (record_update.record, record_update.new_permissions or [], record_update)
                await asyncio.sleep(0)
            except Exception as e:
                self.logger.error(f"Error processing item in generator: {e}", exc_info=True)
                continue

    async def _convert_dropbox_permissions_to_permissions(
        self,
        file_or_folder_id: str,
        is_file: bool,
        team_member_id: Optional[str] = None,
        shared_folder_id: Optional[str] = None
    ) -> List[Permission]:
        """
        Convert Dropbox permissions to our Permission model.
        Handles both user and group permissions for files and folders.

        Args:
            file_or_folder_id: The Dropbox file or folder ID
            is_file: True if this is a file, False if it's a folder
            team_member_id: The team member ID to use for the API call
            shared_folder_id: For shared folders, the shared folder ID

        Returns:
            List of Permission objects
        """
        permissions = []

        try:
            # Fetch members based on type
            if is_file:
                members_result = await self.data_source.sharing_list_file_members(
                    file=file_or_folder_id,
                    include_inherited=True,
                    team_member_id=team_member_id
                )
            else:
                # For folders, only fetch if it's a shared folder
                if not shared_folder_id:
                    # Not a shared folder, no permissions to fetch
                    return []
                members_result = await self.data_source.sharing_list_folder_members(
                    shared_folder_id=shared_folder_id,
                    team_member_id=team_member_id
                )

            if not members_result.success:
                self.logger.debug(f"Could not fetch permissions for {file_or_folder_id}: {members_result.error}")
                return []

            # Map Dropbox AccessLevel to PermissionType
            access_level_map = {
                'owner': PermissionType.OWNER,
                'editor': PermissionType.WRITE,
                'viewer': PermissionType.READ,
            }

            if hasattr(members_result.data, 'users') and members_result.data.users:
                for user_membership in members_result.data.users:
                    access_type_tag = user_membership.access_type._tag
                    permission_type = access_level_map.get(access_type_tag, PermissionType.READ)

                    user_info = user_membership.user

                    # Get email and check validity
                    email = user_info.email if hasattr(user_info, 'email') else None

                    # Skip users without email or with email ending in '#'
                    if not email or email.endswith('#'):
                        self.logger.debug(f"Skipping user {user_info.account_id} with invalid email: {email}")
                        continue

                    permissions.append(Permission(
                        external_id=user_info.account_id,
                        email=email,
                        type=permission_type,
                        entity_type=EntityType.USER
                    ))

            # Process group permissions
            if hasattr(members_result.data, 'groups') and members_result.data.groups:
                for group_membership in members_result.data.groups:
                    access_type_tag = group_membership.access_type._tag
                    permission_type = access_level_map.get(access_type_tag, PermissionType.READ)

                    group_info = group_membership.group
                    permissions.append(Permission(
                        external_id=group_info.group_id,
                        email=None,  # Groups don't have emails
                        type=permission_type,
                        entity_type=EntityType.GROUP
                    ))

        except Exception as e:
            self.logger.debug(f"Error converting Dropbox permissions for {file_or_folder_id}: {e}")

        return permissions


    # Update the _permissions_equal method (fix the comparison logic)

    def _permissions_equal(self, old_perms: List[Permission], new_perms: List[Permission]) -> bool:
        """
        Compare two lists of permissions to detect changes.
        """
        if not old_perms and not new_perms:
            return True
        if not old_perms or not new_perms:  # One is empty, the other is not
            return False
        if len(old_perms) != len(new_perms):
            return False

        # Create sets of permission tuples for comparison
        # Include all relevant fields that would indicate a permission change
        old_set = {(p.external_id, p.type.value, p.entity_type.value) for p in old_perms}
        new_set = {(p.external_id, p.type.value, p.entity_type.value) for p in new_perms}

        return old_set == new_set


    async def _process_entry(
        self, entry: Union[FileMetadata, FolderMetadata, DeletedMetadata]
    ) -> Optional[Tuple[FileRecord, List[Permission]]]:
        """Processes a single entry from Dropbox and converts it to internal models."""
        if isinstance(entry, DeletedMetadata):
            # Dropbox API for deleted items doesn't provide an ID, only path.
            # A robust implementation would need to look up the record by its path.
            self.logger.info(f"Item '{entry.name}' at path '{entry.path_lower}' was deleted. Deletion requires path-based lookup.")
            # Example: await self.data_entities_processor.on_record_deleted(record_path=entry.path_lower)
            return None

        is_file = isinstance(entry, FileMetadata)
        record_type = RecordType.FILE if is_file else RecordType.FOLDER
        mime_type, _ = mimetypes.guess_type(entry.name) if is_file else (None, None)

        file_record = FileRecord(
            id=str(uuid.uuid4()),
            record_name=entry.name,
            record_type=record_type,
            record_group_type=RecordGroupType.DRIVE.value,
            external_record_id=entry.id,
            external_revision_id=entry.rev if is_file else None,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=self.connector_name,
            updated_at=int(entry.server_modified.timestamp() * 1000) if is_file else None,
            source_updated_at=int(entry.server_modified.timestamp() * 1000) if is_file else None,
            web_url=f"https://www.dropbox.com/home{entry.path_display}",
            parent_external_record_id=get_parent_path_from_path(entry.path_lower),
            size_in_bytes=entry.size if is_file else 0,
            is_file=is_file,
            extension=get_file_extension(entry.name) if is_file else None,
            path=entry.path_lower,
            mime_type=mime_type,
            sha256_hash=entry.content_hash if is_file and hasattr(entry, 'content_hash') else None,
        )

        permissions = await self._get_permissions(entry)
        return file_record, permissions

    async def _get_permissions(
        self, entry: Union[FileMetadata, FolderMetadata]
    ) -> List[Permission]:
        """Fetches and converts permissions for a Dropbox entry."""
        if not self.data_source:
            return []

        permissions = []
        try:
            members_result = None
            if isinstance(entry, FileMetadata):
                members_result = await self.data_source.sharing_list_file_members(file=entry.id)
            elif hasattr(entry, 'shared_folder_id') and entry.shared_folder_id:
                members_result = await self.data_source.sharing_list_folder_members(shared_folder_id=entry.shared_folder_id)

            if not members_result:
                return []

            all_members = members_result.data.users + members_result.data.groups

            for member in all_members:
                # Map Dropbox AccessLevel to our internal PermissionType
                access_type = getattr(member, 'access_type', None)
                perm_type = PermissionType.WRITE if access_type in (AccessLevel.owner, AccessLevel.editor) else PermissionType.READ

                member_info = getattr(member, 'user', getattr(member, 'group', None))
                if not member_info:
                    continue

                entity_type = EntityType.USER if hasattr(member_info, 'account_id') else EntityType.GROUP
                external_id = member_info.account_id if entity_type == EntityType.USER else member_info.group_id
                email = getattr(member_info, 'email', None)

                permissions.append(
                    Permission(external_id=external_id, email=email, type=perm_type, entity_type=entity_type)
                )
        except Exception as e:
            # Not all items are shared, so API calls can fail. This is expected.
            self.logger.debug(f"Could not fetch permissions for '{entry.name}': {e}")
        return permissions

    async def _sync_from_source(self, path: str = "", cursor: Optional[str] = None) -> None:
        """Helper to sync a folder, handling pagination and cursor management."""
        if not self.data_source:
            raise ConnectionError("Dropbox connector is not initialized.")

        has_more = True
        batch_records = []
        sync_point_key = generate_record_sync_point_key(RecordType.DRIVE.value, "root")

        while has_more:
            try:
                if cursor:
                    response = await self.data_source.files_list_folder_continue(cursor=cursor)
                else:
                    response = await self.data_source.files_list_folder(path=path, recursive=True, include_deleted=True)

                result: ListFolderResult = response.data

                for entry in result.entries:
                    processed_data = await self._process_entry(entry)
                    if processed_data:
                        batch_records.append(processed_data)

                    if len(batch_records) >= self.batch_size:
                        await self.data_entities_processor.on_new_records(batch_records)
                        batch_records = []

                cursor = result.data.cursor
                has_more = result.data.has_more
                await self.record_sync_point.update_sync_point(sync_point_key, {'cursor': cursor})

            except Exception as e:
                self.logger.error(f"Error during Dropbox folder sync: {e}", exc_info=True)
                has_more = False

        if batch_records:
            await self.data_entities_processor.on_new_records(batch_records)

    async def _process_users_in_batches(self, users: List[User]) -> None:
        """
        Process users in concurrent batches for improved performance.

        Args:
            users: List of users to process
        """
        try:
            # Get all active users
            all_active_users = await self.data_entities_processor.get_all_active_users()
            active_user_emails = {active_user.email.lower() for active_user in all_active_users}


            # Filter users to sync
            users_to_sync = [
                user for user in users
                if user.email and user.email.lower() in active_user_emails
            ]

            self.logger.info(f"Processing {len(users_to_sync)} active users out of {len(users)} total users")

            # Process users in concurrent batches
            for i in range(0, len(users_to_sync), self.max_concurrent_batches):
                batch = users_to_sync[i:i + self.max_concurrent_batches]

                # Run sync for batch of users concurrently
                sync_tasks = [
                    self._run_sync_with_yield(user.source_user_id, user.email)

                    for user in batch
                ]


                print("Going to run sync for these users:", batch)

                await asyncio.gather(*sync_tasks, return_exceptions=True)

                # Small delay between batches to prevent overwhelming the API
                await asyncio.sleep(1)

            self.logger.info("Completed processing all user batches")

        except Exception as e:
            self.logger.error(f"Error processing users in batches: {e}")
            raise



    async def _run_sync_with_yield(self, user_id: str, user_email: str) -> None:
        """
        Synchronizes Dropbox files for a given user using the cursor-based approach.

        This function first lists all shared folders, then loops through the
        personal folder (root) and each shared folder, running a separate sync
        operation with a unique cursor for each.

        Args:
            user_id: The Dropbox team member ID of the user to sync.
            user_email: The email of the user to sync.
        """
        try:
            self.logger.info(f"Starting Dropbox sync with yield for user {user_email}")

            # List all shared folders the user has access to
            shared_folders = await self.data_source.sharing_list_folders(team_member_id=user_id)

            # Create a list of folders to sync: None for personal, plus all shared folder IDs
            folders_to_sync = [None]
            if shared_folders.success:
                self.logger.info(f"Found {len(shared_folders.data.entries)} shared folders for user {user_email}")
                for folder in shared_folders.data.entries:
                    self.logger.info(f"  - Will sync Folder: {folder.name}, ID: {folder.shared_folder_id}")
                    folders_to_sync.append(folder.shared_folder_id)
            else:
                self.logger.warning(f"Could not list shared folders for user {user_email}: {shared_folders.error}")

            # Loop through each folder (personal + shared) and run a separate sync
            for folder_id in folders_to_sync:

                # 1. Determine sync parameters for this specific folder
                if folder_id is None:
                    # This is the user's personal root folder
                    sync_context_id = user_id
                    sync_group = "users"
                    sync_log_name = f"personal folder for user {user_email}"
                    current_record_group_id = user_id
                else:
                    # This is a shared folder
                    sync_context_id = f"{user_id}_{folder_id}" # Safer key than using '/'
                    sync_group = "shared_folders"
                    sync_log_name = f"shared folder {folder_id} for user {user_email}"
                    current_record_group_id = folder_id

                self.logger.info(f"Starting sync loop for: {sync_log_name}")

                # 2. Get current sync state from the database *for this folder*
                sync_point_key = generate_record_sync_point_key(RecordType.DRIVE.value, sync_group, sync_context_id)
                sync_point = await self.dropbox_cursor_sync_point.read_sync_point(sync_point_key)
                cursor = sync_point.get('cursor')

                self.logger.info(f"Sync point key: {sync_point_key}")
                self.logger.info(f"Retrieved sync point: {sync_point}")
                self.logger.info(f"Cursor value: {cursor}")

                # Reset batching and state for each folder sync
                batch_records = []
                batch_count = 0
                has_more = True

                while has_more:
                    # 3. Fetch changes from Dropbox
                    try:
                        async with self.rate_limiter:
                            if cursor:
                                self.logger.info(f"[{sync_log_name}] Calling files_list_folder_continue...")
                                result = await self.data_source.files_list_folder_continue(
                                    cursor,
                                    team_member_id=user_id,
                                    team_folder_id=folder_id,
                                )

                            else:
                                # This is the first sync for this folder
                                try:
                                    result = await self.data_source.files_list_folder(
                                        path="",
                                        team_member_id=user_id,
                                        team_folder_id=folder_id,
                                        recursive=True
                                    )
                                except Exception as e:
                                    print("error in api call:", e)
                        if not result.success:
                            self.logger.error(f"[{sync_log_name}] Dropbox API call failed: {result.error}")
                            # Stop syncing this folder on API error
                            has_more = False
                            continue # Skip to the next 'while' iteration (which will exit)

                        self.logger.info(f"[{sync_log_name}] Got {len(result.data.entries)} entries. Has_more: {result.data.has_more}")
                        entries = result.data.entries

                        # 4. Process the entries from the current page
                        async for file_record, permissions, record_update in self._process_dropbox_items_generator(
                            entries, user_id, user_email, current_record_group_id, folder_id is None
                        ):
                            if record_update.is_deleted:
                                await self._handle_record_updates(record_update)
                                continue

                            if record_update.is_updated:
                                await self._handle_record_updates(record_update)
                                continue

                            if file_record:
                                batch_records.append((file_record, permissions))
                                batch_count += 1

                                if batch_count >= self.batch_size:
                                    self.logger.info(f"[{sync_log_name}] Processing batch of {batch_count} records.")
                                    await self.data_entities_processor.on_new_records(batch_records)
                                    batch_records = []
                                    batch_count = 0
                                    await asyncio.sleep(0.1)

                        # Process any remaining records in the batch from the last page
                        if batch_records:
                            self.logger.info(f"[{sync_log_name}] Processing final batch of {len(batch_records)} records.")
                            await self.data_entities_processor.on_new_records(batch_records)
                            batch_records = []
                            batch_count = 0

                        # 5. Update the sync state for the next iteration
                        cursor = result.data.cursor
                        self.logger.info(f"[{sync_log_name}] Storing new cursor for key {sync_point_key}")
                        await self.dropbox_cursor_sync_point.update_sync_point(
                            sync_point_key,
                            sync_point_data={"cursor": cursor}
                        )

                        has_more = result.data.has_more

                    except ApiError as api_ex:
                        self.logger.error(f"Dropbox API Error during sync for {sync_log_name}: {api_ex}")
                        # If path not found, stop this folder's sync and continue to the next
                        if 'path/not_found' in str(api_ex):
                            self.logger.warning(f"[{sync_log_name}] Path not found. Stopping sync for this folder.")
                            has_more = False # Stop this 'while' loop
                        else:
                            raise # Re-raise other critical API errors
                    except Exception as loop_ex:
                        self.logger.error(f"Error in 'while has_more' loop for {sync_log_name}: {loop_ex}", exc_info=True)
                        has_more = False # Stop this 'while' loop to be safe

                self.logger.info(f"Completed sync loop for: {sync_log_name}")

            self.logger.info(f"Completed all Dropbox sync loops for user {user_id}")

        except ApiError as ex:
            # Error during initial shared folder list
            self.logger.error(f"Dropbox API Error during sync setup for user {user_id}: {ex}")
            raise
        except Exception as ex:
            self.logger.error(f"Unhandled error in Dropbox sync for user {user_id}: {ex}", exc_info=True)
            raise

    async def _handle_record_updates(self, record_update: RecordUpdate) -> None:
        """
        Handle different types of record updates (new, updated, deleted).
        """
        try:
            if record_update.is_deleted:
                await self.data_entities_processor.on_record_deleted(
                    record_id=record_update.external_record_id
                )
            elif record_update.is_new:
                self.logger.info(f"New record detected: {record_update.record.record_name}")
            elif record_update.is_updated:

                if record_update.content_changed:
                    self.logger.info(f"Content changed for record: {record_update.record.record_name}")
                    await self.data_entities_processor.on_record_content_update(record_update.record)
                if record_update.metadata_changed:
                    self.logger.info(f"Metadata changed for record: {record_update.record.record_name}")
                    await self.data_entities_processor.on_record_metadata_update(record_update.record)
                if record_update.permissions_changed:
                    self.logger.info(f"Permissions changed for record: {record_update.record.record_name}")
                    await self.data_entities_processor.on_updated_record_permissions(
                        record_update.record,
                        record_update.new_permissions
                    )
        except Exception as e:
            self.logger.error(f"Error handling record updates: {e}", exc_info=True)

    def get_app_users(self, users: DropboxResponse) -> List[AppUser]:
        app_users: List[AppUser] = []
        for member in users.data.members:
            profile = member.profile
            app_users.append(
                AppUser(
                    app_name=self.connector_name,
                    source_user_id=profile.team_member_id,
                    full_name=profile.name.display_name,
                    email=profile.email,
                    is_active=(profile.status._tag == "active"),
                    title=member.role._tag,
                )
            )
        return app_users

    async def run_sync(self) -> None:
        """Runs a full synchronization from the Dropbox account root."""
        try:
            self.logger.info("Starting Dropbox full sync.")

            # Step 1: fetch and sync all users
            self.logger.info("Syncing users...")
            users = await self.data_source.team_members_list()
            app_users = self.get_app_users(users)

            # Step 1.5: Initialize cursor for member events
            member_sync_key = generate_record_sync_point_key("member_events", "team_events", "global")
            member_sync_point = await self.dropbox_cursor_sync_point.read_sync_point(member_sync_key)

            if not member_sync_point.get('cursor'):
                self.logger.info("Initializing cursor for member events...")
                await self._initialize_event_cursor(member_sync_key, EventCategory.members)
                await self.data_entities_processor.on_new_app_users(app_users)

            else:
                self.logger.info("Running an INCREMENTAL sync for member events...")
                await self._sync_member_changes_with_cursor(app_users)


            # Step 2: fetch and sync all user groups
            group_sync_key = generate_record_sync_point_key("user_group_events", "team_events", "global")
            group_sync_point = await self.dropbox_cursor_sync_point.read_sync_point(group_sync_key)

            if not group_sync_point.get('cursor'):
                self.logger.info("Running a FULL sync for user groups...")

                # IMPORTANT: Initialize cursor BEFORE doing the full sync
                # This creates a bookmark at the current moment
                await self._initialize_event_cursor(group_sync_key, EventCategory.groups)

                # Now do the full sync
                await self._sync_user_groups()
            else:
                self.logger.info("Running an INCREMENTAL sync for user groups...")
                await self._sync_group_changes_with_cursor()


            # Step 3: List all shared folders within a team and create record groups
            record_group_sync_key = generate_record_sync_point_key("record_group_events", "team_events", "global")
            record_group_sync_point = await self.dropbox_cursor_sync_point.read_sync_point(record_group_sync_key)

            if not record_group_sync_point.get('cursor'):
                self.logger.info("Initializing cursor for record group events...")
                await self._initialize_event_cursor(record_group_sync_key, EventCategory.team_folders)
                await self.sync_record_groups(app_users)
            else:
                self.logger.info("Running incremental sync for record group events...")
                await self._sync_record_group_changes_with_cursor(app_users)
            self.logger.info("Syncing record groups...")


            await self.sync_personal_record_groups(app_users)


            # Step 4: fetch and sync all user drives
            self.logger.info("Syncing User Drives")
            await self._process_users_in_batches(app_users)

            # Step 4.5 sync for permissions changes
            sharing_sync_key = generate_record_sync_point_key("sharing_events", "team_events", "global")
            sharing_sync_point = await self.dropbox_cursor_sync_point.read_sync_point(sharing_sync_key)

            if not sharing_sync_point.get('cursor'):
                self.logger.info("Initializing cursor for sharing events...")

                # Initialize cursor BEFORE any potential sync work
                await self._initialize_event_cursor(sharing_sync_key, EventCategory.sharing)
            else:
                self.logger.info("Running an INCREMENTAL sync for sharing events...")
                await self._sync_sharing_changes_with_cursor(sharing_sync_key, sharing_sync_point.get('cursor'))

            self.logger.info("Dropbox full sync completed.")
        except Exception as ex:
            self.logger.error(f"❌ Error in DropBox connector run: {ex}")
            raise


    async def _initialize_event_cursor(self, sync_key: str, category) -> None:
        """
        Initialize a cursor that starts from the current moment.
        """
        try:
            from datetime import datetime, timezone

            from dropbox.team_common import TimeRange  # <- It's in team_common!

            # Create a time range that starts from "now"
            current_time = datetime.now(timezone.utc)

            # TimeRange with only start_time means "from now onwards"
            time_range = TimeRange(start_time=current_time)

            response = await self.data_source.team_log_get_events(
                category=category,
                time=time_range,
                limit=1
            )

            if response.success and hasattr(response.data, 'cursor') and response.data.cursor:
                await self.dropbox_cursor_sync_point.update_sync_point(
                    sync_key, {"cursor": response.data.cursor}
                )
                self.logger.info(f"✓ Initialized cursor for {category} from {current_time}")
            else:
                self.logger.warning(f"Could not initialize cursor for {category}: {response.error if not response.success else 'No cursor returned'}")

        except Exception as e:
            self.logger.error(f"Could not initialize event cursor for {category}: {e}", exc_info=True)

    async def _sync_member_changes_with_cursor(self, app_users: List[AppUser]) -> None:
        """
        Syncs team member changes incrementally using the team event log cursor.
        """
        try:
            self.logger.info("Starting incremental sync for team members...")

            # 1. Define the sync point key for member events
            sync_point_key = generate_record_sync_point_key(
                "member_events", "team_events", "global"
            )

            # 2. Get the last saved cursor from your database
            sync_point = await self.dropbox_cursor_sync_point.read_sync_point(sync_point_key)
            cursor = sync_point.get('cursor')

            if not cursor:
                self.logger.warning("No cursor found for incremental member sync.")
                return

            has_more = True
            latest_cursor_to_save = cursor
            events_processed = 0

            while has_more:
                try:
                    # 3. Fetch the latest events from the Dropbox audit log
                    async with self.rate_limiter:
                        response = await self.data_source.team_log_get_events_continue(cursor)

                    if not response.success:
                        self.logger.error(f"⚠️ Error fetching team member event log: {response.error}")
                        break

                    events = response.data.events
                    self.logger.info(f"Processing {len(events)} new member-related events.")

                    # 4. Process each event individually
                    for event in events:
                        try:
                            await self._process_member_event(event, app_users)
                            events_processed += 1
                        except Exception as e:
                            self.logger.error(f"Error processing member event: {e}", exc_info=True)
                            continue

                    # 5. Update state for the next loop iteration
                    latest_cursor_to_save = response.data.cursor
                    has_more = response.data.has_more
                    cursor = latest_cursor_to_save

                except Exception as e:
                    self.logger.error(f"⚠️ Error in member sync loop: {e}", exc_info=True)
                    has_more = False

            # 6. Save the final cursor
            if latest_cursor_to_save:
                self.logger.info(f"Storing latest member sync cursor for key {sync_point_key}")
                await self.dropbox_cursor_sync_point.update_sync_point(
                    sync_point_key,
                    sync_point_data={"cursor": latest_cursor_to_save}
                )

            self.logger.info(f"Incremental member sync completed. Processed {events_processed} events.")

        except Exception as e:
            self.logger.error(f"⚠️ Fatal error in incremental member sync: {e}", exc_info=True)
            raise

    async def _process_member_event(self, event, app_users: List[AppUser]) -> None:
        """
        Process a single member-related event from the Dropbox audit log.
        """
        try:
            # Log the full event for debugging
            self.logger.debug(f"Processing member event: {event}")

            event_type = event.event_type._tag

            if event_type == "member_change_status":
                await self._handle_member_change_status_event(event, app_users)
            else:
                self.logger.debug(f"Ignoring event type: {event_type}")

        except Exception as e:
            self.logger.error(
                f"Error processing member event of type {getattr(event, 'event_type', 'unknown')}: {e}",
                exc_info=True
            )

    async def _handle_member_change_status_event(self, event, app_users: List[AppUser]) -> None:
        """Handle member_change_status events from Dropbox audit log."""
        # Extract user info from event context
        user_email = None
        user_name = None
        team_member_id = None

        if event.context and event.context.is_team_member():
            user_info = event.context.get_team_member()
            user_email = user_info.email
            user_name = user_info.display_name
            team_member_id = user_info.team_member_id

        # Extract status change details
        new_status = None
        previous_status = None

        if hasattr(event.details, 'get_member_change_status_details'):
            status_details = event.details.get_member_change_status_details()
            new_status = status_details.new_value._tag if status_details.new_value else None
            previous_status = status_details.previous_value._tag if status_details.previous_value else None

        if not user_email or not new_status:
            self.logger.warning(
                f"Could not extract required info from member_change_status event. "
                f"email={user_email}, new_status={new_status}"
            )
            return

        self.logger.info(
            f"Member status change for '{user_name}' ({user_email}): "
            f"{previous_status} -> {new_status}"
        )

        try:
            # If new status is 'active', treat as member added
            if new_status == 'active':
                self.logger.info(f"Adding team member '{user_name}' ({user_email}, ID: {team_member_id})")
                await self._handle_member_added(user_email, team_member_id, app_users)

            # If new status is 'removed', treat as member removed
            elif new_status == 'removed':
                self.logger.info(f"Removing team member '{user_name}' ({user_email}, ID: {team_member_id})")
                # await self._handle_member_removed(user_email)

            else:
                self.logger.info(
                    f"Status change to '{new_status}' for '{user_name}'. No action needed."
                )

        except Exception as e:
            self.logger.error(
                f"Error processing member_change_status event for user {user_email}: {e}",
                exc_info=True
            )

    async def _handle_member_added(self, user_email: str, team_member_id: str, app_users: List[AppUser]) -> None:
        """Process a newly added team member from the app_users list."""
        try:
            # Find the specific user in the app_users list
            new_user = None
            for user in app_users:
                if user.email == user_email:
                    new_user = user
                    break

            if not new_user:
                self.logger.warning(
                    f"Could not find newly added user {user_email} in app_users list. "
                    f"User may need to be synced in the next full sync."
                )
                return

            # Process the single user
            await self.data_entities_processor.on_new_app_users([new_user])

            self.logger.info(f"Successfully processed member addition for {user_email}")

        except Exception as e:
            self.logger.error(f"Error processing member addition for {user_email}: {e}", exc_info=True)

    async def _sync_user_groups(self) -> None:
        """
        Syncs all Dropbox groups and their members, collecting them into a
        single batch before sending to the processor.
        """
        try:
            self.logger.info("Starting Dropbox user group synchronization")

            # --- 1. Get all groups, with pagination ---
            all_groups_list = []
            try:
                groups_response = await self.data_source.team_groups_list()
                if not groups_response.success:
                    raise Exception(f"Error fetching groups list: {groups_response.error}")

                all_groups_list.extend(groups_response.data.groups)
                cursor = groups_response.data.cursor
                has_more = groups_response.data.has_more

                while has_more:
                    self.logger.info("Fetching more groups...")
                    groups_response = await self.data_source.team_groups_list_continue(cursor)
                    if not groups_response.success:
                        self.logger.error(f"Error fetching more groups: {groups_response.error}")
                        break  # Stop pagination on error
                    all_groups_list.extend(groups_response.data.groups)
                    cursor = groups_response.data.cursor
                    has_more = groups_response.data.has_more

            except Exception as e:
                self.logger.error(f"❌ Failed to fetch full group list: {e}", exc_info=True)
                raise  # Stop the sync if we can't get the groups

            self.logger.info(f"Found {len(all_groups_list)} total groups. Now processing members.")

            # --- 2. Define permission mapping (similar to record_groups) ---
            # Dropbox group members are either 'owner' or 'member'

            # This will hold our final list of tuples: List[Tuple[AppUserGroup, List[Permission]]]
            user_groups_batch = []

            # --- 3. Loop through all groups to build the batch (NO processor calls inside loop) ---
            for group in all_groups_list:
                try:
                    all_members = await self._fetch_group_members(group.group_id, group.group_name)

                    processor_group, member_permissions = self._create_user_group_with_permissions(
                        group.group_id, group.group_name, all_members
                    )
                    user_groups_batch.append((processor_group, member_permissions))

                except Exception as e:
                    self.logger.error(f"❌ Failed to process group {group.group_name}: {e}", exc_info=True)
                    continue  # Skip this group and move to the next

            # --- 4. Send the ENTIRE batch to the processor ONCE (outside the loop) ---
            if user_groups_batch:
                self.logger.info(f"Submitting {len(user_groups_batch)} user groups to the processor...")
                await self.data_entities_processor.on_new_user_groups(user_groups_batch)
                self.logger.info("Successfully submitted batch to on_new_user_groups.")
            else:
                self.logger.info("No user groups found or processed.")

            self.logger.info("Completed Dropbox user group synchronization.")

        except Exception as e:
            self.logger.error(f"❌ Fatal error in _sync_dropbox_groups: {e}", exc_info=True)
            raise

    async def _sync_group_changes_with_cursor(self) -> None:
        """
        Syncs user group changes incrementally using the team event log cursor.
        """
        try:
            self.logger.info("Starting incremental sync for user groups...")

            # 1. Define a single, global key for the team-wide group event cursor
            sync_point_key = generate_record_sync_point_key(
                "user_group_events", "team_events", "global"
            )

            # 2. Get the last saved cursor from your database
            sync_point = await self.dropbox_cursor_sync_point.read_sync_point(sync_point_key)
            cursor = sync_point.get('cursor')

            if not cursor:
                self.logger.warning("No cursor found for incremental group sync. Running full sync instead.")
                await self._sync_user_groups()
                return

            has_more = True
            latest_cursor_to_save = cursor
            events_processed = 0

            while has_more:
                try:
                    # 3. Fetch the latest events from the Dropbox audit log
                    async with self.rate_limiter:
                        response = await self.data_source.team_log_get_events_continue(cursor)

                    if not response.success:
                        self.logger.error(f"⚠️ Error fetching team event log: {response.error}")
                        break

                    events = response.data.events
                    self.logger.info(f"Processing {len(events)} new group-related events.")

                    # 4. Process each event individually
                    for event in events:
                        try:
                            await self._process_group_event(event)
                            events_processed += 1
                        except Exception as e:
                            self.logger.error(f"Error processing group event: {e}", exc_info=True)
                            continue

                    # 5. Update state for the next loop iteration and for final saving
                    latest_cursor_to_save = response.data.cursor
                    has_more = response.data.has_more
                    cursor = latest_cursor_to_save

                except Exception as e:
                    self.logger.error(f"⚠️ Error in group sync loop: {e}", exc_info=True)
                    has_more = False

            # 6. Save the final, most recent cursor back to the database
            if latest_cursor_to_save:
                self.logger.info(f"Storing latest group sync cursor for key {sync_point_key}")
                await self.dropbox_cursor_sync_point.update_sync_point(
                    sync_point_key,
                    sync_point_data={"cursor": latest_cursor_to_save}
                )

            self.logger.info(f"Incremental group sync completed. Processed {events_processed} events.")

        except Exception as e:
            self.logger.error(f"⚠️ Fatal error in incremental group sync: {e}", exc_info=True)
            raise

    async def _process_group_event(self, event) -> None:
        """
        Process a single group-related event from the Dropbox audit log.
        Based on the actual API response structure.
        """
        try:
            # Log the full event for debugging
            self.logger.debug(f"Processing event: {event}")

            event_type = event.event_type._tag

            if event_type == "group_create":
                await self._handle_group_created_event(event)
            elif event_type == "group_delete":
                await self._handle_group_deleted_event(event)
            elif event_type in ["group_add_member", "group_remove_member"]:
                await self._handle_group_membership_event(event, event_type)
            elif event_type in ["group_rename"]:
                await self._handle_group_renamed_event(event)
            elif event_type in ["group_change_member_role"]:
                await self._handle_group_change_member_role_event(event)
            else:
                self.logger.debug(f"Ignoring event type: {event_type}")

        except Exception as e:
            self.logger.error(f"Error processing group event of type {getattr(event, 'event_type', 'unknown')}: {e}", exc_info=True)

    async def _handle_group_membership_event(self, event, event_type: str) -> None:
        group_id, group_name = None, None
        member_email, member_name = None, None

        # 1. Extract common information (group and user details)
        try:
            for participant in event.participants:
                if participant.is_group():
                    group_info = participant.get_group()
                    group_id = group_info.group_id
                    group_name = group_info.display_name
                elif participant.is_user():
                    user_info = participant.get_user()
                    member_email = user_info.email
                    member_name = user_info.display_name
        except Exception as e:
            self.logger.error(f"Failed to parse participants for event {event_type}: {e}", exc_info=True)
            return

        # 2. Validate that we have the necessary IDs
        if not group_id or not member_email:
            self.logger.warning(f"Could not extract required group_id or member_email from {event_type} event. Skipping.")
            return

        # 3. Perform the appropriate action based on event_type
        if event_type == "group_add_member":
            self.logger.info(f"Adding member '{member_name}' ({member_email}) to group '{group_name}' ({group_id})")

            # Determine permission type (specific to 'add' events)
            permission_type = PermissionType.WRITE  # Default permission
            if hasattr(event.details, 'is_group_owner') and event.details.is_group_owner:
                permission_type = PermissionType.OWNER

            await self.data_entities_processor.on_user_group_member_added(
                external_group_id=group_id,
                user_email=member_email,
                permission_type=permission_type,
                connector_name=self.connector_name
            )

        elif event_type == "group_remove_member":
            self.logger.info(f"Removing member '{member_name}' ({member_email}) from group '{group_name}' ({group_id})")

            await self.data_entities_processor.on_user_group_member_removed(
                external_group_id=group_id,
                user_email=member_email,
                connector_name=self.connector_name
            )

    async def _handle_group_deleted_event(self, event) -> None:
        """Handle group_delete events from Dropbox audit log."""
        # Extract group_id from participants
        group_id = None
        group_name = None

        for participant in event.participants:
            if participant.is_group():
                group_info = participant.get_group()
                group_id = group_info.group_id
                group_name = group_info.display_name
                print(f"Extracted deleted group: {group_name} ({group_id})")
                break

        # Validate we have required information
        if not group_id:
            self.logger.warning("Could not extract group_id from group_delete event")
            return

        self.logger.info(f"Deleting group {group_name} ({group_id})")

        await self.data_entities_processor.on_user_group_deleted(
            external_group_id=group_id,
            connector_name=self.connector_name
        )

    async def _handle_group_created_event(self, event) -> None:
        """Handle group_create events from Dropbox audit log."""
        # Extract group info from event participants
        group_id = None
        group_name = None

        for participant in event.participants:
            if participant.is_group():
                group_info = participant.get_group()
                group_id = group_info.group_id
                group_name = group_info.display_name
                break

        if not group_id:
            self.logger.warning("Could not extract group_id from group_create event")
            return

        self.logger.info(f"Creating group {group_name} ({group_id})")

        try:
            # Process the single newly created group
            await self._process_single_group(group_id, group_name)

        except Exception as e:
            self.logger.error(f"Error processing group_create event for group {group_id}: {e}", exc_info=True)

    async def _process_single_group(self, group_id: str, group_name: str) -> None:
        """
        Process a single group by fetching its members and creating the appropriate
        AppUserGroup and permissions. This reuses logic from _sync_user_groups.
        """
        try:
            # Get all members for this group (reused from _sync_user_groups section 3a)
            all_members = await self._fetch_group_members(group_id, group_name)

            # Create the AppUserGroup and permissions (reused from _sync_user_groups section 3b-3c)
            processor_group, member_permissions = self._create_user_group_with_permissions(
                group_id, group_name, all_members
            )

            # Send to processor (reused from _sync_user_groups section 4)
            user_groups_batch = [(processor_group, member_permissions)]

            self.logger.info(f"Submitting newly created group {group_name} to processor...")
            await self.data_entities_processor.on_new_user_groups(user_groups_batch)
            self.logger.info(f"Successfully processed group_create event for {group_name}")

        except Exception as e:
            self.logger.error(f"Failed to process single group {group_name} ({group_id}): {e}", exc_info=True)


    async def _fetch_group_members(self, group_id: str, group_name: str) -> list:
        """
        Fetch all members for a group with pagination.
        Extracted from _sync_user_groups section 3a for reusability.
        """
        all_members = []

        members_response = await self.data_source.team_groups_members_list(group=group_id)
        if not members_response.success:
            raise Exception(f"Error fetching members for group {group_name}: {members_response.error}")

        all_members.extend(members_response.data.members)
        member_cursor = members_response.data.cursor
        member_has_more = members_response.data.has_more

        while member_has_more:
            self.logger.debug(f"Fetching more members for {group_name}...")
            members_response = await self.data_source.team_groups_members_list_continue(member_cursor)

            if not members_response.success:
                self.logger.error(f"Error during member pagination for {group_name}: {members_response.error}")
                break

            all_members.extend(members_response.data.members)
            member_cursor = members_response.data.cursor
            member_has_more = members_response.data.has_more

        return all_members

    def _create_user_group_with_permissions(self, group_id: str, group_name: str, all_members: list) -> tuple:
        """
        Create AppUserGroup and permissions list from group members.
        Extracted from _sync_user_groups sections 3b-3c for reusability.
        """
        # Permission mapping (from _sync_user_groups)


        # Create the AppUserGroup object (from _sync_user_groups section 3b)
        processor_group = AppUserGroup(
            app_name=self.connector_name,
            source_user_group_id=group_id,
            name=group_name,
            org_id=self.data_entities_processor.org_id
        )

        # Create permissions list (from _sync_user_groups section 3c)
        member_permissions = []
        for member in all_members:
            user_permission = AppUser(
                source_user_id=member.profile.team_member_id,
                email=member.profile.email,
                full_name=member.profile.name.display_name,
                app_name=self.connector_name,
            )
            member_permissions.append(user_permission)

        return processor_group, member_permissions

    async def _handle_group_renamed_event(self, event) -> None:
        """Handle group_rename events from Dropbox audit log."""
        # Extract group info from event participants
        group_id = None
        new_group_name = None

        for participant in event.participants:
            if participant.is_group():
                group_info = participant.get_group()
                group_id = group_info.group_id
                new_group_name = group_info.display_name  # This should have the updated name
                break


        details_obj = event.details

        # Log the structure for debugging
        self.logger.debug(f"Event details type: {type(details_obj)}")
        self.logger.debug(f"Event details: {details_obj}")

        # Try different ways to access the GroupRenameDetails
        if hasattr(details_obj, 'get_group_rename_details'):
            group_rename_details = details_obj.get_group_rename_details()
            old_name = group_rename_details.previous_value
            new_name = group_rename_details.new_value
            self.logger.debug("Used get_group_rename_details() method: old_name=%s, new_name=%s", old_name, new_name)

        if not group_id or not new_group_name:
            self.logger.warning(
                f"Could not extract required info from group_rename event. "
                f"group_id={group_id}, new_name={new_name}"
            )
            return

        self.logger.info(f"Renaming group {group_id} from '{old_name}' to '{new_name}'")

        try:
            await self._update_group_name(group_id, new_name, old_name)

        except Exception as e:
            self.logger.error(f"Error processing group_rename event for group {group_id}: {e}", exc_info=True)

    async def _update_group_name(self, group_id: str, new_name: str, old_name: str = None) -> None:
        """
        Update the name of an existing group in the database.
        """
        try:
            async with self.data_store_provider.transaction() as tx_store:
                # 1. Look up the existing group by external ID
                existing_group = await tx_store.get_user_group_by_external_id(
                    connector_name=self.connector_name,
                    external_id=group_id
                )

                if not existing_group:
                    self.logger.warning(
                        f"Cannot rename group: Group with external ID {group_id} not found in database"
                    )
                    return

                # 2. Update the group name and timestamp
                existing_group.name = new_name
                existing_group.updated_at = get_epoch_timestamp_in_ms()

                # 3. Upsert the updated group
                await tx_store.batch_upsert_user_groups([existing_group])

                self.logger.info(
                    f"Successfully renamed group {group_id} from '{old_name}' to '{new_name}' "
                    f"(internal_id: {existing_group.id})"
                )

        except Exception as e:
            self.logger.error(f"Failed to update group name for {group_id}: {e}", exc_info=True)
            raise

    async def _handle_group_change_member_role_event(self, event) -> None:
        """Handle group_change_member_role events from Dropbox audit log."""
        # Extract group and user info from event participants
        group_id = None
        group_name = None
        user_email = None

        for participant in event.participants:
            if participant.is_group():
                group_info = participant.get_group()
                group_id = group_info.group_id
                group_name = group_info.display_name
            elif participant.is_user():
                user_info = participant.get_user()
                user_email = user_info.email

        # Extract new role from event details
        new_is_owner = None
        try:
            if hasattr(event.details, 'get_group_change_member_role_details'):
                role_details = event.details.get_group_change_member_role_details()
                new_is_owner = role_details.is_group_owner
            elif hasattr(event.details, 'is_group_owner'):
                new_is_owner = event.details.is_group_owner
            else:
                self.logger.warning(f"Could not extract role details from event: {event.details}")
        except Exception as e:
            self.logger.warning(f"Error extracting role details: {e}")
            self.logger.debug(f"Event details: {event.details}")

        # Validate we have required information
        if not group_id or not user_email or new_is_owner is None:
            self.logger.warning(
                f"Missing required info from group_change_member_role event: "
                f"group_id={group_id}, user_email={user_email}, new_is_owner={new_is_owner}"
            )
            return

        # Convert boolean to permission type
        new_permission_type = PermissionType.OWNER if new_is_owner else PermissionType.WRITE

        self.logger.info(
            f"Changing role for user {user_email} in group '{group_name}' ({group_id}) "
            f"to {'owner' if new_is_owner else 'member'} (permission: {new_permission_type})"
        )

        try:
            success = await self._update_user_group_permission(
                group_id, user_email, new_permission_type
            )

            if success:
                self.logger.info(
                    f"Successfully updated role for {user_email} in group {group_name}"
                )
            else:
                self.logger.warning(
                    f"Failed to update role for {user_email} in group {group_name}"
                )

        except Exception as e:
            self.logger.error(
                f"Error processing group_change_member_role event for user {user_email} "
                f"in group {group_id}: {e}", exc_info=True
            )

    async def _update_user_group_permission(
        self,
        group_id: str,
        user_email: str,
        new_permission_type: PermissionType
    ) -> bool:
        """
        Update a user's permission level within a group.
        """
        try:
            async with self.data_store_provider.transaction() as tx_store:
                # 1. Look up the user by email
                user = await tx_store.get_user_by_email(user_email)
                if not user:
                    self.logger.warning(
                        f"Cannot update group permission: User with email {user_email} not found"
                    )
                    return False

                # 2. Look up the group by external ID
                user_group = await tx_store.get_user_group_by_external_id(
                    connector_name=self.connector_name,
                    external_id=group_id
                )
                if not user_group:
                    self.logger.warning(
                        f"Cannot update group permission: Group with external ID {group_id} not found"
                    )
                    return False

                # 3. Construct edge keys
                from_key = f"{CollectionNames.USERS.value}/{user.id}"
                to_key = f"{CollectionNames.GROUPS.value}/{user_group.id}"

                # 4. Check if permission edge exists
                existing_edge = await tx_store.get_edge(from_key, to_key, CollectionNames.PERMISSION.value)
                if not existing_edge:
                    self.logger.warning(
                        f"No existing permission found between user {user_email} and group {user_group.name}. "
                        f"Creating new permission with type {new_permission_type}"
                    )
                    # Create new permission edge
                    permission = Permission(
                        external_id=user.id,
                        email=user_email,
                        type=new_permission_type,
                        entity_type=EntityType.GROUP
                    )
                    permission_edge = permission.to_arango_permission(from_key, to_key)
                    await tx_store.batch_create_edges([permission_edge], CollectionNames.PERMISSION.value)
                    return True

                # 5. Check if permission type has changed
                current_permission_type = existing_edge.get('permissionType')
                if current_permission_type == new_permission_type.value:
                    self.logger.info(
                        f"Permission type already correct for {user_email} in group {user_group.name}: {new_permission_type}"
                    )
                    return True

                # 6. Update the permission by deleting old edge and creating new one
                self.logger.info(
                    f"Updating permission for {user_email} in group {user_group.name} "
                    f"from {current_permission_type} to {new_permission_type}"
                )

                # Delete old edge
                await tx_store.delete_edge(from_key, to_key, CollectionNames.PERMISSION.value)

                # Create new edge with updated permission
                permission = Permission(
                    external_id=user.id,
                    email=user_email,
                    type=new_permission_type,
                    entity_type=EntityType.GROUP
                )
                permission_edge = permission.to_arango_permission(from_key, to_key)
                await tx_store.batch_create_edges([permission_edge], CollectionNames.PERMISSION.value)

                return True

        except Exception as e:
            self.logger.error(
                f"Failed to update user group permission for {user_email} in group {group_id}: {e}",
                exc_info=True
            )
            return False

    def _extract_folder_info_from_event(self, event) -> tuple[Optional[str], Optional[str]]:
        """
        Extract folder ID and name from event assets.

        Returns:
            Tuple of (folder_id, folder_name) or (None, None) if extraction fails
        """
        folder_id = None
        folder_name = None

        for asset in event.assets:
            if asset.is_folder():
                folder_info = asset.get_folder()
                folder_name = folder_info.display_name

                if (folder_info.path and
                    folder_info.path.namespace_relative and
                    folder_info.path.namespace_relative.ns_id):
                    folder_id = folder_info.path.namespace_relative.ns_id
                break

        return folder_id, folder_name

    async def _create_and_sync_single_record_group(
        self,
        folder_id: str,
        folder_name: str,
        team_admin_user
    ) -> None:
        """
        Fetch folder members and create a single record group.
        Used by both full sync and incremental event handling.

        Args:
            folder_id: The Dropbox team folder ID (ns_id)
            folder_name: The display name of the folder
            team_admin_user: AppUser with team_admin role for API calls
        """
        try:
            # Fetch folder members with pagination
            all_users_list = []
            all_groups_list = []

            folder_members = await self.data_source.sharing_list_folder_members(
                shared_folder_id=folder_id,
                team_member_id=team_admin_user.source_user_id,
                as_admin=True
            )

            if not folder_members.success:
                self.logger.warning(
                    f"Failed to fetch members for folder '{folder_name}': {folder_members.error}"
                )
                return

            # Collect members from first page
            if folder_members.data.users:
                all_users_list.extend(folder_members.data.users)
            if folder_members.data.groups:
                all_groups_list.extend(folder_members.data.groups)

            # Handle pagination
            cursor = folder_members.data.cursor
            has_more = getattr(folder_members.data, 'has_more', False)

            while has_more:
                self.logger.debug(f"Fetching more members for folder '{folder_name}'...")

                members_continue = await self.data_source.sharing_list_folder_members_continue(
                    cursor=cursor,
                    team_member_id=team_admin_user.source_user_id,
                    as_admin=True
                )

                if not members_continue.success:
                    self.logger.error(
                        f"Error during member pagination for folder '{folder_name}': {members_continue.error}"
                    )
                    break

                if members_continue.data.users:
                    all_users_list.extend(members_continue.data.users)
                if members_continue.data.groups:
                    all_groups_list.extend(members_continue.data.groups)

                cursor = members_continue.data.cursor
                has_more = getattr(members_continue.data, 'has_more', False)

            self.logger.info(
                f"Fetched {len(all_users_list)} users and {len(all_groups_list)} groups "
                f"for folder '{folder_name}'"
            )

            # Create record group
            record_group = RecordGroup(
                name=folder_name,
                org_id=self.data_entities_processor.org_id,
                external_group_id=folder_id,
                description="Team Folder",
                connector_name=Connectors.DROPBOX,
                group_type=RecordGroupType.DRIVE,
            )

            # Create permissions
            dropbox_to_permission_type = {
                'owner': PermissionType.OWNER,
                'editor': PermissionType.WRITE,
                'viewer': PermissionType.READ,
            }

            permissions_list = []

            for user_info in all_users_list:
                access_level_tag = user_info.access_type._tag
                permission_type = dropbox_to_permission_type.get(access_level_tag, PermissionType.READ)

                user_permission = Permission(
                    email=user_info.user.email,
                    type=permission_type,
                    entity_type=EntityType.USER
                )
                permissions_list.append(user_permission)

            for group_info in all_groups_list:
                access_level_tag = group_info.access_type._tag
                permission_type = dropbox_to_permission_type.get(access_level_tag, PermissionType.READ)

                group_permission = Permission(
                    external_id=group_info.group.group_id,
                    type=permission_type,
                    entity_type=EntityType.GROUP
                )
                permissions_list.append(group_permission)

            # Submit to processor
            await self.data_entities_processor.on_new_record_groups([(record_group, permissions_list)])
            self.logger.info(f"Successfully synced record group '{folder_name}' ({folder_id})")

        except Exception as e:
            self.logger.error(
                f"Error creating record group for folder '{folder_name}' ({folder_id}): {e}",
                exc_info=True
            )
            raise


    async def sync_record_groups(self, users: List[AppUser]) -> None:
        """Sync all team folders as record groups."""
        # Find a team admin user
        team_admin_user = None
        for user in users:
            if user.title == "team_admin":
                team_admin_user = user
                break

        if not team_admin_user:
            self.logger.error("No team admin user found. Cannot sync record groups.")
            return

        self.logger.info(f"Using team admin user: {team_admin_user.email} (ID: {team_admin_user.source_user_id})")

        # Fetch all team folders with pagination
        all_team_folders = []

        team_folders_response = await self.data_source.team_team_folder_list()

        if not team_folders_response.success:
            self.logger.error(f"Failed to fetch team folders: {team_folders_response.error}")
            return

        all_team_folders.extend(team_folders_response.data.team_folders)

        cursor = team_folders_response.data.cursor
        has_more = getattr(team_folders_response.data, 'has_more', False)

        while has_more:
            self.logger.debug("Fetching more team folders...")

            folders_continue = await self.data_source.team_team_folder_list_continue(cursor=cursor)

            if not folders_continue.success:
                self.logger.error(f"Error during team folder pagination: {folders_continue.error}")
                break

            all_team_folders.extend(folders_continue.data.team_folders)
            cursor = folders_continue.data.cursor
            has_more = getattr(folders_continue.data, 'has_more', False)

        self.logger.info(f"Fetched {len(all_team_folders)} total team folders")

        # Process each active folder using the shared function
        for folder in all_team_folders:
            if folder.status._tag != "active":
                continue

            try:
                await self._create_and_sync_single_record_group(
                    folder_id=folder.team_folder_id,
                    folder_name=folder.name,
                    team_admin_user=team_admin_user
                )
            except Exception as e:
                self.logger.error(f"Failed to sync folder '{folder.name}': {e}", exc_info=True)
                continue

    async def sync_personal_record_groups(self, users: List[AppUser]) -> None:
        record_groups = []
        for user in users:
            # Validate data first
            if not user.full_name or not user.full_name.strip():
                print(f"⚠️ Skipping user with empty full_name: {user.email}")
                continue

            if not user.source_user_id or not user.source_user_id.strip():
                print(f"⚠️ Skipping user with empty source_user_id: {user.email}")
                continue

            record_group = RecordGroup(
                name=user.full_name,
                org_id=self.data_entities_processor.org_id,
                description="Personal Folder",
                external_group_id=user.source_user_id,
                connector_name=Connectors.DROPBOX,
                group_type=RecordGroupType.DRIVE,
            )

            # Create permission for the user (OWNER)
            user_permission = Permission(
                email=user.email,
                type=PermissionType.OWNER,
                entity_type=EntityType.USER
            )

            # Append the record group and its associated permissions
            record_groups.append((record_group, [user_permission]))

        await self.data_entities_processor.on_new_record_groups(record_groups)

    async def _sync_record_group_changes_with_cursor(self, users: List[AppUser]) -> None:
        """
        Syncs record group (team folder) changes incrementally using the team event log cursor.
        """
        try:
            self.logger.info("Starting incremental sync for record groups...")

            sync_point_key = generate_record_sync_point_key(
                "record_group_events", "team_events", "global"
            )

            sync_point = await self.dropbox_cursor_sync_point.read_sync_point(sync_point_key)
            cursor = sync_point.get('cursor')

            if not cursor:
                self.logger.warning("No cursor found for incremental record group sync.")
                # Initialize cursor for team folder events
                await self._initialize_event_cursor(sync_point_key, EventCategory.team_folders)
                return

            has_more = True
            latest_cursor_to_save = cursor

            while has_more:
                try:
                    async with self.rate_limiter:
                        response = await self.data_source.team_log_get_events_continue(cursor)

                    if not response.success:
                        self.logger.error(f"Error fetching team folder event log: {response.error}")
                        break

                    events = response.data.events
                    self.logger.info(f"Processing {len(events)} team folder events.")

                    for event in events:
                        try:
                            await self._process_record_group_event(event, users)
                        except Exception as e:
                            self.logger.error(f"Error processing record group event: {e}", exc_info=True)
                            continue

                    latest_cursor_to_save = response.data.cursor
                    has_more = response.data.has_more
                    cursor = latest_cursor_to_save

                except Exception as e:
                    self.logger.error(f"Error in record group sync loop: {e}", exc_info=True)
                    has_more = False

            if latest_cursor_to_save:
                await self.dropbox_cursor_sync_point.update_sync_point(
                    sync_point_key,
                    sync_point_data={"cursor": latest_cursor_to_save}
                )

        except Exception as e:
            self.logger.error(f"Fatal error in incremental record group sync: {e}", exc_info=True)
            raise

    async def _process_record_group_event(self, event, users) -> None:
        """
        Process a single record group (team folder) event from the Dropbox audit log.
        """
        try:
            self.logger.debug(f"Processing record group event: {event}")

            event_type = event.event_type._tag

            if event_type == "team_folder_create":
                await self._handle_record_group_created_event(event, users)
            elif event_type == "team_folder_rename":
                await self._handle_record_group_renamed_event(event)
            elif event_type == "team_folder_permanently_delete":
                await self._handle_record_group_deleted_event(event)
            elif event_type == "team_folder_change_status":
                await self._handle_record_group_status_changed_event(event, users)
            else:
                self.logger.debug(f"Ignoring event type: {event_type}")

        except Exception as e:
            self.logger.error(f"Error processing record group event of type {getattr(event, 'event_type', 'unknown')}: {e}", exc_info=True)

    async def _handle_record_group_created_event(self, event, users: List[AppUser]) -> None:
        """Handle team_folder_create events."""
        folder_id, folder_name = self._extract_folder_info_from_event(event)

        if not folder_id or not folder_name:
            self.logger.warning(
                f"Could not extract folder info from team_folder_create event. "
                f"folder_id={folder_id}, folder_name={folder_name}"
            )
            return

        self.logger.info(f"Creating record group for team folder '{folder_name}' (ID: {folder_id})")

        # Find team admin user
        team_admin_user = None
        for user in users:
            if hasattr(user, 'title') and user.title == "team_admin":
                team_admin_user = user
                break

        if not team_admin_user:
            self.logger.error("No team admin user found. Cannot sync newly created folder.")
            return

        try:
            await self._create_and_sync_single_record_group(folder_id, folder_name, team_admin_user)
            self.logger.info(f"Successfully processed team_folder_create event for '{folder_name}'")
        except Exception as e:
            self.logger.error(
                f"Error processing team_folder_create event for folder '{folder_name}' ({folder_id}): {e}",
                exc_info=True
            )

    async def _handle_record_group_renamed_event(self, event) -> None:
        """Handle team_folder_rename events."""
        folder_id, new_name = self._extract_folder_info_from_event(event)

        # Try to get old name from event details
        old_name = None
        if hasattr(event.details, 'get_team_folder_rename_details'):
            rename_details = event.details.get_team_folder_rename_details()
            old_name = getattr(rename_details, 'previous_folder_name', None)

        if not folder_id or not new_name:
            self.logger.warning(
                f"Could not extract required info from team_folder_rename event. "
                f"folder_id={folder_id}, new_name={new_name}"
            )
            return

        self.logger.info(f"Renaming record group {folder_id} from '{old_name}' to '{new_name}'")

        try:
            await self.data_entities_processor.update_record_group_name(folder_id, new_name, old_name, self.connector_name)
        except Exception as e:
            self.logger.error(
                f"Error processing team_folder_rename event for folder {folder_id}: {e}",
                exc_info=True
            )

    async def _handle_record_group_deleted_event(self, event) -> None:
        """Handle team_folder_permanently_delete events."""
        folder_id, folder_name = self._extract_folder_info_from_event(event)

        if not folder_id:
            self.logger.warning("Could not extract folder_id from team_folder_permanently_delete event")
            return

        self.logger.info(f"Deleting record group '{folder_name}' ({folder_id})")

        try:
            await self.data_entities_processor.on_record_group_deleted(
                external_group_id=folder_id,
                connector_name=self.connector_name
            )
        except Exception as e:
            self.logger.error(
                f"Error processing team_folder_permanently_delete event for folder {folder_id}: {e}",
                exc_info=True
            )

    async def _handle_record_group_status_changed_event(self, event, users: List[AppUser]) -> None:
        """Handle team_folder_change_status events."""

        folder_id, folder_name = self._extract_folder_info_from_event(event)

        # Extract old and new status from event details
        old_status = None
        new_status = None

        if hasattr(event.details, 'get_team_folder_change_status_details'):
            status_details = event.details.get_team_folder_change_status_details()
            old_status = getattr(status_details.previous_value, '_tag', None)
            new_status = getattr(status_details.new_value, '_tag', None)

        if not folder_id or not new_status:
            self.logger.warning(
                f"Could not extract required info from team_folder_change_status event. "
                f"folder_id={folder_id}, old_status={old_status}, new_status={new_status}"
            )
            return

        self.logger.info(
            f"Status change for record group '{folder_name}' ({folder_id}): "
            f"{old_status} -> {new_status}"
        )

        try:
            # If changing TO active from any other status, treat as creation/reactivation
            if new_status == 'active':
                self.logger.info(
                    f"Folder '{folder_name}' is now active. Syncing as new/reactivated record group."
                )

                await self._handle_record_group_created_event(event, users)

            # If changing FROM active to any other status (archived, etc.), treat as deletion
            elif old_status == 'active':
                self.logger.info(
                    f"Folder '{folder_name}' is no longer active. Deleting record group."
                )
                await self._handle_record_group_deleted_event(event)

            # For other status transitions (e.g., archived -> permanently_deleted), log but don't act
            else:
                self.logger.info(
                    f"Status change from {old_status} to {new_status} for '{folder_name}'. "
                    f"No action needed (folder was already inactive)."
                )

        except Exception as e:
            self.logger.error(
                f"Error processing team_folder_change_status event for folder {folder_id}: {e}",
                exc_info=True
            )

    async def _sync_sharing_changes_with_cursor(self, sync_point_key: str, cursor: str) -> None:
        """
        Listens for sharing events and triggers the re-sync workflow using the correct
        response structure.
        """
        self.logger.info("Starting incremental sync for sharing events...")
        has_more = True
        latest_cursor_to_save = cursor

        while has_more:
            try:
                response = await self.data_source.team_log_get_events_continue(cursor)

                if not response.success:
                    self.logger.error(f"⚠️ Error fetching sharing event log: {response.error}")
                    break

                for event in response.data.events:
                    try:
                        event_type = event.event_type._tag

                        # 1. Check for the correct, specific event types
                        if event_type in [
                            "shared_content_add_member",
                            "shared_content_remove_member",
                            "shared_content_change_member_role" # This is a likely name for role changes
                        ]:
                            self.logger.info(f"Processing permission change event: {event_type}")

                            # 2. Get the actor's information
                            actor = event.actor.get_admin() if event.actor.is_admin() else event.actor.get_user()
                            if not actor:
                                self.logger.warning("Could not determine actor for event. Skipping.")
                                continue

                            team_member_id = actor.team_member_id
                            user_email = actor.email

                            # 3. Iterate through assets to find the file/folder ID (CORRECTED LOGIC)
                            if not event.assets:
                                self.logger.warning(f"Event {event_type} has no assets. Skipping.")
                                continue

                            for asset in event.assets:

                                if asset.is_file():
                                    file_info = asset.get_file()
                                    id_to_sync = file_info.file_id
                                    ns_id_for_file = None
                                    if file_info.path and file_info.path.namespace_relative:
                                        ns_id_for_file = file_info.path.namespace_relative.ns_id

                                    await self._resync_record_by_external_id(
                                        external_id=id_to_sync,
                                        team_member_id=team_member_id,
                                        user_email=user_email,
                                        is_folder=False,  # <-- Set flag for file
                                        ns_id=ns_id_for_file
                                    )

                                elif asset.is_folder():
                                    folder_info = asset.get_folder()
                                    id_to_sync = None
                                    if folder_info.path and folder_info.path.namespace_relative:
                                        id_to_sync = folder_info.path.namespace_relative.ns_id

                                    if id_to_sync:
                                        await self._resync_record_by_external_id(
                                            external_id=id_to_sync,
                                            team_member_id=team_member_id,
                                            user_email=user_email,
                                            is_folder=True  # <-- Set flag for folder
                                        )

                    except Exception as e:
                        self.logger.error(f"Error processing a single sharing event: {e}", exc_info=True)
                        continue # Move to the next event

                # 5. Update state for the next loop
                latest_cursor_to_save = response.data.cursor
                has_more = response.data.has_more
                cursor = latest_cursor_to_save

            except Exception as e:
                self.logger.error(f"Fatal error in sharing sync loop: {e}", exc_info=True)
                has_more = False

        # 6. Save the final cursor
        self.logger.info(f"Storing latest sharing sync cursor for key {sync_point_key}")
        await self.dropbox_cursor_sync_point.update_sync_point(
            sync_point_key,
            sync_point_data={"cursor": latest_cursor_to_save}
        )

    async def _resync_record_by_external_id(
        self,
        external_id: str,
        team_member_id: str,
        user_email: str,
        is_folder: bool,
        ns_id: Optional[str] = None
    ) -> None:
        """
        Fetches the latest metadata for a single record (file or folder) from Dropbox
        and processes it through the existing update workflow.
        """
        self.logger.info(f"Re-syncing record (is_folder={is_folder}) due to a permission event: {external_id}")
        try:
            metadata_result = None
            record_group_id = None
            file_id = None
            is_person_folder = False
            entry = None

            # 1. Fetch metadata based on the is_folder flag.
            if not is_folder:  # It's a File
                metadata_result = await self.data_source.files_get_metadata(
                    path=external_id,
                    team_member_id=team_member_id,
                    team_folder_id=ns_id
                )
            else:  # It's a Folder
                # For shared folders, use ns: prefix AND provide team_folder_id
                self.logger.info(f"Fetching FolderMetadata with files/get_metadata for ns:{external_id}")
                ns_metadata_result = await self.data_source.files_get_metadata(
                    path=f"ns:{external_id}",
                    team_member_id=team_member_id,
                    team_folder_id=external_id
                )

                if not ns_metadata_result or not ns_metadata_result.success:
                    self.logger.error(f"Could not fetch ns: metadata for re-sync: {external_id}. Error: {ns_metadata_result.error if ns_metadata_result else 'No response'}")
                    return

                # Second call with the file ID to get complete metadata
                file_id = ns_metadata_result.data.id
                self.logger.info(f"Fetching complete metadata using file ID: {file_id}")
                metadata_result = await self.data_source.files_get_metadata(
                    path=file_id,
                    team_member_id=team_member_id,
                    team_folder_id=external_id
                )
                file_id = metadata_result.data.id

            if not metadata_result or not metadata_result.success:
                self.logger.error(f"Could not fetch metadata for re-sync: {external_id}. Error: {metadata_result.error}")
                return

            entry = metadata_result.data

            # Determine record_group_id based on entry type
            if isinstance(entry, FileMetadata):
                async with self.data_store_provider.transaction() as tx_store:
                    existing_record = await tx_store.get_record_by_external_id(self.connector_name, external_id)
                    if not existing_record:
                        self.logger.warning(f"File record {external_id} not found in DB for re-sync. Cannot determine parent group.")
                        return
                    record_group_id = existing_record.external_record_group_id
                    is_person_folder = (record_group_id == team_member_id)
            else:  # FolderMetadata (shared folder)
                async with self.data_store_provider.transaction() as tx_store:
                    existing_record = await tx_store.get_record_by_external_id(self.connector_name, file_id)
                    if not existing_record:
                        self.logger.warning(f"File record {file_id} not found in DB for re-sync. Cannot determine parent group.")
                        return
                    record_group_id = existing_record.external_record_group_id
                    is_person_folder = (record_group_id == team_member_id)
                # record_group_id = external_id
                # is_person_folder = False

            record_update = await self._process_dropbox_entry(
                entry=entry,
                user_id=team_member_id,
                user_email=user_email,
                record_group_id=record_group_id,
                is_person_folder=is_person_folder
            )

            if record_update and record_update.is_updated:
                self.logger.info(f"Permissions updated for {record_update.record.record_name}")
                await self._handle_record_updates(record_update)
            else:
                self.logger.info(f"Re-sync for {external_id} did not result in an update.")

        except Exception as e:
            self.logger.error(f"Error during single record re-sync for {external_id}: {e}", exc_info=True)

    async def run_incremental_sync(self) -> None:
        """Runs an incremental sync using the last known cursor."""
        self.logger.info("Starting Dropbox incremental sync.")
        sync_point_key = generate_record_sync_point_key(RecordType.DRIVE.value, "root", )
        sync_point = await self.record_sync_point.dropbox_cursor_sync_point(sync_point_key)

        cursor = sync_point.get('cursor') if sync_point else None
        if not cursor:
            self.logger.warning("No cursor found. Running a full sync instead.")
            await self.run_sync()
            return

        await self._sync_from_source(cursor=cursor)
        self.logger.info("Dropbox incremental sync completed.")

    async def get_signed_url(self, record: Record) -> Optional[str]:
        if not self.data_source:
            return None
        try:
            user_with_permission = None
            async with self.data_store_provider.transaction() as tx_store:
                user_with_permission = await tx_store.get_first_user_with_permission_to_node(f"{CollectionNames.RECORDS.value}/{record.id}")
                file_record = await tx_store.get_file_record_by_id(record.id)
            if not user_with_permission:
                self.logger.warning(f"No user found with permission to node: {record.id}")
                return None
            if not file_record:
                self.logger.warning(f"No file record found for node: {record.id}")
                return None

            members = [UserSelectorArg("email", user_with_permission.email)]
            team_member_info = await self.data_source.team_members_get_info_v2(members=members)
            team_member_id = team_member_info.data.members_info[0].get_member_info().profile.team_member_id
            # Dropbox uses path or file ID for temporary links. ID is more robust.
            team_folder_id = None
            if record.external_record_group_id and not record.external_record_group_id.startswith("dbmid:"):
                team_folder_id = record.external_record_group_id

            response = await self.data_source.files_get_temporary_link(path=file_record.path, team_folder_id=team_folder_id, team_member_id=team_member_id)
            return response.data.link
        except Exception as e:
            self.logger.error(f"Error creating signed URL for record {record.id}: {e}")
            return None


    async def stream_record(self, record: Record) -> StreamingResponse:
        signed_url = await self.get_signed_url(record)
        if not signed_url:
            raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="File not found or access denied")

        return StreamingResponse(
            stream_content(signed_url),
            media_type=record.mime_type if record.mime_type else "application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename={record.record_name}"
            }
        )

    async def test_connection_and_access(self) -> bool:
        if not self.data_source:
            return False
        try:
            await self.data_source.users_get_current_account()
            self.logger.info("Dropbox connection test successful.")
            return True
        except Exception as e:
            self.logger.error(f"Dropbox connection test failed: {e}", exc_info=True)
            return False

    def handle_webhook_notification(self, notification: Dict) -> None:
        """Handles a webhook notification by triggering an incremental sync."""
        self.logger.info("Dropbox webhook received. Triggering incremental sync.")
        asyncio.create_task(self.run_incremental_sync())

    def cleanup(self) -> None:
        self.logger.info("Cleaning up Dropbox connector resources.")
        self.data_source = None

    # @classmethod
    # async def create_connector(
    #     cls, logger, arango_service: BaseArangoService, config_service: ConfigurationService
    # ) -> "BaseConnector":
    #     data_entities_processor = DataSourceEntitiesProcessor(
    #         logger, arango_service, config_service
    #     )
    #     await data_entities_processor.initialize()
    #     return DropboxConnector(
    #         logger, data_entities_processor, arango_service, config_service
    #     )

    @classmethod
    async def create_connector(
        cls, logger, data_store_provider: DataStoreProvider, config_service: ConfigurationService
    ) -> "BaseConnector":
        data_entities_processor = DataSourceEntitiesProcessor(
            logger, data_store_provider, config_service
        )
        await data_entities_processor.initialize()
        return DropboxConnector(
            logger, data_entities_processor, data_store_provider, config_service
        )
