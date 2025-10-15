import asyncio
import uuid
from datetime import datetime, timezone
from logging import Logger
from typing import Dict, Optional, List, Tuple, Callable, Awaitable
from datetime import datetime, timezone

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

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
)
from app.connectors.core.registry.connector_builder import (
    CommonFields,
    ConnectorBuilder,
    DocumentationLink,
)
from app.connectors.sources.bookstack.common.apps import BookStackApp
from app.models.entities import (
    Record, AppUserGroup, AppUser, RecordGroup, RecordGroupType
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.bookstack.bookstack import (
    BookStackClient,
    BookStackTokenConfig,
)
from app.sources.external.bookstack.bookstack import BookStackDataSource
from app.sources.client.bookstack.bookstack import BookStackResponse
from app.utils.streaming import stream_content



class BookStackConnector(BaseConnector):
    """
    Connector for synchronizing data from a BookStack instance.
    Syncs books, chapters, pages, attachments, users, roles, and permissions.
    """

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

            credentials_config = config.get("credentials", {})
            # Read the correct keys required by BookStackTokenConfig
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
        if not record.weburl:
            self.logger.warning(f"No web URL found for record {record.id}")
            return None
        
        return record.weburl

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
        
        # For BookStack, we would need to fetch the content based on record type
        # This is a placeholder implementation
        web_url = await self.get_signed_url(record)
        if not web_url:
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail="Record not found or access denied"
            )
        
        # Stream the content from the URL
        return StreamingResponse(
            stream_content(web_url),
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
        

    async def run_sync(self) -> None:
        """
        Runs a full synchronization from the BookStack instance.
        Syncs users, groups, record groups (books/shelves), and records (pages/chapters).
        """
        try:
            self.logger.info("Starting BookStack full sync.")
            
            users = await self.get_all_users()

            # Step 1: Sync all users
            self.logger.info("Syncing users...")
            await self._sync_users(users)
            
            # Step 2: Sync all user groups (roles in BookStack)
            self.logger.info("Syncing user groups (roles)...")
            await self._sync_user_groups()
            
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

    async def _sync_record_groups(self) -> None:
        """
        Sync all record groups (shelves, books, and chapters) from BookStack
        and their associated permissions.
        """
        self.logger.info("Starting sync for shelves, books, and chapters as record groups.")
    
        # Sync all shelves as record groups
        await self._sync_content_type_as_record_group(
            content_type_name="bookshelf",
            list_method=self.data_source.list_shelves
        )

        # Sync all books as record groups
        await self._sync_content_type_as_record_group(
            content_type_name="book",
            list_method=self.data_source.list_books
        )

        # Sync all chapters as record groups
        await self._sync_content_type_as_record_group(
            content_type_name="chapter",
            list_method=self.data_source.list_chapters
        )

        self.logger.info("✅ Finished syncing all record groups.")
    
    async def _sync_content_type_as_record_group(
        self,
        content_type_name: str,
        list_method: Callable[..., Awaitable[BookStackResponse]],
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
                self._create_record_group_with_permissions(item, content_type_name, parent_external_id)
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
        self, item: Dict, content_type_name: str, parent_external_id: Optional[str] = None
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
                permissions_list = await self._parse_bookstack_permissions(permissions_response.data)
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

    async def _parse_bookstack_permissions(self, permissions_data: Dict) -> List[Permission]:
        """Parses the BookStack permission object into a list of Permission objects."""
        permissions_list = []

        # 1. Handle the owner (user permission)
        owner = permissions_data.get("owner")
        if owner and owner.get("id"):
            user = await self.data_source.get_user(owner.get("id"))
            user_email = user.data.get("email")
            permissions_list.append(
                Permission(
                    external_id=str(owner.get("id")),
                    email=user_email,
                    type=PermissionType.OWNER,
                    entity_type=EntityType.USER
                )
            )

        # 2. Handle role permissions (group permissions)
        role_permissions = permissions_data.get("role_permissions", [])
        for role_perm in role_permissions:
            role_id = role_perm.get("role_id")
            if not role_id:
                continue

            # Determine the permission level
            # WRITE access is granted if update or delete permissions are present
            if role_perm.get("update") or role_perm.get("delete") or role_perm.get("create"):
                perm_type = PermissionType.WRITE
            # READ access is granted if only view permission is present
            elif role_perm.get("view"):
                perm_type = PermissionType.READ
            else:
                # If no relevant permissions, skip creating a permission object
                continue

            permissions_list.append(
                Permission(
                    external_id=str(role_id),
                    type=perm_type,
                    entity_type=EntityType.GROUP
                )
            )

        return permissions_list
    
    async def _sync_records(self) -> None:
        """
        Sync all records (pages, chapters, attachments) from BookStack.
        To be implemented.
        """
        pass

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