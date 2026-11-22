"""Zammad Connector Implementation"""
import asyncio
import base64
import json
import mimetypes
import os
import re
from dataclasses import dataclass, field
from collections import defaultdict
from datetime import datetime, timezone
from enum import Enum
from logging import Logger
from typing import (
    Any,
    AsyncGenerator,
    Dict,
    List,
    Optional,
    Tuple,
)
from urllib.parse import urlsplit
from uuid import uuid4

from bs4 import BeautifulSoup  # pyright: ignore[reportMissingModuleSource]
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from app.utils.html_markdown import html_to_markdown_async

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    AppGroups,
    Connectors,
    ProgressStatus,
    RecordRelations,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.constants import IconPaths
from app.connectors.core.base.connector.connector_service import BaseConnector, ConnectorInitError
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.error.stream_errors import (
    raise_for_stream_fetch,
    to_stream_error,
)
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
    SyncStrategy,
)
from app.connectors.core.constants import CONNECTOR_EMAIL_IDENTITY_INFO
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
from app.connectors.sources.zammad.access import GroupReaders, RoleAccess, UserAccess, is_external
from app.connectors.sources.zammad.common.apps import ZammadApp
from app.connectors.sources.zammad.kb_access import (
    ARCHIVED,
    DRAFT,
    INTERNAL,
    PUBLISHED,
    CategoryAccess,
    answer_changed_at,
    answer_state,
)
from app.connectors.utils.record_ids import new_record_id
from app.connectors.utils.value_mapper import ValueMapper
from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
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
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.sources.client.zammad.zammad import (
    ZammadClient,
)
from app.sources.external.zammad.zammad import ZammadDataSource
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# Config path for Zammad connector
ZAMMAD_CONFIG_PATH = "/services/connectors/{connector_id}/config"
# Zammad has no API rate limit of its own; this keeps one sync from crowding out
# the agents working in the same instance. A 429 or 5xx from a proxy in front is
# retried with backoff (Retry-After honoured, up to 30 s a wait).
ZAMMAD_REQUESTS_PER_SECOND = 20

# Constants for batch processing and parsing
BATCH_SIZE_KB_ANSWERS = 50
# Not "kb_sync": the search-based sync saved that key even when it read no answers
# (Zammad 6.0-6.4), so a new key makes every install read its whole knowledge base once.
KB_SYNC_POINT_KEY = "kb_listing_sync"
KB_LISTING_ASSET_TYPES = (
    "KnowledgeBase",
    "KnowledgeBaseTranslation",
    "KnowledgeBaseCategory",
    "KnowledgeBaseCategoryTranslation",
    "KnowledgeBaseAnswer",
    "KnowledgeBaseAnswerTranslation",
)
ATTACHMENT_ID_PARTS_COUNT = 3
KB_ANSWER_ATTACHMENT_PARTS_COUNT = 2
TICKET_ID_LISTING_PAGE_SIZE = 50
# Held tickets counted per call. Elasticsearch's _count is exact at any size; a chunk
# that comes up short is listed, so bigger means fewer counts but longer listings.
TICKET_ID_COUNT_CHUNK = 1_000
# Elasticsearch refuses a search whose offset + limit passes index.max_result_window.
SEARCH_RESULT_WINDOW = 10_000
# Syncs in a row one version of a ticket may fail to read before the group checkpoint
# moves past it. Scheduled syncs run hourly by default, so three spans a few hours:
# long enough for an outage or rate limit to clear, short enough that one ticket
# Zammad always refuses doesn't make every sync re-read a growing backlog.
MAX_TICKET_READ_ATTEMPTS = 3
# Narrowest updated_at window a group's ticket search is split into (queries use whole seconds).
MIN_SPLIT_WINDOW_MS = 60_000
GRAPH_RECORDS_PAGE_SIZE = 500
# Largest image inlined into an answer's text as a data URI; a larger one stays a link.
MAX_INLINE_IMAGE_BYTES = 5 * 1024 * 1024
FILTER_CLEANUP_SYNC_POINT_KEY = "filter_cleanup:excluded_groups"
# A Zammad before 6.5 can't count tickets, so finding deleted ones means listing every
# held ticket. That runs at most this often per group; 6.5 and later check every sync.
# The integration stack sets 0 so a delete lands on the next sync.
FULL_TICKET_LISTING_INTERVAL_MS = int(
    os.getenv("ZAMMAD_FULL_TICKET_LISTING_INTERVAL_MS", str(24 * 60 * 60 * 1000))
)
# Attachment records are "<ticket>_<article>_<attachment>".
TICKET_ATTACHMENT_ID_PATTERN = re.compile(r"^(\d+)_\d+_\d+$")
# Knowledge-base answer records are "kb_answer_<answer>".
KB_ANSWER_ID_PATTERN = re.compile(r"^kb_answer_(\d+)$")


@dataclass(frozen=True)
class BurstResume:
    """Where to pick up an updated_at window too dense to split, read by ticket id.

    Every ticket in [low, high] with an id below ``next_id`` has been read.
    """

    low: int
    high: int
    next_id: int


@dataclass
class TicketReadFailures:
    """Tickets of one group that failed to read, and for how many syncs in a row.

    A version is ``"<ticket id>@<updated_at>"``, so a ticket edited in Zammad
    starts a fresh count. ``failing`` holds the checkpoint back; a version that
    reached MAX_TICKET_READ_ATTEMPTS is ``given_up`` and no longer does.
    """

    earlier: dict[str, int] = field(default_factory=dict)
    failing: dict[str, str] = field(default_factory=dict)
    given_up: dict[str, int] = field(default_factory=dict)
    read: set[str] = field(default_factory=set)

    def counts_to_keep(self, *, listing_complete: bool) -> dict[str, int]:
        # After a complete read the checkpoint has passed every version not failing now.
        counts = {} if listing_complete else {
            version: count for version, count in self.earlier.items() if version not in self.read
        }
        for version in self.failing.values():
            counts[version] = self.earlier.get(version, 0) + 1
        if not listing_complete:
            counts.update(dict.fromkeys(self.given_up, MAX_TICKET_READ_ATTEMPTS))
        return counts


class ZammadReadError(Exception):
    """A read from Zammad failed, so what depends on it must wait for the next sync.

    ``read_until`` is the updated_at below which every ticket was read, when known;
    ``burst`` says how far a dense window was read by id.
    """

    def __init__(self, message: str, read_until: int | None = None, burst: BurstResume | None = None) -> None:
        super().__init__(message)
        self.read_until = read_until
        self.burst = burst


class ZammadSyncIncompleteError(Exception):
    """Part of a sync could not be read or written; raised once the rest has run.

    Everything that was read is written first, and nothing is removed on the
    strength of a read that failed. Raising makes the run end as failed, so it
    is never reported as a success, and a full sync does not unlink what it did
    not see.
    """

    def __init__(self, parts: list[str]) -> None:
        super().__init__("Zammad sync incomplete: " + "; ".join(parts))
        self.parts = parts


class ReadOutcome(Enum):
    """How far a read of who belongs where got, which decides what may be written from it."""

    COMPLETE = "complete"
    # Stopped for a reason that passes (no answer, a timeout, a rate limit, a server
    # error): replacing stored members with what was read would take access from
    # everyone not read, so what is stored stays until a sync reads it all.
    INTERRUPTED = "interrupted"
    # Zammad refused it (401, 403 and other 4xx), which repeats every sync until the
    # token or Zammad is changed. Keeping stored members would keep access no one can
    # check, so the write goes ahead without what couldn't be read: access fails closed.
    REFUSED = "refused"


# Client errors that pass with time, unlike the rest of 4xx.
RETRYABLE_CLIENT_ERRORS = frozenset({408, HttpStatusCode.TOO_MANY_REQUESTS.value})


