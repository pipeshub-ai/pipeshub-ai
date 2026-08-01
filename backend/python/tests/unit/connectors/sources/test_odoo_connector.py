"""Tests for app.connectors.sources.odoo.connector (CRM scope only)."""

import logging
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.odoo.connector import (
    OdooConnector,
    _m2o_id,
    _m2o_name,
    _odoo_now,
    _parse_odoo_datetime,
    _str_or_none,
)
from app.models.entities import DealRecord, FileRecord, Person, RecordType
from app.models.permission import EntityType, PermissionType
from app.sources.external.odoo.odoo import (
    Attachment,
    CrmLead,
    MailFollower,
    Partner,
    ResUser,
)


# ===========================================================================
# Helpers
# ===========================================================================


def _make_mock_deps():
    logger = logging.getLogger("test.odoo")

    dep = MagicMock()
    dep.org_id = "org-odoo-1"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_new_user_groups = AsyncMock()
    dep.on_record_content_update = AsyncMock()
    dep.on_updated_record_permissions = AsyncMock()
    dep.reindex_existing_records = AsyncMock()
    dep.get_user_by_user_id = AsyncMock(return_value=None)

    dsp = MagicMock()
    cs = MagicMock()
    cs.get_config = AsyncMock()

    return logger, dep, dsp, cs


def _make_connector(created_by: str = "creator-user-id") -> OdooConnector:
    logger, dep, dsp, cs = _make_mock_deps()
    connector = OdooConnector(
        logger=logger,
        data_entities_processor=dep,
        data_store_provider=dsp,
        config_service=cs,
        connector_id="conn-odoo-1",
        scope="TEAM",
        created_by=created_by,
    )
    connector.data_source = MagicMock()
    connector.base_url = "https://mycompany.odoo.com"
    return connector


def _mock_transaction(connector: OdooConnector, existing_record: Optional[Any] = None) -> MagicMock:
    """Wire data_store_provider.transaction() to a fake async context manager
    returning a tx_store whose get_record_by_external_id resolves to
    existing_record (None for "new record")."""
    tx_store = MagicMock()
    tx_store.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx_store.batch_upsert_people = AsyncMock()
    tx_store.batch_create_edges = AsyncMock()

    tx_ctx = MagicMock()
    tx_ctx.__aenter__ = AsyncMock(return_value=tx_store)
    tx_ctx.__aexit__ = AsyncMock(return_value=None)
    connector.data_store_provider.transaction = MagicMock(return_value=tx_ctx)
    return tx_store


def _lead(**overrides: Any) -> CrmLead:
    """An *opportunity* by default — that's the only crm.lead kind that
    becomes a record. Pass type="lead" for the unconverted-prospect path."""
    defaults: dict[str, Any] = {
        "id": 1,
        "name": "Test Lead",
        "type": "opportunity",
        "user_id": [7, "Alice"],
        "team_id": [3, "Sales"],
        "stage_id": [1, "New"],
        "create_date": "2024-01-01 10:00:00",
        "write_date": "2024-01-02 10:00:00",
    }
    defaults.update(overrides)
    return CrmLead.model_validate(defaults)


def _partner(**overrides: Any) -> Partner:
    defaults: dict[str, Any] = {
        "id": 42,
        "name": "Acme Contact",
        "create_date": "2024-01-01 09:00:00",
        "write_date": "2024-01-02 09:00:00",
    }
    defaults.update(overrides)
    return Partner.model_validate(defaults)


def _attachment(**overrides: Any) -> Attachment:
    defaults: dict[str, Any] = {
        "id": 9,
        "name": "proposal.pdf",
        "mimetype": "application/pdf",
        "file_size": 1024,
        "res_id": 1,
        "res_model": "crm.lead",
        "create_date": "2024-01-01 08:00:00",
        "write_date": "2024-01-02 08:00:00",
        "checksum": "abc123",
    }
    defaults.update(overrides)
    return Attachment.model_validate(defaults)


# ===========================================================================
# Module-level helper functions
# ===========================================================================


class TestHelperFunctions:
    def test_m2o_id_from_pair(self):
        assert _m2o_id([5, "Team"]) == 5

    def test_m2o_id_from_false(self):
        assert _m2o_id(False) is None

    def test_m2o_id_from_none(self):
        assert _m2o_id(None) is None

    def test_m2o_name_from_pair(self):
        assert _m2o_name([5, "Team"]) == "Team"

    def test_m2o_name_from_false(self):
        assert _m2o_name(False) is None

    def test_str_or_none_with_string(self):
        assert _str_or_none("hello") == "hello"

    def test_str_or_none_with_false(self):
        """Odoo returns False (not None) for empty char/date fields over XML-RPC."""
        assert _str_or_none(False) is None

    def test_parse_odoo_datetime_valid(self):
        ms = _parse_odoo_datetime("2024-01-15 10:30:00")
        assert isinstance(ms, int) and ms > 0

    def test_parse_odoo_datetime_invalid(self):
        assert _parse_odoo_datetime("not-a-date") is None

    def test_parse_odoo_datetime_non_string(self):
        assert _parse_odoo_datetime(False) is None

    def test_odoo_now_format(self):
        value = _odoo_now()
        # "YYYY-MM-DD HH:MM:SS" — must parse back with the same format used
        # for write_date comparisons in the sync cursor.
        assert len(value) == 19
        assert value[4] == "-" and value[10] == " "


# ===========================================================================
# Filters
# ===========================================================================


