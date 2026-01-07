"""Linear Connector Implementation"""
import re
from datetime import datetime, timezone
from logging import Logger
from typing import (
    Any,
    AsyncGenerator,
    Dict,
    List,
    Optional,
    Set,
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
from app.connectors.core.registry.filters import (
    FilterCategory,
    FilterField,
    FilterOperatorType,
    FilterOption,
    FilterOptionsResponse,
    FilterType,
    IndexingFilterKey,
    OptionSourceType,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.sources.linear.common.apps import LinearApp
from app.models.entities import (
    AppUser,
    AppUserGroup,
    CommentRecord,
    FileRecord,
    IndexingStatus,
    LinkPublicStatus,
    LinkRecord,
    MimeTypes,
    OriginTypes,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    TicketRecord,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.linear.linear import LinearClient
from app.sources.external.linear.linear import LinearDataSource
from app.utils.time_conversion import get_epoch_timestamp_in_ms

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
            "https://linear.app/developers/oauth-2-0-authentication",
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
        .add_filter_field(CommonFields.modified_date_filter("Filter issues by modification date."))
        .add_filter_field(CommonFields.created_date_filter("Filter issues by creation date."))
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
        .add_filter_field(FilterField(
            name="issue_attachments",
            display_name="Index Issue Attachments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of issue attachments",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="documents",
            display_name="Index Issue Documents",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of issue documents",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="files",
            display_name="Index Issue Files",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of files extracted from issue descriptions and comments",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="projects",
            display_name="Index Projects",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of projects",
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

        self.attachments_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=data_store_provider
        )

        self.documents_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=data_store_provider
        )

        self.projects_sync_point = SyncPoint(
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
            team_ids_filter = None
            team_ids = None
            team_ids_operator = None
            if self.sync_filters:
                team_ids_filter = self.sync_filters.get("team_ids")
                if team_ids_filter:
                    team_ids = team_ids_filter.get_value(default=[])
                    team_ids_operator = team_ids_filter.get_operator()
                    if team_ids:
                        # Extract operator value string (handles both enum and string)
                        operator_value = team_ids_operator.value if hasattr(team_ids_operator, 'value') else str(team_ids_operator) if team_ids_operator else "in"
                        action = "Excluding" if operator_value == "not_in" else "Including"
                        self.logger.info(f"📋 Filter: {action} teams by IDs: {team_ids}")
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
            team_user_groups, team_record_groups = await self._fetch_teams(
                team_ids=team_ids,
                team_ids_operator=team_ids_operator,
                user_email_map=user_email_map
            )

            # Step 6a: Sync teams as UserGroups (membership tracking)
            if team_user_groups:
                await self.data_entities_processor.on_new_user_groups(team_user_groups)
                total_members = sum(len(members) for _, members in team_user_groups)
                self.logger.info(f"👥 Synced {len(team_user_groups)} Linear teams as UserGroups ({total_members} total memberships)")

            # Step 6b: Sync teams as RecordGroups (content organization)
            if team_record_groups:
                await self.data_entities_processor.on_new_record_groups(team_record_groups)
                self.logger.info(f"📁 Synced {len(team_record_groups)} Linear teams as RecordGroups")

            # Step 7: Sync issues for teams
            await self._sync_issues_for_teams(team_record_groups)

            # Step 8: Sync attachments separately (Linear doesn't update issue.updatedAt when attachments are added)
            await self._sync_attachments(team_record_groups)

            # Step 9: Sync documents separately (Linear doesn't update issue.updatedAt when documents are added)
            await self._sync_documents(team_record_groups)

            # Step 10: Sync projects
            await self._sync_projects_for_teams(team_record_groups)

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
        self,
        team_ids: Optional[List[str]] = None,
        team_ids_operator: Optional[FilterOperatorType] = None,
        user_email_map: Optional[Dict[str, AppUser]] = None
    ) -> Tuple[List[Tuple[AppUserGroup, List[AppUser]]],List[Tuple[RecordGroup, List[Permission]]]]:
        """
        Fetch Linear teams and convert them to both UserGroups and RecordGroups.

        Dual approach:
        - UserGroups: Track WHO is in each team (membership management)
        - RecordGroups: Track WHAT each team contains (issues/content organization)
        - Permissions: UserGroup → RecordGroup for private teams, ORG → RecordGroup for public teams

        Args:
            team_ids: Optional list of team IDs to include/exclude
            team_ids_operator: Optional filter operator (IN or NOT_IN)
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
                # Check operator: "in" (include) or "not_in" (exclude)
                # Extract operator value string (handles both enum and string)
                if team_ids_operator:
                    operator_value = team_ids_operator.value if hasattr(team_ids_operator, 'value') else str(team_ids_operator)
                else:
                    operator_value = "in"  # Default to "in" if no operator specified

                is_exclude = operator_value == "not_in"

                if is_exclude:
                    # Linear TeamFilter supports "nin" for not in
                    filter_dict = {"id": {"nin": team_ids}}
                else:
                    # Linear TeamFilter supports "in" for include
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

    async def _sync_issues_for_teams(
        self,
        team_record_groups: List[Tuple[RecordGroup, List[Permission]]]
    ) -> None:
        """
        Sync issues for all teams with batch processing and incremental sync.
        Uses simple team-level sync points.

        Sync point logic:
        - Before sync: Read last_sync_time
        - Query: Fetch issues with updatedAt > last_sync_time
        - After EACH batch: Update last_sync_time to max issue updated_at (fault tolerance)
        - After all batches: Update last_sync_time to current time


        Args:
            team_record_groups: List of (RecordGroup, permissions) tuples for teams to sync
        """
        if not team_record_groups:
            self.logger.info("ℹ️ No teams to sync issues for")
            return

        for team_record_group, team_perms in team_record_groups:
            try:
                team_id = team_record_group.external_group_id
                team_key = team_record_group.short_name or team_record_group.name

                self.logger.info(f"📋 Starting issue sync for team: {team_key}")

                # Read team-level sync point
                last_sync_time = await self._get_team_sync_checkpoint(team_key)

                if last_sync_time:
                    self.logger.info(f"🔄 Incremental sync for team {team_key} from {last_sync_time}")

                # Fetch and process issues for this team
                total_records_processed = 0
                total_tickets = 0
                total_comments = 0
                # Track max updatedAt ONLY from issues (TicketRecords)
                # We query by issue.updatedAt, so sync point must be based on issue timestamps
                max_issue_updated_at: Optional[int] = None

                async for batch_records in self._fetch_issues_for_team_batch(
                    team_id=team_id,
                    team_key=team_key,
                    last_sync_time=last_sync_time
                ):
                    if not batch_records:
                        continue

                    # Count records by type and track max issue updatedAt
                    for record, _ in batch_records:
                        if isinstance(record, TicketRecord):
                            total_tickets += 1
                            # Only track max from ISSUES - sync point must match issue query filter
                            if record.source_updated_at:
                                if max_issue_updated_at is None or record.source_updated_at > max_issue_updated_at:
                                    max_issue_updated_at = record.source_updated_at
                        elif isinstance(record, CommentRecord):
                            total_comments += 1

                    # Process batch
                    total_records_processed += len(batch_records)
                    await self.data_entities_processor.on_new_records(batch_records)

                    # Update sync point after each batch for fault tolerance
                    # Uses max from ISSUES ONLY (we query by issue.updatedAt)
                    if max_issue_updated_at:
                        await self._update_team_sync_checkpoint(team_key, max_issue_updated_at)

                # Log final status
                if total_records_processed > 0:
                    self.logger.info(f"✅ Team {team_key}: {total_tickets} tickets, {total_comments} comments ({total_records_processed} total)")
                else:
                    self.logger.info(f"ℹ️ No new/updated issues for team {team_key}")

            except Exception as e:
                team_name = team_record_group.name or team_record_group.short_name or "unknown"
                self.logger.error(f"❌ Error syncing issues for team {team_name}: {e}", exc_info=True)
                continue

    async def _fetch_issues_for_team_batch(
        self,
        team_id: str,
        team_key: str,
        last_sync_time: Optional[int] = None,
        batch_size: int = 50
    ) -> AsyncGenerator[List[Tuple[Record, List[Permission]]], None]:
        """
        Fetch issues for a team with pagination and incremental sync, yielding batches.

        Args:
            team_id: Team ID
            team_key: Team key (e.g., "ENG")
            last_sync_time: Last sync timestamp in ms (for incremental sync)
            batch_size: Number of issues to fetch per batch

        Yields:
            Batches of (record, permissions) tuples
        """
        datasource = await self._get_fresh_datasource()
        after_cursor: Optional[str] = None

        # Build filter for team
        team_filter: Dict[str, Any] = {
            "team": {"id": {"eq": team_id}}
        }

        # Apply date filters to team_filter
        self._apply_date_filters_to_linear_filter(team_filter, last_sync_time)

        # Order by updatedAt ASC - critical for sync point logic to work correctly
        # This ensures each batch's max updatedAt >= previous batches, so checkpoint
        order_by = {"updatedAt": "ASC"}

        while True:
            # Fetch issues batch ordered by updatedAt ASC
            response = await datasource.issues(
                first=batch_size,
                after=after_cursor,
                filter=team_filter,
                orderBy=order_by
            )

            if not response.success:
                self.logger.error(f"❌ Failed to fetch issues for team {team_key}: {response.message}")
                break

            issues_data = response.data.get("issues", {}) if response.data else {}
            issues_list = issues_data.get("nodes", [])
            page_info = issues_data.get("pageInfo", {})

            if not issues_list:
                break

            self.logger.info(f"📋 Fetched {len(issues_list)} issues for team {team_key}")

            # Process batch and transform to records
            batch_records: List[Tuple[Record, List[Permission]]] = []

            # Use transaction context to look up existing records
            async with self.data_store_provider.transaction() as tx_store:
                for issue_data in issues_list:
                    try:
                        issue_id = issue_data.get("id", "")

                        # Look up existing record to handle versioning
                        existing_record = await tx_store.get_record_by_external_id(
                            connector_id=self.connector_id,
                            external_id=issue_id
                        )

                        # Transform issue to TicketRecord with existing record info
                        ticket_record = self._transform_issue_to_ticket_record(
                            issue_data, team_id, existing_record
                        )

                        # Set indexing status based on filters
                        if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.ISSUES):
                            ticket_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                        # Records inherit permissions from RecordGroup (team), so pass empty list
                        # Permissions are set on RecordGroup in _fetch_teams (ORG for public, GROUP for private)
                        batch_records.append((ticket_record, []))

                        # Process comments for this issue
                        comment_records = await self._process_issue_comments(
                            issue_data, ticket_record, team_id, tx_store
                        )
                        batch_records.extend(comment_records)

                        # Extract files from issue description
                        issue_description = issue_data.get("description", "")
                        if issue_description:
                            # Get issue timestamps for file records
                            issue_created_at = self._parse_linear_datetime(issue_data.get("createdAt", "")) or 0
                            issue_updated_at = self._parse_linear_datetime(issue_data.get("updatedAt", "")) or 0

                            file_records = await self._extract_files_from_markdown(
                                markdown_text=issue_description,
                                parent_external_id=issue_id,
                                parent_node_id=ticket_record.id,
                                parent_record_type=RecordType.TICKET,
                                team_id=team_id,
                                tx_store=tx_store,
                                parent_created_at=issue_created_at,
                                parent_updated_at=issue_updated_at,
                                parent_weburl=ticket_record.weburl
                            )
                            batch_records.extend(file_records)

                        # because Linear doesn't update issue.updatedAt when attachments are added

                    except Exception as e:
                        issue_id = issue_data.get("id", "unknown")
                        self.logger.error(f"❌ Error processing issue {issue_id}: {e}", exc_info=True)
                        continue

            # Yield batch if we have records
            if batch_records:
                yield batch_records

            # Check if there are more pages
            if not page_info.get("hasNextPage", False):
                break

            after_cursor = page_info.get("endCursor")
            if not after_cursor:
                break

    async def _sync_attachments(
        self,
        team_record_groups: List[Tuple[RecordGroup, List[Permission]]]
    ) -> None:
        """
        Sync attachments separately from issues.

        Linear doesn't update issue.updatedAt when attachments are added,
        so we query attachments directly with their own sync point.
        This ensures new attachments are captured even if the parent issue wasn't updated.
        """
        if not team_record_groups:
            return

        # Build team lookup map for quick access
        team_map: Dict[str, Tuple[RecordGroup, List[Permission]]] = {}
        for team_record_group, team_perms in team_record_groups:
            team_id = team_record_group.external_group_id
            if team_id:
                team_map[team_id] = (team_record_group, team_perms)

        try:
            # Get attachments sync point (separate from issues sync point)
            last_sync_time = await self._get_attachments_sync_checkpoint()

            datasource = await self._get_fresh_datasource()
            after_cursor: Optional[str] = None
            total_attachments = 0
            max_attachment_updated_at: Optional[int] = None

            # Build filter for attachments
            attachment_filter: Dict[str, Any] = {}
            # Apply date filters (includes checkpoint merge)
            self._apply_date_filters_to_linear_filter(attachment_filter, last_sync_time)

            while True:
                response = await datasource.attachments(
                    first=50,
                    after=after_cursor,
                    filter=attachment_filter if attachment_filter else None
                )

                if not response.success:
                    self.logger.error(f"❌ Failed to fetch attachments: {response.message}")
                    break

                attachments_data = response.data.get("attachments", {}) if response.data else {}
                attachments_list = attachments_data.get("nodes", [])
                page_info = attachments_data.get("pageInfo", {})

                if not attachments_list:
                    break

                self.logger.info(f"📎 Fetched {len(attachments_list)} attachments")

                # Process attachments
                batch_records: List[Tuple[Record, List[Permission]]] = []

                async with self.data_store_provider.transaction() as tx_store:
                    for attachment_data in attachments_list:
                        try:
                            attachment_id = attachment_data.get("id", "")
                            if not attachment_id:
                                continue

                            # Get parent issue info
                            issue_data = attachment_data.get("issue", {})
                            issue_id = issue_data.get("id", "")
                            team_data = issue_data.get("team", {})
                            team_id = team_data.get("id", "")

                            if not team_id or team_id not in team_map:
                                # Skip attachments from teams not in our sync scope
                                continue

                            # Get parent issue's internal record ID
                            parent_record = await tx_store.get_record_by_external_id(
                                connector_id=self.connector_id,
                                external_id=issue_id
                            )
                            parent_node_id = parent_record.id if parent_record else None

                            if not parent_node_id:
                                # Parent issue not synced yet, skip this attachment
                                self.logger.debug(f"⚠️ Skipping attachment {attachment_id}: parent issue {issue_id} not synced")
                                continue

                            # Check if attachment already exists
                            existing_attachment = await tx_store.get_record_by_external_id(
                                connector_id=self.connector_id,
                                external_id=attachment_id
                            )

                            # Transform attachment to LinkRecord
                            link_record = self._transform_attachment_to_link_record(
                                attachment_data, issue_id, parent_node_id, team_id, existing_attachment
                            )

                            # Set indexing status based on filters
                            if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.ISSUE_ATTACHMENTS):
                                link_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                            # Look up related record by weburl
                            if link_record.weburl:
                                try:
                                    related_record = await tx_store.get_record_by_weburl(
                                        link_record.weburl,
                                        org_id=self.data_entities_processor.org_id
                                    )
                                    if related_record:
                                        link_record.linked_record_id = related_record.id
                                        self.logger.info(f"🔗 Found related record {related_record.id} for attachment URL: {link_record.weburl}")
                                except Exception as e:
                                    self.logger.debug(f"⚠️ Could not fetch related record for URL {link_record.weburl}: {e}")

                            batch_records.append((link_record, []))
                            total_attachments += 1

                            # Track max updatedAt
                            if link_record.source_updated_at:
                                if max_attachment_updated_at is None or link_record.source_updated_at > max_attachment_updated_at:
                                    max_attachment_updated_at = link_record.source_updated_at

                        except Exception as e:
                            attachment_id = attachment_data.get("id", "unknown")
                            self.logger.error(f"❌ Error processing attachment {attachment_id}: {e}", exc_info=True)
                            continue

                # Process batch
                if batch_records:
                    await self.data_entities_processor.on_new_records(batch_records)

                # Update sync point after each batch
                if max_attachment_updated_at:
                    await self._update_attachments_sync_checkpoint(max_attachment_updated_at)

                # Check for more pages
                if page_info.get("hasNextPage") and page_info.get("endCursor"):
                    after_cursor = page_info.get("endCursor")
                else:
                    break

            if total_attachments > 0:
                self.logger.info(f"✅ Synced {total_attachments} attachments")
            else:
                self.logger.info("ℹ️ No new/updated attachments")

        except Exception as e:
            self.logger.error(f"❌ Error syncing attachments: {e}", exc_info=True)

    async def _sync_documents(
        self,
        team_record_groups: List[Tuple[RecordGroup, List[Permission]]]
    ) -> None:
        """
        Sync documents separately from issues.

        Linear doesn't update issue.updatedAt when documents are added,
        so we query documents directly with their own sync point.
        This ensures new documents are captured even if the parent issue wasn't updated.
        """
        if not team_record_groups:
            return

        # Build team lookup map for quick access
        team_map: Dict[str, Tuple[RecordGroup, List[Permission]]] = {}
        for team_record_group, team_perms in team_record_groups:
            team_id = team_record_group.external_group_id
            if team_id:
                team_map[team_id] = (team_record_group, team_perms)

        try:
            # Get documents sync point (separate from issues sync point)
            last_sync_time = await self._get_documents_sync_checkpoint()

            datasource = await self._get_fresh_datasource()
            after_cursor: Optional[str] = None
            total_documents = 0
            max_document_updated_at: Optional[int] = None

            # Build filter for documents
            document_filter: Dict[str, Any] = {}
            # Apply date filters (includes checkpoint merge)
            self._apply_date_filters_to_linear_filter(document_filter, last_sync_time)

            while True:
                response = await datasource.documents(
                    first=50,
                    after=after_cursor,
                    filter=document_filter if document_filter else None
                )

                if not response.success:
                    self.logger.error(f"❌ Failed to fetch documents: {response.message}")
                    break

                documents_data = response.data.get("documents", {}) if response.data else {}
                documents_list = documents_data.get("nodes", [])
                page_info = documents_data.get("pageInfo", {})

                if not documents_list:
                    break

                self.logger.info(f"📄 Fetched {len(documents_list)} documents")

                # Process documents
                batch_records: List[Tuple[Record, List[Permission]]] = []

                async with self.data_store_provider.transaction() as tx_store:
                    for document_data in documents_list:
                        try:
                            document_id = document_data.get("id", "")
                            if not document_id:
                                continue

                            # Get parent issue info
                            issue_data = document_data.get("issue")

                            # Skip documents without an issue (standalone documents)
                            # Track updatedAt to avoid refetching in next sync
                            if not issue_data:
                                document_updated_at = self._parse_linear_datetime(document_data.get("updatedAt", "")) or 0
                                if document_updated_at:
                                    if max_document_updated_at is None or document_updated_at > max_document_updated_at:
                                        max_document_updated_at = document_updated_at
                                self.logger.debug(f"⚠️ Skipping document {document_id}: no parent issue (standalone document)")
                                continue

                            issue_id = issue_data.get("id", "")
                            issue_identifier = issue_data.get("identifier", "")
                            team_data = issue_data.get("team", {})
                            team_id = team_data.get("id", "")

                            if not issue_id or not team_id:
                                self.logger.debug(f"⚠️ Skipping document {document_id}: missing issue or team info")
                                continue

                            if team_id not in team_map:
                                # Skip documents from teams not in our sync scope
                                continue

                            # Get parent issue's internal record ID
                            parent_record = await tx_store.get_record_by_external_id(
                                connector_id=self.connector_id,
                                external_id=issue_id
                            )
                            parent_node_id = parent_record.id if parent_record else None

                            if not parent_node_id:
                                # Parent issue not synced yet, skip this document
                                self.logger.debug(f"⚠️ Skipping document {document_id}: parent issue {issue_id} not synced")
                                continue

                            # Check if document already exists
                            existing_document = await tx_store.get_record_by_external_id(
                                connector_id=self.connector_id,
                                external_id=document_id
                            )

                            # Transform document to WebpageRecord
                            webpage_record = self._transform_document_to_webpage_record(
                                document_data, issue_id, parent_node_id, team_id, existing_document
                            )

                            # Set indexing status based on filters
                            if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.DOCUMENTS):
                                webpage_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                            batch_records.append((webpage_record, []))
                            total_documents += 1

                            self.logger.debug(f"✅ Processed document {document_id[:8]} (issue: {issue_identifier})")

                            # Track max updatedAt
                            if webpage_record.source_updated_at:
                                if max_document_updated_at is None or webpage_record.source_updated_at > max_document_updated_at:
                                    max_document_updated_at = webpage_record.source_updated_at

                        except Exception as e:
                            document_id = document_data.get("id", "unknown")
                            self.logger.error(f"❌ Error processing document {document_id}: {e}", exc_info=True)
                            continue

                    # Process batch inside transaction
                    if batch_records:
                        self.logger.info(f"💾 Processing batch of {len(batch_records)} documents")
                        await self.data_entities_processor.on_new_records(batch_records)
                        self.logger.debug("✅ Batch processed successfully")
                        batch_records = []  # Clear batch after processing

                # Update sync point after each batch
                if max_document_updated_at:
                    await self._update_documents_sync_checkpoint(max_document_updated_at)

                # Check for more pages
                if page_info.get("hasNextPage") and page_info.get("endCursor"):
                    after_cursor = page_info.get("endCursor")
                else:
                    break

            if total_documents > 0:
                self.logger.info(f"✅ Synced {total_documents} documents")
            else:
                self.logger.info("ℹ️ No new/updated documents")

        except Exception as e:
            self.logger.error(f"❌ Error syncing documents: {e}", exc_info=True)

    async def _sync_projects_for_teams(
        self,
        team_record_groups: List[Tuple[RecordGroup, List[Permission]]]
    ) -> None:
        """
        Sync projects for all teams with batch processing and incremental sync.
        Uses team-level sync points, exactly like _sync_issues_for_teams().

        Sync point logic:
        - Before sync: Read last_sync_time for each team
        - Query: Fetch projects with teams filter and updatedAt > last_sync_time
        - After EACH batch: Update last_sync_time to max project updated_at (fault tolerance)

        Args:
            team_record_groups: List of (RecordGroup, permissions) tuples for teams to sync
        """
        if not team_record_groups:
            self.logger.info("ℹ️ No teams to sync projects for")
            return

        for team_record_group, team_perms in team_record_groups:
            try:
                team_id = team_record_group.external_group_id
                team_key = team_record_group.short_name or team_record_group.name

                self.logger.info(f"📋 Starting project sync for team: {team_key}")

                # Read team-level sync point
                last_sync_time = await self._get_team_project_sync_checkpoint(team_key)

                if last_sync_time:
                    self.logger.info(f"🔄 Incremental project sync for team {team_key} from {last_sync_time}")

                # Fetch and process projects for this team
                total_records_processed = 0
                total_projects = 0
                # Track max updatedAt ONLY from projects (TicketRecords)
                # We query by project.updatedAt, so sync point must be based on project timestamps
                max_project_updated_at: Optional[int] = None

                async for batch_records in self._fetch_projects_for_team_batch(
                    team_id=team_id,
                    team_key=team_key,
                    last_sync_time=last_sync_time
                ):
                    if not batch_records:
                        continue

                    # Count records by type and track max project updatedAt
                    for record, _ in batch_records:
                        if isinstance(record, TicketRecord):
                            total_projects += 1
                            # Only track max from PROJECTS - sync point must match project query filter
                            if record.source_updated_at:
                                if max_project_updated_at is None or record.source_updated_at > max_project_updated_at:
                                    max_project_updated_at = record.source_updated_at

                    # Process batch
                    total_records_processed += len(batch_records)
                    await self.data_entities_processor.on_new_records(batch_records)

                    # Update sync point after each batch for fault tolerance
                    # Uses max from PROJECTS ONLY (we query by project.updatedAt)
                    if max_project_updated_at:
                        await self._update_team_project_sync_checkpoint(team_key, max_project_updated_at)

                # Log final status
                if total_records_processed > 0:
                    self.logger.info(f"✅ Team {team_key}: {total_projects} projects ({total_records_processed} total)")
                else:
                    self.logger.info(f"ℹ️ No new/updated projects for team {team_key}")

            except Exception as e:
                team_name = team_record_group.name or team_record_group.short_name or "unknown"
                self.logger.error(f"❌ Error syncing projects for team {team_name}: {e}", exc_info=True)
                continue

    async def _fetch_projects_for_team_batch(
        self,
        team_id: str,
        team_key: str,
        last_sync_time: Optional[int] = None,
        batch_size: int = 50
    ) -> AsyncGenerator[List[Tuple[Record, List[Permission]]], None]:
        """
        Fetch projects for a team with pagination and incremental sync, yielding batches.
        Follows exact same pattern as _fetch_issues_for_team_batch().

        Args:
            team_id: Team ID
            team_key: Team key (e.g., "ENG")
            last_sync_time: Last sync timestamp in ms (for incremental sync)
            batch_size: Number of projects to fetch per batch

        Yields:
            Batches of (record, permissions) tuples
        """
        datasource = await self._get_fresh_datasource()
        after_cursor: Optional[str] = None

        # Build filter for team - projects use "accessibleTeams" filter
        team_filter: Dict[str, Any] = {
            "accessibleTeams": {
                "some": {
                    "id": {"eq": team_id}
                }
            }
        }

        # Apply date filters to team_filter
        self._apply_date_filters_to_linear_filter(team_filter, last_sync_time)

        while True:
            # Fetch projects batch
            response = await datasource.projects(
                first=batch_size,
                after=after_cursor,
                filter=team_filter,
                orderBy=None
            )

            if not response.success:
                self.logger.error(f"❌ Failed to fetch projects for team {team_key}: {response.message}")
                break

            projects_data = response.data.get("projects", {}) if response.data else {}
            projects_list = projects_data.get("nodes", [])
            page_info = projects_data.get("pageInfo", {})

            if not projects_list:
                break

            self.logger.info(f"📋 Fetched {len(projects_list)} projects for team {team_key}")

            # Process batch and transform to records
            batch_records: List[Tuple[Record, List[Permission]]] = []

            # Use transaction context to look up existing records
            async with self.data_store_provider.transaction() as tx_store:
                for project_data in projects_list:
                    try:
                        project_id = project_data.get("id", "")

                        # Look up existing record to handle versioning
                        existing_record = await tx_store.get_record_by_external_id(
                            connector_id=self.connector_id,
                            external_id=project_id
                        )

                        # Transform project to TicketRecord with existing record info
                        project_record = self._transform_project_to_ticket_record(
                            project_data, team_id, existing_record
                        )

                        # Set indexing status based on filters
                        if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.PROJECTS):
                            project_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                        # Records inherit permissions from RecordGroup (team), so pass empty list
                        batch_records.append((project_record, []))

                    except Exception as e:
                        project_id = project_data.get("id", "unknown")
                        self.logger.error(f"❌ Error processing project {project_id}: {e}", exc_info=True)
                        continue

            # Yield batch if we have records
            if batch_records:
                yield batch_records

            # Check if there are more pages
            if not page_info.get("hasNextPage", False):
                break

            after_cursor = page_info.get("endCursor")
            if not after_cursor:
                break

    # ==================== HELPER FUNCTIONS ====================

    async def _process_issue_comments(
        self,
        issue_data: Dict[str, Any],
        ticket_record: TicketRecord,
        team_id: str,
        tx_store
    ) -> List[Tuple[Record, List[Permission]]]:
        """
        Process comments for an issue.

        Args:
            issue_data: Raw issue data from Linear API
            ticket_record: The parent ticket record
            team_id: Team ID for external_record_group_id
            tx_store: Transaction store for looking up existing records

        Returns:
            List of (CommentRecord, permissions) tuples
        """
        comment_records: List[Tuple[Record, List[Permission]]] = []
        issue_id = issue_data.get("id", "")
        issue_identifier = issue_data.get("identifier", "")
        comments_data = issue_data.get("comments", {}).get("nodes", [])

        if not comments_data:
            return comment_records

        for comment_data in comments_data:
            try:
                comment_id = comment_data.get("id", "")
                if not comment_id:
                    continue

                # Look up existing comment record to handle versioning
                existing_comment = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=comment_id
                )

                comment_record = self._transform_comment_to_comment_record(
                    comment_data, issue_id, issue_identifier, ticket_record.id, team_id, existing_comment
                )

                # Set indexing status based on filters
                if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.ISSUE_COMMENTS):
                    comment_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                # Comments inherit permissions from RecordGroup (team), so pass empty list
                comment_records.append((comment_record, []))

                # Extract files from comment body
                comment_body = comment_data.get("body", "")
                if comment_body:
                    # Get comment timestamps for file records
                    comment_created_at = self._parse_linear_datetime(comment_data.get("createdAt", "")) or 0
                    comment_updated_at = self._parse_linear_datetime(comment_data.get("updatedAt", "")) or 0

                    file_records = await self._extract_files_from_markdown(
                        markdown_text=comment_body,
                        parent_external_id=comment_id,
                        parent_node_id=ticket_record.id,  # Use ticket's internal ID, not comment's
                        parent_record_type=RecordType.COMMENT,
                        team_id=team_id,
                        tx_store=tx_store,
                        parent_created_at=comment_created_at,
                        parent_updated_at=comment_updated_at,
                        parent_weburl=comment_record.weburl
                    )
                    comment_records.extend(file_records)

            except Exception as e:
                comment_id = comment_data.get("id", "unknown")
                self.logger.error(f"❌ Error processing comment {comment_id} for issue {issue_id}: {e}", exc_info=True)
                continue

        if comment_records:
            issue_identifier = issue_data.get("identifier", issue_id)
            self.logger.debug(f"💬 Issue {issue_identifier}: {len(comment_records)} comments")

        return comment_records

    async def _extract_files_from_markdown(
        self,
        markdown_text: str,
        parent_external_id: str,
        parent_node_id: str,
        parent_record_type: RecordType,
        team_id: str,
        tx_store,
        parent_created_at: Optional[int] = None,
        parent_updated_at: Optional[int] = None,
        parent_weburl: Optional[str] = None
    ) -> List[Tuple[Record, List[Permission]]]:
        """
        Extract files from markdown text and create FileRecords.

        Args:
            markdown_text: Markdown content to extract files from
            parent_external_id: External ID of parent (issue or comment)
            parent_node_id: Internal ID of parent record
            parent_record_type: Type of parent record (TICKET or COMMENT)
            team_id: Team ID for external_record_group_id
            tx_store: Transaction store for looking up existing records
            parent_created_at: Source created timestamp of parent (in ms)
            parent_updated_at: Source updated timestamp of parent (in ms)
            parent_weburl: Web URL of parent record (used for file weburl)

        Returns:
            List of (FileRecord, permissions) tuples
        """
        file_records: List[Tuple[Record, List[Permission]]] = []

        if not markdown_text:
            return file_records

        # Extract file URLs from markdown
        file_urls = self._extract_file_urls_from_markdown(markdown_text)

        if not file_urls:
            return file_records

        for file_info in file_urls:
            try:
                file_url = file_info["url"]
                filename = file_info["filename"]

                # Extract file UUID from URL to use as external_record_id
                external_id = file_url.rstrip('/').split('/')[-1] if file_url else file_url

                # Look up existing file record
                existing_file = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=external_id
                )

                # Transform to FileRecord
                file_record = self._transform_file_url_to_file_record(
                    file_url=file_url,
                    filename=filename,
                    parent_external_id=parent_external_id,
                    parent_node_id=parent_node_id,
                    parent_record_type=parent_record_type,
                    team_id=team_id,
                    existing_record=existing_file,
                    parent_created_at=parent_created_at,
                    parent_updated_at=parent_updated_at,
                    parent_weburl=parent_weburl
                )

                # Set indexing status based on filters
                if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.FILES):
                    file_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                # Files inherit permissions from parent, so pass empty list
                file_records.append((file_record, []))

            except Exception as e:
                self.logger.error(f"❌ Error processing file {file_info.get('url', 'unknown')}: {e}", exc_info=True)
                continue

        return file_records

    def _extract_file_urls_from_markdown(self, markdown_text: str) -> List[Dict[str, str]]:
        """
        Extract file URLs from markdown text (images and file links).
        Only extracts URLs from Linear uploads (uploads.linear.app).

        Args:
            markdown_text: Markdown content to extract from

        Returns:
            List of dicts with 'url', 'filename', 'alt_text' keys
        """
        if not markdown_text:
            return []

        file_urls: List[Dict[str, str]] = []
        seen_urls: Set[str] = set()

        # Pattern for markdown images: ![alt text](url)
        image_pattern = r'!\[([^\]]*)\]\(([^)]+)\)'

        # Pattern for markdown links: [text](url) - only if URL looks like a file
        link_pattern = r'\[([^\]]+)\]\(([^)]+)\)'

        # Extract images
        for match in re.finditer(image_pattern, markdown_text):
            alt_text = match.group(1) or ""
            url = match.group(2).strip()

            # Only process Linear upload URLs
            if "uploads.linear.app" in url and url not in seen_urls:
                seen_urls.add(url)
                filename = alt_text if alt_text else url.split("/")[-1].split("?")[0]
                file_urls.append({
                    "url": url,
                    "filename": filename,
                    "alt_text": alt_text
                })

        # Extract file links (all Linear upload URLs, not just those with extensions)
        for match in re.finditer(link_pattern, markdown_text):
            link_text = match.group(1) or ""
            url = match.group(2).strip()

            # Process all Linear upload URLs (images are already extracted above, so this catches file links)
            if "uploads.linear.app" in url and url not in seen_urls:
                seen_urls.add(url)
                url_path = url.split("?")[0]  # Remove query params
                filename = link_text if link_text else url_path.split("/")[-1]
                file_urls.append({
                    "url": url,
                    "filename": filename,
                    "alt_text": link_text
                })

        return file_urls

    def _get_mime_type_from_url(self, url: str, filename: str = "") -> str:
        """
        Determine mime type from URL or filename extension.

        Args:
            url: File URL
            filename: Optional filename

        Returns:
            Mime type string
        """
        # Extract extension from filename or URL
        ext = ""
        if filename and "." in filename:
            ext = filename.split(".")[-1].lower()
        elif "." in url:
            url_path = url.split("?")[0]  # Remove query params
            ext = url_path.split(".")[-1].lower()

        # Map common extensions to mime types
        extension_to_mime = {
            "pdf": MimeTypes.PDF.value,
            "png": MimeTypes.PNG.value,
            "jpg": MimeTypes.JPEG.value,
            "jpeg": MimeTypes.JPEG.value,
            "gif": MimeTypes.GIF.value,
            "webp": MimeTypes.WEBP.value,
            "svg": MimeTypes.SVG.value,
            "doc": MimeTypes.DOC.value,
            "docx": MimeTypes.DOCX.value,
            "xls": MimeTypes.XLS.value,
            "xlsx": MimeTypes.XLSX.value,
            "ppt": MimeTypes.PPT.value,
            "pptx": MimeTypes.PPTX.value,
            "csv": MimeTypes.CSV.value,
            "zip": MimeTypes.ZIP.value,
            "json": MimeTypes.JSON.value,
            "xml": MimeTypes.XML.value,
            "txt": MimeTypes.PLAIN_TEXT.value,
            "md": MimeTypes.MARKDOWN.value,
            "html": MimeTypes.HTML.value,
        }

        return extension_to_mime.get(ext, MimeTypes.UNKNOWN.value)

    # ==================== TRANSFORMATIONS ====================

    def _transform_issue_to_ticket_record(
        self,
        issue_data: Dict[str, Any],
        team_id: str,
        existing_record: Optional[Record] = None
    ) -> TicketRecord:
        """
        Transform Linear issue data to TicketRecord.
        This method is reusable for both initial sync and reindex operations.

        Args:
            issue_data: Raw issue data from Linear API (from GraphQL query)
            team_id: Team ID for external_record_group_id
            existing_record: Existing record from DB (if any) for version handling

        Returns:
            TicketRecord: Transformed ticket record
        """
        issue_id = issue_data.get("id", "")
        if not issue_id:
            raise ValueError("Issue data missing required 'id' field")

        identifier = issue_data.get("identifier", "")
        title = issue_data.get("title", "")

        # Build record name: "ENG-123: Title" or fallback to identifier or title
        if identifier and title:
            record_name = f"{identifier}: {title}"
        elif identifier:
            record_name = identifier
        elif title:
            record_name = title
        else:
            # Last resort: use issue ID but log a warning
            self.logger.warning(f"Issue {issue_id} missing both identifier and title, using ID as record name")
            record_name = issue_id

        # Ensure team_id is not None or empty
        if not team_id:
            self.logger.error(f"Issue {issue_id} has no team_id, cannot create record without team association")
            raise ValueError(f"team_id is required but was {team_id}")

        # Priority mapping (Linear uses numeric: 0=None, 1=Urgent, 2=High, 3=Medium, 4=Low)
        priority_num = issue_data.get("priority")
        priority_map = {1: "Urgent", 2: "High", 3: "Medium", 4: "Low"}
        priority = priority_map.get(priority_num) if priority_num else None

        # State information
        state = issue_data.get("state", {})
        state_name = state.get("name") if state else None
        state_type = state.get("type") if state else None

        # Assignee information
        assignee = issue_data.get("assignee", {})
        assignee_email = assignee.get("email") if assignee else None
        assignee_name = assignee.get("displayName") or assignee.get("name") if assignee else None

        # Creator information
        creator = issue_data.get("creator", {})
        creator_email = creator.get("email") if creator else None
        creator_name = creator.get("displayName") or creator.get("name") if creator else None

        # Parent issue (for sub-issues)
        parent = issue_data.get("parent")
        parent_external_record_id = parent.get("id") if parent else None

        # Timestamps
        created_at = self._parse_linear_datetime(issue_data.get("createdAt", "")) or 0
        updated_at = self._parse_linear_datetime(issue_data.get("updatedAt", "")) or 0

        # Handle versioning: use existing record's id and increment version if changed
        is_new = existing_record is None
        record_id = existing_record.id if existing_record else str(uuid4())

        if is_new:
            version = 0
        elif hasattr(existing_record, 'source_updated_at') and existing_record.source_updated_at != updated_at:
            version = existing_record.version + 1
            self.logger.debug(f"📝 Issue {identifier} changed, incrementing version to {version}")
        else:
            version = existing_record.version if existing_record else 0

        # Get web URL directly from Linear API response
        weburl = issue_data.get("url")

        # Create TicketRecord
        # Use updatedAt as external_revision_id so placeholders (None) will trigger update
        external_revision_id = str(updated_at) if updated_at else None

        ticket = TicketRecord(
            id=record_id,
            org_id=self.data_entities_processor.org_id,
            record_name=record_name,
            record_type=RecordType.TICKET,
            external_record_id=issue_id,
            external_revision_id=external_revision_id,
            external_record_group_id=team_id,
            parent_external_record_id=parent_external_record_id,
            parent_record_type=RecordType.TICKET if parent_external_record_id else None,
            version=version,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            mime_type=MimeTypes.MARKDOWN.value,
            weburl=weburl,
            created_at=get_epoch_timestamp_in_ms(),
            updated_at=get_epoch_timestamp_in_ms(),
            source_created_at=created_at,
            source_updated_at=updated_at,
            status=state_name,
            priority=priority,
            type=state_type,
            assignee=assignee_name,
            preview_renderable=False,
            assignee_email=assignee_email,
            creator_email=creator_email,
            creator_name=creator_name,
            inherit_permissions=True,
        )

        return ticket

    def _transform_project_to_ticket_record(
        self,
        project_data: Dict[str, Any],
        team_id: str,
        existing_record: Optional[Record] = None
    ) -> TicketRecord:
        """
        Transform Linear project to TicketRecord.
        Normalizes project data to match issue structure and reuses _transform_issue_to_ticket_record().

        Args:
            project_data: Raw project data from Linear API
            team_id: Team ID for external_record_group_id
            existing_record: Existing record from DB (if any) for version handling

        Returns:
            TicketRecord: Transformed ticket record
        """
        project_id = project_data.get("id", "")
        if not project_id:
            raise ValueError("Project data missing required 'id' field")

        # Build record_name: Use name directly (projects don't have meaningful identifiers like issues)
        name = project_data.get("name", "")
        slug_id = project_data.get("slugId", "")
        if name:
            record_name = name
        elif slug_id:
            record_name = slug_id
        else:
            self.logger.warning(f"Project {project_id} missing both slugId and name, using ID as record name")
            record_name = project_id

        # Normalize project data to match issue structure
        normalized_data = project_data.copy()

        # Map name -> title (projects use name, issues use title)
        # Set title to the final record_name we want (since identifier will be empty, transform will use just title)
        normalized_data["title"] = record_name
        normalized_data["identifier"] = ""  # Projects don't have identifiers like issues

        # Map status -> state (projects use status, issues use state)
        status = project_data.get("status", {})
        status_type = status.get("type") if status else None
        if status:
            normalized_data["state"] = {
                "name": status.get("name"),
                "type": status_type or "project"
            }
        else:
            normalized_data["state"] = {"type": "project"}

        # Map priorityLabel -> priority (projects have priorityLabel, issues have numeric priority)
        priority_label = project_data.get("priorityLabel", "")
        priority_map = {"Urgent": 1, "High": 2, "Medium": 3, "Low": 4, "No priority": None}
        normalized_data["priority"] = priority_map.get(priority_label) if priority_label else None

        # Map lead -> assignee (projects use lead, issues use assignee)
        lead = project_data.get("lead", {})
        if lead:
            normalized_data["assignee"] = lead
        else:
            normalized_data["assignee"] = {}

        # Projects don't have parent, so ensure it's None
        normalized_data["parent"] = None

        # Reuse existing transform function
        ticket = self._transform_issue_to_ticket_record(normalized_data, team_id, existing_record)

        # Override type to ensure it's "project" if status_type was None
        if not ticket.type:
            ticket.type = "project"

        return ticket

    def _transform_comment_to_comment_record(
        self,
        comment_data: Dict[str, Any],
        issue_id: str,
        issue_identifier: str,
        parent_node_id: str,
        team_id: str,
        existing_record: Optional[Record] = None
    ) -> CommentRecord:
        """
        Transform Linear comment data to CommentRecord.
        This method handles versioning similar to TicketRecord.
        Follows Jira pattern for record naming.

        Args:
            comment_data: Raw comment data from Linear API
            issue_id: Parent issue external ID
            issue_identifier: Parent issue identifier (e.g., "BAC-1")
            parent_node_id: Internal record ID of parent ticket
            team_id: Team ID for external_record_group_id
            existing_record: Existing record from DB (if any) for version handling

        Returns:
            CommentRecord: Transformed comment record
        """
        comment_id = comment_data.get("id", "")
        if not comment_id:
            raise ValueError("Comment data missing required 'id' field")

        comment_data.get("body", "")
        user = comment_data.get("user", {})
        author_source_id = user.get("id", "") if user else ""
        author_name = user.get("displayName") or user.get("name") or "Unknown"

        # Timestamps
        created_at = self._parse_linear_datetime(comment_data.get("createdAt", "")) or 0
        updated_at = self._parse_linear_datetime(comment_data.get("updatedAt", "")) or 0

        # Use updatedAt as external_revision_id
        external_revision_id = str(updated_at) if updated_at else None

        # Build record name following Jira pattern: "Comment by {author} on {issue_key}"
        if issue_identifier:
            record_name = f"Comment by {author_name} on {issue_identifier}"
        else:
            record_name = f"Comment by {author_name}"

        # Get web URL directly from Linear API response
        weburl = comment_data.get("url")

        # Handle versioning: use existing record's id and increment version if changed
        is_new = existing_record is None
        record_id = existing_record.id if existing_record else str(uuid4())

        if is_new:
            version = 0
        elif hasattr(existing_record, 'source_updated_at') and existing_record.source_updated_at != updated_at:
            version = existing_record.version + 1
            self.logger.debug(f"📝 Comment {comment_id[:8]} changed, incrementing version to {version}")
        else:
            version = existing_record.version if existing_record else 0

        comment = CommentRecord(
            id=record_id,
            org_id=self.data_entities_processor.org_id,
            record_name=record_name,
            record_type=RecordType.COMMENT,
            external_record_id=comment_id,
            external_revision_id=external_revision_id,
            external_record_group_id=team_id,
            parent_external_record_id=issue_id,
            parent_record_type=RecordType.TICKET,
            version=version,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            mime_type=MimeTypes.MARKDOWN.value,
            weburl=weburl,
            created_at=get_epoch_timestamp_in_ms(),
            updated_at=get_epoch_timestamp_in_ms(),
            source_created_at=created_at,
            source_updated_at=updated_at,
            author_source_id=author_source_id,
            preview_renderable=False,
            is_dependent_node=True,
            parent_node_id=parent_node_id,
            inherit_permissions=True,
        )

        return comment

    def _transform_attachment_to_link_record(
        self,
        attachment_data: Dict[str, Any],
        issue_id: str,
        parent_node_id: str,
        team_id: str,
        existing_record: Optional[Record] = None
    ) -> LinkRecord:
        """
        Transform Linear attachment data to LinkRecord.
        This method handles versioning similar to TicketRecord.

        Args:
            attachment_data: Raw attachment data from Linear API
            issue_id: Parent issue external ID
            parent_node_id: Internal record ID of parent ticket
            team_id: Team ID for external_record_group_id
            existing_record: Existing record from DB (if any) for version handling

        Returns:
            LinkRecord: Transformed link record
        """
        attachment_id = attachment_data.get("id", "")
        if not attachment_id:
            raise ValueError("Attachment data missing required 'id' field")

        url = attachment_data.get("url", "")
        if not url:
            raise ValueError(f"Attachment {attachment_id} missing required 'url' field")

        title = attachment_data.get("title") or attachment_data.get("subtitle")

        # Timestamps
        created_at = self._parse_linear_datetime(attachment_data.get("createdAt", "")) or 0
        updated_at = self._parse_linear_datetime(attachment_data.get("updatedAt", "")) or 0

        # Use updatedAt as external_revision_id
        external_revision_id = str(updated_at) if updated_at else None

        # Set is_public to UNKNOWN for Linear attachments (we don't know if they're public or private)
        is_public = LinkPublicStatus.UNKNOWN

        # Build record name
        record_name = title if title else url.split("/")[-1] or f"Attachment {attachment_id[:8]}"

        # Handle versioning: use existing record's id and increment version if changed
        is_new = existing_record is None
        record_id = existing_record.id if existing_record else str(uuid4())

        if is_new:
            version = 0
        elif hasattr(existing_record, 'source_updated_at') and existing_record.source_updated_at != updated_at:
            version = existing_record.version + 1
            self.logger.debug(f"📝 Attachment {attachment_id[:8]} changed, incrementing version to {version}")
        else:
            version = existing_record.version if existing_record else 0

        link = LinkRecord(
            id=record_id,
            org_id=self.data_entities_processor.org_id,
            record_name=record_name,
            record_type=RecordType.LINK,
            external_record_id=attachment_id,
            external_revision_id=external_revision_id,
            external_record_group_id=team_id,
            parent_external_record_id=issue_id,
            parent_record_type=RecordType.TICKET,
            version=version,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            mime_type=MimeTypes.MARKDOWN.value,
            weburl=url,
            url=url,
            title=title,
            is_public=is_public,
            created_at=get_epoch_timestamp_in_ms(),
            updated_at=get_epoch_timestamp_in_ms(),
            source_created_at=created_at,
            source_updated_at=updated_at,
            preview_renderable=False,
            is_dependent_node=True,
            parent_node_id=parent_node_id,
            inherit_permissions=True,
        )

        return link

    def _transform_document_to_webpage_record(
        self,
        document_data: Dict[str, Any],
        issue_id: str,
        parent_node_id: str,
        team_id: str,
        existing_record: Optional[Record] = None
    ) -> WebpageRecord:
        """
        Transform Linear document data to WebpageRecord.
        This method handles versioning similar to TicketRecord.

        Args:
            document_data: Raw document data from Linear API
            issue_id: Parent issue external ID
            parent_node_id: Internal record ID of parent ticket
            team_id: Team ID for external_record_group_id
            existing_record: Existing record from DB (if any) for version handling

        Returns:
            WebpageRecord: Transformed webpage record
        """
        document_id = document_data.get("id", "")
        if not document_id:
            raise ValueError("Document data missing required 'id' field")

        url = document_data.get("url", "")
        if not url:
            raise ValueError(f"Document {document_id} missing required 'url' field")

        title = document_data.get("title", "")
        document_data.get("slugId", "")

        # Timestamps
        created_at = self._parse_linear_datetime(document_data.get("createdAt", "")) or 0
        updated_at = self._parse_linear_datetime(document_data.get("updatedAt", "")) or 0

        # Use updatedAt as external_revision_id
        external_revision_id = str(updated_at) if updated_at else None

        # Build record name
        record_name = title if title else f"Document {document_id[:8]}"

        # Handle versioning: use existing record's id and increment version if changed
        is_new = existing_record is None
        record_id = existing_record.id if existing_record else str(uuid4())

        if is_new:
            version = 0
        elif hasattr(existing_record, 'source_updated_at') and existing_record.source_updated_at != updated_at:
            version = existing_record.version + 1
            self.logger.debug(f"📝 Document {document_id[:8]} changed, incrementing version to {version}")
        else:
            version = existing_record.version if existing_record else 0

        webpage = WebpageRecord(
            id=record_id,
            org_id=self.data_entities_processor.org_id,
            record_name=record_name,
            record_type=RecordType.WEBPAGE,
            external_record_id=document_id,
            external_revision_id=external_revision_id,
            external_record_group_id=team_id,
            parent_external_record_id=issue_id,
            parent_record_type=RecordType.TICKET,
            version=version,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            mime_type=MimeTypes.MARKDOWN.value,
            weburl=url,
            created_at=get_epoch_timestamp_in_ms(),
            updated_at=get_epoch_timestamp_in_ms(),
            source_created_at=created_at,
            source_updated_at=updated_at,
            preview_renderable=True,
            is_dependent_node=True,
            parent_node_id=parent_node_id,
            inherit_permissions=True,
        )

        return webpage

    def _transform_file_url_to_file_record(
        self,
        file_url: str,
        filename: str,
        parent_external_id: str,
        parent_node_id: str,
        parent_record_type: RecordType,
        team_id: str,
        existing_record: Optional[Record] = None,
        parent_created_at: Optional[int] = None,
        parent_updated_at: Optional[int] = None,
        parent_weburl: Optional[str] = None
    ) -> FileRecord:
        """
        Transform a file URL to FileRecord.

        Args:
            file_url: URL of the file
            filename: Name of the file
            parent_external_id: External ID of parent (issue or comment)
            parent_node_id: Internal ID of parent record
            parent_record_type: Type of parent record (TICKET or COMMENT)
            team_id: Team ID for external_record_group_id
            existing_record: Existing record from DB (if any) for version handling
            parent_created_at: Source created timestamp of parent (in ms)
            parent_updated_at: Source updated timestamp of parent (in ms)
            parent_weburl: Web URL of parent record (used for file weburl instead of file_url)

        Returns:
            FileRecord: Transformed file record
        """
        # Extract file UUID from URL to use as external_record_id
        file_id = file_url.rstrip('/').split('/')[-1] if file_url else file_url

        # Extract extension and determine mime type
        extension = None
        if "." in filename:
            extension = filename.split(".")[-1].lower()

        mime_type = self._get_mime_type_from_url(file_url, filename)

        # Handle versioning
        is_new = existing_record is None
        record_id = existing_record.id if existing_record else str(uuid4())

        if is_new:
            version = 0
        else:
            # For files, we don't track updatedAt, so keep same version unless URL changed
            version = existing_record.version if existing_record else 0

        file_record = FileRecord(
            id=record_id,
            org_id=self.data_entities_processor.org_id,
            record_name=filename,
            record_type=RecordType.FILE,
            external_record_id=file_id,
            external_revision_id=None,
            external_record_group_id=team_id,
            parent_external_record_id=parent_external_id,
            parent_record_type=parent_record_type,
            version=version,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            mime_type=mime_type,
            weburl=parent_weburl or file_url,  # Use parent's weburl if available, otherwise fallback to file_url
            created_at=get_epoch_timestamp_in_ms(),
            updated_at=get_epoch_timestamp_in_ms(),
            source_created_at=parent_created_at,
            source_updated_at=parent_updated_at ,
            preview_renderable=True,
            is_dependent_node=True,
            parent_node_id=parent_node_id,
            inherit_permissions=True,
            is_file=True,
            extension=extension,
            size_in_bytes=0,
        )

        return file_record

    # ==================== DATE FILTERS ====================

    def _apply_date_filters_to_linear_filter(
        self,
        linear_filter: Dict[str, Any],
        last_sync_time: Optional[int] = None
    ) -> None:
        """
        Apply date filters (modified/created) to Linear filter dict.
        Merges filter values with checkpoint timestamp for incremental sync.

        Args:
            linear_filter: Linear filter dict to modify (in-place)
            last_sync_time: Last sync timestamp in ms (for incremental sync)
        """
        # Get modified filter from sync_filters
        modified_filter = self.sync_filters.get(SyncFilterKey.MODIFIED) if self.sync_filters else None
        modified_after_ts = None
        modified_before_ts = None

        if modified_filter:
            modified_after_ts, modified_before_ts = modified_filter.get_value(default=(None, None))

        # Get created filter from sync_filters
        created_filter = self.sync_filters.get(SyncFilterKey.CREATED) if self.sync_filters else None
        created_after_ts = None
        created_before_ts = None

        if created_filter:
            created_after_ts, created_before_ts = created_filter.get_value(default=(None, None))

        # Merge modified_after with checkpoint (use the latest)
        if modified_after_ts and last_sync_time:
            modified_after_ts = max(modified_after_ts, last_sync_time)
            self.logger.info(f"🔄 Using latest modified_after: {modified_after_ts} (filter: {modified_after_ts}, checkpoint: {last_sync_time})")
        elif modified_after_ts:
            self.logger.info(f"🔍 Using filter: Fetching issues modified after {modified_after_ts}")
        elif last_sync_time:
            modified_after_ts = last_sync_time
            self.logger.info(f"🔄 Incremental sync: Fetching issues updated after {modified_after_ts}")

        # Apply modified date filters
        if modified_after_ts:
            linear_datetime = self._linear_datetime_from_timestamp(modified_after_ts)
            if linear_datetime:
                linear_filter["updatedAt"] = {"gt": linear_datetime}

        if modified_before_ts:
            linear_datetime = self._linear_datetime_from_timestamp(modified_before_ts)
            if linear_datetime:
                if "updatedAt" in linear_filter:
                    linear_filter["updatedAt"]["lte"] = linear_datetime
                else:
                    linear_filter["updatedAt"] = {"lte": linear_datetime}

        # Apply created date filters
        if created_after_ts:
            linear_datetime = self._linear_datetime_from_timestamp(created_after_ts)
            if linear_datetime:
                linear_filter["createdAt"] = {"gte": linear_datetime}

        if created_before_ts:
            linear_datetime = self._linear_datetime_from_timestamp(created_before_ts)
            if linear_datetime:
                if "createdAt" in linear_filter:
                    linear_filter["createdAt"]["lte"] = linear_datetime
                else:
                    linear_filter["createdAt"] = {"lte": linear_datetime}

    # ==================== SYNC CHECKPOINTS ====================

    async def _get_team_sync_checkpoint(self, team_key: str) -> Optional[int]:
        """
        Get team-specific sync checkpoint (last_sync_time).

        """
        sync_point_key = f"team_{team_key}"
        data = await self.issues_sync_point.read_sync_point(sync_point_key)
        return data.get("last_sync_time") if data else None

    async def _update_team_sync_checkpoint(self, team_key: str, timestamp: Optional[int] = None) -> None:
        """
        Update team-specific sync checkpoint.

        """
        sync_point_key = f"team_{team_key}"
        sync_time = timestamp if timestamp is not None else get_epoch_timestamp_in_ms()

        await self.issues_sync_point.update_sync_point(
            sync_point_key,
            {"last_sync_time": sync_time}
        )
        self.logger.debug(f"💾 Updated sync checkpoint for team {team_key}: {sync_time}")

    async def _get_team_project_sync_checkpoint(self, team_key: str) -> Optional[int]:
        """
        Get team-specific project sync checkpoint (last_sync_time).
        Uses projects_sync_point with team-specific key.
        """
        sync_point_key = f"team_{team_key}_projects"
        data = await self.projects_sync_point.read_sync_point(sync_point_key)
        return data.get("last_sync_time") if data else None

    async def _update_team_project_sync_checkpoint(self, team_key: str, timestamp: Optional[int] = None) -> None:
        """
        Update team-specific project sync checkpoint.
        Uses projects_sync_point with team-specific key.
        """
        sync_point_key = f"team_{team_key}_projects"
        sync_time = timestamp if timestamp is not None else get_epoch_timestamp_in_ms()

        await self.projects_sync_point.update_sync_point(
            sync_point_key,
            {"last_sync_time": sync_time}
        )
        self.logger.debug(f"💾 Updated project sync checkpoint for team {team_key}: {sync_time}")

    async def _get_attachments_sync_checkpoint(self) -> Optional[int]:
        """Get the attachments sync checkpoint timestamp."""
        sync_point_key = "attachments_sync_point"
        data = await self.attachments_sync_point.read_sync_point(sync_point_key)
        return data.get("last_sync_time") if data else None

    async def _update_attachments_sync_checkpoint(self, timestamp: int) -> None:
        """Update the attachments sync checkpoint."""
        sync_point_key = "attachments_sync_point"
        await self.attachments_sync_point.update_sync_point(
            sync_point_key,
            {"last_sync_time": timestamp}
        )
        self.logger.debug(f"💾 Updated attachments sync point: {timestamp}")

    async def _get_documents_sync_checkpoint(self) -> Optional[int]:
        """Get the documents sync checkpoint timestamp."""
        sync_point_key = "documents_sync_point"
        data = await self.documents_sync_point.read_sync_point(sync_point_key)
        return data.get("last_sync_time") if data else None

    async def _update_documents_sync_checkpoint(self, timestamp: int) -> None:
        """Update the documents sync checkpoint."""
        sync_point_key = "documents_sync_point"
        await self.documents_sync_point.update_sync_point(
            sync_point_key,
            {"last_sync_time": timestamp}
        )
        self.logger.debug(f"💾 Updated documents sync point: {timestamp}")

    def _linear_datetime_from_timestamp(self, timestamp_ms: int) -> str:
        """
        Convert epoch timestamp (milliseconds) to Linear datetime format.

        Args:
            timestamp_ms: Epoch timestamp in milliseconds

        Returns:
            Linear datetime string in ISO 8601 format (e.g., "2024-01-15T10:30:00.000Z")
        """
        try:
            # Convert using UTC to avoid local timezone skew in incremental sync filters
            dt = datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc)
            return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        except (ValueError, OSError) as e:
            self.logger.warning(f"Failed to convert timestamp {timestamp_ms} to Linear datetime: {e}")
            return ""

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

    # ==================== CONTENT STREAMING HELPERS ====================

    async def _fetch_issue_content(self, issue_id: str) -> str:
        """Fetch full issue content for streaming.

        Args:
            issue_id: Linear issue ID

        Returns:
            Formatted markdown content for the issue
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        # Use DataSource to get issue details
        datasource = await self._get_fresh_datasource()
        response = await datasource.issue(id=issue_id)

        if not response.success:
            raise Exception(f"Failed to fetch issue content: {response.message}")

        issue_data = response.data.get("issue", {}) if response.data else {}
        if not issue_data:
            raise Exception(f"No issue data found for ID: {issue_id}")

        # Return only the description field
        description = issue_data.get("description", "")
        return description if description else ""

    async def _fetch_comment_content(self, comment_id: str) -> str:
        """Fetch comment content for streaming.

        Args:
            comment_id: Linear comment ID

        Returns:
            Formatted markdown content for the comment
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        # Use DataSource to get comment details
        datasource = await self._get_fresh_datasource()
        response = await datasource.comment(id=comment_id)

        if not response.success:
            raise Exception(f"Failed to fetch comment content: {response.message}")

        comment_data = response.data.get("comment", {}) if response.data else {}
        if not comment_data:
            raise Exception(f"No comment data found for ID: {comment_id}")

        # Return only the comment body
        body = comment_data.get("body", "")
        return body if body else ""

    async def _fetch_document_content(self, document_id: str) -> str:
        """Fetch document content for streaming.

        Args:
            document_id: Linear document ID

        Returns:
            Document content (markdown format)
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        # Use DataSource to get document details
        datasource = await self._get_fresh_datasource()

        # Query document by ID using filter
        response = await datasource.documents(
            first=1,
            filter={"id": {"eq": document_id}}
        )

        if not response.success:
            raise Exception(f"Failed to fetch document content: {response.message}")

        documents_data = response.data.get("documents", {}) if response.data else {}
        documents_list = documents_data.get("nodes", [])

        if not documents_list:
            raise Exception(f"No document data found for ID: {document_id}")

        document_data = documents_list[0]

        # Return the content field (markdown)
        content = document_data.get("content", "")
        return content if content else ""


    # ==================== ABSTRACT METHODS ====================
    async def stream_record(self, record: Record) -> StreamingResponse:
        """
        Stream record content (issue, comment, attachment, document, or file).

        Handles:
        - TICKET: Stream issue description (markdown)
        - COMMENT: Stream comment body (markdown)
        - LINK: Stream attachment/link from weburl
        - WEBPAGE: Stream document content (markdown)
        - FILE: Stream file content from weburl
        """
        try:
            if not self.data_source:
                await self.init()

            if record.record_type == RecordType.TICKET:
                # Stream issue content (markdown format)
                issue_id = record.external_record_id
                content = await self._fetch_issue_content(issue_id)

                return StreamingResponse(
                    iter([content.encode('utf-8')]),
                    media_type=MimeTypes.MARKDOWN.value,
                    headers={
                        "Content-Disposition": f'inline; filename="{record.external_record_id}.md"'
                    }
                )

            elif record.record_type == RecordType.COMMENT:
                # Stream comment content (markdown format)
                comment_id = record.external_record_id.replace("comment_", "")
                content = await self._fetch_comment_content(comment_id)

                return StreamingResponse(
                    iter([content.encode('utf-8')]),
                    media_type=MimeTypes.MARKDOWN.value,
                    headers={
                        "Content-Disposition": f'inline; filename="{record.external_record_id}.md"'
                    }
                )

            elif record.record_type == RecordType.LINK:
                # Stream attachment/link as markdown (clickable link format)
                if not record.weburl:
                    raise ValueError(f"LinkRecord {record.external_record_id} missing weburl")

                # Return simple markdown link format (same as issue/comment descriptions)
                link_name = record.record_name or 'Link'
                markdown_content = f"# {link_name}\n\n[{record.weburl}]({record.weburl})"

                return StreamingResponse(
                    iter([markdown_content.encode('utf-8')]),
                    media_type=MimeTypes.MARKDOWN.value,
                    headers={
                        "Content-Disposition": f'inline; filename="{record.external_record_id}.md"'
                    }
                )

            elif record.record_type == RecordType.WEBPAGE:
                # Stream document content (markdown format)
                document_id = record.external_record_id
                content = await self._fetch_document_content(document_id)

                return StreamingResponse(
                    iter([content.encode('utf-8')]),
                    media_type=MimeTypes.MARKDOWN.value,
                    headers={
                        "Content-Disposition": f'inline; filename="{record.external_record_id}.md"'
                    }
                )

            elif record.record_type == RecordType.FILE:
                # Stream file content from weburl
                if not record.weburl:
                    raise ValueError(f"FileRecord {record.external_record_id} missing weburl")

                # Download file content and stream it with authentication
                async def file_stream() -> AsyncGenerator[bytes, None]:
                    if not self.data_source:
                        await self.init()

                    datasource = await self._get_fresh_datasource()
                    try:
                        async for chunk in datasource.download_file(record.weburl):
                            yield chunk
                    except Exception as e:
                        self.logger.error(f"❌ Error downloading file from {record.weburl}: {e}")
                        raise

                # Determine filename from record_name or weburl
                filename = record.record_name or record.external_record_id
                # Safely access extension attribute (only exists on FileRecord)
                extension = getattr(record, 'extension', None)
                if extension:
                    filename = f"{filename}.{extension}" if not filename.endswith(f".{extension}") else filename

                # Use record's mime_type if available, otherwise detect from extension
                media_type = record.mime_type if record.mime_type else MimeTypes.UNKNOWN.value

                return StreamingResponse(
                    file_stream(),
                    media_type=media_type,
                    headers={
                        "Content-Disposition": f'attachment; filename="{filename}"'
                    }
                )

            else:
                raise ValueError(f"Unsupported record type for streaming: {record.record_type}")

        except Exception as e:
            self.logger.error(f"❌ Error streaming record {record.external_record_id} ({record.record_type}): {e}", exc_info=True)
            raise

    async def run_incremental_sync(self) -> None:
        """
        Incremental sync - calls run_sync which handles incremental logic.
        """
        self.logger.info(f"🔄 Starting Linear incremental sync for connector {self.connector_id}")
        await self.run_sync()
        self.logger.info("✅ Linear incremental sync completed")

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