def read_failure(status_code: int | None) -> ReadOutcome:
    """The outcome of a read Zammad answered with ``status_code`` instead of the data.

    No status means no answer at all (a network error, or a body that wasn't JSON).
    """
    if (
        isinstance(status_code, int)
        and HttpStatusCode.BAD_REQUEST.value <= status_code < HttpStatusCode.INTERNAL_SERVER_ERROR.value
        and status_code not in RETRYABLE_CLIENT_ERRORS
    ):
        return ReadOutcome.REFUSED
    return ReadOutcome.INTERRUPTED


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
    .with_resilience_config(rate_limit=ZAMMAD_REQUESTS_PER_SECOND, max_retries=3, max_delay=30.0)\
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
    .with_info(CONNECTOR_EMAIL_IDENTITY_INFO)\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.ZAMMAD.value))
        .with_realtime_support(False)
        .add_documentation_link(DocumentationLink(
            "Zammad API Token Setup",
            "https://docs.zammad.org/en/latest/api/user-access-token.html",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/zammad/zammad',
            'pipeshub'
        ))
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
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
        connector_id: str,
        scope: str,
        created_by: str,
    ) -> None:
        super().__init__(
            ZammadApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
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
        self._user_id_to_data: dict[int, dict[str, Any]] = {}  # user_id -> {"email", "name", "role_ids"} (lightweight mapping)
        # What each synced user brings to ticket access (active, roles, own group rows).
        self._user_access: dict[int, UserAccess] = {}
        # How the last _fetch_users read ended; run_sync reads it straight after.
        self._users_read = ReadOutcome.COMPLETE
        # Parts of the current sync that could not be read or written (see ZammadSyncIncompleteError).
        self._incomplete: list[str] = []
        # Ids of the groups listed as inactive; their tickets leave PipesHub like a filtered-out group's.
        self._inactive_group_ids: set[str] | None = None
        self._inactive_group_names: dict[str, str] = {}
        # Ticket id -> external ids of the attachments written for it this sync (the group being synced).
        self._written_attachments: dict[str, set[str]] = {}

        # Filter collections (initialized in run_sync)
        self.sync_filters: Any = None
        self.indexing_filters: Any = None
        # Ids of groups the applied group filter leaves out; None until a complete
        # group listing has been read, so a failed listing removes nothing.
        self._filter_excluded_group_ids: set[str] | None = None
        # Ids of the groups synced as RecordGroups, under the same rule.
        self._synced_group_ids: set[str] | None = None
        # Zammad before 6.5 has no only_total_count; then reconciliation lists every held ticket.
        self._ticket_count_unsupported = False

    async def init(self) -> bool:
        """
        Initialize Zammad client using proper Client + DataSource architecture.
        """
        try:
            # Use ZammadClient.build_from_services() to create client with proper auth
            client = await ZammadClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id,
                resilience=self.resilience,
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
                connector_instance_id=self.connector_id,
                resilience=self.resilience,
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
            self._ticket_count_unsupported = False
            self._incomplete = []

            # Step 1: Roles, read first: who is staff and who a customer, and who may
            # read a group's tickets, depend on the roles' permissions and group access.
            self.logger.info("🎭 Step 1: Reading roles...")
            roles_data, roles_read = await self._fetch_roles()
            if roles_read is not ReadOutcome.COMPLETE:
                self._incomplete.append(f"the role list ({roles_read.value})")
            roles = {int(r["id"]): RoleAccess.from_role(r) for r in roles_data if str(r.get("id", "")).isdigit()}

            # Step 2: Fetch and sync users
            self.logger.info("👤 Step 2: Syncing users...")
            users, user_email_map = await self._fetch_users(roles if roles_read is ReadOutcome.COMPLETE else None)
            if users:
                await self.data_entities_processor.on_new_app_users(users)
                self.logger.info(f"✅ Synced {len(users)} users")
            if self._users_read is not ReadOutcome.COMPLETE:
                self._incomplete.append(f"the user list ({self._users_read.value})")
            # Group and role members are built from the user and role lists, and
            # writing them replaces what is stored: from a list cut short, everyone
            # not on it would lose access through every group and role.
            keep_members = ReadOutcome.INTERRUPTED in (self._users_read, roles_read)
            readers = GroupReaders(self._user_access, roles)

            # Step 3: Fetch and sync groups (creates BOTH RecordGroups AND UserGroups)
            self.logger.info("👥 Step 3: Syncing groups...")
            group_record_groups, group_user_groups = await self._fetch_groups(user_email_map, readers)

            # IMPORTANT: Sync UserGroups BEFORE RecordGroups!
            # so UserGroups must exist first for permission edges to be created.
            if keep_members:
                self.logger.warning(
                    "⚠️ The user or role list was not read in full, so group members stay as stored this sync"
                )
            elif group_user_groups:
                await self.data_entities_processor.on_new_user_groups(group_user_groups)
                self.logger.info(f"✅ Synced {len(group_user_groups)} groups as UserGroups")

            if group_record_groups:
                await self.data_entities_processor.on_new_record_groups(group_record_groups)
                self.logger.info(f"✅ Synced {len(group_record_groups)} groups as RecordGroups")

            # Step 4: Sync roles and their members
            if keep_members:
                self.logger.warning(
                    "⚠️ The user or role list was not read in full, so roles and their members stay as stored this sync"
                )
            else:
                await self._sync_roles(roles_data, user_email_map)

            # Step 5: Sync tickets (linked to group RecordGroups via group_id)
            self.logger.info("🎫 Step 5: Syncing tickets...")
            await self._sync_tickets_for_groups(group_record_groups)

            await self._remove_tickets_in_excluded_groups()

            # Step 6: Sync knowledge base (always fetch, indexing filters control indexing_status)
            self.logger.info("📚 Step 6: Syncing knowledge base...")
            await self._sync_knowledge_bases()

            if self._incomplete:
                # Everything readable has been written; the run still must not count as a success.
                raise ZammadSyncIncompleteError(self._incomplete)
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

    async def _fetch_users(
        self, roles: Optional[dict[int, RoleAccess]] = None,
    ) -> tuple[list[AppUser], dict[str, AppUser]]:
        """
        Fetch all users from Zammad with pagination.

        Args:
            roles: The complete role list, when it could be read. A user none of whose
                active roles is a staff role is a customer of the help desk and is
                registered as a guest, so PipesHub does not make them an
                organisation member. Without the roles nobody is marked a guest.

        Returns:
            Tuple of (list of AppUser objects, email -> AppUser map). Whether
            every page was read is left in ``self._users_read``.
        """
        users: List[AppUser] = []
        user_email_map: Dict[str, AppUser] = {}
        self._users_read = ReadOutcome.INTERRUPTED
        # The connector instance outlives a sync: last sync's users must not linger.
        self._user_id_to_data = {}
        self._user_access = {}

        datasource = await self._get_fresh_datasource()
        page = 1
        per_page = 100
        outcome = ReadOutcome.COMPLETE

        while True:
            response = await datasource.list_users(page=page, per_page=per_page)

            if not response.success:
                outcome = read_failure(response.status_code)
                self.logger.warning(
                    f"Failed to read Zammad users (page {page}, HTTP {response.status_code}): "
                    f"{response.error or response.message}"
                )
                break

            users_data = response.data
            if not users_data:
                self.logger.debug(f"Empty users list for page {page}")
                break
            if not isinstance(users_data, list):
                users_data = [users_data]

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

                # Store lightweight mapping: user_id -> {email, name, role_ids} (instead of full user data)
                access = UserAccess.from_user(user_data)
                self._user_access[user_id] = access
                # Use full_name (already computed above) or fallback to email
                display_name = full_name or email
                self._user_id_to_data[user_id] = {
                    "email": email.lower(),
                    "name": display_name,
                    "role_ids": sorted(access.role_ids),
                }

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
                    is_guest=roles is not None and is_external(access, roles),
                )

                users.append(app_user)
                user_email_map[email.lower()] = app_user

            # Check if we got less than per_page, meaning we're done
            if len(users_data) < per_page:
                break

            page += 1

        self._users_read = outcome
        if outcome is ReadOutcome.REFUSED:
            self.logger.error(
                f"❌ Zammad refused the user list after {len(users)} users; group and role members are "
                "written from those alone, so access through them is withdrawn from the rest until it can be read"
            )
        self.logger.info(f"📥 Fetched {len(users)} users from Zammad")
        return users, user_email_map

    async def _fetch_groups(
        self,
        user_email_map: dict[str, AppUser],
        readers: GroupReaders,
    ) -> Tuple[List[Tuple[RecordGroup, List[Permission]]], List[Tuple[AppUserGroup, List[AppUser]]]]:
        """
        Fetch Zammad groups and create BOTH RecordGroups AND UserGroups.
        Groups are the permission boundary for tickets in Zammad.

        A group's UserGroup holds the users who may read its tickets (``readers``),
        not its ``user_ids``: those list every access level, and ``change``,
        ``create`` or ``overview`` alone does not let an agent read a ticket. An
        inactive group is written with no members, since nobody reads its tickets
        as an agent, and gets no RecordGroup; its tickets are removed like a
        filtered-out group's (see ``_remove_tickets_in_excluded_groups``).

        Args:
            user_email_map: Map of email -> AppUser for membership tracking
            readers: Who may read each group's tickets, from the user and role lists

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
        self._filter_excluded_group_ids = None
        self._synced_group_ids = None
        self._inactive_group_ids = None
        excluded_group_ids: set[str] = set()
        synced_group_ids: set[str] = set()
        inactive_group_ids: set[str] = set()
        listing_complete = True

        while True:
            response = await datasource.list_groups(page=page, per_page=per_page)

            if not response.success:
                listing_complete = False
                outcome = read_failure(response.status_code)
                hint = ""
                if response.status_code == HttpStatusCode.FORBIDDEN.value:
                    # Zammad before 6.5 lists groups to admin.group only.
                    hint = "; before Zammad 6.5 the token needs the admin.group permission"
                self._incomplete.append(f"the group list ({outcome.value}{hint})")
                self.logger.warning(
                    f"Failed to fetch groups from Zammad (page {page}, HTTP {response.status_code}){hint}"
                )
                break
            if not response.data:
                break

            groups_data = response.data
            if not isinstance(groups_data, list):
                groups_data = [groups_data]

            if not groups_data:
                break

            for group_data in groups_data:
                group_id = group_data.get("id")
                group_name = group_data.get("name", "")
                active = group_data.get("active", True) is not False

                if group_id and not self._is_group_allowed_by_filter(str(group_id)):
                    excluded_group_ids.add(str(group_id))
                    self.logger.debug(f"⏭️ Skipping group {group_id} ({group_name}) - excluded by filter")
                    continue

                if not group_id or not group_name:
                    continue

                if active:
                    record_groups.append((self._group_record_group(group_data), [
                        # Permission: UserGroup -> RecordGroup (the group's readers can access)
                        Permission(
                            entity_type=EntityType.GROUP,
                            type=PermissionType.READ,
                            external_id=str(group_id)  # Links to UserGroup.source_user_group_id
                        )
                    ]))
                    synced_group_ids.add(str(group_id))
                else:
                    inactive_group_ids.add(str(group_id))
                    self._inactive_group_names[str(group_id)] = group_name

                user_group = AppUserGroup(
                    id=str(uuid4()),
                    org_id=self.data_entities_processor.org_id,
                    source_user_group_id=str(group_id),
                    connector_id=self.connector_id,
                    app_name=Connectors.ZAMMAD,
                    name=group_name,
                    description=group_data.get("note", "") or "",
                )
                members: list[AppUser] = []
                for user_id in sorted(readers.readers(group_id, group_active=active)):
                    email = self._user_id_to_data.get(user_id, {}).get("email")
                    if email and email in user_email_map:
                        members.append(user_email_map[email])
                user_groups.append((user_group, members))

            # Check if we got less than per_page, meaning we're done
            if len(groups_data) < per_page:
                break

            page += 1

        if listing_complete:
            self._filter_excluded_group_ids = excluded_group_ids
            self._synced_group_ids = synced_group_ids
            self._inactive_group_ids = inactive_group_ids
        self.logger.info(
            f"📥 Fetched {len(user_groups)} groups, "
            f"created {len(record_groups)} RecordGroups and {len(user_groups)} UserGroups"
        )
        return (record_groups, user_groups)

    def _group_record_group(self, group_data: dict[str, Any]) -> RecordGroup:
        """The RecordGroup a Zammad group's tickets sit in."""
        group_id = group_data.get("id")
        created_at = self._parse_zammad_datetime(group_data.get("created_at", ""))
        updated_at = self._parse_zammad_datetime(group_data.get("updated_at", ""))
        return RecordGroup(
            id=str(uuid4()),
            org_id=self.data_entities_processor.org_id,
            external_group_id=f"group_{group_id}",
            connector_id=self.connector_id,
            connector_name=self.connector_name,
            name=group_data.get("name", ""),
            short_name=str(group_id),
            group_type=RecordGroupType.PROJECT,
            web_url=f"{self.base_url}/#manage/groups/{group_id}" if self.base_url else None,
            source_created_at=created_at if created_at else None,
            source_updated_at=updated_at if updated_at else None,
        )

    async def _fetch_roles(self) -> tuple[list[dict[str, Any]], ReadOutcome]:
        """Every role, with its permission names (``expand``), and how far the read got."""
        datasource = await self._get_fresh_datasource()
        roles: list[dict[str, Any]] = []
        page = 1
        per_page = 100
        while True:
            response = await datasource.list_roles(page=page, per_page=per_page, expand=True)
            if not response.success:
                outcome = read_failure(response.status_code)
                hint = ""
                if response.status_code == HttpStatusCode.FORBIDDEN.value:
                    # Zammad before 6.5 lists roles to admin.role only.
                    hint = "; before Zammad 6.5 the token needs the admin.role permission"
                self.logger.warning(
                    f"Failed to read Zammad roles (page {page}, HTTP {response.status_code}){hint}: "
                    f"{response.error or response.message}"
                )
                return roles, outcome
            data = response.data
            if not data:
                return roles, ReadOutcome.COMPLETE
            if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
                self.logger.warning(f"Zammad answered the role list (page {page}) with something that is not a list of roles")
                return roles, ReadOutcome.INTERRUPTED
            if any(r.get("permission_ids") and not isinstance(r.get("permissions"), list) for r in data):
                # Without the names nobody would count as an agent, and every member list would be emptied.
                self.logger.warning("Zammad listed roles without their permission names (expand); members stay as stored")
                return roles, ReadOutcome.INTERRUPTED
            roles.extend(data)
            if len(data) < per_page:
                return roles, ReadOutcome.COMPLETE
            page += 1

    async def _sync_roles(
        self,
        roles_data: list[dict[str, Any]],
        user_email_map: Dict[str, AppUser]
    ) -> None:
        """Write the roles as AppRoles with their members.

        An inactive role is written with no members: Zammad grants nothing
        through it, so nobody keeps knowledge-base access through it.
        """
        app_roles: list[tuple[AppRole, list[AppUser]]] = []
        for role_data in roles_data:
            role_id = role_data.get("id")
            if not role_id:
                continue

            role_name = role_data.get("name", f"Role {role_id}")
            active = role_data.get("active", True) is not False

            # Parse timestamps
            created_at = self._parse_zammad_datetime(role_data.get("created_at", ""))
            updated_at = self._parse_zammad_datetime(role_data.get("updated_at", ""))

            # Get users assigned to this role by checking user_id_to_data mapping
            role_users: list[AppUser] = []
            if active:
                for user_data in self._user_id_to_data.values():
                    if role_id in user_data.get("role_ids", []):
                        email = user_data.get("email")
                        if email and email in user_email_map:
                            role_users.append(user_email_map[email])

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

        if app_roles:
            await self.data_entities_processor.on_new_app_roles(app_roles)
            self.logger.info(f"✅ Synced {len(app_roles)} roles")
        else:
            self.logger.info("No roles found")

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

                self._written_attachments = {}
                # Read group-level sync point (using group name as key)
                last_sync_time = await self._get_group_sync_checkpoint(group_name)
                burst_resume = await self._get_burst_resume(group_name)
                failures = TicketReadFailures(earlier=await self._get_read_failures(group_name))

                if last_sync_time:
                    self.logger.info(f"🔄 Incremental sync for group {group_name} from {last_sync_time}")
                else:
                    self.logger.info(f"🔄 Full sync for group {group_name} (first time)")

                # Fetch and process tickets for this group only
                total_tickets = 0
                total_attachments = 0
                max_ticket_updated_at: Optional[int] = None
                listing_complete = True

                try:
                    async for batch_records in self._fetch_tickets_for_group_batch(
                        group_id=int(group_id),
                        group_name=group_name,
                        last_sync_time=last_sync_time,
                        burst_resume=burst_resume,
                        failures=failures,
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
                except ZammadReadError as e:
                    listing_complete = False
                    self._incomplete.append(f"the tickets of group '{group_name}'")
                    self.logger.warning(
                        f"⚠️ Incomplete ticket read for group {group_name}; the next sync reads again from the last complete window: {e}"
                    )
                    if e.read_until and e.read_until != last_sync_time:
                        await self._update_group_sync_checkpoint(group_name, e.read_until)
                    if e.burst is not None and e.burst != burst_resume:
                        await self._save_burst_resume(group_name, e.burst)

                if failures.given_up:
                    max_ticket_updated_at = max(max_ticket_updated_at or 0, *failures.given_up.values())
                kept_failures = failures.counts_to_keep(listing_complete=listing_complete)
                if kept_failures != failures.earlier:
                    await self._save_read_failures(group_name, kept_failures)

                # Pages come newest first, so the checkpoint moves only once every
                # page and every ticket has been read.
                if listing_complete and burst_resume is not None:
                    await self._save_burst_resume(group_name, None)
                if listing_complete and max_ticket_updated_at:
                    await self._update_group_sync_checkpoint(group_name, max_ticket_updated_at + 1000)
                elif listing_complete and total_tickets > 0:
                    self.logger.warning(f"Processed {total_tickets} tickets but max_ticket_updated_at not set, using current time")
                    await self._update_group_sync_checkpoint(group_name)
                elif listing_complete:
                    self.logger.debug(f"No tickets found for group {group_name}, keeping existing checkpoint to avoid skipping older tickets")

                await self._remove_tickets_gone_from_group(int(group_id), group_name)

                total_tickets_all_groups += total_tickets
                total_attachments_all_groups += total_attachments
                if total_attachments > 0:
                    self.logger.info(f"✅ Synced {total_tickets} tickets, {total_attachments} attachments for group {group_name}")
                else:
                    self.logger.info(f"✅ Synced {total_tickets} tickets for group {group_name}")

            except Exception as e:
                self._incomplete.append(f"the tickets of group '{group_record_group.name}'")
                self.logger.error(f"❌ Error syncing tickets for group {group_record_group.name}: {e}", exc_info=True)
                continue  # Continue with next group even if one fails

        if total_attachments_all_groups > 0:
            self.logger.info(f"✅ Total: Synced {total_tickets_all_groups} tickets, {total_attachments_all_groups} attachments across {len(group_record_groups)} groups")
        else:
            self.logger.info(f"✅ Total: Synced {total_tickets_all_groups} tickets across {len(group_record_groups)} groups")

    def _date_filter_bounds(self, key: SyncFilterKey) -> tuple[int | None, int | None]:
        date_filter = self.sync_filters.get(key) if self.sync_filters else None
        if not date_filter:
            return None, None
        return date_filter.get_value(default=(None, None))

    def _build_ticket_search_query(
        self, group_id: int, last_sync_time: int | None, until: int | None = None
    ) -> str:
        """Zammad search query for a group's tickets within the sync date filters."""
        query_parts = [f"group_id:{group_id}"]
        modified_after, modified_before = self._date_filter_bounds(SyncFilterKey.MODIFIED)
        created_after, created_before = self._date_filter_bounds(SyncFilterKey.CREATED)

        if last_sync_time:
            modified_after = max(modified_after, last_sync_time) if modified_after else last_sync_time
        # ``until`` is the next window's ``last_sync_time``. Both round down to the same second,
        # so it is exclusive: otherwise tickets stamped in that second match both windows. It is
        # compared to the user's inclusive bound by second too, as the query states both.
        before_bracket = "]"
        if until and (not modified_before or until // 1000 <= modified_before // 1000):
            modified_before, before_bracket = until, "}"

        def _iso(epoch_ms: int) -> str:
            return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if modified_after:
            query_parts.append(f"updated_at:[{_iso(modified_after)} TO *]")
        if modified_before:
            query_parts.append(f"updated_at:[* TO {_iso(modified_before)}{before_bracket}")
        if created_after:
            query_parts.append(f"created_at:[{_iso(created_after)} TO *]")
        if created_before:
            query_parts.append(f"created_at:[* TO {_iso(created_before)}]")
        return " AND ".join(query_parts)

    def _window_bounds(self, low: int | None, high: int | None) -> tuple[int, int]:
        lo = low if low is not None else (self._date_filter_bounds(SyncFilterKey.MODIFIED)[0] or 0)
        # An open window ends at the whole second after the clock's, an edge the query can state exactly.
        hi = high if high is not None else (get_epoch_timestamp_in_ms() // 1000 + 1) * 1000
        return lo, hi

    def _split_window(self, low: int | None, high: int | None) -> list[tuple[int | None, int | None]] | None:
        """Halves of an updated_at window, or None when it is already as narrow as it may get."""
        lo, hi = self._window_bounds(low, high)
        if hi - lo < 2 * MIN_SPLIT_WINDOW_MS:
            return None
        # On a whole second, as the query states it, so "read every ticket before mid" holds to the ms.
        mid = (lo + hi) // 2 // 1000 * 1000
        return [(low, mid), (mid, high)]

    async def _fetch_tickets_for_group_batch(
        self,
        group_id: int,
        group_name: str,
        last_sync_time: Optional[int],
        burst_resume: BurstResume | None = None,
        failures: TicketReadFailures | None = None,
    ) -> AsyncGenerator[List[Tuple[Record, List[Permission]]], None]:
        """
        Fetch tickets for a specific group with pagination and incremental sync support.

        One search can page through only SEARCH_RESULT_WINDOW tickets, so a
        group with more is read in updated_at windows, halved until each fits.
        A window too narrow to halve (a bulk edit that stamped one time on that
        many tickets) is read in ticket-id ranges instead. A failed page or
        ticket raises ``ZammadReadError`` carrying ``read_until``: every ticket
        updated before it has been read, and ``burst``: how far a dense window
        was read by id, so the next sync carries on from there. A ticket that
        has failed on MAX_TICKET_READ_ATTEMPTS syncs no longer holds it back.

        Args:
            group_id: Zammad group ID to fetch tickets for
            group_name: Group name for logging
            last_sync_time: Last sync timestamp (epoch ms) or None for full sync
            failures: The group's ticket read failures, updated in place

        Yields:
            Batches of (Record, permissions) tuples (includes TicketRecords and FileRecords)

        Raises:
            ZammadReadError: a search failed or a page repeated, so the listing did not reach its end
        """
        datasource = await self._get_fresh_datasource()
        limit = 50
        batch_size = 50
        windows: list[tuple[int | None, int | None]] = [(last_sync_time, None)]
        # A dense window a failed sync stopped in is finished first, from the id it reached.
        resume_from: dict[tuple[int | None, int | None], int] = {}
        if burst_resume is not None and self._window_bounds(last_sync_time, None)[0] == burst_resume.low:
            windows = [(last_sync_time, burst_resume.high), (burst_resume.high, None)]
            resume_from[windows[0]] = burst_resume.next_id
        read_until = last_sync_time
        # Ticket id -> updated_at already written this sync; a split window reads some tickets twice.
        written: dict[str, Any] = {}
        failures = failures if failures is not None else TicketReadFailures()

        while windows:
            low, high = windows.pop(0)
            query = self._build_ticket_search_query(group_id, low, until=high)
            self.logger.debug(f"Fetching tickets for group '{group_name}' with query: {query}")
            offset = 0
            overflowed = (low, high) in resume_from
            previous_page_ids: set[str] | None = None

            while not overflowed:
                if offset + limit > SEARCH_RESULT_WINDOW:
                    overflowed = True
                    break
                response = await datasource.search_tickets(query=query, limit=limit, offset=offset)
                if not response.success:
                    raise ZammadReadError(
                        f"ticket search for group '{group_name}' failed at offset {offset}: "
                        f"{response.error or response.message}",
                        read_until=read_until,
                    )

                # search_tickets answers with a list of ticket objects, or fails.
                tickets_data = response.data
                if not isinstance(tickets_data, list):
                    tickets_data = [tickets_data] if tickets_data else []
                if not tickets_data:
                    break
                page_ids = {str(t.get("id")) for t in tickets_data}
                if page_ids == previous_page_ids:
                    raise ZammadReadError(
                        f"Zammad returned the same {len(tickets_data)} tickets for group '{group_name}' "
                        f"at offset {offset} as on the page before, so it is not paging. Stopped reading "
                        f"this group's tickets here; the group's sync point stays below the tickets not read.",
                        read_until=read_until,
                    )
                previous_page_ids = page_ids
                if offset == 0 and len(tickets_data) >= limit:
                    overflowed = await self._fills_search_window(datasource, query, group_name, read_until)
                    if overflowed:
                        break

                self.logger.debug(f"Fetched {len(tickets_data)} tickets for group '{group_name}' from offset {offset}")
                async for batch_records in self._ticket_batches(tickets_data, written, failures, group_name, batch_size):
                    yield batch_records
                if len(tickets_data) < limit:
                    break
                offset += limit

            if overflowed:
                halves = None if (low, high) in resume_from else self._split_window(low, high)
                if halves is not None:
                    windows[0:0] = halves
                    continue
                bounds = self._window_bounds(low, high)
                if high is None:
                    # The read by id is bounded by the edge a resume will use. Waiting out the second
                    # means no ticket is stamped below that edge after its ids are read.
                    await asyncio.sleep(max(0, bounds[1] - get_epoch_timestamp_in_ms()) / 1000)
                    query = self._build_ticket_search_query(group_id, low, until=bounds[1])
                async for batch_records in self._read_dense_window(
                    datasource, query, group_name, bounds, read_until,
                    resume_from.get((low, high), 0), written, failures, limit, batch_size,
                ):
                    yield batch_records
            if high is not None and not failures.failing:
                read_until = high

        if failures.failing:
            raise ZammadReadError(
                f"tickets {', '.join(sorted(failures.failing))} in group '{group_name}' could not be read",
                read_until=read_until,
            )

    async def _fills_search_window(
        self, datasource: ZammadDataSource, query: str, group_name: str, read_until: int | None,
    ) -> bool:
        """Whether ``query`` matches more tickets than one search can page through.

        One read of the window's last slot answers it, instead of paging up to it.
        """
        probe = await datasource.search_tickets(query=query, limit=1, offset=SEARCH_RESULT_WINDOW - 1)
        if not probe.success:
            raise ZammadReadError(
                f"ticket search for group '{group_name}' failed probing the result window: {probe.message}",
                read_until=read_until,
            )
        return bool(probe.data)

    async def _ticket_batches(
        self,
        tickets_data: list[dict[str, Any]],
        written: dict[str, Any],
        failures: TicketReadFailures,
        group_name: str,
        batch_size: int,
    ) -> AsyncGenerator[list[tuple[Record, list[Permission]]], None]:
        """Records for one page of tickets, in batches; a ticket that fails is noted in ``failures``."""
        batch_records: list[tuple[Record, list[Permission]]] = []
        for ticket_data in tickets_data:
            ticket_key = str(ticket_data.get("id", "unknown"))
            updated_at = ticket_data.get("updated_at")
            if ticket_key in written and written[ticket_key] == updated_at:
                continue
            version = f"{ticket_key}@{updated_at}"
            try:
                records = await self._ticket_records(ticket_data)
                batch_records.extend(records)
                self._written_attachments[ticket_key] = {
                    r.external_record_id for r, _ in records if isinstance(r, FileRecord)
                }
                written[ticket_key] = updated_at
                failures.failing.pop(ticket_key, None)
                failures.given_up.pop(version, None)
                failures.read.add(version)
            except Exception as e:
                if failures.earlier.get(version, 0) + 1 >= MAX_TICKET_READ_ATTEMPTS:
                    failures.failing.pop(ticket_key, None)
                    failures.given_up[version] = self._parse_zammad_datetime(updated_at or "")
                    self.logger.error(
                        f"❌ Ticket {ticket_key} in group '{group_name}' could not be read on "
                        f"{MAX_TICKET_READ_ATTEMPTS} syncs in a row, so the sync moves past it "
                        f"and it is read again when it next changes in Zammad. Last error: {e}",
                        exc_info=True,
                    )
                else:
                    failures.failing[ticket_key] = version
                    self.logger.error(f"❌ Error processing ticket {ticket_key}: {e}", exc_info=True)
                continue
            if len(batch_records) >= batch_size:
                yield batch_records
                batch_records = []
        if batch_records:
            yield batch_records

    async def _read_dense_window(
        self,
        datasource: ZammadDataSource,
        query: str,
        group_name: str,
        bounds: tuple[int, int],
        read_until: int | None,
        next_id: int,
        written: dict[str, Any],
        failures: TicketReadFailures,
        limit: int,
        batch_size: int,
    ) -> AsyncGenerator[list[tuple[Record, list[Permission]]], None]:
        """Read an updated_at window that halving can't bring under the search window, by ticket id.

        Each id range is at most SEARCH_RESULT_WINDOW - limit ids wide, so it
        holds no more tickets than that and its pages never pass the window.
        The read starts at ``next_id`` (every ticket in the window with a lower
        id was read by an earlier sync), and stops, saying how far it got, at a
        range that failed or held a ticket that couldn't be read.
        """
        low, high = bounds
        span = SEARCH_RESULT_WINDOW - limit

        def stopped(reason: str) -> ZammadReadError:
            return ZammadReadError(
                f"{reason} while reading group '{group_name}' by ticket id from {next_id}",
                read_until=read_until,
                burst=BurstResume(low, high, next_id),
            )

        while True:
            probe = await datasource.search_tickets(query=f"{query} AND id:[{next_id} TO *]", limit=1, offset=0)
            if not probe.success or not isinstance(probe.data, list):
                raise stopped("a ticket search failed")
            if not probe.data:
                return
            ranged = f"{query} AND id:[{next_id} TO {next_id + span - 1}]"
            offset = 0
            in_range: set[str] = set()
            previous_page_ids: set[str] | None = None
            while True:
                response = await datasource.search_tickets(query=ranged, limit=limit, offset=offset)
                if not response.success or not isinstance(response.data, list):
                    raise stopped("a ticket search failed")
                page_ids = {str(t.get("id")) for t in response.data}
                if response.data and page_ids == previous_page_ids:
                    raise stopped(f"Zammad returned the same {len(response.data)} tickets at offset {offset} as on the page before")
                previous_page_ids = page_ids
                in_range.update(str(t.get("id", "unknown")) for t in response.data)
                async for batch_records in self._ticket_batches(
                    response.data, written, failures, group_name, batch_size,
                ):
                    yield batch_records
                if len(response.data) < limit:
                    break
                offset += limit
            # Only this range's tickets hold it back; one from another window has its own.
            failed_here = in_range & failures.failing.keys()
            if failed_here:
                raise stopped(f"tickets {', '.join(sorted(failed_here))} could not be read")
            next_id += span

    async def _get_read_failures(self, group_name: str) -> dict[str, int]:
        data = await self.tickets_sync_point.read_sync_point(group_name) or {}
        versions = data.get("read_failure_versions") or []
        counts = data.get("read_failure_counts") or []
        return {str(v): int(c) for v, c in zip(versions, counts)}

    async def _save_read_failures(self, group_name: str, counts: dict[str, int]) -> None:
        # Two flat lists: a Neo4j property can't hold a map.
        await self.tickets_sync_point.update_sync_point(group_name, {
            "read_failure_versions": list(counts),
            "read_failure_counts": list(counts.values()),
        })

    async def _get_burst_resume(self, group_name: str) -> BurstResume | None:
        data = await self.tickets_sync_point.read_sync_point(group_name) or {}
        if not data.get("burst_next_id"):
            return None
        return BurstResume(int(data["burst_low"]), int(data["burst_high"]), int(data["burst_next_id"]))

    async def _save_burst_resume(self, group_name: str, burst: BurstResume | None) -> None:
        # The store merges fields, so clearing is an explicit write of zeros.
        await self.tickets_sync_point.update_sync_point(group_name, {
            "burst_low": burst.low if burst else 0,
            "burst_high": burst.high if burst else 0,
            "burst_next_id": burst.next_id if burst else 0,
        })

    async def _ticket_records(self, ticket_data: dict[str, Any]) -> list[tuple[Record, list[Permission]]]:
        """A ticket and its attachments, ready for ``on_new_records``; raises if its articles can't be read."""
        ticket_record = await self._transform_ticket_to_ticket_record(ticket_data)
        if not ticket_record:
            return []
        if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.TICKETS):
            ticket_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
        # Records inherit permissions from the group's RecordGroup.
        return [(ticket_record, []), *await self._fetch_ticket_attachments(ticket_data, ticket_record)]

    @staticmethod
    def _ticket_id_of(record: Record) -> str | None:
        """The Zammad ticket a ticket or ticket-attachment record belongs to, else None."""
        external_id = record.external_record_id or ""
        if record.record_type == RecordType.TICKET:
            return external_id if external_id.isdigit() else None
        match = TICKET_ATTACHMENT_ID_PATTERN.match(external_id)
        return match.group(1) if match else None

    async def _records_by_ticket_in_group(
        self, external_group_id: str, attachments: dict[str, list[tuple[str, str]]] | None = None,
    ) -> dict[str, list[str]]:
        """Record ids of each ticket held in a group record group: its attachments, then the ticket.

        ``attachments``, when given, receives each ticket's attachment records as (record id, external id).
        """
        by_ticket: dict[str, list[str]] = defaultdict(list)
        after_key: str | None = None
        while True:
            # The trash too: a ticket the search no longer returns is removed even if already trashed.
            page = await self.data_entities_processor.get_records_in_record_group(
                self.connector_id, external_group_id, GRAPH_RECORDS_PAGE_SIZE, after_key,
                visibility=RecordVisibility.ALL,
            )
            for record in page:
                ticket_id = self._ticket_id_of(record)
                if not ticket_id:
                    continue
                # Ticket last: a failed delete leaves the ticket for the next sync to find again.
                if record.record_type == RecordType.TICKET:
                    by_ticket[ticket_id].append(record.id)
                else:
                    by_ticket[ticket_id].insert(0, record.id)
                    if attachments is not None:
                        attachments.setdefault(ticket_id, []).append((record.id, record.external_record_id or ""))
            if len(page) < GRAPH_RECORDS_PAGE_SIZE:
                return by_ticket
            after_key = page[-1].id

    @staticmethod
    def _id_ranges(ticket_ids: list[int], span: int) -> list[tuple[int, int]]:
        """Contiguous id ranges covering ``ticket_ids`` (sorted), each at most ``span`` ids wide."""
        ranges: list[tuple[int, int]] = []
        for ticket_id in ticket_ids:
            if ranges and ticket_id - ranges[-1][0] < span:
                ranges[-1] = (ranges[-1][0], ticket_id)
            else:
                ranges.append((ticket_id, ticket_id))
        return ranges

    async def _list_ticket_ids_in_range(self, query: str, low: int, high: int) -> set[str] | None:
        """Ids of the tickets matching ``query`` with ids in [low, high], or None if a page failed."""
        datasource = await self._get_fresh_datasource()
        ranged = f"{query} AND id:[{low} TO {high}]"
        ticket_ids: set[str] = set()
        offset = 0
        while True:
            response = await datasource.search_tickets(
                query=ranged, limit=TICKET_ID_LISTING_PAGE_SIZE, offset=offset
            )
            if not response.success or not isinstance(response.data, list):
                return None
            ticket_ids.update(
                str(t["id"]) for t in response.data if isinstance(t, dict) and t.get("id") is not None
            )
            if len(response.data) < TICKET_ID_LISTING_PAGE_SIZE:
                return ticket_ids
            offset += TICKET_ID_LISTING_PAGE_SIZE

    async def _unlisted_ticket_ids(
        self, group_id: int, group_name: str, held: list[str], *, full_listing_recent: bool = False,
    ) -> tuple[list[str], bool] | None:
        """The held tickets Zammad's search does not return for this group.

        Held tickets are first counted in chunks, among their own ids: a chunk
        whose count matches is fully listed, which is the usual case and costs
        one call. Only a chunk that comes up short (or a Zammad that can't
        count) is listed, in id ranges around its tickets, each narrow enough
        that its pages stay inside Elasticsearch's result window. A range that
        fails is left out of the answer, so its tickets are kept this sync.

        Returns the unlisted ids and whether every listed range was read, or
        None when this Zammad can't count and ``full_listing_recent`` says the
        full listing already ran within its interval.
        """
        query = self._build_ticket_search_query(group_id, last_sync_time=None)
        # A range of this many ids holds at most this many tickets, so offset + limit never passes the window.
        span = SEARCH_RESULT_WINDOW - TICKET_ID_LISTING_PAGE_SIZE
        held_ids = sorted(int(t) for t in held)
        unlisted: list[str] = []
        all_read = True
        for start in range(0, len(held_ids), TICKET_ID_COUNT_CHUNK):
            chunk = held_ids[start:start + TICKET_ID_COUNT_CHUNK]
            if await self._search_returns_all(query, chunk):
                continue
            if self._ticket_count_unsupported and full_listing_recent:
                return None
            for low, high in self._id_ranges(chunk, span):
                listed = await self._list_ticket_ids_in_range(query, low, high)
                if listed is None:
                    self.logger.warning(
                        f"Could not list tickets {low}-{high} of group '{group_name}'; keeping them this sync"
                    )
                    all_read = False
                    continue
                unlisted.extend(str(t) for t in chunk if low <= t <= high and str(t) not in listed)
        return unlisted, all_read

    async def _search_returns_all(self, query: str, ticket_ids: list[int]) -> bool:
        """Whether Zammad's search matches every one of ``ticket_ids`` for ``query``, by one count.

        The count is taken among these ids only, so it equals their number
        exactly when none is missing. False when it is lower, or when no count
        could be had (a failed call, or a Zammad before 6.5): the caller lists.
        """
        if self._ticket_count_unsupported:
            return False
        datasource = await self._get_fresh_datasource()
        response = await datasource.count_tickets(query=query, ids=ticket_ids)
        if not response.success:
            return False
        total = response.data.get("total_count") if isinstance(response.data, dict) else None
        if not isinstance(total, int) or total > len(ticket_ids):
            # No count, or one the ids didn't narrow: listing is the only safe answer from here on.
            self._ticket_count_unsupported = True
            return False
        return total == len(ticket_ids)

    def _passes_date_filters(self, ticket: dict[str, Any]) -> bool:
        for key, field_name in ((SyncFilterKey.MODIFIED, "updated_at"), (SyncFilterKey.CREATED, "created_at")):
            after, before = self._date_filter_bounds(key)
            value = self._parse_zammad_datetime(ticket.get(field_name) or "")
            if not value:
                continue
            if (after and value < after) or (before and value > before):
                return False
        return True

    async def _read_back_ticket(self, ticket_id: str) -> tuple[bool, dict[str, Any] | None]:
        """``(True, None)`` when Zammad says the ticket is gone, ``(True, ticket)`` when it is there.

        Anything else, including a success whose body is not this ticket, is
        ``(False, None)``: a read that failed.
        """
        datasource = await self._get_fresh_datasource()
        response = await datasource.get_ticket(int(ticket_id))
        if response.status_code == HttpStatusCode.NOT_FOUND.value:
            return True, None
        ticket = response.data
        if not response.success or not isinstance(ticket, dict) or str(ticket.get("id")) != ticket_id:
            return False, None
        return True, ticket

    async def _settle_ticket(self, ticket_id: str, record_ids: list[str], held_in_group: str, reason: str) -> bool:
        """Settle a held ticket its group no longer lists, or whose group the filter excludes.

        A search listing can miss a ticket that still exists (the index lags
        or is being rebuilt), so the ticket is read back first. It is removed
        only when Zammad says it is gone or an applied filter leaves it out; a
        ticket now in another synced group is written there. One now in a group
        that isn't synced (inactive or nameless) has no RecordGroup to sit in
        and no sync that would reach it again, so it is removed too. Returns
        False when a read or a delete failed and the next sync must try again.
        """
        read, ticket = await self._read_back_ticket(ticket_id)
        if not read:
            return False
        if ticket is not None and ticket.get("group_id") is None:
            return True
        if ticket is None or not self._is_group_allowed_by_filter(str(ticket["group_id"])) \
                or not self._passes_date_filters(ticket):
            return await self._delete_ticket_records(record_ids, reason)
        if self._synced_group_ids is None:
            return False
        if str(ticket["group_id"]) not in self._synced_group_ids:
            # An inactive group (no agent reads it) or a nameless one: nothing syncs it.
            return await self._delete_ticket_records(
                record_ids, f"{reason}; it is in group {ticket['group_id']}, which is not synced"
            )
        if f"group_{ticket['group_id']}" == held_in_group:
            return True
        await self.data_entities_processor.on_new_records(await self._ticket_records(ticket))
        return True

    async def _delete_ticket_records(self, record_ids: list[str], reason: str) -> bool:
        """Delete one ticket's records in order through the shared delete path.

        Stops at the first failure so the ticket itself stays and the next sync
        finds it and retries. Returns whether every record was removed.
        """
        for record_id in record_ids:
            try:
                await self.data_entities_processor.on_record_deleted(record_id)
            except Exception as e:
                self.logger.warning(f"Failed to remove record {record_id} ({reason}); retrying next sync: {e}")
                return False
        return True

    async def _remove_tickets_gone_from_group(self, group_id: int, group_name: str) -> None:
        """Remove tickets deleted in Zammad, or now outside the sync filters, from a synced group.

        The incremental search only returns tickets updated since the checkpoint,
        so a deleted ticket never appears in it. This checks the graph's tickets
        against the group's search instead (see ``_unlisted_ticket_ids``). It reads everything afresh each sync,
        so a failed read or delete is simply repeated next time. On a Zammad that can't count (before 6.5)
        that means listing every held ticket, so it runs at most once per FULL_TICKET_LISTING_INTERVAL_MS.
        """
        external_group_id = f"group_{group_id}"
        try:
            held_attachments: dict[str, list[tuple[str, str]]] = {}
            by_ticket = await self._records_by_ticket_in_group(external_group_id, held_attachments)
            if not by_ticket:
                return
            await self._remove_attachments_no_longer_on_their_ticket(group_name, by_ticket, held_attachments)
            point = await self.tickets_sync_point.read_sync_point(group_name) or {}
            last_full = point.get("full_listing_at") or 0
            full_listing_recent = get_epoch_timestamp_in_ms() - last_full < FULL_TICKET_LISTING_INTERVAL_MS
            outcome = await self._unlisted_ticket_ids(
                group_id, group_name, list(by_ticket), full_listing_recent=full_listing_recent,
            )
            if outcome is None:
                self.logger.info(
                    f"Zammad can't count tickets (before 6.5); group '{group_name}' was fully checked for "
                    "deleted tickets less than a day ago, so it is not listed again this sync"
                )
                return
            unlisted, all_read = outcome
            removed = 0
            settled_all = True
            for ticket_id in unlisted:
                try:
                    if await self._settle_ticket(
                        ticket_id, by_ticket[ticket_id], external_group_id,
                        f"ticket {ticket_id} left group '{group_name}'",
                    ):
                        removed += 1
                    else:
                        settled_all = False
                except Exception as e:
                    settled_all = False
                    self.logger.warning(f"Could not settle ticket {ticket_id} of group '{group_name}': {e}")
            if removed:
                self.logger.info(f"🗑️ Settled {removed} tickets no longer listed in group '{group_name}'")
            if not (all_read and settled_all):
                self._incomplete.append(f"the check for deleted tickets of group '{group_name}'")
            # Only a full listing that read everything and settled everything starts the next interval.
            if self._ticket_count_unsupported and all_read and settled_all:
                await self.tickets_sync_point.update_sync_point(
                    group_name, {"full_listing_at": get_epoch_timestamp_in_ms()},
                )
        except Exception as e:
            self._incomplete.append(f"the check for deleted tickets of group '{group_name}'")
            self.logger.warning(f"Could not check group '{group_name}' for deleted tickets: {e}", exc_info=True)

    async def _remove_attachments_no_longer_on_their_ticket(
        self,
        group_name: str,
        by_ticket: dict[str, list[str]],
        held_attachments: dict[str, list[tuple[str, str]]],
    ) -> None:
        """Remove the attachment records of a ticket written this sync that its articles no longer hold.

        A merge moves the source ticket's articles to the target, where their
        attachments are written under the target's id; an article an agent deletes
        takes its attachments with it. Only tickets whose articles were read in full
        this sync are compared, so a failed read removes nothing.
        """
        for ticket_id, attachments in held_attachments.items():
            current = self._written_attachments.get(ticket_id)
            if current is None:
                continue
            for record_id, external_id in attachments:
                if external_id in current:
                    continue
                if await self._delete_ticket_records(
                    [record_id], f"attachment {external_id} is no longer on ticket {ticket_id}",
                ):
                    by_ticket[ticket_id].remove(record_id)
                else:
                    self._incomplete.append(f"the removal of attachment {external_id} in group '{group_name}'")

    async def _remove_tickets_in_excluded_groups(self) -> None:
        """Remove the tickets held in a group that is out of the sync's scope.

        That is a group the applied group filter leaves out, or one Zammad lists
        as inactive (no agent reads its tickets there). Each ticket is read back
        first, so one that moved to a synced group is written there instead of
        removed. An inactive group's ticket sync point is cleared with it, so if
        the group is made active again its tickets are all read again. The
        clean-up is remembered in a sync point only when every ticket was
        settled; editing the filters deletes the sync points, so the next sync
        cleans up again. Nothing is done before a complete group listing.
        """
        excluded = self._filter_excluded_group_ids
        inactive = self._inactive_group_ids
        if excluded is None or inactive is None:
            return
        wanted = sorted(excluded | inactive)
        try:
            done = await self.tickets_sync_point.read_sync_point(FILTER_CLEANUP_SYNC_POINT_KEY)
            done_ids = (done or {}).get("excluded_group_ids") or []
            if done_ids == wanted:
                return
            if not wanted:
                # A group made active again: its next deactivation must clean up again.
                await self.tickets_sync_point.update_sync_point(FILTER_CLEANUP_SYNC_POINT_KEY, {"excluded_group_ids": []})
                return
            unsettled = 0
            for group_id in wanted:
                external_group_id = f"group_{group_id}"
                reason = "group excluded by filter" if group_id in excluded else "group is inactive in Zammad"
                by_ticket = await self._records_by_ticket_in_group(external_group_id)
                if by_ticket:
                    self.logger.info(f"🗑️ Settling {len(by_ticket)} tickets of group {group_id} ({reason})")
                for ticket_id, record_ids in by_ticket.items():
                    try:
                        if group_id in inactive and group_id not in excluded:
                            # Zammad's own word that the group is out of reach: it lists the
                            # group inactive, and refuses even an admin a ticket in it, so
                            # there is nothing to read back. A ticket that moved to an active
                            # group was refiled there by that group's read, earlier in this sync.
                            settled = await self._delete_ticket_records(record_ids, f"group {group_id}: {reason}")
                        else:
                            settled = await self._settle_ticket(
                                ticket_id, record_ids, external_group_id, f"group {group_id}: {reason}",
                            )
                    except Exception as e:
                        settled = False
                        self.logger.warning(f"Could not settle ticket {ticket_id} of group {group_id}: {e}")
                    unsettled += not settled
                if group_id in inactive and group_id not in excluded:
                    await self._forget_group_sync_point(group_id)
                if not unsettled and not await self._records_by_ticket_in_group(external_group_id):
                    # Empty now: the group's folder goes too. Only after every ticket
                    # in it was settled, so it takes no record with it.
                    await self.data_entities_processor.on_record_group_deleted(external_group_id, self.connector_id)
            if unsettled:
                self._incomplete.append(f"the removal of {unsettled} tickets of groups out of scope")
                self.logger.warning(
                    f"{unsettled} tickets in groups out of scope could not be settled; retrying next sync"
                )
                return
            await self.tickets_sync_point.update_sync_point(
                FILTER_CLEANUP_SYNC_POINT_KEY, {"excluded_group_ids": wanted}
            )
        except Exception as e:
            self._incomplete.append("the removal of tickets of groups out of scope")
            self.logger.warning(f"Could not remove tickets of groups out of scope: {e}", exc_info=True)

    async def _forget_group_sync_point(self, group_id: str) -> None:
        """Clear an inactive group's ticket sync point, so its tickets are read in full if it comes back."""
        name = self._inactive_group_names.get(group_id)
        if not name:
            return
        await self.tickets_sync_point.update_sync_point(name, {
            "last_sync_time": 0, "burst_low": 0, "burst_high": 0, "burst_next_id": 0, "full_listing_at": 0,
            "read_failure_versions": [], "read_failure_counts": [],
        })

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

        if not response.success or (isinstance(response.data, dict) and response.data.get("error")):
            raise ZammadReadError(f"articles of ticket {ticket_id} could not be read: {response.message}")
        if not response.data:
            return attachments

        articles = response.data
        if not isinstance(articles, list):
            articles = [articles]

        for article in articles:
            if not isinstance(article, dict):
                raise ZammadReadError(f"articles of ticket {ticket_id} hold an entry that is not an article")
            article_id = article.get("id")
            skip = self._article_skip_reason(article)
            if skip:
                self.logger.debug(f"Skipping attachments of article {article_id}: {skip}")
                continue

            article_attachments = article.get("attachments") or []

            for attachment in article_attachments:
                try:
                    attachment_id = attachment.get("id") if isinstance(attachment, dict) else None
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

    @staticmethod
    def _article_skip_reason(article: dict[str, Any]) -> str | None:
        """Why an article is left out of the ticket (a system note, an auto reply, a bounce), or None.

        Any of its fields may be null: an article made through the API as an
        e-mail has no ``from``, and ``preferences`` may be empty or null.
        """
        if article.get("sender") == "System":
            return "a system article"
        preferences = article.get("preferences")
        if isinstance(preferences, dict) and (
            preferences.get("is-auto-response") or preferences.get("send-auto-response") is False
        ):
            return "an auto response"
        article_from = article.get("from")
        if isinstance(article_from, str) and ("MAILER-DAEMON" in article_from or "Mail Delivery System" in article_from):
            return "a bounce notification"
        return None

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

        # Customer (creator) and owner (assignee), from the user list read this sync.
        customer = self._user_id_to_data.get(ticket_data.get("customer_id"), {})
        owner = self._user_id_to_data.get(ticket_data.get("owner_id"), {})
        creator_email = customer.get("email", "")
        creator_name = customer.get("name", "") if creator_email else ""
        assignee_email = owner.get("email", "")
        assignee_name = owner.get("name", "") if assignee_email else ""

        # Parse timestamps
        created_at = self._parse_zammad_datetime(ticket_data.get("created_at", ""))
        updated_at = self._parse_zammad_datetime(ticket_data.get("updated_at", ""))

        # Build web URL
        weburl = f"{self.base_url}/#ticket/zoom/{ticket_id}" if self.base_url else ""

        # Check for existing record
        existing_record = await self.data_entities_processor.get_record_by_external_id(
            self.connector_id, str(ticket_id)
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
            indexing_status=ProgressStatus.NOT_STARTED,
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

        # Linked tickets and KB answers. A failed read raises: the ticket's link edges
        # are replaced by what is written, so writing none would delete them all.
        related_external_records = await self._fetch_ticket_links(ticket_id=int(ticket_id))
        if related_external_records:
            ticket_record.related_external_records = related_external_records
            self.logger.debug(
                f"🔗 Ticket {ticket_id} has {len(related_external_records)} linked records"
            )

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

        filename = attachment_data.get("filename") or ""
        size = self._attachment_size(attachment_data.get("size"))
        content_type = self._attachment_mime_type(attachment_data.get("preferences"), filename)

        # Check for existing record
        existing_record = await self.data_entities_processor.get_record_by_external_id(
            self.connector_id, external_record_id
        )

        is_new = existing_record is None
        record_id = existing_record.id if existing_record else new_record_id(self.connector_id, external_record_id)
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
            indexing_status=ProgressStatus.NOT_STARTED,
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
            file_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

        return file_record

    @staticmethod
    def _attachment_mime_type(preferences: object, filename: str) -> str:
        """An attachment's type: ``Content-Type`` (mail), ``Mime-Type`` (files added through the
        API or the agent UI, which carry no ``Content-Type``), else from the file name."""
        if isinstance(preferences, dict):
            for key in ("Content-Type", "Mime-Type", "Content-type", "mime_type"):
                value = preferences.get(key)
                if isinstance(value, str) and value.strip():
                    return value.split(";")[0].strip()
        return mimetypes.guess_type(filename)[0] or "application/octet-stream"

    @staticmethod
    def _attachment_size(size: object) -> int:
        """Zammad sends an attachment's size as a string of digits."""
        try:
            return max(int(str(size)), 0)
        except (TypeError, ValueError):
            return 0

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

        Raises:
            ZammadReadError: the links could not be read
        """
        related_records: List[RelatedExternalRecord] = []

        datasource = await self._get_fresh_datasource()
        response = await datasource.list_links(
            link_object="Ticket",
            link_object_value=ticket_id
        )
        if not response.success or (response.data is not None and not isinstance(response.data, dict)):
            raise ZammadReadError(
                f"links of ticket {ticket_id} could not be read: {response.error or response.message}"
            )
        if not response.data:
            return related_records

        try:
            links = response.data.get("links") or []
            assets = response.data.get("assets") or {}

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

                if link_object == "Ticket" and not self._linked_ticket_in_scope(assets, link_object_value):
                    # A link to a ticket the sync leaves out would write a placeholder record for it.
                    continue

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

    def _linked_ticket_in_scope(self, assets: dict[str, Any], ticket_id: object) -> bool:
        """Whether a linked ticket's group is synced; True when the links' assets do not say."""
        tickets = assets.get("Ticket") if isinstance(assets, dict) else None
        linked = tickets.get(str(ticket_id)) if isinstance(tickets, dict) else None
        group_id = linked.get("group_id") if isinstance(linked, dict) else None
        if group_id is None:
            return True
        if not self._is_group_allowed_by_filter(str(group_id)):
            return False
        return self._synced_group_ids is None or str(group_id) in self._synced_group_ids

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
        data = await self.kb_sync_point.read_sync_point(KB_SYNC_POINT_KEY)
        return data.get("last_sync_time") if data else None

    async def _update_kb_sync_checkpoint(self, timestamp: Optional[int] = None) -> None:
        """
        Update KB sync checkpoint.

        Args:
            timestamp: Timestamp to set (defaults to current time if None)
        """
        sync_time = timestamp if timestamp is not None else get_epoch_timestamp_in_ms()
        await self.kb_sync_point.update_sync_point(
            KB_SYNC_POINT_KEY,
            {"last_sync_time": sync_time}
        )
        self.logger.debug(f"💾 Updated KB sync checkpoint: {sync_time}")

    # ==================== KNOWLEDGE BASE SYNCING ====================

    async def _sync_knowledge_bases(self) -> None:
        """Sync knowledge bases, categories, and the answers changed since the KB sync point.

        The answers come from POST /api/v1/knowledge_bases/init, which lists every
        knowledge base, category and answer the account can see in one response on
        Zammad 6.0 and later. /api/v1/search can't list them before 6.5: it ignores
        ``offset``, and answers ``objects=KnowledgeBaseAnswerTranslation`` with a 500.
        The sync point moves only once every listed answer has been written.

        An answer is written again when it was edited, when a scheduled state
        change came due, when it is listed but not held (restored, or newly
        visible to the token), and, if it is a draft or archived (granted to the
        category's editor roles by name), when those roles changed. An answer
        held but no longer listed is read back, and removed only when Zammad says
        it is gone or not visible to the token.
        """
        last_sync_time = await self._get_kb_sync_checkpoint()
        datasource = await self._get_fresh_datasource()
        try:
            listing = await self._read_kb_listing(datasource)
        except ZammadReadError as e:
            self._incomplete.append("the knowledge base listing")
            self.logger.warning(f"⚠️ Knowledge base not synced; its sync point stays where it was: {e}")
            return

        if last_sync_time:
            since = last_sync_time // 1000 * 1000
            since_iso = datetime.fromtimestamp(since / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self.logger.info(f"🔄 Incremental KB sync from {since_iso}")
        else:
            since = None
            self.logger.info("🔄 Full KB sync")

        listed_answers: dict[str, dict[str, Any]] = {
            str(answer_id): answer_data
            for answer_id, answer_data in listing.get("KnowledgeBaseAnswer", {}).items()
        }
        held_answers = await self._held_kb_answer_ids()
        kb_map: Dict[int, RecordGroup] = {}
        category_map: Dict[int, RecordGroup] = {}
        category_permissions_map: Dict[int, Dict[str, Any]] = {}
        await self._process_kb_entities(
            assets=listing,
            kb_map=kb_map,
            category_map=category_map,
            category_permissions_map=category_permissions_map
        )
        category_editors = {
            str(cat_id): sorted(perms.get("editor_role_ids", []))
            for cat_id, perms in category_permissions_map.items()
            if perms.get("read") is not False
        }
        earlier_editors = await self._get_kb_category_editors()
        if earlier_editors is None and since is not None:
            # Answers written before this version used other rules (an archived
            # answer stayed public, a scheduled time counted at once), so every
            # answer is written again once, the first time the editors are recorded.
            self.logger.info("🔄 KB answers are all written again once, under the current access rules")
            since = None
        editors_changed = {
            cat_id for cat_id, editors in category_editors.items()
            if earlier_editors is not None and earlier_editors.get(cat_id) != editors
        }

        changed_answers = {
            answer_id: answer_data
            for answer_id, answer_data in listed_answers.items()
            if self._kb_answer_changed_since(answer_data, since)
            or answer_id not in held_answers
            or (
                str(answer_data.get("category_id")) in editors_changed
                and self._determine_visibility(answer_data) in ("ARCHIVED", "DRAFT")
            )
        }

        total_synced, max_updated_at, failed_updated_at = 0, 0, []
        if changed_answers:
            total_synced, max_updated_at, failed_updated_at = await self._sync_kb_answers(
                answers=changed_answers,
                assets=listing,
                category_map=category_map,
                category_permissions_map=category_permissions_map
            )
        else:
            self.logger.info("No KB answers changed since the last sync")

        await self._remove_kb_answers_gone(
            sorted(held_answers - set(listed_answers)), next(iter(kb_map), None)
        )

        if failed_updated_at:
            self._incomplete.append(f"{len(failed_updated_at)} knowledge base answer(s)")
            # Every answer older than the oldest failure was written, so the sync point
            # may move up to that failure but not past it.
            self.logger.warning(
                f"⚠️ {len(failed_updated_at)} KB answer(s) could not be synced; the next sync reads them again"
            )
            hold_at = None if 0 in failed_updated_at else min(failed_updated_at)
            if hold_at and (not last_sync_time or hold_at > last_sync_time):
                await self._update_kb_sync_checkpoint(hold_at)
        else:
            # Remembered only once every answer they gate was written with them.
            if category_editors != earlier_editors:
                await self._save_kb_category_editors(category_editors)
            if max_updated_at > 0:
                await self._update_kb_sync_checkpoint(max_updated_at + 1000)
            elif total_synced > 0:
                await self._update_kb_sync_checkpoint()

        self.logger.info(f"✅ KB sync completed: {total_synced} answers processed")

    async def _get_kb_category_editors(self) -> dict[str, list[int]] | None:
        """The editor roles of each category as last written, or None before the first such sync."""
        data = await self.kb_sync_point.read_sync_point(KB_SYNC_POINT_KEY) or {}
        raw = data.get("category_editors")
        if not isinstance(raw, str):
            return None
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    async def _save_kb_category_editors(self, editors: dict[str, list[int]]) -> None:
        # A JSON string: a Neo4j property can't hold a map.
        await self.kb_sync_point.update_sync_point(
            KB_SYNC_POINT_KEY, {"category_editors": json.dumps(editors, sort_keys=True)}
        )

    async def _held_kb_answer_ids(self) -> set[str]:
        """Zammad ids of the knowledge-base answers PipesHub holds for this connector."""
        records = await self.data_entities_processor.get_records_by_record_type(
            self.connector_id, RecordType.WEBPAGE
        )
        held: set[str] = set()
        for record in records:
            # One in the trash is already removed; reading it back every sync would only cost a request.
            if getattr(record, "is_deleted", False):
                continue
            match = KB_ANSWER_ID_PATTERN.match(record.external_record_id or "")
            if match:
                held.add(match.group(1))
        return held

    async def _remove_kb_answers_gone(self, answer_ids: list[str], kb_id: int | None) -> None:
        """Remove held answers the listing no longer has, each only on Zammad's definite word.

        The answer is read back: only 404 (deleted) removes it, with its
        attachments. A 403 keeps it: Zammad does not say it is gone, only that
        the token may not see it. Any other answer keeps it and marks the sync
        incomplete, so the next one tries again.
        """
        if not answer_ids:
            return
        datasource = await self._get_fresh_datasource()
        removed = 0
        for answer_id in answer_ids:
            try:
                response = await datasource.get_kb_answer(id=int(answer_id), kb_id=kb_id)
            except Exception as e:
                self._incomplete.append(f"the check of knowledge base answer {answer_id}")
                self.logger.warning(f"Could not read back KB answer {answer_id}: {e}")
                continue
            if response.success:
                # Listed nowhere yet readable: keep it; it is written when the listing has it again.
                continue
            if response.status_code == HttpStatusCode.FORBIDDEN.value:
                self.logger.warning(
                    f"KB answer {answer_id} is no longer listed and the token may not read it; keeping it"
                )
                continue
            if response.status_code != HttpStatusCode.NOT_FOUND.value:
                self._incomplete.append(f"the check of knowledge base answer {answer_id}")
                self.logger.warning(
                    f"KB answer {answer_id} is no longer listed and could not be read back "
                    f"(HTTP {response.status_code}); keeping it this sync"
                )
                continue
            external_id = f"kb_answer_{answer_id}"
            record = await self.data_entities_processor.get_record_by_external_id(self.connector_id, external_id)
            if record is None:
                continue
            children = await self.data_entities_processor.get_records_by_parent(
                self.connector_id, external_id, visibility=RecordVisibility.ALL,
            )
            if await self._delete_ticket_records(
                [child.id for child in children] + [record.id],
                f"KB answer {answer_id} is gone from Zammad (HTTP {response.status_code})",
            ):
                removed += 1
            else:
                self._incomplete.append(f"the removal of knowledge base answer {answer_id}")
        if removed:
            self.logger.info(f"🗑️ Removed {removed} KB answer(s) no longer in Zammad")

    async def _read_kb_listing(self, datasource: ZammadDataSource) -> dict[str, Any]:
        """Every knowledge base, category and answer Zammad lists, keyed by asset type.

        Raises:
            ZammadReadError: the listing failed, or holds an entry that is not an object
        """
        response = await datasource.init_knowledge_base()
        if not response.success:
            raise ZammadReadError(
                f"the knowledge base listing could not be read: {response.error or response.message}"
            )
        listing = response.data or {}
        for asset_type in KB_LISTING_ASSET_TYPES:
            entries = listing.get(asset_type, {})
            if not isinstance(entries, dict) or not all(isinstance(e, dict) for e in entries.values()):
                raise ZammadReadError(
                    f"the knowledge base listing holds {asset_type} entries that are not objects"
                )
        return listing

    def _kb_answer_changed_since(self, answer_data: dict[str, Any], since: int | None) -> bool:
        """Whether the answer was edited, or a scheduled state change came due, since ``since``."""
        if since is None:
            return True
        changed_at = answer_changed_at(answer_data, get_epoch_timestamp_in_ms())
        return not changed_at or changed_at >= since

    async def _process_kb_entities(
        self,
        assets: Dict[str, Any],
        kb_map: Dict[int, RecordGroup],
        category_map: Dict[int, RecordGroup],
        category_permissions_map: Dict[int, Dict[str, Any]]
    ) -> None:
        """
        Process knowledge bases and categories from the knowledge base listing.

        Args:
            assets: Assets dictionary from the knowledge base listing
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

                # KB needs ORG-level permission so all org users can see it in Knowledge Hub
                # Categories have their own role-based permissions, no inheritance from KB
                kb_permissions: List[Permission] = [
                    Permission(
                        entity_type=EntityType.ORG,
                        type=PermissionType.READ,
                        external_id=None
                    )
                ]

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

                # Effective access per role: the category's (inherited) rows, and the
                # default of every role without a row. Only the permissions endpoint
                # says which roles hold knowledge_base.editor / .reader, so it is read
                # for every category; the listing's permissions_effective has rows only.
                editor_role_ids: List[int] = []
                reader_role_ids: List[int] = []
                # False when the category's permissions couldn't be read for a reason
                # that passes: saving it would replace its stored grants with none.
                permissions_read = True
                try:
                    perm_response = await datasource.get_kb_category_permissions(
                        kb_id=kb_id,
                        cat_id=cat_id
                    )
                except Exception as e:
                    perm_response = None
                    self.logger.warning(f"Failed to fetch permissions for category {cat_id}: {e}")
                perm_data = perm_response.data if perm_response else None
                if perm_response and perm_response.success and isinstance(perm_data, dict):
                    access = CategoryAccess.from_permissions_response(perm_data)
                    editor_role_ids = list(access.editor_role_ids)
                    reader_role_ids = list(access.reader_role_ids)
                elif (
                    perm_response is None
                    or perm_response.success
                    or read_failure(perm_response.status_code) is ReadOutcome.INTERRUPTED
                ):
                    permissions_read = False
                    self._incomplete.append(f"the permissions of knowledge base category {cat_id}")
                    self.logger.warning(
                        f"Could not read the permissions of KB category {cat_id}; it and its "
                        "archived and draft answers keep their stored access this sync"
                    )
                else:
                    # Refused (only editors may read them), or gone (404): saved with no
                    # role grants, so nobody keeps access through it that Zammad can't confirm.
                    self.logger.warning(
                        f"Zammad refused the permissions of KB category {cat_id} "
                        f"(HTTP {perm_response.status_code}); saving it with no role grants. "
                        "The token needs knowledge_base.editor for internal and draft answers to be shared"
                    )

                # Store permissions and KB ID for answer processing
                category_permissions_map[cat_id] = {
                    "kb_id": kb_id,
                    "editor_role_ids": editor_role_ids,
                    "reader_role_ids": reader_role_ids,
                    "read": permissions_read,
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

                if permissions_read:
                    category_record_groups.append((cat_record_group, cat_permissions))
                # Kept in the map either way: answers and child categories find their place through it.
                category_map[cat_id] = cat_record_group

            if category_record_groups:
                await self.data_entities_processor.on_new_record_groups(category_record_groups)
                self.logger.info(f"✅ Synced {len(category_record_groups)} KB categories")

    async def _sync_kb_answers(
        self,
        answers: dict[str, dict[str, Any]],
        assets: dict[str, Any],
        category_map: Dict[int, RecordGroup],
        category_permissions_map: Dict[int, Dict[str, Any]]
    ) -> tuple[int, int, list[int]]:
        """
        Write KB answers from the knowledge base listing, BATCH_SIZE_KB_ANSWERS at a time.

        Args:
            answers: Answer id -> answer data, for the answers to write
            assets: The knowledge base listing, for the answers' translations
            category_map: Map of category_id -> RecordGroup
            category_permissions_map: Map of category_id -> permissions dict

        Returns:
            Tuple of (total_synced_count, max_updated_at_timestamp, the updated_at of
            each answer that could not be written, 0 where it has none)
        """
        total_synced = 0
        max_updated_at = 0
        failed_updated_at: list[int] = []
        answer_translations = assets.get("KnowledgeBaseAnswerTranslation", {})
        answer_items = list(answers.items())

        for batch_start in range(0, len(answer_items), BATCH_SIZE_KB_ANSWERS):
            batch_records: List[Tuple[Record, List[Permission]]] = []

            for answer_id_str, answer_data in answer_items[batch_start:batch_start + BATCH_SIZE_KB_ANSWERS]:
                answer_id = int(answer_id_str)
                # The checkpoint follows state changes that came due too (see _kb_answer_changed_since).
                answer_updated_at = answer_changed_at(answer_data, get_epoch_timestamp_in_ms())

                try:
                    # Track max updated_at for checkpoint
                    if answer_updated_at and answer_updated_at > max_updated_at:
                        max_updated_at = answer_updated_at

                    # Get category permissions
                    category_id = answer_data.get("category_id")
                    cat_perms = category_permissions_map.get(category_id, {})
                    editor_role_ids = cat_perms.get("editor_role_ids", [])

                    # Determine visibility
                    visibility = self._determine_visibility(answer_data)
                    if visibility in ("ARCHIVED", "DRAFT") and cat_perms.get("read") is False:
                        # Granted to the category's editor roles, which couldn't be read;
                        # writing it now would replace its stored grants with none.
                        self.logger.warning(
                            f"KB answer {answer_id} waits for its category's permissions; the next sync reads it again"
                        )
                        failed_updated_at.append(answer_updated_at)
                        continue

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
                    existing_record = await self.data_entities_processor.get_record_by_external_id(
                        self.connector_id, external_record_id
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
                            answer_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

                        # An answer is written when its state, its editors or its text may have
                        # changed; its grants are then replaced, not added to. Outside a full sync
                        # on_new_records only adds, so a scheduled archive that came due (same
                        # revision, same inheritance) would keep its organisation-wide grant.
                        needs_permission_update = existing_record is not None

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
                    failed_updated_at.append(answer_updated_at)

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

        return total_synced, max_updated_at, failed_updated_at

    def _determine_visibility(self, answer_data: Dict[str, Any]) -> str:
        """
        Determine answer visibility based on timestamps.

        Args:
            answer_data: Answer data from Zammad

        Returns:
            Visibility state: "PUBLIC", "INTERNAL", "ARCHIVED", or "DRAFT"
        """
        # Zammad's state machine: the first of archived, published, internal whose
        # time has passed. An archived answer keeps its published_at, and a
        # scheduled (future) time does not count yet.
        return {
            ARCHIVED: "ARCHIVED",
            PUBLISHED: "PUBLIC",
            INTERNAL: "INTERNAL",
            DRAFT: "DRAFT",
        }[answer_state(answer_data, get_epoch_timestamp_in_ms())]

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
            indexing_status=ProgressStatus.NOT_STARTED,
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
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_REQUEST.value,
                    detail=f"Unsupported record type for streaming: {record.record_type}",
                )

            return StreamingResponse(
                iter([content_bytes]),
                media_type=MimeTypes.BLOCKS.value,
                headers={
                    "Content-Disposition": f'inline; filename="{record.external_record_id}_blocks.json"'
                }
            )

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"❌ Error streaming record {record.id}: {e}", exc_info=True)
            raise to_stream_error(e, connector=self.display_name) from e

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
        for att in attachments:
            att_id = att.get("id")
            att_filename = att.get("filename", "")
            if not att_id:
                continue

            # Ticket attachment external_record_id format: {ticket_id}_{article_id}_{attachment_id}
            external_record_id = f"{ticket_id}_{article_id}_{att_id}"

            # Look up existing record to get the actual record ID
            existing_record = await self.data_entities_processor.get_record_by_external_id(
                connector_id=self.connector_id,
                external_record_id=external_record_id
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
            self.logger.warning("Failed to fetch ticket %s for streaming", ticket_id)
            raise_for_stream_fetch(
                success=ticket_response.success,
                has_payload=bool(ticket_response.data),
                connector=self.display_name,
                status=ticket_response.status_code,
                message=ticket_response.message or ticket_response.error,
            )

        ticket_data = ticket_response.data

        # Fetch articles for this ticket. A failed read must fail the stream: an
        # empty article list would index the ticket as its title alone, and it
        # stays that way until the ticket next changes in Zammad.
        articles_response = await datasource.list_ticket_articles(ticket_id=int(ticket_id))
        articles_data = articles_response.data
        if (
            not articles_response.success
            or (isinstance(articles_data, dict) and articles_data.get("error"))
            or (articles_data and not isinstance(articles_data, list))
        ):
            self.logger.warning("Failed to fetch the articles of ticket %s for streaming", ticket_id)
            raise_for_stream_fetch(
                success=False,
                has_payload=False,
                connector=self.display_name,
                status=articles_response.status_code,
                message=articles_response.message or articles_response.error,
            )
        articles = [a for a in (articles_data or []) if isinstance(a, dict)]

        # Sort articles by created_at
        articles.sort(key=lambda a: a.get("created_at") or "")

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
            description_body_html = first_article.get("body") or ""
            first_article_id = first_article.get("id")

            # Get attachments for first article (description) as children_records
            first_article_attachments = first_article.get("attachments") or []
            description_children_records: List[ChildRecord] = []
            if first_article_id and first_article_attachments:
                description_children_records = await self._build_ticket_attachment_child_records(
                    ticket_id=ticket_id,
                    article_id=str(first_article_id),
                    attachments=first_article_attachments,
                    parent_record=record
                )

            # Convert HTML to Markdown
            description_body_md = await html_to_markdown_async(description_body_html) if description_body_html else ""
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
                article_body_html = article.get("body") or ""
                article_from = article.get("from") or ""
                article_subject = article.get("subject") or ""

                # Skip articles without ID
                if not article_id:
                    continue

                # Skip system notes, auto replies and bounces
                skip = self._article_skip_reason(article)
                if skip:
                    self.logger.debug(f"Skipping article {article_id} of ticket {ticket_id}: {skip}")
                    continue

                if not article_body_html:
                    continue

                # Convert HTML to Markdown
                article_body_md = await html_to_markdown_async(article_body_html)
                # Get author name
                author_name = article_from if article_from else "Unknown"

                # Build comment name
                comment_name = f"Comment by {author_name}"
                if article_subject:
                    comment_name = f"{article_subject} - {author_name}"

                # Get attachments for this article as children_records
                article_attachments = article.get("attachments") or []
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
                    if perm_response.success and isinstance(perm_response.data, dict):
                        editor_role_ids = list(
                            CategoryAccess.from_permissions_response(perm_response.data).editor_role_ids
                        )
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
        for att in answer_attachments:
            att_id = att.get("id")
            att_filename = att.get("filename", "")
            if not att_id:
                continue

            # KB answer attachment external_record_id format: kb_answer_{answer_id}_attachment_{attachment_id}
            external_record_id = f"kb_answer_{answer_id}_attachment_{att_id}"

            # Look up existing record to get the actual record ID
            existing_record = await self.data_entities_processor.get_record_by_external_id(
                connector_id=self.connector_id,
                external_record_id=external_record_id
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
        content_id: Optional[int] = None
        try:
            init_response = await datasource.init_knowledge_base()
            if init_response.success and init_response.data:
                kb_assets = init_response.data.get("KnowledgeBase", {})
                if kb_assets:
                    kb_id = int(list(kb_assets.keys())[0])
                # The body is a translation's content, asked for by the content's id,
                # which is not the answer's id once an answer has a second translation.
                for translation in (init_response.data.get("KnowledgeBaseAnswerTranslation") or {}).values():
                    if isinstance(translation, dict) and translation.get("answer_id") == answer_id \
                            and isinstance(translation.get("content_id"), int):
                        content_id = translation["content_id"]
                        break
        except Exception:
            pass

        # Try to fetch KB answer using correct endpoint format
        answer_response = await datasource.get_kb_answer(id=answer_id, kb_id=kb_id, content_id=content_id)
        # Without this the body stays "" and a title-only container streams at 200.
        raise_for_stream_fetch(
            success=answer_response.success,
            has_payload=bool(answer_response.data),
            connector=self.display_name,
            status=answer_response.status_code,
            message=answer_response.message or answer_response.error,
        )

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

        # Convert embedded images to base64 before converting HTML to Markdown. Only
        # this answer's own attachments: the token can read attachments the answer's
        # readers can't, and an image tag may name any attachment id.
        if body:
            body = await self._convert_html_images_to_base64(body, self._inline_image_sizes(answer_attachments))

        # Convert HTML body to Markdown (same as ticket streaming)
        body_md = await html_to_markdown_async(body) if body else ""
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

    @staticmethod
    def _inline_image_sizes(attachments: list[dict[str, Any]]) -> dict[int, int]:
        """Attachment id -> size in bytes (0 when not stated) for the attachments an answer may inline."""
        sizes: dict[int, int] = {}
        for attachment in attachments or []:
            if not isinstance(attachment, dict):
                continue
            attachment_id = attachment.get("id")
            if isinstance(attachment_id, str) and attachment_id.isdigit():
                attachment_id = int(attachment_id)
            if not isinstance(attachment_id, int) or isinstance(attachment_id, bool):
                continue
            size = attachment.get("size")
            try:
                sizes[attachment_id] = int(size) if size is not None else 0
            except (TypeError, ValueError):
                sizes[attachment_id] = 0
        return sizes

    def _is_own_attachment_url(self, src: str) -> bool:
        """Whether an image source is relative or on the configured Zammad host."""
        parts = urlsplit(src)
        if not parts.scheme and not parts.netloc:
            return True
        base = urlsplit(self.base_url or "")
        return (parts.scheme, (parts.hostname or "").lower(), parts.port) == (
            base.scheme, (base.hostname or "").lower(), base.port
        )

    async def _convert_html_images_to_base64(
        self, html_content: str, allowed_attachments: Optional[dict[int, int]] = None,
    ) -> str:
        """
        Convert embedded images in HTML content to base64 data URIs.

        Finds all <img> tags with src pointing to Zammad attachments (e.g., /api/v1/attachments/9)
        and replaces them with base64-encoded data URIs in markdown format. Only an
        attachment in ``allowed_attachments`` (id -> size) is fetched, at most
        MAX_INLINE_IMAGE_BYTES, and only from a relative URL or the configured
        Zammad host; any other image is left as it is.

        Args:
            html_content: HTML content that may contain embedded images
            allowed_attachments: The attachments this content may inline, with their sizes

        Returns:
            HTML content with images converted to base64 data URIs (ready for markdown conversion)
        """
        allowed = allowed_attachments or {}
        if not html_content:
            return html_content

        # Parse HTML with BeautifulSoup
        soup = BeautifulSoup(html_content, 'html.parser')
        img_tags = soup.find_all('img')

        if not img_tags:
            return html_content

        # Datasource is initialized lazily on first Zammad attachment match
        datasource = None

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
            if attachment_id not in allowed or not self._is_own_attachment_url(src):
                self.logger.debug(f"Not inlining image {src}: not an attachment of this content")
                continue
            if allowed[attachment_id] > MAX_INLINE_IMAGE_BYTES:
                self.logger.debug(f"Not inlining image {src}: {allowed[attachment_id]} bytes")
                continue

            # Initialize datasource on first Zammad attachment encountered
            if datasource is None:
                if not self.data_source:
                    await self.init()
                datasource = await self._get_fresh_datasource()

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
                if len(image_bytes) > MAX_INLINE_IMAGE_BYTES:
                    self.logger.debug(f"Not inlining attachment {attachment_id}: {len(image_bytes)} bytes")
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

            if not response.success or response.data is None:
                self.logger.warning(
                    "Failed to download KB answer attachment %s: %s",
                    attachment_id,
                    response.message,
                )
                raise_for_stream_fetch(
                    success=response.success,
                    has_payload=response.data is not None,
                    connector=self.display_name,
                    status=response.status_code,
                    message=response.message or response.error,
                )

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

            if not response.success or response.data is None:
                self.logger.warning(
                    "Failed to download ticket attachment %s: %s",
                    attachment_id,
                    response.message,
                )
                raise_for_stream_fetch(
                    success=response.success,
                    has_payload=response.data is not None,
                    connector=self.display_name,
                    status=response.status_code,
                    message=response.message or response.error,
                )

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
        """Check the token signs in and can read what a sync reads: users, groups and roles.

        Before Zammad 6.5, groups and roles are listed only to admin.group and
        admin.role; a token without them would sync nothing. A refusal raises
        ConnectorInitError naming the permission, which the setup screen shows.
        """
        try:
            datasource = await self._get_fresh_datasource()
            me = await datasource.get_current_user()
        except Exception as e:
            self.logger.error(f"❌ Connection test failed: {e}", exc_info=True)
            return False
        if me.status_code in (HttpStatusCode.UNAUTHORIZED.value, HttpStatusCode.FORBIDDEN.value):
            raise ConnectorInitError(
                "Zammad did not accept the access token. Check that it is current and that "
                "API token access is turned on in Zammad (Admin > System > API)."
            )
        if not me.success:
            self.logger.error(f"❌ Connection test failed: users/me answered HTTP {me.status_code}: {me.message}")
            return False
        for what, response, permission in (
            ("users", await datasource.list_users(page=1, per_page=1), "ticket.agent"),
            ("groups", await datasource.list_groups(page=1, per_page=1), "admin.group"),
            ("roles", await datasource.list_roles(page=1, per_page=1), "admin.role"),
        ):
            if response.status_code == HttpStatusCode.FORBIDDEN.value:
                raise ConnectorInitError(
                    f"Zammad refused this token the list of {what}. Give the token the {permission} permission"
                    + ("" if what == "users" else " (Zammad before 6.5 lists them only to it)") + "."
                )
            if not response.success:
                self.logger.error(
                    f"❌ Connection test failed: Zammad did not list {what} (HTTP {response.status_code})"
                )
                return False
        self.logger.info("✅ Zammad connection test successful")
        return True

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

            if record.external_record_group_id:
                cat_rg = await self.data_entities_processor.get_record_group_by_external_id(
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
                    if perm_response.success and isinstance(perm_response.data, dict):
                        editor_role_ids = list(
                            CategoryAccess.from_permissions_response(perm_response.data).editor_role_ids
                        )
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
        connector_id: str,
        scope: str,
        created_by: str,
        data_entities_processor,
        **kwargs,
    ) -> "BaseConnector":
        """Factory method to create ZammadConnector instance"""
        return ZammadConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
