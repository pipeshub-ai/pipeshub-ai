"""Zendesk connector implementation."""

import asyncio
import base64
import hashlib
import json
import mimetypes
import re
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone
from functools import partial
from logging import Logger
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from fastapi.responses import StreamingResponse
from html_to_markdown import convert as html_to_markdown  # type: ignore[import-untyped]

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    AppGroups,
    CollectionNames,
    Connectors,
    ProgressStatus,
    RecordRelations,
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
    map_source_status,
    not_found_at_source,
)
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
)
from app.connectors.core.base.token_service.startup_service import startup_service
from app.connectors.core.constants import CONNECTOR_EMAIL_IDENTITY_INFO, IconPaths
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
    SyncStrategy,
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
from app.connectors.sources.zendesk.common.apps import ZendeskApp
from app.connectors.utils.value_mapper import ValueMapper
from app.models.blocks import (
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
from app.services.notification.types import NotificationSeverity, NotificationType
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_retry import call_with_retry
from app.sources.client.zendesk.zendesk import (
    ZendeskClient,
    ZendeskConfigError,
    ZendeskResponse,
    redact_attachment_url,
)
from app.sources.external.zendesk.zendesk import ZendeskDataSource
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import get_epoch_timestamp_in_ms


SYNC_POINT_KEY = "zendesk_incremental"
ARTICLES_SYNC_POINT_KEY = "zendesk_articles_incremental"
USERS_SYNC_POINT_KEY = "zendesk_users_incremental"
HELP_CENTER_GROUPS_STATE_KEY = "zendesk_help_center_groups"
TICKET_GROUPS_STATE_KEY = "zendesk_ticket_groups"
ARTICLE_IDS_STATE_KEY = "zendesk_article_ids"
REDACTED_ATTACHMENT_NAME = "redacted.txt"
# Offset pagination stops at 10,000 records; this bounds a walk that never ends.
MAX_OFFSET_PAGES = 10_000
# Agents, admins, groups and memberships are always exported from here, never from a
# checkpoint. on_new_user_groups deletes every membership edge before re-adding from
# the list it is given, so the list has to be the whole truth — a window cannot tell
# "unchanged" from "removed", and guessing wrong revokes real access. Only the record
# stages (tickets, articles) resume from a sync point.
DEFAULT_INCREMENTAL_START_TIME = 1
# The only roles synced as users. End users are never listed: a B2C desk has millions,
# and the grants they need come from what the ticket pages sideload.
STAFF_ROLES = ["agent", "admin"]
# Sideloaded users are looked up while their page is processed, so one page is all that
# has to fit. Bounded because the connector process outlives any one sync.
SIDELOADED_USER_CACHE_SIZE = 20_000
PAGE_SIZE = 100
# Matches the Jira connectors: cap how many records go into one on_new_records call.
BATCH_PROCESSING_SIZE = 100
# Zendesk rejects an incremental start_time inside the last minute.
INCREMENTAL_SAFETY_LAG_SECONDS = 60
RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
HTTP_ERROR_STATUS = 400
CDN_FETCH_TIMEOUT_SECONDS = 60.0
UNRELIABLE_MIME_TYPES = frozenset({"application/unknown", "application/octet-stream", "application/binary"})
# Zendesk reports trashed tickets in the incremental export under this status.
DELETED_TICKET_STATUS = "deleted"
# Admins and agents whose role grants ticket_access "all" read every ticket in Zendesk
# whatever its group, so they cannot be derived from group membership.
ALL_TICKETS_GROUP_ID = "role_all_tickets"
ALL_TICKETS_ACCESS = "all"
# Custom-role ticket_access "within-groups-and-public-groups": Zendesk treats these
# agents as members of every public group and lets them read unassigned tickets.
PUBLIC_GROUPS_ACCESS = "public_groups"
PUBLIC_GROUPS_GROUP_ID = "role_public_groups"
UNASSIGNED_GROUP_ID = "unassigned_tickets"
# Custom roles exist on Enterprise plans only; other plans answer with one of these.
CUSTOM_ROLES_UNAVAILABLE_STATUSES = frozenset({403, 404})
# Base64 inflates by a third and the result is held in the record body.
MAX_INLINE_IMAGE_BYTES = 10 * 1024 * 1024
IMG_SRC_PATTERN = re.compile(r'(<img\b[^>]*?\bsrc=["\'])([^"\']+)(["\'])', re.IGNORECASE)
# What the builder requests for team sync; the connector only ever reads.
REQUIRED_OAUTH_SCOPES = ("read",)
AUTH_FAILED_MESSAGE = (
    "Zendesk rejected the stored credentials and refreshing them failed. "
    "Re-authorize the connector."
)


class ZendeskAuthError(Exception):
    """Zendesk answered 401 even after a token refresh; the message is user-facing."""


@ConnectorBuilder("Zendesk")\
    .in_group(AppGroups.ZENDESK.value)\
    .with_description("Sync tickets, comments, attachments, articles, users, and groups from Zendesk")\
    .with_categories(["Help Desk", "Knowledge Base"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_auth([
        # OAuth only: Zendesk no longer issues API tokens and stops honouring the
        # existing ones on 2027-04-30.
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Zendesk",
            # Per-subdomain endpoints, but the registry takes literal URLs — the real
            # ones are collected as fields, as ServiceNow does.
            authorize_url="https://example.zendesk.com/oauth/authorizations/new",
            token_url="https://example.zendesk.com/oauth/tokens",
            redirect_uri="connectors/oauth/callback/Zendesk",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=["read"],
                agent=[],
            ),
            fields=[
                AuthField(
                    name="subdomain",
                    display_name="Subdomain",
                    placeholder="acme",
                    description="Your Zendesk subdomain (the acme in acme.zendesk.com)",
                    field_type="TEXT",
                    max_length=2000,
                ),
                AuthField(
                    name="authorizeUrl",
                    display_name="Authorize URL",
                    placeholder="https://acme.zendesk.com/oauth/authorizations/new",
                    description="OAuth authorize URL for your Zendesk subdomain",
                    field_type="URL",
                    max_length=2000,
                ),
                AuthField(
                    name="tokenUrl",
                    display_name="Token URL",
                    placeholder="https://acme.zendesk.com/oauth/tokens",
                    description="OAuth token URL for your Zendesk subdomain",
                    field_type="URL",
                    max_length=2000,
                ),
                CommonFields.client_id("Zendesk OAuth Client"),
                CommonFields.client_secret("Zendesk OAuth Client"),
            ],
            icon_path=IconPaths.connector_icon(Connectors.ZENDESK.value.lower()),
            app_group=AppGroups.ZENDESK.value,
            app_description="OAuth application for syncing Zendesk tickets, articles, users, and groups",
            app_categories=["Help Desk", "Knowledge Base"],
        ),
    ])\
    .with_info(CONNECTOR_EMAIL_IDENTITY_INFO)\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.ZENDESK.value.lower()))
        .add_documentation_link(DocumentationLink(
            "Zendesk OAuth Setup",
            "https://developer.zendesk.com/documentation/ticketing/working-with-oauth/creating-and-using-oauth-tokens-with-the-api/",
            "setup",
        ))
        .add_documentation_link(DocumentationLink(
            "Pipeshub Documentation",
            "https://docs.pipeshub.com/connectors/zendesk/zendesk",
            "pipeshub",
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
            option_source_type=OptionSourceType.DYNAMIC,
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
            default_value=True,
        ))
        .add_filter_field(FilterField(
            name="attachments",
            display_name="Index Attachments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of ticket and Help Center article attachments",
            default_value=True,
        ))
        .add_filter_field(FilterField(
            name="knowledge_base",
            display_name="Index Knowledge Base",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of Help Center articles",
            default_value=True,
        ))
    )\
    .build_decorator()
