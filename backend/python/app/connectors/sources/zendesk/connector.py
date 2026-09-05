"""Zendesk connector implementation."""

import base64
import re
from collections import defaultdict
from datetime import datetime
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
    Connectors,
    ProgressStatus,
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
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_retry import call_with_retry
from app.sources.client.zendesk.zendesk import ZendeskClient
from app.sources.external.zendesk.zendesk import ZendeskDataSource
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import get_epoch_timestamp_in_ms


SYNC_POINT_KEY = "zendesk_incremental"
ARTICLES_SYNC_POINT_KEY = "zendesk_articles_incremental"
# Users, groups, memberships and organizations are always exported from here, never
# from a checkpoint. on_new_user_groups deletes every membership edge before re-adding
# from the list it is given, so the list has to be the whole truth — a window cannot
# tell "unchanged" from "removed", and guessing wrong revokes real access. Only the
# record stages (tickets, articles) resume from a sync point.
DEFAULT_INCREMENTAL_START_TIME = 1
PAGE_SIZE = 100
# Matches the Jira connectors: cap how many records go into one on_new_records call.
BATCH_PROCESSING_SIZE = 100
# Zendesk rejects an incremental start_time inside the last minute.
INCREMENTAL_SAFETY_LAG_SECONDS = 60
RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
HTTP_ERROR_STATUS = 400
CDN_FETCH_TIMEOUT_SECONDS = 60.0
# Zendesk reports trashed tickets in the incremental export under this status.
DELETED_TICKET_STATUS = "deleted"
# Base64 inflates by a third and the result is held in the record body.
MAX_INLINE_IMAGE_BYTES = 10 * 1024 * 1024
IMG_SRC_PATTERN = re.compile(r'(<img\b[^>]*?\bsrc=["\'])([^"\']+)(["\'])', re.IGNORECASE)


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
        self._group_id_to_data: Dict[str, Dict[str, Any]] = {}
        self._section_id_to_data: Dict[str, Dict[str, Any]] = {}
        self._category_id_to_data: Dict[str, Dict[str, Any]] = {}
        self._org_id_to_data: Dict[str, Dict[str, Any]] = {}
        self._user_id_to_app_user: Dict[str, AppUser] = {}
        self._rebuild_ticket_edges = False
        self._rebuild_article_edges = False
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
        if await self._oauth_token_rotated():
            # init() reports failure by returning False; ignoring it would serve the
            # client holding the superseded token and 401 on every following call.
            if not await self.init():
                raise RuntimeError(
                    "Zendesk credentials rotated but the client could not be rebuilt"
                )
        return self.data_source

    async def _oauth_token_rotated(self) -> bool:
        client = self.external_client.get_client()
        in_use = getattr(client, "access_token", None)
        if not in_use:
            return False
        try:
            config = await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config", use_cache=False
            )
        except Exception as e:
            self.logger.warning(f"Zendesk: could not re-read stored credentials: {e}")
            return False
        stored = ((config or {}).get("credentials") or {}).get("access_token")
        return bool(stored) and stored != in_use

    async def run_sync(self) -> None:
        self.logger.info(f"Starting Zendesk sync for connector {self.connector_id}")

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

        group_record_groups, group_user_groups, memberships_complete = (
            await self._fetch_groups(user_email_map)
        )
        if group_user_groups and users_complete and memberships_complete:
            await self.data_entities_processor.on_new_user_groups(group_user_groups)
        elif group_user_groups:
            self.logger.error(
                "Zendesk: skipping group membership sync — the %s export was truncated "
                "and on_new_user_groups would rebuild each group from partial data",
                "user" if not users_complete else "group membership",
            )
        if group_record_groups:
            await self.data_entities_processor.on_new_record_groups(group_record_groups)
        self.logger.info(f"Zendesk: synced {len(group_record_groups)} groups")

        org_user_groups, orgs_complete = await self._fetch_organizations()
        if org_user_groups and users_complete and orgs_complete:
            await self.data_entities_processor.on_new_user_groups(org_user_groups)
        elif org_user_groups:
            self.logger.error(
                "Zendesk: skipping organization membership sync — the %s export was "
                "truncated and partial membership would revoke existing access",
                "user" if not users_complete else "organization",
            )
        self.logger.info(f"Zendesk: synced {len(org_user_groups)} organizations")

        # Without those AppUserGroups the group grant is dropped, and the advanced sync
        # point would stop any later run repairing it.
        if users_complete and memberships_complete:
            ticket_count = await self._sync_tickets()
        else:
            ticket_count = 0
            self.logger.error(
                "Zendesk: skipping ticket sync — group membership was not written, so "
                "every ticket would land without its group grant and the advanced sync "
                "point would stop any later run from repairing it"
            )
        article_count = await self._sync_help_center_articles()
        self.logger.info(
            f"Zendesk sync completed for connector {self.connector_id}: "
            f"{len(users)} users, {len(group_record_groups)} groups, "
            f"{len(org_user_groups)} organizations, "
            f"{ticket_count} tickets, {article_count} articles"
        )

    async def _fetch_users(self) -> Tuple[List[AppUser], Dict[str, AppUser], bool]:
        # Full map, not just changed users: on_new_user_groups rebuilds membership
        # from scratch, so a truncated one revokes access. Third value flags that.
        start_time = DEFAULT_INCREMENTAL_START_TIME
        users_data: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        complete = True

        while True:
            response = await self._call_incremental(
                "incremental_users",
                start_time=start_time,
                cursor=cursor,
            )
            if response is None or not response.success:
                error = response.error if response else "retries exhausted"
                self.logger.error(f"Zendesk incremental_users failed: {error}")
                complete = False
                break
            if not response.data:
                break
            payload = response.data
            users_data.extend(self._extract_list(payload, "users"))
            next_cursor = payload.get("after_cursor") or payload.get("cursor")
            # A repeated cursor means the export is not advancing.
            if payload.get("end_of_stream", True) or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

        users: List[AppUser] = []
        user_email_map: Dict[str, AppUser] = {}
        for user_data in users_data:
            user_id = user_data.get("id")
            email = user_data.get("email")
            # Permissions resolve by email; a synthesised address matches nobody.
            if not user_id or not email:
                continue
            full_name = user_data.get("name") or email
            app_user = AppUser(
                app_name=Connectors.ZENDESK,
                connector_id=self.connector_id,
                source_user_id=str(user_id),
                org_id=self.data_entities_processor.org_id,
                email=email,
                full_name=full_name,
                is_active=bool(user_data.get("active", True)),
                source_created_at=self._parse_datetime(user_data.get("created_at")),
                source_updated_at=self._parse_datetime(user_data.get("updated_at")),
            )
            users.append(app_user)
            user_email_map[str(user_id)] = app_user
            user_email_map[email] = app_user
            self._user_id_to_data[str(user_id)] = user_data
            self._user_id_to_app_user[str(user_id)] = app_user

        return users, user_email_map, complete

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
        groups_data, groups_complete = await self._fetch_paginated_list_checked(
            datasource.list_groups,
            "groups",
            exclude_deleted=True,
        )
        memberships, memberships_complete = await self._fetch_paginated_list_checked(
            datasource.list_group_memberships,
            "group_memberships",
        )
        members_by_group: Dict[str, List[AppUser]] = defaultdict(list)
        for membership in memberships:
            group_id = str(membership.get("group_id", ""))
            user_id = str(membership.get("user_id", ""))
            user = user_email_map.get(user_id)
            if group_id and user:
                members_by_group[group_id].append(user)

        record_groups: List[Tuple[RecordGroup, List[Permission]]] = []
        user_groups: List[Tuple[AppUserGroup, List[AppUser]]] = []
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
            user_groups.append((user_group, members_by_group.get(group_id, [])))

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
                )
            ]
            record_groups.append((record_group, permissions))

        # Both feed a rebuild-from-scratch that would revoke whatever fell off the end.
        return record_groups, user_groups, groups_complete and memberships_complete

    async def _fetch_organizations(self) -> Tuple[List[Tuple[AppUserGroup, List[AppUser]]], bool]:
        """Sync Zendesk organizations as user groups.

        Membership comes from the users already fetched — Zendesk has no bulk
        organization-membership endpoint. Not RecordGroups: nothing files a record
        under one, so they would render empty; tickets carry the org permission.

        The second return value flags a truncated export: on_new_user_groups rebuilds
        each group from scratch, so writing partial membership revokes real access.
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
                break
            payload = response.data
            orgs_data.extend(self._extract_list(payload, "organizations"))
            end_time = payload.get("end_time")
            # An end_time that does not advance would page over the same window forever.
            if payload.get("end_of_stream", True) or not end_time or end_time <= start_time:
                break
            start_time = end_time

        members_by_org: Dict[str, List[AppUser]] = defaultdict(list)
        for user_id, user_data in self._user_id_to_data.items():
            org_id = user_data.get("organization_id")
            app_user = self._user_id_to_app_user.get(user_id)
            if org_id and app_user:
                members_by_org[str(org_id)].append(app_user)

        user_groups: List[Tuple[AppUserGroup, List[AppUser]]] = []
        for org_data in orgs_data:
            org_id = org_data.get("id")
            if not org_id:
                continue
            org_id = str(org_id)
            name = org_data.get("name") or f"Organization {org_id}"
            self._org_id_to_data[org_id] = org_data

            user_groups.append((
                AppUserGroup(
                    app_name=Connectors.ZENDESK,
                    connector_id=self.connector_id,
                    source_user_group_id=f"org_{org_id}",
                    name=name,
                    org_id=self.data_entities_processor.org_id,
                    source_created_at=self._parse_datetime(org_data.get("created_at")),
                    source_updated_at=self._parse_datetime(org_data.get("updated_at")),
                ),
                members_by_org.get(org_id, []),
            ))

        return user_groups, complete

    async def _sync_tickets(self) -> int:
        synced = 0
        removed = 0
        start_time = await self._get_start_time()
        cursor: Optional[str] = None
        max_end_time = start_time
        complete = True
        while True:
            response = await self._call_incremental(
                "incremental_tickets",
                start_time=start_time,
                cursor=cursor,
                include="users,groups,organizations",
            )
            if response is None or not response.success:
                error = response.error if response else "retries exhausted"
                self.logger.error(f"Zendesk incremental_tickets failed: {error}")
                complete = False
                break
            if not response.data:
                break
            payload = response.data
            self._cache_sideloads(payload)
            tickets = self._extract_list(payload, "tickets")

            removed_ids = await self._resolve_removable_record_ids(tickets)
            if removed_ids:
                # Cascade, not on_record_deleted: attachments are child records, and only
                # the cascade path emits the events that purge the vectors from Qdrant.
                await self.data_entities_processor.on_records_deleted_cascade(
                    removed_ids, self.connector_id
                )
                removed += len(removed_ids)

            records_with_permissions: List[Tuple[Record, List[Permission]]] = []
            for ticket_data in tickets:
                record_tuple = await self._ticket_to_record(ticket_data)
                if record_tuple:
                    records_with_permissions.append(record_tuple)
            if records_with_permissions:
                for start in range(0, len(records_with_permissions), BATCH_PROCESSING_SIZE):
                    await self.data_entities_processor.on_new_records(
                        records_with_permissions[start:start + BATCH_PROCESSING_SIZE]
                    )
                synced += len(records_with_permissions)
                # At sync time, not on the streaming path: an attachment is a record in
                # its own right and must exist even if its ticket is never indexed. This
                # is also what rebuilds their edges after a full sync wipes them, so an
                # unchanged ticket needs no forced reindex to get them back.
                for record, _ in records_with_permissions:
                    await self._sync_ticket_attachments(record)

            # Cursor export returns no end_time; resume from the newest ticket seen.
            for ticket_data in tickets:
                updated_ms = self._parse_datetime(ticket_data.get("updated_at"))
                if updated_ms:
                    max_end_time = max(max_end_time, updated_ms // 1000)

            next_cursor = payload.get("after_cursor") or payload.get("cursor")
            if payload.get("end_of_stream", True) or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

        # Advancing past a truncated export skips every ticket the failed pages held,
        # permanently — the next run would start after tickets it never saw.
        if not complete:
            self.logger.error(
                "Zendesk: ticket export truncated — leaving the sync point at %s so the "
                "next run re-reads the missing window", start_time,
            )
            return synced

        now_seconds = get_epoch_timestamp_in_ms() // 1000
        max_end_time = min(max_end_time, now_seconds - INCREMENTAL_SAFETY_LAG_SECONDS)
        await self.records_sync_point.update_sync_point(
            SYNC_POINT_KEY,
            {"lastEndTime": max_end_time, "updatedAt": get_epoch_timestamp_in_ms()},
        )
        if removed:
            self.logger.info(
                f"Zendesk: removed {removed} tickets deleted at source or outside the filters"
            )
        return synced

    def _is_deleted_ticket(self, ticket_data: Dict[str, Any]) -> bool:
        return str(ticket_data.get("status") or "").lower() == DELETED_TICKET_STATUS

    def _is_ticket_in_scope(self, ticket_data: Dict[str, Any]) -> bool:
        """Whether this ticket belongs in the graph at all under the current filters."""
        if self._is_deleted_ticket(ticket_data):
            return False
        group_id = ticket_data.get("group_id")
        if group_id and not self._is_group_allowed_by_filter(str(group_id)):
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
                record_ids.append(existing.id)
        return record_ids

    async def _ticket_to_record(self, ticket_data: Dict[str, Any]) -> Optional[Tuple[Record, List[Permission]]]:
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

        if (
            existing_record
            and existing_record.source_updated_at == updated_at
            and not self._rebuild_ticket_edges
        ):
            return None

        record_id = existing_record.id if existing_record else str(uuid4())
        version = 0 if existing_record is None else existing_record.version + 1
        requester = self._user_id_to_data.get(str(ticket_data.get("requester_id")), {})
        assignee = self._user_id_to_data.get(str(ticket_data.get("assignee_id")), {})
        submitter = self._user_id_to_data.get(str(ticket_data.get("submitter_id")), {})
        status = self.value_mapper.map_status(ticket_data.get("status")) or Status.UNKNOWN
        priority = self.value_mapper.map_priority(ticket_data.get("priority")) or Priority.UNKNOWN
        item_type = self.value_mapper.map_type(ticket_data.get("type")) or ItemType.UNKNOWN
        # A group we never synced would be auto-created unnamed, org-less and App-less.
        known_group = group_id is not None and str(group_id) in self._group_id_to_data
        external_group_id = f"group_{group_id}" if known_group else None
        if group_id and not known_group:
            self.logger.warning(
                "Zendesk: ticket %s references unknown group %s — filing it without a "
                "record group rather than inventing one", ticket_id, group_id,
            )

        record = TicketRecord(
            id=record_id,
            org_id=self.data_entities_processor.org_id,
            record_name=ticket_data.get("subject") or f"Zendesk ticket {ticket_id}",
            record_type=RecordType.TICKET,
            external_record_id=str(ticket_id),
            external_revision_id=str(updated_at) if updated_at else None,
            external_record_group_id=external_group_id,
            record_group_type=RecordGroupType.PROJECT if external_group_id else None,
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
        permissions = self._record_permissions(
            group_id, requester, ticket_data.get("organization_id")
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
        articles, articles_complete, max_end_time = await self._fetch_incremental_articles(
            start_time
        )
        if not articles_complete:
            # A short list would read as "deleted" to the removal pass below, and
            # advancing past a truncated window would skip those articles for good.
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
                f"Zendesk: removed {len(removed_ids)} articles no longer published org-wide"
            )

        await self._resolve_missing_sections(articles)

        records_with_permissions: List[Tuple[Record, List[Permission]]] = []
        for article_data in articles:
            record_tuple = await self._article_to_record(article_data)
            if record_tuple:
                records_with_permissions.append(record_tuple)
        for start in range(0, len(records_with_permissions), BATCH_PROCESSING_SIZE):
            await self.data_entities_processor.on_new_records(
                records_with_permissions[start:start + BATCH_PROCESSING_SIZE]
            )
        # After the articles are published, so the parent exists before its children.
        for record, _ in records_with_permissions:
            await self._build_article_attachment_child_records(
                record.external_record_id.removeprefix("article_"), record
            )

        now_seconds = get_epoch_timestamp_in_ms() // 1000
        max_end_time = min(max_end_time, now_seconds - INCREMENTAL_SAFETY_LAG_SECONDS)
        await self.records_sync_point.update_sync_point(
            ARTICLES_SYNC_POINT_KEY,
            {"lastEndTime": max_end_time, "updatedAt": get_epoch_timestamp_in_ms()},
        )
        return len(records_with_permissions)

    async def _fetch_incremental_articles(
        self, start_time: int
    ) -> Tuple[List[Dict[str, Any]], bool, int]:
        """Walk the Help Center incremental export from ``start_time``.

        Not ``list_articles``: that pages by offset and Zendesk 400s past 10,000
        records, so a large Help Center could never finish a first sync.
        """
        articles: List[Dict[str, Any]] = []
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
                break
            payload = response.data
            self._cache_sideloads(payload)
            articles.extend(self._extract_list(payload, "articles"))
            end_time = payload.get("end_time")
            if end_time:
                max_end_time = max(max_end_time, int(end_time))
            # An end_time that does not advance would page over the same window forever.
            if payload.get("end_of_stream", True) or not end_time or end_time <= start_time:
                break
            start_time = end_time
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
            self.logger.error(
                "Zendesk: category list truncated - sections would be filed under a "
                "parent that does not exist yet, so skipping this pass"
            )
            return False
        sections, sections_complete = await self._fetch_paginated_list_checked(
            datasource.list_sections, "sections"
        )
        if not sections_complete:
            self.logger.error(
                "Zendesk: section list truncated - articles under the missing sections "
                "would be filed under an invented record group, so skipping this pass"
            )
            return False

        record_groups: List[Tuple[RecordGroup, List[Permission]]] = []
        for category_data in categories:
            category_id = category_data.get("id")
            if not category_id:
                continue
            self._category_id_to_data[str(category_id)] = category_data
            record_groups.append(self._category_record_group(category_data))
        for section_data in sections:
            section_id = section_data.get("id")
            if not section_id:
                continue
            self._section_id_to_data[str(section_id)] = section_data
            record_groups.append(self._section_record_group(section_data))

        if record_groups:
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
            response = await datasource.show_section(section_id=int(section_id))
            if not response.success or not response.data:
                self.logger.warning(
                    f"Zendesk: could not resolve section {section_id}: {response.error}"
                )
                continue
            section_data = self._extract_object(response.data, "section")
            if not section_data.get("id"):
                continue
            self._section_id_to_data[section_id] = section_data
            resolved.append(self._section_record_group(section_data))
            # Walk up: an unlisted subsection's parent may be unlisted too.
            parent_section_id = section_data.get("parent_section_id")
            if parent_section_id and str(parent_section_id) not in self._section_id_to_data:
                pending.append(str(parent_section_id))
        if resolved:
            await self.data_entities_processor.on_new_record_groups(resolved)

    def _is_article_in_scope(self, article_data: Dict[str, Any]) -> bool:
        """Whether this article may be published to the whole tenant.

        user_segment_id is the article's entire ACL and segments are not synced, so one
        that becomes restricted must lose the org-wide grant it already has.
        """
        if article_data.get("draft"):
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
                record_ids.append(existing.id)
        return record_ids

    async def _article_to_record(self, article_data: Dict[str, Any]) -> Optional[Tuple[Record, List[Permission]]]:
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
        if (
            existing_record
            and existing_record.source_updated_at == updated_at
            and not self._rebuild_article_edges
        ):
            return None

        record_id = existing_record.id if existing_record else str(uuid4())
        version = 0 if existing_record is None else existing_record.version + 1
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
            external_revision_id=str(updated_at) if updated_at else None,
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
        # Restricted articles were filtered out above, so ORG is the right grant.
        permissions = [
            Permission(
                type=PermissionType.READ,
                entity_type=EntityType.ORG,
                external_id=self.data_entities_processor.org_id,
            )
        ]
        return record, permissions

    async def stream_record(
        self,
        record: Record,
        user_id: Optional[str] = None,
        convertTo: Optional[str] = None,
    ) -> StreamingResponse:
        if record.record_type == RecordType.FILE:
            # Attachment bytes are not a BlocksContainer.
            content = await self._process_file_for_streaming(record)

            async def file_bytes() -> AsyncGenerator[bytes, None]:
                yield content

            return create_stream_record_response(
                file_bytes(),
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
        return StreamingResponse(iter([content]), media_type=MimeTypes.BLOCKS.value)

    async def _fetch_public_comments(self, ticket_id: str) -> List[Dict[str, Any]]:
        """A ticket's public comments, oldest first.

        public=False is an internal agent note. This ticket grants the requester READ,
        so indexing one would hand it to the customer.
        """
        datasource = await self._get_fresh_datasource()
        comments = await self._fetch_paginated_list(
            datasource.list_comments,
            "comments",
            ticket_id=int(ticket_id),
            sort_order="asc",
            include="users",
        )
        return [comment for comment in comments if comment.get("public", True)]

    async def _sync_ticket_attachments(self, ticket_record: Record) -> None:
        """Publish the ticket's comment attachments as records during the sync.

        Zendesk hangs attachments off comments rather than the ticket, and the
        incremental export cannot sideload them, so this costs one call per changed
        ticket. Jira gets the same records for free from ``fields.attachment``.
        """
        for comment in await self._fetch_public_comments(ticket_record.external_record_id):
            await self._build_attachment_child_records(comment, ticket_record)

    async def _process_ticket_blockgroups_for_streaming(self, record: Record) -> bytes:
        comments = await self._fetch_public_comments(record.external_record_id)
        block_groups: List[BlockGroup] = []
        for index, comment in enumerate(comments):
            body = comment.get("html_body") or comment.get("body") or ""
            if "<" in body and ">" in body:
                body = html_to_markdown(await self._inline_images_as_base64(body))
            children_records = await self._build_attachment_child_records(comment, record)
            author = self._user_id_to_data.get(str(comment.get("author_id")), {})
            is_description = index == 0
            block_groups.append(BlockGroup(
                id=str(uuid4()),
                index=index,
                parent_index=None if is_description else 0,
                name="Description" if is_description else f"Comment by {author.get('name') or comment.get('author_id') or 'Unknown'}",
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
        response = await datasource.show_article(article_id=int(article_id))
        if not response.success or not response.data:
            raise Exception(f"Failed to fetch Zendesk article {article_id}")
        article = self._extract_object(response.data, "article")
        body = article.get("body") or ""
        body_md = html_to_markdown(await self._inline_images_as_base64(body)) if body else ""
        children_records = await self._build_article_attachment_child_records(article_id, record)
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
            author = self._user_id_to_data.get(str(comment.get("author_id")), {})
            block_groups.append(BlockGroup(
                id=str(uuid4()),
                index=index,
                parent_index=0,
                name=f"Comment by {author.get('name') or comment.get('author_id') or 'Unknown'}",
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
        try:
            status, raw, mime = await self._fetch_asset(datasource, url)
            if status >= HTTP_ERROR_STATUS:
                self.logger.warning(f"Zendesk inline image {url} returned {status}")
                return None
            if len(raw) > MAX_INLINE_IMAGE_BYTES:
                self.logger.warning(f"Skipping oversized Zendesk inline image ({len(raw)} bytes): {url}")
                return None
            if not mime.startswith("image/"):
                return None
            return f"data:{mime};base64,{base64.b64encode(raw).decode('utf-8')}"
        except Exception as e:
            self.logger.warning(f"Could not inline Zendesk image {url}: {e}")
            return None

    async def _build_attachment_child_records(
        self,
        comment: Dict[str, Any],
        parent_record: Record,
    ) -> List[ChildRecord]:
        # An attachment has no ACL of its own — it inherits its comment's.
        if not comment.get("public", True):
            return []
        return await self._emit_attachment_records(
            comment.get("attachments") or [],
            parent_record,
            f"ticket_{parent_record.external_record_id}_comment_{comment.get('id')}",
            self._rebuild_ticket_edges,
        )

    async def _build_article_attachment_child_records(
        self,
        article_id: str,
        parent_record: Record,
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
            self.logger.error(
                "Zendesk: attachment list for article %s truncated — indexing the ones "
                "that arrived rather than dropping the article", article_id,
            )
        return await self._emit_attachment_records(
            attachments,
            parent_record,
            parent_record.external_record_id,
            self._rebuild_article_edges,
        )

    async def _emit_attachment_records(
        self,
        attachments: List[Dict[str, Any]],
        parent_record: Record,
        external_id_prefix: str,
        rebuild_edges: bool,
    ) -> List[ChildRecord]:
        """Publish FileRecords for a parent's attachments and return their child links.

        One attachment has one home: an image referenced from the content is embedded
        there as base64, everything else becomes a record. Never both.

        No explicit permissions: ``inherit_permissions`` is on and the record lands in
        the parent's record group, so it picks up that group's grants. That is the
        Jira shape (``jira_cloud/connector.py:3596`` copies an always-empty list).
        """
        child_records: List[ChildRecord] = []
        records_with_permissions: List[Tuple[Record, List[Permission]]] = []
        for attachment in attachments:
            attachment_id = attachment.get("id")
            content_url = attachment.get("content_url")
            if not attachment_id or not content_url:
                continue
            if self._is_embedded_image(attachment):
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
                parent_external_record_id=parent_record.external_record_id,
                # Omitted, the processor writes PARENT_CHILD instead of ATTACHMENT.
                parent_record_type=parent_record.record_type,
                external_record_group_id=parent_record.external_record_group_id,
                record_group_type=parent_record.record_group_type,
                version=version,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.ZENDESK,
                connector_id=self.connector_id,
                mime_type=attachment.get("content_type") or MimeTypes.UNKNOWN.value,
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
            if existing_record is None or rebuild_edges:
                records_with_permissions.append((file_record, []))
            child_records.append(ChildRecord(
                child_type=ChildType.RECORD,
                child_id=record_id,
                child_name=file_name,
            ))
        if records_with_permissions:
            await self.data_entities_processor.on_new_records(records_with_permissions)
        return child_records

    async def _process_file_for_streaming(self, record: Record) -> bytes:
        content_url = await self._resolve_attachment_url(record)
        if not content_url:
            raise ValueError("Zendesk attachment missing content URL")
        if not self._is_safe_zendesk_asset_url(content_url):
            raise ValueError(
                f"Refusing to fetch Zendesk attachment from untrusted host: {urlparse(content_url).hostname}"
            )

        datasource = await self._get_fresh_datasource()
        status, raw, _ = await self._fetch_asset(datasource, content_url)
        if status >= HTTP_ERROR_STATUS:
            raise Exception(
                f"Failed to download Zendesk attachment {record.external_record_id}: {status}"
            )
        return raw

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
            response = await datasource.show_article_attachment(attachment_id=int(attachment_id))
        else:
            response = await datasource.show_attachment(attachment_id=int(attachment_id))
        if not response.success or not response.data:
            raise Exception(
                f"Failed to resolve Zendesk attachment {attachment_id}: {response.error}"
            )
        key = "article_attachment" if is_article_attachment else "attachment"
        return self._extract_object(response.data, key).get("content_url")

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
            groups = await self._fetch_paginated_list(
                datasource.list_groups,
                "groups",
                exclude_deleted=True,
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
        try:
            datasource = await self._get_fresh_datasource()
            response = await datasource.list_groups(page=1, per_page=1)
            if not response.success:
                self.logger.error(f"Zendesk connection test failed: {response.error}")
            return bool(response.success)
        except Exception as e:
            self.logger.error(f"Zendesk connection test failed: {e}", exc_info=True)
            return False

    async def get_signed_url(self, record: Record) -> str:
        return ""

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
        self, api_method: Any, key: str, **kwargs: Any
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
        by_cursor = True
        while True:
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
                break
            # Sideloads ride in the same payload; dropped, authors render as raw ids.
            self._cache_sideloads(response.data)
            items = self._extract_list(response.data, key)
            if not items:
                break
            results.extend(items)

            meta = response.data.get("meta") if isinstance(response.data, dict) else None
            if by_cursor and not isinstance(meta, dict):
                # The endpoint ignored the cursor params and served page 1 offset-style;
                # that page is already collected, so just continue by number.
                by_cursor = False
            if by_cursor:
                if not meta.get("has_more"):
                    break
                after = meta.get("after_cursor")
                if not after:
                    break
            else:
                if len(items) < PAGE_SIZE:
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

    def _cache_sideloads(self, payload: Dict[str, Any]) -> None:
        for user in self._extract_list(payload, "users"):
            if user.get("id") is not None:
                self._user_id_to_data[str(user["id"])] = user
        for group in self._extract_list(payload, "groups"):
            if group.get("id") is not None:
                self._group_id_to_data[str(group["id"])] = group

    async def _get_start_time(self, sync_point_key: str = SYNC_POINT_KEY) -> int:
        sync_point = await self.records_sync_point.read_sync_point(sync_point_key)
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
        return bool(org_data.get("shared_tickets"))

    def _record_permissions(
        self,
        group_id: Any,
        requester: Dict[str, Any],
        organization_id: Any = None,
    ) -> List[Permission]:
        permissions: List[Permission] = []
        if group_id:
            permissions.append(Permission(
                external_id=f"group_{group_id}",
                type=PermissionType.READ,
                entity_type=EntityType.GROUP,
            ))
        # Only when Zendesk itself shares the org's tickets — see _org_shares_tickets.
        if organization_id and self._org_shares_tickets(organization_id):
            permissions.append(Permission(
                external_id=f"org_{organization_id}",
                type=PermissionType.READ,
                entity_type=EntityType.GROUP,
            ))
        requester_email = requester.get("email")
        if requester_email:
            permissions.append(Permission(
                email=requester_email,
                type=PermissionType.READ,
                entity_type=EntityType.USER,
            ))
        if not permissions:
            self.logger.warning(
                "Zendesk: no permissions resolved for a record (group_id=%s) — "
                "it will not be visible to anyone",
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
        return bool(attachment.get("inline")) and str(
            attachment.get("content_type") or ""
        ).startswith("image/")

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