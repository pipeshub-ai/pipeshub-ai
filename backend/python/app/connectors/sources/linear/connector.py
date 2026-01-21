"""Linear Connector Implementation"""
import base64
import re
from collections import defaultdict
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
from app.config.constants.arangodb import (
    AppGroups,
    Connectors,
    LinkRelationshipTag,
    RecordRelations,
)
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
    OAuthScopeConfig,
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
from app.connectors.utils.value_mapper import ValueMapper
from app.models.blocks import (
    Block,
    BlockComment,
    BlockContainerIndex,
    BlockGroup,
    BlocksContainer,
    ChildRecord,
    ChildType,
    CommentAttachment,
    DataFormat,
    GroupSubType,
    GroupType,
    TableRowMetadata,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    IndexingStatus,
    ItemType,
    LinkPublicStatus,
    LinkRecord,
    MimeTypes,
    OriginTypes,
    ProjectRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    RelatedExternalRecord,
    Status,
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
    .with_description("Sync issues, comments, and projects from Linear")\
    .with_categories(["Issue Tracking", "Project Management"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Linear",
            authorize_url="https://linear.app/oauth/authorize",
            token_url="https://api.linear.app/oauth/token",
            redirect_uri="connectors/oauth/callback/Linear",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=["read"],
                agent=["read","write","admin"]
            ),
            fields=[
                AuthField(
                    name="clientId",
                    display_name="Client ID",
                    placeholder="Enter your Linear OAuth Client ID",
                    description="OAuth Client ID from Linear (https://linear.app/settings/api)",
                    field_type="TEXT",
                    max_length=2000
                ),
                AuthField(
                    name="clientSecret",
                    display_name="Client Secret",
                    placeholder="Enter your Linear OAuth Client Secret",
                    description="OAuth Client Secret from Linear",
                    field_type="PASSWORD",
                    max_length=2000,
                    is_secret=True
                )
            ],
            documentation_links=[
                DocumentationLink(
                    "Linear OAuth Setup",
                    "https://linear.app/developers/oauth-2-0-authentication",
                    "setup"
                )
            ]
        )
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/linear.svg")
        .with_realtime_support(False)
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/linear/linear',
            'pipeshub'
        ))
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
            display_name="Index Issue and Comment Files",
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

        # Initialize value mapper (all mappings are now in defaults)
        self.value_mapper = ValueMapper()

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

        self.deletion_sync_point = SyncPoint(
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
        Get LinearDataSource with ALWAYS-FRESH access token.

        This method:
        1. Fetches current token from config (supports both API_TOKEN and OAUTH)
        2. Compares with existing client's token
        3. Updates client ONLY if token changed (mutation)
        4. Returns datasource with current token

        Returns:
            LinearDataSource with current valid token
        """
        if not self.external_client:
            raise Exception("Linear client not initialized. Call init() first.")

        # Fetch current config from etcd (async I/O)
        config = await self.config_service.get_config(f"/services/connectors/{self.connector_id}/config")

        if not config:
            raise Exception("Linear configuration not found")

        # Extract fresh token (supports both API_TOKEN and OAUTH)
        auth_config = config.get("auth", {})
        auth_type = auth_config.get("authType", "API_TOKEN")

        if auth_type == "OAUTH":
            credentials_config = config.get("credentials", {}) or {}
            fresh_token = credentials_config.get("access_token", "")
        else:
            # API_TOKEN
            fresh_token = auth_config.get("apiToken", "")

        if not fresh_token:
            raise Exception("No access token available")

        # Get current token from client
        internal_client = self.external_client.get_client()
        current_token = internal_client.get_token()

        # Update client's token if it changed (mutation)
        if current_token != fresh_token:
            self.logger.debug("🔄 Updating client with refreshed access token")
            internal_client.set_token(fresh_token)

        # Return datasource with updated client
        return LinearDataSource(self.external_client)

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
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
        limit: int = 20,
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
                after=cursor,
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
        Syncs users, teams, issues, attachments, documents, projects from Linear.
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
            team_ids = None
            team_ids_operator = None
            team_ids_filter = self.sync_filters.get("team_ids") if self.sync_filters else None

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

            # Step 11: Sync deleted issues
            await self._sync_deleted_issues(team_record_groups)

            # Step 12: Sync deleted projects
            await self._sync_deleted_projects(team_record_groups)

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

        # Build filter if specific team IDs are requested (build once before pagination loop)
        filter_dict: Optional[Dict[str, Any]] = None
        if team_ids:
            # Check operator: "in" (include) or "not_in" (exclude)
            if team_ids_operator:
                operator_value = team_ids_operator.value if hasattr(team_ids_operator, 'value') else str(team_ids_operator)
            else:
                operator_value = "in"

            is_exclude = operator_value == "not_in"

            if is_exclude:
                # Linear TeamFilter supports "nin" for not in
                filter_dict = {"id": {"nin": team_ids}}
            else:
                # Linear TeamFilter supports "in" for include
                filter_dict = {"id": {"in": team_ids}}

        # Fetch all teams with cursor-based pagination
        # Note: filter_dict is sent on every request - Linear applies filter first, then paginates
        while True:
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

                # Skip teams without external_group_id (shouldn't happen, but defensive check)
                if not team_id:
                    self.logger.warning(f"⚠️ Skipping team {team_key}: missing external_group_id")
                    continue

                self.logger.info(f"📋 Starting issue sync for team: {team_key}")

                # Read team-level sync point
                last_sync_time = await self._get_team_sync_checkpoint(team_key)

                if last_sync_time:
                    self.logger.info(f"🔄 Incremental sync for team {team_key} from {last_sync_time}")

                # Fetch and process issues for this team
                total_records_processed = 0
                total_tickets = 0
                total_files = 0
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
                        elif isinstance(record, FileRecord):
                            total_files += 1

                    # Process batch
                    total_records_processed += len(batch_records)
                    await self.data_entities_processor.on_new_records(batch_records)

                    # Update sync point after each batch for fault tolerance
                    if max_issue_updated_at:
                        await self._update_team_sync_checkpoint(team_key, max_issue_updated_at)

                # Log final status
                if total_records_processed > 0:
                    parts = []
                    if total_tickets > 0:
                        parts.append(f"{total_tickets} tickets")
                    if total_files > 0:
                        parts.append(f"{total_files} files")
                    summary = ", ".join(parts) if parts else "0 records"
                    self.logger.info(f"✅ Team {team_key}: {summary} ({total_records_processed} total)")
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
                        batch_records.append((ticket_record, []))

                        # Note: Comments are handled as blocks during streaming, not as separate records

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
                                parent_weburl=ticket_record.weburl,
                                exclude_images=True
                            )
                            batch_records.extend(file_records)

                        # Extract files from comment bodies and create FileRecords
                        comments_data = issue_data.get("comments", {}).get("nodes", [])
                        if comments_data:
                            for comment_data in comments_data:
                                comment_id = comment_data.get("id", "")
                                comment_body = comment_data.get("body", "")

                                if not comment_id or not comment_body:
                                    continue

                                # Get comment timestamps for file records
                                comment_created_at = self._parse_linear_datetime(comment_data.get("createdAt", "")) or 0
                                comment_updated_at = self._parse_linear_datetime(comment_data.get("updatedAt", "")) or 0
                                comment_url = comment_data.get("url", ticket_record.weburl)

                                comment_file_records = await self._extract_files_from_markdown(
                                    markdown_text=comment_body,
                                    parent_external_id=issue_id,
                                    parent_node_id=ticket_record.id,
                                    parent_record_type=RecordType.TICKET,
                                    team_id=team_id,
                                    tx_store=tx_store,
                                    parent_created_at=comment_created_at,
                                    parent_updated_at=comment_updated_at,
                                    parent_weburl=comment_url,
                                    exclude_images=True,
                                    indexing_filter_key=IndexingFilterKey.FILES
                                )
                                batch_records.extend(comment_file_records)

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

                            # Skip documents without an issue (standalone or project-attached documents)
                            # Track updatedAt to avoid refetching in next sync
                            if not issue_data:
                                document_updated_at = self._parse_linear_datetime(document_data.get("updatedAt", "")) or 0
                                if document_updated_at:
                                    if max_document_updated_at is None or document_updated_at > max_document_updated_at:
                                        max_document_updated_at = document_updated_at
                                # Check if it's a project document or standalone
                                project_data = document_data.get("project")
                                if project_data:
                                    self.logger.debug(f"⚠️ Skipping document {document_id}: project-attached (synced via project sync)")
                                else:
                                    self.logger.debug(f"⚠️ Skipping document {document_id}: no parent issue or project (standalone)")
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

                # Skip teams without external_group_id (shouldn't happen, but defensive check)
                if not team_id:
                    self.logger.warning(f"⚠️ Skipping team {team_key}: missing external_group_id")
                    continue

                self.logger.info(f"📋 Starting project sync for team: {team_key}")

                # Read team-level sync point
                last_sync_time = await self._get_team_project_sync_checkpoint(team_key)

                if last_sync_time:
                    self.logger.info(f"🔄 Incremental project sync for team {team_key} from {last_sync_time}")

                # Fetch and process projects for this team
                total_records_processed = 0
                total_projects = 0
                # Track max updatedAt ONLY from projects (ProjectRecords)
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
                        if isinstance(record, ProjectRecord):
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
        batch_size: int = 10
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

                        # Fetch full project details with nested data for blocks
                        datasource = await self._get_fresh_datasource()
                        full_project_response = await datasource.project(project_id)

                        if not full_project_response.success:
                            self.logger.warning(f"⚠️ Failed to fetch full project details for {project_id}: {full_project_response.message}")
                            full_project_data = project_data
                        else:
                            full_project_data = full_project_response.data.get("project", {}) if full_project_response.data else project_data

                        # Look up existing record to handle versioning
                        existing_record = await tx_store.get_record_by_external_id(
                            connector_id=self.connector_id,
                            external_id=project_id
                        )

                        # Transform project to ProjectRecord FIRST (without BlocksContainer - created only during streaming)
                        project_record = self._transform_to_project_record(
                            full_project_data, team_id, existing_record
                        )

                        # Process related records (links, documents) using project_record.id as parent
                        project_batch_records = await self._prepare_project_related_records(
                            full_project_data=full_project_data,
                            project_id=project_id,
                            existing_record=project_record,
                            team_id=team_id,
                            tx_store=tx_store
                        )

                        # Add project-related records to batch
                        batch_records.extend(project_batch_records)

                        # Set indexing status based on filters
                        if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.PROJECTS):
                            project_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                        # Extract issues data directly from full_project_data and add to related_external_records
                        issues_data = full_project_data.get("issues", {}).get("nodes", [])
                        if issues_data:
                            from app.models.entities import RelatedExternalRecord
                            project_record.related_external_records = [
                                RelatedExternalRecord(
                                    external_record_id=issue.get("id"),
                                    record_type=RecordType.TICKET,
                                    relation_type=RecordRelations.LINKED_TO
                                )
                                for issue in issues_data
                                if issue.get("id")
                            ]

                        # Extract files (not images) from project description and create FileRecords
                        project_content = full_project_data.get("content", "")
                        if project_content:
                            # Extract files from project content (excluding images)
                            file_records = await self._extract_files_from_markdown(
                                markdown_text=project_content,
                                parent_external_id=project_id,
                                parent_node_id=project_record.id,
                                parent_record_type=RecordType.PROJECT,
                                team_id=team_id,
                                tx_store=tx_store,
                                parent_created_at=project_record.source_created_at,
                                parent_updated_at=project_record.source_updated_at,
                                parent_weburl=project_record.weburl,
                                exclude_images=True,
                                indexing_filter_key=IndexingFilterKey.PROJECTS
                            )
                            # Add file records to batch
                            batch_records.extend(file_records)

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

    async def _prepare_project_related_records(
        self,
        full_project_data: Dict[str, Any],
        project_id: str,
        existing_record: Optional[Record],
        team_id: str,
        tx_store
    ) -> List[Tuple[Record, List[Permission]]]:
        """
        Prepare project-related records (links, documents) for sync.

        This method processes external links and documents without creating BlockGroups
        (BlockGroups are only created during streaming, not during sync).

        Args:
            full_project_data: Full project data from Linear API
            project_id: Project external ID
            existing_record: Existing project record (if any)
            team_id: Team ID for external_record_group_id
            tx_store: Transaction store for looking up existing records

        Returns:
            List of (Record, permissions) tuples
        """
        project_batch_records: List[Tuple[Record, List[Permission]]] = []

        # Get project node ID (use existing record ID if available, otherwise will be set after record creation)
        project_node_id = existing_record.id if existing_record else ""

        # Extract external links data
        external_links_data = full_project_data.get("externalLinks", {}).get("nodes", [])
        if external_links_data:
            # Process external links (without creating block groups for sync)
            link_records, _ = await self._process_project_external_links(
                external_links_data=external_links_data,
                project_id=project_id,
                project_node_id=project_node_id,
                team_id=team_id,
                tx_store=tx_store,
                create_block_groups=False
            )
            project_batch_records.extend(link_records)

        # Extract documents data
        documents_data = full_project_data.get("documents", {}).get("nodes", [])
        if documents_data:
            # Process documents (without creating block groups for sync)
            document_records, _ = await self._process_project_documents(
                documents_data=documents_data,
                project_id=project_id,
                project_node_id=project_node_id,
                team_id=team_id,
                tx_store=tx_store,
                create_block_groups=False  # No block groups during sync
            )
            project_batch_records.extend(document_records)

        return project_batch_records

    async def _process_project_external_links(
        self,
        external_links_data: List[Dict[str, Any]],
        project_id: str,
        project_node_id: str,
        team_id: str,
        tx_store,
        create_block_groups: bool = False
    ) -> Tuple[List[Tuple[Record, List[Permission]]], List[BlockGroup]]:
        """
        Process project external links and create LinkRecords. Optionally create BlockGroups.

        Args:
            external_links_data: List of external link data from Linear API
            project_id: Project external ID
            project_node_id: Internal record ID of project
            team_id: Team ID for external_record_group_id
            tx_store: Transaction store for looking up existing records
            create_block_groups: If True, also create BlockGroups (for streaming). Default False (for sync).

        Returns:
            Tuple of (List of (LinkRecord, permissions) tuples, List of BlockGroups)
        """
        link_records: List[Tuple[Record, List[Permission]]] = []
        block_groups: List[BlockGroup] = []

        for link_data in external_links_data:
            try:
                link_id = link_data.get("id", "")
                if not link_id:
                    continue

                # Check if link already exists
                existing_link = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=link_id
                )

                # Transform external link to LinkRecord (reuse attachment transform function)
                link_record = self._transform_attachment_to_link_record(
                    attachment_data=link_data,
                    issue_id=project_id,
                    parent_node_id=project_node_id,
                    team_id=team_id,
                    existing_record=existing_link,
                    parent_record_type=RecordType.PROJECT
                )

                # Set indexing status based on FILES filter (external links are treated as files)
                if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.PROJECTS):
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
                            self.logger.debug(f"🔗 Found related record {related_record.id} for project link URL: {link_record.weburl}")
                    except Exception as e:
                        self.logger.debug(f"⚠️ Could not fetch related record for URL {link_record.weburl}: {e}")

                link_records.append((link_record, []))

                # Create BlockGroup only if requested (for streaming)
                if create_block_groups:
                    child_record = ChildRecord(
                        child_type=ChildType.RECORD,
                        child_id=link_record.id,
                        child_name=link_record.record_name
                    )

                    block_group = BlockGroup(
                        id=str(uuid4()),
                        index=len(block_groups),
                        name=link_record.record_name,
                        type=GroupType.TEXT_SECTION,
                        sub_type=GroupSubType.CHILD_RECORD,
                        description=f"External Link: {link_record.record_name}",
                        source_group_id=link_id,
                        weburl=link_record.weburl,
                        requires_processing=False,  # No data to process, just a child record reference
                        table_row_metadata=TableRowMetadata(
                            children_records=[child_record]
                        )
                    )
                    block_groups.append(block_group)

            except Exception as e:
                link_id = link_data.get("id", "unknown")
                self.logger.error(f"❌ Error processing project external link {link_id}: {e}", exc_info=True)
                continue

        return link_records, block_groups

    async def _process_project_documents(
        self,
        documents_data: List[Dict[str, Any]],
        project_id: str,
        project_node_id: str,
        team_id: str,
        tx_store,
        create_block_groups: bool = False
    ) -> Tuple[List[Tuple[Record, List[Permission]]], List[BlockGroup]]:
        """
        Process project documents and create WebpageRecords. Optionally create BlockGroups.

        Args:
            documents_data: List of document data from Linear API
            project_id: Project external ID
            project_node_id: Internal record ID of project
            team_id: Team ID for external_record_group_id
            tx_store: Transaction store for looking up existing records
            create_block_groups: If True, also create BlockGroups (for streaming). Default False (for sync).

        Returns:
            Tuple of (List of (WebpageRecord, permissions) tuples, List of BlockGroups)
        """
        document_records: List[Tuple[Record, List[Permission]]] = []
        block_groups: List[BlockGroup] = []

        for document_data in documents_data:
            try:
                document_id = document_data.get("id", "")
                if not document_id:
                    continue

                # Check if document already exists
                existing_document = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=document_id
                )

                # Transform document to WebpageRecord (reuse existing transform function)
                webpage_record = self._transform_document_to_webpage_record(
                    document_data=document_data,
                    issue_id=project_id,
                    parent_node_id=project_node_id,
                    team_id=team_id,
                    existing_record=existing_document,
                    parent_record_type=RecordType.PROJECT
                )

                # Set indexing status based on PROJECTS filter (documents are part of project content)
                if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.PROJECTS):
                    webpage_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                document_records.append((webpage_record, []))

                # Create BlockGroup only if requested (for streaming)
                if create_block_groups:
                    child_record = ChildRecord(
                        child_type=ChildType.RECORD,
                        child_id=webpage_record.id,
                        child_name=webpage_record.record_name
                    )

                    block_group = BlockGroup(
                        id=str(uuid4()),
                        index=len(block_groups),
                        name=webpage_record.record_name,
                        type=GroupType.TEXT_SECTION,
                        sub_type=GroupSubType.CHILD_RECORD,
                        description=f"Document: {webpage_record.record_name}",
                        source_group_id=document_id,
                        weburl=webpage_record.weburl,
                        requires_processing=False,  # No data to process, just a child record reference
                        table_row_metadata=TableRowMetadata(
                            children_records=[child_record]
                        )
                    )
                    block_groups.append(block_group)

            except Exception as e:
                document_id = document_data.get("id", "unknown")
                self.logger.error(f"❌ Error processing project document {document_id}: {e}", exc_info=True)
                continue

        return document_records, block_groups

    async def _process_issue_attachments(
        self,
        attachments_data: List[Dict[str, Any]],
        issue_id: str,
        issue_node_id: str,
        team_id: str,
        tx_store,
    ) -> List[ChildRecord]:
        """
        Process issue attachments and create ChildRecords for TableRowMetadata.
        Creates LinkRecords if they don't exist (like projects do).

        Args:
            attachments_data: List of attachment data from Linear API
            issue_id: Issue external ID
            issue_node_id: Internal record ID of issue
            team_id: Team ID for external_record_group_id
            tx_store: Transaction store for looking up existing records

        Returns:
            List of ChildRecord objects for attachments
        """
        child_records: List[ChildRecord] = []

        for attachment_data in attachments_data:
            try:
                attachment_id = attachment_data.get("id", "")
                if not attachment_id:
                    continue

                # Look up existing attachment record from database
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=attachment_id
                )

                # Create record if it doesn't exist (like projects do)
                if not existing_record:
                    link_record = self._transform_attachment_to_link_record(
                        attachment_data=attachment_data,
                        issue_id=issue_id,
                        parent_node_id=issue_node_id,
                        team_id=team_id,
                        existing_record=None,
                        parent_record_type=RecordType.TICKET
                    )

                    # Set indexing status based on filters
                    if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.ISSUE_ATTACHMENTS):
                        link_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                    # Save the record
                    await self.data_entities_processor.on_new_records([(link_record, [])])
                    existing_record = link_record

                if existing_record:
                    child_records.append(ChildRecord(
                        child_type=ChildType.RECORD,
                        child_id=existing_record.id,
                        child_name=existing_record.record_name
                    ))

            except Exception as e:
                attachment_id = attachment_data.get("id", "unknown")
                self.logger.error(f"❌ Error processing issue attachment {attachment_id} for children_records: {e}", exc_info=True)
                continue

        return child_records

    async def _process_issue_documents(
        self,
        documents_data: List[Dict[str, Any]],
        issue_id: str,
        issue_node_id: str,
        team_id: str,
        tx_store,
    ) -> List[ChildRecord]:
        """
        Process issue documents and create ChildRecords for TableRowMetadata.
        Creates WebpageRecords if they don't exist (like projects do).

        Args:
            documents_data: List of document data from Linear API
            issue_id: Issue external ID
            issue_node_id: Internal record ID of issue
            team_id: Team ID for external_record_group_id
            tx_store: Transaction store for looking up existing records

        Returns:
            List of ChildRecord objects for documents
        """
        child_records: List[ChildRecord] = []

        for document_data in documents_data:
            try:
                document_id = document_data.get("id", "")
                if not document_id:
                    continue

                # Look up existing document record from database
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=document_id
                )

                # Create record if it doesn't exist (like projects do)
                if not existing_record:
                    webpage_record = self._transform_document_to_webpage_record(
                        document_data=document_data,
                        issue_id=issue_id,
                        parent_node_id=issue_node_id,
                        team_id=team_id,
                        existing_record=None,
                        parent_record_type=RecordType.TICKET
                    )

                    # Set indexing status based on filters
                    if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.DOCUMENTS):
                        webpage_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                    # Save the record
                    await self.data_entities_processor.on_new_records([(webpage_record, [])])
                    existing_record = webpage_record

                if existing_record:
                    child_records.append(ChildRecord(
                        child_type=ChildType.RECORD,
                        child_id=existing_record.id,
                        child_name=existing_record.record_name
                    ))

            except Exception as e:
                document_id = document_data.get("id", "unknown")
                self.logger.error(f"❌ Error processing issue document {document_id} for children_records: {e}", exc_info=True)
                continue

        return child_records

    async def _process_content_files_for_children(
        self,
        content: str,
        parent_id: str,
        tx_store,
    ) -> List[ChildRecord]:
        """
        Process files extracted from content (description/body) and create ChildRecords.
        Fetches already-synced FileRecords from database (excluding images).

        This method is reusable for both issues and projects.

        Args:
            content: Markdown content to extract files from
            parent_id: Parent record external ID (for logging)
            tx_store: Transaction store for looking up existing records

        Returns:
            List of ChildRecord objects for files (excluding images)
        """
        child_records: List[ChildRecord] = []

        if not content:
            return child_records

        # Extract file URLs from content (excluding images)
        file_urls = self._extract_file_urls_from_markdown(content, exclude_images=True)

        for file_info in file_urls:
            try:
                file_url = file_info["url"]

                # Look up existing file record by URL (external_record_id = file_url)
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=file_url
                )

                if existing_record:
                    child_records.append(ChildRecord(
                        child_type=ChildType.RECORD,
                        child_id=existing_record.id,
                        child_name=existing_record.record_name
                    ))

            except Exception as e:
                self.logger.error(f"❌ Error processing file {file_info.get('url', 'unknown')} for children_records: {e}", exc_info=True)
                continue

        return child_records

    async def _process_comment_files_for_children(
        self,
        comments_data: List[Dict[str, Any]],
        issue_id: str,
        issue_node_id: str,
        team_id: str,
        issue_weburl: Optional[str],
        tx_store,
    ) -> Dict[str, List[ChildRecord]]:
        """
        Process files from comment bodies and return a map of comment_id -> ChildRecords.
        Fetches already-synced FileRecords from database, or creates them if they don't exist (excluding images).
        Uses the same indexing keys as issue files.

        Args:
            comments_data: List of comment data from Linear API
            issue_id: Issue external ID
            issue_node_id: Internal record ID of issue
            team_id: Team ID for external_record_group_id
            issue_weburl: Issue web URL (for file weburl)
            tx_store: Transaction store for looking up existing records

        Returns:
            Dictionary mapping comment_id to List[ChildRecord] for files in that comment
        """
        comment_file_children_map: Dict[str, List[ChildRecord]] = {}

        if not comments_data:
            return comment_file_children_map

        for comment_data in comments_data:
            comment_id = comment_data.get("id", "")
            comment_body = comment_data.get("body", "")

            if not comment_id or not comment_body:
                continue

            # Get comment timestamps for file records
            comment_created_at = self._parse_linear_datetime(comment_data.get("createdAt", "")) or 0
            comment_updated_at = self._parse_linear_datetime(comment_data.get("updatedAt", "")) or 0
            comment_url = comment_data.get("url", issue_weburl)

            # Extract files from comment body and create FileRecords if they don't exist
            file_records = await self._extract_files_from_markdown(
                markdown_text=comment_body,
                parent_external_id=issue_id,
                parent_node_id=issue_node_id,
                parent_record_type=RecordType.TICKET,
                team_id=team_id,
                tx_store=tx_store,
                parent_created_at=comment_created_at,
                parent_updated_at=comment_updated_at,
                parent_weburl=comment_url,
                exclude_images=True,
                indexing_filter_key=IndexingFilterKey.FILES
            )

            # Save FileRecords if any were created (new files added after sync)
            if file_records:
                await self.data_entities_processor.on_new_records(file_records)

            # Create ChildRecords for the files
            child_records: List[ChildRecord] = []
            for file_record, _ in file_records:
                child_records.append(ChildRecord(
                    child_type=ChildType.RECORD,
                    child_id=file_record.id,
                    child_name=file_record.record_name
                ))

            if child_records:
                comment_file_children_map[comment_id] = child_records

        return comment_file_children_map

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
        parent_weburl: Optional[str] = None,
        exclude_images: bool = False,
        indexing_filter_key: Optional[IndexingFilterKey] = IndexingFilterKey.FILES
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
            exclude_images: If True, exclude image patterns and only extract file links
            indexing_filter_key: Indexing filter key to use (defaults to FILES)

        Returns:
            List of (FileRecord, permissions) tuples
        """
        file_records: List[Tuple[Record, List[Permission]]] = []

        if not markdown_text:
            return file_records

        # Extract file URLs from markdown (excluding images if requested)
        file_urls = self._extract_file_urls_from_markdown(markdown_text, exclude_images=exclude_images)

        if not file_urls:
            return file_records

        for file_info in file_urls:
            try:
                file_url = file_info["url"]
                filename = file_info["filename"]

                # Use full file URL as external_record_id (for streaming)
                # Look up existing file record by full URL
                existing_file = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=file_url
                )

                # Transform to FileRecord
                file_record = await self._transform_file_url_to_file_record(
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

                # Set indexing status based on filters (use provided filter key or default to FILES)
                filter_key = indexing_filter_key or IndexingFilterKey.FILES
                if self.indexing_filters and not self.indexing_filters.is_enabled(filter_key):
                    file_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                # Files inherit permissions from parent, so pass empty list
                file_records.append((file_record, []))

            except Exception as e:
                self.logger.error(f"❌ Error processing file {file_info.get('url', 'unknown')}: {e}", exc_info=True)
                continue

        return file_records

    def _extract_file_urls_from_markdown(self, markdown_text: str, exclude_images: bool = False) -> List[Dict[str, str]]:
        """
        Extract file URLs from markdown text (images and file links).
        Only extracts URLs from Linear uploads (uploads.linear.app).

        Args:
            markdown_text: Markdown content to extract from
            exclude_images: If True, only extract file links (skip URLs that appear in image patterns)

        Returns:
            List of dicts with 'url', 'filename', 'alt_text' keys
        """
        if not markdown_text:
            return []

        file_urls: List[Dict[str, str]] = []
        seen_urls: Set[str] = set()

        # If excluding images, collect image URLs first to skip them in links
        image_urls = set()
        if exclude_images:
            image_pattern = r'!\[([^\]]*)\]\(([^)]+)\)'  # ![alt](url)
            for match in re.finditer(image_pattern, markdown_text):
                url = match.group(2).strip()
                if "uploads.linear.app" in url:
                    image_urls.add(url)

        # Extract images (only if not excluding)
        if not exclude_images:
            image_pattern = r'!\[([^\]]*)\]\(([^)]+)\)'  # ![alt](url)
            for match in re.finditer(image_pattern, markdown_text):
                url = match.group(2).strip()
                if "uploads.linear.app" in url and url not in seen_urls:
                    seen_urls.add(url)
                    alt_text = match.group(1) or ""
                    filename = alt_text or url.split("/")[-1].split("?")[0]
                    file_urls.append({"url": url, "filename": filename, "alt_text": alt_text})

        # Extract file links (skip image URLs if excluding images)
        link_pattern = r'\[([^\]]+)\]\(([^)]+)\)'  # [text](url)
        for match in re.finditer(link_pattern, markdown_text):
            url = match.group(2).strip()
            if "uploads.linear.app" in url and url not in seen_urls:
                # Skip if excluding images and this URL appears in an image pattern
                if exclude_images and url in image_urls:
                    continue

                seen_urls.add(url)
                link_text = match.group(1) or ""
                filename = link_text or url.split("?")[0].split("/")[-1]
                file_urls.append({"url": url, "filename": filename, "alt_text": link_text})

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

    async def _convert_images_to_base64_in_markdown(self, markdown_text: str) -> str:
        """
        Convert all images in markdown text to base64 data URIs.

        Finds all image patterns like ![alt](url) and replaces them with
        base64-encoded data URIs: ![alt](data:image/png;base64,...)

        Only processes images from Linear uploads (uploads.linear.app).

        Args:
            markdown_text: Markdown content that may contain images

        Returns:
            Markdown text with images converted to base64 data URIs
        """
        if not markdown_text:
            return markdown_text

        # Pattern to match markdown images: ![alt](url)
        image_pattern = r'!\[([^\]]*)\]\(([^)]+)\)'

        # Find all image matches
        matches = list(re.finditer(image_pattern, markdown_text))

        if not matches:
            return markdown_text  # No images found

        # Initialize datasource if needed
        if not self.data_source:
            await self.init()

        datasource = await self._get_fresh_datasource()

        # Process all images and build replacements
        replacements = {}
        for match in matches:
            alt_text = match.group(1) or ""
            image_url = match.group(2).strip()

            # Only process Linear upload URLs
            if "uploads.linear.app" not in image_url:
                continue  # Skip non-Linear images

            try:
                # Download image content
                image_bytes = b""
                async for chunk in datasource.download_file(image_url):
                    image_bytes += chunk

                if not image_bytes:
                    self.logger.debug(f"⚠️ Empty image content from {image_url}")
                    continue  # Skip empty images

                # Determine image type from URL
                image_type = "png"  # default
                url_lower = image_url.lower()
                if ".jpg" in url_lower or ".jpeg" in url_lower:
                    image_type = "jpeg"
                elif ".gif" in url_lower:
                    image_type = "gif"
                elif ".webp" in url_lower:
                    image_type = "webp"
                elif ".svg" in url_lower:
                    image_type = "svg"
                elif ".png" in url_lower:
                    image_type = "png"

                # Convert to base64
                base64_encoded = base64.b64encode(image_bytes).decode('utf-8')

                # Create data URI
                if image_type == "svg":
                    data_uri = f"data:image/svg+xml;base64,{base64_encoded}"
                else:
                    data_uri = f"data:image/{image_type};base64,{base64_encoded}"

                # Store replacement
                original = match.group(0)
                replacement = f"![{alt_text}]({data_uri})"
                replacements[original] = replacement

            except Exception as e:
                self.logger.debug(f"⚠️ Failed to convert image {image_url} to base64: {e}")
                continue  # Skip on error

        # Apply replacements (process in reverse order to preserve string indices)
        result = markdown_text
        for match in reversed(matches):
            original = match.group(0)
            if original in replacements:
                start, end = match.span()
                result = result[:start] + replacements[original] + result[end:]

        return result

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

        if identifier and title:
            record_name = title
        elif identifier:
            record_name = identifier
        else:
            self.logger.warning(f"Issue {issue_id} missing both identifier and title, using ID as record name")
            record_name = issue_id

        # Ensure team_id is not None or empty
        if not team_id:
            self.logger.error(f"Issue {issue_id} has no team_id, cannot create record without team association")
            raise ValueError(f"team_id is required but was {team_id}")

        # Priority mapping
        priority_num = issue_data.get("priority")
        if priority_num is None:
            priority_str = None
        elif priority_num == 0:
            priority_str = "none"  # Linear priority 0 = no priority
        else:
            priority_map = {1: "Urgent", 2: "High", 3: "Medium", 4: "Low"}
            priority_str = priority_map.get(priority_num)

        # Map to standard Priority enum
        priority = self.value_mapper.map_priority(priority_str)

        # State information - map to standard Status enum
        state = issue_data.get("state", {})
        state_name = state.get("name") if state else None
        state_type = state.get("type") if state else None

        # Try custom state name first (e.g., "new", "In Review")
        status = self.value_mapper.map_status(state_name)

        # If state_name didn't map to enum, try state_type (backlog, started, etc.)
        if status and not isinstance(status, Status):
            status = self.value_mapper.map_status(state_type)

        # Type extraction from labels with fallback logic
        labels = issue_data.get("labels", {})
        label_nodes = labels.get("nodes", []) if labels else []
        type_value = None

        # Check all labels for type match
        for label in label_nodes:
            label_name = label.get("name", "") if label else ""
            if label_name:
                mapped_type = self.value_mapper.map_type(label_name)
                if mapped_type and isinstance(mapped_type, ItemType):
                    type_value = mapped_type
                    break  # Use first matching type

        # If no label matched, check if it's a sub-issue or default to TASK
        if not type_value:
            parent = issue_data.get("parent")
            if parent and parent.get("id"):
                type_value = ItemType.SUB_ISSUE
            else:
                type_value = ItemType.ISSUE

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
            mime_type=MimeTypes.BLOCKS.value,
            weburl=weburl,
            created_at=get_epoch_timestamp_in_ms(),
            updated_at=get_epoch_timestamp_in_ms(),
            source_created_at=created_at,
            source_updated_at=updated_at,
            status=status,
            priority=priority,
            type=type_value,
            delivery_status=None,
            assignee=assignee_name,
            preview_renderable=False,
            assignee_email=assignee_email,
            creator_email=creator_email,
            creator_name=creator_name,
            inherit_permissions=True,
        )

        # Extract and map issue relationships
        relations = issue_data.get("relations", {})
        relation_nodes = relations.get("nodes", []) if relations else []

        # Map Linear relationship types to LinkRelationshipTag enum values
        type_mapping = {
            "blocks": LinkRelationshipTag.BLOCKS.value,
            "blocked": LinkRelationshipTag.BLOCKED_BY.value,
            "related": LinkRelationshipTag.RELATED.value,
            "duplicate": LinkRelationshipTag.DUPLICATES.value,
        }

        # Process each relation
        related_external_records = []
        for relation in relation_nodes:
            relation_type = relation.get("type")
            related_issue = relation.get("relatedIssue", {})
            related_issue_id = related_issue.get("id") if related_issue else None

            # Skip if missing required data
            if not related_issue_id or not relation_type:
                continue

            # Map Linear type to LinkRelationshipTag enum value
            custom_tag = type_mapping.get(relation_type)

            # Only create record if we have a valid mapping
            if custom_tag:
                related_external_records.append(
                    RelatedExternalRecord(
                        external_record_id=related_issue_id,
                        record_type=RecordType.TICKET,
                        relation_type=RecordRelations.LINKED_TO,
                        custom_relationship_tag=custom_tag,
                    )
                )

        # Add all related external records to ticket
        ticket.related_external_records.extend(related_external_records)

        return ticket

    def _transform_to_project_record(
        self,
        project_data: Dict[str, Any],
        team_id: str,
        existing_record: Optional[Record] = None
    ) -> ProjectRecord:
        """
        Transform Linear project to ProjectRecord.

        Args:
            project_data: Raw project data from Linear API
            team_id: Team ID for external_record_group_id
            existing_record: Existing record from DB (if any) for version handling

        Returns:
            ProjectRecord: Transformed project record
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

        # Status information
        status = project_data.get("status", {})
        status_name = status.get("name") if status else None

        # Priority information
        priority_label = project_data.get("priorityLabel", "")

        # Lead information
        lead = project_data.get("lead", {})
        lead_id = lead.get("id") if lead else None
        lead_name = lead.get("displayName") or lead.get("name") if lead else None
        lead_email = lead.get("email") if lead else None

        # Timestamps
        created_at = self._parse_linear_datetime(project_data.get("createdAt", "")) or 0
        updated_at = self._parse_linear_datetime(project_data.get("updatedAt", "")) or 0

        # Handle versioning: use existing record's id and increment version if changed
        is_new = existing_record is None
        record_id = existing_record.id if existing_record else str(uuid4())

        if is_new:
            version = 0
        elif hasattr(existing_record, 'source_updated_at') and existing_record.source_updated_at != updated_at:
            version = existing_record.version + 1
            self.logger.debug(f"📝 Project {record_name} changed, incrementing version to {version}")
        else:
            version = existing_record.version if existing_record else 0

        # Get web URL directly from Linear API response
        weburl = project_data.get("url")

        # Use updatedAt as external_revision_id
        external_revision_id = str(updated_at) if updated_at else None

        # Create ProjectRecord
        project = ProjectRecord(
            id=record_id,
            org_id=self.data_entities_processor.org_id,
            record_name=record_name,
            record_type=RecordType.PROJECT,
            external_record_id=project_id,
            external_revision_id=external_revision_id,
            external_record_group_id=team_id,
            parent_external_record_id=None,  # Projects don't have parents
            parent_record_type=None,
            version=version,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=Connectors.LINEAR,
            connector_id=self.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            weburl=weburl,
            created_at=get_epoch_timestamp_in_ms(),
            updated_at=get_epoch_timestamp_in_ms(),
            source_created_at=created_at,
            source_updated_at=updated_at,
            status=status_name,
            priority=priority_label,
            lead_id=lead_id,
            lead_name=lead_name,
            lead_email=lead_email,
            preview_renderable=False,
            inherit_permissions=True,
            is_dependent_node=False,
            parent_node_id=None,
        )


        return project

    def _transform_attachment_to_link_record(
        self,
        attachment_data: Dict[str, Any],
        issue_id: str,
        parent_node_id: str,
        team_id: str,
        existing_record: Optional[Record] = None,
        parent_record_type: RecordType = RecordType.TICKET
    ) -> LinkRecord:
        """
        Transform Linear attachment or external link data to LinkRecord.
        Can be used for both issue attachments and project external links.

        Args:
            attachment_data: Raw attachment/external link data from Linear API (supports title/subtitle/label)
            issue_id: Parent record external ID (issue or project)
            parent_node_id: Internal record ID of parent (ticket or project)
            team_id: Team ID for external_record_group_id
            existing_record: Existing record from DB (if any) for version handling
            parent_record_type: Type of parent record (TICKET or PROJECT)

        Returns:
            LinkRecord: Transformed link record
        """
        attachment_id = attachment_data.get("id", "")
        if not attachment_id:
            raise ValueError("Attachment data missing required 'id' field")

        url = attachment_data.get("url", "")
        if not url:
            raise ValueError(f"Attachment {attachment_id} missing required 'url' field")

        # Handle both attachments (title/subtitle) and external links (label)
        title = attachment_data.get("title") or attachment_data.get("subtitle") or attachment_data.get("label")

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
            parent_record_type=parent_record_type,
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
        existing_record: Optional[Record] = None,
        parent_record_type: RecordType = RecordType.TICKET
    ) -> WebpageRecord:
        """
        Transform Linear document data to WebpageRecord.
        This method handles versioning similar to TicketRecord.

        Args:
            document_data: Raw document data from Linear API
            issue_id: Parent issue/project external ID
            parent_node_id: Internal record ID of parent ticket/project
            team_id: Team ID for external_record_group_id
            existing_record: Existing record from DB (if any) for version handling
            parent_record_type: Type of parent record (TICKET or PROJECT)

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
            parent_record_type=parent_record_type,
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
            preview_renderable=False,
            is_dependent_node=True,
            parent_node_id=parent_node_id,
            inherit_permissions=True,
        )

        return webpage

    async def _transform_file_url_to_file_record(
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
            parent_weburl: Web URL of parent record (used for file weburl)

        Returns:
            FileRecord: Transformed file record
        """
        # Use full file URL as external_record_id (for streaming)
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

        # Get file size from URL using HEAD request
        size_in_bytes = 0
        try:
            if self.data_source:
                datasource = await self._get_fresh_datasource()
                file_size = await datasource.get_file_size(file_url)
                if file_size is not None:
                    size_in_bytes = file_size
        except Exception as e:
            # Log error but don't fail - use 0 as fallback
            self.logger.debug(f"⚠️ Could not fetch file size for {file_url}: {e}")

        file_record = FileRecord(
            id=record_id,
            org_id=self.data_entities_processor.org_id,
            record_name=filename,
            record_type=RecordType.FILE,
            external_record_id=file_url,  # Use full file URL as external_record_id for streaming
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
            source_updated_at=parent_updated_at,
            preview_renderable=True,
            is_dependent_node=True,
            parent_node_id=parent_node_id,
            inherit_permissions=True,
            is_file=True,
            extension=extension,
            size_in_bytes=size_in_bytes,
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
            original_filter_ts = modified_after_ts
            modified_after_ts = max(modified_after_ts, last_sync_time)
            self.logger.info(f"🔄 Using latest modified_after: {modified_after_ts} (filter: {original_filter_ts}, checkpoint: {last_sync_time})")
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

    # ==================== DELETION SYNC ====================

    async def _sync_deleted_issues(
        self,
        team_record_groups: List[Tuple[RecordGroup, List[Permission]]]
    ) -> None:
        """
        Sync deleted/trashed issues to mark records as deleted.
        Also marks related records (comments, attachments, files) as deleted.
        Uses incremental sync based on archivedAt timestamp.

        Note: Only runs if this is not the initial sync (i.e., we have previous sync checkpoints).
        During initial sync, there are no previously synced records to mark as deleted.
        """
        if not team_record_groups:
            return

        # Build team IDs list
        team_ids = [
            team_rg.external_group_id
            for team_rg, _ in team_record_groups
            if team_rg.external_group_id
        ]

        if not team_ids:
            return

        # Get last deletion sync checkpoint - if None, this is initial sync, skip deletion sync
        last_sync_time = await self._get_deletion_sync_checkpoint("issues")

        if last_sync_time is None:
            self.logger.info("ℹ️ Skipping deletion sync - this is the initial sync (no deletion checkpoint)")
            # Create initial checkpoint so next sync knows it's not initial anymore
            current_time = get_epoch_timestamp_in_ms()
            await self._update_deletion_sync_checkpoint("issues", current_time)
            return

        try:
            self.logger.info("🗑️ Starting deleted issues sync")

            datasource = await self._get_fresh_datasource()
            after_cursor: Optional[str] = None
            total_deleted = 0
            max_archived_at: Optional[int] = None

            # Build filter for issues (can't filter by trashed in GraphQL, so we filter in code)
            issue_filter: Dict[str, Any] = {
                "team": {"id": {"in": team_ids}}
            }

            # Add incremental filter if we have a checkpoint
            if last_sync_time:
                archived_after = self._linear_datetime_from_timestamp(last_sync_time)
                if archived_after:
                    issue_filter["archivedAt"] = {"gte": archived_after}
                    self.logger.info(f"🔄 Incremental deletion sync from {archived_after}")

            while True:
                # Use regular issues query with includeArchived=true, then filter for trashed in code
                response = await datasource.issues(
                    first=50,
                    after=after_cursor,
                    filter=issue_filter,
                    includeArchived=True
                )

                if not response.success:
                    self.logger.error(f"❌ Failed to fetch issues for deletion sync: {response.message}")
                    break

                issues_data = response.data.get("issues", {}) if response.data else {}
                issues_list = issues_data.get("nodes", [])
                page_info = issues_data.get("pageInfo", {})

                if not issues_list:
                    break

                # Filter for trashed issues in application code
                trashed_issues = [issue for issue in issues_list if issue.get("trashed")]

                for issue in trashed_issues:
                    issue_id = issue.get("id")
                    identifier = issue.get("identifier", "")
                    archived_at = issue.get("archivedAt")

                    self.logger.info(f"🗑️ Marking issue {identifier} ({issue_id}) as deleted")

                    # Mark the issue record as deleted
                    await self._mark_record_and_children_deleted(
                        external_record_id=issue_id,
                        record_type="issue"
                    )
                    total_deleted += 1

                    # Track max archivedAt for checkpoint
                    if archived_at:
                        archived_timestamp = self._parse_linear_datetime(archived_at)
                        if archived_timestamp:
                            if max_archived_at is None or archived_timestamp > max_archived_at:
                                max_archived_at = archived_timestamp

                # Check for more pages
                if not page_info.get("hasNextPage", False):
                    break

                after_cursor = page_info.get("endCursor")
                if not after_cursor:
                    break

            # Update checkpoint
            if max_archived_at:
                await self._update_deletion_sync_checkpoint("issues", max_archived_at)

            if total_deleted > 0:
                self.logger.info(f"✅ Marked {total_deleted} issues as deleted")
            else:
                self.logger.info("ℹ️ No deleted issues found")

        except Exception as e:
            self.logger.error(f"❌ Error syncing deleted issues: {e}", exc_info=True)

    async def _sync_deleted_projects(
        self,
        team_record_groups: List[Tuple[RecordGroup, List[Permission]]]
    ) -> None:
        """
        Sync deleted/trashed projects to mark records as deleted.
        Also marks related records (documents, milestones, updates, comments, files) as deleted.
        Uses incremental sync based on archivedAt timestamp.

        Note: Only runs if this is not the initial sync (i.e., we have previous sync checkpoints).
        During initial sync, there are no previously synced records to mark as deleted.
        """
        if not team_record_groups:
            return

        # Build team IDs list
        team_ids = [
            team_rg.external_group_id
            for team_rg, _ in team_record_groups
            if team_rg.external_group_id
        ]

        if not team_ids:
            return

        # Get last deletion sync checkpoint - if None, this is initial sync, skip deletion sync
        last_sync_time = await self._get_deletion_sync_checkpoint("projects")

        if last_sync_time is None:
            self.logger.info("ℹ️ Skipping deletion sync - this is the initial sync (no deletion checkpoint)")
            # Create initial checkpoint so next sync knows it's not initial anymore
            current_time = get_epoch_timestamp_in_ms()
            await self._update_deletion_sync_checkpoint("projects", current_time)
            return

        try:
            self.logger.info("🗑️ Starting deleted projects sync")

            datasource = await self._get_fresh_datasource()
            after_cursor: Optional[str] = None
            total_deleted = 0
            max_archived_at: Optional[int] = None

            # Build filter for projects (can't filter by trashed or archivedAt in GraphQL, so we filter in code)
            project_filter: Dict[str, Any] = {
                "accessibleTeams": {
                    "some": {
                        "id": {"in": team_ids}
                    }
                }
            }

            # Log incremental sync info
            if last_sync_time:
                archived_after = self._linear_datetime_from_timestamp(last_sync_time)
                if archived_after:
                    self.logger.info(f"🔄 Incremental deletion sync from {archived_after}")

            while True:
                # Use regular projects query with includeArchived=true, then filter for trashed in code
                response = await datasource.projects(
                    first=50,
                    after=after_cursor,
                    filter=project_filter,
                    includeArchived=True
                )

                if not response.success:
                    self.logger.error(f"❌ Failed to fetch projects for deletion sync: {response.message}")
                    break

                projects_data = response.data.get("projects", {}) if response.data else {}
                projects_list = projects_data.get("nodes", [])
                page_info = projects_data.get("pageInfo", {})

                if not projects_list:
                    break

                # Filter for trashed projects in application code
                trashed_projects = []
                for project in projects_list:
                    if not project.get("trashed"):
                        continue

                    # If we have a checkpoint, also filter by archivedAt in code (since GraphQL doesn't support it)
                    if last_sync_time:
                        archived_at = project.get("archivedAt")
                        if not archived_at:
                            continue
                        archived_timestamp = self._parse_linear_datetime(archived_at)
                        if not archived_timestamp or archived_timestamp < last_sync_time:
                            continue

                    trashed_projects.append(project)

                for project in trashed_projects:
                    project_id = project.get("id")
                    project_name = project.get("name", "")
                    archived_at = project.get("archivedAt")

                    self.logger.info(f"🗑️ Marking project '{project_name}' ({project_id}) as deleted")

                    # Mark the project record and all children as deleted
                    await self._mark_record_and_children_deleted(
                        external_record_id=project_id,
                        record_type="project"
                    )
                    total_deleted += 1

                    # Track max archivedAt for checkpoint
                    if archived_at:
                        archived_timestamp = self._parse_linear_datetime(archived_at)
                        if archived_timestamp:
                            if max_archived_at is None or archived_timestamp > max_archived_at:
                                max_archived_at = archived_timestamp

                # Check for more pages
                if not page_info.get("hasNextPage", False):
                    break

                after_cursor = page_info.get("endCursor")
                if not after_cursor:
                    break

            # Update checkpoint
            if max_archived_at:
                await self._update_deletion_sync_checkpoint("projects", max_archived_at)

            if total_deleted > 0:
                self.logger.info(f"✅ Marked {total_deleted} projects as deleted")
            else:
                self.logger.info("ℹ️ No deleted projects found")

        except Exception as e:
            self.logger.error(f"❌ Error syncing deleted projects: {e}", exc_info=True)

    async def _mark_record_and_children_deleted(
        self,
        external_record_id: str,
        record_type: str
    ) -> None:
        """
        Mark a record and all its child records as deleted.

        For Issues:
            - Issue (TicketRecord) - contains comments as blocks (not separate records)
            - Attachments (LinkRecord) - separate records
            - Documents (WebPageRecord) - separate records
            - Files (FileRecord) - extracted from issue descriptions

        For Projects:
            - Project (ProjectRecord) - contains milestones, updates, and comments as blocks (not separate records)
            - Documents (WebPageRecord) - separate records
            - External Links (LinkRecord) - separate records
            - Files (FileRecord) - extracted from project content markdown

        Note: Issue comments and project milestones/updates/comments are stored as blocks within
        the parent record itself, not as separate records, so they are automatically deleted
        when the parent record is deleted.
        """
        try:
            # Use transaction to delete parent and all children
            async with self.data_store_provider.transaction() as tx_store:
                # Get the parent record within transaction
                parent_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=external_record_id
                )

                if not parent_record:
                    self.logger.debug(f"Record {external_record_id} not found in DB, skipping deletion")
                    return

                # Get and delete all child records first (recursively)
                child_records = await tx_store.get_records_by_parent(
                    connector_id=self.connector_id,
                    parent_external_record_id=external_record_id
                )

                for child_record in child_records:
                    # Recursively delete grandchildren (e.g., files attached to comments)
                    grandchild_records = await tx_store.get_records_by_parent(
                        connector_id=self.connector_id,
                        parent_external_record_id=child_record.external_record_id
                    )
                    for grandchild in grandchild_records:
                        # Delete grandchild record and all its relations
                        await tx_store.delete_records_and_relations(
                            record_key=grandchild.id,
                            hard_delete=True
                        )

                    # Delete child record and all its relations
                    await tx_store.delete_records_and_relations(
                        record_key=child_record.id,
                        hard_delete=True
                    )

                # Finally, delete the parent record and all its relations
                await tx_store.delete_records_and_relations(
                    record_key=parent_record.id,
                    hard_delete=True
                )

            self.logger.debug(
                f"Marked {external_record_id} and {len(child_records)} children as deleted"
            )

        except Exception as e:
            self.logger.error(f"❌ Error marking record {external_record_id} as deleted: {e}")

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

    async def _get_deletion_sync_checkpoint(self, entity_type: str) -> Optional[int]:
        """Get deletion sync checkpoint for issues or projects."""
        sync_point_key = f"{entity_type}_deletion_sync"
        data = await self.deletion_sync_point.read_sync_point(sync_point_key)
        return data.get("last_sync_time") if data else None

    async def _update_deletion_sync_checkpoint(self, entity_type: str, timestamp: int) -> None:
        """Update deletion sync checkpoint."""
        sync_point_key = f"{entity_type}_deletion_sync"
        await self.deletion_sync_point.update_sync_point(
            sync_point_key,
            {"last_sync_time": timestamp}
        )
        self.logger.debug(f"💾 Updated deletion sync checkpoint for {entity_type}: {timestamp}")

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

    def _parse_linear_datetime_to_datetime(self, datetime_str: str) -> Optional[datetime]:
        """
        Parse Linear datetime string to datetime object.

        Linear format: "2025-01-01T12:00:00.000Z" (ISO 8601 with Z suffix)

        Args:
            datetime_str: Linear datetime string

        Returns:
            datetime: Datetime object or None if parsing fails
        """
        if not datetime_str:
            return None
        try:
            # Parse ISO 8601 format: '2025-01-01T12:00:00.000Z'
            # Replace 'Z' with '+00:00' for proper ISO format parsing
            return datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))
        except Exception as e:
            self.logger.debug(f"Failed to parse Linear datetime '{datetime_str}': {e}")
            return None

    # ==================== PROJECT BLOCKS PARSER ====================

    def _organize_comments_by_thread(
        self,
        comments: List[BlockComment]
    ) -> List[List[BlockComment]]:
        """
        Organize comments into a 2D list grouped by thread_id, with each thread sorted by created_at.

        Args:
            comments: List of BlockComment objects

        Returns:
            List of lists, where each inner list contains comments for one thread,
            sorted by created_at (oldest first)
        """
        if not comments:
            return []

        # Group comments by thread_id
        threads_dict: Dict[str, List[BlockComment]] = {}
        for comment in comments:
            thread_id = comment.thread_id or "no_thread"
            if thread_id not in threads_dict:
                threads_dict[thread_id] = []
            threads_dict[thread_id].append(comment)

        # Sort each thread's comments by created_at (oldest first)
        organized_comments: List[List[BlockComment]] = []
        for thread_id, thread_comments in threads_dict.items():
            sorted_thread = sorted(
                thread_comments,
                key=lambda c: c.created_at if c.created_at else datetime.max.replace(tzinfo=timezone.utc)
            )
            organized_comments.append(sorted_thread)

        # Sort threads by the first comment's created_at (oldest thread first)
        organized_comments.sort(
            key=lambda thread: thread[0].created_at if thread[0].created_at else datetime.max.replace(tzinfo=timezone.utc)
        )

        return organized_comments

    def _create_blockgroup(
        self,
        name: str,
        weburl: str,
        data: str,
        group_type: GroupType = GroupType.TEXT_SECTION,
        group_subtype: Optional[GroupSubType] = None,
        description: Optional[str] = None,
        source_group_id: Optional[str] = None,
        format: DataFormat = DataFormat.MARKDOWN,
        index: Optional[int] = None,
        requires_processing: bool = True,
        comments: Optional[List[List[BlockComment]]] = None,
    ) -> BlockGroup:
        """
        Create a BlockGroup object directly without using BlockGroupBuilder.

        Args:
            name: Name of the block group
            weburl: Web URL for the original source context (e.g., Linear project page)
            data: Content data (markdown/HTML) to be processed
            group_type: Type of the block group (defaults to TEXT_SECTION)
            group_subtype: Optional subtype of the block group (e.g., MILESTONE, UPDATE, PROJECT_CONTENT)
            description: Optional description of the block group
            source_group_id: Optional source group identifier
            format: Data format (defaults to MARKDOWN)
            index: Optional index for the block group
            requires_processing: Whether this block group requires further processing (defaults to True)
            comments: Optional 2D list of BlockComment objects (grouped by thread_id, sorted by created_at)

        Returns:
            BlockGroup instance

        Raises:
            ValueError: If weburl or data is not provided
        """
        if not weburl:
            raise ValueError("weburl is required when creating BlockGroup")
        if not data:
            raise ValueError("data is required when creating BlockGroup")

        return BlockGroup(
            name=name,
            type=group_type,
            sub_type=group_subtype,
            description=description,
            source_group_id=source_group_id,
            data=data,
            format=format,
            weburl=weburl,
            index=index,
            requires_processing=requires_processing,
            comments=comments or [],
        )

    async def _parse_project_to_blocks(
        self,
        project_data: Dict[str, Any],
        weburl: Optional[str] = None,
        related_external_record_ids: Optional[List[str]] = None,
    ) -> BlocksContainer:
        """
        Parse project data into multiple BlockGroups that will be processed by Docling.

        This creates separate BlockGroups with process_docling=True for:
        - Project Description/Content (if exists) - One BlockGroup
        - Project Comments (if exists) - One BlockGroup with comments as BlockComments (not embedded in markdown)
        - Each Project Milestone (if exists) - One BlockGroup per milestone
        - Each Project Update (if exists) - One BlockGroup per update with update comments as BlockComments

        Args:
            project_data: Full project data from Linear API (with nested data)
            weburl: Project web URL (required for docling BlockGroups)
            related_external_record_ids: List of external IDs of related records (issues, documents, external links)

        Returns:
            BlocksContainer with multiple BlockGroups (process_docling=True), one per section
        """
        project_id = project_data.get("id", "")
        project_name = project_data.get("name", "")
        project_description = project_data.get("description", "")

        if not weburl:
            raise ValueError("weburl is required when creating docling BlockGroup for projects")

        block_groups: List[BlockGroup] = []
        block_group_index = 0

        # 1. Project Description/Content BlockGroup
        # Include both description and content: description first, then content below
        content = project_data.get("content", "")
        description_sections = []

        # Add description first (if exists)
        if project_description:
            description_sections.append(f"# Project Description\n\n{project_description}")

        # Add content below description (if exists)
        if content:
            if description_sections:
                # If we have description, add content as a new section
                description_sections.append(f"\n# Project Content\n\n{content}")
            else:
                # If no description, content becomes the main section
                description_sections.append(f"# Project Content\n\n{content}")

        # Create BlockGroup if we have either description or content
        if description_sections:
            combined_content = "\n".join(description_sections)

            # Convert images in project description/content to base64
            combined_content = await self._convert_images_to_base64_in_markdown(combined_content)

            # Build project comments as BlockComments
            block_comments: List[BlockComment] = []
            comments = project_data.get("comments", {}).get("nodes", [])
            if comments:
                for comment_data in comments:
                    comment_body = comment_data.get("body", "")
                    if comment_body:
                        author = comment_data.get("user", {})
                        author_id = author.get("id")
                        author_name = author.get("displayName") or author.get("name", "Unknown")
                        comment_id = comment_data.get("id", "")
                        comment_url = comment_data.get("url", "")
                        comment_created_at_str = comment_data.get("createdAt", "")
                        comment_updated_at_str = comment_data.get("updatedAt", "")

                        # Parse datetime strings to datetime objects
                        comment_created_at = self._parse_linear_datetime_to_datetime(comment_created_at_str)
                        comment_updated_at = self._parse_linear_datetime_to_datetime(comment_updated_at_str)

                        # Convert images in comment body to base64
                        comment_body = await self._convert_images_to_base64_in_markdown(comment_body)

                        # Extract attachments from comment body (excluding images)
                        attachments = None
                        if comment_body:
                            file_urls = self._extract_file_urls_from_markdown(comment_body, exclude_images=True)
                            if file_urls:
                                attachments = [
                                    CommentAttachment(
                                        name=file_info.get("filename", ""),
                                        id=file_info.get("url", "")  # Use file URL as ID (same as external_record_id)
                                    )
                                    for file_info in file_urls
                                    if file_info.get("filename") and file_info.get("url")
                                ]

                        # Handle quoted_text if present
                        quoted_text = comment_data.get("quotedText") or comment_data.get("quoted_text")

                        # Get thread_id from parent comment id, or use comment's own id if top-level
                        parent_comment = comment_data.get("parent", {})
                        thread_id = parent_comment.get("id") if parent_comment else comment_id

                        block_comments.append(
                            BlockComment(
                                text=comment_body,
                                format=DataFormat.MARKDOWN,
                                author_id=author_id,
                                author_name=author_name,
                                thread_id=thread_id,
                                resolution_status=None,
                                weburl=comment_url if comment_url else None,
                                created_at=comment_created_at,
                                updated_at=comment_updated_at,
                                attachments=attachments if attachments else None,
                                quoted_text=quoted_text,
                            )
                        )

            # Organize comments by thread_id and sort by created_at
            organized_comments = self._organize_comments_by_thread(block_comments)

            content_block_group = self._create_blockgroup(
                name=f"{project_name} - Description & Content" if project_name else "Project Description & Content",
                weburl=weburl,
                data=combined_content,
                group_type=GroupType.TEXT_SECTION,
                group_subtype=GroupSubType.PROJECT_CONTENT,
                description=project_description or "Project content and description",
                source_group_id=f"{project_id}_description_content",
                format=DataFormat.MARKDOWN,
                index=block_group_index,
                requires_processing=True,
                comments=organized_comments,
            )

            block_groups.append(content_block_group)
            block_group_index += 1

        # 3. Project Milestones - One BlockGroup per Milestone
        milestones = project_data.get("projectMilestones", {}).get("nodes", [])
        for milestone_data in milestones:
            milestone_id = milestone_data.get("id", "")
            milestone_name = milestone_data.get("name", "")
            milestone_description = milestone_data.get("description", "")

            if milestone_name or milestone_description:
                # Build markdown content for this milestone with project context for searchability
                milestone_markdown = f"# Project: {project_name}\n\n## Milestone: {milestone_name}\n"
                if milestone_description:
                    milestone_markdown += f"\n{milestone_description}\n"

                # Convert images in milestone description to base64
                milestone_markdown = await self._convert_images_to_base64_in_markdown(milestone_markdown)

                # Construct milestone-specific URL: {project_url}/overview#milestone-{milestone_id}
                milestone_weburl = f"{weburl.rstrip('/')}/overview#milestone-{milestone_id}" if milestone_id and weburl else weburl

                milestone_block_group = self._create_blockgroup(
                    name=f"{project_name} - Milestone: {milestone_name}" if project_name else f"Milestone: {milestone_name}",
                    weburl=milestone_weburl,
                    data=milestone_markdown,
                    group_type=GroupType.TEXT_SECTION,
                    group_subtype=GroupSubType.MILESTONE,
                    description=milestone_description or f"Milestone: {milestone_name}",
                    source_group_id=milestone_id or f"{project_id}_milestone_{block_group_index}",
                    format=DataFormat.MARKDOWN,
                    index=block_group_index,
                    requires_processing=True,
                )
                block_groups.append(milestone_block_group)
                block_group_index += 1

        # 4. Project Updates - One BlockGroup per Update (with comments as BlockComments)
        project_updates = project_data.get("projectUpdates", {}).get("nodes", [])
        for update_data in project_updates:
            update_id = update_data.get("id", "")
            update_body = update_data.get("body", "")

            if update_body:
                # Build markdown content for this update with project context for searchability
                author = update_data.get("user", {})
                author_name = author.get("displayName") or author.get("name", "Unknown")
                update_created_at = update_data.get("createdAt", "")

                update_markdown = f"# Project: {project_name}\n\n## Project Update by {author_name}\n"
                if update_created_at:
                    update_markdown += f"*Created: {update_created_at}*\n"
                update_markdown += f"\n{update_body}\n"

                # Convert images in update body to base64
                update_markdown = await self._convert_images_to_base64_in_markdown(update_markdown)

                # Update URL is always provided by Linear API
                update_weburl = update_data.get("url") or weburl

                # Build update comments as BlockComments
                update_block_comments: List[BlockComment] = []
                update_comments = update_data.get("comments", {}).get("nodes", [])
                if update_comments:
                    for comment_data in update_comments:
                        comment_body = comment_data.get("body", "")
                        if comment_body:
                            comment_author = comment_data.get("user", {})
                            comment_author_id = comment_author.get("id")
                            comment_author_name = comment_author.get("displayName") or comment_author.get("name", "Unknown")
                            comment_id = comment_data.get("id", "")
                            comment_url = comment_data.get("url", "")
                            comment_created_at_str = comment_data.get("createdAt", "")
                            comment_updated_at_str = comment_data.get("updatedAt", "")

                            # Parse datetime strings to datetime objects
                            comment_created_at = self._parse_linear_datetime_to_datetime(comment_created_at_str)
                            comment_updated_at = self._parse_linear_datetime_to_datetime(comment_updated_at_str)

                            # Convert images in comment body to base64
                            comment_body = await self._convert_images_to_base64_in_markdown(comment_body)

                            # Extract attachments from comment body (excluding images) using existing function
                            attachments = None
                            if comment_body:
                                file_urls = self._extract_file_urls_from_markdown(comment_body, exclude_images=True)
                                if file_urls:
                                    attachments = [
                                        CommentAttachment(
                                            name=file_info.get("filename", ""),
                                            id=file_info.get("url", "")  # Use file URL as ID (same as external_record_id)
                                        )
                                        for file_info in file_urls
                                        if file_info.get("filename") and file_info.get("url")
                                    ]

                            # Handle quoted_text if present
                            quoted_text = comment_data.get("quotedText") or comment_data.get("quoted_text")

                            # Get thread_id from parent comment id, or use comment's own id if top-level
                            parent_comment = comment_data.get("parent", {})
                            thread_id = parent_comment.get("id") if parent_comment else comment_id

                            update_block_comments.append(
                                BlockComment(
                                    text=comment_body,
                                    format=DataFormat.MARKDOWN,
                                    author_id=comment_author_id,
                                    author_name=comment_author_name,
                                    thread_id=thread_id,
                                    resolution_status=None,
                                    weburl=comment_url if comment_url else None,
                                    created_at=comment_created_at,
                                    updated_at=comment_updated_at,
                                    attachments=attachments if attachments else None,
                                    quoted_text=quoted_text,
                                )
                            )

                # Organize comments by thread_id and sort by created_at
                organized_update_comments = self._organize_comments_by_thread(update_block_comments)

                update_block_group = self._create_blockgroup(
                    name=f"{project_name} - Update by {author_name}" if project_name else f"Update by {author_name}",
                    weburl=update_weburl,
                    data=update_markdown,
                    group_type=GroupType.TEXT_SECTION,
                    group_subtype=GroupSubType.UPDATE,
                    description=f"Project update by {author_name}",
                    source_group_id=update_id or f"{project_id}_update_{block_group_index}",
                    format=DataFormat.MARKDOWN,
                    index=block_group_index,
                    requires_processing=True,
                    comments=organized_update_comments,
                )
                block_groups.append(update_block_group)
                block_group_index += 1

        # If no blockgroups were created (no description, content, milestones, or updates),
        # create a minimal BlockGroup with just the project name
        if not block_groups:
            minimal_data = f"# {project_name}" if project_name else f"# Project {project_id}"
            minimal_data = await self._convert_images_to_base64_in_markdown(minimal_data)

            minimal_block_group = self._create_blockgroup(
                name=project_name or f"Project {project_id}",
                weburl=weburl,
                data=minimal_data,
                group_type=GroupType.TEXT_SECTION,
                group_subtype=GroupSubType.PROJECT_CONTENT,
                description="Project information",
                source_group_id=project_id,
                format=DataFormat.MARKDOWN,
                index=0,
                requires_processing=True,
            )
            block_groups.append(minimal_block_group)

        return BlocksContainer(blocks=[], block_groups=block_groups)

    def _organize_issue_comments_to_threads(self, comments_data: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """
        Group comments by thread (parent_id) and sort by created_at.
        Returns list of threads, each thread is a list of comments sorted by created_at.

        Args:
            comments_data: List of comment data from Linear API

        Returns:
            List of threads, each thread is a list of comments sorted by created_at (oldest first)
        """
        if not comments_data:
            return []

        threads: Dict[str, List[Dict[str, Any]]] = {}

        for comment in comments_data:
            parent = comment.get("parent", {})
            # Thread ID is parent's ID if it's a reply, or self ID if top-level
            thread_id = parent.get("id") if parent and parent.get("id") else comment.get("id", "")
            if not thread_id:
                continue

            if thread_id not in threads:
                threads[thread_id] = []
            threads[thread_id].append(comment)

        # Sort each thread by created_at (oldest first)
        for thread_id in threads:
            threads[thread_id].sort(
                key=lambda c: self._parse_linear_datetime(c.get("createdAt", "")) or 0
            )

        # Sort threads by first comment's created_at (oldest thread first)
        sorted_threads = sorted(
            threads.values(),
            key=lambda t: self._parse_linear_datetime(t[0].get("createdAt", "")) or 0 if t else 0
        )

        return sorted_threads

    async def _parse_issue_to_blocks(
        self,
        issue_data: Dict[str, Any],
        weburl: Optional[str] = None,
        children_records: Optional[List[ChildRecord]] = None,
        comment_file_children_map: Optional[Dict[str, List[ChildRecord]]] = None,
    ) -> BlocksContainer:
        """
        Parse issue data into BlockGroups that will be processed by Docling.

        This creates:
        - Description BlockGroup (index=0) with:
          - Issue description markdown
          - children_records in TableRowMetadata (attachments, documents, files)
        - Thread BlockGroups (index=1,2,...) for each comment thread with:
          - parent_index=0 (pointing to Description BlockGroup)
        - Comment BlockGroups (sub_type=COMMENT) for each comment with:
          - parent_index pointing to thread BlockGroup index
          - requires_processing=True (so Docling processes them)
          - children_records in TableRowMetadata (files from comment body)

        Args:
            issue_data: Full issue data from Linear API (with nested data)
            weburl: Issue web URL (required for docling BlockGroups)
            children_records: List of ChildRecord for attachments, documents, files (for description)
            comment_file_children_map: Dictionary mapping comment_id to List[ChildRecord] for files in comments

        Returns:
            BlocksContainer with Description BlockGroup, Thread BlockGroups, and Comment BlockGroups
        """
        issue_id = issue_data.get("id", "")
        issue_identifier = issue_data.get("identifier", "")
        issue_title = issue_data.get("title", "")
        issue_description = issue_data.get("description", "")

        if not weburl:
            raise ValueError("weburl is required when creating docling BlockGroup for issues")

        block_groups: List[BlockGroup] = []
        blocks: List[Block] = []
        block_group_index = 0

        # 1. Description BlockGroup (index=0)
        # Use description if available, otherwise create minimal content with title
        description_content = issue_description if issue_description else f"# {issue_title}" if issue_title else f"# Issue {issue_identifier or issue_id}"

        # Convert images in description to base64
        description_content = await self._convert_images_to_base64_in_markdown(description_content)

        # Create TableRowMetadata with children_records if any
        table_row_metadata = None
        if children_records:
            table_row_metadata = TableRowMetadata(
                children_records=children_records
            )

        description_block_group = BlockGroup(
            id=str(uuid4()),
            index=block_group_index,
            name=issue_title if issue_title else (f"{issue_identifier} - Description" if issue_identifier else "Issue Description"),
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.ISSUE_CONTENT,
            description=f"Description for issue {issue_identifier}" if issue_identifier else "Issue description",
            source_group_id=f"{issue_id}_description",
            data=description_content,
            format=DataFormat.MARKDOWN,
            weburl=weburl,
            requires_processing=True,
            table_row_metadata=table_row_metadata,
        )
        block_groups.append(description_block_group)
        block_group_index += 1

        # 2. Comment Thread BlockGroups (index=1,2,...) and Comment Blocks
        comments_data = issue_data.get("comments", {}).get("nodes", [])

        if comments_data:
            sorted_threads = self._organize_issue_comments_to_threads(comments_data)

            for thread_comments in sorted_threads:
                if not thread_comments:
                    continue

                # Get thread ID from first comment (either its ID if top-level, or parent ID if reply)
                first_comment = thread_comments[0]
                parent = first_comment.get("parent", {})
                thread_id = parent.get("id") if parent and parent.get("id") else first_comment.get("id", "")

                # Create thread BlockGroup with parent_index=0 (Description BlockGroup)
                thread_block_group = BlockGroup(
                    id=str(uuid4()),
                    index=block_group_index,
                    parent_index=0,  # Points to Description BlockGroup
                    name=f"Comment Thread - {thread_id[:8]}" if thread_id else "Comment Thread",
                    type=GroupType.TEXT_SECTION,
                    sub_type=GroupSubType.COMMENT_THREAD,
                    description=f"Comment thread for issue {issue_identifier}" if issue_identifier else "Comment thread",
                    source_group_id=f"{issue_id}_thread_{thread_id}" if thread_id else f"{issue_id}_thread_{block_group_index}",
                    weburl=thread_comments[0].get("url") if thread_comments else weburl,
                    requires_processing=False,
                )
                block_groups.append(thread_block_group)
                thread_block_group_index = block_group_index
                block_group_index += 1

                # Create BlockGroup objects for each comment in the thread
                for comment in thread_comments:
                    comment_body = comment.get("body", "")
                    if not comment_body:
                        continue

                    comment_id = comment.get("id", "")
                    comment_url = comment.get("url", "")
                    comment_created_at_str = comment.get("createdAt", "")
                    comment_updated_at_str = comment.get("updatedAt", "")

                    # Parse datetime strings to datetime objects
                    self._parse_linear_datetime_to_datetime(comment_created_at_str)
                    self._parse_linear_datetime_to_datetime(comment_updated_at_str)

                    user = comment.get("user", {})
                    author_name = user.get("displayName") or user.get("name") or "Unknown"

                    # Convert images in comment body to base64
                    comment_body = await self._convert_images_to_base64_in_markdown(comment_body)

                    # Get file children_records for this comment if any
                    comment_file_children = None
                    if comment_file_children_map and comment_id in comment_file_children_map:
                        comment_file_children = comment_file_children_map[comment_id]

                    # Create TableRowMetadata with children_records if comment has files
                    comment_table_row_metadata = None
                    if comment_file_children:
                        comment_table_row_metadata = TableRowMetadata(
                            children_records=comment_file_children
                        )

                    # Create BlockGroup with sub_type=COMMENT
                    comment_block_group = BlockGroup(
                        id=str(uuid4()),
                        index=block_group_index,
                        parent_index=thread_block_group_index,  # Points to thread BlockGroup
                        name=f"Comment by {author_name}",
                        type=GroupType.TEXT_SECTION,
                        sub_type=GroupSubType.COMMENT,
                        description=f"Comment by {author_name}",
                        source_group_id=comment_id,
                        data=comment_body,
                        format=DataFormat.MARKDOWN,
                        weburl=comment_url if comment_url else None,
                        requires_processing=True,
                        table_row_metadata=comment_table_row_metadata,
                    )
                    block_groups.append(comment_block_group)
                    block_group_index += 1

        # If no blockgroups were created (no description and no comments), create minimal BlockGroup
        if not block_groups:
            minimal_data = f"# {issue_title}" if issue_title else f"# Issue {issue_identifier or issue_id}"
            minimal_data = await self._convert_images_to_base64_in_markdown(minimal_data)
            minimal_block_group = BlockGroup(
                id=str(uuid4()),
                index=0,
                name=f"{issue_identifier} - Description" if issue_identifier else "Issue Description",
                type=GroupType.TEXT_SECTION,
                sub_type=GroupSubType.ISSUE_CONTENT,
                description=f"Description for issue {issue_identifier}" if issue_identifier else "Issue description",
                source_group_id=f"{issue_id}_description",
                data=minimal_data,
                format=DataFormat.MARKDOWN,
                weburl=weburl,
                requires_processing=True,
                table_row_metadata=TableRowMetadata(children_records=children_records) if children_records else None,
            )
            block_groups.append(minimal_block_group)

        # Populate children arrays for BlockGroups
        # Build a map of parent_index -> list of child BlockGroup indices
        blockgroup_children_map = defaultdict(list)  # Maps BlockGroup index -> list of child BlockGroup indices

        # Collect all BlockGroup children (thread groups that are children of description,
        # and comment groups that are children of thread groups)
        for bg in block_groups:
            if bg.parent_index is not None:
                blockgroup_children_map[bg.parent_index].append(bg.index)

        # Now populate the children arrays
        for bg in block_groups:
            children_list = []

            # Add child BlockGroups
            if bg.index in blockgroup_children_map:
                for child_bg_index in sorted(blockgroup_children_map[bg.index]):
                    children_list.append(BlockContainerIndex(block_group_index=child_bg_index))

            # Set children if we have any
            if children_list:
                bg.children = children_list

        return BlocksContainer(blocks=blocks, block_groups=block_groups)

    async def _process_project_blockgroups_for_streaming(self, record: Record) -> bytes:
        """
        Process project BlockGroups for streaming by creating BlocksContainer on-demand.

        This function:
        1. Fetches project data from Linear API
        2. Parses project content to BlockGroups (description, milestones, updates)
        3. Processes external links and documents with BlockGroups
        4. Fetches FileRecords and adds them to description BlockGroup's children_records
        5. Combines everything into BlocksContainer
        6. Serializes BlocksContainer to JSON bytes for streaming

        Args:
            record: ProjectRecord to stream

        Returns:
            bytes: Serialized BlocksContainer as JSON bytes

        Raises:
            Exception: If project data cannot be fetched or processed
        """
        record_id = record.external_record_id
        datasource = await self._get_fresh_datasource()
        project_response = await datasource.project(id=record_id)

        if not project_response.success:
            raise Exception(f"Failed to fetch project content: {project_response.message}")

        project_data = project_response.data.get("project", {}) if project_response.data else {}
        if not project_data:
            raise Exception(f"No project data found for ID: {record_id}")

        # Get project weburl for BlockGroup
        project_weburl = project_data.get("url") or f"https://linear.app/project/{record_id}"

        # Parse project content to BlockGroups
        blocks_container = await self._parse_project_to_blocks(
            project_data,
            weburl=project_weburl,
            related_external_record_ids=None
        )

        # Get external links, documents data from API
        external_links_data = project_data.get("externalLinks", {}).get("nodes", [])
        documents_data = project_data.get("documents", {}).get("nodes", [])
        project_content = project_data.get("content", "")

        link_block_groups: List[BlockGroup] = []
        document_block_groups: List[BlockGroup] = []
        file_children: List[ChildRecord] = []

        # Use data_store_provider to look up existing records
        async with self.data_store_provider.transaction() as tx_store:
            if external_links_data:
                # Process external links and create BlockGroups (with create_block_groups=True)
                link_records, link_block_groups = await self._process_project_external_links(
                    external_links_data=external_links_data,
                    project_id=record_id,
                    project_node_id=record.id,
                    team_id=record.external_record_group_id or "",
                    tx_store=tx_store,
                    create_block_groups=True
                )

            if documents_data:
                # Process documents and create BlockGroups (with create_block_groups=True)
                document_records, document_block_groups = await self._process_project_documents(
                    documents_data=documents_data,
                    project_id=record_id,
                    project_node_id=record.id,
                    team_id=record.external_record_group_id or "",
                    tx_store=tx_store,
                    create_block_groups=True
                )

            # Fetch FileRecords from database (already synced during project sync)
            if project_content:
                file_children = await self._process_content_files_for_children(
                    content=project_content,
                    parent_id=record_id,
                    tx_store=tx_store
                )

        # Add file children to the first BlockGroup's (description) children_records
        if file_children and blocks_container.block_groups:
            first_block_group = blocks_container.block_groups[0]
            if first_block_group.table_row_metadata:
                # Append to existing children_records
                if first_block_group.table_row_metadata.children_records:
                    first_block_group.table_row_metadata.children_records.extend(file_children)
                else:
                    first_block_group.table_row_metadata.children_records = file_children
            else:
                # Create new table_row_metadata with children_records
                first_block_group.table_row_metadata = TableRowMetadata(
                    children_records=file_children
                )

        # Add BlockGroups for external links and documents to the blocks container
        base_index = len(blocks_container.block_groups)
        for i, block_group in enumerate(link_block_groups + document_block_groups):
            block_group.index = base_index + i
            blocks_container.block_groups.append(block_group)

        # Serialize BlocksContainer to JSON bytes
        blocks_json = blocks_container.model_dump_json(indent=2)
        return blocks_json.encode('utf-8')

    async def _process_issue_blockgroups_for_streaming(self, record: Record) -> bytes:
        """
        Process issue BlockGroups for streaming by creating BlocksContainer on-demand.

        This function:
        1. Fetches issue data from Linear API
        2. Fetches related records from database (attachments, documents, files)
        3. Parses issue to BlocksContainer with Description and Thread BlockGroups
        4. Serializes BlocksContainer to JSON bytes for streaming

        Args:
            record: TicketRecord to stream

        Returns:
            bytes: Serialized BlocksContainer as JSON bytes

        Raises:
            Exception: If issue data cannot be fetched or processed
        """
        issue_id = record.external_record_id
        datasource = await self._get_fresh_datasource()
        response = await datasource.issue(id=issue_id)

        if not response.success:
            raise Exception(f"Failed to fetch issue content: {response.message}")

        issue_data = response.data.get("issue", {}) if response.data else {}
        if not issue_data:
            raise Exception(f"No issue data found for ID: {issue_id}")

        issue_weburl = issue_data.get("url")
        issue_description = issue_data.get("description", "")

        # Get attachments, documents data from API response
        attachments_data = issue_data.get("attachments", {}).get("nodes", [])
        documents_data = issue_data.get("documents", {}).get("nodes", [])

        # Fetch child records from database
        all_children: List[ChildRecord] = []
        comment_file_children_map: Dict[str, List[ChildRecord]] = {}

        async with self.data_store_provider.transaction() as tx_store:
            # Process attachments
            if attachments_data:
                attachment_children = await self._process_issue_attachments(
                    attachments_data=attachments_data,
                    issue_id=issue_id,
                    issue_node_id=record.id,
                    team_id=record.external_record_group_id or "",
                    tx_store=tx_store
                )
                all_children.extend(attachment_children)

            # Process documents
            if documents_data:
                document_children = await self._process_issue_documents(
                    documents_data=documents_data,
                    issue_id=issue_id,
                    issue_node_id=record.id,
                    team_id=record.external_record_group_id or "",
                    tx_store=tx_store
                )
                all_children.extend(document_children)

            # Process files from description (excluding images)
            if issue_description:
                file_children = await self._process_content_files_for_children(
                    content=issue_description,
                    parent_id=issue_id,
                    tx_store=tx_store
                )
                all_children.extend(file_children)

            # Process files from comment bodies and create FileRecords
            comments_data = issue_data.get("comments", {}).get("nodes", [])
            if comments_data:
                comment_file_children_map = await self._process_comment_files_for_children(
                    comments_data=comments_data,
                    issue_id=issue_id,
                    issue_node_id=record.id,
                    team_id=record.external_record_group_id or "",
                    issue_weburl=issue_weburl,
                    tx_store=tx_store
                )

        # Parse issue to BlocksContainer
        blocks_container = await self._parse_issue_to_blocks(
            issue_data=issue_data,
            weburl=issue_weburl,
            children_records=all_children if all_children else None,
            comment_file_children_map=comment_file_children_map if comment_file_children_map else None
        )

        # Serialize BlocksContainer to JSON bytes
        blocks_json = blocks_container.model_dump_json(indent=2)
        return blocks_json.encode('utf-8')

    # ==================== CONTENT STREAMING HELPERS ====================

    async def _fetch_document_content(self, document_id: str) -> str:
        """Fetch document content for streaming.
        Args:
            document_id: Linear document ID
        Returns:
            Document content (markdown format) with images converted to base64
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        # Use DataSource to get document by ID directly
        datasource = await self._get_fresh_datasource()
        response = await datasource.document(id=document_id)

        if not response.success:
            raise Exception(f"Failed to fetch document content: {response.message}")

        document_data = response.data.get("document", {}) if response.data else {}
        if not document_data:
            raise Exception(f"No document data found for ID: {document_id}")

        # Get the content field (markdown)
        content = document_data.get("content", "")
        if not content:
            return ""

        # Convert images in document content to base64
        content = await self._convert_images_to_base64_in_markdown(content)
        return content

    # ==================== ABSTRACT METHODS ====================

    async def stream_record(self, record: Record) -> StreamingResponse:
        """
        Stream record content (issue, project, attachment, document, or file).

        Handles:
        - TICKET: Stream issue BlocksContainer (description BlockGroup + thread BlockGroups with comments as BlockGroups with sub_type=COMMENT)
        - PROJECT: Stream project BlocksContainer (description, milestones, updates BlockGroups)
        - LINK: Stream attachment/link from weburl
        - WEBPAGE: Stream document content (markdown)
        - FILE: Stream file content from weburl

        Note: Comments are handled as BlockGroups (sub_type=COMMENT) within
        issue BlocksContainer, not as separate records. Each comment BlockGroup has
        requires_processing=True so Docling processes the comment content.
        """
        try:
            if not self.data_source:
                await self.init()

            if record.record_type == RecordType.PROJECT:
                # Project: Fetch and stream BlocksContainer (create on-demand, serialize to JSON)
                blocks_json_bytes = await self._process_project_blockgroups_for_streaming(record)

                return StreamingResponse(
                    iter([blocks_json_bytes]),
                    media_type=MimeTypes.BLOCKS.value,
                    headers={
                        "Content-Disposition": f'inline; filename="{record.external_record_id}_blocks.json"'
                    }
                )

            elif record.record_type == RecordType.TICKET:
                # Issue: Fetch and stream BlocksContainer (like projects)
                # Comments are handled as Block objects within issue BlocksContainer
                blocks_json_bytes = await self._process_issue_blockgroups_for_streaming(record)

                return StreamingResponse(
                    iter([blocks_json_bytes]),
                    media_type=MimeTypes.BLOCKS.value,
                    headers={
                        "Content-Disposition": f'inline; filename="{record.external_record_id}_blocks.json"'
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
                # Stream file content from external_record_id (file URL)
                if not record.external_record_id:
                    raise ValueError(f"FileRecord {record.id} missing external_record_id (file URL)")

                # Download file content and stream it with authentication
                async def file_stream() -> AsyncGenerator[bytes, None]:
                    if not self.data_source:
                        await self.init()

                    datasource = await self._get_fresh_datasource()
                    try:
                        async for chunk in datasource.download_file(record.external_record_id):
                            yield chunk
                    except Exception as e:
                        self.logger.error(f"❌ Error downloading file from {record.external_record_id}: {e}")
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

    # ==================== REINDEXING METHODS ====================

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

            # Check records at source for updates
            updated_records = []
            non_updated_records = []

            for record in record_results:
                try:
                    updated_record_data = await self._check_and_fetch_updated_record(record)
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
                reindexable_records = []
                skipped_count = 0

                for record in non_updated_records:
                    # Only reindex properly typed records (TicketRecord, ProjectRecord, etc.)
                    record_class_name = type(record).__name__
                    if record_class_name != 'Record':
                        reindexable_records.append(record)
                    else:
                        self.logger.warning(
                            f"Record {record.id} ({record.record_type}) is base Record class "
                            f"(not properly typed), skipping reindex"
                        )
                        skipped_count += 1

                if reindexable_records:
                    try:
                        await self.data_entities_processor.reindex_existing_records(reindexable_records)
                        self.logger.info(f"Published reindex events for {len(reindexable_records)} records")
                    except NotImplementedError as e:
                        self.logger.warning(
                            f"Cannot reindex records - to_kafka_record not implemented: {e}"
                        )

                if skipped_count > 0:
                    self.logger.warning(f"Skipped reindex for {skipped_count} records that are not properly typed")

        except Exception as e:
            self.logger.error(f"❌ Failed to reindex Linear records: {e}")
            raise

    async def _check_and_fetch_updated_record(
        self, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch record from source and return data for reindexing.

        Args:
            record: Record to check

        Returns:
            Tuple of (Record, List[Permission]) if updated, None if not updated or error
        """
        try:
            # Handle ProjectRecord
            if record.record_type == RecordType.PROJECT:
                return await self._check_and_fetch_updated_project(record)

            # Handle TicketRecord: only issues now
            elif record.record_type == RecordType.TICKET:
                return await self._check_and_fetch_updated_issue(record)

            # Note: Comments are handled as blocks within issues, not as separate records

            # Handle WebpageRecord: only one type (documents)
            elif record.record_type == RecordType.WEBPAGE:
                return await self._check_and_fetch_updated_document(record)

            # Handle LinkRecord: can be from issue attachments or project external links
            elif record.record_type == RecordType.LINK:
                # Check parent to determine source
                parent_external_id = record.parent_external_record_id
                if not parent_external_id:
                    self.logger.warning(f"LinkRecord {record.external_record_id} has no parent, cannot determine source")
                    return None

                # Check if parent is a project by looking up parent record's record_type
                try:
                    async with self.data_store_provider.transaction() as tx_store:
                        parent_record = await tx_store.get_record_by_external_id(
                            connector_id=self.connector_id,
                            external_id=parent_external_id
                        )
                        if parent_record:
                            if parent_record.record_type == RecordType.PROJECT:
                                # Project external link - check parent project
                                return await self._check_and_fetch_updated_project_link(record, parent_record)
                            else:
                                # Issue attachment - check parent issue
                                return await self._check_and_fetch_updated_issue_link(record, parent_record)
                        else:
                            self.logger.warning(f"LinkRecord {record.external_record_id} parent {parent_external_id} not found")
                            return None
                except Exception as e:
                    self.logger.debug(f"Could not determine LinkRecord source: {e}")
                    return None

            # Handle FileRecord: can be from issue descriptions, comment bodies, or project descriptions
            elif record.record_type == RecordType.FILE:
                # They don't change independently, so return None (no update needed)
                self.logger.debug(f"FileRecord {record.external_record_id} reindex: no update needed (handled by parent reindex)")
                return None

            else:
                self.logger.warning(f"Unsupported record type for reindex: {record.record_type}")
                return None

        except Exception as e:
            self.logger.error(f"Error checking record {record.id} at source: {e}")
            return None

    async def _check_and_fetch_updated_issue(
        self, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch issue from source for reindexing."""
        try:
            issue_id = record.external_record_id

            # Fetch issue from source
            datasource = await self._get_fresh_datasource()
            response = await datasource.issue(id=issue_id)

            if not response.success:
                self.logger.warning(f"Issue {issue_id} not found at source: {response.message}")
                return None

            issue_data = response.data.get("issue", {}) if response.data else {}
            if not issue_data:
                self.logger.warning(f"No issue data found for {issue_id}")
                return None

            # Check if updated timestamp changed
            current_updated_at = None
            updated_at_str = issue_data.get("updatedAt")
            if updated_at_str:
                current_updated_at = self._parse_linear_datetime(updated_at_str)

            # Compare with stored timestamp
            if hasattr(record, 'source_updated_at') and record.source_updated_at and current_updated_at:
                if record.source_updated_at == current_updated_at:
                    self.logger.debug(f"Issue {issue_id} has not changed at source")
                    return None

            self.logger.info(f"Issue {issue_id} has changed at source")

            # Get team_id from external_record_group_id
            team_id = record.external_record_group_id or ""
            if not team_id:
                self.logger.warning(f"Issue {issue_id} has no team_id, cannot reindex")
                return None

            # Transform issue to ticket record
            ticket_record = self._transform_issue_to_ticket_record(issue_data, team_id, record)

            # Extract permissions (empty list for now, Linear doesn't have explicit permissions)
            permissions = []

            return (ticket_record, permissions)

        except Exception as e:
            self.logger.error(f"Error checking issue {record.id} at source: {e}")
            return None

    async def _check_and_fetch_updated_project(
        self, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """
        Fetch project from source for reindexing.

        Note: Projects use lazy block creation - blocks are created only during streaming,
        not during sync/reindexing. This method only updates the project record metadata.
        """
        try:
            project_id = record.external_record_id

            # Fetch project from source
            datasource = await self._get_fresh_datasource()
            response = await datasource.project(id=project_id)

            if not response.success:
                self.logger.warning(f"Project {project_id} not found at source: {response.message}")
                return None

            project_data = response.data.get("project", {}) if response.data else {}
            if not project_data:
                self.logger.warning(f"No project data found for {project_id}")
                return None

            # Check if updated timestamp changed
            current_updated_at = None
            updated_at_str = project_data.get("updatedAt")
            if updated_at_str:
                current_updated_at = self._parse_linear_datetime(updated_at_str)

            # Compare with stored timestamp
            if hasattr(record, 'source_updated_at') and record.source_updated_at and current_updated_at:
                if record.source_updated_at == current_updated_at:
                    self.logger.debug(f"Project {project_id} has not changed at source")
                    return None

            self.logger.info(f"Project {project_id} has changed at source")

            # Get team_id from external_record_group_id
            team_id = record.external_record_group_id or ""
            if not team_id:
                self.logger.warning(f"Project {project_id} has no team_id, cannot reindex")
                return None

            # Transform project to ProjectRecord (without BlocksContainer - created only during streaming)
            project_record = self._transform_to_project_record(
                project_data, team_id, record
            )

            # Set indexing status based on filters
            if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.PROJECTS):
                project_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

            # Extract issues data directly from project_data and add to related_external_records
            issues_data = project_data.get("issues", {}).get("nodes", [])
            if issues_data:
                from app.models.entities import RelatedExternalRecord
                project_record.related_external_records = [
                    RelatedExternalRecord(
                        external_record_id=issue.get("id"),
                        record_type=RecordType.TICKET,
                        relation_type=RecordRelations.LINKED_TO
                    )
                    for issue in issues_data
                    if issue.get("id")
                ]

            # Records inherit permissions from RecordGroup (team), so pass empty list
            permissions = []

            return (project_record, permissions)

        except Exception as e:
            self.logger.error(f"Error checking project {record.id} at source: {e}")
            return None

    async def _check_and_fetch_updated_document(
        self, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch document from source for reindexing."""
        try:
            document_id = record.external_record_id
            if not document_id:
                self.logger.warning(f"Document record {record.id} has no external_record_id")
                return None

            # Fetch document from Linear API
            datasource = await self._get_fresh_datasource()
            response = await datasource.document(id=document_id)

            if not response.success:
                self.logger.warning(f"Document {document_id} not found at source: {response.message}")
                return None

            document_data = response.data.get("document", {}) if response.data else {}
            if not document_data:
                return None

            # Check if document has been updated
            updated_at = self._parse_linear_datetime(document_data.get("updatedAt", ""))
            if not updated_at:
                return None

            # Compare with existing record's source_updated_at
            if hasattr(record, 'source_updated_at') and record.source_updated_at and updated_at:
                if record.source_updated_at == updated_at:
                    # Document hasn't changed
                    return None

            # Check if document is associated with an issue or a project
            issue_data = document_data.get("issue", {})
            project_data = document_data.get("project", {})

            # Determine parent type and ID (one of them must be present)
            parent_external_id = None

            if project_data:
                # Document is associated with a project
                parent_external_id = project_data.get("id")
            elif issue_data:
                # Document is associated with an issue
                parent_external_id = issue_data.get("id")
            else:
                # Neither issue nor project found - this should not happen
                self.logger.warning(f"Document {document_id} has neither issue nor project, cannot transform properly")
                return None

            if not parent_external_id:
                self.logger.warning(f"Document {document_id} has no parent ID, cannot transform properly")
                return None

            team_id = record.external_record_group_id or ""
            parent_node_id = record.parent_node_id

            if not parent_node_id:
                self.logger.warning(f"Document {document_id} has no parent_node_id, cannot transform properly")
                return None

            # Transform document to WebpageRecord
            webpage_record = self._transform_document_to_webpage_record(
                document_data=document_data,
                issue_id=parent_external_id,
                parent_node_id=parent_node_id,
                team_id=team_id,
                existing_record=record
            )

            permissions = []
            return (webpage_record, permissions)

        except Exception as e:
            self.logger.error(f"Error checking document {record.id} at source: {e}")
            return None

    async def _check_and_fetch_updated_issue_link(
        self, record: Record, parent_record: Optional[Record] = None
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch issue attachment/link from source for reindexing."""
        try:
            attachment_id = record.external_record_id
            if not attachment_id:
                self.logger.warning(f"LinkRecord {record.id} has no external_record_id")
                return None

            # Fetch attachment from Linear API
            datasource = await self._get_fresh_datasource()
            response = await datasource.attachment(id=attachment_id)

            if not response.success:
                self.logger.warning(f"Attachment {attachment_id} not found at source: {response.message}")
                return None

            attachment_data = response.data.get("attachment", {}) if response.data else {}
            if not attachment_data:
                return None

            # Check if attachment has been updated
            updated_at = self._parse_linear_datetime(attachment_data.get("updatedAt", ""))
            if not updated_at:
                return None

            # Compare with existing record's source_updated_at
            if hasattr(record, 'source_updated_at') and record.source_updated_at and updated_at:
                if record.source_updated_at == updated_at:
                    # Attachment hasn't changed
                    return None

            # Attachment has changed, transform and return
            # Get issue_id from attachment_data first, fallback to record
            issue_data = attachment_data.get("issue", {})
            issue_id = issue_data.get("id", record.parent_external_record_id or "")
            team_id = record.external_record_group_id or ""

            # Get parent node ID - prefer from parent_record if provided, otherwise use record's parent_node_id
            parent_node_id = None
            if parent_record:
                parent_node_id = parent_record.id
            elif record.parent_node_id:
                parent_node_id = record.parent_node_id

            if not issue_id:
                self.logger.warning(f"Attachment {attachment_id} has no issue_id, cannot transform properly")
                return None

            if not parent_node_id:
                self.logger.warning(f"Attachment {attachment_id} has no parent_node_id, cannot transform properly")
                return None

            # Transform attachment to LinkRecord
            link_record = self._transform_attachment_to_link_record(
                attachment_data=attachment_data,
                issue_id=issue_id,
                parent_node_id=parent_node_id,
                team_id=team_id,
                existing_record=record
            )

            # Look up related record by weburl
            if link_record.weburl:
                try:
                    async with self.data_store_provider.transaction() as tx_store:
                        related_record = await tx_store.get_record_by_weburl(
                            link_record.weburl,
                            org_id=self.data_entities_processor.org_id
                        )
                        if related_record:
                            link_record.linked_record_id = related_record.id
                            self.logger.debug(f"🔗 Found related record {related_record.id} for attachment URL: {link_record.weburl}")
                except Exception as e:
                    self.logger.debug(f"⚠️ Could not fetch related record for URL {link_record.weburl}: {e}")

            permissions = []
            return (link_record, permissions)

        except Exception as e:
            self.logger.error(f"Error checking attachment {record.id} at source: {e}")
            return None

    async def _check_and_fetch_updated_project_link(
        self, record: Record, parent_record: Optional[Record] = None
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch project external link from source for reindexing."""
        try:
            link_id = record.external_record_id
            if not link_id:
                self.logger.warning(f"LinkRecord {record.id} has no external_record_id")
                return None

            # Get project_id from parent_record if available, otherwise from record
            project_id = None
            if parent_record:
                project_id = parent_record.external_record_id
            else:
                project_id = record.parent_external_record_id or ""

            if not project_id:
                self.logger.warning(f"External link {link_id} has no project_id")
                return None

            # Fetch project with external links from Linear API
            datasource = await self._get_fresh_datasource()
            response = await datasource.project(id=project_id)

            if not response.success:
                self.logger.warning(f"Project {project_id} not found at source: {response.message}")
                return None

            project_data = response.data.get("project", {}) if response.data else {}
            if not project_data:
                return None

            # Find the specific external link by ID in project's externalLinks
            external_links = project_data.get("externalLinks", {}).get("nodes", [])
            link_data = None
            for link in external_links:
                if link.get("id") == link_id:
                    link_data = link
                    break

            if not link_data:
                self.logger.warning(f"External link {link_id} not found in project {project_id}")
                return None

            # Check if link has been updated
            updated_at = self._parse_linear_datetime(link_data.get("updatedAt", ""))
            if not updated_at:
                return None

            # Compare with existing record's source_updated_at
            if hasattr(record, 'source_updated_at') and record.source_updated_at and updated_at:
                if record.source_updated_at == updated_at:
                    # Link hasn't changed
                    return None

            # Link has changed, transform and return
            # project_id already set above
            team_id = record.external_record_group_id or ""

            # Get parent node ID - prefer from parent_record if provided, otherwise use record's parent_node_id
            parent_node_id = None
            if parent_record:
                parent_node_id = parent_record.id
            elif record.parent_node_id:
                parent_node_id = record.parent_node_id

            if not parent_node_id:
                self.logger.warning(f"External link {link_id} has no parent_node_id, cannot transform properly")
                return None

            # Transform external link to LinkRecord
            link_record = self._transform_attachment_to_link_record(
                attachment_data=link_data,
                issue_id=project_id,  # Use project_id as parent
                parent_node_id=parent_node_id,
                team_id=team_id,
                existing_record=record
            )

            # Look up related record by weburl
            if link_record.weburl:
                try:
                    async with self.data_store_provider.transaction() as tx_store:
                        related_record = await tx_store.get_record_by_weburl(
                            link_record.weburl,
                            org_id=self.data_entities_processor.org_id
                        )
                        if related_record:
                            link_record.linked_record_id = related_record.id
                            self.logger.debug(f"🔗 Found related record {related_record.id} for external link URL: {link_record.weburl}")
                except Exception as e:
                    self.logger.debug(f"⚠️ Could not fetch related record for URL {link_record.weburl}: {e}")

            permissions = []
            return (link_record, permissions)

        except Exception as e:
            self.logger.error(f"Error checking external link {record.id} at source: {e}")
            return None

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
