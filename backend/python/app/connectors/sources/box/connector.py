import asyncio
import mimetypes
import uuid
from datetime import datetime, timezone
from logging import Logger
from typing import AsyncGenerator, Dict, List, Optional, Tuple

from aiohttp import ClientSession
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
    generate_record_sync_point_key,
)
from app.connectors.core.registry.connector_builder import (
    AuthField,
    CommonFields,
    ConnectorBuilder,
    DocumentationLink,
)

# App-specific Box client imports
from app.connectors.sources.box.common.apps import BoxApp
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate

# Model imports
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.box.box import (
    BoxClient,
    BoxTokenConfig,
)
from app.sources.external.box.box import BoxDataSource
from app.utils.streaming import stream_content


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
    .with_auth_type("API_TOKEN")\
    .with_description("Sync files and folders from Box")\
    .with_categories(["Storage"])\
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
        # .with_redirect_uri("connectors/oauth/callback/Box", True)
        .with_oauth_urls(
            "https://account.box.com/api/oauth2/authorize",
            "https://api.box.com/oauth2/token",
            ["root_readwrite", "manage_managed_users", "manage_groups"]  # Note: Box scopes are configured in Developer Console, not in OAuth URL
        )
        .add_auth_field(CommonFields.client_id("Box Developer Console"))
        .add_auth_field(CommonFields.client_secret("Box Developer Console"))
        .add_auth_field(AuthField(
            name="enterpriseId",
            display_name="Box Enterprise ID",
            placeholder="Enter Box Enterprise ID",
            description="The Enterprise ID from Box Developer Console"
        ))
        .with_webhook_config(True, ["FILE.UPLOADED", "FILE.DELETED", "FILE.MOVED", "FOLDER.CREATED"])
        .with_scheduled_config(True, 60)
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

    current_user_id: Optional[str] = None

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
    ) -> None:

        super().__init__(BoxApp(), logger, data_entities_processor, data_store_provider, config_service)

        self.connector_name = Connectors.BOX

        # Initialize sync point for tracking record changes
        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_name=self.connector_name,
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
        
        # Track the current access token to detect changes
        self._current_access_token: Optional[str] = None

    async def init(self) -> bool:
        """Initializes the Box client using credentials from the config service."""
        config = await self.config_service.get_config(
            "/services/connectors/box/config"
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
        # Extract enterprise_id from auth config
        enterprise_id = auth_config.get("enterpriseId")

        if not client_id or not client_secret or not enterprise_id:
            self.logger.error("Box client_id, client_secret, or enterprise_id not found in configuration.")
            return False

        try:
            # Check if we already have credentials (OAuth flow)
            credentials_config = config.get("credentials", {}) or {}
            access_token = credentials_config.get("access_token")

            # If no stored access token, attempt to get one via HTTP API call
            if not access_token:
                self.logger.info("No stored access token found. Attempting to fetch via HTTP API...")
                # Pass enterprise_id to the fetch method
                access_token = await self._fetch_access_token_via_http(client_id, client_secret, enterprise_id)
                
                if not access_token:
                    self.logger.error("Failed to fetch access token via HTTP API.")
                    return False

            # Initialize Box client with the access token
            config_obj = BoxTokenConfig(token=access_token)
            client = await BoxClient.build_with_config(config_obj)
            await client.get_client().create_client()
            self.data_source = BoxDataSource(client)
            
            # Store the initial token
            self._current_access_token = access_token
            
            self.logger.info("Box client initialized successfully.")
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize Box client: {e}", exc_info=True)
            return False

    async def _fetch_access_token_via_http(self, client_id: str, client_secret: str, enterprise_id: str) -> Optional[str]:
        """
        Fetch access token from Box API using client credentials.
        
        Args:
            client_id: Box application client ID
            client_secret: Box application client secret
            enterprise_id: Box Enterprise ID for subject_id
            
        Returns:
            Access token string or None if failed
        """
        token_url = f"{self.BASE_URL}{self.TOKEN_ENDPOINT}"
        
        try:
            async with ClientSession() as session:
                # Prepare request data for OAuth token exchange
                data = {
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "box_subject_type": "enterprise",
                    "box_subject_id": enterprise_id
                }
                
                self.logger.info(f"Fetching access token from {token_url}")
                
                async with session.post(token_url, data=data) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        self.logger.error(
                            f"Failed to fetch access token. Status: {response.status}, "
                            f"Response: {error_text}"
                        )
                        return None
                    
                    token_data = await response.json()
                    access_token = token_data.get("access_token")
                    return access_token                    

                    
        except Exception as e:
            self.logger.error(f"Error fetching access token via HTTP: {e}", exc_info=True)
            return None

    async def _get_fresh_datasource(self) -> None:
        """
        Ensures self.data_source is using an ALWAYS-FRESH access token.
        It checks the central config and rebuilds the client if the token has changed.
        """
        try:
            # 1. Fetch current config from configuration service
            config = await self.config_service.get_config("/services/connectors/box/config")
            
            if not config:
                self.logger.warning("Could not fetch Box config for token refresh check.")
                return

            # 2. Extract fresh OAuth access token
            credentials_config = config.get("credentials", {}) or {}
            fresh_token = credentials_config.get("access_token", "")

            if not fresh_token:
                self.logger.warning("No OAuth access token found in config refresh check.")
                return

            # 3. Compare with existing token
            if self._current_access_token != fresh_token:
                self.logger.info("🔄 Detected new Box Access Token. Re-initializing client...")
                
                # 4. Re-initialize the client with the new token
                config_obj = BoxTokenConfig(token=fresh_token)
                client = await BoxClient.build_with_config(config_obj)
                
                # Create the internal client instance
                await client.get_client().create_client()
                
                # 5. Update the datasource and the tracker
                self.data_source = BoxDataSource(client)
                self._current_access_token = fresh_token
                
                # 6. Clear any cached user ID to force re-fetch with new token
                self.current_user_id = None
                
                self.logger.info("✅ Box client successfully updated with fresh token.")
            else:
                self.logger.debug("Token unchanged, skipping client refresh.")
                
        except Exception as e:
            # Log error but don't crash; attempt to proceed with existing token
            self.logger.error(f"Error checking for fresh datasource: {e}", exc_info=True)

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

    async def _process_box_entry(
        self,
        entry: Dict,
        user_id: str,
        user_email: str,
        record_group_id: str,
        is_personal_folder: bool
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
                    connector_name=self.connector_name
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

            web_url = entry.get('shared_link', {}).get('url')
            if not web_url:
                web_url = f"https://app.box.com/{entry_type}/{entry_id}"

            file_record = FileRecord(
                id=record_id,
                org_id=self.data_entities_processor.org_id,
                record_name=entry_name,
                record_type=record_type.value,
                record_group_type=RecordGroupType.DRIVE.value,
                external_record_id=entry_id,
                external_record_group_id=record_group_id,
                parent_external_record_id=parent_external_record_id,
                parent_record_type=RecordType.FILE.value if parent_external_record_id else None,
                version=version,
                origin=OriginTypes.CONNECTOR.value,
                connector_name=self.connector_name.value,
                mime_type=mime_type,
                weburl=web_url,
                created_at=current_timestamp,
                updated_at=current_timestamp,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
                is_file=is_file,
                size_in_bytes=file_size,
                extension=get_file_extension(entry_name) if is_file else None,
                path=file_path,
                etag=entry.get('etag'),
                sha1_hash=entry.get('sha1'),
                external_revision_id=entry.get('etag')
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
                # Handle 404 (Not Found) gracefully - usually means no permission to view collabs
                if response.status_code == 404:
                    # Just debug log a short message, don't dump the whole error
                    self.logger.debug(f"No collaborations found or accessible for {item_type} {item_id} (404).")
                else:
                    self.logger.debug(f"Could not fetch permissions for {item_type} {item_id}: {response.error}")
                return permissions

            collaborations = response.data.get('entries', []) if response.data else []

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
        is_personal_folder: bool
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
                is_personal_folder=is_personal_folder
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
                        connector_name=self.connector_name
                    )
                    if existing_record:
                        await self.data_entities_processor.on_record_deleted(
                            record_id=existing_record.id
                        )

            elif record_update.is_updated:
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

                users_data = response.data.get('entries', []) if response.data else []

                if not users_data:
                    break

                for user in users_data:
                    app_user = AppUser(
                        app_name=self.connector_name,
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
        Get AppUser objects by their email addresses from database.

        Args:
            emails: List of user email addresses

        Returns:
            List of AppUser objects found in database
        """
        if not emails:
            return []

        try:
            # Fetch all users from database
            all_app_users = await self.data_entities_processor.get_all_app_users(
                app_name=Connectors.BOX
            )

            self.logger.debug(f"Fetched {len(all_app_users)} total users from database for email lookup")

            # Create email lookup set
            email_set = set(emails)

            # Filter users by email
            filtered_users = [user for user in all_app_users if user.email in email_set]

            if len(filtered_users) < len(emails):
                missing_count = len(emails) - len(filtered_users)
                self.logger.debug(f"  ⚠️ {missing_count} user(s) not found in database")

            return filtered_users

        except Exception as e:
            self.logger.error(f"❌ Failed to get users by emails: {e}")
            return []

    async def _sync_user_groups(self) -> None:
        """
        Sync Box groups and their memberships.
        """
        try:
            self.logger.info("Syncing Box groups...")

            offset = 0
            limit = 1000

            while True:
                response = await self.data_source.groups_get_groups(limit=limit, offset=offset)

                if not response.success:
                    self.logger.error(f"Failed to fetch groups: {response.error}")
                    break

                groups_data = response.data.get('entries', []) if response.data else []

                if not groups_data:
                    break

                for group in groups_data:
                    group_id = group.get('id')
                    group_name = group.get('name', '')

                    # Create AppUserGroup
                    app_user_group = AppUserGroup(
                        app_name=self.connector_name,
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
                    self.logger.info(f"Syncing group {group_name} with members {members_response}")
                    user_emails = []
                    if members_response.success:
                        memberships = members_response.data.get('entries', []) if members_response.data else []
                        for membership in memberships:
                            user = membership.get('user', {})
                            email = user.get('login')
                            if email:
                                user_emails.append(email)

                    # Get AppUser objects for members
                    app_users = await self._get_app_users_by_emails(user_emails)
                    for app_user in app_users:
                        self.logger.info(f"Syncing group {app_user_group.name} with member {app_user.email}")
                    # Sync group and memberships (wrapped in list as expected by on_new_user_groups)
                    await self.data_entities_processor.on_new_user_groups([(app_user_group, app_users)])

                offset += limit

                if len(groups_data) < limit:
                    break

            self.logger.info("Box groups sync completed")

        except Exception as e:
            self.logger.error(f"Error syncing Box groups: {e}", exc_info=True)

    async def _sync_record_groups(self, users: List[AppUser]) -> None:
        """
        Sync Box drives as RecordGroup entities.
        In Box, each user has a root "All Files" folder (folder_id='0') which acts as their drive.
        RecordGroup represents this drive, while individual folders and files are FileRecords.
        """
        try:
            self.logger.info("Syncing Box record groups (user drives)...")

            for user in users:
                # Get the root folder info to use as the user's drive
                response = await self.data_source.folders_get_folder_by_id(folder_id='0')

                if not response.success:
                    self.logger.warning(f"Could not fetch root folder for user {user.email}: {response.error}")
                    continue

                root_folder = response.data if response.data else {}

                # Create RecordGroup for user's drive (their "All Files" root storage)
                record_group = RecordGroup(
                    org_id=self.data_entities_processor.org_id,
                    name=f"{user.full_name or user.email}'s Box",
                    external_group_id=user.source_user_id,
                    external_user_id=user.source_user_id,
                    connector_name=self.connector_name.value,
                    group_type=RecordGroupType.DRIVE.value,
                    web_url=root_folder.get('shared_link', {}).get('url') if root_folder.get('shared_link') else None,
                    source_created_at=int(datetime.fromisoformat(root_folder.get('created_at', '').replace('Z', '+00:00')).timestamp() * 1000) if root_folder.get('created_at') else None,
                    source_updated_at=int(datetime.fromisoformat(root_folder.get('modified_at', '').replace('Z', '+00:00')).timestamp() * 1000) if root_folder.get('modified_at') else None,
                )

                # User has owner permission on their own drive
                drive_permissions = [Permission(
                    external_id=user.source_user_id,
                    email=user.email,
                    type=PermissionType.OWNER,
                    entity_type=EntityType.USER
                )]

                await self.data_entities_processor.on_new_record_groups([(record_group, drive_permissions)])

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
                if current_user_response.success:
                    self.current_user_id = current_user_response.data.id
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
            # 1. Capture the current datasource instance before checking for updates
            previous_datasource = self.data_source
            
            await self._get_fresh_datasource()
            
            # 2. If the datasource was replaced (token refresh), re-apply the user context!
            if self.data_source != previous_datasource:
                try:
                    if self.current_user_id and user.source_user_id != self.current_user_id:
                        self.logger.info(f"🔄 Token refreshed. Re-applying As-User context for {user.email}")
                        await self.data_source.set_as_user_context(user.source_user_id)
                    else:
                        await self.data_source.clear_as_user_context()
                except Exception as e:
                    self.logger.error(f"Failed to re-apply As-User context after refresh: {e}")

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

            data = response.data
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
                True
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

                # Check is_file is False instead of mime_type
                if file_record and not file_record.is_file:
                    sub_folders_to_traverse.append(file_record.external_record_id)

            for sub_folder_id in sub_folders_to_traverse:
                await self._sync_folder_recursively(user, sub_folder_id, batch_records)

            offset += len(items)
            if offset >= total_count:
                break
        try:
            await self.data_source.clear_as_user_context()
        except:
            pass

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

    async def run_sync(self) -> None:
        """Runs a full synchronization from the Box account."""
        try:
            self.logger.info("Starting Box full sync.")

            # Step 1: Sync users
            self.logger.info("Syncing users...")
            users = await self._sync_users()
            await self.data_entities_processor.on_new_app_users(users)

            # Step 2: Sync user groups
            self.logger.info("Syncing user groups...")
            await self._sync_user_groups()

            # Step 3: Sync record groups (user drives)
            self.logger.info("Syncing user drives...")
            await self._sync_record_groups(users)

            # Step 4: Sync user files and folders
            self.logger.info("Syncing user files and folders...")
            await self._process_users_in_batches(users)

            self.logger.info("Box full sync completed.")
        except Exception as ex:
            self.logger.error(f"Error in Box connector run: {ex}", exc_info=True)
            raise

    async def run_incremental_sync(self) -> None:
        """Runs an incremental sync using Box events API."""
        self.logger.info("Starting Box incremental sync.")
        # TODO: Implement incremental sync using Box events stream
        self.logger.warning("Incremental sync not yet implemented for Box")
        pass

    async def get_signed_url(self, record: Record) -> Optional[str]:
        """
        Get a signed URL, ensuring we impersonate the correct user (Record Group Owner).
        """
        if not self.data_source:
            return None
            
        # 1. Determine the user context
        # In our sync logic, external_record_group_id IS the User ID who owns/sees the file.
        context_user_id = record.external_record_group_id
        
        try:
            # 2. Set As-User Context
            if context_user_id:
                # self.logger.info(f"🎭 Impersonating user {context_user_id} to fetch URL for {record.record_name}")
                await self.data_source.set_as_user_context(context_user_id)
            
            # 3. Try to get existing file info
            response = await self.data_source.files_get_file_by_id(
                file_id=record.external_record_id
            )

            download_url = None

            if response.success and response.data:
                file_data = response.data
                
                # Check for shared link safely
                shared_link = None
                if isinstance(file_data, dict):
                    shared_link = file_data.get('shared_link')
                else:
                    shared_link = getattr(file_data, 'shared_link', None)

                if shared_link:
                    if isinstance(shared_link, dict):
                        download_url = shared_link.get('download_url')
                    else:
                        download_url = getattr(shared_link, 'download_url', None)

            # 4. If no URL found, create a temporary shared link
            if not download_url:
                # self.logger.info(f"No existing shared link for {record.record_name}, creating one...")
                
                # Note: We are still in the 'As-User' context here, so this PUT request 
                # will now work instead of returning 404
                link_response = await self.data_source.shared_links_create_shared_link_for_file(
                    file_id=record.external_record_id,
                    access='open' # or 'company' depending on security requirements
                )

                if link_response.success and link_response.data:
                    file_data = link_response.data
                    shared_link = None
                    if isinstance(file_data, dict):
                        shared_link = file_data.get('shared_link')
                    else:
                        shared_link = getattr(file_data, 'shared_link', None)

                    if shared_link:
                        if isinstance(shared_link, dict):
                            download_url = shared_link.get('download_url')
                        else:
                            download_url = getattr(shared_link, 'download_url', None)
                else:
                    self.logger.warning(f"Failed to create shared link for {record.record_name}: {link_response.error}")

            if download_url:
                return download_url

            return None

        except Exception as e:
            self.logger.error(f"Error creating signed URL for record {record.id}: {e}", exc_info=True)
            return None
        finally:
            # 5. ALWAYS clear context to avoid polluting other requests
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

        return StreamingResponse(
            stream_content(signed_url),
            media_type=record.mime_type if record.mime_type else "application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename={record.record_name}"
            }
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
        config_service: ConfigurationService
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
            config_service
        )

    async def cleanup(self) -> None:
        """Clean up Box connector resources."""
        self.logger.info("Cleaning up Box connector resources.")
        self.data_source = None

    async def reindex_records(self, record_results: List[Record]) -> None:
        """Reindex records - not implemented for Box yet."""
        self.logger.warning("Reindex not implemented for Box connector")
        pass
