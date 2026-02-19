import asyncio
import mimetypes
import uuid
from collections import deque
from datetime import datetime, timezone
from logging import Logger
from typing import AsyncGenerator, Dict, List, NoReturn, Optional, Set, Tuple

from aiolimiter import AsyncLimiter
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

# Base connector and service imports
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
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
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
    FilterType,
    IndexingFilterKey,
    SyncFilterKey,
    load_connector_filters,
)

# App-specific Box client imports
from app.connectors.sources.box.common.apps import BoxApp
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate

# Model imports
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    IndexingStatus,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.box.box import (
    BoxCCGConfig,
    BoxClient,
)
from app.sources.external.box.box import BoxDataSource
from app.utils.streaming import create_stream_record_response, stream_content


# Helper functions
def get_parent_path_from_path(path: str) -> Optional[str]:
    """Extracts the parent path from a file/folder path."""
    if not path or path == "/" or "/" not in path.lstrip("/"):
        return None
    parent_path = "/".join(path.strip("/").split("/")[:-1])
    return f"/{parent_path}" if parent_path else "/"


def get_file_extension(filename: str) -> Optional[str]:
    """Extracts the extension from a filename."""
    if "." in filename:
        parts = filename.split(".")
        if len(parts) > 1:
            return parts[-1].lower()
    return None


def get_mimetype_enum_for_box(entry_type: str, filename: str = None) -> MimeTypes:
    """
    Determines the correct MimeTypes enum member for a Box entry.

    Args:
        entry_type: Type of Box entry ('file' or 'folder')
        filename: Name of the file (for MIME type guessing)

    Returns:
        The corresponding MimeTypes enum member.
    """
    if entry_type == 'folder':
        return MimeTypes.FOLDER

    if entry_type == 'file' and filename:
        mime_type_str, _ = mimetypes.guess_type(filename)
        if mime_type_str:
            try:
                return MimeTypes(mime_type_str)
            except ValueError:
                return MimeTypes.BIN

    return MimeTypes.BIN