class TestGetLeadTypeFilter:
    def test_no_filter_returns_none(self):
        c = _make_connector()
        c.sync_filters = FilterCollection(filters=[])
        assert c._get_lead_type_filter() is None

    def test_single_type_selected(self):
        c = _make_connector()
        c.sync_filters = MagicMock(get_value=MagicMock(return_value=["lead"]))
        assert c._get_lead_type_filter() == ["lead"]

    def test_both_types_selected_treated_as_no_filter(self):
        """Avoids two separate Odoo API calls when the filter is a no-op."""
        c = _make_connector()
        c.sync_filters = MagicMock(get_value=MagicMock(return_value=["lead", "opportunity"]))
        assert c._get_lead_type_filter() is None


class TestGetModifiedSinceFilter:
    def test_no_filter_configured(self):
        c = _make_connector()
        c.sync_filters = MagicMock(get=MagicMock(return_value=None))
        assert c._get_modified_since_filter() is None

    def test_empty_filter(self):
        c = _make_connector()
        f = MagicMock()
        f.is_empty.return_value = True
        c.sync_filters = MagicMock(get=MagicMock(return_value=f))
        assert c._get_modified_since_filter() is None

    def test_converts_iso_to_odoo_format(self):
        c = _make_connector()
        f = MagicMock()
        f.is_empty.return_value = False
        f.get_datetime_iso.return_value = ("2024-01-15T10:30:00", None)
        c.sync_filters = MagicMock(get=MagicMock(return_value=f))
        assert c._get_modified_since_filter() == "2024-01-15 10:30:00"


# ===========================================================================
# Permissions
# ===========================================================================


class TestCreatorOwnerPermission:
    def test_no_creator_email_returns_none(self):
        c = _make_connector()
        c.creator_email = None
        assert c._creator_owner_permission() is None

    def test_with_creator_email(self):
        c = _make_connector(created_by="user-123")
        c.creator_email = "creator@example.com"
        perm = c._creator_owner_permission()
        assert perm is not None
        assert perm.email == "creator@example.com"
        assert perm.external_id == "user-123"
        assert perm.type == PermissionType.OWNER
        assert perm.entity_type == EntityType.USER


class TestBuildLeadPermissions:
    def test_owner_only(self):
        c = _make_connector()
        c._user_email_by_id = {7: "alice@example.com"}
        perms = c._build_lead_permissions(owner_id=7, follower_partner_ids=[])
        assert len(perms) == 1
        assert perms[0].email == "alice@example.com"
        assert perms[0].type == PermissionType.OWNER

    def test_owner_plus_followers(self):
        c = _make_connector()
        c._user_email_by_id = {7: "alice@example.com"}
        c._user_email_by_partner_id = {50: "bob@example.com"}
        perms = c._build_lead_permissions(owner_id=7, follower_partner_ids=[50])
        assert len(perms) == 2
        by_type = {p.type for p in perms}
        assert PermissionType.OWNER in by_type
        assert PermissionType.READ in by_type

    def test_follower_matching_owner_email_not_duplicated(self):
        c = _make_connector()
        c._user_email_by_id = {7: "alice@example.com"}
        c._user_email_by_partner_id = {50: "alice@example.com"}
        perms = c._build_lead_permissions(owner_id=7, follower_partner_ids=[50])
        assert len(perms) == 1

    def test_unresolvable_owner_and_no_followers_uses_fallback(self):
        """Owner id present but not a known internal user, no followers —
        must not end up with zero permissions."""
        c = _make_connector()
        c.creator_email = "creator@example.com"
        c._user_email_by_id = {}
        perms = c._build_lead_permissions(owner_id=999, follower_partner_ids=[])
        assert len(perms) == 1
        assert perms[0].email == "creator@example.com"
        assert perms[0].type == PermissionType.OWNER

    def test_no_owner_no_followers_no_creator_email_yields_empty(self):
        c = _make_connector()
        c.creator_email = None
        perms = c._build_lead_permissions(owner_id=None, follower_partner_ids=[])
        assert perms == []

    def test_unresolvable_follower_partner_id_skipped(self):
        c = _make_connector()
        c.creator_email = "creator@example.com"
        c._user_email_by_id = {7: "alice@example.com"}
        perms = c._build_lead_permissions(owner_id=7, follower_partner_ids=[999])
        assert len(perms) == 1
        assert perms[0].email == "alice@example.com"


# ===========================================================================
# _process_lead
# ===========================================================================


