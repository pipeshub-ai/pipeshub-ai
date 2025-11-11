import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from logging import Logger
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

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
    AuthField,
    ConnectorBuilder,
    DocumentationLink,
)
from app.connectors.sources.bookstack.common.apps import BookStackApp
from app.models.entities import (
    AppRole,
    AppUser,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.bookstack.bookstack import (
    BookStackClient,
    BookStackResponse,
    BookStackTokenConfig,
)
from app.sources.external.bookstack.bookstack import BookStackDataSource


@dataclass
class RecordUpdate:
    """Track updates to a record"""
    record: Optional[FileRecord]
    is_new: bool
    is_updated: bool
    is_deleted: bool
    metadata_changed: bool
    content_changed: bool
    permissions_changed: bool
    old_permissions: Optional[List[Permission]] = None
    new_permissions: Optional[List[Permission]] = None
    external_record_id: Optional[str] = None

@ConnectorBuilder("BookStack")\
    .in_group("BookStack")\
    .with_auth_type("API_TOKEN")\
    .with_description("Sync content from your BookStack instance")\
    .with_categories(["Knowledge Management"])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/bookstack.svg")\
        .add_documentation_link(DocumentationLink(
            "BookStack API Docs",
            "https://demo.bookstackapp.com/api/docs",
            "docs"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/bookstack/bookstack',
            'pipeshub'
        ))
        .with_redirect_uri("", False)
        .add_auth_field(AuthField(
            name="base_url",
            display_name="Base URL",
            placeholder="https://bookstack.example.com",
            description="The base URL of your BookStack instance",
            field_type="TEXT",
            max_length=2048
        ))
        .add_auth_field(AuthField(
            name="token_id",
            display_name="Token ID",
            placeholder="YourTokenID",
            description="The Token ID generated from your BookStack profile",
            field_type="TEXT",
            max_length=100
        ))
        .add_auth_field(AuthField(
            name="token_secret",
            display_name="Token Secret",
            placeholder="YourTokenSecret",
            description="The Token Secret generated from your BookStack profile",
            field_type="PASSWORD",
            max_length=100,
            is_secret=True
        ))
        .with_scheduled_config(True, 60)
    )\
    .build_decorator()