@ConnectorBuilder("Box")\
    .in_group("Cloud Storage")\
    .with_description("Sync files and folders from Box")\
    .with_categories(["Storage"])\
    .with_scopes([ConnectorScope.TEAM])\
    .with_auth([
        AuthBuilder.type(AuthType.API_TOKEN).fields([
                AuthField(
                    name="clientId",
                    display_name="Application (Client) ID",
                    placeholder="Enter your Box Developer Console Application (Client) ID",
                    description="The Application (Client) ID from Box Developer Console"
                ),
                AuthField(
                    name="clientSecret",
                    display_name="Client Secret",
                    placeholder="Enter your Box Developer Console Client Secret",
                    description="The Client Secret from Box Developer Console",
                    field_type="PASSWORD",
                    is_secret=True
                ),
                AuthField(
                    name="enterpriseId",
                    display_name="Box Enterprise ID",
                    placeholder="Enter your Box Enterprise ID",
                    description="The Enterprise ID from Box Developer Console"
                )
        ])
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/box.svg")
        .with_realtime_support(True)
        .add_documentation_link(DocumentationLink(
            "Box App Setup",
            "https://developer.box.com/guides/authentication/",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/box',
            'pipeshub'
        ))
        .add_filter_field(CommonFields.modified_date_filter("Filter files and folders by modification date."))
        .add_filter_field(CommonFields.created_date_filter("Filter files and folders by creation date."))
        .add_filter_field(CommonFields.file_extension_filter())
        .add_filter_field(FilterField(
            name="shared",
            display_name="Index Items Shared by Me",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of items you have shared (with a link or collaboration)",
            default_value=True,
        ))
        .add_filter_field(FilterField(
            name="shared_with_me",
            display_name="Index Items Shared With Me",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of items shared with you via collaboration",
            default_value=True,
        ))
        .with_webhook_config(True, ["FILE.UPLOADED", "FILE.DELETED", "FILE.MOVED", "FOLDER.CREATED", "COLLABORATION.CREATED", "COLLABORATION.ACCEPTED", "COLLABORATION.REMOVED"])
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .with_agent_support(False)
        .with_sync_support(True)
        .add_sync_custom_field(CommonFields.batch_size_field())
    )\
    .build_decorator()

class BoxConnector(BaseConnector):
    """
    Connector for synchronizing data from a Box account.
    """

    # Box API constants
    BASE_URL = "https://api.box.com"
    TOKEN_ENDPOINT = "/oauth2/token"
    HTTP_OK = 200
    HTTP_NOT_FOUND = 404
    current_user_id: Optional[str] = None

    # Box only retains admin_logs_streaming events for 2 weeks. If last sync was longer ago, do full sync.
    BOX_EVENT_STREAM_RETENTION_DAYS = 14

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
    ) -> None:

        super().__init__(
            BoxApp(connector_id=connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id=connector_id
        )

        self.connector_name = Connectors.BOX
        self.connector_id = connector_id

        # Initialize sync point for tracking record changes
        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_id=self.connector_id,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider
            )

        # Initialize sync points
        self.box_cursor_sync_point = _create_sync_point(SyncDataPointType.RECORDS)
        self.user_sync_point = _create_sync_point(SyncDataPointType.USERS)
        self.user_group_sync_point = _create_sync_point(SyncDataPointType.GROUPS)

        self.data_source: Optional[BoxDataSource] = None
        self.batch_size = 100
        self.max_concurrent_batches = 5
        self.rate_limiter = AsyncLimiter(50, 1)  # 50 requests per second
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

    async def init(self) -> bool:
        """Initializes the Box client using CCG authentication."""
        config = await self.config_service.get_config(
            f"/services/connectors/{self.connector_id}/config"
        )
        if not config:
            self.logger.error("Box configuration not found.")
            return False

        auth_config = config.get("auth")
        if not auth_config:
            self.logger.error("Box auth configuration not found.")
            return False

        client_id = auth_config.get("clientId")
        client_secret = auth_config.get("clientSecret")
        enterprise_id = auth_config.get("enterpriseId")

        if not client_id or not client_secret or not enterprise_id:
            self.logger.error("Box client_id, client_secret, or enterprise_id not found in configuration.")
            return False

        try:
            # Use CCG authentication - SDK handles token refresh automatically
            config_obj = BoxCCGConfig(
                client_id=client_id,
                client_secret=client_secret,
                enterprise_id=enterprise_id
            )
            client = await BoxClient.build_with_config(config_obj)
            await client.get_client().create_client()
            self.data_source = BoxDataSource(client)

            self.logger.info(f"Box CCG client initialized successfully for enterprise {enterprise_id}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize Box CCG client: {e}", exc_info=True)
            return False

    def _parse_box_timestamp(self, ts_str: Optional[str], field_name: str, entry_name: str) -> int:
        """Helper to parse Box timestamps safely."""
        if ts_str:
            try:
                # Handle Box's ISO format
                return int(datetime.fromisoformat(ts_str.replace('Z', '+00:00')).timestamp() * 1000)
            except Exception as e:
                self.logger.debug(f"Could not parse {field_name} for {entry_name}: {e}")

        # Fallback to current time
        return int(datetime.now(timezone.utc).timestamp() * 1000)

    def _to_dict(self, obj: Optional[object]) -> Dict[str, Optional[object]]:
        """
        Safely converts Box SDK objects or mixed responses to dictionary.
        Returns Dict[str, Optional[object]] to satisfy strict linter (ANN401).
        """
        if obj is None:
            return {}

        if isinstance(obj, dict):
            return obj

        if hasattr(obj, 'to_dict') and callable(getattr(obj, 'to_dict')):
            return obj.to_dict()

        if hasattr(obj, 'response_object'):
            val = getattr(obj, 'response_object')
            if isinstance(val, dict):
                return val

        return {}

    async def _process_box_entry(
        self,
        entry: Dict,
        user_id: str,
        user_email: str,
        record_group_id: str,
    ) -> Optional[RecordUpdate]:
        """
        Process a single Box entry and detect changes.
        """
        try:
            entry_type = entry.get('type')
            entry_id = entry.get('id')
            entry_name = entry.get('name')

            if not entry_id or not entry_name:
                self.logger.warning(f"Skipping entry without ID or name: {entry}")
                return None

            # Apply file extension filter for files
            if entry_type == 'file' and not self._should_include_file(entry):
                self.logger.debug(f"File {entry_name} filtered out by extension filter")
                return None

            path_collection = entry.get('path_collection', {}).get('entries', [])
            path_parts = [p.get('name') for p in path_collection if p.get('name')]
            path_parts.append(entry_name)
            file_path = '/' + '/'.join(path_parts)

            parent_external_record_id = None
            if path_collection:
                parent_folder = path_collection[-1]
                parent_id = parent_folder.get('id')
                if parent_id and parent_id != '0':
                    parent_external_record_id = parent_id

            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    external_id=entry_id,
                    connector_id=self.connector_id
                )

            is_file = entry_type == 'file'
            record_type = RecordType.FILE

            source_created_at = self._parse_box_timestamp(entry.get('created_at'), 'created_at', entry_name)
            source_updated_at = self._parse_box_timestamp(entry.get('modified_at'), 'modified_at', entry_name)
            current_timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)

            mime_type_enum = get_mimetype_enum_for_box(entry_type, entry_name)
            mime_type = mime_type_enum.value

            record_id = existing_record.id if existing_record else str(uuid.uuid4())
            version = (existing_record.version + 1) if existing_record else 1

            file_size = 0
            if is_file:
                raw_size = entry.get('size')
                if raw_size is not None:
                    file_size = int(raw_size)
                else:
                    self.logger.warning(f"Size field missing for file {entry_name}")

            web_url = f"https://app.box.com/{entry_type}/{entry_id}"

            # Shared by me: item has a shared link or was explicitly shared
            is_shared = bool(entry.get('shared_link'))
            # Shared with me: item is owned by someone else (e.g. from collaboration list)
            owned_by = entry.get('owned_by') or {}
            owner_id = str(owned_by.get('id', '')) if owned_by else ''
            is_shared_with_me = bool(owner_id and owner_id != user_id)

            file_record = FileRecord(
                id=record_id,
                org_id=self.data_entities_processor.org_id,
                record_name=entry_name,
                record_type=record_type,
                record_group_type=RecordGroupType.DRIVE,
                external_record_id=entry_id,
                external_record_group_id=record_group_id,
                parent_external_record_id=parent_external_record_id,
                parent_record_type=RecordType.FILE if parent_external_record_id else None,
                version=version,
                origin=OriginTypes.CONNECTOR,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                mime_type=mime_type,
                weburl=web_url,
                created_at=current_timestamp,
                updated_at=current_timestamp,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
                is_file=is_file,
                preview_renderable=is_file,
                size_in_bytes=file_size,
                extension=get_file_extension(entry_name) if is_file else None,
                path=file_path,
                etag=entry.get('etag'),
                sha1_hash=entry.get('sha1'),
                external_revision_id=entry.get('etag'),
                is_shared=is_shared,
                is_shared_with_me=is_shared_with_me,
            )

            # 1. Fetch explicit API permissions (Collaborators only)
            api_permissions = await self._get_permissions(entry_id, entry_type)
            final_permissions_map = {p.external_id: p for p in api_permissions}

            # 2. Inject Shared Link Permissions (Organization/Public)
            # This handles files that are "Shared with Company" but users aren't invited explicitly
            shared_link = entry.get('shared_link')
            if shared_link:
                access_level = shared_link.get('access')
                if access_level == 'company':
                    # Use Org ID to represent the whole company
                    org_perm_id = f"ORG_{self.data_entities_processor.org_id}"
                    final_permissions_map[org_perm_id] = Permission(
                        external_id=org_perm_id,
                        email="organization_wide_access",
                        type=PermissionType.READ,
                        entity_type=EntityType.GROUP
                    )
                elif access_level == 'open':
                    public_perm_id = "PUBLIC"
                    final_permissions_map[public_perm_id] = Permission(
                        external_id=public_perm_id,
                        email="public_access",
                        type=PermissionType.READ,
                        entity_type=EntityType.GROUP
                    )

            permissions = list(final_permissions_map.values())

            # Respect indexing filters for shared / shared_with_me (same as Drive)
            if self.indexing_filters:
                shared_disabled = (
                    file_record.is_shared
                    and not file_record.is_shared_with_me
                    and not self.indexing_filters.is_enabled(IndexingFilterKey.SHARED, default=True)
                )
                shared_with_me_disabled = (
                    file_record.is_shared_with_me
                    and not self.indexing_filters.is_enabled(IndexingFilterKey.SHARED_WITH_ME, default=True)
                )
                if shared_disabled or shared_with_me_disabled:
                    file_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

            # Link shared-with-me records to the user's "Shared with Me" record group (for future collaboration sync)
            if file_record.is_shared_with_me and user_email:
                file_record.external_record_group_id = None
                async with self.data_store_provider.transaction() as tx_store:
                    shared_with_me_group = await tx_store.get_record_group_by_external_id(
                        connector_id=self.connector_id,
                        external_id=f"0S:{user_email}",
                    )
                    if shared_with_me_group:
                        await tx_store.create_record_group_relation(
                            file_record.id,
                            shared_with_me_group.id,
                        )

            # Determine if new or updated
            if existing_record:
                is_content_modified = existing_record.source_updated_at != source_updated_at
                # Always return update if exists to ensure permissions sync
                return RecordUpdate(
                    record=file_record,
                    is_new=False,
                    is_updated=True,
                    is_deleted=False,
                    metadata_changed=is_content_modified,
                    content_changed=is_content_modified,
                    permissions_changed=True,
                    new_permissions=permissions,
                    external_record_id=entry_id
                )
            else:
                return RecordUpdate(
                    record=file_record,
                    is_new=True,
                    is_updated=False,
                    is_deleted=False,
                    metadata_changed=False,
                    content_changed=False,
                    permissions_changed=False,
                    new_permissions=permissions,
                    external_record_id=entry_id
                )

        except Exception as e:
            self.logger.error(f"Error processing Box entry {entry.get('id')}: {e}", exc_info=True)
            return None

    async def _get_permissions(self, item_id: str, item_type: str) -> List[Permission]:
        """
        Fetch permissions for a Box item (file or folder).
        """
        permissions = []
        try:
            # Get collaborations for the item
            if item_type == 'file':
                response = await self.data_source.collaborations_get_file_collaborations(file_id=item_id)
            else:
                response = await self.data_source.collaborations_get_folder_collaborations(folder_id=item_id)

            if not response.success:
                # 404 or no permission to view collabs (BoxResponse has no status_code; check error string)
                if response.error and "404" in str(response.error):
                    self.logger.debug(f"No collaborations found or accessible for {item_type} {item_id} (404).")
                else:
                    self.logger.debug(f"Could not fetch permissions for {item_type} {item_id}: {response.error}")
                return permissions

            data = self._to_dict(response.data)
            collaborations = data.get('entries', [])

            for collab in collaborations:
                accessible_by = collab.get('accessible_by', {})
                role = collab.get('role', 'viewer')

                # Map Box roles to our permission types
                permission_type = PermissionType.READ
                if role in ['editor', 'co-owner']:
                    permission_type = PermissionType.WRITE
                elif role == 'owner':
                    permission_type = PermissionType.OWNER

                # Determine entity type
                entity_type = EntityType.USER
                accessible_by_type = accessible_by.get('type')
                if accessible_by_type == 'group':
                    entity_type = EntityType.GROUP

                # Skip if no ID (invalid collaboration)
                accessible_by_id = accessible_by.get('id')
                if not accessible_by_id:
                    continue

                permissions.append(Permission(
                    external_id=accessible_by_id,
                    email=accessible_by.get('login'),
                    type=permission_type,
                    entity_type=entity_type
                ))

        except Exception as e:
            self.logger.debug(f"Error fetching permissions for {item_type} {item_id}: {e}")

        return permissions


    async def _process_box_items_generator(
        self,
        entries: List[Dict],
        user_id: str,
        user_email: str,
        record_group_id: str,
    ) -> AsyncGenerator[Tuple[Optional[FileRecord], List[Permission], RecordUpdate], None]:
        """
        Process Box items and yield FileRecord, permissions, and RecordUpdate.
        """
        for entry in entries:
            record_update = await self._process_box_entry(
                entry=entry,
                user_id=user_id,
                user_email=user_email,
                record_group_id=record_group_id,
            )

            if record_update:
                if record_update.is_deleted:
                    yield None, [], record_update
                elif record_update.is_updated:
                    yield record_update.record, record_update.new_permissions or [], record_update
                elif record_update.is_new:
                    yield record_update.record, record_update.new_permissions or [], record_update

    async def _handle_record_updates(self, record_update: RecordUpdate) -> None:
        """Handle record updates (modified or deleted records)."""
        try:
            if record_update.is_deleted:
                async with self.data_store_provider.transaction() as tx_store:
                    existing_record = await tx_store.get_record_by_external_id(
                        external_id=record_update.external_record_id,
                        connector_id=self.connector_id
                    )
                    if existing_record:
                        await self.data_entities_processor.on_record_deleted(
                            record_id=existing_record.id
                        )

            elif record_update.is_updated:
                # Update the record
                await self.data_entities_processor.on_new_records([
                    (record_update.record, record_update.new_permissions or [])
                ])

        except Exception as e:
            self.logger.error(f"Error handling record update: {e}", exc_info=True)

    async def _sync_users(self) -> List[AppUser]:
        """
        Sync Box users and return list of AppUser objects.
        """
        try:
            self.logger.info("Syncing Box users...")

            app_users = []
            offset = 0
            limit = 1000

            while True:
                response = await self.data_source.users_get_users(limit=limit, offset=offset)

                if not response.success:
                    self.logger.error(f"Failed to fetch users: {response.error}")
                    break

                data = self._to_dict(response.data)
                users_data = data.get('entries', [])

                if not users_data:
                    break

                for user in users_data:
                    app_user = AppUser(
                        app_name=self.connector_name,
                        connector_id=self.connector_id,
                        source_user_id=user.get('id'),
                        org_id=self.data_entities_processor.org_id,
                        email=user.get('login', ''),
                        full_name=user.get('name', ''),
                        is_active=user.get('status') == 'active',
                        title=user.get('job_title')
                    )
                    app_users.append(app_user)

                offset += limit

                # Check if there are more users
                if len(users_data) < limit:
                    break

            self.logger.info(f"Synced {len(app_users)} Box users")
            return app_users

        except Exception as e:
            self.logger.error(f"Error syncing Box users: {e}", exc_info=True)
            return []

    async def _get_app_users_by_emails(self, emails: List[str]) -> List[AppUser]:
        """
        Get AppUser objects by their email addresses.
        Uses singular fetches since batch fetch is unavailable on tx_store.
        """
        if not emails:
            return []

        found_users = []
        try:
            async with self.data_store_provider.transaction() as tx_store:
                for email in emails:
                    user = await tx_store.get_app_user_by_email(
                        connector_id=self.connector_id,
                        email=email
                    )
                    if user:
                        found_users.append(user)

            if len(found_users) < len(emails):
                missing_count = len(emails) - len(found_users)
                self.logger.debug(f"⚠️ {missing_count} user(s) not found in database for provided emails")

            return found_users

        except Exception as e:
            self.logger.error(f"❌ Failed to get users by emails: {e}", exc_info=True)
            return []

    async def _remove_user_access_from_folder_recursively(
        self,
        folder_external_id: str,
        user_id: str
    ) -> None:
        """
        Remove user access from a folder and all its descendant files and folders.
        This ensures that when folder collaboration is revoked, all items inside
        also have the user's permissions removed.
        Args:
            folder_external_id: External ID of the folder
            user_id: Internal user ID whose access should be removed
        """
        try:
            self.logger.info(f"📁 Removing user {user_id} access from folder {folder_external_id} and descendants")

            # Track all items to remove access from
            items_to_process = deque([folder_external_id])
            processed_items = set()

            async with self.data_store_provider.transaction() as tx_store:
                # Process items recursively (BFS approach)
                while items_to_process:
                    current_external_id = items_to_process.popleft()

                    if current_external_id in processed_items:
                        continue

                    processed_items.add(current_external_id)

                    # Remove user access from this item
                    try:
                        await tx_store.remove_user_access_to_record(
                            connector_id=self.connector_id,
                            external_id=current_external_id,
                            user_id=user_id
                        )
                    except Exception as e:
                        self.logger.warning(f"⚠️ Failed to remove access from {current_external_id}: {e}")

                    # Get children of this item
                    try:
                        children = await tx_store.get_records_by_parent(
                            connector_id=self.connector_id,
                            parent_external_record_id=current_external_id
                        )

                        if children:
                            # Add children to the processing queue
                            for child in children:
                                if child.external_record_id and child.external_record_id not in processed_items:
                                    items_to_process.append(child.external_record_id)
                    except Exception as e:
                        self.logger.debug(f"No children found for {current_external_id} or error: {e}")

                self.logger.info(f"✅ Removed user access from folder and {len(processed_items) - 1} descendants")

        except Exception as e:
            self.logger.error(f"❌ Failed to remove folder access recursively: {e}", exc_info=True)

    async def _sync_user_groups(self) -> None:
        """
        Sync Box groups and their memberships.
        Includes Reconciliation: Deletes groups from DB that no longer exist in Box.
        """
        try:
            self.logger.info("Syncing Box groups...")

            all_users = await self.data_entities_processor.get_all_app_users(
                connector_id=self.connector_id
            )
            user_map = {u.email.lower(): u for u in all_users if u.email}

            self.logger.info(f"Pre-fetched {len(user_map)} users for group sync lookup.")

            # Track all IDs found in Box
            found_box_group_ids = set()

            # Add Virtual Group IDs to this set so we don't accidentally delete them
            found_box_group_ids.add("PUBLIC")
            found_box_group_ids.add(f"ORG_{self.data_entities_processor.org_id}")

            offset = 0
            limit = 1000

            while True:
                response = await self.data_source.groups_get_groups(limit=limit, offset=offset)

                if not response.success:
                    self.logger.error(f"Failed to fetch groups: {response.error}")
                    break

                data = self._to_dict(response.data)
                groups_data = data.get('entries', [])

                if not groups_data:
                    break

                for group in groups_data:
                    group_id = group.get('id')

                    if group_id:
                        found_box_group_ids.add(group_id)

                    group_name = group.get('name', '')

                    app_user_group = AppUserGroup(
                        app_name=self.connector_name,
                        connector_id=self.connector_id,
                        source_user_group_id=group_id,
                        name=group_name,
                        org_id=self.data_entities_processor.org_id,
                        description=group.get('description')
                    )

                    # Get group members
                    members_response = await self.data_source.groups_get_group_memberships(
                        group_id=group_id,
                        limit=1000
                    )

                    group_member_users = []

                    if members_response.success:
                        members_data = self._to_dict(members_response.data)
                        memberships = members_data.get('entries', [])
                        for membership in memberships:
                            user_info = membership.get('user', {})
                            email = user_info.get('login')

                            if email:
                                # Lookup user in our pre-fetched map
                                found_user = user_map.get(email.lower())
                                if found_user:
                                    group_member_users.append(found_user)

                    # Sync group and memberships using the in-memory list
                    await self.data_entities_processor.on_new_user_groups([(app_user_group, group_member_users)])

                offset += limit

                if len(groups_data) < limit:
                    break

            # Delete Stale Groups
            await self._reconcile_deleted_groups(found_box_group_ids)

            self.logger.info("Box groups sync completed")

        except Exception as e:
            self.logger.error(f"Error syncing Box groups: {e}", exc_info=True)

    async def _reconcile_deleted_groups(self, active_box_ids: set) -> None:
        """
        Compares Box IDs against DB IDs and deletes stale groups.
        """
        try:
            # 1. Get all groups currently in the DB for this connector using Transaction Store
            async with self.data_store_provider.transaction() as tx_store:
                db_groups = await tx_store.get_user_groups(
                    connector_id=self.connector_id,
                    org_id=self.data_entities_processor.org_id
                )

            # 2. Identify groups in DB that are NOT in the active_box_ids set
            stale_groups = [
                g for g in db_groups
                if g.source_user_group_id not in active_box_ids
            ]

            if not stale_groups:
                self.logger.info("No stale groups found.")
                return

            self.logger.info(f"🧹 Found {len(stale_groups)} stale groups to delete.")

            # 3. Delete
            for group in stale_groups:
                external_id = group.source_user_group_id
                self.logger.info(f"Deleting stale group: {group.name} ({external_id})")

                # Use existing delete handler
                await self.data_entities_processor.on_user_group_deleted(
                    external_group_id=external_id,
                    connector_id=self.connector_id
                )

        except Exception as e:
            self.logger.error(f"Error during group reconciliation: {e}", exc_info=True)

    async def _sync_record_groups(self, users: List[AppUser]) -> None:
        """
        Sync Box drives as RecordGroup entities.
        In Box, each user has a root "All Files" folder (folder_id='0') which acts as their drive.
        RecordGroup represents this drive, while individual folders and files are FileRecords.
        """
        try:
            self.logger.info("Syncing Box record groups (user drives)...")

            for user in users:
                try:
                    # Folder '0' is the user's root "All Files"; must set As-User to get that user's root.
                    await self.data_source.set_as_user_context(user.source_user_id)
                except Exception as e:
                    self.logger.warning(f"Could not set As-User for {user.email}: {e}")
                    continue

                try:
                    response = await self.data_source.folders_get_folder_by_id(folder_id='0')
                finally:
                    await self.data_source.clear_as_user_context()

                if not response.success:
                    self.logger.warning(f"Could not fetch root folder for user {user.email}: {response.error}")
                    continue

                root_folder = self._to_dict(response.data)

                # Create RecordGroup for user's drive (their "All Files" root storage)
                record_group = RecordGroup(
                    name=f"{user.full_name or user.email}'s Box",
                    org_id=self.data_entities_processor.org_id,
                    external_group_id=user.source_user_id,
                    connector_name=self.connector_name,
                    connector_id=self.connector_id,
                    group_type=RecordGroupType.DRIVE,
                    web_url=root_folder.get('shared_link', {}).get('url') if root_folder.get('shared_link') else None,
                    source_created_at=int(datetime.fromisoformat(root_folder.get('created_at', '').replace('Z', '+00:00')).timestamp() * 1000) if root_folder.get('created_at') else None,
                    source_updated_at=int(datetime.fromisoformat(root_folder.get('modified_at', '').replace('Z', '+00:00')).timestamp() * 1000) if root_folder.get('modified_at') else None,
                )

                # Shared with Me: virtual group for items shared with this user (e.g. via collaborations)
                shared_with_me_record_group = RecordGroup(
                    name=f"{user.full_name or user.email}'s Shared with Me",
                    org_id=self.data_entities_processor.org_id,
                    external_group_id=f"0S:{user.email}",
                    connector_name=self.connector_name,
                    connector_id=self.connector_id,
                    group_type=RecordGroupType.DRIVE,
                    is_internal=True,
                )

                # User has owner permission on their own drive and on Shared with Me
                drive_permissions = [Permission(
                    external_id=user.source_user_id,
                    email=user.email,
                    type=PermissionType.OWNER,
                    entity_type=EntityType.USER
                )]

                await self.data_entities_processor.on_new_record_groups([
                    (record_group, drive_permissions),
                    (shared_with_me_record_group, drive_permissions),
                ])

            self.logger.info("Box record groups sync completed")

        except Exception as e:
            self.logger.error(f"Error syncing Box record groups: {e}", exc_info=True)

    async def _run_sync_for_user(self, user: AppUser) -> None:
        """
        Synchronize Box files for a given user starting from Root.
        """
        try:
            self.logger.info(f"Starting Box sync for user {user.email}")

            # Initialize a shared batch list to hold records across recursion
            batch_records = []

            # Start recursion from the Root Folder ('0')
            # Root folder ID is usually '0' in Box
            await self._sync_folder_recursively(user, folder_id='0', batch_records=batch_records)

            # Flush any remaining records in the batch after recursion finishes
            if batch_records:
                self.logger.info(f"Processing final batch of {len(batch_records)} records")
                await self.data_entities_processor.on_new_records(batch_records)

            self.logger.info(f"Completed sync for user {user.email}")

        except Exception as e:
            self.logger.error(f"Error syncing for user {user.email}: {e}", exc_info=True)

    async def _sync_folder_recursively(self, user: AppUser, folder_id: str, batch_records: List) -> None:
        """
        Recursively fetch all items in a folder with pagination.
        """
        offset = 0
        limit = 1000

        fields = 'type,id,name,size,created_at,modified_at,path_collection,etag,sha1,shared_link,owned_by'

        if not self.current_user_id:
            try:
                current_user_response = await self.data_source.get_current_user()
                if current_user_response.success and current_user_response.data:
                    user_data = self._to_dict(current_user_response.data)
                    self.current_user_id = user_data.get("id")
                    if self.current_user_id is not None:
                        self.current_user_id = str(self.current_user_id)
                    if self.current_user_id:
                        self.logger.info(f"🔍 Current Token Owner ID: {self.current_user_id}")
            except Exception as e:
                self.logger.warning(f"Could not fetch current user ID: {e}")

        # Set As-User context if syncing for a different user
        try:
            if self.current_user_id and user.source_user_id != self.current_user_id:
                self.logger.info(f"🎭 Setting As-User context to: {user.source_user_id} ({user.email})")
                await self.data_source.set_as_user_context(user.source_user_id)
            else:
                # Clear any existing As-User context
                await self.data_source.clear_as_user_context()
        except Exception as e:
            self.logger.error(f"Failed to set As-User context: {e}")
            # Continue without impersonation
            pass

        while True:

            async with self.rate_limiter:
                response = await self.data_source.folders_get_folder_items(
                    folder_id=folder_id,
                    limit=limit,
                    offset=offset,
                    fields=fields,
                )

            if not response.success:
                self.logger.error(f"Failed to fetch items for folder {folder_id}: {response.error}")
                break

            data = self._to_dict(response.data)
            items = data.get('entries', [])
            total_count = data.get('total_count', 0)

            if not items:
                break

            sub_folders_to_traverse = []
            current_record_group_id = user.source_user_id

            async for file_record, permissions, record_update in self._process_box_items_generator(
                items,
                user.source_user_id,
                user.email,
                current_record_group_id,
            ):
                if record_update.is_deleted or record_update.is_updated:
                    await self._handle_record_updates(record_update)
                    continue

                if file_record:
                    batch_records.append((file_record, permissions))

                    if len(batch_records) >= self.batch_size:
                        self.logger.info(f"Processing batch of {len(batch_records)} records")
                        await self.data_entities_processor.on_new_records(batch_records)
                        batch_records.clear()
                        await asyncio.sleep(0.1)

                # Check if it's a folder (not a file)
                if file_record and file_record.mime_type == MimeTypes.FOLDER.value:
                    sub_folders_to_traverse.append(file_record.external_record_id)

            for sub_folder_id in sub_folders_to_traverse:
                await self._sync_folder_recursively(user, sub_folder_id, batch_records)

            offset += len(items)
            if offset >= total_count:
                break
        try:
            await self.data_source.clear_as_user_context()
        except Exception as e:
            self.logger.warning(f"Failed to clear As-User context at the end of recursive sync: {e}")

    async def _process_users_in_batches(self, users: List[AppUser]) -> None:
        """
        Process users SEQUENTIALLY to prevent 'As-User' context collisions.
        """
        try:
            # Filter for active users only
            all_active_users = await self.data_entities_processor.get_all_active_users()
            active_user_emails = {active_user.email.lower() for active_user in all_active_users}
            users_to_sync = [
                user for user in users
                if user.email and user.email.lower() in active_user_emails
            ]

            self.logger.info(f"Processing {len(users_to_sync)} active users SEQUENTIALLY")

            # Loop directly, awaiting each user fully before starting the next
            for i, user in enumerate(users_to_sync):
                self.logger.info(f"[{i+1}/{len(users_to_sync)}] Syncing user: {user.email}")
                try:
                    await self._run_sync_for_user(user)
                except Exception as e:
                    self.logger.error(f"Error syncing user {user.email}: {e}")
                    # Continue to next user even if one fails
                    continue

            self.logger.info("Completed processing all user batches")

        except Exception as e:
            self.logger.error(f"Error processing users in batches: {e}")
            raise

    async def _ensure_virtual_groups(self) -> None:
        """
        Creates 'stub' groups for Public and Organization-wide access.
        This fixes the "No user group found" warnings.
        """
        try:
            virtual_groups = []

            # 1. Public Group (For 'open' shared links)
            virtual_groups.append(AppUserGroup(
                app_name=self.connector_name,
                connector_id=self.connector_id,
                source_user_group_id="PUBLIC",
                name="Public (External)",
                org_id=self.data_entities_processor.org_id,
                description="Virtual group for content shared publicly via link"
            ))

            # 2. Organization Group (For 'company' shared links)
            org_group_id = f"ORG_{self.data_entities_processor.org_id}"
            virtual_groups.append(AppUserGroup(
                app_name=self.connector_name,
                connector_id=self.connector_id,
                source_user_group_id=org_group_id,
                name="Entire Organization",
                org_id=self.data_entities_processor.org_id,
                description="Virtual group for content shared with the entire company"
            ))

            # Upsert them (Empty member list [] because they are virtual)
            await self.data_entities_processor.on_new_user_groups(
                [(g, []) for g in virtual_groups]
            )

        except Exception as e:
            self.logger.error(f"Failed to create virtual groups: {e}")

    async def run_sync(self) -> None:
        """
        Smart Sync: Decides between Full vs. Incremental based on cursor state.
        """
        try:
            self.logger.info("🔍 [Smart Sync] Checking sync state...")

            # Load filters
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "box", self.connector_id, self.logger
            )

            # Cache date filters once at sync start for performance
            self._cached_date_filters = self._get_date_filters()

            # 1. Check if we have an existing cursor
            key = "event_stream_cursor"

            cursor_data = None
            try:
                cursor_data = await self.box_cursor_sync_point.read_sync_point(key)
            except Exception as e:
                self.logger.debug(f"⚠️ [Smart Sync] Could not read sync point (first run?): {e}")

            # 2. DECISION LOGIC
            if cursor_data and cursor_data.get("cursor"):
                cursor_val = cursor_data.get("cursor")
                cursor_updated_at_ms = cursor_data.get("cursor_updated_at")
                now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                retention_ms = self.BOX_EVENT_STREAM_RETENTION_DAYS * 24 * 3600 * 1000

                if cursor_updated_at_ms is not None and (now_ms - cursor_updated_at_ms) < retention_ms:
                    self.logger.info(f"✅ [Smart Sync] Found existing cursor: {cursor_val} (within retention window)")
                    self.logger.info("🚀 [Smart Sync] Switching to INCREMENTAL SYNC path.")
                    await self.run_incremental_sync()
                    return
                else:
                    self.logger.info(
                        f"⚪ [Smart Sync] Cursor too old or missing timestamp (> {self.BOX_EVENT_STREAM_RETENTION_DAYS} days). "
                        "Box retains events only 2 weeks. Starting FULL SYNC."
                    )

            # NO CURSOR OR CURSOR TOO OLD: PROCEED WITH FULL SYNC
            if not (cursor_data and cursor_data.get("cursor")):
                self.logger.info("⚪ [Smart Sync] No cursor found. Starting FULL SYNC & Anchoring.")

            # ANCHOR THE STREAM
            try:
                # Get current position ('now')
                response = await self.data_source.events_get_events(
                    stream_type='admin_logs_streaming',
                    stream_position='now',
                    limit=1
                )

                if response.success:
                    data = self._to_dict(response.data)
                    next_stream_pos = data.get('next_stream_position')

                    if next_stream_pos:
                        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                        await self.box_cursor_sync_point.update_sync_point(
                            key,
                            {"cursor": next_stream_pos, "cursor_updated_at": now_ms}
                        )
                        self.logger.info(f"⚓ [Smart Sync] Anchored Event Stream at: {next_stream_pos}")
                    else:
                        self.logger.warning("⚠️ [Smart Sync] Anchoring Warning: 'next_stream_position' not found.")
            except Exception as e:
                self.logger.warning(f"❌ [Smart Sync] Failed to anchor event stream: {e}", exc_info=True)

            # SYNC RESOURCES (Full Scan)
            self.logger.info("📦 [Full Sync] Syncing users...")
            users = await self._sync_users()
            await self.data_entities_processor.on_new_app_users(users)

            self.logger.info("📦 [Full Sync] Creating virtual groups...")
            await self._ensure_virtual_groups()

            self.logger.info("📦 [Full Sync] Syncing user groups...")
            await self._sync_user_groups()

            self.logger.info("📦 [Full Sync] Syncing user drives...")
            await self._sync_record_groups(users)

            self.logger.info("📦 [Full Sync] Syncing user files and folders...")
            await self._process_users_in_batches(users)

            self.logger.info("✅ [Full Sync] Completed successfully.")

        except Exception as ex:
            self.logger.error(f"❌ [Run Sync] Error in Box connector run: {ex}", exc_info=True)
            raise

    async def run_incremental_sync(self) -> None:
        """
        Runs an incremental sync using the Box Enterprise Event Stream.
        """
        self.logger.info("🔄 [Incremental] Starting Box Enterprise incremental sync.")

        our_org_box_user_ids: Set[str] = set()
        try:
            self.logger.info("👥 [Incremental] Refreshing User list...")
            users = await self._sync_users()

            # Update the in-memory or DB map of users so we can link files to them later
            await self.data_entities_processor.on_new_app_users(users)

            # Box user IDs that belong to our org (for distinguishing external vs internal shares)
            our_org_box_user_ids = {
                str(u.source_user_id) for u in (users or [])
                if getattr(u, 'source_user_id', None)
            }

            self.logger.info("🛡️ [Incremental] Refreshing Virtual Groups...")
            await self._ensure_virtual_groups()

            self.logger.info("👥 [Incremental] Refreshing User Groups...")
            await self._sync_user_groups()

        except Exception as e:
            # If this fails, log it, but maybe still try to process file events?
            self.logger.error(f"⚠️ [Incremental] Failed to refresh users/groups: {e}")

        key = "event_stream_cursor"

        # 1. Load Cursor (Box guarantees events after cursor are new; duplicates only within that stream)
        stream_position = 'now'
        try:
            data = await self.box_cursor_sync_point.read_sync_point(key)
            if data and isinstance(data, dict):
                stream_position = data.get("cursor") or 'now'
            self.logger.info(f"📍 [Incremental] Loaded Cursor: {stream_position}")
        except Exception:
            self.logger.info("⚠️ [Incremental] No existing cursor found, starting from 'now'")

        limit = 500
        has_more = True

        try:
            while has_more:
                self.logger.info(f"📡 [Incremental] Polling Box events from pos: {stream_position}")

                response = await self.data_source.events_get_events(
                    stream_position=stream_position,
                    stream_type='admin_logs_streaming',
                    limit=limit
                )

                if not response.success:
                    self.logger.error(f"❌ [Incremental] Failed to fetch events: {response.error}")
                    if response.error and "stream_position" in str(response.error):
                         self.logger.warning("⚠️ [Incremental] Stream position expired. Resetting to 'now'.")
                         stream_position = 'now'
                         continue
                    break

                data = self._to_dict(response.data)
                events = data.get('entries', [])
                next_stream_position = data.get('next_stream_position')

                if events:
                    self.logger.info(f"📥 [Incremental] Fetched {len(events)} new events from Box.")
                    await self._process_event_batch(events, our_org_box_user_ids=our_org_box_user_ids)
                else:
                    self.logger.info("ℹ️ [Incremental] Box says: No new events yet.")
                    has_more = False

                if next_stream_position:
                    stream_position = next_stream_position
                    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                    await self.box_cursor_sync_point.update_sync_point(
                        key,
                        {"cursor": stream_position, "cursor_updated_at": now_ms}
                    )
                    self.logger.debug(f"💾 [Incremental] Updated cursor to: {stream_position}")

        except Exception as e:
            self.logger.error(f"❌ [Incremental] Error during sync: {e}", exc_info=True)

    async def _process_event_batch(
        self,
        events: List[Dict],
        our_org_box_user_ids: Optional[Set[str]] = None,
    ) -> None:
        """
        Deduplicates events by event_id (Box may return events more than once or out of order),
        handles deletions, and groups updates.
        Supports "Flat" dictionary schemas and fetches missing emails. Handles files and folders.

        Shared-with-me: we only link to Shared with Me when the share is *from outside our org*
        to a user in our org. Shares from our org to external users are ignored for the grantee;
        shares from our org to our org (internal) are not added to Shared with Me.
        """
        items_to_sync: Dict[str, Tuple[str, str]] = {}  # item_id -> (owner_id, item_type)
        items_to_sync_shared_with_me: List[Tuple[str, str, str, str]] = []  # (item_id, item_type, collaborator_box_id, collaborator_email)
        items_to_delete: set = set()

        DELETION_EVENTS = {
            'ITEM_TRASH', 'ITEM_DELETE', 'DELETE', 'TRASH',
            'PERMANENT_DELETE', 'DISCARD'
        }

        REVOCATION_EVENTS = {
            'COLLABORATION_REMOVE',
            'REMOVE_COLLABORATOR',
            'COLLABORATION_DELETED',
            'unshared'
        }

        COLLABORATION_GRANT_EVENTS = {
            'COLLABORATION_CREATED',
            'COLLABORATION_INVITE',
            'COLLABORATION_INVITE_ACCEPTED',
            'COLLABORATION_ACCEPTED',
            'COLLABORATION.CREATED',
            'COLLABORATION.ACCEPTED',
            'COLLABORATION_ADD',
            'ADD_COLLABORATOR',
            'COLLABORATION.INVITE',
        }

        def get_val(obj: Optional[object], key: str, default: Optional[object] = None) -> Optional[object]:
            if obj is None:
                return default
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        # Box may return events out of chronological order; sort by created_at so we process in time order.
        def _event_sort_key(e: Dict) -> Tuple[int, str]:
            ts = get_val(e, 'created_at')
            if ts is not None and isinstance(ts, str):
                return (0, ts)  # ISO 8601 strings sort correctly
            return (1, '')  # events without timestamp last

        events = sorted(events, key=_event_sort_key)
        processed_ids_set = set()

        for event in events:
            event_id = get_val(event, 'event_id')
            if event_id:
                if event_id in processed_ids_set:
                    self.logger.debug(f"Skipping duplicate event (event_id={event_id})")
                    continue
                processed_ids_set.add(event_id)

            event_type = get_val(event, 'event_type')
            source = get_val(event, 'source')

            self.logger.debug(f"🔍 Processing event: type={event_type}, source_type={get_val(source, 'type')}, source_id={get_val(source, 'id')}")

            if event_type and ('COLLABORATION' in event_type.upper() or 'COLLAB' in event_type.upper()):
                self.logger.debug(f"📋 Full collaboration event: {event}")

            # 1. HANDLE COLLABORATION GRANTS (Permissions Added)
            if event_type in COLLABORATION_GRANT_EVENTS:
                item_id = None
                granted_email = None
                granted_user_box_id = None
                item_type = 'file'

                if source:
                    # PATH A: Standard Box Collaboration Object
                    item = get_val(source, 'item')
                    if item:
                        item_id = get_val(item, 'id')
                        item_type = get_val(item, 'type', 'file')

                    accessible_by = get_val(source, 'accessible_by')
                    if accessible_by:
                        granted_email = get_val(accessible_by, 'login')
                        granted_user_box_id = get_val(accessible_by, 'id')

                    # PATH B: Flat Dictionary
                    if not item_id:
                        item_id = get_val(source, 'file_id') or get_val(source, 'folder_id')
                        if get_val(source, 'folder_id'):
                            item_type = 'folder'

                    if not granted_user_box_id:
                        granted_user_box_id = get_val(source, 'user_id')

                    if not item_type or item_type == 'file':
                        source_type = get_val(source, 'type')
                        if source_type == 'folder' or get_val(source, 'folder_id'):
                            item_type = 'folder'

                if not item_id:
                    item_id = get_val(event, 'source_item_id') or get_val(event, 'item_id')

                # Fetch Email from Box if we only have ID
                if granted_user_box_id and not granted_email:
                    try:
                        user_response = await self.data_source.users_get_user_by_id(granted_user_box_id)

                        if user_response.success and user_response.data:
                            user_data = self._to_dict(user_response.data)
                            granted_email = user_data.get('login')
                        else:
                            self.logger.warning(f"⚠️ Failed to fetch user details for ID {granted_user_box_id}: {user_response.error}")
                    except Exception as e:
                        self.logger.error(f"❌ Failed to resolve Box ID {granted_user_box_id}: {e}")

                # EXECUTE GRANT - Queue item for sync to update permissions
                if item_id:
                    self.logger.info(f"✅ Collaboration granted on {item_type} {item_id}" + (f" to {granted_email}" if granted_email else ""))

                    # Get owner information
                    owner = get_val(source, 'owned_by') or get_val(event, 'created_by')
                    owner_id = get_val(owner, 'id') if owner else None

                    # If no owner found, try to fetch the item to get owner info
                    if not owner_id:
                        try:
                            if item_type == 'folder':
                                item_response = await self.data_source.folders_get_folder_by_id(item_id)
                            else:
                                item_response = await self.data_source.files_get_file_by_id(item_id)

                            if item_response.success:
                                item_data = self._to_dict(item_response.data)
                                owned_by = item_data.get('owned_by', {})
                                owner_id = owned_by.get('id')
                        except Exception as e:
                            self.logger.warning(f"⚠️ Failed to fetch owner for {item_type} {item_id}: {e}")

                    if owner_id:
                        # Queue for sync to refresh permissions (owner's drive)
                        items_to_sync[item_id] = (str(owner_id), item_type)
                    else:
                        self.logger.warning(f"⚠️ Cannot sync {item_type} {item_id} - no owner found")

                    # Queue for "Shared with Me" only when: collaborator is in our org AND owner is outside our org.
                    # (Our org → external: we ignore for the grantee. External → our org: link to Shared with Me.)
                    owner_in_our_org = (
                        our_org_box_user_ids is not None
                        and len(our_org_box_user_ids) > 0
                        and str(owner_id) in our_org_box_user_ids
                    )
                    if item_id and granted_email and granted_user_box_id and not owner_in_our_org:
                        collaborators_in_org = await self._get_app_users_by_emails([granted_email])
                        if collaborators_in_org:
                            items_to_sync_shared_with_me.append((
                                str(item_id),
                                item_type or 'file',
                                str(granted_user_box_id),
                                granted_email,
                            ))
                            self.logger.info(
                                f"📥 Queued {item_type} {item_id} for Shared with Me (external→our org) for {granted_email}"
                            )
                        else:
                            self.logger.debug(
                                f"Skip Shared with Me for {item_id}: grantee {granted_email} not in our org"
                            )
                    elif owner_in_our_org and item_id and granted_email:
                        self.logger.debug(
                            f"Skip Shared with Me for {item_id}: owner in our org (internal share or our→external)"
                        )
                else:
                    self.logger.warning("⚠️ Collaboration grant skipped. Missing item ID")

                continue

            # 2. HANDLE REVOCATIONS (Permissions Removed)
            if event_type in REVOCATION_EVENTS:
                file_id = None
                removed_email = None
                removed_user_box_id = None
                collaboration_id = None

                if source:
                    # PATH A: Standard Box Object
                    item = get_val(source, 'item')
                    if item:
                        file_id = get_val(item, 'id')

                    accessible_by = get_val(source, 'accessible_by')
                    if accessible_by:
                        removed_email = get_val(accessible_by, 'login')
                        removed_user_box_id = get_val(accessible_by, 'id')

                    # PATH B: Flat Dictionary (check both file_id and folder_id)
                    if not file_id:
                        file_id = get_val(source, 'file_id') or get_val(source, 'folder_id')

                    if not removed_user_box_id:
                        removed_user_box_id = get_val(source, 'user_id')

                    # PATH C: Get collaboration ID to look up later if needed
                    collaboration_id = get_val(source, 'id')

                if not file_id:
                    file_id = get_val(event, 'source_item_id') or get_val(event, 'item_id')

                # PATH D: If we still don't have file_id but have collaboration_id, try to fetch collaboration details
                if not file_id and collaboration_id:
                    try:
                        self.logger.debug(f"Attempting to fetch collaboration {collaboration_id} to get item details")
                        collab_response = await self.data_source.collaborations_get_collaboration_by_id(collaboration_id)
                        if collab_response.success and collab_response.data:
                            collab_data = self._to_dict(collab_response.data)
                            item = collab_data.get('item', {})
                            if item:
                                file_id = item.get('id')
                                self.logger.debug(f"Found item ID {file_id} from collaboration lookup")
                    except Exception as e:
                        self.logger.debug(f"Could not fetch collaboration details for {collaboration_id}: {e}")

                # Log what we found for debugging
                self.logger.debug(f"Revocation event - file_id={file_id}, email={removed_email}, user_box_id={removed_user_box_id}, collab_id={collaboration_id}")

                # Fetch Email from Box if we only have ID using data_source
                if removed_user_box_id and not removed_email:
                    try:
                        user_response = await self.data_source.users_get_user_by_id(removed_user_box_id)

                        if user_response.success and user_response.data:
                            user_data = self._to_dict(user_response.data)
                            removed_email = user_data.get('login')
                        else:
                            self.logger.warning(f"⚠️ Failed to fetch user details for ID {removed_user_box_id}: {user_response.error}")
                    except Exception as e:
                        self.logger.error(f"❌ Failed to resolve Box ID {removed_user_box_id}: {e}")

                # EXECUTE REMOVAL
                if file_id and removed_email:
                    self.logger.info(f"🚫 Stream detected revocation: {removed_email} from {file_id}")

                    internal_user = None

                    if removed_email:
                        users = await self._get_app_users_by_emails([removed_email])
                        if users:
                            internal_user = users[0]

                    if internal_user:
                        user_id = getattr(internal_user, 'id', None)
                        if user_id:
                            # First, check if this is a folder to handle recursively
                            async with self.data_store_provider.transaction() as tx_store:
                                record = await tx_store.get_record_by_external_id(
                                    external_id=file_id,
                                    connector_id=self.connector_id
                                )

                                if record and record.mime_type == MimeTypes.FOLDER.value:
                                    # Folder - remove access from folder and all descendants
                                    self.logger.info(f"📁 Removing folder access recursively for {file_id}")
                                    await self._remove_user_access_from_folder_recursively(
                                        folder_external_id=file_id,
                                        user_id=user_id
                                    )
                                else:
                                    # File - remove direct access
                                    await tx_store.remove_user_access_to_record(
                                        connector_id=self.connector_id,
                                        external_id=file_id,
                                        user_id=user_id
                                    )
                        else:
                            self.logger.warning("⚠️ User found but has no Internal ID")
                    else:
                        self.logger.warning(f"⚠️ User {removed_email} not found in DB")

                else:
                    self.logger.warning(f"⚠️ Revocation skipped. Missing: FileID={file_id}, Email={removed_email}")

                continue

            # 3. EXTRACT FILE ID (Standard Events)
            file_id = None
            if source:
                file_id = get_val(source, 'id') or get_val(source, 'item_id') or get_val(source, 'file_id')

            if not file_id:
                file_id = get_val(event, 'item_id') or get_val(event, 'source_item_id')

            if not file_id:
                continue

            # 4. HANDLE DELETIONS
            if event_type in DELETION_EVENTS:
                self.logger.info(f"🗑️ Found DELETION event ({event_type}) for Item ID: {file_id}")
                items_to_delete.add(file_id)
                items_to_sync.pop(file_id, None)
                continue

            # 5. FILTER & PREPARE SYNC (Accept both files and folders)
            item_type = 'file'  # Default to file
            if source:
                item_type = get_val(source, 'item_type') or get_val(source, 'type') or 'file'

            if file_id in items_to_delete:
                items_to_delete.remove(file_id)

            owner = get_val(source, 'owned_by') or get_val(event, 'created_by')
            owner_id = get_val(owner, 'id') if owner else None

            if owner_id:
                items_to_sync[file_id] = (owner_id, item_type)

        # 6. EXECUTE BATCHES
        if items_to_delete:
            self.logger.info(f"⚠️ Executing {len(items_to_delete)} deletions...")
            await self._execute_deletions(list(items_to_delete))

        if items_to_sync:
            self.logger.info(f"📋 Queued {len(items_to_sync)} items for sync (files + folders)")
            owner_groups = {}  # owner_id -> {'files': [], 'folders': []}
            for item_id, (owner_id, item_type) in items_to_sync.items():
                if owner_id not in owner_groups:
                    owner_groups[owner_id] = {'files': [], 'folders': []}

                if item_type and item_type.lower() == 'folder':
                    owner_groups[owner_id]['folders'].append(item_id)
                else:
                    owner_groups[owner_id]['files'].append(item_id)

            for owner_id, items in owner_groups.items():
                if items['files']:
                    self.logger.info(f"🔄 Syncing {len(items['files'])} file(s) for owner {owner_id}")
                    await self._fetch_and_sync_files_for_owner(owner_id, items['files'])
                if items['folders']:
                    self.logger.info(f"📁 Syncing {len(items['folders'])} folder(s) for owner {owner_id}")
                    await self._fetch_and_sync_folders_for_owner(owner_id, items['folders'])

        # 7. SYNC ITEMS AS "SHARED WITH ME" FOR COLLABORATORS (from COLLABORATION_* events)
        if items_to_sync_shared_with_me:
            self.logger.info(f"📥 Syncing {len(items_to_sync_shared_with_me)} item(s) as Shared with Me for collaborators")
            await self._fetch_and_sync_items_as_shared_with_me(items_to_sync_shared_with_me)

        if not items_to_sync and not items_to_sync_shared_with_me and not items_to_delete:
            self.logger.debug("ℹ️ No items to sync from this event batch")

    async def _fetch_and_sync_items_as_shared_with_me(
        self,
        items: List[Tuple[str, str, str, str]],  # (item_id, item_type, collaborator_box_id, collaborator_email)
    ) -> None:
        """
        For collaboration-grant events: fetch each item as the collaborator and upsert with
        is_shared_with_me=True so the record is linked to their Shared with Me group.
        """
        if not items:
            return
        # Group by collaborator to minimize as-user context switches
        by_collaborator: Dict[Tuple[str, str], List[Tuple[str, str]]] = {}  # (box_id, email) -> [(item_id, item_type)]
        for item_id, item_type, collab_box_id, collab_email in items:
            key = (collab_box_id, collab_email)
            if key not in by_collaborator:
                by_collaborator[key] = []
            by_collaborator[key].append((item_id, item_type))

        for (collab_box_id, collab_email), collab_items in by_collaborator.items():
            try:
                await self.data_source.set_as_user_context(collab_box_id)
                updates_to_push = []
                for item_id, item_type in collab_items:
                    try:
                        if (item_type or "").lower() == "folder":
                            folder_response = await self.data_source.folders_get_folder_by_id(item_id)
                            if not folder_response.success:
                                self.logger.warning(f"Failed to fetch folder {item_id} as shared-with-me: {folder_response.error}")
                                continue
                            entry = self._to_dict(folder_response.data)
                        else:
                            file_response = await self.data_source.files_get_file_by_id(item_id)
                            if not file_response.success:
                                self.logger.warning(f"Failed to fetch file {item_id} as shared-with-me: {file_response.error}")
                                continue
                            entry = self._to_dict(file_response.data)
                        if not entry:
                            continue
                        update_obj = await self._process_box_entry(
                            entry=entry,
                            user_id=collab_box_id,
                            user_email=collab_email,
                            record_group_id=collab_box_id,
                        )
                        if update_obj:
                            updates_to_push.append((update_obj.record, update_obj.new_permissions or []))
                            if (item_type or "").lower() == "folder":
                                batch_records = []
                                await self._sync_folder_contents_recursively(
                                    owner_id=collab_box_id,
                                    folder_id=item_id,
                                    batch_records=batch_records,
                                    user_email=collab_email,
                                )
                                if batch_records:
                                    await self.data_entities_processor.on_new_records(batch_records)
                    except Exception as e:
                        self.logger.warning(f"Error syncing shared-with-me item {item_id} for {collab_email}: {e}")
                if updates_to_push:
                    await self.data_entities_processor.on_new_records(updates_to_push)
            except Exception as e:
                self.logger.error(f"Error syncing shared-with-me for collaborator {collab_email}: {e}")
            finally:
                await self.data_source.clear_as_user_context()

    async def _fetch_and_sync_files_for_owner(self, owner_id: str, file_ids: List[str]) -> None:
        """
        Impersonates the owner, fetches full file details in parallel, and upserts.
        Ensures parent folders exist before processing files.
        """
        try:
            # 1. Switch Context to the File Owner
            await self.data_source.set_as_user_context(owner_id)

            # 2. Parallel Fetch of File Details
            tasks = [self.data_source.files_get_file_by_id(fid) for fid in file_ids]
            responses = await asyncio.gather(*tasks, return_exceptions=True)

            updates_to_push = []
            parent_folders_to_ensure = set()

            # First pass: collect all parent folders that need to exist
            for res in responses:
                if isinstance(res, Exception) or not getattr(res, 'success', False):
                    continue

                file_entry = self._to_dict(res.data)
                if not file_entry:
                    continue

                # Check if file has a parent folder
                path_collection = file_entry.get('path_collection', {}).get('entries', [])
                if path_collection:
                    parent_folder = path_collection[-1]
                    parent_id = parent_folder.get('id')
                    if parent_id and parent_id != '0':
                        parent_folders_to_ensure.add(parent_id)

            # 3. Ensure parent folders exist in database
            if parent_folders_to_ensure:
                await self._ensure_parent_folders_exist(owner_id, list(parent_folders_to_ensure))

            # 4. Process files (parent folders now exist)
            for res in responses:
                if isinstance(res, Exception) or not getattr(res, 'success', False):
                    continue

                file_entry = self._to_dict(res.data)

                if not file_entry:
                    self.logger.warning("Converted file entry is empty")
                    continue

                # 5. Reuse existing _process_box_entry logic
                update_obj = await self._process_box_entry(
                    entry=file_entry,
                    user_id=owner_id,
                    user_email="incremental_sync_user",
                    record_group_id=owner_id,
                )

                if update_obj:
                    updates_to_push.append((update_obj.record, update_obj.new_permissions))

            # 6. Batch Upsert to Database
            if updates_to_push:
                await self.data_entities_processor.on_new_records(updates_to_push)

        except Exception as e:
            self.logger.error(f"Error syncing files for owner {owner_id}: {e}")
        finally:
            # 7. ALWAYS Clear Context
            await self.data_source.clear_as_user_context()

    async def _ensure_parent_folders_exist(self, owner_id: str, folder_ids: List[str]) -> None:
        """
        Ensures parent folders exist in the database before processing files.
        Recursively creates folder hierarchy if needed.
        """
        async with self.data_store_provider.transaction() as tx_store:
            for folder_id in folder_ids:
                try:
                    # Check if folder already exists in DB
                    existing_folder = await tx_store.get_record_by_external_id(
                        external_id=folder_id,
                        connector_id=self.connector_id
                    )

                    if existing_folder:
                        continue  # Folder already exists, skip

                    # Fetch folder from Box and create it
                    self.logger.info(f"📁 Parent folder {folder_id} not found in DB, fetching and creating...")
                    folder_response = await self.data_source.folders_get_folder_by_id(folder_id)

                    if not folder_response.success:
                        self.logger.warning(f"Failed to fetch parent folder {folder_id}: {folder_response.error}")
                        continue

                    folder_entry = self._to_dict(folder_response.data)
                    if not folder_entry:
                        continue

                    # Check if this folder has parents that need to be created first (recursive)
                    path_collection = folder_entry.get('path_collection', {}).get('entries', [])
                    if path_collection:
                        grandparent_ids = [p.get('id') for p in path_collection if p.get('id') and p.get('id') != '0']
                        if grandparent_ids:
                            # Recursively ensure grandparents exist first
                            await self._ensure_parent_folders_exist(owner_id, grandparent_ids)

                    # Now create the folder record
                    update_obj = await self._process_box_entry(
                        entry=folder_entry,
                        user_id=owner_id,
                        user_email="incremental_sync_user",
                        record_group_id=owner_id,
                    )

                    if update_obj:
                        # Create folder record immediately (not batched)
                        await self.data_entities_processor.on_new_records([(update_obj.record, update_obj.new_permissions)])
                        self.logger.info(f"✅ Created parent folder {folder_id} in database")

                except Exception as e:
                    self.logger.error(f"Error ensuring parent folder {folder_id} exists: {e}", exc_info=True)

    async def _fetch_and_sync_folders_for_owner(self, owner_id: str, folder_ids: List[str]) -> None:
        """
        Impersonates the owner, fetches folder details, syncs folder record,
        and recursively syncs folder contents (like full sync).
        """
        try:
            # 1. Switch Context to the Folder Owner
            await self.data_source.set_as_user_context(owner_id)

            batch_records = []

            for folder_id in folder_ids:
                try:
                    # 2. Fetch folder metadata from Box
                    folder_response = await self.data_source.folders_get_folder_by_id(folder_id)

                    if not folder_response.success:
                        self.logger.warning(f"Failed to fetch folder {folder_id}: {folder_response.error}")
                        continue

                    folder_entry = self._to_dict(folder_response.data)

                    if not folder_entry:
                        self.logger.warning(f"Converted folder entry {folder_id} is empty")
                        continue

                    # 3. Process the folder itself as a record
                    update_obj = await self._process_box_entry(
                        entry=folder_entry,
                        user_id=owner_id,
                        user_email="incremental_sync_user",
                        record_group_id=owner_id,
                    )

                    if update_obj:
                        batch_records.append((update_obj.record, update_obj.new_permissions))

                    # 4. Recursively sync folder contents (all items inside)
                    await self._sync_folder_contents_recursively(
                        owner_id=owner_id,
                        folder_id=folder_id,
                        batch_records=batch_records
                    )

                except Exception as e:
                    self.logger.error(f"Error processing folder {folder_id}: {e}", exc_info=True)

            # 5. Commit any remaining records
            if batch_records:
                self.logger.info(f"Processing batch of {len(batch_records)} records from folder sync")
                await self.data_entities_processor.on_new_records(batch_records)

        except Exception as e:
            self.logger.error(f"Error syncing folders for owner {owner_id}: {e}", exc_info=True)
        finally:
            # 6. ALWAYS Clear Context
            await self.data_source.clear_as_user_context()

    async def _sync_folder_contents_recursively(
        self,
        owner_id: str,
        folder_id: str,
        batch_records: List,
        user_email: Optional[str] = None,
    ) -> None:
        """
        Recursively fetch all items in a folder, similar to _sync_folder_recursively but simpler.
        Used by incremental sync to handle new folders.
        When user_email is provided (e.g. for shared-with-me sync), use it for Shared with Me group linking.
        """
        offset = 0
        limit = 1000
        fields = 'type,id,name,size,created_at,modified_at,path_collection,etag,sha1,shared_link,owned_by'
        effective_email = user_email or "incremental_sync_user"

        while True:
            try:
                response = await self.data_source.folders_get_folder_items(
                    folder_id=folder_id,
                    limit=limit,
                    offset=offset,
                    fields=fields,
                )

                if not response.success:
                    self.logger.error(f"Failed to fetch items for folder {folder_id}: {response.error}")
                    break

                data = self._to_dict(response.data)
                items = data.get('entries', [])
                total_count = data.get('total_count', 0)

                if not items:
                    break

                sub_folders_to_traverse = []

                # Process each item
                for item in items:
                    try:
                        update_obj = await self._process_box_entry(
                            entry=item,
                            user_id=owner_id,
                            user_email=effective_email,
                            record_group_id=owner_id,
                        )

                        if update_obj:
                            batch_records.append((update_obj.record, update_obj.new_permissions))

                            # If it's a folder, mark it for recursive traversal
                            if update_obj.record.mime_type == MimeTypes.FOLDER.value:
                                sub_folders_to_traverse.append(update_obj.record.external_record_id)

                        # Process batch if it gets too large
                        if len(batch_records) >= self.batch_size:
                            self.logger.info(f"Processing batch of {len(batch_records)} records")
                            await self.data_entities_processor.on_new_records(batch_records)
                            batch_records.clear()
                            await asyncio.sleep(0.1)

                    except Exception as e:
                        self.logger.error(f"Error processing item in folder {folder_id}: {e}", exc_info=True)

                # Recursively process subfolders
                for sub_folder_id in sub_folders_to_traverse:
                    await self._sync_folder_contents_recursively(
                        owner_id, sub_folder_id, batch_records, user_email=user_email
                    )

                offset += len(items)
                if offset >= total_count:
                    break

            except Exception as e:
                self.logger.error(f"Error in _sync_folder_contents_recursively for folder {folder_id}: {e}", exc_info=True)
                break

    async def _execute_deletions(self, file_ids: List[str]) -> None:
        """
        Handles batch deletion of records.
        """
        if not file_ids:
            return

        # self.logger.info(f"🗑️ Processing batch deletion for {len(file_ids)} Box files...")
        self.logger.info(f"ℹ️ [TODO] Skipped deletion for {len(file_ids)} files (Backend support pending). IDs: {file_ids}")
        # arango_service = self.data_store_provider.arango_service

        # deleted_count = 0

        # for external_id in file_ids:
        #     try:
        #         # 1. Use the service to find the record
        #         existing_record = await arango_service.get_record_by_external_id(
        #             connector_id=self.connector_id,
        #             external_id=external_id
        #         )

        #         if not existing_record:
        #             self.logger.debug(f"ℹ️ Skipped deletion: Box File {external_id} not found in DB.")
        #             continue

        #         # 2. Get the internal ID
        #         internal_id = existing_record.id

        #         # 3. Delete using the processor
        #         await self.data_entities_processor.on_record_deleted(
        #             record_id=internal_id
        #         )

        #         deleted_count += 1
        #         self.logger.info(f"✅ Deleted record: {internal_id} (Box ID: {external_id})")

        #     except Exception as e:
        #         self.logger.error(f"❌ Failed to process deletion for Box File {external_id}: {str(e)}")

        # if deleted_count > 0:
        #     self.logger.info(f"🗑️ Batch Deletion Complete: Removed {deleted_count} records.")

    async def get_signed_url(self, record: Record) -> Optional[str]:
        """
        Get a download URL for indexing (creates temporary shared link if needed).
        Uses Box's official downloads.get_download_file_url() API which:
        - Creates a temporary shared link visible in Box UI (expires in ~24 hours)
        - Returns existing shared link URL if file already has one
        - Does not require authentication headers for download
        Note: Similar to Dropbox's get_temporary_link approach. The created shared
        links expire automatically and are visible in Box UI with expiration badge.
        """
        if not self.data_source:
            return None

        # Determine the user context for As-User impersonation
        context_user_id = record.external_record_group_id

        try:
            # Set As-User Context
            if context_user_id:
                await self.data_source.set_as_user_context(context_user_id)

            # Get download URL (creates temporary shared link if needed, expires ~24 hours)
            response = await self.data_source.downloads_get_download_file_url(
                file_id=record.external_record_id
            )

            if response.success and response.data:
                # Response.data is the download URL (shared link or existing link)
                return str(response.data)
            else:
                self.logger.warning(
                    f"Failed to get download URL for {record.record_name}: {response.error}"
                )
                return None

        except Exception as e:
            self.logger.error(
                f"Error getting temporary download URL for record {record.id}: {e}",
                exc_info=True
            )
            return None
        finally:
            # Always clear As-User context to avoid polluting other requests
            if context_user_id:
                await self.data_source.clear_as_user_context()

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Stream a Box file."""
        signed_url = await self.get_signed_url(record)
        if not signed_url:
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail="File not found or access denied"
            )

        return create_stream_record_response(
            stream_content(signed_url),
            filename=record.record_name,
            mime_type=record.mime_type,
            fallback_filename=f"record_{record.id}"
        )

    async def test_connection_and_access(self) -> bool:
        """Test Box connection."""
        if not self.data_source:
            return False
        try:
            response = await self.data_source.get_current_user()
            self.logger.info("Box connection test successful.")
            return response.success
        except Exception as e:
            self.logger.error(f"Box connection test failed: {e}", exc_info=True)
            return False

    def handle_webhook_notification(self, notification: Dict) -> None:
        """Handle a webhook notification by triggering an incremental sync."""
        self.logger.info("Box webhook received. Triggering incremental sync.")
        asyncio.create_task(self.run_incremental_sync())

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
    ) -> "BoxConnector":
        """Factory method to create a Box connector instance."""
        data_entities_processor = DataSourceEntitiesProcessor(
            logger,
            data_store_provider,
            config_service
        )
        await data_entities_processor.initialize()

        return cls(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id=connector_id
        )

    async def cleanup(self) -> None:
        """Clean up Box connector resources."""
        self.logger.info("Cleaning up Box connector resources.")
        self.data_source = None

    async def reindex_records(self, records: List[Record]) -> None:
        """
        Reindex a list of Box records.
        """
        try:
            if not records:
                self.logger.info("No records to reindex")
                return

            self.logger.info(f"Starting reindex for {len(records)} Box records")

            # 1. Group records by Owner
            records_by_owner: Dict[str, List[Record]] = {}
            for record in records:
                owner_id = record.external_record_group_id or self.current_user_id
                if owner_id:
                    records_by_owner.setdefault(owner_id, []).append(record)

            updated_records_batch = []
            non_updated_records_batch = []

            # 2. Process per Owner
            for owner_id, owner_records in records_by_owner.items():
                try:
                    await self.data_source.set_as_user_context(owner_id)

                    tasks = []
                    for rec in owner_records:
                        if rec.mime_type != MimeTypes.FOLDER.value:
                            tasks.append(self.data_source.files_get_file_by_id(rec.external_record_id))
                        else:
                            tasks.append(self.data_source.folders_get_folder_by_id(rec.external_record_id))

                    responses = await asyncio.gather(*tasks, return_exceptions=True)

                    for record, response in zip(owner_records, responses):

                        if isinstance(response, Exception) or not getattr(response, 'success', False):
                            self.logger.warning(f"Could not fetch record {record.record_name} ({record.external_record_id}) during reindex. It may be deleted.")
                            continue

                        entry_dict = self._to_dict(response.data)

                        if not entry_dict:
                            continue

                        update_result = await self._process_box_entry(
                            entry=entry_dict,
                            user_id=owner_id,
                            user_email="reindex_process",
                            record_group_id=owner_id,
                        )

                        if update_result:
                            if update_result.is_updated or update_result.is_new:
                                updated_records_batch.append((update_result.record, update_result.new_permissions or []))
                            else:
                                non_updated_records_batch.append(record)

                except Exception as ex:
                    self.logger.error(f"Error reindexing batch for owner {owner_id}: {ex}")
                finally:
                    await self.data_source.clear_as_user_context()

            # 3. Commit Updates
            if updated_records_batch:
                self.logger.info(f"📝 Updating {len(updated_records_batch)} records that changed at source.")
                await self.data_entities_processor.on_new_records(updated_records_batch)

            # 4. Non-Updated Records
            if non_updated_records_batch:
                self.logger.info(f"✅ Verified {len(non_updated_records_batch)} records (no changes).")
                await self.data_entities_processor.reindex_existing_records(non_updated_records_batch)

        except Exception as e:
            self.logger.error(f"Error during Box reindex: {e}", exc_info=True)
            raise

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

    def _should_include_file(self, entry: Dict) -> bool:
        """
        Determines if a file should be included based on the file extension filter and date filters.

        Args:
            entry: Box file entry dict

        Returns:
            True if the file should be included, False otherwise
        """
        # Only filter files
        if entry.get('type') != 'file':
            return True

        # Get date filters from cache (performance optimization)
        modified_after, modified_before, created_after, created_before = getattr(
            self, '_cached_date_filters', (None, None, None, None)
        )

        # Parse Box timestamps
        modified_at_str = entry.get('modified_at')
        created_at_str = entry.get('created_at')

        modified_at = None
        created_at = None

        if modified_at_str:
            try:
                modified_at = datetime.fromisoformat(modified_at_str.replace('Z', '+00:00'))
                if modified_at.tzinfo is None:
                    modified_at = modified_at.replace(tzinfo=timezone.utc)
            except Exception as e:
                self.logger.debug(f"Could not parse modified_at for {entry.get('name')}: {e}")

        if created_at_str:
            try:
                created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
            except Exception as e:
                self.logger.debug(f"Could not parse created_at for {entry.get('name')}: {e}")

        # Validate: If date filter is configured but file has no date, exclude it
        if modified_after or modified_before:
            if not modified_at:
                self.logger.debug(f"Skipping {entry.get('name')}: no modified date available")
                return False

        if created_after or created_before:
            if not created_at:
                self.logger.debug(f"Skipping {entry.get('name')}: no created date available")
                return False

        # Apply modified date filters
        if modified_at:
            if modified_after and modified_at < modified_after:
                self.logger.debug(f"Skipping {entry.get('name')}: modified {modified_at} before cutoff {modified_after}")
                return False
            if modified_before and modified_at > modified_before:
                self.logger.debug(f"Skipping {entry.get('name')}: modified {modified_at} after cutoff {modified_before}")
                return False

        # Apply created date filters
        if created_at:
            if created_after and created_at < created_after:
                self.logger.debug(f"Skipping {entry.get('name')}: created {created_at} before cutoff {created_after}")
                return False
            if created_before and created_at > created_before:
                self.logger.debug(f"Skipping {entry.get('name')}: created {created_at} after cutoff {created_before}")
                return False

        # Get the extensions filter
        extensions_filter = self.sync_filters.get(SyncFilterKey.FILE_EXTENSIONS)

        # If no filter configured or filter is empty, allow all files
        if extensions_filter is None or extensions_filter.is_empty():
            return True

        # Get the file extension from the entry name
        entry_name = entry.get('name', '')
        file_extension = None
        if entry_name and "." in entry_name:
            file_extension = entry_name.rsplit(".", 1)[-1].lower()

        # Handle files without extensions
        if file_extension is None:
            operator = extensions_filter.get_operator()
            operator_str = operator.value if hasattr(operator, 'value') else str(operator)
            return operator_str == FilterOperator.NOT_IN

        # Get the list of extensions from the filter value
        allowed_extensions = extensions_filter.value
        if not isinstance(allowed_extensions, list):
            return True  # Invalid filter value, allow the file

        # Normalize extensions (lowercase, without dots)
        normalized_extensions = [ext.lower().lstrip(".") for ext in allowed_extensions]

        # Apply the filter based on operator
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

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> NoReturn:
        """Box connector does not support dynamic filter options."""
        raise NotImplementedError("Box connector does not support dynamic filter options")