class TestProcessLead:
    @pytest.mark.asyncio
    async def test_new_lead_is_filed_under_its_company_group(self):
        """Leads group under the customer company, the way Salesforce groups
        an Opportunity under its Account — teams are user groups now."""
        c = _make_connector()
        _mock_transaction(c, existing_record=None)
        c._user_email_by_id = {7: "alice@example.com"}
        c._stage_is_won = {1: False}

        record, permissions, is_new = await c._process_lead(
            _lead(), company_group_id="res.partner/99"
        )

        assert is_new is True
        assert isinstance(record, DealRecord)
        assert record.record_type == RecordType.DEAL
        assert record.external_record_group_id == "res.partner/99"
        assert record.owner_id == "7"
        assert len(permissions) == 1

    @pytest.mark.asyncio
    async def test_lead_without_company_falls_back_to_unassigned_group(self):
        c = _make_connector()
        _mock_transaction(c, existing_record=None)
        c._stage_is_won = {}

        record, _permissions, _is_new = await c._process_lead(_lead(partner_id=False))

        assert record.external_record_group_id == c._UNASSIGNED_DEAL_EXTERNAL_GROUP_ID

    @pytest.mark.asyncio
    async def test_team_is_not_used_as_a_record_group(self):
        """Regression guard: a lead's team must never become its container."""
        c = _make_connector()
        _mock_transaction(c, existing_record=None)
        c._stage_is_won = {}

        record, _permissions, _is_new = await c._process_lead(_lead(team_id=[3, "Sales"]))

        assert "crm.team" not in (record.external_record_group_id or "")

    @pytest.mark.asyncio
    async def test_is_won_resolved_from_stage_map(self):
        c = _make_connector()
        _mock_transaction(c, existing_record=None)
        c._stage_is_won = {1: True}

        record, _permissions, _is_new = await c._process_lead(_lead(stage_id=[1, "Won"]))

        assert record.is_won is True

    @pytest.mark.asyncio
    async def test_unknown_stage_defaults_is_won_false(self):
        c = _make_connector()
        _mock_transaction(c, existing_record=None)
        c._stage_is_won = {}

        record, _permissions, _is_new = await c._process_lead(_lead(stage_id=False))

        assert record.is_won is False

    @pytest.mark.asyncio
    async def test_existing_lead_reuses_id_and_bumps_version(self):
        c = _make_connector()
        existing = MagicMock(id="existing-record-id", version=2)
        _mock_transaction(c, existing_record=existing)
        c._stage_is_won = {}

        record, _permissions, is_new = await c._process_lead(_lead())

        assert is_new is False
        assert record.id == "existing-record-id"
        assert record.version == 3

    @pytest.mark.asyncio
    async def test_mime_type_is_plain_text(self):
        """The indexing pipeline gates on mimeType before calling
        stream_record(); it must match what stream_record() actually
        streams, or every lead silently drops as unsupported."""
        c = _make_connector()
        _mock_transaction(c, existing_record=None)
        c._stage_is_won = {}

        record, _permissions, _is_new = await c._process_lead(_lead())

        assert record.mime_type == "text/plain"

    @pytest.mark.asyncio
    async def test_followers_become_reader_permissions(self):
        c = _make_connector()
        _mock_transaction(c, existing_record=None)
        c._user_email_by_id = {7: "alice@example.com"}
        c._user_email_by_partner_id = {50: "bob@example.com"}
        c._stage_is_won = {}

        _record, permissions, _is_new = await c._process_lead(
            _lead(), follower_partner_ids=[50]
        )

        emails = {p.email for p in permissions}
        assert emails == {"alice@example.com", "bob@example.com"}


# ===========================================================================
# crm.lead split: opportunity -> DealRecord, lead -> Person
# ===========================================================================


class TestSyncUsers:
    @pytest.mark.asyncio
    async def test_non_email_login_is_not_used_as_an_email(self):
        """Odoo's default admin login is a bare "admin". Permissions resolve by
        email, so accepting it would grant to nobody and hide the record."""
        c = _make_connector()
        c.data_source.list_users = AsyncMock(
            return_value=[
                ResUser(id=7, name="Admin", email=False, login="admin", active=True),
                ResUser(id=8, name="Alice", email=False, login="alice@acme.com", active=True),
            ]
        )

        await c._sync_users()

        assert c._user_email_by_id == {8: "alice@acme.com"}
        synced = c.data_entities_processor.on_new_app_users.call_args[0][0]
        assert [u.email for u in synced] == ["alice@acme.com"]


class TestBuildLeadPerson:
    def test_unconverted_lead_becomes_person(self):
        c = _make_connector()

        person = c._build_lead_person(
            _lead(type="lead", email_from="Prospect@Acme.com", contact_name="Jane Doe")
        )

        assert isinstance(person, Person)
        assert person.email == "prospect@acme.com"
        assert person.first_name == "Jane"
        assert person.last_name == "Doe"

    def test_lead_without_email_is_skipped(self):
        c = _make_connector()

        assert c._build_lead_person(_lead(type="lead", email_from=False)) is None

    def test_lead_person_shares_id_with_same_email_contact(self):
        """A prospect who is also a contact must collapse to one Person."""
        c = _make_connector()

        from_lead = c._build_lead_person(_lead(type="lead", email_from="x@acme.com"))
        from_contact = c._build_person(_partner(email="x@acme.com"))

        assert from_lead.id == from_contact.id


