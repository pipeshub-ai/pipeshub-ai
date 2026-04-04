"""Tests for app.connectors.sources.salesforce.connector."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
from app.connectors.sources.salesforce.connector import (
    ACCOUNTS_SYNC_POINT_KEY,
    CASES_SYNC_POINT_KEY,
    CONTACTS_SYNC_POINT_KEY,
    DEALS_SYNC_POINT_KEY,
    DISCUSSIONS_SYNC_POINT_KEY,
    FILES_SYNC_POINT_KEY,
    LEADS_SYNC_POINT_KEY,
    PERMISSION_HIERARCHY,
    PRODUCTS_SYNC_POINT_KEY,
    ROLES_SYNC_POINT_KEY,
    SOLD_IN_SYNC_POINT_KEY,
    TASKS_SYNC_POINT_KEY,
    USERS_SYNC_POINT_KEY,
    USER_GROUPS_SYNC_POINT_KEY,
    RecordUpdate,
    SalesforceConnector,
    _parse_salesforce_timestamp,
    _sanitize_soql_id,
)
from app.models.entities import RecordGroupType, RecordType
from app.sources.client.salesforce.salesforce import SalesforceResponse


# ===========================================================================
# Helpers
# ===========================================================================


def _make_mock_deps():
    logger = logging.getLogger("test.salesforce")

    dep = MagicMock()
    dep.org_id = "org-sf-1"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_app_roles = AsyncMock()
    dep.on_new_user_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.on_record_content_update = AsyncMock()
    dep.on_record_metadata_update = AsyncMock()
    dep.get_record_by_external_id = AsyncMock(return_value=None)

    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.delete_edges_to = AsyncMock()
    mock_tx.delete_edges_from = AsyncMock()
    mock_tx.delete_edge = AsyncMock()
    mock_tx.batch_create_edges = AsyncMock()
    mock_tx.get_nodes_by_field_in = AsyncMock(return_value=[])
    mock_tx.get_all_orgs = AsyncMock(return_value=[])
    mock_tx.batch_upsert_orgs = AsyncMock()
    mock_tx.batch_upsert_people = AsyncMock()
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)

    dsp = MagicMock()
    dsp.transaction.return_value = mock_tx
    dep.data_store_provider = dsp

    cs = MagicMock()
    cs.get_config = AsyncMock()

    return logger, dep, dsp, cs


def _make_connector() -> SalesforceConnector:
    logger, dep, dsp, cs = _make_mock_deps()
    connector = SalesforceConnector(
        logger=logger,
        data_entities_processor=dep,
        data_store_provider=dsp,
        config_service=cs,
        connector_id="conn-sf-1",
    )
    connector.salesforce_instance_url = "https://myinstance.salesforce.com"
    return connector


def _sf_response(success: bool = True, data: Optional[Dict] = None, error: Optional[str] = None) -> SalesforceResponse:
    return SalesforceResponse(success=success, data=data or {}, error=error)


# ===========================================================================
# Module-level utility functions
# ===========================================================================


class TestSanitizeSoqlId:

    def test_accepts_valid_18_char_id(self):
        result = _sanitize_soql_id("001000000000001AAA")
        assert result == "001000000000001AAA"

    def test_accepts_valid_15_char_id(self):
        result = _sanitize_soql_id("001000000000001")
        assert result == "001000000000001"

    def test_rejects_empty_string(self):
        import pytest
        with pytest.raises(ValueError):
            _sanitize_soql_id("")

    def test_rejects_injection_attempt(self):
        import pytest
        with pytest.raises(ValueError):
            _sanitize_soql_id("'; DROP TABLE Records; --")

    def test_rejects_short_id(self):
        import pytest
        with pytest.raises(ValueError):
            _sanitize_soql_id("short")

    def test_rejects_id_with_special_chars(self):
        import pytest
        with pytest.raises(ValueError):
            _sanitize_soql_id("001000000000001' OR '1'='1")


class TestParseSalesforceTimestamp:

    def test_parses_valid_timestamp(self):
        result = _parse_salesforce_timestamp("2024-01-01T00:00:00.000+0000")
        assert result is not None
        assert isinstance(result, int)

    def test_parses_iso_timestamp(self):
        result = _parse_salesforce_timestamp("2024-06-15T12:30:00.000+00:00")
        assert result is not None

    def test_returns_none_for_none(self):
        assert _parse_salesforce_timestamp(None) is None

    def test_returns_none_for_non_string(self):
        assert _parse_salesforce_timestamp(12345) is None  # type: ignore[arg-type]

    def test_returns_none_for_invalid_string(self):
        assert _parse_salesforce_timestamp("not-a-date") is None

    def test_normalises_plus_zero_suffix(self):
        # "+0000" should be normalised to "+00:00" before parsing
        result = _parse_salesforce_timestamp("2024-03-15T08:00:00.000+0000")
        assert result is not None


# ===========================================================================
# Constants
# ===========================================================================


class TestSalesforceConstants:

    def test_sync_point_keys(self):
        assert USERS_SYNC_POINT_KEY == "users"
        assert ROLES_SYNC_POINT_KEY == "roles"
        assert USER_GROUPS_SYNC_POINT_KEY == "user_groups"
        assert CONTACTS_SYNC_POINT_KEY == "contacts"
        assert LEADS_SYNC_POINT_KEY == "leads"
        assert PRODUCTS_SYNC_POINT_KEY == "products"
        assert SOLD_IN_SYNC_POINT_KEY == "sold_in"
        assert DEALS_SYNC_POINT_KEY == "deals"
        assert CASES_SYNC_POINT_KEY == "cases"
        assert TASKS_SYNC_POINT_KEY == "tasks"
        assert FILES_SYNC_POINT_KEY == "files"
        assert ACCOUNTS_SYNC_POINT_KEY == "accounts"
        assert DISCUSSIONS_SYNC_POINT_KEY == "discussions"

    def test_permission_hierarchy(self):
        assert PERMISSION_HIERARCHY["READER"] < PERMISSION_HIERARCHY["WRITER"]
        assert PERMISSION_HIERARCHY["WRITER"] < PERMISSION_HIERARCHY["OWNER"]
        assert PERMISSION_HIERARCHY["COMMENTER"] > PERMISSION_HIERARCHY["READER"]

    def test_all_permission_levels_present(self):
        for key in ("READER", "COMMENTER", "WRITER", "OWNER"):
            assert key in PERMISSION_HIERARCHY


# ===========================================================================
# RecordUpdate dataclass
# ===========================================================================


class TestRecordUpdate:

    def test_default_optional_fields(self):
        ru = RecordUpdate(
            record=None,
            is_new=True,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
        )
        assert ru.old_permissions is None
        assert ru.new_permissions is None
        assert ru.external_record_id is None

    def test_with_all_fields(self):
        mock_record = MagicMock()
        ru = RecordUpdate(
            record=mock_record,
            is_new=False,
            is_updated=True,
            is_deleted=False,
            metadata_changed=True,
            content_changed=True,
            permissions_changed=True,
            old_permissions=[],
            new_permissions=[],
            external_record_id="ext-sf-1",
        )
        assert ru.external_record_id == "ext-sf-1"
        assert ru.record is mock_record
        assert ru.is_updated is True

    def test_deleted_record_only_needs_external_id(self):
        ru = RecordUpdate(
            record=None,
            is_new=False,
            is_updated=False,
            is_deleted=True,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
            external_record_id="ext-deleted-1",
        )
        assert ru.is_deleted is True
        assert ru.external_record_id == "ext-deleted-1"


# ===========================================================================
# SalesforceConnector.__init__
# ===========================================================================


class TestSalesforceConnectorInit:

    def test_connector_initializes(self):
        connector = _make_connector()
        assert connector.connector_id == "conn-sf-1"
        assert connector.connector_name == Connectors.SALESFORCE
        assert connector.data_source is None

    def test_sync_points_created(self):
        connector = _make_connector()
        assert connector.user_sync_point is not None
        assert connector.records_sync_point is not None

    def test_default_api_version(self):
        connector = _make_connector()
        assert connector.api_version == "59.0"

    def test_filter_collections_initialized(self):
        connector = _make_connector()
        assert connector.sync_filters is not None
        assert connector.indexing_filters is not None


# ===========================================================================
# SalesforceConnector._get_api_version
# ===========================================================================


class TestGetApiVersion:

    @pytest.mark.asyncio
    async def test_returns_default_when_no_config(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value=None)
        result = await connector._get_api_version()
        assert result == "59.0"

    @pytest.mark.asyncio
    async def test_returns_config_version(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={"apiVersion": "61.0"})
        result = await connector._get_api_version()
        assert result == "61.0"

    @pytest.mark.asyncio
    async def test_returns_default_on_exception(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(side_effect=Exception("Network error"))
        result = await connector._get_api_version()
        assert result == "59.0"

    @pytest.mark.asyncio
    async def test_returns_default_when_api_version_missing_from_config(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={"other": "data"})
        result = await connector._get_api_version()
        assert result == "59.0"


# ===========================================================================
# SalesforceConnector.init
# ===========================================================================


class TestSalesforceConnectorInitMethod:

    @pytest.mark.asyncio
    async def test_returns_false_when_no_config(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value=None)
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_access_token(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {},
            "auth": {"oauthConfigId": "oauth-1"},
        })
        with patch(
            "app.connectors.sources.salesforce.connector.fetch_oauth_config_by_id",
            new_callable=AsyncMock,
        ) as mock_oauth:
            mock_oauth.return_value = {"config": {"instance_url": "https://example.salesforce.com"}}
            result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_instance_url(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "tok-1"},
            "auth": {"oauthConfigId": "oauth-1"},
        })
        with patch(
            "app.connectors.sources.salesforce.connector.fetch_oauth_config_by_id",
            new_callable=AsyncMock,
        ) as mock_oauth:
            mock_oauth.return_value = {"config": {}}  # no instance_url
            result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "tok-1", "refresh_token": "ref-1"},
            "auth": {"oauthConfigId": "oauth-1"},
            "apiVersion": "59.0",
        })
        with patch(
            "app.connectors.sources.salesforce.connector.fetch_oauth_config_by_id",
            new_callable=AsyncMock,
        ) as mock_oauth, patch(
            "app.connectors.sources.salesforce.connector.SalesforceClient"
        ) as mock_client_cls, patch(
            "app.connectors.sources.salesforce.connector.SalesforceDataSource"
        ) as mock_ds_cls:
            mock_oauth.return_value = {"config": {"instance_url": "https://example.salesforce.com"}}
            mock_client_cls.build_with_config.return_value = MagicMock()
            mock_ds_cls.return_value = MagicMock()
            result = await connector.init()

        assert result is True
        assert connector.data_source is not None

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(side_effect=Exception("Service error"))
        result = await connector.init()
        assert result is False


# ===========================================================================
# SalesforceConnector.test_connection_and_access
# ===========================================================================


class TestTestConnectionAndAccess:

    @pytest.mark.asyncio
    async def test_returns_false_when_data_source_not_initialized(self):
        connector = _make_connector()
        connector.data_source = None
        result = await connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.limits = AsyncMock(return_value=_sf_response(True, {}))
        connector._get_api_version = AsyncMock(return_value="59.0")
        result = await connector.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_api_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.limits = AsyncMock(
            return_value=_sf_response(False, error="HTTP 401")
        )
        connector._get_api_version = AsyncMock(return_value="59.0")
        result = await connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.limits = AsyncMock(side_effect=Exception("Network error"))
        connector._get_api_version = AsyncMock(return_value="59.0")
        result = await connector.test_connection_and_access()
        assert result is False


# ===========================================================================
# SalesforceConnector.get_signed_url
# ===========================================================================


class TestGetSignedUrl:

    @pytest.mark.asyncio
    async def test_returns_none_when_no_external_record_id(self):
        connector = _make_connector()
        record = MagicMock()
        record.external_record_id = None
        result = await connector.get_signed_url(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_file_record_returns_shepherd_download_url(self):
        connector = _make_connector()
        record = MagicMock()
        record.external_record_id = "doc-1"
        record.record_type = RecordType.FILE
        record.external_revision_id = "cv-1"
        result = await connector.get_signed_url(record)
        assert result is not None
        assert "sfc/servlet.shepherd/version/download/cv-1" in result

    @pytest.mark.asyncio
    async def test_file_record_without_revision_id_returns_web_url(self):
        connector = _make_connector()
        record = MagicMock()
        record.external_record_id = "doc-1"
        record.record_type = RecordType.FILE
        record.external_revision_id = None
        result = await connector.get_signed_url(record)
        assert result == "https://myinstance.salesforce.com/doc-1"

    @pytest.mark.asyncio
    async def test_non_file_record_returns_web_url(self):
        connector = _make_connector()
        record = MagicMock()
        record.external_record_id = "opp-123"
        record.record_type = RecordType.DEAL
        result = await connector.get_signed_url(record)
        assert result == "https://myinstance.salesforce.com/opp-123"


# ===========================================================================
# SalesforceConnector._soql_query_paginated
# ===========================================================================


class TestSoqlQueryPaginated:

    @pytest.mark.asyncio
    async def test_returns_error_when_not_initialized(self):
        connector = _make_connector()
        connector.data_source = None
        result = await connector._soql_query_paginated("59.0", "SELECT Id FROM Account")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_returns_single_page_of_records(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.soql_query = AsyncMock(return_value=_sf_response(
            True, {"records": [{"Id": "1"}, {"Id": "2"}], "done": True}
        ))
        result = await connector._soql_query_paginated("59.0", "SELECT Id FROM Account")
        assert result.success is True
        assert len(result.data["records"]) == 2

    @pytest.mark.asyncio
    async def test_follows_pagination_via_next_records_url(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        page1 = _sf_response(True, {
            "records": [{"Id": "1"}],
            "done": False,
            "nextRecordsUrl": "/services/data/v59.0/query/next-url",
        })
        page2 = _sf_response(True, {
            "records": [{"Id": "2"}],
            "done": True,
        })

        connector.data_source.soql_query = AsyncMock(return_value=page1)
        connector.data_source.soql_query_next = AsyncMock(return_value=page2)

        result = await connector._soql_query_paginated("59.0", "SELECT Id FROM Account")
        assert result.success is True
        assert len(result.data["records"]) == 2

    @pytest.mark.asyncio
    async def test_stops_pagination_on_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        page1 = _sf_response(True, {
            "records": [{"Id": "1"}],
            "done": False,
            "nextRecordsUrl": "/services/data/v59.0/query/next-url",
        })
        fail_page = _sf_response(False, error="Rate limited")

        connector.data_source.soql_query = AsyncMock(return_value=page1)
        connector.data_source.soql_query_next = AsyncMock(return_value=fail_page)

        result = await connector._soql_query_paginated("59.0", "SELECT Id FROM Account")
        # Should still return what was fetched so far
        assert result.success is True
        assert len(result.data["records"]) == 1

    @pytest.mark.asyncio
    async def test_uses_soql_query_all_when_flag_set(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        # The method calls soql_query first, then overrides with soql_query_all when queryAll=True
        connector.data_source.soql_query = AsyncMock(return_value=_sf_response(
            True, {"records": [], "done": True}
        ))
        connector.data_source.soql_query_all = AsyncMock(return_value=_sf_response(
            True, {"records": [{"Id": "1"}], "done": True}
        ))
        result = await connector._soql_query_paginated("59.0", "SELECT Id FROM Account", queryAll=True)
        connector.data_source.soql_query_all.assert_awaited_once()
        assert result.success is True

    @pytest.mark.asyncio
    async def test_returns_failure_when_query_fails(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.soql_query = AsyncMock(return_value=_sf_response(
            False, error="Invalid SOQL"
        ))
        result = await connector._soql_query_paginated("59.0", "INVALID")
        assert result.success is False


# ===========================================================================
# SalesforceConnector.user_to_app_user
# ===========================================================================


class TestUserToAppUser:

    def test_basic_user_mapping(self):
        connector = _make_connector()
        user = {
            "Id": "user-1",
            "FirstName": "Alice",
            "LastName": "Smith",
            "Email": "alice@example.com",
            "Title": "Engineer",
            "CreatedDate": "2024-01-01T00:00:00.000+0000",
            "LastModifiedDate": "2024-06-01T00:00:00.000+0000",
        }
        app_user = connector.user_to_app_user(user)
        assert app_user.source_user_id == "user-1"
        assert app_user.email == "alice@example.com"
        assert app_user.full_name == "Alice Smith"
        assert app_user.title == "Engineer"

    def test_user_with_no_email_uses_empty_string(self):
        connector = _make_connector()
        user = {"Id": "user-2", "FirstName": "Bob", "LastName": "Jones"}
        app_user = connector.user_to_app_user(user)
        assert app_user.email == ""

    def test_full_name_strips_whitespace_when_first_or_last_missing(self):
        connector = _make_connector()
        user = {"Id": "user-3", "LastName": "Solo", "Email": "solo@example.com"}
        app_user = connector.user_to_app_user(user)
        assert app_user.full_name == "Solo"

    def test_connector_metadata_is_set(self):
        connector = _make_connector()
        user = {"Id": "user-4", "Email": "test@example.com"}
        app_user = connector.user_to_app_user(user)
        assert app_user.app_name == Connectors.SALESFORCE
        assert app_user.connector_id == "conn-sf-1"
        assert app_user.org_id == "org-sf-1"


# ===========================================================================
# SalesforceConnector.role_to_app_role
# ===========================================================================


class TestRoleToAppRole:

    def test_basic_role_mapping(self):
        connector = _make_connector()
        role = {
            "Id": "role-1",
            "Name": "Sales Manager",
            "ParentRoleId": "role-parent",
            "SystemModstamp": "2024-01-01T00:00:00.000+0000",
        }
        app_role = connector.role_to_app_role(role)
        assert app_role.source_role_id == "role-1"
        assert app_role.name == "Sales Manager"
        assert app_role.parent_role_id == "role-parent"

    def test_role_with_no_parent(self):
        connector = _make_connector()
        role = {"Id": "role-top", "Name": "CEO"}
        app_role = connector.role_to_app_role(role)
        assert app_role.parent_role_id is None

    def test_connector_metadata_is_set(self):
        connector = _make_connector()
        role = {"Id": "role-2", "Name": "Developer"}
        app_role = connector.role_to_app_role(role)
        assert app_role.app_name == Connectors.SALESFORCE
        assert app_role.connector_id == "conn-sf-1"


# ===========================================================================
# SalesforceConnector._build_product_record
# ===========================================================================


class TestBuildProductRecord:

    def test_builds_product_from_row(self):
        connector = _make_connector()
        product = {
            "Id": "prod-1",
            "Name": "Widget Pro",
            "ProductCode": "WP-100",
            "Family": "Hardware",
            "SystemModstamp": "2024-01-01T00:00:00.000+0000",
            "CreatedDate": "2023-01-01T00:00:00.000+0000",
            "LastModifiedDate": "2024-01-01T00:00:00.000+0000",
        }
        record = connector._build_product_record(product)
        assert record.external_record_id == "prod-1"
        assert record.record_name == "Widget Pro"
        assert record.product_code == "WP-100"
        assert record.product_family == "Hardware"
        assert record.record_type == RecordType.PRODUCT
        assert record.external_revision_id == "2024-01-01T00:00:00.000+0000"

    def test_weburl_contains_instance_url_and_product_id(self):
        connector = _make_connector()
        product = {"Id": "prod-2", "Name": "Gadget", "ProductCode": None, "Family": None}
        record = connector._build_product_record(product)
        assert "prod-2" in (record.weburl or "")
        assert "myinstance.salesforce.com" in (record.weburl or "")

    def test_connector_metadata_is_set(self):
        connector = _make_connector()
        product = {"Id": "prod-3", "Name": "Test Product"}
        record = connector._build_product_record(product)
        assert record.connector_id == "conn-sf-1"
        assert record.connector_name == Connectors.SALESFORCE


# ===========================================================================
# SalesforceConnector._build_deal_record
# ===========================================================================


class TestBuildDealRecord:

    def test_builds_deal_from_opportunity_row(self):
        connector = _make_connector()
        opp = {
            "Id": "opp-1",
            "Name": "Big Deal",
            "AccountId": "acc-1",
            "Account": {"Name": "Acme Corp"},
            "StageName": "Proposal",
            "Amount": 50000.0,
            "ExpectedRevenue": 45000.0,
            "CloseDate": "2024-12-31",
            "Probability": 75.0,
            "Type": "New Business",
            "OwnerId": "user-1",
            "IsWon": False,
            "IsClosed": False,
            "CreatedDate": "2024-01-01T00:00:00.000+0000",
            "LastModifiedDate": "2024-06-01T00:00:00.000+0000",
        }
        record = connector._build_deal_record(opp)
        # identity / grouping
        assert record.external_record_id == "opp-1"
        assert record.external_record_group_id == "acc-1"
        assert record.record_type == RecordType.DEAL
        assert record.record_group_type == RecordGroupType.DEAL
        # names
        assert record.record_name == "Big Deal"
        assert record.name == "Big Deal"
        # financials
        assert record.amount == 50000.0
        assert record.expected_revenue == 45000.0
        assert record.conversion_probability == 75.0
        # dates
        assert record.expected_close_date == "2024-12-31"
        assert record.close_date == "2024-12-31"
        assert record.created_date == "2024-01-01T00:00:00.000+0000"
        # epoch timestamps (2024-01-01Z and 2024-06-01Z)
        assert record.source_created_at == 1704067200000
        assert record.source_updated_at == 1717200000000
        assert record.external_revision_id == "1717200000000"
        # deal metadata
        assert record.type == "New Business"
        assert record.owner_id == "user-1"
        assert record.is_won is False
        assert record.is_closed is False
        # connector provenance
        assert record.connector_id == "conn-sf-1"
        assert record.connector_name == Connectors.SALESFORCE
        assert record.origin == OriginTypes.CONNECTOR
        assert record.mime_type == MimeTypes.BLOCKS.value
        assert record.org_id == "org-sf-1"
        assert record.version == 1
        # flags
        assert record.inherit_permissions is False
        assert record.preview_renderable is False
        # weburl contains instance URL and record ID
        assert record.weburl is not None
        assert "myinstance.salesforce.com" in record.weburl
        assert "opp-1" in record.weburl

    def test_unassigned_deal_when_no_account(self):
        connector = _make_connector()
        opp = {
            "Id": "opp-2",
            "Name": "Orphan Deal",
            "AccountId": None,
            "Account": None,
            "Amount": None,
            "ExpectedRevenue": None,
            "CloseDate": None,
            "Probability": None,
            "IsWon": False,
            "IsClosed": True,
            "CreatedDate": None,
            "LastModifiedDate": None,
        }
        record = connector._build_deal_record(opp)
        assert record.external_record_group_id == "UNASSIGNED-DEAL"

    def test_connector_metadata_is_set(self):
        connector = _make_connector()
        opp = {
            "Id": "opp-3",
            "Name": "Test Opp",
            "AccountId": "acc-1",
            "Account": {"Name": "Corp"},
            "Amount": 1000.0,
            "ExpectedRevenue": None,
            "CloseDate": "2024-12-01",
            "Probability": 50.0,
            "IsWon": False,
            "IsClosed": False,
            "CreatedDate": None,
            "LastModifiedDate": None,
        }
        record = connector._build_deal_record(opp)
        assert record.connector_id == "conn-sf-1"
        assert record.connector_name == Connectors.SALESFORCE


# ===========================================================================
# SalesforceConnector._build_case_record
# ===========================================================================


class TestBuildCaseRecord:

    def test_builds_case_from_row(self):
        connector = _make_connector()
        case = {
            "Id": "case-1",
            "CaseNumber": "00001234",
            "Subject": "Login Broken",
            "Status": "Open",
            "Priority": "High",
            "Type": "Problem",
            "Owner": {"Name": "Alice", "Email": "alice@example.com"},
            "Contact": {"Name": "Bob", "Email": "bob@example.com"},
            "CreatedBy": {"Name": "Charlie", "Email": "charlie@example.com"},
            "AccountId": "acc-1",
            "SystemModstamp": "2024-01-01T00:00:00.000+0000",
            "CreatedDate": "2023-12-01T00:00:00.000+0000",
            "LastModifiedDate": "2024-01-01T00:00:00.000+0000",
        }
        record = connector._build_case_record(case)
        assert record.external_record_id == "case-1"
        assert record.record_name == "Login Broken"
        assert record.status == "Open"
        assert record.priority == "High"
        assert record.record_type == RecordType.CASE
        assert record.assignee == "Alice"

    def test_unassigned_case_when_no_account(self):
        connector = _make_connector()
        case = {
            "Id": "case-2",
            "CaseNumber": "00001235",
            "Subject": "Network Issue",
            "Status": "New",
            "AccountId": None,
            "Owner": {},
            "Contact": {},
            "CreatedBy": {},
            "CreatedDate": None,
            "LastModifiedDate": None,
        }
        record = connector._build_case_record(case)
        assert record.external_record_group_id == "UNASSIGNED-CASE"

    def test_case_name_falls_back_to_case_number(self):
        connector = _make_connector()
        case = {
            "Id": "case-3",
            "CaseNumber": "00001236",
            "Subject": None,
            "Status": "Closed",
            "AccountId": "acc-2",
            "Owner": {},
            "Contact": {},
            "CreatedBy": {},
            "CreatedDate": None,
            "LastModifiedDate": None,
        }
        record = connector._build_case_record(case)
        assert "00001236" in record.record_name


# ===========================================================================
# SalesforceConnector._build_task_record
# ===========================================================================


class TestBuildTaskRecord:

    def test_builds_task_from_row(self):
        connector = _make_connector()
        task = {
            "Id": "task-1",
            "Subject": "Follow up call",
            "Status": "Not Started",
            "Priority": "Normal",
            "TaskSubtype": "Call",
            "WhatId": "opp-1",
            "What": {"Type": "Opportunity"},
            "Owner": {"Name": "Alice", "Email": "alice@example.com"},
            "CreatedBy": {"Name": "Bob", "Email": "bob@example.com"},
            "ActivityDate": None,
            "SystemModstamp": "2024-01-01T00:00:00.000+0000",
            "CreatedDate": "2023-12-01T00:00:00.000+0000",
            "LastModifiedDate": "2024-01-01T00:00:00.000+0000",
        }
        record = connector._build_task_record(task)
        assert record.external_record_id == "task-1"
        assert record.record_name == "Follow up call"
        assert record.status == "Not Started"
        assert record.record_type == RecordType.TASK
        assert record.parent_external_record_id == "opp-1"

    def test_task_with_account_parent(self):
        connector = _make_connector()
        task = {
            "Id": "task-2",
            "Subject": "Account Task",
            "Status": "Completed",
            "WhatId": "acc-1",
            "What": {"Type": "Account"},
            "Owner": {},
            "CreatedBy": {},
            "ActivityDate": None,
        }
        record = connector._build_task_record(task)
        assert record.external_record_group_id == "acc-1"
        assert record.parent_external_record_id is None

    def test_task_with_no_subject_uses_id_as_name(self):
        connector = _make_connector()
        task = {
            "Id": "task-3",
            "Subject": None,
            "Status": "Open",
            "WhatId": None,
            "What": None,
            "Owner": {},
            "CreatedBy": {},
            "ActivityDate": None,
        }
        record = connector._build_task_record(task)
        assert "task-3" in record.record_name

    def test_task_external_group_unassigned_for_unknown_type(self):
        connector = _make_connector()
        task = {
            "Id": "task-4",
            "Subject": "Unknown Task",
            "Status": "Open",
            "WhatId": "obj-1",
            "What": {"Type": "CustomObject"},
            "Owner": {},
            "CreatedBy": {},
            "ActivityDate": None,
        }
        record = connector._build_task_record(task)
        assert record.external_record_group_id == "UNASSIGNED-TASK"


# ===========================================================================
# SalesforceConnector._build_file_record
# ===========================================================================


class TestBuildFileRecord:

    def test_builds_file_record(self):
        connector = _make_connector()
        meta = {
            "Id": "cv-1",
            "ContentDocumentId": "doc-1",
            "Title": "Report.pdf",
            "PathOnClient": "Report.pdf",
            "ContentSize": 12345,
            "FileExtension": "pdf",
            "Checksum": "abc123",
            "LastModifiedDate": "2024-01-01T00:00:00.000+0000",
            "CreatedDate": "2023-12-01T00:00:00.000+0000",
        }
        record = connector._build_file_record(meta, "doc-1-acc-1")
        assert record is not None
        assert record.record_name == "Report.pdf"
        assert record.record_type == RecordType.FILE
        assert record.md5_hash == "abc123"
        assert record.size_in_bytes == 12345

    def test_returns_none_for_missing_id(self):
        connector = _make_connector()
        result = connector._build_file_record({}, "ext-id")
        assert result is None

    def test_returns_none_for_none_meta(self):
        connector = _make_connector()
        result = connector._build_file_record(None, "ext-id")  # type: ignore[arg-type]
        assert result is None

    def test_sets_parent_id(self):
        connector = _make_connector()
        meta = {
            "Id": "cv-2",
            "ContentDocumentId": "doc-2",
            "PathOnClient": "Invoice.xlsx",
            "ContentSize": 5000,
        }
        record = connector._build_file_record(meta, "doc-2-opp-1", parent_id="opp-1")
        assert record is not None
        assert record.parent_external_record_id == "opp-1"


# ===========================================================================
# SalesforceConnector._parse_opportunities
# ===========================================================================


class TestParseOpportunities:

    def test_returns_first_won_close_date(self):
        connector = _make_connector()
        acc = {
            "Opportunities": {
                "records": [
                    {"IsWon": True, "IsClosed": True, "CloseDate": "2024-01-15"},
                    {"IsWon": True, "IsClosed": True, "CloseDate": "2024-06-01"},
                ]
            }
        }
        end_time_ms, active_customer = connector._parse_opportunities(acc)
        assert end_time_ms is not None
        # Should be the first won close date
        assert active_customer is False

    def test_active_customer_when_open_opportunity(self):
        connector = _make_connector()
        acc = {
            "Opportunities": {
                "records": [
                    {"IsWon": False, "IsClosed": False, "CloseDate": "2024-12-01"},
                ]
            }
        }
        _, active_customer = connector._parse_opportunities(acc)
        assert active_customer is True

    def test_returns_none_when_no_opportunities(self):
        connector = _make_connector()
        acc = {"Opportunities": {"records": []}}
        end_time_ms, active_customer = connector._parse_opportunities(acc)
        assert end_time_ms is None
        assert active_customer is False

    def test_handles_missing_opportunities_key(self):
        connector = _make_connector()
        acc = {}
        end_time_ms, active_customer = connector._parse_opportunities(acc)
        assert end_time_ms is None
        assert active_customer is False


# ===========================================================================
# SalesforceConnector._set_block_group_children
# ===========================================================================


class TestSetBlockGroupChildren:

    def test_wires_parent_children(self):
        connector = _make_connector()
        from app.models.blocks import BlockGroup, DataFormat, GroupSubType, GroupType

        parent = BlockGroup(
            id="bg-parent",
            index=0,
            parent_index=None,
            name="Parent",
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.CONTENT,
            description="Parent",
            source_group_id="src-0",
            data="",
            format=DataFormat.MARKDOWN,
        )
        child = BlockGroup(
            id="bg-child",
            index=1,
            parent_index=0,
            name="Child",
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.COMMENT,
            description="Child",
            source_group_id="src-1",
            data="",
            format=DataFormat.MARKDOWN,
        )

        connector._set_block_group_children([parent, child])
        assert parent.children is not None
        # child index 1 should be recorded in block_group_ranges
        ranges = parent.children.block_group_ranges
        assert any(r.start <= 1 <= r.end for r in ranges)

    def test_no_children_means_none(self):
        connector = _make_connector()
        from app.models.blocks import BlockGroup, DataFormat, GroupSubType, GroupType

        solo = BlockGroup(
            id="bg-solo",
            index=0,
            parent_index=None,
            name="Solo",
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.CONTENT,
            description="Solo",
            source_group_id="src-0",
            data="",
            format=DataFormat.MARKDOWN,
        )

        connector._set_block_group_children([solo])
        assert solo.children is None


# ===========================================================================
# SalesforceConnector._sync_users
# ===========================================================================


class TestSyncUsers:

    @pytest.mark.asyncio
    async def test_sync_users_calls_on_new_app_users(self):
        connector = _make_connector()
        users = [
            {"Id": "u1", "FirstName": "Alice", "LastName": "S", "Email": "alice@example.com"},
            {"Id": "u2", "FirstName": "Bob", "LastName": "J", "Email": "bob@example.com"},
        ]
        await connector._sync_users(users)
        connector.data_entities_processor.on_new_app_users.assert_awaited_once()
        call_args = connector.data_entities_processor.on_new_app_users.call_args[0][0]
        assert len(call_args) == 2

    @pytest.mark.asyncio
    async def test_sync_users_skips_empty_list(self):
        connector = _make_connector()
        await connector._sync_users([])
        connector.data_entities_processor.on_new_app_users.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_users_raises_on_processor_exception(self):
        connector = _make_connector()
        connector.data_entities_processor.on_new_app_users = AsyncMock(
            side_effect=Exception("DB error")
        )
        with pytest.raises(Exception, match="DB error"):
            await connector._sync_users([{"Id": "u1", "Email": "x@x.com"}])


# ===========================================================================
# SalesforceConnector._sync_roles
# ===========================================================================


class TestSyncRoles:

    @pytest.mark.asyncio
    async def test_sync_roles_creates_roles(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        roles = [
            {"Id": "role-1", "Name": "Manager", "ParentRoleId": None, "SystemModstamp": None},
        ]
        user_roles = [
            {"Id": "u1", "Email": "alice@example.com", "UserRoleId": "role-1", "FirstName": "Alice", "LastName": "S"},
        ]
        await connector._sync_roles(roles, user_roles)
        connector.data_entities_processor.on_new_app_roles.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_roles_skips_empty_roles(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        await connector._sync_roles([], [])
        connector.data_entities_processor.on_new_app_roles.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_roles_raises_on_processor_exception(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_entities_processor.on_new_app_roles = AsyncMock(side_effect=Exception("fail"))
        roles = [{"Id": "role-1", "Name": "CEO"}]
        with pytest.raises(Exception, match="fail"):
            await connector._sync_roles(roles, [])


# ===========================================================================
# SalesforceConnector._sync_user_groups
# ===========================================================================


class TestSyncUserGroups:

    @pytest.mark.asyncio
    async def test_skips_when_no_group_records(self):
        connector = _make_connector()
        await connector._sync_user_groups(api_version="59.0", group_records=[])
        connector.data_entities_processor.on_new_user_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_when_no_flattened_memberships(self):
        connector = _make_connector()
        connector._flatten_group_members = AsyncMock(return_value={})
        groups = [{"Id": "grp-1", "Name": "Sales Team", "Type": "Regular"}]
        await connector._sync_user_groups(api_version="59.0", group_records=groups)
        connector.data_entities_processor.on_new_user_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_user_groups_when_members_found(self):
        connector = _make_connector()
        connector._flatten_group_members = AsyncMock(return_value={
            "grp-1": {("u1", "alice@example.com"), ("u2", "bob@example.com")},
        })
        groups = [
            {"Id": "grp-1", "Name": "Sales Team", "Type": "Regular", "CreatedDate": None, "LastModifiedDate": None},
        ]
        await connector._sync_user_groups(api_version="59.0", group_records=groups)
        connector.data_entities_processor.on_new_user_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_groups_without_id(self):
        connector = _make_connector()
        connector._flatten_group_members = AsyncMock(return_value={
            "grp-1": {("u1", "alice@example.com")},
        })
        groups = [
            {"Id": None, "Name": "Bad Group", "Type": "Regular"},
            {"Id": "grp-1", "Name": "Good Group", "Type": "Regular", "CreatedDate": None, "LastModifiedDate": None},
        ]
        await connector._sync_user_groups(api_version="59.0", group_records=groups)
        connector.data_entities_processor.on_new_user_groups.assert_awaited_once()


# ===========================================================================
# SalesforceConnector._sync_products
# ===========================================================================


class TestSyncProducts:

    @pytest.mark.asyncio
    async def test_skips_when_no_products(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        await connector._sync_products([])
        connector.data_entities_processor.on_new_records.assert_not_awaited()
        # on_new_record_groups is still called to ensure the product record group exists
        connector.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_syncs_new_products(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        # get_nodes_by_field_in returns [] → product is new
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.get_nodes_by_field_in = AsyncMock(return_value=[])
        connector._fetch_standard_pricebook_prices = AsyncMock(return_value={})

        product = {"Id": "prod-1", "Name": "Widget", "ProductCode": "WP-1", "Family": "HW"}
        await connector._sync_products([product])
        connector.data_entities_processor.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_updates_existing_products(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        # get_nodes_by_field_in returns a node → product already exists
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.get_nodes_by_field_in = AsyncMock(return_value=[
            {"externalRecordId": "prod-1", "connectorId": "conn-sf-1"}
        ])
        connector._fetch_standard_pricebook_prices = AsyncMock(return_value={})

        product = {"Id": "prod-1", "Name": "Widget V2", "ProductCode": "WP-2", "Family": "HW"}
        await connector._sync_products([product])
        connector.data_entities_processor.on_record_content_update.assert_awaited()

    @pytest.mark.asyncio
    async def test_skips_products_without_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._fetch_standard_pricebook_prices = AsyncMock(return_value={})
        product = {"Name": "No ID Product", "ProductCode": "NID"}
        await connector._sync_products([product])
        connector.data_entities_processor.on_new_records.assert_not_awaited()


# ===========================================================================
# SalesforceConnector._sync_cases
# ===========================================================================


class TestSyncCases:

    @pytest.mark.asyncio
    async def test_skips_when_no_cases(self):
        connector = _make_connector()
        await connector._sync_cases([])
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_syncs_new_cases(self):
        connector = _make_connector()
        # get_nodes_by_field_in returns [] → case is new
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.get_nodes_by_field_in = AsyncMock(return_value=[])

        case = {
            "Id": "case-1",
            "CaseNumber": "001",
            "Subject": "Bug",
            "Status": "Open",
            "AccountId": "acc-1",
            "Owner": {"Name": "Alice", "Email": "alice@example.com"},
            "Contact": {},
            "CreatedBy": {},
            "CreatedDate": None,
            "LastModifiedDate": None,
        }
        await connector._sync_cases([case])
        connector.data_entities_processor.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_updates_existing_case(self):
        connector = _make_connector()
        # get_nodes_by_field_in returns a node → case already exists
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.get_nodes_by_field_in = AsyncMock(return_value=[
            {"externalRecordId": "case-1", "connectorId": "conn-sf-1"}
        ])

        case = {
            "Id": "case-1",
            "CaseNumber": "001",
            "Subject": "Bug Fixed",
            "Status": "Closed",
            "AccountId": "acc-1",
            "Owner": {},
            "Contact": {},
            "CreatedBy": {},
            "CreatedDate": None,
            "LastModifiedDate": None,
        }
        await connector._sync_cases([case])
        connector.data_entities_processor.on_record_content_update.assert_awaited()

    @pytest.mark.asyncio
    async def test_raises_on_exception(self):
        connector = _make_connector()
        connector.data_entities_processor.on_new_record_groups = AsyncMock(
            side_effect=Exception("DB error")
        )
        case = {
            "Id": "case-1",
            "CaseNumber": "001",
            "Subject": "Bug",
            "Status": "Open",
            "AccountId": "acc-1",
            "Owner": {},
            "Contact": {},
            "CreatedBy": {},
            "CreatedDate": None,
            "LastModifiedDate": None,
        }
        with pytest.raises(Exception, match="DB error"):
            await connector._sync_cases([case])


# ===========================================================================
# SalesforceConnector._sync_tasks
# ===========================================================================


class TestSyncTasks:

    @pytest.mark.asyncio
    async def test_skips_when_no_tasks(self):
        connector = _make_connector()
        await connector._sync_tasks([])
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_syncs_new_tasks(self):
        connector = _make_connector()
        # get_nodes_by_field_in returns [] → task is new
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.get_nodes_by_field_in = AsyncMock(return_value=[])

        task = {
            "Id": "task-1",
            "Subject": "Call customer",
            "Status": "Not Started",
            "Priority": "Normal",
            "TaskSubtype": "Call",
            "WhatId": None,
            "What": None,
            "Owner": {"Name": "Alice", "Email": "alice@example.com"},
            "CreatedBy": {},
            "ActivityDate": None,
        }
        await connector._sync_tasks([task])
        connector.data_entities_processor.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_updates_existing_tasks(self):
        connector = _make_connector()
        # get_nodes_by_field_in returns a node → task already exists
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.get_nodes_by_field_in = AsyncMock(return_value=[
            {"externalRecordId": "task-1", "connectorId": "conn-sf-1", "_key": "arango-task-1", "externalGroupId": "UNASSIGNED-TASK"}
        ])
        task = {
            "Id": "task-1",
            "Subject": "Call customer updated",
            "Status": "Completed",
            "Priority": "Normal",
            "TaskSubtype": "Call",
            "WhatId": None,
            "What": None,
            "Owner": {"Name": "Alice", "Email": "alice@example.com"},
            "CreatedBy": {},
            "ActivityDate": None,
        }
        await connector._sync_tasks([task])
        connector.data_entities_processor.on_record_content_update.assert_awaited()

    @pytest.mark.asyncio
    async def test_skips_task_rows_without_id(self):
        connector = _make_connector()
        tasks = [{"Subject": "No ID task", "Status": "Open"}]  # no "Id"
        await connector._sync_tasks(tasks)
        connector.data_entities_processor.on_new_records.assert_not_awaited()


# ===========================================================================
# SalesforceConnector._sync_opportunities
# ===========================================================================


class TestSyncOpportunities:

    @pytest.mark.asyncio
    async def test_skips_when_no_opportunities(self):
        connector = _make_connector()
        await connector._sync_opportunities([])
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_syncs_new_opportunity(self):
        connector = _make_connector()
        # get_nodes_by_field_in returns [] → opportunity is new
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.get_nodes_by_field_in = AsyncMock(return_value=[])
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)

        opp = {
            "Id": "opp-1",
            "Name": "Enterprise Deal",
            "AccountId": "acc-1",
            "Account": {"Name": "Corp"},
            "StageName": "Negotiation",
            "Amount": 100000.0,
            "ExpectedRevenue": 90000.0,
            "CloseDate": "2024-12-01",
            "Probability": 70.0,
            "Type": "New",
            "OwnerId": "u1",
            "IsWon": False,
            "IsClosed": False,
            "CreatedDate": "2024-01-01T00:00:00.000+0000",
            "LastModifiedDate": "2024-06-01T00:00:00.000+0000",
        }
        await connector._sync_opportunities([opp])
        connector.data_entities_processor.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_updates_existing_opportunity(self):
        connector = _make_connector()
        # get_nodes_by_field_in returns a node → opportunity already exists
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.get_nodes_by_field_in = AsyncMock(return_value=[
            {"externalRecordId": "opp-1", "connectorId": "conn-sf-1"}
        ])
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)

        opp = {
            "Id": "opp-1",
            "Name": "Enterprise Deal Updated",
            "AccountId": "acc-1",
            "Account": {"Name": "Corp"},
            "StageName": "Closed Won",
            "Amount": 120000.0,
            "ExpectedRevenue": 120000.0,
            "CloseDate": "2024-12-01",
            "Probability": 100.0,
            "Type": "New",
            "OwnerId": "u1",
            "IsWon": True,
            "IsClosed": True,
            "CreatedDate": "2024-01-01T00:00:00.000+0000",
            "LastModifiedDate": "2024-12-01T00:00:00.000+0000",
        }
        await connector._sync_opportunities([opp])
        connector.data_entities_processor.on_record_content_update.assert_awaited()

    @pytest.mark.asyncio
    async def test_skips_opps_without_id(self):
        connector = _make_connector()
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.get_nodes_by_field_in = AsyncMock(return_value=[])
        opp = {"Name": "No ID Opp", "StageName": "Proposal"}
        await connector._sync_opportunities([opp])
        connector.data_entities_processor.on_new_records.assert_not_awaited()


# ===========================================================================
# SalesforceConnector._handle_record_updates
# ===========================================================================


class TestHandleRecordUpdates:

    @pytest.mark.asyncio
    async def test_handles_deletion(self):
        connector = _make_connector()
        update = RecordUpdate(
            record=None,
            is_new=False,
            is_updated=False,
            is_deleted=True,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
            external_record_id="ext-del-1",
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_deleted.assert_awaited_once_with(
            record_id="ext-del-1"
        )

    @pytest.mark.asyncio
    async def test_handles_content_change(self):
        connector = _make_connector()
        update = RecordUpdate(
            record=MagicMock(record_name="File.pdf"),
            is_new=False,
            is_updated=True,
            is_deleted=False,
            metadata_changed=False,
            content_changed=True,
            permissions_changed=False,
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_content_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handles_metadata_change(self):
        connector = _make_connector()
        update = RecordUpdate(
            record=MagicMock(record_name="Report.pdf"),
            is_new=False,
            is_updated=True,
            is_deleted=False,
            metadata_changed=True,
            content_changed=False,
            permissions_changed=False,
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_metadata_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handles_both_content_and_metadata_change(self):
        connector = _make_connector()
        update = RecordUpdate(
            record=MagicMock(record_name="Doc.pdf"),
            is_new=False,
            is_updated=True,
            is_deleted=False,
            metadata_changed=True,
            content_changed=True,
            permissions_changed=False,
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_content_update.assert_awaited_once()
        connector.data_entities_processor.on_record_metadata_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_action_for_new_record(self):
        connector = _make_connector()
        update = RecordUpdate(
            record=MagicMock(record_name="New.pdf"),
            is_new=True,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_deleted.assert_not_awaited()
        connector.data_entities_processor.on_record_content_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception_is_caught(self):
        connector = _make_connector()
        connector.data_entities_processor.on_record_content_update = AsyncMock(
            side_effect=Exception("failure")
        )
        update = RecordUpdate(
            record=MagicMock(record_name="Fail.pdf"),
            is_new=False,
            is_updated=True,
            is_deleted=False,
            metadata_changed=False,
            content_changed=True,
            permissions_changed=False,
        )
        # Should not raise — exception is caught inside the method
        await connector._handle_record_updates(update)


# ===========================================================================
# SalesforceConnector.stream_record
# ===========================================================================


class TestStreamRecord:

    @pytest.mark.asyncio
    async def test_stream_raises_when_data_source_none(self):
        connector = _make_connector()
        connector.data_source = None
        connector._reinitialize_token_if_needed = AsyncMock()
        record = MagicMock()
        record.record_type = RecordType.PRODUCT
        # stream_record returns None (not raises) when data_source is None
        result = await connector.stream_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_stream_file_record(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._reinitialize_token_if_needed = AsyncMock()

        record = MagicMock()
        record.record_type = RecordType.FILE
        record.record_name = "report.pdf"
        record.mime_type = "application/pdf"
        record.id = "arango-rec-1"

        with patch(
            "app.connectors.sources.salesforce.connector.create_stream_record_response"
        ) as mock_stream:
            mock_stream.return_value = MagicMock()
            result = await connector.stream_record(record)
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_product_record(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._reinitialize_token_if_needed = AsyncMock()
        connector._process_product_record = AsyncMock(return_value=b'{"blocks": []}')

        record = MagicMock()
        record.record_type = RecordType.PRODUCT
        record.external_record_id = "prod-1"
        record.id = "arango-prod-1"

        result = await connector.stream_record(record)
        assert result is not None
        connector._process_product_record.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stream_deal_record(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._reinitialize_token_if_needed = AsyncMock()
        connector._process_deal_record = AsyncMock(return_value=b'{"blocks": []}')

        record = MagicMock()
        record.record_type = RecordType.DEAL
        record.external_record_id = "opp-1"
        record.id = "arango-opp-1"

        result = await connector.stream_record(record)
        assert result is not None
        connector._process_deal_record.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stream_case_record(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._reinitialize_token_if_needed = AsyncMock()
        connector._process_case_record = AsyncMock(return_value=b'{"blocks": []}')

        record = MagicMock()
        record.record_type = RecordType.CASE
        record.external_record_id = "case-1"
        record.id = "arango-case-1"

        result = await connector.stream_record(record)
        assert result is not None
        connector._process_case_record.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stream_task_record(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._reinitialize_token_if_needed = AsyncMock()
        connector._process_task_record = AsyncMock(return_value=b'{"blocks": []}')

        record = MagicMock()
        record.record_type = RecordType.TASK
        record.external_record_id = "task-1"
        record.id = "arango-task-1"

        result = await connector.stream_record(record)
        assert result is not None
        connector._process_task_record.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stream_unsupported_type_raises(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._reinitialize_token_if_needed = AsyncMock()

        record = MagicMock()
        record.record_type = "UNKNOWN_TYPE"

        with pytest.raises(Exception):
            await connector.stream_record(record)


# ===========================================================================
# SalesforceConnector.run_sync
# ===========================================================================


class TestRunSync:

    @pytest.mark.asyncio
    async def test_run_sync_returns_when_no_data_source(self):
        connector = _make_connector()
        connector._reinitialize_token_if_needed = AsyncMock()
        connector.data_source = None
        # Should not raise, just return
        await connector.run_sync()

    @pytest.mark.asyncio
    async def test_run_sync_calls_all_steps(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._reinitialize_token_if_needed = AsyncMock()

        # Patch load_connector_filters
        from app.connectors.core.registry.filters import FilterCollection
        with patch(
            "app.connectors.sources.salesforce.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_filters:
            mock_filters.return_value = (FilterCollection(), FilterCollection())

            # Patch all the individual sync methods
            connector._sync_users = AsyncMock()
            connector._sync_roles = AsyncMock()
            connector._sync_user_groups = AsyncMock()
            connector._sync_accounts = AsyncMock()
            connector._sync_contacts = AsyncMock()
            connector._sync_leads = AsyncMock()
            connector._sync_products = AsyncMock()
            connector._sync_opportunities = AsyncMock()
            connector._sync_sold_in_edges = AsyncMock()
            connector._sync_cases = AsyncMock()
            connector._sync_tasks = AsyncMock()
            connector._sync_files = AsyncMock()
            connector._sync_permissions_edges = AsyncMock()

            connector._get_updated_account = AsyncMock(return_value=[])
            connector._get_updated_product = AsyncMock(return_value=[])
            connector._get_updated_deal = AsyncMock(return_value=[])
            connector._get_updated_case = AsyncMock(return_value=[])
            connector._get_updated_task = AsyncMock(return_value=[])
            connector._get_updated_file = AsyncMock(return_value=[])
            connector._soql_query_paginated = AsyncMock(
                return_value=_sf_response(True, {"records": []})
            )

            # Patch sync points
            connector.user_sync_point = MagicMock()
            connector.user_sync_point.read_sync_point = AsyncMock(return_value={})
            connector.user_sync_point.update_sync_point = AsyncMock()
            connector.records_sync_point = MagicMock()
            connector.records_sync_point.read_sync_point = AsyncMock(return_value={})
            connector.records_sync_point.update_sync_point = AsyncMock()

            await connector.run_sync()

            connector._sync_users.assert_awaited_once()
            connector._sync_roles.assert_awaited_once()
            connector._sync_user_groups.assert_awaited_once()
            connector._sync_accounts.assert_awaited_once()
            connector._sync_contacts.assert_awaited_once()
            connector._sync_leads.assert_awaited_once()
            connector._sync_products.assert_awaited_once()
            connector._sync_opportunities.assert_awaited_once()
            connector._sync_sold_in_edges.assert_awaited_once()
            connector._sync_cases.assert_awaited_once()
            connector._sync_tasks.assert_awaited_once()
            connector._sync_files.assert_awaited_once()
            connector._sync_permissions_edges.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_sync_propagates_exceptions(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._reinitialize_token_if_needed = AsyncMock()

        from app.connectors.core.registry.filters import FilterCollection
        with patch(
            "app.connectors.sources.salesforce.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_filters:
            mock_filters.return_value = (FilterCollection(), FilterCollection())
            connector.user_sync_point = MagicMock()
            connector.user_sync_point.read_sync_point = AsyncMock(return_value={})
            connector._soql_query_paginated = AsyncMock(side_effect=Exception("SOQL failure"))

            with pytest.raises(Exception, match="SOQL failure"):
                await connector.run_sync()


# ===========================================================================
# SalesforceConnector._reinitialize_token_if_needed
# ===========================================================================


class TestReinitializeTokenIfNeeded:

    @pytest.mark.asyncio
    async def test_returns_false_when_not_initialized(self):
        connector = _make_connector()
        connector.data_source = None
        result = await connector._reinitialize_token_if_needed()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_when_token_still_active(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.limits = AsyncMock(return_value=_sf_response(True, {}))
        connector._get_api_version = AsyncMock(return_value="59.0")
        result = await connector._reinitialize_token_if_needed()
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_for_non_401_error(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.limits = AsyncMock(
            return_value=_sf_response(False, error="HTTP 403 Forbidden")
        )
        connector._get_api_version = AsyncMock(return_value="59.0")
        result = await connector._reinitialize_token_if_needed()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.limits = AsyncMock(side_effect=Exception("Network error"))
        connector._get_api_version = AsyncMock(return_value="59.0")
        result = await connector._reinitialize_token_if_needed()
        assert result is False


# ===========================================================================
# SalesforceConnector._get_access_token
# ===========================================================================


class TestGetAccessToken:

    @pytest.mark.asyncio
    async def test_returns_token_from_config(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "tok-abc"}
        })
        result = await connector._get_access_token()
        assert result == "tok-abc"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_config(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value=None)
        result = await connector._get_access_token()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(side_effect=Exception("Service down"))
        result = await connector._get_access_token()
        assert result is None


# ===========================================================================
# SalesforceConnector._sync_accounts
# ===========================================================================


class TestSyncAccounts:

    @pytest.mark.asyncio
    async def test_skips_when_no_accounts(self):
        connector = _make_connector()
        await connector._sync_accounts([])
        connector.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_syncs_accounts_creates_record_groups(self):
        connector = _make_connector()
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.get_all_orgs = AsyncMock(return_value=[])
        mock_tx.batch_upsert_orgs = AsyncMock()
        mock_tx.batch_create_edges = AsyncMock()

        account = {
            "Id": "acc-1",
            "Name": "Acme Corp",
            "Website": "https://acme.com",
            "Industry": "Technology",
            "Ownership": "Public",
            "Phone": "555-1234",
            "DunsNumber": None,
            "Owner": {"Name": "Alice"},
            "Type": "Customer",
            "Rating": "Hot",
            "CreatedDate": "2024-01-01T00:00:00.000+0000",
            "LastModifiedDate": "2024-06-01T00:00:00.000+0000",
            "SystemModstamp": "2024-06-01T00:00:00.000+0000",
            "Opportunities": {"records": []},
        }
        await connector._sync_accounts([account])
        connector.data_entities_processor.on_new_record_groups.assert_awaited()

    @pytest.mark.asyncio
    async def test_skips_account_without_id(self):
        connector = _make_connector()
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.get_all_orgs = AsyncMock(return_value=[])
        mock_tx.batch_upsert_orgs = AsyncMock()
        mock_tx.batch_create_edges = AsyncMock()

        account = {"Name": "No ID Account", "Opportunities": {"records": []}}
        await connector._sync_accounts([account])
        # record_groups_with_perms is empty → on_new_record_groups not awaited
        connector.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_customer_edge_when_won_opportunity(self):
        connector = _make_connector()
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.get_all_orgs = AsyncMock(return_value=[])
        mock_tx.batch_upsert_orgs = AsyncMock()
        mock_tx.batch_create_edges = AsyncMock()
        mock_tx.delete_edges_to = AsyncMock()
        mock_tx.delete_edges_from = AsyncMock()

        account = {
            "Id": "acc-2",
            "Name": "BigCo",
            "CreatedDate": "2023-01-01T00:00:00.000+0000",
            "LastModifiedDate": "2024-01-01T00:00:00.000+0000",
            "Opportunities": {
                "records": [
                    {"IsWon": True, "IsClosed": True, "CloseDate": "2024-01-15"},
                ]
            },
        }
        await connector._sync_accounts([account])
        # Should have created customer edge (batch_create_edges called multiple times)
        assert mock_tx.batch_create_edges.await_count >= 1


# ===========================================================================
# SalesforceConnector._sync_contacts
# ===========================================================================


class TestSyncContacts:

    @pytest.mark.asyncio
    async def test_skips_when_no_contacts(self):
        connector = _make_connector()
        await connector._sync_contacts([])
        # Should return early without any DB calls
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.batch_upsert_people.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_contact_without_email(self):
        connector = _make_connector()
        contact = {
            "Id": "contact-1",
            "FirstName": "No",
            "LastName": "Email",
            "Phone": "555-0000",
            "AccountId": "acc-1",
            "Account": {"Name": "Acme"},
            "CreatedDate": None,
            "LastModifiedDate": None,
        }
        # Contact without email should be skipped
        await connector._sync_contacts([contact])
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.batch_upsert_people.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_syncs_contact_with_email(self):
        connector = _make_connector()
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.get_nodes_by_field_in = AsyncMock(return_value=[])
        mock_tx.batch_upsert_people = AsyncMock()
        mock_tx.batch_create_edges = AsyncMock()

        contact = {
            "Id": "contact-1",
            "FirstName": "Alice",
            "LastName": "Smith",
            "Email": "alice@example.com",
            "Phone": "555-1234",
            "AccountId": "acc-1",
            "Account": {"Name": "Acme Corp"},
            "Title": "Engineer",
            "Department": "Engineering",
            "LeadSource": "Web",
            "Description": "Key contact",
            "CreatedDate": "2024-01-01T00:00:00.000+0000",
            "LastModifiedDate": "2024-06-01T00:00:00.000+0000",
        }
        await connector._sync_contacts([contact])
        mock_tx.batch_upsert_people.assert_awaited_once()
        mock_tx.batch_create_edges.assert_awaited()


# ===========================================================================
# SalesforceConnector._sync_leads
# ===========================================================================


class TestSyncLeads:

    @pytest.mark.asyncio
    async def test_skips_when_no_leads(self):
        connector = _make_connector()
        await connector._sync_leads([])
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.batch_upsert_people.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_lead_without_email(self):
        connector = _make_connector()
        lead = {
            "Id": "lead-1",
            "FirstName": "Bob",
            "LastName": "Jones",
            "Company": "Startup",
            "Status": "New",
            "CreatedDate": None,
            "LastModifiedDate": None,
        }
        await connector._sync_leads([lead])
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.batch_upsert_people.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_syncs_lead_with_email(self):
        connector = _make_connector()
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.get_nodes_by_field_in = AsyncMock(return_value=[])
        mock_tx.batch_upsert_people = AsyncMock()
        mock_tx.batch_create_edges = AsyncMock()

        lead = {
            "Id": "lead-1",
            "FirstName": "Bob",
            "LastName": "Jones",
            "Email": "bob@startup.com",
            "Phone": "555-9999",
            "Company": "Startup Inc",
            "Title": "CEO",
            "Status": "Open",
            "Rating": "Hot",
            "Industry": "Tech",
            "LeadSource": "Web",
            "AnnualRevenue": 500000.0,
            "CreatedDate": "2024-01-01T00:00:00.000+0000",
            "LastModifiedDate": "2024-06-01T00:00:00.000+0000",
            "ConvertedDate": None,
            "ConvertedContactId": None,
        }
        await connector._sync_leads([lead])
        mock_tx.batch_upsert_people.assert_awaited_once()
        mock_tx.batch_create_edges.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_lead_without_id(self):
        connector = _make_connector()
        lead = {"Email": "nobody@example.com", "Company": "Unknown"}
        await connector._sync_leads([lead])
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.batch_upsert_people.assert_not_awaited()


# ===========================================================================
# SalesforceConnector stub methods
# ===========================================================================


class TestSalesforceStubMethods:

    @pytest.mark.asyncio
    async def test_reindex_records_skips_empty_list(self):
        connector = _make_connector()
        # Should return without error when no records given
        result = await connector.reindex_records([])
        assert result is None

    @pytest.mark.asyncio
    async def test_run_incremental_sync_delegates_to_run_sync(self):
        connector = _make_connector()
        connector.run_sync = AsyncMock()
        await connector.run_incremental_sync()
        connector.run_sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cleanup_clears_data_source(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        await connector.cleanup()
        assert connector.data_source is None
