import asyncio
import io
import logging
import os
import tempfile
import uuid
from datetime import datetime
from logging import Logger
from pathlib import Path
from typing import AsyncGenerator, Dict, List, Optional, Tuple

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    ExtensionTypes,
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
from app.connectors.core.registry.auth_builder import AuthType, OAuthScopeConfig
from app.connectors.core.registry.connector_builder import (
    AuthBuilder,
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
    FilterOption,
    FilterOptionsResponse,
    FilterType,
    IndexingFilterKey,
    OptionSourceType,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.sources.google.common.apps import GoogleDriveTeamApp
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
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
from app.sources.client.google.google import GoogleClient
from app.sources.external.google.admin.admin import GoogleAdminDataSource
from app.sources.external.google.drive.drive import GoogleDriveDataSource
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import get_epoch_timestamp_in_ms, parse_timestamp


@ConnectorBuilder("Drive Workspace")\
    .in_group("Google Workspace")\
    .with_description("Sync files and folders from Google Drive")\
    .with_categories(["Storage"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Drive Workspace",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            redirect_uri="connectors/oauth/callback/Drive",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[
                    "https://www.googleapis.com/auth/drive.readonly",
                    "https://www.googleapis.com/auth/drive.metadata.readonly",
                    "https://www.googleapis.com/auth/drive.metadata",
                    "https://www.googleapis.com/auth/documents.readonly",
                    "https://www.googleapis.com/auth/spreadsheets.readonly",
                    "https://www.googleapis.com/auth/presentations.readonly",
                    "https://www.googleapis.com/auth/drive.file",
                    "https://www.googleapis.com/auth/drive",
                ],
                agent=[]
            ),
            fields=[
                CommonFields.client_id("Google Cloud Console"),
                CommonFields.client_secret("Google Cloud Console")
            ],
            icon_path="/assets/icons/connectors/drive.svg",
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
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/drive.svg")
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
        .add_filter_field(CommonFields.modified_date_filter("Filter files and folders by modification date."))
        .add_filter_field(CommonFields.created_date_filter("Filter files and folders by creation date."))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .add_filter_field(FilterField(
            name="file_extensions",
            display_name="Sync Files with Extensions",
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            description="Sync files with specific extensions",
            default_value=[],
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
            display_name="Index Items Shared by me",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of items shared by me",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="shared_with_me",
            display_name="Index Items Shared With Me",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of shared with me items",
            default_value=True
        ))
        .with_webhook_config(False, [])
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .add_sync_custom_field(CommonFields.batch_size_field())
        .with_sync_support(True)
        .with_agent_support(True)
    )\
    .build_decorator()
