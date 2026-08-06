"""Odoo CRM sync. Entity mapping mirrors the Salesforce connector:

    crm.lead (opportunity) -> DealRecord     res.partner (company)    -> RecordGroup
    crm.lead (lead)        -> Person + edge  res.partner (individual) -> Person
    crm.team               -> AppUserGroup   ir.attachment            -> FileRecord

Only opportunities are records; people never are. Opportunities group under
their customer company, falling back to "Unassigned". Permissions: OWNER =
user_id, READER = mail.followers; attachments inherit their lead's. Roles
aren't synced — res.groups gates models, not records.
"""

from __future__ import annotations

import asyncio
import base64
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from logging import Logger
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
)
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
    AppUserGroup,
    DealRecord,
    FileRecord,
    Person,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.odoo.odoo import OdooClient, OdooClientBuilder
from app.sources.external.odoo.odoo import (
    Attachment,
    CrmLead,
    OdooDataSource,
    Partner,
)
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


# Ceiling for attachment streaming: content arrives base64-encoded and is then
# decoded, so peak memory is roughly 2.3x this per concurrent request.
_MAX_ATTACHMENT_BYTES = 100 * 1024 * 1024


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

        self._sync_lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task] = set()

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
        self.attachment_sync_point = SyncPoint(
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
                # Base _load_creator_email() only resolves for PERSONAL scope.
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

            # Before leads: company groups must exist before a lead cites one.
            self.logger.info("Syncing partners (res.partner)...")
            await self._sync_partners(full_sync=full_sync)

            self.logger.info("Syncing stages...")
            await self._sync_stages()

            self.logger.info("Syncing leads/opportunities...")
            await self._sync_leads(full_sync=full_sync)

            self.logger.info("Syncing attachments (ir.attachment)...")
            await self._sync_attachments(full_sync=full_sync)

            self.logger.info(f"Odoo {label} sync completed.")
        except Exception as ex:
            self.logger.error(f"Error in Odoo connector run: {ex}", exc_info=True)
            raise

    # -- Users -----------------------------------------------------------

    async def _sync_users(self) -> None:
        """Full refresh every run — the salesperson list is small enough that a
        separate incremental cursor isn't worth it."""
        if not self.data_source:
            return

        users = await self.data_source.list_users(include_inactive=True)

        app_users: List[AppUser] = []
        self._user_email_by_id = {}
        self._user_email_by_partner_id = {}
        for user in users:
            email = user.email if isinstance(user.email, str) else user.login
            # login is usually an email but can be a bare username ("admin").
            # Permissions resolve by email, so a non-email grant matches nobody
            # and silently hides the record — skip and let the creator fallback run.
            if not email or "@" not in email:
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

    # -- Teams (user groups) -----------------------------------------------

    # Fallback container when no customer company resolves.
    _UNASSIGNED_DEAL_EXTERNAL_GROUP_ID = "res.partner/__unassigned__"
    # Single container for every synced attachment.
    _FILES_EXTERNAL_GROUP_ID = "ir.attachment/__files__"

    @staticmethod
    def _team_source_user_group_id(team_id: int) -> str:
        return f"crm.team/{team_id}"

    @staticmethod
    def _company_external_group_id(partner_id: int) -> str:
        return f"res.partner/{partner_id}"

    async def _sync_teams(self) -> None:
        """A team is a set of salespeople, not a container of documents."""
        if not self.data_source:
            return

        teams = await self.data_source.list_teams()
        org_id = self.data_entities_processor.org_id

        user_groups: List[Tuple[AppUserGroup, List[AppUser]]] = []
        for team in teams:
            members = [
                AppUser(
                    app_name=Connectors.ODOO,
                    connector_id=self.connector_id,
                    source_user_id=str(member_id),
                    email=email,
                    full_name="",
                    org_id=org_id,
                )
                for member_id in team.member_ids
                if (email := self._user_email_by_id.get(member_id))
            ]
            user_groups.append(
                (
                    AppUserGroup(
                        app_name=Connectors.ODOO,
                        connector_id=self.connector_id,
                        source_user_group_id=self._team_source_user_group_id(team.id),
                        name=team.name or f"Team {team.id}",
                        org_id=org_id,
                    ),
                    members,
                )
            )

        if user_groups:
            await self.data_entities_processor.on_new_user_groups(user_groups)
        self.logger.info(f"Synced {len(user_groups)} Odoo sales teams as user groups.")

    # -- Leads -------------------------------------------------------------

    def _get_lead_type_filter(self) -> Optional[List[str]]:
        """Returns the selected lead types, or None if all types should sync.
        Both ["lead", "opportunity"] selected is the same as no filter."""
        values = self.sync_filters.get_value("lead_type")
        if not values:
            return None
        types = list(values)
        # Both picked == no filter, avoiding two separate Odoo calls.
        if set(types) >= {"lead", "opportunity"}:
            return None
        return types

    def _get_modified_since_filter(self) -> Optional[str]:
        """Configured 'modified' filter as an Odoo naive-UTC string (lower bound)."""
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
        total_deals = 0
        total_people = 0

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

                # crm.lead holds both: "lead" is an unconverted person,
                # "opportunity" is a real deal.
                opportunities = [lead for lead in leads if lead.type == "opportunity"]
                raw_leads = [lead for lead in leads if lead.type != "opportunity"]

                if raw_leads:
                    total_people += await self._upsert_lead_people(raw_leads)

                if opportunities:
                    followers_by_lead, company_group_by_lead = await asyncio.gather(
                        self._fetch_followers_by_lead([o.id for o in opportunities]),
                        self._fetch_company_group_by_lead(opportunities),
                    )

                    for lead in opportunities:
                        record, permissions, is_new = await self._process_lead(
                            lead,
                            followers_by_lead.get(lead.id, []),
                            company_group_by_lead.get(lead.id),
                        )
                        total_deals += 1

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
        self.logger.info(
            f"Finished syncing {total_deals} Odoo opportunities (deal records) "
            f"and {total_people} unconverted leads (Person nodes)."
        )

    async def _upsert_lead_people(self, leads: List[CrmLead]) -> int:
        """A prospect is a Person, not a document; the LEAD edge holds its
        qualification data (company, stage, source)."""
        people: List[Person] = []
        edges: List[Dict[str, Any]] = []
        org_id = self.data_entities_processor.org_id

        for lead in leads:
            person = self._build_lead_person(lead)
            if person is None:
                continue
            people.append(person)
            edges.append(
                {
                    "from_id": org_id,
                    "from_collection": CollectionNames.ORGS.value,
                    "to_id": person.id,
                    "to_collection": CollectionNames.PEOPLE.value,
                    "company": _str_or_none(lead.partner_name),
                    "title": _str_or_none(lead.function),
                    "status": _m2o_name(lead.stage_id),
                    "rating": lead.priority,
                    "leadSource": _m2o_name(lead.source_id),
                    "externalId": f"crm.lead/{lead.id}",
                    "startTime": _parse_odoo_datetime(lead.create_date),
                    "endTime": _parse_odoo_datetime(lead.date_closed),
                    "createdAtTimestamp": person.created_at,
                    "updatedAtTimestamp": person.updated_at,
                }
            )

        if not people:
            return 0

        async with self.data_store_provider.transaction() as tx_store:
            await tx_store.batch_upsert_people(people)
            await tx_store.batch_create_edges(edges, collection=CollectionNames.LEAD.value)
        return len(people)

    def _build_lead_person(self, lead: CrmLead) -> Optional[Person]:
        """Person nodes are keyed by email, so a lead without one can't be
        deduplicated — skipped, same rule the Salesforce connector applies."""
        email = _str_or_none(lead.email_from)
        if not email:
            return None
        email = email.lower()
        contact = _str_or_none(lead.contact_name) or ""
        first_name, _, last_name = contact.partition(" ")
        return Person(
            id=str(uuid.uuid5(uuid.NAMESPACE_DNS, email)),
            email=email,
            first_name=first_name or None,
            last_name=last_name or None,
            phone=_str_or_none(lead.phone),
            created_at=_parse_odoo_datetime(lead.create_date) or get_epoch_timestamp_in_ms(),
            updated_at=_parse_odoo_datetime(lead.write_date) or get_epoch_timestamp_in_ms(),
        )

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

    async def _fetch_company_group_by_lead(
        self, leads: List[CrmLead]
    ) -> Dict[int, str]:
        """Lead -> the record group of its customer company. partner_id may be an
        individual, so that partner's parent company wins. One read per page."""
        if not self.data_source:
            return {}

        partner_id_by_lead: Dict[int, int] = {}
        for lead in leads:
            partner_id = _m2o_id(lead.partner_id)
            if partner_id is not None:
                partner_id_by_lead[lead.id] = partner_id
        if not partner_id_by_lead:
            return {}

        partners = await self.data_source.read_partners(
            sorted(set(partner_id_by_lead.values()))
        )
        group_by_partner: Dict[int, str] = {}
        parent_by_partner: Dict[int, int] = {}
        for partner in partners:
            if partner.is_company:
                group_by_partner[partner.id] = self._company_external_group_id(partner.id)
                continue
            parent_id = _m2o_id(partner.parent_id)
            if parent_id is not None:
                parent_by_partner[partner.id] = parent_id

        # A contact's parent can itself be an individual, and _sync_partners only
        # creates groups for companies — so verify before pointing at one.
        if parent_by_partner:
            parents = await self.data_source.read_partners(
                sorted(set(parent_by_partner.values()))
            )
            company_parents = {p.id for p in parents if p.is_company}
            for partner_id, parent_id in parent_by_partner.items():
                if parent_id in company_parents:
                    group_by_partner[partner_id] = self._company_external_group_id(parent_id)

        return {
            lead_id: group_by_partner[partner_id]
            for lead_id, partner_id in partner_id_by_lead.items()
            if partner_id in group_by_partner
        }

    # -- Partners (res.partner) --------------------------------------------

    async def _sync_partners(self, full_sync: bool = False) -> None:
        """Companies become RecordGroups (the container leads file under),
        individuals become Person nodes. Own cursor, independent of leads."""
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

        # Must exist however few partners changed this run.
        await self.data_entities_processor.on_new_record_groups([
            (
                RecordGroup(
                    name="Unassigned",
                    org_id=self.data_entities_processor.org_id,
                    external_group_id=self._UNASSIGNED_DEAL_EXTERNAL_GROUP_ID,
                    connector_name=Connectors.ODOO,
                    connector_id=self.connector_id,
                    group_type=RecordGroupType.PROJECT,
                ),
                [],
            )
        ])

        offset = 0
        total_companies = 0
        total_people = 0

        while True:
            partners = await self.data_source.list_partners(
                updated_since=last_write_date,
                limit=self.batch_size,
                offset=offset,
            )
            if not partners:
                break

            company_groups: List[Tuple[RecordGroup, List[Permission]]] = []
            people: List[Person] = []
            for partner in partners:
                if partner.is_company:
                    company_groups.append((self._build_company_group(partner), []))
                    continue
                person = self._build_person(partner)
                if person is not None:
                    people.append(person)

            if company_groups:
                await self.data_entities_processor.on_new_record_groups(company_groups)
                total_companies += len(company_groups)
            if people:
                async with self.data_store_provider.transaction() as tx_store:
                    await tx_store.batch_upsert_people(people)
                total_people += len(people)

            offset += len(partners)
            if len(partners) < self.batch_size:
                break

        await self.contact_sync_point.update_sync_point(
            sync_key, {"write_date": current_timestamp}
        )
        self.logger.info(
            f"Finished syncing {total_companies} Odoo companies (record groups) "
            f"and {total_people} contacts (Person nodes)."
        )

    def _build_company_group(self, partner: Partner) -> RecordGroup:
        return RecordGroup(
            name=partner.name or f"Company #{partner.id}",
            org_id=self.data_entities_processor.org_id,
            external_group_id=self._company_external_group_id(partner.id),
            connector_name=Connectors.ODOO,
            connector_id=self.connector_id,
            group_type=RecordGroupType.PROJECT,
            source_created_at=_parse_odoo_datetime(partner.create_date),
            source_updated_at=_parse_odoo_datetime(partner.write_date),
        )

    def _build_person(self, partner: Partner) -> Optional[Person]:
        """Person nodes are keyed by email, so a contact without one can't be
        deduplicated — skipped, same rule the Salesforce connector applies."""
        email = _str_or_none(partner.email)
        if not email:
            return None
        email = email.lower()
        first_name, _, last_name = (partner.name or "").partition(" ")
        return Person(
            # Deterministic id: one Person per email, across connectors.
            id=str(uuid.uuid5(uuid.NAMESPACE_DNS, email)),
            email=email,
            first_name=first_name or None,
            last_name=last_name or None,
            phone=_str_or_none(partner.phone),
            created_at=_parse_odoo_datetime(partner.create_date) or get_epoch_timestamp_in_ms(),
            updated_at=_parse_odoo_datetime(partner.write_date) or get_epoch_timestamp_in_ms(),
        )

    # -- Attachments (ir.attachment) ---------------------------------------

    async def _sync_attachments(self, full_sync: bool = False) -> None:
        """Lead attachments as FileRecords in one shared "Odoo Files" group, each
        pointing back at its lead and inheriting that lead's permissions."""
        if not self.data_source:
            return

        current_timestamp = _odoo_now()
        sync_key = generate_record_sync_point_key("odoo", "attachments", "global")
        sync_point = await self.attachment_sync_point.read_sync_point(sync_key)
        cursor_write_date = None if full_sync else sync_point.get("write_date")

        configured_since = self._get_modified_since_filter()
        if cursor_write_date and configured_since:
            last_write_date = max(cursor_write_date, configured_since)
        else:
            last_write_date = cursor_write_date or configured_since

        await self.data_entities_processor.on_new_record_groups([
            (
                RecordGroup(
                    name="Odoo Files",
                    org_id=self.data_entities_processor.org_id,
                    external_group_id=self._FILES_EXTERNAL_GROUP_ID,
                    connector_name=Connectors.ODOO,
                    connector_id=self.connector_id,
                    group_type=RecordGroupType.PROJECT,
                    is_internal=True,
                ),
                [],
            )
        ])

        batch_records: List[Tuple[Record, List[Permission]]] = []
        offset = 0
        total = 0

        while True:
            attachments = await self.data_source.list_attachments(
                res_model="crm.lead",
                updated_since=last_write_date,
                limit=self.batch_size,
                offset=offset,
            )
            if not attachments:
                break

            permissions_by_lead = await self._fetch_lead_permissions(
                [a.res_id for a in attachments if a.res_id is not None]
            )

            for attachment in attachments:
                # An unconverted lead is no record, so a file on it has no parent.
                if attachment.res_id not in permissions_by_lead:
                    continue

                record, is_new = await self._process_attachment(attachment)
                permissions = list(permissions_by_lead[attachment.res_id])
                if not permissions:
                    fallback = self._creator_owner_permission()
                    if fallback:
                        permissions.append(fallback)

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

            offset += len(attachments)
            if len(attachments) < self.batch_size:
                break

        if batch_records:
            await self.data_entities_processor.on_new_records(batch_records)

        await self.attachment_sync_point.update_sync_point(
            sync_key, {"write_date": current_timestamp}
        )
        self.logger.info(f"Finished syncing {total} Odoo attachments.")

    async def _fetch_lead_permissions(
        self, lead_ids: List[int]
    ) -> Dict[int, List[Permission]]:
        """Parent-lead permissions, so an attachment is visible to exactly whoever
        can see its lead. Opportunities only — membership in the returned map
        is how the caller knows a parent is a real record."""
        if not self.data_source or not lead_ids:
            return {}
        unique_ids = sorted(set(lead_ids))
        leads, followers_by_lead = await asyncio.gather(
            self.data_source.read_leads(unique_ids),
            self._fetch_followers_by_lead(unique_ids),
        )
        return {
            lead.id: self._build_lead_permissions(
                _m2o_id(lead.user_id), followers_by_lead.get(lead.id, [])
            )
            for lead in leads
            if lead.type == "opportunity"
        }

    async def _process_attachment(
        self, attachment: Attachment
    ) -> Tuple[FileRecord, bool]:
        external_id = f"ir.attachment/{attachment.id}"

        async with self.data_store_provider.transaction() as tx_store:
            existing_record = await tx_store.get_record_by_external_id(
                connector_id=self.connector_id, external_id=external_id
            )
        is_new = existing_record is None

        created_at_ms = _parse_odoo_datetime(attachment.create_date) or get_epoch_timestamp_in_ms()
        updated_at_ms = _parse_odoo_datetime(attachment.write_date) or get_epoch_timestamp_in_ms()
        name = attachment.name or f"Attachment #{attachment.id}"
        extension = name.rpartition(".")[2] if "." in name else None
        parent_id = (
            f"crm.lead/{attachment.res_id}" if attachment.res_id is not None else None
        )

        record = FileRecord(
            id=existing_record.id if existing_record else str(uuid.uuid4()),
            record_name=name,
            external_record_id=external_id,
            connector_name=Connectors.ODOO,
            connector_id=self.connector_id,
            record_type=RecordType.FILE,
            origin=OriginTypes.CONNECTOR,
            org_id=self.data_entities_processor.org_id,
            version=0 if is_new else existing_record.version + 1,
            external_revision_id=attachment.write_date,
            external_record_group_id=self._FILES_EXTERNAL_GROUP_ID,
            record_group_type=RecordGroupType.PROJECT,
            parent_external_record_id=parent_id,
            parent_record_type=RecordType.DEAL if parent_id else None,
            weburl=f"{self.base_url}/web/content/{attachment.id}?download=true",
            mime_type=_str_or_none(attachment.mimetype) or MimeTypes.UNKNOWN.value,
            is_file=True,
            extension=extension,
            size_in_bytes=attachment.file_size,
            md5_hash=_str_or_none(attachment.checksum),
            created_at=created_at_ms,
            updated_at=updated_at_ms,
            source_created_at=created_at_ms,
            source_updated_at=updated_at_ms,
            inherit_permissions=False,
        )
        return record, is_new

    def _creator_owner_permission(self) -> Optional[Permission]:
        """Fallback OWNER grant so a record never ends up with zero permissions.
        Must key off creator_email, not created_by — EntityType.USER
        resolution matches by email only."""
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
        self,
        lead: CrmLead,
        follower_partner_ids: Optional[List[int]] = None,
        company_group_id: Optional[str] = None,
    ) -> Tuple[DealRecord, List[Permission], bool]:
        external_id = f"crm.lead/{lead.id}"

        async with self.data_store_provider.transaction() as tx_store:
            existing_record = await tx_store.get_record_by_external_id(
                connector_id=self.connector_id, external_id=external_id
            )
        is_new = existing_record is None

        owner_id = _m2o_id(lead.user_id)
        permissions = self._build_lead_permissions(owner_id, follower_partner_ids or [])

        external_group_id = company_group_id or self._UNASSIGNED_DEAL_EXTERNAL_GROUP_ID

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
            # Must match what stream_record() streams: indexing gates on this
            # first, and UNKNOWN silently drops the record as unsupported.
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
        if model == "ir.attachment":
            return f"{self.base_url}/web/content/{rec_id}?download=true"
        return f"{self.base_url}/web#id={rec_id}&model={model}&view_type=form"

    async def stream_record(self, record: Record) -> StreamingResponse:
        if not self.data_source:
            raise HTTPException(
                status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
                detail="Odoo connector not initialized",
            )

        # Dispatch on the model embedded in the external_record_id.
        if record.external_record_id.startswith("ir.attachment/"):
            return await self._stream_attachment(record)
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

        # Enrichment, not core content — a transient failure must not block it.
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

    async def _stream_attachment(self, record: Record) -> StreamingResponse:
        """Stream an ir.attachment's binary content for indexing. Odoo returns
        it base64-encoded over XML-RPC, so it is decoded before streaming."""
        assert self.data_source is not None  # guarded by stream_record()
        attachment_id = int(record.external_record_id.split("/")[-1])

        # Odoo caps nothing; the base64 payload plus its decoded copy peak at
        # ~2.3x the file size, so refuse before fetching rather than OOM.
        size = record.size_in_bytes or 0
        if size > _MAX_ATTACHMENT_BYTES:
            raise HTTPException(
                status_code=HttpStatusCode.PAYLOAD_TOO_LARGE.value,
                detail=f"Attachment exceeds {_MAX_ATTACHMENT_BYTES} bytes",
            )

        encoded = await self.data_source.get_attachment_content(attachment_id)
        if encoded is None:
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail="Attachment not found or has no content",
            )

        content = base64.b64decode(encoded)

        async def _attachment_stream() -> AsyncGenerator[bytes, None]:
            yield content

        return create_stream_record_response(
            _attachment_stream(),
            filename=record.record_name,
            mime_type=record.mime_type or MimeTypes.UNKNOWN.value,
            fallback_filename=f"record_{record.id}",
        )

    def handle_webhook_notification(self, notification: Dict) -> None:
        """Placeholder — Odoo has no native webhooks without a Studio automation."""
        self.logger.info("Odoo webhook received.")
        task = asyncio.create_task(self._sync_from_webhook())
        # The loop only holds a weak reference; without this the task can be
        # collected mid-flight and its exception lost.
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _sync_from_webhook(self) -> None:
        # Serialized: concurrent runs share the email/stage maps and the sync
        # cursor, so a slower run would overwrite a faster one's position and
        # the window between them would never be re-scanned.
        async with self._sync_lock:
            try:
                await self.run_incremental_sync()
            except Exception:
                self.logger.error("Webhook-triggered Odoo sync failed", exc_info=True)

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
                    is_attachment = record.external_record_id.startswith("ir.attachment/")
                    rec_id = int(record.external_record_id.split("/")[-1])

                    if is_attachment:
                        attachment = await self.data_source.get_attachment(rec_id)
                        if attachment is None:
                            continue
                        if attachment.write_date != record.external_revision_id:
                            updated_record, _is_new = await self._process_attachment(
                                attachment
                            )
                            permissions_by_lead = await self._fetch_lead_permissions(
                                [attachment.res_id]
                                if attachment.res_id is not None
                                else []
                            )
                            permissions = list(
                                permissions_by_lead.get(attachment.res_id) or []
                            )
                            if not permissions:
                                fallback = self._creator_owner_permission()
                                if fallback:
                                    permissions.append(fallback)
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
                            # Or the lead gets re-filed under "Unassigned".
                            company_group_by_lead = (
                                await self._fetch_company_group_by_lead([lead])
                            )
                            updated_record, permissions, _is_new = (
                                await self._process_lead(
                                    lead,
                                    follower_partner_ids,
                                    company_group_by_lead.get(lead.id),
                                )
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
