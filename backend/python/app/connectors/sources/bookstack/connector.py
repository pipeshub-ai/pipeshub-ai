import asyncio
import json
import sys
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from enum import Enum
from logging import Logger
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
)

from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.core.constants import IconPaths
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.error.stream_errors import (
    connector_not_ready,
    raise_for_stream_fetch,
)
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
    generate_record_sync_point_key,
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
    FilterCollection,
    FilterField,
    FilterOption,
    FilterOperatorType,
    FilterOptionsResponse,
    FilterType,
    IndexingFilterKey,
    OptionSourceType,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage
from app.connectors.sources.bookstack.access import ItemAccess, ItemRows, item_access
from app.connectors.sources.bookstack.audit_log import AuditLogReader, entity_ref, named_id, restored_ref
from app.connectors.sources.bookstack.common.apps import BookStackApp
from app.connectors.sources.bookstack.notices import BookStackNotices
from app.connectors.sources.bookstack.sync_health import BookStackSyncHealth
from app.models.entities import (
    AppRole,
    AppUser,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import Permission
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.sources.client.bookstack.bookstack import (
    BookStackClient,
    BookStackResponse,
    BookStackTokenConfig,
)
from app.sources.external.bookstack.bookstack import BookStackDataSource
from app.utils.streaming import create_stream_record_response


_AUDIT_LOG_PAGE_SIZE = 500
_RECORD_SCAN_PAGE_SIZE = 1000
_LISTING_PAGE_SIZE = 500
# The sync point that holds the last audit event applied. Its accessModel says how what is stored was worked out
# (who may view it, and which kinds are synced); a run that finds an older one (or none, or the old time-based
# cursors only) reads everything again, which also removes the shelves older versions stored.
_CURSOR_KEY = generate_record_sync_point_key("bookstack", "audit_log", "global")
_ACCESS_MODEL = 3
# Shelves are not synced: only books, chapters and pages. A shelf holds no content and gives no access to its books.
_GROUP_TYPES = {"book": RecordGroupType.BOOK, "chapter": RecordGroupType.CHAPTER}
_SHELF = "bookshelf"
_PAGE_UPSERT_EVENTS = frozenset({"page_create", "page_update", "page_move", "page_restore", "revision_restore"})
_GROUP_UPSERT_EVENTS = frozenset({"book_create", "book_update", "chapter_create", "chapter_update", "chapter_move"})
# Converting a chapter into a book, or a book into a shelf (its chapters become books), moves pages and destroys the
# old item without an event for either: only reading everything again finds where they went.
_RESHAPE_EVENTS = frozenset({"book_create_from_chapter", "bookshelf_create_from_book"})


def _about_a_shelf(event: dict) -> bool:
    """Whether an audit event only changes a shelf, which is not synced. Turning a book into a shelf is not: it
    moves the book's pages."""
    if event.get("type") in _RESHAPE_EVENTS:
        return False
    ref = entity_ref(event) or restored_ref(event)
    return str(event.get("type") or "").startswith(f"{_SHELF}_") or (ref is not None and ref[0] == _SHELF)


def _deleted_item_id(event: dict) -> int | None:
    """The item id of a page, chapter or book delete audit event, when it still has one.

    Only loggable_id is trusted. A purge (the recycle bin emptied, or set to
    keep nothing) clears it and rewrites the detail to the bare item name, and
    a name can itself look like "(1) Introduction".
    """
    loggable_id = event.get("loggable_id")
    if isinstance(loggable_id, bool):
        return None
    if isinstance(loggable_id, int):
        return loggable_id
    if isinstance(loggable_id, str) and loggable_id.isdigit():
        return int(loggable_id)
    return None


def _is_listing(body: object) -> bool:
    """A BookStack list answer. Errors (429, 500) come back as a JSON body without these keys."""
    return (
        isinstance(body, dict)
        and isinstance(body.get("data"), list)
        and isinstance(body.get("total"), int)
    )


class ReadOutcome(Enum):
    """How far a read of users or roles got, which decides what may be written from it."""

    COMPLETE = "complete"
    # Stopped for a reason that passes (no answer, a timeout, a rate limit, a server
    # error): role members and grants written from it would leave out whoever wasn't
    # read, and writing replaces what is stored, so what is stored stays until a
    # sync reads it all.
    INTERRUPTED = "interrupted"
    # BookStack refused it (401, 403 and other 4xx), which repeats every sync until
    # the token or its role is changed. Keeping stored access would keep access no one
    # can check, so the write goes ahead without what couldn't be read: it fails closed.
    REFUSED = "refused"


# In the role sync point: a role was kept through a refusal, so the next sync reads
# every role again instead of only the audit log.
ROLE_REBUILD_OWED = "rebuildOwed"

# Client errors that pass with time, unlike the rest of 4xx.
RETRYABLE_CLIENT_ERRORS = frozenset({408, HttpStatusCode.TOO_MANY_REQUESTS.value})


def read_failure(response: object) -> ReadOutcome:
    """The outcome of a BookStack answer that wasn't the data asked for.

    BookStack answers an error with a JSON body, which the data source can hand
    back as a success; its status tells them apart. No status means no answer at
    all: a network error, a body that wasn't JSON, or an exception.
    """
    status = getattr(response, "status_code", None)
    if (
        isinstance(status, int)
        and HttpStatusCode.BAD_REQUEST.value <= status < HttpStatusCode.INTERNAL_SERVER_ERROR.value
        and status not in RETRYABLE_CLIENT_ERRORS
    ):
        return ReadOutcome.REFUSED
    return ReadOutcome.INTERRUPTED


def _combined(first: ReadOutcome, second: ReadOutcome) -> ReadOutcome:
    """A read made of parts: any part that may still be read keeps what is stored."""
    for outcome in (ReadOutcome.INTERRUPTED, ReadOutcome.REFUSED):
        if outcome in (first, second):
            return outcome
    return ReadOutcome.COMPLETE


def _answered(response: object) -> bool:
    """Whether a get_user or get_role call came back with the item, not an error body."""
    if isinstance(response, BaseException) or not getattr(response, "success", False):
        return False
    status = getattr(response, "status_code", None)
    data = getattr(response, "data", None)
    return (
        (not isinstance(status, int) or status < HttpStatusCode.BAD_REQUEST.value)
        and isinstance(data, dict)
        and "error" not in data
    )


def _gone(response: object) -> bool:
    """BookStack says the item no longer exists: a definite answer, not a failed read."""
    return getattr(response, "status_code", None) == HttpStatusCode.NOT_FOUND.value


@ConnectorBuilder("BookStack")\
    .in_group("BookStack")\
    .with_supported_auth_types("API_TOKEN")\
    .with_description("Sync content from your BookStack instance")\
    .with_categories(["Knowledge Management"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.API_TOKEN).fields([
            AuthField(
                name="base_url",
                display_name="Bookstack Base URL",
                placeholder="https://bookstack.example.com",
                description="The base URL of your BookStack instance",
                field_type="TEXT",
                max_length=2048
            ),
            AuthField(
                name="token_id",
                display_name="Token ID",
                placeholder="YourTokenID",
                description="The Token ID generated from your BookStack profile",
                field_type="TEXT",
                max_length=100
            ),
            AuthField(
                name="token_secret",
                display_name="Token Secret",
                placeholder="YourTokenSecret",
                description="The Token Secret generated from your BookStack profile",
                field_type="PASSWORD",
                max_length=100
            )
        ])
    ])\
    .with_info(CONNECTOR_EMAIL_IDENTITY_INFO)\
    .with_resilience_config(
        # BookStack allows 180 API requests a minute per user by default (API_REQUESTS_PER_MIN) and answers more
        # with 429 and Retry-After, which the transport waits out; 5xx and dropped connections are retried too.
        rate_limit=10, max_retries=4, base_delay=1.0, max_delay=65.0,
    )\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.BOOKSTACK.value))\
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
        .add_filter_field(CommonFields.modified_date_filter("Filter content by modification date."))
        .add_filter_field(CommonFields.created_date_filter("Filter content by creation date."))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .add_filter_field(FilterField(
            name="book_ids",
            display_name="Book Name",
            description="Filter content by book names. Display: Name (slug)",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            default_value=[],
            option_source_type=OptionSourceType.DYNAMIC
        ))
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(False)
    )\
    .build_decorator()
