"""Linear Connector Implementation"""
from datetime import datetime
from logging import Logger
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Tuple,
)
from uuid import uuid4

from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.sync_point.sync_point import SyncDataPointType, SyncPoint
from app.connectors.core.registry.connector_builder import (
    AuthField,
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
)
from app.connectors.core.registry.filters import (
    FilterCategory,
    FilterField,
    FilterOption,
    FilterOptionsResponse,
    FilterType,
    OptionSourceType,
    load_connector_filters,
)
from app.connectors.sources.linear.common.apps import LinearApp
from app.models.entities import (
    AppUser,
    AppUserGroup,
    Record,
    RecordGroup,
    RecordGroupType,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.linear.linear import LinearClient
from app.sources.external.linear.linear import LinearDataSource

# Config path for Linear connector
LINEAR_CONFIG_PATH = "/services/connectors/{connector_id}/config"


@ConnectorBuilder("Linear")\
    .in_group(AppGroups.LINEAR.value)\
    .with_auth_type("OAUTH")\
    .with_description("Sync issues, comments, and projects from Linear")\
    .with_categories(["Issue Tracking", "Project Management"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/linear.svg")
        .with_realtime_support(False)
        .add_documentation_link(DocumentationLink(
            "Linear OAuth Setup",
            "https://linear.app/developers/docs/authentication",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/linear/linear',
            'pipeshub'
        ))
        .with_redirect_uri("connectors/oauth/callback/Linear", True)
        .add_auth_field(AuthField(
            name="clientId",
            display_name="Client ID",
            placeholder="Enter your Linear OAuth Client ID",
            description="OAuth Client ID from Linear (https://linear.app/settings/api)",
            field_type="TEXT",
            max_length=2000
        ))
        .add_auth_field(AuthField(
            name="clientSecret",
            display_name="Client Secret",
            placeholder="Enter your Linear OAuth Client Secret",
            description="OAuth Client Secret from Linear",
            field_type="PASSWORD",
            max_length=2000,
            is_secret=True
        ))
        .with_oauth_urls(
            "https://linear.app/oauth/authorize",
            "https://api.linear.app/oauth/token",
            ["read", "write"]  # Linear OAuth scopes
        )
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(True)
        .add_filter_field(FilterField(
            name="team_ids",
            display_name="Teams",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            description="Filter issues by team (leave empty for all teams)",
            default_value=[],
            option_source_type=OptionSourceType.DYNAMIC
        ))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .add_filter_field(FilterField(
            name="issues",
            display_name="Index Issues",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of issues",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="issue_comments",
            display_name="Index Issue Comments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of issue comments",
            default_value=True
        ))
    )\
    .build_decorator()
class LinearConnector(BaseConnector):
    """
    Linear connector for syncing issues, comments, and users from Linear
    """
    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str
    ) -> None:
        super().__init__(
            LinearApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id
        )
        self.external_client: Optional[LinearClient] = None
        self.data_source: Optional[LinearDataSource] = None
        self.organization_id: Optional[str] = None
        self.organization_name: Optional[str] = None
        self.organization_url_key: Optional[str] = None
        self.connector_id = connector_id
        self.connector_name = Connectors.LINEAR

        # Initialize sync points
        org_id = self.data_entities_processor.org_id

        self.issues_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=data_store_provider
        )

        self.sync_filters = None
        self.indexing_filters = None

    async def init(self) -> bool:
        """
        Initialize Linear client using proper Client + DataSource architecture
        """
        try:
            # Use LinearClient.build_from_services() to create client with proper auth
            client = await LinearClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id
            )

            # Store client for future use
            self.external_client = client

            # Create DataSource from client
            self.data_source = LinearDataSource(client)

            # Validate connection by fetching organization info
            org_response = await self.data_source.organization()
            if not org_response.success:
                raise Exception(f"Failed to connect to Linear: {org_response.message}")

            org_data = org_response.data.get("organization", {}) if org_response.data else {}
            if not org_data:
                raise Exception("No organization data returned from Linear")

            self.organization_id = org_data.get("id")
            self.organization_name = org_data.get("name")
            self.organization_url_key = org_data.get("urlKey")

            self.logger.info(f"✅ Linear client initialized successfully for organization: {self.organization_name}")
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to initialize Linear client: {e}")
            return False

    async def _get_fresh_datasource(self) -> LinearDataSource:
        """
        Get LinearDataSource with fresh API token.
        For API token auth, token doesn't expire, so just return existing datasource.
        """
        if self.data_source is None:
            await self.init()
        return self.data_source  # type: ignore

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 100,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> FilterOptionsResponse:
        """
        Get dynamic filter options for a given filter field.

        Args:
            filter_key: The filter field name (e.g., "team_ids")
            page: Page number (1-indexed)
            limit: Number of items per page
            search: Optional search term to filter results
            cursor: Optional cursor for pagination (for cursor-based pagination)
        """
        if filter_key == "team_ids":
            return await self._get_team_options(page, limit, search, cursor)
        else:
            raise ValueError(f"Unknown filter field: {filter_key}")

    async def _get_team_options(
        self,
        page: int = 1,
        limit: int = 100,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> FilterOptionsResponse:
        """
        Get team options for the team_ids filter with cursor-based pagination.

        Linear uses cursor-based pagination. For the first page, we don't pass a cursor.
        For subsequent pages, we use the cursor from the previous response.
        """
        # Ensure datasource is initialized
        if not self.data_source:
            await self.init()

        datasource = await self._get_fresh_datasource()

        try:
            # Build filter for server-side search if provided
            # Linear TeamFilter supports name filtering, but syntax may vary
            filter_dict: Optional[Dict[str, Any]] = None

            # Fetch teams with pagination
            # Use cursor if provided (for subsequent pages), otherwise start from beginning
            response = await datasource.teams(
                first=limit,
                after=cursor,  # None for first page, cursor for subsequent pages
                filter=filter_dict
            )

            if not response.success:
                self.logger.error(f"❌ Failed to fetch teams: {response.message}")
                raise RuntimeError(f"Failed to fetch teams: {response.message}")

            teams_data = response.data.get("teams", {}) if response.data else {}
            teams_list = teams_data.get("nodes", [])

            # If server-side filtering didn't work or wasn't applied, do client-side filtering
            if search and not filter_dict:
                search_lower = search.lower()
                teams_list = [
                    t for t in teams_list
                    if search_lower in t.get("name", "").lower()
                    or search_lower in t.get("key", "").lower()
                ]

            # Get pagination info
            page_info = teams_data.get("pageInfo", {})
            has_more = page_info.get("hasNextPage", False)
            end_cursor = page_info.get("endCursor")

            # Convert to FilterOption objects
            options = [
                FilterOption(
                    id=team.get("id"),
                    label=f"{team.get('name', '')} ({team.get('key', '')})"
                )
                for team in teams_list
                if team.get("id") and team.get("name")
            ]

            self.logger.debug(
                f"✅ Fetched {len(options)} teams (page {page}, has_more: {has_more})"
            )

            return FilterOptionsResponse(
                success=True,
                options=options,
                page=page,
                limit=limit,
                has_more=has_more,
                cursor=end_cursor
            )
        except Exception as e:
            self.logger.error(f"❌ Error fetching teams: {e}", exc_info=True)
            raise RuntimeError(f"Failed to fetch team options: {str(e)}")

    async def run_sync(self) -> None:
        """
        Main sync orchestration method.
        Syncs users, teams, and issues from Linear.
        """
        try:
            self.logger.info(f"🚀 Starting Linear sync for connector {self.connector_id}")

            # Ensure data source is initialized
            if not self.data_source:
                await self.init()

            # Load sync and indexing filters (loaded in run_sync to ensure latest values)
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service,
                "linear",
                self.connector_id,
                self.logger
            )
            self.logger.info(f"📋 Loaded filters - Sync: {self.sync_filters}, Indexing: {self.indexing_filters}")

            # Step 1: Get active users from system
            users = await self.data_entities_processor.get_all_active_users()
            if not users:
                self.logger.info("ℹ️ No active users found in system")
                return

            # Step 2: Fetch and sync Linear users
            linear_users = await self._fetch_users()
            if linear_users:
                await self.data_entities_processor.on_new_app_users(linear_users)
                self.logger.info(f"👥 Synced {len(linear_users)} Linear users")

            # Step 3: Get team_ids filter and fetch teams
            allowed_team_ids = None
            if self.sync_filters:
                team_ids_filter = self.sync_filters.get("team_ids")
                if team_ids_filter:
                    allowed_team_ids = team_ids_filter.get_value(default=[])
                    if allowed_team_ids:
                        self.logger.info(f"📋 Filtering teams by IDs: {allowed_team_ids}")
                    else:
                        self.logger.info("📋 Team filter is empty, will fetch no teams")
                else:
                    self.logger.info("📋 No team filter set - will fetch all teams")
            else:
                self.logger.info("📋 No sync filters - will fetch all teams")

            # Step 4: Build email map from already-synced users for team member lookup
            user_email_map: Dict[str, AppUser] = {}
            if linear_users:
                for app_user in linear_users:
                    if app_user.email:
                        user_email_map[app_user.email.lower()] = app_user

            # Step 5: Fetch and sync teams (as both UserGroups and RecordGroups)
            team_user_groups, team_record_groups = await self._fetch_teams(allowed_team_ids, user_email_map)

            # Step 6a: Sync teams as UserGroups (membership tracking)
            if team_user_groups:
                await self.data_entities_processor.on_new_user_groups(team_user_groups)
                total_members = sum(len(members) for _, members in team_user_groups)
                self.logger.info(f"👥 Synced {len(team_user_groups)} Linear teams as UserGroups ({total_members} total memberships)")

            # Step 6b: Sync teams as RecordGroups (content organization)
            if team_record_groups:
                await self.data_entities_processor.on_new_record_groups(team_record_groups)
                self.logger.info(f"📁 Synced {len(team_record_groups)} Linear teams as RecordGroups")

            # TODO: Step 7 - Sync issues for teams

            self.logger.info("✅ Linear sync completed")

        except Exception as e:
            self.logger.error(f"❌ Error during Linear sync: {e}", exc_info=True)
            raise

    async def _fetch_users(self) -> List[AppUser]:
        """
        Fetch all Linear users using cursor-based pagination.
        Returns list of AppUser objects.
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        all_users: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        page_size = 50

        datasource = await self._get_fresh_datasource()

        # Fetch all users with cursor-based pagination
        while True:
            response = await datasource.users(first=page_size, after=cursor)

            if not response.success:
                raise RuntimeError(f"Failed to fetch users: {response.message}")

            users_data = response.data.get("users", {}) if response.data else {}
            users_list = users_data.get("nodes", [])

            if not users_list:
                break

            all_users.extend(users_list)

            # Check if there are more pages
            page_info = users_data.get("pageInfo", {})
            has_next_page = page_info.get("hasNextPage", False)

            if not has_next_page:
                break

            cursor = page_info.get("endCursor")
            if not cursor:
                break

        # Convert Linear users to AppUser objects
        app_users: List[AppUser] = []

        for user in all_users:
            user_id = user.get("id")
            email = user.get("email")
            active = user.get("active", True)

            # Skip inactive users
            if not active:
                continue

            # Skip users without email (required for AppUser)
            if not email:
                self.logger.debug(f"Skipping user {user_id} - no email address")
                continue

            # Use displayName if available, otherwise name, otherwise email
            full_name = user.get("displayName") or user.get("name") or email

            # Parse updatedAt timestamp
            source_updated_at = None
            updated_at_str = user.get("updatedAt")
            if updated_at_str:
                source_updated_at = self._parse_linear_datetime(updated_at_str)

            app_user = AppUser(
                app_name=Connectors.LINEAR,
                connector_id=self.connector_id,
                source_user_id=user_id,
                org_id=self.data_entities_processor.org_id,
                email=email,
                full_name=full_name,
                is_active=active,
                source_updated_at=source_updated_at
            )
            app_users.append(app_user)

        self.logger.info(f"📥 Fetched {len(all_users)} Linear users, converted {len(app_users)} to AppUser")
        return app_users


    async def _fetch_teams(
        self, team_ids: Optional[List[str]] = None, user_email_map: Optional[Dict[str, AppUser]] = None
    ) -> Tuple[
        List[Tuple[AppUserGroup, List[AppUser]]],
        List[Tuple[RecordGroup, List[Permission]]]
    ]:
        """
        Fetch Linear teams and convert them to both UserGroups and RecordGroups.

        Dual approach:
        - UserGroups: Track WHO is in each team (membership management)
        - RecordGroups: Track WHAT each team contains (issues/content organization)
        - Permissions: UserGroup → RecordGroup for private teams, ORG → RecordGroup for public teams

        Args:
            team_ids: Optional list of team IDs to fetch. If None, fetch all teams.
            user_email_map: Optional map of email -> AppUser for already-synced users.

        Returns:
            Tuple of:
            - List of (AppUserGroup, List[AppUser]) for membership tracking
            - List of (RecordGroup, List[Permission]) for content organization
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        all_teams: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        page_size = 50

        datasource = await self._get_fresh_datasource()

        # Fetch all teams with cursor-based pagination
        while True:
            # Build filter if specific team IDs are requested
            filter_dict: Optional[Dict[str, Any]] = None
            if team_ids:
                # Linear TeamFilter supports id filtering
                filter_dict = {"id": {"in": team_ids}}

            response = await datasource.teams(first=page_size, after=cursor, filter=filter_dict)

            if not response.success:
                raise RuntimeError(f"Failed to fetch teams: {response.message}")

            teams_data = response.data.get("teams", {}) if response.data else {}
            teams_list = teams_data.get("nodes", [])

            if not teams_list:
                break

            all_teams.extend(teams_list)

            # Check if there are more pages
            page_info = teams_data.get("pageInfo", {})
            has_next_page = page_info.get("hasNextPage", False)

            if not has_next_page:
                break

            cursor = page_info.get("endCursor")
            if not cursor:
                break

        # Convert teams to both UserGroups and RecordGroups
        user_groups: List[Tuple[AppUserGroup, List[AppUser]]] = []
        record_groups: List[Tuple[RecordGroup, List[Permission]]] = []

        for team in all_teams:
            team_id = team.get("id")
            team_name = team.get("name")
            team_key = team.get("key")
            team_description = team.get("description")
            is_private = team.get("private", False)
            team_members = team.get("members", {}).get("nodes", [])

            if not team_id or not team_name:
                self.logger.warning(f"Skipping team with missing id or name: {team}")
                continue

            # Extract parent team information
            parent_team = team.get("parent")
            parent_external_group_id = None
            if parent_team:
                parent_external_group_id = parent_team.get("id")
                self.logger.debug(f"Team {team_key} has parent team: {parent_team.get('name')} ({parent_external_group_id})")

            # Build team URL if we have organization urlKey
            web_url: Optional[str] = None
            if self.organization_url_key and team_key:
                web_url = f"https://linear.app/{self.organization_url_key}/team/{team_key}"

            # 1. Create UserGroup for membership tracking
            user_group = AppUserGroup(
                id=str(uuid4()),
                org_id=self.data_entities_processor.org_id,
                source_user_group_id=team_id,
                connector_id=self.connector_id,
                app_name=Connectors.LINEAR,
                name=team_name,
                description=team_description,
            )

            # Get AppUser objects for team members (use already-synced users)
            member_app_users: List[AppUser] = []
            for member in team_members:
                member_email = member.get("email")
                if member_email and user_email_map:
                    # Look up already-synced AppUser by email
                    app_user = user_email_map.get(member_email.lower())
                    if app_user:
                        member_app_users.append(app_user)
                    else:
                        self.logger.warning(f"⚠️ Team member {member_email} not found in synced users - skipping")

            user_groups.append((user_group, member_app_users))

            # 2. Create RecordGroup for content organization
            record_group = RecordGroup(
                id=str(uuid4()),
                org_id=self.data_entities_processor.org_id,
                external_group_id=team_id,
                connector_id=self.connector_id,
                connector_name=Connectors.LINEAR,
                name=team_name,
                short_name=team_key or team_id,
                group_type=RecordGroupType.PROJECT,
                description=team_description,
                web_url=web_url,
                parent_external_group_id=parent_external_group_id,
            )

            # 3. Handle permissions based on team privacy
            permissions: List[Permission] = []

            if is_private:
                # For private teams: Grant access via UserGroup
                permissions.append(Permission(
                    entity_type=EntityType.GROUP,
                    external_id=team_id,
                    type=PermissionType.READ,
                ))
                self.logger.info(f"Team {team_key} is private - added UserGroup permission (external_id={team_id})")
            else:
                # For public teams: All org members can access
                permissions.append(Permission(
                    entity_type=EntityType.ORG,
                    type=PermissionType.READ,
                    external_id=None
                ))
                self.logger.info(f"Team {team_key} is public - added org-level permission for all org members")

            record_groups.append((record_group, permissions))

        self.logger.info(
            f"📥 Fetched {len(all_teams)} Linear teams, "
            f"created {len(user_groups)} UserGroups and {len(record_groups)} RecordGroups"
        )
        return user_groups, record_groups

    async def run_incremental_sync(self) -> None:
        """
        Incremental sync - calls run_sync which handles incremental logic.
        """
        self.logger.info(f"🔄 Starting Linear incremental sync for connector {self.connector_id}")
        await self.run_sync()
        self.logger.info("✅ Linear incremental sync completed")

    # ==================== HELPER FUNCTIONS ====================

    def _parse_linear_datetime(self, datetime_str: str) -> Optional[int]:
        """
        Parse Linear datetime string to epoch timestamp in milliseconds.

        Linear format: "2025-01-01T12:00:00.000Z" (ISO 8601 with Z suffix)

        Args:
            datetime_str: Linear datetime string

        Returns:
            int: Epoch timestamp in milliseconds or None if parsing fails
        """
        try:
            # Parse ISO 8601 format: '2025-01-01T12:00:00.000Z'
            # Replace 'Z' with '+00:00' for proper ISO format parsing
            dt = datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))
            return int(dt.timestamp() * 1000)
        except Exception as e:
            self.logger.debug(f"Failed to parse Linear datetime '{datetime_str}': {e}")
            return None

    # ==================== ABSTRACT METHODS ====================

    async def test_connection_and_access(self) -> bool:
        """Test connection and access to Linear using DataSource"""
        try:
            if not self.data_source:
                await self.init()

            # Test by fetching organization info (simple API call)
            datasource = await self._get_fresh_datasource()
            response = await datasource.organization()

            if response.success and response.data is not None:
                self.logger.info("✅ Linear connection test successful")
                return True
            else:
                self.logger.error(f"❌ Connection test failed: {response.message if hasattr(response, 'message') else 'Unknown error'}")
                return False
        except Exception as e:
            self.logger.error(f"❌ Connection test failed: {e}", exc_info=True)
            return False

    async def get_signed_url(self, record: Record) -> str:
        """Create a signed URL for a specific record"""
        # Linear doesn't support signed URLs, return empty string
        return ""

    async def stream_record(self, record: Record) -> StreamingResponse:
        """
        Stream record content (issue or comment).
        """
        try:
            if not self.data_source:
                await self.init()

            await self._get_fresh_datasource()

            # For now, return empty content
            # TODO: Implement proper content streaming for Linear issues/comments
            content = ""
            return StreamingResponse(
                iter([content.encode('utf-8')]),
                media_type="text/plain"
            )
        except Exception as e:
            self.logger.error(f"❌ Failed to stream record: {e}")
            return StreamingResponse(
                iter([b"Error streaming record"]),
                media_type="text/plain",
                status_code=500
            )

    async def handle_webhook_notification(self, notification: Dict) -> None:
        """Handle webhook notifications from Linear"""
        # TODO: Implement webhook handling when Linear webhooks are configured
        pass

    async def cleanup(self) -> None:
        """Cleanup resources - close HTTP client connections properly"""
        try:
            self.logger.info("Cleaning up Linear connector resources")

            # Close HTTP client properly BEFORE event loop closes
            # This prevents Windows asyncio "Event loop is closed" errors
            if self.external_client:
                try:
                    internal_client = self.external_client.get_client()
                    if internal_client and hasattr(internal_client, 'close'):
                        await internal_client.close()
                        self.logger.debug("Closed Linear HTTP client connection")
                except Exception as e:
                    # Swallow errors during shutdown - client may already be closed
                    self.logger.debug(f"Error closing Linear client (may be expected during shutdown): {e}")

            self.logger.info("✅ Linear connector cleanup completed")
        except Exception as e:
            self.logger.warning(f"⚠️ Error during Linear connector cleanup: {e}")

    async def reindex_records(self, record_results: List[Record]) -> None:
        """Reindex a list of Linear records.

        This method:
        1. For each record, checks if it has been updated at the source
        2. If updated, upserts the record in DB
        3. Publishes reindex events for all records via data_entities_processor
        4. Skips reindex for records that are not properly typed (base Record class)"""
        try:
            if not record_results:
                return

            self.logger.info(f"Starting reindex for {len(record_results)} Linear records")

            # Ensure external clients are initialized
            if not self.data_source:
                await self.init()

            await self._get_fresh_datasource()

            # TODO: Implement reindex logic for Linear records
            # For now, just log that reindex was called
            self.logger.info(f"Reindex called for {len(record_results)} records (not yet implemented)")

        except Exception as e:
            self.logger.error(f"❌ Failed to reindex Linear records: {e}")
            raise

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str
    ) -> "BaseConnector":
        """Factory method to create LinearConnector instance"""
        data_entities_processor = DataSourceEntitiesProcessor(
            logger,
            data_store_provider,
            config_service
        )
        await data_entities_processor.initialize()

        return LinearConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id
        )
