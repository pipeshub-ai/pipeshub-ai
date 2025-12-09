"""
Confluence Cloud Connector

This connector syncs Confluence Cloud data including:
- Spaces with permissions
- Pages with content and metadata
- Users and their access

Authentication: OAuth 2.0 (3-legged OAuth)
"""

import uuid
from datetime import datetime
from logging import Logger
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
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
from app.connectors.sources.atlassian.core.apps import ConfluenceApp
from app.connectors.sources.atlassian.core.oauth import AtlassianScope
from app.models.entities import (
    AppUser,
    AppUserGroup,
    CommentRecord,
    FileRecord,
    IndexingStatus,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.confluence.confluence import (
    ConfluenceClient as ExternalConfluenceClient,
)
from app.sources.external.confluence.confluence import ConfluenceDataSource

# Confluence Cloud OAuth URLs
AUTHORIZE_URL = "https://auth.atlassian.com/authorize"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"
HTTP_STATUS_200 = 200

# Time offset (in hours) applied to date filters to handle timezone differences
# between the application and Confluence server, ensuring no data is missed during sync
TIME_OFFSET_HOURS = 24

# Expand parameters for fetching pages and blogposts with required metadata
# Includes: ancestors, history, space, attachments, and comments
CONTENT_EXPAND_PARAMS = (
    "ancestors,"
    "history.lastUpdated,"
    "space,"
    "children.attachment,"
    "children.attachment.history.lastUpdated,"
    "children.attachment.version,"
    "childTypes.comment"
)


@ConnectorBuilder("Confluence")\
    .in_group("Atlassian")\
    .with_auth_type("OAUTH")\
    .with_description("Sync pages, spaces, and users from Confluence Cloud")\
    .with_categories(["Knowledge Management", "Collaboration"])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/confluence.svg")
        .with_realtime_support(False)
        .add_documentation_link(DocumentationLink(
            "Confluence Cloud OAuth Setup",
            "https://developer.atlassian.com/cloud/confluence/oauth-2-3lo-apps/",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/confluence/confluence',
            'pipeshub'
        ))
        .with_redirect_uri("connectors/oauth/callback/Confluence", True)
        .with_oauth_urls(AUTHORIZE_URL, TOKEN_URL, AtlassianScope.get_confluence_read_access())
        .add_auth_field(CommonFields.client_id("Atlassian OAuth App"))
        .add_auth_field(CommonFields.client_secret("Atlassian OAuth App"))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .add_filter_field(FilterField(
            name="space_keys",
            display_name="Space Keys",
            description="Filter pages and blogposts by space key",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            default_value=[]
        ))
        .add_filter_field(FilterField(
            name="page_ids",
            display_name="Page IDs",
            description="Filter specific pages by ID.",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            default_value=[]
        ))
        .add_filter_field(FilterField(
            name="blogpost_ids",
            display_name="Blogpost IDs",
            description="Filter specific blogposts by ID.",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            default_value=[]
        ))
        .add_filter_field(CommonFields.modified_date_filter("Filter pages and blogposts by modification date."))
        .add_filter_field(CommonFields.created_date_filter("Filter pages and blogposts by creation date."))
        # Indexing filters - Pages
        .add_filter_field(FilterField(
            name="pages",
            display_name="Index Pages",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of pages",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="page_attachments",
            display_name="Index Page Attachments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of page attachments",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="page_comments",
            display_name="Index Page Comments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of page comments",
            default_value=True
        ))
        # Indexing filters - Blogposts
        .add_filter_field(FilterField(
            name="blogposts",
            display_name="Index Blogposts",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of blogposts",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="blogpost_attachments",
            display_name="Index Blogpost Attachments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of blogpost attachments",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="blogpost_comments",
            display_name="Index Blogpost Comments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of blogpost comments",
            default_value=True
        ))
    )\
    .build_decorator()