class BookStackConnector(BaseConnector):
    """
    Connector for synchronizing data from a BookStack instance.
    Syncs books and chapters (record groups), pages (records), users, roles, and who may view what. Shelves are
    not synced.
    """
    bookstack_base_url: str

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
            BookStackApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )

        self.connector_name = Connectors.BOOKSTACK
        self.connector_id = connector_id

        # Initialize sync points for tracking changes
        self._create_sync_points()

        # Data source and configuration
        self.data_source: Optional[BookStackDataSource] = None
        self.batch_size = 100
        self.max_concurrent_batches = 5

        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

        # How the last _fetch_all_users_with_details and list_roles_with_details reads
        # ended; their callers read them straight after.
        self._users_read = ReadOutcome.COMPLETE
        self._roles_read = ReadOutcome.COMPLETE
        # How the last get_all_users listing ended, and whether this run's user sync
        # stored every user: a role write can only attach members whose user is stored.
        self._user_list_read = ReadOutcome.COMPLETE
        self._users_stored = True
        # Roles the last role read was refused (401, 403), and whether a page of the
        # role list was: those roles keep their stored members.
        self._refused_role_ids: list[int] = []
        self._role_list_refused = False
        # Whether this run left stored roles or record groups unwritten without noting
        # them as seen, so the unlink after a full sync must leave everything alone.
        self._kept_unnoted_access = False
        # Whether this role sync kept a refused role as stored, and whether it read
        # every role (_sync_user_roles_full). A kept role is read again next sync.
        self._refused_roles_kept = False
        self._roles_rebuilt = False
        # This run's health, the role details and user emails access is worked out from, the content
        # permission rows read so far, and the items whose grants depend on today's role memberships.
        self._health = BookStackSyncHealth()
        self._roles_details: dict[int, dict] = {}
        self._user_emails: dict[int, str] = {}
        self._rows_cache: dict[str, ItemRows | None] = {}
        self._dependent_pages: set[int] = set()
        self._dependent_groups: set[str] = set()

    def _create_sync_points(self) -> None:
        """Initialize sync points for different data types."""
        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_id=self.connector_id,
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
                f"/services/connectors/{self.connector_id}/config"
            )
            if not config:
                self.logger.error("BookStack configuration not found.")
                return False

            credentials_config = config.get("auth", {})

            self.bookstack_base_url = credentials_config.get("base_url")
            base_url = credentials_config.get("base_url")
            token_id = credentials_config.get("token_id")
            token_secret = credentials_config.get("token_secret")

            if not all([base_url, token_id, token_secret]):
                self.logger.error(
                    "BookStack base_url, token_id, or token_secret not found in configuration."
                )
                return False

            token_config = BookStackTokenConfig(
                base_url=base_url,
                token_id=token_id,
                token_secret=token_secret
            )
            client = BookStackClient.build_with_config(token_config, self.resilience)
            data_source = BookStackDataSource(client)
            # The same read a sync starts with, so a refused token or a user who may not list users is named.
            response = await data_source.list_users(count=1)
            if not response.success or not _is_listing(response.data):
                self.logger.error(
                    f"BookStack did not accept the connection: HTTP {response.status_code}: {response.error}")
                health = BookStackSyncHealth()
                health.note_status("the connection check", response.status_code, "users-manage", response.error)
                await BookStackNotices(self.notify, self._notification_title).report(health)
                return False
            self.data_source = data_source

            self.logger.info("BookStack client initialized successfully.")
            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize BookStack client: {e}", exc_info=True)
            return False

    async def test_connection_and_access(self) -> bool:
        """Whether the token works and its user may read everything a sync reads.

        A sync lists users and roles, reads the audit log and reads content permissions; each needs a role
        permission an ordinary user does not have, so each is tried, and the first one refused is named.
        """
        if not self.data_source:
            self.logger.error("BookStack data source not initialized")
            return False
        try:
            checks = (
                ("list books", self.data_source.list_books(count=1), None),
                ("list users", self.data_source.list_users(count=1), "Manage users"),
                ("list roles", self.data_source.list_roles(count=1), "Manage roles & role permissions"),
                ("read the audit log", self.data_source.list_audit_log(count=1),
                 "Manage app settings and Manage users"),
            )
            first_book = None
            for what, call, needs in checks:
                response = await call
                if not response.success or not _is_listing(response.data):
                    hint = f"; the token's user needs the '{needs}' permission" if needs and \
                        response.status_code == HttpStatusCode.FORBIDDEN.value else ""
                    self.logger.error(
                        f"BookStack connection test failed: could not {what} "
                        f"(HTTP {response.status_code}: {response.error}){hint}")
                    return False
                if what == "list books" and response.data["data"]:
                    first_book = response.data["data"][0]
            if first_book is not None:
                response = await self.data_source.get_content_permissions(
                    content_type="book", content_id=first_book["id"])
                if not response.success or ItemRows.from_api(response.data) is None:
                    self.logger.error(
                        "BookStack connection test failed: could not read content permissions "
                        f"(HTTP {response.status_code}); the token's user needs the "
                        "'Manage all book, chapter & page permissions' permission")
                    return False
            self.logger.info("BookStack connection test successful.")
            return True
        except Exception as e:
            self.logger.error(f"BookStack connection test failed: {e}", exc_info=True)
            return False

    async def get_signed_url(self, record: Record) -> Optional[str]:
        """BookStack has no signed URLs: its API needs the token, so previews stream the page instead."""
        return None

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
            raise connector_not_ready(self.display_name)

        record_id = record.external_record_id.split('/')[1]
        markdown_response = await self.data_source.export_page_markdown(record_id)
        raw_markdown = (markdown_response.data or {}).get("markdown")
        if not markdown_response.success or raw_markdown is None:
            self.logger.error(
                "BookStack markdown export failed for %s: %s",
                record_id,
                markdown_response.message or markdown_response.error,
            )
            raise_for_stream_fetch(
                success=markdown_response.success,
                has_payload=raw_markdown is not None,
                connector=self.display_name,
                status=markdown_response.status_code,
                message=markdown_response.message or markdown_response.error,
            )

        async def _markdown_stream() -> AsyncGenerator[bytes, None]:
            yield raw_markdown.encode("utf-8")

        return create_stream_record_response(
            _markdown_stream(),
            filename=record.record_name,
            mime_type=record.mime_type,
            fallback_filename=f"record_{record.id}"
        )

    def _get_app_users(self, users: List[Dict]) -> List[AppUser]:
        """Converts a list of BookStack user dictionaries to a list of AppUser objects."""
        app_users: List[AppUser] = []
        for user in users:
            app_users.append(
                AppUser(
                    app_name=Connectors.BOOKSTACK,
                    connector_id=self.connector_id,
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
        # Whether every page was read is left in self._user_list_read.
        all_bookstack_users, self._user_list_read = await self._read_listing(self.data_source.list_users, "users")

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
        self._roles_read = ReadOutcome.INTERRUPTED

        # First, get the basic list of all roles
        basic_roles, outcome = await self._read_listing(self.data_source.list_roles, "roles")
        if not basic_roles:
            self._roles_read = outcome
            if outcome is ReadOutcome.COMPLETE:
                self.logger.info("No roles found in BookStack.")
            return {}

        # Create a list of concurrent tasks to get the details for each role
        tasks = [self.data_source.get_role(role['id']) for role in basic_roles]

        # Execute all tasks in parallel and wait for them to complete
        detail_responses = await asyncio.gather(*tasks, return_exceptions=True)

        # Process the results into the final dictionary
        roles_details_map = {}
        for role, response in zip(basic_roles, detail_responses):
            role_id = role['id']
            if _answered(response):
                roles_details_map[role_id] = response.data
            elif _gone(response):
                self.logger.info(f"Role ID {role_id} is no longer in BookStack")
            else:
                outcome = _combined(outcome, read_failure(response))
                self.logger.error(f"Failed to get details for role ID {role_id}: {getattr(response, 'error', response)}")

        self._roles_read = outcome
        self.logger.info(f"Successfully fetched details for {len(roles_details_map)} roles.")
        return roles_details_map

    async def _read_listing(
        self, list_method: Callable[..., Awaitable[BookStackResponse]], what: str
    ) -> tuple[list[dict], ReadOutcome]:
        """Every row of a paged BookStack listing, and whether it was read to the end."""
        rows: list[dict] = []
        offset = 0
        while True:
            response = await list_method(count=self.batch_size, offset=offset)
            body = response.data if response.success else None
            if not _is_listing(body):
                self.logger.error(
                    f"Failed to list {what} at offset {offset}: {response.error or str(body)[:300]}"
                )
                return rows, read_failure(response)
            page = body["data"]
            if not page:
                if offset < body["total"]:
                    self.logger.warning(f"BookStack stopped listing {what} before the total it reported")
                    return rows, ReadOutcome.INTERRUPTED
                return rows, ReadOutcome.COMPLETE
            rows.extend(page)
            offset += len(page)
            if offset >= body["total"]:
                return rows, ReadOutcome.COMPLETE

    async def _user_details(self, user_ids: list[int]) -> tuple[list[dict], ReadOutcome]:
        """Each user's details, read concurrently. A user BookStack says is gone (404) is left out."""
        responses = await asyncio.gather(
            *(self.data_source.get_user(user_id) for user_id in user_ids), return_exceptions=True
        )
        details: list[dict] = []
        outcome = ReadOutcome.COMPLETE
        for user_id, response in zip(user_ids, responses):
            if _answered(response):
                details.append(response.data)
            elif _gone(response):
                self.logger.info(f"User ID {user_id} is no longer in BookStack")
            else:
                outcome = _combined(outcome, read_failure(response))
                self.logger.error(f"Failed to get details for user ID {user_id}: {getattr(response, 'error', response)}")
        return details, outcome

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

    #-------------------------------The run-----------------------------------#
    async def run_sync(self) -> None:
        """One sync: every change since the last audit event applied, or everything read again.

        Everything is read again on the first run, after the platform cleared the sync points (a full sync, a
        saved filter), after an upgrade from the time-based cursor, and when the audit log cannot be read. The
        cursor (the last audit event id applied) moves only when the whole run worked; a run that skipped
        anything raises, so the platform records it as failed and the next run repeats the work.
        """
        self._health = BookStackSyncHealth()
        self._kept_unnoted_access = False
        self._rows_cache = {}
        try:
            self.logger.info("Starting BookStack sync.")
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "bookstack", self.connector_id, self.logger
            )
            state = await self.record_sync_point.read_sync_point(_CURSOR_KEY) or {}
            self._dependent_pages = set(state.get("dependentPages") or [])
            self._dependent_groups = set(state.get("dependentGroups") or [])
            reader = AuditLogReader(self.data_source.list_audit_log)
            # Taken before reading anything: whatever changes during this run is read again by the next.
            latest = await reader.latest_id()
            start_id = (latest.last_id or 0) if latest.complete else None
            if start_id is None:
                self._health.note_status("the audit log", latest.status, "settings-manage", latest.message)
            if self._health.token_refused:
                # Nothing else will be answered; the run fails below and the admin is told once.
                return

            users = await self.get_all_users()
            if self._user_list_read is ReadOutcome.REFUSED:
                self._health.refused("the user list", 403, "users-manage")
            elif self._user_list_read is ReadOutcome.INTERRUPTED:
                self._health.fail("the user list")
            self._user_emails = {int(u.source_user_id): u.email for u in users if u.source_user_id and u.email}

            cursor = state.get("lastEventId")
            incremental = (start_id is not None and isinstance(cursor, int)
                           and state.get("accessModel") == _ACCESS_MODEL)
            if incremental:
                read = await reader.read_after(cursor)
                if not read.complete:
                    self._health.note_status("the audit log", read.status, "settings-manage", read.message)
                else:
                    await self._apply_events(read.events, users)
                    start_id = max(read.last_id or cursor, cursor)
            else:
                await self._sync_everything(users)

            if self._health.ok and start_id is not None:
                await self.record_sync_point.update_sync_point(_CURSOR_KEY, {
                    "lastEventId": start_id, "accessModel": _ACCESS_MODEL,
                    "dependentPages": sorted(self._dependent_pages),
                    "dependentGroups": sorted(self._dependent_groups),
                })
            self.logger.info("BookStack sync finished%s.", "" if self._health.ok else " with problems")
        except Exception as ex:
            self.logger.error(f"Error in BookStack connector run: {ex}", exc_info=True)
            self._health.fail("the run stopped on an error")
            raise
        finally:
            await BookStackNotices(self.notify, self._notification_title).report(self._health)
            # In the finally block so that no early return can report a failed run as a success.
            if sys.exc_info()[0] is None:
                self._health.raise_if_failed()

    async def _sync_everything(self, users: List[AppUser]) -> None:
        """Users, roles, then every book, chapter and page with its access, and the removal of what
        BookStack no longer has or the filters no longer include."""
        self.logger.info("Reading everything in BookStack.")
        if users:
            await self._sync_users_full(users)
        # Interrupted, the users not read keep their roles until a sync reads them all. Refused, the refusal
        # repeats every sync, so roles are written from the users read: access no one can check is not kept.
        self._users_stored = self._user_list_read is not ReadOutcome.INTERRUPTED
        if self._user_list_read is ReadOutcome.COMPLETE:
            await self._withdraw_users_not_listed(users)
        if not await self._sync_user_roles_full():
            self._health.fail("the roles and their members")
        if not await self._load_roles_for_access():
            return
        self._dependent_pages, self._dependent_groups = set(), set()
        await self._sync_groups_full()
        await self._sync_records_full()

    async def _load_roles_for_access(self) -> bool:
        """Every role with its members and permissions: access cannot be worked out from part of them."""
        self._roles_details = await self.list_roles_with_details()
        if self._roles_read is not ReadOutcome.COMPLETE or not self._roles_details:
            self._health.note_status("the roles", None if self._roles_read is ReadOutcome.INTERRUPTED else 403,
                                     "user-roles-manage")
            self._kept_unnoted_access = True
            self.logger.warning("BookStack roles were not all read, so content keeps its stored access this sync")
            return False
        return True

    #-------------------------------Changes from the audit log-----------------------------------#
    async def _apply_events(self, events: List[Dict], users: List[AppUser]) -> None:
        """Apply the audit events since the last run, by what they change."""
        events = [e for e in events if not _about_a_shelf(e)]
        if not events:
            # Nothing changed: no role, permission or page needs reading.
            return
        by_type: Dict[str, List[Dict]] = {}
        for event in events:
            by_type.setdefault(str(event.get("type")), []).append(event)
        self.logger.info(f"Applying {len(events)} BookStack audit events")

        user_events = by_type.get("user_create", []) + by_type.get("user_update", [])
        changed_users = {uid for e in user_events + by_type.get("user_delete", []) if (uid := named_id(e))}
        role_events = by_type.get("role_create", []) + by_type.get("role_update", []) + by_type.get("role_delete", [])
        if user_events:
            self._users_stored = self._user_list_read is not ReadOutcome.INTERRUPTED
            if not await self._handle_user_upsert_event(user_events, users):
                self._health.fail("role memberships of changed users")
        if by_type.get("user_delete"):
            await self._withdraw_deleted_users(by_type["user_delete"], users)
        everything = any(by_type.get(kind) for kind in _RESHAPE_EVENTS)
        if role_events:
            self._users_stored = self._user_list_read is not ReadOutcome.INTERRUPTED
            for event in by_type.get("role_delete", []):
                role_id = named_id(event)
                if role_id is not None:
                    await self._handle_role_delete_event(role_id)
            # A role's permissions decide the default access of every item, so a role change re-reads them all.
            if not await self._sync_user_roles_full():
                self._health.fail("the roles and their members")
            everything = True
        if not await self._load_roles_for_access():
            return
        if everything:
            self._dependent_pages, self._dependent_groups = set(), set()
            await self._sync_groups_full()
            await self._sync_records_full()
            return

        groups: Dict[str, Tuple[str, int]] = {}
        children_of: Set[Tuple[str, int]] = set()
        pages: Set[int] = set()
        # Rows from the page listings read below, so a listed page is not looked up again.
        rows: dict[int, dict] = {}
        for event in events:
            kind = str(event.get("type"))
            ref = entity_ref(event)
            if kind in _GROUP_UPSERT_EVENTS and ref:
                groups[f"{ref[0]}/{ref[1]}"] = ref
                if kind in ("chapter_move", "chapter_update", "book_update"):
                    children_of.add(ref)
            elif kind in _PAGE_UPSERT_EVENTS and ref and ref[0] == "page":
                pages.add(ref[1])
            elif kind == "permissions_update" and ref:
                if ref[0] == "page":
                    pages.add(ref[1])
                else:
                    groups[f"{ref[0]}/{ref[1]}"] = ref
                    children_of.add(ref)
            elif kind == "book_sort" and ref:
                # The sort screen moves pages and chapters between books and logs only the books.
                groups[f"book/{ref[1]}"] = ref
                children_of.add(ref)
            elif kind == "recycle_bin_restore":
                restored = restored_ref(event)
                if restored and restored[0] == "page":
                    pages.add(restored[1])
                elif restored:
                    groups[f"{restored[0]}/{restored[1]}"] = restored
                    children_of.add(restored)
        if changed_users:
            # Grants that name people, and owners' grants, follow memberships that just changed.
            for page_id in self._dependent_pages:
                pages.add(int(page_id))
            for external_id in self._dependent_groups:
                kind_, _, raw = external_id.partition("/")
                if raw.isdigit():
                    groups[external_id] = (kind_, int(raw))
            rows.update(await self._pages_owned_by(changed_users))

        for ref in groups.values():
            await self._upsert_group(*ref)
        for ref in children_of:
            rows.update(await self._children_of(*ref))
        pages |= set(rows)
        for page_id in sorted(pages):
            await self._upsert_page(page_id, rows.get(page_id))

        unnamed = False
        for kind in ("page_delete", "chapter_delete", "book_delete"):
            for event in by_type.get(kind, []):
                content_type = kind.rsplit("_", 1)[0]
                item_id = _deleted_item_id(event)
                if item_id is None:
                    unnamed = True
                elif content_type == "page":
                    if not await self._handle_page_delete_event(item_id):
                        self._health.fail(f"the delete of page {item_id}")
                elif not await self._handle_record_group_delete_event(event, content_type):
                    self._health.fail(f"the delete of {content_type} {item_id}")
        if unnamed or by_type.get("recycle_bin_destroy") or by_type.get("recycle_bin_empty"):
            # A purge names nothing any more: compare with what BookStack still lists.
            if not await self._remove_pages_no_longer_in_bookstack():
                self._health.fail("removing purged pages")
            if not await self._remove_groups_no_longer_in_bookstack():
                self._health.fail("removing purged books and chapters")

    async def _pages_owned_by(self, user_ids: set[int]) -> dict[int, dict]:
        """The listed rows of the pages these users own, by page id."""
        found: dict[int, dict] = {}
        for user_id in sorted(user_ids):
            listing = await self._list_all_pages({"owned_by": str(user_id)})
            if listing is None:
                self._health.fail(f"the pages owned by user {user_id}")
                continue
            found.update({int(p["id"]): p for p in listing if p.get("id")})
        return found

    async def _children_of(self, kind: str, item_id: int) -> dict[int, dict]:
        """The listed rows of the pages under a book or chapter, by page id, and the book's chapters brought up
        to date on the way."""
        if kind not in ("book", "chapter"):
            return {}
        if kind == "book":
            chapters = await self._list_all(self.data_source.list_chapters, {"book_id": str(item_id)}, "chapters")
            if chapters is None:
                self._health.fail(f"the chapters of book {item_id}")
            for chapter in chapters or []:
                await self._upsert_group("chapter", int(chapter["id"]), chapter)
        listing = await self._list_all_pages({f"{kind}_id": str(item_id)})
        if listing is None:
            self._health.fail(f"the pages of {kind} {item_id}")
            return {}
        return {int(p["id"]): p for p in listing if p.get("id")}

    async def _withdraw_users_not_listed(self, users: List[AppUser]) -> None:
        """A user a complete listing no longer has was deleted in BookStack: they lose what the connector gave."""
        listed = {u.source_user_id for u in users if u.source_user_id}
        if not listed:
            return
        try:
            stored = await self.data_entities_processor.get_all_app_users(self.connector_id, raise_on_error=True)
        except Exception as e:
            self.logger.error(f"Could not list the stored BookStack users: {e}")
            self._health.fail("the stored users")
            return
        gone = [u for u in stored if u.source_user_id and u.source_user_id not in listed]
        for user in gone:
            await self._withdraw_user_roles(int(user.source_user_id), user.full_name)
        if gone:
            await self.data_entities_processor.withdraw_app_user_access(self.connector_id, gone)

    async def _withdraw_deleted_users(self, events: List[Dict], users: List[AppUser]) -> None:
        listed = {u.source_user_id for u in users if u.source_user_id}
        deleted = {str(uid) for e in events if (uid := named_id(e)) is not None} - listed
        if not deleted:
            return
        try:
            stored = await self.data_entities_processor.get_all_app_users(self.connector_id, raise_on_error=True)
        except Exception as e:
            self.logger.error(f"Could not list the stored BookStack users: {e}")
            self._health.fail("the stored users")
            return
        gone = [u for u in stored if u.source_user_id in deleted]
        for user in gone:
            await self._withdraw_user_roles(int(user.source_user_id), user.full_name)
        if gone:
            await self.data_entities_processor.withdraw_app_user_access(self.connector_id, gone)

    #-------------------------------Access-----------------------------------#
    async def _rows(self, kind: str, item_id: int) -> Optional[ItemRows]:
        """An item's content permission rows, read once per run; None when BookStack did not give them."""
        key = f"{kind}/{item_id}"
        if key in self._rows_cache:
            return self._rows_cache[key]
        response = await self.data_source.get_content_permissions(content_type=kind, content_id=item_id)
        rows = ItemRows.from_api(response.data) if response.success else None
        if rows is None:
            if response.status_code == HttpStatusCode.NOT_FOUND.value:
                return None
            self._health.note_status(f"the permissions of {key}", response.status_code, "restrictions-manage-all",
                                     response.error)
            return None
        self._rows_cache[key] = rows
        return rows

    async def _access(self, kind: str, item: Dict) -> Optional[ItemAccess]:
        """Who may view one book, chapter or page, as permissions; None when it cannot be worked out."""
        item_id = int(item["id"])
        chain_refs = [(kind, item_id)]
        if kind == "page" and item.get("chapter_id"):
            chain_refs.append(("chapter", int(item["chapter_id"])))
        if kind in ("page", "chapter") and item.get("book_id"):
            chain_refs.append(("book", int(item["book_id"])))
        chain = []
        for ref in chain_refs:
            rows = await self._rows(*ref)
            if rows is None:
                return None
            chain.append(rows)
        owner = item.get("owned_by")
        owner_id = int(owner["id"]) if isinstance(owner, dict) else (int(owner) if owner else chain[0].owner_id)
        access = item_access(kind, chain, self._roles_details, self._user_emails, owner_id)
        target = self._dependent_pages if kind == "page" else self._dependent_groups
        key: Any = item_id if kind == "page" else f"{kind}/{item_id}"
        if access.membership_dependent:
            target.add(key)
        else:
            target.discard(key)
        return access

    #-------------------------------Books, chapters-----------------------------------#
    async def _list_all(self, list_method: Callable[..., Awaitable[BookStackResponse]],
                        filters: Optional[Dict[str, str]], what: str) -> Optional[List[Dict]]:
        """Every row of a book or chapter listing, or None when it was not read to the end."""
        rows: List[Dict] = []
        offset = 0
        while True:
            response = await list_method(count=_LISTING_PAGE_SIZE, offset=offset, filter=filters)
            body = response.data if response.success else None
            if not _is_listing(body):
                self._health.note_status(f"the {what} listing", response.status_code, "content", response.error)
                return None
            page = body["data"]
            rows.extend(page)
            offset += len(page)
            if offset >= body["total"]:
                return rows
            if not page:
                self._health.fail(f"the {what} listing stopped before its total")
                return None

    async def _list_all_pages(self, filters: Optional[Dict[str, str]]) -> Optional[List[Dict]]:
        rows: List[Dict] = []
        offset = 0
        while True:
            listing = self._page_listing(await self.data_source.list_pages(
                count=_LISTING_PAGE_SIZE, offset=offset, filter=filters))
            if listing is None:
                return None
            rows.extend(listing["data"])
            offset += len(listing["data"])
            if offset >= listing["total"]:
                return rows
            if not listing["data"]:
                return None

    def _book_in_scope(self, book_id: Optional[int]) -> bool:
        book_ids, operator = self._get_book_id_filter()
        if not book_ids:
            return True
        inside = book_id in book_ids
        return inside if operator.value == "in" else not inside

    def _group_for(self, kind: str, item: Dict) -> RecordGroup:
        parent = f"book/{item['book_id']}" if kind == "chapter" and item.get("book_id") else None
        return RecordGroup(
            name=item.get("name"),
            org_id=self.data_entities_processor.org_id,
            external_group_id=f"{kind}/{item['id']}",
            description=item.get("description", ""),
            connector_name=Connectors.BOOKSTACK,
            connector_id=self.connector_id,
            group_type=_GROUP_TYPES[kind],
            parent_external_group_id=parent,
            # Each group carries its own access: BookStack decides a chapter's apart from its book's.
            inherit_permissions=False,
        )

    async def _sync_groups_full(self) -> None:
        listed: Set[str] = set()
        complete = True
        batch: List[Tuple[RecordGroup, Optional[List[Permission]]]] = []
        books_in_scope: Set[int] = set()
        for kind, list_method in (("book", self.data_source.list_books), ("chapter", self.data_source.list_chapters)):
            items = await self._list_all(list_method, None, f"{kind}s")
            if items is None:
                complete = False
                continue
            for item in items:
                if kind == "book" and not self._book_in_scope(item.get("id")):
                    continue
                if kind == "chapter" and item.get("book_id") not in books_in_scope:
                    continue
                if kind == "book":
                    books_in_scope.add(item["id"])
                listed.add(f"{kind}/{item['id']}")
                access = await self._access(kind, item)
                if access is None:
                    self._kept_unnoted_access = True
                    continue
                batch.append((self._group_for(kind, item), access.permissions))
        if batch:
            await self.data_entities_processor.on_new_record_groups(batch)
        if complete:
            if not await self._remove_groups_not_listed(listed):
                self._health.fail("removing books and chapters BookStack no longer has")

    async def _upsert_group(self, kind: str, item_id: int, item: Optional[Dict] = None) -> None:
        if kind not in _GROUP_TYPES:
            return
        if item is None:
            fetch = {"book": self.data_source.get_book, "chapter": self.data_source.get_chapter}[kind]
            response = await fetch(item_id)
            if not response.success:
                if response.status_code == HttpStatusCode.NOT_FOUND.value:
                    # Gone, or deleted later in this batch: its delete event removes it.
                    return
                self._health.note_status(f"{kind} {item_id}", response.status_code, "content", response.error)
                return
            item = response.data
        book_id = item_id if kind == "book" else item.get("book_id")
        if not self._book_in_scope(book_id):
            group = await self.data_entities_processor.get_record_group_by_external_id(
                self.connector_id, f"{kind}/{item_id}")
            if group is not None and not await self.data_entities_processor.on_record_group_deleted(
                    f"{kind}/{item_id}", self.connector_id):
                self._health.fail(f"removing {kind} {item_id}, now outside the book filter")
            return
        access = await self._access(kind, item)
        if access is None:
            return
        await self.data_entities_processor.on_new_record_groups([(self._group_for(kind, item), access.permissions)])

    async def _remove_groups_not_listed(self, listed: Set[str]) -> bool:
        """Remove the books and chapters complete listings no longer return (gone, or now outside the book
        filter), with what is under them, and any shelf an older version stored."""
        try:
            stored = await self.data_entities_processor.get_nodes_by_filters(
                CollectionNames.RECORD_GROUPS.value, {"connectorId": self.connector_id},
                return_fields=["externalGroupId"], raise_on_error=True,
            )
        except Exception as e:
            self.logger.error(f"Could not list the stored BookStack groups, so none are removed: {e}")
            return False
        done = True
        for node in stored:
            external_id = node.get("externalGroupId") or ""
            kind, _, raw = external_id.partition("/")
            if not raw.isdigit() or external_id in listed or (kind not in _GROUP_TYPES and kind != _SHELF):
                continue
            self.logger.info(f"{external_id} is no longer in BookStack or in the sync's scope; removing it")
            # The processor reports a failed removal as False, not as an exception, and also says False for a
            # group already gone: a chapter goes with its book, which may come first in this loop.
            if await self.data_entities_processor.on_record_group_deleted(external_id, self.connector_id):
                continue
            if await self._group_still_stored(external_id):
                self.logger.warning(f"Could not remove {external_id}; it is tried again next sync")
                done = False
        return done

    async def _group_still_stored(self, external_id: str) -> bool:
        """Whether a record group is still stored; True when the lookup fails, so nothing counts as removed."""
        try:
            group = await self.data_entities_processor.get_record_group_by_external_id(self.connector_id, external_id)
        except Exception as e:
            self.logger.warning(f"Could not look up {external_id}: {e}")
            return True
        return group is not None

    async def _remove_groups_no_longer_in_bookstack(self) -> bool:
        listed: Set[str] = set()
        books_in_scope: Set[int] = set()
        for kind, list_method in (("book", self.data_source.list_books), ("chapter", self.data_source.list_chapters)):
            items = await self._list_all(list_method, None, f"{kind}s")
            if items is None:
                return False
            for item in items:
                if kind == "book" and not self._book_in_scope(item.get("id")):
                    continue
                if kind == "chapter" and item.get("book_id") not in books_in_scope:
                    continue
                if kind == "book":
                    books_in_scope.add(item["id"])
                listed.add(f"{kind}/{item['id']}")
        return await self._remove_groups_not_listed(listed)

    #---------------------------Pages-----------------------------------#
    async def _sync_records_full(self) -> None:
        """Every page within the filters, then the removal of the records of pages no longer listed.

        Each listing page is written before the next is read, so memory holds one listing page, one batch and
        the listed ids. Only a listing read to the end removes anything.
        """
        book_ids, book_ids_operator = self._get_book_id_filter()
        date_filters = self._get_date_filters()
        api_filters = self._build_date_filter_params(*date_filters)
        listed: set[str] = set()
        batch: list[tuple[FileRecord, list[Permission]]] = []
        offset = 0
        while True:
            listing = self._page_listing(await self.data_source.list_pages(
                count=_LISTING_PAGE_SIZE, offset=offset, filter=api_filters))
            if listing is None:
                break
            for page in listing["data"]:
                if not page.get("id") or page.get("draft"):
                    continue
                if not self._page_passes_filters(page, book_ids, book_ids_operator, date_filters):
                    continue
                listed.add(f"page/{page['id']}")
                prepared = await self._page_record(page)
                if prepared is None:
                    self._kept_unnoted_access = True
                    continue
                batch.append(prepared)
                if len(batch) >= self.batch_size:
                    await self.data_entities_processor.on_new_records(batch)
                    batch = []
            offset += len(listing["data"])
            if offset >= listing["total"]:
                break
            if not listing["data"]:
                # An empty page short of the total is a cut-off listing, not the end.
                listing = None
                break
        if batch:
            await self.data_entities_processor.on_new_records(batch)
        if listing is None:
            self._health.fail("the page listing")
            self._kept_unnoted_access = True
            return
        if not await self._remove_pages_not_listed(listed, book_ids, book_ids_operator, date_filters):
            self._health.fail("removing pages BookStack no longer has")

    async def _page_record(self, page: Dict) -> Optional[Tuple[FileRecord, List[Permission]]]:
        """The record of a listed page with its exact access; None when its access could not be read."""
        page_id = page["id"]
        access = await self._access("page", page)
        if access is None:
            return None
        existing = await self.data_entities_processor.get_record_by_external_id(self.connector_id, f"page/{page_id}")
        parent = f"chapter/{page['chapter_id']}" if page.get("chapter_id") else f"book/{page.get('book_id')}"
        updated_ms = self._parse_timestamp(page.get("updated_at"))
        created_ms = self._parse_timestamp(page.get("created_at"))
        record = FileRecord(
            id=existing.id if existing else str(uuid.uuid4()),
            record_name=page.get("name"),
            external_record_id=f"page/{page_id}",
            connector_name=Connectors.BOOKSTACK,
            connector_id=self.connector_id,
            record_type=RecordType.FILE.value,
            external_record_group_id=parent,
            origin=OriginTypes.CONNECTOR.value,
            org_id=self.data_entities_processor.org_id,
            source_updated_at=updated_ms,
            updated_at=updated_ms,
            source_created_at=created_ms,
            created_at=created_ms,
            # The processor sets the version: 0 for a new record, one more only when the revision changed.
            version=0,
            external_revision_id=str(page.get("revision_count")),
            weburl=f"{self.bookstack_base_url.rstrip('/')}/books/{page.get('book_slug')}/page/{page.get('slug')}",
            mime_type=MimeTypes.MARKDOWN.value,
            extension="md",
            is_file=True,
            size_in_bytes=0,
            # A page's access is its own: BookStack decides it apart from its book's or chapter's.
            inherit_permissions=False,
        )
        record.rewrite_permissions = True
        if not self.indexing_filters.is_enabled(IndexingFilterKey.FILES, default=True):
            record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
        return record, access.permissions

    async def _upsert_page(self, page_id: int, page: dict | None = None) -> None:
        """Bring one page up to date: new, changed, moved, restored, out of scope or gone. ``page`` is its row from
        a page listing read this run (the same row a lookup by id returns); without one, the page is looked up."""
        if page is None:
            answered, page = await self._fetch_page(page_id)
            if not answered:
                self._health.fail(f"page {page_id}")
                return
        book_ids, book_ids_operator = self._get_book_id_filter()
        date_filters = self._get_date_filters()
        in_scope = page is not None and not page.get("draft") and self._page_passes_filters(
            page, book_ids, book_ids_operator, date_filters)
        if not in_scope:
            # Gone (its delete event follows), or moved or changed out of the sync's scope.
            record = await self.data_entities_processor.get_record_by_external_id(self.connector_id, f"page/{page_id}")
            if record is not None and page is not None and not await self._delete_page_record(record, page_id):
                self._health.fail(f"removing page {page_id}, now outside the sync's scope")
            return
        prepared = await self._page_record(page)
        if prepared is not None:
            await self.data_entities_processor.on_new_records([prepared])


    #-------------------------------Users and roles-----------------------------------#
    async def _sync_users_full(self, app_users: List[AppUser]) -> None:
        """
        Fetches all users from BookStack, transforms them into AppUser objects,
        and upserts them into the database.
        """
        self.logger.info("Starting BookStack user sync...")

        # Pass the AppUser objects to the data processor to save in ArangoDB
        await self.data_entities_processor.on_new_app_users(app_users)
        self.logger.info("✅ Finished syncing BookStack users.")

    async def _handle_user_create_event(self, user_create_events: List[Dict], app_users: List[AppUser]) -> None:
        self.logger.info("Processing new or updated users from audit log !")
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

    async def _handle_user_upsert_event(self, user_upsert_events: List[Dict], app_users: List[AppUser]) -> bool:
        """Re-sync the roles of each user named in the events.

        Returns False when a user's roles could not be read in full; that user's
        role memberships are then left as stored.
        """
        self.logger.info("Processing user upsert events... !")
        done = True

        # First handle the user update itself (updates the user record in arango)
        await self._handle_user_create_event(user_upsert_events, app_users)

        # Process each user upsert event
        for event in user_upsert_events:
            user_id, name = self._parse_id_and_name_from_event(event)

            if user_id is None:
                self.logger.warning(f"Could not parse user ID from event: {event}")
                continue

            self.logger.info(f"Processing role updates for user: {name} (ID: {user_id})")

            try:
                # Get the updated user details to find their roles
                user_response = await self.data_source.get_user(user_id)

                if not _answered(user_response):
                    self.logger.error(f"Failed to fetch details for user ID {user_id}: {user_response.error}")
                    if not _gone(user_response) and read_failure(user_response) is ReadOutcome.INTERRUPTED:
                        done = False
                    else:
                        # Gone, or refused for good: their roles can't be confirmed, so
                        # access through them is withdrawn rather than kept unchecked.
                        await self._withdraw_user_roles(user_id, name)
                    continue

                user_details = user_response.data
                roles = user_details.get('roles', [])

                user_email = user_details.get('email')
                if not user_email:
                    self.logger.warning(f"User {name} (ID: {user_id}) has no email, skipping role updates.")
                    continue

                # Read every role the user is in, with its members, before taking the
                # user out of any: the roles are written back whole, and one that
                # can't be read would otherwise lose this user until its next change.
                role_reads = []
                for role in roles:
                    role_id = role.get('id')
                    if role_id:
                        self.logger.info(f"Resyncing role '{role.get('display_name')}' (ID: {role_id}) due to user update")
                        role_reads.append(await self._read_role_with_members(role_id))
                    else:
                        self.logger.warning(f"Role '{role.get('display_name')}' has no ID, skipping resync")
                if any(outcome is ReadOutcome.INTERRUPTED for outcome, _ in role_reads):
                    self.logger.warning(
                        f"Could not read every role of user {name} (ID: {user_id}); their roles stay as stored this sync"
                    )
                    done = False
                    continue

                # Delete edges to groups for the user. A failed lookup raises rather
                # than answering None, which would count the change as applied.
                user = await self.data_entities_processor.get_user_by_email(user_email, raise_on_error=True)
                if not user:
                    self.logger.warning(f"User {name} (ID: {user_id}) not found in the database, skipping role updates.")
                    continue
                await self.data_entities_processor.delete_edges_between_collections(
                    user.id, CollectionNames.USERS.value, CollectionNames.PERMISSION.value, CollectionNames.ROLES.value,
                    connector_id=self.connector_id,
                )

                if not roles:
                    self.logger.info(f"User {name} (ID: {user_id}) has no roles assigned.")
                    continue

                # Resync each role the user is part of
                self.logger.info(f"User {name} is part of {len(roles)} role(s). Resyncing...")
                read_roles = [role for _, role in role_reads if role]
                if read_roles:
                    await self.data_entities_processor.on_new_app_roles(read_roles)

            except Exception as e:
                self.logger.error(f"Error processing user upsert for user ID {user_id}: {e}", exc_info=True)
                # Its role edges may already be deleted; the next sync applies the event again.
                done = False

        self.logger.info("✅ Finished processing user upsert events and role resyncs")
        return done

    async def _withdraw_user_roles(self, user_id: int, name: Optional[str]) -> None:
        """Take a BookStack user out of every role they are stored in.

        Raises when the stored user can't be looked up or their role edges can't be
        deleted, so the caller reads the event again; a user never stored has nothing to withdraw.
        """
        user = await self.data_entities_processor.get_user_by_source_id(
            str(user_id), self.connector_id, raise_on_error=True
        )
        if not user:
            return
        self.logger.warning(f"Withdrawing the stored roles of user {name} (ID: {user_id})")
        await self.data_entities_processor.delete_edges_between_collections(
            user.id, CollectionNames.USERS.value, CollectionNames.PERMISSION.value, CollectionNames.ROLES.value,
            connector_id=self.connector_id,
        )

    async def _sync_user_roles_full(self) -> bool:
        """
        Fetches all roles with their detailed user assignments to build and
        upsert user groups with their member users (AppUser objects).

        Returns False when the roles or users could not be read in full, or this
        run's user sync didn't store every user: roles that can't be written whole
        are left as stored, since writing one replaces its members, and the next
        sync does this again.
        """
        self.logger.info("Starting BookStack user group and permissions sync...")
        self._roles_rebuilt = True
        # This read decides what is owed: a role refused earlier in this sync and read now is not.
        self._refused_roles_kept = False
        if not self._users_stored:
            # The processor attaches a member only through their stored user, so a role
            # written now would leave out everyone the user sync didn't store.
            self.logger.warning("⚠️ BookStack users were not all stored, so role members stay as stored this sync")
            return False

        # 1. Fetch details for all roles, including the users assigned to each role.
        # A role that wasn't read is not in the batch, so it keeps its stored members.
        # Interrupted, the role checkpoint stays and the next sync reads it again.
        # Refused (401, 403), it keeps its members and the checkpoint moves on: the
        # refusal repeats every sync, and holding the checkpoint for it would replay
        # the audit log on every sync and lose role deletes made meanwhile.
        all_roles_with_details = await self._fetch_all_roles_with_details()
        roles_complete = self._roles_read is not ReadOutcome.INTERRUPTED
        if self._roles_read is ReadOutcome.REFUSED:
            self.logger.error(
                "❌ BookStack refused part of the role list or a role's details; roles not read keep their "
                "stored members until they can be read"
            )
            if not await self._keep_refused_roles():
                return False
        elif not roles_complete:
            self.logger.warning("⚠️ BookStack roles were not all read; the next sync reads them again")
        if not all_roles_with_details:
            if self._roles_read is ReadOutcome.COMPLETE:
                self.logger.warning("No roles found in BookStack. Aborting user group sync.")
            return roles_complete
        self.logger.info(f"Found {len(all_roles_with_details)} total roles with details.")

        # 2. Fetch all user details to get complete user information
        all_users_with_details = await self._fetch_all_users_with_details()
        if self._users_read is ReadOutcome.INTERRUPTED:
            self.logger.warning(
                "⚠️ BookStack users were not read in full, so role members stay as stored this sync"
            )
            return False

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
                connector_id=self.connector_id,
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
                    connector_id=self.connector_id,
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
        if self._roles_read is ReadOutcome.COMPLETE:
            return await self._remove_roles_bookstack_no_longer_has(all_roles_with_details)
        return roles_complete

    async def _remove_roles_bookstack_no_longer_has(self, roles: list[dict]) -> bool:
        """Remove the stored roles a complete role read did not return.

        Writing roles replaces the members of the roles written only, so a role deleted
        in BookStack while its delete event was missed (the role list refused, or a full
        sync's sync points cleared) would keep its members. False when the stored roles
        can't be listed, so the next sync reads every role again.
        """
        present = {str(role.get("id")) for role in roles}
        try:
            stored = await self.data_entities_processor.get_nodes_by_filters(
                CollectionNames.ROLES.value,
                {"connectorId": self.connector_id},
                return_fields=["externalRoleId"],
                raise_on_error=True,
            )
        except Exception as e:
            self.logger.error(f"Could not list the stored BookStack roles: {e}")
            self._kept_unnoted_access = True
            return False
        gone = sorted({str(node.get("externalRoleId")) for node in stored if node.get("externalRoleId")} - present)
        for role_id in gone:
            self.logger.info(f"Role ID {role_id} is no longer in BookStack; removing it")
            await self.data_entities_processor.on_app_role_deleted(
                external_role_id=role_id, connector_id=self.connector_id
            )
        return True

    async def _keep_refused_roles(self) -> bool:
        """Keep the stored members of the roles BookStack refused to show.

        They are not in the role write, so their members stay. Each one is noted as
        seen by a full sync in progress, so the unlink after it leaves them alone. A
        refused page of the role list names no roles, so that sync reports that it
        did not read everything instead. False when the stored roles can't be read.
        """
        if self._role_list_refused:
            self._kept_unnoted_access = True
            self._refused_roles_kept = True
        for role_id in self._refused_role_ids:
            if not await self._keep_refused_role(role_id):
                return False
        return True

    async def _keep_refused_role(self, role_id: int) -> bool:
        """Keep one refused role as stored and note it as seen; False when that can't be read."""
        self._refused_roles_kept = True
        try:
            await self.data_entities_processor.has_stored_permissions(
                "role", self.connector_id, str(role_id), keep=True
            )
        except Exception as e:
            self.logger.error(f"Could not read the stored members of refused role ID {role_id}: {e}")
            self._kept_unnoted_access = True
            return False
        return True

    def full_sync_coverage(self) -> FullSyncCoverage:
        """A full sync that skipped anything, or left access as stored without noting it as seen, did not read
        everything, so nothing is unlinked after it."""
        return FullSyncCoverage(complete=self._health.ok and not self._kept_unnoted_access)

    def _parse_timestamp(self, timestamp_str: Optional[str]) -> Optional[int]:
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

        Whether every role was read is left in ``self._roles_read``.
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
        for role, res in zip(basic_roles, responses):
            if _answered(res):
                detailed_roles.append(res.data)
            elif _gone(res):
                self.logger.info(f"Role ID {role['id']} is no longer in BookStack")
            else:
                outcome = read_failure(res)
                if outcome is ReadOutcome.REFUSED:
                    self._refused_role_ids.append(role['id'])
                self._roles_read = _combined(self._roles_read, outcome)
                self.logger.error(f"Failed to get details for role ID {role['id']}: {getattr(res, 'error', res)}")

        self.logger.info(f"Successfully fetched details for {len(detailed_roles)} roles.")
        return detailed_roles

    async def _fetch_all_roles(self) -> List[Dict]:
        """Helper to fetch all roles with pagination; whether it got to the end is left in ``self._roles_read``."""
        self.logger.info("Fetching all roles from BookStack...")
        all_roles, self._roles_read = await self._read_listing(self.data_source.list_roles, "roles")
        self._refused_role_ids = []
        self._role_list_refused = self._roles_read is ReadOutcome.REFUSED
        return all_roles

    async def _fetch_all_users_with_details(self) -> List[Dict]:
        """Helper to fetch all users and then get detailed profiles concurrently.

        Whether every user was read is left in ``self._users_read``.
        """
        self.logger.info("Fetching all user IDs...")
        self._users_read = ReadOutcome.INTERRUPTED
        all_users_summary, outcome = await self._read_listing(self.data_source.list_users, "users")

        if not all_users_summary:
            self._users_read = outcome
            return []

        self.logger.info(f"Fetching details for {len(all_users_summary)} users concurrently...")
        detailed_users, details_outcome = await self._user_details([user['id'] for user in all_users_summary])

        self._users_read = _combined(outcome, details_outcome)
        if self._users_read is ReadOutcome.REFUSED:
            self.logger.error(
                f"❌ BookStack refused part of the user list; roles are written from the {len(detailed_users)} "
                "users read, so access through them is withdrawn from the rest until they can be read"
            )
        self.logger.info(f"Successfully fetched details for {len(detailed_users)} users.")
        return detailed_users

    async def _read_role_with_members(
        self, role_id: int
    ) -> tuple[ReadOutcome, tuple[AppRole, list[AppUser]] | None]:
        """A role and its members as BookStack has them now; (COMPLETE, None) when the role is gone."""
        role_details_response = await self.data_source.get_role(role_id)
        if not _answered(role_details_response):
            if _gone(role_details_response):
                return ReadOutcome.COMPLETE, None
            self.logger.warning(
                f"Could not fetch details for role ID {role_id}: {getattr(role_details_response, 'error', None)}"
            )
            return read_failure(role_details_response), None

        role_details = role_details_response.data

        # Create the AppRole object
        app_role = AppRole(
            app_name=self.connector_name,
            connector_id=self.connector_id,
            source_role_id=str(role_id),
            name=role_details.get("display_name"),
            org_id=self.data_entities_processor.org_id
        )

        # Fetch detailed user information for each user in the role, in parallel
        user_ids = [user.get("id") for user in role_details.get("users", []) if user.get("id")]
        users_details, outcome = await self._user_details(user_ids)

        app_users = [
            AppUser(
                app_name=self.connector_name,
                connector_id=self.connector_id,
                source_user_id=str(user_details.get("id")),
                email=user_details.get("email", ""),
                full_name=user_details.get("name", ""),
                org_id=self.data_entities_processor.org_id,
                is_active=True,  # Users in roles are assumed to be active
                title=user_details.get("title") if user_details.get("title") else None,
                source_created_at=self._parse_timestamp(user_details.get("created_at")) if user_details.get("created_at") else None,
                source_updated_at=self._parse_timestamp(user_details.get("updated_at")) if user_details.get("updated_at") else None
            )
            for user_details in users_details
        ]
        return outcome, (app_role, app_users)

    async def _handle_role_delete_event(self, role_id: int) -> None:
        """
        Handles a 'role_delete' audit log event by parsing the role ID
        and calling the data processor to delete the corresponding user group.
        """
        self.logger.info(f"Processing deletion for user group (External ID: {role_id})")

        # Call the data processor to delete the group from the database
        await self.data_entities_processor.on_app_role_deleted(
            external_role_id=str(role_id),
            connector_id=self.connector_id
        )

    #---------------------------Removing what BookStack no longer has-----------------------------------#
    async def _remove_pages_not_listed(
        self,
        listed: set[str],
        book_ids: set[int] | None,
        book_ids_operator: FilterOperatorType | None,
        date_filters: tuple[datetime | None, datetime | None, datetime | None, datetime | None],
    ) -> bool:
        """Delete the records of pages a complete listing no longer returns.

        A complete listing leaves out pages deleted at the source and pages the
        sync filters now exclude, so both leave every store. The listing pages
        by offset, so a delete while it runs can shift a live page past it:
        each page is looked up by id first, and kept if BookStack still has it
        within the filters, or if the lookup fails. Returns False when any page
        was kept for want of an answer, or its delete failed.
        """
        stale: list[Record] = []
        after_key: str | None = None
        try:
            while True:
                # Trash included: a trashed page BookStack no longer has is removed too.
                batch = await self.data_entities_processor.get_records_by_status(
                    self.connector_id, None, limit=_RECORD_SCAN_PAGE_SIZE, after_key=after_key,
                    visibility=RecordVisibility.ALL,
                )
                stale.extend(
                    r for r in batch
                    if (r.external_record_id or "").startswith("page/")
                    and r.external_record_id not in listed
                )
                if len(batch) < _RECORD_SCAN_PAGE_SIZE:
                    break
                after_key = batch[-1].id
        except Exception as e:
            self.logger.error(f"Could not read the synced page records, so none are removed: {e}")
            return False

        if stale:
            self.logger.info(f"Checking {len(stale)} page records the listing no longer returned")
        all_done = True
        for record in stale:
            try:
                page_id = int(record.external_record_id.split("/", 1)[1])
            except ValueError:
                self.logger.warning(f"Unreadable page id {record.external_record_id}; keeping its record")
                continue
            answered, page = await self._fetch_page(page_id)
            if not answered:
                all_done = False
                continue
            if page is not None and self._page_passes_filters(page, book_ids, book_ids_operator, date_filters):
                self.logger.info(f"Page {page_id} was missed by the listing but is still in BookStack; keeping it")
                continue
            all_done = await self._delete_page_record(record, page_id) and all_done
        return all_done

    def _page_passes_filters(
        self,
        page: dict,
        book_ids: set[int] | None,
        book_ids_operator: FilterOperatorType | None,
        date_filters: tuple[datetime | None, datetime | None, datetime | None, datetime | None],
    ) -> bool:
        if book_ids:
            in_books = page.get("book_id") in book_ids
            if book_ids_operator.value == "in" and not in_books:
                return False
            if book_ids_operator.value == "not_in" and in_books:
                return False
        return self._pass_date_filters(page, *date_filters)

    @staticmethod

    @staticmethod
    def _page_listing(response: BookStackResponse) -> dict | None:
        """The decoded body of a list_pages answer, or None when it is not a page listing."""
        content = response.data.get('content') if response.success and response.data else None
        try:
            listing = json.loads(content) if content else None
        except (ValueError, TypeError):
            return None
        return listing if _is_listing(listing) else None

    async def _fetch_page(self, page_id: int) -> tuple[bool, dict | None]:
        """Look a page up by id: (answered, page). A page BookStack no longer has is (True, None)."""
        listing = self._page_listing(await self.data_source.list_pages(filter={"id": str(page_id)}))
        if listing is None:
            self.logger.warning(f"Could not read BookStack's answer for page ID {page_id}")
            return False, None
        return True, (listing["data"][0] if listing["data"] else None)

    async def _remove_pages_no_longer_in_bookstack(self) -> bool:
        """List every page id within the sync filters, then remove the records of pages not listed.

        For a delete whose event no longer names the page; reads ids only, so
        it costs one request per 500 pages rather than a full sync.
        """
        book_ids, book_ids_operator = self._get_book_id_filter()
        date_filters = self._get_date_filters()
        api_filters = self._build_date_filter_params(*date_filters)
        listed: set[str] = set()
        offset = 0
        while True:
            listing = self._page_listing(await self.data_source.list_pages(
                count=_AUDIT_LOG_PAGE_SIZE, offset=offset, filter=api_filters,
            ))
            if listing is None:
                self.logger.warning("Could not list BookStack's pages, so purged pages are not removed this sync")
                return False
            pages = listing["data"]
            listed.update(
                f"page/{page['id']}" for page in pages
                if page.get("id") and self._page_passes_filters(page, book_ids, book_ids_operator, date_filters)
            )
            offset += len(pages)
            if offset >= listing["total"]:
                break
            if not pages:
                self.logger.warning(
                    "BookStack stopped listing pages before the total it reported, so purged pages are not removed this sync"
                )
                return False
        return await self._remove_pages_not_listed(listed, book_ids, book_ids_operator, date_filters)

    async def _delete_page_record(self, record: Record, page_id: int) -> bool:
        try:
            await self.data_entities_processor.on_record_deleted(record_id=record.id)
        except Exception as e:
            self.logger.warning(f"Failed to remove the record of page {page_id}: {e}")
            return False
        return True

    async def _handle_page_delete_event(self, page_id: int) -> bool:
        """Remove a page's record once BookStack confirms the page is gone.

        Deleted pages go to BookStack's recycle bin and can be restored under
        the same id, so the audit event alone is not proof: a page restored
        before this sync keeps its record. Returns False when the lookup or
        the delete failed, so the event is read again next sync.
        """
        answered, page = await self._fetch_page(page_id)
        if not answered:
            self.logger.warning(f"Could not confirm page {page_id} was deleted; keeping its record.")
            return False
        if page is not None:
            self.logger.info(f"Page {page_id} was restored after its delete; keeping its record.")
            return True

        try:
            record = await self.data_entities_processor.get_record_by_external_id(
                self.connector_id, f"page/{page_id}"
            )
        except Exception as e:
            self.logger.warning(f"Could not look up the record of deleted page {page_id}: {e}")
            return False
        return record is None or await self._delete_page_record(record, page_id)

    async def _handle_record_group_delete_event(self, event: dict, content_type: str) -> bool:
        """Remove a deleted book or chapter with everything below it, once BookStack confirms it is gone.

        BookStack sends no page_delete for the pages of a deleted chapter or book,
        so the group delete takes its pages and child chapters with it. Deleted
        items go to the recycle bin and can be restored under the same id, so the
        event alone is not proof. Returns False when the lookup or the delete
        failed, so the event is read again next sync.
        """
        item_id = _deleted_item_id(event)
        if item_id is None:
            # TODO(pr3880-fixes): a purge clears the id, and a full sync does not yet remove
            # groups BookStack no longer lists, so such a group stays until it is reconciled.
            self.logger.warning(f"A {content_type} delete event no longer names its {content_type}; it is not removed")
            return True
        exists = await self._content_exists(content_type, item_id)
        if exists is None:
            self.logger.warning(f"Could not confirm {content_type} {item_id} was deleted; keeping its record group.")
            return False
        if exists:
            self.logger.info(f"{content_type.capitalize()} {item_id} was restored after its delete; keeping its record group.")
            return True
        external_id = f"{content_type}/{item_id}"
        try:
            group = await self.data_entities_processor.get_record_group_by_external_id(self.connector_id, external_id)
        except Exception as e:
            self.logger.warning(f"Could not look up the record group of deleted {external_id}: {e}")
            return False
        if group is None:
            return True
        return await self.data_entities_processor.on_record_group_deleted(external_id, self.connector_id)

    async def _content_exists(self, content_type: str, item_id: int) -> bool | None:
        """Whether BookStack still lists a book or chapter by id; None when it gives no usable answer."""
        list_method = {
            "book": self.data_source.list_books,
            "chapter": self.data_source.list_chapters,
        }[content_type]
        response = await list_method(filter={"id": str(item_id)})
        if not response.success or not _is_listing(response.data):
            return None
        return bool(response.data["data"])

    def _get_date_filters(self) -> Tuple[Optional[datetime], Optional[datetime], Optional[datetime], Optional[datetime]]:
        """
        Extract date filter values from sync_filters.

        Returns:
            Tuple of (modified_after, modified_before, created_after, created_before)
        """
        modified_after: Optional[datetime] = None
        modified_before: Optional[datetime] = None
        created_after: Optional[datetime] = None
        created_before: Optional[datetime] = None

        # Get modified date filter
        modified_date_filter = self.sync_filters.get(SyncFilterKey.MODIFIED)
        if modified_date_filter and not modified_date_filter.is_empty():
            after_iso, before_iso = modified_date_filter.get_datetime_iso()
            if after_iso:
                modified_after = datetime.fromisoformat(after_iso).replace(tzinfo=timezone.utc)
                self.logger.info(f"Applying modified date filter: after {modified_after}")
            if before_iso:
                modified_before = datetime.fromisoformat(before_iso).replace(tzinfo=timezone.utc)
                self.logger.info(f"Applying modified date filter: before {modified_before}")

        # Get created date filter
        created_date_filter = self.sync_filters.get(SyncFilterKey.CREATED)
        if created_date_filter and not created_date_filter.is_empty():
            after_iso, before_iso = created_date_filter.get_datetime_iso()
            if after_iso:
                created_after = datetime.fromisoformat(after_iso).replace(tzinfo=timezone.utc)
                self.logger.info(f"Applying created date filter: after {created_after}")
            if before_iso:
                created_before = datetime.fromisoformat(before_iso).replace(tzinfo=timezone.utc)
                self.logger.info(f"Applying created date filter: before {created_before}")

        return modified_after, modified_before, created_after, created_before

    def _build_date_filter_params(
        self,
        modified_after: Optional[datetime] = None,
        modified_before: Optional[datetime] = None,
        created_after: Optional[datetime] = None,
        created_before: Optional[datetime] = None,
        additional_filters: Optional[Dict[str, str]] = None
        ) -> Dict[str, str]:
        """
        Build filter parameters for BookStack API including date filters.

        Args:
            modified_after: Only include pages modified after this date
            modified_before: Only include pages modified before this date
            created_after: Only include pages created after this date
            created_before: Only include pages created before this date
            additional_filters: Any additional filters to include

        Returns:
            Dictionary of filter parameters for the API
        """
        filters: Dict[str, str] = {}

        if additional_filters:
            filters.update(additional_filters)

        # Add modified date filters (using updated_at field)
        if modified_after:
            filters['updated_at:gte'] = modified_after.strftime('%Y-%m-%dT%H:%M:%SZ')
        if modified_before:
            filters['updated_at:lte'] = modified_before.strftime('%Y-%m-%dT%H:%M:%SZ')

        # Add created date filters
        if created_after:
            filters['created_at:gte'] = created_after.strftime('%Y-%m-%dT%H:%M:%SZ')
        if created_before:
            filters['created_at:lte'] = created_before.strftime('%Y-%m-%dT%H:%M:%SZ')

        return filters if filters else None

    def _pass_date_filters(
        self,
        page: Dict,
        modified_after: Optional[datetime] = None,
        modified_before: Optional[datetime] = None,
        created_after: Optional[datetime] = None,
        created_before: Optional[datetime] = None
    ) -> bool:
        """
        Check if a page passes the date filters.

        Args:
            page: The page data dictionary from BookStack API
            modified_after: Only include pages modified after this date
            modified_before: Only include pages modified before this date
            created_after: Only include pages created after this date
            created_before: Only include pages created before this date

        Returns:
            True if the page passes all filters, False if it should be skipped
        """
        # No filters applied - pass everything
        if not any([modified_after, modified_before, created_after, created_before]):
            return True

        # Parse page dates
        page_created_at = None
        page_updated_at = None

        created_at_str = page.get('created_at')
        if created_at_str:
            page_created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))

        updated_at_str = page.get('updated_at')
        if updated_at_str:
            page_updated_at = datetime.fromisoformat(updated_at_str.replace('Z', '+00:00'))

        # Apply created date filters
        if page_created_at:
            if created_after and page_created_at < created_after:
                self.logger.debug(f"Skipping page '{page.get('name')}': created {page_created_at} before filter {created_after}")
                return False
            if created_before and page_created_at > created_before:
                self.logger.debug(f"Skipping page '{page.get('name')}': created {page_created_at} after filter {created_before}")
                return False

        # Apply modified date filters
        if page_updated_at:
            if modified_after and page_updated_at < modified_after:
                self.logger.debug(f"Skipping page '{page.get('name')}': modified {page_updated_at} before filter {modified_after}")
                return False
            if modified_before and page_updated_at > modified_before:
                self.logger.debug(f"Skipping page '{page.get('name')}': modified {page_updated_at} after filter {modified_before}")
                return False

        return True

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

    async def cleanup(self) -> None:
        """
        Cleanup resources used by the connector.
        """
        self.logger.info("Cleaning up BookStack connector resources.")
        self.data_source = None

    async def reindex_records(self, records: List[Record]) -> None:
        """Re-queue records for indexing: a page changed at the source is brought up to date, the rest re-queued."""
        if not records:
            self.logger.info("No records to reindex")
            return
        if not self.data_source:
            raise Exception("BookStack client not initialized. Call init() first.")
        self.logger.info(f"Starting reindex for {len(records)} BookStack records")
        self._health = BookStackSyncHealth()
        self._rows_cache = {}
        users = await self.get_all_users()
        self._user_emails = {int(u.source_user_id): u.email for u in users if u.source_user_id and u.email}
        roles_ready = await self._load_roles_for_access()
        changed: List[Tuple[FileRecord, List[Permission]]] = []
        unchanged: List[Record] = []
        for record in records:
            external_id = record.external_record_id or ""
            if not external_id.startswith("page/"):
                self.logger.warning(f"Invalid external_record_id format for record {record.id}: {external_id}")
                continue
            answered, page = await self._fetch_page(int(external_id.split("/", 1)[1]))
            if roles_ready and answered and page is not None \
                    and str(page.get("revision_count")) != (record.external_revision_id or ""):
                prepared = await self._page_record(page)
                if prepared is not None:
                    changed.append(prepared)
                    continue
            unchanged.append(record)
        if changed:
            await self.data_entities_processor.on_new_records(changed)
        if unchanged:
            await self.data_entities_processor.reindex_existing_records(unchanged)
        self.logger.info(f"Reindex: {len(changed)} updated from BookStack, {len(unchanged)} re-queued")

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> FilterOptionsResponse:
        """BookStack connector does not support dynamic filter options."""

        if filter_key == "book_ids":
            return await self._get_book_options(page, limit, search, cursor)
        else:
            raise ValueError(f"Unsupported filter key: {filter_key}")

    async def _get_book_options(
        self,
        page: int,
        limit: int,
        search: Optional[str],
        cursor: Optional[str]
    ) -> FilterOptionsResponse:
        """
        Get dynamic filter options for BookStack books with page-based pagination.
        """

        search = search.strip() if search else ""

        # Use search API - empty string lists all books
        response = await self.data_source.search_all(
            query=search,
            type="book",
            page=page,
            count=limit
        )

        if not response.success:
            raise RuntimeError(f"Failed to fetch books: {response.error}")

        books_list = response.data.get("data", [])
        total = response.data.get("total", 0)

        # Convert to FilterOption objects
        options = [
            FilterOption(
                id=str(book.get("id")),
                label=book.get("name")+" ("+book.get("slug")+")"
            )
            for book in books_list
        ]

        # Calculate if there are more pages
        has_more = (page * limit) < total

        return FilterOptionsResponse(
            success=True,
            options=options,
            page=page,
            limit=limit,
            has_more=has_more,
            cursor=None
        )

    def _get_book_id_filter(self) -> Tuple[Optional[Set[int]], Optional[Any]]:
        book_ids_filter = self.sync_filters.get(SyncFilterKey.BOOK_IDS)
        if not book_ids_filter or book_ids_filter.is_empty():
            return None, None

        book_ids = set(int(bid) for bid in book_ids_filter.get_value())
        book_ids_operator = book_ids_filter.get_operator()

        if book_ids:
            action = "Excluding" if book_ids_operator.value == "not_in" else "Including"
            self.logger.info(f"🔍 Filter: {action} books by IDs: {book_ids}")

        return book_ids, book_ids_operator

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
        """
        Factory method to create a BookStack connector instance.

        Args:
            logger: Logger instance
            data_store_provider: Data store provider for database operations
            config_service: Configuration service for accessing credentials

        Returns:
            Initialized BookStackConnector instance
        """
        return BookStackConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