class TestSyncLeadsSplitsByType:
    def _prep(self, c, leads):
        c.record_sync_point = MagicMock()
        c.record_sync_point.read_sync_point = AsyncMock(return_value={})
        c.record_sync_point.update_sync_point = AsyncMock()
        c.sync_filters = FilterCollection(filters=[])
        c.data_source.list_leads = AsyncMock(side_effect=[leads, []])
        c.data_source.list_followers = AsyncMock(return_value=[])
        c.data_source.read_partners = AsyncMock(return_value=[])
        return _mock_transaction(c, existing_record=None)

    @pytest.mark.asyncio
    async def test_opportunity_becomes_record_lead_becomes_person(self):
        c = _make_connector()
        c._stage_is_won = {}
        tx_store = self._prep(
            c,
            [
                _lead(id=1, type="opportunity"),
                _lead(id=2, type="lead", email_from="prospect@acme.com"),
            ],
        )

        await c._sync_leads(full_sync=True)

        # Only the opportunity is persisted as a record.
        sent = c.data_entities_processor.on_new_records.call_args[0][0]
        assert len(sent) == 1
        assert sent[0][0].external_record_id == "crm.lead/1"

        # The unconverted lead is persisted as a Person, not a record.
        tx_store.batch_upsert_people.assert_awaited_once()
        people = tx_store.batch_upsert_people.call_args[0][0]
        assert [p.email for p in people] == ["prospect@acme.com"]

    @pytest.mark.asyncio
    async def test_unconverted_lead_gets_lead_edge(self):
        c = _make_connector()
        c._stage_is_won = {}
        tx_store = self._prep(
            c,
            [
                _lead(
                    id=2,
                    type="lead",
                    email_from="prospect@acme.com",
                    partner_name="Acme Corp",
                    stage_id=[1, "New"],
                )
            ],
        )

        await c._sync_leads(full_sync=True)

        tx_store.batch_create_edges.assert_awaited_once()
        edges = tx_store.batch_create_edges.call_args[0][0]
        assert edges[0]["company"] == "Acme Corp"
        assert edges[0]["status"] == "New"
        assert edges[0]["externalId"] == "crm.lead/2"

    @pytest.mark.asyncio
    async def test_leads_only_page_creates_no_records(self):
        c = _make_connector()
        c._stage_is_won = {}
        self._prep(c, [_lead(id=2, type="lead", email_from="p@acme.com")])

        await c._sync_leads(full_sync=True)

        c.data_entities_processor.on_new_records.assert_not_called()


# ===========================================================================
# Partners: companies -> RecordGroup, individuals -> Person
# ===========================================================================


class TestBuildPerson:
    def test_individual_becomes_person_keyed_by_email(self):
        c = _make_connector()

        person = c._build_person(_partner(name="Jane Doe", email="Jane@Acme.com"))

        assert isinstance(person, Person)
        assert person.email == "jane@acme.com"  # normalized
        assert person.first_name == "Jane"
        assert person.last_name == "Doe"

    def test_same_email_yields_same_deterministic_id(self):
        """One Person per email, so re-syncs upsert instead of duplicating."""
        c = _make_connector()

        first = c._build_person(_partner(id=1, email="dup@acme.com"))
        second = c._build_person(_partner(id=2, email="DUP@acme.com"))

        assert first.id == second.id

    def test_contact_without_email_is_skipped(self):
        c = _make_connector()

        assert c._build_person(_partner(email=False)) is None

    def test_single_word_name_has_no_last_name(self):
        c = _make_connector()

        person = c._build_person(_partner(name="Cher", email="cher@acme.com"))

        assert person.first_name == "Cher"
        assert person.last_name is None


class TestBuildCompanyGroup:
    def test_company_becomes_record_group(self):
        c = _make_connector()

        group = c._build_company_group(_partner(id=99, name="Acme Corp", is_company=True))

        assert group.external_group_id == "res.partner/99"
        assert group.name == "Acme Corp"


class TestSyncPartners:
    @pytest.mark.asyncio
    async def _run(self, c, partners):
        c.contact_sync_point = MagicMock()
        c.contact_sync_point.read_sync_point = AsyncMock(return_value={})
        c.contact_sync_point.update_sync_point = AsyncMock()
        c.sync_filters = FilterCollection(filters=[])
        c.data_source.list_partners = AsyncMock(side_effect=[partners, []])
        return _mock_transaction(c, existing_record=None)

    @pytest.mark.asyncio
    async def test_splits_companies_and_individuals(self):
        c = _make_connector()
        tx_store = await self._run(
            c,
            [
                _partner(id=99, name="Acme Corp", is_company=True),
                _partner(id=42, name="Jane Doe", email="jane@acme.com"),
            ],
        )

        await c._sync_partners(full_sync=True)

        # Company -> record group
        group_batches = c.data_entities_processor.on_new_record_groups.call_args_list
        created_group_ids = {
            g.external_group_id for call in group_batches for g, _ in call[0][0]
        }
        assert "res.partner/99" in created_group_ids

        # Individual -> Person node
        tx_store.batch_upsert_people.assert_awaited_once()
        people = tx_store.batch_upsert_people.call_args[0][0]
        assert [p.email for p in people] == ["jane@acme.com"]

    @pytest.mark.asyncio
    async def test_contacts_are_not_created_as_records(self):
        """Regression guard for the review: contacts must be Person nodes."""
        c = _make_connector()
        await self._run(c, [_partner(id=42, email="jane@acme.com")])

        await c._sync_partners(full_sync=True)

        c.data_entities_processor.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_unassigned_group_always_ensured(self):
        c = _make_connector()
        await self._run(c, [])

        await c._sync_partners(full_sync=True)

        batches = c.data_entities_processor.on_new_record_groups.call_args_list
        group_ids = {g.external_group_id for call in batches for g, _ in call[0][0]}
        assert c._UNASSIGNED_DEAL_EXTERNAL_GROUP_ID in group_ids


# ===========================================================================
# _sync_teams  (crm.team -> AppUserGroup)
# ===========================================================================