class ZendeskConnector(BaseConnector):
    """Zendesk connector for ingesting support tickets and Help Center articles."""

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
            ZendeskApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
        self.external_client: Optional[ZendeskClient] = None
        self.data_source: Optional[ZendeskDataSource] = None
        self.base_url: Optional[str] = None
        self.connector_name = Connectors.ZENDESK
        self.value_mapper = ValueMapper()
        self.sync_filters: Any = None
        self.indexing_filters: Any = None
        self._user_id_to_data: Dict[str, Dict[str, Any]] = {}
        self._sideloaded_users: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._group_id_to_data: Dict[str, Dict[str, Any]] = {}
        self._section_id_to_data: Dict[str, Dict[str, Any]] = {}
        self._category_id_to_data: Dict[str, Dict[str, Any]] = {}
        self._org_id_to_data: Dict[str, Dict[str, Any]] = {}
        self._user_id_to_app_user: Dict[str, AppUser] = {}
        self._rebuild_ticket_edges = False
        self._rebuild_article_edges = False
        self._refresh_ticket_grants = False
        self._role_ticket_access: Dict[str, str] = {}
        self._roles_complete = False
        self._ticket_sync_complete = False
        self._stale_help_center_group_ids: List[str] = []
        self._current_help_center_group_ids: List[str] = []
        self._current_ticket_group_ids: List[str] = []
        self._deleted_ticket_group_ids: set[str] = set()
        self._skipped_this_sync: List[str] = []
        self._token_refresh_lock = asyncio.Lock()
        self.records_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=self.data_entities_processor.org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=data_store_provider,
        )

    async def init(self) -> bool:
        try:
            client = await ZendeskClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id,
            )
            self.external_client = client
            self.data_source = ZendeskDataSource(client)
            self.base_url = client.get_base_url()
            self.logger.info(f"Zendesk connector {self.connector_id} initialized")
            return True
        except ZendeskConfigError as e:
            self.logger.error(f"Failed to initialize Zendesk connector: {e}")
            raise ConnectorInitError(str(e)) from e
        except Exception as e:
            self.logger.error(f"Failed to initialize Zendesk connector: {e}", exc_info=True)
            return False

    async def _get_fresh_datasource(self) -> ZendeskDataSource:
        """Resolve the data source for every Zendesk API call.

        Not re-initialising on failure: the factory never returns an uninitialised
        connector, so reaching here uninitialised is a bug. Also the single place an
        OAuth refresh belongs, hence callers re-resolve rather than hold one.
        """
        if not self.external_client or not self.data_source:
            raise RuntimeError("Zendesk data source is not initialized")
        rotated_token = await self._rotated_access_token()
        if rotated_token:
            # In place: a rebuilt client would leave the old connection pool open on
            # every rotation, and callers holding the client would keep the old token.
            self.external_client.get_client().set_access_token(rotated_token)
        return self.data_source

    async def _rotated_access_token(self) -> Optional[str]:
        """The stored access token when it differs from the one in use, else None."""
        client = self.external_client.get_client()
        in_use = getattr(client, "access_token", None)
        if not in_use:
            return None
        try:
            config = await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config", use_cache=False
            )
        except Exception as e:
            self.logger.warning(f"Zendesk: could not re-read stored credentials: {e}")
            return None
        stored = ((config or {}).get("credentials") or {}).get("access_token")
        return stored if stored and stored != in_use else None

    async def run_sync(self) -> None:
        self._skipped_this_sync = []
        try:
            await self._run_sync()
        except ZendeskAuthError as e:
            await self.notify(
                type=NotificationType.CONNECTOR_AUTH_ERROR,
                severity=NotificationSeverity.ERROR,
                title=f"{self.display_name} connector needs to be re-authorized",
                message=str(e),
            )
            raise
        except Exception as e:
            await self.notify(
                type=NotificationType.CONNECTOR_SYNC_ERROR,
                severity=NotificationSeverity.ERROR,
                title=f"{self.display_name} sync failed",
                message=(
                    f"The sync stopped due to an error: {str(e)[:200]}. Recent Zendesk "
                    "changes may not be in search yet. It is retried on the next sync."
                ),
            )
            raise
        if self._skipped_this_sync:
            await self.notify(
                type=NotificationType.CONNECTOR_SYNC_ERROR,
                severity=NotificationSeverity.WARNING,
                title=f"{self.display_name} sync skipped some data",
                message=(
                    "Zendesk did not return everything, so this was left for the next "
                    f"sync: {'; '.join(sorted(set(self._skipped_this_sync)))}."
                ),
            )

    def _skip(self, what: str) -> None:
        self._skipped_this_sync.append(what)

    async def _run_sync(self) -> None:
        self.logger.info(f"Starting Zendesk sync for connector {self.connector_id}")

        # Sideloads and visibility caches describe one source snapshot only. Reusing
        # them after a failed/partial run could keep a suspended user or deleted group.
        self._sideloaded_users.clear()
        self._group_id_to_data.clear()
        self._section_id_to_data.clear()
        self._category_id_to_data.clear()
        self._org_id_to_data.clear()

        self.sync_filters, self.indexing_filters = await load_connector_filters(
            self.config_service,
            "zendesk",
            self.connector_id,
            self.logger,
        )

        # A full sync deletes the sync points and every edge with them, keeping the
        # nodes. Skipping an unchanged record would leave it stranded: still in the
        # graph, attached to nothing, and never touched again because it will never
        # look changed. A missing checkpoint is the signal that just happened, so send
        # everything and let the processor rebuild the edges. One flag per stage — a
        # stage that never runs never writes a checkpoint, and would otherwise pin the
        # other stage's flag on forever.
        self._rebuild_ticket_edges = not (
            await self.records_sync_point.read_sync_point(SYNC_POINT_KEY)
        ).get("lastEndTime")
        self._rebuild_article_edges = not (
            await self.records_sync_point.read_sync_point(ARTICLES_SYNC_POINT_KEY)
        ).get("lastEndTime")
        if self._rebuild_ticket_edges or self._rebuild_article_edges:
            self.logger.info(
                "Zendesk: no sync point — resending unchanged records to rebuild edges "
                "(tickets=%s, articles=%s)",
                self._rebuild_ticket_edges, self._rebuild_article_edges,
            )

        users, user_email_map, users_complete = await self._fetch_users()
        if users:
            await self.data_entities_processor.on_new_app_users(users)
        self.logger.info(f"Zendesk: synced {len(users)} users")

        self._role_ticket_access, self._roles_complete = (
            await self._fetch_custom_role_ticket_access()
        )

        # Without the roles an agent's access is unknown, so memberships are only added
        # to: a rebuild would drop every custom-role agent until the next good sync.
        replace_members = self._roles_complete
        group_record_groups, group_user_groups, memberships_complete = (
            await self._fetch_groups(user_email_map)
        )
        if users_complete and memberships_complete:
            group_user_groups = await self._append_empty_removed_user_groups(
                group_user_groups,
                prefixes=("group_",),
            )
            if group_user_groups:
                await self.data_entities_processor.on_new_user_groups(
                    group_user_groups, replace_members=replace_members
                )
        elif group_user_groups:
            self._skip("group memberships")
            self.logger.error(
                "Zendesk: skipping group membership sync — the %s export was truncated "
                "and on_new_user_groups would rebuild each group from partial data",
                "user" if not users_complete else "group membership",
            )
        all_access_group, roles_complete = await self._build_all_tickets_group(
            self._role_ticket_access, self._roles_complete
        )
        if users_complete:
            staff_access_groups = self._build_staff_access_groups()
            staff_access_groups = await self._append_empty_removed_user_groups(
                staff_access_groups,
                prefixes=("staff_",),
            )
            await self.data_entities_processor.on_new_user_groups(
                [all_access_group, self._build_public_groups_group(), *staff_access_groups],
                replace_members=replace_members,
            )
        else:
            self._skip("agent access")
            self.logger.error(
                "Zendesk: skipping all-tickets access group — the user export was "
                "truncated and the group is rebuilt from scratch on every write"
            )
        if not replace_members:
            self._skip("custom role access")
            self.logger.error(
                "Zendesk: custom role export failed — keeping existing group and "
                "all-tickets members, adding only agents whose access is known"
            )

        if group_record_groups:
            await self.data_entities_processor.on_new_record_groups(group_record_groups)
        self.logger.info(f"Zendesk: synced {len(group_record_groups)} groups")

        org_user_groups, orgs_complete = await self._fetch_organizations()
        if orgs_complete:
            org_user_groups = await self._append_empty_removed_user_groups(
                org_user_groups,
                prefixes=("org_",),
            )
        if org_user_groups:
            shared_org_groups: List[AppUserGroup] = []
            emptied_org_groups = []
            for group, _ in org_user_groups:
                org_id = group.source_user_group_id.removeprefix("org_")
                if self._org_data_shares_tickets(self._org_id_to_data.get(org_id)):
                    shared_org_groups.append(group)
                else:
                    emptied_org_groups.append((group, []))
            if emptied_org_groups:
                await self.data_entities_processor.on_new_user_groups(
                    emptied_org_groups, replace_members=True
                )
            await self._sync_shared_org_members(shared_org_groups)
        if not orgs_complete:
            self._skip("organizations")
            self.logger.error(
                "Zendesk: organization export was truncated — organizations past the "
                "break have no group, so their org-wide ticket grant is withheld"
            )
        self.logger.info(f"Zendesk: synced {len(org_user_groups)} organizations")

        # Without those AppUserGroups the group grant is dropped, and the advanced sync
        # point would stop any later run repairing it.
        ticket_error: Optional[Exception] = None
        if users_complete and memberships_complete:
            try:
                self._refresh_ticket_grants, users_checkpoint = (
                    await self._users_deleted_since_last_sync()
                )
                ticket_count = await self._sync_tickets()
                if self._ticket_sync_complete:
                    await self.data_entities_processor.reap_external_app_users(
                        self.connector_id
                    )
                    await self._delete_removed_ticket_group_folders()
                    if users_checkpoint is not None:
                        await self.records_sync_point.update_sync_point(
                            USERS_SYNC_POINT_KEY, {"lastEndTime": users_checkpoint}
                        )
            except Exception as e:
                ticket_count = 0
                ticket_error = e
                self.logger.exception(
                    "Zendesk: ticket sync failed; continuing with Help Center sync"
                )
        else:
            ticket_count = 0
            self._skip("tickets")
            self.logger.error(
                "Zendesk: skipping ticket sync — group or all-tickets membership was "
                "not written, so every ticket would land without that grant and the advanced sync "
                "point would stop any later run from repairing it"
            )
        article_count = await self._sync_help_center_articles()
        # Raised only now so the Help Center still syncs, but the run reads as failed.
        if ticket_error is not None:
            raise ticket_error
        self.logger.info(
            f"Zendesk sync completed for connector {self.connector_id}: "
            f"{len(users)} users, {len(group_record_groups)} groups, "
            f"{len(org_user_groups)} organizations, "
            f"{ticket_count} tickets, {article_count} articles"
        )

    async def _fetch_users(self) -> Tuple[List[AppUser], Dict[str, AppUser], bool]:
        """Agents and admins only — the people who can hold a group or all-tickets grant.

        The full list every sync, not just changed users: on_new_user_groups rebuilds
        membership from scratch, so a truncated one revokes access. Third value flags
        that. End users are left out; see STAFF_ROLES.
        """
        datasource = await self._get_fresh_datasource()
        users_data, complete = await self._fetch_paginated_list_checked(
            datasource.list_users, "users", roles_=STAFF_ROLES
        )

        # Rebuilt, not merged into: /users no longer lists someone who was deleted or
        # demoted, and a stale entry would keep their all-tickets access until restart.
        self._user_id_to_data = {}
        self._user_id_to_app_user = {}
        users: List[AppUser] = []
        user_email_map: Dict[str, AppUser] = {}
        for user_data in users_data:
            app_user = self._to_app_user(user_data)
            if not app_user:
                continue
            users.append(app_user)
            user_email_map[app_user.source_user_id] = app_user
            user_email_map[app_user.email] = app_user
            self._user_id_to_data[app_user.source_user_id] = user_data
            self._user_id_to_app_user[app_user.source_user_id] = app_user

        return users, user_email_map, complete

    async def _append_empty_removed_user_groups(
        self,
        current_groups: List[Tuple[AppUserGroup, List[AppUser]]],
        *,
        prefixes: Tuple[str, ...],
    ) -> List[Tuple[AppUserGroup, List[AppUser]]]:
        """Keep removed Zendesk groups as empty nodes so stale grants are withdrawn."""
        current_ids = {group.source_user_group_id for group, _ in current_groups}
        async with self.data_store_provider.transaction() as tx_store:
            stored_groups = await tx_store.get_user_groups(
                self.connector_id,
                self.data_entities_processor.org_id,
            )
        for old_group in stored_groups:
            external_id = old_group.source_user_group_id
            if (
                external_id.startswith(prefixes)
                and external_id not in current_ids
            ):
                current_groups.append((old_group, []))
        return current_groups

    def _to_app_user(self, user_data: Dict[str, Any]) -> Optional[AppUser]:
        user_id = user_data.get("id")
        email = user_data.get("email")
        # Permissions resolve by email; a synthesised address matches nobody.
        if not user_id or not email:
            return None
        return AppUser(
            app_name=Connectors.ZENDESK,
            connector_id=self.connector_id,
            source_user_id=str(user_id),
            org_id=self.data_entities_processor.org_id,
            email=email,
            full_name=user_data.get("name") or email,
            is_active=bool(user_data.get("active", True)),
            source_created_at=self._parse_datetime(user_data.get("created_at")),
            source_updated_at=self._parse_datetime(user_data.get("updated_at")),
        )

    def _user_data(self, user_id: int | str | None) -> Dict[str, Any]:
        """A user's record: staff from the sync, anyone else from the page that named them."""
        key = str(user_id)
        return self._user_id_to_data.get(key) or self._sideloaded_users.get(key) or {}

    async def _fetch_groups(
        self,
        user_email_map: Dict[str, AppUser],
    ) -> Tuple[
        List[Tuple[RecordGroup, List[Permission]]],
        List[Tuple[AppUserGroup, List[AppUser]]],
        bool,
    ]:
        # Both lists are walked to the very end every sync, never windowed: they feed
        # a rebuild that deletes first, so a partial answer revokes access.
        datasource = await self._get_fresh_datasource()
        # Deleted groups too: a full sync forgets which folders existed, so this is
        # the only way to find a deleted group's folder and empty it.
        groups_data, groups_complete = await self._fetch_paginated_list_checked(
            datasource.list_groups,
            "groups",
            exclude_deleted=False,
        )
        self._deleted_ticket_group_ids = {
            f"group_{group['id']}" for group in groups_data
            if group.get("deleted") and group.get("id")
        }
        groups_data = [group for group in groups_data if not group.get("deleted")]
        memberships, memberships_complete = await self._fetch_paginated_list_checked(
            datasource.list_group_memberships,
            "group_memberships",
        )
        members_by_group: Dict[str, List[AppUser]] = defaultdict(list)
        for membership in memberships:
            group_id = str(membership.get("group_id", ""))
            user_id = str(membership.get("user_id", ""))
            user = user_email_map.get(user_id)
            user_data = self._user_id_to_data.get(user_id, {})
            if group_id and user and self._has_group_ticket_access(
                user_data, self._role_ticket_access
            ):
                members_by_group[group_id].append(user)
        public_groups_agents = self._public_groups_agents(user_email_map)

        record_groups: List[Tuple[RecordGroup, List[Permission]]] = []
        user_groups: List[Tuple[AppUserGroup, List[AppUser]]] = []
        self._current_ticket_group_ids = []
        for group_data in groups_data:
            group_id = str(group_data.get("id", ""))
            group_name = group_data.get("name") or f"Group {group_id}"
            if not group_id:
                continue
            # Cached before the filter check: _ticket_to_record reads this to tell an
            # unknown group from a deselected one, and only the former is a problem.
            self._group_id_to_data[group_id] = group_data
            if not self._is_group_allowed_by_filter(group_id):
                continue
            self._current_ticket_group_ids.append(f"group_{group_id}")

            source_created_at = self._parse_datetime(group_data.get("created_at"))
            source_updated_at = self._parse_datetime(group_data.get("updated_at"))
            user_group = AppUserGroup(
                app_name=Connectors.ZENDESK,
                connector_id=self.connector_id,
                source_user_group_id=f"group_{group_id}",
                name=group_name,
                org_id=self.data_entities_processor.org_id,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
            )
            group_members = list(members_by_group.get(group_id, []))
            if group_data.get("is_public") is not False:
                listed = {member.source_user_id for member in group_members}
                group_members += [
                    agent for agent in public_groups_agents if agent.source_user_id not in listed
                ]
            user_groups.append((user_group, group_members))

            record_group = RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=group_name,
                external_group_id=f"group_{group_id}",
                connector_name=Connectors.ZENDESK,
                connector_id=self.connector_id,
                group_type=RecordGroupType.PROJECT,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
                web_url=self._agent_group_url(group_id),
            )
            permissions = [
                Permission(
                    external_id=f"group_{group_id}",
                    type=PermissionType.READ,
                    entity_type=EntityType.GROUP,
                ),
                self._all_tickets_permission(),
            ]
            record_groups.append((record_group, permissions))

        if self._is_group_allowed_by_filter(UNASSIGNED_GROUP_ID):
            record_groups.append(self._build_unassigned_record_group())

        # Both feed a rebuild-from-scratch that would revoke whatever fell off the end.
        return record_groups, user_groups, groups_complete and memberships_complete

    def _build_unassigned_record_group(self) -> Tuple[RecordGroup, List[Permission]]:
        """Home for tickets with no usable group (every new ticket before triage).

        A record with no record group gets no App edge and drops out of the tree, so
        it would be visible to its requester alone.
        """
        record_group = RecordGroup(
            org_id=self.data_entities_processor.org_id,
            name="Unassigned",
            external_group_id=UNASSIGNED_GROUP_ID,
            connector_name=Connectors.ZENDESK,
            connector_id=self.connector_id,
            group_type=RecordGroupType.PROJECT,
        )
        return record_group, [self._all_tickets_permission(), self._public_groups_permission()]

    async def _fetch_custom_role_ticket_access(self) -> Tuple[Dict[str, str], bool]:
        """Map custom role id -> ticket_access, plus whether the list is trustworthy.

        Plans without custom roles answer 403/404 and have no agent carrying a
        custom_role_id either, so that is an empty-but-complete answer. Anything else
        is a truncated export: the all-tickets group is rebuilt from scratch from it.
        """
        datasource = await self._get_fresh_datasource()
        try:
            response = await call_with_retry(
                partial(self._call_api, datasource.list_custom_roles),
                logger=self.logger,
                label="zendesk/list_custom_roles",
            )
        except httpx.HTTPStatusError as e:
            self.logger.error(f"Zendesk list_custom_roles gave up after retries: {e}")
            return {}, False
        if response.status_code in CUSTOM_ROLES_UNAVAILABLE_STATUSES:
            self.logger.info(
                "Zendesk: custom roles unavailable on this plan (HTTP %s) — only the "
                "built-in admin and agent roles apply", response.status_code,
            )
            return {}, True
        if not response.success:
            self.logger.error(f"Zendesk list_custom_roles failed: {response.error}")
            return {}, False
        if not isinstance(response.data, dict) or not isinstance(
            response.data.get("custom_roles"), list
        ):
            self.logger.error("Zendesk list_custom_roles omitted custom_roles")
            return {}, False
        access: Dict[str, str] = {}
        for role in self._extract_list(response.data, "custom_roles"):
            role_id = role.get("id")
            if role_id is None:
                continue
            ticket_access = (role.get("configuration") or {}).get("ticket_access")
            access[str(role_id)] = str(ticket_access or "")
        return access, True

    def _has_all_tickets_access(
        self, user_data: Dict[str, Any], role_ticket_access: Dict[str, str]
    ) -> bool:
        """Whether Zendesk lets this user read every ticket regardless of group."""
        if user_data.get("active") is False or user_data.get("suspended"):
            return False
        role = user_data.get("role")
        if role == "admin":
            return True
        if role != "agent":
            return False
        ticket_access = self._effective_ticket_access(user_data, role_ticket_access)
        return ticket_access == ALL_TICKETS_ACCESS

    def _effective_ticket_access(
        self, user_data: Dict[str, Any], role_ticket_access: Dict[str, str]
    ) -> str:
        """Resolve role access while honoring a more restrictive agent override."""
        aliases = {
            "within-groups": "groups",
            "within_groups": "groups",
            "within-groups-and-public-groups": PUBLIC_GROUPS_ACCESS,
            "within-organization": "organization",
            "assigned-only": "assigned",
            "requested-only": "requested",
            "organization-only": "organization",
        }
        # Zendesk reports the agent's own limit as ticket_restriction; null is unrestricted.
        own_access = str(user_data.get("ticket_restriction") or "").lower()
        own_access = aliases.get(own_access, own_access)
        custom_role_id = user_data.get("custom_role_id")
        if custom_role_id is None:
            role_access = ALL_TICKETS_ACCESS
        else:
            role_access = str(role_ticket_access.get(str(custom_role_id)) or "").lower()
            role_access = aliases.get(role_access, role_access)
            if not role_access:
                if own_access in {
                    "assigned", "assigned_only", "requested", "groups",
                    "organization", "organization_only",
                }:
                    return own_access
                # Unknown, not "groups": the role may be assigned-only. Existing
                # memberships survive because the failed export makes writes additive.
                return ""
        access_rank = {
            "assigned": 0,
            "assigned_only": 0,
            "requested": 0,
            "organization": 1,
            "organization_only": 1,
            "groups": 1,
            PUBLIC_GROUPS_ACCESS: 2,
            "all": 3,
        }
        values = [value for value in (role_access, own_access) if value in access_rank]
        if not values:
            return role_access
        return min(values, key=access_rank.__getitem__)

    def _has_group_ticket_access(
        self, user_data: Dict[str, Any], role_ticket_access: Dict[str, str]
    ) -> bool:
        if user_data.get("active") is False or user_data.get("suspended"):
            return False
        access = self._effective_ticket_access(user_data, role_ticket_access)
        return access in {"all", "groups", PUBLIC_GROUPS_ACCESS}

    def _has_public_groups_access(
        self, user_data: Dict[str, Any], role_ticket_access: Dict[str, str]
    ) -> bool:
        if user_data.get("active") is False or user_data.get("suspended"):
            return False
        return self._effective_ticket_access(user_data, role_ticket_access) == PUBLIC_GROUPS_ACCESS

    async def _build_all_tickets_group(
        self,
        role_ticket_access: Optional[Dict[str, str]] = None,
        roles_complete: Optional[bool] = None,
    ) -> Tuple[Tuple[AppUserGroup, List[AppUser]], bool]:
        """One group of every admin and all-access agent, granted on every ticket.

        Always returned, even when empty, so the grant on tickets never points at a
        group that does not exist. Built from the agents and admins fetched this sync.
        """
        if role_ticket_access is None or roles_complete is None:
            role_ticket_access, complete = await self._fetch_custom_role_ticket_access()
        else:
            complete = roles_complete
        members = [
            app_user
            for user_id, app_user in self._user_id_to_app_user.items()
            if self._has_all_tickets_access(
                self._user_id_to_data.get(user_id, {}), role_ticket_access
            )
        ]
        group = AppUserGroup(
            app_name=Connectors.ZENDESK,
            connector_id=self.connector_id,
            source_user_group_id=ALL_TICKETS_GROUP_ID,
            name="Zendesk: all tickets access",
            org_id=self.data_entities_processor.org_id,
        )
        self.logger.info(f"Zendesk: {len(members)} users have all-tickets access")
        return (group, members), complete

    def _public_groups_agents(self, users: Dict[str, AppUser]) -> List[AppUser]:
        return [
            app_user
            for user_id, app_user in users.items()
            if self._has_public_groups_access(
                self._user_id_to_data.get(user_id, {}), self._role_ticket_access
            )
        ]

    def _build_public_groups_group(self) -> Tuple[AppUserGroup, List[AppUser]]:
        """Agents who may read unassigned tickets; always returned so the grant resolves."""
        group = AppUserGroup(
            app_name=Connectors.ZENDESK,
            connector_id=self.connector_id,
            source_user_group_id=PUBLIC_GROUPS_GROUP_ID,
            name="Zendesk: public groups access",
            org_id=self.data_entities_processor.org_id,
        )
        return group, self._public_groups_agents(self._user_id_to_app_user)

    def _public_groups_permission(self) -> Permission:
        return Permission(
            external_id=PUBLIC_GROUPS_GROUP_ID,
            type=PermissionType.READ,
            entity_type=EntityType.GROUP,
        )

    def _build_staff_access_groups(
        self,
    ) -> List[Tuple[AppUserGroup, List[AppUser]]]:
        """Create a private permission group for each restricted staff member."""
        groups: List[Tuple[AppUserGroup, List[AppUser]]] = []
        for user_id, app_user in self._user_id_to_app_user.items():
            user_data = self._user_id_to_data.get(user_id, {})
            if user_data.get("active") is False or user_data.get("suspended"):
                continue
            access = self._effective_ticket_access(user_data, self._role_ticket_access)
            if access not in {
                "assigned", "assigned_only", "requested", "organization",
                "organization_only",
            }:
                continue
            group = AppUserGroup(
                app_name=Connectors.ZENDESK,
                connector_id=self.connector_id,
                source_user_group_id=f"staff_{user_id}",
                name=f"Zendesk staff {user_data.get('name') or user_id}",
                org_id=self.data_entities_processor.org_id,
            )
            groups.append((group, [app_user]))
        return groups

    def _all_tickets_permission(self) -> Permission:
        return Permission(
            external_id=ALL_TICKETS_GROUP_ID,
            type=PermissionType.READ,
            entity_type=EntityType.GROUP,
        )

    async def _fetch_organizations(self) -> Tuple[List[Tuple[AppUserGroup, List[AppUser]]], bool]:
        """Sync Zendesk organizations as user groups.

        Members here are only the agents and admins already fetched. End users join
        their organization's group as the ticket pages sideload them
        (``_add_sideloaded_org_members``), since listing them all is what this avoids.
        Not RecordGroups: nothing files a record under one, so they would render empty;
        tickets carry the org permission.

        The second return value flags a truncated export.
        """
        orgs_data: List[Dict[str, Any]] = []
        start_time = DEFAULT_INCREMENTAL_START_TIME
        complete = True
        while True:
            response = await self._call_incremental(
                "incremental_organizations",
                start_time=start_time,
            )
            if response is None or not response.success:
                error = response.error if response else "retries exhausted"
                self.logger.error(f"Zendesk incremental_organizations failed: {error}")
                complete = False
                break
            if not response.data:
                self.logger.error(
                    "Zendesk incremental_organizations returned no usable payload"
                )
                complete = False
                break
            if not isinstance(response.data, dict) or not isinstance(
                response.data.get("organizations"), list
            ):
                self.logger.error(
                    "Zendesk incremental_organizations omitted organizations"
                )
                complete = False
                break
            payload = response.data
            orgs_data.extend(self._extract_list(payload, "organizations"))
            end_time = payload.get("end_time")
            if payload.get("end_of_stream", True):
                break
            # More pages remain but a stalled end_time cannot reach them, so the list is short.
            if not end_time or end_time <= start_time:
                self.logger.error(
                    "Zendesk incremental_organizations stopped advancing before end_of_stream"
                )
                complete = False
                break
            start_time = end_time

        members_by_org: Dict[str, List[AppUser]] = defaultdict(list)

        user_groups: List[Tuple[AppUserGroup, List[AppUser]]] = []
        for org_data in orgs_data:
            org_id = org_data.get("id")
            if not org_id:
                continue
            org_id = str(org_id)
            self._org_id_to_data[org_id] = org_data
            user_groups.append((self._org_user_group(org_id), members_by_org.get(org_id, [])))

        return user_groups, complete

    async def _sync_shared_org_members(self, groups: List[AppUserGroup]) -> None:
        """Read every customer of each ticket-sharing organization in this sync.

        A colleague who never raised a ticket is never sideloaded by a ticket page, so
        waiting for one would leave them without the organization's tickets.
        """
        if not groups:
            return
        datasource = await self._get_fresh_datasource()
        complete_groups: List[Tuple[AppUserGroup, List[AppUser]]] = []
        partial_groups: List[Tuple[AppUserGroup, List[AppUser]]] = []
        emails: set[str] = set()
        for group in groups:
            org_id = group.source_user_group_id.removeprefix("org_")
            users_data, complete = await self._fetch_paginated_list_checked(
                datasource.list_organization_users,
                "users",
                organization_id=int(org_id),
            )
            members: List[AppUser] = []
            for user_data in users_data:
                if user_data.get("active") is False or user_data.get("suspended"):
                    continue
                app_user = self._to_app_user(user_data)
                if app_user:
                    members.append(app_user)
                    emails.add(app_user.email)
            # A truncated list must not drop members another page or sync added.
            (complete_groups if complete else partial_groups).append((group, members))
        if complete_groups:
            await self.data_entities_processor.on_new_user_groups(
                complete_groups, replace_members=True
            )
        if partial_groups:
            await self.data_entities_processor.on_new_user_groups(
                partial_groups, replace_members=False
            )
        if emails:
            await self.data_entities_processor.on_external_app_users(
                sorted(emails), self.connector_id
            )

    def _org_user_group(self, org_id: str) -> AppUserGroup:
        org_data = self._org_id_to_data[org_id]
        return AppUserGroup(
            app_name=Connectors.ZENDESK,
            connector_id=self.connector_id,
            source_user_group_id=f"org_{org_id}",
            name=org_data.get("name") or f"Organization {org_id}",
            org_id=self.data_entities_processor.org_id,
            source_created_at=self._parse_datetime(org_data.get("created_at")),
            source_updated_at=self._parse_datetime(org_data.get("updated_at")),
        )

    async def _add_sideloaded_org_members(self, payload: Dict[str, Any]) -> None:
        """Put the end users a ticket page names into their organization's group.

        Only organizations that share tickets matter: no other org's group is ever
        granted on a ticket. Additive, never a rebuild — a page shows a slice of an
        org's people, and rebuilding from it would drop everyone else.
        """
        members_by_org: Dict[str, Dict[str, AppUser]] = defaultdict(dict)
        for user_data in self._extract_list(payload, "users"):
            org_id = str(user_data.get("organization_id") or "")
            if not self._org_data_shares_tickets(self._org_id_to_data.get(org_id)):
                continue
            app_user = self._to_app_user(user_data)
            if app_user:
                members_by_org[org_id][app_user.source_user_id] = app_user
        if members_by_org:
            await self.data_entities_processor.on_new_user_groups(
                [
                    (self._org_user_group(org_id), list(members.values()))
                    for org_id, members in members_by_org.items()
                ],
                replace_members=False,
            )

    async def _users_deleted_since_last_sync(self) -> Tuple[bool, Optional[int]]:
        """Whether anyone was deleted since the last completed ticket sync.

        Deleting a user drops them from every ticket's CCs without moving the ticket's
        updated_at, and Zendesk scrubs their email, so their email grants can only be
        withdrawn by re-reading the tickets. Second value is the checkpoint to save once
        that has happened; None leaves the saved one so the next run asks again.
        """
        checkpoint = get_epoch_timestamp_in_ms() // 1000 - INCREMENTAL_SAFETY_LAG_SECONDS
        if self._rebuild_ticket_edges:
            return False, checkpoint
        start_time = (await self.records_sync_point.read_sync_point(USERS_SYNC_POINT_KEY)).get(
            "lastEndTime"
        )
        if not start_time:
            return False, checkpoint
        deleted = False
        cursor: Optional[str] = None
        while True:
            response = await self._call_incremental(
                "incremental_users", start_time=int(start_time), cursor=cursor
            )
            payload = response.data if response is not None and response.success else None
            if not isinstance(payload, dict) or not isinstance(payload.get("users"), list):
                self.logger.warning(
                    "Zendesk: could not check for deleted users (%s) — will retry next sync",
                    response.error if response is not None else "retries exhausted",
                )
                return False, None
            if any(user.get("active") is False for user in payload["users"]):
                deleted = True
            if payload.get("end_of_stream", True):
                return deleted, checkpoint
            next_cursor = payload.get("after_cursor")
            if not next_cursor or next_cursor == cursor:
                return deleted, None
            cursor = next_cursor

    async def _sync_tickets(self) -> int:
        self._ticket_sync_complete = False
        synced = 0
        removed = 0
        start_time = await self._get_start_time(ignore_checkpoint=self._refresh_ticket_grants)
        saved_state = await self.records_sync_point.read_sync_point(SYNC_POINT_KEY)
        reset_id = str(saved_state.get("resetId") or uuid4())
        if saved_state.get("resetId") != reset_id:
            await self.records_sync_point.update_sync_point(
                SYNC_POINT_KEY,
                {**saved_state, "resetId": reset_id},
            )
        cursor: Optional[str] = (
            None if self._refresh_ticket_grants else saved_state.get("lastCursor")
        )
        if self._refresh_ticket_grants:
            self.logger.info(
                "Zendesk: users were deleted since the last sync — re-reading every "
                "ticket to withdraw their grants"
            )
        max_end_time = start_time
        complete = True
        seen_ticket_updated: Dict[str, int] = {}
        # The export cursor moves past a ticket whose comments failed, so it is kept
        # here and re-read by id every sync until it goes through.
        pending_ticket_ids = await self._retry_pending_tickets(
            [str(ticket_id) for ticket_id in saved_state.get("pendingTicketIds") or []]
        )
        while True:
            response = await self._call_incremental(
                "incremental_tickets",
                start_time=start_time,
                cursor=cursor,
                include="users,groups,organizations",
            )
            if (
                cursor
                and response is not None
                and response.status_code == HttpStatusCode.BAD_REQUEST.value
                and "InvalidCursor" in str(response.error)
            ):
                # Kept, the saved cursor fails every run and the sync never moves again.
                self.logger.warning(
                    "Zendesk: saved ticket cursor rejected — re-reading tickets from %s",
                    start_time,
                )
                await self.notify(
                    type=NotificationType.CONNECTOR_WARNING,
                    severity=NotificationSeverity.WARNING,
                    title=f"{self.display_name} connector restarted its ticket sync",
                    message=(
                        "Zendesk no longer accepts the saved sync position, so tickets "
                        "changed since the last completed sync are being read again."
                    ),
                )
                cursor = None
                continue
            if response is None or not response.success:
                error = response.error if response else "retries exhausted"
                self.logger.error(f"Zendesk incremental_tickets failed: {error}")
                complete = False
                break
            if not response.data:
                self.logger.error("Zendesk incremental_tickets returned no payload")
                complete = False
                break
            payload = response.data
            if not isinstance(payload, dict) or not isinstance(payload.get("tickets"), list):
                self.logger.error("Zendesk incremental_tickets omitted tickets")
                complete = False
                break
            self._cache_sideloads(payload)
            await self._add_sideloaded_org_members(payload)
            tickets = []
            for ticket in self._extract_list(payload, "tickets"):
                ticket_id = ticket.get("id")
                if ticket_id is None:
                    continue
                key = str(ticket_id)
                updated = self._parse_datetime(ticket.get("updated_at")) or 0
                if seen_ticket_updated.get(key, -1) >= updated:
                    continue
                seen_ticket_updated[key] = updated
                tickets.append(ticket)

            page_synced, page_removed, failed_ids = await self._process_ticket_batch(tickets)
            synced += page_synced
            removed += page_removed
            pending_ticket_ids.update(failed_ids)
            if failed_ids:
                self._skip("some tickets")

            # Commit the cursor only after every record and attachment on this page
            # has been stored. A crash replays at most the unfinished page.
            next_cursor = payload.get("after_cursor") or payload.get("cursor")
            if not payload.get("end_of_stream", True) and next_cursor and next_cursor != cursor:
                if not await self._sync_reset_is_current(SYNC_POINT_KEY, reset_id):
                    self.logger.warning(
                        "Zendesk: ignoring ticket checkpoint from a superseded sync"
                    )
                    return synced
                await self.records_sync_point.update_sync_point(
                    SYNC_POINT_KEY,
                    {
                        "lastEndTime": start_time,
                        "lastCursor": next_cursor,
                        "resetId": reset_id,
                        "pendingTicketIds": sorted(pending_ticket_ids),
                        "updatedAt": get_epoch_timestamp_in_ms(),
                    },
                )

            # Cursor export returns no end_time; resume from the newest ticket seen.
            for ticket_data in tickets:
                updated_ms = self._parse_datetime(ticket_data.get("updated_at"))
                if updated_ms:
                    max_end_time = max(max_end_time, updated_ms // 1000)

            if payload.get("end_of_stream", True):
                break
            if not next_cursor or next_cursor == cursor:
                self.logger.error(
                    "Zendesk incremental_tickets stopped advancing before end_of_stream"
                )
                complete = False
                break
            cursor = next_cursor

        # Advancing past a truncated export skips every ticket the failed pages held,
        # permanently — the next run would start after tickets it never saw.
        if not complete:
            self._skip("tickets")
            self.logger.error(
                "Zendesk: ticket export truncated — leaving the sync point at %s so the "
                "next run re-reads the missing window", start_time,
            )
            if await self._sync_reset_is_current(SYNC_POINT_KEY, reset_id):
                state = await self.records_sync_point.read_sync_point(SYNC_POINT_KEY)
                await self.records_sync_point.update_sync_point(
                    SYNC_POINT_KEY,
                    {**state, "pendingTicketIds": sorted(pending_ticket_ids)},
                )
            return synced

        now_seconds = get_epoch_timestamp_in_ms() // 1000
        max_end_time = min(max_end_time, now_seconds - INCREMENTAL_SAFETY_LAG_SECONDS)
        if not await self._sync_reset_is_current(SYNC_POINT_KEY, reset_id):
            self.logger.warning(
                "Zendesk: ignoring ticket checkpoint from a superseded sync"
            )
            return synced
        if self._rebuild_ticket_edges:
            removed += await self._remove_records_outside_date_filters(RecordType.TICKET)
        await self.records_sync_point.update_sync_point(
            SYNC_POINT_KEY,
            {
                "lastEndTime": max_end_time,
                "lastCursor": None,
                "resetId": reset_id,
                "pendingTicketIds": sorted(pending_ticket_ids),
                "updatedAt": get_epoch_timestamp_in_ms(),
            },
        )
        self._ticket_sync_complete = True
        if removed:
            self.logger.info(
                f"Zendesk: removed {removed} tickets and attachments deleted at source or "
                "outside the filters"
            )
        return synced

    async def _process_ticket_batch(
        self, tickets: List[Dict[str, Any]]
    ) -> Tuple[int, int, List[str]]:
        """Store tickets with their grants and attachments.

        Returns (stored, removed, ids set aside because their comments failed). A
        set-aside ticket is not written at all: storing it without its comments would
        leave its attachments on the grants it had before.
        """
        removed_ids = await self._resolve_removable_record_ids(tickets)
        if removed_ids:
            # Cascade, not on_record_deleted: attachments are child records, and only
            # the cascade path emits the events that purge the vectors from Qdrant.
            await self.data_entities_processor.on_records_deleted_cascade(
                removed_ids, self.connector_id
            )

        records_with_permissions: List[Tuple[Record, List[Permission]]] = []
        public_comments_by_ticket: Dict[str, List[Dict[str, Any]]] = {}
        failed_ids: List[str] = []
        external_emails: set[str] = set()
        for ticket_data in tickets:
            ticket_id = ticket_data.get("id")
            if ticket_id and self._is_ticket_in_scope(ticket_data):
                try:
                    comments = await self._fetch_public_comments(str(ticket_id))
                except Exception as e:
                    self.logger.error(
                        "Zendesk: setting ticket %s aside until its comments can be read: %s",
                        ticket_id, e,
                    )
                    failed_ids.append(str(ticket_id))
                    continue
                public_comments_by_ticket[str(ticket_id)] = comments
                if comments:
                    requester_email = self._user_data(ticket_data.get("requester_id")).get("email")
                    if requester_email:
                        external_emails.add(requester_email)
                    for collaborator_id in ticket_data.get("collaborator_ids") or []:
                        email = self._user_data(collaborator_id).get("email")
                        if email:
                            external_emails.add(email)
            try:
                record_tuple = await self._ticket_to_record(
                    ticket_data,
                    has_public_comment=bool(public_comments_by_ticket.get(str(ticket_id))),
                    include_unchanged=True,
                    comments=public_comments_by_ticket.get(str(ticket_id)),
                )
            except Exception:
                # Raised, it would fail the page every run and pin the cursor behind it.
                self.logger.exception("Zendesk: setting ticket %s aside", ticket_id)
                failed_ids.append(str(ticket_id))
                continue
            if record_tuple:
                records_with_permissions.append(record_tuple)
        if records_with_permissions:
            for start in range(0, len(records_with_permissions), BATCH_PROCESSING_SIZE):
                await self.data_entities_processor.on_new_records(
                    records_with_permissions[start:start + BATCH_PROCESSING_SIZE]
                )
            await self._replace_changed_record_permissions(records_with_permissions)
            # At sync time, not on the streaming path: an attachment is a record in
            # its own right and must exist even if its ticket is never indexed. This
            # is also what rebuilds their edges after a full sync wipes them, so an
            # unchanged ticket needs no forced reindex to get them back.
            for record, permissions in records_with_permissions:
                try:
                    await self._sync_ticket_attachments(
                        record,
                        permissions,
                        comments=public_comments_by_ticket.get(record.external_record_id, []),
                    )
                except Exception:
                    self.logger.exception(
                        "Zendesk: attachments of ticket %s failed; retrying next sync",
                        record.external_record_id,
                    )
                    failed_ids.append(record.external_record_id)
        if external_emails:
            await self.data_entities_processor.on_external_app_users(
                sorted(external_emails), self.connector_id
            )
        return len(records_with_permissions), len(removed_ids), failed_ids

    async def _retry_pending_tickets(self, ticket_ids: List[str]) -> set[str]:
        """Re-read set-aside tickets by id; returns the ones that still fail."""
        still_pending: set[str] = set()
        if not ticket_ids:
            return still_pending
        datasource = await self._get_fresh_datasource()
        for ticket_id in ticket_ids:
            try:
                response = await self._call_api_with_retry(
                    datasource.show_ticket,
                    "zendesk/show_ticket",
                    ticket_id=int(ticket_id),
                    include="users",
                )
            except Exception as e:
                self.logger.error("Zendesk: retry of ticket %s failed: %s", ticket_id, e)
                still_pending.add(ticket_id)
                continue
            if response.status_code == HttpStatusCode.NOT_FOUND.value:
                existing = await self.data_entities_processor.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_record_id=ticket_id,
                )
                if existing:
                    await self.data_entities_processor.on_records_deleted_cascade(
                        await self._with_attachment_ids(existing), self.connector_id
                    )
                continue
            ticket = (
                self._extract_required_object(response.data, "ticket")
                if response.success
                else {}
            )
            if not ticket:
                self.logger.error(
                    "Zendesk: retry of ticket %s got no ticket: %s", ticket_id, response.error
                )
                still_pending.add(ticket_id)
                continue
            self._cache_sideloads(response.data)
            _, _, failed_ids = await self._process_ticket_batch([ticket])
            still_pending.update(failed_ids)
        return still_pending

    async def _replace_changed_record_permissions(
        self, records_with_permissions: List[Tuple[Record, List[Permission]]]
    ) -> None:
        """Swap the grants of tickets that already existed for the ones just computed.

        on_new_records only adds permission edges, so a ticket moved to another group
        or reassigned to another requester would keep the old grants and stay readable
        by people Zendesk no longer lets see it. After a full sync the edges are gone
        already, and adding is correct.
        """
        if self._rebuild_ticket_edges:
            return
        for record, permissions in records_with_permissions:
            if record.version > 0 or self._refresh_ticket_grants:
                await self.data_entities_processor.on_updated_record_permissions(
                    record, permissions
                )

    def _is_deleted_ticket(self, ticket_data: Dict[str, Any]) -> bool:
        return str(ticket_data.get("status") or "").lower() == DELETED_TICKET_STATUS

    def _is_ticket_in_scope(self, ticket_data: Dict[str, Any]) -> bool:
        """Whether this ticket belongs in the graph at all under the current filters."""
        if self._is_deleted_ticket(ticket_data):
            return False
        # A groupless ticket is filed under Unassigned, which an "in" group filter
        # never selects.
        group_id = ticket_data.get("group_id")
        if not self._is_group_allowed_by_filter(str(group_id) if group_id else UNASSIGNED_GROUP_ID):
            return False
        return self._is_allowed_by_date_filters(
            self._parse_datetime(ticket_data.get("created_at")),
            self._parse_datetime(ticket_data.get("updated_at")),
        )

    async def _resolve_removable_record_ids(self, tickets: List[Dict[str, Any]]) -> List[str]:
        """Record ids for tickets that must not stay in the graph.

        Deleted at source, or no longer admitted by the filters. Skipping them instead
        leaves the records unreachable but still answering queries from the vector store.
        """
        record_ids: List[str] = []
        removable = [t for t in tickets if t.get("id") and not self._is_ticket_in_scope(t)]
        if not removable:
            return record_ids
        for ticket_data in removable:
            existing = await self.data_entities_processor.get_record_by_external_id(
                connector_id=self.connector_id,
                external_record_id=str(ticket_data["id"]),
            )
            if existing:
                record_ids.extend(await self._with_attachment_ids(existing))
        return record_ids

    async def _remove_records_outside_date_filters(self, record_type: RecordType) -> int:
        """Remove stored records a narrowed date filter no longer admits.

        The modified filter moves the export's start forward, so a full sync never
        re-reads, and so never removes, anything last changed before it.
        """
        if not any(
            self.sync_filters
            and self.sync_filters.get(key)
            and isinstance(self.sync_filters.get(key).get_value(default=None), tuple)
            for key in (SyncFilterKey.CREATED, SyncFilterKey.MODIFIED)
        ):
            return 0
        async with self.data_store_provider.transaction() as tx_store:
            held = await tx_store.get_records_by_record_type(
                self.connector_id, record_type.value
            )
        removed_ids: List[str] = []
        for record in held:
            if not self._is_allowed_by_date_filters(
                record.source_created_at, record.source_updated_at
            ):
                removed_ids.extend(await self._with_attachment_ids(record))
        if removed_ids:
            await self.data_entities_processor.on_records_deleted_cascade(
                removed_ids, self.connector_id
            )
        return len(removed_ids)

    async def _with_attachment_ids(self, record: Record) -> List[str]:
        """The record's id plus its attachments', for a cascade delete.

        Found by parent id, not by edge: a full sync wipes the ATTACHMENT edges the
        cascade follows, and a ticket the new filters drop would leave its files behind.
        """
        children = await self.data_entities_processor.get_records_by_parent(
            self.connector_id,
            record.external_record_id,
            record_type=RecordType.FILE.value,
        )
        return [record.id, *(child.id for child in children)]

    async def _ticket_to_record(
        self,
        ticket_data: Dict[str, Any],
        *,
        has_public_comment: bool = True,
        include_unchanged: bool = False,
        comments: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Tuple[Record, List[Permission]]]:
        ticket_id = ticket_data.get("id")
        group_id = ticket_data.get("group_id")
        if not ticket_id:
            return None
        # Guarded here rather than only at the call site so no caller can resurrect a
        # ticket the same page just removed.
        if not self._is_ticket_in_scope(ticket_data):
            return None

        created_at = self._parse_datetime(ticket_data.get("created_at"))
        updated_at = self._parse_datetime(ticket_data.get("updated_at"))

        existing_record = await self.data_entities_processor.get_record_by_external_id(
            connector_id=self.connector_id,
            external_record_id=str(ticket_id),
        )

        requester = self._user_data(ticket_data.get("requester_id"))
        assignee = self._user_data(ticket_data.get("assignee_id"))
        submitter = self._user_data(ticket_data.get("submitter_id"))
        unchanged = bool(
            existing_record
            and existing_record.source_updated_at == updated_at
            and not self._rebuild_ticket_edges
        )
        if unchanged and not include_unchanged:
            return None

        record_id = existing_record.id if existing_record else str(uuid4())
        if existing_record is None:
            version = 0
        else:
            version = existing_record.version + (0 if unchanged else 1)
        status = self.value_mapper.map_status(ticket_data.get("status")) or Status.UNKNOWN
        priority = self.value_mapper.map_priority(ticket_data.get("priority")) or Priority.UNKNOWN
        item_type = self.value_mapper.map_type(ticket_data.get("type")) or ItemType.UNKNOWN
        # A group we never synced would be auto-created unnamed, org-less and App-less.
        known_group = group_id is not None and str(group_id) in self._group_id_to_data
        external_group_id = f"group_{group_id}" if known_group else UNASSIGNED_GROUP_ID
        if group_id and not known_group:
            self.logger.warning(
                "Zendesk: ticket %s references unknown group %s — filing it under "
                "Unassigned rather than inventing a group", ticket_id, group_id,
            )

        record = TicketRecord(
            id=record_id,
            org_id=self.data_entities_processor.org_id,
            record_name=ticket_data.get("subject") or f"Zendesk ticket {ticket_id}",
            record_type=RecordType.TICKET,
            external_record_id=str(ticket_id),
            external_revision_id=(
                self._ticket_content_revision(ticket_data, comments)
                if comments is not None
                else str(updated_at) if updated_at else None
            ),
            external_record_group_id=external_group_id,
            record_group_type=RecordGroupType.PROJECT,
            version=version,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.ZENDESK,
            connector_id=self.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            weburl=self._ticket_web_url(ticket_id),
            created_at=get_epoch_timestamp_in_ms(),
            updated_at=get_epoch_timestamp_in_ms(),
            source_created_at=created_at,
            source_updated_at=updated_at,
            status=status,
            priority=priority,
            type=item_type,
            reporter_email=requester.get("email"),
            reporter_name=requester.get("name"),
            reporter_source_id=str(ticket_data.get("requester_id")) if ticket_data.get("requester_id") else None,
            assignee=assignee.get("name"),
            assignee_email=assignee.get("email"),
            assignee_source_id=[str(ticket_data.get("assignee_id"))] if ticket_data.get("assignee_id") else [],
            creator_email=submitter.get("email"),
            creator_name=submitter.get("name"),
            creator_source_timestamp=created_at,
            related_external_records=self._parse_ticket_links(ticket_data),
            labels=ticket_data.get("tags") or [],
            preview_renderable=False,
        )
        self._apply_indexing_filter(record, IndexingFilterKey.TICKETS)
        if unchanged:
            # The stored node is an untyped Record the Neo4j upsert rejects, so the
            # typed record is rebuilt; keeping the status avoids re-queueing it.
            record.indexing_status = existing_record.indexing_status
        permissions = self._record_permissions(
            group_id,
            requester,
            ticket_data.get("organization_id"),
            collaborators=ticket_data.get("collaborator_ids") or [],
            include_customer_access=has_public_comment,
            assignee_id=ticket_data.get("assignee_id"),
            requester_id=ticket_data.get("requester_id"),
        )
        return record, permissions

    def _parse_ticket_links(self, ticket_data: Dict[str, Any]) -> List[RelatedExternalRecord]:
        """Map Zendesk's ticket-to-ticket links onto RecordRelations.

        Zendesk names its link types structurally rather than as free text, so these
        map directly instead of going through ``map_relationship_type``. Targets that
        have not synced yet are fine — the processor stands up a placeholder record.
        """
        links: List[Tuple[Any, RecordRelations]] = [
            # problem_id sits on the incident and names its cause, so this edge
            # runs incident -> problem. The incident is also the ticket Zendesk
            # touches when the link changes, so it is the one that owns the edge.
            (ticket_data.get("problem_id"), RecordRelations.CAUSED_BY),
            # Only populated once the source ticket is closed.
            *((fid, RecordRelations.RELATED) for fid in ticket_data.get("followup_ids") or []),
        ]
        via_source = ((ticket_data.get("via") or {}).get("source") or {}).get("from") or {}
        links.append((via_source.get("ticket_id"), RecordRelations.RELATED))

        related: List[RelatedExternalRecord] = []
        seen: set[str] = set()
        self_id = str(ticket_data.get("id"))
        for target_id, relation_type in links:
            if not target_id:
                continue
            external_id = str(target_id)
            # A ticket linking to itself would be an edge the traversal never leaves.
            if external_id == self_id or external_id in seen:
                continue
            seen.add(external_id)
            related.append(RelatedExternalRecord(
                external_record_id=external_id,
                record_type=RecordType.TICKET,
                relation_type=relation_type,
            ))
        return related

    async def _sync_help_center_articles(self) -> int:
        # Sections first: a record with no record group is unreachable from the App.
        if not await self._sync_help_center_sections():
            return 0

        start_time = await self._get_start_time(ARTICLES_SYNC_POINT_KEY)
        article_state = await self.records_sync_point.read_sync_point(
            ARTICLES_SYNC_POINT_KEY
        )
        reset_id = str(article_state.get("resetId") or uuid4())
        if article_state.get("resetId") != reset_id:
            await self.records_sync_point.update_sync_point(
                ARTICLES_SYNC_POINT_KEY,
                {**article_state, "resetId": reset_id},
            )
        articles, articles_complete, max_end_time = await self._fetch_incremental_articles(
            start_time
        )
        if not articles_complete:
            # A short list would read as "deleted" to the removal pass below, and
            # advancing past a truncated window would skip those articles for good.
            self._skip("Help Center articles")
            self.logger.error(
                "Zendesk: article export truncated — leaving the sync point at %s so the "
                "next run re-reads the missing window", start_time,
            )
            return 0
        removed_ids = await self._resolve_removable_article_ids(articles)
        if removed_ids:
            await self.data_entities_processor.on_records_deleted_cascade(
                removed_ids, self.connector_id
            )
            self.logger.info(
                f"Zendesk: removed {len(removed_ids)} articles and attachments no longer "
                "published org-wide"
            )
        await self._remove_vanished_articles()

        await self._resolve_missing_sections(articles)

        records_with_permissions: List[Tuple[Record, List[Permission]]] = []
        # Articles have no retry list, so a failure holds the checkpoint back instead.
        article_failed = False
        for article_data in articles:
            try:
                record_tuple = await self._article_to_record(
                    article_data, include_unchanged=True
                )
            except Exception:
                self.logger.exception(
                    "Zendesk: skipping article %s this sync", article_data.get("id")
                )
                article_failed = True
                continue
            if record_tuple:
                records_with_permissions.append(record_tuple)
        for start in range(0, len(records_with_permissions), BATCH_PROCESSING_SIZE):
            await self.data_entities_processor.on_new_records(
                records_with_permissions[start:start + BATCH_PROCESSING_SIZE]
            )
        # After the articles are published, so the parent exists before its children.
        for record, permissions in records_with_permissions:
            existing_children = await self.data_entities_processor.get_records_by_parent(
                self.connector_id,
                record.external_record_id,
                record_type=RecordType.FILE.value,
            )
            try:
                children = await self._build_article_attachment_child_records(
                    record.external_record_id.removeprefix("article_"),
                    record,
                    permissions,
                    refresh_permissions=True,
                )
            except Exception:
                self.logger.exception(
                    "Zendesk: attachments of %s failed this sync", record.external_record_id
                )
                article_failed = True
                continue
            current_ids = {child.child_id for child in children}
            stale_ids = [
                child.id for child in existing_children if child.id not in current_ids
            ]
            if stale_ids:
                await self.data_entities_processor.on_records_deleted_cascade(
                    stale_ids, self.connector_id
                )

        if article_failed:
            self._skip("Help Center articles")
            self.logger.error(
                "Zendesk: some articles failed — leaving the sync point at %s so the "
                "next run re-reads them", start_time,
            )
            return len(records_with_permissions)
        now_seconds = get_epoch_timestamp_in_ms() // 1000
        max_end_time = min(max_end_time, now_seconds - INCREMENTAL_SAFETY_LAG_SECONDS)
        if not await self._sync_reset_is_current(ARTICLES_SYNC_POINT_KEY, reset_id):
            self.logger.warning(
                "Zendesk: ignoring article checkpoint from a superseded sync"
            )
            return 0
        if self._rebuild_article_edges:
            removed_articles = await self._remove_records_outside_date_filters(
                RecordType.WEBPAGE
            )
            if removed_articles:
                self.logger.info(
                    f"Zendesk: removed {removed_articles} articles and attachments "
                    "outside the date filters"
                )
        remaining_stale_groups = await self._delete_empty_record_groups(
            self._stale_help_center_group_ids
        )
        await self.records_sync_point.update_sync_point(
            ARTICLES_SYNC_POINT_KEY,
            {
                "lastEndTime": max_end_time,
                "resetId": reset_id,
                "updatedAt": get_epoch_timestamp_in_ms(),
            },
        )
        await self.records_sync_point.update_sync_point(
            HELP_CENTER_GROUPS_STATE_KEY,
            {
                "groupIds": sorted(
                    set(self._current_help_center_group_ids)
                    | set(remaining_stale_groups)
                ),
                "updatedAt": get_epoch_timestamp_in_ms(),
            },
        )
        return len(records_with_permissions)

    async def _delete_empty_record_groups(self, external_group_ids: List[str]) -> List[str]:
        """Delete removed folders once their records and child folders have moved.

        Returns the ones still holding something, to be retried next sync.
        """
        remaining: List[str] = []
        for external_group_id in external_group_ids:
            async with self.data_store_provider.transaction() as tx_store:
                group = await tx_store.get_record_group_by_external_id(
                    connector_id=self.connector_id,
                    external_id=external_group_id,
                )
                if not group:
                    continue
                records = await tx_store.get_records_by_status(
                    org_id=self.data_entities_processor.org_id,
                    connector_id=self.connector_id,
                    status_filters=None,
                    limit=1,
                    record_group_id=group.id,
                )
                edges = await tx_store.get_edges_to_node(
                    f"{CollectionNames.RECORD_GROUPS.value}/{group.id}",
                    CollectionNames.BELONGS_TO.value,
                )
                has_child_groups = any(
                    (edge.get("_from") or "").startswith(
                        f"{CollectionNames.RECORD_GROUPS.value}/"
                    )
                    for edge in edges
                )
            if records or has_child_groups:
                remaining.append(external_group_id)
                continue
            deleted = await self.data_entities_processor.on_record_group_deleted(
                external_group_id,
                self.connector_id,
            )
            if not deleted:
                remaining.append(external_group_id)
        return remaining

    async def _remove_vanished_articles(self) -> None:
        """Delete articles Zendesk stopped listing, once Zendesk confirms each one.

        The incremental export never reports a deletion or an archive: the article just
        stops appearing, and its record would otherwise be served indefinitely.
        """
        datasource = await self._get_fresh_datasource()
        listed, complete = await self._fetch_paginated_list_checked(
            datasource.list_articles, "articles", offset_only=True
        )
        if not complete:
            self._skip("deleted Help Center articles")
            self.logger.error(
                "Zendesk: article list truncated — skipping the deleted-article check"
            )
            return
        current = {str(a["id"]) for a in listed if a.get("id") is not None}
        state = await self.records_sync_point.read_sync_point(ARTICLE_IDS_STATE_KEY)
        if "articleIds" in state:
            previous = set(state["articleIds"] or [])
        else:
            # A full sync wipes this list; an article archived before it would never be
            # compared again, so start from the articles the graph still holds.
            async with self.data_store_provider.transaction() as tx_store:
                held = await tx_store.get_records_by_record_type(
                    self.connector_id, RecordType.WEBPAGE.value
                )
            previous = {
                r.external_record_id.removeprefix("article_") for r in held
                if (r.external_record_id or "").startswith("article_")
            }
        removed_ids: List[str] = []
        for article_id in sorted(previous - current):
            try:
                response = await self._call_api_with_retry(
                    datasource.show_article,
                    "zendesk/show_article",
                    article_id=int(article_id),
                )
            except httpx.HTTPStatusError as e:
                self.logger.warning(
                    "Zendesk: could not confirm article %s is gone: %s", article_id, e
                )
                current.add(article_id)
                continue
            if response.status_code != HttpStatusCode.NOT_FOUND.value:
                article = (
                    self._extract_required_object(response.data, "article")
                    if response.success
                    else {}
                )
                if not article or self._is_article_in_scope(article):
                    # Still live, or unconfirmed: look again next sync.
                    current.add(article_id)
                    continue
            existing = await self.data_entities_processor.get_record_by_external_id(
                connector_id=self.connector_id,
                external_record_id=f"article_{article_id}",
            )
            if existing:
                removed_ids.extend(await self._with_attachment_ids(existing))
        if removed_ids:
            await self.data_entities_processor.on_records_deleted_cascade(
                removed_ids, self.connector_id
            )
            self.logger.info(
                f"Zendesk: removed {len(removed_ids)} articles and attachments deleted or "
                "archived at source"
            )
        await self.records_sync_point.update_sync_point(
            ARTICLE_IDS_STATE_KEY,
            {"articleIds": sorted(current), "updatedAt": get_epoch_timestamp_in_ms()},
        )

    async def _delete_removed_ticket_group_folders(self) -> None:
        """Remove the folder of a group Zendesk deleted, or the filter dropped.

        Only after a complete ticket pass, which is what moves a deleted group's
        changed tickets under Unassigned.
        """
        previous = await self.records_sync_point.read_sync_point(TICKET_GROUPS_STATE_KEY)
        current = set(self._current_ticket_group_ids)
        # A filter change forces a full sync, which wipes the saved state above, so
        # the groups the filter now drops are named from the source as well.
        deselected = {
            f"group_{group_id}"
            for group_id in self._group_id_to_data
            if not self._is_group_allowed_by_filter(group_id)
        }
        if not self._is_group_allowed_by_filter(UNASSIGNED_GROUP_ID):
            deselected.add(UNASSIGNED_GROUP_ID)
        stale = sorted(
            (set(previous.get("groupIds") or []) | deselected | self._deleted_ticket_group_ids)
            - current
        )
        remaining = await self._delete_empty_record_groups(stale) if stale else []
        if remaining:
            # Zendesk does not touch a deleted group's tickets, so the export never
            # re-files them; re-read them here or the folder outlives its group.
            await self._retry_pending_tickets(await self._ticket_ids_in_groups(remaining))
            remaining = await self._delete_empty_record_groups(remaining)
        await self.records_sync_point.update_sync_point(
            TICKET_GROUPS_STATE_KEY,
            {
                "groupIds": sorted(current | set(remaining)),
                "updatedAt": get_epoch_timestamp_in_ms(),
            },
        )

    async def _ticket_ids_in_groups(self, external_group_ids: List[str]) -> List[str]:
        ticket_ids: List[str] = []
        for external_group_id in external_group_ids:
            async with self.data_store_provider.transaction() as tx_store:
                group = await tx_store.get_record_group_by_external_id(
                    connector_id=self.connector_id, external_id=external_group_id
                )
                after_key: Optional[str] = None
                while group:
                    records = await tx_store.get_records_by_status(
                        org_id=self.data_entities_processor.org_id,
                        connector_id=self.connector_id,
                        status_filters=None,
                        limit=BATCH_PROCESSING_SIZE,
                        record_group_id=group.id,
                        after_key=after_key,
                    )
                    if not records or records[-1].id == after_key:
                        break
                    ticket_ids.extend(
                        r.external_record_id for r in records
                        if getattr(r.record_type, "value", r.record_type) == RecordType.TICKET.value
                    )
                    if len(records) < BATCH_PROCESSING_SIZE:
                        break
                    after_key = records[-1].id
        return ticket_ids

    async def _sync_reset_is_current(self, sync_point_key: str, reset_id: str) -> bool:
        state = await self.records_sync_point.read_sync_point(sync_point_key)
        return state.get("resetId") == reset_id

    async def _fetch_incremental_articles(
        self, start_time: int
    ) -> Tuple[List[Dict[str, Any]], bool, int]:
        """Walk the Help Center incremental export from ``start_time``.

        Not ``list_articles``: that pages by offset and Zendesk 400s past 10,000
        records, so a large Help Center could never finish a first sync.
        """
        articles: List[Dict[str, Any]] = []
        articles_by_id: Dict[str, Dict[str, Any]] = {}
        max_end_time = start_time
        complete = True
        while True:
            response = await self._call_incremental(
                "incremental_articles", start_time=start_time
            )
            if response is None or not response.success:
                error = response.error if response else "retries exhausted"
                self.logger.error(f"Zendesk incremental_articles failed: {error}")
                complete = False
                break
            if not response.data:
                self.logger.error("Zendesk incremental_articles returned no payload")
                complete = False
                break
            payload = response.data
            if not isinstance(payload, dict) or not isinstance(payload.get("articles"), list):
                self.logger.error("Zendesk incremental_articles omitted articles")
                complete = False
                break
            self._cache_sideloads(payload)
            for article in self._extract_list(payload, "articles"):
                article_id = article.get("id")
                if article_id is None:
                    continue
                key = str(article_id)
                previous = articles_by_id.get(key)
                article_updated = self._parse_datetime(article.get("updated_at")) or 0
                previous_updated = (
                    self._parse_datetime(previous.get("updated_at")) or 0
                    if previous
                    else -1
                )
                if article_updated >= previous_updated:
                    articles_by_id[key] = article
            end_time = payload.get("end_time")
            if end_time:
                max_end_time = max(max_end_time, int(end_time))
            if payload.get("end_of_stream", True):
                break
            if not end_time or end_time <= start_time:
                self.logger.error(
                    "Zendesk incremental_articles stopped advancing before end_of_stream"
                )
                complete = False
                break
            start_time = end_time
        articles.extend(articles_by_id.values())
        return articles, complete, max_end_time

    async def _sync_help_center_sections(self) -> bool:
        """Publish the Help Center tree: category -> section -> subsection.

        Zendesk nests up to five section levels under a flat top-level category, and
        ``parent_external_group_id`` is what turns that into RecordGroup edges. Filed
        flat, a subsection's articles land under an invented group with no org and no
        edge to the App: present in the graph, unreachable in the UI.
        """
        datasource = await self._get_fresh_datasource()
        categories, categories_complete = await self._fetch_paginated_list_checked(
            datasource.list_categories, "categories"
        )
        if not categories_complete:
            self._skip("Help Center")
            self.logger.error(
                "Zendesk: category list truncated - sections would be filed under a "
                "parent that does not exist yet, so skipping this pass"
            )
            return False
        sections, sections_complete = await self._fetch_paginated_list_checked(
            datasource.list_sections, "sections"
        )
        if not sections_complete:
            self._skip("Help Center")
            self.logger.error(
                "Zendesk: section list truncated - articles under the missing sections "
                "would be filed under an invented record group, so skipping this pass"
            )
            return False

        previous_groups = await self.records_sync_point.read_sync_point(
            HELP_CENTER_GROUPS_STATE_KEY
        )
        self._current_help_center_group_ids = [
            *(f"category_{category['id']}" for category in categories if category.get("id")),
            *(f"section_{section['id']}" for section in sections if section.get("id")),
        ]
        previous_ids = set(previous_groups.get("groupIds") or [])
        self._stale_help_center_group_ids = sorted(
            previous_ids - set(self._current_help_center_group_ids),
            key=lambda external_id: external_id.startswith("category_"),
        )

        record_groups: List[Tuple[RecordGroup, List[Permission]]] = []
        for category_data in categories:
            category_id = category_data.get("id")
            if not category_id:
                continue
            self._category_id_to_data[str(category_id)] = category_data
            record_groups.append(self._category_record_group(category_data))
        section_map = {
            str(section.get("id")): section
            for section in sections
            if section.get("id")
        }
        for section_data in sorted(
            sections, key=lambda item: self._section_depth(item, section_map)
        ):
            section_id = section_data.get("id")
            if not section_id:
                continue
            self._section_id_to_data[str(section_id)] = section_data
            record_groups.append(self._section_record_group(section_data))

        if record_groups:
            await self._detach_moved_section_edges(record_groups)
            await self.data_entities_processor.on_new_record_groups(record_groups)
        self.logger.info(
            f"Zendesk: synced {len(categories)} Help Center categories and "
            f"{len(sections)} sections"
        )
        return True

    def _category_record_group(
        self, category_data: Dict[str, Any]
    ) -> Tuple[RecordGroup, List[Permission]]:
        category_id = category_data.get("id")
        return (
            RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=category_data.get("name") or f"Category {category_id}",
                external_group_id=f"category_{category_id}",
                connector_name=Connectors.ZENDESK,
                connector_id=self.connector_id,
                group_type=RecordGroupType.KB,
                description=category_data.get("description") or None,
                source_created_at=self._parse_datetime(category_data.get("created_at")),
                source_updated_at=self._parse_datetime(category_data.get("updated_at")),
                web_url=category_data.get("html_url"),
            ),
            self._kb_permissions(),
        )

    def _section_record_group(
        self, section_data: Dict[str, Any]
    ) -> Tuple[RecordGroup, List[Permission]]:
        section_id = section_data.get("id")
        # A subsection hangs off its parent section, a top-level one off its category.
        parent_section_id = section_data.get("parent_section_id")
        category_id = section_data.get("category_id")
        if parent_section_id:
            parent = f"section_{parent_section_id}"
        elif category_id:
            parent = f"category_{category_id}"
        else:
            parent = None
        return (
            RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=section_data.get("name") or f"Section {section_id}",
                external_group_id=f"section_{section_id}",
                parent_external_group_id=parent,
                connector_name=Connectors.ZENDESK,
                connector_id=self.connector_id,
                group_type=RecordGroupType.KB,
                description=section_data.get("description") or None,
                source_created_at=self._parse_datetime(section_data.get("created_at")),
                source_updated_at=self._parse_datetime(section_data.get("updated_at")),
                web_url=section_data.get("html_url"),
            ),
            self._kb_permissions(),
        )

    def _kb_permissions(self) -> List[Permission]:
        """Categories and sections carry no ACL of their own in Zendesk - visibility is
        derived from the articles inside them, and only public articles are synced."""
        return [Permission(
            type=PermissionType.READ,
            entity_type=EntityType.ORG,
            external_id=self.data_entities_processor.org_id,
        )]

    async def _resolve_missing_sections(self, articles: List[Dict[str, Any]]) -> None:
        """Fetch any section an article references that the section list did not return.

        Zendesk does not document whether ``list_sections`` includes subsections, and
        on some tenants it does not. Without this, those articles are filed under a
        record group the processor invents - unnamed, org-less and with no edge to the
        App, so the article never appears in the UI.
        """
        wanted = {
            str(article["section_id"])
            for article in articles
            if article.get("section_id")
            and str(article["section_id"]) not in self._section_id_to_data
        }
        if not wanted:
            return
        self.logger.info(
            f"Zendesk: {len(wanted)} section(s) referenced by articles were missing from "
            "the section list - resolving them individually"
        )
        datasource = await self._get_fresh_datasource()
        resolved: List[Tuple[RecordGroup, List[Permission]]] = []
        pending = list(wanted)
        while pending:
            section_id = pending.pop()
            if section_id in self._section_id_to_data:
                continue
            response = await self._call_api_with_retry(
                datasource.show_section,
                "zendesk/show_section",
                section_id=int(section_id),
            )
            if not response.success or not response.data:
                raise RuntimeError(
                    f"Could not resolve Zendesk section {section_id}: {response.error}"
                )
            section_data = self._extract_required_object(response.data, "section")
            if not section_data.get("id"):
                raise RuntimeError(
                    f"Zendesk returned no section object for section {section_id}"
                )
            self._section_id_to_data[section_id] = section_data
            external_group_id = f"section_{section_id}"
            if external_group_id not in self._current_help_center_group_ids:
                self._current_help_center_group_ids.append(external_group_id)
            resolved.append(self._section_record_group(section_data))
            # Walk up: an unlisted subsection's parent may be unlisted too.
            parent_section_id = section_data.get("parent_section_id")
            if parent_section_id and str(parent_section_id) not in self._section_id_to_data:
                pending.append(str(parent_section_id))
        if resolved:
            await self._detach_moved_section_edges(resolved)
            await self.data_entities_processor.on_new_record_groups(
                sorted(
                    resolved,
                    key=lambda item: self._section_depth(
                        self._section_id_to_data[
                            item[0].external_group_id.removeprefix("section_")
                        ],
                        self._section_id_to_data,
                    ),
                )
            )

    async def _detach_moved_section_edges(
        self, groups: List[Tuple[RecordGroup, List[Permission]]]
    ) -> None:
        """Replace a section's old parent/app edge when its hierarchy changes."""
        async with self.data_store_provider.transaction() as tx_store:
            for group, _ in groups:
                existing = await tx_store.get_record_group_by_external_id(
                    connector_id=self.connector_id,
                    external_id=group.external_group_id,
                )
                if (
                    not existing
                    or existing.parent_external_group_id == group.parent_external_group_id
                ):
                    continue
                node_id = f"{CollectionNames.RECORD_GROUPS.value}/{existing.id}"
                edges = await tx_store.get_edges_from_node(
                    node_id, CollectionNames.BELONGS_TO.value
                )
                for edge in edges:
                    target = edge.get("_to") or ""
                    collection, _, target_id = target.partition("/")
                    if collection not in {
                        CollectionNames.RECORD_GROUPS.value,
                        CollectionNames.APPS.value,
                    } or not target_id:
                        continue
                    await tx_store.delete_edge(
                        existing.id,
                        CollectionNames.RECORD_GROUPS.value,
                        target_id,
                        collection,
                        CollectionNames.BELONGS_TO.value,
                    )

    @staticmethod
    def _section_depth(
        section_data: Dict[str, Any], sections_by_id: Dict[str, Dict[str, Any]]
    ) -> int:
        depth = 0
        parent_id = section_data.get("parent_section_id")
        seen = {str(section_data.get("id"))}
        while parent_id and str(parent_id) in sections_by_id:
            parent_key = str(parent_id)
            if parent_key in seen:
                break
            seen.add(parent_key)
            depth += 1
            parent_id = sections_by_id[parent_key].get("parent_section_id")
        return depth

    def _is_article_in_scope(self, article_data: Dict[str, Any]) -> bool:
        """Whether this article may be published to the whole tenant.

        user_segment_id is the article's entire ACL and segments are not synced, so one
        that becomes restricted must lose the org-wide grant it already has.
        """
        if (
            article_data.get("draft")
            or article_data.get("published") is False
            or str(article_data.get("status") or "").lower()
            in {"draft", "archived", "deleted", "unpublished"}
        ):
            return False
        if article_data.get("user_segment_id") is not None or article_data.get("user_segment_ids"):
            return False
        return self._is_allowed_by_date_filters(
            self._parse_datetime(article_data.get("created_at")),
            self._parse_datetime(article_data.get("updated_at")),
        )

    async def _resolve_removable_article_ids(self, articles: List[Dict[str, Any]]) -> List[str]:
        """Record ids for articles that must not stay published."""
        record_ids: List[str] = []
        removable = [a for a in articles if a.get("id") and not self._is_article_in_scope(a)]
        if not removable:
            return record_ids
        for article_data in removable:
            existing = await self.data_entities_processor.get_record_by_external_id(
                connector_id=self.connector_id,
                external_record_id=f"article_{article_data['id']}",
            )
            if existing:
                record_ids.extend(await self._with_attachment_ids(existing))
        return record_ids

    async def _article_to_record(
        self,
        article_data: Dict[str, Any],
        *,
        include_unchanged: bool = False,
    ) -> Optional[Tuple[Record, List[Permission]]]:
        article_id = article_data.get("id")
        if not article_id:
            return None

        if not self._is_article_in_scope(article_data):
            return None

        created_at = self._parse_datetime(article_data.get("created_at"))
        updated_at = self._parse_datetime(article_data.get("updated_at"))

        existing_record = await self.data_entities_processor.get_record_by_external_id(
            connector_id=self.connector_id,
            external_record_id=f"article_{article_id}",
        )
        unchanged = bool(
            existing_record
            and existing_record.source_updated_at == updated_at
            and not self._rebuild_article_edges
        )
        if unchanged and not include_unchanged:
            return None

        record_id = existing_record.id if existing_record else str(uuid4())
        if existing_record is None:
            version = 0
        else:
            version = existing_record.version + (0 if unchanged else 1)
        # Guarded like _ticket_to_record: an unknown section would be auto-created
        # by the processor with no org and no App edge, hiding the article.
        section_id = article_data.get("section_id")
        known_section = section_id is not None and str(section_id) in self._section_id_to_data
        external_group_id = f"section_{section_id}" if known_section else None
        if section_id and not known_section:
            self.logger.warning(
                "Zendesk: article %s references unknown section %s - filing it "
                "without a record group rather than inventing one", article_id, section_id,
            )
        record = WebpageRecord(
            id=record_id,
            org_id=self.data_entities_processor.org_id,
            record_name=article_data.get("title") or f"Zendesk article {article_id}",
            record_type=RecordType.WEBPAGE,
            external_record_id=f"article_{article_id}",
            external_revision_id=self._content_revision(article_data.get("body") or ""),
            external_record_group_id=external_group_id,
            record_group_type=RecordGroupType.KB if external_group_id else None,
            version=version,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.ZENDESK,
            connector_id=self.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            weburl=article_data.get("html_url") or article_data.get("url"),
            created_at=get_epoch_timestamp_in_ms(),
            updated_at=get_epoch_timestamp_in_ms(),
            source_created_at=created_at,
            source_updated_at=updated_at,
            preview_renderable=False,
        )
        self._apply_indexing_filter(record, IndexingFilterKey.KNOWLEDGE_BASE)
        if unchanged:
            record.indexing_status = existing_record.indexing_status
        # Restricted articles were filtered out above, so ORG is the right grant.
        return record, self._article_permissions()

    def _article_permissions(self) -> List[Permission]:
        return [
            Permission(
                type=PermissionType.READ,
                entity_type=EntityType.ORG,
                external_id=self.data_entities_processor.org_id,
            )
        ]

    async def stream_record(
        self,
        record: Record,
        user_id: Optional[str] = None,
        convertTo: Optional[str] = None,
    ) -> StreamingResponse:
        # After a restart the first download can arrive before any sync loaded these.
        if self.indexing_filters is None:
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service,
                "zendesk",
                self.connector_id,
                self.logger,
            )
        # A linked ticket that has not synced yet: nothing was checked against the filters.
        if getattr(record, "is_placeholder", False) is True:
            raise not_found_at_source(self.display_name)
        try:
            if record.record_type == RecordType.FILE:
                # Attachment bytes are not a BlocksContainer.
                return create_stream_record_response(
                    await self._process_file_for_streaming(record),
                    filename=record.record_name,
                    mime_type=record.mime_type or MimeTypes.UNKNOWN.value,
                    fallback_filename=record.external_record_id,
                )

            if record.record_type == RecordType.TICKET:
                content = await self._process_ticket_blockgroups_for_streaming(record)
            elif record.record_type == RecordType.WEBPAGE:
                content = await self._process_article_blockgroups_for_streaming(record)
            else:
                raise ValueError(f"Unsupported Zendesk record type: {record.record_type}")
        except ZendeskAuthError as e:
            # 409 reconnect, never 401: the frontend treats a 401 as its own session ending.
            raise map_source_status(
                HttpStatusCode.UNAUTHORIZED.value, connector=self.display_name
            ) from e
        return StreamingResponse(iter([content]), media_type=MimeTypes.BLOCKS.value)

    async def _fetch_public_comments(self, ticket_id: str) -> List[Dict[str, Any]]:
        """A ticket's public comments, oldest first.

        public=False is an internal agent note. This ticket grants the requester READ,
        so indexing one would hand it to the customer.
        """
        datasource = await self._get_fresh_datasource()
        comments, complete = await self._fetch_paginated_list_checked(
            datasource.list_comments,
            "comments",
            ticket_id=int(ticket_id),
            sort_order="asc",
            include="users",
        )
        if not complete:
            raise RuntimeError(
                f"Failed to read every comment for Zendesk ticket {ticket_id}"
            )
        return [comment for comment in comments if comment.get("public") is True]

    async def _sync_ticket_attachments(
        self,
        ticket_record: Record,
        permissions: List[Permission],
        *,
        comments: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Publish the ticket's comment attachments as records during the sync.

        Zendesk hangs attachments off comments rather than the ticket, and the
        incremental export cannot sideload them, so this costs one call per changed
        ticket. Jira gets the same records for free from ``fields.attachment``.
        """
        if comments is None:
            comments = await self._fetch_public_comments(ticket_record.external_record_id)
        existing_children = await self.data_entities_processor.get_records_by_parent(
            self.connector_id,
            ticket_record.external_record_id,
            record_type=RecordType.FILE.value,
        )
        current_ids: set[str] = set()
        for comment in comments:
            children = await self._build_attachment_child_records(
                comment, ticket_record, permissions, refresh_permissions=True
            )
            current_ids.update(child.child_id for child in children)
        stale_ids = [child.id for child in existing_children if child.id not in current_ids]
        if stale_ids:
            await self.data_entities_processor.on_records_deleted_cascade(
                stale_ids, self.connector_id
            )

    async def _fetch_ticket_permissions(self, ticket_id: str) -> List[Permission]:
        """Re-derive a ticket's grants for the streaming path, which only has its Record."""
        datasource = await self._get_fresh_datasource()
        # The requester is usually an end user, who is not held between syncs.
        response = await self._call_api_with_retry(
            datasource.show_ticket,
            "zendesk/show_ticket",
            ticket_id=int(ticket_id),
            include="users",
        )
        ticket = self._extract_required_object(response.data, "ticket") if response.success else None
        if not ticket:
            raise ValueError(f"Failed to fetch Zendesk ticket {ticket_id} for its permissions")
        self._cache_sideloads(response.data)
        return self._record_permissions(
            ticket.get("group_id"),
            self._user_data(ticket.get("requester_id")),
            ticket.get("organization_id"),
            collaborators=ticket.get("collaborator_ids") or [],
            assignee_id=ticket.get("assignee_id"),
            requester_id=ticket.get("requester_id"),
        )

    async def _process_ticket_blockgroups_for_streaming(self, record: Record) -> bytes:
        comments = await self._fetch_public_comments(record.external_record_id)
        permissions: List[Permission] = []
        if any(comment.get("attachments") for comment in comments):
            permissions = await self._fetch_ticket_permissions(record.external_record_id)
        block_groups: List[BlockGroup] = []
        for index, comment in enumerate(comments):
            body = comment.get("html_body") or comment.get("body") or ""
            if "<" in body and ">" in body:
                body = html_to_markdown(await self._inline_images_as_base64(body))
            children_records = await self._build_attachment_child_records(
                comment, record, permissions, publish_records=False
            )
            author = self._user_data(comment.get("author_id"))
            author_id = comment.get("author_id")
            author_name = (
                "Zendesk"
                if str(author_id) == "-1"
                else author.get("name") or author_id or "Unknown"
            )
            is_description = index == 0
            block_groups.append(BlockGroup(
                id=str(uuid4()),
                index=index,
                parent_index=None if is_description else 0,
                name="Description" if is_description else f"Comment by {author_name}",
                type=GroupType.TEXT_SECTION,
                sub_type=GroupSubType.CONTENT if is_description else GroupSubType.COMMENT,
                description="Ticket description" if is_description else "Ticket comment",
                source_group_id=str(comment.get("id") or f"{record.external_record_id}_{index}"),
                data=body,
                format=DataFormat.MARKDOWN,
                weburl=record.weburl,
                requires_processing=True,
                children_records=children_records or None,
            ))
        if not block_groups:
            block_groups.append(BlockGroup(
                id=str(uuid4()),
                index=0,
                name=record.record_name,
                type=GroupType.TEXT_SECTION,
                sub_type=GroupSubType.CONTENT,
                source_group_id=f"{record.external_record_id}_description",
                data=f"# {record.record_name}",
                format=DataFormat.MARKDOWN,
                weburl=record.weburl,
                requires_processing=True,
            ))
        self._populate_block_group_children(block_groups)
        return BlocksContainer(blocks=[], block_groups=block_groups).model_dump_json(indent=2).encode("utf-8")

    async def _process_article_blockgroups_for_streaming(self, record: Record) -> bytes:
        datasource = await self._get_fresh_datasource()
        article_id = record.external_record_id.replace("article_", "")
        response = await self._call_api_with_retry(
            datasource.show_article,
            "zendesk/show_article",
            article_id=int(article_id),
        )
        if not response.success or not response.data:
            raise Exception(f"Failed to fetch Zendesk article {article_id}")
        article = self._extract_required_object(response.data, "article")
        if not article or str(article.get("id")) != str(article_id):
            raise Exception(f"Zendesk returned no article {article_id}")
        if not self._is_article_in_scope(article):
            raise not_found_at_source(self.display_name)
        body = article.get("body") or ""
        body_md = html_to_markdown(await self._inline_images_as_base64(body)) if body else ""
        children_records = await self._build_article_attachment_child_records(
            article_id,
            record,
            self._article_permissions(),
            publish_records=False,
        )
        block_groups: List[BlockGroup] = [BlockGroup(
            id=str(uuid4()),
            index=0,
            name=article.get("title") or record.record_name,
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.CONTENT,
            description="Article body",
            source_group_id=str(article_id),
            data=body_md,
            format=DataFormat.MARKDOWN,
            weburl=record.weburl,
            requires_processing=True,
            children_records=children_records or None,
        )]

        # Comments hang off the body the way ticket comments hang off the description.
        # An article comment has no ACL of its own: it is readable by whoever can read
        # the article, and only org-wide-public articles are synced.
        comments = await self._fetch_paginated_list(
            datasource.list_article_comments,
            "comments",
            article_id=int(article_id),
            sort_order="asc",
        )
        for index, comment in enumerate(comments, start=1):
            comment_body = comment.get("body") or ""
            if "<" in comment_body and ">" in comment_body:
                comment_body = html_to_markdown(
                    await self._inline_images_as_base64(comment_body)
                )
            author = self._user_data(comment.get("author_id"))
            author_id = comment.get("author_id")
            author_name = (
                "Zendesk"
                if str(author_id) == "-1"
                else author.get("name") or author_id or "Unknown"
            )
            block_groups.append(BlockGroup(
                id=str(uuid4()),
                index=index,
                parent_index=0,
                name=f"Comment by {author_name}",
                type=GroupType.TEXT_SECTION,
                sub_type=GroupSubType.COMMENT,
                description="Article comment",
                source_group_id=str(comment.get("id") or f"{article_id}_comment_{index}"),
                data=comment_body,
                format=DataFormat.MARKDOWN,
                weburl=comment.get("html_url") or record.weburl,
                requires_processing=True,
            ))

        self._populate_block_group_children(block_groups)
        return BlocksContainer(blocks=[], block_groups=block_groups).model_dump_json(indent=2).encode("utf-8")

    async def _inline_images_as_base64(self, html: str) -> str:
        if not html or "<img" not in html.lower():
            return html
        datasource = await self._get_fresh_datasource()
        resolved: Dict[str, str] = {}
        for match in IMG_SRC_PATTERN.finditer(html):
            url = match.group(2)
            if url in resolved or url.startswith("data:"):
                continue
            # Same host rule as a download: tenant host gets the token, the shared CDN
            # gets a bare client. Skipping the CDN would drop the image entirely, since
            # an embedded image is deliberately not given a FileRecord.
            if not self._is_safe_zendesk_asset_url(url):
                continue
            data_uri = await self._fetch_image_as_data_uri(datasource, url)
            if data_uri:
                resolved[url] = data_uri
        if not resolved:
            return html
        return IMG_SRC_PATTERN.sub(
            lambda m: f"{m.group(1)}{resolved.get(m.group(2), m.group(2))}{m.group(3)}",
            html,
        )

    async def _fetch_image_as_data_uri(self, datasource: ZendeskDataSource, url: str) -> Optional[str]:
        safe_url = redact_attachment_url(url)
        try:
            status, raw, mime = await self._fetch_asset(datasource, url)
            if status >= HTTP_ERROR_STATUS:
                self.logger.warning(f"Zendesk inline image {safe_url} returned {status}")
                return None
            if len(raw) > MAX_INLINE_IMAGE_BYTES:
                self.logger.warning(f"Skipping oversized Zendesk inline image ({len(raw)} bytes): {safe_url}")
                return None
            if not mime.startswith("image/"):
                return None
            return f"data:{mime};base64,{base64.b64encode(raw).decode('utf-8')}"
        except Exception as e:
            self.logger.warning(
                f"Could not inline Zendesk image {safe_url}: {redact_attachment_url(str(e))}"
            )
            return None

    async def _build_attachment_child_records(
        self,
        comment: Dict[str, Any],
        parent_record: Record,
        permissions: List[Permission],
        *,
        refresh_permissions: bool = False,
        publish_records: bool = True,
    ) -> List[ChildRecord]:
        # An attachment has no ACL of its own — it inherits its comment's.
        if comment.get("public") is not True:
            return []
        return await self._emit_attachment_records(
            comment.get("attachments") or [],
            parent_record,
            f"ticket_{parent_record.external_record_id}_comment_{comment.get('id')}",
            self._rebuild_ticket_edges,
            permissions,
            refresh_permissions=refresh_permissions,
            publish_records=publish_records,
        )

    async def _build_article_attachment_child_records(
        self,
        article_id: str,
        parent_record: Record,
        permissions: List[Permission],
        *,
        refresh_permissions: bool = False,
        publish_records: bool = True,
    ) -> List[ChildRecord]:
        """FileRecords for an article's non-inline attachments.

        Inline ones are already embedded in the body as base64 by
        ``_inline_images_as_base64``; emitting them again would index them twice.
        """
        datasource = await self._get_fresh_datasource()
        attachments, complete = await self._fetch_paginated_list_checked(
            datasource.list_article_attachments,
            "article_attachments",
            article_id=int(article_id),
        )
        if not complete:
            raise RuntimeError(
                f"Failed to read every attachment for Zendesk article {article_id}"
            )
        return await self._emit_attachment_records(
            attachments,
            parent_record,
            parent_record.external_record_id,
            self._rebuild_article_edges,
            permissions,
            refresh_permissions=refresh_permissions,
            publish_records=publish_records,
        )

    async def _emit_attachment_records(
        self,
        attachments: List[Dict[str, Any]],
        parent_record: Record,
        external_id_prefix: str,
        rebuild_edges: bool,
        permissions: List[Permission],
        *,
        refresh_permissions: bool = False,
        publish_records: bool = True,
    ) -> List[ChildRecord]:
        """Publish FileRecords for a parent's attachments and return their child links.

        One attachment has one home: an image referenced from the content is embedded
        there as base64, everything else becomes a record. Never both.

        ``permissions`` are the parent's own grants. The record group only carries the
        group grants; the requester and shared-org grants live on the parent record, so
        without them an end user can read a ticket but not its attachments.
        ``refresh_permissions`` replaces the grants of attachments that already exist —
        only the sync path, which knows the parent's current grants, asks for it.
        """
        child_records: List[ChildRecord] = []
        records_with_permissions: List[Tuple[Record, List[Permission]]] = []
        existing_records: List[Record] = []
        for attachment in attachments:
            attachment_id = attachment.get("id")
            content_url = attachment.get("content_url")
            if not attachment_id or not content_url:
                continue
            if self._is_embedded_image(attachment):
                continue
            # Redaction swaps the file for a placeholder with no downloadable content.
            if attachment.get("file_name") == REDACTED_ATTACHMENT_NAME:
                continue
            # _resolve_attachment_url splits on the suffix, so it has to stay last.
            external_id = f"{external_id_prefix}_attachment_{attachment_id}"
            existing_record = await self.data_entities_processor.get_record_by_external_id(
                connector_id=self.connector_id,
                external_record_id=external_id,
            )
            record_id = existing_record.id if existing_record else str(uuid4())
            version = 0 if existing_record is None else existing_record.version
            file_name = attachment.get("file_name") or attachment.get("mapped_content_url") or f"attachment_{attachment_id}"
            file_record = FileRecord(
                id=record_id,
                org_id=self.data_entities_processor.org_id,
                record_name=file_name,
                record_type=RecordType.FILE,
                external_record_id=external_id,
                # Attachments never change, but the processor rewrites a stored record only
                # when this changes: without it a move updates the edges and leaves the
                # record's own group id stale, and the vector payload keeps both groups.
                external_revision_id=f"{attachment_id}:{parent_record.external_record_group_id}",
                parent_external_record_id=parent_record.external_record_id,
                # Omitted, the processor writes PARENT_CHILD instead of ATTACHMENT.
                parent_record_type=parent_record.record_type,
                external_record_group_id=parent_record.external_record_group_id,
                record_group_type=parent_record.record_group_type,
                version=version,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.ZENDESK,
                connector_id=self.connector_id,
                mime_type=self._attachment_mime_type(attachment),
                # Parent page, not content_url: that URL is a bearer capability and
                # weburl is readable from metadata. Re-fetched per download instead.
                weburl=parent_record.weburl,
                is_file=True,
                extension=self._extension(file_name),
                size_in_bytes=attachment.get("size"),
                source_created_at=parent_record.source_created_at,
                source_updated_at=parent_record.source_updated_at,
            )
            self._apply_indexing_filter(file_record, IndexingFilterKey.ATTACHMENTS)
            # Attachments lost their edges to the same wipe, so a rebuild pass has to
            # resend the existing ones too, not just the new ones.
            # An attachment takes its parent's group, so a ticket moved to another
            # group has to drag its existing attachments along.
            moved = (
                existing_record is not None
                and existing_record.external_record_group_id
                != file_record.external_record_group_id
            )
            if existing_record is None or rebuild_edges or moved:
                records_with_permissions.append((file_record, permissions))
            if existing_record is not None and refresh_permissions and not rebuild_edges:
                existing_records.append(file_record)
            child_records.append(ChildRecord(
                child_type=ChildType.RECORD,
                child_id=record_id,
                child_name=file_name,
            ))
        if records_with_permissions and publish_records:
            await self.data_entities_processor.on_new_records(records_with_permissions)
        # on_new_records only adds edges, so a grant the parent lost would linger.
        for file_record in existing_records:
            await self.data_entities_processor.on_updated_record_permissions(
                file_record, permissions
            )
        return child_records

    async def _process_file_for_streaming(self, record: Record) -> AsyncGenerator[bytes, None]:
        """Open the download before returning, so a source error becomes a real status."""
        attachment_id = (record.external_record_id or "").rsplit("_attachment_", 1)[-1]
        if not attachment_id.isdigit():
            raise ValueError(
                f"Unrecognised Zendesk attachment record id: {record.external_record_id}"
            )
        if not await self._is_attachment_currently_public(record):
            raise not_found_at_source(self.display_name)
        content_url = await self._resolve_attachment_url(record)
        if not content_url:
            raise ValueError("Zendesk attachment missing content URL")
        if not self._is_safe_zendesk_asset_url(content_url):
            raise ValueError(
                f"Refusing to fetch Zendesk attachment from untrusted host: {urlparse(content_url).hostname}"
            )

        datasource = await self._get_fresh_datasource()
        # Same host rule as _fetch_asset; httpx drops the header on a cross-host redirect.
        headers = dict(datasource.http.headers) if self._is_tenant_api_url(content_url) else None
        client = httpx.AsyncClient(
            follow_redirects=True, timeout=CDN_FETCH_TIMEOUT_SECONDS, headers=headers
        )
        try:
            response = await client.send(client.build_request("GET", content_url), stream=True)
        except BaseException:
            await client.aclose()
            raise
        if response.status_code >= HTTP_ERROR_STATUS:
            await response.aclose()
            await client.aclose()
            raise map_source_status(response.status_code, connector=self.display_name)

        async def chunks() -> AsyncGenerator[bytes, None]:
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()

        return chunks()

    async def _is_attachment_currently_public(self, record: Record) -> bool:
        """Re-check the parent visibility before serving bytes cached as a record."""
        external_id = record.external_record_id or ""
        attachment_id = external_id.rsplit("_attachment_", 1)[-1]
        if not attachment_id.isdigit():
            return False
        datasource = await self._get_fresh_datasource()
        if external_id.startswith("article_"):
            article_id = external_id.removeprefix("article_").split("_attachment_", 1)[0]
            response = await self._call_api_with_retry(
                datasource.show_article,
                "zendesk/show_article_for_attachment",
                article_id=int(article_id),
            )
            article = (
                self._extract_required_object(response.data, "article")
                if response.success and response.data
                else {}
            )
            if not article or not self._is_article_in_scope(article):
                return False
            attachments, complete = await self._fetch_paginated_list_checked(
                datasource.list_article_attachments,
                "article_attachments",
                article_id=int(article_id),
            )
            return complete and any(str(item.get("id")) == attachment_id for item in attachments)

        ticket_id = external_id.split("_attachment_", 1)[0]
        # Ticket attachment IDs are prefixed with the comment identity.
        match = re.search(r"ticket_(\d+)_comment_(\d+)$", ticket_id)
        if not match:
            return False
        comments = await self._fetch_public_comments(match.group(1))
        return any(
            str(comment.get("id")) == match.group(2)
            and any(str(item.get("id")) == attachment_id for item in comment.get("attachments") or [])
            for comment in comments
        )

    async def _fetch_asset(
        self, datasource: ZendeskDataSource, url: str
    ) -> Tuple[int, bytes, str]:
        """Fetch a Zendesk asset, sending the token only to this tenant's own host.

        The shared CDN is reachable by other tenants, so it gets a client that never
        held the credential — ``HTTPClient`` merges its own headers back in, so passing
        an empty dict cannot withhold it.
        """
        if self._is_tenant_api_url(url):
            response = await datasource.http.execute(HTTPRequest(url=url, method="GET"))
            return response.status, response.bytes(), response.content_type
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=CDN_FETCH_TIMEOUT_SECONDS
        ) as cdn_client:
            cdn_response = await cdn_client.get(url)
        mime = (cdn_response.headers.get("content-type") or "").split(";")[0].strip()
        return cdn_response.status_code, cdn_response.content, mime

    async def _resolve_attachment_url(self, record: Record) -> Optional[str]:
        """Ask Zendesk for the attachment's current content_url.

        Fetched per download so the capability never sits in the graph beside
        metadata that is readable more widely than the file itself.
        """
        external_id = record.external_record_id or ""
        attachment_id = external_id.rsplit("_attachment_", 1)[-1]
        if not attachment_id.isdigit():
            raise ValueError(
                f"Unrecognised Zendesk attachment record id: {record.external_record_id}"
            )
        datasource = await self._get_fresh_datasource()
        # Article attachments have their own path; /attachments/{id} 404s for those ids.
        is_article_attachment = external_id.startswith("article_")
        if is_article_attachment:
            response = await self._call_api_with_retry(
                datasource.show_article_attachment,
                "zendesk/show_article_attachment",
                attachment_id=int(attachment_id),
            )
        else:
            response = await self._call_api_with_retry(
                datasource.show_attachment,
                "zendesk/show_attachment",
                attachment_id=int(attachment_id),
            )
        if not response.success or not response.data:
            raise Exception(
                f"Failed to resolve Zendesk attachment {attachment_id}: {response.error}"
            )
        key = "article_attachment" if is_article_attachment else "attachment"
        attachment = self._extract_required_object(response.data, key)
        if not attachment or not attachment.get("content_url"):
            raise ValueError(
                f"Zendesk returned no content URL for attachment {attachment_id}"
            )
        return attachment.get("content_url")

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> FilterOptionsResponse:
        options: List[FilterOption] = []
        if filter_key == SyncFilterKey.GROUP_IDS.value:
            datasource = await self._get_fresh_datasource()
            groups, complete = await self._fetch_paginated_list_checked(
                datasource.list_groups,
                "groups",
                exclude_deleted=True,
            )
            if not complete:
                return FilterOptionsResponse(
                    success=False,
                    options=[],
                    page=page,
                    limit=limit,
                    has_more=False,
                    message="Could not load Zendesk groups. Try again shortly.",
                )
            for group in groups:
                group_id = group.get("id")
                group_name = group.get("name", "")
                if not group_id or not group_name:
                    continue
                if search and search.lower() not in group_name.lower():
                    continue
                options.append(FilterOption(id=str(group_id), label=group_name))

        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        return FilterOptionsResponse(
            success=True,
            options=options[start_idx:end_idx],
            page=page,
            limit=limit,
            has_more=len(options) > end_idx,
        )

    async def run_incremental_sync(self) -> None:
        await self.run_sync()

    async def test_connection_and_access(self) -> bool:
        """Raises ConnectorInitError so the user sees why, not a generic failure."""
        host = f"{self._subdomain()}.zendesk.com"
        try:
            response = await self._call_api(self._show_current_token)
        except ZendeskAuthError as e:
            raise ConnectorInitError(str(e)) from e
        except httpx.HTTPStatusError as e:
            raise ConnectorInitError(
                f"Zendesk at {host} is unavailable (HTTP {e.response.status_code}). Try again later."
            ) from e
        except httpx.HTTPError as e:
            raise ConnectorInitError(
                f"Could not reach {host}. Check the subdomain is correct."
            ) from e

        if response.status_code == HttpStatusCode.NOT_FOUND.value:
            raise ConnectorInitError(
                f"No Zendesk account found at {host}. Check the subdomain is correct."
            )
        if response.status_code == HttpStatusCode.FORBIDDEN.value:
            # Zendesk refuses token introspection itself without "read" and names the
            # missing scope in the body.
            raise ConnectorInitError(
                f"{self._zendesk_error_description(response.error)} "
                "Re-authorize the connector and approve read access."
            )
        if not response.success:
            self.logger.error(f"Zendesk connection test failed: {response.error}")
            raise ConnectorInitError(
                f"Zendesk connection test failed (HTTP {response.status_code}): {response.error}"
            )

        granted = set((response.data or {}).get("token", {}).get("scopes") or [])
        missing = [scope for scope in REQUIRED_OAUTH_SCOPES if scope not in granted]
        if missing:
            raise ConnectorInitError(
                f"The Zendesk token is missing the required scope(s): {', '.join(missing)}. "
                f"Granted: {', '.join(sorted(granted)) or 'none'}. "
                "Re-authorize the connector and approve read access."
            )
        return True

    @staticmethod
    def _zendesk_error_description(body: Optional[str]) -> str:
        try:
            parsed = json.loads(body or "")
        except ValueError:
            parsed = None
        if isinstance(parsed, dict) and parsed.get("description"):
            return f"Zendesk: {str(parsed['description']).rstrip('.')}."
        return "Zendesk denied access with this token."

    async def get_signed_url(self, record: Record) -> Optional[str]:
        # Zendesk's content_url is a bearer link, so downloads are proxied by stream_record.
        return None

    async def handle_webhook_notification(self, notification: Dict) -> None:
        pass

    async def reindex_records(self, record_results: List[Record]) -> None:
        # Index events only: on_new_records would re-run permission handling.
        if not record_results:
            return
        await self.data_entities_processor.reindex_existing_records(record_results)

    async def cleanup(self) -> None:
        if self.external_client:
            internal_client = self.external_client.get_client()
            if internal_client and hasattr(internal_client, "close"):
                await internal_client.close()

    async def _call_api(self, api_method: Any, **kwargs: Any) -> Any:
        """Re-raise a retryable status as the exception ``call_with_retry`` acts on.

        The data source folds HTTP errors into a ``ZendeskResponse`` instead of
        raising, so a 429 would otherwise never be retried. The response headers ride
        along on the synthesised exception because ``call_with_retry`` reads
        ``Retry-After`` off it — the incremental exports allow 10 requests a minute,
        far longer than the 0.5s/1.0s fallback backoff.
        """
        response = await api_method(**kwargs)
        if response.status_code == HttpStatusCode.UNAUTHORIZED.value:
            # Raised rather than returned: callers treat a failed response as a short
            # page and carry on, which would end the sync as "completed".
            if not await self._refresh_after_unauthorized():
                raise ZendeskAuthError(AUTH_FAILED_MESSAGE)
            response = await api_method(**kwargs)
            if response.status_code == HttpStatusCode.UNAUTHORIZED.value:
                raise ZendeskAuthError(AUTH_FAILED_MESSAGE)
        status = response.status_code
        if not response.success and status in RETRYABLE_STATUS_CODES:
            request = httpx.Request("GET", getattr(api_method, "__name__", "zendesk"))
            raise httpx.HTTPStatusError(
                f"Zendesk HTTP {status}: {response.error}",
                request=request,
                response=httpx.Response(
                    status, request=request, headers=response.headers or {}
                ),
            )
        return response

    async def _call_api_with_retry(self, api_method: Any, label: str, **kwargs: Any) -> Any:
        """Apply the list call retry path to a single-record Zendesk request."""
        return await call_with_retry(
            partial(self._call_api, api_method, **kwargs),
            logger=self.logger,
            label=label,
        )

    async def _refresh_after_unauthorized(self) -> bool:
        """Get a working access token after a 401, refreshing only if nobody else has.

        The token is swapped into the live client rather than rebuilding it, so the
        bound data-source method the caller retries picks it up.
        """
        client = self.external_client.get_client() if self.external_client else None
        if client is None or not hasattr(client, "set_access_token"):
            return False
        token_at_entry = client.access_token
        async with self._token_refresh_lock:
            if client.access_token != token_at_entry:
                return True
            try:
                config = await self.config_service.get_config(
                    f"/services/connectors/{self.connector_id}/config", use_cache=False
                )
                credentials = (config or {}).get("credentials") or {}
                stored_token = credentials.get("access_token")
                if stored_token and stored_token != token_at_entry:
                    client.set_access_token(stored_token)
                    return True
                refresh_token = credentials.get("refresh_token")
                if not refresh_token:
                    self.logger.error("Zendesk: 401 and no refresh token stored")
                    return False
                refresh_service = startup_service.get_token_refresh_service()
                if not refresh_service:
                    self.logger.error("Zendesk: token refresh service unavailable after a 401")
                    return False
                token = await refresh_service.refresh_now(
                    self.connector_id, self.connector_name.value, refresh_token
                )
            except Exception as e:
                self.logger.error(f"Zendesk token refresh after 401 failed: {e}")
                return False
            if not token or not token.access_token:
                return False
            client.set_access_token(token.access_token)
            self.logger.info("Zendesk: refreshed the access token after a 401")
            return True

    async def _show_current_token(self) -> ZendeskResponse:
        datasource = await self._get_fresh_datasource()
        response = await datasource.http.execute(
            HTTPRequest(url=f"{self.base_url}/oauth/tokens/current.json", method="GET")
        )
        ok = response.status < HTTP_ERROR_STATUS
        return ZendeskResponse(
            success=ok,
            data=response.json() if ok else None,
            error=None if ok else response.text()[:300],
            status_code=response.status,
            headers=response.headers,
        )

    async def _call_page(self, api_method: Any, page: int, **kwargs: Any) -> Any:
        return await self._call_api(api_method, page=page, per_page=PAGE_SIZE, **kwargs)

    async def _call_cursor_page(self, api_method: Any, after: Optional[str], **kwargs: Any) -> Any:
        return await self._call_api(
            api_method, page_size=PAGE_SIZE, page_after=after, **kwargs
        )

    async def _call_incremental(self, method_name: str, **kwargs: Any) -> Any:
        """Incremental exports are capped at 10 req/min, so 429s are routine here.

        Resolved per page, not per stage: at that rate a large export outlives the
        ~30 minute OAuth token. Returns None once retries are exhausted, which the
        caller must treat as a truncated export.
        """
        datasource = await self._get_fresh_datasource()
        try:
            return await call_with_retry(
                partial(self._call_api, getattr(datasource, method_name), **kwargs),
                logger=self.logger,
                label=f"zendesk/{method_name}",
            )
        except httpx.HTTPStatusError as e:
            self.logger.error(f"Zendesk {method_name} gave up after retries: {e}")
            return None

    async def _fetch_paginated_list(self, api_method: Any, key: str, **kwargs: Any) -> List[Dict[str, Any]]:
        items, _ = await self._fetch_paginated_list_checked(api_method, key, **kwargs)
        return items

    async def _fetch_paginated_list_checked(
        self, api_method: Any, key: str, *, offset_only: bool = False, **kwargs: Any
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """Walk an endpoint to the end, reporting whether it got there.

        Cursor pagination (``page[size]``/``page[after]``) has no ceiling; offset
        pagination 400s past 10,000 records, which would cap a large tenant. Zendesk
        ignores the cursor params on endpoints that do not support them and answers
        offset-style — the missing ``meta`` block identifies that, so the walk carries
        on by page number instead of stopping short.

        Callers that rebuild state from the full result — group membership above all —
        must not treat a truncated list as authoritative, hence the second value.
        """
        label = getattr(api_method, "__name__", key)
        results: List[Dict[str, Any]] = []
        after: Optional[str] = None
        page = 1
        # offset_only: the datasource method takes no page[size]/page[after] at all.
        by_cursor = not offset_only
        seen_ids: set[str] = set()
        seen_offset_pages: set[Tuple[str, ...]] = set()
        while True:
            if page > MAX_OFFSET_PAGES:
                self.logger.error(
                    "Zendesk %s passed %s pages without reaching the end", label, MAX_OFFSET_PAGES
                )
                return results, False
            where = f"cursor {after}" if by_cursor else f"page {page}"
            call = (
                partial(self._call_cursor_page, api_method, after, **kwargs)
                if by_cursor
                else partial(self._call_page, api_method, page, **kwargs)
            )
            try:
                response = await call_with_retry(
                    call, logger=self.logger, label=f"zendesk/{label} {where}"
                )
            except httpx.HTTPStatusError as e:
                self.logger.error(f"Zendesk {label} {where} gave up: {e}")
                return results, False
            if not response.success:
                self.logger.error(f"Zendesk {label} {where} failed: {response.error}")
                return results, False
            if not response.data:
                self.logger.error(
                    "Zendesk %s %s returned no usable payload", label, where
                )
                return results, False
            if isinstance(response.data, dict):
                value = response.data.get(key)
                if not isinstance(value, (list, dict)):
                    self.logger.error(
                        "Zendesk %s %s omitted the expected %s list",
                        label,
                        where,
                        key,
                    )
                    return results, False
            elif not isinstance(response.data, list):
                self.logger.error(
                    "Zendesk %s %s returned an unexpected payload type", label, where
                )
                return results, False
            # Sideloads ride in the same payload; dropped, authors render as raw ids.
            self._cache_sideloads(response.data)
            items = self._extract_list(response.data, key)
            if not items:
                break
            item_ids = tuple(
                str(item.get("id")) for item in items if item.get("id") is not None
            )
            if not by_cursor and item_ids:
                if item_ids in seen_offset_pages:
                    self.logger.error(
                        "Zendesk %s repeated offset page %s before the end", label, page
                    )
                    return results, False
                seen_offset_pages.add(item_ids)
            for item in items:
                item_id = item.get("id")
                if item_id is not None:
                    normalized_id = str(item_id)
                    if normalized_id in seen_ids:
                        continue
                    seen_ids.add(normalized_id)
                results.append(item)

            meta = response.data.get("meta") if isinstance(response.data, dict) else None
            if by_cursor and not isinstance(meta, dict):
                # The endpoint ignored the cursor params and served page 1 offset-style;
                # that page is already collected, so just continue by number.
                by_cursor = False
            if by_cursor:
                if not meta.get("has_more"):
                    break
                next_after = meta.get("after_cursor")
                # More pages remain but the cursor cannot reach them: the list is short,
                # and a repeated cursor would otherwise loop forever.
                if not next_after or next_after == after:
                    self.logger.error(
                        f"Zendesk {label} stopped advancing before the last page ({where})"
                    )
                    return results, False
                after = next_after
            else:
                if isinstance(response.data, dict) and "next_page" in response.data:
                    # Authoritative: an endpoint may cap per_page below PAGE_SIZE, so a
                    # short page alone does not mean the last one.
                    if response.data.get("next_page") is None:
                        break
                elif len(items) < PAGE_SIZE:
                    break
                page += 1
        return results, True

    def _extract_list(self, payload: Any, key: str) -> List[Dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                return [value]
        return []

    def _extract_object(self, payload: Any, key: str) -> Dict[str, Any]:
        if isinstance(payload, dict):
            value = payload.get(key)
            if isinstance(value, dict):
                return value
            return payload
        return {}

    def _extract_required_object(self, payload: Any, key: str) -> Dict[str, Any]:
        """Return only the named API object; malformed 200s are not empty records."""
        if isinstance(payload, dict) and isinstance(payload.get(key), dict):
            return payload[key]
        return {}

    def _cache_sideloads(self, payload: Dict[str, Any]) -> None:
        for user in self._extract_list(payload, "users"):
            if user.get("id") is not None:
                key = str(user["id"])
                self._sideloaded_users[key] = user
                self._sideloaded_users.move_to_end(key)
        while len(self._sideloaded_users) > SIDELOADED_USER_CACHE_SIZE:
            self._sideloaded_users.popitem(last=False)
        for group in self._extract_list(payload, "groups"):
            if group.get("id") is not None and not group.get("deleted"):
                self._group_id_to_data[str(group["id"])] = group

    async def _get_start_time(
        self, sync_point_key: str = SYNC_POINT_KEY, *, ignore_checkpoint: bool = False
    ) -> int:
        sync_point = (
            {} if ignore_checkpoint
            else await self.records_sync_point.read_sync_point(sync_point_key)
        )
        start_time = sync_point.get("lastEndTime") or DEFAULT_INCREMENTAL_START_TIME
        modified_filter = self.sync_filters.get(SyncFilterKey.MODIFIED) if self.sync_filters else None
        if modified_filter:
            value = modified_filter.get_value(default=None)
            if isinstance(value, tuple) and value[0]:
                start_time = max(int(start_time), int(value[0] / 1000))
        return int(start_time)

    def _org_shares_tickets(self, organization_id: Any) -> bool:
        """Whether an organization's members may read each other's tickets.

        Zendesk's ``shared_tickets`` is false by default; an org missing from the cache
        was never exported, so withhold rather than guess.
        """
        org_data = self._org_id_to_data.get(str(organization_id))
        if org_data is None:
            self.logger.warning(
                "Zendesk: organization %s not in cache — withholding its org-wide grant",
                organization_id,
            )
            return False
        return self._org_data_shares_tickets(org_data)

    @staticmethod
    def _org_data_shares_tickets(org_data: Optional[Dict[str, Any]]) -> bool:
        # The incremental export keeps deleted organizations, with shared_tickets intact.
        return bool(
            org_data
            and org_data.get("shared_tickets")
            and not org_data.get("deleted_at")
        )

    def _record_permissions(
        self,
        group_id: Any,
        requester: Dict[str, Any],
        organization_id: Any = None,
        *,
        collaborators: Optional[List[Any]] = None,
        include_customer_access: bool = True,
        assignee_id: Any = None,
        requester_id: Any = None,
    ) -> List[Permission]:
        permissions: List[Permission] = [self._all_tickets_permission()]
        if group_id:
            permissions.append(Permission(
                external_id=f"group_{group_id}",
                type=PermissionType.READ,
                entity_type=EntityType.GROUP,
            ))
        else:
            permissions.append(self._public_groups_permission())
        for staff_id in {str(value) for value in (assignee_id, requester_id) if value}:
            staff_data = self._user_id_to_data.get(staff_id, {})
            access = self._effective_ticket_access(staff_data, self._role_ticket_access)
            allowed_for_staff = (
                (staff_id == str(assignee_id) and access in {"assigned", "assigned_only"})
                or (staff_id == str(requester_id) and access == "requested")
            )
            if allowed_for_staff:
                permissions.append(Permission(
                    external_id=f"staff_{staff_id}",
                    type=PermissionType.READ,
                    entity_type=EntityType.GROUP,
                ))
        # Granted whether or not the org shares tickets: run_sync empties the group of
        # an org that does not, so toggling sharing needs no change to the tickets.
        if (
            include_customer_access
            and organization_id
            and str(organization_id) in self._org_id_to_data
        ):
            permissions.append(Permission(
                external_id=f"org_{organization_id}",
                type=PermissionType.READ,
                entity_type=EntityType.GROUP,
            ))
        if organization_id and self._org_shares_tickets(organization_id):
            for staff_id, staff_data in self._user_id_to_data.items():
                if (
                    staff_data.get("active") is not False
                    and not staff_data.get("suspended")
                    and str(staff_data.get("organization_id")) == str(organization_id)
                    and self._effective_ticket_access(
                        staff_data, self._role_ticket_access
                    ) in {"organization", "organization_only"}
                ):
                    permissions.append(Permission(
                        external_id=f"staff_{staff_id}",
                        type=PermissionType.READ,
                        entity_type=EntityType.GROUP,
                    ))
        requester_email = requester.get("email") if include_customer_access else None
        if requester_email:
            permissions.append(Permission(
                email=requester_email,
                type=PermissionType.READ,
                entity_type=EntityType.USER,
            ))
        if include_customer_access:
            requester_email_lower = (requester_email or "").lower()
            seen_collaborators: set[str] = set()
            for collaborator_id in collaborators or []:
                collaborator_email = self._user_data(collaborator_id).get("email")
                if not collaborator_email:
                    continue
                normalized_email = collaborator_email.lower()
                if normalized_email == requester_email_lower or normalized_email in seen_collaborators:
                    continue
                seen_collaborators.add(normalized_email)
                permissions.append(Permission(
                    email=collaborator_email,
                    type=PermissionType.READ,
                    entity_type=EntityType.USER,
                ))
        if len(permissions) == 1:
            self.logger.warning(
                "Zendesk: no group or requester grant for a record (group_id=%s) — "
                "only all-tickets users will see it",
                group_id,
            )
        return permissions

    def _is_group_allowed_by_filter(self, group_id: str) -> bool:
        if not self.sync_filters:
            return True
        group_filter = self.sync_filters.get(SyncFilterKey.GROUP_IDS)
        if not group_filter:
            return True
        selected_group_ids = group_filter.get_value(default=[])
        if not selected_group_ids:
            return True
        filter_set = {str(gid) for gid in selected_group_ids}
        operator = group_filter.get_operator()
        operator_value = operator.value if hasattr(operator, "value") else str(operator)
        return group_id not in filter_set if operator_value == "not_in" else group_id in filter_set

    def _apply_indexing_filter(self, record: Record, key: IndexingFilterKey) -> None:
        """Mark a record as not-to-be-indexed when its content type is switched off.

        Turning a type off must not stop it syncing: the record, its edges and its
        permissions still belong in the graph, and dropping it would strand whatever
        already pointed at it. AUTO_INDEX_OFF is what suppresses the indexing event
        (``data_source_entities_processor.py:1136``), and a later reindex can override.
        """
        if self.indexing_filters and not self.indexing_filters.is_enabled(key):
            record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

    def _is_allowed_by_date_filters(
        self,
        created_at: Optional[int],
        updated_at: Optional[int],
    ) -> bool:
        if not self.sync_filters:
            return True

        created_filter = self.sync_filters.get(SyncFilterKey.CREATED)
        if created_filter:
            created_range = created_filter.get_value(default=None)
            if (
                isinstance(created_range, tuple)
                and not self._timestamp_in_range(created_at, created_range)
            ):
                return False

        modified_filter = self.sync_filters.get(SyncFilterKey.MODIFIED)
        if modified_filter:
            modified_range = modified_filter.get_value(default=None)
            if (
                isinstance(modified_range, tuple)
                and not self._timestamp_in_range(updated_at, modified_range)
            ):
                return False

        return True

    def _timestamp_in_range(
        self,
        timestamp: Optional[int],
        date_range: tuple[Optional[int], Optional[int]],
    ) -> bool:
        if timestamp is None:
            return True
        start, end = date_range
        if start is not None and timestamp < start:
            return False
        return not (end is not None and timestamp > end)

    def _parse_datetime(self, value: Any) -> Optional[int]:
        if not value:
            return None
        if isinstance(value, (int, float)):
            return int(value * 1000) if value < 10_000_000_000 else int(value)
        if isinstance(value, str):
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                # Zendesk's API speaks UTC; a naive value must not take the host's zone.
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp() * 1000)
            except ValueError:
                return None
        return None

    def _ticket_web_url(self, ticket_id: Any) -> Optional[str]:
        subdomain = self._subdomain()
        return f"https://{subdomain}.zendesk.com/agent/tickets/{ticket_id}" if subdomain else None

    def _agent_group_url(self, group_id: str) -> Optional[str]:
        subdomain = self._subdomain()
        return f"https://{subdomain}.zendesk.com/admin/people/team/groups/{group_id}" if subdomain else None

    def _subdomain(self) -> Optional[str]:
        if not self.external_client:
            return None
        try:
            return self.external_client.get_subdomain()
        except Exception:
            return None

    def _is_tenant_api_url(self, url: str) -> bool:
        """True only for this connector's own Zendesk host — any ``*.zendesk.com``
        would otherwise receive this tenant's API token."""
        subdomain = self._subdomain()
        if not subdomain:
            return False
        parsed = urlparse(url)
        return (
            parsed.scheme == "https"
            and (parsed.hostname or "").lower() == f"{subdomain.lower()}.zendesk.com"
        )

    def _is_safe_zendesk_asset_url(self, url: str) -> bool:
        if self._is_tenant_api_url(url):
            return True
        parsed = urlparse(url)
        # Shared pre-signed CDN: safe to fetch from, never to authenticate to.
        return parsed.scheme == "https" and (parsed.hostname or "").lower().endswith(
            (".zdusercontent.com", ".zendeskusercontent.com")
        )

    @staticmethod
    def _is_embedded_image(attachment: Dict[str, Any]) -> bool:
        """Whether this attachment already lives in the body as a base64 data URI.

        Zendesk marks an attachment referenced from the content ``inline``, and
        ``_inline_images_as_base64`` embeds the image ones. A record for those would
        index the same bytes twice. An inline non-image — a linked PDF — is not
        embedded by anything, so it still needs its own record.
        """
        return bool(attachment.get("inline")) and ZendeskConnector._attachment_mime_type(
            attachment
        ).startswith("image/")

    @staticmethod
    def _content_revision(*parts: Any) -> str:
        # The title is not hashed: a rename updates the record without re-indexing it.
        return hashlib.sha256(
            json.dumps(parts, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    @classmethod
    def _ticket_content_revision(
        cls, ticket_data: Dict[str, Any], comments: List[Dict[str, Any]]
    ) -> str:
        if not comments:
            # The streamed body falls back to the subject, so it is the content here.
            return cls._content_revision(ticket_data.get("subject") or "")
        return cls._content_revision([
            (
                comment.get("id"),
                comment.get("html_body") or comment.get("body") or "",
                [attachment.get("id") for attachment in comment.get("attachments") or []],
            )
            for comment in comments
        ])

    @staticmethod
    def _attachment_mime_type(attachment: Dict[str, Any]) -> str:
        """Zendesk reports application/unknown for files uploaded without a type."""
        content_type = attachment.get("content_type")
        if content_type and content_type not in UNRELIABLE_MIME_TYPES:
            return content_type
        guessed, _ = mimetypes.guess_type(attachment.get("file_name") or "", strict=False)
        return guessed or content_type or MimeTypes.UNKNOWN.value

    def _extension(self, file_name: str) -> Optional[str]:
        if "." not in file_name:
            return None
        return file_name.rsplit(".", 1)[-1].lower()

    def _populate_block_group_children(self, block_groups: List[BlockGroup]) -> None:
        children_by_parent: Dict[int, List[int]] = defaultdict(list)
        for block_group in block_groups:
            if block_group.parent_index is not None:
                children_by_parent[block_group.parent_index].append(block_group.index)
        for block_group in block_groups:
            child_indices = children_by_parent.get(block_group.index)
            if child_indices:
                block_group.children = BlockGroupChildren.from_indices(block_group_indices=sorted(child_indices))

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
        """Factory method to create ZendeskConnector instance"""
        return ZendeskConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
