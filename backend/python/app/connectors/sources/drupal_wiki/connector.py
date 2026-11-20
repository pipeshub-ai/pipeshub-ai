"""Drupal Wiki connector.

Sync model, with the vendor's words where it documents a rule (all read 2026-11-14):

* spaces become record groups and carry every permission; pages and attachments
  inherit from their home space (https://help.drupal-wiki.com/node/226: "Aus diesem
  Space werden die Zugriffsrechte auf den Inhalt abgeleitet.")
* only content spaces are synced (``SYNCED_SPACE_TYPES``): not the trash, a personal
  workspace, or the system and news-draft spaces, whose readers the API cannot name
* an open space maps to one org-wide read (https://help.drupal-wiki.com/node/363:
  "'Offen' bedeutet, dass alle angemeldeten Nutzer des Wikis grundsätzlich Lesezugriff
  auf den Space haben."); a protected one is granted to its members, its enabled people
  and its groups, as the GraphQL member query names them (https://help.drupal-wiki.com/node/226:
  "Geschlossen: Der Space ist explizit nur für Mitglieder des Space sichtbar"); see
  ``_protected_space_permissions`` for where the vendor's pages disagree
* each run lists every selected space in full and writes the pages changed since the
  space's checkpoint, the wiki's own ``lastModified`` (spec: "The unix timestamp when the
  page was last updated (or created)", silent on shares and moves), or not stored there
  yet; a page the run could not build holds the checkpoint back, and one whose attachments
  could not be listed or whose save failed is written again on the next run, whatever its date
* each space carries its own checkpoint, so a space that fails is written in full once
  it is reachable again
* a page shared into other spaces (spec, ``GET /page`` ``space``: "Filter for pages that
  are either located in the specified space or shared to it") may be seen by the
  members of those spaces (https://help.drupal-wiki.com/node/461); the page says nothing
  about sub-pages, so pages are ``STRICT`` and a share reaches only the shared page and
  its attachments (fail closed)
* a stored attachment its page's complete attachment listing no longer has goes once
  ``GET /attachment/{id}`` answers 404, whenever that page is written (every page is on a
  full sync). The spec offers no listing of deletions (``GET /attachment`` takes one
  ``pageId``, and ``modifiedAfter`` only narrows it to changes) and does not say that
  deleting an attachment moves the page's ``lastModified``, so the attachments of pages left
  unwritten are listed too, ``ATTACHMENT_RECHECKS_PER_RUN`` a run in turn; one listed but
  not stored writes its page again
* a stored page its space no longer lists goes on a 404 or once it is missing from a
  second complete listing (https://help.drupal-wiki.com/node/605: a list of the pages
  seen is how deletions are found); a space the listing no longer returns goes with its
  pages (https://help.drupal-wiki.com/node/505)
* a person the directory does not show enabled (disabled, removed, or now under
  another address) loses the connector (https://help.drupal-wiki.com/node/248: blocked
  users "können sich nicht mehr im Wiki anmelden")
* a token the wiki rejects (a 401; a 403, or a 400 that is not the API's JSON, when listing
  spaces is refused too) fails the run with an auth notification; a rejected read is never
  taken as empty, so it removes nothing (a removal already decided on a read the wiki
  answered stands). A 403 while the token still lists spaces is one item its user may not
  read (https://help.drupal-wiki.com/node/605: the token acts as its user, who "muss zum
  Beispiel die entsprechenden Rechte haben, um Seiten zu lesen"): that item alone fails, and
  a page no space lists or a person the directory left out that answers it is gone
* a wiki that refuses the GraphQL page structure query (Drupal Wiki 8.44 refuses it for
  tokens) syncs pages without their tree, and the admin is told on the connector card
"""

import asyncio
import hashlib
import html
import json
import mimetypes
from collections.abc import AsyncGenerator, Awaitable
from dataclasses import dataclass, field
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
# Attachment listings of unwritten pages per run, connector-wide. The vendor may cut the
# request rate (https://help.drupal-wiki.com/node/605, "Rate Limits"), so this stays small.
ATTACHMENT_RECHECKS_PER_RUN = 100
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


class DrupalWikiRefusedItemError(DrupalWikiSyncError):
    """The wiki refused one item while the token still reads the wiki: the token's user may
    not read it (moved to a space they cannot read, or into the trash)."""


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
    # spaces whose listing had a row without an id or home space: not listed in full
    unidentified: set[int] = field(default_factory=set)
    # space -> page id -> ids of the records stored for it (the page and its attachments)
    # when the space's listing began
    stored: dict[int, dict[int, list[str]]] = field(default_factory=dict)
    # pages stored with grants of their own (written shared): the graph's record of shares
    stored_shared: set[int] = field(default_factory=set)
    # page -> external ids of its stored attachments, from the same read as ``stored``
    stored_attachments: dict[int, set[str]] = field(default_factory=dict)
    # every attachment listed this run
    attachments_listed: set[str] = field(default_factory=set)
    # pages saved this run, and the grants digest each page record was saved with
    written: set[int] = field(default_factory=set)
    written_as: dict[int, str] = field(default_factory=dict)
    # pages whose write is unfinished (attachments not listed, or the save failed): written
    # again whatever their date, this run's and the earlier runs'
    incomplete: set[int] = field(default_factory=set)
    owed_before: set[int] = field(default_factory=set)
    # pages the removal pass found gone this run
    removed: set[int] = field(default_factory=set)
    # page -> spaces that list it as shared into them
    shared: dict[int, set[int]] = field(default_factory=dict)
    # pages written in the form they are stored in (``_share_unconfirmed``), not ``STRICT``
    kept: set[int] = field(default_factory=set)
    # spaces each of whose pages has been written ``STRICT`` (it has a checkpoint)
    settled: set[int] = field(default_factory=set)
    # what each shared page was written with on the runs before: page id -> spaces, grants, and
    # the open spaces that gave the grant
    shares_before: dict[str, dict[str, Any]] = field(default_factory=dict)


TREE_UNREADABLE = SpaceProblem(
    "Could not read the page structure of {spaces}, so their pages keep the structure "
    "they already have and new pages are placed at the top of their space.",
    incomplete=False,
)
# The connection test's GraphQL check only adds a warning, so it gets one try and this long
# in all, rather than the sync's retries and timeouts.
GRAPHQL_PROBE_TIMEOUT_S = 10.0

