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
import re
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
from app.sources.external.odoo.odoo import CrmLead, OdooDataSource, Partner
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
        self._user_email_by_id: Dict[int, str] = {}
        self._user_email_by_partner_id: Dict[int, str] = {}

        self._stage_is_won: Dict[int, bool] = {}

        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

    def _create_sync_points(self) -> None:
        self.record_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=self.data_entities_processor.org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=self.data_store_provider,
        )
        self.contact_sync_point = SyncPoint(
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
            await self._load_creator_email()
            if not self.creator_email and self.created_by:
                # Odoo is TEAM-scoped, so the base class's _load_creator_email()
                # (which only resolves for PERSONAL scope) is a no-op here.
                creator_user = await self.data_entities_processor.get_user_by_user_id(
                    self.created_by
                )
                if creator_user:
                    self.creator_email = creator_user.email

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

            self.logger.info("Syncing stages...")
            await self._sync_stages()

            self.logger.info("Syncing leads/opportunities...")
            await self._sync_leads(full_sync=full_sync)

            self.logger.info("Syncing contacts (res.partner)...")
            await self._sync_contacts(full_sync=full_sync)

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

    # -- Stages ------------------------------------------------------------

    async def _sync_stages(self) -> None:
        if not self.data_source:
            return
        stages = await self.data_source.list_stages()
        self._stage_is_won = {stage.id: stage.is_won for stage in stages}
        self.logger.info(f"Loaded {len(stages)} CRM stages.")

    # -- Teams / record-groups ---------------------------------------------

    # External group ID used for leads that have no sales team assigned.
    _UNASSIGNED_TEAM_EXTERNAL_GROUP_ID = "crm.team/__unassigned__"
    # External group ID for the Contacts record group.
    _CONTACTS_EXTERNAL_GROUP_ID = "res.partner/__contacts__"

    @staticmethod
    def _team_external_group_id(team_id: int) -> str:
        return f"crm.team/{team_id}"

    async def _sync_teams(self) -> None:
        if not self.data_source:
            return

        teams = await self.data_source.list_teams()
        groups: list[tuple[RecordGroup, list]] = [
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
        
        groups.append(
            (
                RecordGroup(
                    name="Unassigned",
                    org_id=self.data_entities_processor.org_id,
                    external_group_id=self._UNASSIGNED_TEAM_EXTERNAL_GROUP_ID,
                    connector_name=Connectors.ODOO,
                    connector_id=self.connector_id,
                    group_type=RecordGroupType.PROJECT,
                ),
                [],
            )
        )
        await self.data_entities_processor.on_new_record_groups(groups)
        self.logger.info(f"Synced {len(groups) - 1} Odoo sales teams + 1 Unassigned group.")

        # Always ensure the Contacts group exists so contact records appear
        # in the All Records browse tree.
        contacts_group: list[tuple[RecordGroup, list]] = [
            (
                RecordGroup(
                    name="Contacts",
                    org_id=self.data_entities_processor.org_id,
                    external_group_id=self._CONTACTS_EXTERNAL_GROUP_ID,
                    connector_name=Connectors.ODOO,
                    connector_id=self.connector_id,
                    group_type=RecordGroupType.PROJECT,
                ),
                [],
            )
        ]
        await self.data_entities_processor.on_new_record_groups(contacts_group)
        self.logger.info("Ensured 'Contacts' record group exists.")

    # -- Leads -------------------------------------------------------------

    def _get_lead_type_filter(self) -> Optional[List[str]]:
        """Returns the selected lead types, or None if all types should sync.
        Both ["lead", "opportunity"] selected is the same as no filter."""
        values = self.sync_filters.get_value("lead_type")
        if not values:
            return None
        types = list(values)
        # If user picked both options, treat as "no filter" to avoid two
        # separate Odoo calls.
        if set(types) >= {"lead", "opportunity"}:
            return None
        return types

    def _get_modified_since_filter(self) -> Optional[str]:
        """Fix #1: Read the configured 'modified' datetime filter and return
        it as an Odoo-style naive-UTC string ("YYYY-MM-DD HH:MM:SS"), or
        None if the user didn't configure one.
        The filter uses IS_AFTER semantics — start_iso is the lower bound."""
        f = self.sync_filters.get("modified")
        if f is None or f.is_empty():
            return None
        start_iso, _ = f.get_datetime_iso()  # e.g. "2024-01-15T10:30:00"
        if start_iso is None:
            return None
        # Convert ISO 8601 ("T" separator) → Odoo write_date format (" " separator)
        return start_iso.replace("T", " ")

    async def _sync_leads(self, full_sync: bool = False) -> None:
        if not self.data_source:
            return

        current_timestamp = _odoo_now()
        sync_key = generate_record_sync_point_key("odoo", "leads", "global")
        sync_point = await self.record_sync_point.read_sync_point(sync_key)
        cursor_write_date = None if full_sync else sync_point.get("write_date")
        configured_since = self._get_modified_since_filter()
        if cursor_write_date and configured_since:
            last_write_date = max(cursor_write_date, configured_since)
        else:
            last_write_date = cursor_write_date or configured_since

        allowed_types = self._get_lead_type_filter()
        type_passes: List[Optional[str]] = allowed_types if allowed_types else [None]

        batch_records: List[Tuple[DealRecord, List[Permission]]] = []

        for lead_type_pass in type_passes:
            offset = 0
            while True:
                leads = await self.data_source.list_leads(
                    lead_type=lead_type_pass,
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

    # -- Contacts (res.partner) -------------------------------------------

    async def _sync_contacts(self, full_sync: bool = False) -> None:
        """Sync Odoo contacts (res.partner) as RecordType.OTHERS records
        inside a dedicated 'Contacts' record group. Uses its own write_date
        cursor so it advances independently from leads."""
        if not self.data_source:
            return

        current_timestamp = _odoo_now()
        sync_key = generate_record_sync_point_key("odoo", "contacts", "global")
        sync_point = await self.contact_sync_point.read_sync_point(sync_key)
        cursor_write_date = None if full_sync else sync_point.get("write_date")

        # Apply the same user-configured modified-date floor used for leads.
        configured_since = self._get_modified_since_filter()
        if cursor_write_date and configured_since:
            last_write_date = max(cursor_write_date, configured_since)
        else:
            last_write_date = cursor_write_date or configured_since

        batch_records: List[Tuple[Record, List[Permission]]] = []
        offset = 0
        total = 0

        while True:
            partners = await self.data_source.list_partners(
                updated_since=last_write_date,
                limit=self.batch_size,
                offset=offset,
            )
            if not partners:
                break

            for partner in partners:
                record, permissions, is_new = await self._process_contact(partner)

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
                total += 1

            offset += len(partners)
            if len(partners) < self.batch_size:
                break

        if batch_records:
            await self.data_entities_processor.on_new_records(batch_records)

        await self.contact_sync_point.update_sync_point(
            sync_key, {"write_date": current_timestamp}
        )
        self.logger.info(f"Finished syncing {total} Odoo contacts.")

    async def _process_contact(
        self, partner: Partner
    ) -> Tuple[Record, List[Permission], bool]:
        """Map a res.partner row to a base Record(RecordType.OTHERS) inside
        the Contacts record group, with the connector creator as fallback owner
        (contacts have no per-record ownership concept in Odoo)."""
        external_id = f"res.partner/{partner.id}"

        async with self.data_store_provider.transaction() as tx_store:
            existing_record = await tx_store.get_record_by_external_id(
                connector_id=self.connector_id, external_id=external_id
            )
        is_new = existing_record is None

        created_at_ms = _parse_odoo_datetime(partner.create_date) or get_epoch_timestamp_in_ms()
        updated_at_ms = _parse_odoo_datetime(partner.write_date) or get_epoch_timestamp_in_ms()

        display_name = partner.name or f"Contact #{partner.id}"

        record = Record(
            id=existing_record.id if existing_record else str(uuid.uuid4()),
            record_name=display_name,
            external_record_id=external_id,
            connector_name=Connectors.ODOO,
            connector_id=self.connector_id,
            record_type=RecordType.OTHERS,
            origin=OriginTypes.CONNECTOR,
            org_id=self.data_entities_processor.org_id,
            version=0 if is_new else existing_record.version + 1,
            external_revision_id=partner.write_date,
            external_record_group_id=self._CONTACTS_EXTERNAL_GROUP_ID,
            record_group_type=RecordGroupType.PROJECT,
            weburl=f"{self.base_url}/web#id={partner.id}&model=res.partner&view_type=form",
            mime_type=MimeTypes.PLAIN_TEXT.value,
            created_at=created_at_ms,
            updated_at=updated_at_ms,
            source_created_at=created_at_ms,
            source_updated_at=updated_at_ms,
            inherit_permissions=False,
        )

        # Contacts have no owner in Odoo — fall back to connector creator.
        permissions: List[Permission] = []
        fallback = self._creator_owner_permission()
        if fallback:
            permissions.append(fallback)

        return record, permissions, is_new

    def _creator_owner_permission(self) -> Optional[Permission]:
        """Fallback OWNER grant so records with no resolvable Odoo owner/
        follower don't end up with zero permissions. Must key off
        creator_email (an actual email), not created_by (an internal user
        id) — permission resolution for EntityType.USER matches by email
        only (see data_source_entities_processor._handle_record_permissions)."""
        if not self.creator_email:
            return None
        return Permission(
            external_id=self.created_by,
            email=self.creator_email,
            type=PermissionType.OWNER,
            entity_type=EntityType.USER,
        )

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

        if not permissions:
            fallback = self._creator_owner_permission()
            if fallback:
                permissions.append(fallback)

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
        external_group_id = (
            self._team_external_group_id(team_id)
            if team_id is not None
            else self._UNASSIGNED_TEAM_EXTERNAL_GROUP_ID
        )

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
            is_won=self._stage_is_won.get(_m2o_id(lead.stage_id) or -1, False),
            is_closed=not lead.active,
            created_date=_str_or_none(lead.create_date),
            close_date=_str_or_none(lead.date_closed),
        )

        return record, permissions, is_new

    # -- Record access -------------------------------------------------------

    async def get_signed_url(self, record: Record) -> Optional[str]:
        """Odoo has no signed-download-URL concept — link straight into the
        backend form view. Dispatches on external_record_id prefix."""
        parts = record.external_record_id.split("/")
        model = parts[0] if len(parts) >= 2 else "crm.lead"
        rec_id = parts[-1]
        return f"{self.base_url}/web#id={rec_id}&model={model}&view_type=form"

    async def stream_record(self, record: Record) -> StreamingResponse:
        if not self.data_source:
            raise HTTPException(
                status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
                detail="Odoo connector not initialized",
            )

        # Dispatch on the model embedded in the external_record_id.
        if record.external_record_id.startswith("res.partner/"):
            return await self._stream_contact(record)
        return await self._stream_lead(record)

    async def _stream_lead(self, record: Record) -> StreamingResponse:
        assert self.data_source is not None  # guarded by stream_record()
        lead_id = int(record.external_record_id.split("/")[-1])
        lead = await self.data_source.get_lead(lead_id)
        if lead is None:
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail="Record not found or access denied",
            )

        # Activities/messages are enrichment, not core content — a transient
        # failure fetching one shouldn't block indexing the lead's core fields.
        activities_result, messages_result = await asyncio.gather(
            self.data_source.list_activities(res_model="crm.lead", res_id=lead_id),
            self.data_source.list_messages(res_model="crm.lead", res_id=lead_id),
            return_exceptions=True,
        )
        if isinstance(activities_result, BaseException):
            self.logger.error(f"Failed to fetch activities for lead {lead_id}: {activities_result}")
            activities = []
        else:
            activities = activities_result
        if isinstance(messages_result, BaseException):
            self.logger.error(f"Failed to fetch messages for lead {lead_id}: {messages_result}")
            messages = []
        else:
            messages = messages_result

        lines: List[str] = [
            f"Name: {lead.name}",
            f"Type: {lead.type}",
            f"Stage: {_m2o_name(lead.stage_id) or ''}",
            f"Priority: {lead.priority}",
            f"Expected Revenue: {lead.expected_revenue}",
            f"Probability: {lead.probability}%",
        ]

        # Contact / company info
        if _str_or_none(lead.partner_name):
            lines.append(f"Company: {lead.partner_name}")
        if _str_or_none(lead.contact_name):
            lines.append(f"Contact: {lead.contact_name}")
        if _str_or_none(lead.email_from):
            lines.append(f"Email: {lead.email_from}")
        if _str_or_none(lead.phone):
            lines.append(f"Phone: {lead.phone}")
        if _str_or_none(lead.function):
            lines.append(f"Job Position: {lead.function}")
        if _str_or_none(lead.website):
            lines.append(f"Website: {lead.website}")

        # Address
        addr_parts = [
            _str_or_none(lead.street),
            _str_or_none(lead.city),
            _m2o_name(lead.state_id),
            _m2o_name(lead.country_id),
        ]
        addr = ", ".join(p for p in addr_parts if p)
        if addr:
            lines.append(f"Address: {addr}")

        # UTM / marketing attribution
        if _m2o_name(lead.source_id):
            lines.append(f"Source: {_m2o_name(lead.source_id)}")
        if _m2o_name(lead.medium_id):
            lines.append(f"Medium: {_m2o_name(lead.medium_id)}")
        if _m2o_name(lead.campaign_id):
            lines.append(f"Campaign: {_m2o_name(lead.campaign_id)}")
        if _str_or_none(lead.referred):
            lines.append(f"Referred By: {lead.referred}")

        # Key dates
        if _str_or_none(lead.date_deadline):
            lines.append(f"Expected Close: {lead.date_deadline}")
        if _str_or_none(lead.date_closed):
            lines.append(f"Close Date: {lead.date_closed}")
        if _m2o_name(lead.lost_reason_id):
            lines.append(f"Lost Reason: {_m2o_name(lead.lost_reason_id)}")

        # Description / internal notes
        if _str_or_none(lead.description):
            lines.append(f"Description:\n{lead.description}")

        # Chatter messages (notes + emails logged on the lead)
        if messages:
            lines.append("\n--- Messages ---")
            for msg in messages:
                author = _m2o_name(msg.author_id) or "Unknown"
                date = _str_or_none(msg.date) or ""
                subject = _str_or_none(msg.subject) or ""
                body = _str_or_none(msg.body) or ""
                # Strip minimal HTML tags from body (Odoo sends HTML chatter)
                body = body.replace("<br>", "\n").replace("<br/>", "\n")
                body = re.sub(r"<[^>]+>", "", body).strip()
                if subject:
                    lines.append(f"[{date}] {author} — {subject}")
                if body:
                    lines.append(body)

        # Scheduled activities
        if activities:
            lines.append("\n--- Activities ---")
            for act in activities:
                atype = _m2o_name(act.activity_type_id) or "Activity"
                deadline = _str_or_none(act.date_deadline) or ""
                summary = _str_or_none(act.summary) or ""
                note = _str_or_none(act.note) or ""
                assigned = _m2o_name(act.user_id) or ""
                parts = [f"[{deadline}] {atype}"]
                if assigned:
                    parts.append(f"assigned to {assigned}")
                if summary:
                    parts.append(f"— {summary}")
                lines.append(" ".join(parts))
                if note:
                    lines.append(note)

        content = "\n".join(lines).encode("utf-8")

        async def _content_stream() -> AsyncGenerator[bytes, None]:
            yield content

        return create_stream_record_response(
            _content_stream(),
            filename=record.record_name,
            mime_type=MimeTypes.PLAIN_TEXT.value,
            fallback_filename=f"record_{record.id}",
        )

    async def _stream_contact(self, record: Record) -> StreamingResponse:
        """Stream a res.partner contact as plain text for indexing."""
        assert self.data_source is not None  # guarded by stream_record()
        partner_id = int(record.external_record_id.split("/")[-1])
        partner = await self.data_source.get_partner(partner_id)
        if partner is None:
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail="Contact not found or access denied",
            )

        lines: List[str] = [f"Name: {partner.name}"]

        if partner.is_company:
            lines.append("Type: Company")
        else:
            lines.append("Type: Individual Contact")

        if _str_or_none(partner.email):
            lines.append(f"Email: {partner.email}")
        if _str_or_none(partner.phone):
            lines.append(f"Phone: {partner.phone}")
        if _str_or_none(partner.mobile):
            lines.append(f"Mobile: {partner.mobile}")
        if _str_or_none(partner.function):
            lines.append(f"Job Position: {partner.function}")

        # Parent company (for individual contacts linked to a company)
        if _m2o_name(partner.parent_id):
            lines.append(f"Company: {_m2o_name(partner.parent_id)}")

        # Address
        addr_parts = [
            _str_or_none(partner.street),
            _str_or_none(partner.city),
            _m2o_name(partner.state_id),
            _m2o_name(partner.country_id),
        ]
        addr = ", ".join(p for p in addr_parts if p)
        if addr:
            lines.append(f"Address: {addr}")

        content = "\n".join(lines).encode("utf-8")

        async def _contact_stream() -> AsyncGenerator[bytes, None]:
            yield content

        return create_stream_record_response(
            _contact_stream(),
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

            # Refresh email maps — reindex can run without a preceding _sync_leads().
            await self._sync_users()
            # Also refresh stage map so is_won stays accurate on reindex.
            await self._sync_stages()

            updated_records: List[Tuple[Record, List[Permission]]] = []
            non_updated_records: List[Record] = []

            for record in records:
                try:
                    is_contact = record.external_record_id.startswith("res.partner/")
                    rec_id = int(record.external_record_id.split("/")[-1])

                    if is_contact:
                        partner = await self.data_source.get_partner(rec_id)
                        if partner is None:
                            continue
                        if partner.write_date != record.external_revision_id:
                            updated_record, permissions, _is_new = (
                                await self._process_contact(partner)
                            )
                            updated_records.append((updated_record, permissions))
                        else:
                            non_updated_records.append(record)
                    else:
                        lead = await self.data_source.get_lead(rec_id)
                        if lead is None:
                            continue
                        if lead.write_date != record.external_revision_id:
                            followers = await self.data_source.list_followers(
                                "crm.lead", [rec_id]
                            )
                            follower_partner_ids = [
                                pid
                                for f in followers
                                if (pid := _m2o_id(f.partner_id)) is not None
                            ]
                            updated_record, permissions, _is_new = (
                                await self._process_lead(lead, follower_partner_ids)
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
