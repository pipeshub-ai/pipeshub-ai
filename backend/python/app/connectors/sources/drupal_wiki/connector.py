"""Drupal Wiki connector.

Sync model (see the design page for the full rationale):

* spaces become record groups and carry every permission; pages and attachments
  inherit from their home space, because Drupal Wiki has no page-level rights
* every space the user selects is synced whatever its type; open spaces
  (``ANONYMOUS``/``AUTHENTICATED``) map to one org-wide read, while private spaces
  need their member list, which only the internal GraphQL API exposes
* pages are listed per space with ``space`` + ``modifiedAfter`` against the wiki's
  own ``lastModified``; a page the run could not build holds the checkpoint back
  rather than being skipped, because there is no second pass behind it
* each space carries its own checkpoint, so a space that fails or is dropped by the
  permission phase is listed in full once it is reachable again
* a page shared into another space is left to its home space: every record belongs
  to exactly one record group, so access always comes from there
* nothing is removed: a page, attachment or space deleted in the wiki stays indexed
"""

import html
import mimetypes
from collections.abc import AsyncGenerator, Awaitable
from logging import Logger
from typing import Any, TypeVar

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.connector_service import (
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
    load_connector_filters,
)
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
from app.sources.client.drupal_wiki.drupal_wiki import DrupalWikiClient
from app.sources.client.drupal_wiki.graphql import DrupalWikiGraphQLClient
from app.sources.client.http.http_response import HTTPResponse
from app.sources.external.drupal_wiki.drupal_wiki import DrupalWikiDataSource
from app.sources.external.drupal_wiki.graphql import (
    DrupalWikiGraphQLDataSource,
    DrupalWikiGraphQLError,
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

# UserDTO.status; the spec enumerates exactly ENABLED and DISABLED.
USER_STATUS_ENABLED = "ENABLED"

# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------
PAGE_SIZE = 100  # the API allows up to 2000; 100 keeps responses small
RECORD_BATCH_SIZE = 100


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


def _plural(count: int, noun: str) -> str:
    """``2 spaces``, ``1 space`` -- notification titles read badly with "space(s)"."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _listed(names: list[str], limit: int = 10) -> str:
    """Name what the message is about, and say how many more there are."""
    shown = ", ".join(names[:limit])
    extra = len(names) - limit
    return f"{shown} and {extra} more" if extra > 0 else shown


def _as_int(value: object) -> int | None:
    """Ids arrive as numbers, strings or nulls depending on the endpoint."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


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
        except ValueError as e:
            raise ConnectorInitError(str(e)) from e
        except Exception as e:
            raise ConnectorInitError(f"Failed to connect to Drupal Wiki: {e}") from e

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

        if response.status == HttpStatusCode.UNAUTHORIZED.value:
            raise ConnectorInitError(
                "Drupal Wiki rejected the personal access token. Check that it is current and its user is not blocked."
            )
        if response.status == HttpStatusCode.FORBIDDEN.value:
            raise ConnectorInitError(
                "The Drupal Wiki token has no access to users. Use a token whose user can read the wiki."
            )
        if response.status >= HttpStatusCode.BAD_REQUEST.value:
            raise ConnectorInitError(f"Drupal Wiki returned status {response.status} for the connection test")

        spaces = await self.data_source.list_spaces(size=1)
        if spaces.status >= HttpStatusCode.BAD_REQUEST.value:
            raise ConnectorInitError(f"Drupal Wiki returned status {spaces.status} when listing spaces")
        return True

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
                await self._run_phase("groups", self._sync_groups(users_by_id))

            spaces = await self._run_phase("spaces", self._sync_spaces())
            if spaces is None:
                self._fail(
                    "Could not read the list of spaces from Drupal Wiki, so no pages were "
                    "synced this time. Existing pages are unchanged."
                )
            else:
                await self._run_phase("pages", self._sync_pages(spaces))

            if self._failures:
                await self.notify(
                    type=NotificationType.CONNECTOR_SYNC_ERROR,
                    severity=NotificationSeverity.ERROR,
                    title=self._notification_title(
                        f"sync finished with {_plural(len(self._failures), 'problem')}"
                    ),
                    message=self._failure_preview(),
                )
            self.logger.info(
                "✅ Drupal Wiki sync finished with %d problem(s)", len(self._failures)
            )
        except Exception as e:
            await self.notify(
                type=NotificationType.CONNECTOR_SYNC_ERROR,
                severity=NotificationSeverity.ERROR,
                title=self._notification_title("sync did not finish"),
                message=f"{str(e)[:300]}\n\nWhat already synced is unchanged.",
            )
            self.logger.error("Drupal Wiki sync failed: %s", e, exc_info=True)
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
        except Exception as e:
            # Logged here with the traceback; recorded without logging a second time.
            # A DrupalWikiSyncError already names what it was reading, so it stands on
            # its own; any other exception needs the phase spelled out.
            self.logger.error("Drupal Wiki: could not sync %s: %s", name, e, exc_info=True)
            self._failures.append(
                str(e) if isinstance(e, DrupalWikiSyncError)
                else f"Could not sync {name} from Drupal Wiki: {e}"
            )
            return None

    # ------------------------------------------------------------------
    # Users and groups
    # ------------------------------------------------------------------

    async def _sync_users(self) -> dict[str, AppUser]:
        """Active users are refreshed in full every run; emails are the identity link.

        Disabled accounts are left out at the source: they cannot open the wiki, and
        nothing downstream reads ``is_active``, so syncing them would only create
        records that grant nothing.

        Each page is written as it arrives, so only one page of new objects is held at
        a time. The directory itself is still returned whole because group membership
        can name any user.
        """
        users_by_id: dict[str, AppUser] = {}
        skipped_without_email = 0
        page = 0
        while True:
            payload = self._require_ok(
                await self.data_source.list_users(only_active=True, page=page, size=PAGE_SIZE),
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
                except DrupalWikiSyncError as e:
                    # Passing an empty member list here would wipe the group's edges,
                    # so a failed fetch leaves the previous membership in place.
                    self._fail(str(e))
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
            if payload.get("last", True) or not content:
                break
            page += 1

        self.logger.info("Synced %d Drupal Wiki group(s)", synced)

    # ------------------------------------------------------------------
    # Spaces -> record groups and their permissions
    # ------------------------------------------------------------------

    async def _sync_spaces(self) -> dict[int, dict[str, Any]]:
        """Spaces become record groups; their permissions are replaced every run."""
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
                    try:
                        members = await self.graphql.get_space_members(space_id)
                    except DrupalWikiGraphQLError as e:
                        # Fail closed: a space whose members cannot be read is left
                        # exactly as it was on the previous run.
                        self._fail(
                            f"Could not read who has access to the Drupal Wiki space "
                            f"'{space_name}', so it was left as it is this time: {e}"
                        )
                        continue

                permissions = self._build_space_permissions(space, members)
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
        for user in members.get("users") or []:
            email = (user.get("email") or "").strip()
            if not email:
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
        """Sync each selected space against its own checkpoint.

        A space keeps its cursor to itself so that a space which fails, or which the
        permission phase dropped, is listed in full the next time it is reachable.
        A single shared cursor would move on without it and its existing pages, being
        older than that cursor, would never be listed again.
        """
        for space_id in spaces:
            try:
                await self._sync_space_pages(
                    space_id, spaces[space_id].get("name") or f"space {space_id}"
                )
            except DrupalWikiSyncError as e:
                # A space that cannot be listed keeps its checkpoint, so the next run
                # resumes it from where it was; the other spaces still sync.
                self._fail(str(e))

    async def _sync_space_pages(self, space_id: int, space_name: str) -> None:
        """Sync one space's changed pages and advance only that space's checkpoint.

        The cursor is the wiki's own ``lastModified``, never this host's clock, so both
        sides of the comparison come from the same machine and no skew allowance is
        needed.
        """
        key = self._space_sync_point_key(space_id)
        checkpoint = await self._get_checkpoint(self.record_sync_point, key)
        modified_after = _as_int(checkpoint.get("last_modified"))
        failures_before = len(self._failures)
        batch: list[tuple[Record, list[Permission]]] = []
        synced = 0
        newest_seen: int | None = None

        parents = await self._fetch_space_tree(space_id, space_name)
        async for page in self._fetch_pages(space_id, modified_after):
            try:
                records = await self._build_page_records(page, parents)
            except DrupalWikiSyncError:
                raise
            except Exception as e:
                # The rest of the space still syncs, but its checkpoint stays put so
                # the next run retries this page. Nothing else would: there is no
                # second pass to backfill it.
                self.logger.error(
                    "Could not read the Drupal Wiki page '%s': %s",
                    page.get("title") or page.get("id"), e, exc_info=True,
                )
                self._fail(
                    f"Could not read the Drupal Wiki page "
                    f"'{page.get('title') or page.get('id')}'. It will be tried again on "
                    f"the next sync."
                )
                continue
            batch.extend(records)
            synced += 1
            last_modified = _as_int(page.get("lastModified"))
            if last_modified is not None and (newest_seen is None or last_modified > newest_seen):
                newest_seen = last_modified
            if len(batch) >= RECORD_BATCH_SIZE:
                await self.data_entities_processor.on_new_records(batch)
                batch = []

        if batch:
            await self.data_entities_processor.on_new_records(batch)
        self.logger.info(
            "Synced %d changed page(s) in the Drupal Wiki space '%s'", synced, space_name
        )

        # A page whose attachments could not be listed is incomplete, so the checkpoint
        # must not move past it.
        if len(self._failures) != failures_before or newest_seen is None:
            return
        # The filter is a strict ``>``, so a page stamped in the same second as the
        # newest one but written just after the query ran would never be returned
        # again. Standing one second back costs re-reading that second's pages.
        await self.record_sync_point.update_sync_point(key, {"last_modified": newest_seen - 1})

    @staticmethod
    def _space_sync_point_key(space_id: int) -> str:
        return generate_record_sync_point_key(RecordType.WEBPAGE.value, "space", str(space_id))

    async def _fetch_pages(
        self,
        space_id: int,
        modified_after: int | None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Yield one space's pages changed since ``modified_after``.

        Asking per space keeps the pages of spaces the user did not select off the
        wire entirely, rather than downloading the whole wiki and discarding most of
        it. ``GET /page`` answers with a Spring envelope, so ``last`` ends the walk.
        """
        index = 0
        while True:
            payload = self._require_ok(
                await self.data_source.list_pages(
                    space_id=space_id,
                    modified_after=modified_after,
                    page=index,
                    size=PAGE_SIZE,
                ),
                f"reading the pages of space {space_id}",
            )
            content = payload.get("content") or []
            for page in content:
                # An id-less page would be saved as "page:None" and, because the data
                # source drops null query params, would pull every attachment in the
                # wiki onto it.
                if _as_int(page.get("id")) is None:
                    continue
                # ``space`` also returns pages merely shared into it. They belong to
                # their home space and are synced by its own pass, which keeps every
                # record under exactly one record group. A page with no home space at
                # all is dropped: it has no group to belong to.
                if _as_int(page.get("homeSpace")) != space_id:
                    continue
                yield page
            # An absent ``last`` stops rather than loops.
            if payload.get("last", True) or not content:
                return
            index += 1

    async def _build_page_records(
        self,
        page: dict[str, Any],
        parents: dict[int, int] | None,
    ) -> list[tuple[Record, list[Permission]]]:
        """Build the page record plus its attachments."""
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

        records: list[tuple[Record, list[Permission]]] = [(record, [])]
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
        extension = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else None
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
            nodes = await self.graphql.get_space_tree(space_id)
            # No node carrying a page id means the reply has changed shape, not that the
            # space is flat. Returning {} would re-save every page with no parent and
            # wipe the structure already stored.
            if nodes and not any(node.get("pageId") is not None for node in nodes):
                self.logger.warning(
                    "Drupal Wiki returned a page structure for the space '%s' that this "
                    "connector does not understand, so its pages keep the structure they "
                    "already have in PipesHub.", space_name
                )
                return None
            return self._extract_parent_page_ids(nodes)
        except DrupalWikiGraphQLError as e:
            self.logger.warning(
                "Could not read the page structure of the Drupal Wiki space '%s', so its "
                "pages keep the structure they already have in PipesHub: %s", space_name, e,
            )
            return None

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

        A record the source no longer has is left alone: this connector does not
        remove anything yet, so it is skipped rather than deleted.
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
                    "Could not refresh '%s' from Drupal Wiki, so it keeps the content it "
                    "already has: %s", record.record_name, e, exc_info=True,
                )
                continue
            if outcome == "updated":
                updated.append((record, []))
            elif outcome == "unchanged":
                unchanged.append(record)
            # "gone": nothing is deleted, so the record is simply left alone

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

        if response is None or response.status in (
            HttpStatusCode.NOT_FOUND.value,
            HttpStatusCode.FORBIDDEN.value,
        ):
            self.logger.warning(
                "Drupal Wiki no longer has '%s', or the token can no longer see it. The "
                "copy already in PipesHub is kept.", record.record_name,
            )
            return "gone"
        if response.status >= HttpStatusCode.BAD_REQUEST.value:
            self.logger.warning(
                "Drupal Wiki returned an error (%s) for '%s', so it keeps the content it "
                "already has.", response.status, record.record_name,
            )
            return "gone"

        payload = self._safe_json_parse(response, f"refreshing {record.record_name}") or {}
        last_modified = payload.get("lastModified")
        revision = str(last_modified) if last_modified is not None else None
        if revision is None or revision == record.external_revision_id:
            return "unchanged"

        record.external_revision_id = revision
        record.indexing_status = ProgressStatus.NOT_STARTED.value
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
            return FilterOptionsResponse(
                success=False, options=[], page=page, limit=limit, has_more=False,
                message=f"Drupal Wiki returned status {response.status}",
            )
        payload = response.json() or {}
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

    def _failure_preview(self, limit: int = 10) -> str:
        """The failures as a bulleted list, because a run can collect a dozen."""
        preview = "\n".join(f"- {failure}" for failure in self._failures[:limit])
        extra = len(self._failures) - limit
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
                "will be synced again this time: %s", key, e
            )
            return {}

    def _require_ok(self, response: HTTPResponse, context: str) -> object:
        if response.status >= HttpStatusCode.BAD_REQUEST.value:
            raise DrupalWikiSyncError(
                f"Drupal Wiki returned an error ({response.status}) while {context}"
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
                f"Drupal Wiki sent something other than data while {context}. Check that the "
                "wiki address points at the wiki itself and not at a login or proxy page."
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