MEMBERS_REFUSED = SpaceProblem(
    "Drupal Wiki refused to list the members of the protected spaces {spaces}, so their pages "
    "are synced but nobody can see them in PipesHub until it lists them.",
    incomplete=False,
)

# The space types whose readers the vendor documents: content spaces, open or protected.
# Not the trash (https://help.drupal-wiki.com/node/460: "Auf den Papierkorb haben Nutzer mit
# den Systemrollen 'System-Administrator' und 'Space & Content Supervisor' Zugriff."), not a
# personal workspace (https://help.drupal-wiki.com/node/226: "Jeder Benutzer hat einen
# persönlichen Arbeitsplatz, auf den kein anderer Benutzer Zugriff hat."), whose owner the
# API does not name, and not the system or news-draft spaces, whose readers no page names.
SYNCED_SPACE_TYPES = frozenset({"BASIC", "CONTAINER", "CORPORATEBLOG", "CORPORATENEWS"})

PAGE_STRUCTURE_REFUSED_ACTION_CODE = "drupal_wiki_page_structure_refused"
PAGE_STRUCTURE_REFUSED_TITLE = "Drupal Wiki refuses the member and page structure queries"
PAGE_STRUCTURE_REFUSED_MESSAGE = (
    "Pages are synced without their place in the page tree: they keep the parent they "
    "already have, and new ones sit at the top of their space. Nobody can see the pages of "
    "a protected space whose members it refuses to list; open spaces are not affected."
)


