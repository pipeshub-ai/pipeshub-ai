"""Odoo connector — syncs CRM leads/opportunities (crm.lead) as DealRecord
entries, sales teams (crm.team) as RecordGroups, plus salespersons
(res.users) as AppUsers.

Scope: CRM leads only, matching app/sources/external/odoo/odoo.py. Teams
are synced purely as a structural container (every connector in this repo
groups its records under something — Odoo is otherwise the only exception,
and a flat/groupless connector's records don't show up at all in the "All
Records" browse tree, since that view only queries RecordGroups under an
app, not bare records). Team-based *sharing* (whole team can see the whole
pipeline, not just its own leads) is a separate, still-open decision — see
the permissions docstring below. Roles are intentionally not synced —
res.groups/ir.rule gate model-level access, not individual records, so
there's no clean "role -> these specific leads" list to sync the way
BookStack's per-content role permissions work.

Permissions per lead: OWNER for the assigned salesperson (user_id) plus
READER for every mail.followers subscriber on that lead — followers are
Odoo's only genuine per-record "who's actually looped into this" signal,
closer to BookStack's content-level permissions than a coarse team-wide
grant would be. Team-based sharing (whole team sees the whole pipeline) is
a separate, still-open decision — add it if followers alone prove too
narrow for how a given org actually uses Odoo teams.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from logging import Logger
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
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
    FilterOptionsResponse,
    FilterType,
    OptionSourceType,
    load_connector_filters,
)
from app.connectors.sources.odoo.apps import OdooApp
from app.models.entities import (
    AppUser,
    DealRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.odoo.odoo import OdooClient, OdooClientBuilder
from app.sources.external.odoo.odoo import CrmLead, OdooDataSource
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import get_epoch_timestamp_in_ms


def _m2o_id(value: Any) -> Optional[int]:
    """Odoo many2one fields come back as [id, "Display Name"] or False."""
    if isinstance(value, (list, tuple)) and value:
        return int(value[0])
    return None


def _m2o_name(value: Any) -> Optional[str]:
    if isinstance(value, (list, tuple)) and len(value) > 1:
        return str(value[1])
    return None


def _str_or_none(value: Any) -> Optional[str]:
    """Odoo returns False (not None) for empty char/date fields over XML-RPC."""
    return value if isinstance(value, str) else None


def _parse_odoo_datetime(value: Any) -> Optional[int]:
    """Odoo datetime fields are naive UTC strings: "2024-01-15 10:30:00"."""
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None


def _odoo_now() -> str:
    """Current time in the same format Odoo's write_date fields use, so it
    can be compared directly in a search_read domain."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@ConnectorBuilder("Odoo")\
    .in_group("Odoo")\
    .with_supported_auth_types("BASIC_AUTH")\
    .with_description("Sync CRM leads and opportunities from your Odoo instance")\
    .with_categories(["CRM"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.BASIC_AUTH).fields([
            CommonFields.base_url("Odoo"),
            AuthField(
                name="db",
                display_name="Database Name",
                placeholder="mycompany",
                description="The Odoo database name",
                field_type="TEXT",
                max_length=200,
            ),
            CommonFields.username(),
            AuthField(
                name="apiKey",
                display_name="API Key",
                placeholder="Enter your API key",
                description="Odoo API key (Settings > Users > Account Security > API Keys)",
                field_type="PASSWORD",
                max_length=200,
                is_secret=True,
            ),
        ])
    ])\
    .with_info(CONNECTOR_EMAIL_IDENTITY_INFO)\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.ODOO.value))\
        .add_documentation_link(DocumentationLink(
            "Odoo External API Docs",
            "https://www.odoo.com/documentation/17.0/developer/reference/external_api.html",
            "docs"
        ))
        .add_filter_field(CommonFields.modified_date_filter("Filter leads by last modification date."))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .add_filter_field(FilterField(
            name="lead_type",
            display_name="Lead Type",
            description="Sync leads, opportunities, or both.",
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            default_value=[],
            options=["lead", "opportunity"],
            option_source_type=OptionSourceType.STATIC,
        ))
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(False)
    )\
    .build_decorator()