class TestSyncTeams:
    @pytest.mark.asyncio
    async def test_team_becomes_user_group_with_members(self):
        c = _make_connector()
        c._user_email_by_id = {7: "alice@example.com", 8: "bob@example.com"}
        team = MagicMock(id=3, member_ids=[7, 8])
        team.name = "Sales"
        c.data_source.list_teams = AsyncMock(return_value=[team])

        await c._sync_teams()

        batch = c.data_entities_processor.on_new_user_groups.call_args[0][0]
        group, members = batch[0]
        assert group.source_user_group_id == "crm.team/3"
        assert group.name == "Sales"
        assert {m.email for m in members} == {"alice@example.com", "bob@example.com"}

    @pytest.mark.asyncio
    async def test_teams_are_not_record_groups(self):
        """The review's core point: crm.team is a UserGroup, not a RecordGroup."""
        c = _make_connector()
        team = MagicMock(id=3, member_ids=[])
        team.name = "Sales"
        c.data_source.list_teams = AsyncMock(return_value=[team])

        await c._sync_teams()

        c.data_entities_processor.on_new_record_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_unresolvable_member_is_skipped(self):
        c = _make_connector()
        c._user_email_by_id = {7: "alice@example.com"}
        team = MagicMock(id=3, member_ids=[7, 999])
        team.name = "Sales"
        c.data_source.list_teams = AsyncMock(return_value=[team])

        await c._sync_teams()

        _group, members = c.data_entities_processor.on_new_user_groups.call_args[0][0][0]
        assert [m.email for m in members] == ["alice@example.com"]


# ===========================================================================
# Attachments (ir.attachment -> FileRecord)
# ===========================================================================


class TestProcessAttachment:
    @pytest.mark.asyncio
    async def test_attachment_becomes_file_record(self):
        c = _make_connector()
        _mock_transaction(c, existing_record=None)

        record, is_new = await c._process_attachment(_attachment())

        assert is_new is True
        assert isinstance(record, FileRecord)
        assert record.record_type == RecordType.FILE
        assert record.is_file is True
        assert record.extension == "pdf"
        assert record.mime_type == "application/pdf"
        assert record.size_in_bytes == 1024
        assert record.md5_hash == "abc123"

    @pytest.mark.asyncio
    async def test_links_back_to_parent_lead(self):
        c = _make_connector()
        _mock_transaction(c, existing_record=None)

        record, _is_new = await c._process_attachment(_attachment(res_id=5))

        assert record.parent_external_record_id == "crm.lead/5"
        assert record.parent_record_type == RecordType.DEAL

    @pytest.mark.asyncio
    async def test_name_without_extension(self):
        c = _make_connector()
        _mock_transaction(c, existing_record=None)

        record, _is_new = await c._process_attachment(_attachment(name="README"))

        assert record.extension is None

    @pytest.mark.asyncio
    async def test_existing_attachment_reuses_id_and_bumps_version(self):
        c = _make_connector()
        existing = MagicMock(id="existing-file-id", version=4)
        _mock_transaction(c, existing_record=existing)

        record, is_new = await c._process_attachment(_attachment())

        assert is_new is False
        assert record.id == "existing-file-id"
        assert record.version == 5


class TestFetchLeadPermissions:
    @pytest.mark.asyncio
    async def test_attachment_inherits_parent_lead_permissions(self):
        c = _make_connector()
        c._user_email_by_id = {7: "alice@example.com"}
        c._user_email_by_partner_id = {50: "bob@example.com"}
        c.data_source.read_leads = AsyncMock(return_value=[_lead(id=1)])
        c.data_source.list_followers = AsyncMock(
            return_value=[MailFollower(id=1, res_id=1, partner_id=[50, "Bob"])]
        )

        result = await c._fetch_lead_permissions([1])

        assert {p.email for p in result[1]} == {"alice@example.com", "bob@example.com"}

    @pytest.mark.asyncio
    async def test_unconverted_lead_parent_is_excluded(self):
        """A file on an unconverted lead has no parent record to belong to."""
        c = _make_connector()
        c.data_source.read_leads = AsyncMock(return_value=[_lead(id=2, type="lead")])
        c.data_source.list_followers = AsyncMock(return_value=[])

        result = await c._fetch_lead_permissions([2])

        assert result == {}

    @pytest.mark.asyncio
    async def test_empty_ids_skips_calls(self):
        c = _make_connector()
        c.data_source.read_leads = AsyncMock()

        assert await c._fetch_lead_permissions([]) == {}
        c.data_source.read_leads.assert_not_called()


class TestSyncAttachments:
    @pytest.mark.asyncio
    async def test_file_on_unconverted_lead_is_not_synced(self):
        c = _make_connector()
        c.attachment_sync_point = MagicMock()
        c.attachment_sync_point.read_sync_point = AsyncMock(return_value={})
        c.attachment_sync_point.update_sync_point = AsyncMock()
        c.sync_filters = FilterCollection(filters=[])
        c.data_source.list_attachments = AsyncMock(
            side_effect=[[_attachment(id=9, res_id=2)], []]
        )
        # res_id=2 is an unconverted lead -> excluded from the permissions map
        c.data_source.read_leads = AsyncMock(return_value=[_lead(id=2, type="lead")])
        c.data_source.list_followers = AsyncMock(return_value=[])
        _mock_transaction(c, existing_record=None)

        await c._sync_attachments(full_sync=True)

        c.data_entities_processor.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_file_on_opportunity_is_synced(self):
        c = _make_connector()
        c.attachment_sync_point = MagicMock()
        c.attachment_sync_point.read_sync_point = AsyncMock(return_value={})
        c.attachment_sync_point.update_sync_point = AsyncMock()
        c.sync_filters = FilterCollection(filters=[])
        c.data_source.list_attachments = AsyncMock(
            side_effect=[[_attachment(id=9, res_id=1)], []]
        )
        c.data_source.read_leads = AsyncMock(return_value=[_lead(id=1)])
        c.data_source.list_followers = AsyncMock(return_value=[])
        _mock_transaction(c, existing_record=None)

        await c._sync_attachments(full_sync=True)

        sent = c.data_entities_processor.on_new_records.call_args[0][0]
        assert sent[0][0].external_record_id == "ir.attachment/9"


