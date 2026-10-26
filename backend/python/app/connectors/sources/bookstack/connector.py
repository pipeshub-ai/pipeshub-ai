import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
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
from app.connectors.sources.bookstack.access import bookstack_grants
from app.connectors.sources.bookstack.common.apps import BookStackApp
from app.models.entities import (
    AppRole,
    AppUser,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType
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


def _deleted_item_id(event: dict) -> int | None:
    """The item id of a page, chapter, book or shelf delete audit event, when it still has one.

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
    Syncs books, chapters, pages, attachments, users, roles, and permissions.
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
            try:
                client = await BookStackClient.build_and_validate(token_config)
            except ValueError as e:
                self.logger.error(f"Failed to initialize BookStack client: {e}", exc_info=True)
                return False
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

    async def run_sync(self) -> None:
        """
        Runs a full synchronization from the BookStack instance.
        Syncs users, groups, record groups (books/shelves), and records (pages/chapters).
        """
        try:
            self.logger.info("Starting BookStack full sync.")
            self._bookstack_role_access = {}
            self._kept_unnoted_access = False

            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "bookstack", self.connector_id, self.logger
            )

            self.logger.info(f"sync filters: {self.sync_filters}")
            self.logger.info(f"Indexing filters: {self.indexing_filters}")

            # Step 1: Sync all users
            self.logger.info("Syncing users...")
            await self._sync_users()

            # Step 2: Sync all user groups (roles in BookStack)
            self.logger.info("Syncing user groups (roles)...")
            await self._sync_user_roles()

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

    #-------------------------------Users Sync-----------------------------------#
    async def _sync_users(self, full_sync: bool = False) -> None:

        current_timestamp = self._get_iso_time()
        bookstack_user_sync_key = generate_record_sync_point_key("bookstack", "user_logs", "global")
        bookstack_user_sync_point = await self.user_sync_point.read_sync_point(bookstack_user_sync_key)

        self._users_stored = False
        users = await self.get_all_users()
        if self._user_list_read is ReadOutcome.INTERRUPTED:
            # The users read are still stored, but the sync point stays, so the next
            # sync reads the whole list again; roles wait for it (_sync_user_roles_full).
            if users:
                await self._sync_users_full(users)
            self.logger.warning("⚠️ BookStack users were not all listed; the next sync reads them again")
            return

        #if no sync point, initialize cursor and run _sync_users else run _sync_users_incremental
        if full_sync or not bookstack_user_sync_point.get('timestamp'):
            await self._sync_users_full(users)
            self._users_stored = True
            await self.user_sync_point.update_sync_point(
                bookstack_user_sync_key,
                {"timestamp": current_timestamp}
            )
        else:
            last_sync_timestamp = bookstack_user_sync_point.get('timestamp')
            if not await self._sync_users_incremental(users, last_sync_timestamp):
                self.logger.warning("User changes were not all applied; the next sync reads them again")
                return
            self._users_stored = True
            await self.user_sync_point.update_sync_point(
                bookstack_user_sync_key,
                {"timestamp": current_timestamp}
            )

    async def _sync_users_full(self, app_users: List[AppUser]) -> None:
        """
        Fetches all users from BookStack, transforms them into AppUser objects,
        and upserts them into the database.
        """
        self.logger.info("Starting BookStack user sync...")

        # Pass the AppUser objects to the data processor to save in ArangoDB
        await self.data_entities_processor.on_new_app_users(app_users)
        self.logger.info("✅ Finished syncing BookStack users.")

    async def _sync_users_incremental(self, app_users: List[AppUser], last_sync_timestamp: str) -> bool:
        """
        Syncs only the users that have been newly created since the last sync timestamp
        by checking the audit log.

        Returns False when a user's roles could not be read in full, so the
        next sync reads the same events again.
        """
        self.logger.info(f"Starting incremental user sync from timestamp: {last_sync_timestamp}")

        # 1. Fetch 'user_create' events from the audit log
        user_create_response = await self.data_source.list_audit_log(
            filter={'type': 'user_create', 'created_at:gte': last_sync_timestamp}
        )
        user_update_response = await self.data_source.list_audit_log(
            filter={'type': 'user_update', 'created_at:gte': last_sync_timestamp}
        )
        user_delete_response = await self.data_source.list_audit_log(
            filter={'type': 'user_delete', 'created_at:gte': last_sync_timestamp}
        )

        done = True
        if user_create_response.success and user_create_response.data.get('data'):
            done = await self._handle_user_upsert_event(user_create_response.data.get('data'), app_users) and done
        if user_update_response.success and user_update_response.data.get('data'):
            done = await self._handle_user_upsert_event(user_update_response.data.get('data'), app_users) and done
        if user_delete_response.success and user_delete_response.data.get('data'):
            # await self._handle_user_delete_event(user_delete_response.data.get('data'), app_users)
            self.logger.info("User delete event handling not implemented yet !")
        return done

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


    async def _handle_user_delete_event(self, user_delete_events: List[Dict], app_users: List[AppUser]) -> None:
        self.logger.info("Deleted users found !")

        for event in user_delete_events:
            user_id, name = self._parse_id_and_name_from_event(event)

            if user_id is None:
                self.logger.warning(f"Could not parse user ID from delete event: {event}")
                continue

            self.logger.info(f"Processing deletion for user: {name} (ID: {user_id})")

            try:
                # Look up the user by their external ID (source_user_id)
                user = await self.data_entities_processor.get_user_by_user_id(str(user_id))

                if not user:
                    self.logger.warning(
                        f"User with BookStack ID {user_id} ({name}) not found in database. "
                        "May have been already deleted or never synced."
                    )
                    continue

                user_email = user.email

                # If user is found, call the data processor to handle removal
                if user_email:
                    self.logger.info(
                        f"Deleting user {name} (BookStack ID: {user_id}, email: {user_email})"
                    )

                    # Call data_entities_processor to remove the user
                    success = await self.data_entities_processor.on_user_removed(
                        user_email=user_email,
                        connector_id=self.connector_id
                    )

                    if success:
                        self.logger.info(
                            f"✅ Successfully deleted user {name} (email: {user_email})"
                        )
                    else:
                        self.logger.error(
                            f"Failed to delete user {name} (email: {user_email}) from database"
                        )
                else:
                    self.logger.warning(
                        f"User {name} (ID: {user_id}) found but has no email address"
                    )

            except Exception as e:
                self.logger.error(
                    f"Error processing user deletion for user ID {user_id}: {e}",
                    exc_info=True
                )

        self.logger.info("✅ Finished processing user delete events")

    #-------------------------------User Groups Sync-----------------------------------#
    async def _sync_user_roles(self, full_sync:bool = False) -> None:
        current_timestamp = self._get_iso_time()
        bookstack_user_role_sync_key = generate_record_sync_point_key("bookstack", "user_role_logs", "global")
        bookstack_user_role_sync_point = await self.app_role_sync_point.read_sync_point(bookstack_user_role_sync_key)

        self._refused_roles_kept = False
        self._roles_rebuilt = False

        #if no sync point, initialize cursor and run _sync_users else run _sync_users_incremental
        if full_sync or not bookstack_user_role_sync_point.get('timestamp'):
            done = await self._sync_user_roles_full()
        else:
            last_sync_timestamp = bookstack_user_role_sync_point.get('timestamp')
            done = await self._sync_user_roles_incremental(last_sync_timestamp)
            if done and bookstack_user_role_sync_point.get(ROLE_REBUILD_OWED) and not self._roles_rebuilt:
                # A role kept through a refusal is read again on every sync until it can
                # be read, so the members it lost in BookStack meanwhile drop out then.
                done = await self._sync_user_roles_full()
        if not done:
            # Moving the timestamp on would leave the unapplied changes behind it.
            self.logger.warning("Role changes were not all applied; the next sync reads them again")
            self._kept_unnoted_access = True
            return
        # A refusal repeats every sync, so the timestamp moves on (holding it would
        # replay the audit log every sync and lose the role deletes made meanwhile),
        # and the flag has the next sync read every role again. Always written: the
        # stores merge a sync point, so a flag left out would stay set.
        await self.app_role_sync_point.update_sync_point(
            bookstack_user_role_sync_key,
            {"timestamp": current_timestamp, ROLE_REBUILD_OWED: self._refused_roles_kept},
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
        """A full sync that left roles or record groups as stored without noting them
        as seen did not read everything, so nothing is unlinked after it."""
        return FullSyncCoverage(complete=not self._kept_unnoted_access)

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

    def _build_role_permissions_map(
        self,
        all_roles_with_details: List[Dict],
        user_email_map: Dict[int, str]
    ) -> Dict[int, List[Permission]]:
        """
        Creates a map of role_id -> [List of Permissions] from detailed role objects.

        Args:
            all_roles_with_details: A list of role dictionaries, each containing a 'users' key.
            user_email_map: A lookup dictionary mapping user IDs to their email addresses.

        Returns:
            A dictionary where each key is a role_id and the value is a list of
            Permission objects for users belonging to that role.
        """
        self.logger.info("Building map of roles to user permissions...")
        role_to_permissions_map = {}
        for role in all_roles_with_details:
            role_id = role.get("id")
            if not role_id:
                continue

            permissions = []
            for user in role.get("users", []):
                user_id = user.get("id")
                user_email = user_email_map.get(user_id)

                if not user_id or not user_email:
                    self.logger.warning(f"Skipping user in role '{role.get('display_name')}' due to missing ID or email.")
                    continue

                # Create a permission object linking this user to this role.
                permission = Permission(
                    external_id=str(user_id),
                    email=user_email,
                    type=PermissionType.WRITE, # Defaulting to WRITE as membership implies access
                    entity_type=EntityType.GROUP
                )
                permissions.append(permission)

            if permissions:
                role_to_permissions_map[role_id] = permissions

        self.logger.info(f"Permission map built for {len(role_to_permissions_map)} roles.")
        return role_to_permissions_map

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

    async def _sync_user_roles_incremental(self, last_sync_timestamp: str) -> bool:
        """
        Sync user groups and permissions incrementally based on the last sync timestamp.

        Returns False when a role could not be read in full, so the next sync
        reads the same events again.
        """
        self.logger.info("Starting BookStack user group sync incremental...")

        roles_create_events = await self.data_source.list_audit_log(
            filter={
                'type': 'role_create',
                'created_at:gte': last_sync_timestamp
            }
        )
        role_update_events = await self.data_source.list_audit_log(
            filter={
                'type': 'role_update',
                'created_at:gte': last_sync_timestamp
            }
        )
        role_delete_events = await self.data_source.list_audit_log(
            filter={
                'type': 'role_delete',
                'created_at:gte': last_sync_timestamp
            }
        )

        all_users = await self._fetch_all_users_with_details()
        user_email_map = {user.get("id"): user.get("email") for user in all_users}

        done = True
        if roles_create_events.success and roles_create_events.data and roles_create_events.data.get('data'):
            for event in roles_create_events.data['data']:
                role_id, _ = self._parse_id_and_name_from_event(event)
                done = await self._handle_role_create_event(role_id, user_email_map) and done

        if role_update_events.success and role_update_events.data and role_update_events.data.get('data'):
            for event in role_update_events.data['data']:
                role_id, _ = self._parse_id_and_name_from_event(event)
                done = await self._handle_role_update_event(role_id, user_email_map) and done

        if role_delete_events.success and role_delete_events.data and role_delete_events.data.get('data'):
            for event in role_delete_events.data['data']:
                role_id, _ = self._parse_id_and_name_from_event(event)
                await self._handle_role_delete_event(role_id)
            # One rebuild covers every delete: each one reads every role and user.
            done = await self._sync_user_roles_full() and done
        return done

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

    async def _handle_role_create_event(self, role_id: int, user_email_map: Dict[int, str]) -> bool:
        """
        Handles a 'role_create' audit log event by fetching the new role's
        details and its members, then sending it to the data processor.

        Returns False when the role or its members could not be read in full:
        writing it would replace its stored members with part of them.
        """

        if role_id is None:
            # This can happen if the parser function fails
            return True

        self.logger.info(f"Role created/updated (ID: {role_id}). Fetching details...")
        outcome, role = await self._read_role_with_members(role_id)
        if outcome is ReadOutcome.INTERRUPTED:
            self.logger.warning(f"Could not read role ID {role_id} and its members in full; it stays as stored this sync")
            return False
        if role is None:
            return True

        # Process the new group and its members
        app_role, app_users = role
        self.logger.info(f"Processing newly created role '{app_role.name}' with {len(app_users)} members...")
        await self.data_entities_processor.on_new_app_roles([role])
        return True

    async def _handle_role_update_event(self, role_id: int, user_email_map: Dict[int, str]) -> bool:
        if role_id is None:
            return True
        # Read before deleting: a role that can't be read now would otherwise be gone
        # until it next changes, and everyone's access through it with it.
        outcome, role = await self._read_role_with_members(role_id)
        if outcome is ReadOutcome.INTERRUPTED:
            self.logger.warning(f"Could not read role ID {role_id} and its members in full; it stays as stored this sync")
            return False
        if outcome is ReadOutcome.REFUSED and role is None:
            # As in the full role sync: a role BookStack refuses to show keeps its
            # stored members, and the next sync reads every role again.
            self.logger.error(f"❌ BookStack refused role ID {role_id}; it keeps its stored members until it can be read")
            if not await self._keep_refused_role(role_id):
                return False
        else:
            await self._handle_role_delete_event(role_id)
        if role is not None:
            await self.data_entities_processor.on_new_app_roles([role])
        done = await self._sync_record_groups(full_sync=True)
        await self._sync_records(full_sync=True)
        return done

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

    #-------------------------------Record Groups Sync-----------------------------------#
    async def _sync_record_groups(self, full_sync:bool = False) -> bool:
        """Sync shelves, books and chapters; False when they were left as stored this sync."""

        current_timestamp = self._get_iso_time()
        bookstack_record_group_sync_key = generate_record_sync_point_key("bookstack", "record_groups", "global")
        bookstack_record_group_sync_point = await self.record_sync_point.read_sync_point(bookstack_record_group_sync_key)

        roles_details = await self.list_roles_with_details()
        if self._roles_read is ReadOutcome.INTERRUPTED:
            # A book that follows the default role permissions is granted to every role
            # whose details were read, and writing it replaces its stored grants. The
            # sync point stays, so the next sync does this step again.
            self.logger.warning(
                "⚠️ BookStack roles were not read in full, so shelves, books and chapters keep their "
                "stored access this sync"
            )
            self._kept_unnoted_access = True
            return False

        #if no sync point, initialize cursor and run _sync_users else run _sync_users_incremental
        if full_sync or not bookstack_record_group_sync_point.get('timestamp'):
            await self._sync_record_groups_full(roles_details)
            await self.record_sync_point.update_sync_point(
                bookstack_record_group_sync_key,
                {"timestamp": current_timestamp}
            )
        else:
            last_sync_timestamp = bookstack_record_group_sync_point.get('timestamp')
            if not await self._sync_record_groups_incremental(last_sync_timestamp, roles_details):
                # Moving on would leave an unconfirmed or failed delete behind the cursor for good.
                self.logger.warning("Book/chapter deletes were not all applied; the next sync starts from the same point")
                return False
            await self.record_sync_point.update_sync_point(
                bookstack_record_group_sync_key,
                {"timestamp": current_timestamp}
            )
        return True

    async def _sync_record_groups_full(self, roles_details: Dict[int, Dict]) -> None:
        self.logger.info("Starting sync for shelves, books, and chapters as record groups.")

        # Sync shelves (no filtering)
        await self._sync_content_type_as_record_group(
            content_type_name="bookshelf",
            list_method=self.data_source.list_shelves,
            roles_details=roles_details
        )

        # Sync books with filtering - returns filtered book IDs
        filtered_book_ids = await self._sync_content_type_as_record_group(
            content_type_name="book",
            list_method=self.data_source.list_books,
            roles_details=roles_details
        )

        # Sync chapters - filtered by parent book IDs
        await self._sync_content_type_as_record_group(
            content_type_name="chapter",
            list_method=self.data_source.list_chapters,
            roles_details=roles_details,
            parent_filter_ids=filtered_book_ids
        )

        self.logger.info("✅ Finished syncing all record groups.")


    async def _sync_content_type_as_record_group(
        self,
        content_type_name: str,
        list_method: Callable[..., Awaitable[BookStackResponse]],
        roles_details: Dict[int, Dict],
        parent_filter_ids: Optional[Set[int]] = None
    ) -> Optional[Set[int]]:
        """
        Generic function to fetch a list of content items (like shelves, books),
        and sync them as record groups with permissions.

        Args:
            content_type_name: Type of content (bookshelf, book, chapter)
            list_method: API method to list items
            roles_details: Role details for permission parsing
            parent_filter_ids: For chapters, only sync those with book_id in this set

        Returns:
            Set of synced item IDs (useful for filtering child content types)
        """
        self.logger.info(f"Starting sync for {content_type_name}s as record groups.")

        # Get book ID filter if syncing books
        book_ids, book_ids_operator = self._get_book_id_filter()
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

            # Apply book ID filter for books
            if content_type_name == "book" and book_ids:
                if book_ids_operator.value == "in":
                    items_page = [b for b in items_page if b.get("id") in book_ids]
                elif book_ids_operator.value == "not_in":
                    items_page = [b for b in items_page if b.get("id") not in book_ids]

            # Apply parent filter for chapters
            if content_type_name == "chapter" and parent_filter_ids is not None:
                items_page = [ch for ch in items_page if ch.get("book_id") in parent_filter_ids]

            all_items.extend(items_page)
            offset += len(response.data.get("data", []))
            if offset >= response.data.get("total", 0):
                break

        if not all_items:
            self.logger.info(f"No {content_type_name}s found to sync.")
            return set() if content_type_name == "book" else None

        self.logger.info(f"Found {len(all_items)} {content_type_name}s. Fetching permissions...")

        # Collect IDs for return (used for filtering child content)
        synced_ids = {item.get("id") for item in all_items if item.get("id")}

        # Concurrently create RecordGroup and fetch permissions for each item
        tasks = []
        for item in all_items:
            parent_external_id = None
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

        # Return synced IDs for books (to filter chapters)
        return synced_ids if content_type_name == "book" else None

    async def _create_record_group_with_permissions(
        self, item: Dict, content_type_name: str, roles_details: Dict[int, Dict], parent_external_id: Optional[str] = None
    ) -> Optional[tuple[RecordGroup, list[Permission] | None]]:
        """Creates a RecordGroup and fetches its permissions for a single BookStack item."""
        try:
            item_id = item.get("id")
            item_name = item.get("name")
            if not all([item_id, item_name]):
                self.logger.warning(f"Skipping {content_type_name} due to missing id or name: {item}")
                return None

            # Map content type to RecordGroupType
            content_type_mapping = {
                "bookshelf": RecordGroupType.SHELF,
                "book": RecordGroupType.BOOK,
                "chapter": RecordGroupType.CHAPTER,
            }
            group_type = content_type_mapping.get(content_type_name, RecordGroupType.KB)

            # 1. Create the RecordGroup object
            record_group = RecordGroup(
                name=item_name,
                org_id=self.data_entities_processor.org_id,
                external_group_id=f"{content_type_name}/{item_id}",
                description=item.get("description", ""),
                connector_name=Connectors.BOOKSTACK,
                connector_id=self.connector_id,
                group_type=group_type,
                parent_external_group_id=parent_external_id,
                inherit_permissions=parent_external_id is not None,
            )

            # 2. Fetch its permissions
            permissions_response = await self.data_source.get_content_permissions(
                content_type=content_type_name, content_id=item_id
            )

            # None when the fetch fails: the processor then keeps the stored grants
            # instead of wiping them.
            permissions_list = None
            if permissions_response.success and permissions_response.data:
                parent_roles = None
                if parent_external_id:
                    book_id = item.get("book_id")
                    parent_roles = await self._parent_role_access(
                        parent_external_id, roles_details, book_id=book_id
                    )
                permissions_list = await self._parse_bookstack_permissions(
                    permissions_response.data, roles_details, content_type_name, parent_roles
                )
                if parent_external_id and hasattr(permissions_list, "inherit"):
                    record_group.inherit_permissions = permissions_list.inherit
                if hasattr(permissions_list, "effective"):
                    cache = getattr(self, "_bookstack_role_access", None) or {}
                    self._bookstack_role_access = cache
                    cache[f"{content_type_name}/{item_id}"] = permissions_list.effective
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

    async def _parse_bookstack_permissions(
        self,
        permissions_data: dict,
        roles_details: dict[int, dict],
        content_type_name: str,
        parent_roles: dict[int, PermissionType] | None = None,
    ) -> list[Permission]:
        """Owner where ownership grants access, plus role grants not already supplied by the parent."""
        owner_permission = None
        owner_role_ids: list[int] = []
        owner = permissions_data.get("owner")
        if owner and owner.get("id"):
            try:
                user_response = await self.data_source.get_user(owner.get("id"))
                if user_response.success and user_response.data.get("email"):
                    owner_permission = Permission(
                        external_id=str(owner.get("id")),
                        email=user_response.data.get("email"),
                        type=PermissionType.OWNER,
                        entity_type=EntityType.USER,
                    )
                    owner_role_ids = [
                        int(role["id"]) for role in user_response.data.get("roles") or [] if role.get("id") is not None
                    ]
            except Exception as e:
                self.logger.error(f"Failed to fetch owner details for user ID {owner.get('id')}: {e}")

        return bookstack_grants(
            owner_permission, permissions_data, roles_details, content_type_name, parent_roles, owner_role_ids
        )

    async def _parent_role_access(
        self, external_id: str, roles_details: dict[int, dict], *, book_id: str | int | None = None
    ) -> dict[int, PermissionType] | None:
        """Effective roles of a book or chapter, fetched once per sync when they are not already known."""
        cache: dict[str, dict[int, PermissionType]] = getattr(self, "_bookstack_role_access", None) or {}
        self._bookstack_role_access = cache
        if external_id in cache:
            return cache[external_id]
        content_type, _, raw_id = external_id.partition("/")
        if not raw_id:
            return None
        parent_roles = None
        if content_type == "chapter" and book_id:
            parent_roles = await self._parent_role_access(f"book/{book_id}", roles_details)
        response = await self.data_source.get_content_permissions(content_type=content_type, content_id=raw_id)
        if not response.success or not response.data:
            return None
        parsed = await self._parse_bookstack_permissions(response.data, roles_details, content_type, parent_roles)
        effective = getattr(parsed, "effective", None)
        if effective is None:
            return None
        cache[external_id] = effective
        return effective

    def _parse_bookstack_permissions_all_users(self, all_users: List[AppUser]) -> List[Permission]:
        """
        Creates a list of Permission objects granting READ access to every user
        in the provided list.
        """
        permissions_list = []
        for user in all_users:
            if user.email and user.source_user_id:
                permissions_list.append(
                    Permission(
                        external_id=user.source_user_id,
                        email=user.email,
                        type=PermissionType.READ,
                        entity_type=EntityType.USER
                    )
                )
            else:
                self.logger.warning(
                    f"Skipping permission for user {user.full_name} "
                    f"due to missing email or source_user_id."
                )

        return permissions_list

    async def _sync_record_groups_incremental(self, last_sync_timestamp: str, roles_details: dict[int, dict]) -> bool:
        """
        Sync all record groups (books, shelves and chapter) from BookStack as RecordGroup objects, handling new/updated/deleted record groups.

        Returns False when a delete could not be confirmed or applied.
        """
        self.logger.info("Starting incremental sync for record groups (books, shelves, and chapters).")

        # Get book ID filter
        book_ids, book_ids_operator = self._get_book_id_filter()

        shelves_done = await self._sync_record_groups_events(
            content_type="bookshelf",
            roles_details=roles_details,
            last_sync_timestamp=last_sync_timestamp
        )

        books_done = await self._sync_record_groups_events(
            content_type="book",
            roles_details=roles_details,
            last_sync_timestamp=last_sync_timestamp,
            book_ids=book_ids,
            book_ids_operator=book_ids_operator
        )

        chapters_done = await self._sync_record_groups_events(
            content_type="chapter",
            roles_details=roles_details,
            last_sync_timestamp=last_sync_timestamp,
            book_ids=book_ids,
            book_ids_operator=book_ids_operator
        )
        return shelves_done and books_done and chapters_done


    async def _sync_record_groups_events(
        self,
        content_type: str,
        roles_details: Dict[int, Dict],
        last_sync_timestamp: str,
        book_ids: Optional[Set[int]] = None,
        book_ids_operator: Optional[str] = None
    ) -> bool:
        """Apply one content type's audit events. Returns False when a delete was not confirmed or applied."""

        tasks = {
            "create": self.data_source.list_audit_log(filter={'type': f'{content_type}_create', 'created_at:gte': last_sync_timestamp}),
            "update": self.data_source.list_audit_log(filter={'type': f'{content_type}_update', 'created_at:gte': last_sync_timestamp}),
            "delete": self.data_source.list_audit_log(filter={'type': f'{content_type}_delete', 'created_at:gte': last_sync_timestamp}),
            "permissions_update": self.data_source.list_audit_log(filter={'type': 'permissions_update', 'created_at:gte': last_sync_timestamp}),
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        event_responses = dict(zip(tasks.keys(), results))

        # --- Handle Create Events ---
        create_response = event_responses.get("create")
        if create_response and create_response.success and create_response.data.get('data'):
            self.logger.info(f"Found {len(create_response.data['data'])} new {content_type}(s) to create.")
            for event in create_response.data['data']:
                await self._handle_record_group_create_event(event, content_type, roles_details, book_ids, book_ids_operator)

        # --- Handle Update Events ---
        update_response = event_responses.get("update")
        if update_response and update_response.success and update_response.data.get('data'):
            self.logger.info(f"Found {len(update_response.data['data'])} updated {content_type}(s) to update.")
            for event in update_response.data['data']:
                await self._handle_record_group_create_event(event, content_type, roles_details, book_ids, book_ids_operator)

        # --- Handle Delete Events ---
        deletes_done = True
        delete_response = event_responses.get("delete")
        if isinstance(delete_response, BaseException) or not delete_response.success or not _is_listing(delete_response.data):
            self.logger.warning(f"Could not read {content_type} deletes from the audit log; reading them again next sync")
            deletes_done = False
        elif delete_response.data['data']:
            self.logger.info(f"Found {len(delete_response.data['data'])} deleted {content_type}(s) to delete.")
            for event in delete_response.data['data']:
                deletes_done = await self._handle_record_group_delete_event(event, content_type) and deletes_done

        permissions_update_response = event_responses.get("permissions_update")
        if permissions_update_response and permissions_update_response.success and permissions_update_response.data.get('data'):
            self.logger.info(f"Found {len(permissions_update_response.data['data'])} permission updates for {content_type}(s).")
            for event in permissions_update_response.data['data']:
                if event.get("loggable_type") == content_type:
                    await self._handle_record_group_create_event(event, content_type, roles_details, book_ids, book_ids_operator)
        return deletes_done


    async def _handle_record_group_create_event(
        self,
        event: Dict,
        content_type: str,
        roles_details: Dict[int, Dict],
        book_ids: Optional[Set[int]] = None,
        book_ids_operator: Optional[str] = None
    ) -> None:
        """
        Handles a create event for any record group type (book, chapter, bookshelf).
        """
        item_id, item_name = self._parse_id_and_name_from_event(event)
        if item_id is None:
            return  # Parser already logged the error

        # Apply book filter for books
        if content_type == "book" and book_ids:
            if book_ids_operator.value == "in" and item_id not in book_ids:
                self.logger.debug(f"Skipping book ID {item_id} - not in filter list")
                return
            elif book_ids_operator.value == "not_in" and item_id in book_ids:
                self.logger.debug(f"Skipping book ID {item_id} - in exclusion list")
                return

        self.logger.info(f"{content_type} created/updated: '{item_name}' (ID: {item_id}). Fetching details...")

        # 1. Map content_type to the correct API fetch method
        fetch_method_map = {
            "book": self.data_source.get_book,
            "bookshelf": self.data_source.get_shelf,
            "chapter": self.data_source.get_chapter,
        }

        fetch_method = fetch_method_map.get(content_type)
        if not fetch_method:
            self.logger.error(f"Invalid content_type '{content_type}' in event handler.")
            return

        # 2. Fetch the full details of the newly created item
        response = await fetch_method(item_id)
        if not response.success or not response.data:
            self.logger.warning(f"Could not fetch details for new {content_type} ID {item_id}. Skipping.")
            return

        item_details = response.data

        # 3. Apply book filter for chapters (check parent book_id)
        if content_type == "chapter" and book_ids:
            chapter_book_id = item_details.get("book_id")
            if book_ids_operator.value == "in" and chapter_book_id not in book_ids:
                self.logger.debug(f"Skipping chapter ID {item_id} - parent book {chapter_book_id} not in filter list")
                return
            elif book_ids_operator.value == "not_in" and chapter_book_id in book_ids:
                self.logger.debug(f"Skipping chapter ID {item_id} - parent book {chapter_book_id} in exclusion list")
                return

        # 4. Determine parent ID if it's a chapter
        parent_external_id = None
        if content_type == "chapter" and item_details.get("book_id"):
            parent_external_id = f"book/{item_details.get('book_id')}"

        # 5. Reuse your existing function to build the RecordGroup and its permissions
        record_group_tuple = await self._create_record_group_with_permissions(
            item=item_details,
            content_type_name=content_type,
            roles_details=roles_details,
            parent_external_id=parent_external_id
        )

        # 6. Process the new record group
        if record_group_tuple:
            # The processor expects a list of tuples
            await self.data_entities_processor.on_new_record_groups([record_group_tuple])
            self.logger.info(f"✅ Successfully processed new {content_type}: '{item_name}'.")
            await self._refresh_child_access(content_type, item_details.get("id"), roles_details)

    async def _refresh_child_access(
        self, content_type: str, item_id: int | str | None, roles_details: dict[int, dict]
    ) -> None:
        """Recompute children that stored a snapshot of this book or chapter.

        A child with its own role rows does not inherit, so a parent change does
        not reach it until the child itself is synced.
        """
        if content_type not in ("book", "chapter") or item_id is None or not self.data_source:
            return
        try:
            if content_type == "book":
                chapters = self._page_listing(await self.data_source.list_chapters(
                    count=500, filter={"book_id": str(item_id)}
                ))
                for chapter in (chapters or {}).get("data") or []:
                    parent_external_id = f"book/{item_id}"
                    record_group_tuple = await self._create_record_group_with_permissions(
                        item=chapter,
                        content_type_name="chapter",
                        roles_details=roles_details,
                        parent_external_id=parent_external_id,
                    )
                    if record_group_tuple:
                        await self.data_entities_processor.on_new_record_groups([record_group_tuple])
                page_filter = {"book_id": str(item_id)}
            else:
                page_filter = {"chapter_id": str(item_id)}
            pages = self._page_listing(await self.data_source.list_pages(count=500, filter=page_filter))
            for page in (pages or {}).get("data") or []:
                await self._reapply_page_permissions(page, roles_details)
        except Exception as exc:
            self.logger.warning("Could not refresh children of %s %s: %s", content_type, item_id, exc)

    async def _reapply_page_permissions(self, page: dict, roles_details: dict[int, dict]) -> None:
        page_id = page.get("id")
        if page_id is None:
            return
        existing = await self.data_entities_processor.get_record_by_external_id(
            self.connector_id, f"page/{page_id}"
        )
        if existing is None or not self.data_source:
            return
        permissions_response = await self.data_source.get_content_permissions(
            content_type="page", content_id=page_id
        )
        if not (permissions_response.success and permissions_response.data):
            return
        parent_external_id = existing.external_record_group_id
        parent_roles = None
        if parent_external_id:
            if page.get("book_id"):
                await self._parent_role_access(f"book/{page.get('book_id')}", roles_details)
            parent_roles = await self._parent_role_access(
                parent_external_id, roles_details, book_id=page.get("book_id")
            )
        grants = await self._parse_bookstack_permissions(
            permissions_response.data, roles_details, "page", parent_roles
        )
        if hasattr(grants, "inherit"):
            existing.inherit_permissions = grants.inherit
        existing.rewrite_permissions = True
        await self.data_entities_processor.on_updated_record_permissions(existing, list(grants))

    async def _handle_record_group_delete_event(self, event: dict, content_type: str) -> bool:
        """Remove a deleted shelf, book or chapter with everything below it, once BookStack confirms it is gone.

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
        """Whether BookStack still lists a shelf, book or chapter by id; None when it gives no usable answer."""
        list_method = {
            "bookshelf": self.data_source.list_shelves,
            "book": self.data_source.list_books,
            "chapter": self.data_source.list_chapters,
        }[content_type]
        response = await list_method(filter={"id": str(item_id)})
        if not response.success or not _is_listing(response.data):
            return None
        return bool(response.data["data"])

    #---------------------------Records Sync-----------------------------------#
    async def _sync_records(self, full_sync:bool = False) -> None:

        current_timestamp = self._get_iso_time()
        bookstack_record_sync_key = generate_record_sync_point_key("bookstack", "records", "global")
        bookstack_record_sync_point = await self.record_sync_point.read_sync_point(bookstack_record_sync_key)

        roles_details = await self.list_roles_with_details()
        users = await self.get_all_users()

        #if no sync point, initialize cursor and run _sync_users else run _sync_users_incremental
        if full_sync or not bookstack_record_sync_point.get('timestamp'):
            done = await self._sync_records_full(roles_details, users)
        else:
            last_sync_timestamp = bookstack_record_sync_point.get('timestamp')
            done = await self._sync_records_incremental(last_sync_timestamp, roles_details, users)
        if not done:
            # Moving the timestamp on would leave the unread or unapplied
            # changes behind it for good; this way the next sync repeats them.
            self.logger.warning("Page changes were not all read or applied; the next sync starts from the same point")
            return
        await self.record_sync_point.update_sync_point(
            bookstack_record_sync_key,
            {"timestamp": current_timestamp}
        )

    async def _sync_records_full(self, roles_details: Dict[int, Dict], users: List[AppUser]) -> bool:
        """
        Sync all pages from BookStack as Record objects, handling new/updated/deleted records.

        Returns True only when the whole listing was read and every page it no
        longer returns was dealt with.
        """
        self.logger.info("Starting sync for pages as records.")

        # Get book ID filter
        book_ids, book_ids_operator = self._get_book_id_filter()

        # Get date filters
        modified_after, modified_before, created_after, created_before = self._get_date_filters()

        # Build API filter params
        api_filters = self._build_date_filter_params(
            modified_after=modified_after,
            modified_before=modified_before,
            created_after=created_after,
            created_before=created_before
        )

        batch_records: List[Tuple[FileRecord, List[Permission]]] = []
        offset = 0
        listed: set[str] = set()
        listing_complete = False

        while True:
            response = await self.data_source.list_pages(
                count=self.batch_size,
                offset=offset,
                filter=api_filters  # Pass date filters to API
            )

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

            # BookStack answers an error (429, 500) with a JSON body and no page
            # list, which must not read as the end of the listing.
            if not _is_listing(pages_data):
                self.logger.error(f"BookStack did not return a page listing: {str(pages_data)[:500]}")
                break

            pages_page = pages_data["data"]

            if not pages_page:
                # Complete only if the total says so: an empty page short of it is a cut-off listing.
                listing_complete = offset >= pages_data.get("total", 0)
                if not listing_complete:
                    self.logger.warning("BookStack stopped listing pages before the total it reported")
                break

            # Apply book ID filter
            if book_ids:
                if book_ids_operator.value == "in":
                    pages_page = [p for p in pages_page if p.get("book_id") in book_ids]
                elif book_ids_operator.value == "not_in":
                    pages_page = [p for p in pages_page if p.get("book_id") not in book_ids]

            self.logger.info(f"Processing {len(pages_page)} pages (offset {offset})...")

            # Process each page from the current API response
            for page in pages_page:
                if page.get("id"):
                    listed.add(f"page/{page['id']}")
                record_update = await self._process_bookstack_page(page, roles_details, users)

                if not record_update:
                    continue

                if record_update.record:
                    if not self.indexing_filters.is_enabled(IndexingFilterKey.FILES, default=True):
                        record_update.record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

                # Handle deleted records
                if record_update.is_deleted:
                    await self._handle_record_updates(record_update)
                    continue

                # Handle updated records
                if record_update.is_updated:
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

            offset += len(pages_data.get("data", []))  # Use original count for offset
            if offset >= pages_data.get("total", 0):
                listing_complete = True
                break

        # Process any remaining records in the final batch
        if batch_records:
            self.logger.info(f"Processing final batch of {len(batch_records)} new page records.")
            await self.data_entities_processor.on_new_records(batch_records)

        if not listing_complete:
            self.logger.warning(
                "The page listing stopped early, so no page records are removed this sync"
            )
            return False

        removed_all = await self._remove_pages_not_listed(
            listed, book_ids, book_ids_operator,
            (modified_after, modified_before, created_after, created_before),
        )
        self.logger.info("✅ Finished syncing all page records.")
        return removed_all

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

    async def _process_bookstack_page(self, page: Dict, roles_details: Dict[int, Dict], users: List[AppUser]) -> Optional[RecordUpdate]:
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
            existing_record = await self.data_entities_processor.get_record_by_external_id(
                self.connector_id, f"page/{page_id}"
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
            updated_at_timestamp_ms = self._parse_timestamp(page.get("updated_at"))
            created_at_timestamp_ms = self._parse_timestamp(page.get("created_at"))

            # 3. Create the FileRecord object
            file_record = FileRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                record_name=page.get("name"),
                external_record_id=f"page/{page_id}",
                connector_name=Connectors.BOOKSTACK,
                connector_id=self.connector_id,
                record_type=RecordType.FILE.value,
                external_record_group_id=parent_external_id,
                origin=OriginTypes.CONNECTOR.value,
                org_id=self.data_entities_processor.org_id,
                source_updated_at=updated_at_timestamp_ms,
                updated_at=updated_at_timestamp_ms,
                source_created_at=created_at_timestamp_ms,
                created_at=created_at_timestamp_ms,
                version=0 if is_new else existing_record.version + 1,
                external_revision_id=str(page.get("revision_count")),
                weburl=f"{self.bookstack_base_url.rstrip('/')}/books/{page.get('book_slug')}/page/{page.get('slug')}",
                mime_type=MimeTypes.MARKDOWN.value,
                extension="md",
                is_file=True,
                size_in_bytes=0,
                inherit_permissions=True,
            )

            # 4. Fetch and parse permissions
            new_permissions = []
            permissions_response = await self.data_source.get_content_permissions(
                content_type="page", content_id=page_id
            )
            if permissions_response.success and permissions_response.data:
                parent_roles = None
                if parent_external_id:
                    if page.get("book_id"):
                        await self._parent_role_access(f"book/{page.get('book_id')}", roles_details)
                    parent_roles = await self._parent_role_access(
                        parent_external_id, roles_details, book_id=page.get("book_id")
                    )
                new_permissions = await self._parse_bookstack_permissions(
                    permissions_response.data, roles_details, "page", parent_roles
                )
                if hasattr(new_permissions, "inherit"):
                    file_record.inherit_permissions = new_permissions.inherit
                if existing_record:
                    permissions_changed = True
                    is_updated = True
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
                # The update carries the source's id; records are deleted by their key.
                existing_record = await self.data_entities_processor.get_record_by_external_id(
                    self.connector_id, record_update.external_record_id
                )
                if existing_record:
                    await self.data_entities_processor.on_record_deleted(record_id=existing_record.id)
            elif record_update.is_new:
                self.logger.info(f"New record detected: {record_update.record.record_name}")
            elif record_update.is_updated:
                if record_update.metadata_changed:
                    self.logger.info(f"Metadata changed for record: {record_update.record.record_name}")
                    await self.data_entities_processor.on_record_metadata_update(record_update.record)
                if record_update.permissions_changed:
                    self.logger.info(f"Permissions changed for record: {record_update.record.record_name}")
                    await self.data_entities_processor.on_updated_record_permissions(
                        record_update.record,
                        record_update.new_permissions
                    )
                if record_update.content_changed:
                    self.logger.info(f"Content changed for record: {record_update.record.record_name}")
                    await self.data_entities_processor.on_record_content_update(record_update.record)
        except Exception as e:
            self.logger.error(f"Error handling record updates: {e}", exc_info=True)

    async def _sync_records_incremental(
        self,
        last_sync_timestamp: str,
        roles_details: Dict[int, Dict],
        users: List[AppUser]
    ) -> bool:
        """
        Syncs records (pages) incrementally by processing create, update, and
        delete events from the audit log since the last sync.

        Returns False when a page delete could not be read or applied.
        """
        self.logger.info(f"Starting incremental record (page) sync from: {last_sync_timestamp}")

        # Get book ID filter
        book_ids, book_ids_operator = self._get_book_id_filter()

        # Get date filters for client-side filtering
        modified_after, modified_before, created_after, created_before = self._get_date_filters()

        # Audit log filters - only use last_sync_timestamp
        # We'll apply page date filters client-side after fetching page details
        tasks = {
            "create": self.data_source.list_audit_log(filter={'type': 'page_create', 'created_at:gte': last_sync_timestamp}),
            "update": self.data_source.list_audit_log(filter={'type': 'page_update', 'created_at:gte': last_sync_timestamp}),
            "permissions_update": self.data_source.list_audit_log(filter={'type': 'permissions_update', 'created_at:gte': last_sync_timestamp}),
            "delete": self._list_page_delete_events(last_sync_timestamp),
            "move": self.data_source.list_audit_log(filter={'type': 'page_move', 'created_at:gte': last_sync_timestamp})
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        event_responses = dict(zip(tasks.keys(), results))

        # 2. Process Create Events
        create_response = event_responses.get("create")
        if create_response and create_response.success and create_response.data.get('data'):
            self.logger.info(f"Found {len(create_response.data['data'])} new page(s) to create.")
            for event in create_response.data['data']:
                await self._handle_page_upsert_event(
                    event, roles_details, users,
                    modified_after=modified_after,
                    modified_before=modified_before,
                    created_after=created_after,
                    created_before=created_before,
                    book_ids=book_ids,
                    book_ids_operator=book_ids_operator
                )

        # 3. Process Update Events
        update_response = event_responses.get("update")
        if update_response and update_response.success and update_response.data.get('data'):
            self.logger.info(f"Found {len(update_response.data['data'])} page(s) to update.")
            for event in update_response.data['data']:
                await self._handle_page_upsert_event(
                    event, roles_details, users,
                    modified_after=modified_after,
                    modified_before=modified_before,
                    created_after=created_after,
                    created_before=created_before,
                    book_ids=book_ids,
                    book_ids_operator=book_ids_operator
                )

        permissions_update_response = event_responses.get("permissions_update")
        if permissions_update_response and permissions_update_response.success and permissions_update_response.data.get('data'):
            self.logger.info(f"Found {len(permissions_update_response.data['data'])} page(s) with permission updates.")
            for event in permissions_update_response.data['data']:
                if event.get('loggable_type') == 'page':
                    await self._handle_page_upsert_event(
                        event, roles_details, users,
                        modified_after=modified_after,
                        modified_before=modified_before,
                        created_after=created_after,
                        created_before=created_before,
                        book_ids=book_ids,
                        book_ids_operator=book_ids_operator
                    )

        # 4. Process Delete Events (no date filter needed - if it was deleted, handle it)
        deletes_done = False
        delete_listing = event_responses.get("delete")
        if isinstance(delete_listing, BaseException):
            self.logger.error(f"Could not read page deletions from the audit log: {delete_listing}")
        else:
            delete_events, deletes_done = delete_listing
            if delete_events:
                self.logger.info(f"Found {len(delete_events)} page(s) to delete.")
            unnamed = False
            for event in delete_events:
                page_id = _deleted_item_id(event)
                if page_id is None:
                    unnamed = True
                    continue
                deletes_done = await self._handle_page_delete_event(page_id) and deletes_done
            if unnamed:
                deletes_done = await self._remove_pages_no_longer_in_bookstack() and deletes_done

        # 5. Process Move Events
        move_response = event_responses.get("move")
        if move_response and move_response.success and move_response.data.get('data'):
            self.logger.info(f"Found {len(move_response.data['data'])} page(s) to move.")
            self.logger.warning("!! method not implemented yet !!")

        self.logger.info("✅ Finished incremental record sync.")
        return deletes_done


    async def _list_page_delete_events(self, since: str) -> tuple[list[dict], bool]:
        """Every page_delete audit event since ``since``, reading past the API's page size.

        Returns the events read and whether the whole log was read: a page of
        the log that cannot be read, including an error body, ends the listing.
        """
        events: list[dict] = []
        offset = 0
        while True:
            response = await self.data_source.list_audit_log(
                count=_AUDIT_LOG_PAGE_SIZE,
                offset=offset,
                filter={'type': 'page_delete', 'created_at:gte': since},
            )
            if not response.success or not _is_listing(response.data):
                self.logger.warning(
                    f"Could not read page deletions from the audit log: {response.error or str(response.data)[:500]}"
                )
                return events, False
            batch = response.data['data']
            events.extend(batch)
            offset += len(batch)
            if offset >= response.data['total']:
                return events, True
            if not batch:
                self.logger.warning("The audit log stopped before the total it reported; page deletions are read again next sync")
                return events, False

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

    async def _handle_page_upsert_event(
        self,
        event: Dict,
        roles_details: Dict[int, Dict],
        users: List[AppUser],
        modified_after: Optional[datetime] = None,
        modified_before: Optional[datetime] = None,
        created_after: Optional[datetime] = None,
        created_before: Optional[datetime] = None,
        book_ids: Optional[Set[int]] = None,
        book_ids_operator: Optional[str] = None
    ) -> None:
        """
        Handles a 'page_create' or 'page_update' event by fetching the page's
        latest details and calling the correct data processor method.
        """
        page_id, page_name = self._parse_id_and_name_from_event(event)
        if page_id is None:
            return

        self.logger.info(f"Processing page upsert for: '{page_name}' (ID: {page_id})")

        # Fetch the full, most recent details of the page
        page_response = await self.data_source.list_pages(filter={"id": str(page_id)})

        if not page_response.success or not page_response.data:
            self.logger.warning(f"Could not fetch details for page ID {page_id}. Skipping.")
            return

        json_content_str = page_response.data.get('content')
        if not json_content_str:
            self.logger.warning(f"API response for page ID {page_id} is empty. Skipping.")
            return

        try:
            pages_data = json.loads(json_content_str)
            pages_list = pages_data.get("data", [])
            if not pages_list:
                self.logger.warning(f"No page data found in the response for ID {page_id}. Skipping.")
                return
            page_details = pages_list[0]

        except json.JSONDecodeError:
            self.logger.error(f"Failed to decode JSON for page ID {page_id}. Content: {json_content_str}")
            return

        # Apply book ID filter
        if book_ids:
            page_book_id = page_details.get("book_id")
            if book_ids_operator.value == "in" and page_book_id not in book_ids:
                self.logger.debug(f"Skipping page '{page_name}' (ID: {page_id}) - book {page_book_id} not in filter list")
                return
            elif book_ids_operator.value == "not_in" and page_book_id in book_ids:
                self.logger.debug(f"Skipping page '{page_name}' (ID: {page_id}) - book {page_book_id} in exclusion list")
                return

        # Apply date filters - skip if page doesn't pass
        if not self._pass_date_filters(
            page_details,
            modified_after=modified_after,
            modified_before=modified_before,
            created_after=created_after,
            created_before=created_before
        ):
            self.logger.info(f"Skipping page '{page_name}' (ID: {page_id}) due to date filters.")
            return

        # Process the page to determine if it's new or updated
        record_update = await self._process_bookstack_page(page_details, roles_details, users)

        if not record_update:
            return

        if record_update.record:
            if not self.indexing_filters.is_enabled(IndexingFilterKey.FILES, default=True):
                record_update.record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

        if record_update.is_new:
            self.logger.info(f"New record detected from event: {record_update.record.record_name}")
            new_record_batch = [(record_update.record, record_update.new_permissions or [])]
            await self.data_entities_processor.on_new_records(new_record_batch)

        elif record_update.is_updated:
            await self._handle_record_updates(record_update)

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
        """
        Reindex records from BookStack.

        This method checks each record at the source for updates:
        - If the record has changed (metadata, content, or permissions), it updates the DB
        - If the record hasn't changed, it publishes a reindex event for the existing record
        """
        try:
            if not records:
                self.logger.info("No records to reindex")
                return

            self.logger.info(f"Starting reindex for {len(records)} BookStack records")

            # Ensure BookStack client is initialized
            if not self.data_source:
                self.logger.error("BookStack client not initialized. Call init() first.")
                raise Exception("BookStack client not initialized. Call init() first.")

            # Fetch roles and users needed for processing pages
            roles_details = await self.list_roles_with_details()
            users = await self.get_all_users()

            # Check records at source for updates
            org_id = self.data_entities_processor.org_id
            updated_records = []
            non_updated_records = []

            for record in records:
                try:
                    updated_record_data = await self._check_and_fetch_updated_record(
                        org_id, record, roles_details, users
                    )
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

            # Publish reindex events for non-updated records
            if non_updated_records:
                await self.data_entities_processor.reindex_existing_records(non_updated_records)
                self.logger.info(f"Published reindex events for {len(non_updated_records)} non-updated records")

        except Exception as e:
            self.logger.error(f"Error during BookStack reindex: {e}", exc_info=True)
            raise

    async def _check_and_fetch_updated_record(
        self, org_id: str, record: Record, roles_details: Dict[int, Dict], users: List[AppUser]
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """
        Fetch record from BookStack and return data for reindexing if changed.

        Args:
            org_id: The organization ID
            record: The record to check for updates
            roles_details: Dictionary of role details for permission parsing
            users: List of all users for permission parsing

        Returns:
            Tuple of (updated_record, permissions) if the record has changed, None otherwise
        """
        try:
            external_id = record.external_record_id

            if not external_id:
                self.logger.warning(f"Missing external_record_id for record {record.id}")
                return None

            # Parse the page ID from external_record_id (format: "page/{page_id}")
            if not external_id.startswith("page/"):
                self.logger.warning(f"Invalid external_record_id format for record {record.id}: {external_id}")
                return None

            page_id = external_id.split("/")[1]

            # Fetch fresh page data from BookStack
            page_response = await self.data_source.list_pages(filter={"id": str(page_id)})

            if not page_response.success or not page_response.data:
                self.logger.warning(f"Could not fetch page data for record {record.id}: {page_response.error if page_response else 'No response'}")
                return None

            json_content_str = page_response.data.get('content')
            if not json_content_str:
                self.logger.warning(f"API response for page ID {page_id} is empty. Skipping.")
                return None

            try:
                # Parse the JSON string into a Python dictionary
                pages_data = json.loads(json_content_str)
                # Extract the list of pages from the 'data' key
                pages_list = pages_data.get("data", [])
                # Check if we actually got a page back
                if not pages_list:
                    self.logger.warning(f"No page data found in the response for ID {page_id}. Skipping.")
                    return None
                # Get the first (and only) page object from the list
                page_details = pages_list[0]

            except json.JSONDecodeError:
                self.logger.error(f"Failed to decode JSON for page ID {page_id}. Content: {json_content_str}")
                return None

            # Process the page using existing logic
            record_update = await self._process_bookstack_page(page_details, roles_details, users)

            if not record_update or record_update.is_deleted:
                return None

            # Only return data if there's an actual update (metadata, content, or permissions)
            if record_update.is_updated:
                self.logger.info(f"Record {external_id} has changed at source. Updating.")
                # Ensure we keep the internal DB ID
                record_update.record.id = record.id
                return (record_update.record, record_update.new_permissions or [])

            return None

        except Exception as e:
            self.logger.error(f"Error checking BookStack record {record.id} at source: {e}", exc_info=True)
            return None

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