def page_structure_refused_action_item() -> ActionItem:
    return ActionItem(
        code=PAGE_STRUCTURE_REFUSED_ACTION_CODE,
        title=PAGE_STRUCTURE_REFUSED_TITLE,
        message=PAGE_STRUCTURE_REFUSED_MESSAGE,
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


def _more_windows(payload: dict[str, Any], content: list[Any], what: str) -> bool:
    """Whether a Spring envelope says another window follows. An absent ``last`` stops rather
    than loops; an empty window that says it is not the last leaves the listing unfinished,
    and an unfinished listing is never read as complete."""
    if payload.get("last", True):
        return False
    if not content:
        raise DrupalWikiSyncError(f"Drupal Wiki answered {what} with an empty window that is not the last")
    return True


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
        # Access status of each synced space, from this run's listing.
        self._space_status: dict[int, str] = {}
        # Whether that listing identified every row, and the ids it named: with a row without
        # an id, a space it did not name is unknown, not gone.
        self._space_listing_complete = True
        self._spaces_named: set[int] = set()
        # Grants each space synced this run was written with; spaces left as they are because
        # their members could not be read this run.
        self._space_grants: dict[int, list[Permission]] = {}
        self._spaces_kept: set[int] = set()
        # Removal candidates this run left unchecked: a full sync must not unlink them.
        self._removals_unchecked = False
        # Emails of the wiki's enabled users this run; None when the directory was not read
        # in full.
        self._enabled_emails: set[str] | None = None
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
        """Ask the page structure query about one space, so a refusal shows at setup.

        A refusal does not fail the test: pages still sync, without their tree. Neither
        does a wiki too slow to answer in time; the first sync checks again.
        """
        self._graphql_refused = None
        probe, probe_rest = self._graphql_probe()
        try:
            async with asyncio.timeout(GRAPHQL_PROBE_TIMEOUT_S):
                await self._graphql_read(probe.get_space_tree(space_id))
        except DrupalWikiGraphQLError as e:
            self.logger.warning("Drupal Wiki connection test: %s", e)
        except TimeoutError:
            self.logger.warning(
                "Drupal Wiki connection test: could not check the page structure query within "
                "%.0f s; the first sync checks it.", GRAPHQL_PROBE_TIMEOUT_S,
            )
        finally:
            await probe_rest.close()
        if self._graphql_refused:
            self.logger.warning(
                "Drupal Wiki connection test: %s. %s", PAGE_STRUCTURE_REFUSED_TITLE, PAGE_STRUCTURE_REFUSED_MESSAGE
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
        return [page_structure_refused_action_item()]

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
            self._space_status = {}
            self._space_listing_complete = True
            self._spaces_named = set()
            self._space_grants = {}
            self._spaces_kept = set()
            self._removals_unchecked = False
            self._enabled_emails = None

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
            and not self._removals_unchecked
            and not any(problem.incomplete for problem in self._space_problems)
        )

    # ------------------------------------------------------------------
    # Users and groups
    # ------------------------------------------------------------------

    async def _sync_users(self) -> dict[str, AppUser]:
        """Active users are refreshed in full every run; emails are the identity link.

        Disabled accounts are listed but not written: they cannot open the wiki
        (https://help.drupal-wiki.com/node/248: blocked users "können sich nicht mehr im
        Wiki anmelden"), so a record for them would grant nothing.

        Each page is written as it arrives, so only one page of new objects is held at
        a time. The directory itself is still returned whole because group membership
        can name any user.
        """
        users_by_id: dict[str, AppUser] = {}
        enabled_emails: set[str] = set()
        skipped_without_email = 0
        page = 0
        while True:
            payload = await self._require_ok(
                await self.data_source.list_users(only_active=False, page=page, size=PAGE_SIZE),
                "reading the list of people",
            )
            content = payload.get("content") or []
            app_users: list[AppUser] = []
            for user in content:
                email = (user.get("email") or "").strip()
                if email and (user.get("status") or "").upper() == USER_STATUS_ENABLED:
                    # UserDTO.id may be null; the person can still open the wiki.
                    enabled_emails.add(email.lower())
                user_id = user.get("id")
                if user_id is None:
                    # Group membership resolves users by this id, so one without it
                    # would be stored under "None" and match every other id-less user.
                    self.logger.warning(
                        "Ignoring a Drupal Wiki user that arrived without an id"
                    )
                    continue
                if (user.get("status") or "").upper() == USER_STATUS_DISABLED:
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
            if not _more_windows(payload, content, "the list of people"):
                break
            page += 1

        self._enabled_emails = enabled_emails
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
        to, so without this they would keep reading it. Their own grants go with it: a group
        whose members could not be read, or that the wiki deleted, keeps its membership, and
        a page this run does not write keeps what an earlier sync gave them, which would come
        back with the link. The next run that lists them enabled links them again, with only
        what the wiki shows them then.
        """
        enabled = self._enabled_emails
        if not enabled:
            # A directory with nobody enabled in it (the token's own user is) is a misread.
            return
        stored = await self.data_entities_processor.get_all_app_users(self.connector_id, raise_on_error=True)
        gone = [
            u for u in stored
            if u.source_user_id and (u.email or "").lower() not in enabled and not await self._still_enabled(u)
        ]
        if gone:
            await self.data_entities_processor.withdraw_app_user_access(self.connector_id, gone, drop_grants=True)
            self.logger.info("Withdrew Drupal Wiki from %d person(s) it no longer shows enabled", len(gone))

    async def _still_enabled(self, user: AppUser) -> bool:
        """Whether the wiki's own record of a person the directory left out still shows them
        enabled under the stored address. The directory pages by offset, so a removal between
        two windows can shift a person out of both. A lookup that fails keeps them; one the
        wiki refuses while the token still reads it does not: left out of the directory and
        not readable, they are not shown enabled (fail closed)."""
        user_id = _as_int(user.source_user_id)
        try:
            if user_id is None:
                raise DrupalWikiSyncError("its stored id is not a number")
            response = await self.data_source.get_user(user_id)
            if response.status == HttpStatusCode.NOT_FOUND.value:
                return False
            record = await self._require_ok(response, f"reading user {user_id}")
        except DrupalWikiAuthError:
            raise
        except DrupalWikiRefusedItemError as e:
            self.logger.info("Drupal Wiki: %s; user %s counts as not enabled", e, user.source_user_id)
            return False
        except Exception as e:
            self._fail(
                f"Could not check whether the Drupal Wiki user {user.source_user_id} is still enabled, so they "
                f"keep access: {describe_exception(e)}"
            )
            return True
        return (
            isinstance(record, dict)
            and (record.get("status") or "").upper() == USER_STATUS_ENABLED
            and (record.get("email") or "").strip().lower() == (user.email or "").lower()
        )

    async def _sync_groups(self, users_by_id: dict[str, AppUser]) -> None:
        """Sync every group with its members.

        Membership is replaced wholesale, so a member removed in the wiki loses access
        on the next run. Groups deleted in the wiki are left in place, as Jira's
        connector does: their grants disappear anyway when space permissions are
        rewritten, so a leftover group node grants nothing.
        """
        synced = 0
        page = 0
        while True:
            payload = await self._require_ok(
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
                    detail = await self._require_ok(
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
                    continue

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
            if not _more_windows(payload, content, "the list of groups"):
                break
            page += 1

        self.logger.info("Synced %d Drupal Wiki group(s)", synced)

    # ------------------------------------------------------------------
    # Spaces -> record groups and their permissions
    # ------------------------------------------------------------------

    async def _sync_spaces(self) -> dict[int, dict[str, Any]]:
        """Content spaces become record groups; their permissions are replaced every run.

        Only the space types whose readers the vendor documents are synced
        (``SYNCED_SPACE_TYPES``). An open space is read by everyone; a protected one by its
        members (``_protected_space_permissions``). Every synced space's grants are written
        each run, which takes back what an earlier sync granted. A protected space whose
        members this run could not read is left as it is, pages included. A stored space
        this run does not sync (gone, of a type no longer synced, or left out by the space
        filter) is removed with its pages, so no grant it carries is left behind.
        """
        allowed_ids, exclude_ids = self._get_space_id_filters()
        spaces: dict[int, dict[str, Any]] = {}
        named: set[str] = set()
        unidentified = False

        page = 0
        while True:
            payload = await self._require_ok(
                await self.data_source.list_spaces(page=page, size=PAGE_SIZE), "reading the list of spaces"
            )
            content = payload.get("content") or []
            groups_with_permissions: list[tuple[RecordGroup, list[Permission]]] = []
            for space in content:
                space_id = _as_int(space.get("id"))
                space_name = space.get("name") or f"space {space.get('id')}"
                if space_id is None:
                    # SpaceExcerptDTO.id may be null: the row may be a stored space.
                    self.logger.warning(
                        "Ignoring a Drupal Wiki space that arrived without a usable id"
                    )
                    unidentified = True
                    continue
                named.add(str(space_id))
                if (space.get("type") or "").upper() not in SYNCED_SPACE_TYPES:
                    continue
                if allowed_ids and str(space_id) not in allowed_ids:
                    continue
                if exclude_ids and str(space_id) in exclude_ids:
                    continue
                access_status = (space.get("accessStatus") or "").upper()
                if access_status in OPEN_SPACE_ACCESS_STATUSES:
                    permissions = [Permission(
                        external_id=None, email=None, type=PermissionType.READ, entity_type=EntityType.ORG,
                    )]
                else:
                    permissions = await self._protected_space_permissions(space_id, space_name)
                    if permissions is None:
                        self._spaces_kept.add(space_id)
                        continue
                self._space_status[space_id] = access_status
                self._space_grants[space_id] = permissions
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
            if not _more_windows(payload, content, "the list of spaces"):
                break
            page += 1

        complete = bool(named) and not unidentified
        self._space_listing_complete, self._spaces_named = complete, {int(s) for s in named}
        if not complete:
            # A listing with no space at all, or a row without an id, may leave out a stored
            # space: only the spaces it names as not synced go, and the full sync's unlink
            # after the run is held back.
            self._fail(
                "Drupal Wiki listed a space without an id, or no space at all, so the spaces it "
                "did not name were kept"
            )
        await self._run_phase(
            "removed spaces",
            self._remove_spaces_gone({str(s) for s in spaces.keys() | self._spaces_kept}, None if complete else named),
        )
        self.logger.info("Synced %d Drupal Wiki space(s)", len(spaces))
        return spaces

    async def _remove_spaces_gone(self, synced: set[str], named: set[str] | None = None) -> None:
        """Remove each stored space this run did not sync, with its pages: deleting a space
        deletes everything in it (https://help.drupal-wiki.com/node/505: "Bei der Löschung
        eines Spaces werden der und alle enthaltenen Inhalte unwiederbringlich gelöscht!").
        A space the token can no longer see, of a type no longer synced (the trash, a
        personal space), or left out by the space filter goes too: its stored grants were
        written under rules that may no longer hold. With ``named``, only a space the listing
        named goes.
        """
        stored = await self.data_entities_processor.get_nodes_by_filters(
            CollectionNames.RECORD_GROUPS.value, {"connectorId": self.connector_id},
            return_fields=["externalGroupId"], raise_on_error=True,
        )
        for group in stored:
            external_id = str(group.get("externalGroupId") or "")
            if not external_id or external_id in synced or (named is not None and external_id not in named):
                continue
            self.logger.info("Drupal Wiki no longer lists the space %s as one to sync; removing it", external_id)
            # False also for a group already gone; a failure leaves it for the next run.
            if not await self.data_entities_processor.on_record_group_deleted(external_id, self.connector_id) and \
                    await self.data_entities_processor.get_record_group_by_external_id(self.connector_id, external_id):
                self._fail(f"Could not remove the Drupal Wiki space {external_id}, which the wiki no longer lists")

    async def _protected_space_permissions(self, space_id: int, space_name: str) -> list[Permission] | None:
        """A protected space's members, as the GraphQL member query names them: each enabled
        person (by email) and each group. None when they cannot be read this run (the people
        directory or the query failed): the space is left as it is. A wiki that refuses the
        query gets no grants (fail closed), and the admin is told.

        shortcut: the vendor's pages disagree on who reads a protected space. Node 226 says
        members ("Geschlossen: Der Space ist explizit nur für Mitglieder des Space sichtbar";
        "Fügt man einen Benutzer zu einem Space hinzu, hat er zunächst nur Leserechte"), node
        363 says the Reader role ("Steht der Space-Status auf 'Geschützt', haben nur Nutzer und
        Gruppen mit der Rolle 'Leser' Zugriff auf den Space"), and the member query names every
        role holder without their roles. So a member whose roles leave out Reader is granted
        read here. Narrow it to Reader holders once a documented API names them.
        """
        if self._enabled_emails is None:
            self._fail(
                f"Could not read the list of people from Drupal Wiki, so the protected space "
                f"'{space_name}' was left as it is this time"
            )
            return None
        try:
            members = await self._graphql_read(self.graphql.get_space_members(space_id))
        except DrupalWikiGraphQLRefusedError:
            self._report_space_problem(MEMBERS_REFUSED, f"'{space_name}'")
            return []
        except DrupalWikiGraphQLError as e:
            self._fail(
                f"Could not read who has access to the Drupal Wiki space '{space_name}', so it was "
                f"left as it is this time: {e}"
            )
            return None
        users, groups = (members or {}).get("users"), (members or {}).get("groups")
        if not isinstance(users, list) or not isinstance(groups, list):
            self._fail(f"Drupal Wiki answered the members of the space '{space_name}' without their lists, "
                       "so it was left as it is this time")
            return None
        grants: dict[tuple[str, str], Permission] = {}
        for user in users:
            email = ((user or {}).get("email") or "").strip().lower()
            if email in self._enabled_emails:
                grants.setdefault(("user", email), Permission(
                    external_id=None, email=email, type=PermissionType.READ, entity_type=EntityType.USER,
                ))
        for group in groups:
            group_id = _as_int((group or {}).get("id"))
            if group_id is not None:
                grants.setdefault(("group", str(group_id)), Permission(
                    external_id=str(group_id), email=None, type=PermissionType.READ, entity_type=EntityType.GROUP,
                ))
        return list(grants.values())

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
        listing = PageListing(
            shares_before=json.loads(
                (await self.record_sync_point.read_sync_point(self._shares_sync_point_key())).get("pages") or "{}"
            ),
            # Read like the shares: forgotten, a page below its checkpoint would never be finished.
            owed_before={
                int(p) for p in (await self.record_sync_point.read_sync_point(self._owed_sync_point_key())).get("pages")
                or []
            },
        )
        try:
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
            await self._run_phase("attachments of unchanged pages", self._recheck_attachments(listing))
        finally:
            try:
                await self.record_sync_point.update_sync_point(
                    self._owed_sync_point_key(), {"pages": sorted(self._pages_owed(listing))}
                )
            except Exception as e:
                # Reported, not raised: raised here it would replace a rejected token unwinding.
                self._fail(f"Could not save which Drupal Wiki pages are still owed a write: {describe_exception(e)}")

    @staticmethod
    def _owed_sync_point_key() -> str:
        return generate_record_sync_point_key("owed", "space", "all")

    @staticmethod
    def _pages_owed(listing: PageListing) -> set[int]:
        """This run's unfinished pages, and the earlier runs' not written or removed yet: one a
        listing skipped and a lookup could not confirm gone is still owed."""
        return listing.incomplete | (listing.owed_before - listing.written - listing.removed)

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
        stored = await self._stored_pages(space_id, listing)
        if modified_after is not None:
            # Every page was written STRICT by the clean pass that set the checkpoint.
            listing.settled.add(space_id)
        home: set[int] = set()
        batch: list[tuple[Record, list[Permission]]] = []
        batch_pages: list[int] = []
        synced = 0
        newest_seen: int | None = None

        parents = await self._fetch_space_tree(space_id, space_name)
        async for page in self._fetch_pages(space_id, listing):
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
                page_id in stored and modified_after is not None and page_id not in listing.owed_before
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
            batch_pages.append(page_id)
            synced += 1
            if len(batch) >= RECORD_BATCH_SIZE:
                await self._save_pages(batch, batch_pages, listing)
                batch, batch_pages = [], []

        await self._save_pages(batch, batch_pages, listing)
        if space_id in listing.unidentified:
            self._fail(
                f"Drupal Wiki listed a page of the space '{space_name}' without an id or home space, so "
                "nothing is removed from it this run"
            )
            return
        listing.home[space_id], listing.stored[space_id] = home, stored
        self.logger.info(
            "Synced %d changed page(s) in the Drupal Wiki space '%s'", synced, space_name
        )

        # A page whose attachments could not be listed is incomplete, so the checkpoint
        # must not move past it.
        if len(self._failures) != failures_before or newest_seen is None:
            return
        if modified_after is None and (stored.keys() - home or home & listing.kept):
            # A first pass settles the space only once every stored page has been written
            # STRICT: one the listing left out (skipped by the offset paging, or gone), or one
            # kept as stored, may still inherit from the page above it the way pages did
            # before, and would read through that page's grants.
            self.logger.info(
                "Drupal Wiki: %d stored page(s) of '%s' were not listed or not written STRICT; its "
                "pages are written in full again next time",
                len(stored.keys() - home | home & listing.kept), space_name,
            )
            return
        listing.settled.add(space_id)
        # A page stamped in the same second as the newest one but saved just after the
        # listing read it would never count as changed again. Standing one second back
        # costs writing that second's pages once more.
        await self.record_sync_point.update_sync_point(key, {"last_modified": newest_seen - 1})

    async def _save_pages(
        self, batch: list[tuple[Record, list[Permission]]], pages: list[int], listing: PageListing
    ) -> None:
        """Save one batch; only then do its pages count as written this run. A batch that
        fails may be saved in part, so the share and removal passes go by what is stored."""
        try:
            if batch:
                await self.data_entities_processor.on_new_records(batch)
        except Exception:
            for page_id in pages:
                listing.written_as.pop(page_id, None)
            listing.incomplete.update(pages)
            raise
        listing.written.update(pages)

    @staticmethod
    def _space_sync_point_key(space_id: int) -> str:
        # Not "space": that checkpoint dates from when a page inherited from the page above
        # it. Without one, every page is written once more and takes its space's readers.
        return generate_record_sync_point_key(RecordType.WEBPAGE.value, "space_pages", str(space_id))

    async def _stored_pages(self, space_id: int, listing: PageListing) -> dict[int, list[str]]:
        """``{page id: ids of its stored records}`` for the pages stored under a space: the
        page's own record and those of its attachments. Pages stored with grants of their
        own (``OPEN``: written shared) are noted in ``listing.stored_shared``, so the share
        pass finds them whatever its memory says."""
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
                    if record.access_rule == AccessRule.OPEN:
                        listing.stored_shared.add(page_id)
                elif (page_id := source_id_from_external(record.parent_external_record_id or "", PAGE_ID_PREFIX)) is not None:
                    attachments.setdefault(page_id, []).append(record.id)
                    listing.stored_attachments.setdefault(page_id, set()).add(record.external_record_id)
            if len(records) < STORED_PAGE_READ_SIZE:
                break
            after = records[-1].id
        for page_id, ids in pages.items():
            ids.extend(attachments.get(page_id, []))
        return pages

    async def _fetch_pages(self, space_id: int, listing: PageListing) -> AsyncGenerator[dict[str, Any], None]:
        """Yield every page of one space: those whose home it is and those shared into it.

        Asking per space keeps the pages of spaces the user did not select off the
        wire entirely, rather than downloading the whole wiki and discarding most of
        it. ``GET /page`` answers with a Spring envelope, so ``last`` ends the walk.
        """
        index = 0
        while True:
            payload = await self._require_ok(
                await self.data_source.list_pages(space_id=space_id, page=index, size=PAGE_SIZE),
                f"reading the pages of space {space_id}",
            )
            content = payload.get("content") or []
            for page in content:
                # An id-less page would be saved as "page:None" and, because the data
                # source drops null query params, would pull every attachment in the
                # wiki onto it. A page with no home space has no group to belong to.
                # PageExcerptDTO.id may be null, and the row may be a stored page, so the
                # space does not count as listed in full.
                if _as_int(page.get("id")) is None or _as_int(page.get("homeSpace")) is None:
                    listing.unidentified.add(space_id)
                    continue
                yield page
            if not _more_windows(payload, content, f"the pages of space {space_id}"):
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
        for page_id in sorted({int(p) for p in before} | set(listing.shared) | listing.stored_shared):
            had = before.get(str(page_id)) or {}
            try:
                record = None
                # The home space that listed it this run, else the one it is stored under.
                home = next((space for space, pages in listing.home.items() if page_id in pages), None)
                if home is None or (page_id not in listing.stored[home] and page_id not in listing.written_as):
                    record = await self._stored_page(page_id)
                    if record is None:
                        continue  # not written yet, or removed; decided when it is
                    home = _as_int(record.external_record_group_id) if home is None else home
                    if record.access_rule == AccessRule.OPEN:
                        listing.stored_shared.add(page_id)
                    else:
                        listing.stored_shared.discard(page_id)
                if self._space_unknown(home):
                    # Its space's pass did not run: the listing may have left it out as a row
                    # without an id. Left as it is.
                    if had:
                        now[str(page_id)] = had
                    continue
                shares, opening, permissions = self._page_access(page_id, home, listing)
                written = _grants_digest(permissions)
                # The stored rule wins over the memory, whose save may have failed: a page stored
                # plain was written plain; one stored with grants of its own that the memory lost,
                # or holds as plain, is compared as unknown, so it is written again.
                before_grants = (had.get("grants") or None) if page_id in listing.stored_shared else ""
                if (
                    permissions is None and page_id not in listing.written_as
                    and self._share_unconfirmed(page_id, home, listing)
                ):
                    self._fail(
                        f"Kept the stored access of the shared Drupal Wiki page {page_id}: an open space "
                        "it may be shared into could not be listed"
                    )
                    if had:
                        now[str(page_id)] = had
                    continue
                if written != listing.written_as.get(page_id, before_grants):
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
                now[str(page_id)] = {"spaces": sorted(shares), "grants": written, "open": sorted(opening) if written else []}
        await self.record_sync_point.update_sync_point(
            self._shares_sync_point_key(), {"pages": json.dumps(now, sort_keys=True)}
        )

    def _page_access(
        self, page_id: int, home: int | None, listing: PageListing
    ) -> tuple[set[int], set[int], list[Permission] | None]:
        """The spaces a page is shared into, as far as this run knows them, the open ones among
        them that give it the org, and the grants it carries of its own: None for a plain page,
        which inherits its home space.

        Every page write takes its access from here, so a later write never undoes a
        share. Members of a space a page is shared into may see it
        (https://help.drupal-wiki.com/node/461: "Die Mitglieder der Ziel-Spaces, die das Recht
        haben entsprechende Inhaltstypen zu sehen, können den geteilten Inhalt dann sehen").
        A page of a protected space shared into an open one is granted to the org; one shared
        only into protected spaces is granted their members and its home space's
        (``_space_grants``); a page of an open space stays plain. A space this run did not
        list (yet, or in full) keeps the shares the page had into it, and so does a space the
        space listing may have left out as a row without an id, or whose members could not be
        read (``_space_unknown``); such a share keeps the org grant the page holds only if it
        was one that gave it (remembered as ``open``), and never adds one. A protected space
        not listed in full adds its members only to a page that already holds grants of its
        own; one whose members could not be read adds none this run. A space no longer synced
        or gone loses them. Until every page of its home space is ``STRICT``, a page gets no
        grants of its own.
        """
        had = listing.shares_before.get(str(page_id)) or {}
        listed = listing.shared.get(page_id, set()) - {home}
        remembered = {_as_int(s) for s in had.get("spaces") or []} - {None, home}
        known = {space for space in remembered if space in self._space_status and space not in listing.home}
        unknown = {space for space in remembered if self._space_unknown(space)}
        shares = known | unknown | listed
        opening = {space for space in listed if self._space_status.get(space) in OPEN_SPACE_ACCESS_STATUSES}
        if had.get("grants") and page_id in listing.stored_shared:
            gave = {_as_int(s) for s in had.get("open") or []}
            opening |= gave & unknown | {
                space for space in gave & known if self._space_status.get(space) in OPEN_SPACE_ACCESS_STATUSES
            }
        if home not in listing.settled or self._space_status.get(home) in OPEN_SPACE_ACCESS_STATUSES:
            return shares, opening, None
        if opening:
            return shares, opening, [
                Permission(external_id=None, email=None, type=PermissionType.READ, entity_type=EntityType.ORG)
            ]
        held = bool(had.get("grants")) and page_id in listing.stored_shared
        protected = sorted(
            space for space in (listed | known if held else listed)
            if space in self._space_grants and self._space_status.get(space) not in OPEN_SPACE_ACCESS_STATUSES
        )
        # Not inheriting, the page carries its home space's members as well as theirs.
        grants: dict[tuple[str, str, str], Permission] = {}
        for space in (home, *protected):
            for grant in self._space_grants.get(space, []):
                grants.setdefault((grant.entity_type.value, grant.email or "", grant.external_id or ""), grant)
        if len(grants) == len({(g.entity_type.value, g.email or "", g.external_id or "") for g in self._space_grants.get(home, [])}):
            return shares, opening, None  # the shares add no reader: plain, inheriting its home space
        return shares, opening, list(grants.values())

    def _space_unknown(self, space: int | None) -> bool:
        """A protected space whose members this run could not read, or a space this run neither
        synced nor saw named while the space listing had a row without an id (it may be that
        row): unknown, not gone."""
        return space in self._spaces_kept or (
            not self._space_listing_complete and space not in self._space_status and space not in self._spaces_named
        )

    def _share_unconfirmed(self, page_id: int, home: int | None, listing: PageListing) -> bool:
        """True for a page stored with grants of its own (``OPEN``) that the share memory does
        not record as shared (a full sync clears it) while an open space other than its home
        is not listed in full yet, or while the space listing had a row without an id: the share
        it was granted for can be neither confirmed nor ruled out, so its stored grants stay."""
        if page_id not in listing.stored_shared or (listing.shares_before.get(str(page_id)) or {}).get("grants"):
            return False
        return not self._space_listing_complete or any(
            status in OPEN_SPACE_ACCESS_STATUSES and space != home and space not in listing.home
            for space, status in self._space_status.items()
        )

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

    async def _current_page(self, page_id: int) -> dict[str, Any] | None:
        """The page as the wiki has it now; None on a 404, the only refusal the spec documents
        for ``GET /page/{id}``, and on a refusal while the token still reads the wiki: the
        token's user may no longer read the page (trashed, or moved to a space they cannot
        read), so the copy stored from where they could is not kept (fail closed). Anything
        else fails the lookup and keeps the page."""
        response = await self.data_source.get_page(page_id)
        if response.status == HttpStatusCode.NOT_FOUND.value:
            return None
        try:
            page = await self._require_ok(response, f"reading page {page_id}")
        except DrupalWikiRefusedItemError as e:
            self.logger.info("Drupal Wiki: %s; the stored copy of page %s goes", e, page_id)
            return None
        if not isinstance(page, dict) or _as_int(page.get("homeSpace")) is None:
            raise DrupalWikiSyncError(f"Drupal Wiki answered page {page_id} without its home space")
        if page.get("id") is not None and _as_int(page.get("id")) != page_id:
            raise DrupalWikiSyncError(f"Drupal Wiki answered page {page_id} with page {page.get('id')}")
        # PageDTO.id may be null; the record id and the attachment query need it.
        return {**page, "id": page_id}

    async def _remove_pages_gone(self, listing: PageListing) -> None:
        """Remove each stored page its space no longer lists: at once when the wiki says it
        is gone, and in any case once it is missing from a second complete listing.

        The vendor's way to see deletions is to compare listings
        (https://help.drupal-wiki.com/node/605: "Wenn Sie eine Liste der
        zugreifbaren/verarbeiteten Seiten ... führen, können Sie zudem ermitteln, welche
        Seiten gelöscht wurden."). The listing pages by offset, so a page can slip between
        two windows: the first time a page is missing it is looked up, removed on a 404
        (the only refusal the spec documents for ``GET /page/{id}``) or written again when
        still in its space. What a trashed page answers is not documented, so a page
        missing twice goes whatever it answers. At most ``GONE_PAGE_CHECKS_PER_RUN`` are
        looked up a run, in page id order from where the last run stopped, so lookups that
        keep failing never hold back the ones after them.
        """
        candidates = sorted(
            (page_id, space_id)
            for space_id, home in listing.home.items()
            for page_id in listing.stored[space_id].keys() - home - listing.written
        )
        key = generate_record_sync_point_key("removals", "space", "all")
        memory = await self._get_checkpoint(self.record_sync_point, key)
        missing_before = {_as_int(p) for p in memory.get("missing") or []}
        for page_id, space_id in candidates:
            if page_id in missing_before:
                try:
                    await self._delete_page_stored_under(page_id, space_id, listing)
                except Exception as e:
                    self._fail(f"Could not remove the Drupal Wiki page {page_id}, which its space no longer lists: "
                               f"{describe_exception(e)}")
        missing = [(page_id, space_id) for page_id, space_id in candidates if page_id not in missing_before]
        after = _as_int(memory.get("after")) or 0
        start = next((i for i, (page_id, _) in enumerate(missing) if page_id > after), 0)
        turn = (missing[start:] + missing[:start])[:GONE_PAGE_CHECKS_PER_RUN]
        try:
            await self._check_pages_gone(turn, listing)
        finally:
            # Saved whatever stopped the lookups (a rejected token too), so the pages missing
            # now are known as missing before, and the next run removes them rather than
            # stopping at the same lookup.
            if len(turn) < len(missing):
                # Their absence is not confirmed, so a full sync must not unlink them.
                self._removals_unchecked = True
                self.logger.info(
                    "Drupal Wiki: %d of %d stored pages no space lists were checked; the rest are "
                    "checked on the next sync", len(turn), len(missing),
                )
            try:
                await self.record_sync_point.update_sync_point(key, {
                    "after": turn[-1][0] if len(turn) < len(missing) else 0,
                    "missing": [page_id for page_id, _ in missing],
                })
            except Exception as e:
                # Reported, not raised: raised here it would replace a rejected token unwinding.
                self._fail(f"Could not save which Drupal Wiki pages no space lists: {describe_exception(e)}")

    async def _check_pages_gone(self, turn: list[tuple[int, int]], listing: PageListing) -> None:
        for page_id, space_id in turn:
            try:
                page = await self._current_page(page_id)
                if page is not None and _as_int(page.get("homeSpace")) == space_id:
                    # Still there: the listing skipped it, or it is in the trash; the next
                    # complete listing decides.
                    if records := await self._build_page_records(page, None, listing):
                        await self.data_entities_processor.on_new_records(records)
                    continue
                await self._delete_page_stored_under(page_id, space_id, listing)
            except DrupalWikiAuthError:
                raise
            except Exception as e:
                self._fail(
                    f"Could not check whether the Drupal Wiki page {page_id} still exists, so "
                    f"it stays until the next sync: {describe_exception(e)}"
                )

    async def _remove_attachments_gone(self, page_id: int, listing: PageListing) -> None:
        """Remove the stored attachments of a page that its complete attachment listing no longer
        has, wherever they are stored (a moved page's may still be under its old space). One
        listed under another page this run (moved) stays."""
        try:
            stored = await self.data_entities_processor.get_records_by_parent(
                self.connector_id, page_external_id(page_id), RecordType.FILE.value
            )
            await self._remove_unlisted_attachments({r.external_record_id for r in stored}, listing)
        except DrupalWikiAuthError:
            raise
        except Exception as e:
            self._fail(
                f"Could not remove the attachments page {page_id} no longer has from Drupal Wiki: "
                f"{describe_exception(e)}"
            )
            listing.incomplete.add(page_id)

    async def _remove_unlisted_attachments(self, stored: set[str], listing: PageListing) -> None:
        """Remove each of ``stored`` no attachment listing had this run, once the wiki answers 404
        for it (not found, or not readable by the token), or refuses it while the token still reads
        the wiki (``DrupalWikiRefusedItemError``: its user may not read it, so the stored copy goes).

        Only that lookup removes one: the attachment listing is a bare array whose walk ends on a
        short page, and the spec does not say that a short page is the last one. The spec gives
        ``GET /attachment/{id}`` a 200 and a 404 only; any other answer keeps the attachment, and a
        rejected token stops the run.
        """
        for external_id in sorted(stored - listing.attachments_listed):
            attachment_id = source_id_from_external(external_id, ATTACHMENT_ID_PREFIX)
            if attachment_id is None:
                continue
            probe = await self.data_source.get_attachment(attachment_id)
            if probe.status != HttpStatusCode.NOT_FOUND.value:
                try:
                    await self._require_ok(probe, f"looking up attachment {attachment_id}")
                    continue
                except DrupalWikiRefusedItemError as e:
                    self.logger.info("Drupal Wiki: %s; the stored copy of attachment %s goes", e, attachment_id)
            await self.data_entities_processor.delete_record_by_external_id(self.connector_id, external_id)

    async def _recheck_attachments(self, listing: PageListing) -> None:
        """List the attachments of pages this run listed but did not write, up to
        ``ATTACHMENT_RECHECKS_PER_RUN``, in page id order from where the last run stopped.

        Deleting an attachment is not documented to change its page, and no listing of deletions
        exists, so an unwritten page is the only place a deleted attachment shows. An attachment
        listed but not stored (one added without a page change, or one removed on a 404 that did
        not last) writes its page again. A recheck that fails is logged and keeps everything: the
        page comes round again, and a full sync writes every page anyway.
        """
        candidates = sorted(
            (page_id, space_id)
            for space_id, home in listing.home.items()
            for page_id in (home & listing.stored[space_id].keys())
            - listing.written - listing.removed - listing.incomplete
        )
        key = generate_record_sync_point_key("attachment_recheck", "space", "all")
        after = _as_int((await self._get_checkpoint(self.record_sync_point, key)).get("after")) or 0
        start = next((i for i, (page_id, _) in enumerate(candidates) if page_id > after), 0)
        turn = (candidates[start:] + candidates[:start])[:ATTACHMENT_RECHECKS_PER_RUN]
        for page_id, space_id in turn:
            try:
                rows: list[dict[str, Any]] = []
                await self._list_page_attachments(page_id, rows)
                listing.attachments_listed |= {attachment_external_id(a["id"]) for a in rows}
                stored = listing.stored_attachments.get(page_id, set())
                if any(
                    attachment_external_id(a["id"]) not in stored
                    for a in rows
                    if extension_passes_filter(self.sync_filters, _attachment_extension(a))
                ):
                    page = await self._current_page(page_id)
                    if (
                        page is not None and _as_int(page.get("homeSpace")) == space_id
                        and (records := await self._build_page_records(page, None, listing))
                    ):
                        await self.data_entities_processor.on_new_records(records)
                    continue
                await self._remove_unlisted_attachments(stored, listing)
            except DrupalWikiAuthError:
                raise
            except Exception as e:
                self.logger.warning(
                    "Drupal Wiki: could not recheck the attachments of page %s, so they stay as they "
                    "are until it comes round again: %s", page_id, describe_exception(e),
                )
        if turn:
            await self.record_sync_point.update_sync_point(
                key, {"after": turn[-1][0] if len(turn) < len(candidates) else 0}
            )

    async def _delete_page_stored_under(self, page_id: int, space_id: int, listing: PageListing) -> None:
        """Delete a page the listing of ``space_id`` no longer has, if it is still stored there.
        A move whose save committed before the batch failed has already put it elsewhere, and
        that copy is the page's current one."""
        record = await self._stored_page(page_id)
        if record is not None and record.external_record_group_id == str(space_id):
            await self.data_entities_processor.delete_record_by_external_id(
                self.connector_id, page_external_id(page_id)
            )
            listing.removed.add(page_id)
        elif record is None:
            listing.removed.add(page_id)

    async def _build_page_records(
        self,
        page: dict[str, Any],
        parents: dict[int, int] | None,
        listing: PageListing,
    ) -> list[tuple[Record, list[Permission]]]:
        """Build the page record, with the access ``_page_access`` decides, plus its
        attachments."""
        page_id = _as_int(page.get("id"))
        pages_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.PAGES.value)
        attachments_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.ATTACHMENTS.value)

        source_attachments: list[dict[str, Any]] = []
        listed_in_full = False
        try:
            await self._list_page_attachments(page_id, source_attachments)
            listed_in_full = True
        except DrupalWikiSyncError as e:
            # The page is still written; it is owed until its attachments are listed, so the
            # next run writes it again even below its space's checkpoint.
            self._fail(str(e))
            listing.incomplete.add(page_id)
        listing.attachments_listed |= {attachment_external_id(a.get("id")) for a in source_attachments}
        if listed_in_full:
            await self._remove_attachments_gone(page_id, listing)

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

        home = _as_int(page.get("homeSpace"))
        _, _, permissions = self._page_access(page_id, home, listing)
        records: list[tuple[Record, list[Permission]]] = []
        if permissions is None and self._share_unconfirmed(page_id, home, listing):
            # Written in the form it is stored in, its grants and inherit edge left as they are
            # (OPEN is also the record default, so it may read only through that edge); the
            # share pass decides it once the open spaces have been listed.
            stored = stored or await self._stored_page(page_id)
            listing.kept.add(page_id)
            record.access_rule = AccessRule.OPEN
            record.inherit_permissions = await self.data_entities_processor.inheritance_when_unreadable(
                CollectionNames.RECORDS.value, stored.id if stored is not None else None
            )
            await self.data_entities_processor.on_record_content_update(record)
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

    async def _list_page_attachments(self, page_id: int, into: list[dict[str, Any]]) -> None:
        """Append a page's attachments to ``into``; DrupalWikiSyncError if the listing is not
        complete, leaving the rows read so far in ``into``.

        ``GET /attachment`` answers with a bare array rather than the envelope every other
        listing uses, so a page shorter than the one asked for ends the walk.
        """
        index = unidentified = 0
        while True:
            batch = await self._require_ok(
                await self.data_source.list_attachments(page_id=page_id, page=index, size=PAGE_SIZE),
                f"reading the attachments of page {page_id}",
            )
            # AttachmentExcerptDTO.id is required; a row without one may be a stored
            # attachment, and would be saved as "attachment:None".
            identified = [a for a in batch if _as_int(a.get("id")) is not None]
            unidentified += len(batch) - len(identified)
            into.extend(identified)
            if len(batch) < PAGE_SIZE:
                break
            index += 1
        if unidentified:
            raise DrupalWikiSyncError(f"Drupal Wiki listed an attachment of page {page_id} without an id")

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

        A record the source no longer has is left alone here: a sync removes a page the wiki no
        longer has with its attachments, and an attachment its page no longer lists once it
        lists that page's attachments again.
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
            # "gone": left alone (see the docstring for what a sync removes)

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
            # Saved with the inherit edge it is stored with: a shared page has none, a plain one
            # (or one OPEN only as the record default) reads through it. A loaded record's
            # inherit_permissions is the model default, so the edge decides.
            record.inherit_permissions = await self.data_entities_processor.inheritance_when_unreadable(
                CollectionNames.RECORDS.value, record.id
            )
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
            # Only a space the sync can sync: chosen alone, any other would sync nothing.
            if _as_int(space.get("id")) is not None and (space.get("type") or "").upper() in SYNCED_SPACE_TYPES
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

    async def _require_ok(self, response: HTTPResponse, context: str) -> object:
        # The spec documents no 401 or 403, and a live wiki answers a token it does not
        # accept with a 403 page, or a 400 page for an unknown one, never a 401. A token of a
        # blocked user gives no access at all (https://help.drupal-wiki.com/node/605: "Token
        # von gesperrten Nutzern ermöglichen keinerlei Zugriff."), but a valid one reads only
        # what its user may read (same page: "Er muss zum Beispiel die entsprechenden Rechte
        # haben, um Seiten zu lesen"), so a 403 can also be one item that user may not read.
        # A 401 fails the run as a rejected token; a 403 or a 400 that is not the API's own
        # JSON does only when the token cannot list spaces either.
        if response.status == HttpStatusCode.UNAUTHORIZED.value:
            raise DrupalWikiAuthError(
                f"Drupal Wiki rejected the personal access token while {context}: "
                f"{describe_response(response)}. It may have been revoked or its user blocked."
            )
        if response.status == HttpStatusCode.FORBIDDEN.value or (
            response.status == HttpStatusCode.BAD_REQUEST.value and not self._is_json(response)
        ):
            token_reads = await self._token_reads_the_wiki()
            if token_reads is None:
                raise DrupalWikiSyncError(
                    f"Drupal Wiki refused {context}: {describe_response(response)}, and the token could "
                    "not be checked"
                )
            if not token_reads:
                raise DrupalWikiAuthError(
                    f"Drupal Wiki rejected the personal access token while {context}: "
                    f"{describe_response(response)}, and it cannot list spaces either. It may have "
                    "been revoked or its user blocked."
                )
            raise DrupalWikiRefusedItemError(
                f"Drupal Wiki refused {context}: {describe_response(response)}. The token still "
                "reads the wiki, so only this was skipped"
            )
        if response.status >= HttpStatusCode.BAD_REQUEST.value:
            raise DrupalWikiSyncError(
                f"Drupal Wiki refused {context}: {describe_response(response)}"
            )
        return self._safe_json_parse(response, context)

    async def _token_reads_the_wiki(self) -> bool | None:
        """Whether the token still lists spaces; None when the check got no answer. ``GET
        /space`` lists the spaces its user may see and documents only 200 and 404 (spec), so
        a valid token gets a page of them, an empty one included. Only a refusal of this call
        too counts as a dead token."""
        try:
            probe = await self.data_source.list_spaces(page=0, size=1)
        except Exception as e:
            self.logger.warning("Drupal Wiki: could not check the token after a refusal: %s", describe_exception(e))
            return None
        if probe.status in (HttpStatusCode.UNAUTHORIZED.value, HttpStatusCode.FORBIDDEN.value) or (
            probe.status == HttpStatusCode.BAD_REQUEST.value and not self._is_json(probe)
        ):
            return False
        return True if probe.status == HttpStatusCode.OK.value and self._is_json(probe) else None

    @staticmethod
    def _is_json(response: HTTPResponse) -> bool:
        try:
            response.json()
        except ValueError:
            return False
        return True

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