# ===========================================================================
# _fetch_company_group_by_lead
# ===========================================================================


class TestFetchCompanyGroupByLead:
    @pytest.mark.asyncio
    async def test_company_partner_maps_to_its_own_group(self):
        c = _make_connector()
        c.data_source.read_partners = AsyncMock(
            return_value=[_partner(id=99, is_company=True)]
        )

        result = await c._fetch_company_group_by_lead([_lead(id=1, partner_id=[99, "Acme"])])

        assert result == {1: "res.partner/99"}

    @pytest.mark.asyncio
    async def test_individual_partner_maps_to_parent_company(self):
        c = _make_connector()
        # Two reads: the lead's partner, then that partner's parent.
        c.data_source.read_partners = AsyncMock(
            side_effect=[
                [_partner(id=42, is_company=False, parent_id=[99, "Acme"])],
                [_partner(id=99, is_company=True)],
            ]
        )

        result = await c._fetch_company_group_by_lead([_lead(id=1, partner_id=[42, "Jane"])])

        assert result == {1: "res.partner/99"}

    @pytest.mark.asyncio
    async def test_individual_parent_is_not_used_as_a_company_group(self):
        """Odoo allows contact->contact parents, but only companies get groups."""
        c = _make_connector()
        c.data_source.read_partners = AsyncMock(
            side_effect=[
                [_partner(id=42, is_company=False, parent_id=[43, "Boss"])],
                [_partner(id=43, is_company=False)],
            ]
        )

        result = await c._fetch_company_group_by_lead([_lead(id=1, partner_id=[42, "Jane"])])

        assert result == {}

    @pytest.mark.asyncio
    async def test_individual_without_company_is_unmapped(self):
        c = _make_connector()
        c.data_source.read_partners = AsyncMock(
            return_value=[_partner(id=42, is_company=False, parent_id=False)]
        )

        result = await c._fetch_company_group_by_lead([_lead(id=1, partner_id=[42, "Jane"])])

        assert result == {}

    @pytest.mark.asyncio
    async def test_lead_without_partner_skips_lookup(self):
        c = _make_connector()
        c.data_source.read_partners = AsyncMock()

        result = await c._fetch_company_group_by_lead([_lead(id=1, partner_id=False)])

        assert result == {}
        c.data_source.read_partners.assert_not_called()


# ===========================================================================
# run_sync / run_incremental_sync full_sync bypass
# ===========================================================================


class TestRunSyncFullVsIncremental:
    @pytest.mark.asyncio
    async def test_run_sync_uses_full_sync_true(self):
        c = _make_connector()
        c._run_sync = AsyncMock()
        await c.run_sync()
        c._run_sync.assert_awaited_once_with(full_sync=True)

    @pytest.mark.asyncio
    async def test_run_incremental_sync_uses_full_sync_false(self):
        c = _make_connector()
        c._run_sync = AsyncMock()
        await c.run_incremental_sync()
        c._run_sync.assert_awaited_once_with(full_sync=False)


class TestSyncLeadsCursorBypass:
    @pytest.mark.asyncio
    async def test_full_sync_ignores_stored_cursor(self):
        c = _make_connector()
        c.record_sync_point = MagicMock()
        c.record_sync_point.read_sync_point = AsyncMock(
            return_value={"write_date": "2024-06-01 00:00:00"}
        )
        c.record_sync_point.update_sync_point = AsyncMock()
        c.sync_filters = FilterCollection(filters=[])
        c.data_source.list_leads = AsyncMock(return_value=[])
        c.data_source.list_followers = AsyncMock(return_value=[])

        await c._sync_leads(full_sync=True)

        _args, kwargs = c.data_source.list_leads.call_args
        assert kwargs["updated_since"] is None

    @pytest.mark.asyncio
    async def test_incremental_sync_uses_stored_cursor(self):
        c = _make_connector()
        c.record_sync_point = MagicMock()
        c.record_sync_point.read_sync_point = AsyncMock(
            return_value={"write_date": "2024-06-01 00:00:00"}
        )
        c.record_sync_point.update_sync_point = AsyncMock()
        c.sync_filters = FilterCollection(filters=[])
        c.data_source.list_leads = AsyncMock(return_value=[])
        c.data_source.list_followers = AsyncMock(return_value=[])

        await c._sync_leads(full_sync=False)

        _args, kwargs = c.data_source.list_leads.call_args
        assert kwargs["updated_since"] == "2024-06-01 00:00:00"

    @pytest.mark.asyncio
    async def test_configured_filter_floor_wins_over_older_cursor(self):
        c = _make_connector()
        c.record_sync_point = MagicMock()
        c.record_sync_point.read_sync_point = AsyncMock(
            return_value={"write_date": "2024-01-01 00:00:00"}
        )
        c.record_sync_point.update_sync_point = AsyncMock()
        c._get_modified_since_filter = MagicMock(return_value="2024-06-01 00:00:00")
        c.sync_filters = FilterCollection(filters=[])
        c.data_source.list_leads = AsyncMock(return_value=[])
        c.data_source.list_followers = AsyncMock(return_value=[])

        await c._sync_leads(full_sync=False)

        _args, kwargs = c.data_source.list_leads.call_args
        assert kwargs["updated_since"] == "2024-06-01 00:00:00"