class GoogleDriveTeamConnector(BaseConnector):
    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str
    ) -> None:
        super().__init__(
            GoogleDriveTeamApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id
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
        self.user_sync_point = _create_sync_point(SyncDataPointType.USERS)
        self.user_group_sync_point = _create_sync_point(SyncDataPointType.GROUPS)
        self.connector_id = connector_id

        # Batch processing configuration
        self.batch_size = 100
        self.max_concurrent_batches = 1 # set to 1 for now to avoid write write conflicts for small number of records

        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

        # Google clients and data sources (initialized in init())
        self.admin_client: Optional[GoogleClient] = None
        self.drive_client: Optional[GoogleClient] = None
        self.admin_data_source: Optional[GoogleAdminDataSource] = None
        self.drive_data_source: Optional[GoogleDriveDataSource] = None
        self.config: Optional[Dict] = None
        logging.getLogger('googleapiclient.http').setLevel(logging.ERROR)

        # Store synced users for use in batch processing
        self.synced_users: List[AppUser] = []

    async def init(self) -> bool:
        """Initialize the Google Drive enterprise connector with service account credentials and services."""
        try:
            # Load connector config
            config = await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config"
            )
            if not config:
                self.logger.error("Google Drive enterprise config not found")
                return False


            self.config = {"credentials": config}

            # Extract service account credentials JSON from config
            # GoogleClient.build_from_services expects credentials in 'auth' field
            credentials_json = config.get("auth", {})
            if not credentials_json:
                self.logger.error(
                    "Service account credentials not found in config. Ensure credentials JSON is configured under 'auth' field."
                )
                raise ValueError(
                    "Service account credentials not found. Ensure credentials JSON is configured under 'auth' field."
                )

            # Extract admin email from credentials JSON
            admin_email = credentials_json.get("adminEmail")
            if not admin_email:
                self.logger.error(
                    "Admin email not found in credentials. Ensure adminEmail is set in credentials JSON."
                )
                raise ValueError(
                    "Admin email not found in credentials. Ensure adminEmail is set in credentials JSON."
                )

            # Initialize Google Admin Client using build_from_services
            try:
                self.admin_client = await GoogleClient.build_from_services(
                    service_name="admin",
                    logger=self.logger,
                    config_service=self.config_service,
                    is_individual=False,  # This is an enterprise connector
                    version="directory_v1",
                    connector_instance_id=self.connector_id
                )

                # Create Google Admin Data Source from the client
                self.admin_data_source = GoogleAdminDataSource(
                    self.admin_client.get_client()
                )

                self.logger.info(
                    "✅ Google Admin client and data source initialized successfully"
                )
            except Exception as e:
                self.logger.error(
                    f"❌ Failed to initialize Google Admin client: {e}",
                    exc_info=True
                )
                raise ValueError(f"Failed to initialize Google Admin client: {e}") from e

            # Initialize Google Drive Client using build_from_services
            try:
                self.drive_client = await GoogleClient.build_from_services(
                    service_name="drive",
                    logger=self.logger,
                    config_service=self.config_service,
                    is_individual=False,  # This is an enterprise connector
                    version="v3",
                    connector_instance_id=self.connector_id
                )

                # Create Google Drive Data Source from the client
                self.drive_data_source = GoogleDriveDataSource(
                    self.drive_client.get_client()
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

            self.logger.info("✅ Google Drive enterprise connector initialized successfully")
            return True

        except Exception as ex:
            self.logger.error(f"❌ Error initializing Google Drive enterprise connector: {ex}", exc_info=True)
            raise

    async def run_sync(self) -> None:
        """
        Main entry point for the Google Drive enterprise connector.
        Implements enterprise sync workflow: users → groups → record groups → process batches.
        """
        try:
            self.logger.info("Starting Google Drive enterprise connector sync")

            # Load sync and indexing filters
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "drive", self.connector_id, self.logger
            )

            # Step 1: Sync users
            self.logger.info("Syncing users...")
            await self._sync_users()

            # Step 2: Sync user groups and their members
            self.logger.info("Syncing user groups...")
            await self._sync_user_groups()

            # Step 3: Sync record groups (drives) for users
            self.logger.info("Syncing record groups...")
            await self._sync_record_groups()

            # Step 4: Process user drives in batches (includes personal drive and shared drives)
            self.logger.info("Processing user drives in batches...")
            # Use users synced in Step 1
            await self._process_users_in_batches(self.synced_users)

            self.logger.info("Google Drive enterprise connector sync completed successfully")

        except Exception as e:
            self.logger.error(f"❌ Error in Google Drive enterprise connector run: {e}", exc_info=True)
            raise

    async def _sync_users(self) -> None:
        """Sync all users from Google Workspace Admin API."""
        try:
            if not self.admin_data_source:
                self.logger.error("Admin data source not initialized. Call init() first.")
                raise ValueError("Admin data source not initialized")

            self.logger.info("Fetching all users from Google Workspace Admin API...")
            all_users: List[AppUser] = []
            page_token: Optional[str] = None

            while True:
                try:
                    # Fetch users with pagination
                    result = await self.admin_data_source.users_list(
                        customer="my_customer",
                        projection="full",
                        orderBy="email",
                        pageToken=page_token,
                        maxResults=500  # Maximum allowed by Google Admin API
                    )

                    users_data = result.get("users", [])
                    if not users_data:
                        break

                    # Transform Google user dictionaries to AppUser objects
                    for user in users_data:
                        try:
                            # Get email
                            email = user.get("primaryEmail") or user.get("email", "")
                            if not email:
                                self.logger.warning(f"Skipping user without email: {user.get('id')}")
                                continue

                            # Get full name
                            name_info = user.get("name", {})
                            full_name = name_info.get("fullName", "")
                            if not full_name:
                                given_name = name_info.get("givenName", "")
                                family_name = name_info.get("familyName", "")
                                full_name = f"{given_name} {family_name}".strip()
                            if not full_name:
                                full_name = email  # Fallback to email if no name available

                            # Get title from organizations
                            title = None
                            organizations = user.get("organizations", [])
                            if organizations and len(organizations) > 0:
                                title = organizations[0].get("title")

                            # Check if user is active (not suspended)
                            is_active = not user.get("suspended", False)

                            # Convert creation time to epoch milliseconds
                            source_created_at = None
                            creation_time = user.get("creationTime")
                            if creation_time:
                                try:
                                    source_created_at = parse_timestamp(creation_time)
                                except Exception as e:
                                    self.logger.warning(f"Failed to parse creation time for user {email}: {e}")

                            app_user = AppUser(
                                app_name=self.connector_name,
                                connector_id=self.connector_id,
                                source_user_id=user.get("id", ""),
                                email=email,
                                full_name=full_name,
                                is_active=is_active,
                                title=title,
                                source_created_at=source_created_at
                            )
                            all_users.append(app_user)

                        except Exception as e:
                            self.logger.error(f"Error processing user {user.get('id', 'unknown')}: {e}", exc_info=True)
                            continue

                    # Check for next page
                    page_token = result.get("nextPageToken")
                    if not page_token:
                        break

                    self.logger.info(f"Fetched {len(users_data)} users (total so far: {len(all_users)})")

                except Exception as e:
                    self.logger.error(f"Error fetching users page: {e}", exc_info=True)
                    raise

            if not all_users:
                self.logger.warning("No users found in Google Workspace")
                self.synced_users = []
                return

            # Process all users through the data entities processor
            self.logger.info(f"Processing {len(all_users)} users...")
            await self.data_entities_processor.on_new_app_users(all_users)

            # Store users for use in batch processing
            self.synced_users = all_users

            self.logger.info(f"✅ Successfully synced {len(all_users)} users")

        except Exception as e:
            self.logger.error(f"❌ Error syncing users: {e}", exc_info=True)
            raise

    async def _sync_user_groups(self) -> None:
        """Sync user groups and their members from Google Workspace Admin API."""
        try:
            if not self.admin_data_source:
                self.logger.error("Admin data source not initialized. Call init() first.")
                raise ValueError("Admin data source not initialized")

            self.logger.info("Fetching all groups from Google Workspace Admin API...")
            page_token: Optional[str] = None
            total_groups_processed = 0

            while True:
                try:
                    # Fetch groups with pagination
                    result = await self.admin_data_source.groups_list(
                        customer="my_customer",
                        orderBy="email",
                        pageToken=page_token,
                        maxResults=200  # Maximum allowed by Google Admin API
                    )

                    groups_data = result.get("groups", [])
                    if not groups_data:
                        break

                    # Process each group
                    for group in groups_data:
                        try:
                            await self._process_group(group)
                            total_groups_processed += 1
                        except Exception as e:
                            self.logger.error(
                                f"Error processing group {group.get('id', 'unknown')}: {e}",
                                exc_info=True
                            )
                            continue

                    # Check for next page
                    page_token = result.get("nextPageToken")
                    if not page_token:
                        break

                    self.logger.info(f"Processed {len(groups_data)} groups (total so far: {total_groups_processed})")

                except Exception as e:
                    self.logger.error(f"Error fetching groups page: {e}", exc_info=True)
                    raise

            self.logger.info(f"✅ Successfully synced {total_groups_processed} user groups")

        except Exception as e:
            self.logger.error(f"❌ Error syncing user groups: {e}", exc_info=True)
            raise

    async def _process_group(self, group: Dict) -> None:
        """
        Process a single group: fetch members and create AppUserGroup with AppUser objects.

        Args:
            group: Group dictionary from Google Admin API
        """
        try:
            group_id = group.get("email")
            if not group_id:
                self.logger.warning("Skipping group without ID")
                return

            # Fetch members for this group
            self.logger.debug(f"Fetching members for group: {group.get('name', group_id)}")
            members = await self._fetch_group_members(group_id)

            # Filter to only include user members (skip groups and customers)
            user_members = [m for m in members if m.get("type") == "USER"]

            # Create AppUserGroup object
            group_name = group.get("name", "")
            if not group_name:
                group_name = group.get("email", group_id)

            # Convert creation time to epoch milliseconds
            source_created_at = None
            creation_time = group.get("creationTime")
            if creation_time:
                try:
                    source_created_at = parse_timestamp(creation_time)
                except Exception as e:
                    self.logger.warning(f"Failed to parse creation time for group {group_id}: {e}")

            user_group = AppUserGroup(
                source_user_group_id=group_id,
                app_name=self.connector_name,
                connector_id=self.connector_id,
                name=group_name,
                description=group.get("description"),
                source_created_at=source_created_at
            )

            # Create AppUser objects for each member
            app_users: List[AppUser] = []
            for member in user_members:
                try:
                    member_email = member.get("email", "")
                    if not member_email:
                        self.logger.warning(f"Skipping member without email: {member.get('id')}")
                        continue

                    # For group members, we may not have full user details
                    # Use email as fallback for full_name if not available
                    member_id = member.get("id", "")

                    # Try to find user in synced users list for full details
                    full_name = member_email  # Default to email
                    source_created_at_user = None

                    # Look up user in synced users if available
                    if self.synced_users:
                        for synced_user in self.synced_users:
                            if synced_user.source_user_id == member_id or synced_user.email.lower() == member_email.lower():
                                full_name = synced_user.full_name
                                source_created_at_user = synced_user.source_created_at
                                break

                    app_user = AppUser(
                        app_name=self.connector_name,
                        connector_id=self.connector_id,
                        source_user_id=member_id,
                        email=member_email,
                        full_name=full_name,
                        source_created_at=source_created_at_user
                    )
                    app_users.append(app_user)

                except Exception as e:
                    self.logger.error(f"Error processing group member {member.get('id', 'unknown')}: {e}", exc_info=True)
                    continue

            # Send to processor
            if app_users:
                await self.data_entities_processor.on_new_user_groups([(user_group, app_users)])
                self.logger.debug(f"Processed group '{group_name}' with {len(app_users)} members")
            else:
                self.logger.debug(f"Group '{group_name}' has no user members, skipping")

        except Exception as e:
            self.logger.error(f"Error processing group {group.get('id', 'unknown')}: {e}", exc_info=True)
            raise

    async def _fetch_group_members(self, group_id: str) -> List[Dict]:
        """
        Fetch all members of a group with pagination.

        Args:
            group_id: The group ID or email

        Returns:
            List of member dictionaries
        """
        members: List[Dict] = []
        page_token: Optional[str] = None

        while True:
            try:
                result = await self.admin_data_source.members_list(
                    groupKey=group_id,
                    pageToken=page_token,
                    maxResults=200  # Maximum allowed by Google Admin API
                )

                page_members = result.get("members", [])
                if not page_members:
                    break

                members.extend(page_members)

                # Check for next page
                page_token = result.get("nextPageToken")
                if not page_token:
                    break

            except Exception as e:
                self.logger.error(f"Error fetching members for group {group_id}: {e}", exc_info=True)
                raise

        return members

    def _map_drive_role_to_permission_type(self, role: str) -> PermissionType:
        """
        Map Google Drive permission role to PermissionType enum.

        Args:
            role: Google Drive permission role (owner, organizer, fileOrganizer, writer, commenter, reader)

        Returns:
            PermissionType enum value
        """
        role_lower = role.lower()
        if role_lower in ["owner", "organizer"]:
            return PermissionType.OWNER
        elif role_lower in ["fileorganizer", "writer"]:
            return PermissionType.WRITE
        elif role_lower == "commenter":
            return PermissionType.COMMENT
        elif role_lower == "reader":
            return PermissionType.READ
        else:
            # Default to read for unknown roles
            self.logger.warning(f"Unknown Google Drive role '{role}', defaulting to READ")
            return PermissionType.READ

    def _map_drive_permission_type_to_entity_type(self, permission_type: str, email: Optional[str] = None) -> EntityType:
        """
        Map Google Drive permission type to EntityType enum.

        Args:
            permission_type: Google Drive permission type (user, group, domain, anyone, anyoneWithLink)
            email: Optional email address for additional context

        Returns:
            EntityType enum value
        """
        perm_type_lower = permission_type.lower()
        if perm_type_lower == "user":
            return EntityType.USER
        elif perm_type_lower == "group":
            return EntityType.GROUP
        elif perm_type_lower == "domain":
            return EntityType.DOMAIN
        elif perm_type_lower == "anyone":
            return EntityType.ANYONE
        elif perm_type_lower in ["anyonewithlink", "anyone_with_link"]:
            return EntityType.ANYONE_WITH_LINK
        else:
            # Default to user for unknown types
            self.logger.warning(f"Unknown Google Drive permission type '{permission_type}', defaulting to USER")
            return EntityType.USER

    async def _fetch_permissions(
        self,
        resource_id: str,
        is_drive: bool = False,
        user_email: Optional[str] = None,
        drive_data_source: Optional[GoogleDriveDataSource] = None
    ) -> Tuple[List[Permission], bool]:
        """
        Fetch all permissions for a Google Drive resource (file or shared drive) with pagination.

        Args:
            resource_id: The file ID or shared drive ID
            is_drive: Whether this is a shared drive (True) or a file (False)
            user_email: Optional user email for fallback permission if access is denied (files only)
            drive_data_source: Optional drive data source to use (if None, uses self.drive_data_source)

        Returns:
            List of Permission objects and a boolean indicating if the permissions were fallback permissions
        """
        permissions: List[Permission] = []
        page_token: Optional[str] = None
        anyone_with_link_permission_type: Optional[PermissionType] = None

        # Use provided drive_data_source or fall back to service account's data source
        data_source = drive_data_source if drive_data_source else self.drive_data_source

        while True:
            try:
                # Build permissions_list parameters
                params = {
                    "fileId": resource_id,
                    "pageSize": 100,  # Maximum allowed by Google Drive API
                    "pageToken": page_token,
                    "supportsAllDrives": True,
                    "fields": "permissions(id, displayName, type, role, domain, emailAddress, deleted)"
                }

                # Only use domain admin access for shared drives
                if is_drive:
                    params["useDomainAdminAccess"] = True

                result = await data_source.permissions_list(**params)

                permissions_data = result.get("permissions", [])
                if not permissions_data:
                    break

                # Process each permission
                for perm_data in permissions_data:
                    try:
                        # Skip deleted permissions for files, but include them for drives
                        if perm_data.get("deleted", False):
                            continue

                        role = perm_data.get("role", "reader")
                        perm_type = perm_data.get("type", "user")

                        # Map role and type
                        permission_type = self._map_drive_role_to_permission_type(role)
                        entity_type = self._map_drive_permission_type_to_entity_type(perm_type)

                        # Extract email or domain based on permission type
                        email = perm_data.get("emailAddress")
                        perm_data.get("domain")
                        external_id = perm_data.get("id")

                        # Create permission object
                        permission = Permission(
                            email=email if entity_type == EntityType.USER else None,
                            external_id=email if entity_type == EntityType.GROUP else external_id,
                            type=permission_type,
                            entity_type=entity_type
                        )
                        permissions.append(permission)

                        # Track "anyone with link" permission type for fallback
                        if entity_type == EntityType.ANYONE:
                            anyone_with_link_permission_type = permission_type

                    except Exception as e:
                        resource_type = "drive" if is_drive else "file"
                        self.logger.error(
                            f"Error processing permission {perm_data.get('id', 'unknown')} for {resource_type} {resource_id}: {e}",
                            exc_info=True
                        )
                        continue

                # Check for next page
                page_token = result.get("nextPageToken")
                if not page_token:
                    break

            except HttpError as http_error:
                # For drives, always raise the error
                if is_drive:
                    resource_type = "drive"
                    self.logger.error(f"Error fetching permissions for {resource_type} {resource_id}: {http_error}", exc_info=True)
                    raise

                # For files, handle 403 errors with fallback permission
                if http_error.resp.status == HttpStatusCode.FORBIDDEN.value:
                    error_reason = None
                    try:
                        error_details = http_error.error_details if hasattr(http_error, 'error_details') else []
                        if error_details and isinstance(error_details, list):
                            for detail in error_details:
                                if isinstance(detail, dict) and detail.get("reason") == "insufficientFilePermissions":
                                    error_reason = "insufficientFilePermissions"
                                    break
                    except Exception:
                        pass

                    # If it's an insufficient permissions error and we have a user_email, create fallback permission
                    if error_reason == "insufficientFilePermissions" and user_email:
                        # Create a fallback permission with READ access for the current user
                        fallback_permission = Permission(
                            email=user_email,
                            type=PermissionType.READ,
                            entity_type=EntityType.USER
                        )
                        self.logger.info(
                            f"Added single user permission for file {resource_id}: {user_email}"
                        )
                        return ([fallback_permission], True)
                    else:
                        self.logger.error(
                            f"Error fetching permissions for file {resource_id}: {http_error}",
                            exc_info=True
                        )
                        # Return empty list if no fallback available
                        return (permissions, False)
                else:
                    # For other HttpErrors, log and return empty list
                    self.logger.error(f"Error fetching permissions for file {resource_id}: {http_error}", exc_info=True)
                    return (permissions, False)
            except Exception as e:
                resource_type = "drive" if is_drive else "file"
                if is_drive:
                    # For drives, raise the error
                    self.logger.error(f"Error fetching permissions for {resource_type} {resource_id}: {e}", exc_info=True)
                    raise
                else:
                    # For files, return empty list on error instead of raising, to allow processing to continue
                    self.logger.error(f"Error fetching permissions for {resource_type} {resource_id}: {e}", exc_info=True)
                    return (permissions, False)

        # If we found an "anyone with link" permission and have a user_email, create a fallback permission
        if anyone_with_link_permission_type is not None and user_email:
            # Check if user_email is already in the permissions list
            user_already_has_permission = any(
                perm.email == user_email for perm in permissions
            )

            if not user_already_has_permission:
                fallback_permission = Permission(
                    email=user_email,
                    type=anyone_with_link_permission_type,
                    entity_type=EntityType.USER
                )
                self.logger.info("Anyone with link permission found for file")
                return ([fallback_permission], True)

        return (permissions, False)

    async def _create_and_sync_shared_drive_record_group(self, drive: Dict) -> None:
        """
        Create and sync a record group for a single shared drive.

        Args:
            drive: Drive dictionary from Google Drive API
        """
        try:
            drive_id = drive.get("id")
            if not drive_id:
                self.logger.warning("Skipping drive without ID")
                return

            drive_name = drive.get("name", "Unnamed Drive")

            # Fetch permissions for this drive
            self.logger.debug(f"Fetching permissions for drive '{drive_name}' ({drive_id})")
            permissions, _ = await self._fetch_permissions(drive_id, is_drive=True)

            self.logger.info(
                f"Fetched {len(permissions)} permissions for drive '{drive_name}'"
            )

            # Convert creation time to epoch milliseconds
            source_created_at = None
            created_time = drive.get("createdTime")
            if created_time:
                try:
                    source_created_at = parse_timestamp(created_time)
                except Exception as e:
                    self.logger.warning(f"Failed to parse creation time for drive {drive_id}: {e}")

            # Create record group
            record_group = RecordGroup(
                name=drive_name,
                org_id=self.data_entities_processor.org_id,
                external_group_id=drive_id,
                description=drive.get("description", "Shared Drive"),
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                group_type=RecordGroupType.DRIVE,
                source_created_at=source_created_at
            )

            # Submit to processor
            await self.data_entities_processor.on_new_record_groups([(record_group, permissions)])
            self.logger.info(f"Successfully synced record group '{drive_name}' ({drive_id})")

        except Exception as e:
            self.logger.error(
                f"Error creating record group for drive '{drive.get('name', 'unknown')}' ({drive.get('id', 'unknown')}): {e}",
                exc_info=True
            )
            raise

    async def _sync_record_groups(self) -> List[Dict]:
        """Sync record groups (drives) for all users.

        Returns:
            List of shared drive dictionaries
        """
        try:
            if not self.drive_data_source:
                self.logger.error("Drive data source not initialized. Call init() first.")
                raise ValueError("Drive data source not initialized")

            # Step 1: Sync shared drives
            self.logger.info("Fetching all shared drives from Google Drive...")
            all_drives: List[Dict] = []
            page_token: Optional[str] = None

            while True:
                try:
                    # Fetch shared drives with pagination
                    result = await self.drive_data_source.drives_list(
                        pageSize=100,  # Maximum allowed by Google Drive API
                        pageToken=page_token,
                        useDomainAdminAccess=True
                    )

                    drives_data = result.get("drives", [])
                    if not drives_data:
                        break

                    all_drives.extend(drives_data)

                    # Check for next page
                    page_token = result.get("nextPageToken")
                    if not page_token:
                        break

                    self.logger.info(f"Fetched {len(drives_data)} shared drives (total so far: {len(all_drives)})")

                except Exception as e:
                    self.logger.error(f"Error fetching shared drives page: {e}", exc_info=True)
                    raise

            self.logger.info(f"Fetched {len(all_drives)} total shared drives")

            # Process each shared drive
            for drive in all_drives:
                try:
                    await self._create_and_sync_shared_drive_record_group(drive)
                except Exception as e:
                    self.logger.error(
                        f"Failed to sync shared drive '{drive.get('name', 'unknown')}': {e}",
                        exc_info=True
                    )
                    continue

            # Step 2: Create "My Drive" record groups for each user
            self.logger.info("Creating 'My Drive' record groups for users...")
            for user in self.synced_users:
                try:
                    await self._create_personal_record_group(user)
                except Exception as e:
                    self.logger.error(
                        f"Failed to create 'My Drive' record group for user {user.email}: {e}",
                        exc_info=True
                    )
                    continue

            self.logger.info(
                f"✅ Successfully synced {len(all_drives)} shared drives and {len(self.synced_users)} user drives"
            )

            return all_drives

        except Exception as e:
            self.logger.error(f"❌ Error syncing record groups: {e}", exc_info=True)
            raise

    async def _create_personal_record_group(self, user: AppUser) -> None:
        """Create a personal record group for the user's My Drive."""
        try:
            # Create user-specific GoogleClient with impersonation
            user_drive_client = await GoogleClient.build_from_services(
                service_name="drive",
                logger=self.logger,
                config_service=self.config_service,
                is_individual=False,  # Enterprise connector
                version="v3",
                user_email=user.email,  # Impersonate this user
                connector_instance_id=self.connector_id
            )

            # Create user-specific GoogleDriveDataSource from the client
            user_drive_data_source = GoogleDriveDataSource(
                user_drive_client.get_client()
            )

            # Fetch root drive info to get the actual drive ID
            drive_info = await user_drive_data_source.files_get(fileId="root", supportsAllDrives=True)
            drive_id = drive_info.get("id")

            if not drive_id:
                raise HTTPException(
                    status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                    detail=f"Failed to get drive ID for user {user.email}"
                )

            # Create a record group for user's personal drive
            my_drive_record_group = RecordGroup(
                name=f"{user.full_name}'s Drive",
                org_id=self.data_entities_processor.org_id,
                external_group_id=drive_id,
                description="My Drive",
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                group_type=RecordGroupType.DRIVE,
                source_created_at=user.source_created_at
            )

            # Create record group for the user's shared with me files
            shared_with_me_record_group = RecordGroup(
                name=f"{user.full_name}'s Shared with Me",
                org_id=self.data_entities_processor.org_id,
                external_group_id=f"0S:{user.email}",
                description="Shared with Me",
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                group_type=RecordGroupType.DRIVE,
                is_internal=True,
            )

            # Create owner permission for the user
            owner_permission = Permission(
                email=user.email,
                type=PermissionType.OWNER,
                entity_type=EntityType.USER
            )

            # Submit to processor
            await self.data_entities_processor.on_new_record_groups(
                [(my_drive_record_group, [owner_permission]), (shared_with_me_record_group, [owner_permission])]
            )
            self.logger.debug(f"Created 'My Drive' and 'Shared with Me' record groups for user {user.email} with drive ID {drive_id}")

        except Exception as e:
            self.logger.error(
                f"Failed to create 'My Drive' record group for user {user.email}: {e}",
                exc_info=True
            )
            raise

    async def _process_drive_files_batch(
        self,
        files: List[Dict],
        user_id: str,
        user_email: Optional[str],
        drive_id: str,
        is_shared_drive: bool,
        context_name: str,
        batch_records: List,
        batch_count: int,
        total_counter: int,
        drive_data_source: Optional[GoogleDriveDataSource] = None
    ) -> Tuple[List, int, int]:
        """
        Process a batch of files from a drive (shared or user drive).

        Args:
            files: List of file metadata dictionaries
            user_id: The user's account ID (empty string for shared drives)
            user_email: The user's email (None for shared drives)
            drive_id: ID to use as drive ID (drive_id for shared drives, user email for user drives)
            is_shared_drive: Whether this is a shared drive
            context_name: Name for logging context (drive name or user email)
            batch_records: Current batch of records to process
            batch_count: Current batch count
            total_counter: Total counter for tracking processed items

        Returns:
            Tuple of (batch_records, batch_count, total_counter)
        """
        async for record, perms, update in self._process_drive_items_generator(
            files,
            user_id=user_id,
            user_email=user_email,
            drive_id=drive_id,
            is_shared_drive=is_shared_drive,
            drive_data_source=drive_data_source
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
                batch_count += 1
                total_counter += 1

                # Process in batches
                if batch_count >= self.batch_size:
                    self.logger.info(
                        f"💾 Processing batch of {len(batch_records)} records from {context_name}"
                    )
                    await self.data_entities_processor.on_new_records(batch_records)
                    batch_records = []
                    batch_count = 0
                    await asyncio.sleep(0.1)

        return batch_records, batch_count, total_counter

    async def _process_remaining_batch_records(
        self,
        batch_records: List,
        context_name: str
    ) -> Tuple[List, int]:
        """
        Process any remaining records in the batch.

        Args:
            batch_records: Current batch of records to process
            context_name: Name for logging context (drive name or user email)

        Returns:
            Tuple of (empty batch_records, zero batch_count)
        """
        if batch_records:
            self.logger.info(
                f"💾 Processing final batch of {len(batch_records)} records from {context_name}"
            )
            await self.data_entities_processor.on_new_records(batch_records)

        return [], 0

    async def _handle_drive_error(
        self,
        error: Exception,
        drive_name: str,
        drive_id: str,
        error_type: str = "files"
    ) -> bool:
        """
        Handle errors when syncing a shared drive.

        Args:
            error: The exception that occurred
            drive_name: Name of the shared drive
            drive_id: ID of the shared drive
            error_type: Type of error ("files" or "changes")

        Returns:
            True if should continue to next drive, False if should break
        """
        if isinstance(error, HttpError):
            # Check if it's a 403 error due to insufficient drive membership
            if error.resp.status == HttpStatusCode.FORBIDDEN.value:
                error_reason = None
                try:
                    error_details = error.error_details if hasattr(error, 'error_details') else []
                    if error_details and isinstance(error_details, list):
                        for detail in error_details:
                            if isinstance(detail, dict) and detail.get("reason") == "teamDriveMembershipRequired":
                                error_reason = "teamDriveMembershipRequired"
                                break
                except Exception:
                    pass

                if error_reason == "teamDriveMembershipRequired":
                    self.logger.warning(
                        f"Service account does not have access to shared drive '{drive_name}' (ID: {drive_id}). "
                        f"Skipping this drive. The service account needs to be added as a member of the shared drive."
                    )
                    return True  # Break and continue to next drive

            # For other HttpErrors, log and break
            self.logger.error(
                f"Error fetching {error_type} page from drive '{drive_name}': {error}",
                exc_info=True
            )
            return True  # Break and continue to next drive
        else:
            # For other exceptions, log and break
            self.logger.error(
                f"Error fetching {error_type} page from drive '{drive_name}': {error}",
                exc_info=True
            )
            return True  # Break and continue to next drive

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

    async def _process_drive_item(
        self,
        metadata: dict,
        user_id: str,
        user_email: str,
        drive_id: str,
        is_shared_drive: bool = False,
        drive_data_source: Optional[GoogleDriveDataSource] = None
    ) -> Optional[RecordUpdate]:
        """
        Process a single Google Drive file and detect changes.

        Args:
            metadata: Google Drive file metadata dictionary
            user_id: The user's account ID
            user_email: The user's email
            drive_id: The drive ID
            is_shared_drive: Whether this file is from a shared drive

        Returns:
            RecordUpdate object or None if entry should be skipped
        """
        try:

            file_id = metadata.get("id")
            if not file_id:
                return None

            # Apply Date Filters
            if not self._pass_date_filters(metadata):
                self.logger.debug(f"Skipping item {metadata.get('name', 'unknown')} (ID: {file_id}) due to date filters.")
                return None  # Skip this item

            # Apply Extension Filters
            if not self._pass_extension_filter(metadata):
                self.logger.debug(f"Skipping item {metadata.get('name', 'unknown')} (ID: {file_id}) due to extension filters.")
                return None  # Skip this item

            org_id = self.data_entities_processor.org_id

            # Get existing record from the database
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=file_id
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

                parent_external_record_id = (metadata.get("parents") or [None])[0]
                if existing_record and parent_external_record_id != existing_record.parent_external_record_id:
                    is_updated = True
                    metadata_changed = True

            # Determine if it's a file or folder
            mime_type = metadata.get("mimeType", "")
            is_file = mime_type != MimeTypes.GOOGLE_DRIVE_FOLDER.value

            # Determine indexing status - shared files are not indexed by default
            is_shared = metadata.get("shared", False)

            # Check if file is shared with me (user is not owner and file is shared)
            owners = metadata.get("owners", [])
            owner_emails = [owner.get("emailAddress") for owner in owners if owner.get("emailAddress")]
            is_shared_with_me = is_shared and user_email not in owner_emails

            if not is_shared_drive and not is_shared_with_me:

                if existing_record and existing_record.external_record_group_id is None:
                    is_updated = True
                    metadata_changed = True

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
                is_shared_with_me=is_shared_with_me,
                shared_with_me_record_group_id=f"0S:{user_email}" if is_shared_with_me else None,
            )

            if existing_record and not content_changed:
                self.logger.debug(f"No content change for file {file_record.record_name} setting indexing status as prev value")
                file_record.indexing_status = existing_record.indexing_status
                file_record.extraction_status = existing_record.extraction_status

            if is_shared_with_me:
                file_record.external_record_group_id = None

            # Handle Permissions - fetch new permissions
            new_permissions = []
            old_permissions = []

            try:
                # Fetch permissions for this file using the provided drive_data_source
                # If drive_data_source is provided, use it; otherwise fall back to service account
                new_permissions, is_fallback_permissions = await self._fetch_permissions(
                    file_id,
                    is_drive=False,
                    user_email=user_email,
                    drive_data_source=drive_data_source
                )

                if is_fallback_permissions:
                    permissions_changed = False

                    if existing_record:
                        await self.data_entities_processor.add_permission_to_record(existing_record, new_permissions)
                else:
                    permissions_changed = True
                    if existing_record:
                        is_updated = True

            except Exception as e:
                self.logger.warning(
                    f"Failed to fetch permissions for file {file_id} ({metadata.get('name', 'unknown')}): {e}"
                )
                # permissions_changed remains False if fetching fails

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

    async def _process_drive_items_generator(
        self,
        files: List[dict],
        user_id: str,
        user_email: str,
        drive_id: str,
        is_shared_drive: bool = False,
        drive_data_source: Optional[GoogleDriveDataSource] = None
    ) -> AsyncGenerator[Tuple[Optional[FileRecord], List[Permission], RecordUpdate], None]:
        """
        Process Google Drive files and yield records with their permissions.
        Generator for non-blocking processing of large datasets.

        Args:
            files: List of Google Drive file metadata
            user_id: The user's account ID
            user_email: The user's email
            drive_id: The drive ID
            is_shared_drive: Whether these files are from a shared drive
        """
        for file_metadata in files:
            try:
                record_update = await self._process_drive_item(
                    file_metadata,
                    user_id,
                    user_email,
                    drive_id,
                    is_shared_drive=is_shared_drive,
                    drive_data_source=drive_data_source
                )
                if record_update and record_update.record:
                    files_disabled = not self.indexing_filters.is_enabled(IndexingFilterKey.FILES, default=True)
                    shared_disabled = record_update.record.is_shared and not record_update.record.is_shared_with_me and not self.indexing_filters.is_enabled(IndexingFilterKey.SHARED, default=True)
                    shared_with_me_disabled = record_update.record.is_shared_with_me and not self.indexing_filters.is_enabled(IndexingFilterKey.SHARED_WITH_ME, default=True)
                    if files_disabled or shared_disabled or shared_with_me_disabled:
                        record_update.record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                    yield (record_update.record, record_update.new_permissions or [], record_update)
                await asyncio.sleep(0)
            except Exception as e:
                self.logger.error(f"Error processing item in generator: {e}", exc_info=True)
                continue

    async def _handle_record_updates(self, record_update: RecordUpdate) -> None:
        """Handle different types of record updates (new, updated, deleted)."""
        try:
            if record_update.is_deleted:
                await self.data_entities_processor.on_record_deleted(
                    record_id=record_update.external_record_id
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

    async def _run_sync_with_yield(self, user: AppUser) -> None:
        """
        Synchronizes Google Drive files for a given user using page token-based approach.
        Consolidates full sync (files_list) and incremental sync (changes_list) in one method.

        Args:
            user: AppUser object containing email, source_user_id, etc.
        """
        try:
            self.logger.info(f"Starting Google Drive sync for user {user.email}")

            # 1. Create user-specific GoogleClient with impersonation
            user_drive_client = await GoogleClient.build_from_services(
                service_name="drive",
                logger=self.logger,
                config_service=self.config_service,
                is_individual=False,  # Enterprise connector
                version="v3",
                user_email=user.email,  # Impersonate this user
                connector_instance_id=self.connector_id
            )

            # 2. Create user-specific GoogleDriveDataSource from the client
            user_drive_data_source = GoogleDriveDataSource(
                user_drive_client.get_client()
            )

            # 3. Get user info via about_get to get user's permissionId
            fields = 'user(displayName,emailAddress,permissionId)'
            user_about = await user_drive_data_source.about_get(fields=fields)
            user_permission_id = user_about.get('user', {}).get('permissionId')
            user_about.get('user', {}).get('emailAddress')

            if not user_permission_id:
                self.logger.error(f"Failed to get user permissionId for {user.email}")
                return

            drive_info = await user_drive_data_source.files_get(fileId="root", supportsAllDrives=True)
            drive_id = drive_info.get("id")

            if not drive_id:
                raise HTTPException(
                    status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                    detail="Failed to get drive ID"
                )


            # 4-7. Sync personal drive
            await self.sync_personal_drive(
                user=user,
                user_drive_data_source=user_drive_data_source,
                user_permission_id=user_permission_id,
                drive_id=drive_id
            )

            # 8. Sync shared drives that the user is a member of
            await self.sync_shared_drives(
                user=user,
                user_drive_data_source=user_drive_data_source,
                user_permission_id=user_permission_id
            )

            self.logger.info(f"Completed Google Drive sync for user {user.email}")

        except Exception as ex:
            self.logger.error(f"❌ Error in Google Drive sync for user {user.email}: {ex}", exc_info=True)
            raise

    async def sync_personal_drive(
        self,
        user: AppUser,
        user_drive_data_source: GoogleDriveDataSource,
        user_permission_id: str,
        drive_id: str
    ) -> None:
        """
        Synchronizes personal "My Drive" files for a given user.
        Handles both full sync (when no pageToken exists) and incremental sync (when pageToken exists).

        Args:
            user: AppUser object containing email, source_user_id, etc.
            user_drive_data_source: GoogleDriveDataSource instance for the user
            user_permission_id: User's permission ID from Google Drive
            drive_id: Drive ID
        """
        # 4. Generate sync point key
        sync_point_key = generate_record_sync_point_key(
            RecordType.DRIVE.value,
            "users",
            user.source_user_id
        )

        # 5. Read sync point to get pageToken (if exists)
        sync_point = await self.drive_delta_sync_point.read_sync_point(sync_point_key)
        page_token = sync_point.get("pageToken") if sync_point else None

        self.logger.info(f"Sync point key: {sync_point_key}")
        self.logger.info(f"Page token: {page_token[:20] if page_token else 'None'}...")

        batch_records = []
        batch_count = 0

        # 6. If pageToken doesn't exist (full sync)
        if not page_token:
            self.logger.info(f"🆕 Starting full sync for user {user.email}")

            # Get start page token for future incremental syncs
            start_token_response = await user_drive_data_source.changes_get_start_page_token()
            start_page_token = start_token_response.get("startPageToken")

            if not start_page_token:
                self.logger.error(f"Failed to get start page token for user {user.email}")
                return

            self.logger.info(f"📋 Start page token: {start_page_token[:20]}...")

            # Fetch all files with pagination using files_list
            current_page_token = None
            total_files = 0

            while True:
                # Prepare files_list parameters
                list_params = {
                    "fields": "nextPageToken, files(id, name, mimeType, size, createdTime, modifiedTime, webViewLink, fileExtension, headRevisionId, version, shared, owners, md5Checksum, sha1Checksum, sha256Checksum, parents)",
                }

                if current_page_token:
                    list_params["pageToken"] = current_page_token

                # Fetch files
                self.logger.info(f"📥 Fetching files page (token: {current_page_token[:20] if current_page_token else 'initial'}...)")
                files_response = await user_drive_data_source.files_list(**list_params)

                files = files_response.get("files", [])

                if not files:
                    self.logger.info("No more files to process")
                    break

                # Process files using common helper method
                batch_records, batch_count, total_files = await self._process_drive_files_batch(
                    files=files,
                    user_id=user_permission_id,
                    user_email=user.email,
                    drive_id=drive_id,
                    is_shared_drive=False,
                    context_name=f"user {user.email}",
                    batch_records=batch_records,
                    batch_count=batch_count,
                    total_counter=total_files,
                    drive_data_source=user_drive_data_source
                )

                # Check for next page
                current_page_token = files_response.get("nextPageToken")
                if not current_page_token:
                    break

            # Process remaining records
            batch_records, batch_count = await self._process_remaining_batch_records(
                batch_records, f"user {user.email}"
            )

            # Save start page token to sync point after initial sync
            await self.drive_delta_sync_point.update_sync_point(
                sync_point_key,
                {"pageToken": start_page_token}
            )

            self.logger.info(f"✅ Full sync completed for user {user.email}. Processed {total_files} files. Saved page token: {start_page_token[:20]}...")

        # 7. If pageToken exists (incremental sync)
        else:
            self.logger.info(f"🔄 Starting incremental sync for user {user.email}")

            current_page_token = page_token
            total_changes = 0

            while True:
                # Prepare changes_list parameters
                changes_params = {
                    "pageToken": current_page_token,
                    "pageSize": 1000,
                    "includeRemoved": True,
                    "restrictToMyDrive": False,  # Include shared files
                    "supportsAllDrives": True,
                    "includeItemsFromAllDrives": False,  # Exclude shared drives, only get "shared with me" files
                    "fields": "nextPageToken, newStartPageToken, changes(fileId, removed, file(id, name, mimeType, size, createdTime, modifiedTime, webViewLink, fileExtension, headRevisionId, version, shared, owners, md5Checksum, sha1Checksum, sha256Checksum, parents))",
                }

                # Fetch changes
                self.logger.info(f"📥 Fetching changes page (token: {current_page_token[:20]}...)")
                changes_response = await user_drive_data_source.changes_list(**changes_params)

                changes = changes_response.get("changes", [])

                # Extract files from changes
                files = []
                for change in changes:
                    is_removed = change.get("removed", False)
                    file_metadata = change.get("file")

                    if is_removed:
                        existing_record = None
                        async with self.data_store_provider.transaction() as tx_store:
                            existing_record = await tx_store.get_record_by_external_id(
                                connector_id=self.connector_id,
                                external_id=change.get("fileId")
                            )

                        self.logger.info(f"Removing permission from record {existing_record.record_name} for user {user.email}")

                        if existing_record and existing_record.id:
                            await self.data_entities_processor.delete_permission_from_record(
                                    record_id=existing_record.id,
                                    user_email=user.email
                                )

                    if file_metadata:
                        files.append(file_metadata)

                # Process files using common helper method (only if there are files)
                if files:
                    batch_records, batch_count, total_changes = await self._process_drive_files_batch(
                        files=files,
                        user_id=user_permission_id,
                        user_email=user.email,
                        drive_id=drive_id,
                        is_shared_drive=False,
                        context_name=f"user {user.email}",
                        batch_records=batch_records,
                        batch_count=batch_count,
                        total_counter=total_changes,
                        drive_data_source=user_drive_data_source
                    )

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
            batch_records, batch_count = await self._process_remaining_batch_records(
                batch_records, f"user {user.email}"
            )

            # Update sync point with latest page token
            if current_page_token and current_page_token != page_token:
                self.logger.info(f"💾 Updating sync point from {page_token[:20]}... to {current_page_token[:20]}...")
                await self.drive_delta_sync_point.update_sync_point(
                    sync_point_key,
                    {"pageToken": current_page_token}
                )
                self.logger.info(f"✅ Incremental sync completed for user {user.email}. Processed {total_changes} changes.")
            else:
                self.logger.info("Sync point not updated (token unchanged)")

    async def sync_shared_drives(
        self,
        user: AppUser,
        user_drive_data_source: GoogleDriveDataSource,
        user_permission_id: str
    ) -> None:
        """
        Synchronizes shared drives that the user is a member of.
        Handles both full sync and incremental sync for each shared drive.

        Args:
            user: AppUser object containing email, source_user_id, etc.
            user_drive_data_source: GoogleDriveDataSource instance for the user
            user_permission_id: User's permission ID from Google Drive
        """
        self.logger.info(f"Syncing shared drives for user {user.email}")
        try:
            # List all shared drives the user has access to
            all_user_drives: List[Dict] = []
            page_token: Optional[str] = None

            while True:
                try:
                    # Fetch shared drives with pagination
                    drives_response = await user_drive_data_source.drives_list(
                        pageSize=100,
                        pageToken=page_token
                    )

                    drives_data = drives_response.get("drives", [])
                    if not drives_data:
                        break

                    all_user_drives.extend(drives_data)

                    # Check for next page
                    page_token = drives_response.get("nextPageToken")
                    if not page_token:
                        break

                except Exception as e:
                    should_break = await self._handle_drive_error(e, "shared drives list", "", "drives_list")
                    if should_break:
                        break

            if not all_user_drives:
                self.logger.info(f"No shared drives found for user {user.email}")
            else:
                self.logger.info(f"Found {len(all_user_drives)} shared drives for user {user.email}")

                # Process each shared drive
                for drive in all_user_drives:
                    drive_id = drive.get("id")
                    drive_name = drive.get("name", "Unnamed Drive")

                    if not drive_id:
                        self.logger.warning(f"Skipping drive without ID: {drive_name}")
                        continue

                    try:
                        self.logger.info(f"Syncing shared drive '{drive_name}' (ID: {drive_id}) for user {user.email}")

                        # Generate sync point key for this specific user-drive pair
                        sync_point_key = generate_record_sync_point_key(
                            RecordType.DRIVE.value,
                            "drives",
                            f"{user.source_user_id}_{drive_id}"
                        )

                        # Read sync point to get pageToken (if exists)
                        sync_point = await self.drive_delta_sync_point.read_sync_point(sync_point_key)
                        page_token = sync_point.get("pageToken") if sync_point else None

                        self.logger.info(f"Sync point key: {sync_point_key}")
                        self.logger.info(f"Page token: {page_token[:20] if page_token else 'None'}...")

                        batch_records = []
                        batch_count = 0

                        # If pageToken doesn't exist (full sync)
                        if not page_token:
                            self.logger.info(f"🆕 Starting full sync for shared drive '{drive_name}' for user {user.email}")

                            # Get start page token for future incremental syncs (with driveId)
                            start_token_response = await user_drive_data_source.changes_get_start_page_token(
                                driveId=drive_id,
                                supportsAllDrives=True
                            )
                            start_page_token = start_token_response.get("startPageToken")

                            if not start_page_token:
                                self.logger.error(f"Failed to get start page token for shared drive '{drive_name}' (ID: {drive_id}) for user {user.email}")
                                continue

                            self.logger.info(f"📋 Start page token: {start_page_token[:20]}...")

                            # Fetch all files with pagination using files_list
                            current_page_token = None
                            total_files = 0

                            while True:
                                try:
                                    # Prepare files_list parameters for shared drive
                                    list_params = {
                                        "driveId": drive_id,
                                        "corpora": "drive",
                                        "supportsAllDrives": True,
                                        "includeItemsFromAllDrives": True,  # Required when driveId is specified
                                        "fields": "nextPageToken, files(id, name, mimeType, size, createdTime, modifiedTime, webViewLink, fileExtension, headRevisionId, version, shared, owners, md5Checksum, sha1Checksum, sha256Checksum, parents)",
                                    }

                                    if current_page_token:
                                        list_params["pageToken"] = current_page_token

                                    # Fetch files from shared drive
                                    self.logger.info(
                                        f"📥 Fetching files page from drive '{drive_name}' for user {user.email} "
                                        f"(token: {current_page_token[:20] if current_page_token else 'initial'}...)"
                                    )
                                    files_response = await user_drive_data_source.files_list(**list_params)

                                    files = files_response.get("files", [])

                                    if not files:
                                        self.logger.info(f"No more files to process from drive '{drive_name}'")
                                        break

                                    # Process files using common helper method
                                    batch_records, batch_count, total_files = await self._process_drive_files_batch(
                                        files=files,
                                        user_id=user_permission_id,
                                        user_email=user.email,
                                        drive_id=drive_id,  # Use drive ID as external record group ID
                                        is_shared_drive=True,
                                        context_name=f"drive '{drive_name}' for user {user.email}",
                                        batch_records=batch_records,
                                        batch_count=batch_count,
                                        total_counter=total_files,
                                        drive_data_source=user_drive_data_source
                                    )

                                    # Check for next page
                                    current_page_token = files_response.get("nextPageToken")
                                    if not current_page_token:
                                        break

                                except Exception as e:
                                    should_break = await self._handle_drive_error(e, drive_name, drive_id, "files")
                                    if should_break:
                                        break

                            # Process remaining records
                            batch_records, batch_count = await self._process_remaining_batch_records(
                                batch_records, f"drive '{drive_name}' for user {user.email}"
                            )

                            # Save start page token to sync point after initial sync
                            await self.drive_delta_sync_point.update_sync_point(
                                sync_point_key,
                                {"pageToken": start_page_token}
                            )

                            self.logger.info(
                                f"✅ Full sync completed for shared drive '{drive_name}' for user {user.email}. "
                                f"Processed {total_files} files. Saved page token: {start_page_token[:20]}..."
                            )

                        # If pageToken exists (incremental sync)
                        else:
                            self.logger.info(f"🔄 Starting incremental sync for shared drive '{drive_name}' for user {user.email}")

                            current_page_token = page_token
                            total_changes = 0

                            while True:
                                try:
                                    # Prepare changes_list parameters for shared drive (with driveId)
                                    changes_params = {
                                        "pageToken": current_page_token,
                                        "driveId": drive_id,  # Specify the shared drive
                                        "pageSize": 1000,
                                        "includeRemoved": True,
                                        "restrictToMyDrive": False,  # Include shared files
                                        "supportsAllDrives": True,
                                        "includeItemsFromAllDrives": True,
                                        "fields": "nextPageToken, newStartPageToken, changes(fileId, removed, file(id, name, mimeType, size, createdTime, modifiedTime, webViewLink, fileExtension, headRevisionId, version, shared, owners, md5Checksum, sha1Checksum, sha256Checksum, parents))",
                                    }

                                    # Fetch changes
                                    self.logger.info(
                                        f"📥 Fetching changes page from drive '{drive_name}' for user {user.email} "
                                        f"(token: {current_page_token[:20]}...)"
                                    )
                                    changes_response = await user_drive_data_source.changes_list(**changes_params)

                                    changes = changes_response.get("changes", [])

                                    # Extract files from changes
                                    files = []
                                    for change in changes:
                                        is_removed = change.get("removed", False)
                                        file_metadata = change.get("file")

                                        if is_removed:
                                            # Handle deleted files
                                            file_id = change.get("fileId")
                                            if file_id:
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
                                            files.append(file_metadata)

                                    # Process files using common helper method (only if there are files)
                                    if files:
                                        batch_records, batch_count, total_changes = await self._process_drive_files_batch(
                                            files=files,
                                            user_id=user_permission_id,
                                            user_email=user.email,
                                            drive_id=drive_id,  # Use drive ID as external record group ID
                                            is_shared_drive=True,
                                            context_name=f"drive '{drive_name}' for user {user.email}",
                                            batch_records=batch_records,
                                            batch_count=batch_count,
                                            total_counter=total_changes,
                                            drive_data_source=user_drive_data_source
                                        )

                                    # Get next page token
                                    next_page_token = changes_response.get("nextPageToken")
                                    new_start_page_token = changes_response.get("newStartPageToken")

                                    if next_page_token:
                                        # More pages to fetch
                                        current_page_token = next_page_token
                                        self.logger.info(
                                            f"📄 More pages available for drive '{drive_name}', "
                                            f"continuing with token: {current_page_token[:20]}..."
                                        )
                                    elif new_start_page_token:
                                        # Sync complete, save the new start token for next sync
                                        current_page_token = new_start_page_token
                                        self.logger.info(
                                            f"✅ Sync complete for drive '{drive_name}', "
                                            f"new start token: {current_page_token[:20]}..."
                                        )
                                        break
                                    else:
                                        self.logger.warning(
                                            f"⚠️ No nextPageToken or newStartPageToken found for drive '{drive_name}'"
                                        )
                                        break

                                except Exception as e:
                                    should_break = await self._handle_drive_error(e, drive_name, drive_id, "changes")
                                    if should_break:
                                        break

                            # Process remaining records
                            batch_records, batch_count = await self._process_remaining_batch_records(
                                batch_records, f"drive '{drive_name}' for user {user.email}"
                            )

                            # Update sync point with latest page token
                            if current_page_token and current_page_token != page_token:
                                self.logger.info(
                                    f"💾 Updating sync point for drive '{drive_name}' "
                                    f"from {page_token[:20]}... to {current_page_token[:20]}..."
                                )
                                await self.drive_delta_sync_point.update_sync_point(
                                    sync_point_key,
                                    {"pageToken": current_page_token}
                                )
                                self.logger.info(
                                    f"✅ Incremental sync completed for shared drive '{drive_name}' for user {user.email}. "
                                    f"Processed {total_changes} changes."
                                )
                            else:
                                self.logger.warning(
                                    f"⚠️ Sync point not updated for drive '{drive_name}' "
                                    f"(token unchanged or invalid)"
                                )

                    except Exception as e:
                        error_reason = None
                        error_details = e.error_details if hasattr(e, 'error_details') else []
                        if error_details and isinstance(error_details, list):
                            for detail in error_details:
                                if isinstance(detail, dict) and detail.get("reason") == "teamDriveMembershipRequired":
                                    error_reason = "teamDriveMembershipRequired"
                                    break

                        if error_reason == "teamDriveMembershipRequired":
                            self.logger.warning(
                                f"⚠️ User {user.email} does not have access to shared drive '{drive_name}' (ID: {drive_id}). "
                                f"Skipping this drive."
                            )
                        else:
                            self.logger.error(
                                f"❌ Error syncing files from shared drive '{drive_name}' (ID: {drive_id}) for user {user.email}: {e}",
                                exc_info=True
                            )
                        # Continue processing other drives
                        continue

                self.logger.info(f"✅ Completed syncing shared drives for user {user.email}")

        except Exception as e:
            self.logger.error(f"❌ Error syncing shared drives for user {user.email}: {e}", exc_info=True)
            # Don't raise - continue with user sync completion

    async def _process_users_in_batches(self, users: List[AppUser]) -> None:
        """
        Process user drives in concurrent batches for improved performance.

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
                    self._run_sync_with_yield(user)
                    for user in batch
                ]

                await asyncio.gather(*sync_tasks, return_exceptions=True)

                # Small delay between batches to prevent overwhelming the API
                await asyncio.sleep(1)

            self.logger.info("Completed processing all user batches")

        except Exception as e:
            self.logger.error(f"❌ Error processing users in batches: {e}", exc_info=True)
            raise

    async def test_connection_and_access(self) -> bool:
        """Test connection and access to Google Drive enterprise account."""
        try:
            self.logger.info("Testing connection and access to Google Drive enterprise account")
            if not self.drive_data_source:
                self.logger.error("Drive data source not initialized. Call init() first.")
                return False

            if not self.admin_data_source:
                self.logger.error("Admin data source not initialized. Call init() first.")
                return False

            if not self.drive_client or not self.admin_client:
                self.logger.warning("Google clients not initialized")
                return False

            return True
        except Exception as e:
            self.logger.error(f"❌ Error testing connection and access to Google Drive enterprise account: {e}")
            return False

    def get_signed_url(self, record: Record) -> Optional[str]:
        """Get a signed URL for a specific record."""
        raise NotImplementedError("get_signed_url is not yet implemented for Google Drive enterprise")

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
            downloader = MediaIoBaseDownload(buffer, request)
            done = False

            while not done:
                try:
                    _, done = downloader.next_chunk()
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

                # Yield control back to event loop
                await asyncio.sleep(0)
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

    async def _get_file_metadata_from_drive(self, file_id: str, drive_service) -> Dict:
        """
        Get file metadata from Google Drive API.

        Args:
            file_id: Google Drive file ID
            drive_service: Google Drive service client

        Returns:
            Dictionary with file metadata including mimeType
        """
        try:
            file_metadata = drive_service.files().get(
                fileId=file_id,
                fields="id,name,mimeType",
                supportsAllDrives=True  # ADD THIS for Shared Drive support
            ).execute()
            return file_metadata
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

    async def _get_drive_service_for_user(self, user_email: Optional[str] = None) -> object:
        """
        Get the appropriate Google Drive service client with user impersonation.

        Args:
            user_email: Optional user email to impersonate

        Returns:
            Google Drive service client
        """
        if user_email:
            # Use user impersonation
            self.logger.info(f"Using user impersonation for user: {user_email}")
            try:
                user_drive_client = await GoogleClient.build_from_services(
                    service_name="drive",
                    logger=self.logger,
                    config_service=self.config_service,
                    is_individual=False,  # Enterprise connector
                    version="v3",
                    user_email=user_email,  # Impersonate this user
                    connector_instance_id=self.connector_id
                )

                self.logger.info(f"User-specific client created for {user_email}")
                return user_drive_client.get_client()
            except Exception as e:
                self.logger.error(f"Failed to create user-specific client for {user_email}: {e}")
                # Fall back to service account
                self.logger.warning("Falling back to service account client")

        # If no user_email provided or impersonation fails, use service account
        if not self.drive_client:
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail="Drive client not initialized"
            )
        self.logger.info("Using service account drive client")
        return self.drive_client.get_client()

    async def stream_record(self, record: Record, user_id: Optional[str] = None, convertTo: Optional[str] = None) -> StreamingResponse:
        """
        Stream a record from Google Drive.

        Args:
            record: Record object containing file information
            user_id: Optional user ID to use for impersonation
            convertTo: Optional format to convert to (e.g., "application/pdf")

        Returns:
            StreamingResponse with file content
        """
        try:
            file_id = record.external_record_id
            file_name = record.record_name

            if not file_id:
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_REQUEST.value,
                    detail="File ID not found in record"
                )
            self.logger.info(f"Streaming Drive file: {file_id}, convertTo: {convertTo}")

            # Get user email from user_id if provided, otherwise get user with permission to node
            user_email = None
            if user_id and user_id != "None":
                async with self.data_store_provider.transaction() as tx_store:
                    user = await tx_store.get_user_by_user_id(user_id)
                    if user:
                        user_email = user.get("email")
                        self.logger.info(f"Retrieved user email {user_email} for user_id {user_id}")
                    else:
                        self.logger.warning(f"User not found for user_id {user_id}, trying to get user with permission to node")
                        # Fall through to get user with permission
            else:
                self.logger.info("user_id not provided or is None, getting user with permission to node")

            # If we don't have user_email yet, get user with permission to the node
            if not user_email:
                user_with_permission = None
                async with self.data_store_provider.transaction() as tx_store:
                    user_with_permission = await tx_store.get_first_user_with_permission_to_node(
                        record.id, CollectionNames.RECORDS.value
                    )
                if user_with_permission:
                    user_email = user_with_permission.email
                    self.logger.info(f"Retrieved user email {user_email} from user with permission to node")
                else:
                    self.logger.warning(f"No user found with permission to node: {record.id}, falling back to service account")

            drive_service = await self._get_drive_service_for_user(user_email)

            # Get file metadata with Shared Drive support
            file_metadata = await self._get_file_metadata_from_drive(file_id, drive_service)
            mime_type = file_metadata.get("mimeType", "application/octet-stream")

            google_workspace_export_formats = {
                "application/vnd.google-apps.spreadsheet": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/vnd.google-apps.document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/vnd.google-apps.presentation": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            }

            # PDF conversion for Google Workspace files
            if convertTo == MimeTypes.PDF.value and mime_type in google_workspace_export_formats:
                self.logger.info(f"Exporting Google Workspace file ({mime_type}) directly to PDF")

                request = drive_service.files().export_media(
                    fileId=file_id,
                    mimeType="application/pdf"
                    # Note: export_media doesn't need supportsAllDrives
                )
                return create_stream_record_response(
                    self._stream_google_api_request(request, error_context="PDF export"),
                    filename=file_name,
                    mime_type="application/pdf",
                    fallback_filename=f"record_{record.id}",
                )


            # Regular export for Google Workspace files
            if mime_type in google_workspace_export_formats:
                export_mime_type = google_workspace_export_formats[mime_type]
                self.logger.info(f"Exporting Google Workspace file ({mime_type}) to {export_mime_type}")

                request = drive_service.files().export_media(
                    fileId=file_id,
                    mimeType=export_mime_type
                )

                export_media_types = {
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
                }

                response_media_type, file_ext = export_media_types.get(export_mime_type, (export_mime_type, ""))
                file_name_with_ext = file_name if file_name.endswith(file_ext) else f"{file_name}{file_ext}"

                return create_stream_record_response(
                    self._stream_google_api_request(request, error_context="Google Workspace file export"),
                    filename=file_name_with_ext,
                    mime_type=response_media_type,
                    fallback_filename=f"record_{record.id}",
                )


            # PDF conversion for regular files
            if convertTo == MimeTypes.PDF.value:
                self.logger.info(f"Converting file to PDF: {file_name}")
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_file_path = os.path.join(temp_dir, file_name)

                    try:
                        with open(temp_file_path, "wb") as f:
                            request = drive_service.files().get_media(
                                fileId=file_id,
                                supportsAllDrives=True  # ADDED
                            )
                            downloader = MediaIoBaseDownload(f, request)

                            done = False
                            while not done:
                                status, done = downloader.next_chunk()
                                self.logger.info(f"Download {int(status.progress() * 100)}%.")
                    except HttpError as http_error:
                        if http_error.resp.status == HttpStatusCode.FORBIDDEN.value:
                            error_details = http_error.error_details if hasattr(http_error, 'error_details') else []
                            for detail in error_details:
                                if detail.get('reason') == 'fileNotDownloadable':
                                    self.logger.error(
                                        f"Google Workspace file cannot be downloaded for PDF conversion: {str(http_error)}"
                                    )
                                    raise HTTPException(
                                        status_code=HttpStatusCode.BAD_REQUEST.value,
                                        detail="Google Workspace files (Sheets, Docs, Slides) cannot be converted to PDF using direct download.",
                                    )
                        raise

                    pdf_path = await self._convert_to_pdf(temp_file_path, temp_dir)
                    self.logger.info(f"PDF file converted: {pdf_path}")

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


            # Regular file download - WITH supportsAllDrives
            request = drive_service.files().get_media(
                fileId=file_id,
                supportsAllDrives=True  # ADDED - This is the key fix!
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

    async def run_incremental_sync(self) -> None:
        """Run incremental sync for Google Drive enterprise."""
        raise NotImplementedError("run_incremental_sync is not yet implemented for Google Drive enterprise")

    def handle_webhook_notification(self, notification: Dict) -> None:
        """Handle webhook notifications from Google Drive."""
        raise NotImplementedError("handle_webhook_notification is not yet implemented for Google Drive enterprise")

    async def reindex_records(self, records: List[Record]) -> None:
        """Reindex records for Google Drive enterprise."""
        try:
            if not records:
                self.logger.info("No records to reindex")
                return

            self.logger.info(f"Starting reindex for {len(records)} Google Drive enterprise records")

            if not self.drive_data_source:
                self.logger.error("Drive data source not initialized. Call init() first.")
                raise Exception("Drive data source not initialized. Call init() first.")

            # Check records at source for updates
            org_id = self.data_entities_processor.org_id
            updated_records = []
            non_updated_records = []
            for record in records:
                try:
                    updated_record_data = await self._check_and_fetch_updated_record(org_id, record)
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
            self.logger.error(f"Error during Google Drive enterprise reindex: {e}", exc_info=True)
            raise

    async def _check_and_fetch_updated_record(
        self, org_id: str, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch record from Google Drive and return data for reindexing if changed."""
        try:
            file_id = record.external_record_id
            record_group_id = record.external_record_group_id

            if not file_id:
                self.logger.warning(f"Missing file_id for record {record.id}")
                return None

            # Get user with permission to the node
            user_with_permission = None
            async with self.data_store_provider.transaction() as tx_store:
                user_with_permission = await tx_store.get_first_user_with_permission_to_node(
                    record.id, CollectionNames.RECORDS.value
                )

            if not user_with_permission:
                self.logger.warning(f"No user found with permission to node: {record.id}")
                return None

            user_email = user_with_permission.email
            if not user_email:
                self.logger.warning(f"User found but email is missing for record {record.id}")
                return None

            # Create drive service with user impersonation
            drive_service = await self._get_drive_service_for_user(user_email)

            # Wrap drive service in GoogleDriveDataSource to use files_get method
            user_drive_data_source = GoogleDriveDataSource(drive_service)

            # Get user information (permissionId) from the user-specific drive service
            fields = 'user(displayName,emailAddress,permissionId)'
            user_about = await user_drive_data_source.about_get(fields=fields)
            user_id = user_about.get('user', {}).get('permissionId')
            user_email_from_api = user_about.get('user', {}).get('emailAddress')

            if not user_id:
                self.logger.warning(f"Failed to get user permissionId for {user_email}")
                # Fallback to using source_user_id if available
                user_id = user_with_permission.source_user_id
                if not user_id:
                    self.logger.warning(f"Could not determine user_id for record {record.id}")
                    return None

            # Use user_email from API if available, otherwise use the one from database
            if user_email_from_api:
                user_email = user_email_from_api

            # Fetch fresh file from Google Drive API
            try:
                file_metadata = await user_drive_data_source.files_get(
                    fileId=file_id,
                    supportsAllDrives=True
                )
            except HttpError as e:
                if e.resp.status == HttpStatusCode.NOT_FOUND.value:
                    self.logger.warning(f"File {file_id} not found at source")
                    return None
                raise

            if not file_metadata:
                self.logger.warning(f"File {file_id} not found at source")
                return None

            # Determine if it's a shared drive (check if driveId is present in metadata)
            is_shared_drive = 'driveId' in file_metadata

            # Use existing logic to detect changes and transform to FileRecord
            record_update = await self._process_drive_item(
                file_metadata,
                user_id,
                user_email,
                record_group_id,
                is_shared_drive=is_shared_drive,
                drive_data_source=user_drive_data_source
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
            self.logger.error(f"Error checking Google Drive enterprise record {record.id} at source: {e}")
            return None



    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> FilterOptionsResponse:
        """Google Drive enterprise connector does not support dynamic filter options."""
        raise NotImplementedError("Google Drive enterprise connector does not support dynamic filter options")

    async def cleanup(self) -> None:
        """Cleanup resources when shutting down the connector."""
        try:
            self.logger.info("Cleaning up Google Drive enterprise connector resources")

            # Clear data source references
            if hasattr(self, 'drive_data_source') and self.drive_data_source:
                self.drive_data_source = None

            if hasattr(self, 'admin_data_source') and self.admin_data_source:
                self.admin_data_source = None

            # Clear client references
            if hasattr(self, 'drive_client') and self.drive_client:
                self.drive_client = None

            if hasattr(self, 'admin_client') and self.admin_client:
                self.admin_client = None

            # Clear config
            self.config = None

            self.logger.info("Google Drive enterprise connector cleanup completed")

        except Exception as e:
            self.logger.error(f"❌ Error during cleanup: {e}")

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str
    ) -> BaseConnector:
        """Create a new instance of the Google Drive enterprise connector."""
        data_entities_processor = DataSourceEntitiesProcessor(
            logger,
            data_store_provider,
            config_service
        )
        await data_entities_processor.initialize()

        return GoogleDriveTeamConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id
        )