class OdooConnector(BaseConnector):
    """Connector for synchronizing CRM leads/opportunities from an Odoo instance."""

    base_url: str

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
            OdooApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )

        self.connector_name = Connectors.ODOO
        self.connector_id = connector_id

        self._create_sync_points()

        self.client: Optional[OdooClient] = None
        self.data_source: Optional[OdooDataSource] = None
        self.base_url = ""
        self.batch_size = 100

        # id -> email, built during _sync_users(); used to attach owner
        # permissions to leads without a per-lead user lookup.
        self._user_email_by_id: Dict[int, str] = {}
        # res.partner id -> email, for resolving mail.followers subscribers
        # (followers are stored as partner_id, not user_id) back to a known
        # internal user. Followers that don't match any entry here are
        # external contacts, not PipesHub users — silently skipped.
        self._user_email_by_partner_id: Dict[int, str] = {}

        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

    def _create_sync_points(self) -> None:
        self.record_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=self.data_entities_processor.org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=self.data_store_provider,
        )

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
            logger, data_store_provider, config_service
        )
        await data_entities_processor.initialize()

        return OdooConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )

    async def init(self) -> bool:
        try:
            client_builder = await OdooClientBuilder.build_from_services(
                self.logger, self.config_service, self.connector_id
            )
            client = client_builder.get_client()
            await client.connect()

            self.client = client
            self.base_url = client.url
            self.data_source = OdooDataSource(client)

            self.logger.info("Odoo client initialized successfully.")
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize Odoo client: {e}", exc_info=True)
            return False

    async def test_connection_and_access(self) -> bool:
        if not self.data_source:
            self.logger.error("Odoo data source not initialized")
            return False

        try:
            await self.data_source.count_leads()
            self.logger.info("Odoo connection test successful.")
            return True
        except Exception as e:
            self.logger.error(f"Odoo connection test failed: {e}", exc_info=True)
            return False

    async def cleanup(self) -> None:
        self.logger.info("Cleaning up Odoo connector resources.")
        if self.client:
            await self.client.close()
        self.client = None
        self.data_source = None

    async def run_sync(self) -> None:
        await self._run_sync(full_sync=True)

    async def run_incremental_sync(self) -> None:
        await self._run_sync(full_sync=False)

    async def _run_sync(self, full_sync: bool) -> None:
        """full_sync=True (the "Full sync" button / first-ever sync) ignores
        the stored write_date cursor and re-fetches every lead — this is
        what actually lets a connector-code change (e.g. a new field we
        now want to backfill) reach leads that haven't changed in Odoo
        since they were first synced. full_sync=False only fetches what's
        genuinely new since the last run."""
        try:
            label = "full" if full_sync else "incremental"
            self.logger.info(f"Starting Odoo {label} sync.")

            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "odoo", self.connector_id, self.logger
            )

            self.logger.info("Syncing users (salespersons)...")
            await self._sync_users()

            self.logger.info("Syncing sales teams...")
            await self._sync_teams()

            self.logger.info("Syncing leads/opportunities...")
            await self._sync_leads(full_sync=full_sync)

            self.logger.info(f"Odoo {label} sync completed.")
        except Exception as ex:
            self.logger.error(f"Error in Odoo connector run: {ex}", exc_info=True)
            raise

    # -- Users -----------------------------------------------------------

    async def _sync_users(self) -> None:
        """Odoo's user list is just internal salespersons — small enough
        that a full refresh every run is simpler and cheap enough to skip
        a separate incremental cursor for it."""
        if not self.data_source:
            return

        users = await self.data_source.list_users(include_inactive=True)

        app_users: List[AppUser] = []
        self._user_email_by_id = {}
        self._user_email_by_partner_id = {}
        for user in users:
            email = user.email if isinstance(user.email, str) else (user.login or None)
            if not email:
                continue
            self._user_email_by_id[user.id] = email
            partner_id = _m2o_id(user.partner_id)
            if partner_id is not None:
                self._user_email_by_partner_id[partner_id] = email
            app_users.append(
                AppUser(
                    app_name=Connectors.ODOO,
                    connector_id=self.connector_id,
                    source_user_id=str(user.id),
                    full_name=user.name,
                    email=email,
                    is_active=user.active,
                )
            )

        await self.data_entities_processor.on_new_app_users(app_users)
        self.logger.info(f"Synced {len(app_users)} Odoo users.")

    # -- Teams -------------------------------------------------------------

    @staticmethod
    def _team_external_group_id(team_id: int) -> str:
        return f"crm.team/{team_id}"

    async def _sync_teams(self) -> None:
        """Sales teams as RecordGroups — purely structural (lets leads show
        up in the All Records browse tree, which only lists RecordGroups
        under an app, never bare records). Small, rarely-changing list —
        full refresh every run, no separate incremental cursor."""
        if not self.data_source:
            return

        teams = await self.data_source.list_teams()
        groups = [
            (
                RecordGroup(
                    name=team.name or f"Team {team.id}",
                    org_id=self.data_entities_processor.org_id,
                    external_group_id=self._team_external_group_id(team.id),
                    connector_name=Connectors.ODOO,
                    connector_id=self.connector_id,
                    group_type=RecordGroupType.PROJECT,
                ),
                [],
            )
            for team in teams
        ]
        if groups:
            await self.data_entities_processor.on_new_record_groups(groups)
        self.logger.info(f"Synced {len(groups)} Odoo sales teams.")

    # -- Leads -------------------------------------------------------------

    def _get_lead_type_filter(self) -> Optional[List[str]]:
        values = self.sync_filters.get_value("lead_type")
        return list(values) if values else None

    async def _sync_leads(self, full_sync: bool = False) -> None:
        if not self.data_source:
            return

        current_timestamp = _odoo_now()
        sync_key = generate_record_sync_point_key("odoo", "leads", "global")
        sync_point = await self.record_sync_point.read_sync_point(sync_key)
        # full_sync ignores the stored cursor entirely — this is what lets
        # a connector-code change (e.g. a newly-added field) reach leads
        # that haven't changed in Odoo since they were first synced.
        last_write_date = None if full_sync else sync_point.get("write_date")

        allowed_types = self._get_lead_type_filter()

        batch_records: List[Tuple[DealRecord, List[Permission]]] = []
        offset = 0

        while True:
            leads = await self.data_source.list_leads(
                updated_since=last_write_date,
                include_archived=True,
                limit=self.batch_size,
                offset=offset,
            )
            if not leads:
                break

            followers_by_lead = await self._fetch_followers_by_lead(
                [lead.id for lead in leads]
            )

            for lead in leads:
                if allowed_types and lead.type not in allowed_types:
                    continue

                record, permissions, is_new = await self._process_lead(
                    lead, followers_by_lead.get(lead.id, [])
                )

                if is_new:
                    batch_records.append((record, permissions))
                    if len(batch_records) >= self.batch_size:
                        await self.data_entities_processor.on_new_records(batch_records)
                        batch_records = []
                else:
                    await self.data_entities_processor.on_record_content_update(record)
                    await self.data_entities_processor.on_updated_record_permissions(
                        record, permissions
                    )

            offset += len(leads)
            if len(leads) < self.batch_size:
                break

        if batch_records:
            await self.data_entities_processor.on_new_records(batch_records)

        await self.record_sync_point.update_sync_point(
            sync_key, {"write_date": current_timestamp}
        )
        self.logger.info("Finished syncing Odoo leads.")

    async def _fetch_followers_by_lead(
        self, lead_ids: List[int]
    ) -> Dict[int, List[int]]:
        """One batched mail.followers call per page instead of one per
        lead — group the resulting (res_id, partner_id) rows by lead."""
        if not self.data_source or not lead_ids:
            return {}
        followers = await self.data_source.list_followers("crm.lead", lead_ids)
        by_lead: Dict[int, List[int]] = defaultdict(list)
        for follower in followers:
            if follower.res_id is None:
                continue
            partner_id = _m2o_id(follower.partner_id)
            if partner_id is not None:
                by_lead[follower.res_id].append(partner_id)
        return by_lead

    def _build_lead_permissions(
        self, owner_id: Optional[int], follower_partner_ids: List[int]
    ) -> List[Permission]:
        permissions: List[Permission] = []
        seen_emails: set[str] = set()

        if owner_id is not None:
            email = self._user_email_by_id.get(owner_id)
            if email:
                permissions.append(
                    Permission(
                        external_id=str(owner_id),
                        email=email,
                        type=PermissionType.OWNER,
                        entity_type=EntityType.USER,
                    )
                )
                seen_emails.add(email)

        for partner_id in follower_partner_ids:
            email = self._user_email_by_partner_id.get(partner_id)
            if not email or email in seen_emails:
                continue
            seen_emails.add(email)
            permissions.append(
                Permission(
                    external_id=email,
                    email=email,
                    type=PermissionType.READ,
                    entity_type=EntityType.USER,
                )
            )

        return permissions

    async def _process_lead(
        self, lead: CrmLead, follower_partner_ids: Optional[List[int]] = None
    ) -> Tuple[DealRecord, List[Permission], bool]:
        external_id = f"crm.lead/{lead.id}"

        async with self.data_store_provider.transaction() as tx_store:
            existing_record = await tx_store.get_record_by_external_id(
                connector_id=self.connector_id, external_id=external_id
            )
        is_new = existing_record is None

        owner_id = _m2o_id(lead.user_id)
        permissions = self._build_lead_permissions(owner_id, follower_partner_ids or [])

        team_id = _m2o_id(lead.team_id)
        external_group_id = self._team_external_group_id(team_id) if team_id is not None else None

        created_at_ms = _parse_odoo_datetime(lead.create_date) or get_epoch_timestamp_in_ms()
        updated_at_ms = _parse_odoo_datetime(lead.write_date) or get_epoch_timestamp_in_ms()

        record = DealRecord(
            id=existing_record.id if existing_record else str(uuid.uuid4()),
            record_name=lead.name or f"Lead #{lead.id}",
            external_record_id=external_id,
            connector_name=Connectors.ODOO,
            connector_id=self.connector_id,
            record_type=RecordType.DEAL,
            origin=OriginTypes.CONNECTOR,
            org_id=self.data_entities_processor.org_id,
            version=0 if is_new else existing_record.version + 1,
            external_revision_id=lead.write_date,
            external_record_group_id=external_group_id,
            record_group_type=RecordGroupType.PROJECT if external_group_id else None,
            weburl=f"{self.base_url}/web#id={lead.id}&model=crm.lead&view_type=form",
            # Must match the media_type stream_record() actually streams —
            # the indexing pipeline gates on this before ever calling
            # stream_record(); UNKNOWN silently drops every lead as
            # FILE_TYPE_NOT_SUPPORTED without embedding anything.
            mime_type=MimeTypes.PLAIN_TEXT.value,
            created_at=created_at_ms,
            updated_at=updated_at_ms,
            source_created_at=created_at_ms,
            source_updated_at=updated_at_ms,
            inherit_permissions=False,
            name=lead.name,
            amount=lead.expected_revenue,
            expected_revenue=lead.expected_revenue,
            expected_close_date=_str_or_none(lead.date_deadline),
            conversion_probability=lead.probability,
            type=lead.type,
            owner_id=str(owner_id) if owner_id is not None else None,
            is_won=lead.probability >= 100,
            is_closed=not lead.active,
            created_date=_str_or_none(lead.create_date),
            close_date=_str_or_none(lead.date_closed),
        )

        return record, permissions, is_new

    # -- Record access -------------------------------------------------------

    async def get_signed_url(self, record: Record) -> Optional[str]:
        """Odoo has no signed-download-URL concept for CRM records — link
        straight into the backend form view instead."""
        lead_id = record.external_record_id.split("/")[-1]
        return f"{self.base_url}/web#id={lead_id}&model=crm.lead&view_type=form"

    async def stream_record(self, record: Record) -> StreamingResponse:
        if not self.data_source:
            raise HTTPException(
                status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
                detail="Odoo connector not initialized",
            )

        lead_id = int(record.external_record_id.split("/")[-1])
        lead = await self.data_source.get_lead(lead_id)
        if lead is None:
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail="Record not found or access denied",
            )

        summary = "\n".join([
            f"Name: {lead.name}",
            f"Type: {lead.type}",
            f"Stage: {_m2o_name(lead.stage_id) or ''}",
            f"Expected revenue: {lead.expected_revenue}",
            f"Probability: {lead.probability}",
            f"Description: {_str_or_none(lead.description) or ''}",
        ]).encode("utf-8")

        async def _content_stream() -> AsyncGenerator[bytes, None]:
            yield summary

        return create_stream_record_response(
            _content_stream(),
            filename=record.record_name,
            mime_type=MimeTypes.PLAIN_TEXT.value,
            fallback_filename=f"record_{record.id}",
        )

    def handle_webhook_notification(self, notification: Dict) -> None:
        """Odoo has no native webhooks without a custom Studio automation —
        placeholder for future compatibility, same as other connectors that
        lack push support."""
        self.logger.info("Odoo webhook received.")
        asyncio.create_task(self.run_incremental_sync())

    async def reindex_records(self, records: List[Record]) -> None:
        try:
            if not records:
                self.logger.info("No records to reindex")
                return

            if not self.data_source:
                raise Exception("Odoo client not initialized. Call init() first.")

            # Refresh the owner/follower email maps — reindex can run
            # without a preceding _sync_leads() in this connector instance.
            await self._sync_users()

            updated_records: List[Tuple[Record, List[Permission]]] = []
            non_updated_records: List[Record] = []

            for record in records:
                try:
                    lead_id = int(record.external_record_id.split("/")[-1])
                    lead = await self.data_source.get_lead(lead_id)
                    if lead is None:
                        continue
                    if lead.write_date != record.external_revision_id:
                        followers = await self.data_source.list_followers(
                            "crm.lead", [lead_id]
                        )
                        follower_partner_ids = [
                            pid
                            for f in followers
                            if (pid := _m2o_id(f.partner_id)) is not None
                        ]
                        updated_record, permissions, _is_new = await self._process_lead(
                            lead, follower_partner_ids
                        )
                        updated_records.append((updated_record, permissions))
                    else:
                        non_updated_records.append(record)
                except Exception as e:
                    self.logger.error(f"Error checking record {record.id} at source: {e}")
                    continue

            if updated_records:
                await self.data_entities_processor.on_new_records(updated_records)
                self.logger.info(f"Updated {len(updated_records)} records that changed at source")

            if non_updated_records:
                await self.data_entities_processor.reindex_existing_records(non_updated_records)
                self.logger.info(
                    f"Published reindex events for {len(non_updated_records)} non-updated records"
                )

        except Exception as e:
            self.logger.error(f"Error during Odoo reindex: {e}", exc_info=True)
            raise

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> FilterOptionsResponse:
        """No dynamic (API-fetched) filter options declared for Odoo yet —
        lead_type is a static list."""
        raise ValueError(f"Unsupported filter key: {filter_key}")