# ===========================================================================
# get_signed_url
# ===========================================================================


class TestGetSignedUrl:
    @pytest.mark.asyncio
    async def test_lead_url(self):
        c = _make_connector()
        record = MagicMock(external_record_id="crm.lead/5")
        url = await c.get_signed_url(record)
        assert url == "https://mycompany.odoo.com/web#id=5&model=crm.lead&view_type=form"

    @pytest.mark.asyncio
    async def test_attachment_url_points_at_download_endpoint(self):
        c = _make_connector()
        record = MagicMock(external_record_id="ir.attachment/9")
        url = await c.get_signed_url(record)
        assert url == "https://mycompany.odoo.com/web/content/9?download=true"


# ===========================================================================
# stream_record dispatch
# ===========================================================================


class TestStreamRecordDispatch:
    @pytest.mark.asyncio
    async def test_dispatches_to_stream_lead(self):
        c = _make_connector()
        record = MagicMock(external_record_id="crm.lead/1")
        c._stream_lead = AsyncMock(return_value="lead-response")
        c._stream_attachment = AsyncMock(return_value="attachment-response")

        result = await c.stream_record(record)

        assert result == "lead-response"
        c._stream_lead.assert_awaited_once_with(record)
        c._stream_attachment.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatches_to_stream_attachment(self):
        c = _make_connector()
        record = MagicMock(external_record_id="ir.attachment/9")
        c._stream_lead = AsyncMock(return_value="lead-response")
        c._stream_attachment = AsyncMock(return_value="attachment-response")

        result = await c.stream_record(record)

        assert result == "attachment-response"
        c._stream_attachment.assert_awaited_once_with(record)
        c._stream_lead.assert_not_called()

    @pytest.mark.asyncio
    async def test_stream_attachment_decodes_base64_content(self):
        import base64

        c = _make_connector()
        c.data_source.get_attachment_content = AsyncMock(
            return_value=base64.b64encode(b"PDF-BYTES").decode()
        )
        record = MagicMock(
            external_record_id="ir.attachment/9",
            record_name="proposal.pdf",
            mime_type="application/pdf",
            size_in_bytes=1024,
            id="r9",
        )

        response = await c._stream_attachment(record)

        chunks = [chunk async for chunk in response.body_iterator]
        body = b"".join(
            ch if isinstance(ch, bytes) else ch.encode("utf-8") for ch in chunks
        )
        assert body == b"PDF-BYTES"

    @pytest.mark.asyncio
    async def test_oversized_attachment_is_refused_before_fetching(self):
        from fastapi import HTTPException

        c = _make_connector()
        c.data_source.get_attachment_content = AsyncMock()
        record = MagicMock(
            external_record_id="ir.attachment/9",
            record_name="huge.iso",
            mime_type="application/octet-stream",
            size_in_bytes=500 * 1024 * 1024,
            id="r9",
        )

        with pytest.raises(HTTPException) as exc:
            await c._stream_attachment(record)

        assert exc.value.status_code == 413
        c.data_source.get_attachment_content.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_when_not_initialized(self):
        from fastapi import HTTPException

        c = _make_connector()
        c.data_source = None
        record = MagicMock(external_record_id="crm.lead/1")

        with pytest.raises(HTTPException) as exc:
            await c.stream_record(record)

        assert exc.value.status_code == 503


class TestStreamLeadContent:
    @pytest.mark.asyncio
    async def test_stream_lead_includes_core_fields(self):
        c = _make_connector()
        c.data_source.get_lead = AsyncMock(return_value=_lead(name="Big Deal"))
        c.data_source.list_activities = AsyncMock(return_value=[])
        c.data_source.list_messages = AsyncMock(return_value=[])
        record = MagicMock(external_record_id="crm.lead/1", record_name="Big Deal", id="r1")

        response = await c._stream_lead(record)

        chunks = [chunk async for chunk in response.body_iterator]
        body = b"".join(
            c if isinstance(c, bytes) else c.encode("utf-8") for c in chunks
        ).decode("utf-8")
        assert "Name: Big Deal" in body

    @pytest.mark.asyncio
    async def test_stream_lead_not_found_raises_404(self):
        from fastapi import HTTPException

        c = _make_connector()
        c.data_source.get_lead = AsyncMock(return_value=None)
        record = MagicMock(external_record_id="crm.lead/999")

        with pytest.raises(HTTPException):
            await c._stream_lead(record)


# ===========================================================================
# reindex_records
# ===========================================================================


