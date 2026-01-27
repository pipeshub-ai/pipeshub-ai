"""Zammad Connector Implementation"""
from collections import defaultdict
from datetime import datetime, timezone
from logging import Logger
from typing import (
    Any,
    AsyncGenerator,
    Dict,
    List,
    Optional,
    Tuple,
)
from uuid import uuid4

from fastapi.responses import StreamingResponse
from html_to_markdown import convert as html_to_markdown  # type: ignore[import-untyped]

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    AppGroups,
    Connectors,
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
    FilterOption,
    FilterOptionsResponse,
    FilterType,
    IndexingFilterKey,
    OptionSourceType,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.sources.zammad.common.apps import ZammadApp
from app.connectors.utils.value_mapper import ValueMapper
from app.models.blocks import (
    Block,
    BlockContainerIndex,
    BlockGroup,
    BlocksContainer,
    ChildRecord,
    ChildType,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.models.entities import (
    AppRole,
    AppUser,
    AppUserGroup,
    FileRecord,
    IndexingStatus,
    ItemType,
    MimeTypes,
    OriginTypes,
    Priority,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    Status,
    TicketRecord,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.zammad.zammad import (
    ZammadClient,
)
from app.sources.external.zammad.zammad import ZammadDataSource
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# Config path for Zammad connector
ZAMMAD_CONFIG_PATH = "/services/connectors/{connector_id}/config"

# Constants for batch processing and parsing
BATCH_SIZE_KB_ANSWERS = 50
ATTACHMENT_ID_PARTS_COUNT = 3

@ConnectorBuilder("Zammad")\
    .in_group(AppGroups.ZAMMAD.value)\
    .with_description("Sync tickets, articles, knowledge base, and users from Zammad")\
    .with_categories(["Help Desk", "Knowledge Base"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.API_TOKEN).fields([
            AuthField(
                name="baseUrl",
                display_name="Zammad Instance URL",
                placeholder="https://your-instance.zammad.com",
                description="The base URL of your Zammad instance",
                field_type="TEXT",
                max_length=2000
            ),
            AuthField(
                name="token",
                display_name="Access Token",
                placeholder="Enter your Zammad Access Token",
                description="Access Token from Zammad (Profile → Token Access)",
                field_type="PASSWORD",
                max_length=2000,
                is_secret=True
            )
        ])
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/zammad.svg")
        .with_realtime_support(False)
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/zammad/zammad',
            'pipeshub'
        ))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(False)
        .add_filter_field(FilterField(
            name="group_ids",
            display_name="Groups",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            description="Filter tickets by group/team (leave empty for all groups)",
            option_source_type=OptionSourceType.DYNAMIC
        ))
        .add_filter_field(CommonFields.modified_date_filter("Filter tickets by modification date."))
        .add_filter_field(CommonFields.created_date_filter("Filter tickets by creation date."))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .add_filter_field(FilterField(
            name="tickets",
            display_name="Index Tickets",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of tickets",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="issue_attachments",
            display_name="Index Ticket Attachments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of ticket attachments",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="knowledge_base",
            display_name="Index Knowledge Base",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of knowledge base articles",
            default_value=True
        ))
    )\
    .build_decorator()
