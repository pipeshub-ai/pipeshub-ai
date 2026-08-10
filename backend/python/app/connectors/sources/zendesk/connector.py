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
from app.config.constants.arangodb import AppGroups, Connectors, ProgressStatus
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
DEFAULT_INCREMENTAL_START_TIME = 1
PAGE_SIZE = 100
# Matches the Jira connectors: cap how many records go into one on_new_records call.
BATCH_PROCESSING_SIZE = 100
# Zendesk rejects an incremental start_time inside the last minute.
INCREMENTAL_SAFETY_LAG_SECONDS = 60
RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
# Base64 inflates by a third and the result is held in the record body.
MAX_INLINE_IMAGE_BYTES = 10 * 1024 * 1024
IMG_SRC_PATTERN = re.compile(r'(<img\b[^>]*?\bsrc=["\'])([^"\']+)(["\'])', re.IGNORECASE)


@ConnectorBuilder("Zendesk")\
    .in_group(AppGroups.ZENDESK.value)\
    .with_description("Sync tickets, comments, attachments, articles, users, and groups from Zendesk")\
    .with_categories(["Help Desk", "Knowledge Base"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.API_TOKEN).fields([
            AuthField(
                name="apiToken",
                display_name="API Token",
                placeholder="Enter your API Token",
                description="The API Token from Zendesk instance",
                field_type="PASSWORD",
                max_length=2000,
                is_secret=True,
            ),
            AuthField(
                name="email",
                display_name="Email",
                placeholder="Enter your Email",
                description="The Email from Zendesk instance",
                field_type="TEXT",
                max_length=2000,
            ),
            AuthField(
                name="subdomain",
                display_name="Subdomain",
                placeholder="Enter your Subdomain",
                description="The Subdomain from Zendesk instance",
                field_type="TEXT",
                max_length=2000,
            ),
        ]),
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
            "Zendesk API Token Setup",
            "https://developer.zendesk.com/documentation/ticketing/introduction/authentication/",
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
            name="issue_attachments",
            display_name="Index Ticket Attachments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of ticket attachments",
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
        self._org_id_to_data: Dict[str, Dict[str, Any]] = {}
        self._user_id_to_app_user: Dict[str, AppUser] = {}
        self._reemit_unchanged = False
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
            await self.init()
        return self.data_source

    async def _oauth_token_rotated(self) -> bool:
        # True when the stored access token differs from the one the client holds.
        client = self.external_client.get_client()
        in_use = getattr(client, "access_token", None)
        if not in_use:  # API-token auth carries no rotating credential
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

        # A full sync deletes the sync point and every BELONGS_TO edge with it. Skipping
        # unchanged records would then leave them orphaned — unreachable from the App and
        # counted as zero — so re-emit everything when the checkpoint is gone.
        sync_point = await self.records_sync_point.read_sync_point(SYNC_POINT_KEY)
        self._reemit_unchanged = not (sync_point or {}).get("lastEndTime")
        if self._reemit_unchanged:
            self.logger.info("Zendesk: no sync point — re-emitting every record")

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

        org_user_groups = await self._fetch_organizations()
        if org_user_groups and users_complete:
            await self.data_entities_processor.on_new_user_groups(org_user_groups)
        elif org_user_groups:
            self.logger.error(
                "Zendesk: skipping organization membership sync — the user export was "
                "truncated and partial membership would revoke existing access"
            )
        self.logger.info(f"Zendesk: synced {len(org_user_groups)} organizations")

        ticket_count = await self._sync_tickets()
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
        datasource = await self._get_fresh_datasource()
        start_time = DEFAULT_INCREMENTAL_START_TIME
        users_data: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        complete = True

        while True:
            response = await datasource.incremental_users(start_time=start_time, cursor=cursor)
            if not response.success:
                self.logger.error(f"Zendesk incremental_users failed: {response.error}")
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
        datasource = await self._get_fresh_datasource()
        groups_data = await self._fetch_paginated_list(
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
            self._group_id_to_data[group_id] = group_data

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

        return record_groups, user_groups, memberships_complete

    async def _fetch_organizations(self) -> List[Tuple[AppUserGroup, List[AppUser]]]:
        """Sync Zendesk organizations as user groups.

        Membership comes from the users already fetched — Zendesk has no bulk
        organization-membership endpoint. Not RecordGroups: nothing files a record
        under one, so they would render empty; tickets carry the org permission.
        """
        datasource = await self._get_fresh_datasource()
        orgs_data: List[Dict[str, Any]] = []
        start_time = DEFAULT_INCREMENTAL_START_TIME
        while True:
            response = await datasource.incremental_organizations(start_time=start_time)
            if not response.success:
                self.logger.error(f"Zendesk incremental_organizations failed: {response.error}")
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

        return user_groups

    async def _sync_tickets(self) -> int:
        if not self._is_indexing_enabled(IndexingFilterKey.TICKETS.value):
            return 0

        datasource = await self._get_fresh_datasource()
        synced = 0
        start_time = await self._get_start_time()
        cursor: Optional[str] = None
        max_end_time = start_time
        while True:
            response = await datasource.incremental_tickets(
                start_time=start_time,
                cursor=cursor,
                include="users,groups,organizations",
            )
            if not response.success:
                self.logger.error(f"Zendesk incremental_tickets failed: {response.error}")
                break
            if not response.data:
                break
            payload = response.data
            self._cache_sideloads(payload)
            tickets = self._extract_list(payload, "tickets")
            records_with_permissions: List[Tuple[Record, List[Permission]]] = []
            for ticket_data in tickets:
                record_tuple = await self._ticket_to_record(ticket_data)
                if record_tuple:
                    records_with_permissions.append(record_tuple)
            if records_with_permissions:
                await self.data_entities_processor.on_new_records(records_with_permissions)
                synced += len(records_with_permissions)

            # Cursor export returns no end_time; resume from the newest ticket seen.
            for ticket_data in tickets:
                updated_ms = self._parse_datetime(ticket_data.get("updated_at"))
                if updated_ms:
                    max_end_time = max(max_end_time, updated_ms // 1000)

            next_cursor = payload.get("after_cursor") or payload.get("cursor")
            if payload.get("end_of_stream", True) or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

        now_seconds = get_epoch_timestamp_in_ms() // 1000
        max_end_time = min(max_end_time, now_seconds - INCREMENTAL_SAFETY_LAG_SECONDS)
        await self.records_sync_point.update_sync_point(
            SYNC_POINT_KEY,
            {"lastEndTime": max_end_time, "updatedAt": get_epoch_timestamp_in_ms()},
        )
        return synced

    async def _ticket_to_record(self, ticket_data: Dict[str, Any]) -> Optional[Tuple[Record, List[Permission]]]:
        ticket_id = ticket_data.get("id")
        group_id = ticket_data.get("group_id")
        if not ticket_id:
            return None
        if group_id and not self._is_group_allowed_by_filter(str(group_id)):
            return None

        created_at = self._parse_datetime(ticket_data.get("created_at"))
        updated_at = self._parse_datetime(ticket_data.get("updated_at"))
        if not self._is_allowed_by_date_filters(created_at, updated_at):
            return None

        existing_record = None
        async with self.data_store_provider.transaction() as tx_store:
            existing_record = await tx_store.get_record_by_external_id(
                connector_id=self.connector_id,
                external_id=str(ticket_id),
            )

        if (
            existing_record
            and existing_record.source_updated_at == updated_at
            and not self._reemit_unchanged
        ):
            return None

        record_id = existing_record.id if existing_record else str(uuid4())
        version = 0 if existing_record is None else existing_record.version + 1
        requester = self._user_id_to_data.get(str(ticket_data.get("requester_id")), {})
        assignee = self._user_id_to_data.get(str(ticket_data.get("assignee_id")), {})
        status = self.value_mapper.map_status(ticket_data.get("status")) or Status.UNKNOWN
        priority = self.value_mapper.map_priority(ticket_data.get("priority")) or Priority.UNKNOWN
        item_type = self.value_mapper.map_type(ticket_data.get("type")) or ItemType.UNKNOWN
        external_group_id = f"group_{group_id}" if group_id else None

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
            labels=ticket_data.get("tags") or [],
            indexing_status=ProgressStatus.NOT_STARTED.value,
        )
        permissions = self._record_permissions(
            group_id, requester, ticket_data.get("organization_id")
        )
        return record, permissions

    async def _sync_help_center_articles(self) -> int:
        if not self._is_indexing_enabled(IndexingFilterKey.KNOWLEDGE_BASE.value):
            return 0

        # Sections first: a record with no record group is unreachable from the App.
        await self._sync_help_center_sections()

        datasource = await self._get_fresh_datasource()
        articles = await self._fetch_paginated_list(
            datasource.list_articles,
            "articles",
            sort_by="updated_at",
            sort_order="asc",
            include="users,sections,categories",
        )
        records_with_permissions: List[Tuple[Record, List[Permission]]] = []
        for article_data in articles:
            record_tuple = await self._article_to_record(article_data)
            if record_tuple:
                records_with_permissions.append(record_tuple)
        for start in range(0, len(records_with_permissions), BATCH_PROCESSING_SIZE):
            await self.data_entities_processor.on_new_records(
                records_with_permissions[start:start + BATCH_PROCESSING_SIZE]
            )
        return len(records_with_permissions)

    async def _sync_help_center_sections(self) -> None:
        datasource = await self._get_fresh_datasource()
        sections = await self._fetch_paginated_list(datasource.list_sections, "sections")
        record_groups: List[Tuple[RecordGroup, List[Permission]]] = []
        for section_data in sections:
            section_id = section_data.get("id")
            if not section_id:
                continue
            self._section_id_to_data[str(section_id)] = section_data
            record_groups.append((
                RecordGroup(
                    org_id=self.data_entities_processor.org_id,
                    name=section_data.get("name") or f"Section {section_id}",
                    external_group_id=f"section_{section_id}",
                    connector_name=Connectors.ZENDESK,
                    connector_id=self.connector_id,
                    group_type=RecordGroupType.KB,
                    description=section_data.get("description") or None,
                    source_created_at=self._parse_datetime(section_data.get("created_at")),
                    source_updated_at=self._parse_datetime(section_data.get("updated_at")),
                    web_url=section_data.get("html_url"),
                ),
                [Permission(
                    type=PermissionType.READ,
                    entity_type=EntityType.ORG,
                    external_id=self.data_entities_processor.org_id,
                )],
            ))
        if record_groups:
            await self.data_entities_processor.on_new_record_groups(record_groups)
        self.logger.info(f"Zendesk: synced {len(record_groups)} Help Center sections")

    async def _article_to_record(self, article_data: Dict[str, Any]) -> Optional[Tuple[Record, List[Permission]]]:
        article_id = article_data.get("id")
        if not article_id:
            return None

        # user_segment_id is the article's whole ACL. Restricted articles and drafts
        # are skipped, not published — segments need roles this connector has no sync for.
        if article_data.get("draft"):
            return None
        if article_data.get("user_segment_id") is not None or article_data.get("user_segment_ids"):
            return None

        created_at = self._parse_datetime(article_data.get("created_at"))
        updated_at = self._parse_datetime(article_data.get("updated_at"))
        if not self._is_allowed_by_date_filters(created_at, updated_at):
            return None

        existing_record = None
        async with self.data_store_provider.transaction() as tx_store:
            existing_record = await tx_store.get_record_by_external_id(
                connector_id=self.connector_id,
                external_id=f"article_{article_id}",
            )
        if (
            existing_record
            and existing_record.source_updated_at == updated_at
            and not self._reemit_unchanged
        ):
            return None

        record_id = existing_record.id if existing_record else str(uuid4())
        version = 0 if existing_record is None else existing_record.version + 1
        section_id = article_data.get("section_id")
        external_group_id = f"section_{section_id}" if section_id else None
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
            indexing_status=ProgressStatus.NOT_STARTED.value,
        )
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

    async def _process_ticket_blockgroups_for_streaming(self, record: Record) -> bytes:
        datasource = await self._get_fresh_datasource()
        comments = await self._fetch_paginated_list(
            datasource.list_comments,
            "comments",
            ticket_id=int(record.external_record_id),
            sort_order="asc",
            include="users",
        )
        # public=False is an internal agent note. This record grants the requester
        # READ, so indexing one would hand it to the customer.
        comments = [c for c in comments if c.get("public", True)]
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
        block_group = BlockGroup(
            id=str(uuid4()),
            index=0,
            name=article.get("title") or record.record_name,
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.CONTENT,
            source_group_id=str(article_id),
            data=body_md,
            format=DataFormat.MARKDOWN,
            weburl=record.weburl,
            requires_processing=True,
        )
        return BlocksContainer(blocks=[], block_groups=[block_group]).model_dump_json(indent=2).encode("utf-8")

    async def _inline_images_as_base64(self, html: str) -> str:
        # Rewrite ``<img src>`` to base64 data URIs so inline images get indexed.
        if not html or "<img" not in html.lower():
            return html
        datasource = await self._get_fresh_datasource()
        resolved: Dict[str, str] = {}
        for match in IMG_SRC_PATTERN.finditer(html):
            url = match.group(2)
            if url in resolved or url.startswith("data:"):
                continue
            if not self._is_tenant_api_url(url):
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
            response = await datasource.http.execute(
                HTTPRequest(url=url, method="GET", headers=datasource.http.headers.copy())
            )
            if response.status >= 400:
                self.logger.warning(f"Zendesk inline image {url} returned {response.status}")
                return None
            raw = response.bytes()
            if len(raw) > MAX_INLINE_IMAGE_BYTES:
                self.logger.warning(f"Skipping oversized Zendesk inline image ({len(raw)} bytes): {url}")
                return None
            mime = response.content_type
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
        if not self._is_indexing_enabled(IndexingFilterKey.ISSUE_ATTACHMENTS.value):
            return []
        # An attachment has no ACL of its own — it inherits its comment's.
        if not comment.get("public", True):
            return []
        attachments = comment.get("attachments") or []
        child_records: List[ChildRecord] = []
        records_with_permissions: List[Tuple[Record, List[Permission]]] = []
        for attachment in attachments:
            attachment_id = attachment.get("id")
            content_url = attachment.get("content_url")
            if not attachment_id or not content_url:
                continue
            external_id = f"ticket_{parent_record.external_record_id}_comment_{comment.get('id')}_attachment_{attachment_id}"
            existing_record = None
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=external_id,
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
                external_record_group_id=parent_record.external_record_group_id,
                record_group_type=parent_record.record_group_type,
                version=version,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.ZENDESK,
                connector_id=self.connector_id,
                mime_type=attachment.get("content_type") or MimeTypes.UNKNOWN.value,
                # Ticket page, not content_url: that URL is a bearer capability and
                # weburl is readable from metadata. Re-fetched per download instead.
                weburl=parent_record.weburl,
                is_file=True,
                extension=self._extension(file_name),
                size_in_bytes=attachment.get("size"),
                source_created_at=parent_record.source_created_at,
                source_updated_at=parent_record.source_updated_at,
                indexing_status=ProgressStatus.NOT_STARTED.value,
            )
            parent_group_id = None
            if parent_record.external_record_group_id and parent_record.external_record_group_id.startswith("group_"):
                parent_group_id = parent_record.external_record_group_id.removeprefix("group_")
            if existing_record is None:
                requester = {"email": getattr(parent_record, "reporter_email", None)}
                records_with_permissions.append(
                    (file_record, self._record_permissions(parent_group_id, requester))
                )
            child_records.append(ChildRecord(
                child_type=ChildType.RECORD,
                child_id=record_id,
                child_name=file_name,
            ))
        if records_with_permissions:
            await self.data_entities_processor.on_new_records(records_with_permissions)
        return child_records

    async def _process_file_for_streaming(self, record: Record) -> bytes:
        datasource = await self._get_fresh_datasource()
        content_url = await self._resolve_attachment_url(record)
        if not content_url:
            raise ValueError("Zendesk attachment missing content URL")
        if not self._is_safe_zendesk_asset_url(content_url):
            raise ValueError(
                f"Refusing to fetch Zendesk attachment from untrusted host: {urlparse(content_url).hostname}"
            )
        # CDN URLs are pre-signed and the host is shared across tenants.
        headers = (
            datasource.http.headers.copy()
            if self._is_tenant_api_url(content_url)
            else {}
        )
        response = await datasource.http.execute(HTTPRequest(url=content_url, method="GET", headers=headers))
        if response.status >= 400:
            raise Exception(f"Failed to download Zendesk attachment {record.external_record_id}: {response.status}")
        return response.bytes()

    async def _resolve_attachment_url(self, record: Record) -> Optional[str]:
        """Ask Zendesk for the attachment's current content_url.

        Fetched per download so the capability never sits in the graph beside
        metadata that is readable more widely than the file itself.
        """
        attachment_id = (record.external_record_id or "").rsplit("_attachment_", 1)[-1]
        if not attachment_id.isdigit():
            raise ValueError(
                f"Unrecognised Zendesk attachment record id: {record.external_record_id}"
            )
        datasource = await self._get_fresh_datasource()
        response = await datasource.show_attachment(attachment_id=int(attachment_id))
        if not response.success or not response.data:
            raise Exception(
                f"Failed to resolve Zendesk attachment {attachment_id}: {response.error}"
            )
        return self._extract_object(response.data, "attachment").get("content_url")

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

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
    ) -> "BaseConnector":
        data_entities_processor = DataSourceEntitiesProcessor(
            logger,
            data_store_provider,
            config_service,
        )
        await data_entities_processor.initialize()
        return ZendeskConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )

    async def _call_page(self, api_method: Any, page: int, **kwargs: Any) -> Any:
        """Re-raise a retryable status as the exception ``call_with_retry`` acts on.

        The data source folds HTTP errors into a ``ZendeskResponse`` instead of
        raising, so a 429 would otherwise never be retried.
        """
        response = await api_method(page=page, per_page=PAGE_SIZE, **kwargs)
        status = response.status_code
        if not response.success and status in RETRYABLE_STATUS_CODES:
            request = httpx.Request("GET", getattr(api_method, "__name__", "zendesk"))
            raise httpx.HTTPStatusError(
                f"Zendesk HTTP {status}: {response.error}",
                request=request,
                response=httpx.Response(status, request=request),
            )
        return response

    async def _fetch_paginated_list(self, api_method: Any, key: str, **kwargs: Any) -> List[Dict[str, Any]]:
        items, _ = await self._fetch_paginated_list_checked(api_method, key, **kwargs)
        return items

    async def _fetch_paginated_list_checked(
        self, api_method: Any, key: str, **kwargs: Any
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """Paginate an offset-based endpoint, reporting whether it ran to the end.

        Callers that rebuild state from the full result — group membership above all —
        must not treat a truncated list as authoritative. Zendesk 400s past 10,000
        records here rather than raising.
        """
        label = getattr(api_method, "__name__", key)
        page = 1
        results: List[Dict[str, Any]] = []
        while True:
            try:
                response = await call_with_retry(
                    partial(self._call_page, api_method, page, **kwargs),
                    logger=self.logger,
                    label=f"zendesk/{label} page {page}",
                )
            except httpx.HTTPStatusError as e:
                self.logger.error(f"Zendesk {label} page {page} gave up: {e}")
                return results, False
            if not response.success:
                self.logger.error(f"Zendesk {label} page {page} failed: {response.error}")
                return results, False
            if not response.data:
                break
            # Sideloads ride in the same payload; dropped, authors render as raw ids.
            self._cache_sideloads(response.data)
            items = self._extract_list(response.data, key)
            if not items:
                break
            results.extend(items)
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

    async def _get_start_time(self) -> int:
        sync_point = await self.records_sync_point.read_sync_point(SYNC_POINT_KEY)
        start_time = sync_point.get("lastEndTime") or DEFAULT_INCREMENTAL_START_TIME
        modified_filter = self.sync_filters.get(SyncFilterKey.MODIFIED) if self.sync_filters else None
        if modified_filter:
            value = modified_filter.get_value(default=None)
            if isinstance(value, tuple) and value[0]:
                start_time = max(int(start_time), int(value[0] / 1000))
        return int(start_time)

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
        # Shared-organization visibility; the org user group carries the membership.
        if organization_id:
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

    def _is_indexing_enabled(self, key: str) -> bool:
        if not self.indexing_filters:
            return True
        filter_obj = self.indexing_filters.get(key)
        if not filter_obj:
            return True
        return bool(filter_obj.get_value(default=True))

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
