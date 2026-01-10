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
)
from app.connectors.core.registry.connector_builder import (
    AuthField,
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
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
    .with_auth_type("API_TOKEN")\
    .with_description("Sync files and folders from Box")\
    .with_categories(["Storage"])\
    .with_scopes([ConnectorScope.TEAM.value, ConnectorScope.PERSONAL.value])\
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
        .with_oauth_urls(
            "https://account.box.com/api/oauth2/authorize",
            "https://api.box.com/oauth2/token",
            ["root_readwrite", "manage_managed_users", "manage_groups", "manage_enterprise_properties"]  # Note: Box scopes are configured in Developer Console, not in OAuth URL
        )
        .add_auth_field(CommonFields.client_id("Box Developer Console"))
        .add_auth_field(CommonFields.client_secret("Box Developer Console"))
        .add_auth_field(AuthField(
            name="enterpriseId",
            display_name="Box Enterprise ID",
            placeholder="Enter Box Enterprise ID",
            description="The Enterprise ID from Box Developer Console"
        ))
        .with_webhook_config(True, ["FILE.UPLOADED", "FILE.DELETED", "FILE.MOVED", "FOLDER.CREATED", "COLLABORATION.REMOVED"])
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

        # Track the current access token to detect changes
        self._current_access_token: Optional[str] = None

    async def init(self) -> bool:
        """Initializes the Box client using credentials from the config service."""
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
                    if response.status != self.HTTP_OK:
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
            config = await self.config_service.get_config(f"/services/connectors/{self.connector_id}/config")

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
                connector_id=self.connector_id,
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
                # Handle 404 no permission to view collabs
                if response.status_code == self.HTTP_NOT_FOUND:
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
                        connector_id=self.connector_id
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
                # Get the root folder info to use as the user's drive
                response = await self.data_source.folders_get_folder_by_id(folder_id='0')

                if not response.success:
                    self.logger.warning(f"Could not fetch root folder for user {user.email}: {response.error}")
                    continue

                root_folder = self._to_dict(response.data)

                # Create RecordGroup for user's drive (their "All Files" root storage)
                record_group = RecordGroup(
                    org_id=self.data_entities_processor.org_id,
                    name=f"{user.full_name or user.email}'s Box",
                    external_group_id=user.source_user_id,
                    external_user_id=user.source_user_id,
                    connector_name=self.connector_name.value,
                    connector_id=self.connector_id,
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
                self.logger.info(f"✅ [Smart Sync] Found existing cursor: {cursor_val}")
                self.logger.info("🚀 [Smart Sync] Switching to INCREMENTAL SYNC path.")

                # Hand off to the incremental engine
                await self.run_incremental_sync()
                return

            # NO CURSOR FOUND PROCEED WITH FULL SYNC
            self.logger.info("⚪ [Smart Sync] No cursor found. Starting FULL SYNC & Anchoring.")

            # ANCHOR THE STREAM
            try:
                await self._get_fresh_datasource()

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
                        await self.box_cursor_sync_point.update_sync_point(
                            key,
                            {"cursor": next_stream_pos}
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

        try:
            self.logger.info("👥 [Incremental] Refreshing User list...")
            users = await self._sync_users()

            # Update the in-memory or DB map of users so we can link files to them later
            await self.data_entities_processor.on_new_app_users(users)

            self.logger.info("🛡️ [Incremental] Refreshing Virtual Groups...")
            await self._ensure_virtual_groups()

            self.logger.info("👥 [Incremental] Refreshing User Groups...")
            await self._sync_user_groups()

        except Exception as e:
            # If this fails, log it, but maybe still try to process file events?
            self.logger.error(f"⚠️ [Incremental] Failed to refresh users/groups: {e}")

        key = "event_stream_cursor"

        # 1. Load Cursor
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
                await self._get_fresh_datasource()

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
                    await self._process_event_batch(events)
                else:
                    self.logger.info("ℹ️ [Incremental] Box says: No new events yet.")
                    has_more = False

                if next_stream_position:
                    stream_position = next_stream_position
                    # Update cursor immediately
                    await self.box_cursor_sync_point.update_sync_point(
                        key,
                        {"cursor": stream_position}
                    )
                    self.logger.debug(f"💾 [Incremental] Updated cursor to: {stream_position}")

        except Exception as e:
            self.logger.error(f"❌ [Incremental] Error during sync: {e}", exc_info=True)

    async def _process_event_batch(self, events: List[Dict]) -> None:
        """
        Deduplicates events, handles deletions, and groups updates.
        Now handles "Flat" dictionary schemas AND fetches missing emails.
        """
        files_to_sync: Dict[str, str] = {}
        files_to_delete: set = set()

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

        def get_val(obj: Optional[object], key: str, default: Optional[object] = None) -> Optional[object]:
            if obj is None:
                return default
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        for event in events:
            event_type = get_val(event, 'event_type')
            source = get_val(event, 'source')

            # 1. HANDLE REVOCATIONS (Permissions Removed)
            if event_type in REVOCATION_EVENTS:
                file_id = None
                removed_email = None
                removed_user_box_id = None

                if source:
                    # PATH A: Standard Box Object
                    item = get_val(source, 'item')
                    if item:
                        file_id = get_val(item, 'id')

                    accessible_by = get_val(source, 'accessible_by')
                    if accessible_by:
                        removed_email = get_val(accessible_by, 'login')
                        removed_user_box_id = get_val(accessible_by, 'id')

                    # PATH B: Flat Dictionary
                    if not file_id:
                        file_id = get_val(source, 'file_id')

                    if not removed_user_box_id:
                        removed_user_box_id = get_val(source, 'user_id')

                if not file_id:
                    file_id = get_val(event, 'source_item_id') or get_val(event, 'item_id')

                # Fetch Email from Box if we only have ID using data_source
                if removed_user_box_id and not removed_email:
                    try:
                        user_response = await self.data_source.users_get_user_by_id(removed_user_box_id)

                        if user_response.success and user_response.data:
                            user_data = self._to_dict(user_response.data)
                            removed_email = user_data.get('login')
                        else:
                            self.logger.warning(f"⚠️ Failed to fetch user details for ID {removed_user_box_id}: {user_response.error}")

                    except AttributeError:
                        try:
                            # Fallback attempt
                            user_response = await self.data_source.users_get_user(removed_user_box_id)
                            if user_response.success:
                                user_data = self._to_dict(user_response.data)
                                removed_email = user_data.get('login')
                        except Exception as e:
                             self.logger.error(f"❌ Failed to resolve Box ID {removed_user_box_id} (Fallback): {e}")

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
                            async with self.data_store_provider.transaction() as tx_store:
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

            # 2. EXTRACT FILE ID (Standard Events)
            file_id = None
            if source:
                file_id = get_val(source, 'id') or get_val(source, 'item_id') or get_val(source, 'file_id')

            if not file_id:
                file_id = get_val(event, 'item_id') or get_val(event, 'source_item_id')

            if not file_id:
                continue

            # 3. HANDLE DELETIONS
            if event_type in DELETION_EVENTS:
                self.logger.info(f"🗑️ Found DELETION event ({event_type}) for File ID: {file_id}")
                files_to_delete.add(file_id)
                files_to_sync.pop(file_id, None)
                continue

            # 4. FILTER & PREPARE SYNC
            if source:
                item_type = get_val(source, 'item_type') or get_val(source, 'type')
                if item_type and item_type.lower() != 'file':
                    continue

            if file_id in files_to_delete:
                files_to_delete.remove(file_id)

            owner = get_val(source, 'owned_by') or get_val(event, 'created_by')
            owner_id = get_val(owner, 'id') if owner else None

            if owner_id:
                files_to_sync[file_id] = owner_id

        # 5. EXECUTE BATCHES
        if files_to_delete:
            self.logger.info(f"⚠️ Executing {len(files_to_delete)} deletions...")
            await self._execute_deletions(list(files_to_delete))

        if files_to_sync:
            owner_groups = {}
            for fid, oid in files_to_sync.items():
                owner_groups.setdefault(oid, []).append(fid)

            for owner_id, file_ids in owner_groups.items():
                await self._fetch_and_sync_files_for_owner(owner_id, file_ids)

    async def _fetch_and_sync_files_for_owner(self, owner_id: str, file_ids: List[str]) -> None:
        """
        Impersonates the owner, fetches full file details in parallel, and upserts.
        """
        try:
            # 1. Switch Context to the File Owner
            await self.data_source.set_as_user_context(owner_id)

            # 2. Parallel Fetch of File Details
            tasks = [self.data_source.files_get_file_by_id(fid) for fid in file_ids]
            responses = await asyncio.gather(*tasks, return_exceptions=True)

            updates_to_push = []

            for res in responses:
                if isinstance(res, Exception) or not getattr(res, 'success', False):
                    continue

                file_entry = self._to_dict(res.data)

                if not file_entry:
                    self.logger.warning("Converted file entry is empty")
                    continue

                # 3. Reuse existing _process_box_entry logic
                update_obj = await self._process_box_entry(
                    entry=file_entry,
                    user_id=owner_id,
                    user_email="incremental_sync_user",
                    record_group_id=owner_id,
                    is_personal_folder=True
                )

                if update_obj:
                    updates_to_push.append((update_obj.record, update_obj.new_permissions))

            # 4. Batch Upsert to Database
            if updates_to_push:
                await self.data_entities_processor.on_new_records(updates_to_push)

        except Exception as e:
            self.logger.error(f"Error syncing files for owner {owner_id}: {e}")
        finally:
            # 5. ALWAYS Clear Context
            await self.data_source.clear_as_user_context()

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
        Get a signed URL, ensuring we impersonate the correct user (Record Group Owner).
        """
        if not self.data_source:
            return None

        # 1. Determine the user context
        context_user_id = record.external_record_group_id

        try:
            # 2. Set As-User Context
            if context_user_id:
                await self.data_source.set_as_user_context(context_user_id)

            # 3. Try to get existing file info
            response = await self.data_source.files_get_file_by_id(
                file_id=record.external_record_id
            )

            download_url = None

            if response.success:
                file_data = self._to_dict(response.data)

                # shared_link might be None, dict, or Object (if _to_dict was shallow, though it usually handles it)
                shared_link = file_data.get('shared_link')
                if isinstance(shared_link, dict):
                    download_url = shared_link.get('download_url')
                elif hasattr(shared_link, 'download_url'):
                    # Fallback for SDK objects that survived
                    download_url = getattr(shared_link, 'download_url', None)

            # 4. If no URL found, create a temporary shared link
            if not download_url:
                link_response = await self.data_source.shared_links_create_shared_link_for_file(
                    file_id=record.external_record_id,
                    access='open'
                )

                if link_response.success:
                    file_data = self._to_dict(link_response.data)
                    shared_link = file_data.get('shared_link')

                    if isinstance(shared_link, dict):
                        download_url = shared_link.get('download_url')
                else:
                    self.logger.warning(f"Failed to create shared link for {record.record_name}: {link_response.error}")

            return str(download_url) if download_url else None

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
                        if rec.is_file:
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
                            is_personal_folder=True
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