class ConfluenceConnector(BaseConnector):
    """
    Confluence Cloud Connector

    This connector syncs Confluence Cloud data including:
    - Spaces with permissions
    - Pages with content and metadata
    - Users and their access

    Authentication: OAuth 2.0 (3LO - 3-legged OAuth)
    """

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
    ) -> None:
        """Initialize the Confluence connector."""
        super().__init__(
            ConfluenceApp(),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service
        )

        # Client instances
        self.external_client: Optional[ExternalConfluenceClient] = None
        self.data_source: Optional[ConfluenceDataSource] = None

        # Initialize sync points for incremental sync
        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_name=self.connector_name,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider,
            )

        self.pages_sync_point = _create_sync_point(SyncDataPointType.RECORDS)
        self.audit_log_sync_point = _create_sync_point(SyncDataPointType.RECORDS)

        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

    async def init(self) -> bool:
        """Initialize the Confluence connector with credentials and client."""
        try:
            self.logger.info("🔧 Initializing Confluence Cloud Connector...")

            # Build client from services (handles config loading, token, base URL internally)
            self.external_client = await ExternalConfluenceClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
            )

            # Initialize data source
            self.data_source = ConfluenceDataSource(self.external_client)

            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "confluence", self.logger
            )

            # Test connection
            if not await self.test_connection_and_access():
                self.logger.error("❌ Confluence connector connection test failed")
                return False

            self.logger.info("✅ Confluence connector initialized successfully")
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to initialize Confluence connector: {e}", exc_info=True)
            return False

    async def _get_fresh_datasource(self) -> ConfluenceDataSource:
        """
        Get ConfluenceDataSource with ALWAYS-FRESH access token.

        This method:
        1. Fetches current OAuth token from config
        2. Compares with existing client's token
        3. Updates client ONLY if token changed (mutation)
        4. Returns datasource with current token

        Returns:
            ConfluenceDataSource with current valid token
        """
        if not self.external_client:
            raise Exception("Confluence client not initialized. Call init() first.")

        # Fetch current config from etcd (async I/O)
        config = await self.config_service.get_config("/services/connectors/confluence/config")

        if not config:
            raise Exception("Confluence configuration not found")

        # Extract fresh OAuth access token
        credentials_config = config.get("credentials", {}) or {}
        fresh_token = credentials_config.get("access_token", "")

        if not fresh_token:
            raise Exception("No OAuth access token available")

        # Get current token from client
        internal_client = self.external_client.get_client()
        current_token = internal_client.get_token()

        # Update client's token if it changed (mutation)
        if current_token != fresh_token:
            self.logger.debug("🔄 Updating client with refreshed access token")
            internal_client.set_token(fresh_token)

        # Return datasource with updated client
        return ConfluenceDataSource(self.external_client)

    async def test_connection_and_access(self) -> bool:
        """Test connection and access to Confluence API."""
        try:
            if not self.external_client:
                self.logger.error("External client not initialized")
                return False

            # Test by fetching spaces with a limit of 1
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_spaces(
                limit=1
            )

            if not response or response.status != HTTP_STATUS_200:
                self.logger.error(f"Connection test failed with status: {response.status if response else 'No response'}")
                return False

            self.logger.info("✅ Confluence connector connection test passed")
            return True

        except Exception as e:
            self.logger.error(f"Connection test failed: {e}", exc_info=True)
            return False

    async def run_sync(self) -> None:
        """
        Run full synchronization of Confluence Cloud data.

        Sync order:
        1. Users and Groups (global, includes group memberships)
        2. Spaces
            - Permissions
        3. Pages (per space)
            - Permissions
            - Attachments
            - Comments (inline, footer)
        4. Blogposts (per space)
            - Permissions
            - Attachments
            - Comments (inline, footer)
        """
        try:
            org_id = self.data_entities_processor.org_id
            self.logger.info(f"🚀 Starting Confluence Cloud sync for org: {org_id}")

            # Ensure client is initialized
            if not self.external_client or not self.data_source:
                raise Exception("Confluence client not initialized. Call init() first.")

            # Step 1: Sync users
            await self._sync_users()

            # Step 2: Sync groups and memberships
            await self._sync_user_groups()

            # Step 3: Sync spaces
            spaces = await self._sync_spaces()

            # Step 4: Sync pages and blogposts per space
            for space in spaces:
                space_key = space.short_name

                # Sync pages (with attachments, comments, permissions)
                self.logger.info(f"Syncing pages for space: {space.name} ({space_key})")
                await self._sync_content(space_key, RecordType.CONFLUENCE_PAGE)

                # Sync blogposts (with attachments, comments, permissions)
                self.logger.info(f"Syncing blogposts for space: {space.name} ({space_key})")
                await self._sync_content(space_key, RecordType.CONFLUENCE_BLOGPOST)

            # Step 5: Sync permission changes from audit log
            # This catches permission changes that don't update content's lastModified
            await self._sync_permission_changes_from_audit_log()

            self.logger.info("✅ Confluence sync completed successfully")

        except Exception as e:
            self.logger.error(f"❌ Error during Confluence sync: {e}", exc_info=True)
            raise

    async def _sync_users(self) -> None:
        """
        Sync users from Confluence using offset-based pagination.

        Uses CQL search: type=user
        Filters out users without email addresses.
        """
        try:
            self.logger.info("Starting user synchronization...")

            # Pagination variables
            batch_size = 100
            start = 0
            total_synced = 0
            total_skipped = 0

            # Paginate through all users
            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.search_users(
                    cql="type=user",
                    start=start,
                    limit=batch_size
                )

                # Check response
                if not response or response.status != HTTP_STATUS_200:
                    self.logger.error(f"❌ Failed to fetch users: {response.status if response else 'No response'}")
                    break

                response_data = response.json()
                users_data = response_data.get("results", [])

                if not users_data:
                    break

                # Transform users (skip users without email)
                app_users = []
                for user_result in users_data:
                    # Flatten: merge nested 'user' dict with top-level fields
                    user_data = {**user_result.get("user", {}), **{k: v for k, v in user_result.items() if k != "user"}}

                    # Skip if no email
                    email = user_data.get("email", "").strip()
                    if not email:
                        total_skipped += 1
                        continue

                    app_user = self._transform_to_app_user(user_data)
                    if app_user:
                        app_users.append(app_user)

                # Save batch to database
                if app_users:
                    await self.data_entities_processor.on_new_app_users(app_users)
                    total_synced += len(app_users)
                    self.logger.info(f"Synced {len(app_users)} users (batch starting at {start})")

                # Move to next page
                start += batch_size

                # Check if we've reached the end
                total_size = response_data.get("totalSize", 0)
                if start >= total_size:
                    break

            self.logger.info(f"✅ User sync complete. Synced: {total_synced}, Skipped (no email): {total_skipped}")

        except Exception as e:
            self.logger.error(f"❌ User sync failed: {e}", exc_info=True)
            raise

    async def _sync_user_groups(self) -> None:
        """
        Sync user groups and their memberships from Confluence.

        Steps:
        1. Fetch all groups with pagination
        2. For each group, fetch all members with pagination
        3. Create group and membership records
        """
        try:
            self.logger.info("Starting user group synchronization...")

            # Pagination variables for groups
            batch_size = 50
            start = 0
            total_groups_synced = 0
            total_memberships_synced = 0

            # Paginate through all groups
            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_groups(
                    start=start,
                    limit=batch_size
                )

                # Check response
                if not response or response.status != HTTP_STATUS_200:
                    self.logger.error(f"❌ Failed to fetch groups: {response.status if response else 'No response'}")
                    break

                response_data = response.json()
                groups_data = response_data.get("results", [])

                if not groups_data:
                    break

                # Process each group and its members
                for group_data in groups_data:
                    try:
                        group_id = group_data.get("id")
                        group_name = group_data.get("name")

                        if not group_id or not group_name:
                            continue

                        self.logger.debug(f"  Processing group: {group_name} ({group_id})")

                        # Fetch members for this group
                        member_emails = await self._fetch_group_members(group_id, group_name)

                        # Create user group
                        user_group = self._transform_to_user_group(group_data)
                        if not user_group:
                            continue

                        # Get AppUser objects for members
                        app_users = await self._get_app_users_by_emails(member_emails)

                        # Save group with members
                        await self.data_entities_processor.on_new_user_groups([(user_group, app_users)])
                        total_groups_synced += 1
                        total_memberships_synced += len(app_users)
                        self.logger.debug(f"Group {group_name}: {len(app_users)} members")

                    except Exception as group_error:
                        self.logger.error(f"❌ Failed to process group {group_data.get('name')}: {group_error}")
                        continue

                # Move to next page
                start += batch_size

                # Check if we have more groups
                size = response_data.get("size", 0)
                if size < batch_size:
                    break

            self.logger.info(f"✅ Group sync complete. Groups: {total_groups_synced}, Memberships: {total_memberships_synced}")

        except Exception as e:
            self.logger.error(f"❌ Group sync failed: {e}", exc_info=True)
            raise

    async def _sync_spaces(self) -> List[RecordGroup]:
        """
        Sync spaces from Confluence with permissions using cursor-based pagination.

        Steps:
        1. Fetch all spaces with cursor pagination
        2. Apply exclusion filters if NOT_IN operator is used
        3. For each space, fetch permissions
        4. Create RecordGroup with Permission objects
        """
        try:
            self.logger.info("Starting space synchronization...")

            # Get sync filter values for API
            space_keys_filter = self.sync_filters.get(SyncFilterKey.SPACE_KEYS)
            included_space_keys = None
            excluded_space_keys = None

            # Determine filter mode
            if space_keys_filter is not None:
                filter_operator = space_keys_filter.get_operator()
                if filter_operator == FilterOperator.IN:
                    included_space_keys = space_keys_filter.get_value()
                    self.logger.info(f"Filtering to include space keys: {included_space_keys}")
                elif filter_operator == FilterOperator.NOT_IN:
                    excluded_space_keys = space_keys_filter.get_value()
                    self.logger.info(f"Filtering to exclude space keys: {excluded_space_keys}")

            # Pagination variables
            batch_size = 20
            cursor = None
            total_spaces_synced = 0
            total_permissions_synced = 0
            base_url = None  # Extract from first response
            record_groups = []

            # Paginate through all spaces using cursor
            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_spaces(
                    limit=batch_size,
                    cursor=cursor,
                    keys=included_space_keys  # None for NOT_IN (fetch all then filter), list for IN
                )

                # Check response
                if not response or response.status != HTTP_STATUS_200:
                    self.logger.error(f"❌ Failed to fetch spaces: {response.status if response else 'No response'}")
                    break

                response_data = response.json()
                spaces_data = response_data.get("results", [])

                # Extract base URL from first response
                if not base_url and response_data.get("_links", {}).get("base"):
                    base_url = response_data["_links"]["base"]
                    self.logger.debug(f"Base URL extracted: {base_url}")

                if not spaces_data:
                    break

                # Apply client-side exclusion filter if NOT_IN
                if excluded_space_keys:
                    original_count = len(spaces_data)
                    spaces_data = [
                        space for space in spaces_data
                        if space.get("key") not in excluded_space_keys
                    ]
                    filtered_count = original_count - len(spaces_data)
                    if filtered_count > 0:
                        self.logger.debug(f"Filtered out {filtered_count} excluded spaces from batch")

                # Process each space
                record_groups_with_permissions = []
                for space_data in spaces_data:
                    try:
                        space_id = space_data.get("id")
                        space_name = space_data.get("name")

                        if not space_id or not space_name:
                            continue

                        self.logger.debug(f"Processing space: {space_name} ({space_id})")

                        # Fetch permissions for this space
                        permissions = await self._fetch_space_permissions(space_id, space_name)
                        total_permissions_synced += len(permissions)

                        # Create RecordGroup for space
                        record_group = self._transform_to_space_record_group(space_data, base_url)
                        if not record_group:
                            continue

                        # Add to batch
                        record_groups_with_permissions.append((record_group, permissions))
                        record_groups.append(record_group)
                        total_spaces_synced += 1
                        self.logger.debug(f"Space {space_name}: {len(permissions)} permissions")

                    except Exception as space_error:
                        self.logger.error(f"❌ Failed to process space {space_data.get('name')}: {space_error}")
                        continue

                # Save batch to database
                if record_groups_with_permissions:
                    await self.data_entities_processor.on_new_record_groups(record_groups_with_permissions)
                    self.logger.info(f"Synced batch of {len(record_groups_with_permissions)} spaces")

                # Extract next cursor from _links.next
                next_url = response_data.get("_links", {}).get("next")
                if not next_url:
                    break

                cursor = self._extract_cursor_from_next_link(next_url)
                if not cursor:
                    break

            self.logger.info(f"✅ Space sync complete. Spaces: {total_spaces_synced}, Permissions: {total_permissions_synced}")

            return record_groups

        except Exception as e:
            self.logger.error(f"❌ Space sync failed: {e}", exc_info=True)
            raise

    async def _sync_content(self, space_key: str, record_type: RecordType) -> None:
        """
        Unified sync for pages and blogposts from Confluence using v1 API.

        Uses cursor-based pagination with modification time filtering for incremental sync.
        Creates WebpageRecord for each content item with attachments, comments, and permissions.

        Args:
            space_key: The space key to sync content from
            record_type: RecordType.CONFLUENCE_PAGE or RecordType.CONFLUENCE_BLOGPOST
        """
        # Derive content_type from record_type for logging and sync point
        content_type = "page" if record_type == RecordType.CONFLUENCE_PAGE else "blogpost"

        try:
            self.logger.info(f"Starting {content_type} synchronization for space {space_key}...")
            # Get indexing filter settings based on content type (default=True means index if not configured)
            content_ids_filter = None
            if record_type == RecordType.CONFLUENCE_PAGE:
                content_indexing_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.PAGES)
                content_comments_indexing_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.PAGE_COMMENTS)
                content_attachments_indexing_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.PAGE_ATTACHMENTS)
                content_ids_filter = self.sync_filters.get(SyncFilterKey.PAGE_IDS)
            else:  # CONFLUENCE_BLOGPOST
                content_indexing_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.BLOGPOSTS)
                content_comments_indexing_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.BLOGPOST_COMMENTS)
                content_attachments_indexing_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.BLOGPOST_ATTACHMENTS)
                content_ids_filter = self.sync_filters.get(SyncFilterKey.BLOGPOST_IDS)

            # Get content IDs filter based on content type
            content_ids = None
            content_ids_operator_str = None
            if content_ids_filter is not None:
                content_ids = content_ids_filter.get_value()
                content_ids_operator = content_ids_filter.get_operator()
                # Extract operator value string for datasource
                content_ids_operator_str = content_ids_operator.value if hasattr(content_ids_operator, 'value') else str(content_ids_operator)
                if content_ids:
                    action = "Excluding" if content_ids_operator_str == "not_in" else "Including"
                    self.logger.info(f"🔍 Filter: {action} {content_type}s by IDs: {content_ids}")

            # Get last sync checkpoint (use content_type as suffix)
            sync_point_key = generate_record_sync_point_key(
                RecordType.WEBPAGE.value, f"confluence_{content_type}s", space_key
            )
            last_sync_data = await self.pages_sync_point.read_sync_point(sync_point_key)
            last_sync_time = last_sync_data.get("last_sync_time") if last_sync_data else None
            if last_sync_time:
                self.logger.info(f"🔄 Incremental sync: Fetching {content_type}s modified after {last_sync_time}")

            # Build date filter parameters from sync filters
            # Get modified filter
            modified_filter = self.sync_filters.get(SyncFilterKey.MODIFIED)
            modified_after = None
            modified_before = None

            if modified_filter:
                modified_after, modified_before = modified_filter.get_datetime_iso()

            # Get created filter
            created_filter = self.sync_filters.get(SyncFilterKey.CREATED)
            created_after = None
            created_before = None

            if created_filter:
                created_after, created_before = created_filter.get_datetime_iso()

            # Merge modified_after with checkpoint (use the latest)
            if modified_after and last_sync_time:
                modified_after = max(modified_after, last_sync_time)
                self.logger.info(f"🔄 Using latest modified_after: {modified_after} (filter: {modified_after}, checkpoint: {last_sync_time})")
            elif modified_after:
                self.logger.info(f"🔍 Using filter: Fetching {content_type}s modified after {modified_after}")
            elif last_sync_time:
                modified_after = last_sync_time
                self.logger.info(f"🔄 Incremental sync: Fetching {content_type}s modified after {modified_after}")
            else:
                self.logger.info(f"🆕 Full sync: Fetching all {content_type}s (first time)")

            # Log other filters if set
            if modified_before:
                self.logger.info(f"🔍 Filter: Fetching {content_type}s modified before {modified_before}")
            if created_after:
                self.logger.info(f"🔍 Filter: Fetching {content_type}s created after {created_after}")
            if created_before:
                self.logger.info(f"🔍 Filter: Fetching {content_type}s created before {created_before}")

            # Pagination variables
            batch_size = 50
            cursor = None
            total_synced = 0
            total_attachments_synced = 0
            total_comments_synced = 0
            total_permissions_synced = 0
            latest_update_time = None

            # Paginate through all content items
            while True:
                datasource = await self._get_fresh_datasource()

                if record_type == RecordType.CONFLUENCE_PAGE:
                    response = await datasource.get_pages_v1(
                        modified_after=modified_after,
                        modified_before=modified_before,
                        created_after=created_after,
                        created_before=created_before,
                        cursor=cursor,
                        limit=batch_size,
                        space_key=space_key,
                        page_ids=content_ids,
                        page_ids_operator=content_ids_operator_str,
                        include_children=True,
                        order_by="lastModified",
                        sort_order="asc",
                        expand=CONTENT_EXPAND_PARAMS,
                        time_offset_hours=TIME_OFFSET_HOURS
                    )
                else:  # CONFLUENCE_BLOGPOST
                    response = await datasource.get_blogposts_v1(
                        modified_after=modified_after,
                        modified_before=modified_before,
                        created_after=created_after,
                        created_before=created_before,
                        cursor=cursor,
                        limit=batch_size,
                        space_key=space_key,
                        blogpost_ids=content_ids,
                        blogpost_ids_operator=content_ids_operator_str,
                        order_by="lastModified",
                        sort_order="asc",
                        expand=CONTENT_EXPAND_PARAMS,
                        time_offset_hours=TIME_OFFSET_HOURS
                    )

                # Check response
                if not response or response.status != HTTP_STATUS_200:
                    self.logger.error(f"❌ Failed to fetch {content_type}s: {response.status if response else 'No response'}")
                    break

                response_data = response.json()
                items_data = response_data.get("results", [])

                if not items_data:
                    break

                # Track the latest update timestamp for checkpoint (items are in ascending order)
                last_item = items_data[-1]
                last_updated_when = last_item.get("history", {}).get("lastUpdated", {}).get("when")
                if last_updated_when:
                    latest_update_time = last_updated_when

                # Transform items to WebpageRecords with permissions
                records_with_permissions = []
                for item_data in items_data:
                    try:
                        item_id = item_data.get("id")
                        item_title = item_data.get("title")

                        if not item_id or not item_title:
                            continue

                        self.logger.debug(f"Processing {content_type}: {item_title} ({item_id})")

                        # Transform to WebpageRecord
                        webpage_record = self._transform_to_webpage_record(item_data, record_type)
                        if not webpage_record:
                            continue

                        # Set indexing status based on filter
                        if not content_indexing_enabled:
                            webpage_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                        # Fetch page permissions
                        permissions = await self._fetch_page_permissions(item_id)
                        total_permissions_synced += len(permissions)

                        # Add item to batch
                        records_with_permissions.append((webpage_record, permissions))
                        total_synced += 1
                        self.logger.debug(f"{content_type.capitalize()} {item_title}: {len(permissions)} permissions")

                        # Extract space_id for children
                        space_data = item_data.get("space", {})
                        space_id = str(space_data.get("id")) if space_data.get("id") else None

                        # Process comments
                        child_types = item_data.get("childTypes", {})
                        comment_info = child_types.get("comment", {})
                        has_comments = comment_info.get("value", False)

                        if has_comments:
                            self.logger.debug(f"{content_type.capitalize()} {item_title} has comments, fetching...")

                            # Fetch comments (footer and inline)
                            for comment_type in ["footer", "inline"]:
                                comments = await self._fetch_comments_recursive(
                                    item_id,
                                    item_title,
                                    comment_type,
                                    permissions,
                                    space_id,
                                    content_type
                                )
                                # Set indexing status for comments if disabled
                                for comment_record, comment_permissions in comments:
                                    if not content_comments_indexing_enabled:
                                        comment_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value
                                records_with_permissions.extend(comments)
                                total_comments_synced += len(comments)

                        # Process attachments
                        children = item_data.get("children", {})
                        attachment_data = children.get("attachment", {})
                        attachments = attachment_data.get("results", [])

                        if attachments:
                            self.logger.debug(f"Found {len(attachments)} attachments for {content_type} {item_title}")

                            for attachment in attachments:
                                try:
                                    attachment_record = self._transform_to_attachment_file_record(
                                        attachment,
                                        item_id,
                                        space_id
                                    )

                                    if attachment_record:
                                        # Set indexing status based on filter
                                        if not content_attachments_indexing_enabled:
                                            attachment_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value
                                        # Attachments inherit permissions from parent
                                        records_with_permissions.append((attachment_record, permissions))
                                        total_attachments_synced += 1
                                        self.logger.debug(f"Attachment: {attachment_record.record_name}")

                                except Exception as att_error:
                                    self.logger.error(f"❌ Failed to process attachment: {att_error}")
                                    continue

                    except Exception as item_error:
                        self.logger.error(f"❌ Failed to process {content_type} {item_data.get('title')}: {item_error}")
                        continue

                # Save batch to database
                if records_with_permissions:
                    await self.data_entities_processor.on_new_records(records_with_permissions)
                    self.logger.info(f"Synced batch of {len(records_with_permissions)} items ({content_type}s + attachments + comments)")

                # Extract next cursor from response
                cursor_url = response_data.get("_links", {}).get("next")
                if not cursor_url:
                    break

                cursor = self._extract_cursor_from_next_link(cursor_url)
                if not cursor:
                    break

            # Update sync checkpoint with latest modification time
            if latest_update_time:
                await self.pages_sync_point.update_sync_point(sync_point_key, {"last_sync_time": latest_update_time})
                self.logger.info(f"Updated {content_type}s sync checkpoint to {latest_update_time}")

            self.logger.info(f"✅ {content_type.capitalize()} sync complete. {content_type.capitalize()}s: {total_synced}, Attachments: {total_attachments_synced}, Comments: {total_comments_synced}, Permissions: {total_permissions_synced}")

        except Exception as e:
            self.logger.error(f"❌ {content_type.capitalize()} sync failed: {e}", exc_info=True)
            raise

    async def _sync_permission_changes_from_audit_log(self) -> None:
        """
        Sync permission changes for pages/blogs using Confluence Audit Log API.

        This method tracks permission changes that don't update the content's
        lastModified timestamp, ensuring we capture:
        - Content restriction added (user/group gets access)
        - Content restriction removed (user/group loses access)

        Flow:
        1. Get last audit sync time
        2. If first run (no checkpoint): Initialize with current time and skip (permissions already synced)
        3. If subsequent run: Fetch audit logs since last sync
        4. Extract unique content titles from audit records
        5. Search content by titles and check if exists in DB
        6. For each existing content: Fetch current permissions and update
        7. Update audit log sync point with current timestamp

        Note: First run initializes checkpoint but skips permission sync because
        the initial content sync (_sync_content) already synced all permissions.
        """
        try:
            self.logger.info("🔍 Starting permission sync from audit log...")

            # Sync point key for audit log
            audit_sync_key = generate_record_sync_point_key(RecordType.WEBPAGE.value, "permissions", "audit_log")

            # Get last audit sync timestamp
            last_audit_sync = await self.audit_log_sync_point.read_sync_point(audit_sync_key)
            last_sync_time_ms = last_audit_sync.get("last_sync_time_ms") if last_audit_sync else None

            # Current time as checkpoint
            current_time_ms = int(datetime.now().timestamp() * 1000)

            # First run: Initialize checkpoint and skip (permissions already synced during content sync)
            if last_sync_time_ms is None:
                self.logger.info(
                    "🆕 First audit log sync - initializing checkpoint to current time and skipping. "
                    "Permissions already synced during content sync."
                )

                # Save initial checkpoint
                await self.audit_log_sync_point.update_sync_point(
                    audit_sync_key,
                    {"last_sync_time_ms": current_time_ms}
                )
                return

            self.logger.info(f"🔄 Fetching audit logs from {last_sync_time_ms} to {current_time_ms}")

            # Fetch audit logs and extract content titles that had permission changes
            content_titles = await self._fetch_permission_audit_logs(last_sync_time_ms, current_time_ms)

            if not content_titles:
                self.logger.info("✅ No permission changes found in audit log")
                # Update sync point even if no changes
                await self.audit_log_sync_point.update_sync_point(
                    audit_sync_key,
                    {"last_sync_time_ms": current_time_ms}
                )
                return

            self.logger.info(f"📋 Found {len(content_titles)} content items with permission changes")

            # Search for content by titles and sync their permissions (only if exists in DB)
            await self._sync_content_permissions_by_titles(content_titles)

            # Update audit log sync point with current time
            await self.audit_log_sync_point.update_sync_point(
                audit_sync_key,
                {"last_sync_time_ms": current_time_ms}
            )

            self.logger.info("✅ Permission sync from audit log completed")

        except Exception as e:
            self.logger.error(f"❌ Permission sync from audit log failed: {e}", exc_info=True)
            raise

    async def _fetch_permission_audit_logs(
        self,
        start_date_ms: int,
        end_date_ms: int
    ) -> List[str]:
        """
        Fetch audit logs and extract content titles that had permission changes.

        Filters for:
        - category = "Permissions"
        - scope = content (pages/blogs, not global or space-level)

        Args:
            start_date_ms: Start timestamp in milliseconds
            end_date_ms: End timestamp in milliseconds

        Returns:
            List of unique content titles (pages/blogs) that had permission changes
        """
        content_titles_set: set[str] = set()
        batch_size = 100
        start = 0

        while True:
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_audit_logs(
                start_date=start_date_ms,
                end_date=end_date_ms,
                start=start,
                limit=batch_size
            )

            if not response or response.status != HTTP_STATUS_200:
                self.logger.warning(f"⚠️ Failed to fetch audit logs: {response.status if response else 'No response'}")
                break

            response_data = response.json()
            audit_records = response_data.get("results", [])

            if not audit_records:
                break

            # Process each audit record
            for record in audit_records:
                content_title = self._extract_content_title_from_audit_record(record)
                if content_title:
                    content_titles_set.add(content_title)

            # Check for more pages
            size = response_data.get("size", 0)
            if size < batch_size:
                break

            start += batch_size

        return list(content_titles_set)

    def _extract_content_title_from_audit_record(self, record: Dict[str, Any]) -> Optional[str]:
        """
        Extract content title from an audit record if it's a content permission change.

        Filters for:
        - category = "Permissions"
        - Has a Page or Blog in associatedObjects (content-level permission)
        - Has a Space in associatedObjects (confirms it's content, not global)

        Args:
            record: Raw audit log record

        Returns:
            Content title (page/blog) or None if not a content permission change
        """
        # Must be a permission-related event
        if record.get("category") != "Permissions":
            return None

        associated_objects = record.get("associatedObjects", [])

        # Check for content-level permission (must have both content AND space)
        has_space = any(obj.get("objectType") == "Space" for obj in associated_objects)
        content_obj = next(
            (obj for obj in associated_objects if obj.get("objectType") in ["Page", "Blog"]),
            None
        )

        # Content restriction must have both page/blog AND space
        if not has_space or not content_obj:
            return None

        return content_obj.get("name")


    async def _sync_content_permissions_by_titles(self, titles: List[str]) -> None:
        """
        Search for content by titles and sync their current permissions.

        IMPORTANT: This method ONLY updates permissions for records that already exist in the database.
        It will NOT create new records, ensuring sync filters are respected.

        For each found content item:
        1. Check if record exists in DB (by external_record_id)
        2. If exists: Fetch current permissions and update
        3. If not exists: Skip (record was filtered out during initial sync)

        Args:
            titles: List of content titles to search for
        """
        if not titles:
            return

        # Batch titles to avoid CQL query size limits (process 50 at a time)
        batch_size = 50
        total_synced = 0
        total_skipped = 0
        total_permissions = 0

        for i in range(0, len(titles), batch_size):
            batch_titles = titles[i:i + batch_size]

            try:
                datasource = await self._get_fresh_datasource()
                response = await datasource.search_content_by_titles(
                    titles=batch_titles,
                    expand="version,space,history.lastUpdated,ancestors"
                )

                if not response or response.status != HTTP_STATUS_200:
                    self.logger.warning(f"⚠️ Failed to search content by titles: {response.status if response else 'No response'}")
                    continue

                response_data = response.json()
                content_items = response_data.get("results", [])

                if not content_items:
                    self.logger.debug(f"No content found for titles batch {i // batch_size + 1}")
                    continue

                # Process each content item
                records_with_permissions = []
                for item_data in content_items:
                    try:
                        item_id = item_data.get("id")
                        item_title = item_data.get("title")
                        item_type = item_data.get("type", "").lower()

                        if not item_id or not item_title:
                            continue

                        # Determine record type
                        if item_type == "page":
                            record_type = RecordType.CONFLUENCE_PAGE
                        elif item_type == "blogpost":
                            record_type = RecordType.CONFLUENCE_BLOGPOST
                        else:
                            self.logger.debug(f"Skipping unknown content type: {item_type}")
                            continue

                        # Check if record exists in database (respects sync filters)
                        existing_record = await self.data_store.get_record_by_external_id(
                            external_record_id=item_id
                        )

                        if not existing_record:
                            # Record doesn't exist - it was filtered out during initial sync
                            self.logger.debug(
                                f"Skipping {item_type} '{item_title}' ({item_id}) - "
                                f"not in database (filtered out during sync)"
                            )
                            total_skipped += 1
                            continue

                        self.logger.debug(f"Updating permissions for {item_type}: {item_title} ({item_id})")

                        # Transform to WebpageRecord
                        webpage_record = self._transform_to_webpage_record(item_data, record_type)
                        if not webpage_record:
                            continue

                        # Fetch current permissions
                        permissions = await self._fetch_page_permissions(item_id)
                        total_permissions += len(permissions)

                        # Add to batch for update
                        records_with_permissions.append((webpage_record, permissions))
                        total_synced += 1

                    except Exception as item_error:
                        self.logger.error(f"❌ Failed to sync permissions for {item_data.get('title')}: {item_error}")
                        continue

                # Update batch in database
                if records_with_permissions:
                    await self.data_entities_processor.on_new_records(records_with_permissions)
                    self.logger.info(f"Updated permissions for {len(records_with_permissions)} content items")

            except Exception as batch_error:
                self.logger.error(f"❌ Failed to process titles batch: {batch_error}")
                continue

        if total_skipped > 0:
            self.logger.info(f"🔍 Skipped {total_skipped} items not in database (filtered during sync)")

        self.logger.info(f"✅ Permission sync complete. Items updated: {total_synced}, Permissions: {total_permissions}")

    async def _fetch_space_permissions(self, space_id: str, space_name: str) -> List[Permission]:
        """
        Fetch all permissions for a space with cursor-based pagination.

        Args:
            space_id: The space ID
            space_name: The space name (for logging)

        Returns:
            List of Permission objects
        """
        try:
            permissions = []
            batch_size = 100
            cursor = None

            # Paginate through space permissions
            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_space_permissions_assignments(
                    id=space_id,
                    limit=batch_size,
                    cursor=cursor
                )

                # Check response
                if not response or response.status != HTTP_STATUS_200:
                    self.logger.warning(f"⚠️ Failed to fetch permissions for space {space_name}: {response.status if response else 'No response'}")
                    break

                response_data = response.json()
                permissions_data = response_data.get("results", [])

                if not permissions_data:
                    break

                # Transform permissions and add to list
                for perm_data in permissions_data:
                    permission = await self._transform_space_permission(perm_data)
                    if permission:
                        permissions.append(permission)

                # Extract next cursor
                next_url = response_data.get("_links", {}).get("next")
                if not next_url:
                    break

                cursor = self._extract_cursor_from_next_link(next_url)
                if not cursor:
                    break

            return permissions

        except Exception as e:
            self.logger.error(f"❌ Failed to fetch permissions for space {space_name}: {e}")
            return []  # Return empty list on error, space will be created without permissions

    async def _fetch_page_permissions(self, page_id: str) -> List[Permission]:
        """
        Fetch permissions for a Confluence page using v1 API.

        Args:
            page_id: The page ID

        Returns:
            List of Permission objects
        """
        permissions = []

        try:
            self.logger.debug(f"Fetching permissions for page: {page_id}")

            # Fetch page restrictions using v1 API
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_page_permissions_v1(
                page_id=page_id
            )

            # Check response
            if not response or response.status != HTTP_STATUS_200:
                self.logger.warning(f"⚠️ Failed to fetch permissions for page {page_id}: {response.status if response else 'No response'}")
                return []

            response_data = response.json()
            restrictions = response_data.get("results", [])

            # Process each restriction (read and update operations)
            for restriction_data in restrictions:
                operation_permissions = await self._transform_page_restriction_to_permissions(restriction_data)
                permissions.extend(operation_permissions)

            self.logger.debug(f"Found {len(permissions)} permissions for page {page_id}")
            return permissions

        except Exception as e:
            self.logger.error(f"❌ Failed to fetch permissions for page {page_id}: {e}")
            return []  # Return empty list on error, page will be created without permissions

    async def _fetch_comments_recursive(
        self,
        page_id: str,
        page_title: str,
        comment_type: str,
        page_permissions: List[Permission],
        parent_space_id: Optional[str],
        parent_type: str = "page"
    ) -> List[tuple[CommentRecord, List[Permission]]]:
        """
        Recursively fetch all comments (footer or inline) for a page or blogpost.

        Fetches top-level comments and all nested replies in a flat list.
        Each comment inherits permissions from the parent.

        Args:
            page_id: The page/blogpost ID
            page_title: The page/blogpost title (for logging)
            comment_type: "footer" or "inline"
            page_permissions: Permissions inherited from parent
            parent_space_id: Space ID for external_record_group_id
            parent_type: "page" or "blogpost" (determines which API to call)

        Returns:
            List of tuples (CommentRecord, permissions list)
        """
        try:
            all_comments = []
            batch_size = 100
            cursor = None

            self.logger.debug(f"Fetching {comment_type} comments for {parent_type}: {page_title}")

            # Fetch top-level comments
            while True:
                datasource = await self._get_fresh_datasource()

                # Route to correct API based on parent_type
                if parent_type == "page":
                    if comment_type == "footer":
                        response = await datasource.get_page_footer_comments(
                            id=int(page_id),
                            cursor=cursor,
                            limit=batch_size,
                            body_format="storage"
                        )
                    else:  # inline
                        response = await datasource.get_page_inline_comments(
                            id=int(page_id),
                            cursor=cursor,
                            limit=batch_size,
                            body_format="storage"
                        )
                elif parent_type == "blogpost":
                    if comment_type == "footer":
                        response = await datasource.get_blog_post_footer_comments(
                            id=int(page_id),
                            cursor=cursor,
                            limit=batch_size,
                            body_format="storage"
                        )
                    else:  # inline
                        response = await datasource.get_blog_post_inline_comments(
                            id=int(page_id),
                            cursor=cursor,
                            limit=batch_size,
                            body_format="storage"
                        )
                else:
                    self.logger.error(f"Unknown parent type: {parent_type}")
                    break

                # Check response
                if not response or response.status != HTTP_STATUS_200:
                    self.logger.warning(f"⚠️ Failed to fetch {comment_type} comments for page {page_title}: {response.status if response else 'No response'}")
                    break

                response_data = response.json()
                comments_data = response_data.get("results", [])

                if not comments_data:
                    break

                # Process each comment
                for comment_data in comments_data:
                    try:
                        comment_id = comment_data.get("id")

                        if not comment_id:
                            continue

                        # Transform comment to CommentRecord
                        comment_record = self._transform_to_comment_record(
                            comment_data,
                            page_id,
                            parent_space_id,
                            comment_type,
                            None  # No parent comment for top-level
                        )

                        if comment_record:
                            all_comments.append((comment_record, page_permissions))

                        # Recursively fetch children
                        children = await self._fetch_comment_children_recursive(
                            comment_id,
                            comment_type,
                            page_id,
                            parent_space_id,
                            page_permissions
                        )
                        all_comments.extend(children)

                    except Exception as comment_error:
                        self.logger.error(f"❌ Failed to process comment {comment_data.get('id')}: {comment_error}")
                        continue

                # Extract next cursor
                next_url = response_data.get("_links", {}).get("next")
                if not next_url:
                    break

                cursor = self._extract_cursor_from_next_link(next_url)
                if not cursor:
                    break

            self.logger.debug(f"✓ Fetched {len(all_comments)} {comment_type} comments (including replies) for page {page_title}")
            return all_comments

        except Exception as e:
            self.logger.error(f"❌ Failed to fetch {comment_type} comments for page {page_title}: {e}")
            return []

    async def _fetch_comment_children_recursive(
        self,
        comment_id: str,
        comment_type: str,
        page_id: str,
        parent_space_id: Optional[str],
        page_permissions: List[Permission]
    ) -> List[tuple[CommentRecord, List[Permission]]]:
        """
        Recursively fetch all children (replies) of a comment.

        Args:
            comment_id: The parent comment ID
            comment_type: "footer" or "inline"
            page_id: The parent page ID
            parent_space_id: Space ID for external_record_group_id
            page_permissions: Permissions inherited from parent page

        Returns:
            List of tuples (CommentRecord, permissions list)
        """
        try:
            all_children = []
            batch_size = 100
            cursor = None

            # Fetch children comments
            while True:
                datasource = await self._get_fresh_datasource()
                if comment_type == "footer":
                    response = await datasource.get_footer_comment_children(
                        id=int(comment_id),
                        cursor=cursor,
                        limit=batch_size,
                        body_format="storage"
                    )
                else:  # inline
                    response = await datasource.get_inline_comment_children(
                        id=int(comment_id),
                        cursor=cursor,
                        limit=batch_size,
                        body_format="storage"
                    )

                # Check response
                if not response or response.status != HTTP_STATUS_200:
                    break

                response_data = response.json()
                children_data = response_data.get("results", [])

                if not children_data:
                    break

                # Process each child comment
                for child_data in children_data:
                    try:
                        child_id = child_data.get("id")

                        if not child_id:
                            continue

                        # Transform child to CommentRecord
                        child_record = self._transform_to_comment_record(
                            child_data,
                            page_id,
                            parent_space_id,
                            comment_type,
                            comment_id  # Parent comment ID
                        )

                        if child_record:
                            all_children.append((child_record, page_permissions))

                        # Recursively fetch grandchildren
                        grandchildren = await self._fetch_comment_children_recursive(
                            child_id,
                            comment_type,
                            page_id,
                            parent_space_id,
                            page_permissions
                        )
                        all_children.extend(grandchildren)

                    except Exception as child_error:
                        self.logger.error(f"❌ Failed to process child comment {child_data.get('id')}: {child_error}")
                        continue

                # Extract next cursor
                next_url = response_data.get("_links", {}).get("next")
                if not next_url:
                    break

                cursor = self._extract_cursor_from_next_link(next_url)
                if not cursor:
                    break

            return all_children

        except Exception as e:
            self.logger.error(f"❌ Failed to fetch children for comment {comment_id}: {e}")
            return []

    def _transform_to_comment_record(
        self,
        comment_data: Dict[str, Any],
        page_id: str,
        parent_space_id: Optional[str],
        comment_type: str,
        parent_comment_id: Optional[str]
    ) -> Optional[CommentRecord]:
        """
        Transform Confluence comment data to CommentRecord entity.

        Args:
            comment_data: Raw comment data from Confluence API
            page_id: Parent page external_record_id
            parent_space_id: Space ID from parent page
            comment_type: "footer" or "inline"
            parent_comment_id: Parent comment ID (None for top-level comments)

        Returns:
            CommentRecord object or None if transformation fails
        """
        try:
            comment_id = comment_data.get("id")
            title = comment_data.get("title", "")

            if not comment_id:
                return None

            # Extract author accountId
            author = comment_data.get("version", {}).get("authorId")
            if not author:
                self.logger.warning(f"Comment {comment_id} has no author - skipping")
                return None

            # Parse timestamps
            source_created_at = None

            created_at_str = comment_data.get("version", {}).get("createdAt")
            if created_at_str:
                source_created_at = self._parse_confluence_datetime(created_at_str)

            # Extract resolution status (for inline comments)
            resolution_status = None
            if comment_type == "inline":
                is_resolved = comment_data.get("resolutionStatus", False)
                resolution_status = "resolved" if is_resolved else "open"

            # Extract inline original selection (for inline comments)
            inline_original_selection = None
            if comment_type == "inline":
                inline_properties = comment_data.get("properties", {})
                if inline_properties:
                    inline_original_selection = inline_properties.get("inlineOriginalSelection")

            # Determine parent record ID and type
            parent_external_record_id = parent_comment_id if parent_comment_id else page_id
            parent_record_type = RecordType.COMMENT if parent_comment_id else RecordType.WEBPAGE

            # Generate unique ID for comment
            comment_record_id = str(uuid.uuid4())

            version_number = comment_data.get("version", {}).get("number", 0)

            return CommentRecord(
                id=comment_record_id,
                org_id=self.data_entities_processor.org_id,
                record_name=title,
                record_type=RecordType.INLINE_COMMENT if comment_type == "inline" else RecordType.COMMENT,
                external_record_id=comment_id,
                external_revision_id=str(version_number) if version_number else None,
                version=0,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.CONFLUENCE,
                mime_type=MimeTypes.HTML.value,
                parent_external_record_id=parent_external_record_id,
                parent_record_type=parent_record_type,
                external_record_group_id=parent_space_id,
                record_group_type=RecordGroupType.CONFLUENCE_SPACES,
                source_created_at=source_created_at,
                source_updated_at=source_created_at,
                author_source_id=author,
                resolution_status=resolution_status,
                comment_selection=inline_original_selection,
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform comment: {e}")
            return None

    def _extract_cursor_from_next_link(self, next_url: str) -> Optional[str]:
        """
        Extract cursor value from _links.next URL.

        Args:
            next_url: The next URL from API response
            Example: "/wiki/api/v2/spaces?limit=20&cursor=eyJ..."

        Returns:
            Cursor string or None if not found
        """
        try:
            if not next_url:
                return None

            parsed = urlparse(next_url)
            query_params = parse_qs(parsed.query)

            # Get cursor value (could be list if multiple, take first)
            cursor_values = query_params.get("cursor", [])
            if cursor_values:
                return cursor_values[0]

            return None

        except Exception as e:
            self.logger.error(f"❌ Failed to extract cursor from URL '{next_url}': {e}")
            return None

    async def _create_permission_from_principal(
        self,
        principal_type: str,
        principal_id: str,
        permission_type: PermissionType
    ) -> Optional[Permission]:
        """
        Create Permission object from principal data (user or group).

        This is a common function used by both space and page permission processing.

        Args:
            principal_type: "user" or "group"
            principal_id: accountId for users, groupId for groups
            permission_type: Mapped PermissionType enum

        Returns:
            Permission object or None if principal not found in DB
        """
        try:
            if principal_type == "user":
                entity_type = EntityType.USER
                # Lookup user by source_user_id (accountId) using transaction store
                async with self.data_store_provider.transaction() as tx_store:
                    user = await tx_store.get_user_by_source_id(
                        principal_id,
                        Connectors.CONFLUENCE,
                    )
                    if not user:
                        self.logger.debug(f"  ⚠️ User {principal_id} not found in DB, skipping permission")
                        return None

                    return Permission(
                        email=user.email,
                        type=permission_type,
                        entity_type=entity_type
                    )

            elif principal_type == "group":
                entity_type = EntityType.GROUP
                # Lookup group by source_user_group_id using transaction store
                async with self.data_store_provider.transaction() as tx_store:
                    group = await tx_store.get_user_group_by_external_id(
                        Connectors.CONFLUENCE,
                        principal_id,
                    )
                    if not group:
                        self.logger.debug(f"  ⚠️ Group {principal_id} not found in DB, skipping permission")
                        return None

                    return Permission(
                        external_id=group.source_user_group_id,
                        type=permission_type,
                        entity_type=entity_type
                    )

            return None

        except Exception as e:
            self.logger.error(f"❌ Failed to create permission from principal: {e}")
            return None

    async def _transform_space_permission(self, perm_data: Dict[str, Any]) -> Optional[Permission]:
        """
        Transform Confluence space permission to Permission object.

        Maps Confluence operations to PermissionType:
        - administer → OWNER
        - read → READ
        - create/delete (comment) → COMMENT
        - create/delete/archive (page/blogpost/attachment) → WRITE
        - restrict_content/export → OTHER
        - delete (space) → OWNER

        Args:
            perm_data: Raw permission data from Confluence API

        Returns:
            Permission object or None if invalid or user/group not found in DB
        """
        try:
            principal = perm_data.get("principal", {})
            operation = perm_data.get("operation", {})

            principal_type = principal.get("type")  # "user" or "group"
            principal_id = principal.get("id")  # accountId or groupId
            operation_key = operation.get("key")  # e.g., "read", "administer"
            target_type = operation.get("targetType")  # e.g., "space", "page"

            if not principal_type or not principal_id or not operation_key:
                return None

            # Map Confluence permission to PermissionType
            permission_type = self._map_confluence_permission(operation_key, target_type)

            # Use common function to create permission
            return await self._create_permission_from_principal(
                principal_type,
                principal_id,
                permission_type
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform space permission: {e}")
            return None

    def _map_confluence_permission(self, operation_key: str, target_type: str) -> PermissionType:
        """
        Map Confluence operation to PermissionType enum.

        Mapping logic:
        - administer → OWNER
        - read → READ
        - create/delete (comment) → COMMENT
        - create/delete/archive (page/blogpost/attachment) → WRITE
        - restrict_content/export → OTHER
        - delete (space) → OWNER

        Args:
            operation_key: Operation key (e.g., "read", "create", "delete")
            target_type: Target type (e.g., "space", "page", "comment")

        Returns:
            PermissionType enum value
        """
        # Administer = OWNER
        if operation_key == "administer":
            return PermissionType.OWNER

        # Read = READ
        if operation_key == "read":
            return PermissionType.READ

        # Delete space = OWNER
        if operation_key == "delete" and target_type == "space":
            return PermissionType.OWNER

        # Comment operations = COMMENT
        if target_type == "comment" and operation_key in ["create", "delete"]:
            return PermissionType.COMMENT

        # Page/blogpost/attachment operations = WRITE
        if target_type in ["page", "blogpost", "attachment"]:
            if operation_key in ["create", "delete", "archive"]:
                return PermissionType.WRITE

        # Everything else = OTHER
        return PermissionType.OTHER

    def _map_page_permission(self, operation: str) -> PermissionType:
        """
        Map page restriction operation to PermissionType enum.

        Page restrictions only have two operations:
        - read → READ
        - update → WRITE

        Args:
            operation: Operation string ("read" or "update")

        Returns:
            PermissionType enum value
        """
        if operation == "read":
            return PermissionType.READ
        elif operation == "update":
            return PermissionType.WRITE
        else:
            return PermissionType.OTHER

    async def _transform_page_restriction_to_permissions(
        self,
        restriction_data: Dict[str, Any]
    ) -> List[Permission]:
        """
        Transform page restriction data (from v1 API) to Permission objects.

        The v1 API returns restrictions in this format:
        {
            "operation": "read" | "update",
            "restrictions": {
                "user": {
                    "results": [{"type": "known", "accountId": "...", "displayName": "..."}]
                },
                "group": {
                    "results": [{"type": "group", "name": "...", "id": "..."}]
                }
            }
        }

        Args:
            restriction_data: Single restriction object with operation and restrictions

        Returns:
            List of Permission objects
        """
        permissions = []

        try:
            operation = restriction_data.get("operation")
            if not operation:
                return permissions

            # Map operation to PermissionType
            permission_type = self._map_page_permission(operation)

            restrictions = restriction_data.get("restrictions", {})

            # Process user restrictions
            user_restrictions = restrictions.get("user", {})
            user_results = user_restrictions.get("results", [])

            for user_data in user_results:
                # Extract accountId (could be under different keys)
                principal_id = user_data.get("accountId") or user_data.get("id")
                if principal_id:
                    permission = await self._create_permission_from_principal(
                        "user",
                        principal_id,
                        permission_type
                    )
                    if permission:
                        permissions.append(permission)

            # Process group restrictions
            group_restrictions = restrictions.get("group", {})
            group_results = group_restrictions.get("results", [])

            for group_data in group_results:
                principal_id = group_data.get("id")
                if principal_id:
                    permission = await self._create_permission_from_principal(
                        "group",
                        principal_id,
                        permission_type
                    )
                    if permission:
                        permissions.append(permission)

        except Exception as e:
            self.logger.error(f"❌ Failed to transform page restriction: {e}")

        return permissions

    def _transform_to_space_record_group(
        self,
        space_data: Dict[str, Any],
        base_url: Optional[str] = None
    ) -> Optional[RecordGroup]:
        """
        Transform Confluence space data to RecordGroup entity.

        Args:
            space_data: Raw space data from Confluence API
            base_url: Base URL from API response (_links.base)

        Returns:
            RecordGroup object or None if transformation fails
        """
        try:
            space_id = space_data.get("id")
            space_name = space_data.get("name")
            space_description = space_data.get("description", "")
            space_key = space_data.get("key", "")

            if not space_id or not space_name:
                return None

            # Parse timestamps
            source_created_at = None
            created_at_str = space_data.get("createdAt")
            if created_at_str:
                source_created_at = self._parse_confluence_datetime(created_at_str)

            # Construct web URL: base + webui
            web_url = None
            if base_url:
                webui = space_data.get("_links", {}).get("webui")
                if webui:
                    web_url = f"{base_url}{webui}"

            return RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=space_name,
                short_name=space_key,
                description=space_description,
                external_group_id=space_id,
                connector_name=Connectors.CONFLUENCE,
                group_type=RecordGroupType.CONFLUENCE_SPACES,
                web_url=web_url,
                source_created_at=source_created_at,
                source_updated_at=source_created_at,  # Confluence doesn't provide updated timestamp for spaces
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform space: {e}")
            return None

    def _transform_to_webpage_record(
        self,
        data: Dict[str, Any],
        record_type: RecordType
    ) -> Optional[WebpageRecord]:
        """
        Unified transform for page/blogpost data to WebpageRecord.

        Args:
            data: Raw data from Confluence API
            record_type: RecordType.CONFLUENCE_PAGE or RecordType.CONFLUENCE_BLOGPOST

        Returns:
            WebpageRecord object or None if transformation fails
        """
        # Derive content_type for logging
        content_type = "page" if record_type == RecordType.CONFLUENCE_PAGE else "blogpost"

        try:
            item_id = data.get("id")
            item_title = data.get("title")

            if not item_id or not item_title:
                return None

            # Parse timestamps - v1 vs v2 have different structures
            source_created_at = None
            source_updated_at = None
            version_number = 0

            # Try v2 format first (createdAt at top level)
            created_at_v2 = data.get("createdAt")
            if created_at_v2:
                source_created_at = self._parse_confluence_datetime(created_at_v2)
            else:
                # Fall back to v1 format (history.createdDate)
                history = data.get("history", {})
                created_date = history.get("createdDate")
                if created_date:
                    source_created_at = self._parse_confluence_datetime(created_date)

            # Try v2 format for updated date and version (version.createdAt, version.number)
            version_data = data.get("version", {})
            if isinstance(version_data, dict):
                version_created_at = version_data.get("createdAt")
                if version_created_at:
                    source_updated_at = self._parse_confluence_datetime(version_created_at)
                version_number = version_data.get("number", 0)

            # Fall back to v1 format (history.lastUpdated.when, history.lastUpdated.number)
            if not source_updated_at:
                history = data.get("history", {})
                last_updated = history.get("lastUpdated", {})
                if isinstance(last_updated, dict):
                    updated_when = last_updated.get("when")
                    if updated_when:
                        source_updated_at = self._parse_confluence_datetime(updated_when)
                    if not version_number:
                        version_number = last_updated.get("number", 0)

            # Extract space ID - v2 has spaceId at top level, v1 has space.id
            external_record_group_id = None
            space_id_v2 = data.get("spaceId")  # v2 format
            if space_id_v2:
                external_record_group_id = str(space_id_v2)
            else:
                # v1 format
                space_data = data.get("space", {})
                space_id = space_data.get("id")
                external_record_group_id = str(space_id) if space_id else None

            if not external_record_group_id:
                self.logger.warning(f"{content_type.capitalize()} {item_id} has no space - skipping")
                return None

            # Extract parent page ID - v2 has parentId at top level, v1 uses ancestors
            parent_external_record_id = None
            parent_id_v2 = data.get("parentId")  # v2 format
            if parent_id_v2:
                parent_external_record_id = str(parent_id_v2)
            else:
                # v1 format - last ancestor is direct parent
                ancestors = data.get("ancestors", [])
                if ancestors and len(ancestors) > 0:
                    direct_parent = ancestors[-1]
                    parent_external_record_id = direct_parent.get("id")

            # Construct web URL - v1 vs v2 have different link structures
            web_url = None
            links = data.get("_links", {})
            webui = links.get("webui")

            if webui:
                # Try v2 format first (_links.base)
                base_url = links.get("base")
                if base_url:
                    web_url = f"{base_url}{webui}"
                else:
                    # Fall back to v1 format (extract from _links.self)
                    self_link = links.get("self")
                    if self_link and "/wiki/" in self_link:
                        base_url = self_link.split("/wiki/")[0] + "/wiki"
                        web_url = f"{base_url}{webui}"

            return WebpageRecord(
                org_id=self.data_entities_processor.org_id,
                record_name=item_title,
                record_type=record_type,
                external_record_id=item_id,
                external_revision_id=str(version_number) if version_number else None,
                version=0,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.CONFLUENCE,
                record_group_type=RecordGroupType.CONFLUENCE_SPACES,
                external_record_group_id=external_record_group_id,
                parent_external_record_id=parent_external_record_id,
                weburl=web_url,
                mime_type=MimeTypes.HTML.value,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform {content_type}: {e}")
            return None


    def _transform_to_attachment_file_record(
        self,
        attachment_data: Dict[str, Any],
        parent_external_record_id: str,
        parent_external_record_group_id: Optional[str]
    ) -> Optional[FileRecord]:
        """
        Transform Confluence attachment to FileRecord entity.
        Supports both v1 and v2 API response formats.

        Args:
            attachment_data: Raw attachment data from v1 (children.attachment.results) or v2 API
            parent_external_record_id: Parent page external_record_id
            parent_external_record_group_id: Space ID from parent page

        Returns:
            FileRecord object or None if transformation fails
        """
        try:
            # Get attachment ID - both v1 and v2 use "id" field with "att" prefix
            attachment_id = attachment_data.get("id")
            if not attachment_id:
                return None
            # Get filename - same field in both v1 and v2
            file_name = attachment_data.get("title")
            if not file_name:
                return None

            # Clean query params from filename if present
            if '?' in file_name:
                file_name = file_name.split('?')[0]

            # Parse timestamps - v1 vs v2 have different structures
            source_created_at = None
            source_updated_at = None
            version_number = 0

            # Try v2 format first (createdAt at top level)
            created_at_v2 = attachment_data.get("createdAt")
            if created_at_v2:
                source_created_at = self._parse_confluence_datetime(created_at_v2)
            else:
                # Fall back to v1 format (history.createdDate)
                history = attachment_data.get("history", {})
                created_date = history.get("createdDate")
                if created_date:
                    source_created_at = self._parse_confluence_datetime(created_date)

            # Try v2 format for updated date and version (version.createdAt, version.number)
            version_data = attachment_data.get("version", {})
            if isinstance(version_data, dict):
                version_created_at = version_data.get("createdAt")
                if version_created_at:
                    source_updated_at = self._parse_confluence_datetime(version_created_at)
                version_number = version_data.get("number", 0)

            # Fall back to v1 format (history.lastUpdated.when, history.lastUpdated.number)
            if not source_updated_at or not version_number:
                history = attachment_data.get("history", {})
                last_updated = history.get("lastUpdated", {})
                if isinstance(last_updated, dict):
                    if not source_updated_at:
                        updated_when = last_updated.get("when")
                        if updated_when:
                            source_updated_at = self._parse_confluence_datetime(updated_when)
                    if not version_number:
                        version_number = last_updated.get("number", 0)

            # Extract file size - v2 has it at top level, v1 in extensions
            file_size = attachment_data.get("fileSize")  # v2 format
            if file_size is None:
                extensions = attachment_data.get("extensions", {})
                file_size = extensions.get("fileSize")  # v1 format

            # Extract mime type - v2 has it at top level (mediaType), v1 in extensions or metadata
            media_type = attachment_data.get("mediaType")  # v2 format
            if not media_type:
                extensions = attachment_data.get("extensions", {})
                media_type = extensions.get("mediaType")  # v1 format (extensions)
            if not media_type:
                metadata = attachment_data.get("metadata", {})
                media_type = metadata.get("mediaType")  # v1 format (metadata)

            mime_type = None
            if media_type:
                # Try to map to MimeTypes enum
                for mime in MimeTypes:
                    if mime.value == media_type:
                        mime_type = mime
                        break

                # If not found in enum, use the raw value
                if not mime_type:
                    mime_type = media_type

            # Extract extension from filename
            extension = None
            if '.' in file_name:
                extension = file_name.split('.')[-1].lower()

            # Construct web URL - v1 vs v2 have different link structures
            web_url = None
            links = attachment_data.get("_links", {})
            web_path = links.get("webui")

            if web_path:
                # Try v2 format first (_links.base)
                base_url = links.get("base")
                if base_url:
                    web_url = f"{base_url}{web_path}"
                else:
                    # Fall back to v1 format (extract from _links.self)
                    self_link = links.get("self")
                    if self_link and "https://" in self_link:
                        if "/wiki/" in self_link:
                            base_url = self_link.split("/wiki/")[0] + "/wiki"
                            web_url = f"{base_url}{web_path}"

            # Generate unique ID for attachment
            attachment_record_id = str(uuid.uuid4())

            return FileRecord(
                id=attachment_record_id,
                org_id=self.data_entities_processor.org_id,
                record_name=file_name,
                record_type=RecordType.FILE,
                external_record_id=attachment_id,
                external_revision_id=str(version_number) if version_number else None,
                version=0,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.CONFLUENCE,
                mime_type=mime_type,
                parent_external_record_id=parent_external_record_id,
                parent_record_type=RecordType.WEBPAGE,
                external_record_group_id=parent_external_record_group_id,
                record_group_type=RecordGroupType.CONFLUENCE_SPACES,
                weburl=web_url,
                is_file=True,
                size_in_bytes=file_size,
                extension=extension,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform attachment: {e}")
            return None

    async def _fetch_group_members(self, group_id: str, group_name: str) -> List[str]:
        """
        Fetch all members of a group with pagination.

        Args:
            group_id: The group ID
            group_name: The group name (for logging)

        Returns:
            List of member email addresses
        """
        try:
            member_emails = []
            batch_size = 100
            start = 0

            # Paginate through group members
            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_group_members(
                    group_id=group_id,
                    start=start,
                    limit=batch_size
                )

                # Check response
                if not response or response.status != HTTP_STATUS_200:
                    self.logger.warning(f"⚠️ Failed to fetch members for group {group_name}: {response.status if response else 'No response'}")
                    break

                response_data = response.json()
                members_data = response_data.get("results", [])

                if not members_data:
                    break

                # Extract emails from members (skip members without email)
                for member_data in members_data:
                    email = member_data.get("email", "").strip()
                    if email:
                        member_emails.append(email)

                # Move to next page
                start += batch_size

                # Check if we have more members
                size = response_data.get("size", 0)
                if size < batch_size:
                    break

            return member_emails

        except Exception as e:
            self.logger.error(f"❌ Failed to fetch members for group {group_name}: {e}")
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
                app_name=Connectors.CONFLUENCE
            )

            self.logger.debug(f"Fetched {len(all_app_users)} total users from database for email lookup")

            # Create email lookup map
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

    def _transform_to_app_user(self, user_data: Dict[str, Any]) -> Optional[AppUser]:
        """
        Transform Confluence user data to AppUser entity.

        Args:
            user_data: Raw user data from Confluence API

        Returns:
            AppUser object or None if transformation fails
        """
        try:
            account_id = user_data.get("accountId")
            email = user_data.get("email", "").strip()

            if not account_id or not email:
                return None

            # Parse lastModified timestamp
            source_updated_at = None
            last_modified = user_data.get("lastModified")
            if last_modified:
                source_updated_at = self._parse_confluence_datetime(last_modified)

            return AppUser(
                app_name=Connectors.CONFLUENCE,
                source_user_id=account_id,
                org_id=self.data_entities_processor.org_id,
                email=email,
                full_name=user_data.get("displayName"),
                is_active=False,
                source_updated_at=source_updated_at,
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform user: {e}")
            return None

    def _transform_to_user_group(
        self,
        group_data: Dict[str, Any]
    ) -> Optional[AppUserGroup]:
        """
        Transform Confluence group data to AppUserGroup entity.

        Args:
            group_data: Raw group data from Confluence API

        Returns:
            AppUserGroup object or None if transformation fails
        """
        try:
            group_id = group_data.get("id")
            group_name = group_data.get("name")

            if not group_id or not group_name:
                return None

            return AppUserGroup(
                app_name=Connectors.CONFLUENCE,
                source_user_group_id=group_id,
                name=group_name,
                org_id=self.data_entities_processor.org_id,
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform group: {e}")
            return None

    def _parse_confluence_datetime(self, datetime_str: str) -> Optional[int]:
        """
        Parse Confluence datetime string to epoch timestamp in milliseconds.

        Confluence format: "2025-11-13T07:51:50.526Z" (ISO 8601 with Z suffix)

        Args:
            datetime_str: Confluence datetime string

        Returns:
            int: Epoch timestamp in milliseconds or None if parsing fails
        """
        try:
            # Parse ISO 8601 format: '2025-11-13T07:51:50.526Z'
            # Replace 'Z' with '+00:00' for proper ISO format parsing
            dt = datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))
            return int(dt.timestamp() * 1000)
        except Exception as e:
            self.logger.warning(f"Failed to parse datetime '{datetime_str}': {e}")
            return None

    async def get_signed_url(self, record: Record) -> str:
        """Get a signed URL for a record (not implemented for Confluence)."""
        # Confluence uses OAuth, signed URLs are not applicable
        return ""

    async def stream_record(self, record: Record) -> StreamingResponse:
        """
        Stream record content (page HTML, comment HTML, or attachment file) from Confluence.

        For pages (WebpageRecord): Fetches HTML content from page body.export_view
        For comments (CommentRecord): Fetches HTML content from comment body.storage.value
        For attachments (FileRecord): Downloads file from attachment download URL

        Args:
            record: The record to stream (page, comment, or attachment)

        Returns:
            StreamingResponse: Streaming response with page/comment HTML or file content
        """
        try:
            self.logger.info(f"📥 Streaming record: {record.record_name} ({record.external_record_id})")

            if record.record_type in [RecordType.CONFLUENCE_PAGE, RecordType.CONFLUENCE_BLOGPOST]:
                # Page or blogpost - fetch HTML content based on record type
                html_content = await self._fetch_page_content(record.external_record_id, record.record_type)

                async def generate_page() -> AsyncGenerator[bytes, None]:
                    yield html_content.encode('utf-8')

                return StreamingResponse(
                    generate_page(),
                    media_type='text/html',
                    headers={"Content-Disposition": f'inline; filename="{record.external_record_id}.html"'}
                )

            elif record.record_type in [RecordType.COMMENT, RecordType.INLINE_COMMENT]:
                # Comment - fetch HTML content based on comment type
                html_content = await self._fetch_comment_content(record)

                async def generate_comment() -> AsyncGenerator[bytes, None]:
                    yield html_content.encode('utf-8')

                return StreamingResponse(
                    generate_comment(),
                    media_type='text/html',
                    headers={"Content-Disposition": f'inline; filename="comment_{record.external_record_id}.html"'}
                )

            elif record.record_type == RecordType.FILE:
                media_type = record.mime_type or 'application/octet-stream'
                filename = record.record_name or f"{record.external_record_id}"

                return StreamingResponse(
                    self._fetch_attachment_content(record),
                    media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'}
                )

            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported record type for streaming: {record.record_type}"
                )

        except HTTPException:
            raise  # Re-raise HTTP exceptions as-is
        except Exception as e:
            self.logger.error(f"❌ Failed to stream record: {e}", exc_info=True)
            raise HTTPException(
                status_code=500, detail=f"Failed to stream record: {str(e)}"
            )

    async def _fetch_page_content(self, page_id: str, record_type: RecordType) -> str:
        """
        Fetch page or blogpost HTML content from Confluence using v2 API.

        Args:
            page_id: The page or blogpost ID
            record_type: RecordType.CONFLUENCE_PAGE or RecordType.CONFLUENCE_BLOGPOST

        Returns:
            str: HTML content of the page/blogpost

        Raises:
            HTTPException: If content not found or fetch fails
        """
        try:
            self.logger.debug(f"Fetching content for {page_id} (type: {record_type})")

            datasource = await self._get_fresh_datasource()

            # Call appropriate API based on record type
            if record_type == RecordType.CONFLUENCE_PAGE:
                response = await datasource.get_page_content_v2(
                    page_id=page_id,
                    body_format="export_view"
                )
            elif record_type == RecordType.CONFLUENCE_BLOGPOST:
                response = await datasource.get_blogpost_content_v2(
                    blogpost_id=page_id,
                    body_format="export_view"
                )
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported record type: {record_type}"
                )

            # Check response
            if not response or response.status != HTTP_STATUS_200:
                raise HTTPException(
                    status_code=404,
                    detail=f"Content not found: {page_id}"
                )

            response_data = response.json()

            # Extract HTML content from body.export_view.value
            body = response_data.get("body", {})
            export_view = body.get("export_view", {})
            html_content = export_view.get("value", "")

            if not html_content:
                self.logger.warning(f"Content {page_id} has no body")
                html_content = "<p>No content available</p>"

            self.logger.debug(f"✅ Fetched {len(html_content)} bytes of HTML for {page_id}")
            return html_content

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to fetch content: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Failed to fetch content: {str(e)}"
            )

    async def _fetch_comment_content(self, record: CommentRecord) -> str:
        """
        Fetch comment HTML content from Confluence based on record type.

        Args:
            record: CommentRecord with external_record_id and record_type

        Returns:
            str: HTML content of the comment

        Raises:
            HTTPException: If comment not found or fetch fails
        """
        try:
            comment_id = record.external_record_id
            self.logger.debug(f"Fetching comment content for {comment_id} (type: {record.record_type})")

            datasource = await self._get_fresh_datasource()

            # Call appropriate API based on record type
            if record.record_type == RecordType.COMMENT:
                # Footer comment
                response = await datasource.get_footer_comment_by_id(
                    comment_id=int(comment_id),
                    body_format="storage"
                )
            elif record.record_type == RecordType.INLINE_COMMENT:
                # Inline comment
                response = await datasource.get_inline_comment_by_id(
                    comment_id=int(comment_id),
                    body_format="storage"
                )
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported comment type: {record.record_type}"
                )

            # Check response
            if not response or response.status != HTTP_STATUS_200:
                raise HTTPException(
                    status_code=404,
                    detail=f"Comment not found: {comment_id}"
                )

            response_data = response.json()

            # Extract HTML content from body.storage.value
            body = response_data.get("body", {})
            storage = body.get("storage", {})
            html_content = storage.get("value", "")

            if not html_content:
                self.logger.warning(f"Comment {comment_id} has no content")
                html_content = "<p>No content available</p>"

            self.logger.debug(f"✅ Fetched {len(html_content)} bytes of HTML for comment {comment_id}")
            return html_content

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to fetch comment content: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Failed to fetch comment content: {str(e)}"
            )

    async def _fetch_attachment_content(self, record: Record) -> AsyncGenerator[bytes, None]:
        """
        Stream attachment file content from Confluence Cloud.

        Args:
            record: Record with external_record_id and parent_external_record_id

        Yields:
            bytes: File content in 8KB chunks

        Raises:
            HTTPException: If attachment not found or download fails
        """
        try:
            attachment_id = record.external_record_id
            parent_page_id = record.parent_external_record_id

            if not attachment_id:
                raise HTTPException(
                    status_code=400,
                    detail=f"No attachment ID available for record {record.id}"
                )

            if not parent_page_id:
                raise HTTPException(
                    status_code=400,
                    detail=f"No parent page ID available for attachment {attachment_id}"
                )

            # Use datasource to stream attachment content
            datasource = await self._get_fresh_datasource()
            async for chunk in datasource.download_attachment(
                parent_page_id=parent_page_id,
                attachment_id=attachment_id
            ):
                yield chunk

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to download attachment {record.external_record_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Failed to download attachment: {str(e)}"
            )

    async def run_incremental_sync(self) -> None:
        """Run incremental sync (delegates to full sync)."""
        await self.run_sync()

    async def reindex_records(self, records: List[Record]) -> None:
        """Reindex a list of Confluence records.

        This method:
        1. For each record, checks if it has been updated at the source
        2. If updated, upserts the record in DB
        3. Publishes reindex events for all records via data_entities_processor

        Args:
            records: List of properly typed Record instances (WebpageRecord, FileRecord, CommentRecord, etc.)
        """
        try:
            if not records:
                self.logger.info("No records to reindex")
                return

            self.logger.info(f"Starting reindex for {len(records)} Confluence records")

            # Ensure external clients are initialized
            if not self.external_client or not self.data_source:
                self.logger.error("External API clients not initialized. Call init() first.")
                raise Exception("External API clients not initialized. Call init() first.")

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
            self.logger.error(f"Error during Confluence reindex: {e}", exc_info=True)
            raise

    async def _check_and_fetch_updated_record(
        self, org_id: str, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch record from source and return data for reindexing.

        Args:
            org_id: Organization ID
            record: Record to check

        Returns:
            Tuple of (Record, List[Permission]) if updated, None if not updated or error
        """
        try:
            if record.record_type == RecordType.CONFLUENCE_PAGE:
                return await self._check_and_fetch_updated_page(org_id, record)
            elif record.record_type == RecordType.CONFLUENCE_BLOGPOST:
                return await self._check_and_fetch_updated_blogpost(org_id, record)
            elif record.record_type in [RecordType.COMMENT, RecordType.INLINE_COMMENT]:
                return await self._check_and_fetch_updated_comment(org_id, record)
            elif record.record_type == RecordType.FILE:
                return await self._check_and_fetch_updated_attachment(org_id, record)
            else:
                self.logger.warning(f"Unsupported record type for reindex: {record.record_type}")
                return None

        except Exception as e:
            self.logger.error(f"Error checking record {record.id} at source: {e}")
            return None

    async def _check_and_fetch_updated_page(
        self, org_id: str, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch page from source for reindexing."""
        try:
            page_id = record.external_record_id

            # Fetch page from source using v2 API
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_page_content_v2(
                page_id=page_id,
                body_format="storage"
            )

            if not response or response.status != HTTP_STATUS_200:
                self.logger.warning(f"Page {page_id} not found at source, may have been deleted")
                return None

            page_data = response.json()

            # Check if version changed
            current_version = page_data.get("version", {}).get("number")
            if current_version is None:
                self.logger.warning(f"Page {page_id} has no version number")
                return None

            # Compare versions
            if record.external_revision_id and str(current_version) == record.external_revision_id:
                self.logger.debug(f"Page {page_id} has not changed at source (version {current_version})")
                return None

            self.logger.info(f"Page {page_id} has changed at source (version {record.external_revision_id} -> {current_version})")

            # Transform page to WebpageRecord
            webpage_record = self._transform_to_webpage_record(page_data, RecordType.CONFLUENCE_PAGE)
            if not webpage_record:
                return None

            # Preserve existing record ID
            webpage_record.id = record.id

            # Fetch fresh permissions
            permissions = await self._fetch_page_permissions(page_id)

            return (webpage_record, permissions)

        except Exception as e:
            self.logger.error(f"Error fetching page {record.external_record_id}: {e}")
            return None

    async def _check_and_fetch_updated_blogpost(
        self, org_id: str, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch blogpost from source for reindexing."""
        try:
            blogpost_id = record.external_record_id

            datasource = await self._get_fresh_datasource()
            response = await datasource.get_blog_post_by_id(
                id=int(blogpost_id),
                body_format="storage"
            )

            if not response or response.status != HTTP_STATUS_200:
                self.logger.warning(f"Blogpost {blogpost_id} not found at source, may have been deleted")
                return None

            blogpost_data = response.json()

            # Check if version changed
            current_version = blogpost_data.get("version", {}).get("number")
            if current_version is None:
                self.logger.warning(f"Blogpost {blogpost_id} has no version number")
                return None

            # Compare versions
            if record.external_revision_id and str(current_version) == record.external_revision_id:
                self.logger.debug(f"Blogpost {blogpost_id} has not changed at source (version {current_version})")
                return None

            self.logger.info(f"Blogpost {blogpost_id} has changed at source (version {record.external_revision_id} -> {current_version})")

            # Transform blogpost to WebpageRecord
            webpage_record = self._transform_to_webpage_record(blogpost_data, RecordType.CONFLUENCE_BLOGPOST)
            if not webpage_record:
                return None

            # Preserve existing record ID
            webpage_record.id = record.id

            # Fetch fresh permissions
            permissions = await self._fetch_page_permissions(blogpost_id)

            return (webpage_record, permissions)

        except Exception as e:
            self.logger.error(f"Error fetching blogpost {record.external_record_id}: {e}")
            return None

    async def _check_and_fetch_updated_comment(
        self, org_id: str, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch comment from source for reindexing."""
        try:
            comment_id = record.external_record_id
            is_inline = record.record_type == RecordType.INLINE_COMMENT

            # Fetch comment from source using v2 API
            datasource = await self._get_fresh_datasource()

            if is_inline:
                response = await datasource.get_inline_comment_by_id(
                    comment_id=int(comment_id),
                    body_format="storage"
                )
            else:
                response = await datasource.get_footer_comment_by_id(
                    comment_id=int(comment_id),
                    body_format="storage"
                )

            if not response or response.status != HTTP_STATUS_200:
                self.logger.warning(f"Comment {comment_id} not found at source, may have been deleted")
                return None

            comment_data = response.json()

            # Check if version changed
            current_version = comment_data.get("version", {}).get("number")
            if current_version is None:
                self.logger.warning(f"Comment {comment_id} has no version number")
                return None

            # Compare versions using external_revision_id
            if record.external_revision_id and str(current_version) == record.external_revision_id:
                self.logger.debug(f"Comment {comment_id} has not changed at source (version {current_version})")
                return None

            self.logger.info(f"Comment {comment_id} has changed at source (version {record.external_revision_id} -> {current_version})")

            # Transform comment to CommentRecord
            comment_type = "inline" if is_inline else "footer"

            comment_record = self._transform_to_comment_record(
                comment_data,
                record.parent_external_record_id,
                record.external_record_group_id,
                comment_type,
                record.parent_external_record_id
            )

            if not comment_record:
                return None

            # Preserve existing record ID
            comment_record.id = record.id

            # Comments inherit permissions from parent page - fetch page permissions
            parent_page_id = record.parent_external_record_id
            permissions = []
            if parent_page_id:
                permissions = await self._fetch_page_permissions(parent_page_id)

            return (comment_record, permissions)

        except Exception as e:
            self.logger.error(f"Error fetching comment {record.external_record_id}: {e}")
            return None

    async def _check_and_fetch_updated_attachment(
        self, org_id: str, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch attachment from source for reindexing."""
        try:
            attachment_id = record.external_record_id
            parent_page_id = record.parent_external_record_id

            if not parent_page_id:
                self.logger.warning(f"Attachment {attachment_id} has no parent page ID")
                return None

            # Fetch attachment metadata from source using v2 API
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_attachment_by_id(
                id=attachment_id,
                include_version=True
            )

            if not response or response.status != HTTP_STATUS_200:
                self.logger.warning(f"Attachment {attachment_id} not found at source, may have been deleted")
                return None

            attachment_data = response.json()

            # Check if version changed
            current_version = attachment_data.get("version", {}).get("number")
            if current_version is None:
                self.logger.warning(f"Attachment {attachment_id} has no version number")
                return None

            # Compare versions using external_revision_id
            if record.external_revision_id and str(current_version) == record.external_revision_id:
                self.logger.debug(f"Attachment {attachment_id} has not changed at source (version {current_version})")
                return None

            self.logger.info(f"Attachment {attachment_id} has changed at source (version {record.external_revision_id} -> {current_version})")

            # Get space_id from parent page or use existing
            parent_space_id = record.external_record_group_id

            # Transform attachment to FileRecord using v1 data
            attachment_record = self._transform_to_attachment_file_record(
                attachment_data,
                parent_page_id,
                parent_space_id
            )

            if not attachment_record:
                return None

            # Preserve existing record ID
            attachment_record.id = record.id

            # Attachments inherit permissions from parent page - fetch page permissions
            permissions = await self._fetch_page_permissions(parent_page_id)

            return (attachment_record, permissions)

        except Exception as e:
            self.logger.error(f"Error fetching attachment {record.external_record_id}: {e}")
            return None

    async def cleanup(self) -> None:
        """Cleanup resources."""
        self.logger.info("Cleaning up Confluence connector resources")
        # Add cleanup logic if needed

    async def handle_webhook_notification(self, notification: Dict) -> None:
        """Handle webhook notifications (not implemented)."""
        self.logger.warning("Webhook notifications not yet supported for Confluence")
        pass

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService
    ) -> "ConfluenceConnector":
        """Factory method to create a Confluence connector instance."""
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
