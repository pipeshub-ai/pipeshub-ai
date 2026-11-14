"""Drupal Wiki connector.

Sync model (see the design page for the full rationale):

* spaces become record groups and carry every permission; pages and attachments
  inherit from their home space, because Drupal Wiki has no page-level rights
* every space the user selects is synced whatever its type; open spaces
  (``ANONYMOUS``/``AUTHENTICATED``) map to one org-wide read, while private spaces
  need their member list, which only the internal GraphQL API exposes
* each run lists every selected space in full (a share or a deletion need not move
  ``lastModified``) and writes the pages changed since the space's checkpoint, the
  wiki's own ``lastModified``, or not stored there yet; a page the run could not build
  holds the checkpoint back rather than being skipped
* each space carries its own checkpoint, so a space that fails or is dropped by the
  permission phase is written in full once it is reachable again
* a page shared into other spaces is readable by the readers of each (handbook node
  461), its sub-pages are not: pages are ``STRICT``, so only a page's own grants open
  it outside its space, and a shared page carries as its own grants the readers of
  its home space and of every space it is shared into
* a stored page its space no longer lists is looked up on its own and removed, with
  its attachments, when the wiki no longer has it in that space
* a person the directory does not show enabled (disabled, removed, or now under
  another address) loses the connector; the grants rewritten each run leave them out
* a token the wiki rejects fails the run with an auth notification
* a wiki that refuses the GraphQL queries (Drupal Wiki 8.44 refuses them for tokens)
  keeps each private space synced under the access stored on an earlier run; a
  private space with nothing stored is skipped, and the admin is told on the
  connector card and after every run
"""

import asyncio
import hashlib
import html
import json
import mimetypes
from collections.abc import AsyncGenerator, Awaitable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from logging import Logger
from typing import Any, TypeVar

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    AccessRule,
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.connector_service import (
    ActionItem,
    BaseConnector,
    ConnectorInitError,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.error.stream_errors import (
    connector_not_ready,
    map_source_status,
    not_found_at_source,
    to_stream_error,
)
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
    generate_record_sync_point_key,
)
from app.connectors.core.constants import CONNECTOR_EMAIL_IDENTITY_INFO, IconPaths
from app.connectors.core.registry.auth_builder import AuthBuilder, AuthType
from app.connectors.core.registry.connector_builder import (
    AuthField,
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
    SyncStrategy,
)
from app.connectors.core.registry.filters import (
    FilterCategory,
    FilterCollection,
    FilterField,
    FilterOption,
    FilterOptionsResponse,
    FilterType,
    IndexingFilterKey,
    OptionSourceType,
    SyncFilterKey,
    extension_passes_filter,
    load_connector_filters,
)
from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage
from app.connectors.sources.drupal_wiki.common.apps import DrupalWikiApp
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.services.notification.types import NotificationSeverity, NotificationType
from app.sources.client.drupal_wiki.drupal_wiki import (
    DrupalWikiClient,
    DrupalWikiRESTClientViaToken,
    check_wiki_address,
    describe_exception,
    describe_response,
)
from app.sources.client.drupal_wiki.graphql import DrupalWikiGraphQLClient
from app.sources.client.http.http_response import HTTPResponse
from app.sources.external.drupal_wiki.drupal_wiki import DrupalWikiDataSource
from app.sources.external.drupal_wiki.graphql import (
    DrupalWikiGraphQLDataSource,
    DrupalWikiGraphQLError,
    DrupalWikiGraphQLRefusedError,
)
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import get_epoch_timestamp_in_ms

T = TypeVar("T")

# ============================================================================
# Vocabulary and tuning
#
# Values come from the vendor's OpenAPI spec and handbook; anything the spec
# leaves open is noted where it is used.
# ============================================================================

# ---------------------------------------------------------------------------
# Record external ids
# ---------------------------------------------------------------------------
# Records are looked up by (connector, external id) alone, and Drupal Wiki numbers
# pages and attachments from independent sequences -- page 88 and attachment 88 both
# exist. Bare ids would make them one record. Spaces and groups need no prefix: they
# live in their own collections and are never parsed back out.
PAGE_ID_PREFIX = "page:"
ATTACHMENT_ID_PREFIX = "attachment:"


def page_external_id(page_id: int | str) -> str:
    return f"{PAGE_ID_PREFIX}{page_id}"


def attachment_external_id(attachment_id: int | str) -> str:
    return f"{ATTACHMENT_ID_PREFIX}{attachment_id}"


def _attachment_extension(attachment: dict[str, Any]) -> str | None:
    """Lower-cased extension of an attachment's file name, or None when it has none."""
    file_name = (
        attachment.get("fileName")
        or attachment.get("name")
        or f"attachment-{attachment.get('id')}"
    )
    return file_name.rsplit(".", 1)[-1].lower() if "." in file_name else None


def source_id_from_external(external_id: str, prefix: str) -> int | None:
    """Inverse of the helpers above; None when the id does not carry the prefix."""
    if not external_id or not external_id.startswith(prefix):
        return None
    try:
        return int(external_id[len(prefix):])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Spaces
# ---------------------------------------------------------------------------
SPACE_ACCESS_PRIVATE = "PRIVATE"
SPACE_ACCESS_AUTHENTICATED = "AUTHENTICATED"
SPACE_ACCESS_ANONYMOUS = "ANONYMOUS"
# Everyone on the wiki can read these, so they map to an org-wide permission.
OPEN_SPACE_ACCESS_STATUSES = frozenset({SPACE_ACCESS_AUTHENTICATED, SPACE_ACCESS_ANONYMOUS})

# The only grants this connector writes on a private space. A stored space whose grants
# include anything else (an org-wide one from when the space was open) cannot stand in
# for a member list.
PRIVATE_SPACE_GRANT_TYPES = frozenset({EntityType.USER.value, EntityType.GROUP.value})

# UserDTO.status; the spec enumerates exactly ENABLED and DISABLED.
USER_STATUS_ENABLED = "ENABLED"
USER_STATUS_DISABLED = "DISABLED"

# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------
PAGE_SIZE = 100  # the API allows up to 2000; 100 keeps responses small
RECORD_BATCH_SIZE = 100
# Stored pages a run looks up one by one because their space no longer lists them. The
# rest wait for the next run, which finds them again.
GONE_PAGE_CHECKS_PER_RUN = 500
# _page_access's answer when a page was written shared and the readers of one of its spaces
# cannot be confirmed this run: its stored access stays as it is.
KEEP_STORED_ACCESS = "keep stored access"
STORED_PAGE_READ_SIZE = 1000


SECONDS_TO_MILLIS = 1000


def _epoch_millis(seconds: int | str | None) -> int | None:
    """Drupal Wiki reports ``lastModified`` in unix seconds; records store millis."""
    try:
        return int(seconds) * SECONDS_TO_MILLIS
    except (TypeError, ValueError):
        return None


# ============================================================================
# Connector
# ============================================================================

class DrupalWikiSyncError(RuntimeError):
    """A sync phase failed; the run is reported as failed and no checkpoint moves."""


class DrupalWikiAuthError(RuntimeError):
    """The wiki rejected the token. Nothing else can be read, so the run fails.

    Not a ``DrupalWikiSyncError``: those are recorded and skipped one item at a time.
    """


@dataclass(frozen=True)
class SpaceProblem:
    """A problem that hits spaces the same way, reported as one line that names them.

    ``incomplete``: the run did not read the whole wiki, so a full sync unlinks nothing.
    """

    message: str
    incomplete: bool


@dataclass
class PageListing:
    """What one run's page listings showed, for the share and removal passes after them."""

    # space -> ids of the pages it is home to; only spaces listed in full
    home: dict[int, set[int]] = field(default_factory=dict)
    # space -> page id -> ids of the records stored for it (the page and its attachments)
    # when the space's listing began
    stored: dict[int, dict[int, list[str]]] = field(default_factory=dict)
    # pages written this run, and the grants digest each page record was written with
    written: set[int] = field(default_factory=set)
    written_as: dict[int, str] = field(default_factory=dict)
    # page -> spaces that list it as shared into them
    shared: dict[int, set[int]] = field(default_factory=dict)
    # spaces each of whose pages has been written ``STRICT`` (it has a checkpoint)
    settled: set[int] = field(default_factory=set)
    # what each shared page was written with on the runs before: page id -> home, spaces, grants
    shares_before: dict[str, dict[str, Any]] = field(default_factory=dict)


TREE_UNREADABLE = SpaceProblem(
    "Could not read the page structure of {spaces}, so their pages keep the structure "
    "they already have and new pages are placed at the top of their space.",
    incomplete=False,
)
# How long a private space may keep syncing under the access last confirmed for it while
# the wiki refuses its member list. Past it, someone removed from the space in the wiki
# would go on receiving its new pages, so the space is skipped instead.
KEPT_ACCESS_MAX_AGE_DAYS = 30
# Kept access this close to its end is announced once, before the space starts being skipped.
KEPT_ACCESS_NOTICE_DAYS = 7
_DAY_MS = 24 * 60 * 60 * 1000
# The connection test's GraphQL check only adds a warning, so it gets one try and this long
# in all, rather than the sync's retries and timeouts.
GRAPHQL_PROBE_TIMEOUT_S = 10.0

# A full sync's prep deletes every sync point, the confirmed dates with them.
FULL_SYNC_ENDS_KEPT_ACCESS = (
    "A full sync, including one started by changing the filters, ends this for private "
    "spaces until Drupal Wiki lists their members again."
)

MEMBERS_REFUSED_KEPT = SpaceProblem(
    "Drupal Wiki refused to list the members of {spaces}, so their pages were synced "
    "with the access confirmed on the date shown. " + FULL_SYNC_ENDS_KEPT_ACCESS,
    incomplete=True,
)
MEMBERS_REFUSED_KEPT_ENDING = SpaceProblem(
    "Drupal Wiki refused to list the members of {spaces}, so their pages were synced "
    "with the access confirmed on the date shown, which ends within "
    f"{KEPT_ACCESS_NOTICE_DAYS} days; after that they are skipped. " + FULL_SYNC_ENDS_KEPT_ACCESS,
    incomplete=True,
)
KEPT_ACCESS_PROBLEMS = (MEMBERS_REFUSED_KEPT, MEMBERS_REFUSED_KEPT_ENDING)
MEMBERS_REFUSED_STALE = SpaceProblem(
    "Drupal Wiki refused to list the members of {spaces}, and their access was last "
    f"confirmed more than {KEPT_ACCESS_MAX_AGE_DAYS} days ago, so they were skipped.",
    incomplete=True,
)
MEMBERS_REFUSED_UNVERIFIED = SpaceProblem(
    "Drupal Wiki refused to list the members of {spaces}, and their stored access names "
    "someone this sync could not confirm as an active person or group in Drupal Wiki, so "
    "they were skipped.",
    incomplete=True,
)
MEMBERS_REFUSED_SKIPPED = SpaceProblem(
    "Drupal Wiki refused to list the members of {spaces} and no confirmed earlier access "
    "is stored, so they were skipped.",
    incomplete=True,
)

