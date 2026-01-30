"""Zammad Connector Implementation"""
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
    Tuple,
)
from uuid import uuid4

from bs4 import BeautifulSoup  # pyright: ignore[reportMissingModuleSource]
from fastapi.responses import StreamingResponse
from html_to_markdown import convert as html_to_markdown  # type: ignore[import-untyped]

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    AppGroups,
    Connectors,
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
    BlockGroup,
    BlocksContainer,
    BlockGroupChildren,
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
    RelatedExternalRecord,
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
KB_ANSWER_ATTACHMENT_PARTS_COUNT = 2

# Zammad link type to RecordRelations mapping
# Zammad supports: normal, parent, child
ZAMMAD_LINK_TYPE_MAP: Dict[str, RecordRelations] = {
    "normal": RecordRelations.RELATED,
    "parent": RecordRelations.DEPENDS_ON,  # Current ticket depends on parent
    "child": RecordRelations.LINKED_TO,    # Child depends on current ticket
}

# Zammad link object type to RecordType mapping
# Note: KB answers come as "KnowledgeBase::Answer::Translation" in links
ZAMMAD_LINK_OBJECT_MAP: Dict[str, RecordType] = {
    "Ticket": RecordType.TICKET,
    "KnowledgeBase::Answer::Translation": RecordType.WEBPAGE,  # KB answer translations
}

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
        """
        try:
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

            # Load state and priority mappings (only once on initialization)
            await self._load_lookup_tables()

            self.logger.info(f"✅ Zammad connector {self.connector_id} initialized successfully")
            return True
        except Exception as e:
            self.logger.error(f"❌ Failed to initialize Zammad connector: {e}", exc_info=True)
            return False

    async def _get_fresh_datasource(self) -> ZammadDataSource:
        """
        Get ZammadDataSource with ALWAYS-FRESH access token.

        This method:
        1. Fetches current token from config (API_TOKEN)
        2. Compares with existing client's token
        3. Updates client ONLY if token changed (mutation)
        4. Returns datasource with current token

        Returns:
            ZammadDataSource with current valid token
        """
        if not self.external_client:
            raise Exception("Zammad client not initialized. Call init() first.")

        # Fetch current config from etcd (async I/O)
        config = await self.config_service.get_config(f"/services/connectors/{self.connector_id}/config")

        if not config:
            raise Exception("Zammad configuration not found")

        # Extract fresh token (API_TOKEN only)
        auth_config = config.get("auth", {})
        auth_type = auth_config.get("authType", "API_TOKEN")

        # Only support API_TOKEN authentication
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
            # Ensure connector is initialized before proceeding
            if not self.external_client:
                await self.init()

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
            self.logger.info("📚 Step 6: Syncing knowledge base...")
            await self._sync_knowledge_bases()

            self.logger.info(f"✅ Zammad sync completed for connector {self.connector_id}")

        except Exception as e:
            self.logger.error(f"❌ Zammad sync failed: {e}", exc_info=True)
            raise

    def _is_group_allowed_by_filter(self, group_id: str) -> bool:
        """
        Check if a group_id is allowed by the group_ids sync filter.

        Args:
            group_id: The group ID to check

        Returns:
            True if the group should be processed, False if it should be skipped
        """
        # Check if filter is set
        if not self.sync_filters:
            return True  # No filter, allow all

        group_ids_filter = self.sync_filters.get(SyncFilterKey.GROUP_IDS)
        if not group_ids_filter:
            return True  # No group filter, allow all

        selected_group_ids = group_ids_filter.get_value(default=[])
        if not selected_group_ids:
            return True  # Empty filter, allow all

        # Convert to set of strings for easy lookup
        filter_set = set(str(gid) for gid in selected_group_ids)

        # Check operator: "in" (include) or "not_in" (exclude)
        group_ids_operator = group_ids_filter.get_operator()
        operator_value = "in"
        if group_ids_operator:
            operator_value = group_ids_operator.value if hasattr(group_ids_operator, 'value') else str(group_ids_operator)

        is_exclude = operator_value == "not_in"

        if is_exclude:
            # NOT_IN: allow if NOT in filter list
            return group_id not in filter_set
        else:
            # IN: allow if IN filter list
            return group_id in filter_set

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
                firstname = user_data.get("firstname", "") or ""
                lastname = user_data.get("lastname", "") or ""
                full_name = f"{firstname} {lastname}".strip()

                # Skip inactive users, users without email, or users without ID
                if not active or not email or not user_id:
                    continue

                # Skip system/bot users (mailer-daemon, noreply, etc.)
                email_lower = email.lower()
                if (
                    "mailer-daemon" in email_lower
                    or "noreply" in email_lower
                    or "no-reply" in email_lower
                    or "Mail Delivery System" in full_name
                ):
                    self.logger.debug(f"Skipping system user: {email} ({full_name})")
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

                # Use full_name (already computed above) or fallback to email
                display_name = full_name or email

                # Create AppUser
                app_user = AppUser(
                    id=str(uuid4()),
                    org_id=self.data_entities_processor.org_id,
                    source_user_id=str(user_id),
                    connector_id=self.connector_id,
                    app_name=Connectors.ZAMMAD,
                    email=email,
                    full_name=display_name,
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

                # Apply group_ids filter early - skip groups that don't match the filter
                if not self._is_group_allowed_by_filter(str(group_id)):
                    self.logger.debug(f"⏭️ Skipping group {group_id} ({group_name}) - excluded by filter")
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
            (already filtered by group_ids sync filter in _fetch_groups)
        """
        if not group_record_groups:
            self.logger.info("ℹ️ No groups to sync tickets for")
            return

        self.logger.info(f"📋 Will sync tickets for {len(group_record_groups)} groups")

        total_tickets_all_groups = 0
        total_attachments_all_groups = 0

        # Sync each group independently
        for group_record_group, group_perms in group_record_groups:
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
                total_tickets = 0
                total_attachments = 0
                max_ticket_updated_at: Optional[int] = None

                async for batch_records in self._fetch_tickets_for_group_batch(
                    group_id=int(group_id),
                    group_name=group_name,
                    last_sync_time=last_sync_time
                ):
                    if not batch_records:
                        continue

                    # Count tickets vs attachments separately
                    batch_tickets = 0
                    batch_attachments = 0
                    for record, _ in batch_records:
                        if isinstance(record, TicketRecord):
                            batch_tickets += 1
                            # Track max updated_at from tickets for sync point
                            if record.source_updated_at:
                                if max_ticket_updated_at is None or record.source_updated_at > max_ticket_updated_at:
                                    max_ticket_updated_at = record.source_updated_at
                        else:
                            # Assume it's an attachment (FileRecord)
                            batch_attachments += 1

                    # Process batch
                    total_tickets += batch_tickets
                    total_attachments += batch_attachments
                    await self.data_entities_processor.on_new_records(batch_records)
                    if batch_attachments > 0:
                        self.logger.debug(f"📝 Synced batch: {batch_tickets} tickets, {batch_attachments} attachments for group {group_name}")
                    else:
                        self.logger.debug(f"📝 Synced batch: {batch_tickets} tickets for group {group_name}")

                    # Update sync point after each batch (fault tolerance)
                    if max_ticket_updated_at:
                        await self._update_group_sync_checkpoint(group_name, max_ticket_updated_at + 1000)

                # Final sync point update: Only update to current time if we processed tickets
                if total_tickets > 0:
                    # If max_ticket_updated_at wasn't set (edge case), use current time as fallback
                    if not max_ticket_updated_at:
                        self.logger.warning(f"Processed {total_tickets} tickets but max_ticket_updated_at not set, using current time")
                        await self._update_group_sync_checkpoint(group_name)
                    # else: max_ticket_updated_at was already set above, no need to update again
                else:
                    self.logger.debug(f"No tickets found for group {group_name}, keeping existing checkpoint to avoid skipping older tickets")

                total_tickets_all_groups += total_tickets
                total_attachments_all_groups += total_attachments
                if total_attachments > 0:
                    self.logger.info(f"✅ Synced {total_tickets} tickets, {total_attachments} attachments for group {group_name}")
                else:
                    self.logger.info(f"✅ Synced {total_tickets} tickets for group {group_name}")

            except Exception as e:
                self.logger.error(f"❌ Error syncing tickets for group {group_record_group.name}: {e}", exc_info=True)
                continue  # Continue with next group even if one fails

        if total_attachments_all_groups > 0:
            self.logger.info(f"✅ Total: Synced {total_tickets_all_groups} tickets, {total_attachments_all_groups} attachments across {len(group_record_groups)} groups")
        else:
            self.logger.info(f"✅ Total: Synced {total_tickets_all_groups} tickets across {len(group_record_groups)} groups")

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

        # Get modified date filter from sync_filters
        modified_filter = self.sync_filters.get(SyncFilterKey.MODIFIED) if self.sync_filters else None
        modified_after: Optional[int] = None
        modified_before: Optional[int] = None

        if modified_filter:
            modified_after, modified_before = modified_filter.get_value(default=(None, None))

        # Get created date filter from sync_filters
        created_filter = self.sync_filters.get(SyncFilterKey.CREATED) if self.sync_filters else None
        created_after: Optional[int] = None
        created_before: Optional[int] = None

        if created_filter:
            created_after, created_before = created_filter.get_value(default=(None, None))

        # Determine modified_after from filter and/or incremental sync checkpoint
        if last_sync_time:
            # Use the greater of last_sync_time and modified_after filter
            if modified_after:
                modified_after = max(modified_after, last_sync_time)
            else:
                modified_after = last_sync_time

        # Add modified date range filter (updated_at)
        if modified_after:
            dt = datetime.fromtimestamp(modified_after / 1000, tz=timezone.utc)
            iso_format = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            query_parts.append(f"updated_at:[{iso_format} TO *]")

        if modified_before:
            dt = datetime.fromtimestamp(modified_before / 1000, tz=timezone.utc)
            iso_format = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            query_parts.append(f"updated_at:[* TO {iso_format}]")

        # Add created date range filter (created_at)
        if created_after:
            dt = datetime.fromtimestamp(created_after / 1000, tz=timezone.utc)
            iso_format = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            query_parts.append(f"created_at:[{iso_format} TO *]")

        if created_before:
            dt = datetime.fromtimestamp(created_before / 1000, tz=timezone.utc)
            iso_format = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            query_parts.append(f"created_at:[* TO {iso_format}]")

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

    # ==================== ATTACHMENT HANDLING AND TRANSFORMATIONS ====================

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
                    attachment_id = attachment.get("id")
                    if not attachment_id:
                        continue
                    # Ticket attachment external_record_id format: {ticket_id}_{article_id}_{attachment_id}
                    external_record_id = f"{ticket_id}_{article_id}_{attachment_id}"
                    file_record = await self._transform_attachment_to_file_record(
                        attachment_data=attachment,
                        external_record_id=external_record_id,
                        parent_record=ticket_record,
                        parent_record_type=RecordType.TICKET,
                        indexing_filter_key=IndexingFilterKey.ISSUE_ATTACHMENTS
                    )
                    if file_record:
                        # FileRecords inherit permissions from parent
                        attachments.append((file_record, []))
                except Exception as e:
                    self.logger.warning(f"Failed to process attachment: {e}")

        return attachments

    async def _fetch_kb_answer_attachments(
        self,
        answer_data: Dict[str, Any],
        answer_record: WebpageRecord,
        answer_permissions: List[Permission]
    ) -> List[Tuple[Record, List[Permission]]]:
        """
        Fetch attachments for a KB answer.

        Args:
            answer_data: Raw answer data from Zammad
            answer_record: Parent WebpageRecord
            answer_permissions: Permissions from the parent answer (PUBLIC/ARCHIVED/DRAFT have explicit permissions, INTERNAL inherits)

        Returns:
            List of (FileRecord, permissions) tuples
        """
        attachments: List[Tuple[Record, List[Permission]]] = []

        answer_id = answer_data.get("id")
        if not answer_id:
            return attachments

        # Check if answer has attachments in the data
        # Attachments might be in answer_data.get("attachments") or in translations
        answer_attachments = answer_data.get("attachments", [])

        # Also check translations for attachments
        translations = answer_data.get("translations", [])
        for trans in translations:
            trans_attachments = trans.get("attachments", [])
            if trans_attachments:
                answer_attachments.extend(trans_attachments)

        if not answer_attachments:
            return attachments

        for attachment in answer_attachments:
            try:
                attachment_id = attachment.get("id")
                if not attachment_id:
                    continue
                # KB answer attachment external_record_id format: kb_answer_{answer_id}_attachment_{attachment_id}
                external_record_id = f"kb_answer_{answer_id}_attachment_{attachment_id}"
                # Use the same inherit_permissions value as the parent answer
                # PUBLIC/ARCHIVED/DRAFT: inherit_permissions=False (explicit permissions)
                # INTERNAL: inherit_permissions=True (inherits from category)
                answer_inherit_permissions = answer_record.inherit_permissions if hasattr(answer_record, 'inherit_permissions') else False
                file_record = await self._transform_attachment_to_file_record(
                    attachment_data=attachment,
                    external_record_id=external_record_id,
                    parent_record=answer_record,
                    parent_record_type=RecordType.WEBPAGE,
                    indexing_filter_key=IndexingFilterKey.KNOWLEDGE_BASE,
                    inherit_permissions=answer_inherit_permissions
                )
                if file_record:
                    # Pass the same permissions as the parent answer
                    # For PUBLIC: ORG permission, inherit_permissions=False
                    # For ARCHIVED/DRAFT: Editor role permissions, inherit_permissions=False
                    # For INTERNAL: Empty list, inherit_permissions=True will handle category inheritance
                    attachments.append((file_record, answer_permissions))
            except Exception as e:
                self.logger.warning(f"Failed to process KB answer attachment: {e}")

        return attachments

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

        # Fetch linked tickets and KB answers for this ticket
        try:
            related_external_records = await self._fetch_ticket_links(ticket_id=int(ticket_id))
            if related_external_records:
                ticket_record.related_external_records = related_external_records
                self.logger.debug(
                    f"🔗 Ticket {ticket_id} has {len(related_external_records)} linked records"
                )
        except Exception as e:
            self.logger.debug(f"Failed to fetch links for ticket {ticket_id}: {e}")

        return ticket_record

    async def _transform_attachment_to_file_record(
        self,
        attachment_data: Dict[str, Any],
        external_record_id: str,
        parent_record: Record,
        parent_record_type: RecordType,
        indexing_filter_key: IndexingFilterKey,
        inherit_permissions: bool = True
    ) -> Optional[FileRecord]:
        """
        Transform Zammad attachment to FileRecord.

        Args:
            attachment_data: Attachment data from article/answer
            external_record_id: Unique external ID for this attachment
            parent_record: Parent record (TicketRecord or WebpageRecord)
            parent_record_type: Type of parent record (TICKET or WEBPAGE)
            indexing_filter_key: Indexing filter key to check for auto-index
            inherit_permissions: Whether to inherit permissions from parent (default True for tickets, should match parent for answers)

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
        existing_record = None
        async with self.data_store_provider.transaction() as tx_store:
            existing_record = await tx_store.get_record_by_external_id(
                connector_id=self.connector_id,
                external_id=external_record_id
            )

        is_new = existing_record is None
        record_id = existing_record.id if existing_record else str(uuid4())
        version = 0 if is_new else existing_record.version

        # Determine record_group_type from parent
        file_record_group_type = None
        if parent_record.external_record_group_id:
            # If parent has group_{id} format, it's PROJECT type (tickets)
            if parent_record.external_record_group_id.startswith("group_"):
                file_record_group_type = RecordGroupType.PROJECT
            # If parent has cat_{id} format, it's KB type (answers)
            elif parent_record.external_record_group_id.startswith("cat_"):
                file_record_group_type = RecordGroupType.KB
            # If parent has record_group_type set, use it
            elif parent_record.record_group_type:
                file_record_group_type = parent_record.record_group_type

        # Get file extension from filename
        extension = filename.split('.')[-1] if '.' in filename else None

        # Use parent's updated_at as external_revision_id (attachments inherit from parent)
        external_revision_id = str(parent_record.source_updated_at) if parent_record.source_updated_at else None

        file_record = FileRecord(
            id=record_id,
            org_id=self.data_entities_processor.org_id,
            record_type=RecordType.FILE,
            record_name=filename,
            external_record_id=external_record_id,
            external_revision_id=external_revision_id,
            external_record_group_id=parent_record.external_record_group_id,
            record_group_type=file_record_group_type,
            parent_record_id=parent_record.id,
            parent_external_record_id=parent_record.external_record_id,
            parent_record_type=parent_record_type,
            indexing_status=IndexingStatus.NOT_STARTED,
            version=version,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            mime_type=content_type,
            weburl=parent_record.weburl,
            created_at=get_epoch_timestamp_in_ms(),
            updated_at=get_epoch_timestamp_in_ms(),
            source_created_at=parent_record.source_created_at,
            source_updated_at=parent_record.source_updated_at,
            size_in_bytes=size,
            inherit_permissions=inherit_permissions,
            is_file=True,
            extension=extension,
            is_dependent_node=True,
            parent_node_id=parent_record.id,
        )

        # Set indexing status based on indexing filters
        if self.indexing_filters and not self.indexing_filters.is_enabled(indexing_filter_key):
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

    async def _fetch_ticket_links(self, ticket_id: int) -> List[RelatedExternalRecord]:
        """
        Fetch linked tickets and KB answers for a ticket.

        Uses Zammad's Links API to get all objects linked to this ticket,
        including other tickets and KB answers.

        Only creates edges in one direction to avoid duplicates:
        - "parent" links: create edge (child -> parent)
        - "child" links: skip (parent ticket will create the edge)
        - "normal" links: only create if current_ticket_id < linked_ticket_id

        Args:
            ticket_id: Zammad ticket ID

        Returns:
            List of RelatedExternalRecord objects for creating LINKED_TO edges
        """
        related_records: List[RelatedExternalRecord] = []

        try:
            datasource = await self._get_fresh_datasource()
            response = await datasource.list_links(
                link_object="Ticket",
                link_object_value=ticket_id
            )

            if not response.success or not response.data:
                return related_records

            links = response.data.get("links", [])
            assets = response.data.get("assets", {})

            for link in links:
                link_type = link.get("link_type", "normal")
                link_object = link.get("link_object", "")
                link_object_value = link.get("link_object_value")

                if not link_object_value:
                    continue

                # Skip "child" links - the parent ticket will create the edge
                # when it processes its "child" links (which will be "parent" from child's perspective)
                if link_type.lower() == "child":
                    continue

                # For "normal" links between tickets, only create edge in one direction (deterministic by ticket ID)
                if link_type.lower() == "normal" and link_object == "Ticket":
                    try:
                        linked_ticket_id = int(link_object_value)
                        # Only create edge if current ticket ID is less than linked ticket ID
                        # This ensures only one direction is created (A->B, not B->A)
                        if ticket_id >= linked_ticket_id:
                            continue
                    except (ValueError, TypeError):
                        # If link_object_value is not a valid integer, skip this check
                        pass

                # Map Zammad object type to RecordType (inline mapping)
                record_type = ZAMMAD_LINK_OBJECT_MAP.get(link_object)
                if not record_type:
                    # Case-insensitive fallback for KB translations
                    normalized = link_object.lower().strip()
                    if "knowledgebase" in normalized and "answer" in normalized and "translation" in normalized:
                        record_type = RecordType.WEBPAGE
                    else:
                        self.logger.debug(f"Unknown link object type: {link_object}")
                        continue

                # Map Zammad link type to RecordRelations (inline mapping)
                normalized_type = link_type.lower().strip()
                relation_type = ZAMMAD_LINK_TYPE_MAP.get(normalized_type, RecordRelations.LINKED_TO)

                # Build external_record_id based on object type
                if record_type == RecordType.TICKET:
                    # Tickets use ticket ID directly
                    external_record_id = str(link_object_value)
                elif record_type == RecordType.WEBPAGE:
                    # KB answers: link_object_value is translation ID, need to get answer_id from assets
                    # Format in assets: assets["KnowledgeBaseAnswerTranslation"][translation_id]["answer_id"]
                    answer_id = None

                    # Try to get answer_id from KnowledgeBaseAnswerTranslation assets
                    kb_translations = assets.get("KnowledgeBaseAnswerTranslation", {})
                    translation_data = kb_translations.get(str(link_object_value))

                    if translation_data and isinstance(translation_data, dict):
                        answer_id = translation_data.get("answer_id")

                    if answer_id:
                        # KB answers use format: "kb_answer_{answer_id}"
                        external_record_id = f"kb_answer_{answer_id}"
                    else:
                        # Fallback: use translation ID if answer_id not found
                        self.logger.warning(
                            f"Could not find answer_id for KB translation {link_object_value}, "
                            f"using translation ID as fallback"
                        )
                        external_record_id = f"kb_answer_{link_object_value}"
                else:
                    external_record_id = str(link_object_value)

                related_records.append(
                    RelatedExternalRecord(
                        external_record_id=external_record_id,
                        record_type=record_type,
                        relation_type=relation_type
                    )
                )

                self.logger.debug(
                    f"Found link: Ticket {ticket_id} -> {link_object} {link_object_value} "
                    f"(type: {link_type} -> {relation_type.value})"
                )

        except Exception as e:
            self.logger.warning(f"Failed to fetch links for ticket {ticket_id}: {e}")

        return related_records

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

    async def _get_kb_sync_checkpoint(self) -> Optional[int]:
        """
        Get KB sync checkpoint (last_sync_time).

        Returns:
            Last sync timestamp in epoch ms, or None if not set
        """
        data = await self.kb_sync_point.read_sync_point("kb_sync")
        return data.get("last_sync_time") if data else None

    async def _update_kb_sync_checkpoint(self, timestamp: Optional[int] = None) -> None:
        """
        Update KB sync checkpoint.

        Args:
            timestamp: Timestamp to set (defaults to current time if None)
        """
        sync_time = timestamp if timestamp is not None else get_epoch_timestamp_in_ms()
        await self.kb_sync_point.update_sync_point(
            "kb_sync",
            {"last_sync_time": sync_time}
        )
        self.logger.debug(f"💾 Updated KB sync checkpoint: {sync_time}")

    # ==================== KNOWLEDGE BASE SYNCING ====================

    async def _sync_knowledge_bases(self) -> None:
        """
        Sync knowledge bases, categories, and answers from Zammad with incremental support.
        Uses search API for pagination and incremental sync based on updated_at timestamp.
        """
        # Step 1: Get checkpoint for incremental sync
        last_sync_time = await self._get_kb_sync_checkpoint()

        # Step 2: Build query
        if last_sync_time:
            # Incremental: only answers updated since last sync
            dt = datetime.fromtimestamp(last_sync_time / 1000, tz=timezone.utc)
            iso_format = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            query = f"updated_at:[{iso_format} TO *]"
            self.logger.info(f"🔄 Incremental KB sync from {iso_format}")
        else:
            # Full sync: all answers
            query = "*"
            self.logger.info("🔄 Full KB sync")

        # Step 3: Track entities for processing
        kb_map: Dict[int, RecordGroup] = {}
        category_map: Dict[int, RecordGroup] = {}
        category_permissions_map: Dict[int, Dict[str, List[int]]] = {}

        # Step 4: Process first page to get KB and categories, and initial answers
        limit = 50
        datasource = await self._get_fresh_datasource()
        first_response = await datasource.search_kb_answers(query=query, limit=limit, offset=0)
        if not first_response.success or not first_response.data:
            self.logger.info("No KB answers found")
            return

        first_assets = first_response.data.copy()
        first_result_count = first_assets.pop("_result_count", 0)

        await self._process_kb_entities_from_first_page(
            assets=first_assets,
            kb_map=kb_map,
            category_map=category_map,
            category_permissions_map=category_permissions_map
        )

        # Step 5: Process answers from first page and continue pagination
        total_synced, max_updated_at = await self._sync_kb_answers_paginated(
            query=query,
            limit=limit,
            start_offset=0,
            first_page_assets=first_assets,
            first_result_count=first_result_count,
            category_map=category_map,
            category_permissions_map=category_permissions_map
        )

        # Step 6: Update checkpoint
        if max_updated_at > 0:
            await self._update_kb_sync_checkpoint(max_updated_at + 1000)
        elif total_synced > 0:
            await self._update_kb_sync_checkpoint()

        self.logger.info(f"✅ KB sync completed: {total_synced} answers processed")

    async def _process_kb_entities_from_first_page(
        self,
        assets: Dict[str, Any],
        kb_map: Dict[int, RecordGroup],
        category_map: Dict[int, RecordGroup],
        category_permissions_map: Dict[int, Dict[str, List[int]]]
    ) -> None:
        """
        Process KB and categories from the first page response.

        Args:
            assets: Assets dictionary from search response
            kb_map: Output dict to store kb_id -> RecordGroup mapping
            category_map: Output dict to store cat_id -> RecordGroup mapping
            category_permissions_map: Output dict to store category permissions
        """
        datasource = await self._get_fresh_datasource()
        # Process KB (single)
        kb_assets = assets.get("KnowledgeBase", {})
        kb_translations = assets.get("KnowledgeBaseTranslation", {})

        if kb_assets:
            kb_record_groups: List[Tuple[RecordGroup, List[Permission]]] = []

            for kb_id_str, kb_data in kb_assets.items():
                kb_id = int(kb_id_str)

                # Get KB name and locale from translations
                translation_ids = kb_data.get("translation_ids", [])
                kb_name = "Knowledge Base"
                kb_locale = "en-us"  # Default locale
                for trans_id in translation_ids:
                    trans_data = kb_translations.get(str(trans_id))
                    if trans_data:
                        if trans_data.get("title"):
                            kb_name = trans_data.get("title")
                        if trans_data.get("locale"):
                            kb_locale = trans_data.get("locale")
                            break

                # Build KB web URL: /#knowledge_base/{kb_id}/locale/{locale}
                kb_web_url = f"{self.base_url}/#knowledge_base/{kb_id}/locale/{kb_locale}" if self.base_url else None

                kb_record_group = RecordGroup(
                    id=str(uuid4()),
                    org_id=self.data_entities_processor.org_id,
                    external_group_id=f"kb_{kb_id}",
                    connector_id=self.connector_id,
                    connector_name=self.connector_name,
                    name=kb_name,
                    short_name=f"KB-{kb_id}",
                    group_type=RecordGroupType.KB,
                    web_url=kb_web_url
                )

                # KB has no permissions - categories have their own permissions, no inheritance
                kb_permissions: List[Permission] = []

                kb_record_groups.append((kb_record_group, kb_permissions))
                kb_map[kb_id] = kb_record_group

            if kb_record_groups:
                await self.data_entities_processor.on_new_record_groups(kb_record_groups)
                self.logger.info(f"✅ Synced {len(kb_record_groups)} knowledge base(s)")

        # Process categories with permissions
        category_assets = assets.get("KnowledgeBaseCategory", {})
        category_translations = assets.get("KnowledgeBaseCategoryTranslation", {})

        if category_assets:
            category_record_groups: List[Tuple[RecordGroup, List[Permission]]] = []

            for cat_id_str, cat_data in category_assets.items():
                cat_id = int(cat_id_str)
                kb_id = cat_data.get("knowledge_base_id")

                if not kb_id:
                    continue

                # Get category name from translations
                translation_ids = cat_data.get("translation_ids", [])
                cat_name = "Category"
                for trans_id in translation_ids:
                    trans_data = category_translations.get(str(trans_id))
                    if trans_data:
                        if trans_data.get("title"):
                            cat_name = trans_data.get("title")
                            break

                # Get KB locale for building category URL
                kb_locale = "en-us"  # Default
                if kb_id:
                    kb_data_for_locale = kb_assets.get(str(kb_id), {})
                    kb_trans_ids = kb_data_for_locale.get("translation_ids", [])
                    for trans_id in kb_trans_ids:
                        trans_data = kb_translations.get(str(trans_id))
                        if trans_data and trans_data.get("locale"):
                            kb_locale = trans_data.get("locale")
                            break

                # Build category web URL: /#knowledge_base/{kb_id}/locale/{locale}/category/{cat_id}
                cat_web_url = f"{self.base_url}/#knowledge_base/{kb_id}/locale/{kb_locale}/category/{cat_id}" if self.base_url and kb_id else None

                # Determine parent
                parent_cat_id = cat_data.get("parent_id")
                if parent_cat_id and parent_cat_id in category_map:
                    parent_external_group_id = f"cat_{parent_cat_id}"
                elif kb_id in kb_map:
                    parent_external_group_id = f"kb_{kb_id}"
                else:
                    parent_external_group_id = None

                # Extract permissions from permissions_effective if present
                permissions_effective = cat_data.get("permissions_effective", [])
                editor_role_ids: List[int] = []
                reader_role_ids: List[int] = []

                if permissions_effective:
                    editor_role_ids = [
                        p["role_id"] for p in permissions_effective
                        if p.get("access") == "editor"
                    ]
                    reader_role_ids = [
                        p["role_id"] for p in permissions_effective
                        if p.get("access") == "reader"
                    ]
                else:
                    # Fetch from API if not in response
                    try:
                        perm_response = await datasource.get_kb_category_permissions(
                            kb_id=kb_id,
                            cat_id=cat_id
                        )
                        if perm_response.success and perm_response.data:
                            # Use 'permissions' array for actual access levels (not roles_editor/roles_reader)
                            # permissions array has: {"id": X, "access": "editor|reader|none", "role_id": Y}
                            permissions_list = perm_response.data.get("permissions", [])
                            editor_role_ids = [
                                p["role_id"] for p in permissions_list
                                if p.get("access") == "editor"
                            ]
                            reader_role_ids = [
                                p["role_id"] for p in permissions_list
                                if p.get("access") == "reader"
                            ]
                    except Exception as e:
                        self.logger.warning(f"Failed to fetch permissions for category {cat_id}: {e}")

                # Store permissions and KB ID for answer processing
                category_permissions_map[cat_id] = {
                    "kb_id": kb_id,
                    "editor_role_ids": editor_role_ids,
                    "reader_role_ids": reader_role_ids
                }

                # Create category permissions (all roles: editor + reader)
                cat_permissions: List[Permission] = []
                for role_id in editor_role_ids:
                    cat_permissions.append(Permission(
                        entity_type=EntityType.ROLE,
                        type=PermissionType.WRITE,
                        external_id=str(role_id)
                    ))
                for role_id in reader_role_ids:
                    cat_permissions.append(Permission(
                        entity_type=EntityType.ROLE,
                        type=PermissionType.READ,
                        external_id=str(role_id)
                    ))

                # If no role permissions, default to ORG permission
                # Each category has its own permissions, no inheritance from KB
                # if not cat_permissions:
                #     cat_permissions.append(Permission(
                #         entity_type=EntityType.ORG,
                #         type=PermissionType.READ,
                #         external_id=None
                #     ))

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
                    inherit_permissions=False,  # Categories have their own permissions, no inheritance
                    web_url=cat_web_url,
                )

                category_record_groups.append((cat_record_group, cat_permissions))
                category_map[cat_id] = cat_record_group

            if category_record_groups:
                await self.data_entities_processor.on_new_record_groups(category_record_groups)
                self.logger.info(f"✅ Synced {len(category_record_groups)} KB categories")

    async def _sync_kb_answers_paginated(
        self,
        query: str,
        limit: int,
        start_offset: int,
        first_page_assets: Dict[str, Any],
        first_result_count: int,
        category_map: Dict[int, RecordGroup],
        category_permissions_map: Dict[int, Dict[str, List[int]]]
    ) -> Tuple[int, int]:
        """
        Sync KB answers with pagination.

        Args:
            query: Search query string
            limit: Page size for pagination
            start_offset: Starting offset (usually 0)
            first_page_assets: Assets from first page (already fetched)
            first_result_count: Result count from first page
            category_map: Map of category_id -> RecordGroup
            category_permissions_map: Map of category_id -> permissions dict

        Returns:
            Tuple of (total_synced_count, max_updated_at_timestamp)
        """
        datasource = await self._get_fresh_datasource()
        offset = start_offset
        total_synced = 0
        max_updated_at = 0
        is_first_page = True

        while True:
            # Use first page data if available, otherwise fetch
            if is_first_page:
                is_first_page = False
                assets = first_page_assets
                result_count = first_result_count
            else:
                response = await datasource.search_kb_answers(
                    query=query,
                    limit=limit,
                    offset=offset
                )

                if not response.success:
                    self.logger.warning(f"Failed to search KB answers: {response.message}")
                    break

                if not response.data:
                    break

                assets = response.data.copy()

                # Get result count for proper pagination
                result_count = assets.pop("_result_count", 0)

            # Process Answers
            answer_assets = assets.get("KnowledgeBaseAnswer", {})
            answer_translations = assets.get("KnowledgeBaseAnswerTranslation", {})

            if not answer_assets:
                break

            batch_records: List[Tuple[Record, List[Permission]]] = []

            for answer_id_str, answer_data in answer_assets.items():
                answer_id = int(answer_id_str)

                try:
                    # Track max updated_at for checkpoint
                    answer_updated_at = self._parse_zammad_datetime(answer_data.get("updated_at", ""))
                    if answer_updated_at and answer_updated_at > max_updated_at:
                        max_updated_at = answer_updated_at

                    # Get category permissions
                    category_id = answer_data.get("category_id")
                    cat_perms = category_permissions_map.get(category_id, {})
                    editor_role_ids = cat_perms.get("editor_role_ids", [])

                    # Determine visibility
                    visibility = self._determine_visibility(answer_data)

                    # Enrich answer with translations
                    translation_ids = answer_data.get("translation_ids", [])
                    translations = []
                    # Get content assets if available
                    content_assets = assets.get("KnowledgeBaseAnswerTranslationContent", {})
                    for tid in translation_ids:
                        trans_data = answer_translations.get(str(tid))
                        if trans_data:
                            # Extract content from KnowledgeBaseAnswerTranslationContent using content_id
                            content_id = trans_data.get("content_id")
                            if content_id:
                                content_data = content_assets.get(str(content_id))
                                if content_data:
                                    # Add content body and attachments to translation
                                    trans_data["body"] = content_data.get("body", "")
                                    trans_data["content"] = {"body": content_data.get("body", "")}
                                    # Merge attachments from content (if any)
                                    content_attachments = content_data.get("attachments", [])
                                    if content_attachments:
                                        trans_data["attachments"] = content_attachments
                            translations.append(trans_data)
                    answer_data["translations"] = translations

                    # Check for existing record before creating/updating
                    external_record_id = f"kb_answer_{answer_id}"
                    existing_record = None
                    async with self.data_store_provider.transaction() as tx_store:
                        existing_record = await tx_store.get_record_by_external_id(
                            connector_id=self.connector_id,
                            external_id=external_record_id
                        )

                    # Create record with visibility-based permissions
                    answer_record, permissions = self._create_answer_with_permissions(
                        answer_data=answer_data,
                        category_id=category_id,
                        visibility=visibility,
                        editor_role_ids=editor_role_ids,
                        category_map=category_map,
                        existing_record=existing_record
                    )

                    if answer_record:
                        # Set indexing status based on indexing filters
                        if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.KNOWLEDGE_BASE):
                            answer_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                        # Check if this is an existing record with changed permissions
                        # When visibility changes (PUBLIC/INTERNAL/DRAFT/ARCHIVED), inherit_permissions changes too
                        needs_permission_update = False
                        if existing_record is not None:
                            old_inherit = getattr(existing_record, 'inherit_permissions', None)
                            new_inherit = answer_record.inherit_permissions
                            # Detect permission change: inherit_permissions flag changed OR
                            # record was updated (revision changed) - always refresh permissions on update
                            if old_inherit != new_inherit or existing_record.external_revision_id != answer_record.external_revision_id:
                                needs_permission_update = True
                                self.logger.debug(f"Permission update needed for answer {answer_id}: inherit_permissions {old_inherit} -> {new_inherit}")

                        batch_records.append((answer_record, permissions, needs_permission_update))
                        total_synced += 1

                        # Fetch attachments for the answer
                        attachment_records = await self._fetch_kb_answer_attachments(
                            answer_data=answer_data,
                            answer_record=answer_record,
                            answer_permissions=permissions
                        )
                        if attachment_records:
                            # Attachments also need permission update if parent does
                            for att_record, att_perms in attachment_records:
                                batch_records.append((att_record, att_perms, needs_permission_update))

                except Exception as e:
                    self.logger.warning(f"Failed to process KB answer {answer_id}: {e}", exc_info=True)

            # Save batch
            if batch_records:
                # Separate records that need permission updates from new records
                records_for_new = [(rec, perms) for rec, perms, needs_update in batch_records]
                records_needing_permission_update = [(rec, perms) for rec, perms, needs_update in batch_records if needs_update]

                # Count answers vs attachments separately
                batch_answers = sum(1 for record, _, _ in batch_records if isinstance(record, WebpageRecord))
                batch_attachments = len(batch_records) - batch_answers
                await self.data_entities_processor.on_new_records(records_for_new)

                # Update permissions for records that had visibility/permission changes
                # This properly cleans up old permission edges before adding new ones
                for record, perms in records_needing_permission_update:
                    await self.data_entities_processor.on_updated_record_permissions(record, perms)
                if batch_attachments > 0:
                    self.logger.debug(f"Processed batch: {batch_answers} KB answers, {batch_attachments} attachments")
                else:
                    self.logger.debug(f"Processed batch: {batch_answers} KB answers")

            # Check pagination using result count (not answer count)
            if result_count < limit:
                break

            offset += limit

        return total_synced, max_updated_at

    def _determine_visibility(self, answer_data: Dict[str, Any]) -> str:
        """
        Determine answer visibility based on timestamps.

        Args:
            answer_data: Answer data from Zammad

        Returns:
            Visibility state: "PUBLIC", "INTERNAL", "ARCHIVED", or "DRAFT"
        """
        if answer_data.get("published_at"):
            return "PUBLIC"
        elif answer_data.get("internal_at"):
            return "INTERNAL"
        elif answer_data.get("archived_at"):
            return "ARCHIVED"
        else:
            return "DRAFT"

    def _create_answer_with_permissions(
        self,
        answer_data: Dict[str, Any],
        category_id: Optional[int],
        visibility: str,
        editor_role_ids: List[int],
        category_map: Dict[int, RecordGroup],
        existing_record: Optional[Record] = None
    ) -> Tuple[Optional[WebpageRecord], List[Permission]]:
        """
        Create answer record with visibility-based permissions.

        Args:
            answer_data: Answer data from Zammad
            category_id: Category ID for the answer
            visibility: Visibility state (PUBLIC, INTERNAL, ARCHIVED, DRAFT)
            editor_role_ids: List of editor role IDs for ARCHIVED/DRAFT visibility
            category_map: Map of category_id -> RecordGroup
            existing_record: Optional existing record for version handling (for updates)

        Returns:
            Tuple of (WebpageRecord, List[Permission])
        """
        answer_id = answer_data.get("id")
        if not answer_id:
            return None, []

        external_record_group_id = f"cat_{category_id}" if category_id else None

        # Determine record_group_type based on external_record_group_id format
        kb_record_group_type = None
        if external_record_group_id and external_record_group_id.startswith("cat_"):
            kb_record_group_type = RecordGroupType.KB

        # Get title, content, and locale from translations
        translations = answer_data.get("translations", [])
        title = "KB Answer"
        content_body = None
        answer_locale = "en-us"  # Default locale

        for trans in translations:
            trans_title = trans.get("title")

            # Content body can be in different locations depending on response format
            trans_content_body = None
            if isinstance(trans.get("content"), dict):
                trans_content_body = trans.get("content", {}).get("body")
            if not trans_content_body:
                trans_content_body = trans.get("content_body")
            if not trans_content_body:
                trans_content_body = trans.get("body")

            if trans_title:
                title = trans_title
            if trans_content_body:
                content_body = trans_content_body
            if trans.get("locale"):
                answer_locale = trans.get("locale")

            if title and title != "KB Answer" and content_body:
                break

        # Get KB ID from category's parent (simple extraction)
        kb_id = 1  # Default (Zammad typically has one KB per instance)
        if category_id:
            cat_rg = category_map.get(category_id)
            if cat_rg and cat_rg.parent_external_group_id and cat_rg.parent_external_group_id.startswith("kb_"):
                try:
                    kb_id = int(cat_rg.parent_external_group_id.replace("kb_", ""))
                except (ValueError, AttributeError):
                    pass

        # Build answer web URL: /#knowledge_base/{kb_id}/locale/{locale}/answer/{answer_id}
        answer_weburl = f"{self.base_url}/#knowledge_base/{kb_id}/locale/{answer_locale}/answer/{answer_id}" if self.base_url else ""

        # Parse timestamps
        created_at = self._parse_zammad_datetime(answer_data.get("created_at", ""))
        updated_at = self._parse_zammad_datetime(answer_data.get("updated_at", ""))

        external_record_id = f"kb_answer_{answer_id}"
        external_revision_id = str(updated_at) if updated_at else None

        # Only INTERNAL inherits from category (reader + editor roles)
        inherit_permissions = visibility == "INTERNAL"

        # Handle versioning
        is_new = existing_record is None
        record_id = existing_record.id if existing_record else str(uuid4())
        version = 0 if is_new else (existing_record.version + 1 if existing_record.source_updated_at != updated_at else existing_record.version)

        webpage_record = WebpageRecord(
            id=record_id,
            org_id=self.data_entities_processor.org_id,
            record_type=RecordType.WEBPAGE,
            record_name=title,
            external_record_id=external_record_id,
            external_revision_id=external_revision_id,
            external_record_group_id=external_record_group_id,
            record_group_type=kb_record_group_type,
            indexing_status=IndexingStatus.NOT_STARTED,
            version=version,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            weburl=answer_weburl,
            created_at=get_epoch_timestamp_in_ms(),
            updated_at=get_epoch_timestamp_in_ms(),
            source_created_at=created_at,
            source_updated_at=updated_at,
            inherit_permissions=inherit_permissions,
            preview_renderable=False,
        )

        # Create permissions based on visibility
        permissions: List[Permission] = []
        if visibility == "PUBLIC":
            # Everyone can access via direct ORG permission
            permissions.append(Permission(entity_type=EntityType.ORG, type=PermissionType.READ))
        elif visibility in ["ARCHIVED", "DRAFT"]:
            # Only editors can access - they should have WRITE permission since they are editors
            for role_id in editor_role_ids:
                permissions.append(Permission(entity_type=EntityType.ROLE, type=PermissionType.WRITE, external_id=str(role_id)))
        # INTERNAL: no direct permissions, inherits from category

        return webpage_record, permissions

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

    async def _build_ticket_attachment_child_records(
        self,
        ticket_id: str,
        article_id: str,
        attachments: List[Dict[str, Any]],
        parent_record: Record
    ) -> List[ChildRecord]:
        """
        Build child records for ticket article attachments.
        Looks up existing records or creates them if they don't exist.

        Args:
            ticket_id: Ticket ID
            article_id: Article ID
            attachments: List of attachment data from article
            parent_record: Parent TicketRecord

        Returns:
            List of ChildRecord objects for attachments
        """
        child_records = []
        async with self.data_store_provider.transaction() as tx_store:
            for att in attachments:
                att_id = att.get("id")
                att_filename = att.get("filename", "")
                if not att_id:
                    continue

                # Ticket attachment external_record_id format: {ticket_id}_{article_id}_{attachment_id}
                external_record_id = f"{ticket_id}_{article_id}_{att_id}"

                # Look up existing record to get the actual record ID
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=external_record_id
                )

                if existing_record:
                    child_records.append(ChildRecord(
                        child_type=ChildType.RECORD,
                        child_id=existing_record.id,
                        child_name=existing_record.record_name or att_filename
                    ))
                else:
                    # Create the record if it doesn't exist
                    try:
                        file_record = await self._transform_attachment_to_file_record(
                            attachment_data=att,
                            external_record_id=external_record_id,
                            parent_record=parent_record,
                            parent_record_type=RecordType.TICKET,
                            indexing_filter_key=IndexingFilterKey.ISSUE_ATTACHMENTS
                        )
                        if file_record:
                            # Save the record - FileRecords inherit permissions from group (inherit_permissions=True by default)
                            await self.data_entities_processor.on_new_records([(file_record, [])])
                            child_records.append(ChildRecord(
                                child_type=ChildType.RECORD,
                                child_id=file_record.id,
                                child_name=file_record.record_name or att_filename
                            ))
                    except Exception as e:
                        self.logger.warning(f"Failed to create attachment record {external_record_id}: {e}")
                        continue

        return child_records

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
            first_article_id = first_article.get("id")

            # Get attachments for first article (description) as children_records
            first_article_attachments = first_article.get("attachments", [])
            description_children_records: List[ChildRecord] = []
            if first_article_id and first_article_attachments:
                description_children_records = await self._build_ticket_attachment_child_records(
                    ticket_id=ticket_id,
                    article_id=str(first_article_id),
                    attachments=first_article_attachments,
                    parent_record=record
                )

            # Convert HTML to Markdown
            description_body_md = html_to_markdown(description_body_html) if description_body_html else ""
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
                article_id = article.get("id")
                article_body_html = article.get("body", "")
                article_from = article.get("from", "")
                article_subject = article.get("subject", "")
                article_sender = article.get("sender", "")
                article_preferences = article.get("preferences", {})

                # Skip articles without ID
                if not article_id:
                    continue

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

                # Convert HTML to Markdown
                article_body_md = html_to_markdown(article_body_html)
                # Get author name
                author_name = article_from if article_from else "Unknown"

                # Build comment name
                comment_name = f"Comment by {author_name}"
                if article_subject:
                    comment_name = f"{article_subject} - {author_name}"

                # Get attachments for this article as children_records
                article_attachments = article.get("attachments", [])
                comment_children_records: List[ChildRecord] = []
                if article_attachments:
                    comment_children_records = await self._build_ticket_attachment_child_records(
                        ticket_id=ticket_id,
                        article_id=str(article_id),
                        attachments=article_attachments,
                        parent_record=record
                    )

                comment_block_group = BlockGroup(
                    id=str(uuid4()),
                    index=block_group_index,
                    parent_index=0,
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
        
        # Populate children arrays for BlockGroups
        # Build a map of parent_index -> list of child BlockGroup indices
        blockgroup_children_map: Dict[int, List[int]] = defaultdict(list)
        for bg in block_groups:
            if bg.parent_index is not None:
                blockgroup_children_map[bg.parent_index].append(bg.index)
        
        # Now populate the children arrays using range-based structure
        for bg in block_groups:
            if bg.index in blockgroup_children_map:
                child_bg_indices = sorted(blockgroup_children_map[bg.index])
                # Convert to range-based structure
                bg.children = BlockGroupChildren.from_indices(block_group_indices=child_bg_indices)

        blocks_container = BlocksContainer(blocks=blocks, block_groups=block_groups)
        return blocks_container.model_dump_json(indent=2).encode('utf-8')

    async def _build_kb_answer_child_records(
        self,
        answer_id: int,
        answer_data: Dict[str, Any],
        answer_attachments: List[Dict[str, Any]],
        record: Record,
        kb_id: int
    ) -> List[ChildRecord]:
        """
        Build child records for KB answer attachments.
        Looks up existing records or creates them if they don't exist, with proper permissions.

        Args:
            answer_id: KB answer ID
            answer_data: Answer data from Zammad API
            answer_attachments: List of attachment data from answer
            record: Parent WebpageRecord
            kb_id: Knowledge base ID

        Returns:
            List of ChildRecord objects for attachments
        """
        # Calculate answer permissions based on visibility (same as during sync)
        answer_permissions: List[Permission] = []
        visibility = self._determine_visibility(answer_data) if answer_data else "DRAFT"

        if visibility == "PUBLIC":
            # Everyone can access via direct ORG permission
            answer_permissions.append(Permission(entity_type=EntityType.ORG, type=PermissionType.READ))
        elif visibility in ["ARCHIVED", "DRAFT"]:
            # Only editors can access - need to get editor_role_ids from category
            category_id = answer_data.get("category_id") if answer_data else None
            editor_role_ids: List[int] = []

            if category_id:
                try:
                    # Fetch category permissions to get editor role IDs
                    datasource = await self._get_fresh_datasource()
                    perm_response = await datasource.get_kb_category_permissions(
                        kb_id=kb_id,
                        cat_id=category_id
                    )
                    if perm_response.success and perm_response.data:
                        # Use 'permissions' array for actual access levels (not roles_editor/roles_reader)
                        # permissions array has: {"id": X, "access": "editor|reader|none", "role_id": Y}
                        permissions_list = perm_response.data.get("permissions", [])
                        editor_role_ids = [
                            p["role_id"] for p in permissions_list
                            if p.get("access") == "editor"
                        ]
                except Exception as e:
                    self.logger.debug(f"Failed to fetch category permissions for category {category_id}: {e}")

            # Add editor role permissions
            for role_id in editor_role_ids:
                answer_permissions.append(Permission(entity_type=EntityType.ROLE, type=PermissionType.WRITE, external_id=str(role_id)))
        # INTERNAL: no direct permissions, inherits from category (empty list)

        # Use the same inherit_permissions value as the parent answer
        answer_inherit_permissions = record.inherit_permissions if hasattr(record, 'inherit_permissions') else (visibility == "INTERNAL")

        # Build children_records for attachments
        answer_children_records = []
        async with self.data_store_provider.transaction() as tx_store:
            for att in answer_attachments:
                att_id = att.get("id")
                att_filename = att.get("filename", "")
                if not att_id:
                    continue

                # KB answer attachment external_record_id format: kb_answer_{answer_id}_attachment_{attachment_id}
                external_record_id = f"kb_answer_{answer_id}_attachment_{att_id}"

                # Look up existing record to get the actual record ID
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=external_record_id
                )

                if existing_record:
                    answer_children_records.append(ChildRecord(
                        child_type=ChildType.RECORD,
                        child_id=existing_record.id,
                        child_name=existing_record.record_name or att_filename
                    ))
                else:
                    # Create the record if it doesn't exist
                    try:
                        file_record = await self._transform_attachment_to_file_record(
                            attachment_data=att,
                            external_record_id=external_record_id,
                            parent_record=record,
                            parent_record_type=RecordType.WEBPAGE,
                            indexing_filter_key=IndexingFilterKey.KNOWLEDGE_BASE,
                            inherit_permissions=answer_inherit_permissions
                        )
                        if file_record:
                            # Save the record with the same permissions as the parent answer
                            # For PUBLIC: ORG permission, inherit_permissions=False
                            # For ARCHIVED/DRAFT: Editor role permissions, inherit_permissions=False
                            # For INTERNAL: Empty list, inherit_permissions=True will handle category inheritance
                            await self.data_entities_processor.on_new_records([(file_record, answer_permissions)])
                            answer_children_records.append(ChildRecord(
                                child_type=ChildType.RECORD,
                                child_id=file_record.id,
                                child_name=file_record.record_name or att_filename
                            ))
                    except Exception as e:
                        self.logger.warning(f"Failed to create attachment record {external_record_id}: {e}")
                        continue

        return answer_children_records

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

        # Get KB ID from init response or use default (Zammad has single KB per instance)
        kb_id = 1  # Default KB ID
        try:
            init_response = await datasource.init_knowledge_base()
            if init_response.success and init_response.data:
                kb_assets = init_response.data.get("KnowledgeBase", {})
                if kb_assets:
                    kb_id = int(list(kb_assets.keys())[0])
        except Exception:
            pass

        # Try to fetch KB answer using correct endpoint format
        answer_response = await datasource.get_kb_answer(id=answer_id, kb_id=kb_id)

        title = record.record_name
        body = ""
        answer_attachments: List[Dict[str, Any]] = []
        answer_data: Dict[str, Any] = {}  # Initialize to empty dict to avoid undefined variable

        if answer_response.success and answer_response.data:
            response_data = answer_response.data

            # Handle assets structure (Zammad API returns data in assets object)
            if isinstance(response_data, dict) and "assets" in response_data:
                assets = response_data.get("assets", {})
                # Extract answer from assets.KnowledgeBaseAnswer
                answer_assets = assets.get("KnowledgeBaseAnswer", {})
                answer_data = answer_assets.get(str(answer_id), {})

                # Extract translations from assets.KnowledgeBaseAnswerTranslation
                answer_translations = assets.get("KnowledgeBaseAnswerTranslation", {})
                # Extract content from assets.KnowledgeBaseAnswerTranslationContent
                content_assets = assets.get("KnowledgeBaseAnswerTranslationContent", {})
            else:
                # Fallback: assume direct answer data structure
                answer_data = response_data if isinstance(response_data, dict) else {}
                answer_translations = {}
                content_assets = {}

            # Get attachments from answer data
            answer_attachments = answer_data.get("attachments", [])

            # Get title and content from translations
            translation_ids = answer_data.get("translation_ids", [])
            translations = []
            for tid in translation_ids:
                trans_data = answer_translations.get(str(tid))
                if trans_data:
                    # Extract content from KnowledgeBaseAnswerTranslationContent using content_id
                    content_id = trans_data.get("content_id")
                    if content_id:
                        content_data = content_assets.get(str(content_id))
                        if content_data:
                            # Add content body and attachments to translation
                            trans_data["body"] = content_data.get("body", "")
                            trans_data["content"] = {"body": content_data.get("body", "")}
                            # Merge attachments from content (if any)
                            content_attachments = content_data.get("attachments", [])
                            if content_attachments:
                                trans_data["attachments"] = content_attachments
                    translations.append(trans_data)

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

                    # Also check for attachments in translations
                    trans_attachments = trans.get("attachments", [])
                    if trans_attachments:
                        answer_attachments.extend(trans_attachments)

                    break

        # Build children_records for attachments using helper function
        answer_children_records = await self._build_kb_answer_child_records(
            answer_id=answer_id,
            answer_data=answer_data,
            answer_attachments=answer_attachments,
            record=record,
            kb_id=kb_id
        )

        # Convert embedded images to base64 before converting HTML to Markdown
        if body:
            body = await self._convert_html_images_to_base64(body)

        # Convert HTML body to Markdown (same as ticket streaming)
        body_md = html_to_markdown(body) if body else ""
        block_content = f"# {title}\n\n{body_md}" if body_md else f"# {title}"

        # Build single BlockGroup for answer content
        answer_block_group = BlockGroup(
            id=str(uuid4()),
            index=0,
            name=title,
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.CONTENT,
            description=f"KB Answer: {title}",
            source_group_id=str(answer_id),
            data=block_content,
            format=DataFormat.MARKDOWN,
            weburl=record.weburl,
            requires_processing=True,
            children_records=answer_children_records if answer_children_records else None,
        )

        blocks_container = BlocksContainer(blocks=[], block_groups=[answer_block_group])
        return blocks_container.model_dump_json(indent=2).encode('utf-8')

    async def _convert_html_images_to_base64(self, html_content: str) -> str:
        """
        Convert embedded images in HTML content to base64 data URIs.

        Finds all <img> tags with src pointing to Zammad attachments (e.g., /api/v1/attachments/9)
        and replaces them with base64-encoded data URIs in markdown format.

        Args:
            html_content: HTML content that may contain embedded images

        Returns:
            HTML content with images converted to base64 data URIs (ready for markdown conversion)
        """
        if not html_content:
            return html_content

        # Parse HTML with BeautifulSoup
        soup = BeautifulSoup(html_content, 'html.parser')
        img_tags = soup.find_all('img')

        if not img_tags:
            return html_content

        # Initialize datasource if needed
        if not self.data_source:
            await self.init()

        datasource = await self._get_fresh_datasource()

        # Process each image tag
        for img_tag in img_tags:
            src = img_tag.get('src', '')
            if not src:
                continue

            # Check if this is a Zammad attachment URL
            attachment_match = re.search(r'/api/v1/attachments/(\d+)', src)
            if not attachment_match:
                continue

            attachment_id = int(attachment_match.group(1))

            try:
                # Download attachment using datasource
                response = await datasource.get_kb_answer_attachment(
                    id=attachment_id
                )

                if not response.success or response.data is None:
                    self.logger.debug(f"⚠️ Failed to download attachment {attachment_id}: {response.message}")
                    continue  # Skip on error

                image_bytes = response.data
                if isinstance(image_bytes, str):
                    image_bytes = image_bytes.encode('utf-8')

                if not image_bytes:
                    self.logger.debug(f"⚠️ Empty image content from attachment {attachment_id}")
                    continue

                # Determine image type from Content-Type header or URL
                # Try to infer from URL first
                image_type = "png"  # default
                url_lower = src.lower()
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
                else:
                    # Try to detect from content (basic check)
                    if image_bytes.startswith(b'\x89PNG'):
                        image_type = "png"
                    elif image_bytes.startswith(b'\xff\xd8\xff'):
                        image_type = "jpeg"
                    elif image_bytes.startswith(b'GIF'):
                        image_type = "gif"
                    elif image_bytes.startswith(b'<svg') or image_bytes.startswith(b'<?xml'):
                        image_type = "svg"

                # Convert to base64
                base64_encoded = base64.b64encode(image_bytes).decode('utf-8')

                # Create data URI
                if image_type == "svg":
                    data_uri = f"data:image/svg+xml;base64,{base64_encoded}"
                else:
                    data_uri = f"data:image/{image_type};base64,{base64_encoded}"

                # Replace the src attribute with the data URI
                img_tag['src'] = data_uri

            except Exception as e:
                self.logger.debug(f"⚠️ Failed to convert image {src} to base64: {e}")
                continue  # Skip on error

        return str(soup)

    async def _process_file_for_streaming(self, record: Record) -> bytes:
        """
        Process file/attachment for streaming.
        Handles both ticket attachments and KB answer attachments.

        Args:
            record: FileRecord to process (can be ticket attachment or KB answer attachment)

        Returns:
            File content as bytes
        """
        external_id = record.external_record_id
        datasource = await self._get_fresh_datasource()

        # Check if this is a KB answer attachment (format: kb_answer_{answer_id}_attachment_{attachment_id})
        if external_id.startswith("kb_answer_") and "_attachment_" in external_id:
            # Parse KB answer attachment ID
            # Format: kb_answer_{answer_id}_attachment_{attachment_id}
            parts = external_id.replace("kb_answer_", "").split("_attachment_")
            if len(parts) != KB_ANSWER_ATTACHMENT_PARTS_COUNT:
                raise ValueError(f"Invalid KB answer attachment ID format: {external_id}")

            answer_id, attachment_id = parts

            # Download KB answer attachment
            response = await datasource.get_kb_answer_attachment(
                id=int(attachment_id)
            )

            if not response.success:
                raise Exception(f"Failed to download KB answer attachment: {response.message}")

            # Return raw content bytes
            content = response.data
            if isinstance(content, str):
                return content.encode('utf-8')
            elif isinstance(content, bytes):
                return content
            else:
                return str(content).encode('utf-8')
        else:
            # Parse ticket attachment ID (format: {ticket_id}_{article_id}_{attachment_id})
            parts = external_id.split("_")
            if len(parts) != ATTACHMENT_ID_PARTS_COUNT:
                raise ValueError(f"Invalid attachment ID format: {external_id}")

            ticket_id, article_id, attachment_id = parts

            # Download ticket attachment
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
        """Fetch KB answer from source for reindexing with visibility-based permissions."""
        try:
            # Extract answer ID from external_record_id (format: kb_answer_{id})
            external_id = record.external_record_id
            if not external_id.startswith("kb_answer_"):
                self.logger.warning(f"Invalid KB answer external_record_id format: {external_id}")
                return None

            answer_id = int(external_id.replace("kb_answer_", ""))

            # Fetch KB answer from source
            datasource = await self._get_fresh_datasource()

            # Get KB ID from init response or use default (Zammad has single KB per instance)
            kb_id = 1
            try:
                init_response = await datasource.init_knowledge_base()
                if init_response.success and init_response.data:
                    kb_assets = init_response.data.get("KnowledgeBase", {})
                    if kb_assets:
                        kb_id = int(list(kb_assets.keys())[0])
            except Exception as e:
                self.logger.debug(f"Failed to get KB ID for reindexing, using default: {e}")

            response = await datasource.get_kb_answer(id=answer_id, kb_id=kb_id)

            if not response.success:
                self.logger.warning(f"KB answer {answer_id} not found at source: {response.message if hasattr(response, 'message') else 'Unknown error'}")
                return None

            if not response.data:
                self.logger.warning(f"No KB answer data found for {answer_id}")
                return None

            response_data = response.data

            # Handle assets structure (Zammad API returns data in assets object)
            if isinstance(response_data, dict) and "assets" in response_data:
                assets = response_data.get("assets", {})
                # Extract answer from assets.KnowledgeBaseAnswer
                answer_assets = assets.get("KnowledgeBaseAnswer", {})
                answer_data = answer_assets.get(str(answer_id), {})

                # Extract translations from assets.KnowledgeBaseAnswerTranslation
                answer_translations = assets.get("KnowledgeBaseAnswerTranslation", {})
                # Extract content from assets.KnowledgeBaseAnswerTranslationContent
                content_assets = assets.get("KnowledgeBaseAnswerTranslationContent", {})

                # Enrich answer with translations (same as sync method)
                translation_ids = answer_data.get("translation_ids", [])
                translations = []
                for tid in translation_ids:
                    trans_data = answer_translations.get(str(tid))
                    if trans_data:
                        # Extract content from KnowledgeBaseAnswerTranslationContent using content_id
                        content_id = trans_data.get("content_id")
                        if content_id:
                            content_data = content_assets.get(str(content_id))
                            if content_data:
                                # Add content body and attachments to translation
                                trans_data["body"] = content_data.get("body", "")
                                trans_data["content"] = {"body": content_data.get("body", "")}
                                # Merge attachments from content (if any)
                                content_attachments = content_data.get("attachments", [])
                                if content_attachments:
                                    trans_data["attachments"] = content_attachments
                        translations.append(trans_data)
                answer_data["translations"] = translations
            else:
                # Fallback: assume direct answer data structure
                answer_data = response_data

            current_updated_at = self._parse_zammad_datetime(answer_data.get("updated_at", ""))

            # Compare with stored timestamp
            if hasattr(record, 'source_updated_at') and record.source_updated_at and current_updated_at:
                if record.source_updated_at == current_updated_at:
                    self.logger.debug(f"KB answer {answer_id} has not changed at source")
                    return None

            self.logger.info(f"KB answer {answer_id} has changed at source")

            # Get category info and permissions
            category_id = answer_data.get("category_id")
            category_map: Dict[int, RecordGroup] = {}
            editor_role_ids: List[int] = []

            async with self.data_store_provider.transaction() as tx_store:
                if record.external_record_group_id:
                    cat_rg = await tx_store.get_record_group_by_external_id(
                        connector_id=self.connector_id,
                        external_id=record.external_record_group_id
                    )
                    if cat_rg:
                        category_id = int(record.external_record_group_id.replace("cat_", ""))
                        category_map[category_id] = cat_rg

            # Fetch category permissions for visibility-based permission handling
            if category_id:
                try:
                    perm_response = await datasource.get_kb_category_permissions(
                        kb_id=kb_id,
                        cat_id=category_id
                    )
                    if perm_response.success and perm_response.data:
                        # Use 'permissions' array for actual access levels (not roles_editor/roles_reader)
                        permissions_list = perm_response.data.get("permissions", [])
                        editor_role_ids = [
                            p["role_id"] for p in permissions_list
                            if p.get("access") == "editor"
                        ]
                except Exception as e:
                    self.logger.warning(f"Failed to fetch category permissions for reindexing: {e}")

            # Determine visibility
            visibility = self._determine_visibility(answer_data)

            # Create answer with visibility-based permissions
            updated_answer, permissions = self._create_answer_with_permissions(
                answer_data=answer_data,
                category_id=category_id,
                visibility=visibility,
                editor_role_ids=editor_role_ids,
                category_map=category_map,
                existing_record=record
            )

            if not updated_answer:
                return None

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