class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_changed_lead_gets_reprocessed(self):
        c = _make_connector()
        c._sync_users = AsyncMock()
        c._sync_stages = AsyncMock()
        c.data_source.get_lead = AsyncMock(return_value=_lead(write_date="2024-02-01 00:00:00"))
        c.data_source.list_followers = AsyncMock(return_value=[])
        _mock_transaction(c, existing_record=None)
        record = MagicMock(
            id="r1",
            external_record_id="crm.lead/1",
            external_revision_id="2024-01-01 00:00:00",
        )

        await c.reindex_records([record])

        c.data_entities_processor.on_new_records.assert_awaited_once()
        c.data_entities_processor.reindex_existing_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_unchanged_lead_goes_to_reindex_existing(self):
        c = _make_connector()
        c._sync_users = AsyncMock()
        c._sync_stages = AsyncMock()
        c.data_source.get_lead = AsyncMock(
            return_value=_lead(write_date="2024-01-01 00:00:00")
        )
        record = MagicMock(
            id="r1",
            external_record_id="crm.lead/1",
            external_revision_id="2024-01-01 00:00:00",
        )

        await c.reindex_records([record])

        c.data_entities_processor.reindex_existing_records.assert_awaited_once_with([record])
        c.data_entities_processor.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_changed_attachment_dispatches_to_process_attachment(self):
        c = _make_connector()
        c._sync_users = AsyncMock()
        c._sync_stages = AsyncMock()
        c.creator_email = "creator@example.com"
        c.data_source.get_attachment = AsyncMock(
            return_value=_attachment(write_date="2024-03-01 00:00:00")
        )
        c.data_source.read_leads = AsyncMock(return_value=[])
        c.data_source.list_followers = AsyncMock(return_value=[])
        _mock_transaction(c, existing_record=None)
        record = MagicMock(
            id="r9",
            external_record_id="ir.attachment/9",
            external_revision_id="2024-01-01 00:00:00",
        )

        await c.reindex_records([record])

        c.data_entities_processor.on_new_records.assert_awaited_once()
        sent_record, _perms = c.data_entities_processor.on_new_records.call_args[0][0][0]
        assert sent_record.record_type == RecordType.FILE

    @pytest.mark.asyncio
    async def test_reindexed_lead_keeps_its_company_group(self):
        """Without the company lookup, reindex would silently re-file every
        lead under "Unassigned"."""
        c = _make_connector()
        c._sync_users = AsyncMock()
        c._sync_stages = AsyncMock()
        c.data_source.get_lead = AsyncMock(
            return_value=_lead(write_date="2024-02-01 00:00:00", partner_id=[99, "Acme"])
        )
        c.data_source.list_followers = AsyncMock(return_value=[])
        c.data_source.read_partners = AsyncMock(
            return_value=[_partner(id=99, is_company=True)]
        )
        _mock_transaction(c, existing_record=None)
        record = MagicMock(
            id="r1",
            external_record_id="crm.lead/1",
            external_revision_id="2024-01-01 00:00:00",
        )

        await c.reindex_records([record])

        sent_record, _perms = c.data_entities_processor.on_new_records.call_args[0][0][0]
        assert sent_record.external_record_group_id == "res.partner/99"

    @pytest.mark.asyncio
    async def test_empty_records_is_noop(self):
        c = _make_connector()
        await c.reindex_records([])
        c.data_entities_processor.on_new_records.assert_not_called()
        c.data_entities_processor.reindex_existing_records.assert_not_called()


# ===========================================================================
# _fetch_followers_by_lead
# ===========================================================================


class TestFetchFollowersByLead:
    @pytest.mark.asyncio
    async def test_groups_followers_by_lead_id(self):
        c = _make_connector()
        c.data_source.list_followers = AsyncMock(
            return_value=[
                MailFollower(id=1, res_id=10, partner_id=[100, "A"]),
                MailFollower(id=2, res_id=10, partner_id=[101, "B"]),
                MailFollower(id=3, res_id=20, partner_id=[102, "C"]),
            ]
        )

        result = await c._fetch_followers_by_lead([10, 20])

        assert result[10] == [100, 101]
        assert result[20] == [102]

    @pytest.mark.asyncio
    async def test_empty_lead_ids_skips_call(self):
        c = _make_connector()
        c.data_source.list_followers = AsyncMock()

        result = await c._fetch_followers_by_lead([])

        assert result == {}
        c.data_source.list_followers.assert_not_called()


# ===========================================================================
# init() — creator_email resolution for TEAM scope
# ===========================================================================


class TestInitCreatorEmailResolution:
    @pytest.mark.asyncio
    async def test_resolves_creator_email_via_user_id_for_team_scope(self, monkeypatch):
        c = _make_connector(created_by="user-123")

        fake_client = MagicMock()
        fake_client.url = "https://mycompany.odoo.com"
        fake_client.connect = AsyncMock()

        fake_builder = MagicMock()
        fake_builder.get_client = MagicMock(return_value=fake_client)

        async def fake_build_from_services(*_args, **_kwargs):
            return fake_builder

        monkeypatch.setattr(
            "app.connectors.sources.odoo.connector.OdooClientBuilder.build_from_services",
            fake_build_from_services,
        )

        creator_user = MagicMock(email="creator@example.com")
        c.data_entities_processor.get_user_by_user_id = AsyncMock(return_value=creator_user)

        ok = await c.init()

        assert ok is True
        assert c.creator_email == "creator@example.com"
        c.data_entities_processor.get_user_by_user_id.assert_awaited_once_with("user-123")

    @pytest.mark.asyncio
    async def test_no_creator_user_found_leaves_creator_email_none(self, monkeypatch):
        c = _make_connector(created_by="user-123")

        fake_client = MagicMock()
        fake_client.url = "https://mycompany.odoo.com"
        fake_client.connect = AsyncMock()

        fake_builder = MagicMock()
        fake_builder.get_client = MagicMock(return_value=fake_client)

        async def fake_build_from_services(*_args, **_kwargs):
            return fake_builder

        monkeypatch.setattr(
            "app.connectors.sources.odoo.connector.OdooClientBuilder.build_from_services",
            fake_build_from_services,
        )
        c.data_entities_processor.get_user_by_user_id = AsyncMock(return_value=None)

        ok = await c.init()

        assert ok is True
        assert c.creator_email is None