GRAPHQL_REFUSED_ACTION_CODE = "drupal_wiki_graphql_refused"
GRAPHQL_REFUSED_TITLE = "Drupal Wiki refuses member and page structure queries"
GRAPHQL_REFUSED_MESSAGE = (
    "Private spaces sync only with the access confirmed on an earlier sync, and only for "
    f"{KEPT_ACCESS_MAX_AGE_DAYS} days after it was confirmed. Private spaces never synced, "
    "or confirmed longer ago, are skipped, and the page hierarchy is not available. Open "
    "spaces are not affected. " + FULL_SYNC_ENDS_KEPT_ACCESS
)
GRAPHQL_REFUSED_CONFIRMED = " The oldest access in use was confirmed on {confirmed_on}."


def _utc_date(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, tz=UTC).date().isoformat()


def graphql_refused_action_item(oldest_confirmed_ms: int | None = None) -> ActionItem:
    """``params`` lets the UI word the item in the reader's language; ``context`` picks
    the variant that names the date."""
    params = {"days": str(KEPT_ACCESS_MAX_AGE_DAYS)}
    message = GRAPHQL_REFUSED_MESSAGE
    if oldest_confirmed_ms is not None:
        confirmed_on = _utc_date(oldest_confirmed_ms)
        params.update(confirmedOn=confirmed_on, context="dated")
        message += GRAPHQL_REFUSED_CONFIRMED.format(confirmed_on=confirmed_on)
    return ActionItem(
        code=GRAPHQL_REFUSED_ACTION_CODE,
        title=GRAPHQL_REFUSED_TITLE,
        message=message,
        params=params,
    )


def _plural(count: int, noun: str) -> str:
    """``2 spaces``, ``1 space`` -- notification titles read badly with "space(s)"."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _listed(names: list[str], limit: int = 10) -> str:
    """Name what the message is about, and say how many more there are."""
    shown = ", ".join(names[:limit])
    extra = len(names) - limit
    return f"{shown} and {extra} more" if extra > 0 else shown


def _edge_source_key(edge: dict[str, Any]) -> str | None:
    """The key of the node an edge comes from. Most providers return ``from_id``; ArangoDB
    returns the raw edge, whose ``_from`` is ``<collection>/<key>``."""
    if edge.get("from_id"):
        return str(edge["from_id"])
    source = edge.get("_from")
    return str(source).split("/", 1)[-1] if source else None


def _as_int(value: object) -> int | None:
    """Ids arrive as numbers, strings or nulls depending on the endpoint."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _grants_digest(permissions: list[Permission] | None) -> str:
    """What a page was written with: "" for plain, else a digest of its own grants."""
    if permissions is None:
        return ""
    keys = sorted(
        f"{p.entity_type.value}|{(p.email or '').lower()}|{p.external_id or ''}|{p.type.value}" for p in permissions
    )
    return "shared:" + hashlib.sha256("\n".join(keys).encode()).hexdigest()


@ConnectorBuilder("Drupal Wiki")\
    .in_group("Drupal Wiki")\
    .with_description("Sync spaces and pages from your Drupal Wiki instance")\
    .with_categories(["Knowledge Management"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_resilience_config(rate_limit=10, max_retries=3)\
    .with_auth([
        AuthBuilder.type(AuthType.API_TOKEN).fields([
            AuthField(
                name="baseUrl",
                display_name="Wiki URL",
                placeholder="https://wiki.example.com",
                description="The address you use to open your Drupal Wiki",
                field_type="URL",
                max_length=2048,
            ),
            AuthField(
                name="apiToken",
                display_name="Personal Access Token",
                placeholder="pat:xxxxxxxx",
                description="From User settings > API-Token. Paste it with the pat: prefix.",
                field_type="PASSWORD",
                max_length=1024,
                is_secret=True,
            ),
        ])
    ])\
    .with_info(CONNECTOR_EMAIL_IDENTITY_INFO)\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.DRUPAL_WIKI.value))
        .add_documentation_link(DocumentationLink(
            "Drupal Wiki REST API",
            "https://help.drupal-wiki.com/node/605",
            "setup",
        ))
        .add_filter_field(FilterField(
            name=SyncFilterKey.SPACE_IDS.value,
            display_name="Spaces",
            description="Limit the sync to these spaces.",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            default_value=[],
            option_source_type=OptionSourceType.DYNAMIC,
        ))
        .add_filter_field(CommonFields.file_extension_filter(
            display_name="Attachment types",
            description=(
                "Sync only these attachment types (Include), or every type except them "
                "(Exclude). Applies to attachments only; pages are always synced. "
                "Leave empty to sync every attachment."
            ),
        ))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .add_filter_field(FilterField(
            name=IndexingFilterKey.PAGES.value,
            display_name="Pages",
            description="Index wiki pages.",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            default_value=True,
        ))
        .add_filter_field(FilterField(
            name=IndexingFilterKey.ATTACHMENTS.value,
            display_name="Attachments",
            description="Index files attached to pages.",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            default_value=True,
        ))
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_realtime_support(False)
        .with_agent_support(False)
    )\
    .build_decorator()