class ZammadConnector(BaseConnector):
    """
    Zammad connector for syncing tickets, articles, knowledge base, and users from Zammad
    """
    # ==================== INITIALIZATION ====================

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str
    ) -> None:
        super().__init__(
            ZammadApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id
        )
        self.external_client: Optional[ZammadClient] = None
        self.data_source: Optional[ZammadDataSource] = None
        self.base_url: Optional[str] = None
        self.connector_id = connector_id
        self.connector_name = Connectors.ZAMMAD

        # Initialize value mapper (Zammad-specific mappings are in ValueMapper defaults)
        self.value_mapper = ValueMapper()

        # Initialize sync points
        org_id = self.data_entities_processor.org_id

        self.tickets_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=data_store_provider
        )
        self.kb_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=data_store_provider
        )

        # Cache for lookups
        self._state_map: Dict[int, str] = {}  # state_id -> state_name
        self._priority_map: Dict[int, str] = {}  # priority_id -> priority_name
        self._user_id_to_data: Dict[int, Dict[str, Any]] = {}  # user_id -> {"email": str, "role_ids": List[int]} (lightweight mapping)

        # Filter collections (initialized in run_sync)
        self.sync_filters: Any = None
        self.indexing_filters: Any = None

    async def init(self) -> bool:
        """
        Initialize Zammad client using proper Client + DataSource architecture.
        Note: Actual initialization happens lazily in _get_fresh_datasource().
        This method satisfies the abstract method requirement.
        """
        try:
            # Initialize client lazily - actual work done in _get_fresh_datasource()
            await self._get_fresh_datasource()
            self.logger.info(f"✅ Zammad connector {self.connector_id} initialized successfully")
            return True
        except Exception as e:
            self.logger.error(f"❌ Failed to initialize Zammad connector: {e}", exc_info=True)
            return False

    async def _get_fresh_datasource(self) -> ZammadDataSource:
        """
        Get ZammadDataSource with ALWAYS-FRESH access token.

        This method:
        1. Initializes client if not already initialized
        2. Fetches current token from config (API_TOKEN)
        3. Compares with existing client's token
        4. Updates client ONLY if token changed (mutation)
        5. Returns datasource with current token

        Returns:
            ZammadDataSource with current valid token
        """
        # Initialize client if not already done
        if not self.external_client:
            self.logger.info(f"🔧 Initializing Zammad client for connector {self.connector_id}")

            # Use ZammadClient.build_from_services() to create client with proper auth
            client = await ZammadClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id
            )

            # Store client for future use
            self.external_client = client

            # Create DataSource from client
            self.data_source = ZammadDataSource(client)

            # Get base URL from client
            self.base_url = client.get_base_url()

            # Load state and priority mappings (only once on first initialization)
            await self._load_lookup_tables()

            self.logger.info("✅ Zammad client initialized successfully")
            return self.data_source

        # Fetch current config from etcd (async I/O)
        config = await self.config_service.get_config(f"/services/connectors/{self.connector_id}/config")

        if not config:
            raise Exception("Zammad configuration not found")

        # Extract fresh token (API_TOKEN only)
        auth_config = config.get("auth", {})
        auth_type = auth_config.get("authType", "API_TOKEN")

        # Only support API_TOKEN authentication
        # Check against AuthType enum for type safety, but also allow "TOKEN" for backward compatibility
        if auth_type != AuthType.API_TOKEN and auth_type != "TOKEN":
            raise ValueError(f"Unsupported auth type: {auth_type}. Only API_TOKEN is supported.")

        fresh_token = auth_config.get("token", "")

        if not fresh_token:
            raise Exception("No access token available")

        # Get current token from client
        internal_client = self.external_client.get_client()

        # For Zammad, we need to check if token changed and rebuild client if needed
        # Since Zammad clients don't have a set_token method, we rebuild the client
        current_token = None
        if hasattr(internal_client, 'token'):
            current_token = internal_client.token

        # Update client's token if it changed (mutation)
        if current_token != fresh_token:
            self.logger.debug("🔄 Updating client with refreshed access token")
            # Rebuild client with fresh token
            client = await ZammadClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id
            )
            self.external_client = client
            self.data_source = ZammadDataSource(client)
            self.base_url = client.get_base_url()

        # Return datasource with updated client
        return self.data_source

    async def _load_lookup_tables(self) -> None:
        """Load state and priority lookup tables from Zammad"""
        if not self.data_source:
            return

        # Load ticket states
        try:
            states_response = await self.data_source.list_ticket_states()
            if states_response.success and states_response.data:
                for state in states_response.data:
                    state_id = state.get("id")
                    state_name = state.get("name", "")
                    # Only cache if both ID and name are present
                    if state_id is not None and state_name:
                        self._state_map[state_id] = state_name.lower()
                self.logger.info(f"📊 Loaded {len(self._state_map)} ticket states")
        except Exception as e:
            self.logger.warning(f"Failed to load ticket states: {e}")

        # Load ticket priorities
        try:
            priorities_response = await self.data_source.list_ticket_priorities()
            if priorities_response.success and priorities_response.data:
                for priority in priorities_response.data:
                    priority_id = priority.get("id")
                    priority_name = priority.get("name", "")
                    # Only cache if both ID and name are present
                    if priority_id is not None and priority_name:
                        self._priority_map[priority_id] = priority_name.lower()
                self.logger.info(f"📊 Loaded {len(self._priority_map)} ticket priorities")
        except Exception as e:
            self.logger.warning(f"Failed to load ticket priorities: {e}")

    # ==================== MAIN SYNC ORCHESTRATION ====================

    async def run_sync(self) -> None:
        """Main sync orchestration method"""
        self.logger.info(f"🔄 Starting Zammad sync for connector {self.connector_id}")

        try:
            # Ensure initialized (will be done lazily in _get_fresh_datasource if needed)
            await self._get_fresh_datasource()

            # Load filters
            sync_filters, indexing_filters = await load_connector_filters(
                self.config_service,
                "zammad",
                self.connector_id,
                self.logger
            )
            # Store filters for use in sync methods
            self.sync_filters = sync_filters
            self.indexing_filters = indexing_filters

            # Step 1: Fetch and sync users
            self.logger.info("👤 Step 1: Syncing users...")
            users, user_email_map = await self._fetch_users()
            if users:
                await self.data_entities_processor.on_new_app_users(users)
                self.logger.info(f"✅ Synced {len(users)} users")

            # Step 2: Fetch and sync groups (creates BOTH RecordGroups AND UserGroups)
            self.logger.info("👥 Step 2: Syncing groups...")
            group_record_groups, group_user_groups = await self._fetch_groups(user_email_map)

            # IMPORTANT: Sync UserGroups BEFORE RecordGroups!
            # so UserGroups must exist first for permission edges to be created.
            if group_user_groups:
                await self.data_entities_processor.on_new_user_groups(group_user_groups)
                self.logger.info(f"✅ Synced {len(group_user_groups)} groups as UserGroups")

            if group_record_groups:
                await self.data_entities_processor.on_new_record_groups(group_record_groups)
                self.logger.info(f"✅ Synced {len(group_record_groups)} groups as RecordGroups")

            # Step 3: Fetch and sync roles
            self.logger.info("🎭 Step 3: Syncing roles...")
            await self._sync_roles(users, user_email_map)

            # Step 5: Sync tickets (linked to group RecordGroups via group_id)
            self.logger.info("🎫 Step 5: Syncing tickets...")
            await self._sync_tickets_for_groups(group_record_groups)

            # Step 6: Sync knowledge base (always fetch, indexing filters control indexing_status)
            # self.logger.info("📚 Step 7: Syncing knowledge base...")
            # await self._sync_knowledge_bases()

            self.logger.info(f"✅ Zammad sync completed for connector {self.connector_id}")

        except Exception as e:
            self.logger.error(f"❌ Zammad sync failed: {e}", exc_info=True)
            raise

    def _filter_groups_by_sync_filter(
        self,
        all_groups: List[Tuple[RecordGroup, List[Permission]]]
    ) -> List[Tuple[RecordGroup, List[Permission]]]:
        """
        Apply group_ids sync filter to determine which groups to process.

        Args:
            all_groups: List of all (RecordGroup, permissions) tuples

        Returns:
            Filtered list of groups to process
        """
        # Check if filter is set
        if not self.sync_filters:
            return all_groups  # No filter, process all

        group_ids_filter = self.sync_filters.get(SyncFilterKey.GROUP_IDS)
        if not group_ids_filter:
            return all_groups  # No group filter, process all

        selected_group_ids = group_ids_filter.get_value(default=[])
        if not selected_group_ids:
            return all_groups  # Empty filter, process all

        # Convert to set of strings for easy lookup
        filter_set = set(str(gid) for gid in selected_group_ids)

        # Check operator: "in" (include) or "not_in" (exclude)
        group_ids_operator = group_ids_filter.get_operator()
        operator_value = "in"
        if group_ids_operator:
            operator_value = group_ids_operator.value if hasattr(group_ids_operator, 'value') else str(group_ids_operator)

        is_exclude = operator_value == "not_in"

        filtered_groups = []
        for group_record_group, group_perms in all_groups:
            # Extract group_id from external_group_id (e.g., "group_1" -> "1")
            group_id = group_record_group.short_name or group_record_group.external_group_id.replace("group_", "")

            if is_exclude:
                # NOT_IN: include if NOT in filter list
                if group_id not in filter_set:
                    filtered_groups.append((group_record_group, group_perms))
            else:
                # IN: include if IN filter list
                if group_id in filter_set:
                    filtered_groups.append((group_record_group, group_perms))

        self.logger.debug(f"📋 Filtered groups: {len(filtered_groups)}/{len(all_groups)} (filter: {operator_value} {list(filter_set)})")
        return filtered_groups

    # ==================== ENTITY FETCHING & SYNCING ====================

    async def _fetch_users(self) -> Tuple[List[AppUser], Dict[str, AppUser]]:
        """
        Fetch all users from Zammad with pagination.

        Returns:
            Tuple of (list of AppUser objects, email -> AppUser map)
        """
        users: List[AppUser] = []
        user_email_map: Dict[str, AppUser] = {}

        datasource = await self._get_fresh_datasource()
        page = 1
        per_page = 100

        while True:
            response = await datasource.list_users(page=page, per_page=per_page)

            if not response.success or not response.data:
                break

            users_data = response.data
            if not isinstance(users_data, list):
                users_data = [users_data]

            if not users_data:
                self.logger.debug(f"Empty users list for page {page}")
                break

            self.logger.debug(f"Fetched {len(users_data)} users from page {page}")

            for user_data in users_data:
                user_id = user_data.get("id")
                email = user_data.get("email", "")
                active = user_data.get("active", True)

                # Skip inactive users, users without email, or users without ID
                if not active or not email or not user_id:
                    continue

                # Store lightweight mapping: user_id -> {email, role_ids} (instead of full user data)
                role_ids = user_data.get("role_ids", [])
                role_ids_int = []
                if role_ids:
                    # Convert to list of integers
                    for role_id in role_ids:
                        if isinstance(role_id, int):
                            role_ids_int.append(role_id)
                        elif isinstance(role_id, str) and role_id.isdigit():
                            role_ids_int.append(int(role_id))

                self._user_id_to_data[user_id] = {
                    "email": email.lower(),
                    "role_ids": role_ids_int
                }

                # Build full name
                firstname = user_data.get("firstname", "") or ""
                lastname = user_data.get("lastname", "") or ""
                full_name = f"{firstname} {lastname}".strip() or email

                # Create AppUser
                app_user = AppUser(
                    id=str(uuid4()),
                    org_id=self.data_entities_processor.org_id,
                    source_user_id=str(user_id),
                    connector_id=self.connector_id,
                    app_name=Connectors.ZAMMAD,
                    email=email,
                    full_name=full_name,
                    is_active=active,
                )

                users.append(app_user)
                user_email_map[email.lower()] = app_user

            # Check if we got less than per_page, meaning we're done
            if len(users_data) < per_page:
                break

            page += 1

        self.logger.info(f"📥 Fetched {len(users)} users from Zammad")
        return users, user_email_map

    async def _fetch_groups(
        self,
        user_email_map: Dict[str, AppUser]
    ) -> Tuple[List[Tuple[RecordGroup, List[Permission]]], List[Tuple[AppUserGroup, List[AppUser]]]]:
        """
        Fetch Zammad groups and create BOTH RecordGroups AND UserGroups.
        Groups are the permission boundary for tickets in Zammad.

        Args:
            user_email_map: Map of email -> AppUser for membership tracking

        Returns:
            Tuple of (record_groups, user_groups):
            - record_groups: List of (RecordGroup, permissions) tuples
            - user_groups: List of (UserGroup, members) tuples
        """
        record_groups: List[Tuple[RecordGroup, List[Permission]]] = []
        user_groups: List[Tuple[AppUserGroup, List[AppUser]]] = []

        datasource = await self._get_fresh_datasource()
        page = 1
        per_page = 100

        while True:
            response = await datasource.list_groups(page=page, per_page=per_page)

            if not response.success or not response.data:
                if page == 1:
                    self.logger.warning("Failed to fetch groups from Zammad")
                break

            groups_data = response.data
            if not isinstance(groups_data, list):
                groups_data = [groups_data]

            if not groups_data:
                break

            for group_data in groups_data:
                group_id = group_data.get("id")
                group_name = group_data.get("name", "")
                active = group_data.get("active", True)

                if not active or not group_id or not group_name:
                    continue

                # Parse timestamps
                created_at = self._parse_zammad_datetime(group_data.get("created_at", ""))
                updated_at = self._parse_zammad_datetime(group_data.get("updated_at", ""))

                # Build web URL for group
                web_url = None
                if self.base_url:
                    web_url = f"{self.base_url}/#manage/groups/{group_id}"

                # 1. Create RecordGroup for permission inheritance
                record_group = RecordGroup(
                    id=str(uuid4()),
                    org_id=self.data_entities_processor.org_id,
                    external_group_id=f"group_{group_id}",
                    connector_id=self.connector_id,
                    connector_name=self.connector_name,
                    name=group_name,
                    short_name=str(group_id),
                    group_type=RecordGroupType.PROJECT,
                    web_url=web_url,
                    source_created_at=created_at if created_at else None,
                    source_updated_at=updated_at if updated_at else None,
                )

                # Permission: UserGroup -> RecordGroup (group members can access)
                permissions: List[Permission] = [
                    Permission(
                        entity_type=EntityType.GROUP,
                        type=PermissionType.READ,
                        external_id=str(group_id)  # Links to UserGroup.source_user_group_id
                    )
                ]

                record_groups.append((record_group, permissions))

                # 2. Create AppUserGroup for membership tracking
                user_group = AppUserGroup(
                    id=str(uuid4()),
                    org_id=self.data_entities_processor.org_id,
                    source_user_group_id=str(group_id),
                    connector_id=self.connector_id,
                    app_name=Connectors.ZAMMAD,
                    name=group_name,
                    description=group_data.get("note", ""),
                )

                # Get users assigned to this group by calling get_group API
                member_app_users: List[AppUser] = []
                try:
                    group_detail_response = await datasource.get_group(group_id)
                    if group_detail_response.success and group_detail_response.data:
                        group_detail = group_detail_response.data
                        # Zammad API returns user_ids or member_ids in group detail
                        member_user_ids = group_detail.get("user_ids", [])

                        for member_user_id in member_user_ids:
                            # Convert to int if needed
                            if isinstance(member_user_id, str) and member_user_id.isdigit():
                                member_user_id = int(member_user_id)

                            # Get email from lightweight mapping
                            user_data = self._user_id_to_data.get(member_user_id)
                            if user_data:
                                email = user_data.get("email")
                                if email and email in user_email_map:
                                    member_app_users.append(user_email_map[email])
                except Exception as e:
                    self.logger.warning(f"Failed to fetch group members for group {group_id}: {e}")

                user_groups.append((user_group, member_app_users))

            # Check if we got less than per_page, meaning we're done
            if len(groups_data) < per_page:
                break

            page += 1

        self.logger.info(
            f"📥 Fetched {len(user_groups)} groups, "
            f"created {len(record_groups)} RecordGroups and {len(user_groups)} UserGroups"
        )
        return (record_groups, user_groups)

    async def _sync_roles(
        self,
        users: List[AppUser],
        user_email_map: Dict[str, AppUser]
    ) -> None:
        """Fetch and sync roles from Zammad as AppRoles with user mappings."""
        try:
            datasource = await self._get_fresh_datasource()
            page = 1
            per_page = 100
            app_roles: List[Tuple[AppRole, List[AppUser]]] = []

            while True:
                response = await datasource.list_roles(page=page, per_page=per_page)

                if not response.success:
                    if page == 1:
                        self.logger.warning(f"Failed to fetch roles: {response.message if hasattr(response, 'message') else 'Unknown error'}")
                    break

                if not response.data:
                    break

                roles_data = response.data
                if not isinstance(roles_data, list):
                    roles_data = [roles_data]

                if not roles_data:
                    break

                # Process each role directly in the loop (like groups)
                for role_data in roles_data:
                    role_id = role_data.get("id")
                    if not role_id:
                        continue

                    role_name = role_data.get("name", f"Role {role_id}")
                    active = role_data.get("active", True)

                    if not active:
                        continue

                    # Parse timestamps
                    created_at = self._parse_zammad_datetime(role_data.get("created_at", ""))
                    updated_at = self._parse_zammad_datetime(role_data.get("updated_at", ""))

                    # Get users assigned to this role by checking user_id_to_data mapping
                    role_users: List[AppUser] = []
                    for user_id, user_data in self._user_id_to_data.items():
                        user_role_ids = user_data.get("role_ids", [])
                        if role_id in user_role_ids:
                            # Get email from lightweight mapping
                            email = user_data.get("email")
                            if email and email in user_email_map:
                                role_users.append(user_email_map[email])

                    # Create AppRole
                    app_role = AppRole(
                        id=str(uuid4()),
                        org_id=self.data_entities_processor.org_id,
                        source_role_id=str(role_id),
                        connector_id=self.connector_id,
                        app_name=Connectors.ZAMMAD,
                        name=role_name,
                        source_created_at=created_at if created_at else None,
                        source_updated_at=updated_at if updated_at else None,
                    )

                    app_roles.append((app_role, role_users))

                # Check if we got less than per_page, meaning we're done
                if len(roles_data) < per_page:
                    break

                page += 1

            # Sync roles
            if app_roles:
                await self.data_entities_processor.on_new_app_roles(app_roles)
                self.logger.info(f"✅ Synced {len(app_roles)} roles")
            else:
                self.logger.info("No roles found")

        except Exception as e:
            self.logger.error(f"❌ Error syncing roles: {e}", exc_info=True)
            raise

    # ==================== TICKET SYNCING ====================

    async def _sync_tickets_for_groups(
        self,
        group_record_groups: List[Tuple[RecordGroup, List[Permission]]]
    ) -> None:
        """
        Sync tickets per group with group-level sync points.
        Similar to Linear's per-team sync pattern.

        Args:
            group_record_groups: List of (RecordGroup, permissions) tuples from Step 2
        """
        if not group_record_groups:
            self.logger.info("ℹ️ No groups to sync tickets for")
            return

        # Apply filter to get groups to process
        groups_to_process = self._filter_groups_by_sync_filter(group_record_groups)

        if not groups_to_process:
            self.logger.info("ℹ️ No groups match the filter criteria")
            return

        self.logger.info(f"📋 Will sync tickets for {len(groups_to_process)} groups")

        total_records_all_groups = 0

        # Sync each group independently
        for group_record_group, group_perms in groups_to_process:
            try:
                # Extract group info
                external_group_id = group_record_group.external_group_id
                group_id = external_group_id.replace("group_", "") if external_group_id else None
                group_name = group_record_group.name

                if not group_id:
                    self.logger.warning(f"⚠️ Skipping group {group_name}: missing group_id")
                    continue

                self.logger.info(f"📋 Starting ticket sync for group: {group_name}")

                # Read group-level sync point (using group name as key)
                last_sync_time = await self._get_group_sync_checkpoint(group_name)

                if last_sync_time:
                    self.logger.info(f"🔄 Incremental sync for group {group_name} from {last_sync_time}")
                else:
                    self.logger.info(f"🔄 Full sync for group {group_name} (first time)")

                # Fetch and process tickets for this group only
                total_records = 0
                max_ticket_updated_at: Optional[int] = None

                async for batch_records in self._fetch_tickets_for_group_batch(
                    group_id=int(group_id),
                    group_name=group_name,
                    last_sync_time=last_sync_time
                ):
                    if not batch_records:
                        continue

                    # Track max updated_at from tickets for sync point
                    for record, _ in batch_records:
                        if isinstance(record, TicketRecord) and record.source_updated_at:
                            if max_ticket_updated_at is None or record.source_updated_at > max_ticket_updated_at:
                                max_ticket_updated_at = record.source_updated_at

                    # Process batch
                    total_records += len(batch_records)
                    await self.data_entities_processor.on_new_records(batch_records)
                    self.logger.debug(f"📝 Synced batch of {len(batch_records)} records for group {group_name}")

                    # Update sync point after each batch (fault tolerance)
                    if max_ticket_updated_at:
                        await self._update_group_sync_checkpoint(group_name, max_ticket_updated_at + 1000)

                # Final sync point update: Only update to current time if we processed tickets
                if total_records > 0:
                    # If max_ticket_updated_at wasn't set (edge case), use current time as fallback
                    if not max_ticket_updated_at:
                        self.logger.warning(f"Processed {total_records} records but max_ticket_updated_at not set, using current time")
                        await self._update_group_sync_checkpoint(group_name)
                    # else: max_ticket_updated_at was already set above, no need to update again
                else:
                    self.logger.debug(f"No tickets found for group {group_name}, keeping existing checkpoint to avoid skipping older tickets")

                total_records_all_groups += total_records
                self.logger.info(f"✅ Synced {total_records} records for group {group_name}")

            except Exception as e:
                self.logger.error(f"❌ Error syncing tickets for group {group_record_group.name}: {e}", exc_info=True)
                continue  # Continue with next group even if one fails

        self.logger.info(f"✅ Total: Synced {total_records_all_groups} records across {len(groups_to_process)} groups")

    async def _fetch_tickets_for_group_batch(
        self,
        group_id: int,
        group_name: str,
        last_sync_time: Optional[int]
    ) -> AsyncGenerator[List[Tuple[Record, List[Permission]]], None]:
        """
        Fetch tickets for a specific group with pagination and incremental sync support.

        Args:
            group_id: Zammad group ID to fetch tickets for
            group_name: Group name for logging
            last_sync_time: Last sync timestamp (epoch ms) or None for full sync

        Yields:
            Batches of (Record, permissions) tuples (includes TicketRecords and FileRecords)
        """
        datasource = await self._get_fresh_datasource()
        limit = 50
        offset = 0
        batch_size = 50

        # Build query: always filter by group_id
        query_parts = [f"group_id:{group_id}"]

        # Add timestamp filter if incremental sync
        if last_sync_time:
            # Convert timestamp to ISO format with UTC timezone
            dt = datetime.fromtimestamp(last_sync_time / 1000, tz=timezone.utc)
            iso_format = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            query_parts.append(f"updated_at:[{iso_format} TO *]")

        # Build final query
        query = " AND ".join(query_parts)
        self.logger.debug(f"Fetching tickets for group '{group_name}' with query: {query}")

        while True:
            # Use search_tickets for fetching
            response = await datasource.search_tickets(
                query=query,
                limit=limit,
                offset=offset
            )

            if not response.success:
                self.logger.warning(f"Failed to fetch tickets for group '{group_name}' (offset {offset}): {response.message if hasattr(response, 'message') else 'Unknown error'}")
                break

            if not response.data:
                self.logger.debug(f"No ticket data returned for group '{group_name}' at offset {offset}")
                break

            # Response.data is now a list of ticket objects (already extracted from assets.Ticket)
            tickets_data = response.data
            if not isinstance(tickets_data, list):
                tickets_data = [tickets_data] if tickets_data else []

            if not tickets_data:
                self.logger.debug(f"Empty tickets list for group '{group_name}' at offset {offset}")
                break

            self.logger.debug(f"Fetched {len(tickets_data)} tickets for group '{group_name}' from offset {offset}")

            batch_records: List[Tuple[Record, List[Permission]]] = []

            for ticket_data in tickets_data:
                try:
                    ticket_record = await self._transform_ticket_to_ticket_record(ticket_data)

                    if ticket_record:
                        # Set indexing status based on indexing filters
                        if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.TICKETS):
                            ticket_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                        # Records inherit permissions from RecordGroup
                        batch_records.append((ticket_record, []))

                        # Always fetch attachments (sync filter controls fetching, indexing filter controls status)
                        attachment_records = await self._fetch_ticket_attachments(
                            ticket_data,
                            ticket_record
                        )
                        batch_records.extend(attachment_records)

                except Exception as e:
                    ticket_id = ticket_data.get("id", "unknown")
                    self.logger.error(f"❌ Error processing ticket {ticket_id}: {e}", exc_info=True)
                    continue

                # Yield batch when size reached
                if len(batch_records) >= batch_size:
                    yield batch_records
                    batch_records = []

            # Yield remaining records
            if batch_records:
                yield batch_records

            # Check if we got less than limit, meaning we're done
            if len(tickets_data) < limit:
                break

            # Increment offset for next page
            offset += limit

    async def _fetch_ticket_attachments(
        self,
        ticket_data: Dict[str, Any],
        ticket_record: TicketRecord
    ) -> List[Tuple[Record, List[Permission]]]:
        """
        Fetch attachments for a ticket from its articles.

        Args:
            ticket_data: Raw ticket data
            ticket_record: Parent TicketRecord

        Returns:
            List of (FileRecord, permissions) tuples
        """
        attachments: List[Tuple[Record, List[Permission]]] = []

        ticket_id = ticket_data.get("id")
        if not ticket_id:
            return attachments

        datasource = await self._get_fresh_datasource()
        response = await datasource.list_ticket_articles(ticket_id=ticket_id)

        if not response.success or not response.data:
            return attachments

        articles = response.data
        if not isinstance(articles, list):
            articles = [articles]

        for article in articles:
            article_id = article.get("id")
            article_sender = article.get("sender", "")
            article_from = article.get("from", "")
            article_preferences = article.get("preferences", {})

            # Skip attachments from system-generated articles (auto-replies, bounce notifications, etc.)
            if article_sender == "System":
                self.logger.debug(f"Skipping attachments from system article {article_id} (sender: System)")
                continue

            # Skip auto-response emails (bounce notifications, delivery failures, etc.)
            if article_preferences.get("is-auto-response") or article_preferences.get("send-auto-response") is False:
                self.logger.debug(f"Skipping attachments from auto-response article {article_id}")
                continue

            # Skip MAILER-DAEMON bounce notifications
            if "MAILER-DAEMON" in article_from or "Mail Delivery System" in article_from:
                self.logger.debug(f"Skipping attachments from bounce notification article {article_id}")
                continue

            article_attachments = article.get("attachments", [])

            for attachment in article_attachments:
                try:
                    file_record = await self._transform_attachment_to_file_record(
                        attachment,
                        ticket_id,
                        article_id,
                        ticket_record
                    )
                    if file_record:
                        # FileRecords inherit permissions from parent
                        attachments.append((file_record, []))
                except Exception as e:
                    self.logger.warning(f"Failed to process attachment: {e}")

        return attachments

    # ==================== TRANSFORMATIONS ====================

    async def _transform_ticket_to_ticket_record(
        self,
        ticket_data: Dict[str, Any]
    ) -> Optional[TicketRecord]:
        """
        Transform Zammad ticket to TicketRecord.

        Args:
            ticket_data: Raw ticket data from Zammad API

        Returns:
            TicketRecord or None if transformation fails
        """
        ticket_id = ticket_data.get("id")
        if not ticket_id:
            return None

        # Get ticket number and title
        title = ticket_data.get("title", "")
        record_name = title

        # Groups control who can access the ticket (agent teams)
        group_id = ticket_data.get("group_id")

        # Groups use "group_{id}" format and are RecordGroupType.PROJECT
        record_group_type = None
        if group_id:
            external_record_group_id = f"group_{group_id}"
            record_group_type = RecordGroupType.PROJECT  # Groups are PROJECT type
        else:
            external_record_group_id = None  # Tickets without group won't be linked to a RecordGroup

        # Map state to Status enum using ValueMapper
        state_id = ticket_data.get("state_id")
        state_name = self._state_map.get(state_id, "")
        status = self.value_mapper.map_status(state_name)
        # Default to OPEN if value_mapper returns string (no match)
        if status and not isinstance(status, Status):
            status = Status.OPEN

        # Map priority to Priority enum using ValueMapper
        priority_id = ticket_data.get("priority_id")
        priority_name = self._priority_map.get(priority_id, "")
        priority = self.value_mapper.map_priority(priority_name)
        # Default to MEDIUM if value_mapper returns string (no match)
        if priority and not isinstance(priority, Priority):
            priority = Priority.MEDIUM

        # Get customer (creator) and owner (assignee) info - fetch on-demand
        customer_id = ticket_data.get("customer_id")
        owner_id = ticket_data.get("owner_id")
        creator_email = ""
        creator_name = ""
        assignee_email = ""
        assignee_name = ""

        # Fetch datasource once if we need to get user details
        datasource = None
        if customer_id or owner_id:
            try:
                datasource = await self._get_fresh_datasource()
            except Exception as e:
                self.logger.debug(f"Failed to get datasource for ticket user lookups: {e}")

        # Get customer (creator) info
        if customer_id:
            # Get email from lightweight mapping
            customer_user_data = self._user_id_to_data.get(customer_id, {})
            creator_email = customer_user_data.get("email", "")
            # Fetch user details on-demand if needed for name
            if creator_email and datasource:
                try:
                    user_response = await datasource.get_user(customer_id)
                    if user_response.success and user_response.data:
                        customer_detail = user_response.data
                        firstname = customer_detail.get("firstname", "") or ""
                        lastname = customer_detail.get("lastname", "") or ""
                        creator_name = f"{firstname} {lastname}".strip() or creator_email
                except Exception as e:
                    self.logger.debug(f"Failed to fetch user {customer_id} for ticket: {e}")

        # Get owner (assignee) info - reuse datasource
        if owner_id:
            # Get email from lightweight mapping
            owner_user_data = self._user_id_to_data.get(owner_id, {})
            assignee_email = owner_user_data.get("email", "")
            # Fetch user details on-demand if needed for name
            if assignee_email and datasource:
                try:
                    user_response = await datasource.get_user(owner_id)
                    if user_response.success and user_response.data:
                        owner_detail = user_response.data
                        firstname = owner_detail.get("firstname", "") or ""
                        lastname = owner_detail.get("lastname", "") or ""
                        assignee_name = f"{firstname} {lastname}".strip() or assignee_email
                except Exception as e:
                    self.logger.debug(f"Failed to fetch user {owner_id} for ticket: {e}")

        # Parse timestamps
        created_at = self._parse_zammad_datetime(ticket_data.get("created_at", ""))
        updated_at = self._parse_zammad_datetime(ticket_data.get("updated_at", ""))

        # Build web URL
        weburl = f"{self.base_url}/#ticket/zoom/{ticket_id}" if self.base_url else ""

        # Check for existing record
        existing_record = None
        async with self.data_store_provider.transaction() as tx_store:
            existing_record = await tx_store.get_record_by_external_id(
                connector_id=self.connector_id,
                external_id=str(ticket_id)
            )

        # Handle versioning
        is_new = existing_record is None
        record_id = existing_record.id if existing_record else str(uuid4())
        version = 0 if is_new else (existing_record.version + 1 if existing_record.source_updated_at != updated_at else existing_record.version)

        # Use updated_at as external_revision_id so placeholders (None) will trigger update
        external_revision_id = str(updated_at) if updated_at else None

        # Create TicketRecord
        ticket_record = TicketRecord(
            id=record_id,
            org_id=self.data_entities_processor.org_id,
            record_type=RecordType.TICKET,
            record_name=record_name,
            external_record_id=str(ticket_id),
            external_revision_id=external_revision_id,
            external_record_group_id=external_record_group_id,
            record_group_type=record_group_type,
            indexing_status=IndexingStatus.NOT_STARTED,
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
            type=ItemType.ISSUE,
            assignee=assignee_name,
            assignee_email=assignee_email,
            creator_email=creator_email,
            creator_name=creator_name,
            inherit_permissions=True,
            preview_renderable=False,
        )

        return ticket_record

    async def _transform_attachment_to_file_record(
        self,
        attachment_data: Dict[str, Any],
        ticket_id: int,
        article_id: int,
        parent_ticket: TicketRecord
    ) -> Optional[FileRecord]:
        """
        Transform Zammad attachment to FileRecord.

        Args:
            attachment_data: Attachment data from article
            ticket_id: Parent ticket ID
            article_id: Parent article ID
            parent_ticket: Parent TicketRecord

        Returns:
            FileRecord or None
        """
        attachment_id = attachment_data.get("id")
        if not attachment_id:
            return None

        filename = attachment_data.get("filename", "")
        size = attachment_data.get("size", 0)
        content_type = attachment_data.get("preferences", {}).get("Content-Type", "application/octet-stream")

        # Check for existing record
        external_record_id = f"{ticket_id}_{article_id}_{attachment_id}"
        existing_record = None
        async with self.data_store_provider.transaction() as tx_store:
            existing_record = await tx_store.get_record_by_external_id(
                connector_id=self.connector_id,
                external_id=external_record_id
            )

        is_new = existing_record is None
        record_id = existing_record.id if existing_record else str(uuid4())
        version = 0 if is_new else existing_record.version

        # Determine record_group_type from parent ticket
        # Attachments inherit the same group type as their parent ticket
        file_record_group_type = None
        if parent_ticket.external_record_group_id:
            # If parent has group_{id} format, it's PROJECT type
            if parent_ticket.external_record_group_id.startswith("group_"):
                file_record_group_type = RecordGroupType.PROJECT
            # If parent has record_group_type set, use it
            elif parent_ticket.record_group_type:
                file_record_group_type = parent_ticket.record_group_type

        # Get file extension from filename
        extension = filename.split('.')[-1] if '.' in filename else None

        # Use parent ticket's updated_at as external_revision_id (attachments inherit from parent)
        external_revision_id = str(parent_ticket.source_updated_at) if parent_ticket.source_updated_at else None

        file_record = FileRecord(
            id=record_id,
            org_id=self.data_entities_processor.org_id,
            record_type=RecordType.FILE,
            record_name=filename,
            external_record_id=external_record_id,
            external_revision_id=external_revision_id,
            external_record_group_id=parent_ticket.external_record_group_id,
            record_group_type=file_record_group_type,
            parent_record_id=parent_ticket.id,
            parent_external_record_id=parent_ticket.external_record_id,
            parent_record_type=RecordType.TICKET,
            indexing_status=IndexingStatus.NOT_STARTED,
            version=version,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            mime_type=content_type,
            weburl=parent_ticket.weburl,  # Use parent ticket's weburl
            created_at=get_epoch_timestamp_in_ms(),
            updated_at=get_epoch_timestamp_in_ms(),
            source_created_at=parent_ticket.source_created_at,
            source_updated_at=parent_ticket.source_updated_at,
            size_in_bytes=size,
            inherit_permissions=True,
            is_file=True,
            extension=extension,
        )

        # Set indexing status based on indexing filters
        if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.ISSUE_ATTACHMENTS):
            file_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

        return file_record

    # ==================== HELPER FUNCTIONS ====================

    def _parse_zammad_datetime(self, datetime_str: str) -> int:
        """Parse Zammad ISO8601 datetime string to epoch milliseconds"""
        if not datetime_str:
            return 0
        try:
            # Parse ISO 8601 format
            dt = datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))
            return int(dt.timestamp() * 1000)
        except (ValueError, TypeError):
            return 0

    # ==================== SYNC CHECKPOINTS ====================

    async def _get_group_sync_checkpoint(self, group_name: str) -> Optional[int]:
        """
        Get group-specific sync checkpoint (last_sync_time).

        Args:
            group_name: Group name (e.g., "Users", "Support Team")

        Returns:
            Last sync timestamp in epoch ms, or None if not set
        """
        data = await self.tickets_sync_point.read_sync_point(group_name)
        return data.get("last_sync_time") if data else None

    async def _update_group_sync_checkpoint(self, group_name: str, timestamp: Optional[int] = None) -> None:
        """
        Update group-specific sync checkpoint.

        Args:
            group_name: Group name (e.g., "Users", "Support Team")
            timestamp: Timestamp to set (defaults to current time if None)
        """
        sync_time = timestamp if timestamp is not None else get_epoch_timestamp_in_ms()
        await self.tickets_sync_point.update_sync_point(
            group_name,
            {"last_sync_time": sync_time}
        )
        self.logger.debug(f"💾 Updated sync checkpoint for group '{group_name}': {sync_time}")

    # ==================== KNOWLEDGE BASE SYNCING ====================

    async def _sync_knowledge_bases(self) -> None:
        """
        Sync knowledge bases, categories, and answers from Zammad.

        Args:
            filters: Filter configuration
        """
        datasource = await self._get_fresh_datasource()

        # Step 1: Initialize KB to get all metadata
        try:
            init_response = await datasource.init_knowledge_base()

            if not init_response.success:
                error_msg = getattr(init_response, 'message', 'Unknown error')
                error_detail = getattr(init_response, 'error', None)
                self.logger.warning(f"Failed to initialize knowledge base: {error_msg}" + (f" - {error_detail}" if error_detail else ""))
                return

            if not init_response.data:
                self.logger.warning("Knowledge base initialization returned empty data - KB may not be configured in Zammad")
                return

            kb_data = init_response.data

            # Debug: Log the response structure to help diagnose issues
            self.logger.debug(f"KB init response data type: {type(kb_data)}, keys: {list(kb_data.keys()) if isinstance(kb_data, dict) else 'not a dict'}")

        except Exception as e:
            self.logger.warning(f"Knowledge base not available: {e}", exc_info=True)
            return

        # Get knowledge bases, categories, and answers from init response
        # Handle different possible response structures
        if isinstance(kb_data, dict):
            # Try different possible key names (PascalCase, snake_case, camelCase, etc.)
            # Zammad API uses PascalCase: KnowledgeBase, KnowledgeBaseCategory, KnowledgeBaseAnswer
            # Check each key in order of preference
            knowledge_bases = None
            for key in ["KnowledgeBase", "knowledge_bases", "knowledgeBases", "kb"]:
                if key in kb_data:
                    knowledge_bases = kb_data[key]
                    break
            knowledge_bases = knowledge_bases if knowledge_bases is not None else []

            categories = None
            for key in ["KnowledgeBaseCategory", "categories", "category"]:
                if key in kb_data:
                    categories = kb_data[key]
                    break
            categories = categories if categories is not None else []

            answers = None
            for key in ["KnowledgeBaseAnswer", "answers", "answer"]:
                if key in kb_data:
                    answers = kb_data[key]
                    break
            answers = answers if answers is not None else []

            # Extract translations for linking
            # Zammad stores translations separately with translation_ids reference
            kb_translations = kb_data.get("KnowledgeBaseTranslation", {})
            category_translations = kb_data.get("KnowledgeBaseCategoryTranslation", {})
            answer_translations = kb_data.get("KnowledgeBaseAnswerTranslation", {})

            # Convert translations to lookup dicts if they're dicts with ID keys
            if isinstance(kb_translations, dict):
                kb_translations = {int(k): v for k, v in kb_translations.items()}
            if isinstance(category_translations, dict):
                category_translations = {int(k): v for k, v in category_translations.items()}
            if isinstance(answer_translations, dict):
                answer_translations = {int(k): v for k, v in answer_translations.items()}

            # If still empty, check if the dict itself contains KB data at top level
            if not knowledge_bases and not categories and not answers:
                # Check if the dict keys suggest it's a single KB object
                if "id" in kb_data or "translations" in kb_data:
                    self.logger.debug("KB init response appears to be a single KB object, wrapping in list")
                    knowledge_bases = [kb_data]
        elif isinstance(kb_data, list):
            # If response is a list directly, treat it as knowledge bases
            self.logger.debug("KB init response is a list, treating as knowledge bases")
            knowledge_bases = kb_data
            categories = []
            answers = []
            kb_translations = {}
            category_translations = {}
            answer_translations = {}
        else:
            self.logger.warning(f"Unexpected KB init response format: {type(kb_data)}")
            knowledge_bases = []
            categories = []
            answers = []
            kb_translations = {}
            category_translations = {}
            answer_translations = {}

        # Ensure knowledge_bases is a list
        # Zammad API may return a dict with IDs as keys, convert to list
        if isinstance(knowledge_bases, dict):
            self.logger.debug(f"knowledge_bases is a dict with {len(knowledge_bases)} entries, converting to list")
            knowledge_bases = list(knowledge_bases.values())
        elif not isinstance(knowledge_bases, list):
            self.logger.warning(f"knowledge_bases is not a list or dict: {type(knowledge_bases)}, converting")
            knowledge_bases = [knowledge_bases] if knowledge_bases else []

        # Same for categories and answers
        if isinstance(categories, dict):
            self.logger.debug(f"categories is a dict with {len(categories)} entries, converting to list")
            categories = list(categories.values())
        elif not isinstance(categories, list):
            categories = [categories] if categories else []

        if isinstance(answers, dict):
            self.logger.debug(f"answers is a dict with {len(answers)} entries, converting to list")
            answers = list(answers.values())
        elif not isinstance(answers, list):
            answers = [answers] if answers else []

        # Enrich answers with their translations from the separate translations dict
        # Answers have translation_ids that reference KnowledgeBaseAnswerTranslation
        for answer in answers:
            if isinstance(answer, dict):
                translation_ids = answer.get("translation_ids", [])
                if translation_ids and answer_translations:
                    translations_list = []
                    for trans_id in translation_ids:
                        trans_data = answer_translations.get(int(trans_id))
                        if trans_data:
                            translations_list.append(trans_data)
                    if translations_list:
                        answer["translations"] = translations_list
                        self.logger.debug(f"Enriched answer {answer.get('id')} with {len(translations_list)} translations")

        # Same for categories
        for category in categories:
            if isinstance(category, dict):
                translation_ids = category.get("translation_ids", [])
                if translation_ids and category_translations:
                    translations_list = []
                    for trans_id in translation_ids:
                        trans_data = category_translations.get(int(trans_id))
                        if trans_data:
                            translations_list.append(trans_data)
                    if translations_list:
                        category["translations"] = translations_list

        # Same for knowledge bases
        for kb in knowledge_bases:
            if isinstance(kb, dict):
                translation_ids = kb.get("translation_ids", [])
                if translation_ids and kb_translations:
                    translations_list = []
                    for trans_id in translation_ids:
                        trans_data = kb_translations.get(int(trans_id))
                        if trans_data:
                            translations_list.append(trans_data)
                    if translations_list:
                        kb["translations"] = translations_list

        if not knowledge_bases:
            self.logger.info(f"No knowledge bases found in response. Response structure: {type(kb_data)}, keys: {list(kb_data.keys()) if isinstance(kb_data, dict) else 'N/A'}")
            return

        # Step 2: Create RecordGroups for KBs
        kb_record_groups: List[Tuple[RecordGroup, List[Permission]]] = []
        kb_map: Dict[int, RecordGroup] = {}

        for kb in knowledge_bases:
            kb_id = kb.get("id")
            if not kb_id:
                continue

            # Get KB name from translations
            translations = kb.get("translations", [])
            kb_name = "Knowledge Base"
            for trans in translations:
                if trans.get("title"):
                    kb_name = trans.get("title")
                    break

            kb_record_group = RecordGroup(
                id=str(uuid4()),
                org_id=self.data_entities_processor.org_id,
                external_group_id=f"kb_{kb_id}",
                connector_id=self.connector_id,
                connector_name=self.connector_name,
                name=kb_name,
                short_name=f"KB-{kb_id}",
                group_type=RecordGroupType.KB,
                web_url=f"{self.base_url}/help/{kb.get('custom_address', '')}" if self.base_url else None,
                inherit_permissions=True,
            )

            permissions = [
                Permission(
                    entity_type=EntityType.ORG,
                    type=PermissionType.READ,
                    external_id=None
                )
            ]

            kb_record_groups.append((kb_record_group, permissions))
            kb_map[kb_id] = kb_record_group

        if kb_record_groups:
            await self.data_entities_processor.on_new_record_groups(kb_record_groups)
            self.logger.info(f"✅ Synced {len(kb_record_groups)} knowledge bases")

        # Step 3: Create RecordGroups for categories
        category_record_groups: List[Tuple[RecordGroup, List[Permission]]] = []
        category_map: Dict[int, RecordGroup] = {}

        for category in categories:
            cat_id = category.get("id")
            kb_id = category.get("knowledge_base_id")

            if not cat_id or not kb_id:
                continue

            # Get category name from translations
            translations = category.get("translations", [])
            cat_name = "Category"
            for trans in translations:
                if trans.get("title"):
                    cat_name = trans.get("title")
                    break

            # Determine parent
            parent_cat_id = category.get("parent_id")
            if parent_cat_id and parent_cat_id in category_map:
                parent_external_group_id = f"cat_{parent_cat_id}"
            elif kb_id in kb_map:
                parent_external_group_id = f"kb_{kb_id}"
            else:
                parent_external_group_id = None

            cat_record_group = RecordGroup(
                id=str(uuid4()),
                org_id=self.data_entities_processor.org_id,
                external_group_id=f"cat_{cat_id}",
                connector_id=self.connector_id,
                connector_name=self.connector_name,
                name=cat_name,
                short_name=f"CAT-{cat_id}",
                group_type=RecordGroupType.KB,
                parent_external_group_id=parent_external_group_id,
                inherit_permissions=True,
            )

            permissions = [
                Permission(
                    entity_type=EntityType.ORG,
                    type=PermissionType.READ,
                    external_id=None
                )
            ]

            category_record_groups.append((cat_record_group, permissions))
            category_map[cat_id] = cat_record_group

        if category_record_groups:
            await self.data_entities_processor.on_new_record_groups(category_record_groups)
            self.logger.info(f"✅ Synced {len(category_record_groups)} KB categories")

        # Step 4: Fetch and sync KB answers one by one
        total_answers = 0
        batch_records: List[Tuple[Record, List[Permission]]] = []

        if not answers:
            self.logger.info("No KB answers found in init response")
        else:
            self.logger.info(f"Processing {len(answers)} KB answers...")

        for answer_meta in answers:
            answer_id = answer_meta.get("id")
            if not answer_id:
                self.logger.debug(f"Skipping KB answer with missing ID: {answer_meta}")
                continue

            try:
                # Debug: Log answer_meta structure
                self.logger.debug(f"Processing KB answer {answer_id}, keys: {list(answer_meta.keys()) if isinstance(answer_meta, dict) else 'N/A'}")

                # Check if answer_meta already has full content (translations, etc.)
                # The init response may contain full answer data, so try using it first
                has_translations = answer_meta.get("translations") is not None
                has_content = answer_meta.get("content") is not None

                if has_translations or has_content:
                    # Use the answer data from init response directly
                    self.logger.debug(f"Using KB answer data from init response for answer {answer_id}")
                    answer_data = answer_meta
                else:
                    # Fetch full answer content if not in init response
                    # Note: The answer ID from init might need to be used differently
                    # Try fetching with the ID from answer_meta
                    answer_response = await datasource.get_kb_answer(id=answer_id)

                    if not answer_response.success:
                        # If fetch fails, try using answer_meta directly anyway
                        # The init response might have all the data we need
                        self.logger.debug(
                            f"Failed to fetch KB answer {answer_id} via API, trying to use init response data. "
                            f"Answer meta has translations: {has_translations}, content: {has_content}"
                        )
                        # Use answer_meta as fallback - it might have enough data
                        answer_data = answer_meta
                    else:
                        if not answer_response.data:
                            self.logger.warning(f"KB answer {answer_id} returned empty data, using init response data")
                            answer_data = answer_meta
                        else:
                            answer_data = answer_response.data

                answer_record = await self._transform_kb_answer_to_webpage_record(
                    answer_data,
                    category_map
                )

                if answer_record:
                    # Set indexing status based on indexing filters
                    if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.KNOWLEDGE_BASE):
                        answer_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                    batch_records.append((answer_record, []))
                    total_answers += 1

                    # Batch processing
                    if len(batch_records) >= BATCH_SIZE_KB_ANSWERS:
                        await self.data_entities_processor.on_new_records(batch_records)
                        self.logger.debug(f"Processed batch of {len(batch_records)} KB answers")
                        batch_records = []

            except Exception as e:
                self.logger.warning(f"Failed to process KB answer {answer_id}: {e}", exc_info=True)

        # Process remaining batch
        if batch_records:
            await self.data_entities_processor.on_new_records(batch_records)

        self.logger.info(f"✅ Synced {total_answers} KB answers as WebpageRecords")

    async def _transform_kb_answer_to_webpage_record(
        self,
        answer_data: Dict[str, Any],
        category_map: Dict[int, RecordGroup]
    ) -> Optional[WebpageRecord]:
        """
        Transform KB answer to WebpageRecord.

        Args:
            answer_data: Answer data from Zammad
            category_map: Map of category_id -> RecordGroup

        Returns:
            WebpageRecord or None
        """
        answer_id = answer_data.get("id")
        if not answer_id:
            return None

        # Get category
        category_id = answer_data.get("category_id")
        external_record_group_id = f"cat_{category_id}" if category_id else None

        # Determine record_group_type based on external_record_group_id format
        # KB categories use "cat_{id}" format and are RecordGroupType.KB
        kb_record_group_type = None
        if external_record_group_id and external_record_group_id.startswith("cat_"):
            kb_record_group_type = RecordGroupType.KB  # KB categories are KB type

        # Get title and content from translations
        translations = answer_data.get("translations", [])
        title = "KB Answer"
        content_body = None

        for trans in translations:
            trans_title = trans.get("title")

            # Content body can be in different locations depending on response format:
            # 1. trans["content"]["body"] - nested format from individual answer fetch
            # 2. trans["content_body"] - flat format from init response translations
            # 3. trans["body"] - another possible flat format
            trans_content_body = None
            if isinstance(trans.get("content"), dict):
                trans_content_body = trans.get("content", {}).get("body")
            if not trans_content_body:
                trans_content_body = trans.get("content_body")
            if not trans_content_body:
                trans_content_body = trans.get("body")

            # Update title if found
            if trans_title:
                title = trans_title

            # Update content_body if found
            if trans_content_body:
                content_body = trans_content_body

            # Break early if we found both title and content_body
            if title and title != "KB Answer" and content_body:
                break

        # Parse timestamps
        created_at = self._parse_zammad_datetime(answer_data.get("created_at", ""))
        updated_at = self._parse_zammad_datetime(answer_data.get("updated_at", ""))

        # Check for existing record
        external_record_id = f"kb_answer_{answer_id}"
        existing_record = None
        async with self.data_store_provider.transaction() as tx_store:
            existing_record = await tx_store.get_record_by_external_id(
                connector_id=self.connector_id,
                external_id=external_record_id
            )

        is_new = existing_record is None
        record_id = existing_record.id if existing_record else str(uuid4())
        version = 0 if is_new else (existing_record.version + 1 if existing_record.source_updated_at != updated_at else existing_record.version)

        # Use updated_at as external_revision_id so placeholders (None) will trigger update
        external_revision_id = str(updated_at) if updated_at else None

        webpage_record = WebpageRecord(
            id=record_id,
            org_id=self.data_entities_processor.org_id,
            record_type=RecordType.WEBPAGE,
            record_name=title,
            external_record_id=external_record_id,
            external_revision_id=external_revision_id,
            external_record_group_id=external_record_group_id,
            record_group_type=kb_record_group_type,  # Set group type for auto-creation if needed
            indexing_status=IndexingStatus.NOT_STARTED,
            version=version,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            weburl=f"{self.base_url}/help/kb_answers/{answer_id}" if self.base_url else "",
            created_at=get_epoch_timestamp_in_ms(),
            updated_at=get_epoch_timestamp_in_ms(),
            source_created_at=created_at,
            source_updated_at=updated_at,
            inherit_permissions=True,
            preview_renderable=False,
        )

        return webpage_record

    # ==================== CONTENT STREAMING ====================

    async def stream_record(self, record: Record) -> StreamingResponse:
        """
        Stream record content as BlocksContainer.

        Args:
            record: Record to stream

        Returns:
            StreamingResponse with serialized BlocksContainer
        """
        try:
            if record.record_type == RecordType.TICKET:
                content_bytes = await self._process_ticket_blockgroups_for_streaming(record)
            elif record.record_type == RecordType.WEBPAGE:
                content_bytes = await self._process_kb_answer_blockgroups_for_streaming(record)
            elif record.record_type == RecordType.FILE:
                content_bytes = await self._process_file_for_streaming(record)
            else:
                raise ValueError(f"Unsupported record type for streaming: {record.record_type}")

            return StreamingResponse(
                iter([content_bytes]),
                media_type=MimeTypes.BLOCKS.value,
                headers={
                    "Content-Disposition": f'inline; filename="{record.external_record_id}_blocks.json"'
                }
            )

        except Exception as e:
            self.logger.error(f"❌ Error streaming record {record.id}: {e}", exc_info=True)
            raise

    async def _process_ticket_blockgroups_for_streaming(self, record: Record) -> bytes:
        """
        Process ticket into BlocksContainer for streaming.

        Structure:
        - Description BlockGroup (index=0) - First article or ticket description
        - Comment BlockGroups (index=1,2,...) - Each subsequent article

        No Thread BlockGroups - Zammad articles are linear (no threading).

        Args:
            record: TicketRecord to process

        Returns:
            Serialized BlocksContainer as bytes
        """
        ticket_id = record.external_record_id
        datasource = await self._get_fresh_datasource()

        # Fetch ticket data
        ticket_response = await datasource.get_ticket(id=int(ticket_id), expand=True)
        if not ticket_response.success or not ticket_response.data:
            raise Exception(f"Failed to fetch ticket {ticket_id}")

        ticket_data = ticket_response.data

        # Fetch articles for this ticket
        articles_response = await datasource.list_ticket_articles(ticket_id=int(ticket_id))
        articles = []
        if articles_response.success and articles_response.data:
            articles = articles_response.data if isinstance(articles_response.data, list) else [articles_response.data]

        # Sort articles by created_at
        articles.sort(key=lambda a: a.get("created_at", ""))

        # Build BlockGroups
        block_groups: List[BlockGroup] = []
        blocks: List[Block] = []
        block_group_index = 0

        # Get ticket metadata for description
        ticket_title = ticket_data.get("title", "")
        ticket_number = ticket_data.get("number", "")

        # First article becomes the Description BlockGroup
        if articles:
            first_article = articles[0]
            description_body_html = first_article.get("body", "")
            first_article_id = first_article.get("id", "")

            # Get attachments for first article (description) as children_records
            first_article_attachments = first_article.get("attachments", [])
            description_children_records = []
            for att in first_article_attachments:
                att_id = att.get("id")
                att_filename = att.get("filename", "")
                if att_id:
                    description_children_records.append(ChildRecord(
                        child_type=ChildType.RECORD,
                        child_id=f"{ticket_id}_{first_article_id}_{att_id}",
                        child_name=att_filename
                    ))

            self.logger.debug(f"Description body HTML: {description_body_html}")
            # Convert HTML to Markdown
            description_body_md = html_to_markdown(description_body_html) if description_body_html else ""
            self.logger.debug(f"Description body MD: {description_body_md}")
            description_data = f"# {ticket_title}\n\n{description_body_md}" if description_body_md else f"# {ticket_title}"

            description_block_group = BlockGroup(
                id=str(uuid4()),
                index=block_group_index,
                name=ticket_title if ticket_title else f"#{ticket_number} - Description",
                type=GroupType.TEXT_SECTION,
                sub_type=GroupSubType.CONTENT,
                description=f"Description for ticket #{ticket_number}",
                source_group_id=f"{ticket_id}_description",
                data=description_data,
                format=DataFormat.MARKDOWN,
                weburl=record.weburl,
                requires_processing=True,
                children_records=description_children_records if description_children_records else None,
            )
            block_groups.append(description_block_group)
            block_group_index += 1

            # Remaining articles become Comment BlockGroups
            for article in articles[1:]:
                article_id = article.get("id", "")
                article_body_html = article.get("body", "")
                article_from = article.get("from", "")
                article_subject = article.get("subject", "")
                article_sender = article.get("sender", "")
                article_preferences = article.get("preferences", {})

                # Skip system-generated articles (auto-replies, triggers, etc.)
                if article_sender == "System":
                    self.logger.debug(f"Skipping system-generated article {article_id} for ticket {ticket_id}")
                    continue

                # Skip auto-response emails (bounce notifications, delivery failures, etc.)
                if article_preferences.get("is-auto-response") or article_preferences.get("send-auto-response") is False:
                    self.logger.debug(f"Skipping auto-response article {article_id} for ticket {ticket_id}")
                    continue

                # Skip MAILER-DAEMON bounce notifications
                if "MAILER-DAEMON" in article_from or "Mail Delivery System" in article_from:
                    self.logger.debug(f"Skipping bounce notification article {article_id} for ticket {ticket_id}")
                    continue

                if not article_body_html:
                    continue

                self.logger.debug(f"Article body HTML: {article_body_html}")
                # Convert HTML to Markdown
                article_body_md = html_to_markdown(article_body_html)
                self.logger.debug(f"Article body MD: {article_body_md}")
                # Get author name
                author_name = article_from if article_from else "Unknown"

                # Build comment name
                comment_name = f"Comment by {author_name}"
                if article_subject:
                    comment_name = f"{article_subject} - {author_name}"

                # Get attachments for this article as children_records
                article_attachments = article.get("attachments", [])
                comment_children_records = []

                for att in article_attachments:
                    att_id = att.get("id")
                    att_filename = att.get("filename", "")
                    if att_id:
                        comment_children_records.append(ChildRecord(
                            child_type=ChildType.RECORD,
                            child_id=f"{ticket_id}_{article_id}_{att_id}",
                            child_name=att_filename
                        ))

                comment_block_group = BlockGroup(
                    id=str(uuid4()),
                    index=block_group_index,
                    parent_index=0,  # Points to Description BlockGroup
                    name=comment_name,
                    type=GroupType.TEXT_SECTION,
                    sub_type=GroupSubType.COMMENT,
                    description=f"Comment by {author_name}",
                    source_group_id=str(article_id),
                    data=article_body_md,
                    format=DataFormat.MARKDOWN,
                    weburl=record.weburl,
                    requires_processing=True,
                    children_records=comment_children_records if comment_children_records else None,
                )
                block_groups.append(comment_block_group)
                block_group_index += 1

        else:
            # No articles - create minimal description
            minimal_block_group = BlockGroup(
                id=str(uuid4()),
                index=0,
                name=ticket_title if ticket_title else f"#{ticket_number} - Description",
                type=GroupType.TEXT_SECTION,
                sub_type=GroupSubType.CONTENT,
                description=f"Description for ticket #{ticket_number}",
                source_group_id=f"{ticket_id}_description",
                data=f"# {ticket_title}",
                format=DataFormat.MARKDOWN,
                weburl=record.weburl,
                requires_processing=True,
            )
            block_groups.append(minimal_block_group)

        # Build children arrays for BlockGroups
        blockgroup_children_map: Dict[int, List[int]] = defaultdict(list)
        for bg in block_groups:
            if bg.parent_index is not None:
                blockgroup_children_map[bg.parent_index].append(bg.index)

        for bg in block_groups:
            if bg.index in blockgroup_children_map:
                bg.children = [
                    BlockContainerIndex(block_group_index=child_idx)
                    for child_idx in sorted(blockgroup_children_map[bg.index])
                ]

        blocks_container = BlocksContainer(blocks=blocks, block_groups=block_groups)
        return blocks_container.model_dump_json(indent=2).encode('utf-8')

    async def _process_kb_answer_blockgroups_for_streaming(self, record: Record) -> bytes:
        """
        Process KB answer into BlocksContainer for streaming.

        Args:
            record: WebpageRecord to process

        Returns:
            Serialized BlocksContainer as bytes
        """
        # Extract answer ID from external_record_id (format: kb_answer_{id})
        external_id = record.external_record_id
        answer_id = int(external_id.replace("kb_answer_", ""))

        datasource = await self._get_fresh_datasource()

        # Try to fetch KB answer - may fail with 404 for some Zammad configurations
        answer_response = await datasource.get_kb_answer(id=answer_id)

        title = record.record_name
        body = ""

        if answer_response.success and answer_response.data:
            answer_data = answer_response.data

            # Get title and content from translations
            translations = answer_data.get("translations", [])

            for trans in translations:
                # Try different content body locations
                trans_body = None
                if isinstance(trans.get("content"), dict):
                    trans_body = trans.get("content", {}).get("body")
                if not trans_body:
                    trans_body = trans.get("content_body")
                if not trans_body:
                    trans_body = trans.get("body")

                if trans_body:
                    body = trans_body
                    # Also try to get title from translation
                    if trans.get("title"):
                        title = trans.get("title")
                    break
        else:
            # If API fetch fails, we'll just use the record name as title
            self.logger.debug(f"KB answer {answer_id} fetch failed, using record name as title")

        # Build single BlockGroup for answer content
        # Use HTML tags for title since body is HTML from Zammad KB
        answer_block_group = BlockGroup(
            id=str(uuid4()),
            index=0,
            name=title,
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.CONTENT,
            description=f"KB Answer: {title}",
            source_group_id=str(answer_id),
            data=f"<h1>{title}</h1>{body}" if body else f"<h1>{title}</h1>",
            format=DataFormat.HTML,
            weburl=record.weburl,
            requires_processing=True,
        )

        blocks_container = BlocksContainer(blocks=[], block_groups=[answer_block_group])
        return blocks_container.model_dump_json(indent=2).encode('utf-8')

    async def _process_file_for_streaming(self, record: Record) -> bytes:
        """
        Process file/attachment for streaming.

        Args:
            record: FileRecord to process

        Returns:
            File content as bytes
        """
        # Parse external_record_id (format: {ticket_id}_{article_id}_{attachment_id})
        parts = record.external_record_id.split("_")
        if len(parts) != ATTACHMENT_ID_PARTS_COUNT:
            raise ValueError(f"Invalid attachment ID format: {record.external_record_id}")

        ticket_id, article_id, attachment_id = parts

        datasource = await self._get_fresh_datasource()

        # Download attachment
        response = await datasource.get_ticket_attachment(
            ticket_id=int(ticket_id),
            article_id=int(article_id),
            id=int(attachment_id)
        )

        if not response.success:
            raise Exception(f"Failed to download attachment: {response.message}")

        # Return raw content bytes
        content = response.data
        if isinstance(content, str):
            return content.encode('utf-8')
        elif isinstance(content, bytes):
            return content
        else:
            return str(content).encode('utf-8')

    # ==================== FILTER OPTIONS ====================

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> FilterOptionsResponse:
        """
        Get dynamic filter options for filter fields.

        Args:
            filter_key: Name of the filter field
            page: Page number
            limit: Items per page
            search: Optional search query
            cursor: Optional cursor for pagination (not used for Zammad)

        Returns:
            FilterOptionsResponse with available options
        """
        options: List[FilterOption] = []

        if filter_key == SyncFilterKey.GROUP_IDS.value:
            datasource = await self._get_fresh_datasource()
            fetch_page = 1
            per_page = 100
            all_groups = []

            # Fetch all groups with pagination
            while True:
                response = await datasource.list_groups(
                    page=fetch_page,
                    per_page=per_page
                )

                if not response.success or not response.data:
                    break

                groups_data = response.data
                if not isinstance(groups_data, list):
                    groups_data = [groups_data]

                if not groups_data:
                    break

                all_groups.extend(groups_data)

                # Check if there are more pages
                if len(groups_data) < per_page:
                    break

                fetch_page += 1

            # Build filter options from groups
            for group in all_groups:
                group_id = group.get("id")
                group_name = group.get("name", "")
                active = group.get("active", True)

                if not active or not group_id or not group_name:
                    continue

                # Apply search filter
                if search and search.lower() not in group_name.lower():
                    continue

                options.append(FilterOption(
                    id=str(group_id),
                    label=group_name
                ))

        # Apply pagination
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        paginated_options = options[start_idx:end_idx]

        return FilterOptionsResponse(
            success=True,
            options=paginated_options,
            page=page,
            limit=limit,
            has_more=len(options) > end_idx
        )

    # ==================== ABSTRACT METHODS ====================

    async def run_incremental_sync(self) -> None:
        """
        Incremental sync - calls run_sync which handles incremental logic.
        """
        self.logger.info(f"🔄 Starting Zammad incremental sync for connector {self.connector_id}")
        await self.run_sync()
        self.logger.info("✅ Zammad incremental sync completed")

    async def test_connection_and_access(self) -> bool:
        """Test connection and access to Zammad using DataSource"""
        try:
            # Initialize client if needed
            datasource = await self._get_fresh_datasource()

            # Test by fetching groups (simple API call)
            response = await datasource.list_groups()

            if response.success:
                self.logger.info("✅ Zammad connection test successful")
                return True
            else:
                self.logger.error(f"❌ Connection test failed: {response.message if hasattr(response, 'message') else 'Unknown error'}")
                return False
        except Exception as e:
            self.logger.error(f"❌ Connection test failed: {e}", exc_info=True)
            return False

    async def get_signed_url(self, record: Record) -> str:
        """Create a signed URL for a specific record"""
        # Zammad doesn't support signed URLs, return empty string
        return ""

    async def handle_webhook_notification(self, notification: Dict) -> None:
        """Handle webhook notifications from Zammad"""
        # TODO: Implement webhook handling when Zammad webhooks are configured
        pass

    async def cleanup(self) -> None:
        """Cleanup resources - close HTTP client connections properly"""
        try:
            self.logger.info("Cleaning up Zammad connector resources")

            # Close HTTP client properly BEFORE event loop closes
            # This prevents Windows asyncio "Event loop is closed" errors
            if self.external_client:
                try:
                    internal_client = self.external_client.get_client()
                    if internal_client and hasattr(internal_client, 'close'):
                        await internal_client.close()
                        self.logger.debug("Closed Zammad HTTP client connection")
                except Exception as e:
                    # Swallow errors during shutdown - client may already be closed
                    self.logger.debug(f"Error closing Zammad client (may be expected during shutdown): {e}")

            self.logger.info("✅ Zammad connector cleanup completed")
        except Exception as e:
            self.logger.warning(f"⚠️ Error during Zammad connector cleanup: {e}")

    # ==================== REINDEXING METHODS ====================

    async def reindex_records(self, record_results: List[Record]) -> None:
        """Reindex a list of Zammad records.

        This method:
        1. For each record, checks if it has been updated at the source
        2. If updated, upserts the record in DB
        3. Publishes reindex events for all records via data_entities_processor
        4. Skips reindex for records that are not properly typed (base Record class)"""
        try:
            if not record_results:
                return

            self.logger.info(f"Starting reindex for {len(record_results)} Zammad records")

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
                    # Only reindex properly typed records (TicketRecord, WebpageRecord, etc.)
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
            self.logger.error(f"❌ Failed to reindex Zammad records: {e}", exc_info=True)
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
            # Handle TicketRecord
            if record.record_type == RecordType.TICKET:
                return await self._check_and_fetch_updated_ticket(record)

            # Handle WebpageRecord (KB answers)
            elif record.record_type == RecordType.WEBPAGE:
                return await self._check_and_fetch_updated_kb_answer(record)

            # Handle FileRecord (attachments)
            elif record.record_type == RecordType.FILE:
                # Attachments are typically not updated independently
                return None

            else:
                self.logger.warning(f"Unsupported record type for reindex: {record.record_type}")
                return None

        except Exception as e:
            self.logger.error(f"Error checking record {record.id} at source: {e}")
            return None

    async def _check_and_fetch_updated_ticket(
        self, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch ticket from source for reindexing."""
        try:
            ticket_id = int(record.external_record_id)

            # Fetch ticket from source
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_ticket(id=ticket_id, expand=True)

            if not response.success:
                self.logger.warning(f"Ticket {ticket_id} not found at source: {response.message if hasattr(response, 'message') else 'Unknown error'}")
                return None

            if not response.data:
                self.logger.warning(f"No ticket data found for {ticket_id}")
                return None

            ticket_data = response.data
            current_updated_at = self._parse_zammad_datetime(ticket_data.get("updated_at", ""))

            # Compare with stored timestamp
            if hasattr(record, 'source_updated_at') and record.source_updated_at and current_updated_at:
                if record.source_updated_at == current_updated_at:
                    self.logger.debug(f"Ticket {ticket_id} has not changed at source")
                    return None

            self.logger.info(f"Ticket {ticket_id} has changed at source")

            # Re-transform ticket
            updated_ticket = await self._transform_ticket_to_ticket_record(ticket_data)
            if not updated_ticket:
                return None

            # Extract permissions (empty list, records inherit permissions from RecordGroup)
            permissions = []

            return (updated_ticket, permissions)

        except Exception as e:
            self.logger.error(f"Error checking ticket {record.id} at source: {e}")
            return None

    async def _check_and_fetch_updated_kb_answer(
        self, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch KB answer from source for reindexing."""
        try:
            # Extract answer ID from external_record_id (format: kb_answer_{id})
            external_id = record.external_record_id
            if not external_id.startswith("kb_answer_"):
                self.logger.warning(f"Invalid KB answer external_record_id format: {external_id}")
                return None

            answer_id = int(external_id.replace("kb_answer_", ""))

            # Fetch KB answer from source
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_kb_answer(id=answer_id)

            if not response.success:
                self.logger.warning(f"KB answer {answer_id} not found at source: {response.message if hasattr(response, 'message') else 'Unknown error'}")
                return None

            if not response.data:
                self.logger.warning(f"No KB answer data found for {answer_id}")
                return None

            answer_data = response.data
            current_updated_at = self._parse_zammad_datetime(answer_data.get("updated_at", ""))

            # Compare with stored timestamp
            if hasattr(record, 'source_updated_at') and record.source_updated_at and current_updated_at:
                if record.source_updated_at == current_updated_at:
                    self.logger.debug(f"KB answer {answer_id} has not changed at source")
                    return None

            self.logger.info(f"KB answer {answer_id} has changed at source")

            # Re-transform KB answer
            category_map: Dict[int, RecordGroup] = {}
            async with self.data_store_provider.transaction() as tx_store:
                if record.external_record_group_id:
                    cat_rg = await tx_store.get_record_group_by_external_id(
                        connector_id=self.connector_id,
                        external_id=record.external_record_group_id
                    )
                    if cat_rg:
                        category_id = int(record.external_record_group_id.replace("cat_", ""))
                        category_map[category_id] = cat_rg

            updated_answer = await self._transform_kb_answer_to_webpage_record(answer_data, category_map)
            if not updated_answer:
                return None

            # Extract permissions (empty list, records inherit permissions from RecordGroup)
            permissions = []

            return (updated_answer, permissions)

        except Exception as e:
            self.logger.error(f"Error checking KB answer {record.id} at source: {e}")
            return None

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str
    ) -> "BaseConnector":
        """Factory method to create ZammadConnector instance"""
        data_entities_processor = DataSourceEntitiesProcessor(
            logger,
            data_store_provider,
            config_service
        )
        await data_entities_processor.initialize()

        return ZammadConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id
        )
