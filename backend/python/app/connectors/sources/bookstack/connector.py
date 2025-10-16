import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from logging import Logger
from typing import Dict, Optional, List, Tuple, Callable, Awaitable
from datetime import datetime, timezone
import json 

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    Connectors,
    MimeTypes,
    OriginTypes
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
    CommonFields,
    ConnectorBuilder,
    DocumentationLink,
)
from app.connectors.sources.bookstack.common.apps import BookStackApp
from app.models.entities import (
    Record, FileRecord, RecordType, AppUserGroup, AppUser, RecordGroup, RecordGroupType
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.bookstack.bookstack import (
    BookStackClient,
    BookStackTokenConfig,
)
from app.sources.external.bookstack.bookstack import BookStackDataSource
from app.sources.client.bookstack.bookstack import BookStackResponse
from app.utils.streaming import stream_content
from app.connectors.core.registry.connector_builder import (
    AuthField,
)


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
            "BookStack API Access",
            "https://www.bookstackapp.com/docs/admin/api-access/"
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
        self.user_group_sync_point = _create_sync_point(SyncDataPointType.GROUPS)

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
            print("!!!!!!!!!!!!!!!!! config", config)
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
            client = BookStackClient.build_with_config(token_config) #it was await before
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
        
        print("\n\n\n\n!!!!!!!!!!!!!!!!!!!! called stream_record")
        # For BookStack, we would need to fetch the content based on record type
        # This is a placeholder implementation
        # signed_url = await self.get_signed_url(record)
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
            return

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
        

    async def run_sync(self) -> None:
        """
        Runs a full synchronization from the BookStack instance.
        Syncs users, groups, record groups (books/shelves), and records (pages/chapters).
        """
        try:
            self.logger.info("Starting BookStack full sync.")
            
            users = await self.get_all_users()

            roles_details = await self.list_roles_with_details()
            
            # Step 1: Sync all users
            self.logger.info("Syncing users...")
            await self._sync_users(users)
            
            # Step 2: Sync all user groups (roles in BookStack)
            self.logger.info("Syncing user groups (roles)...")
            await self._sync_user_groups()
            
            # Step 3: Sync record groups (books and shelves)
            self.logger.info("Syncing record groups (chapters/books/shelves)...")
            await self._sync_record_groups(roles_details)
            
            # Step 4: Sync all records (pages, chapters, attachments)
            self.logger.info("Syncing records (pages/chapters)...")
            await self._sync_records(roles_details)
            
            self.logger.info("BookStack full sync completed.")
            
        except Exception as ex:
            self.logger.error(f"Error in BookStack connector run: {ex}", exc_info=True)
            raise

    async def _sync_users(self, app_users: List[AppUser]) -> None:
        """
        Fetches all users from BookStack, transforms them into AppUser objects,
        and upserts them into the database.
        """
        self.logger.info("Starting BookStack user sync...")
        
        # Pass the AppUser objects to the data processor to save in ArangoDB
        await self.data_entities_processor.on_new_app_users(app_users)
        self.logger.info("✅ Finished syncing BookStack users.")


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

    async def _sync_user_groups(self) -> None:
        """
        Fetches all roles, then fetches all users with their role assignments
        to build and upsert user groups with their member permissions.
        """
        self.logger.info("Starting BookStack user group and permissions sync...")

        # 1. Fetch all available roles to define the user groups    
        all_roles = await self._fetch_all_roles()
        if not all_roles:
            self.logger.warning("No roles found in BookStack. Aborting user group sync.")
            return
        self.logger.info(f"Found {len(all_roles)} total roles.")

        # 2. Fetch details for all users to find their role memberships 
        all_users_with_details = await self._fetch_all_users_with_details()
        if not all_users_with_details:
            self.logger.warning("No users found; no permission edges will be created.")

        # 3. Create a map of {role_id: [List of Permissions]}
        role_to_permissions_map = self._build_role_permissions_map(all_users_with_details)

        # 4. Build the final batch for the data processor 
        user_groups_batch = []
        for role in all_roles:
            app_user_group = self._get_app_user_groups([role])[0][0] # Reuse existing helper
            
            # Get the list of permissions for this role from our map
            permissions = role_to_permissions_map.get(role.get("id"), [])
            
            user_groups_batch.append((app_user_group, permissions))

        # 5. Send the complete batch to the processor 
        if user_groups_batch:
            self.logger.info(f"Submitting {len(user_groups_batch)} groups with permissions...")
            await self.data_entities_processor.on_new_user_groups(user_groups_batch)
            self.logger.info("✅ Successfully processed user groups and permissions.")
        else:
            self.logger.info("No user groups were processed.")


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
            if not roles_page: break
            all_roles.extend(roles_page)
            offset += len(roles_page)
            if offset >= response.data.get("total", 0): break
        return all_roles


    async def _fetch_all_users_with_details(self) -> List[Dict]:
        """Helper to fetch all users and then get detailed profiles concurrently."""
        self.logger.info("Fetching all user IDs...")
        # 1. Get a list of all users to find their IDs
        all_users_summary = []
        offset = 0
        while True:
            response = await self.data_source.list_users(count=self.batch_size, offset=offset)
            if not response.success or not response.data:
                self.logger.error(f"Failed to list users: {response.error}")
                break
            users_page = response.data.get("data", [])
            if not users_page: break
            all_users_summary.extend(users_page)
            offset += len(users_page)
            if offset >= response.data.get("total", 0): break

        if not all_users_summary:
            return []

        self.logger.info(f"Fetching details for {len(all_users_summary)} users concurrently...")
        # 2. Asynchronously fetch the full details for each user
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
    
    def _build_role_permissions_map(self, all_users_with_details: List[Dict]) -> Dict[int, List[Permission]]:
        """
        Creates a map of role_id -> [List of Permissions] from detailed user objects.

        Args:
            all_users_with_details: A list of user dictionaries, each containing a 'roles' key.

        Returns:
            A dictionary where each key is a role_id and the value is a list of
            Permission objects for users belonging to that role.
        """
        self.logger.info("Building map of roles to user permissions...")
        role_to_permissions_map = {}
        for user in all_users_with_details:
            for role in user.get("roles", []):
                role_id = role.get("id")
                if not role_id:
                    continue

                # Create a permission object linking this user to this role.
                permission = Permission(
                    external_id=str(user.get("id")),
                    email=user.get("email"),
                    type=PermissionType.WRITE,
                    entity_type=EntityType.GROUP
                )

                if role_id not in role_to_permissions_map:
                    role_to_permissions_map[role_id] = []
                role_to_permissions_map[role_id].append(permission)
        
        self.logger.info(f"Permission map built for {len(role_to_permissions_map)} roles.")
        return role_to_permissions_map

    def _get_app_user_groups(self, roles: List[Dict]) -> List[Tuple[AppUserGroup, List[Permission]]]:
        """
        Converts a list of BookStack role dicts into a list of (AppUserGroup, permissions) tuples.
        Permissions are ignored for now as requested.
        """
        user_groups_list = []
        for role in roles:
            app_user_group = AppUserGroup(
                app_name=self.connector_name,
                source_user_group_id=str(role.get("id")),
                # The API uses 'display_name' for the role's name
                name=role.get("display_name"),
                org_id=self.data_entities_processor.org_id

            )
            
            # The 'on_new_user_groups' method expects a tuple of (AppUserGroup, List[Permission]).
            # We pass an empty list for permissions.
            user_groups_list.append((app_user_group, []))

        return user_groups_list

    # async def _sync_record_groups(self) -> None:
    #     """
    #     Sync all record groups (books and shelves) from BookStack.
    #     To be implemented.
    #     """
    #     pass

    async def _sync_record_groups(self, roles_details: Dict[int, Dict]) -> None:
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
                parent_external_group_id=parent_external_id
            )

            # 2. Fetch its permissions
            permissions_response = await self.data_source.get_content_permissions(
                content_type=content_type_name, content_id=item_id
            )

            permissions_list = []
            if permissions_response.success and permissions_response.data:
                permissions_list = await self._parse_bookstack_permissions(permissions_response.data, roles_details, content_type_name)
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
                        entity_type=EntityType.GROUP
                    )
                )
        # CASE B: No explicit permissions; fall back to default role permissions
        else:
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
                            entity_type=EntityType.GROUP
                        )
                    )

        return permissions_list
    
    async def _sync_records(self, roles_details: Dict[int, Dict]) -> None:
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
                record_update = await self._process_bookstack_page(page, roles_details)
                
                if not record_update:
                    continue
                    
                # Handle deleted records
                if record_update.is_deleted:
                    await self._handle_record_updates(record_update)
                    continue
                
                # Handle updated records
                if record_update.is_updated:
                    print("!!!!!!!!!!!!!!!!!!!!!!!!!!!! got record update")
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
    
    async def _process_bookstack_page(self, page: Dict, roles_details: Dict[int, Dict]) -> Optional[RecordUpdate]:
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
                weburl=f"{self.bookstack_base_url}books/{page.get('book_slug')}/page/{page.get('slug')}",
                mime_type=MimeTypes.MARKDOWN,
                extension="md",
                is_file=True,
                size_in_bytes=0,
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
                    print("\n\n\n!!!!!!!!!!!!!!!!!!!!!!!!!!!! got content changed")
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