class DrupalWikiConnector(BaseConnector):
    """Syncs a Drupal Wiki instance: users, groups, spaces, pages and attachments."""

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
            DrupalWikiApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
        self.connector_name = Connectors.DRUPAL_WIKI

        self.client: DrupalWikiClient | None = None
        self.data_source: DrupalWikiDataSource | None = None
        self.graphql_client: DrupalWikiGraphQLClient | None = None
        self.graphql: DrupalWikiGraphQLDataSource | None = None

        self.base_url = ""
        # Partial failures collected across one run: a space or group that failed is
        # reported at the end, and its checkpoint is held back, but the run continues.
        self._failures: list[str] = []
        # Space names per kind of problem, reported one line per kind.
        self._space_problems: dict[SpaceProblem, list[str]] = {}
        # Whether the wiki refused a GraphQL query this run; None when none was answered
        # either way, which leaves the admin's action item as it is.
        self._graphql_refused: bool | None = None
        # Emails the user directory marks DISABLED, or None when it could not be
        # read this run. Private spaces are then left as they were.
        self._disabled_emails: set[str] | None = None
        # The grants written this run for each space; a shared page carries them too.
        self._space_readers: dict[int, list[Permission]] = {}
        # Emails of the wiki's enabled users, and the ids of the groups whose members were
        # read, both this run; None when that phase did not finish in full. Stored grants
        # stand in for a refused member list only if every one names a principal in these.
        self._enabled_emails: set[str] | None = None
        self._groups_read: set[str] | None = None
        # When each kept private space's access was last confirmed, for the action item.
        self._kept_confirmed_ms: list[int] = []
        self._principals: tuple[dict[str, str], dict[str, str]] | None = None
        self._principals_loaded = False
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

        self.record_sync_point = SyncPoint(
            connector_id=connector_id,
            org_id=data_entities_processor.org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=data_store_provider,
        )

    async def init(self) -> bool:
        try:
            self.client = await DrupalWikiClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id,
                resilience=self.resilience,
            )
            await check_wiki_address(self.client.get_base_url())
        except ValueError as e:
            raise ConnectorInitError(str(e)) from e
        except Exception as e:
            self.logger.error("Could not set up the Drupal Wiki client: %s", describe_exception(e), exc_info=True)
            raise ConnectorInitError(f"Failed to connect to Drupal Wiki: {describe_exception(e)}") from e

        self.base_url = self.client.get_base_url()
        self.data_source = DrupalWikiDataSource(self.client)

        # The internal GraphQL API carries private-space members and the page tree.
        self.graphql_client = DrupalWikiGraphQLClient.build_from_rest_client(
            self.client.get_client()
        )
        self.graphql = DrupalWikiGraphQLDataSource(self.graphql_client)
        return True

    async def test_connection_and_access(self) -> bool:
        if not self.data_source or not self.client:
            raise ConnectorInitError("Drupal Wiki client is not initialized")

        response = await self.data_source.list_users(size=1)

        if response.status >= HttpStatusCode.BAD_REQUEST.value:
            reason = describe_response(response)
            self.logger.error("Drupal Wiki connection test: listing users failed: %s", reason)
            if response.status == HttpStatusCode.UNAUTHORIZED.value:
                raise ConnectorInitError(
                    f"Drupal Wiki rejected the personal access token: {reason}. Check that it "
                    "is current and its user is not blocked."
                )
            if response.status == HttpStatusCode.FORBIDDEN.value:
                raise ConnectorInitError(
                    f"The Drupal Wiki token has no access to users: {reason}. Use a token whose "
                    "user can read the wiki."
                )
            raise ConnectorInitError(f"Drupal Wiki failed the connection test: {reason}")

        spaces = await self.data_source.list_spaces(size=1)
        if spaces.status >= HttpStatusCode.BAD_REQUEST.value:
            reason = describe_response(spaces)
            self.logger.error("Drupal Wiki connection test: listing spaces failed: %s", reason)
            raise ConnectorInitError(f"Drupal Wiki could not list spaces: {reason}")
        # A proxy or SSO page in front of the wiki answers 200 with HTML.
        try:
            _ = self._safe_json_parse(response, "testing the connection")
            space_listing = self._safe_json_parse(spaces, "testing the connection")
        except DrupalWikiSyncError as e:
            self.logger.error("Drupal Wiki connection test: %s", e)
            raise ConnectorInitError(str(e)) from e

        content = space_listing.get("content") if isinstance(space_listing, dict) else None
        space_id = _as_int(content[0].get("id")) if content and isinstance(content[0], dict) else None
        if space_id is not None:
            await self._probe_graphql(space_id)
        return True

    def _graphql_probe(self) -> tuple[DrupalWikiGraphQLDataSource, DrupalWikiRESTClientViaToken]:
        """A GraphQL source for the connection test only: same wiki and token, one try each,
        a short timeout. Returned with the REST client it owns, which the caller closes."""
        rest = self.client.get_client()
        probe_rest = DrupalWikiRESTClientViaToken(
            rest.get_base_url(), rest.get_token(), timeout=GRAPHQL_PROBE_TIMEOUT_S
        )
        return DrupalWikiGraphQLDataSource(DrupalWikiGraphQLClient.build_from_rest_client(probe_rest)), probe_rest

    async def _probe_graphql(self, space_id: int) -> None:
        """Ask both GraphQL operations about one space, so a refusal shows at setup.

        A refusal does not fail the test: open spaces still sync in full. Neither does a
        wiki too slow to answer in time; the first sync checks again.
        """
        self._graphql_refused = None
        probe, probe_rest = self._graphql_probe()
        operations = (probe.get_space_members, probe.get_space_tree)
        answered = 0
        try:
            async with asyncio.timeout(GRAPHQL_PROBE_TIMEOUT_S):
                for operation in operations:
                    try:
                        await self._graphql_read(operation(space_id))
                        answered += 1
                    except DrupalWikiGraphQLError as e:
                        self.logger.warning("Drupal Wiki connection test: %s", e)
        except TimeoutError:
            self.logger.warning(
                "Drupal Wiki connection test: could not check the member and page structure "
                "queries within %.0f s; the first sync checks them.", GRAPHQL_PROBE_TIMEOUT_S,
            )
        finally:
            await probe_rest.close()
        # One answer is not enough to clear the card: the query that did not answer may be
        # the refused one, so the stored items stay until both answer or one is refused.
        if not self._graphql_refused and answered < len(operations):
            self._graphql_refused = None
        if self._graphql_refused:
            self.logger.warning(
                "Drupal Wiki connection test: %s. %s", GRAPHQL_REFUSED_TITLE, GRAPHQL_REFUSED_MESSAGE
            )
        self.last_action_items = self._graphql_action_items()

    async def _graphql_read(self, call: Awaitable[T]) -> T:
        """Await one GraphQL query, noting whether the wiki answers them at all."""
        try:
            result = await call
        except DrupalWikiGraphQLRefusedError:
            self._graphql_refused = True
            raise
        if self._graphql_refused is None:
            self._graphql_refused = False
        return result

    def _graphql_action_items(self) -> list[ActionItem] | None:
        if self._graphql_refused is None:
            return None
        if not self._graphql_refused:
            return []
        return [graphql_refused_action_item(min(self._kept_confirmed_ms, default=None))]

    async def cleanup(self) -> None:
        if self.client is not None:
            await self.client.get_client().close()
        if self.graphql_client is not None:
            await self.graphql_client.close()
        self.data_source = None
        self.graphql = None

    async def get_signed_url(self, record: Record) -> str | None:
        """Drupal Wiki has no pre-signed downloads; content is streamed instead."""
        return None

    async def handle_webhook_notification(self, notification: dict) -> None:
        """Webhooks are configured per wiki by an admin and are not routed here yet."""
        self.logger.warning("Drupal Wiki webhook received but webhook sync is not enabled")

    # ------------------------------------------------------------------
    # Sync orchestration
    # ------------------------------------------------------------------

    async def run_incremental_sync(self) -> None:
        """The platform only ever calls ``run_sync``; incremental state lives there."""
        await self.run_sync()

    async def run_sync(self) -> None:
        """Sync users, groups, spaces and pages.

        A space or group that fails on its own is recorded and reported at the end;
        only an error escaping a phase aborts the run.
        """
        try:
            if not self.data_source:
                raise DrupalWikiSyncError("Drupal Wiki data source is not initialized")

            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "Drupal Wiki", self.connector_id, self.logger
            )
            self._failures = []
            self._space_problems = {}
            self._graphql_refused = None
            self._disabled_emails = None
            self._space_readers = {}
            self._enabled_emails = None
            self._groups_read = None
            self._kept_confirmed_ms = []
            self._principals = None
            self._principals_loaded = False

            users_by_id = await self._run_phase("people", self._sync_users())
            if users_by_id is None:
                # Groups resolve members from that directory. Running them against a
                # partial one would strip every member it could not resolve, so the
                # existing membership is left alone instead.
                self._fail(
                    "Could not read the list of people from Drupal Wiki. Group membership "
                    "was left as it is rather than emptied, so nobody loses access."
                )
            else:
                await self._run_phase("people who lost access", self._withdraw_people_without_access())
                await self._run_phase("groups", self._sync_groups(users_by_id))

            spaces = await self._run_phase("spaces", self._sync_spaces())
            if spaces is None:
                self._fail(
                    "Could not read the list of spaces from Drupal Wiki, so no pages were "
                    "synced this time. Existing pages are unchanged."
                )
            else:
                await self._run_phase("pages", self._sync_pages(spaces))

            self.last_action_items = self._graphql_action_items()
            problems = self._problem_lines()
            await self._notify_problems(problems)
            self.logger.info(
                "✅ Drupal Wiki sync finished with %d problem(s)", len(problems)
            )
        except DrupalWikiAuthError as e:
            await self.notify(
                type=NotificationType.CONNECTOR_AUTH_ERROR,
                severity=NotificationSeverity.ERROR,
                title=self._notification_title("personal access token rejected"),
                message=(
                    f"{e}\n\nCreate a new token in Drupal Wiki (User settings > API-Token) "
                    "and update it in the connector settings. What already synced is unchanged."
                ),
            )
            self.logger.error("Drupal Wiki sync stopped: %s", e)
            raise
        except Exception as e:
            await self.notify(
                type=NotificationType.CONNECTOR_SYNC_ERROR,
                severity=NotificationSeverity.ERROR,
                title=self._notification_title("sync did not finish"),
                message=f"{describe_exception(e)[:300]}\n\nWhat already synced is unchanged.",
            )
            self.logger.error("Drupal Wiki sync failed: %s", describe_exception(e), exc_info=True)
            raise

    async def _run_phase(self, name: str, coro: Awaitable[T]) -> T | None:
        """Run one sync phase, returning None if it failed.

        A phase that dies takes only itself down: the failure is logged with its
        traceback, recorded for the end-of-run notification, and the remaining phases
        still run. Without this a single bad listing response would abort the run and
        nothing at all would sync.
        """
        try:
            return await coro
        except DrupalWikiAuthError:
            raise
        except Exception as e:
            # Logged here with the traceback; recorded without logging a second time.
            # A DrupalWikiSyncError already names what it was reading, so it stands on
            # its own; any other exception needs the phase spelled out.
            self.logger.error(
                "Drupal Wiki: could not sync %s: %s", name, describe_exception(e), exc_info=True
            )
            self._failures.append(
                str(e) if isinstance(e, DrupalWikiSyncError)
                else f"Could not sync {name} from Drupal Wiki: {describe_exception(e)}"
            )
            return None

    def full_sync_coverage(self) -> FullSyncCoverage:
        """A full sync that skipped anything (a space whose members could not be read,
        a page, a group, a whole phase) did not read the whole wiki, so nothing it did
        not write may be unlinked after it."""
        return FullSyncCoverage(
            complete=not self._failures
            and not any(problem.incomplete for problem in self._space_problems)
        )

    # ------------------------------------------------------------------
    # Users and groups
    # ------------------------------------------------------------------

    async def _sync_users(self) -> dict[str, AppUser]:
        """Active users are refreshed in full every run; emails are the identity link.

        Disabled accounts are listed but not written: they cannot open the wiki, so a
        record for them would grant nothing. Their emails are kept for the run, because
        a private space's member list still names them and must not grant them access.

        Each page is written as it arrives, so only one page of new objects is held at
        a time. The directory itself is still returned whole because group membership
        can name any user.
        """
        users_by_id: dict[str, AppUser] = {}
        disabled_emails: set[str] = set()
        skipped_without_email = 0
        page = 0
        while True:
            payload = self._require_ok(
                await self.data_source.list_users(only_active=False, page=page, size=PAGE_SIZE),
                "reading the list of people",
            )
            content = payload.get("content") or []
            app_users: list[AppUser] = []
            for user in content:
                user_id = user.get("id")
                if user_id is None:
                    # Group membership resolves users by this id, so one without it
                    # would be stored under "None" and match every other id-less user.
                    self.logger.warning(
                        "Ignoring a Drupal Wiki user that arrived without an id"
                    )
                    continue
                email = (user.get("email") or "").strip()
                if (user.get("status") or "").upper() == USER_STATUS_DISABLED:
                    if email:
                        disabled_emails.add(email.lower())
                    continue
                if not email:
                    # Without an email there is nothing to match a PipesHub account on,
                    # so the user could never be granted anything.
                    self.logger.debug(
                        "Drupal Wiki user %s has no email address, so they cannot be "
                        "matched to a PipesHub account", user_id
                    )
                    skipped_without_email += 1
                    continue
                first_name = (user.get("firstName") or "").strip()
                last_name = (user.get("lastName") or "").strip()
                app_user = AppUser(
                    app_name=Connectors.DRUPAL_WIKI,
                    connector_id=self.connector_id,
                    org_id=self.data_entities_processor.org_id,
                    source_user_id=str(user_id),
                    email=email,
                    full_name=(
                        " ".join(part for part in (first_name, last_name) if part)
                        or user.get("userName")
                        or email
                    ),
                    is_active=(user.get("status") or "").upper() == USER_STATUS_ENABLED,
                )
                users_by_id[str(user_id)] = app_user
                app_users.append(app_user)

            if app_users:
                await self.data_entities_processor.on_new_app_users(app_users)
            # An absent ``last`` stops rather than loops.
            if payload.get("last", True) or not content:
                break
            page += 1

        # Only a definite DISABLED counts, and never against an address another,
        # enabled account also uses.
        self._disabled_emails = disabled_emails - {
            user.email.lower() for user in users_by_id.values()
        }
        self._enabled_emails = {
            user.email.lower() for user in users_by_id.values() if user.is_active
        }
        self.logger.info("Synced %d Drupal Wiki user(s)", len(users_by_id))
        if skipped_without_email:
            # Nobody without an email can be granted anything, so a large count here
            # explains an otherwise silent loss of access.
            self.logger.warning(
                "%d Drupal Wiki user(s) have no email address. They cannot be matched to a "
                "PipesHub account, so they will not see any wiki content until an email is "
                "added for them in Drupal Wiki.", skipped_without_email
            )
        return users_by_id

    async def _withdraw_people_without_access(self) -> None:
        """A stored person the directory just read does not show enabled (disabled, removed
        from the wiki, or now under another address) cannot open the wiki, so they lose the
        connector. An open space's org-wide grant reaches everyone the connector is linked
        to, so without this they would keep reading it. Their own grants are already
        rewritten without them: private spaces, group membership and shared pages leave out
        every account that is not enabled. The next run that lists them enabled links them
        again.
        """
        enabled = self._enabled_emails
        if not enabled:
            # A directory with nobody enabled in it (the token's own user is) is a misread.
            return
        stored = await self.data_entities_processor.get_all_app_users(self.connector_id, raise_on_error=True)
        gone = [u for u in stored if u.source_user_id and (u.email or "").lower() not in enabled]
        if gone:
            await self.data_entities_processor.withdraw_app_user_access(self.connector_id, gone)
            self.logger.info("Withdrew Drupal Wiki from %d person(s) it no longer shows enabled", len(gone))

    async def _sync_groups(self, users_by_id: dict[str, AppUser]) -> None:
        """Sync every group with its members.

        Membership is replaced wholesale, so a member removed in the wiki loses access
        on the next run. Groups deleted in the wiki are left in place, as Jira's
        connector does: their grants disappear anyway when space permissions are
        rewritten, so a leftover group node grants nothing.
        """
        synced = 0
        read: set[str] = set()
        all_read = True
        page = 0
        while True:
            payload = self._require_ok(
                await self.data_source.list_groups(page=page, size=PAGE_SIZE), "reading the list of groups"
            )
            content = payload.get("content") or []
            groups_with_members: list[tuple[AppUserGroup, list[AppUser]]] = []
            for group in content:
                group_id = _as_int(group.get("id"))
                if group_id is None:
                    self.logger.warning(
                        "Ignoring a Drupal Wiki group that arrived without an id"
                    )
                    continue
                try:
                    detail = self._require_ok(
                        await self.data_source.get_group(group_id), f"reading the members of group {group_id}"
                    )
                except DrupalWikiAuthError:
                    raise
                except Exception as e:
                    # Passing an empty member list here would wipe the group's edges,
                    # so a failed fetch leaves the previous membership in place.
                    self._fail(
                        str(e) if isinstance(e, DrupalWikiSyncError)
                        else f"Could not read the members of group {group_id}: {describe_exception(e)}"
                    )
                    all_read = False
                    continue

                read.add(str(group_id))
                members = [
                    users_by_id[str(member.get("id"))]
                    for member in (detail.get("members") or [])
                    if str(member.get("id")) in users_by_id
                    and users_by_id[str(member.get("id"))].is_active
                ]
                source = group.get("groupSource")
                groups_with_members.append((
                    AppUserGroup(
                        app_name=Connectors.DRUPAL_WIKI,
                        connector_id=self.connector_id,
                        org_id=self.data_entities_processor.org_id,
                        source_user_group_id=str(group_id),
                        name=group.get("name") or f"Group {group_id}",
                        description=f"source={source}" if source else None,
                    ),
                    members,
                ))

            if groups_with_members:
                await self.data_entities_processor.on_new_user_groups(groups_with_members)
                synced += len(groups_with_members)
            if payload.get("last", True) or not content:
                break
            page += 1

        self._groups_read = read if all_read else None
        self.logger.info("Synced %d Drupal Wiki group(s)", synced)

    # ------------------------------------------------------------------
    # Spaces -> record groups and their permissions
    # ------------------------------------------------------------------

    async def _sync_spaces(self) -> dict[int, dict[str, Any]]:
        """Spaces become record groups; their permissions are replaced every run.

        A private space is skipped when the user directory or its member list
        could not be read, so stored grants stay put.
        """
        allowed_ids, exclude_ids = self._get_space_id_filters()
        spaces: dict[int, dict[str, Any]] = {}
        unreadable_spaces: list[str] = []

        page = 0
        while True:
            payload = self._require_ok(
                await self.data_source.list_spaces(page=page, size=PAGE_SIZE), "reading the list of spaces"
            )
            content = payload.get("content") or []
            groups_with_permissions: list[tuple[RecordGroup, list[Permission]]] = []
            confirmed: list[int] = []
            for space in content:
                space_id = _as_int(space.get("id"))
                space_name = space.get("name") or f"space {space.get('id')}"
                if space_id is None:
                    self.logger.warning(
                        "Ignoring a Drupal Wiki space that arrived without a usable id"
                    )
                    continue
                if allowed_ids and str(space_id) not in allowed_ids:
                    continue
                if exclude_ids and str(space_id) in exclude_ids:
                    continue

                members = None
                if (space.get("accessStatus") or "").upper() == SPACE_ACCESS_PRIVATE:
                    if self._disabled_emails is None:
                        # Same as an unreadable member list: writing grants without
                        # a directory would guess. A space first seen this run waits
                        # for a run that loaded users.
                        self._fail(
                            f"Could not read the list of people from Drupal Wiki, so "
                            f"the space '{space_name}' was left as it is this time."
                        )
                        continue
                    try:
                        members = await self._graphql_read(self.graphql.get_space_members(space_id))
                    except DrupalWikiGraphQLRefusedError:
                        # Not written: the stored record group and its grants stay exactly
                        # as they are, and the pages inherit them.
                        verdict, confirmed_at = await self._kept_access_verdict(space_id)
                        label = f"'{space_name}'" + (
                            f" (access as of {_utc_date(confirmed_at)})" if confirmed_at else ""
                        )
                        self._report_space_problem(verdict, label)
                        if verdict in KEPT_ACCESS_PROBLEMS and confirmed_at is not None:
                            self._kept_confirmed_ms.append(confirmed_at)
                            spaces[space_id] = space
                        continue
                    except DrupalWikiGraphQLError as e:
                        # Fail closed: a space whose members cannot be read is left
                        # exactly as it was on the previous run.
                        self._fail(
                            f"Could not read who has access to the Drupal Wiki space "
                            f"'{space_name}', so it was left as it is this time: {e}"
                        )
                        continue
                    confirmed.append(space_id)

                permissions = self._build_space_permissions(space, members)
                self._space_readers[space_id] = permissions
                if not permissions:
                    # Synced anyway, with no grants: the pages still need a group to
                    # belong to, and giving someone the read right later then only has
                    # to add the edge instead of waiting for a full resync.
                    self.logger.warning(
                        "Nobody has access to the Drupal Wiki space '%s'. Its pages are "
                        "synced but will not appear in search for anyone until someone is "
                        "given access to that space in Drupal Wiki.", space_name
                    )
                    unreadable_spaces.append(space_name)

                spaces[space_id] = space
                groups_with_permissions.append((
                    RecordGroup(
                        org_id=self.data_entities_processor.org_id,
                        name=space.get("name") or f"Space {space_id}",
                        description=space.get("description"),
                        external_group_id=str(space_id),
                        connector_name=Connectors.DRUPAL_WIKI,
                        connector_id=self.connector_id,
                        group_type=RecordGroupType.DRUPAL_WIKI_SPACE,
                        inherit_permissions=False,
                    ),
                    permissions,
                ))

            if groups_with_permissions:
                await self.data_entities_processor.on_new_record_groups(groups_with_permissions)
            # Only once the grants read from the member list are written.
            for space_id in confirmed:
                await self._confirm_space_access(space_id)
            if payload.get("last", True) or not content:
                break
            page += 1

        if unreadable_spaces:
            await self.notify(
                type=NotificationType.CONNECTOR_WARNING,
                severity=NotificationSeverity.WARNING,
                title=self._notification_title(
                    f"{_plural(len(unreadable_spaces), 'space')} with no members"
                ),
                message=(
                    f"Nobody can see pages in {_listed(unreadable_spaces)}. "
                    "Add members in Drupal Wiki."
                ),
            )

        self.logger.info("Synced %d Drupal Wiki space(s)", len(spaces))
        return spaces

    @staticmethod
    def _access_sync_point_key(space_id: int) -> str:
        return generate_record_sync_point_key("space_access", "space", str(space_id))

    async def _confirm_space_access(self, space_id: int) -> None:
        """Note that this space's stored grants were just rewritten from its member list."""
        try:
            await self.record_sync_point.update_sync_point(
                self._access_sync_point_key(space_id),
                {"confirmed_at": get_epoch_timestamp_in_ms()},
            )
        except Exception as e:
            # Fails closed: without the date the space is skipped while members are refused.
            self.logger.warning(
                "Could not record when the access of the Drupal Wiki space %s was confirmed: %s",
                space_id, describe_exception(e),
            )

    async def _kept_access_verdict(self, space_id: int) -> tuple[SpaceProblem, int | None]:
        """Whether a private space whose members are refused may sync under its stored grants.

        Returns the problem to report and when its access was last confirmed. Only
        ``MEMBERS_REFUSED_KEPT`` lets the space sync; every other outcome, a failed read
        included, skips it.
        """
        checkpoint = await self._get_checkpoint(
            self.record_sync_point, self._access_sync_point_key(space_id)
        )
        confirmed_at = _as_int(checkpoint.get("confirmed_at"))
        age_ms = get_epoch_timestamp_in_ms() - confirmed_at if confirmed_at is not None else None
        # A date ahead of this host's clock came from a skewed clock; counting from it would
        # stretch the bound, so it is no confirmation at all.
        if age_ms is None or age_ms < 0:
            return MEMBERS_REFUSED_SKIPPED, None
        if age_ms > KEPT_ACCESS_MAX_AGE_DAYS * _DAY_MS:
            return MEMBERS_REFUSED_STALE, confirmed_at
        grants = await self._stored_space_grants(space_id)
        if not grants or any(grant.get("type") not in PRIVATE_SPACE_GRANT_TYPES for grant in grants):
            return MEMBERS_REFUSED_SKIPPED, None
        if not await self._grants_name_live_principals(grants):
            return MEMBERS_REFUSED_UNVERIFIED, confirmed_at
        if age_ms > (KEPT_ACCESS_MAX_AGE_DAYS - KEPT_ACCESS_NOTICE_DAYS) * _DAY_MS:
            return MEMBERS_REFUSED_KEPT_ENDING, confirmed_at
        return MEMBERS_REFUSED_KEPT, confirmed_at

    async def _stored_space_grants(self, space_id: int) -> list[dict[str, Any]] | None:
        """The PERMISSION edges on a space's live stored record group; None if there is none."""
        try:
            group = await self.data_entities_processor.get_record_group_by_external_id(
                self.connector_id, str(space_id)
            )
            if group is None or group.is_deleted_at_source:
                return None
            async with self.data_store_provider.transaction() as tx_store:
                return await tx_store.get_edges_to_node(
                    f"{CollectionNames.RECORD_GROUPS.value}/{group.id}",
                    CollectionNames.PERMISSION.value,
                    raise_on_error=True,
                )
        except Exception as e:
            self.logger.warning(
                "Could not read the stored access of the Drupal Wiki space %s: %s",
                space_id, describe_exception(e),
            )
            return None

    async def _grants_name_live_principals(self, grants: list[dict[str, Any]]) -> bool:
        """Whether every grant names an enabled wiki user or a group read in full this run.

        A user grant points at the PipesHub user the connector matched by email, so it is
        checked by that user's email; a group grant by the group's wiki id.
        """
        user_keys = {_edge_source_key(g) for g in grants if g.get("type") == EntityType.USER.value}
        group_keys = {_edge_source_key(g) for g in grants if g.get("type") == EntityType.GROUP.value}
        if (user_keys and self._enabled_emails is None) or (group_keys and self._groups_read is None):
            return False
        stored = await self._stored_principals()
        if stored is None:
            return False
        emails, group_ids = stored
        return all(emails.get(key) in (self._enabled_emails or set()) for key in user_keys) and all(
            group_ids.get(key) in (self._groups_read or set()) for key in group_keys
        )

    async def _stored_principals(self) -> tuple[dict[str, str], dict[str, str]] | None:
        """``({user key: email}, {group key: wiki group id})`` as stored, read once per run
        however many spaces ask; None (cached too) when the read failed."""
        if self._principals_loaded:
            return self._principals
        self._principals_loaded = True
        org_id = self.data_entities_processor.org_id
        try:
            async with self.data_store_provider.transaction() as tx_store:
                users = await tx_store.get_users(org_id)
                groups = await tx_store.get_user_groups(self.connector_id, org_id, raise_on_error=True)
        except Exception as e:
            self.logger.warning("Could not read stored people or groups: %s", describe_exception(e))
            return None
        self._principals = (
            {user.id: (user.email or "").lower() for user in users},
            {group.id: group.source_user_group_id for group in groups},
        )
        return self._principals

    def _build_space_permissions(
        self,
        space: dict[str, Any],
        members: dict[str, Any] | None,
    ) -> list[Permission]:
        """Permissions for one space.

        Open spaces (``AUTHENTICATED``/``ANONYMOUS``) are readable by every wiki user,
        which maps to a single org-wide read. Private spaces are readable only by their
        members, which needs ``members`` from the internal GraphQL API.
        """
        access_status = (space.get("accessStatus") or "").upper()
        if access_status in OPEN_SPACE_ACCESS_STATUSES:
            return [Permission(
                external_id=None, email=None,
                type=PermissionType.READ, entity_type=EntityType.ORG,
            )]

        if not members:
            return []

        # Holding any role on a space means being able to see it: a revision approver
        # or technical reviewer cannot do their job without reading the page, and every
        # other right is strictly more than read. So membership is the grant, and it is
        # always READ -- nothing downstream separates the kinds, as in the Jira connector.
        permissions: dict[tuple[str, str], Permission] = {}
        disabled = self._disabled_emails or set()
        for user in members.get("users") or []:
            email = (user.get("email") or "").strip()
            if not email or email.lower() in disabled:
                continue
            permissions.setdefault(("user", email.lower()), Permission(
                external_id=None,
                email=email,
                type=PermissionType.READ,
                entity_type=EntityType.USER,
            ))

        for group in members.get("groups") or []:
            group_id = group.get("id")
            if group_id is None:
                continue
            external_id = str(group_id)
            permissions.setdefault(("group", external_id), Permission(
                external_id=external_id,
                email=None,
                type=PermissionType.READ,
                entity_type=EntityType.GROUP,
            ))

        return list(permissions.values())

    # ------------------------------------------------------------------
    # Pages and attachments
    # ------------------------------------------------------------------

    async def _sync_pages(self, spaces: dict[int, dict[str, Any]]) -> None:
        """Sync each selected space against its own checkpoint, then the shares its
        listing showed and the pages it no longer lists.

        A space keeps its cursor to itself so that a space which fails, or which the
        permission phase dropped, is written in full the next time it is reachable.
        A single shared cursor would move on without it and its existing pages, being
        older than that cursor, would never be written again.
        """
        # A failed read fails the pass: read as "nothing shared", a shared page would be
        # written plain and an unshared one keep its readers. JSON text: Neo4j stores no
        # nested maps.
        listing = PageListing(shares_before=json.loads(
            (await self.record_sync_point.read_sync_point(self._shares_sync_point_key())).get("pages") or "{}"
        ))
        for space_id in spaces:
            try:
                await self._sync_space_pages(
                    space_id, spaces[space_id].get("name") or f"space {space_id}", listing
                )
            except DrupalWikiAuthError:
                raise
            except Exception as e:
                # A space that cannot be listed keeps its checkpoint, so the next run
                # resumes it from where it was; the other spaces still sync. A timeout
                # or dropped connection counts too: it says nothing about the others.
                if isinstance(e, DrupalWikiSyncError):
                    self._fail(str(e))
                    continue
                self.logger.error(
                    "Drupal Wiki: could not sync the pages of space %s: %s",
                    space_id, describe_exception(e), exc_info=True,
                )
                self._failures.append(
                    f"Could not sync the pages of the Drupal Wiki space "
                    f"'{spaces[space_id].get('name') or space_id}': {describe_exception(e)}"
                )
        await self._run_phase("shared pages", self._sync_shares(listing))
        await self._run_phase("removed pages", self._remove_pages_gone(listing))

    async def _sync_space_pages(self, space_id: int, space_name: str, listing: PageListing) -> None:
        """List one space in full, write its pages that changed or are not stored under it,
        and advance only that space's checkpoint.

        The cursor is the wiki's own ``lastModified``, never this host's clock, so both
        sides of the comparison come from the same machine and no skew allowance is
        needed. A page not stored under the space is written whatever its date: a move
        need not change it.
        """
        key = self._space_sync_point_key(space_id)
        checkpoint = await self._get_checkpoint(self.record_sync_point, key)
        modified_after = _as_int(checkpoint.get("last_modified"))
        failures_before = len(self._failures)
        stored = await self._stored_pages(space_id)
        if modified_after is not None:
            # Every page was written STRICT by the clean pass that set the checkpoint.
            listing.settled.add(space_id)
        home: set[int] = set()
        batch: list[tuple[Record, list[Permission]]] = []
        synced = 0
        newest_seen: int | None = None

        parents = await self._fetch_space_tree(space_id, space_name)
        async for page in self._fetch_pages(space_id):
            page_id = _as_int(page.get("id"))
            if _as_int(page.get("homeSpace")) != space_id:
                # Shared into this space: its home space's pass writes it, and the share
                # pass adds this space's readers.
                listing.shared.setdefault(page_id, set()).add(space_id)
                continue
            home.add(page_id)
            last_modified = _as_int(page.get("lastModified"))
            if last_modified is not None and (newest_seen is None or last_modified > newest_seen):
                newest_seen = last_modified
            if (
                page_id in stored and modified_after is not None
                and last_modified is not None and last_modified <= modified_after
            ):
                # Kept as stored: a full sync that kept its checkpoints (their deletion
                # failed) must not unlink it.
                self.data_entities_processor.note_kept_records(self.connector_id, stored[page_id])
                continue
            try:
                records = await self._build_page_records(page, parents, listing)
            except (DrupalWikiSyncError, DrupalWikiAuthError):
                raise
            except Exception as e:
                # The rest of the space still syncs, but its checkpoint stays put so
                # the next run retries this page. Nothing else would: there is no
                # second pass to backfill it.
                self.logger.error(
                    "Drupal Wiki: could not read page %s in space %s: %s",
                    page.get("id"), space_id, describe_exception(e), exc_info=True,
                )
                self._failures.append(
                    f"Could not read the Drupal Wiki page "
                    f"'{page.get('title') or page.get('id')}' ({describe_exception(e)}). "
                    f"It will be tried again on the next sync."
                )
                continue
            batch.extend(records)
            listing.written.add(page_id)
            synced += 1
            if len(batch) >= RECORD_BATCH_SIZE:
                await self.data_entities_processor.on_new_records(batch)
                batch = []

        if batch:
            await self.data_entities_processor.on_new_records(batch)
        listing.home[space_id], listing.stored[space_id] = home, stored
        self.logger.info(
            "Synced %d changed page(s) in the Drupal Wiki space '%s'", synced, space_name
        )

        # A page whose attachments could not be listed is incomplete, so the checkpoint
        # must not move past it.
        if len(self._failures) != failures_before or newest_seen is None:
            return
        if modified_after is None and stored.keys() - home:
            # A first pass settles the space only once every stored page has been written
            # STRICT: one the listing left out (skipped by the offset paging, or gone) may
            # still inherit from the page above it the way pages did before.
            self.logger.info(
                "Drupal Wiki: %d stored page(s) of '%s' were not listed; its pages are written "
                "in full again next time", len(stored.keys() - home), space_name,
            )
            return
        listing.settled.add(space_id)
        # A page stamped in the same second as the newest one but saved just after the
        # listing read it would never count as changed again. Standing one second back
        # costs writing that second's pages once more.
        await self.record_sync_point.update_sync_point(key, {"last_modified": newest_seen - 1})

    @staticmethod
    def _space_sync_point_key(space_id: int) -> str:
        # Not "space": that checkpoint dates from when a page inherited from the page above
        # it. Without one, every page is written once more and takes its space's readers.
        return generate_record_sync_point_key(RecordType.WEBPAGE.value, "space_pages", str(space_id))

    async def _stored_pages(self, space_id: int) -> dict[int, list[str]]:
        """``{page id: ids of its stored records}`` for the pages stored under a space: the
        page's own record and those of its attachments."""
        pages: dict[int, list[str]] = {}
        attachments: dict[int, list[str]] = {}
        after: str | None = None
        while True:
            records = await self.data_entities_processor.get_records_in_record_group(
                self.connector_id, str(space_id), STORED_PAGE_READ_SIZE, after
            )
            for record in records:
                if (page_id := source_id_from_external(record.external_record_id, PAGE_ID_PREFIX)) is not None:
                    pages.setdefault(page_id, []).append(record.id)
                elif (page_id := source_id_from_external(record.parent_external_record_id or "", PAGE_ID_PREFIX)) is not None:
                    attachments.setdefault(page_id, []).append(record.id)
            if len(records) < STORED_PAGE_READ_SIZE:
                break
            after = records[-1].id
        for page_id, ids in pages.items():
            ids.extend(attachments.get(page_id, []))
        return pages

    async def _fetch_pages(self, space_id: int) -> AsyncGenerator[dict[str, Any], None]:
        """Yield every page of one space: those whose home it is and those shared into it.

        Asking per space keeps the pages of spaces the user did not select off the
        wire entirely, rather than downloading the whole wiki and discarding most of
        it. ``GET /page`` answers with a Spring envelope, so ``last`` ends the walk.
        """
        index = 0
        while True:
            payload = self._require_ok(
                await self.data_source.list_pages(space_id=space_id, page=index, size=PAGE_SIZE),
                f"reading the pages of space {space_id}",
            )
            content = payload.get("content") or []
            for page in content:
                # An id-less page would be saved as "page:None" and, because the data
                # source drops null query params, would pull every attachment in the
                # wiki onto it. A page with no home space has no group to belong to.
                if _as_int(page.get("id")) is None or _as_int(page.get("homeSpace")) is None:
                    continue
                yield page
            # An absent ``last`` stops rather than loops.
            if payload.get("last", True) or not content:
                return
            index += 1

    @staticmethod
    def _shares_sync_point_key() -> str:
        return generate_record_sync_point_key("shares", "space", "all")

    async def _sync_shares(self, listing: PageListing) -> None:
        """Rewrite each page whose share or whose spaces' readers changed since it was last
        written, and remember what every shared page was written with.

        The wiki tells only by listing a space, so what each shared page was written with
        is kept from run to run; a page is written again only when ``_page_access`` now
        decides otherwise. A page the home pass wrote this run is compared with what it
        was written with then, which may be short of spaces listed after its own.
        """
        before = listing.shares_before
        now: dict[str, dict[str, Any]] = {}
        for page_id in sorted({int(p) for p in before} | set(listing.shared)):
            had = before.get(str(page_id)) or {}
            try:
                record = None
                # A page written this run may have moved.
                home = None if page_id in listing.written else _as_int(had.get("home"))
                if home is None:
                    record = await self._stored_page(page_id)
                    if record is None:
                        continue  # not written yet, or removed; decided when it is
                    home = _as_int(record.external_record_group_id)
                shares, permissions = self._page_access(page_id, home, listing)
                if permissions == KEEP_STORED_ACCESS:
                    # Who reads one of its spaces is not known this run: as it was written.
                    now[str(page_id)] = {"home": home, "spaces": sorted(shares), "grants": had.get("grants", "")}
                    continue
                written = _grants_digest(permissions)
                if written != listing.written_as.get(page_id, had.get("grants")):
                    record = record or await self._stored_page(page_id)
                    if record is None:
                        continue
                    await self.data_entities_processor.on_updated_record_permissions(
                        record, self._apply_page_access(record, permissions)
                    )
            except DrupalWikiAuthError:
                raise
            except Exception as e:
                self._fail(
                    f"Could not update who reads the shared Drupal Wiki page {page_id}: {describe_exception(e)}"
                )
                if had:
                    now[str(page_id)] = had
                continue
            if shares:
                now[str(page_id)] = {"home": home, "spaces": sorted(shares), "grants": written}
        await self.record_sync_point.update_sync_point(
            self._shares_sync_point_key(), {"pages": json.dumps(now, sort_keys=True)}
        )

    def _page_access(
        self, page_id: int, home: int | None, listing: PageListing
    ) -> tuple[set[int], list[Permission] | str | None]:
        """The spaces a page is shared into, as far as this run knows them, and the grants
        it carries of its own: None for a plain page, ``KEEP_STORED_ACCESS`` for a page
        written shared whose readers cannot all be confirmed this run.

        Every page write takes its access from here, so a later write never undoes a
        share. A space this run did not list (yet, or in full) keeps the shares the page
        had into it. Unknown is not empty: a page stays as it was written until every
        reader of its spaces is read and every page of its home space is ``STRICT``, so
        nothing is opened to new readers and nobody loses a read on a guess.
        """
        had = listing.shares_before.get(str(page_id)) or {}
        shares = {
            space for space in (_as_int(s) for s in had.get("spaces") or [])
            if space is not None and space not in listing.home
        } | listing.shared.get(page_id, set())
        shares.discard(home)
        if not shares:
            return shares, None
        permissions = self._shared_page_permissions(home, shares, listing)
        if permissions is None and had:
            if not {s for s in (_as_int(x) for x in had.get("spaces") or []) if s is not None} - shares - {home}:
                return shares, KEEP_STORED_ACCESS
            # A space confirmed to no longer hold the page: its readers go now, even while
            # another space's readers are unknown. What is left is only readers confirmed
            # this run, which may be short of the unknown space's until they are read.
            return shares, self._shared_page_permissions(home, shares, listing, known_only=True)
        return shares, permissions

    @staticmethod
    def _apply_page_access(record: Record, permissions: list[Permission] | None) -> list[Permission]:
        """Set a page record's rule and inheritance for the grants ``_page_access`` decided,
        and return the grants to write, which replace the stored ones."""
        record.access_rule = AccessRule.STRICT if permissions is None else AccessRule.OPEN
        record.inherit_permissions = permissions is None
        record.rewrite_permissions = True
        return permissions or []

    async def _stored_page(self, page_id: int) -> Record | None:
        return await self.data_entities_processor.get_record_by_external_id(
            self.connector_id, page_external_id(page_id)
        )

    def _shared_page_permissions(
        self, home: int | None, shares: set[int], listing: PageListing, *, known_only: bool = False
    ) -> list[Permission] | None:
        """The readers of the home space and of every space the page is shared into, or
        None when they cannot be given: not shared, its home space's pages not all
        ``STRICT`` yet, or the readers of one of those spaces not read this run.

        ``known_only``: the readers of the home space and of those share spaces whose
        readers were read, leaving out the rest; None when that is none of the share
        spaces, or the home space's readers are not known either."""
        known = {s for s in shares if s in self._space_readers}
        if (
            not shares or home not in listing.settled or home not in self._space_readers
            or (not known if known_only else known != shares)
        ):
            return None
        spaces = {home, *known}
        unique: dict[tuple, Permission] = {}
        for space in sorted(spaces):
            for permission in self._space_readers[space]:
                unique.setdefault(
                    (permission.entity_type, (permission.email or "").lower(), permission.external_id), permission
                )
        return list(unique.values())

    async def _current_page(self, page_id: int) -> dict[str, Any] | None:
        """The page as the wiki has it now; None when it no longer has it, or the token can
        no longer see it."""
        response = await self.data_source.get_page(page_id)
        if response.status in (HttpStatusCode.NOT_FOUND.value, HttpStatusCode.FORBIDDEN.value):
            return None
        page = self._require_ok(response, f"reading page {page_id}")
        if not isinstance(page, dict) or _as_int(page.get("homeSpace")) is None:
            raise DrupalWikiSyncError(f"Drupal Wiki answered page {page_id} without its home space")
        return page

    async def _remove_pages_gone(self, listing: PageListing) -> None:
        """Remove each stored page its space no longer lists, once the wiki confirms it is
        not there: deleted, in the trash, or moved where this run did not write it.

        The listing pages by offset, so a page can slip between two windows when another
        is deleted meanwhile; each one is looked up before it goes. At most
        ``GONE_PAGE_CHECKS_PER_RUN`` are looked up a run, in page id order from where the
        last run stopped, so lookups that keep failing never hold back the ones after them.
        """
        candidates = sorted(
            (page_id, space_id)
            for space_id, home in listing.home.items()
            for page_id in listing.stored[space_id].keys() - home - listing.written
        )
        key = generate_record_sync_point_key("removals", "space", "all")
        after = _as_int((await self._get_checkpoint(self.record_sync_point, key)).get("after")) or 0
        start = next((i for i, (page_id, _) in enumerate(candidates) if page_id > after), 0)
        turn = (candidates[start:] + candidates[:start])[:GONE_PAGE_CHECKS_PER_RUN]
        for page_id, space_id in turn:
            try:
                page = await self._current_page(page_id)
                if page is not None and _as_int(page.get("homeSpace")) == space_id:
                    # Still there: the listing skipped it.
                    records = await self._build_page_records(page, None, listing)
                    if records:
                        await self.data_entities_processor.on_new_records(records)
                    continue
                await self.data_entities_processor.delete_record_by_external_id(
                    self.connector_id, page_external_id(page_id)
                )
            except DrupalWikiAuthError:
                raise
            except Exception as e:
                self._fail(
                    f"Could not check whether the Drupal Wiki page {page_id} still exists, so "
                    f"it stays until the next sync: {describe_exception(e)}"
                )
        if len(turn) < len(candidates):
            self.logger.info(
                "Drupal Wiki: %d of %d stored pages no space lists were checked; the rest are "
                "checked on the next sync", len(turn), len(candidates),
            )
        await self.record_sync_point.update_sync_point(
            key, {"after": turn[-1][0] if len(turn) < len(candidates) else 0}
        )

    async def _build_page_records(
        self,
        page: dict[str, Any],
        parents: dict[int, int] | None,
        listing: PageListing,
    ) -> list[tuple[Record, list[Permission]]]:
        """Build the page record, with the access ``_page_access`` decides, plus its
        attachments. A page whose stored access is kept is written here, without grants,
        and left out of what is returned."""
        page_id = _as_int(page.get("id"))
        pages_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.PAGES.value)
        attachments_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.ATTACHMENTS.value)

        # ``GET /attachment`` answers with a bare array rather than the envelope every
        # other listing uses, so a page shorter than the one asked for ends the walk.
        source_attachments: list[dict[str, Any]] = []
        try:
            index = 0
            while True:
                batch = self._require_ok(
                    await self.data_source.list_attachments(
                        page_id=page_id, page=index, size=PAGE_SIZE
                    ),
                    f"reading the attachments of page {page_id}",
                )
                source_attachments.extend(batch)
                if len(batch) < PAGE_SIZE:
                    break
                index += 1
        except DrupalWikiSyncError as e:
            # The page is still written; the failure holds the checkpoint back so the
            # attachments are picked up on the next run.
            self._fail(str(e))

        # Fetched only when one of the two things below is actually read off it. A page
        # already costs a request of its own, so a point lookup is cheaper than holding
        # every page record in the org in memory for the length of the run.
        stored = (
            await self.data_entities_processor.get_record_by_external_id(
                self.connector_id, page_external_id(page_id)
            )
            if parents is None or source_attachments
            else None
        )

        record = self._build_page_record(
            page,
            parent_page_id=parents.get(page_id) if parents is not None else None,
            auto_index_off=not pages_enabled,
        )
        if parents is None and stored is not None and stored.parent_external_record_id:
            # The tree could not be read. Re-saving the page without a parent would
            # delete its PARENT_CHILD edge, so the stored parent is carried over.
            record.parent_external_record_id = stored.parent_external_record_id
            record.parent_record_type = RecordType.WEBPAGE

        _, permissions = self._page_access(page_id, _as_int(page.get("homeSpace")), listing)
        records: list[tuple[Record, list[Permission]]] = []
        if permissions == KEEP_STORED_ACCESS:
            # Written in the form it was last written in, its grants left as stored, even
            # by a full sync, which would otherwise replace them.
            written = (listing.shares_before.get(str(page_id)) or {}).get("grants", "")
            shared = written.startswith("shared:")
            record.access_rule = AccessRule.OPEN if shared else AccessRule.STRICT
            record.inherit_permissions = not shared
            await self.data_entities_processor.on_record_content_update(record)
            listing.written_as[page_id] = written
        else:
            listing.written_as[page_id] = _grants_digest(permissions)
            records.append((record, self._apply_page_access(record, permissions)))
        # Search annotates a dependent hit with its parent, so an attachment has to
        # name the page's *stored* node id -- the id it was built with is discarded
        # when the processor matches an existing page.
        parent_node_id = stored.id if stored is not None else record.id
        records.extend(
            (
                self._build_attachment_record(
                    attachment,
                    page,
                    parent_node_id=parent_node_id,
                    auto_index_off=not attachments_enabled,
                ),
                [],
            )
            for attachment in source_attachments
            if extension_passes_filter(self.sync_filters, _attachment_extension(attachment))
        )
        return records

    def _build_page_record(
        self,
        page: dict[str, Any],
        *,
        parent_page_id: int | None = None,
        auto_index_off: bool = False,
    ) -> WebpageRecord:
        """``PageExcerptDTO`` -> WebpageRecord.

        The body is fetched at stream time, so only metadata is stored here. Version is
        left at 0: the processor bumps it whenever ``external_revision_id`` changes.
        """
        page_id = page.get("id")
        last_modified = page.get("lastModified")
        record = WebpageRecord(
            org_id=self.data_entities_processor.org_id,
            record_name=page.get("title") or f"Page {page_id}",
            record_type=RecordType.WEBPAGE,
            external_record_id=page_external_id(page_id),
            external_revision_id=str(last_modified) if last_modified is not None else None,
            external_record_group_id=str(page.get("homeSpace")),
            record_group_type=RecordGroupType.DRUPAL_WIKI_SPACE,
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.DRUPAL_WIKI,
            connector_id=self.connector_id,
            mime_type=MimeTypes.HTML.value,
            weburl=self._page_web_url(page_id),
            source_updated_at=_epoch_millis(last_modified),
            inherit_permissions=True,
            # A shared page's own grants open it outside its space; STRICT keeps them from
            # opening its sub-pages too. _apply_page_access sets the access of a written page.
            access_rule=AccessRule.STRICT,
            parent_external_record_id=(
                page_external_id(parent_page_id) if parent_page_id is not None else None
            ),
            parent_record_type=RecordType.WEBPAGE if parent_page_id is not None else None,
        )
        if auto_index_off:
            record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
        return record

    def _build_attachment_record(
        self,
        attachment: dict[str, Any],
        page: dict[str, Any],
        *,
        parent_node_id: str | None = None,
        auto_index_off: bool = False,
    ) -> FileRecord:
        """``AttachmentExcerptDTO`` -> FileRecord hanging off its page.

        ``parent_record_type=WEBPAGE`` makes the processor write an ATTACHMENT edge; the
        record still inherits its permissions from the page's home space.
        """
        attachment_id = attachment.get("id")
        file_name = (
            attachment.get("fileName")
            or attachment.get("name")
            or f"attachment-{attachment_id}"
        )
        last_modified = attachment.get("lastModified")
        extension = _attachment_extension(attachment)
        mime_type, _ = mimetypes.guess_type(file_name)
        record = FileRecord(
            org_id=self.data_entities_processor.org_id,
            record_name=attachment.get("name") or file_name,
            record_type=RecordType.FILE,
            external_record_id=attachment_external_id(attachment_id),
            external_revision_id=str(last_modified) if last_modified is not None else None,
            external_record_group_id=str(page.get("homeSpace")),
            record_group_type=RecordGroupType.DRUPAL_WIKI_SPACE,
            parent_external_record_id=page_external_id(page.get("id")),
            parent_record_type=RecordType.WEBPAGE,
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.DRUPAL_WIKI,
            connector_id=self.connector_id,
            mime_type=mime_type or MimeTypes.UNKNOWN.value,
            size_in_bytes=attachment.get("fileSize"),
            weburl=self._page_web_url(page.get("id")),
            source_updated_at=_epoch_millis(last_modified),
            inherit_permissions=True,
            is_file=True,
            extension=extension,
            is_dependent_node=True,
            parent_node_id=parent_node_id,
        )
        if auto_index_off:
            record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
        return record

    def _page_web_url(self, page_id: int | str) -> str:
        """Permalink format used by the wiki itself, e.g. ``https://wiki/node/733``."""
        return f"{self.base_url.rstrip('/')}/node/{page_id}"

    # ------------------------------------------------------------------
    # Page hierarchy
    # ------------------------------------------------------------------

    async def _fetch_space_tree(self, space_id: int, space_name: str) -> dict[int, int] | None:
        """``{page id: parent page id}`` for one space, or None when it is unknown.

        None and ``{}`` mean different things: ``{}`` is a flat space, while None means
        the tree could not be read. Re-saving a page with no parent deletes its
        PARENT_CHILD edge, so an unknown tree keeps whatever is already stored.
        """
        try:
            nodes = await self._graphql_read(self.graphql.get_space_tree(space_id))
        except DrupalWikiGraphQLRefusedError as e:
            self.logger.warning("Drupal Wiki: page structure of '%s': %s", space_name, e)
            self._report_space_problem(TREE_UNREADABLE, f"'{space_name}'")
            return None
        except DrupalWikiGraphQLError as e:
            # Not a lasting refusal, so an error every run rather than a warning sent once.
            self._fail(
                f"Could not read the page structure of the Drupal Wiki space '{space_name}', "
                f"so its pages keep the structure they already have: {e}"
            )
            return None
        # No node carrying a page id means the reply has changed shape, not that the
        # space is flat. Returning {} would re-save every page with no parent and wipe
        # the structure already stored.
        if nodes and not any(node.get("pageId") is not None for node in nodes):
            self.logger.warning(
                "Drupal Wiki: page structure of '%s' has no page ids", space_name
            )
            self._report_space_problem(TREE_UNREADABLE, f"'{space_name}'")
            return None
        return self._extract_parent_page_ids(nodes)

    @staticmethod
    def _extract_parent_page_ids(tree_nodes: list[dict[str, Any]]) -> dict[int, int]:
        """``spaceContentStructureFlatTree`` -> ``{page id: parent page id}``.

        The tree is keyed by node id, not page id, so parents are resolved through the
        node table first. Nodes whose parent is the space root are left out.
        """
        page_by_node: dict[str, int] = {}
        parent_node_by_node: dict[str, str] = {}
        for node in tree_nodes:
            node_id = node.get("id")
            page_id = node.get("pageId")
            if node_id is None or page_id is None:
                continue
            try:
                page_by_node[str(node_id)] = int(page_id)
            except (TypeError, ValueError):
                continue
            parent_id = node.get("parentId")
            if parent_id is not None:
                parent_node_by_node[str(node_id)] = str(parent_id)

        parents: dict[int, int] = {}
        for node_id, page_id in page_by_node.items():
            parent_node = parent_node_by_node.get(node_id)
            if parent_node is None:
                continue
            parent_page = page_by_node.get(parent_node)
            if parent_page is not None and parent_page != page_id:
                parents[page_id] = parent_page
        return parents

    # ------------------------------------------------------------------
    # Writing records
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Streaming and reindex
    # ------------------------------------------------------------------

    async def stream_record(
        self,
        record: Record,
        user_id: str | None = None,
        convertTo: str | None = None,
    ) -> StreamingResponse:
        if not self.data_source:
            raise connector_not_ready(connector=self.display_name)

        try:
            if record.record_type == RecordType.FILE:
                return await self._stream_attachment(record)
            if record.record_type == RecordType.WEBPAGE:
                return await self._stream_page(record)
            # The connector only ever creates these two; anything else reaching here
            # is a caller error, not a source problem.
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail=f"Unsupported record type for streaming: {record.record_type}",
            )
        except HTTPException:
            raise
        except Exception as e:
            raise to_stream_error(e, connector=self.display_name) from e

    async def _stream_attachment(self, record: Record) -> StreamingResponse:
        """Download an attachment's bytes."""
        attachment_id = source_id_from_external(record.external_record_id, ATTACHMENT_ID_PREFIX)
        if attachment_id is None:
            raise not_found_at_source(connector=self.display_name)
        # Probe first: once bytes start flowing the status line is already sent, so a
        # 401 or 404 during the download could not be reported properly.
        probe = await self.data_source.get_attachment(attachment_id)
        if probe.status >= HttpStatusCode.BAD_REQUEST.value:
            self.logger.warning(
                "Opening Drupal Wiki attachment %s failed: %s", attachment_id, describe_response(probe)
            )
        if probe.status == HttpStatusCode.NOT_FOUND.value:
            raise not_found_at_source(connector=self.display_name)
        if probe.status >= HttpStatusCode.BAD_REQUEST.value:
            raise map_source_status(probe.status, connector=self.display_name)
        return create_stream_record_response(
            self.data_source.download_attachment(attachment_id),
            record.record_name,
            record.mime_type,
        )

    async def _stream_page(self, record: Record) -> StreamingResponse:
        """Render a page as HTML. The body is only fetched here, never at sync time."""
        page_id = source_id_from_external(record.external_record_id, PAGE_ID_PREFIX)
        if page_id is None:
            raise not_found_at_source(connector=self.display_name)
        response = await self.data_source.get_page(page_id)
        if response.status >= HttpStatusCode.BAD_REQUEST.value:
            self.logger.warning(
                "Opening Drupal Wiki page %s failed: %s", page_id, describe_response(response)
            )
        if response.status == HttpStatusCode.NOT_FOUND.value:
            raise not_found_at_source(connector=self.display_name)
        if response.status >= HttpStatusCode.BAD_REQUEST.value:
            raise map_source_status(response.status, connector=self.display_name)
        page = self._safe_json_parse(response, f"reading page {page_id}") or {}
        return StreamingResponse(
            iter([self._render_page_html(page).encode("utf-8")]),
            media_type=MimeTypes.HTML.value,
        )

    @staticmethod
    def _render_page_html(page: dict[str, Any]) -> str:
        """Render a page for indexing: title, body, then tags and categories."""
        title = html.escape(str(page.get("title") or ""))
        body = page.get("body") or ""
        terms = []
        for key, label in (("categories", "Categories"), ("tags", "Tags")):
            names = [
                html.escape(str(term.get("name")))
                for term in (page.get(key) or [])
                if term.get("name")
            ]
            if names:
                terms.append(f"<p><strong>{label}:</strong> {', '.join(names)}</p>")
        return f"<h1>{title}</h1>\n{body}\n{''.join(terms)}"

    async def reindex_records(self, record_results: list[Record]) -> None:
        """Refresh content for records the platform asks to reindex.

        A record the source no longer has is left alone here; the next sync removes it.
        """
        if not self.data_source:
            raise DrupalWikiSyncError("Drupal Wiki data source is not initialized")

        updated: list[tuple[Record, list[Permission]]] = []
        unchanged: list[Record] = []
        for record in record_results:
            try:
                outcome = await self._refresh_record(record)
            except Exception as e:
                # One record must not abort the rest of the batch.
                self.logger.error(
                    "Could not refresh %s from Drupal Wiki, so it keeps the content it "
                    "already has: %s", record.external_record_id, describe_exception(e),
                    exc_info=True,
                )
                continue
            if outcome == "updated":
                updated.append((record, []))
            elif outcome == "unchanged":
                unchanged.append(record)
            # "gone": the next sync removes it

        if updated:
            await self.data_entities_processor.on_new_records(updated)
        if unchanged:
            await self.data_entities_processor.reindex_existing_records(unchanged)

    async def _refresh_record(self, record: Record) -> str:
        """Bring one record up to date with the source, in place.

        Returns ``"updated"`` when the revision moved and the record should be
        republished, ``"unchanged"`` when the content is the same, and ``"gone"``
        when the source no longer serves it.
        """
        if record.record_type == RecordType.FILE:
            source_id = source_id_from_external(record.external_record_id, ATTACHMENT_ID_PREFIX)
            response = await self.data_source.get_attachment(source_id) if source_id else None
        else:
            source_id = source_id_from_external(record.external_record_id, PAGE_ID_PREFIX)
            response = await self.data_source.get_page(source_id) if source_id else None

        if response is None:
            self.logger.warning(
                "Cannot refresh %s from Drupal Wiki: its id is not one this connector "
                "wrote. The copy already in PipesHub is kept.", record.external_record_id,
            )
            return "gone"
        if response.status in (HttpStatusCode.NOT_FOUND.value, HttpStatusCode.FORBIDDEN.value):
            self.logger.warning(
                "Drupal Wiki no longer has %s, or the token can no longer see it (%s). The "
                "copy already in PipesHub is kept.",
                record.external_record_id, describe_response(response),
            )
            return "gone"
        if response.status >= HttpStatusCode.BAD_REQUEST.value:
            self.logger.warning(
                "Refreshing %s from Drupal Wiki failed (%s), so it keeps the content it "
                "already has.", record.external_record_id, describe_response(response),
            )
            return "gone"

        payload = self._safe_json_parse(response, f"refreshing {record.record_name}") or {}
        last_modified = payload.get("lastModified")
        revision = str(last_modified) if last_modified is not None else None
        if revision is None or revision == record.external_revision_id:
            return "unchanged"

        record.external_revision_id = revision
        record.indexing_status = ProgressStatus.NOT_STARTED.value
        if record.record_type == RecordType.WEBPAGE:
            # A page carrying grants of its own (a shared one) inherits nothing: an edge to
            # a shared page above it would hand it that page's readers.
            record.inherit_permissions = record.access_rule != AccessRule.OPEN
        if record.record_type == RecordType.FILE:
            # parent_record_type is not persisted, and without it the processor would
            # write PARENT_CHILD instead of an ATTACHMENT edge.
            record.parent_record_type = RecordType.WEBPAGE
        return "updated"

    # ------------------------------------------------------------------
    # Filters
    # ------------------------------------------------------------------

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: str | None = None,
        cursor: str | None = None,
    ) -> FilterOptionsResponse:
        if filter_key != SyncFilterKey.SPACE_IDS.value:
            return FilterOptionsResponse(
                success=False, options=[], page=page, limit=limit, has_more=False,
                message=f"Unsupported filter: {filter_key}",
            )
        if not self.data_source:
            return FilterOptionsResponse(
                success=False, options=[], page=page, limit=limit, has_more=False,
                message="Drupal Wiki data source is not initialized",
            )

        response = await self.data_source.list_spaces(
            search=search or None, page=max(page - 1, 0), size=limit
        )
        if response.status >= HttpStatusCode.BAD_REQUEST.value:
            self.logger.warning(
                "Listing Drupal Wiki spaces for the filter failed: %s", describe_response(response)
            )
            return FilterOptionsResponse(
                success=False, options=[], page=page, limit=limit, has_more=False,
                message=f"Drupal Wiki could not list spaces: {describe_response(response)}",
            )
        try:
            payload = response.json() or {}
        except ValueError:
            return FilterOptionsResponse(
                success=False, options=[], page=page, limit=limit, has_more=False,
                message="Drupal Wiki sent something other than data. Check that the wiki "
                        "address points at the wiki itself and not at a login or proxy page.",
            )
        options = [
            FilterOption(id=str(space.get("id")), label=space.get("name") or str(space.get("id")))
            for space in payload.get("content") or []
        ]
        return FilterOptionsResponse(
            success=True,
            options=options,
            page=page,
            limit=limit,
            has_more=not payload.get("last", True),
        )

    def _get_space_id_filters(self) -> tuple[set[str], set[str]]:
        """(include, exclude) space ids from the sync filter."""
        space_filter = self.sync_filters.get(SyncFilterKey.SPACE_IDS.value)
        if space_filter is None or space_filter.is_empty():
            return set(), set()
        values = {str(value) for value in (space_filter.get_value() or [])}
        operator = space_filter.get_operator()
        if getattr(operator, "value", operator) == "not_in":
            return set(), values
        return values, set()

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _fail(self, message: str) -> None:
        """Record a partial failure. The phase moves on; the run reports it at the end."""
        self._failures.append(message)
        self.logger.error("Drupal Wiki: %s", message)

    def _report_space_problem(self, problem: SpaceProblem, label: str) -> None:
        """``label`` names the space as the notification shows it, quoted."""
        self._space_problems.setdefault(problem, []).append(label)
        self.logger.warning("Drupal Wiki: %s", problem.message.format(spaces=label))

    def _space_problem_lines(self) -> list[str]:
        return [
            problem.message.format(spaces=_listed(names))
            for problem, names in self._space_problems.items()
        ]

    def _problem_lines(self) -> list[str]:
        """One line per kind of space problem, then each failure."""
        return self._space_problem_lines() + self._failures

    async def _notify_problems(self, problems: list[str]) -> None:
        """A failure is an error every run. Space problems last as long as the wiki refuses
        GraphQL and the card already shows them, so they are a warning sent only when they
        change: new spaces affected, kept access ending or ended."""
        changed = await self._space_problems_changed(self._space_problem_lines())
        if not self._failures and not changed:
            return
        await self.notify(
            type=NotificationType.CONNECTOR_SYNC_ERROR if self._failures else NotificationType.CONNECTOR_WARNING,
            severity=NotificationSeverity.ERROR if self._failures else NotificationSeverity.WARNING,
            title=self._notification_title(f"sync finished with {_plural(len(problems), 'problem')}"),
            message=self._failure_preview(problems),
        )

    async def _space_problems_changed(self, lines: list[str]) -> bool:
        """Whether there are space problems unlike those last reported, remembering these."""
        key = generate_record_sync_point_key("notice", "space_problems", "all")
        digest = hashlib.sha256("\n".join(lines).encode()).hexdigest() if lines else ""
        if (await self._get_checkpoint(self.record_sync_point, key)).get("digest", "") == digest:
            return False
        try:
            await self.record_sync_point.update_sync_point(key, {"digest": digest})
        except Exception as e:
            # Reported again next run, which is better than not at all.
            self.logger.warning("Could not remember the reported Drupal Wiki problems: %s", describe_exception(e))
        return bool(lines)

    @staticmethod
    def _failure_preview(problems: list[str], limit: int = 10) -> str:
        """The problems as a bulleted list, because a run can collect a dozen."""
        preview = "\n".join(f"- {problem}" for problem in problems[:limit])
        extra = len(problems) - limit
        return f"{preview}\n- ...and {extra} more" if extra > 0 else preview

    def _notification_title(self, event: str) -> str:
        """Several wikis can be connected at once, so the instance has to be named."""
        return f"{self.connector_instance_name or 'Drupal Wiki'}: {event}"

    async def _get_checkpoint(self, sync_point: SyncPoint, key: str) -> dict[str, Any]:
        """An unreadable checkpoint means "start over", never "fail the run"."""
        try:
            return await sync_point.read_sync_point(key)
        except Exception as e:
            # Worth a warning: the caller carries on with no cursor, which quietly
            # turns an incremental run into a full one.
            self.logger.warning(
                "Could not read where the last Drupal Wiki sync stopped (%s), so everything "
                "will be synced again this time: %s", key, describe_exception(e)
            )
            return {}

    def _require_ok(self, response: HTTPResponse, context: str) -> object:
        if response.status == HttpStatusCode.UNAUTHORIZED.value:
            raise DrupalWikiAuthError(
                f"Drupal Wiki rejected the personal access token while {context}: "
                f"{describe_response(response)}. It may have been revoked or its user blocked."
            )
        if response.status >= HttpStatusCode.BAD_REQUEST.value:
            raise DrupalWikiSyncError(
                f"Drupal Wiki refused {context}: {describe_response(response)}"
            )
        return self._safe_json_parse(response, context)

    @staticmethod
    def _safe_json_parse(response: HTTPResponse, context: str) -> object:
        """Parse a JSON body, turning a non-JSON reply into a readable sync error.

        A misconfigured instance can answer 200 with an SSO login page, and a 204 has
        no body at all.
        """
        try:
            return response.json()
        except ValueError as e:
            raise DrupalWikiSyncError(
                f"Drupal Wiki sent something other than data while {context} "
                f"({describe_response(response)}). Check that the wiki address points at the "
                "wiki itself and not at a login or proxy page."
            ) from e

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
        data_entities_processor: DataSourceEntitiesProcessor,
        **kwargs: object,
    ) -> "BaseConnector":
        return DrupalWikiConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
