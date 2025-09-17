import asyncio
import mimetypes
import uuid
from logging import Logger
from typing import AsyncGenerator, Dict, List, Optional, Tuple, Union
# from datetime import datetime
from datetime import datetime, timezone

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

# Dropbox SDK specific types
from dropbox.files import DeletedMetadata, FileMetadata, FolderMetadata, ListFolderResult
from dropbox.sharing import AccessLevel

# Base connector and service imports
from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors, OriginTypes
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
from app.connectors.core.base.data_store.arango_data_store import ArangoDataStore
from app.connectors.services.base_arango_service import BaseArangoService

# App-specific Dropbox client imports
from app.connectors.sources.dropbox.common.apps import DropboxApp
from app.sources.client.dropbox.dropbox_ import DropboxClient, DropboxTokenConfig

from app.sources.external.dropbox.dropbox_ import DropboxDataSource

# Model imports
from app.models.entities import (
    FileRecord,
    Record,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.utils.streaming import stream_content

from app.models.entities import User, AppUser
from app.sources.external.dropbox.pretty_print import to_pretty_json
from aiolimiter import AsyncLimiter

from app.connectors.sources.microsoft.common.msgraph_client import (
    RecordUpdate,
)

from app.config.constants.arangodb import MimeTypes

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
        credentials_config = await self.config_service.get_config(
            f"/services/connectors/dropbox/config/{self.data_entities_processor.org_id}"
        )
        if not credentials_config or not credentials_config.get("accessToken"):
            self.logger.error("Dropbox access token not found in configuration.")
            return False

        access_token = credentials_config.get("accessToken")
        is_team = credentials_config.get("isTeam", False)

        try:
            config = DropboxTokenConfig(token=access_token)
            client = await DropboxClient.build_with_config(config, is_team=is_team)
            self.data_source = DropboxDataSource(client)
            self.logger.info("Dropbox client initialized successfully.")
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize Dropbox client: {e}", exc_info=True)
            return False

    # Place these new methods inside the DropboxConnector class

    def _permissions_equal(self, old_perms: List[Permission], new_perms: List[Permission]) -> bool:
        """
        Compare two lists of permissions to detect changes.
        """
        if not old_perms and not new_perms:
            return True
        if not old_perms or not new_perms: # Catches one list being empty and the other not
            return False
        if len(old_perms) != len(new_perms):
            return False

        # Create sets of permission tuples for comparison
        old_set = {(p.external_id, p.type, p.entity_type) for p in old_perms}
        new_set = {(p.external_id, p.type, p.entity_type) for p in new_perms}

        return old_set == new_set

    async def _process_dropbox_entry(
        self, entry: Union[FileMetadata, FolderMetadata, DeletedMetadata], user_id: str, user_email: str
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
                    print("!!!!!!!!!!!! content changed !!!!!!!!!!!!!!")
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
            

            signed_url = None
            if is_file:
                temp_link_result = await self.data_source.files_get_temporary_link(
                    entry.path_lower,
                    team_member_id=user_id
                )
                if temp_link_result.success:
                    signed_url = temp_link_result.data.link
            
            file_record = FileRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                record_name=entry.name,
                record_type=record_type,
                record_group_type=RecordGroupType.DRIVE.value,
                external_record_group_id=user_id,
                external_record_id=entry.id,
                external_revision_id=entry.rev if is_file else None,
                version=0 if is_new else existing_record.version + 1,
                origin=OriginTypes.CONNECTOR.value,
                connector_name=self.connector_name,
                # Use the safely acquired timestamp
                created_at=timestamp_ms,
                updated_at=timestamp_ms,
                source_created_at=timestamp_ms,
                source_updated_at=timestamp_ms,
                weburl=f"https://www.dropbox.com/home{entry.path_display}",
                signed_url=signed_url,
                parent_external_record_id=None,
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
            new_permissions = [
                Permission(external_id=user_id, email=user_email, type=PermissionType.WRITE, entity_type=EntityType.USER)
            ]
            
            old_permissions = []
            # if existing_record:
            #     # NOTE: This assumes your arango_service has a method to fetch permissions for a record.
            #     old_permissions = await self.arango_service.get_permissions_for_record(existing_record.id) or []
            
            # if not self._permissions_equal(old_permissions, new_permissions):
            #     permissions_changed = True
            #     is_updated = True

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
        self, entries: List[Union[FileMetadata, FolderMetadata, DeletedMetadata]], user_id: str, user_email: str
    ) -> AsyncGenerator[Tuple[Optional[FileRecord], List[Permission], RecordUpdate], None]:
        """
        Process Dropbox entries and yield records with their permissions.
        This allows non-blocking processing of large datasets.
        """
        for entry in entries:
            try:
                record_update = await self._process_dropbox_entry(entry, user_id, user_email)
                if record_update:
                    yield (record_update.record, record_update.new_permissions or [], record_update)
                await asyncio.sleep(0)
            except Exception as e:
                self.logger.error(f"Error processing item in generator: {e}", exc_info=True)
                continue

    async def   _handle_record_updates(self, record_update: RecordUpdate) -> None:
        """
        Handle different types of record updates (new, updated, deleted).
        """
        try:
            if record_update.is_deleted:
                await self.data_entities_processor.on_record_deleted(
                    record_id=record_update.external_record_id
                )
                print("should be deleted by now 2")
            elif record_update.is_new:
                self.logger.info(f"New record detected: {record_update.record.record_name}")
            elif record_update.is_updated:
                async with self.data_store_provider.transaction() as tx_store:
                    if record_update.content_changed:
                        self.logger.info(f"Content changed for record: {record_update.record.record_name}")
                        await self.data_entities_processor.on_record_content_update(record_update.record, tx_store)
                    if record_update.metadata_changed:
                        self.logger.info(f"Metadata changed for record: {record_update.record.record_name}")
                        await self.data_entities_processor.on_record_metadata_update(record_update.record, tx_store)
                    if record_update.permissions_changed:
                        self.logger.info(f"Permissions changed for record: {record_update.record.record_name}")
                        await self.data_entities_processor.on_updated_record_permissions(
                            record_update.record,
                            record_update.new_permissions,
                            tx_store
                        )
        except Exception as e:
            self.logger.error(f"Error handling record updates: {e}", exc_info=True)

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
                if not member_info: continue

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

    async def _sync_from_source(self, path: str = "", cursor: Optional[str] = None):
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

    async def _process_users_in_batches(self, users: List[User]):
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

                
                print("Going to run sync for these users:", batch);

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

        This function fetches a list of all file and folder changes since the last
        sync, processes them in batches, and yields control periodically to allow
        for concurrent operations.

        Args:
            user_id: The Dropbox team member ID of the user to sync.
        """
        try:
            self.logger.info(f"Starting Dropbox sync with yield for user {user_email}")

            # 1. Get current sync state from the database
            # Instead of deltaLink/nextLink, Dropbox uses a single 'cursor'.
            sync_point_key = generate_record_sync_point_key(RecordType.DRIVE.value, "users", user_id)
            sync_point = await self.dropbox_cursor_sync_point.read_sync_point(sync_point_key)
            cursor = sync_point.get('cursor')

            self.logger.info(f"Sync point key: {sync_point_key}")
            self.logger.info(f"Retrieved sync point: {sync_point}")
            self.logger.info(f"Cursor value: {cursor}")
            self.logger.info(f"Cursor is None: {cursor is None}")
            self.logger.info(f"Cursor is empty string: {cursor == ''}")

            batch_records = []
            batch_count = 0
            has_more = True

            

            while has_more:
                # 2. Fetch changes from Dropbox
                # We use files_list_folder() for the first call and
                # files_list_folder_continue() for subsequent calls.
                async with self.rate_limiter:
                    if cursor:
                        print("\n old cursor: ", cursor)
                        print("\n!!!!!!!!! continue api should run here !!!!!!!\n")
                        result = await self.data_source.files_list_folder_continue(cursor,
                        team_member_id=user_id,
                        )
                        print("\n!!!!!!!!! continue api has ran !!!!!!!\n")
                        
                    else:
                        # This is the first sync for this user
                        result = await self.data_source.files_list_folder(path="", 
                        team_member_id=user_id,
                        recursive=True)
    
                
                # print("Result:",result)
                print("! new Cursor:", result.data.cursor)
                # return
                entries = result.data.entries

                # 3. Process the entries from the current page
                # This requires a new generator that understands the Dropbox entry format
                async for file_record, permissions, record_update in self._process_dropbox_items_generator(entries, user_id, user_email):
                    # print("file_record: ", file_record)
                    # print("permissions: ", permissions)
                    # print("record_update: ", record_update)
                    if record_update.is_deleted:
                        # Handle deletion immediately
                        await self._handle_record_updates(record_update)
                        continue

                    print("!!!!!!!!!!!!!!!!! D1")

                    if file_record:
                        # Add to batch
                        batch_records.append((file_record, permissions))
                        batch_count += 1

                        # Handle updates if needed
                        if record_update.is_updated:
                            await self._handle_record_updates(record_update)

                        print("!!!!!!!!!!!!!!!!! D2")

                        # Process batch when it reaches the size limit
                        if batch_count >= self.batch_size:
                            print("!!!!! running on new records !!!!!")
                            await self.data_entities_processor.on_new_records(batch_records)
                            batch_records = []
                            batch_count = 0

                            # Allow other operations to proceed (the "yield" part)
                            await asyncio.sleep(0.1)
                        
                        print("!!!!!!!!!!!!!!!!! D3")

                # Process any remaining records in the batch from the last page
                print("OUT OF THE FOR LOOP- _process_dropbox_items_generator")
                if batch_records:
                    await self.data_entities_processor.on_new_records(batch_records)
                    batch_records = []
                    batch_count = 0

                # 4. Update the sync state for the next iteration
                # We save the new cursor after every page. This makes the process
                # resilient to interruptions.
                
                cursor = result.data.cursor
                print("!!!!!!!!! Trying to store cursor: ", cursor)
                await self.dropbox_cursor_sync_point.update_sync_point(
                    sync_point_key,
                    sync_point_data={"cursor": cursor}
                )

                # The loop continues as long as Dropbox says there's more data
                has_more = result.has_more

            self.logger.info(f"Completed Dropbox sync for user {user_id}")

        except ApiError as ex:
            self.logger.error(f"Dropbox API Error during sync for user {user_id}: {ex}")
            # Here you could add specific handling for auth errors, etc.
            raise
        except Exception as ex:
            self.logger.error(f"Error in Dropbox sync for user {user_id}: {ex}")
            raise

    async def run_sync(self) -> None:
        """Runs a full synchronization from the Dropbox account root."""
        try:
            self.logger.info("Starting Dropbox full sync.")
            # self.logger.info("Code will excecute here!")

            # step 1: fetch ans sync all users
            users = await self.data_source.team_members_list()

            await self.data_entities_processor.on_new_app_users(users.data["members"])

            # Step 2: fetch and sync all user groups
            #for later

            # Step 3: fetch and sync all user drives
            self.logger.info("Syncing User Drives")
            await self._process_users_in_batches(users.data["members"])
            
            self.logger.info("Dropbox full sync completed.")
        except Exception as ex:
            self.logger.error("❌ Error in DropBox connector run: {ex}")
            raise

    async def run_incremental_sync(self) -> None:
        """Runs an incremental sync using the last known cursor."""
        self.logger.info("Starting Dropbox incremental sync.")
        sync_point_key = generate_record_sync_point_key(RecordType.DRIVE.value, "root", )
        sync_point = await self.record_sync_point.read_sync_point(sync_point_key)
        
        cursor = sync_point.get('cursor') if sync_point else None
        if not cursor:
            self.logger.warning("No cursor found. Running a full sync instead.")
            await self.run_sync()
            return
            
        await self._sync_from_source(cursor=cursor)
        self.logger.info("Dropbox incremental sync completed.")

    async def get_signed_url(self, record: Record) -> Optional[str]:
        if not self.data_source: return None
        try:
            # Dropbox uses path or file ID for temporary links. ID is more robust.
            response = await self.data_source.files_get_temporary_link(path=record.external_record_id)
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
            media_type=record.mime_type,
            headers={"Content-Disposition": f"attachment; filename=\"{record.record_name}\""}
        )

    async def test_connection_and_access(self) -> bool:
        if not self.data_source: return False
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

    @classmethod
    async def create_connector(
        cls, logger, arango_service: BaseArangoService, config_service: ConfigurationService
    ) -> "BaseConnector":
        data_entities_processor = DataSourceEntitiesProcessor(
            logger, arango_service, config_service
        )
        await data_entities_processor.initialize()
        return DropboxConnector(
            logger, data_entities_processor, arango_service, config_service
        )