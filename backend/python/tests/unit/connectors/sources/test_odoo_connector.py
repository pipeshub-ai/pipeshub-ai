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
from app.models.entities import DealRecord, Record, RecordType
from app.models.permission import EntityType, PermissionType
from app.sources.external.odoo.odoo import CrmLead, MailFollower, Partner


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

    tx_ctx = MagicMock()
    tx_ctx.__aenter__ = AsyncMock(return_value=tx_store)
    tx_ctx.__aexit__ = AsyncMock(return_value=None)
    connector.data_store_provider.transaction = MagicMock(return_value=tx_ctx)
    return tx_store


def _lead(**overrides: Any) -> CrmLead:
    defaults: dict[str, Any] = {
        "id": 1,
        "name": "Test Lead",
        "type": "lead",
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
    async def test_new_lead_with_team_gets_team_group(self):
        c = _make_connector()
        _mock_transaction(c, existing_record=None)
        c._user_email_by_id = {7: "alice@example.com"}
        c._stage_is_won = {1: False}

        record, permissions, is_new = await c._process_lead(_lead())

        assert is_new is True
        assert isinstance(record, DealRecord)
        assert record.record_type == RecordType.DEAL
        assert record.external_record_group_id == "crm.team/3"
        assert record.owner_id == "7"
        assert len(permissions) == 1

    @pytest.mark.asyncio
    async def test_lead_without_team_falls_back_to_unassigned_group(self):
        c = _make_connector()
        _mock_transaction(c, existing_record=None)
        c._stage_is_won = {}

        record, _permissions, _is_new = await c._process_lead(_lead(team_id=False))

        assert record.external_record_group_id == c._UNASSIGNED_TEAM_EXTERNAL_GROUP_ID

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
# _process_contact
# ===========================================================================


class TestProcessContact:
    @pytest.mark.asyncio
    async def test_new_contact_goes_into_contacts_group(self):
        c = _make_connector()
        _mock_transaction(c, existing_record=None)
        c.creator_email = "creator@example.com"

        record, permissions, is_new = await c._process_contact(_partner())

        assert is_new is True
        assert isinstance(record, Record)
        assert record.record_type == RecordType.OTHERS
        assert record.external_record_group_id == c._CONTACTS_EXTERNAL_GROUP_ID
        assert len(permissions) == 1
        assert permissions[0].email == "creator@example.com"

    @pytest.mark.asyncio
    async def test_contact_with_no_resolvable_creator_email_has_no_permissions(self):
        c = _make_connector()
        _mock_transaction(c, existing_record=None)
        c.creator_email = None

        _record, permissions, _is_new = await c._process_contact(_partner())

        assert permissions == []

    @pytest.mark.asyncio
    async def test_existing_contact_reuses_id_and_bumps_version(self):
        c = _make_connector()
        existing = MagicMock(id="existing-contact-id", version=0)
        _mock_transaction(c, existing_record=existing)
        c.creator_email = "creator@example.com"

        record, _permissions, is_new = await c._process_contact(_partner())

        assert is_new is False
        assert record.id == "existing-contact-id"
        assert record.version == 1

    @pytest.mark.asyncio
    async def test_uses_create_date_not_write_date_for_created_at(self):
        c = _make_connector()
        _mock_transaction(c, existing_record=None)
        c.creator_email = None

        record, _permissions, _is_new = await c._process_contact(
            _partner(create_date="2020-01-01 00:00:00", write_date="2024-06-01 00:00:00")
        )

        assert record.created_at != record.updated_at
        assert record.created_at == _parse_odoo_datetime("2020-01-01 00:00:00")


# ===========================================================================
# _sync_teams
# ===========================================================================


class TestSyncTeams:
    @pytest.mark.asyncio
    async def test_always_creates_unassigned_and_contacts_groups(self):
        c = _make_connector()
        c.data_source.list_teams = AsyncMock(return_value=[])

        await c._sync_teams()

        calls = c.data_entities_processor.on_new_record_groups.call_args_list
        assert len(calls) == 2
        first_batch = calls[0][0][0]
        group_ids = {g.external_group_id for g, _ in first_batch}
        assert c._UNASSIGNED_TEAM_EXTERNAL_GROUP_ID in group_ids

        second_batch = calls[1][0][0]
        assert second_batch[0][0].external_group_id == c._CONTACTS_EXTERNAL_GROUP_ID

    @pytest.mark.asyncio
    async def test_real_teams_included(self):
        c = _make_connector()
        # MagicMock(name=...) sets the mock's own repr, not an attribute —
        # must be assigned after construction.
        team = MagicMock(id=3)
        team.name = "Sales"
        c.data_source.list_teams = AsyncMock(return_value=[team])

        await c._sync_teams()

        first_batch = c.data_entities_processor.on_new_record_groups.call_args_list[0][0][0]
        group_ids = {g.external_group_id for g, _ in first_batch}
        assert "crm.team/3" in group_ids
        assert c._UNASSIGNED_TEAM_EXTERNAL_GROUP_ID in group_ids


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
    async def test_contact_url(self):
        c = _make_connector()
        record = MagicMock(external_record_id="res.partner/42")
        url = await c.get_signed_url(record)
        assert url == "https://mycompany.odoo.com/web#id=42&model=res.partner&view_type=form"


# ===========================================================================
# stream_record dispatch
# ===========================================================================


class TestStreamRecordDispatch:
    @pytest.mark.asyncio
    async def test_dispatches_to_stream_lead(self):
        c = _make_connector()
        record = MagicMock(external_record_id="crm.lead/1")
        c._stream_lead = AsyncMock(return_value="lead-response")
        c._stream_contact = AsyncMock(return_value="contact-response")

        result = await c.stream_record(record)

        assert result == "lead-response"
        c._stream_lead.assert_awaited_once_with(record)
        c._stream_contact.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatches_to_stream_contact(self):
        c = _make_connector()
        record = MagicMock(external_record_id="res.partner/1")
        c._stream_lead = AsyncMock(return_value="lead-response")
        c._stream_contact = AsyncMock(return_value="contact-response")

        result = await c.stream_record(record)

        assert result == "contact-response"
        c._stream_contact.assert_awaited_once_with(record)
        c._stream_lead.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_when_not_initialized(self):
        c = _make_connector()
        c.data_source = None
        record = MagicMock(external_record_id="crm.lead/1")
        with pytest.raises(Exception):
            await c.stream_record(record)


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
    async def test_changed_contact_dispatches_to_process_contact(self):
        c = _make_connector()
        c._sync_users = AsyncMock()
        c._sync_stages = AsyncMock()
        c.creator_email = "creator@example.com"
        c.data_source.get_partner = AsyncMock(
            return_value=_partner(write_date="2024-03-01 00:00:00")
        )
        _mock_transaction(c, existing_record=None)
        record = MagicMock(
            id="r2",
            external_record_id="res.partner/42",
            external_revision_id="2024-01-01 00:00:00",
        )

        await c.reindex_records([record])

        c.data_entities_processor.on_new_records.assert_awaited_once()

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
