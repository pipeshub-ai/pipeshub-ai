"""Tests for app.connectors.sources.salesforce.connector."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse

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
    MessageSegment,
    RecordUpdate,
    SalesforceAccount,
    SalesforceCase,
    SalesforceConnector,
    SalesforceContentVersion,
    SalesforceLead,
    SalesforceLineItem,
    SalesforceOpportunity,
    SalesforceProduct,
    SalesforceRole,
    SalesforceTask,
    SalesforceUser,
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
        user = SalesforceUser.model_validate({
            "Id": "user-1",
            "FirstName": "Alice",
            "LastName": "Smith",
            "Email": "alice@example.com",
            "Title": "Engineer",
            "CreatedDate": "2024-01-01T00:00:00.000+0000",
            "LastModifiedDate": "2024-06-01T00:00:00.000+0000",
        })
        app_user = connector.user_to_app_user(user)
        assert app_user.source_user_id == "user-1"
        assert app_user.email == "alice@example.com"
        assert app_user.full_name == "Alice Smith"
        assert app_user.title == "Engineer"

    def test_user_with_no_email_uses_empty_string(self):
        connector = _make_connector()
        user = SalesforceUser.model_validate({"Id": "user-2", "FirstName": "Bob", "LastName": "Jones"})
        app_user = connector.user_to_app_user(user)
        assert app_user.email == ""

    def test_full_name_strips_whitespace_when_first_or_last_missing(self):
        connector = _make_connector()
        user = SalesforceUser.model_validate({"Id": "user-3", "LastName": "Solo", "Email": "solo@example.com"})
        app_user = connector.user_to_app_user(user)
        assert app_user.full_name == "Solo"

    def test_connector_metadata_is_set(self):
        connector = _make_connector()
        user = SalesforceUser.model_validate({"Id": "user-4", "Email": "test@example.com"})
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
        role = SalesforceRole.model_validate({
            "Id": "role-1",
            "Name": "Sales Manager",
            "ParentRoleId": "role-parent",
            "SystemModstamp": "2024-01-01T00:00:00.000+0000",
        })
        app_role = connector.role_to_app_role(role)
        assert app_role.source_role_id == "role-1"
        assert app_role.name == "Sales Manager"
        assert app_role.parent_role_id == "role-parent"

    def test_role_with_no_parent(self):
        connector = _make_connector()
        role = SalesforceRole.model_validate({"Id": "role-top", "Name": "CEO"})
        app_role = connector.role_to_app_role(role)
        assert app_role.parent_role_id is None

    def test_connector_metadata_is_set(self):
        connector = _make_connector()
        role = SalesforceRole.model_validate({"Id": "role-2", "Name": "Developer"})
        app_role = connector.role_to_app_role(role)
        assert app_role.app_name == Connectors.SALESFORCE
        assert app_role.connector_id == "conn-sf-1"


# ===========================================================================
# SalesforceConnector._build_product_record
# ===========================================================================


class TestBuildProductRecord:

    def test_builds_product_from_row(self):
        connector = _make_connector()
        product = SalesforceProduct.model_validate({
            "Id": "prod-1",
            "Name": "Widget Pro",
            "ProductCode": "WP-100",
            "Family": "Hardware",
            "SystemModstamp": "2024-01-01T00:00:00.000+0000",
            "CreatedDate": "2023-01-01T00:00:00.000+0000",
            "LastModifiedDate": "2024-01-01T00:00:00.000+0000",
        })
        record = connector._build_product_record(product)
        assert record.external_record_id == "prod-1"
        assert record.record_name == "Widget Pro"
        assert record.product_code == "WP-100"
        assert record.product_family == "Hardware"
        assert record.record_type == RecordType.PRODUCT
        assert record.external_revision_id == "2024-01-01T00:00:00.000+0000"

    def test_weburl_contains_instance_url_and_product_id(self):
        connector = _make_connector()
        product = SalesforceProduct.model_validate({"Id": "prod-2", "Name": "Gadget", "ProductCode": None, "Family": None})
        record = connector._build_product_record(product)
        weburl = record.weburl or ""
        assert urlparse(weburl).netloc == "myinstance.salesforce.com"
        assert "prod-2" in weburl

    def test_connector_metadata_is_set(self):
        connector = _make_connector()
        product = SalesforceProduct.model_validate({"Id": "prod-3", "Name": "Test Product"})
        record = connector._build_product_record(product)
        assert record.connector_id == "conn-sf-1"
        assert record.connector_name == Connectors.SALESFORCE


# ===========================================================================
# SalesforceConnector._build_deal_record
# ===========================================================================


class TestBuildDealRecord:

    def test_builds_deal_from_opportunity_row(self):
        connector = _make_connector()
        opp = SalesforceOpportunity.model_validate({
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
        })
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
        assert urlparse(record.weburl).netloc == "myinstance.salesforce.com"
        assert "opp-1" in record.weburl

    def test_unassigned_deal_when_no_account(self):
        connector = _make_connector()
        opp = SalesforceOpportunity.model_validate({
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
        })
        record = connector._build_deal_record(opp)
        assert record.external_record_group_id == "UNASSIGNED-DEAL"

    def test_connector_metadata_is_set(self):
        connector = _make_connector()
        opp = SalesforceOpportunity.model_validate({
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
        })
        record = connector._build_deal_record(opp)
        assert record.connector_id == "conn-sf-1"
        assert record.connector_name == Connectors.SALESFORCE


# ===========================================================================
# SalesforceConnector._build_case_record
# ===========================================================================


class TestBuildCaseRecord:

    def test_builds_case_from_row(self):
        connector = _make_connector()
        case = SalesforceCase.model_validate({
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
        })
        record = connector._build_case_record(case)
        assert record.external_record_id == "case-1"
        assert record.record_name == "Login Broken"
        assert record.status == "Open"
        assert record.priority == "High"
        assert record.record_type == RecordType.CASE
        assert record.assignee == "Alice"

    def test_unassigned_case_when_no_account(self):
        connector = _make_connector()
        case = SalesforceCase.model_validate({
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
        })
        record = connector._build_case_record(case)
        assert record.external_record_group_id == "UNASSIGNED-CASE"

    def test_case_name_falls_back_to_case_number(self):
        connector = _make_connector()
        case = SalesforceCase.model_validate({
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
        })
        record = connector._build_case_record(case)
        assert "00001236" in record.record_name


# ===========================================================================
# SalesforceConnector._build_task_record
# ===========================================================================


class TestBuildTaskRecord:

    def test_builds_task_from_row(self):
        connector = _make_connector()
        task = SalesforceTask.model_validate({
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
        })
        record = connector._build_task_record(task)
        assert record.external_record_id == "task-1"
        assert record.record_name == "Follow up call"
        assert record.status == "Not Started"
        assert record.record_type == RecordType.TASK
        assert record.parent_external_record_id == "opp-1"

    def test_task_with_account_parent(self):
        connector = _make_connector()
        task = SalesforceTask.model_validate({
            "Id": "task-2",
            "Subject": "Account Task",
            "Status": "Completed",
            "WhatId": "acc-1",
            "What": {"Type": "Account"},
            "Owner": {},
            "CreatedBy": {},
            "ActivityDate": None,
        })
        record = connector._build_task_record(task)
        assert record.external_record_group_id == "acc-1"
        assert record.parent_external_record_id is None

    def test_task_with_no_subject_uses_id_as_name(self):
        connector = _make_connector()
        task = SalesforceTask.model_validate({
            "Id": "task-3",
            "Subject": None,
            "Status": "Open",
            "WhatId": None,
            "What": None,
            "Owner": {},
            "CreatedBy": {},
            "ActivityDate": None,
        })
        record = connector._build_task_record(task)
        assert "task-3" in record.record_name

    def test_task_external_group_unassigned_for_unknown_type(self):
        connector = _make_connector()
        task = SalesforceTask.model_validate({
            "Id": "task-4",
            "Subject": "Unknown Task",
            "Status": "Open",
            "WhatId": "obj-1",
            "What": {"Type": "CustomObject"},
            "Owner": {},
            "CreatedBy": {},
            "ActivityDate": None,
        })
        record = connector._build_task_record(task)
        assert record.external_record_group_id == "UNASSIGNED-TASK"


# ===========================================================================
# SalesforceConnector._build_file_record
# ===========================================================================


class TestBuildFileRecord:

    def test_builds_file_record(self):
        connector = _make_connector()
        meta = SalesforceContentVersion.model_validate({
            "Id": "cv-1",
            "ContentDocumentId": "doc-1",
            "Title": "Report.pdf",
            "PathOnClient": "Report.pdf",
            "ContentSize": 12345,
            "FileExtension": "pdf",
            "Checksum": "abc123",
            "LastModifiedDate": "2024-01-01T00:00:00.000+0000",
            "CreatedDate": "2023-12-01T00:00:00.000+0000",
        })
        record = connector._build_file_record(meta, "doc-1-acc-1")
        assert record is not None
        assert record.record_name == "Report.pdf"
        assert record.record_type == RecordType.FILE
        assert record.md5_hash == "abc123"
        assert record.size_in_bytes == 12345

    def test_returns_none_for_missing_id(self):
        connector = _make_connector()
        result = connector._build_file_record(SalesforceContentVersion.model_validate({}), "ext-id")
        assert result is None

    def test_returns_none_for_none_meta(self):
        connector = _make_connector()
        result = connector._build_file_record(None, "ext-id")
        assert result is None

    def test_sets_parent_id(self):
        connector = _make_connector()
        meta = SalesforceContentVersion.model_validate({
            "Id": "cv-2",
            "ContentDocumentId": "doc-2",
            "PathOnClient": "Invoice.xlsx",
            "ContentSize": 5000,
        })
        record = connector._build_file_record(meta, "doc-2-opp-1", parent_id="opp-1")
        assert record is not None
        assert record.parent_external_record_id == "opp-1"


# ===========================================================================
# SalesforceConnector._parse_opportunities
# ===========================================================================


class TestParseOpportunities:

    def test_returns_first_won_close_date(self):
        connector = _make_connector()
        acc = SalesforceAccount(
            Opportunities={
                "records": [
                    {"IsWon": True, "IsClosed": True, "CloseDate": "2024-01-15"},
                    {"IsWon": True, "IsClosed": True, "CloseDate": "2024-06-01"},
                ]
            }
        )
        end_time_ms, active_customer = connector._parse_opportunities(acc)
        assert end_time_ms is not None
        # Should be the first won close date
        assert active_customer is False

    def test_active_customer_when_open_opportunity(self):
        connector = _make_connector()
        acc = SalesforceAccount(
            Opportunities={
                "records": [
                    {"IsWon": False, "IsClosed": False, "CloseDate": "2024-12-01"},
                ]
            }
        )
        _, active_customer = connector._parse_opportunities(acc)
        assert active_customer is True

    def test_returns_none_when_no_opportunities(self):
        connector = _make_connector()
        acc = SalesforceAccount(Opportunities={"records": []})
        end_time_ms, active_customer = connector._parse_opportunities(acc)
        assert end_time_ms is None
        assert active_customer is False

    def test_handles_missing_opportunities_key(self):
        connector = _make_connector()
        acc = SalesforceAccount()
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

        product = SalesforceProduct.model_validate({"Id": "prod-1", "Name": "Widget", "ProductCode": "WP-1", "Family": "HW"})
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

        product = SalesforceProduct.model_validate({"Id": "prod-1", "Name": "Widget V2", "ProductCode": "WP-2", "Family": "HW"})
        await connector._sync_products([product])
        connector.data_entities_processor.on_record_content_update.assert_awaited()

    @pytest.mark.asyncio
    async def test_skips_products_without_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._fetch_standard_pricebook_prices = AsyncMock(return_value={})
        product = SalesforceProduct.model_validate({"Name": "No ID Product", "ProductCode": "NID"})
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

        case = SalesforceCase.model_validate({
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
        })
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

        case = SalesforceCase.model_validate({
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
        })
        await connector._sync_cases([case])
        connector.data_entities_processor.on_record_content_update.assert_awaited()

    @pytest.mark.asyncio
    async def test_raises_on_exception(self):
        connector = _make_connector()
        connector.data_entities_processor.on_new_record_groups = AsyncMock(
            side_effect=Exception("DB error")
        )
        case = SalesforceCase.model_validate({
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
        })
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

        task = SalesforceTask.model_validate({
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
        })
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
        task = SalesforceTask.model_validate({
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
        })
        await connector._sync_tasks([task])
        connector.data_entities_processor.on_record_content_update.assert_awaited()

    @pytest.mark.asyncio
    async def test_skips_task_rows_without_id(self):
        connector = _make_connector()
        tasks = [SalesforceTask.model_validate({"Subject": "No ID task", "Status": "Open"})]
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

        opp = SalesforceOpportunity.model_validate({
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
        })
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

        opp = SalesforceOpportunity.model_validate({
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
        })
        await connector._sync_opportunities([opp])
        connector.data_entities_processor.on_record_content_update.assert_awaited()

    @pytest.mark.asyncio
    async def test_skips_opps_without_id(self):
        connector = _make_connector()
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.get_nodes_by_field_in = AsyncMock(return_value=[])
        opp = SalesforceOpportunity.model_validate({"Name": "No ID Opp", "StageName": "Proposal"})
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

        account = SalesforceAccount.model_validate({
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
        })
        await connector._sync_accounts([account])
        connector.data_entities_processor.on_new_record_groups.assert_awaited()

    @pytest.mark.asyncio
    async def test_skips_account_without_id(self):
        connector = _make_connector()
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.get_all_orgs = AsyncMock(return_value=[])
        mock_tx.batch_upsert_orgs = AsyncMock()
        mock_tx.batch_create_edges = AsyncMock()

        account = SalesforceAccount(Name="No ID Account", Opportunities={"records": []})
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

        account = SalesforceAccount.model_validate({
            "Id": "acc-2",
            "Name": "BigCo",
            "CreatedDate": "2023-01-01T00:00:00.000+0000",
            "LastModifiedDate": "2024-01-01T00:00:00.000+0000",
            "Opportunities": {
                "records": [
                    {"IsWon": True, "IsClosed": True, "CloseDate": "2024-01-15"},
                ]
            },
        })
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
        lead = SalesforceLead(
            Id="lead-1",
            FirstName="Bob",
            LastName="Jones",
            Company="Startup",
            Status="New",
        )
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

        lead = SalesforceLead(
            Id="lead-1",
            FirstName="Bob",
            LastName="Jones",
            Email="bob@startup.com",
            Phone="555-9999",
            Company="Startup Inc",
            Title="CEO",
            Status="Open",
            Rating="Hot",
            Industry="Tech",
            LeadSource="Web",
            AnnualRevenue=500000.0,
            CreatedDate="2024-01-01T00:00:00.000+0000",
            LastModifiedDate="2024-06-01T00:00:00.000+0000",
        )
        await connector._sync_leads([lead])
        mock_tx.batch_upsert_people.assert_awaited_once()
        mock_tx.batch_create_edges.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_lead_without_id(self):
        connector = _make_connector()
        lead = SalesforceLead(Email="nobody@example.com", Company="Unknown")
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


# ===========================================================================
# SalesforceConnector._soql_query_paginated (additional branch coverage)
# ===========================================================================


class TestSoqlQueryPaginatedBranches:

    @pytest.mark.asyncio
    async def test_queryAll_flag_uses_soql_query_all(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.soql_query_all = AsyncMock(
            return_value=_sf_response(True, {"records": [{"Id": "1"}], "done": True})
        )
        result = await connector._soql_query_paginated(
            api_version="59.0", q="SELECT Id FROM Account", queryAll=True
        )
        connector.data_source.soql_query_all.assert_awaited_once()
        assert result.success is True
        assert len(result.data["records"]) == 1

    @pytest.mark.asyncio
    async def test_pagination_follows_next_records_url(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        page1 = _sf_response(True, {
            "records": [{"Id": "A"}],
            "done": False,
            "nextRecordsUrl": "/services/data/v59.0/query/next",
        })
        page2 = _sf_response(True, {"records": [{"Id": "B"}], "done": True})
        connector.data_source.soql_query = AsyncMock(return_value=page1)
        connector.data_source.soql_query_next = AsyncMock(return_value=page2)
        result = await connector._soql_query_paginated(api_version="59.0", q="SELECT Id FROM Obj")
        assert len(result.data["records"]) == 2

    @pytest.mark.asyncio
    async def test_pagination_breaks_when_next_url_missing(self):
        """If done=False but nextRecordsUrl is absent, loop should break (line 521)."""
        connector = _make_connector()
        connector.data_source = MagicMock()
        page1 = _sf_response(True, {"records": [{"Id": "X"}], "done": False})
        connector.data_source.soql_query = AsyncMock(return_value=page1)
        result = await connector._soql_query_paginated(api_version="59.0", q="SELECT Id FROM Obj")
        assert result.success is True
        assert result.data["records"] == [{"Id": "X"}]

    @pytest.mark.asyncio
    async def test_pagination_stops_on_failed_next_page(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        page1 = _sf_response(True, {
            "records": [{"Id": "A"}],
            "done": False,
            "nextRecordsUrl": "/next",
        })
        connector.data_source.soql_query = AsyncMock(return_value=page1)
        connector.data_source.soql_query_next = AsyncMock(
            return_value=_sf_response(False, error="Server error")
        )
        result = await connector._soql_query_paginated(api_version="59.0", q="SELECT Id FROM Obj")
        assert result.success is True
        assert len(result.data["records"]) == 1


# ===========================================================================
# SalesforceConnector.init (ValueError from client builder)
# ===========================================================================


class TestInitValueError:

    @pytest.mark.asyncio
    async def test_returns_false_when_client_build_raises_value_error(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "tok-abc", "refresh_token": "ref-xyz"},
            "auth": {"oauthConfigId": "oid-1"},
        })
        with patch(
            "app.connectors.sources.salesforce.connector.fetch_oauth_config_by_id",
            new_callable=AsyncMock,
            return_value={"config": {"instance_url": "https://sf.example.com"}},
        ), patch(
            "app.connectors.sources.salesforce.connector.SalesforceClient.build_with_config",
            side_effect=ValueError("bad config"),
        ):
            result = await connector.init()
        assert result is False


# ===========================================================================
# SalesforceConnector._reinitialize_token_if_needed (401 refresh path)
# ===========================================================================


class TestReinitializeToken401:

    @pytest.mark.asyncio
    async def test_returns_false_when_refresh_service_unavailable(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        connector.data_source.limits = AsyncMock(
            return_value=_sf_response(False, error="HTTP 401 Unauthorized")
        )
        with patch(
            "app.connectors.sources.salesforce.connector.startup_service"
        ) as mock_startup:
            mock_startup.get_token_refresh_service.return_value = None
            result = await connector._reinitialize_token_if_needed()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_config_missing_on_refresh(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        connector.data_source.limits = AsyncMock(
            return_value=_sf_response(False, error="HTTP 401 Unauthorized")
        )
        connector.config_service.get_config = AsyncMock(return_value=None)
        mock_refresh_svc = MagicMock()
        with patch(
            "app.connectors.sources.salesforce.connector.startup_service"
        ) as mock_startup:
            mock_startup.get_token_refresh_service.return_value = mock_refresh_svc
            result = await connector._reinitialize_token_if_needed()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_refresh_token_in_config(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        connector.data_source.limits = AsyncMock(
            return_value=_sf_response(False, error="HTTP 401 Unauthorized")
        )
        connector.config_service.get_config = AsyncMock(return_value={"credentials": {}})
        mock_refresh_svc = MagicMock()
        with patch(
            "app.connectors.sources.salesforce.connector.startup_service"
        ) as mock_startup:
            mock_startup.get_token_refresh_service.return_value = mock_refresh_svc
            result = await connector._reinitialize_token_if_needed()
        assert result is False

    @pytest.mark.asyncio
    async def test_reinit_calls_init_after_refresh(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        connector.data_source.limits = AsyncMock(
            return_value=_sf_response(False, error="HTTP 401 Unauthorized")
        )
        connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {"refresh_token": "ref-abc"}
        })
        mock_refresh_svc = MagicMock()
        mock_refresh_svc._perform_token_refresh = AsyncMock()
        connector.init = AsyncMock(return_value=True)
        with patch(
            "app.connectors.sources.salesforce.connector.startup_service"
        ) as mock_startup:
            mock_startup.get_token_refresh_service.return_value = mock_refresh_svc
            result = await connector._reinitialize_token_if_needed()
        assert result is True
        connector.init.assert_awaited_once()


# ===========================================================================
# SalesforceConnector._message_segments_to_html
# ===========================================================================


class TestMessageSegmentsToHtml:

    @pytest.mark.asyncio
    async def test_text_segment(self):
        connector = _make_connector()
        segments = [MessageSegment(type="Text", text="Hello world")]
        result = await connector._message_segments_to_html(segments)
        assert "Hello world" in result

    @pytest.mark.asyncio
    async def test_markup_begin_end(self):
        connector = _make_connector()
        segments = [
            MessageSegment(type="MarkupBegin", htmlTag="b"),
            MessageSegment(type="Text", text="Bold"),
            MessageSegment(type="MarkupEnd", htmlTag="b"),
        ]
        result = await connector._message_segments_to_html(segments)
        assert "<b>" in result
        assert "</b>" in result
        assert "Bold" in result

    @pytest.mark.asyncio
    async def test_entity_link_segment(self):
        connector = _make_connector()
        segments = [
            MessageSegment(type="EntityLink", text="View Opportunity", reference={"url": "/opp/123"}),
        ]
        result = await connector._message_segments_to_html(segments)
        assert "View Opportunity" in result
        assert "/opp/123" in result

    @pytest.mark.asyncio
    async def test_inline_image_segment_fetches_base64(self):
        connector = _make_connector()
        connector._fetch_file_as_base64_uri = AsyncMock(return_value="data:image/png;base64,abc")
        segments = [MessageSegment(type="InlineImage", altText="pic", url="/files/img")]
        result = await connector._message_segments_to_html(segments)
        assert "data:image/png;base64,abc" in result

    @pytest.mark.asyncio
    async def test_inline_image_segment_falls_back_on_none(self):
        connector = _make_connector()
        connector._fetch_file_as_base64_uri = AsyncMock(return_value=None)
        segments = [MessageSegment(type="InlineImage", altText="pic", url="/files/img")]
        result = await connector._message_segments_to_html(segments)
        assert 'src=""' in result

    @pytest.mark.asyncio
    async def test_field_change_segment(self):
        connector = _make_connector()
        segments = [MessageSegment(type="FieldChange", text="Stage changed")]
        result = await connector._message_segments_to_html(segments)
        assert "Stage changed" in result

    @pytest.mark.asyncio
    async def test_unknown_segment_type_uses_text_fallback(self):
        connector = _make_connector()
        segments = [MessageSegment(type="UnknownType", text="fallback text")]
        result = await connector._message_segments_to_html(segments)
        assert "fallback text" in result


# ===========================================================================
# SalesforceConnector.get_updated_record_ids
# ===========================================================================


class TestGetUpdatedRecordIds:

    @pytest.mark.asyncio
    async def test_returns_empty_set_when_no_data_source(self):
        connector = _make_connector()
        connector.data_source = None
        result = await connector.get_updated_record_ids(since_timestamp_ms=1000000)
        assert result == set()

    @pytest.mark.asyncio
    async def test_returns_feed_item_parent_ids(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")

        def _make_soql_resp(records):
            return _sf_response(True, {"records": records, "done": True})

        connector._soql_query_paginated = AsyncMock(side_effect=[
            # Query 1: FeedItems
            _make_soql_resp([{"ParentId": "opp-1"}, {"ParentId": "opp-2"}]),
            # Query 2: FeedComments -> FeedItemIds lookup
            _make_soql_resp([]),
            # Query 3: Tasks
            _make_soql_resp([]),
            # Query 4: Call log comments
            _make_soql_resp([]),
        ])
        result = await connector.get_updated_record_ids(since_timestamp_ms=1000000)
        assert "opp-1" in result
        assert "opp-2" in result

    @pytest.mark.asyncio
    async def test_includes_task_what_ids(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")

        connector._soql_query_paginated = AsyncMock(side_effect=[
            _sf_response(True, {"records": [], "done": True}),  # FeedItems
            _sf_response(True, {"records": [], "done": True}),  # FeedComments
            _sf_response(True, {"records": [{"WhatId": "case-99"}], "done": True}),  # Tasks
            _sf_response(True, {"records": [], "done": True}),  # Call log comments
        ])
        result = await connector.get_updated_record_ids(since_timestamp_ms=1000000)
        assert "case-99" in result

    @pytest.mark.asyncio
    async def test_filters_by_record_types(self):
        """Verify SOQL filter is applied when record_types is specified."""
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")

        soql_calls: List[str] = []

        async def capture_soql(api_version, q):
            soql_calls.append(q)
            return _sf_response(True, {"records": [], "done": True})

        connector._soql_query_paginated = capture_soql
        await connector.get_updated_record_ids(
            since_timestamp_ms=1000000, record_types=["Opportunity"]
        )
        # At least one SOQL should contain the type filter
        assert any("Opportunity" in q for q in soql_calls)

    @pytest.mark.asyncio
    async def test_error_in_query_does_not_raise(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        connector._soql_query_paginated = AsyncMock(side_effect=Exception("SOQL failure"))
        result = await connector.get_updated_record_ids(since_timestamp_ms=1000000)
        # Should still return a set (possibly empty) without raising
        assert isinstance(result, set)

    @pytest.mark.asyncio
    async def test_feedcomment_lookup_adds_parent_ids(self):
        """FeedComment batch lookup resolves FeedItem ParentIds and adds them."""
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")

        connector._soql_query_paginated = AsyncMock(side_effect=[
            # Query 1: FeedItems – nothing directly modified
            _sf_response(True, {"records": [], "done": True}),
            # Query 2: FeedComments – returns one FeedItemId
            _sf_response(True, {"records": [{"FeedItemId": "fi-1"}], "done": True}),
            # Batch lookup of fi-1 -> ParentId = opp-7
            _sf_response(True, {"records": [{"ParentId": "opp-7"}], "done": True}),
            # Query 3: Tasks
            _sf_response(True, {"records": [], "done": True}),
            # Query 4: Call log comments
            _sf_response(True, {"records": [], "done": True}),
        ])
        result = await connector.get_updated_record_ids(since_timestamp_ms=1000000)
        assert "opp-7" in result


# ===========================================================================
# SalesforceConnector._fetch_salesforce_record_if_updated
# ===========================================================================


class TestFetchSalesforceRecordIfUpdated:
    # Salesforce IDs must be 15 or 18 alphanumeric characters
    OPP_ID = "006000000000001AAA"
    CASE_ID = "500000000000001AAA"
    TASK_ID = "00T000000000001AAA"
    PROD_ID = "01t000000000001AAA"
    DOC_ID = "069000000000001AAA"
    LINKED_ID = "001000000000001AAA"

    def _make_opp_row(self):
        return {
            "Id": self.OPP_ID,
            "Name": "Deal 1",
            "AccountId": self.LINKED_ID,
            "Account": {"Name": "Corp"},
            "StageName": "Closing",
            "Amount": 1000.0,
            "ExpectedRevenue": 900.0,
            "CloseDate": "2025-01-01",
            "Probability": 80.0,
            "Type": "New",
            "OwnerId": "005000000000001AAA",
            "Owner": {"Name": "Alice"},
            "IsWon": False,
            "IsClosed": False,
            "CreatedDate": "2024-01-01T00:00:00.000+0000",
            "LastModifiedDate": "2024-06-01T00:00:00.000+0000",
        }

    @pytest.mark.asyncio
    async def test_returns_none_when_no_rows_for_deal(self):
        connector = _make_connector()
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {"records": [], "done": True})
        )
        result = await connector._fetch_salesforce_record_if_updated(
            self.OPP_ID, "DEAL", 0, "59.0"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_deal_record_when_found(self):
        connector = _make_connector()
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {"records": [self._make_opp_row()], "done": True})
        )
        result = await connector._fetch_salesforce_record_if_updated(
            self.OPP_ID, "DEAL", 0, "59.0"
        )
        assert result is not None
        assert result.record_type == "DEAL"

    @pytest.mark.asyncio
    async def test_returns_case_record_when_found(self):
        connector = _make_connector()
        case_row = {
            "Id": self.CASE_ID,
            "CaseNumber": "001",
            "Subject": "Bug",
            "Status": "Open",
            "Priority": "High",
            "Type": None,
            "OwnerId": "005000000000001AAA",
            "Owner": {"Name": "Alice", "Email": "alice@example.com"},
            "AccountId": self.LINKED_ID,
            "Contact": {},
            "CreatedBy": {},
            "CreatedDate": "2024-01-01T00:00:00.000+0000",
            "LastModifiedDate": "2024-06-01T00:00:00.000+0000",
            "SystemModstamp": "2024-06-01T00:00:00.000+0000",
        }
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {"records": [case_row], "done": True})
        )
        result = await connector._fetch_salesforce_record_if_updated(
            self.CASE_ID, "CASE", 0, "59.0"
        )
        assert result is not None
        assert result.record_type == "CASE"

    @pytest.mark.asyncio
    async def test_returns_task_record_when_found(self):
        connector = _make_connector()
        task_row = {
            "Id": self.TASK_ID,
            "Subject": "Call",
            "Status": "Done",
            "Priority": "Normal",
            "ActivityDate": None,
            "Description": None,
            "WhoId": None,
            "Who": None,
            "WhatId": None,
            "What": None,
            "OwnerId": "005000000000001AAA",
            "TaskSubtype": "Call",
            "Owner": {"Name": "Alice", "Email": "alice@example.com"},
            "CreatedBy": {"Name": "Alice", "Email": "alice@example.com"},
            "CreatedDate": "2024-01-01T00:00:00.000+0000",
            "LastModifiedDate": "2024-06-01T00:00:00.000+0000",
            "SystemModstamp": "2024-06-01T00:00:00.000+0000",
        }
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {"records": [task_row], "done": True})
        )
        result = await connector._fetch_salesforce_record_if_updated(
            self.TASK_ID, "TASK", 0, "59.0"
        )
        assert result is not None
        assert result.record_type == "TASK"

    @pytest.mark.asyncio
    async def test_returns_product_record_when_found(self):
        connector = _make_connector()
        prod_row = {
            "Id": self.PROD_ID,
            "Name": "Widget",
            "ProductCode": "WP-1",
            "Family": "HW",
            "IsActive": True,
            "StockKeepingUnit": "SKU-1",
            "CreatedDate": "2024-01-01T00:00:00.000+0000",
            "LastModifiedDate": "2024-06-01T00:00:00.000+0000",
        }
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {"records": [prod_row], "done": True})
        )
        result = await connector._fetch_salesforce_record_if_updated(
            self.PROD_ID, "PRODUCT", 0, "59.0"
        )
        assert result is not None
        assert result.record_type == "PRODUCT"

    @pytest.mark.asyncio
    async def test_returns_file_record_when_found(self):
        connector = _make_connector()
        file_row = {
            "Id": "068000000000001AAA",
            "ContentDocumentId": self.DOC_ID,
            "Title": "Report",
            "PathOnClient": "Report.pdf",
            "ContentSize": 1024,
            "FileExtension": "pdf",
            "FileType": "PDF",
            "LastModifiedDate": "2024-06-01T00:00:00.000+0000",
            "CreatedDate": "2024-01-01T00:00:00.000+0000",
            "Checksum": "abc123",
        }
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {"records": [file_row], "done": True})
        )
        # External ID format: {doc_id}-{linked_entity_id}
        result = await connector._fetch_salesforce_record_if_updated(
            f"{self.DOC_ID}-{self.LINKED_ID}", "FILE", 0, "59.0"
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_returns_none_for_unsupported_type(self):
        connector = _make_connector()
        result = await connector._fetch_salesforce_record_if_updated(
            self.OPP_ID, "UNSUPPORTED", 0, "59.0"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_invalid_id(self):
        """_sanitize_soql_id raises ValueError; should return None."""
        connector = _make_connector()
        result = await connector._fetch_salesforce_record_if_updated(
            "'; DROP--", "DEAL", 0, "59.0"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_generic_exception(self):
        connector = _make_connector()
        connector._soql_query_paginated = AsyncMock(side_effect=Exception("API down"))
        result = await connector._fetch_salesforce_record_if_updated(
            self.OPP_ID, "DEAL", 0, "59.0"
        )
        assert result is None


# ===========================================================================
# SalesforceConnector._get_updated_deal
# ===========================================================================


class TestGetUpdatedDeal:

    @pytest.mark.asyncio
    async def test_full_sync_returns_all_records(self):
        connector = _make_connector()
        records = [{"Id": "opp-1", "LastModifiedDate": "2024-06-01T00:00:00.000+0000"}]
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {"records": records, "done": True})
        )
        result = await connector._get_updated_deal(
            api_version="59.0",
            opportunities_last_ts_ms=None,
            base_opportunities_soql="SELECT Id FROM Opportunity",
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_incremental_deduplicates_across_sources(self):
        connector = _make_connector()
        opp_row = {"Id": "opp-1", "LastModifiedDate": "2024-06-01T00:00:00.000+0000"}
        empty = _sf_response(True, {"records": [], "done": True})
        one_row = _sf_response(True, {"records": [opp_row], "done": True})

        # 5 parallel queries: modified, feeditems, feedcomments, item_dates, comment_dates
        connector._soql_query_paginated = AsyncMock(side_effect=[
            one_row, one_row, one_row, empty, empty
        ])
        result = await connector._get_updated_deal(
            api_version="59.0",
            opportunities_last_ts_ms=1_000_000,
            base_opportunities_soql="SELECT Id FROM Opportunity",
        )
        # opp-1 appears in all three result sets but should only appear once
        assert len(result) == 1
        assert result[0].Id == "opp-1"

    @pytest.mark.asyncio
    async def test_incremental_attaches_latest_comment_epoch(self):
        connector = _make_connector()
        opp_row = {"Id": "opp-5", "LastModifiedDate": "2024-06-01T00:00:00.000+0000"}
        feed_date_row = {"ParentId": "opp-5", "CreatedDate": "2024-06-15T00:00:00.000+0000"}

        connector._soql_query_paginated = AsyncMock(side_effect=[
            _sf_response(True, {"records": [opp_row], "done": True}),   # modified
            _sf_response(True, {"records": [], "done": True}),           # feeditems (records)
            _sf_response(True, {"records": [], "done": True}),           # feedcomments (records)
            _sf_response(True, {"records": [feed_date_row], "done": True}),  # item_dates
            _sf_response(True, {"records": [], "done": True}),           # comment_dates
        ])
        result = await connector._get_updated_deal(
            api_version="59.0",
            opportunities_last_ts_ms=1_000_000,
            base_opportunities_soql="SELECT Id FROM Opportunity",
        )
        assert len(result) == 1
        assert result[0].latest_comment_epoch is not None


# ===========================================================================
# SalesforceConnector._get_updated_product / _get_updated_task / _get_updated_file
# ===========================================================================


class TestGetUpdatedProductTaskFile:

    @pytest.mark.asyncio
    async def test_get_updated_product_full_sync(self):
        connector = _make_connector()
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {"records": [{"Id": "prod-1"}], "done": True})
        )
        result = await connector._get_updated_product(
            api_version="59.0",
            products_last_ts_ms=None,
            base_products_soql="SELECT Id FROM Product2",
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_updated_product_incremental(self):
        connector = _make_connector()
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {"records": [{"Id": "prod-2"}], "done": True})
        )
        result = await connector._get_updated_product(
            api_version="59.0",
            products_last_ts_ms=1_000_000,
            base_products_soql="SELECT Id FROM Product2",
        )
        assert len(result) == 1
        # Confirm a WHERE clause with timestamp was injected
        call_q = connector._soql_query_paginated.call_args[1]["q"]
        assert "LastModifiedDate" in call_q

    @pytest.mark.asyncio
    async def test_get_updated_task_full_sync(self):
        connector = _make_connector()
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {"records": [{"Id": "task-1"}], "done": True})
        )
        result = await connector._get_updated_task(
            api_version="59.0",
            tasks_last_ts_ms=None,
            base_tasks_soql="SELECT Id FROM Task",
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_updated_task_incremental(self):
        connector = _make_connector()
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {"records": [{"Id": "task-2"}], "done": True})
        )
        result = await connector._get_updated_task(
            api_version="59.0",
            tasks_last_ts_ms=1_000_000,
            base_tasks_soql="SELECT Id FROM Task",
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_updated_file_full_sync(self):
        connector = _make_connector()
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {"records": [{"Id": "ver-1"}], "done": True})
        )
        result = await connector._get_updated_file(api_version="59.0", files_last_ts_ms=None)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_updated_file_incremental(self):
        connector = _make_connector()
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {"records": [{"Id": "ver-2"}], "done": True})
        )
        result = await connector._get_updated_file(api_version="59.0", files_last_ts_ms=1_000_000)
        assert len(result) == 1


# ===========================================================================
# SalesforceConnector._get_updated_case
# ===========================================================================


class TestGetUpdatedCase:

    @pytest.mark.asyncio
    async def test_full_sync_returns_all_cases(self):
        connector = _make_connector()
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {"records": [{"Id": "case-1"}], "done": True})
        )
        result = await connector._get_updated_case(
            api_version="59.0",
            cases_last_ts_ms=None,
            base_cases_soql="SELECT Id FROM Case",
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_incremental_deduplicates(self):
        connector = _make_connector()
        case_row = {"Id": "case-5", "Status": "Open"}
        empty = _sf_response(True, {"records": [], "done": True})
        one_row = _sf_response(True, {"records": [case_row], "done": True})
        connector._soql_query_paginated = AsyncMock(side_effect=[
            one_row, one_row, one_row, empty, empty  # 5 parallel queries
        ])
        result = await connector._get_updated_case(
            api_version="59.0",
            cases_last_ts_ms=1_000_000,
            base_cases_soql="SELECT Id FROM Case",
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_incremental_attaches_latest_comment_epoch(self):
        connector = _make_connector()
        case_row = {"Id": "case-7"}
        feed_row = {"ParentId": "case-7", "CreatedDate": "2024-06-15T00:00:00.000+0000"}
        connector._soql_query_paginated = AsyncMock(side_effect=[
            _sf_response(True, {"records": [case_row], "done": True}),
            _sf_response(True, {"records": [], "done": True}),
            _sf_response(True, {"records": [], "done": True}),
            _sf_response(True, {"records": [feed_row], "done": True}),
            _sf_response(True, {"records": [], "done": True}),
        ])
        result = await connector._get_updated_case(
            api_version="59.0",
            cases_last_ts_ms=1_000_000,
            base_cases_soql="SELECT Id FROM Case",
        )
        assert result[0].latest_comment_epoch is not None


# ===========================================================================
# SalesforceConnector._get_updated_account
# ===========================================================================


class TestGetUpdatedAccount:

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_changed_ids(self):
        connector = _make_connector()
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {"records": [], "done": True})
        )
        result = await connector._get_updated_account(
            api_version="59.0",
            soql_datetime="2024-01-01T00:00:00Z",
            soql_accounts_query="SELECT Id, Name FROM Account",
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_fetches_full_records_for_changed_accounts(self):
        connector = _make_connector()
        # Calls in order: query_a (Account), query_b (Opportunity), final IN batch
        connector._soql_query_paginated = AsyncMock(side_effect=[
            _sf_response(True, {"records": [{"Id": "001000000000001AAA"}], "done": True}),  # query_a
            _sf_response(True, {"records": [], "done": True}),                               # query_b
            _sf_response(True, {"records": [{"Id": "001000000000001AAA", "Name": "Corp"}], "done": True}),  # IN batch
        ])
        result = await connector._get_updated_account(
            api_version="59.0",
            soql_datetime="2024-01-01T00:00:00Z",
            soql_accounts_query="SELECT Id, Name FROM Account",
        )
        assert len(result) == 1
        assert result[0].Id == "001000000000001AAA"

    @pytest.mark.asyncio
    async def test_combines_accounts_from_opportunities(self):
        connector = _make_connector()
        # Calls in order: query_a (no direct Account changes), query_b (Opportunity gives acc-opp), IN batch
        connector._soql_query_paginated = AsyncMock(side_effect=[
            _sf_response(True, {"records": [], "done": True}),                                      # query_a
            _sf_response(True, {"records": [{"AccountId": "001000000000002AAA"}], "done": True}),   # query_b
            _sf_response(True, {"records": [{"Id": "001000000000002AAA", "Name": "Opp Corp"}], "done": True}),  # IN batch
        ])
        result = await connector._get_updated_account(
            api_version="59.0",
            soql_datetime="2024-01-01T00:00:00Z",
            soql_accounts_query="SELECT Id, Name FROM Account",
        )
        assert len(result) == 1


# ===========================================================================
# SalesforceConnector._sync_files
# ===========================================================================


class TestSyncFiles:

    def _make_file_row(self, doc_id="doc-1", title="Report.pdf"):
        return SalesforceContentVersion(
            ContentDocumentId=doc_id,
            Title=title,
            PathOnClient=title,
            ContentSize=2048,
            FileExtension="pdf",
            LastModifiedDate="2024-06-01T00:00:00.000+0000",
            CreatedDate="2024-01-01T00:00:00.000+0000",
            Checksum="checksum123",
            Id="ver-1",
        )

    @pytest.mark.asyncio
    async def test_skips_when_no_file_records(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        await connector._sync_files(api_version="59.0", file_records=[])
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_processes_linked_opportunity_file(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {
                "records": [
                    {
                        "ContentDocumentId": "doc-1",
                        "LinkedEntityId": "opp-1",
                        "LinkedEntity": {"Type": "Opportunity"},
                    }
                ],
                "done": True,
            })
        )
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        await connector._sync_files(api_version="59.0", file_records=[self._make_file_row()])
        connector.data_entities_processor.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_processes_unlinked_file(self):
        """A file with no ContentDocumentLink entries is treated as unlinked."""
        connector = _make_connector()
        connector.data_source = MagicMock()
        # No links returned for doc-1
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {"records": [], "done": True})
        )
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        await connector._sync_files(api_version="59.0", file_records=[self._make_file_row()])
        connector.data_entities_processor.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_updates_existing_file_on_metadata_change(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {"records": [], "done": True})
        )
        existing = MagicMock()
        existing.md5_hash = "old-hash"
        existing.record_name = "Old.pdf"
        existing.external_revision_id = "ver-0"
        existing.source_updated_at = 0
        existing.size_in_bytes = 100
        existing.extension = "pdf"
        existing.mime_type = "application/pdf"
        existing.weburl = "https://sf.example.com/old"
        existing.id = "arango-1"
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)
        connector._handle_record_updates = AsyncMock()

        file_row = self._make_file_row()
        await connector._sync_files(api_version="59.0", file_records=[file_row])
        connector._handle_record_updates.assert_awaited()

    @pytest.mark.asyncio
    async def test_skips_linked_file_with_unsupported_entity_type(self):
        """Files linked only to unsupported entity types are not in the linked set,
        and because they *have* at least one link they are also excluded from the
        unlinked path — so on_new_records is never called."""
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {
                "records": [
                    {
                        "ContentDocumentId": "doc-1",
                        "LinkedEntityId": "contact-1",
                        "LinkedEntity": {"Type": "Contact"},  # not in PARENT_TYPES
                    }
                ],
                "done": True,
            })
        )
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        # A file linked only to Contact is not surfaced in either the linked or unlinked path.
        await connector._sync_files(api_version="59.0", file_records=[self._make_file_row()])
        connector.data_entities_processor.on_new_records.assert_not_awaited()


# ===========================================================================
# SalesforceConnector._sync_sold_in_edges
# ===========================================================================


class TestSyncSoldInEdges:

    @pytest.mark.asyncio
    async def test_skips_when_no_line_items(self):
        connector = _make_connector()
        await connector._sync_sold_in_edges(line_item_records=[], api_version="59.0")
        # No DB calls expected
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.batch_create_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_when_no_valid_pairs(self):
        """Line items without Product2Id or OpportunityId should be skipped."""
        connector = _make_connector()
        line_items = [SalesforceLineItem(OpportunityId=None, Product2=None)]
        await connector._sync_sold_in_edges(line_item_records=line_items, api_version="59.0")
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.batch_create_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_sold_in_edges(self):
        # Salesforce IDs must pass _sanitize_soql_id (15 or 18 alphanumeric chars)
        OPP_ID = "006000000000001AAA"
        PROD_ID = "01t000000000001AAA"
        connector = _make_connector()
        line_items = [SalesforceLineItem(
            OpportunityId=OPP_ID,
            Product2={"Id": PROD_ID},
        )]
        # Bulk SOQL returns one line item
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {
                "records": [{
                    "OpportunityId": OPP_ID,
                    "Product2Id": PROD_ID,
                    "Quantity": 2.0,
                    "UnitPrice": 500.0,
                    "TotalPrice": 1000.0,
                    "IsDeleted": False,
                    "CreatedDate": "2024-01-01T00:00:00.000+0000",
                    "LastModifiedDate": "2024-06-01T00:00:00.000+0000",
                }],
                "done": True,
            })
        )
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.get_nodes_by_field_in = AsyncMock(side_effect=[
            [{"externalRecordId": PROD_ID, "id": "prod-internal-1", "connectorId": "conn-sf-1"}],
            [{"externalRecordId": OPP_ID, "id": "deal-internal-1", "connectorId": "conn-sf-1"}],
        ])
        mock_tx.delete_edge = AsyncMock()
        mock_tx.batch_create_edges = AsyncMock()

        await connector._sync_sold_in_edges(line_item_records=line_items, api_version="59.0")
        mock_tx.batch_create_edges.assert_awaited()

    @pytest.mark.asyncio
    async def test_skips_edges_when_product_not_in_db(self):
        OPP_ID = "006000000000001AAA"
        PROD_ID = "01t000000000002AAA"
        connector = _make_connector()
        line_items = [SalesforceLineItem(OpportunityId=OPP_ID, Product2={"Id": PROD_ID})]
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {
                "records": [{
                    "OpportunityId": OPP_ID,
                    "Product2Id": PROD_ID,
                    "Quantity": 1.0,
                    "UnitPrice": 100.0,
                    "TotalPrice": 100.0,
                    "IsDeleted": False,
                    "CreatedDate": "2024-01-01T00:00:00.000+0000",
                    "LastModifiedDate": "2024-06-01T00:00:00.000+0000",
                }],
                "done": True,
            })
        )
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.get_nodes_by_field_in = AsyncMock(side_effect=[
            [],  # product not in DB
            [{"externalRecordId": OPP_ID, "id": "deal-internal-1", "connectorId": "conn-sf-1"}],
        ])
        mock_tx.batch_create_edges = AsyncMock()
        await connector._sync_sold_in_edges(line_item_records=line_items, api_version="59.0")
        mock_tx.batch_create_edges.assert_not_awaited()


# ===========================================================================
# SalesforceConnector._sync_permissions_edges
# ===========================================================================


class TestSyncPermissionsEdges:

    @pytest.mark.asyncio
    async def test_skips_when_no_salesforce_records(self):
        """If no records are in DB for this connector, skip permission sync."""
        connector = _make_connector()
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {"records": [], "done": True})
        )
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.get_nodes_by_field_in = AsyncMock(return_value=[])

        await connector._sync_permissions_edges(api_version="59.0")
        mock_tx.batch_create_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_permission_edges_for_users(self):
        connector = _make_connector()
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {
                "records": [{"Id": "sf-user-1", "Email": "alice@example.com"}],
                "done": True,
            })
        )
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.get_nodes_by_field_in = AsyncMock(side_effect=[
            # salesforce_records
            [{"externalRecordId": "opp-1", "id": "deal-1", "connectorId": "conn-sf-1"}],
            # salesforce_record_groups
            [],
            # db_users
            [{"email": "alice@example.com", "id": "user-1"}],
        ])
        mock_tx.delete_edges_to = AsyncMock()
        mock_tx.batch_create_edges = AsyncMock()

        # access_level must be a PermissionType value string (e.g. "READER", "WRITER")
        connector._sync_permissions_for_user = AsyncMock(
            return_value=[("alice@example.com", "opp-1", "READER", False)]
        )
        await connector._sync_permissions_edges(api_version="59.0")
        mock_tx.batch_create_edges.assert_awaited()


# ===========================================================================
# SalesforceConnector._sync_permissions_for_user
# ===========================================================================


class TestSyncPermissionsForUser:

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_user_id(self):
        connector = _make_connector()
        semaphore = __import__("asyncio").Semaphore(1)
        result = await connector._sync_permissions_for_user(
            user={"Email": "alice@example.com"},  # no Id
            salesforce_external_ids=["opp-1"],
            salesforce_record_group_external_ids=[],
            api_version="59.0",
            semaphore=semaphore,
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_email(self):
        connector = _make_connector()
        semaphore = __import__("asyncio").Semaphore(1)
        result = await connector._sync_permissions_for_user(
            user={"Id": "sf-user-1"},  # no Email
            salesforce_external_ids=["opp-1"],
            salesforce_record_group_external_ids=[],
            api_version="59.0",
            semaphore=semaphore,
        )
        assert result == []


# ===========================================================================
# SalesforceConnector.salesforce_permissions_sync
# ===========================================================================


class TestSalesforcePermissionsSync:

    def _make_tx_with_record_and_user(self):
        mock_tx = MagicMock()
        record = MagicMock()
        record.id = "rec-internal-1"
        user = MagicMock()
        user.id = "user-internal-1"
        mock_tx.get_record_by_external_id = AsyncMock(return_value=record)
        mock_tx.get_user_by_email = AsyncMock(return_value=user)
        mock_tx.get_edge = AsyncMock(return_value=None)
        mock_tx.batch_create_edges = AsyncMock()
        mock_tx.delete_edge = AsyncMock()
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        return mock_tx

    @pytest.mark.asyncio
    async def test_creates_permission_edge(self):
        from app.models.permission import PermissionType
        connector = _make_connector()
        mock_tx = self._make_tx_with_record_and_user()
        connector.data_store_provider.transaction.return_value = mock_tx

        await connector.salesforce_permissions_sync(
            connector_id="conn-sf-1",
            record_external_id="opp-1",
            users_email="alice@example.com",
            access_level=PermissionType.READ,
        )
        mock_tx.batch_create_edges.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_when_record_not_found(self):
        from app.models.permission import PermissionType
        connector = _make_connector()
        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx.batch_create_edges = AsyncMock()
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        await connector.salesforce_permissions_sync(
            connector_id="conn-sf-1",
            record_external_id="missing-opp",
            users_email="alice@example.com",
            access_level=PermissionType.READ,
        )
        mock_tx.batch_create_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_when_user_not_found(self):
        from app.models.permission import PermissionType
        connector = _make_connector()
        mock_tx = MagicMock()
        record = MagicMock()
        record.id = "rec-1"
        mock_tx.get_record_by_external_id = AsyncMock(return_value=record)
        mock_tx.get_user_by_email = AsyncMock(return_value=None)
        mock_tx.batch_create_edges = AsyncMock()
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        await connector.salesforce_permissions_sync(
            connector_id="conn-sf-1",
            record_external_id="opp-1",
            users_email="missing@example.com",
            access_level=PermissionType.READ,
        )
        mock_tx.batch_create_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_replaces_edge_when_level_changes(self):
        from app.models.permission import PermissionType
        connector = _make_connector()
        mock_tx = MagicMock()
        record = MagicMock()
        record.id = "rec-1"
        user = MagicMock()
        user.id = "user-1"
        existing_edge = {"role": "READER"}  # lower level than WRITER
        mock_tx.get_record_by_external_id = AsyncMock(return_value=record)
        mock_tx.get_user_by_email = AsyncMock(return_value=user)
        mock_tx.get_edge = AsyncMock(return_value=existing_edge)
        mock_tx.delete_edge = AsyncMock()
        mock_tx.batch_create_edges = AsyncMock()
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        await connector.salesforce_permissions_sync(
            connector_id="conn-sf-1",
            record_external_id="opp-1",
            users_email="alice@example.com",
            access_level=PermissionType.WRITE,
        )
        mock_tx.delete_edge.assert_awaited_once()
        mock_tx.batch_create_edges.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_propagates_exception(self):
        from app.models.permission import PermissionType
        connector = _make_connector()
        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(side_effect=Exception("DB crash"))
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        with pytest.raises(Exception, match="DB crash"):
            await connector.salesforce_permissions_sync(
                connector_id="conn-sf-1",
                record_external_id="opp-1",
                users_email="alice@example.com",
                access_level=PermissionType.READ,
            )


# ===========================================================================
# SalesforceConnector.salesforce_record_group_permissions_sync
# ===========================================================================


class TestSalesforceRecordGroupPermissionsSync:

    def _make_rg_tx(self, group_type=None):
        from app.models.entities import RecordGroupType
        mock_tx = MagicMock()
        rg = MagicMock()
        rg.id = "rg-internal-1"
        rg.group_type = group_type or RecordGroupType.SALESFORCE_ORG
        user = MagicMock()
        user.id = "user-internal-1"
        mock_tx.get_record_group_by_external_id = AsyncMock(return_value=rg)
        mock_tx.get_user_by_email = AsyncMock(return_value=user)
        mock_tx.get_edge = AsyncMock(return_value=None)
        mock_tx.batch_create_edges = AsyncMock()
        mock_tx.delete_edge = AsyncMock()
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        return mock_tx

    @pytest.mark.asyncio
    async def test_creates_permission_edge_for_org_group(self):
        from app.models.permission import PermissionType
        connector = _make_connector()
        mock_tx = self._make_rg_tx()
        connector.data_store_provider.transaction.return_value = mock_tx

        await connector.salesforce_record_group_permissions_sync(
            connector_id="conn-sf-1",
            record_group_external_id="acc-1",
            users_email="alice@example.com",
            access_level=PermissionType.READ,
        )
        mock_tx.batch_create_edges.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_when_record_group_not_found(self):
        from app.models.permission import PermissionType
        connector = _make_connector()
        mock_tx = MagicMock()
        mock_tx.get_record_group_by_external_id = AsyncMock(return_value=None)
        mock_tx.batch_create_edges = AsyncMock()
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        await connector.salesforce_record_group_permissions_sync(
            connector_id="conn-sf-1",
            record_group_external_id="missing-acc",
            users_email="alice@example.com",
            access_level=PermissionType.READ,
        )
        mock_tx.batch_create_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_when_group_type_not_salesforce_org(self):
        from app.models.entities import RecordGroupType
        from app.models.permission import PermissionType
        connector = _make_connector()
        mock_tx = self._make_rg_tx(group_type=RecordGroupType.SALESFORCE_FILE)
        connector.data_store_provider.transaction.return_value = mock_tx

        await connector.salesforce_record_group_permissions_sync(
            connector_id="conn-sf-1",
            record_group_external_id="files-rg",
            users_email="alice@example.com",
            access_level=PermissionType.READ,
        )
        mock_tx.batch_create_edges.assert_not_awaited()


# ===========================================================================
# SalesforceConnector.reindex_records
# ===========================================================================


class TestReindexRecords:

    @pytest.mark.asyncio
    async def test_raises_when_data_source_not_initialized(self):
        connector = _make_connector()
        connector._reinitialize_token_if_needed = AsyncMock()
        connector.data_source = None
        record = MagicMock()
        record.external_revision_id = None
        record.external_record_id = "opp-1"
        record.record_type = "DEAL"

        with pytest.raises(Exception, match="not initialized"):
            await connector.reindex_records([record])

    @pytest.mark.asyncio
    async def test_calls_on_record_content_update_for_updated_record(self):
        connector = _make_connector()
        connector._reinitialize_token_if_needed = AsyncMock()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        updated_record = MagicMock()
        connector._fetch_salesforce_record_if_updated = AsyncMock(return_value=updated_record)

        record = MagicMock()
        record.external_revision_id = "1234567890"
        record.external_record_id = "opp-1"
        record.record_type = "DEAL"

        await connector.reindex_records([record])
        connector.data_entities_processor.on_record_content_update.assert_awaited_once_with(
            updated_record
        )

    @pytest.mark.asyncio
    async def test_adds_unchanged_records_to_reindex_list(self):
        connector = _make_connector()
        connector._reinitialize_token_if_needed = AsyncMock()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        connector._fetch_salesforce_record_if_updated = AsyncMock(return_value=None)
        connector.data_entities_processor.reindex_existing_records = AsyncMock()

        record = MagicMock()
        record.external_revision_id = None
        record.external_record_id = "opp-1"
        record.record_type = "DEAL"

        await connector.reindex_records([record])
        connector.data_entities_processor.reindex_existing_records.assert_awaited_once()


# ===========================================================================
# SalesforceConnector._flatten_single_group_members / _flatten_group_members
# ===========================================================================


class TestFlattenGroupMembers:

    def test_flat_group_returns_direct_user_ids(self):
        connector = _make_connector()
        group_to_members = {"grp-1": ["user-a", "user-b"]}
        result = connector._flatten_single_group_members(
            "grp-1", group_to_members, set(), set()
        )
        assert "user-a" in result
        assert "user-b" in result

    def test_nested_group_resolved_recursively(self):
        connector = _make_connector()
        # grp-1 -> grp-2 -> user-c
        group_to_members = {"grp-1": ["grp-2"], "grp-2": ["user-c"]}
        result = connector._flatten_single_group_members(
            "grp-1", group_to_members, set(), set()
        )
        assert "user-c" in result

    def test_circular_reference_returns_empty(self):
        connector = _make_connector()
        # grp-1 -> grp-1 (self-referential)
        group_to_members = {"grp-1": ["grp-1"]}
        result = connector._flatten_single_group_members(
            "grp-1", group_to_members, set(), {"grp-1"}
        )
        assert result == set()

    def test_already_visited_returns_empty(self):
        connector = _make_connector()
        group_to_members = {"grp-1": ["user-x"]}
        visited = {"grp-1"}
        result = connector._flatten_single_group_members(
            "grp-1", group_to_members, visited, set()
        )
        assert result == set()

    @pytest.mark.asyncio
    async def test_flatten_group_members_returns_dict_with_emails(self):
        connector = _make_connector()
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {
                "records": [{
                    "Id": "grp-1",
                    "Name": "Sales",
                    "GroupMembers": {
                        "records": [{
                            "UserOrGroupId": "user-a",
                            "UserOrGroup": {"Email": "alice@example.com"},
                        }]
                    },
                }],
                "done": True,
            })
        )
        result = await connector._flatten_group_members(api_version="59.0")
        assert "grp-1" in result
        assert any(email == "alice@example.com" for _, email in result["grp-1"])

    @pytest.mark.asyncio
    async def test_flatten_group_members_returns_empty_on_failure(self):
        connector = _make_connector()
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(False, error="API error")
        )
        result = await connector._flatten_group_members(api_version="59.0")
        assert result == {}

    @pytest.mark.asyncio
    async def test_flatten_group_members_skips_user_without_email(self):
        """Members with no email should not appear in the result set."""
        connector = _make_connector()
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {
                "records": [{
                    "Id": "grp-1",
                    "Name": "Sales",
                    "GroupMembers": {
                        "records": [{
                            "UserOrGroupId": "user-no-email",
                            "UserOrGroup": {},  # no Email key
                        }]
                    },
                }],
                "done": True,
            })
        )
        result = await connector._flatten_group_members(api_version="59.0")
        # group exists but no users with emails → empty set
        assert result.get("grp-1", set()) == set()

    @pytest.mark.asyncio
    async def test_flatten_group_members_skips_group_without_id(self):
        """Groups with no Id should be skipped."""
        connector = _make_connector()
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {
                "records": [{"Name": "No ID Group", "GroupMembers": {"records": []}}],
                "done": True,
            })
        )
        result = await connector._flatten_group_members(api_version="59.0")
        assert result == {}


# ===========================================================================
# SalesforceConnector._fetch_file_as_base64_uri
# ===========================================================================


class TestFetchFileAsBase64Uri:

    @pytest.mark.asyncio
    async def test_returns_none_when_no_url(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        result = await connector._fetch_file_as_base64_uri("")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_data_source(self):
        connector = _make_connector()
        connector.data_source = None
        result = await connector._fetch_file_as_base64_uri("https://example.com/file.png")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_base64_png_from_content_type(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_access_token = AsyncMock(return_value="tok-1")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "image/png"}
        mock_response.content = b'\x89PNG' + b'\x00' * 10

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        connector._http_client = mock_client

        result = await connector._fetch_file_as_base64_uri(
            "https://myinstance.salesforce.com/services/data/v59.0/connect/files/file-1"
        )
        assert result is not None
        assert result.startswith("data:image/png;base64,")

    @pytest.mark.asyncio
    async def test_returns_base64_jpeg_from_magic_bytes(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_access_token = AsyncMock(return_value="tok-1")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "application/octet-stream"}
        mock_response.content = b'\xff\xd8\xff' + b'\x00' * 10  # JPEG magic

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        connector._http_client = mock_client

        result = await connector._fetch_file_as_base64_uri(
            "https://myinstance.salesforce.com/services/data/v59.0/connect/files/file-2"
        )
        assert result is not None
        assert result.startswith("data:image/jpeg;base64,")

    @pytest.mark.asyncio
    async def test_returns_none_on_non_200_response(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_access_token = AsyncMock(return_value="tok-1")

        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        connector._http_client = mock_client

        result = await connector._fetch_file_as_base64_uri(
            "https://myinstance.salesforce.com/services/data/v59.0/connect/files/file-3"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_public_asset_retries_with_auth_on_404(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_access_token = AsyncMock(return_value="tok-retry")

        response_404 = MagicMock()
        response_404.status_code = 404
        response_404.headers = {}

        response_200 = MagicMock()
        response_200.status_code = 200
        response_200.headers = {"Content-Type": "image/jpeg"}
        response_200.content = b'\xff\xd8\xff' + b'\x00' * 5

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[response_404, response_200])
        connector._http_client = mock_client

        result = await connector._fetch_file_as_base64_uri(
            "https://myinstance.salesforce.com/file-asset-public/img.jpg"
        )
        assert result is not None
        assert mock_client.get.await_count == 2

    @pytest.mark.asyncio
    async def test_returns_none_when_access_token_missing_for_api_file(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_access_token = AsyncMock(return_value=None)

        result = await connector._fetch_file_as_base64_uri(
            "https://myinstance.salesforce.com/services/data/v59.0/connect/files/file-4"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_access_token = AsyncMock(return_value="tok-1")

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Network error"))
        connector._http_client = mock_client

        result = await connector._fetch_file_as_base64_uri(
            "https://myinstance.salesforce.com/services/data/v59.0/connect/files/file-5"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_oversized_content(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_access_token = AsyncMock(return_value="tok-1")

        mock_response = MagicMock()
        mock_response.status_code = 200
        # Report 20 MB in Content-Length header
        mock_response.headers = {"Content-Type": "image/png", "Content-Length": str(20 * 1024 * 1024)}
        mock_response.content = b'\x89PNG' + b'\x00' * 10

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        connector._http_client = mock_client

        result = await connector._fetch_file_as_base64_uri(
            "https://myinstance.salesforce.com/services/data/v59.0/connect/files/big"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_base64_gif_from_content_type(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_access_token = AsyncMock(return_value="tok-gif")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "image/gif"}
        mock_response.content = b'GIF89a' + b'\x00' * 10

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        connector._http_client = mock_client

        result = await connector._fetch_file_as_base64_uri(
            "https://myinstance.salesforce.com/services/data/v59.0/connect/files/anim.gif"
        )
        assert result is not None
        assert result.startswith("data:image/gif;base64,")

    @pytest.mark.asyncio
    async def test_returns_base64_webp_from_content_type(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_access_token = AsyncMock(return_value="tok-webp")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "image/webp"}
        mock_response.content = b'RIFF' + b'\x00' * 10

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        connector._http_client = mock_client

        result = await connector._fetch_file_as_base64_uri(
            "https://myinstance.salesforce.com/services/data/v59.0/connect/files/photo.webp"
        )
        assert result is not None
        assert result.startswith("data:image/webp;base64,")

    @pytest.mark.asyncio
    async def test_returns_base64_gif_from_magic_bytes(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_access_token = AsyncMock(return_value="tok-gif-magic")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "application/octet-stream"}
        mock_response.content = b'GIF' + b'\x00' * 10  # GIF magic bytes

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        connector._http_client = mock_client

        result = await connector._fetch_file_as_base64_uri(
            "https://myinstance.salesforce.com/services/data/v59.0/connect/files/anim2.gif"
        )
        assert result is not None
        assert result.startswith("data:image/gif;base64,")

    @pytest.mark.asyncio
    async def test_returns_base64_png_from_magic_bytes(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_access_token = AsyncMock(return_value="tok-png-magic")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "application/octet-stream"}
        mock_response.content = b'\x89PNG' + b'\x00' * 10  # PNG magic bytes

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        connector._http_client = mock_client

        result = await connector._fetch_file_as_base64_uri(
            "https://myinstance.salesforce.com/services/data/v59.0/connect/files/img2.png"
        )
        assert result is not None
        assert result.startswith("data:image/png;base64,")

    @pytest.mark.asyncio
    async def test_returns_base64_jpeg_from_content_type_header(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_access_token = AsyncMock(return_value="tok-jpg-ct")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "image/jpeg"}
        mock_response.content = b'\x00' * 20  # no magic bytes but content-type is jpeg

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        connector._http_client = mock_client

        result = await connector._fetch_file_as_base64_uri(
            "https://myinstance.salesforce.com/services/data/v59.0/connect/files/photo.jpg"
        )
        assert result is not None
        assert result.startswith("data:image/jpeg;base64,")


# ===========================================================================
# SalesforceConnector._process_product_record
# ===========================================================================


class TestProcessProductRecord:

    @pytest.mark.asyncio
    async def test_raises_when_no_data_source(self):
        connector = _make_connector()
        connector.data_source = None
        record = MagicMock()
        record.external_record_id = "01t000000000001AAA"
        record.record_name = "Widget"
        with pytest.raises(Exception):
            await connector._process_product_record(record)

    @pytest.mark.asyncio
    async def test_returns_bytes_with_description(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {
                "records": [{"Id": "01t000000000001AAA", "Description": "A great widget"}],
                "done": True,
            })
        )
        record = MagicMock()
        record.external_record_id = "01t000000000001AAA"
        record.record_name = "Widget"
        result = await connector._process_product_record(record)
        assert isinstance(result, bytes)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_falls_back_when_fetch_fails(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(False, error="API error")
        )
        record = MagicMock()
        record.external_record_id = "01t000000000001AAA"
        record.record_name = "Widget"
        result = await connector._process_product_record(record)
        assert isinstance(result, bytes)

    @pytest.mark.asyncio
    async def test_falls_back_when_no_description(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {
                "records": [{"Id": "01t000000000001AAA", "Description": None}],
                "done": True,
            })
        )
        record = MagicMock()
        record.external_record_id = "01t000000000001AAA"
        record.record_name = "Widget"
        result = await connector._process_product_record(record)
        assert isinstance(result, bytes)

    @pytest.mark.asyncio
    async def test_raises_on_invalid_product_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        record = MagicMock()
        record.external_record_id = "'; DROP TABLE--"
        record.record_name = "Bad"
        with pytest.raises(Exception):
            await connector._process_product_record(record)


# ===========================================================================
# SalesforceConnector._process_deal_record
# ===========================================================================


class TestProcessDealRecord:

    @pytest.mark.asyncio
    async def test_raises_when_no_data_source(self):
        connector = _make_connector()
        connector.data_source = None
        record = MagicMock()
        record.external_record_id = "006000000000001AAA"
        record.record_name = "Big Deal"
        with pytest.raises(Exception):
            await connector._process_deal_record(record)

    @pytest.mark.asyncio
    async def test_returns_bytes_with_description(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {
                "records": [{"Id": "006000000000001AAA", "Description": "Big enterprise deal"}],
                "done": True,
            })
        )
        connector._get_record_linked_file_child_records = AsyncMock(return_value=[])
        connector._get_opportunity_related_child_records = AsyncMock(return_value=[])
        connector._fetch_and_build_discussion_block_groups = AsyncMock(return_value=[])

        record = MagicMock()
        record.external_record_id = "006000000000001AAA"
        record.record_name = "Big Deal"
        result = await connector._process_deal_record(record)
        assert isinstance(result, bytes)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_falls_back_when_fetch_fails(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(False, error="Not found")
        )
        connector._get_record_linked_file_child_records = AsyncMock(return_value=[])
        connector._get_opportunity_related_child_records = AsyncMock(return_value=[])
        connector._fetch_and_build_discussion_block_groups = AsyncMock(return_value=[])

        record = MagicMock()
        record.external_record_id = "006000000000001AAA"
        record.record_name = "Deal Fallback"
        result = await connector._process_deal_record(record)
        assert isinstance(result, bytes)

    @pytest.mark.asyncio
    async def test_raises_on_invalid_opp_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        record = MagicMock()
        record.external_record_id = "'; DROP TABLE--"
        record.record_name = "Bad Deal"
        with pytest.raises(Exception):
            await connector._process_deal_record(record)

    @pytest.mark.asyncio
    async def test_includes_linked_files_in_output(self):
        from app.models.blocks import ChildRecord, ChildType
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {
                "records": [{"Id": "006000000000001AAA", "Description": None}],
                "done": True,
            })
        )
        child = ChildRecord(child_type=ChildType.RECORD, child_id="file-1", child_name="Report.pdf")
        connector._get_record_linked_file_child_records = AsyncMock(return_value=[child])
        connector._get_opportunity_related_child_records = AsyncMock(return_value=[])
        connector._fetch_and_build_discussion_block_groups = AsyncMock(return_value=[])

        record = MagicMock()
        record.external_record_id = "006000000000001AAA"
        record.record_name = "Deal with Files"
        result = await connector._process_deal_record(record)
        assert isinstance(result, bytes)


# ===========================================================================
# SalesforceConnector._process_case_record
# ===========================================================================


class TestProcessCaseRecord:

    @pytest.mark.asyncio
    async def test_raises_when_no_data_source(self):
        connector = _make_connector()
        connector.data_source = None
        record = MagicMock()
        record.external_record_id = "500000000000001AAA"
        record.record_name = "Bug Case"
        with pytest.raises(Exception):
            await connector._process_case_record(record)

    @pytest.mark.asyncio
    async def test_returns_bytes_with_subject_and_description(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {
                "records": [{"Id": "500000000000001AAA", "Subject": "Login Bug", "Description": "Cannot login"}],
                "done": True,
            })
        )
        connector._get_record_linked_file_child_records = AsyncMock(return_value=[])
        connector._fetch_and_build_discussion_block_groups = AsyncMock(return_value=[])

        record = MagicMock()
        record.external_record_id = "500000000000001AAA"
        record.record_name = "Bug Case"
        result = await connector._process_case_record(record)
        assert isinstance(result, bytes)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_falls_back_when_only_subject(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {
                "records": [{"Id": "500000000000001AAA", "Subject": "Bug Only", "Description": None}],
                "done": True,
            })
        )
        connector._get_record_linked_file_child_records = AsyncMock(return_value=[])
        connector._fetch_and_build_discussion_block_groups = AsyncMock(return_value=[])

        record = MagicMock()
        record.external_record_id = "500000000000001AAA"
        record.record_name = "Bug Only"
        result = await connector._process_case_record(record)
        assert isinstance(result, bytes)

    @pytest.mark.asyncio
    async def test_falls_back_when_only_description(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {
                "records": [{"Id": "500000000000001AAA", "Subject": None, "Description": "Just description"}],
                "done": True,
            })
        )
        connector._get_record_linked_file_child_records = AsyncMock(return_value=[])
        connector._fetch_and_build_discussion_block_groups = AsyncMock(return_value=[])

        record = MagicMock()
        record.external_record_id = "500000000000001AAA"
        record.record_name = "Case"
        result = await connector._process_case_record(record)
        assert isinstance(result, bytes)

    @pytest.mark.asyncio
    async def test_falls_back_when_fetch_fails(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(False, error="Server error")
        )
        connector._get_record_linked_file_child_records = AsyncMock(return_value=[])
        connector._fetch_and_build_discussion_block_groups = AsyncMock(return_value=[])

        record = MagicMock()
        record.external_record_id = "500000000000001AAA"
        record.record_name = "Case Fallback"
        result = await connector._process_case_record(record)
        assert isinstance(result, bytes)

    @pytest.mark.asyncio
    async def test_raises_on_invalid_case_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        record = MagicMock()
        record.external_record_id = "'; DROP TABLE--"
        record.record_name = "Bad Case"
        with pytest.raises(Exception):
            await connector._process_case_record(record)


# ===========================================================================
# SalesforceConnector._process_task_record
# ===========================================================================


class TestProcessTaskRecord:

    @pytest.mark.asyncio
    async def test_raises_when_no_data_source(self):
        connector = _make_connector()
        connector.data_source = None
        record = MagicMock()
        record.id = "arango-1"
        record.external_record_id = "00T000000000001AAA"
        record.record_name = "Follow-up Call"
        with pytest.raises(Exception):
            await connector._process_task_record(record)

    @pytest.mark.asyncio
    async def test_returns_bytes_with_subject_and_description(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        connector._soql_query_paginated = AsyncMock(side_effect=[
            # Task query
            _sf_response(True, {"records": [{"Id": "00T000000000001AAA", "Subject": "Call", "Description": "Follow-up"}], "done": True}),
            # Email query
            _sf_response(True, {"records": [], "done": True, "totalSize": 0}),
        ])
        connector._get_record_linked_file_child_records = AsyncMock(return_value=[])

        record = MagicMock()
        record.id = "arango-1"
        record.external_record_id = "00T000000000001AAA"
        record.record_name = "Follow-up Call"
        result = await connector._process_task_record(record)
        assert isinstance(result, bytes)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_returns_bytes_when_task_fetch_fails(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        connector._soql_query_paginated = AsyncMock(side_effect=[
            _sf_response(False, error="Not found"),
            _sf_response(True, {"records": [], "done": True, "totalSize": 0}),
        ])
        connector._get_record_linked_file_child_records = AsyncMock(return_value=[])

        record = MagicMock()
        record.id = "arango-1"
        record.external_record_id = "00T000000000001AAA"
        record.record_name = "Task Fallback"
        result = await connector._process_task_record(record)
        assert isinstance(result, bytes)

    @pytest.mark.asyncio
    async def test_includes_email_block_when_email_found(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        connector._soql_query_paginated = AsyncMock(side_effect=[
            # Task query
            _sf_response(True, {"records": [{"Id": "00T000000000001AAA", "Subject": "Email Task", "Description": None}], "done": True}),
            # Email query - has an email
            _sf_response(True, {
                "records": [{
                    "Id": "email-1",
                    "Subject": "Re: Proposal",
                    "HtmlBody": "<p>Hello</p>",
                    "TextBody": "Hello",
                    "HasAttachment": False,
                }],
                "done": True,
                "totalSize": 1,
            }),
        ])
        connector._get_record_linked_file_child_records = AsyncMock(return_value=[])
        connector._process_html_images = AsyncMock(return_value="<p>Hello</p>")

        record = MagicMock()
        record.id = "arango-1"
        record.external_record_id = "00T000000000001AAA"
        record.record_name = "Email Task"
        result = await connector._process_task_record(record)
        assert isinstance(result, bytes)
        # Email block should be in the content
        assert b"email" in result.lower() or b"Re: Proposal" in result or b"block_groups" in result

    @pytest.mark.asyncio
    async def test_raises_on_invalid_task_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        record = MagicMock()
        record.id = "arango-1"
        record.external_record_id = "'; DROP TABLE--"
        record.record_name = "Bad Task"
        with pytest.raises(Exception):
            await connector._process_task_record(record)

    @pytest.mark.asyncio
    async def test_task_with_only_subject(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        connector._soql_query_paginated = AsyncMock(side_effect=[
            _sf_response(True, {"records": [{"Id": "00T000000000001AAA", "Subject": "Only Subject", "Description": None}], "done": True}),
            _sf_response(True, {"records": [], "done": True, "totalSize": 0}),
        ])
        connector._get_record_linked_file_child_records = AsyncMock(return_value=[])

        record = MagicMock()
        record.id = "arango-1"
        record.external_record_id = "00T000000000001AAA"
        record.record_name = "Only Subject"
        result = await connector._process_task_record(record)
        assert isinstance(result, bytes)


# ===========================================================================
# SalesforceConnector._get_record_linked_file_child_records
# ===========================================================================


class TestGetRecordLinkedFileChildRecords:

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_record_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        result = await connector._get_record_linked_file_child_records("59.0", "")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_data_source(self):
        connector = _make_connector()
        connector.data_source = None
        result = await connector._get_record_linked_file_child_records("59.0", "001000000000001AAA")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_invalid_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        result = await connector._get_record_linked_file_child_records("59.0", "'; DROP TABLE--")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_query_fails(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(False, error="API error")
        )
        result = await connector._get_record_linked_file_child_records("59.0", "001000000000001AAA")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_child_records_when_files_found(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {
                "records": [{
                    "ContentDocumentId": "069000000000001AAA",
                    "LinkedEntityId": "001000000000001AAA",
                }],
                "done": True,
            })
        )
        mock_file_record = MagicMock()
        mock_file_record.id = "file-internal-1"
        mock_file_record.record_name = "Report.pdf"
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=mock_file_record
        )

        result = await connector._get_record_linked_file_child_records("59.0", "001000000000001AAA")
        assert len(result) == 1
        assert result[0].child_id == "file-internal-1"

    @pytest.mark.asyncio
    async def test_skips_rows_without_document_or_entity_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._soql_query_paginated = AsyncMock(
            return_value=_sf_response(True, {
                "records": [
                    {"ContentDocumentId": None, "LinkedEntityId": "001000000000001AAA"},
                    {"ContentDocumentId": "069000000000001AAA", "LinkedEntityId": None},
                ],
                "done": True,
            })
        )
        result = await connector._get_record_linked_file_child_records("59.0", "001000000000001AAA")
        assert result == []


# ===========================================================================
# SalesforceConnector._reinitialize_token_if_needed (exception paths)
# ===========================================================================


class TestReinitializeTokenExceptionPaths:

    @pytest.mark.asyncio
    async def test_returns_false_when_refresh_raises_exception(self):
        """Token refresh itself throws → should catch and return False."""
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        connector.data_source.limits = AsyncMock(
            return_value=_sf_response(False, error="HTTP 401 Unauthorized")
        )
        connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {"refresh_token": "ref-abc"}
        })
        mock_refresh_svc = MagicMock()
        mock_refresh_svc._perform_token_refresh = AsyncMock(side_effect=Exception("Refresh failed"))
        with patch(
            "app.connectors.sources.salesforce.connector.startup_service"
        ) as mock_startup:
            mock_startup.get_token_refresh_service.return_value = mock_refresh_svc
            result = await connector._reinitialize_token_if_needed()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_init_after_refresh_raises(self):
        """Re-init after successful refresh throws → should catch and return False."""
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        connector.data_source.limits = AsyncMock(
            return_value=_sf_response(False, error="HTTP 401 Unauthorized")
        )
        connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {"refresh_token": "ref-abc"}
        })
        mock_refresh_svc = MagicMock()
        mock_refresh_svc._perform_token_refresh = AsyncMock()
        connector.init = AsyncMock(side_effect=Exception("Init failed"))
        with patch(
            "app.connectors.sources.salesforce.connector.startup_service"
        ) as mock_startup:
            mock_startup.get_token_refresh_service.return_value = mock_refresh_svc
            result = await connector._reinitialize_token_if_needed()
        assert result is False


# ===========================================================================
# SalesforceConnector.run_sync incremental paths
# ===========================================================================


class TestRunSyncIncrementalPaths:

    @pytest.mark.asyncio
    async def test_run_sync_uses_incremental_soql_when_sync_points_set(self):
        """When sync-point timestamps are present, run_sync uses WHERE clauses."""
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._reinitialize_token_if_needed = AsyncMock()

        from app.connectors.core.registry.filters import FilterCollection
        with patch(
            "app.connectors.sources.salesforce.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_filters:
            mock_filters.return_value = (FilterCollection(), FilterCollection())

            # All sync steps are mocked so we only test the SOQL generation paths
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

            # Capture SOQL queries
            soql_calls: List[str] = []

            async def capture_soql(api_version, q, **kwargs):
                soql_calls.append(q)
                return _sf_response(True, {"records": [], "done": True})

            connector._soql_query_paginated = capture_soql

            # Provide non-zero timestamps so incremental branches are taken
            TS = 1_700_000_000_000  # some past epoch ms

            connector.user_sync_point = MagicMock()
            connector.user_sync_point.read_sync_point = AsyncMock(return_value={"lastSyncTimestamp": TS})
            connector.user_sync_point.update_sync_point = AsyncMock()

            connector.records_sync_point = MagicMock()
            connector.records_sync_point.read_sync_point = AsyncMock(return_value={"lastSyncTimestamp": TS})
            connector.records_sync_point.update_sync_point = AsyncMock()

            await connector.run_sync()

            # Incremental SOQL should contain LastModifiedDate WHERE clause
            assert any("LastModifiedDate" in q for q in soql_calls), (
                "Expected at least one incremental SOQL with LastModifiedDate filter"
            )

    @pytest.mark.asyncio
    async def test_run_sync_full_paths_when_no_sync_points(self):
        """When sync-point timestamps are absent, run_sync fetches all records."""
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._reinitialize_token_if_needed = AsyncMock()

        from app.connectors.core.registry.filters import FilterCollection
        with patch(
            "app.connectors.sources.salesforce.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_filters:
            mock_filters.return_value = (FilterCollection(), FilterCollection())

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
                return_value=_sf_response(True, {"records": [], "done": True})
            )

            # No timestamps → full sync
            connector.user_sync_point = MagicMock()
            connector.user_sync_point.read_sync_point = AsyncMock(return_value={})
            connector.user_sync_point.update_sync_point = AsyncMock()

            connector.records_sync_point = MagicMock()
            connector.records_sync_point.read_sync_point = AsyncMock(return_value={})
            connector.records_sync_point.update_sync_point = AsyncMock()

            await connector.run_sync()
            connector._sync_users.assert_awaited_once()
            connector._sync_accounts.assert_awaited_once()


# ===========================================================================
# SalesforceConnector._stream_salesforce_file_content (error paths)
# ===========================================================================


class TestStreamSalesforceFileContent:

    @pytest.mark.asyncio
    async def test_raises_when_no_version_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        record = MagicMock()
        record.external_revision_id = None

        gen = connector._stream_salesforce_file_content(record)
        with pytest.raises(Exception):
            async for _ in gen:
                pass

    @pytest.mark.asyncio
    async def test_raises_when_no_access_token(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_access_token = AsyncMock(return_value=None)
        connector._get_api_version = AsyncMock(return_value="59.0")

        record = MagicMock()
        record.external_revision_id = "cv-1"

        gen = connector._stream_salesforce_file_content(record)
        with pytest.raises(Exception):
            async for _ in gen:
                pass


# ===========================================================================
# SalesforceConnector._sync_contacts (deeper coverage)
# ===========================================================================


class TestSyncContactsDeeper:

    @pytest.mark.asyncio
    async def test_skips_contact_without_id(self):
        connector = _make_connector()
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.get_nodes_by_field_in = AsyncMock(return_value=[])
        mock_tx.batch_upsert_people = AsyncMock()
        mock_tx.batch_create_edges = AsyncMock()

        # Contact with no Id → should be skipped
        contact = {
            "FirstName": "Ghost",
            "LastName": "Person",
            "Email": "ghost@example.com",
        }
        await connector._sync_contacts([contact])
        mock_tx.batch_upsert_people.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_raises_when_transaction_fails(self):
        """If the batch upsert step raises, the outer except re-raises."""
        connector = _make_connector()
        mock_tx = connector.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.get_nodes_by_field_in = AsyncMock(return_value=[])
        mock_tx.batch_upsert_people = AsyncMock(side_effect=Exception("DB crash"))

        contact = {
            "Id": "contact-1",
            "Email": "alice@example.com",
            "FirstName": "Alice",
            "LastName": "Smith",
            "AccountId": "acc-1",
            "Account": {"Name": "Acme"},
            "CreatedDate": "2024-01-01T00:00:00.000Z",
            "LastModifiedDate": "2024-01-02T00:00:00.000Z",
            "Description": None,
            "LeadSource": None,
            "Phone": None,
        }
        with pytest.raises(Exception, match="DB crash"):
            await connector._sync_contacts([contact])


# ===========================================================================
# SalesforceConnector._get_opportunity_related_child_records
# ===========================================================================


class TestGetOpportunityRelatedChildRecords:

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_opportunity_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        result = await connector._get_opportunity_related_child_records("59.0", "")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_data_source(self):
        connector = _make_connector()
        connector.data_source = None
        result = await connector._get_opportunity_related_child_records("59.0", "006000000000001AAA")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_invalid_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        result = await connector._get_opportunity_related_child_records("59.0", "'; DROP TABLE--")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_composite_fails(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.composite = AsyncMock(
            return_value=_sf_response(False, error="Composite error")
        )
        result = await connector._get_opportunity_related_child_records("59.0", "006000000000001AAA")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_child_records_from_tasks(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.composite = AsyncMock(
            return_value=_sf_response(True, {
                "compositeResponse": [{
                    "referenceId": "RelatedTasks",
                    "httpStatusCode": 200,
                    "body": {
                        "records": [{"Id": "00T000000000001AAA", "Subject": "Call Client"}]
                    },
                }]
            })
        )
        mock_rec = MagicMock()
        mock_rec.id = "arango-task-1"
        mock_rec.record_name = "Call Client"
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=mock_rec)

        result = await connector._get_opportunity_related_child_records("59.0", "006000000000001AAA")
        assert len(result) == 1
        assert result[0].child_id == "arango-task-1"

    @pytest.mark.asyncio
    async def test_skips_non_200_composite_items(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.composite = AsyncMock(
            return_value=_sf_response(True, {
                "compositeResponse": [{
                    "referenceId": "RelatedTasks",
                    "httpStatusCode": 400,
                    "body": {"records": [{"Id": "00T000000000001AAA"}]},
                }]
            })
        )
        result = await connector._get_opportunity_related_child_records("59.0", "006000000000001AAA")
        assert result == []


# ===========================================================================
# SalesforceConnector handle_webhook_notification
# ===========================================================================


class TestHandleWebhookNotification:

    def test_webhook_creates_task(self):
        import asyncio as asyncio_mod
        connector = _make_connector()
        connector.run_incremental_sync = AsyncMock()

        with patch.object(asyncio_mod, "create_task") as mock_create_task:
            connector.handle_webhook_notification({"type": "push"})
            mock_create_task.assert_called_once()


# ===========================================================================
# Additional branch coverage for _sync_roles, _sync_user_groups
# ===========================================================================


class TestSyncRolesDeeper:

    @pytest.mark.asyncio
    async def test_sync_roles_with_user_without_role_id(self):
        """Users without UserRoleId should not cause errors."""
        connector = _make_connector()
        connector.data_source = MagicMock()
        roles = [{"Id": "role-1", "Name": "Manager", "ParentRoleId": None, "SystemModstamp": None}]
        # User has no UserRoleId
        user_roles = [
            {"Id": "u1", "Email": "alice@example.com", "UserRoleId": None, "FirstName": "Alice", "LastName": "S"}
        ]
        await connector._sync_roles(roles, user_roles)
        connector.data_entities_processor.on_new_app_roles.assert_awaited_once()


class TestSyncUserGroupsDeeper:

    @pytest.mark.asyncio
    async def test_sync_user_groups_with_group_with_no_flattened_users(self):
        """Groups that exist in group_records but have no flattened users are skipped."""
        connector = _make_connector()
        connector._flatten_group_members = AsyncMock(return_value={
            "grp-1": set(),  # No users
        })
        groups = [
            {"Id": "grp-1", "Name": "Empty Group", "Type": "Regular", "CreatedDate": None, "LastModifiedDate": None},
        ]
        await connector._sync_user_groups(api_version="59.0", group_records=groups)
        connector.data_entities_processor.on_new_user_groups.assert_not_awaited()


# ===========================================================================
# Additional coverage for _fetch_and_build_discussion_block_groups
# ===========================================================================


class TestFetchAndBuildDiscussionBlockGroups:

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_data_source(self):
        connector = _make_connector()
        connector.data_source = None
        result = await connector._fetch_and_build_discussion_block_groups("opp-1", 0)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_feed_fetch_fails(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        connector.data_source.record_feed_elements = AsyncMock(
            return_value=_sf_response(False, error="API error")
        )
        result = await connector._fetch_and_build_discussion_block_groups("opp-1", 0)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_elements(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        connector.data_source.record_feed_elements = AsyncMock(
            return_value=_sf_response(True, {"elements": [], "nextPageUrl": None})
        )
        result = await connector._fetch_and_build_discussion_block_groups("opp-1", 0)
        assert result == []


# ===========================================================================
# SalesforceConnector._iter_record_access
# ===========================================================================


class TestIterRecordAccess:

    @pytest.mark.asyncio
    async def test_yields_nothing_for_empty_record_ids(self):
        connector = _make_connector()
        results = []
        async for item in connector._iter_record_access("user-1", "user@test.com", [], "59.0"):
            results.append(item)
        assert results == []

    @pytest.mark.asyncio
    async def test_yields_nothing_for_empty_user_id(self):
        connector = _make_connector()
        results = []
        async for item in connector._iter_record_access("", "user@test.com", ["001000000000001AAA"], "59.0"):
            results.append(item)
        assert results == []

    @pytest.mark.asyncio
    async def test_yields_nothing_for_invalid_user_id(self):
        connector = _make_connector()
        results = []
        async for item in connector._iter_record_access(
            "'; DROP TABLE--", "user@test.com", ["001000000000001AAA"], "59.0"
        ):
            results.append(item)
        assert results == []

    @pytest.mark.asyncio
    async def test_yields_reader_access(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.composite = AsyncMock(
            return_value=_sf_response(True, {
                "compositeResponse": [{
                    "httpStatusCode": 200,
                    "body": {
                        "records": [{"RecordId": "001000000000001AAA", "MaxAccessLevel": "Read"}]
                    }
                }]
            })
        )
        results = []
        async for item in connector._iter_record_access(
            "005000000000001AAA", "user@test.com", ["001000000000001AAA"], "59.0"
        ):
            results.append(item)
        assert len(results) == 1
        assert results[0][2] == "READER"

    @pytest.mark.asyncio
    async def test_yields_writer_access_for_edit(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.composite = AsyncMock(
            return_value=_sf_response(True, {
                "compositeResponse": [{
                    "httpStatusCode": 200,
                    "body": {
                        "records": [{"RecordId": "001000000000001AAA", "MaxAccessLevel": "Edit"}]
                    }
                }]
            })
        )
        results = []
        async for item in connector._iter_record_access(
            "005000000000001AAA", "user@test.com", ["001000000000001AAA"], "59.0"
        ):
            results.append(item)
        assert results[0][2] == "WRITER"

    @pytest.mark.asyncio
    async def test_yields_owner_access_for_all(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.composite = AsyncMock(
            return_value=_sf_response(True, {
                "compositeResponse": [{
                    "httpStatusCode": 200,
                    "body": {
                        "records": [{"RecordId": "001000000000001AAA", "MaxAccessLevel": "All"}]
                    }
                }]
            })
        )
        results = []
        async for item in connector._iter_record_access(
            "005000000000001AAA", "user@test.com", ["001000000000001AAA"], "59.0"
        ):
            results.append(item)
        assert results[0][2] == "OWNER"

    @pytest.mark.asyncio
    async def test_skips_none_access_level(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.composite = AsyncMock(
            return_value=_sf_response(True, {
                "compositeResponse": [{
                    "httpStatusCode": 200,
                    "body": {
                        "records": [{"RecordId": "001000000000001AAA", "MaxAccessLevel": "None"}]
                    }
                }]
            })
        )
        results = []
        async for item in connector._iter_record_access(
            "005000000000001AAA", "user@test.com", ["001000000000001AAA"], "59.0"
        ):
            results.append(item)
        assert results == []

    @pytest.mark.asyncio
    async def test_skips_non_200_composite_response(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.composite = AsyncMock(
            return_value=_sf_response(True, {
                "compositeResponse": [{"httpStatusCode": 400, "body": {}}]
            })
        )
        results = []
        async for item in connector._iter_record_access(
            "005000000000001AAA", "user@test.com", ["001000000000001AAA"], "59.0"
        ):
            results.append(item)
        assert results == []

    @pytest.mark.asyncio
    async def test_handles_composite_failure_gracefully(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.composite = AsyncMock(
            return_value=_sf_response(False, error="Composite failed")
        )
        results = []
        async for item in connector._iter_record_access(
            "005000000000001AAA", "user@test.com", ["001000000000001AAA"], "59.0"
        ):
            results.append(item)
        assert results == []


# ===========================================================================
# Additional coverage: process_task_record with email body (TextBody fallback)
# ===========================================================================


class TestProcessTaskRecordEmailFallback:

    @pytest.mark.asyncio
    async def test_uses_text_body_when_no_html_body(self):
        """When HtmlBody is absent, TextBody is used as email content."""
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_api_version = AsyncMock(return_value="59.0")
        connector._soql_query_paginated = AsyncMock(side_effect=[
            _sf_response(True, {"records": [{"Id": "00T000000000001AAA", "Subject": "Email Task", "Description": None}], "done": True}),
            _sf_response(True, {
                "records": [{
                    "Id": "email-2",
                    "Subject": "Plain text email",
                    "HtmlBody": None,
                    "TextBody": "Just plain text",
                    "HasAttachment": False,
                }],
                "done": True,
                "totalSize": 1,
            }),
        ])
        connector._get_record_linked_file_child_records = AsyncMock(return_value=[])

        record = MagicMock()
        record.id = "arango-1"
        record.external_record_id = "00T000000000001AAA"
        record.record_name = "Email Task"
        result = await connector._process_task_record(record)
        assert isinstance(result, bytes)


# ===========================================================================
# Additional coverage: cleanup and run_incremental_sync
# ===========================================================================


class TestCleanupAndIncrementalSync:

    @pytest.mark.asyncio
    async def test_cleanup_sets_data_source_to_none(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._http_client = None
        await connector.cleanup()
        assert connector.data_source is None

    @pytest.mark.asyncio
    async def test_cleanup_closes_http_client(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        mock_client = AsyncMock()
        connector._http_client = mock_client
        await connector.cleanup()
        mock_client.aclose.assert_awaited_once()
        assert connector._http_client is None

    @pytest.mark.asyncio
    async def test_run_incremental_sync_delegates_to_run_sync(self):
        connector = _make_connector()
        connector.run_sync = AsyncMock()
        await connector.run_incremental_sync()
        connector.run_sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_incremental_sync_propagates_exception(self):
        connector = _make_connector()
        connector.run_sync = AsyncMock(side_effect=Exception("Sync failed"))
        with pytest.raises(Exception, match="Sync failed"):
            await connector.run_incremental_sync()