class BookStackConnector(BaseConnector):
    """
    Connector for synchronizing data from a BookStack instance.
    Syncs books, chapters, pages, attachments, users, roles, and permissions.
    """
    bookstack_base_url: str

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
    ) -> None:
        super().__init__(
            BookStackApp(),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service
        )

        self.connector_name = Connectors.BOOKSTACK

        # Initialize sync points for tracking changes
        self._create_sync_points()

        # Data source and configuration
        self.data_source: Optional[BookStackDataSource] = None
        self.batch_size = 100
        self.max_concurrent_batches = 5

    def _create_sync_points(self) -> None:
        """Initialize sync points for different data types."""
        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_name=self.connector_name,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider
            )

        self.record_sync_point = _create_sync_point(SyncDataPointType.RECORDS)
        self.user_sync_point = _create_sync_point(SyncDataPointType.USERS)
        self.app_role_sync_point = _create_sync_point(SyncDataPointType.GROUPS)

    async def init(self) -> bool:
        """
        Initializes the BookStack client using credentials from the config service.

        Returns:
            True if initialization was successful, False otherwise
        """
        try:
            config = await self.config_service.get_config(
                "/services/connectors/bookstack/config"
            )
            if not config:
                self.logger.error("BookStack configuration not found.")
                return False

            credentials_config = config.get("auth", {})

            # Read the correct keys required by BookStackTokenConfig
            self.bookstack_base_url = credentials_config.get("base_url")
            base_url = credentials_config.get("base_url")
            token_id = credentials_config.get("token_id")
            token_secret = credentials_config.get("token_secret")

            if not all([base_url, token_id, token_secret]):
                self.logger.error(
                    "BookStack base_url, token_id, or token_secret not found in configuration."
                )
                return False

            # Initialize BookStack client with the correct config fields
            token_config = BookStackTokenConfig(
                base_url=base_url,
                token_id=token_id,
                token_secret=token_secret
            )
            try:
                client = await BookStackClient.build_and_validate(token_config)
            except ValueError as e:
                self.logger.error(f"Failed to initialize BookStack client: {e}", exc_info=True)
                return False
            self.data_source = BookStackDataSource(client)

            self.logger.info("BookStack client initialized successfully.")
            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize BookStack client: {e}", exc_info=True)
            return False

    async def test_connection_and_access(self) -> bool:
        """
        Tests the connection to BookStack by attempting to list books.

        Returns:
            True if connection test was successful, False otherwise
        """
        if not self.data_source:
            self.logger.error("BookStack data source not initialized")
            return False

        try:
            # Try to list books with minimal count to test API access
            response = await self.data_source.list_books(count=1)
            if response.success:
                self.logger.info("BookStack connection test successful.")
                return True
            else:
                self.logger.error(f"BookStack connection test failed: {response.error}")
                return False

        except Exception as e:
            self.logger.error(f"BookStack connection test failed: {e}", exc_info=True)
            return False

    async def get_signed_url(self, record: Record) -> Optional[str]:
        """
        Get a signed URL for accessing a BookStack record.
        BookStack doesn't use signed URLs, so we return the web URL.

        Args:
            record: The record to get URL for

        Returns:
            The web URL of the record or None
        """
        signed_url = f"{self.bookstack_base_url}api/pages/{record.external_record_id}/export/markdown"
        if not record.weburl:
            self.logger.warning(f"No web URL found for record {record.id}")
            return None

        return signed_url

    async def stream_record(self, record: Record) -> StreamingResponse:
        """
        Stream the content of a BookStack record.

        Args:
            record: The record to stream

        Returns:
            StreamingResponse with the record content

        Raises:
            HTTPException if record cannot be streamed
        """
        if not self.data_source:
            raise HTTPException(
                status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
                detail="BookStack connector not initialized"
            )

        record_id = record.external_record_id.split('/')[1]
        markdown_response = await self.data_source.export_page_markdown(record_id)
        if not markdown_response.success:
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail="Record not found or access denied"
            )
        raw_markdown = markdown_response.data.get("markdown")
        # Stream the content from the URL
        return StreamingResponse(
            raw_markdown,
            media_type=record.mime_type if record.mime_type else "application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename={record.record_name}"
            }
        )

    def _get_app_users(self, users: List[Dict]) -> List[AppUser]:
        """Converts a list of BookStack user dictionaries to a list of AppUser objects."""
        app_users: List[AppUser] = []
        for user in users:
            app_users.append(
                AppUser(
                    app_name=self.connector_name,
                    source_user_id=str(user.get("id")),
                    full_name=user.get("name"),
                    email=user.get("email"),
                    is_active=True,  # Assuming users returned from the API are active
                    title=None,      # Role/title is not provided in the /api/users endpoint
                )
            )
        return app_users

    async def get_all_users(self) -> List[AppUser]:
        """
        Fetches all users from BookStack, transforms them into AppUser objects,
        """
        self.logger.info("Starting BookStack user sync...")
        all_bookstack_users = []
        offset = 0

        # Loop to handle API pagination and fetch all users
        while True:
            response = await self.data_source.list_users(
                count=self.batch_size,
                offset=offset
            )

            if not response.success or not response.data:
                self.logger.error(f"Failed to fetch users from BookStack: {response.error}")
                break

            users_page = response.data.get("data", [])
            if not users_page:
                # No more users to fetch, exit the loop
                break

            all_bookstack_users.extend(users_page)
            offset += len(users_page)

            # Stop if we have fetched all available users
            if offset >= response.data.get("total", 0):
                break

        if not all_bookstack_users:
            self.logger.warning("No users found in BookStack instance.")
            return []

        self.logger.info(f"Successfully fetched {len(all_bookstack_users)} users from BookStack.")

        # 1. Convert the raw user data to the standardized AppUser format
        app_users = self._get_app_users(all_bookstack_users)

        return app_users

    async def list_roles_with_details(self) -> Dict[int, Dict]:
        """
        Gets a list of all roles with their detailed information, including
        permissions and assigned users.

        Returns:
            A dictionary where the key is the role_id and the value is the
            dictionary containing the role's full details.
        """
        self.logger.info("Fetching all roles with details...")

        # First, get the basic list of all roles
        list_response = await self.data_source.list_roles()
        if not list_response.success:
            self.logger.error(f"Failed to list roles: {list_response.error}")
            return {}

        basic_roles = list_response.data.get('data', [])
        if not basic_roles:
            self.logger.info("No roles found in BookStack.")
            return {}

        # Create a list of concurrent tasks to get the details for each role
        tasks = [self.data_source.get_role(role['id']) for role in basic_roles]

        # Execute all tasks in parallel and wait for them to complete
        detail_responses = await asyncio.gather(*tasks, return_exceptions=True)

        # Process the results into the final dictionary
        roles_details_map = {}
        for i, response in enumerate(detail_responses):
            role_id = basic_roles[i]['id']
            if isinstance(response, Exception):
                self.logger.error(f"Exception fetching details for role ID {role_id}: {response}")
            elif not response.success:
                self.logger.error(f"Failed to get details for role ID {role_id}: {response.error}")
            else:
                roles_details_map[role_id] = response.data

        self.logger.info(f"Successfully fetched details for {len(roles_details_map)} roles.")
        return roles_details_map

    def _parse_id_and_name_from_event(self, event: Dict) -> Tuple[Optional[int], Optional[str]]:
        """
        Parses an audit log event's 'detail' string to extract the entity's ID and name.
        The expected format is '(ID) Name', e.g., '(5) Tester'.
        """
        detail = event.get('detail')
        if not detail:
            self.logger.warning(f"Audit log event (ID: {event.get('id')}) is missing 'detail'.")
            return None, None

        try:
            # Split the string just once at the first space
            id_part, name_part = detail.split(' ', 1)

            # Clean up and convert the parts
            entity_id = int(id_part.strip('()'))
            entity_name = name_part.strip()

            return entity_id, entity_name

        except (ValueError, IndexError):
            self.logger.error(f"Could not parse ID and name from audit log detail: '{detail}'")
            return None, None

    def _get_iso_time(self) -> str:
        # Get the current time in UTC
        utc_now = datetime.now(timezone.utc)

        # Format the time into the ISO 8601 string format with 'Z'
        iso_format_string = utc_now.strftime('%Y-%m-%dT%H:%M:%SZ')
        return iso_format_string

    async def run_sync(self) -> None:
        """
        Runs a full synchronization from the BookStack instance.
        Syncs users, groups, record groups (books/shelves), and records (pages/chapters).
        """
        try:
            self.logger.info("Starting BookStack full sync.")

            # Step 1: Sync all users
            self.logger.info("Syncing users...")
            await self._sync_users()

            # Step 2: Sync all user groups (roles in BookStack)
            self.logger.info("Syncing user groups (roles)...")
            await self._sync_user_roles()

            # Step 3: Sync record groups (books and shelves)
            self.logger.info("Syncing record groups (chapters/books/shelves)...")
            await self._sync_record_groups()

            # Step 4: Sync all records (pages, chapters, attachments)
            self.logger.info("Syncing records (pages/chapters)...")
            await self._sync_records()

            self.logger.info("BookStack full sync completed.")

        except Exception as ex:
            self.logger.error(f"Error in BookStack connector run: {ex}", exc_info=True)
            raise

    #-------------------------------Users Sync-----------------------------------#
    async def _sync_users(self, full_sync: bool = False) -> None:

        current_timestamp = self._get_iso_time()
        bookstack_user_sync_key = generate_record_sync_point_key("bookstack", "user_logs", "global")
        bookstack_user_sync_point = await self.user_sync_point.read_sync_point(bookstack_user_sync_key)

        users = await self.get_all_users()


        #if no sync point, initialize cursor and run _sync_users else run _sync_users_incremental
        if full_sync or not bookstack_user_sync_point.get('timestamp'):
            await self.user_sync_point.update_sync_point(
                bookstack_user_sync_key,
                {"timestamp": current_timestamp}
            )
            await self._sync_users_full(users)
        else:
            last_sync_timestamp = bookstack_user_sync_point.get('timestamp')
            await self._sync_users_incremental(users, last_sync_timestamp)
            await self.user_sync_point.update_sync_point(
                bookstack_user_sync_key,
                {"timestamp": current_timestamp}
            )

    async def _sync_users_full(self, app_users: List[AppUser]) -> None:
        """
        Fetches all users from BookStack, transforms them into AppUser objects,
        and upserts them into the database.
        """
        self.logger.info("Starting BookStack user sync...")

        # Pass the AppUser objects to the data processor to save in ArangoDB
        await self.data_entities_processor.on_new_app_users(app_users)
        self.logger.info("✅ Finished syncing BookStack users.")

    async def _sync_users_incremental(self, app_users: List[AppUser], last_sync_timestamp: str) -> None:
        """
        Syncs only the users that have been newly created since the last sync timestamp
        by checking the audit log.
        """
        self.logger.info(f"Starting incremental user sync from timestamp: {last_sync_timestamp}")

        # 1. Fetch 'user_create' events from the audit log
        user_create_response = await self.data_source.list_audit_log(
            filter={'type': 'user_create', 'created_at:gte': last_sync_timestamp}
        )
        user_update_response = await self.data_source.list_audit_log(
            filter={'type': 'user_update', 'created_at:gte': last_sync_timestamp}
        )
        user_delete_response = await self.data_source.list_audit_log(
            filter={'type': 'user_delete', 'created_at:gte': last_sync_timestamp}
        )

        if user_create_response.success and user_create_response.data.get('data'):
            await self._handle_user_create_event(user_create_response.data.get('data'), app_users)
        if user_update_response.success and user_update_response.data.get('data'):
            await self._handle_user_update_event(user_update_response.data.get('data'), app_users)
        if user_delete_response.success and user_delete_response.data.get('data'):
            # await self._handle_user_delete_event(user_delete_response.data.get('data'), app_users)
            self.logger.info("Method not implemenet yet !")

    async def _handle_user_create_event(self, user_create_events: List[Dict], app_users: List[AppUser]) -> None:
        self.logger.info("New users found !")
        # 2. Parse the audit log to get the IDs of newly created users
        new_user_ids = set()
        log_entries = user_create_events
        for entry in log_entries:
            detail_string = entry.get('detail')
            if not detail_string:
                continue

            try:
                # The detail string is formatted as '(ID) Name', e.g., '(5) Harshit'
                id_part, _ = detail_string.split(' ', 1)
                user_id_str = id_part.strip('()')

                # Ensure the extracted part is a digit before adding
                if user_id_str.isdigit():
                    new_user_ids.add(user_id_str)
            except (ValueError, IndexError):
                self.logger.warning(f"Could not parse user ID from audit log detail: '{detail_string}'")

        if not new_user_ids:
            self.logger.info("Audit log checked, but no new users were found to sync.")
            return

        self.logger.info(f"Found {len(new_user_ids)} new user(s) in audit log with IDs: {new_user_ids}")

        # 3. Filter the full list of `app_users` to find the new user objects
        newly_created_users = [
            user for user in app_users if user.source_user_id in new_user_ids
        ]

        # 4. If any new users were found, send them to the data processor
        if newly_created_users:
            self.logger.info(f"Submitting {len(newly_created_users)} newly created users for processing.")
            await self.data_entities_processor.on_new_app_users(newly_created_users)
            self.logger.info("✅ Finished syncing new BookStack users.")
        else:
            self.logger.warning("Found new user IDs in audit log, but no matching user objects were found in the full user list.")

    async def _handle_user_update_event(self, user_update_events: List[Dict], app_users: List[AppUser]) -> None:
        self.logger.info("Updated users found !")

        # First handle the user update itself (updates the user record)
        await self._handle_user_create_event(user_update_events, app_users)

        # Build user email map for role updates
        user_email_map = {
            int(user.source_user_id): user.email
            for user in app_users
            if user.source_user_id and user.email
        }

        # Process each user update event
        for event in user_update_events:
            user_id, name = self._parse_id_and_name_from_event(event)

            if user_id is None:
                self.logger.warning(f"Could not parse user ID from event: {event}")
                continue

            self.logger.info(f"Processing role updates for user: {name} (ID: {user_id})")

            try:
                # Get the updated user details to find their roles
                user_response = await self.data_source.get_user(user_id)

                if not user_response.success or not user_response.data:
                    self.logger.error(f"Failed to fetch details for user ID {user_id}: {user_response.error}")
                    continue

                user_details = user_response.data
                roles = user_details.get('roles', [])

                user_email = user_details.get('email')
                if not user_email:
                    self.logger.warning(f"User {name} (ID: {user_id}) has no email, skipping role updates.")
                    continue

                # Delete edges to groups for the user
                async with self.data_store_provider.transaction() as tx_store:
                    user = await tx_store.get_user_by_email(user_email)
                    if not user:
                        self.logger.warning(f"User {name} (ID: {user_id}) not found in the database, skipping role updates.")
                        continue
                    user_key = f"{CollectionNames.USERS.value}/{user.id}"
                    await tx_store.delete_edges_between_collections(user_key, CollectionNames.PERMISSION.value, CollectionNames.ROLES.value)

                if not roles:
                    self.logger.info(f"User {name} (ID: {user_id}) has no roles assigned.")
                    continue

                # Resync each role the user is part of
                self.logger.info(f"User {name} is part of {len(roles)} role(s). Resyncing...")

                for role in roles:
                    role_id = role.get('id')
                    role_name = role.get('display_name')

                    if role_id:
                        self.logger.info(f"Resyncing role '{role_name}' (ID: {role_id}) due to user update")
                        await self._handle_role_create_event(role_id, user_email_map)
                    else:
                        self.logger.warning(f"Role '{role_name}' has no ID, skipping resync")

            except Exception as e:
                self.logger.error(f"Error processing user update for user ID {user_id}: {e}", exc_info=True)

        self.logger.info("✅ Finished processing user update events and role resyncs")


    async def _handle_user_delete_event(self, user_delete_events: List[Dict], app_users: List[AppUser]) -> None:
        self.logger.info("Deleted users found !")

        for event in user_delete_events:
            user_id, name = self._parse_id_and_name_from_event(event)

            if user_id is None:
                self.logger.warning(f"Could not parse user ID from delete event: {event}")
                continue

            self.logger.info(f"Processing deletion for user: {name} (ID: {user_id})")

            try:
                # Fetch user by user_id using tx_store
                async with self.data_store_provider.transaction() as tx_store:
                    # Look up the user by their external ID (source_user_id)
                    user = await tx_store.get_user_by_user_id(
                        user_id=str(user_id)
                    )

                    if not user:
                        self.logger.warning(
                            f"User with BookStack ID {user_id} ({name}) not found in database. "
                            "May have been already deleted or never synced."
                        )
                        continue

                    user_email = user.get("email")

                # If user is found, call the data processor to handle removal
                if user_email:
                    self.logger.info(
                        f"Deleting user {name} (BookStack ID: {user_id}, email: {user_email})"
                    )

                    # Call data_entities_processor to remove the user
                    success = await self.data_entities_processor.on_user_removed(
                        user_email=user_email,
                        connector_name=self.connector_name
                    )

                    if success:
                        self.logger.info(
                            f"✅ Successfully deleted user {name} (email: {user_email})"
                        )
                    else:
                        self.logger.error(
                            f"Failed to delete user {name} (email: {user_email}) from database"
                        )
                else:
                    self.logger.warning(
                        f"User {name} (ID: {user_id}) found but has no email address"
                    )

            except Exception as e:
                self.logger.error(
                    f"Error processing user deletion for user ID {user_id}: {e}",
                    exc_info=True
                )

        self.logger.info("✅ Finished processing user delete events")

    #-------------------------------User Groups Sync-----------------------------------#
    async def _sync_user_roles(self, full_sync:bool = False) -> None:
        current_timestamp = self._get_iso_time()
        bookstack_user_role_sync_key = generate_record_sync_point_key("bookstack", "user_role_logs", "global")
        bookstack_user_role_sync_point = await self.app_role_sync_point.read_sync_point(bookstack_user_role_sync_key)

        #if no sync point, initialize cursor and run _sync_users else run _sync_users_incremental
        if full_sync or not bookstack_user_role_sync_point.get('timestamp'):
            await self.app_role_sync_point.update_sync_point(
                bookstack_user_role_sync_key,
                {"timestamp": current_timestamp}
            )
            await self._sync_user_roles_full()
        else:
            last_sync_timestamp = bookstack_user_role_sync_point.get('timestamp')
            await self._sync_user_roles_incremental(last_sync_timestamp)
            await self.app_role_sync_point.update_sync_point(
                bookstack_user_role_sync_key,
                {"timestamp": current_timestamp}
            )


    async def _sync_user_roles_full(self) -> None:
        """
        Fetches all roles with their detailed user assignments to build and
        upsert user groups with their member users (AppUser objects).
        """
        self.logger.info("Starting BookStack user group and permissions sync...")

        # 1. Fetch details for all roles, including the users assigned to each role.
        all_roles_with_details = await self._fetch_all_roles_with_details()
        if not all_roles_with_details:
            self.logger.warning("No roles found in BookStack. Aborting user group sync.")
            return
        self.logger.info(f"Found {len(all_roles_with_details)} total roles with details.")

        # 2. Fetch all user details to get complete user information
        all_users_with_details = await self._fetch_all_users_with_details()

        # Create a map for quick user lookup by ID
        user_details_map = {
            user.get("id"): user for user in all_users_with_details
        }

        # 3. Build the final batch for the data processor.
        roles_batch = []
        for role in all_roles_with_details:
            # Create the AppRole object for the role.
            app_role = AppRole(
                app_name=self.connector_name,
                source_role_id=str(role.get("id")),
                name=role.get("display_name"),
                org_id=self.data_entities_processor.org_id
            )

            # Build list of AppUser objects for users in this role
            app_users = []
            for user in role.get("users", []):
                user_id = user.get("id")
                user_details = user_details_map.get(user_id)

                if not user_details:
                    self.logger.warning(f"No details found for user ID {user_id} in role {role.get('display_name')}")
                    continue

                # Create AppUser object from the user details
                app_user = AppUser(
                    app_name=self.connector_name,
                    source_user_id=str(user_id),
                    email=user_details.get("email", ""),
                    full_name=user_details.get("name", ""),
                    org_id=self.data_entities_processor.org_id,
                    is_active=True,  # Assuming users in roles are active
                    title=user_details.get("title") if user_details.get("title") else None,
                    source_created_at=self._parse_timestamp(user_details.get("created_at")) if user_details.get("created_at") else None,
                    source_updated_at=self._parse_timestamp(user_details.get("updated_at")) if user_details.get("updated_at") else None
                )
                app_users.append(app_user)

            roles_batch.append((app_role, app_users))

        # 4. Send the complete batch to the processor.
        if roles_batch:
            self.logger.info(f"Submitting {len(roles_batch)} roles with their users...")
            await self.data_entities_processor.on_new_app_roles(roles_batch)
            self.logger.info("✅ Successfully processed app roles and permissions.")
        else:
            self.logger.info("No app roles were processed.")


    def _parse_timestamp(self, timestamp_str: str) -> Optional[int]:
        """Helper to parse timestamp string to epoch milliseconds."""
        if not timestamp_str:
            return None
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            return int(dt.timestamp() * 1000)
        except Exception as e:
            self.logger.warning(f"Failed to parse timestamp '{timestamp_str}': {e}")
            return None

    async def _fetch_all_roles_with_details(self) -> List[Dict]:
        """
        Helper to fetch all roles and then get their detailed profiles,
        including assigned users, concurrently.
        """
        # First, get the basic list of all roles to find their IDs.
        self.logger.info("Fetching basic list of all roles...")
        basic_roles = await self._fetch_all_roles()
        if not basic_roles:
            return []

        # Asynchronously fetch the full details for each role in parallel.
        self.logger.info(f"Fetching details for {len(basic_roles)} roles concurrently...")
        tasks = [self.data_source.get_role(role['id']) for role in basic_roles]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        detailed_roles = []
        for i, res in enumerate(responses):
            if isinstance(res, Exception) or not res.success:
                role_id = basic_roles[i]['id']
                self.logger.error(f"Failed to get details for role ID {role_id}: {res}")
            else:
                detailed_roles.append(res.data)

        self.logger.info(f"Successfully fetched details for {len(detailed_roles)} roles.")
        return detailed_roles

    def _build_role_permissions_map(
        self,
        all_roles_with_details: List[Dict],
        user_email_map: Dict[int, str]
    ) -> Dict[int, List[Permission]]:
        """
        Creates a map of role_id -> [List of Permissions] from detailed role objects.

        Args:
            all_roles_with_details: A list of role dictionaries, each containing a 'users' key.
            user_email_map: A lookup dictionary mapping user IDs to their email addresses.

        Returns:
            A dictionary where each key is a role_id and the value is a list of
            Permission objects for users belonging to that role.
        """
        self.logger.info("Building map of roles to user permissions...")
        role_to_permissions_map = {}
        for role in all_roles_with_details:
            role_id = role.get("id")
            if not role_id:
                continue

            permissions = []
            for user in role.get("users", []):
                user_id = user.get("id")
                user_email = user_email_map.get(user_id)

                if not user_id or not user_email:
                    self.logger.warning(f"Skipping user in role '{role.get('display_name')}' due to missing ID or email.")
                    continue

                # Create a permission object linking this user to this role.
                permission = Permission(
                    external_id=str(user_id),
                    email=user_email,
                    type=PermissionType.WRITE, # Defaulting to WRITE as membership implies access
                    entity_type=EntityType.GROUP
                )
                permissions.append(permission)

            if permissions:
                role_to_permissions_map[role_id] = permissions

        self.logger.info(f"Permission map built for {len(role_to_permissions_map)} roles.")
        return role_to_permissions_map

    async def _fetch_all_roles(self) -> List[Dict]:
        """Helper to fetch all roles with pagination."""
        self.logger.info("Fetching all roles from BookStack...")
        all_roles = []
        offset = 0
        while True:
            response = await self.data_source.list_roles(count=self.batch_size, offset=offset)
            if not response.success or not response.data:
                self.logger.error(f"Failed to fetch roles: {response.error}")
                break
            roles_page = response.data.get("data", [])
            if not roles_page:
                break
            all_roles.extend(roles_page)
            offset += len(roles_page)
            if offset >= response.data.get("total", 0):
                break
        return all_roles

    async def _fetch_all_users_with_details(self) -> List[Dict]:
        """Helper to fetch all users and then get detailed profiles concurrently."""
        self.logger.info("Fetching all user IDs...")
        all_users_summary = []
        offset = 0
        while True:
            response = await self.data_source.list_users(count=self.batch_size, offset=offset)
            if not response.success or not response.data:
                self.logger.error(f"Failed to list users: {response.error}")
                break
            users_page = response.data.get("data", [])
            if not users_page:
                break
            all_users_summary.extend(users_page)
            offset += len(users_page)
            if offset >= response.data.get("total", 0):
                break

        if not all_users_summary:
            return []

        self.logger.info(f"Fetching details for {len(all_users_summary)} users concurrently...")
        tasks = [self.data_source.get_user(user['id']) for user in all_users_summary]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        detailed_users = []
        for i, res in enumerate(responses):
            if isinstance(res, Exception) or not res.success:
                user_id = all_users_summary[i]['id']
                self.logger.error(f"Failed to get details for user ID {user_id}: {res}")
            else:
                detailed_users.append(res.data)

        self.logger.info(f"Successfully fetched details for {len(detailed_users)} users.")
        return detailed_users

    async def _sync_user_roles_incremental(self, last_sync_timestamp: str) -> None:
        """
        Sync user groups and permissions incrementally based on the last sync timestamp.
        """
        self.logger.info("Starting BookStack user group sync incremental...")

        roles_create_events = await self.data_source.list_audit_log(
            filter={
                'type': 'role_create',
                'created_at:gte': last_sync_timestamp
            }
        )
        role_update_events = await self.data_source.list_audit_log(
            filter={
                'type': 'role_update',
                'created_at:gte': last_sync_timestamp
            }
        )
        role_delete_events = await self.data_source.list_audit_log(
            filter={
                'type': 'role_delete',
                'created_at:gte': last_sync_timestamp
            }
        )

        all_users = await self._fetch_all_users_with_details()
        user_email_map = {user.get("id"): user.get("email") for user in all_users}

        if roles_create_events.success and roles_create_events.data and roles_create_events.data.get('data'):
            for event in roles_create_events.data['data']:
                role_id, _ = self._parse_id_and_name_from_event(event)
                await self._handle_role_create_event(role_id, user_email_map)

        if role_update_events.success and role_update_events.data and role_update_events.data.get('data'):
            for event in role_update_events.data['data']:
                role_id, _ = self._parse_id_and_name_from_event(event)
                await self._handle_role_update_event(role_id, user_email_map)

        if role_delete_events.success and role_delete_events.data and role_delete_events.data.get('data'):
            for event in role_delete_events.data['data']:
                role_id, _ = self._parse_id_and_name_from_event(event)
                await self._handle_role_delete_event(role_id)
                await self._sync_user_roles_full()

    async def _handle_role_create_event(self, role_id: int, user_email_map: Dict[int, str]) -> None:
        """
        Handles a 'role_create' audit log event by fetching the new role's
        details and its members, then sending it to the data processor.
        """

        if role_id is None:
            # This can happen if the parser function fails
            return

        self.logger.info(f"Role created/updated (ID: {role_id}). Fetching details...")

        # Fetch the full details of the newly created role
        role_details_response = await self.data_source.get_role(role_id)
        if not role_details_response.success or not role_details_response.data:
            self.logger.warning(f"Could not fetch details for role ID {role_id}, it may not be available yet. Skipping.")
            return

        role_details = role_details_response.data

        # Create the AppRole object
        app_role = AppRole(
            app_name=self.connector_name,
            source_role_id=str(role_id),
            name=role_details.get("display_name"),
            org_id=self.data_entities_processor.org_id
        )

        # Build the list of AppUser objects for members of this group
        app_users = []
        role_users = role_details.get("users", [])

        if role_users:
            # Fetch detailed user information for each user in the role
            user_ids = [user.get("id") for user in role_users if user.get("id")]

            # Fetch user details in parallel
            tasks = [self.data_source.get_user(user_id) for user_id in user_ids]
            user_responses = await asyncio.gather(*tasks, return_exceptions=True)

            for i, res in enumerate(user_responses):
                if isinstance(res, Exception) or not res.success or not res.data:
                    self.logger.warning(f"Failed to get details for user ID {user_ids[i]}: {res}")
                    continue

                user_details = res.data
                user_id = user_details.get("id")

                # Create AppUser object from the fetched details
                app_user = AppUser(
                    app_name=self.connector_name,
                    source_user_id=str(user_id),
                    email=user_details.get("email", ""),
                    full_name=user_details.get("name", ""),
                    org_id=self.data_entities_processor.org_id,
                    is_active=True,  # Users in roles are assumed to be active
                    title=user_details.get("title") if user_details.get("title") else None,
                    source_created_at=self._parse_timestamp(user_details.get("created_at")) if user_details.get("created_at") else None,
                    source_updated_at=self._parse_timestamp(user_details.get("updated_at")) if user_details.get("updated_at") else None
                )
                app_users.append(app_user)

        # Process the new group and its members
        self.logger.info(f"Processing newly created role '{app_role.name}' with {len(app_users)} members...")
        await self.data_entities_processor.on_new_app_roles([(app_role, app_users)])

    async def _handle_role_update_event(self, role_id: int, user_email_map: Dict[int, str]) -> None:
        await self._handle_role_delete_event(role_id)
        await self._handle_role_create_event(role_id, user_email_map)
        await self._sync_record_groups(full_sync=True)
        await self._sync_records(full_sync=True)


    async def _handle_role_delete_event(self, role_id: int) -> None:
        """
        Handles a 'role_delete' audit log event by parsing the role ID
        and calling the data processor to delete the corresponding user group.
        """
        self.logger.info(f"Processing deletion for user group (External ID: {role_id})")

        # Call the data processor to delete the group from the database
        await self.data_entities_processor.on_app_role_deleted(
            external_role_id=str(role_id),
            connector_name=self.connector_name
        )

    #-------------------------------Record Groups Sync-----------------------------------#
    async def _sync_record_groups(self, full_sync:bool = False) -> None:

        current_timestamp = self._get_iso_time()
        bookstack_record_group_sync_key = generate_record_sync_point_key("bookstack", "record_groups", "global")
        bookstack_record_group_sync_point = await self.record_sync_point.read_sync_point(bookstack_record_group_sync_key)

        roles_details = await self.list_roles_with_details()

        #if no sync point, initialize cursor and run _sync_users else run _sync_users_incremental
        if full_sync or not bookstack_record_group_sync_point.get('timestamp'):
            await self._sync_record_groups_full(roles_details)
            await self.record_sync_point.update_sync_point(
                bookstack_record_group_sync_key,
                {"timestamp": current_timestamp}
            )
        else:
            last_sync_timestamp = bookstack_record_group_sync_point.get('timestamp')
            await self._sync_record_groups_incremental(last_sync_timestamp, roles_details)
            await self.record_sync_point.update_sync_point(
                bookstack_record_group_sync_key,
                {"timestamp": current_timestamp}
            )

    async def _sync_record_groups_full(self, roles_details: Dict[int, Dict]) -> None:
        """
        Sync all record groups (shelves, books, and chapters) from BookStack
        and their associated permissions.
        """
        self.logger.info("Starting sync for shelves, books, and chapters as record groups.")

        # Sync all shelves as record groups
        await self._sync_content_type_as_record_group(
            content_type_name="bookshelf",
            list_method=self.data_source.list_shelves,
            roles_details=roles_details
        )

        # Sync all books as record groups
        await self._sync_content_type_as_record_group(
            content_type_name="book",
            list_method=self.data_source.list_books,
            roles_details=roles_details
        )

        # Sync all chapters as record groups
        await self._sync_content_type_as_record_group(
            content_type_name="chapter",
            list_method=self.data_source.list_chapters,
            roles_details=roles_details
        )

        self.logger.info("✅ Finished syncing all record groups.")

    async def _sync_content_type_as_record_group(
        self,
        content_type_name: str,
        list_method: Callable[..., Awaitable[BookStackResponse]],
        roles_details: Dict[int, Dict]
    ) -> None:
        """
        Generic function to fetch a list of content items (like shelves, books),
        and sync them as record groups with permissions.
        """
        self.logger.info(f"Starting sync for {content_type_name}s as record groups.")
        all_items = []
        offset = 0

        # Paginate through all items of the given content type
        while True:
            response = await list_method(count=self.batch_size, offset=offset)
            if not response.success or not response.data:
                self.logger.error(f"Failed to fetch {content_type_name}s: {response.error}")
                break

            items_page = response.data.get("data", [])
            if not items_page:
                break

            all_items.extend(items_page)
            offset += len(items_page)
            if offset >= response.data.get("total", 0):
                break

        if not all_items:
            self.logger.info(f"No {content_type_name}s found to sync.")
            return

        self.logger.info(f"Found {len(all_items)} {content_type_name}s. Fetching permissions...")

        # Concurrently create RecordGroup and fetch permissions for each item
        tasks = []
        for item in all_items:
            parent_external_id = None
            # A chapter's parent is always a book
            if content_type_name == "chapter" and item.get("book_id"):
                parent_external_id = f"book/{item.get('book_id')}"

            tasks.append(
                self._create_record_group_with_permissions(item, content_type_name, roles_details, parent_external_id)
            )
        results = await asyncio.gather(*tasks)

        # Filter out any tasks that failed (returned None)
        record_groups_batch = [res for res in results if res is not None]

        if record_groups_batch:
            await self.data_entities_processor.on_new_record_groups(record_groups_batch)
            self.logger.info(
                f"✅ Successfully processed {len(record_groups_batch)} {content_type_name} record groups."
            )
        else:
            self.logger.warning(f"No {content_type_name} record groups were processed.")

    async def _create_record_group_with_permissions(
        self, item: Dict, content_type_name: str, roles_details: Dict[int, Dict], parent_external_id: Optional[str] = None
    ) -> Optional[Tuple[RecordGroup, List[Permission]]]:
        """Creates a RecordGroup and fetches its permissions for a single BookStack item."""
        try:
            item_id = item.get("id")
            item_name = item.get("name")
            if not all([item_id, item_name]):
                self.logger.warning(f"Skipping {content_type_name} due to missing id or name: {item}")
                return None

            # 1. Create the RecordGroup object
            record_group = RecordGroup(
                name=item_name,
                org_id=self.data_entities_processor.org_id,
                external_group_id=f"{content_type_name}/{item_id}",
                description=item.get("description", ""),
                connector_name=self.connector_name,
                group_type=RecordGroupType.KB,
                parent_external_group_id=parent_external_id,
                inherit_permissions=parent_external_id is not None,
            )

            # 2. Fetch its permissions
            permissions_response = await self.data_source.get_content_permissions(
                content_type=content_type_name, content_id=item_id
            )

            permissions_list = []
            if permissions_response.success and permissions_response.data:
                permissions_list = await self._parse_bookstack_permissions(permissions_response.data, roles_details, content_type_name)

                fallback_permissions = permissions_response.data.get("fallback_permissions")
                if fallback_permissions and parent_external_id:
                    # Set inherit_permissions based on the 'inheriting' flag
                    record_group.inherit_permissions = fallback_permissions.get("inheriting", False)
            else:
                self.logger.warning(
                    f"Failed to fetch permissions for {content_type_name} '{item_name}' (ID: {item_id}): "
                    f"{permissions_response.error}"
                )

            return (record_group, permissions_list)

        except Exception as e:
            item_name = item.get("name", "N/A")
            item_id = item.get("id", "N/A")
            self.logger.error(
                f"Error processing {content_type_name} '{item_name}' (ID: {item_id}): {e}",
                exc_info=True
            )
            return None

    async def _parse_bookstack_permissions(self, permissions_data: Dict, roles_details: Dict[int, Dict], content_type_name: str) -> List[Permission]:
        """
        Parses the BookStack permission object into a list of Permission objects.
        If explicit role permissions are not set on an item, it calculates permissions
        based on the default role definitions.
        """
        permissions_list = []

        # 1. Handle the owner (user permission), which is always explicit
        owner = permissions_data.get("owner")
        if owner and owner.get("id"):
            try:
                user_response = await self.data_source.get_user(owner.get("id"))
                if user_response.success and user_response.data.get("email"):
                    permissions_list.append(
                        Permission(
                            external_id=str(owner.get("id")),
                            email=user_response.data.get("email"),
                            type=PermissionType.OWNER,
                            entity_type=EntityType.USER
                        )
                    )
            except Exception as e:
                self.logger.error(f"Failed to fetch owner details for user ID {owner.get('id')}: {e}")

        # 2. Handle role permissions (group permissions)
        role_permissions = permissions_data.get("role_permissions", [])

        # CASE A: Explicit permissions are set on the content item
        if role_permissions:
            for role_perm in role_permissions:
                role_id = role_perm.get("role_id")
                if not role_id:
                    continue

                # Determine permission level from explicit settings
                if role_perm.get("update") or role_perm.get("delete") or role_perm.get("create"):
                    perm_type = PermissionType.WRITE
                elif role_perm.get("view"):
                    perm_type = PermissionType.READ
                else:
                    continue

                permissions_list.append(
                    Permission(
                        external_id=str(role_id),
                        type=perm_type,
                        entity_type=EntityType.ROLE
                    )
                )
        #IMPORTANT: If in fallback_permisions inheriting is true that means the permissions will follow from the parent record_group
        #So either we can replicate the permissions of it's parent or not create the permission's edge at all (it will fetch the records from inherited permissions)
        #Need to check if view/Create/Delete/Update permissions are set and how to handle it
        #Maybe if inherit permissions are set true and its a book then we can create edges otherwise skip it
        # CASE B: fall back to default role permissions
        fallback_permissions = permissions_data.get("fallback_permissions", {})
        inheriting_bool = fallback_permissions.get("inheriting", False)
        if content_type_name == "book" and inheriting_bool:
            for role_id, role_details in roles_details.items():
                role_system_permissions = set(role_details.get("permissions", []))

                # Define the required permissions for READ and WRITE access
                write_perms = {
                    f"{content_type_name}-create-all",
                    f"{content_type_name}-update-all",
                    f"{content_type_name}-delete-all"
                }
                read_perm = f"{content_type_name}-view-all"

                perm_type = None
                # Check for WRITE access first, as it's higher priority
                if not role_system_permissions.isdisjoint(write_perms):
                    perm_type = PermissionType.WRITE
                # If no WRITE access, check for READ access
                elif read_perm in role_system_permissions:
                    perm_type = PermissionType.READ

                # If the role has either read or write permissions, create the object
                if perm_type:
                    permissions_list.append(
                        Permission(
                            external_id=str(role_id),
                            type=perm_type,
                            entity_type=EntityType.ROLE
                        )
                    )

        return permissions_list

    def _parse_bookstack_permissions_all_users(self, all_users: List[AppUser]) -> List[Permission]:
        """
        Creates a list of Permission objects granting READ access to every user
        in the provided list.
        """
        permissions_list = []
        for user in all_users:
            if user.email and user.source_user_id:
                permissions_list.append(
                    Permission(
                        external_id=user.source_user_id,
                        email=user.email,
                        type=PermissionType.READ,
                        entity_type=EntityType.USER
                    )
                )
            else:
                self.logger.warning(
                    f"Skipping permission for user {user.full_name} "
                    f"due to missing email or source_user_id."
                )

        return permissions_list

    async def _sync_record_groups_incremental(self, last_sync_timestamp: str, roles_details: Dict[int, Dict]) -> None:
        """
        Sync all record groups (books, shelves and chapter) from BookStack as RecordGroup objects, handling new/updated/deleted record groups.
        """
        self.logger.info("Starting sync for record groups (books and shelves) as record groups.")

        await self._sync_record_groups_events(
            content_type="bookshelf",
            roles_details=roles_details,
            last_sync_timestamp=last_sync_timestamp
        )

        await self._sync_record_groups_events(
            content_type="book",
            roles_details=roles_details,
            last_sync_timestamp=last_sync_timestamp
        )

        await self._sync_record_groups_events(
            content_type="chapter",
            roles_details=roles_details,
            last_sync_timestamp=last_sync_timestamp
        )

    async def _sync_record_groups_events(self, content_type: str, roles_details: Dict[int, Dict], last_sync_timestamp: str) -> None:

        tasks = {
            "create": self.data_source.list_audit_log(filter={'type': f'{content_type}_create', 'created_at:gte': last_sync_timestamp}),
            "update": self.data_source.list_audit_log(filter={'type': f'{content_type}_update', 'created_at:gte': last_sync_timestamp}),
            "delete": self.data_source.list_audit_log(filter={'type': f'{content_type}_delete', 'created_at:gte': last_sync_timestamp}),
            "permissions_update": self.data_source.list_audit_log(filter={'type': 'permissions_update', 'created_at:gte': last_sync_timestamp}),
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        event_responses = dict(zip(tasks.keys(), results))

        # --- Handle Create Events ---
        create_response = event_responses.get("create")
        if create_response and create_response.success and create_response.data.get('data'):
            self.logger.info(f"Found {len(create_response.data['data'])} new {content_type}(s) to create.")
            for event in create_response.data['data']:
                await self._handle_record_group_create_event(event, content_type, roles_details)

        # --- Handle Update Events ---
        update_response = event_responses.get("update")
        if update_response and update_response.success and update_response.data.get('data'):
            self.logger.info(f"Found {len(update_response.data['data'])} updated {content_type}(s) to update.")
            for event in update_response.data['data']:
                await self._handle_record_group_create_event(event, content_type, roles_details)

        # --- Handle Delete Events ---
        delete_response = event_responses.get("delete")
        if delete_response and delete_response.success and delete_response.data.get('data'):
            self.logger.info(f"Found {len(delete_response.data['data'])} deleted {content_type}(s) to delete.")
            for event in delete_response.data['data']:
                await self._handle_record_group_delete_event(event, content_type)

        permissions_update_response = event_responses.get("permissions_update")
        if permissions_update_response and permissions_update_response.success and permissions_update_response.data.get('data'):
            self.logger.info(f"Found {len(permissions_update_response.data['data'])} updated {content_type}(s) to update.")
            for event in permissions_update_response.data['data']:
                if event.get("loggable_type") == content_type:
                    await self._handle_record_group_create_event(event, content_type, roles_details)


    async def _handle_record_group_create_event(self, event: Dict, content_type: str, roles_details: Dict[int, Dict]) -> None:
        """
        Handles a create event for any record group type (book, chapter, bookshelf).
        """
        item_id, item_name = self._parse_id_and_name_from_event(event)
        if item_id is None:
            return  # Parser already logged the error

        self.logger.info(f"{content_type} created/updated: '{item_name}' (ID: {item_id}). Fetching details...")

        # 1. Map content_type to the correct API fetch method
        fetch_method_map = {
            "book": self.data_source.get_book,
            "bookshelf": self.data_source.get_shelf,
            "chapter": self.data_source.get_chapter,
        }

        fetch_method = fetch_method_map.get(content_type)
        if not fetch_method:
            self.logger.error(f"Invalid content_type '{content_type}' in event handler.")
            return

        # 2. Fetch the full details of the newly created item
        response = await fetch_method(item_id)
        if not response.success or not response.data:
            self.logger.warning(f"Could not fetch details for new {content_type} ID {item_id}. Skipping.")
            return

        item_details = response.data

        # 3. Determine parent ID if it's a chapter
        parent_external_id = None
        if content_type == "chapter" and item_details.get("book_id"):
            parent_external_id = f"book/{item_details.get('book_id')}"

        # 4. Reuse your existing function to build the RecordGroup and its permissions
        record_group_tuple = await self._create_record_group_with_permissions(
            item=item_details,
            content_type_name=content_type,
            roles_details=roles_details,
            parent_external_id=parent_external_id
        )

        # 5. Process the new record group
        if record_group_tuple:
            # The processor expects a list of tuples
            await self.data_entities_processor.on_new_record_groups([record_group_tuple])
            self.logger.info(f"✅ Successfully processed new {content_type}: '{item_name}'.")

    async def _handle_record_group_delete_event(self, event: Dict, content_type: str) -> None:
        self.logger.warning("!! method not implemented yet !!")

    #---------------------------Records Sync-----------------------------------#
    async def _sync_records(self, full_sync:bool = False) -> None:

        current_timestamp = self._get_iso_time()
        bookstack_record_sync_key = generate_record_sync_point_key("bookstack", "records", "global")
        bookstack_record_sync_point = await self.record_sync_point.read_sync_point(bookstack_record_sync_key)

        roles_details = await self.list_roles_with_details()
        users = await self.get_all_users()

        #if no sync point, initialize cursor and run _sync_users else run _sync_users_incremental
        if full_sync or not bookstack_record_sync_point.get('timestamp'):
            await self._sync_records_full(roles_details, users)
            await self.record_sync_point.update_sync_point(
                bookstack_record_sync_key,
                {"timestamp": current_timestamp}
            )
        else:
            last_sync_timestamp = bookstack_record_sync_point.get('timestamp')
            await self._sync_records_incremental(last_sync_timestamp, roles_details, users)
            await self.record_sync_point.update_sync_point(
                bookstack_record_sync_key,
                {"timestamp": current_timestamp}
            )

    async def _sync_records_full(self, roles_details: Dict[int, Dict], users: List[AppUser]) -> None:
        """
        Sync all pages from BookStack as Record objects, handling new/updated/deleted records.
        """
        self.logger.info("Starting sync for pages as records.")

        batch_records: List[Tuple[FileRecord, List[Permission]]] = []
        offset = 0

        while True:
            response = await self.data_source.list_pages(count=self.batch_size, offset=offset)

            pages_data = {}
            if response.success and response.data and 'content' in response.data:
                try:
                    pages_data = json.loads(response.data['content'])
                except json.JSONDecodeError:
                    self.logger.error(f"Failed to decode JSON content for pages: {response.data['content']}")
                    break
            else:
                self.logger.error(f"Failed to fetch pages or malformed response: {response.error}")
                break

            pages_page = pages_data.get("data", [])
            if not pages_page:
                self.logger.info("No more pages to sync.")
                break

            self.logger.info(f"Processing page {offset + 1} to {offset + len(pages_page)}...")

            # Process each page from the current API response
            for page in pages_page:
                record_update = await self._process_bookstack_page(page, roles_details, users)

                if not record_update:
                    continue

                # Handle deleted records
                if record_update.is_deleted:
                    await self._handle_record_updates(record_update)
                    continue

                # Handle updated records
                if record_update.is_updated:
                    await self._handle_record_updates(record_update)
                    continue

                # Handle new records - add to batch
                if record_update.record:
                    batch_records.append((record_update.record, record_update.new_permissions or []))

                    # When the batch is full, send it to the data processor
                    if len(batch_records) >= self.batch_size:
                        self.logger.info(f"Processing batch of {len(batch_records)} new page records.")
                        await self.data_entities_processor.on_new_records(batch_records)
                        batch_records = []
                        await asyncio.sleep(0.1)

            offset += len(pages_page)
            if offset >= pages_data.get("total", 0):
                break

        # Process any remaining records in the final batch
        if batch_records:
            self.logger.info(f"Processing final batch of {len(batch_records)} new page records.")
            await self.data_entities_processor.on_new_records(batch_records)

        self.logger.info("✅ Finished syncing all page records.")

    async def _process_bookstack_page(self, page: Dict, roles_details: Dict[int, Dict], users: List[AppUser]) -> Optional[RecordUpdate]:
        """
        Process a single BookStack page, create a Record object, detect changes, and fetch its permissions.
        Returns RecordUpdate object containing the record and change information.
        """
        try:
            page_id = page.get("id")
            if not page_id:
                self.logger.warning("Skipping page with missing ID.")
                return None

            # Check for existing record
            existing_record = None
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_name=self.connector_name,
                    external_id=f"page/{page_id}"
                )

            # Detect changes
            is_new = existing_record is None
            is_updated = False
            metadata_changed = False
            content_changed = False
            permissions_changed = False

            # Check for updates if record exists
            if existing_record:
                # Check if name changed
                if existing_record.record_name != page.get("name"):
                    metadata_changed = True
                    is_updated = True

                # Check if content changed (using revision count)
                if existing_record.external_revision_id != str(page.get("revision_count")):
                    content_changed = True
                    is_updated = True

            # 1. Determine parent relationship
            parent_external_id = None
            if page.get("book_id"):
                parent_external_id = f"book/{page.get('book_id')}"
            if page.get("chapter_id"):
                parent_external_id = f"chapter/{page.get('chapter_id')}"

            # 2. Convert timestamp
            timestamp_ms = None
            updated_at_str = page.get("updated_at")
            if updated_at_str:
                dt_obj = datetime.fromisoformat(updated_at_str.replace('Z', '+00:00'))
                timestamp_ms = int(dt_obj.timestamp() * 1000)

            # 3. Create the FileRecord object
            file_record = FileRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                record_name=page.get("name"),
                external_record_id=f"page/{page_id}",
                connector_name=self.connector_name,
                record_type=RecordType.FILE.value,
                external_record_group_id=parent_external_id,
                origin=OriginTypes.CONNECTOR.value,
                org_id=self.data_entities_processor.org_id,
                source_updated_at=timestamp_ms,
                updated_at=timestamp_ms,
                version=0 if is_new else existing_record.version + 1,
                external_revision_id=str(page.get("revision_count")),
                weburl=f"{self.bookstack_base_url.rstrip('/')}/books/{page.get('book_slug')}/page/{page.get('slug')}",
                mime_type=MimeTypes.MARKDOWN,
                extension="md",
                is_file=True,
                size_in_bytes=0,
                inherit_permissions=True,
            )

            # 4. Fetch and parse permissions
            new_permissions = []
            permissions_response = await self.data_source.get_content_permissions(
                content_type="page", content_id=page_id
            )
            if permissions_response.success and permissions_response.data:
                new_permissions = await self._parse_bookstack_permissions(
                    permissions_response.data, roles_details, "page"
                )

                # new_permissions = self._parse_bookstack_permissions_all_users(all_users=users)

                fallback_permissions = permissions_response.data.get("fallback_permissions")

                if fallback_permissions:
                    # Set inherit_permissions based on the 'inheriting' flag
                    file_record.inherit_permissions = fallback_permissions.get("inheriting", True)
            else:
                self.logger.warning(
                    f"Failed to fetch permissions for page '{page.get('name')}' (ID: {page_id}): "
                    f"{permissions_response.error}"
                )

            # Get old permissions if the record exists (you'll need to implement this)
            old_permissions = []
            if existing_record:
                # TODO: Implement fetching old permissions
                # old_permissions = await tx_store.get_permissions_for_record(existing_record.id)
                # For now, if permissions changed, mark it
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
                external_record_id=f"page/{page_id}"
            )

        except Exception as e:
            self.logger.error(f"Error processing BookStack page ID {page.get('id')}: {e}", exc_info=True)
            return None

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

    async def _sync_records_incremental(self, last_sync_timestamp: str, roles_details: Dict[int, Dict], users: List[AppUser]) -> None:
        """
        Syncs records (pages) incrementally by processing create, update, and
        delete events from the audit log since the last sync.
        """
        self.logger.info(f"Starting incremental record (page) sync from: {last_sync_timestamp}")

        # 1. Concurrently fetch all page-related events
        tasks = {
            "create": self.data_source.list_audit_log(filter={'type': 'page_create', 'created_at:gte': last_sync_timestamp}),
            "update": self.data_source.list_audit_log(filter={'type': 'page_update', 'created_at:gte': last_sync_timestamp}),
            "permissions_update": self.data_source.list_audit_log(filter={'type': 'permissions_update', 'created_at:gte': last_sync_timestamp}),
            "delete": self.data_source.list_audit_log(filter={'type': 'page_delete', 'created_at:gte': last_sync_timestamp}),
            "move": self.data_source.list_audit_log(filter={'type': 'page_move', 'created_at:gte': last_sync_timestamp})
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        event_responses = dict(zip(tasks.keys(), results))

        # 2. Process Create Events
        create_response = event_responses.get("create")
        if create_response and create_response.success and create_response.data.get('data'):
            self.logger.info(f"Found {len(create_response.data['data'])} new page(s) to create.")
            for event in create_response.data['data']:
                await self._handle_page_upsert_event(event, roles_details, users)

        # 3. Process Update Events
        update_response = event_responses.get("update")
        if update_response and update_response.success and update_response.data.get('data'):
            self.logger.info(f"Found {len(update_response.data['data'])} page(s) to update.")
            for event in update_response.data['data']:
                await self._handle_page_upsert_event(event, roles_details, users)

        permissions_update_response = event_responses.get("permissions_update")
        if permissions_update_response and permissions_update_response.success and permissions_update_response.data.get('data'):
            self.logger.info(f"Found {len(permissions_update_response.data['data'])} page(s) to update.")
            for event in permissions_update_response.data['data']:
                if event.get('loggable_type') == 'page':
                    await self._handle_page_upsert_event(event, roles_details, users)

        # 4. Process Delete Events
        delete_response = event_responses.get("delete")
        if delete_response and delete_response.success and delete_response.data.get('data'):
            self.logger.info(f"Found {len(delete_response.data['data'])} page(s) to delete.")
            # for event in delete_response.data['data']:
            #     await self._handle_page_delete_event(event)

        # 5. Process Move Events
        move_response = event_responses.get("move")
        if move_response and move_response.success and move_response.data.get('data'):
            self.logger.info(f"Found {len(move_response.data['data'])} page(s) to move.")
            self.logger.warning("!! method not implemented yet !!")
            # for event in move_response.data['data']:
            #     await self._handle_page_move_event(event)

        self.logger.info("✅ Finished incremental record sync.")

    async def _handle_page_upsert_event(self, event: Dict, roles_details: Dict[int, Dict], users: List[AppUser]) -> None:
        """
        Handles a 'page_create' or 'page_update' event by fetching the page's
        latest details and calling the correct data processor method.
        """
        page_id, page_name = self._parse_id_and_name_from_event(event)
        if page_id is None:
            return

        self.logger.info(f"Processing page upsert for: '{page_name}' (ID: {page_id})")

        # Fetch the full, most recent details of the page
        page_response = await self.data_source.list_pages(filter={"id": str(page_id)})

        if not page_response.success or not page_response.data:
            self.logger.warning(f"Could not fetch details for page ID {page_id}. Skipping.")
            return

        json_content_str = page_response.data.get('content')
        if not json_content_str:
            self.logger.warning(f"API response for page ID {page_id} is empty. Skipping.")
            return

        try:
            # 1. Parse the JSON string into a Python dictionary
            pages_data = json.loads(json_content_str)
            # 2. Extract the list of pages from the 'data' key
            pages_list = pages_data.get("data", [])
            # 3. Check if we actually got a page back
            if not pages_list:
                self.logger.warning(f"No page data found in the response for ID {page_id}. Skipping.")
                return
            # 4. Get the first (and only) page object from the list
            page_details = pages_list[0]

        except json.JSONDecodeError:
            self.logger.error(f"Failed to decode JSON for page ID {page_id}. Content: {json_content_str}")
            return

        # Process the page to determine if it's new or updated
        record_update = await self._process_bookstack_page(page_details, roles_details, users)

        if not record_update:
            return

        # **This is the logic you requested:**
        # If the record is new, handle it here directly.
        if record_update.is_new:
            self.logger.info(f"New record detected from event: {record_update.record.record_name}")
            new_record_batch = [(record_update.record, record_update.new_permissions or [])]
            await self.data_entities_processor.on_new_records(new_record_batch)

        # If it's an existing record that was updated, pass it to the original handler.
        elif record_update.is_updated:
            await self._handle_record_updates(record_update)

    async def run_incremental_sync(self) -> None:
        """
        Runs an incremental sync based on last sync timestamp.
        BookStack doesn't have a native incremental sync API, so we check for updates.
        """
        try:
            self.logger.info("Starting BookStack incremental sync.")

            # BookStack doesn't have a cursor-based API like Dropbox,
            # so we would need to implement timestamp-based checking
            # or use the audit log API to detect changes

            # For now, run a full sync
            await self.run_sync()

            self.logger.info("BookStack incremental sync completed.")

        except Exception as ex:
            self.logger.error(f"Error in BookStack incremental sync: {ex}", exc_info=True)
            raise

    def handle_webhook_notification(self, notification: Dict) -> None:
        """
        Handles webhook notifications from BookStack.
        BookStack doesn't have webhooks by default, but this is here for future compatibility.

        Args:
            notification: The webhook notification payload
        """
        self.logger.info("BookStack webhook received.")
        # BookStack doesn't have native webhooks, so this is a placeholder
        # Could potentially trigger an incremental sync if webhooks are added
        asyncio.create_task(self.run_incremental_sync())

    def cleanup(self) -> None:
        """
        Cleanup resources used by the connector.
        """
        self.logger.info("Cleaning up BookStack connector resources.")
        self.data_source = None

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService
    ) -> "BaseConnector":
        """
        Factory method to create a BookStack connector instance.

        Args:
            logger: Logger instance
            data_store_provider: Data store provider for database operations
            config_service: Configuration service for accessing credentials

        Returns:
            Initialized BookStackConnector instance
        """
        data_entities_processor = DataSourceEntitiesProcessor(
            logger, data_store_provider, config_service
        )
        await data_entities_processor.initialize()

        return BookStackConnector(
            logger, data_entities_processor, data_store_provider, config_service
        )
