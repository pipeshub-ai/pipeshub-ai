"""
Unit tests for ArangoHTTPProvider — record, record-group, user, permission,
sync-point, department, and bulk-operation methods (lines ~3542–6800+).
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_logger():
    return MagicMock(spec=logging.Logger)


@pytest.fixture
def mock_config_service():
    cs = AsyncMock()
    cs.get_config = AsyncMock(return_value={
        "url": "http://localhost:8529",
        "username": "root",
        "password": "secret",
        "db": "test_db",
    })
    return cs


@pytest.fixture
def provider(mock_logger, mock_config_service):
    return ArangoHTTPProvider(mock_logger, mock_config_service)


@pytest.fixture
def connected_provider(provider):
    provider.http_client = AsyncMock()
    return provider


def _make_mock_record(key="rec1"):
    """Create a lightweight mock Record with .id attribute."""
    rec = MagicMock()
    rec.id = key
    return rec


@pytest.fixture
def typed_provider(connected_provider):
    """connected_provider with _create_typed_record_from_arango patched
    to return a mock Record, so tests of outer methods don't need to
    satisfy the full entity constructor chain."""
    original = connected_provider._create_typed_record_from_arango

    def _factory(record_dict, type_doc):
        key = record_dict.get("_key", "unknown")
        return _make_mock_record(key)

    connected_provider._create_typed_record_from_arango = _factory
    return connected_provider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_bind_vars(mock_execute_aql):
    """Extract bind_vars from execute_aql call regardless of positional/keyword style."""
    call = mock_execute_aql.call_args
    if len(call[0]) > 1:
        return call[0][1]
    return call.kwargs.get("bind_vars", {})

def _arango_record(key="rec1", record_type="FILE", connector_id="conn1",
                   org_id="org1", **extra):
    base = {
        "_key": key,
        "_id": f"records/{key}",
        "orgId": org_id,
        "recordName": f"Record {key}",
        "recordType": record_type,
        "externalRecordId": f"ext-{key}",
        "version": "1",
        "origin": "CONNECTOR",
        "connectorId": connector_id,
        "connectorName": "DRIVE",
        "indexingStatus": "COMPLETED",
        "createdAtTimestamp": 1000,
        "updatedAtTimestamp": 2000,
    }
    base.update(extra)
    return base


def _arango_file_type_doc(key="rec1"):
    return {
        "_key": key,
        "_id": f"files/{key}",
        "isFile": True,
        "extension": ".txt",
    }


def _arango_user(key="u1", email="user@example.com", org_id="org1", **extra):
    base = {
        "_key": key,
        "_id": f"users/{key}",
        "email": email,
        "orgId": org_id,
        "userId": f"mongo-{key}",
        "isActive": True,
        "firstName": "Test",
        "lastName": "User",
        "fullName": "Test User",
    }
    base.update(extra)
    return base


def _arango_record_group(key="rg1", org_id="org1", connector_id="conn1", **extra):
    base = {
        "_key": key,
        "_id": f"recordGroups/{key}",
        "orgId": org_id,
        "groupName": f"Group {key}",
        "externalGroupId": f"ext-{key}",
        "connectorId": connector_id,
        "connectorName": "DRIVE",
        "groupType": "KB",
        "createdAtTimestamp": 1000,
        "updatedAtTimestamp": 2000,
    }
    base.update(extra)
    return base


# ===================================================================
# get_records_by_status
# ===================================================================

class TestGetRecordsByStatus:
    async def test_success_returns_typed_records(self, typed_provider):
        rec = _arango_record()
        type_doc = _arango_file_type_doc()
        typed_provider.http_client.execute_aql.return_value = [
            {"record": rec, "typeDoc": type_doc}
        ]
        result = await typed_provider.get_records_by_status(
            org_id="org1",
            connector_id="conn1",
            status_filters=["COMPLETED"],
        )
        assert len(result) == 1
        assert result[0].id == "rec1"
        typed_provider.http_client.execute_aql.assert_awaited_once()

    async def test_empty_result(self, typed_provider):
        typed_provider.http_client.execute_aql.return_value = []
        result = await typed_provider.get_records_by_status(
            org_id="org1", connector_id="conn1", status_filters=None,
        )
        assert result == []

    async def test_with_limit_offset(self, typed_provider):
        typed_provider.http_client.execute_aql.return_value = []
        result = await typed_provider.get_records_by_status(
            org_id="org1", connector_id="conn1", status_filters=["QUEUED"],
            limit=10, offset=5,
        )
        assert result == []
        bind = _get_bind_vars(typed_provider.http_client.execute_aql)
        assert bind["limit"] == 10
        assert bind["offset"] == 5

    async def test_with_record_group_id(self, typed_provider):
        typed_provider.http_client.execute_aql.return_value = []
        await typed_provider.get_records_by_status(
            org_id="org1", connector_id="conn1", status_filters=None,
            record_group_id="rg1",
        )
        bind = _get_bind_vars(typed_provider.http_client.execute_aql)
        assert bind["record_group_id"] == "rg1"

    async def test_with_is_placeholder_true(self, typed_provider):
        typed_provider.http_client.execute_aql.return_value = []
        await typed_provider.get_records_by_status(
            org_id="org1", connector_id="conn1", status_filters=None,
            is_placeholder=True,
        )
        query = typed_provider.http_client.execute_aql.call_args[0][0]
        assert "isPlaceholder == true" in query

    async def test_with_is_placeholder_false(self, typed_provider):
        typed_provider.http_client.execute_aql.return_value = []
        await typed_provider.get_records_by_status(
            org_id="org1", connector_id="conn1", status_filters=None,
            is_placeholder=False,
        )
        query = typed_provider.http_client.execute_aql.call_args[0][0]
        assert "isPlaceholder != true" in query

    async def test_with_after_key(self, typed_provider):
        typed_provider.http_client.execute_aql.return_value = []
        await typed_provider.get_records_by_status(
            org_id="org1", connector_id="conn1", status_filters=None,
            after_key="abc",
        )
        bind = _get_bind_vars(typed_provider.http_client.execute_aql)
        assert bind["after_key"] == "abc"

    async def test_with_exclude_statuses(self, typed_provider):
        typed_provider.http_client.execute_aql.return_value = []
        await typed_provider.get_records_by_status(
            org_id="org1", connector_id="conn1", status_filters=None,
            exclude_statuses=["FAILED"],
        )
        bind = _get_bind_vars(typed_provider.http_client.execute_aql)
        assert bind["exclude_statuses"] == ["FAILED"]

    async def test_exception_returns_empty_list(self, typed_provider):
        typed_provider.http_client.execute_aql.side_effect = Exception("boom")
        result = await typed_provider.get_records_by_status(
            org_id="org1", connector_id="conn1", status_filters=["COMPLETED"],
        )
        assert result == []

    async def test_multiple_records(self, typed_provider):
        recs = [
            {"record": _arango_record(key=f"r{i}"), "typeDoc": _arango_file_type_doc(key=f"r{i}")}
            for i in range(3)
        ]
        typed_provider.http_client.execute_aql.return_value = recs
        result = await typed_provider.get_records_by_status(
            org_id="org1", connector_id="conn1", status_filters=None,
        )
        assert len(result) == 3


# ===================================================================
# get_records_by_record_group
# ===================================================================

class TestGetRecordsByRecordGroup:
    async def test_success(self, typed_provider):
        rec = _arango_record()
        type_doc = _arango_file_type_doc()
        typed_provider.http_client.execute_aql.return_value = [
            {"record": rec, "typeDoc": type_doc}
        ]
        result = await typed_provider.get_records_by_record_group(
            record_group_id="rg1", connector_id="conn1", org_id="org1", depth=1,
        )
        assert len(result) == 1

    async def test_empty(self, typed_provider):
        typed_provider.http_client.execute_aql.return_value = []
        result = await typed_provider.get_records_by_record_group(
            record_group_id="rg1", connector_id="conn1", org_id="org1", depth=0,
        )
        assert result == []

    async def test_invalid_depth_returns_empty(self, typed_provider):
        result = await typed_provider.get_records_by_record_group(
            record_group_id="rg1", connector_id="conn1", org_id="org1", depth=-2,
        )
        assert result == []

    async def test_unlimited_depth(self, typed_provider):
        typed_provider.http_client.execute_aql.return_value = []
        await typed_provider.get_records_by_record_group(
            record_group_id="rg1", connector_id="conn1", org_id="org1", depth=-1,
        )
        typed_provider.http_client.execute_aql.assert_awaited_once()

    async def test_with_user_key(self, typed_provider):
        typed_provider.http_client.execute_aql.return_value = []
        await typed_provider.get_records_by_record_group(
            record_group_id="rg1", connector_id="conn1", org_id="org1",
            depth=1, user_key="uk1",
        )
        bind = _get_bind_vars(typed_provider.http_client.execute_aql)
        assert bind["user_key"] == "uk1"

    async def test_with_pagination(self, typed_provider):
        typed_provider.http_client.execute_aql.return_value = []
        await typed_provider.get_records_by_record_group(
            record_group_id="rg1", connector_id="conn1", org_id="org1",
            depth=1, limit=20, offset=10,
        )
        bind = _get_bind_vars(typed_provider.http_client.execute_aql)
        assert bind["limit"] == 20
        assert bind["offset"] == 10

    async def test_with_status_filters(self, typed_provider):
        typed_provider.http_client.execute_aql.return_value = []
        await typed_provider.get_records_by_record_group(
            record_group_id="rg1", connector_id="conn1", org_id="org1",
            depth=1, status_filters=["COMPLETED", "QUEUED"],
        )
        bind = _get_bind_vars(typed_provider.http_client.execute_aql)
        assert bind["status_filters"] == ["COMPLETED", "QUEUED"]

    async def test_with_after_key_and_exclude(self, typed_provider):
        typed_provider.http_client.execute_aql.return_value = []
        await typed_provider.get_records_by_record_group(
            record_group_id="rg1", connector_id="conn1", org_id="org1",
            depth=1, after_key="abc", exclude_statuses=["FAILED"],
        )
        bind = _get_bind_vars(typed_provider.http_client.execute_aql)
        assert bind["after_key"] == "abc"
        assert bind["exclude_statuses"] == ["FAILED"]

    async def test_exception_returns_empty(self, typed_provider):
        typed_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await typed_provider.get_records_by_record_group(
            record_group_id="rg1", connector_id="conn1", org_id="org1", depth=1,
        )
        assert result == []

    async def test_depth_zero_no_traversal(self, typed_provider):
        typed_provider.http_client.execute_aql.return_value = []
        await typed_provider.get_records_by_record_group(
            record_group_id="rg1", connector_id="conn1", org_id="org1", depth=0,
        )
        query = typed_provider.http_client.execute_aql.call_args[0][0]
        assert "allRecordGroups = [recordGroup]" in query


# ===================================================================
# get_records_by_parent_record
# ===================================================================

class TestGetRecordsByParentRecord:
    async def test_success(self, typed_provider):
        rec = _arango_record()
        type_doc = _arango_file_type_doc()
        typed_provider.http_client.execute_aql.return_value = [
            {"record": rec, "typedRecord": type_doc, "depth": 0}
        ]
        result = await typed_provider.get_records_by_parent_record(
            parent_record_id="p1", connector_id="conn1", org_id="org1", depth=1,
        )
        assert len(result) == 1

    async def test_empty(self, typed_provider):
        typed_provider.http_client.execute_aql.return_value = []
        result = await typed_provider.get_records_by_parent_record(
            parent_record_id="p1", connector_id="conn1", org_id="org1", depth=1,
        )
        assert result == []

    async def test_invalid_depth(self, typed_provider):
        result = await typed_provider.get_records_by_parent_record(
            parent_record_id="p1", connector_id="conn1", org_id="org1", depth=-2,
        )
        assert result == []

    async def test_unlimited_depth(self, typed_provider):
        typed_provider.http_client.execute_aql.return_value = []
        await typed_provider.get_records_by_parent_record(
            parent_record_id="p1", connector_id="conn1", org_id="org1", depth=-1,
        )
        bind = _get_bind_vars(typed_provider.http_client.execute_aql)
        assert bind["max_depth"] == 100

    async def test_with_user_key(self, typed_provider):
        typed_provider.http_client.execute_aql.return_value = []
        await typed_provider.get_records_by_parent_record(
            parent_record_id="p1", connector_id="conn1", org_id="org1",
            depth=1, user_key="uk1",
        )
        bind = _get_bind_vars(typed_provider.http_client.execute_aql)
        assert bind["user_key"] == "uk1"

    async def test_with_pagination(self, typed_provider):
        typed_provider.http_client.execute_aql.return_value = []
        await typed_provider.get_records_by_parent_record(
            parent_record_id="p1", connector_id="conn1", org_id="org1",
            depth=1, limit=5, offset=2,
        )
        bind = _get_bind_vars(typed_provider.http_client.execute_aql)
        assert bind["limit"] == 5
        assert bind["offset"] == 2

    async def test_exception_returns_empty(self, typed_provider):
        typed_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await typed_provider.get_records_by_parent_record(
            parent_record_id="p1", connector_id="conn1", org_id="org1", depth=1,
        )
        assert result == []


# ===================================================================
# get_documents_by_status
# ===================================================================

class TestGetDocumentsByStatus:
    async def test_success(self, connected_provider):
        docs = [{"_key": "d1", "indexingStatus": "COMPLETED"}]
        connected_provider.http_client.execute_aql.return_value = docs
        result = await connected_provider.get_documents_by_status("records", "COMPLETED")
        assert result == docs

    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_documents_by_status("records", "QUEUED")
        assert result == []

    async def test_none_result(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = None
        result = await connected_provider.get_documents_by_status("records", "QUEUED")
        assert result == []

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_documents_by_status("records", "COMPLETED")
        assert result == []


# ===================================================================
# get_record_by_conversation_index
# ===================================================================

class TestGetRecordByConversationIndex:
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [_arango_record()]
        result = await connected_provider.get_record_by_conversation_index(
            connector_id="conn1", conversation_index="ci1",
            thread_id="t1", org_id="org1", user_id="u1",
        )
        assert result is not None
        assert result.id == "rec1"

    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_record_by_conversation_index(
            connector_id="conn1", conversation_index="ci1",
            thread_id="t1", org_id="org1", user_id="u1",
        )
        assert result is None

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_record_by_conversation_index(
            connector_id="conn1", conversation_index="ci1",
            thread_id="t1", org_id="org1", user_id="u1",
        )
        assert result is None


# ===================================================================
# get_record_by_issue_key
# ===================================================================

class TestGetRecordByIssueKey:
    async def test_found(self, typed_provider):
        rec = _arango_record(record_type="TICKET")
        ticket_doc = {"_key": "rec1", "_id": "tickets/rec1", "type": "Story"}
        typed_provider.http_client.execute_aql.return_value = [
            {"record": rec, "ticket": ticket_doc}
        ]
        result = await typed_provider.get_record_by_issue_key(
            connector_id="conn1", issue_key="PROJ-123",
        )
        assert result is not None

    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_record_by_issue_key(
            connector_id="conn1", issue_key="PROJ-999",
        )
        assert result is None

    async def test_none_result(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [None]
        result = await connected_provider.get_record_by_issue_key(
            connector_id="conn1", issue_key="PROJ-999",
        )
        assert result is None

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_record_by_issue_key(
            connector_id="conn1", issue_key="PROJ-123",
        )
        assert result is None


# ===================================================================
# get_record_by_weburl
# ===================================================================

class TestGetRecordByWeburl:
    async def test_found_non_link(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [_arango_record()]
        result = await connected_provider.get_record_by_weburl(weburl="https://example.com/doc1")
        assert result is not None
        assert result.id == "rec1"

    async def test_skip_link_records(self, connected_provider):
        link_rec = _arango_record(record_type="LINK")
        normal_rec = _arango_record(key="rec2")
        connected_provider.http_client.execute_aql.return_value = [link_rec, normal_rec]
        result = await connected_provider.get_record_by_weburl(weburl="https://example.com/doc1")
        assert result is not None
        assert result.id == "rec2"

    async def test_all_link_records(self, connected_provider):
        link_rec = _arango_record(record_type="LINK")
        connected_provider.http_client.execute_aql.return_value = [link_rec]
        result = await connected_provider.get_record_by_weburl(weburl="https://example.com/doc1")
        assert result is None

    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_record_by_weburl(weburl="https://example.com/nope")
        assert result is None

    async def test_with_org_id(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        await connected_provider.get_record_by_weburl(
            weburl="https://example.com/doc1", org_id="org1",
        )
        bind = _get_bind_vars(connected_provider.http_client.execute_aql)
        assert bind["org_id"] == "org1"

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_record_by_weburl(weburl="https://example.com/doc1")
        assert result is None


# ===================================================================
# get_records_by_parent
# ===================================================================

class TestGetRecordsByParent:
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [_arango_record()]
        result = await connected_provider.get_records_by_parent(
            connector_id="conn1", parent_external_record_id="ext-p1",
        )
        assert len(result) == 1

    async def test_with_record_type_filter(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        await connected_provider.get_records_by_parent(
            connector_id="conn1", parent_external_record_id="ext-p1",
            record_type="COMMENT",
        )
        bind = _get_bind_vars(connected_provider.http_client.execute_aql)
        assert bind["record_type"] == "COMMENT"

    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_records_by_parent(
            connector_id="conn1", parent_external_record_id="ext-p1",
        )
        assert result == []

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_records_by_parent(
            connector_id="conn1", parent_external_record_id="ext-p1",
        )
        assert result == []


# ===================================================================
# get_record_group_by_external_id
# ===================================================================

class TestGetRecordGroupByExternalId:
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [_arango_record_group()]
        result = await connected_provider.get_record_group_by_external_id(
            connector_id="conn1", external_id="ext-rg1",
        )
        assert result is not None
        assert result.id == "rg1"

    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_record_group_by_external_id(
            connector_id="conn1", external_id="ext-nope",
        )
        assert result is None

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_record_group_by_external_id(
            connector_id="conn1", external_id="ext-rg1",
        )
        assert result is None


# ===================================================================
# get_record_group_by_id
# ===================================================================

class TestGetRecordGroupById:
    async def test_found(self, connected_provider):
        connected_provider.http_client.get_document.return_value = _arango_record_group()
        result = await connected_provider.get_record_group_by_id("rg1")
        assert result is not None
        assert result["_key"] == "rg1"

    async def test_not_found(self, connected_provider):
        connected_provider.http_client.get_document.return_value = None
        result = await connected_provider.get_record_group_by_id("nope")
        assert result is None

    async def test_exception(self, connected_provider):
        connected_provider.http_client.get_document.side_effect = Exception("err")
        result = await connected_provider.get_record_group_by_id("rg1")
        assert result is None


# ===================================================================
# get_file_record_by_id
# ===================================================================

class TestGetFileRecordById:
    async def test_found(self, connected_provider):
        file_doc = {
            "_key": "f1", "_id": "files/f1",
            "isFile": True, "extension": ".pdf",
        }
        rec_doc = _arango_record(key="f1")
        connected_provider.http_client.get_document.side_effect = [file_doc, rec_doc]
        result = await connected_provider.get_file_record_by_id("f1")
        assert result is not None

    async def test_file_missing(self, connected_provider):
        connected_provider.http_client.get_document.side_effect = [None, _arango_record()]
        result = await connected_provider.get_file_record_by_id("f1")
        assert result is None

    async def test_record_missing(self, connected_provider):
        file_doc = {"_key": "f1", "_id": "files/f1", "isFile": True}
        connected_provider.http_client.get_document.side_effect = [file_doc, None]
        result = await connected_provider.get_file_record_by_id("f1")
        assert result is None

    async def test_exception(self, connected_provider):
        connected_provider.http_client.get_document.side_effect = Exception("err")
        result = await connected_provider.get_file_record_by_id("f1")
        assert result is None


# ===================================================================
# get_user_by_email
# ===================================================================

class TestGetUserByEmail:
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [_arango_user()]
        result = await connected_provider.get_user_by_email("user@example.com")
        assert result is not None
        assert result.email == "user@example.com"

    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_user_by_email("nobody@example.com")
        assert result is None

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_user_by_email("user@example.com")
        assert result is None


# ===================================================================
# get_user_by_source_id
# ===================================================================

class TestGetUserBySourceId:
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [_arango_user()]
        result = await connected_provider.get_user_by_source_id(
            source_user_id="src1", connector_id="conn1",
        )
        assert result is not None

    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_user_by_source_id(
            source_user_id="src-none", connector_id="conn1",
        )
        assert result is None

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_user_by_source_id(
            source_user_id="src1", connector_id="conn1",
        )
        assert result is None


# ===================================================================
# get_user_by_user_id
# ===================================================================

class TestGetUserByUserId:
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [_arango_user()]
        result = await connected_provider.get_user_by_user_id("mongo-u1")
        assert result is not None
        assert result["_key"] == "u1"

    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_user_by_user_id("mongo-none")
        assert result is None

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_user_by_user_id("mongo-u1")
        assert result is None


# ===================================================================
# get_graph_user_keys_by_mongo_user_ids
# ===================================================================

class TestGetGraphUserKeysByMongoUserIds:
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"userId": "m1", "_key": "k1"},
            {"userId": "m2", "_key": "k2"},
        ]
        result = await connected_provider.get_graph_user_keys_by_mongo_user_ids(
            user_ids=["m1", "m2"], chunk_size=100,
        )
        assert result == {"m1": "k1", "m2": "k2"}

    async def test_empty_input(self, connected_provider):
        result = await connected_provider.get_graph_user_keys_by_mongo_user_ids(
            user_ids=[], chunk_size=100,
        )
        assert result == {}

    async def test_missing_user_raises(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"userId": "m1", "_key": "k1"},
        ]
        with pytest.raises(ValueError, match="Users not found"):
            await connected_provider.get_graph_user_keys_by_mongo_user_ids(
                user_ids=["m1", "m2"], chunk_size=100,
            )

    async def test_chunking(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [{"userId": "m1", "_key": "k1"}],
            [{"userId": "m2", "_key": "k2"}],
        ]
        result = await connected_provider.get_graph_user_keys_by_mongo_user_ids(
            user_ids=["m1", "m2"], chunk_size=1,
        )
        assert result == {"m1": "k1", "m2": "k2"}
        assert connected_provider.http_client.execute_aql.await_count == 2


# ===================================================================
# get_user_apps
# ===================================================================

class TestGetUserApps:
    async def test_success(self, connected_provider):
        apps = [{"_key": "app1"}, {"_key": "app2"}]
        connected_provider.http_client.execute_aql.return_value = apps
        result = await connected_provider.get_user_apps("u1")
        assert len(result) == 2

    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_user_apps("u1")
        assert result == []

    async def test_none_result(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = None
        result = await connected_provider.get_user_apps("u1")
        assert result == []

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_user_apps("u1")
        assert result == []


# ===================================================================
# _get_user_app_ids
# ===================================================================

class TestGetUserAppIds:
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [_arango_user()],  # get_user_by_user_id
            [{"_key": "app1"}, {"_key": "app2"}],  # get_user_apps
        ]
        result = await connected_provider._get_user_app_ids("mongo-u1")
        assert result == ["app1", "app2"]

    async def test_user_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider._get_user_app_ids("mongo-none")
        assert result == []

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider._get_user_app_ids("mongo-u1")
        assert result == []


# ===================================================================
# get_users (all users by org)
# ===================================================================

class TestGetUsers:
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [_arango_user()]
        result = await connected_provider.get_users("org1")
        assert len(result) == 1

    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_users("org1")
        assert result == []

    async def test_inactive_users(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        await connected_provider.get_users("org1", active=False)
        bind = _get_bind_vars(connected_provider.http_client.execute_aql)
        assert bind["active"] is False

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_users("org1")
        assert result == []


# ===================================================================
# get_app_user_by_email
# ===================================================================

class TestGetAppUserByEmail:
    async def test_found(self, connected_provider):
        user_doc = _arango_user()
        user_doc["sourceUserId"] = "src1"
        user_doc["appName"] = "DRIVE"
        user_doc["connectorId"] = "conn1"
        connected_provider.http_client.execute_aql.return_value = [user_doc]
        result = await connected_provider.get_app_user_by_email(
            email="user@example.com", connector_id="conn1",
        )
        assert result is not None

    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [None]
        result = await connected_provider.get_app_user_by_email(
            email="nope@example.com", connector_id="conn1",
        )
        assert result is None

    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_app_user_by_email(
            email="nope@example.com", connector_id="conn1",
        )
        assert result is None

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_app_user_by_email(
            email="user@example.com", connector_id="conn1",
        )
        assert result is None


# ===================================================================
# get_app_users
# ===================================================================

class TestGetAppUsers:
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [_arango_user()]
        result = await connected_provider.get_app_users("org1", "conn1")
        assert len(result) == 1

    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_app_users("org1", "conn1")
        assert result == []

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_app_users("org1", "conn1")
        assert result == []


# ===================================================================
# get_user_group_by_external_id
# ===================================================================

class TestGetUserGroupByExternalId:
    async def test_found(self, connected_provider):
        group_doc = {
            "_key": "g1", "_id": "groups/g1",
            "externalGroupId": "ext-g1",
            "connectorId": "conn1",
            "connectorName": "DRIVE",
            "name": "Group 1",
            "createdAtTimestamp": 1000,
            "updatedAtTimestamp": 2000,
        }
        connected_provider.http_client.execute_aql.return_value = [group_doc]
        result = await connected_provider.get_user_group_by_external_id(
            connector_id="conn1", external_id="ext-g1",
        )
        assert result is not None

    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_user_group_by_external_id(
            connector_id="conn1", external_id="ext-nope",
        )
        assert result is None

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_user_group_by_external_id(
            connector_id="conn1", external_id="ext-g1",
        )
        assert result is None


# ===================================================================
# get_user_groups
# ===================================================================

class TestGetUserGroups:
    async def test_success(self, connected_provider):
        group_doc = {
            "_key": "g1", "_id": "groups/g1",
            "externalGroupId": "ext-g1",
            "connectorId": "conn1",
            "connectorName": "DRIVE",
            "name": "Group 1",
            "createdAtTimestamp": 1000,
            "updatedAtTimestamp": 2000,
        }
        connected_provider.http_client.execute_aql.return_value = [group_doc]
        result = await connected_provider.get_user_groups("conn1", "org1")
        assert len(result) == 1

    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_user_groups("conn1", "org1")
        assert result == []

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_user_groups("conn1", "org1")
        assert result == []


# ===================================================================
# get_app_role_by_external_id
# ===================================================================

class TestGetAppRoleByExternalId:
    async def test_found(self, connected_provider):
        role_doc = {
            "_key": "r1", "_id": "roles/r1",
            "externalRoleId": "ext-r1",
            "connectorId": "conn1",
            "connectorName": "DRIVE",
            "name": "Admin",
            "createdAtTimestamp": 1000,
            "updatedAtTimestamp": 2000,
        }
        connected_provider.http_client.execute_aql.return_value = [role_doc]
        result = await connected_provider.get_app_role_by_external_id(
            connector_id="conn1", external_id="ext-r1",
        )
        assert result is not None

    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_app_role_by_external_id(
            connector_id="conn1", external_id="ext-nope",
        )
        assert result is None

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_app_role_by_external_id(
            connector_id="conn1", external_id="ext-r1",
        )
        assert result is None


# ===================================================================
# get_all_orgs
# ===================================================================

class TestGetAllOrgs:
    async def test_success(self, connected_provider):
        orgs = [{"_key": "org1", "isActive": True}]
        connected_provider.http_client.execute_aql.return_value = orgs
        result = await connected_provider.get_all_orgs()
        assert len(result) == 1

    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_all_orgs()
        assert result == []

    async def test_inactive_orgs(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        await connected_provider.get_all_orgs(active=False)
        query = connected_provider.http_client.execute_aql.call_args[0][0]
        assert "isActive" not in query

    async def test_external_orgs(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        await connected_provider.get_all_orgs(is_external=True)
        query = connected_provider.http_client.execute_aql.call_args[0][0]
        assert "isExternal == true" in query

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_all_orgs()
        assert result == []


# ===================================================================
# create_record_relation
# ===================================================================

class TestCreateRecordRelation:
    async def test_success(self, connected_provider):
        connected_provider.batch_create_edges = AsyncMock()
        await connected_provider.create_record_relation(
            from_record_id="r1", to_record_id="r2", relation_type="BLOCKS",
        )
        connected_provider.batch_create_edges.assert_awaited_once()

    async def test_with_transaction(self, connected_provider):
        connected_provider.batch_create_edges = AsyncMock()
        await connected_provider.create_record_relation(
            from_record_id="r1", to_record_id="r2",
            relation_type="LINKED_TO", transaction="txn1",
        )
        connected_provider.batch_create_edges.assert_awaited_once()


# ===================================================================
# create_record_group_relation
# ===================================================================

class TestCreateRecordGroupRelation:
    async def test_success(self, connected_provider):
        connected_provider.batch_create_edges = AsyncMock()
        await connected_provider.create_record_group_relation(
            record_id="r1", record_group_id="rg1",
        )
        connected_provider.batch_create_edges.assert_awaited_once()


# ===================================================================
# create_record_groups_relation
# ===================================================================

class TestCreateRecordGroupsRelation:
    async def test_success(self, connected_provider):
        connected_provider.batch_create_edges = AsyncMock()
        await connected_provider.create_record_groups_relation(
            child_id="rg-child", parent_id="rg-parent",
        )
        connected_provider.batch_create_edges.assert_awaited_once()


# ===================================================================
# create_inherit_permissions_relation_record_group
# ===================================================================

class TestCreateInheritPermissionsRelation:
    async def test_success(self, connected_provider):
        connected_provider.batch_create_edges = AsyncMock()
        await connected_provider.create_inherit_permissions_relation_record_group(
            record_id="r1", record_group_id="rg1",
        )
        connected_provider.batch_create_edges.assert_awaited_once()


# ===================================================================
# delete_inherit_permissions_relation_record_group
# ===================================================================

class TestDeleteInheritPermissionsRelation:
    async def test_success(self, connected_provider):
        connected_provider.delete_edge = AsyncMock()
        await connected_provider.delete_inherit_permissions_relation_record_group(
            record_id="r1", record_group_id="rg1",
        )
        connected_provider.delete_edge.assert_awaited_once()


# ===================================================================
# get_all_documents
# ===================================================================

class TestGetAllDocuments:
    async def test_success(self, connected_provider):
        docs = [{"_key": "d1"}, {"_key": "d2"}]
        connected_provider.http_client.execute_aql.return_value = docs
        result = await connected_provider.get_all_documents("myCollection")
        assert len(result) == 2

    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_all_documents("myCollection")
        assert result == []

    async def test_none_result(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = None
        result = await connected_provider.get_all_documents("myCollection")
        assert result == []

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_all_documents("myCollection")
        assert result == []


# ===================================================================
# get_documents_paginated
# ===================================================================

class TestGetDocumentsPaginated:
    async def test_success(self, connected_provider):
        docs = [{"_key": "d1"}]
        connected_provider.http_client.execute_aql.return_value = docs
        result = await connected_provider.get_documents_paginated("col", skip=0, limit=10)
        assert result == docs

    async def test_with_filters(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_documents_paginated(
            "col", filters={"orgId": "org1", "status": "ACTIVE"},
        )
        assert result == []
        bind = _get_bind_vars(connected_provider.http_client.execute_aql)
        assert bind["fv0"] == "org1"
        assert bind["fv1"] == "ACTIVE"

    async def test_with_sort(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        await connected_provider.get_documents_paginated("col", sort_field="createdAt")
        query = connected_provider.http_client.execute_aql.call_args[0][0]
        assert "SORT doc.createdAt" in query

    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = None
        result = await connected_provider.get_documents_paginated("col")
        assert result == []

    async def test_exception_no_raise(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_documents_paginated("col")
        assert result == []

    async def test_exception_with_raise(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        with pytest.raises(Exception, match="err"):
            await connected_provider.get_documents_paginated("col", raise_on_error=True)


# ===================================================================
# get_app_creator_user
# ===================================================================

class TestGetAppCreatorUser:
    async def test_success(self, connected_provider):
        app_doc = {"_key": "conn1", "createdBy": "mongo-u1"}
        user_doc = _arango_user()
        connected_provider.http_client.get_document.return_value = app_doc
        connected_provider.http_client.execute_aql.return_value = [user_doc]
        result = await connected_provider.get_app_creator_user("conn1")
        assert result is not None

    async def test_app_not_found(self, connected_provider):
        connected_provider.http_client.get_document.return_value = None
        result = await connected_provider.get_app_creator_user("conn1")
        assert result is None

    async def test_no_created_by(self, connected_provider):
        connected_provider.http_client.get_document.return_value = {"_key": "conn1"}
        result = await connected_provider.get_app_creator_user("conn1")
        assert result is None

    async def test_user_not_found(self, connected_provider):
        connected_provider.http_client.get_document.return_value = {"_key": "conn1", "createdBy": "u1"}
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_app_creator_user("conn1")
        assert result is None

    async def test_exception(self, connected_provider):
        connected_provider.http_client.get_document.side_effect = Exception("err")
        result = await connected_provider.get_app_creator_user("conn1")
        assert result is None


# ===================================================================
# get_org_apps
# ===================================================================

class TestGetOrgApps:
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [{"_key": "app1"}]
        result = await connected_provider.get_org_apps("org1")
        assert len(result) == 1

    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_org_apps("org1")
        assert result == []

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_org_apps("org1")
        assert result == []


# ===================================================================
# get_departments
# ===================================================================

class TestGetDepartments:
    async def test_with_org_id(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["Engineering", "Sales"]
        result = await connected_provider.get_departments(org_id="org1")
        assert result == ["Engineering", "Sales"]

    async def test_without_org_id(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["HR"]
        result = await connected_provider.get_departments()
        assert result == ["HR"]

    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_departments()
        assert result == []

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_departments()
        assert result == []


# ===================================================================
# update_queued_duplicates_status
# ===================================================================

class TestUpdateQueuedDuplicatesStatus:
    async def test_no_duplicates(self, connected_provider):
        ref_record = {"_key": "r1", "md5Checksum": "abc", "sizeInBytes": 100}
        connected_provider.http_client.execute_aql.side_effect = [
            [ref_record],  # get reference record
            [],            # no queued duplicates
        ]
        result = await connected_provider.update_queued_duplicates_status(
            record_id="r1", new_indexing_status="COMPLETED",
        )
        assert result == 0

    async def test_ref_record_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.update_queued_duplicates_status(
            record_id="nope", new_indexing_status="COMPLETED",
        )
        assert result == 0

    async def test_no_md5(self, connected_provider):
        ref_record = {"_key": "r1"}
        connected_provider.http_client.execute_aql.return_value = [ref_record]
        result = await connected_provider.update_queued_duplicates_status(
            record_id="r1", new_indexing_status="COMPLETED",
        )
        assert result == 0

    async def test_with_duplicates_completed(self, connected_provider):
        ref = {"_key": "r1", "md5Checksum": "abc", "sizeInBytes": 100}
        dup = {"_key": "r2", "md5Checksum": "abc", "indexingStatus": "QUEUED"}
        connected_provider.http_client.execute_aql.side_effect = [
            [ref],   # reference
            [dup],   # duplicates found
        ]
        connected_provider.batch_update_nodes = AsyncMock(return_value=True)
        result = await connected_provider.update_queued_duplicates_status(
            record_id="r1", new_indexing_status="COMPLETED",
        )
        assert result == 1

    async def test_with_duplicates_empty_status(self, connected_provider):
        ref = {"_key": "r1", "md5Checksum": "abc", "sizeInBytes": 100}
        dup = {"_key": "r2", "md5Checksum": "abc", "indexingStatus": "QUEUED"}
        connected_provider.http_client.execute_aql.side_effect = [
            [ref],
            [dup],
        ]
        connected_provider.batch_update_nodes = AsyncMock(return_value=True)
        result = await connected_provider.update_queued_duplicates_status(
            record_id="r1", new_indexing_status="EMPTY",
        )
        assert result == 1

    async def test_batch_update_fails(self, connected_provider):
        ref = {"_key": "r1", "md5Checksum": "abc", "sizeInBytes": 100}
        dup = {"_key": "r2", "md5Checksum": "abc", "indexingStatus": "QUEUED"}
        connected_provider.http_client.execute_aql.side_effect = [
            [ref],
            [dup],
        ]
        connected_provider.batch_update_nodes = AsyncMock(return_value=False)
        result = await connected_provider.update_queued_duplicates_status(
            record_id="r1", new_indexing_status="COMPLETED",
        )
        assert result == -1

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.update_queued_duplicates_status(
            record_id="r1", new_indexing_status="COMPLETED",
        )
        assert result == -1


# ===================================================================
# batch_upsert_record_permissions
# ===================================================================

class TestBatchUpsertRecordPermissions:
    async def test_success(self, connected_provider):
        perms = [{"_from": "users/u1", "_to": "records/r1", "role": "OWNER"}]
        connected_provider.http_client.execute_aql.return_value = []
        await connected_provider.batch_upsert_record_permissions("r1", perms)

    async def test_empty_permissions(self, connected_provider):
        await connected_provider.batch_upsert_record_permissions("r1", [])
        connected_provider.http_client.execute_aql.assert_not_awaited()

    async def test_exception_raises(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        with pytest.raises(Exception, match="err"):
            await connected_provider.batch_upsert_record_permissions(
                "r1", [{"_from": "users/u1", "_to": "records/r1"}],
            )


# ===================================================================
# get_file_permissions
# ===================================================================

class TestGetFilePermissions:
    async def test_success(self, connected_provider):
        edges = [{"_from": "users/u1", "_to": "records/r1", "role": "OWNER"}]
        connected_provider.http_client.execute_aql.return_value = edges
        result = await connected_provider.get_file_permissions("records/r1")
        assert len(result) == 1

    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_file_permissions("records/r1")
        assert result == []

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_file_permissions("records/r1")
        assert result == []


# ===================================================================
# get_first_user_with_permission_to_node
# ===================================================================

class TestGetFirstUserWithPermission:
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [_arango_user()]
        result = await connected_provider.get_first_user_with_permission_to_node(
            node_id="r1", node_collection="records",
        )
        assert result is not None
        assert result.email == "user@example.com"

    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_first_user_with_permission_to_node(
            node_id="r1", node_collection="records",
        )
        assert result is None

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_first_user_with_permission_to_node(
            node_id="r1", node_collection="records",
        )
        assert result is None


# ===================================================================
# get_users_with_permission_to_node
# ===================================================================

class TestGetUsersWithPermission:
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            _arango_user(key="u1"), _arango_user(key="u2", email="u2@example.com"),
        ]
        result = await connected_provider.get_users_with_permission_to_node(
            node_id="r1", node_collection="records",
        )
        assert len(result) == 2

    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_users_with_permission_to_node(
            node_id="r1", node_collection="records",
        )
        assert result == []

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_users_with_permission_to_node(
            node_id="r1", node_collection="records",
        )
        assert result == []


# ===================================================================
# get_record_owner_source_user_email
# ===================================================================

class TestGetRecordOwnerSourceUserEmail:
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["owner@example.com"]
        result = await connected_provider.get_record_owner_source_user_email("r1")
        assert result == "owner@example.com"

    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_record_owner_source_user_email("r1")
        assert result is None

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_record_owner_source_user_email("r1")
        assert result is None


# ===================================================================
# get_file_parents
# ===================================================================

class TestGetFileParents:
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [{
            "input_file_key": "f1",
            "found_relations": ["records/p1"],
            "parsed_parent_keys": [{"original_id": "records/p1", "parsed_key": "p1"}],
            "found_parent_files": [{"key": "p1", "externalRecordId": "ext-p1"}],
        }]
        result = await connected_provider.get_file_parents("f1")
        assert result == ["ext-p1"]

    async def test_no_parents(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [{
            "input_file_key": "f1",
            "found_relations": [],
            "parsed_parent_keys": [],
            "found_parent_files": [],
        }]
        result = await connected_provider.get_file_parents("f1")
        assert result == []

    async def test_empty_file_key(self, connected_provider):
        result = await connected_provider.get_file_parents("")
        assert result == []

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_file_parents("f1")
        assert result == []


# ===================================================================
# get_sync_point / upsert_sync_point / remove_sync_point
# ===================================================================

class TestSyncPoint:
    async def test_get_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"syncPointKey": "sp1", "value": "v1"}
        ]
        result = await connected_provider.get_sync_point("sp1", "syncPoints")
        assert result["syncPointKey"] == "sp1"

    async def test_get_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_sync_point("sp-nope", "syncPoints")
        assert result is None

    async def test_get_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_sync_point("sp1", "syncPoints")
        assert result is None

    async def test_upsert_insert(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [],  # get_sync_point returns empty
            [],  # insert
        ]
        result = await connected_provider.upsert_sync_point(
            "sp1", {"value": "v1"}, "syncPoints",
        )
        assert result is True

    async def test_upsert_update(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [{"syncPointKey": "sp1"}],  # existing
            [],  # update
        ]
        result = await connected_provider.upsert_sync_point(
            "sp1", {"value": "v2"}, "syncPoints",
        )
        assert result is True

    async def test_upsert_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        with pytest.raises(Exception, match="err"):
            await connected_provider.upsert_sync_point(
                "sp1", {"value": "v1"}, "syncPoints",
            )

    async def test_remove(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        await connected_provider.remove_sync_point("sp1", "syncPoints")
        connected_provider.http_client.execute_aql.assert_awaited_once()

    async def test_remove_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        with pytest.raises(Exception, match="err"):
            await connected_provider.remove_sync_point("sp1", "syncPoints")


# ===================================================================
# batch_upsert_record_groups
# ===================================================================

class TestBatchUpsertRecordGroups:
    async def test_success(self, connected_provider):
        rg = MagicMock()
        rg.to_arango_base_record_group.return_value = {"_key": "rg1"}
        connected_provider.batch_upsert_nodes = AsyncMock()
        await connected_provider.batch_upsert_record_groups([rg])
        connected_provider.batch_upsert_nodes.assert_awaited_once()

    async def test_exception_raises(self, connected_provider):
        rg = MagicMock()
        rg.to_arango_base_record_group.return_value = {"_key": "rg1"}
        connected_provider.batch_upsert_nodes = AsyncMock(side_effect=Exception("err"))
        with pytest.raises(Exception, match="err"):
            await connected_provider.batch_upsert_record_groups([rg])


# ===================================================================
# batch_upsert_records
# ===================================================================

class TestBatchUpsertRecords:
    async def test_success(self, connected_provider):
        rec = MagicMock()
        rec.id = "r1"
        rec.record_type = MagicMock()
        rec.record_type.__eq__ = lambda self, other: True
        rec.record_type.__hash__ = lambda self: hash("FILE")
        rec.to_arango_base_record.return_value = {"_key": "r1"}
        rec.to_arango_record.return_value = {"_key": "r1", "isFile": True}
        connected_provider.batch_upsert_nodes = AsyncMock()
        connected_provider.batch_create_edges = AsyncMock()
        result = await connected_provider.batch_upsert_records([rec])
        assert result is True

    async def test_exception_raises(self, connected_provider):
        from app.models.entities import RecordType
        rec = MagicMock()
        rec.id = "r1"
        rec.record_type = RecordType.FILE
        rec.to_arango_base_record.return_value = {"_key": "r1"}
        rec.to_arango_record.return_value = {"_key": "r1"}
        connected_provider.batch_upsert_nodes = AsyncMock(side_effect=Exception("err"))
        connected_provider.batch_create_edges = AsyncMock()
        with pytest.raises(Exception, match="err"):
            await connected_provider.batch_upsert_records([rec])


# ===================================================================
# batch_upsert_user_groups
# ===================================================================

class TestBatchUpsertUserGroups:
    async def test_success(self, connected_provider):
        ug = MagicMock()
        ug.to_arango_base_user_group.return_value = {"_key": "g1"}
        connected_provider.batch_upsert_nodes = AsyncMock()
        await connected_provider.batch_upsert_user_groups([ug])
        connected_provider.batch_upsert_nodes.assert_awaited_once()

    async def test_exception_raises(self, connected_provider):
        ug = MagicMock()
        ug.to_arango_base_user_group.return_value = {"_key": "g1"}
        connected_provider.batch_upsert_nodes = AsyncMock(side_effect=Exception("err"))
        with pytest.raises(Exception, match="err"):
            await connected_provider.batch_upsert_user_groups([ug])


# ===================================================================
# batch_upsert_app_roles
# ===================================================================

class TestBatchUpsertAppRoles:
    async def test_success(self, connected_provider):
        role = MagicMock()
        role.to_arango_base_role.return_value = {"_key": "r1"}
        connected_provider.batch_upsert_nodes = AsyncMock()
        await connected_provider.batch_upsert_app_roles([role])
        connected_provider.batch_upsert_nodes.assert_awaited_once()


# ===================================================================
# batch_upsert_people
# ===================================================================

class TestBatchUpsertPeople:
    async def test_success(self, connected_provider):
        person = MagicMock()
        person.to_arango_person.return_value = {"_key": "p1"}
        connected_provider.batch_upsert_nodes = AsyncMock()
        await connected_provider.batch_upsert_people([person])
        connected_provider.batch_upsert_nodes.assert_awaited_once()

    async def test_empty_list(self, connected_provider):
        connected_provider.batch_upsert_nodes = AsyncMock()
        await connected_provider.batch_upsert_people([])
        connected_provider.batch_upsert_nodes.assert_not_awaited()

    async def test_exception_raises(self, connected_provider):
        person = MagicMock()
        person.to_arango_person.return_value = {"_key": "p1"}
        connected_provider.batch_upsert_nodes = AsyncMock(side_effect=Exception("err"))
        with pytest.raises(Exception, match="err"):
            await connected_provider.batch_upsert_people([person])


# ===================================================================
# get_legacy_kb_record_groups
# ===================================================================

class TestGetLegacyKbRecordGroups:
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [_arango_record_group()]
        result = await connected_provider.get_legacy_kb_record_groups("org1")
        assert len(result) == 1

    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_legacy_kb_record_groups("org1")
        assert result == []

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_legacy_kb_record_groups("org1")
        assert result == []


# ===================================================================
# count_legacy_kb_record_groups
# ===================================================================

class TestCountLegacyKbRecordGroups:
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [3]
        result = await connected_provider.count_legacy_kb_record_groups("org1")
        assert result == 3

    async def test_empty_returns_zero(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.count_legacy_kb_record_groups("org1")
        assert result == 0

    async def test_exception_returns_negative(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.count_legacy_kb_record_groups("org1")
        assert result == -1


# ===================================================================
# _extract_legacy_record_group_ids (static method)
# ===================================================================

class TestExtractLegacyRecordGroupIds:
    def test_none_input(self):
        assert ArangoHTTPProvider._extract_legacy_record_group_ids(None) is None

    def test_empty_string(self):
        assert ArangoHTTPProvider._extract_legacy_record_group_ids("") is None

    def test_valid_json_string(self):
        import json
        raw = json.dumps({"recordGroups": ["rg1", "rg2"]})
        result = ArangoHTTPProvider._extract_legacy_record_group_ids(raw)
        assert result == ["rg1", "rg2"]

    def test_dict_input(self):
        result = ArangoHTTPProvider._extract_legacy_record_group_ids(
            {"recordGroups": ["rg1"]}
        )
        assert result == ["rg1"]

    def test_empty_record_groups(self):
        result = ArangoHTTPProvider._extract_legacy_record_group_ids(
            {"recordGroups": []}
        )
        assert result is None

    def test_no_record_groups_key(self):
        result = ArangoHTTPProvider._extract_legacy_record_group_ids({"other": "val"})
        assert result is None

    def test_non_list_record_groups(self):
        result = ArangoHTTPProvider._extract_legacy_record_group_ids(
            {"recordGroups": "not-a-list"}
        )
        assert result is None

    def test_invalid_json(self):
        assert ArangoHTTPProvider._extract_legacy_record_group_ids("{bad json") is None

    def test_non_dict_parsed(self):
        import json
        assert ArangoHTTPProvider._extract_legacy_record_group_ids(json.dumps([1, 2])) is None

    def test_filters_non_strings(self):
        result = ArangoHTTPProvider._extract_legacy_record_group_ids(
            {"recordGroups": ["rg1", 42, "", "rg2"]}
        )
        assert result == ["rg1", "rg2"]


# ===================================================================
# ensure_team_app_edge
# ===================================================================

class TestEnsureTeamAppEdge:
    async def test_edge_exists(self, connected_provider):
        connected_provider.get_edge = AsyncMock(return_value={"_key": "edge1"})
        await connected_provider.ensure_team_app_edge("conn1", "org1")
        connected_provider.batch_create_edges = AsyncMock()
        connected_provider.batch_create_edges.assert_not_awaited()

    async def test_edge_created(self, connected_provider):
        connected_provider.get_edge = AsyncMock(return_value=None)
        connected_provider.batch_create_edges = AsyncMock()
        await connected_provider.ensure_team_app_edge("conn1", "org1")
        connected_provider.batch_create_edges.assert_awaited_once()

    async def test_exception_raises(self, connected_provider):
        connected_provider.get_edge = AsyncMock(side_effect=Exception("err"))
        with pytest.raises(Exception, match="err"):
            await connected_provider.ensure_team_app_edge("conn1", "org1")


# ===================================================================
# delete_kb_hub_app
# ===================================================================

class TestDeleteKbHubApp:
    async def test_still_referenced(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"belongs_to_refs": 1, "permission_refs": 0}
        ]
        result = await connected_provider.delete_kb_hub_app("org1")
        assert result is False

    async def test_success_no_refs(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [{"belongs_to_refs": 0, "permission_refs": 0}],  # safety check
            [],  # org edges
            [],  # user edges
            [],  # delete
        ]
        connected_provider.delete_edge = AsyncMock()
        result = await connected_provider.delete_kb_hub_app("org1")
        assert result is True

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.delete_kb_hub_app("org1")
        assert result is False


# ===================================================================
# batch_upsert_app_users (complex method)
# ===================================================================

class TestBatchUpsertAppUsers:
    async def test_empty_list(self, connected_provider):
        await connected_provider.batch_upsert_app_users([])

    async def test_no_orgs_raises(self, connected_provider):
        user = MagicMock()
        user.connector_id = "conn1"
        connected_provider.http_client.execute_aql.return_value = []  # no orgs
        with pytest.raises(Exception, match="No organizations"):
            await connected_provider.batch_upsert_app_users([user])

    async def test_exception_raises(self, connected_provider):
        user = MagicMock()
        user.connector_id = "conn1"
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        with pytest.raises(Exception):
            await connected_provider.batch_upsert_app_users([user])


# ===================================================================
# add_user_to_all_team
# ===================================================================

class TestAddUserToAllTeam:
    async def test_already_exists(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "all_org1"})
        connected_provider.get_edge = AsyncMock(return_value={"_key": "edge1"})
        await connected_provider.add_user_to_all_team("org1", "u1")
        # Should not create edge

    async def test_first_user_gets_owner(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "all_org1"})
        connected_provider.get_edge = AsyncMock(return_value=None)
        connected_provider.get_team_with_users = AsyncMock(return_value={"members": []})
        connected_provider.update_node = AsyncMock()
        connected_provider.batch_create_edges = AsyncMock()
        await connected_provider.add_user_to_all_team("org1", "u1")
        edge_call = connected_provider.batch_create_edges.call_args[0][0][0]
        assert edge_call["role"] == "OWNER"

    async def test_subsequent_user_gets_reader(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "all_org1"})
        connected_provider.get_edge = AsyncMock(return_value=None)
        connected_provider.get_team_with_users = AsyncMock(
            return_value={"members": [{"userEmail": "first@example.com"}]}
        )
        connected_provider.batch_create_edges = AsyncMock()
        await connected_provider.add_user_to_all_team("org1", "u2")
        edge_call = connected_provider.batch_create_edges.call_args[0][0][0]
        assert edge_call["role"] == "READER"

    async def test_creates_team_if_missing(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value=None)
        connected_provider.batch_upsert_nodes = AsyncMock()
        connected_provider.get_edge = AsyncMock(return_value=None)
        connected_provider.get_team_with_users = AsyncMock(return_value={"members": []})
        connected_provider.update_node = AsyncMock()
        connected_provider.batch_create_edges = AsyncMock()
        await connected_provider.add_user_to_all_team("org1", "u1")
        connected_provider.batch_upsert_nodes.assert_awaited_once()

    async def test_exception_raises(self, connected_provider):
        connected_provider.get_document = AsyncMock(side_effect=Exception("err"))
        with pytest.raises(Exception, match="err"):
            await connected_provider.add_user_to_all_team("org1", "u1")


# ===================================================================
# batch_upsert_orgs
# ===================================================================

class TestBatchUpsertOrgs:
    async def test_success(self, connected_provider):
        connected_provider.batch_upsert_nodes = AsyncMock()
        await connected_provider.batch_upsert_orgs([{"id": "o1"}])
        connected_provider.batch_upsert_nodes.assert_awaited_once()

    async def test_empty_skips(self, connected_provider):
        connected_provider.batch_upsert_nodes = AsyncMock()
        await connected_provider.batch_upsert_orgs([])
        connected_provider.batch_upsert_nodes.assert_not_awaited()

    async def test_exception_raises(self, connected_provider):
        connected_provider.batch_upsert_nodes = AsyncMock(side_effect=Exception("err"))
        with pytest.raises(Exception, match="err"):
            await connected_provider.batch_upsert_orgs([{"id": "o1"}])


# ===================================================================
# batch_upsert_domains
# ===================================================================

@patch.object(CollectionNames, "DOMAINS", create=True, new=MagicMock(value="domains"))
class TestBatchUpsertDomains:
    async def test_success(self, connected_provider):
        connected_provider.batch_upsert_nodes = AsyncMock()
        await connected_provider.batch_upsert_domains([{"id": "d1"}])
        connected_provider.batch_upsert_nodes.assert_awaited_once()

    async def test_empty_skips(self, connected_provider):
        connected_provider.batch_upsert_nodes = AsyncMock()
        await connected_provider.batch_upsert_domains([])
        connected_provider.batch_upsert_nodes.assert_not_awaited()

    async def test_exception_raises(self, connected_provider):
        connected_provider.batch_upsert_nodes = AsyncMock(side_effect=Exception("err"))
        with pytest.raises(Exception, match="err"):
            await connected_provider.batch_upsert_domains([{"id": "d1"}])


# ===================================================================
# batch_upsert_anyone
# ===================================================================

class TestBatchUpsertAnyone:
    async def test_success(self, connected_provider):
        connected_provider.batch_upsert_nodes = AsyncMock()
        await connected_provider.batch_upsert_anyone([{"id": "a1"}])
        connected_provider.batch_upsert_nodes.assert_awaited_once()

    async def test_empty_skips(self, connected_provider):
        connected_provider.batch_upsert_nodes = AsyncMock()
        await connected_provider.batch_upsert_anyone([])
        connected_provider.batch_upsert_nodes.assert_not_awaited()

    async def test_exception_raises(self, connected_provider):
        connected_provider.batch_upsert_nodes = AsyncMock(side_effect=Exception("err"))
        with pytest.raises(Exception, match="err"):
            await connected_provider.batch_upsert_anyone([{"id": "a1"}])


# ===================================================================
# batch_upsert_anyone_with_link
# ===================================================================

@patch.object(CollectionNames, "ANYONE_WITH_LINK", create=True, new=MagicMock(value="anyoneWithLink"))
class TestBatchUpsertAnyoneWithLink:
    async def test_success(self, connected_provider):
        connected_provider.batch_upsert_nodes = AsyncMock()
        await connected_provider.batch_upsert_anyone_with_link([{"id": "a1"}])
        connected_provider.batch_upsert_nodes.assert_awaited_once()

    async def test_empty_skips(self, connected_provider):
        connected_provider.batch_upsert_nodes = AsyncMock()
        await connected_provider.batch_upsert_anyone_with_link([])
        connected_provider.batch_upsert_nodes.assert_not_awaited()

    async def test_exception_raises(self, connected_provider):
        connected_provider.batch_upsert_nodes = AsyncMock(side_effect=Exception("err"))
        with pytest.raises(Exception, match="err"):
            await connected_provider.batch_upsert_anyone_with_link([{"id": "a1"}])


# ===================================================================
# batch_upsert_anyone_same_org
# ===================================================================

@patch.object(CollectionNames, "ANYONE_SAME_ORG", create=True, new=MagicMock(value="anyoneSameOrg"))
class TestBatchUpsertAnyoneSameOrg:
    async def test_success(self, connected_provider):
        connected_provider.batch_upsert_nodes = AsyncMock()
        await connected_provider.batch_upsert_anyone_same_org([{"id": "a1"}])
        connected_provider.batch_upsert_nodes.assert_awaited_once()

    async def test_empty_skips(self, connected_provider):
        connected_provider.batch_upsert_nodes = AsyncMock()
        await connected_provider.batch_upsert_anyone_same_org([])
        connected_provider.batch_upsert_nodes.assert_not_awaited()

    async def test_exception_raises(self, connected_provider):
        connected_provider.batch_upsert_nodes = AsyncMock(side_effect=Exception("err"))
        with pytest.raises(Exception, match="err"):
            await connected_provider.batch_upsert_anyone_same_org([{"id": "a1"}])


# ===================================================================
# batch_create_user_app_edges
# ===================================================================

@patch.object(CollectionNames, "USER_APP", create=True, new=MagicMock(value="userApp"))
class TestBatchCreateUserAppEdges:
    async def test_success(self, connected_provider):
        edges = [{"_from": "users/u1", "_to": "apps/a1"}]
        connected_provider.batch_create_edges = AsyncMock()
        result = await connected_provider.batch_create_user_app_edges(edges)
        assert result == 1

    async def test_empty_returns_zero(self, connected_provider):
        result = await connected_provider.batch_create_user_app_edges([])
        assert result == 0

    async def test_exception_raises(self, connected_provider):
        connected_provider.batch_create_edges = AsyncMock(side_effect=Exception("err"))
        with pytest.raises(Exception, match="err"):
            await connected_provider.batch_create_user_app_edges(
                [{"_from": "users/u1", "_to": "apps/a1"}]
            )


# ===================================================================
# get_entity_id_by_email
# ===================================================================

class TestGetEntityIdByEmail:
    async def test_found_in_users(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["u1"]
        result = await connected_provider.get_entity_id_by_email("user@example.com")
        assert result == "u1"

    async def test_found_in_groups(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [],      # users
            ["g1"],  # groups
        ]
        result = await connected_provider.get_entity_id_by_email("group@example.com")
        assert result == "g1"

    async def test_found_in_people(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [],      # users
            [],      # groups
            ["p1"],  # people
        ]
        result = await connected_provider.get_entity_id_by_email("external@example.com")
        assert result == "p1"

    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [[], [], []]
        result = await connected_provider.get_entity_id_by_email("nobody@example.com")
        assert result is None

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_entity_id_by_email("user@example.com")
        assert result is None


# ===================================================================
# bulk_get_entity_ids_by_email
# ===================================================================

class TestBulkGetEntityIdsByEmail:
    async def test_empty_input(self, connected_provider):
        result = await connected_provider.bulk_get_entity_ids_by_email([])
        assert result == {}

    async def test_found_in_users(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [{"email": "user@example.com", "id": "u1"}],  # users
            [],  # groups (remaining)
            [],  # people (remaining)
        ]
        result = await connected_provider.bulk_get_entity_ids_by_email(
            ["user@example.com"]
        )
        assert "user@example.com" in result
        assert result["user@example.com"][0] == "u1"

    async def test_exception_returns_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.bulk_get_entity_ids_by_email(
            ["user@example.com"]
        )
        assert result == {}


# ===================================================================
# store_permission
# ===================================================================

class TestStorePermission:
    async def test_empty_entity_key(self, connected_provider):
        result = await connected_provider.store_permission(
            file_key="r1", entity_key="",
            permission_data={"type": "USER", "role": "READER"},
        )
        assert result is False

    async def test_new_permission(self, connected_provider):
        connected_provider.get_file_permissions = AsyncMock(return_value=[])
        connected_provider.http_client.execute_aql.return_value = []
        connected_provider.batch_upsert_nodes = AsyncMock()

        result = await connected_provider.store_permission(
            file_key="r1", entity_key="u1",
            permission_data={"type": "USER", "role": "READER", "id": "perm1"},
        )
        assert result is True
        connected_provider.batch_upsert_nodes.assert_awaited()

    async def test_existing_needs_update(self, connected_provider):
        existing = [{"_from": "users/u1", "_to": "records/r1", "_key": "e1",
                     "role": "READER"}]
        connected_provider.get_file_permissions = AsyncMock(return_value=existing)
        connected_provider.http_client.execute_aql.return_value = [existing[0]]
        connected_provider.batch_upsert_nodes = AsyncMock()
        connected_provider._permission_needs_update = MagicMock(return_value=True)

        result = await connected_provider.store_permission(
            file_key="r1", entity_key="u1",
            permission_data={"type": "USER", "role": "WRITER", "id": "perm1"},
        )
        assert result is True

    async def test_existing_no_update(self, connected_provider):
        existing = [{"_from": "users/u1", "_to": "records/r1", "_key": "e1",
                     "role": "READER"}]
        connected_provider.get_file_permissions = AsyncMock(return_value=existing)
        connected_provider.http_client.execute_aql.return_value = [existing[0]]
        connected_provider.batch_upsert_nodes = AsyncMock()
        connected_provider._permission_needs_update = MagicMock(return_value=False)

        result = await connected_provider.store_permission(
            file_key="r1", entity_key="u1",
            permission_data={"type": "USER", "role": "READER", "id": "perm1"},
        )
        assert result is True

    async def test_domain_type_uses_orgs(self, connected_provider):
        connected_provider.get_file_permissions = AsyncMock(return_value=[])
        connected_provider.http_client.execute_aql.return_value = []
        connected_provider.batch_upsert_nodes = AsyncMock()

        result = await connected_provider.store_permission(
            file_key="r1", entity_key="d1",
            permission_data={"type": "DOMAIN", "role": "READER", "id": "perm1"},
        )
        assert result is True
        edge = connected_provider.batch_upsert_nodes.call_args[0][0][0]
        assert edge["_from"].startswith("organizations/")

    async def test_exception_without_txn(self, connected_provider):
        connected_provider.get_file_permissions = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider.store_permission(
            file_key="r1", entity_key="u1",
            permission_data={"type": "USER", "role": "READER"},
        )
        assert result is False

    async def test_exception_with_txn_raises(self, connected_provider):
        connected_provider.get_file_permissions = AsyncMock(side_effect=Exception("err"))
        with pytest.raises(Exception, match="err"):
            await connected_provider.store_permission(
                file_key="r1", entity_key="u1",
                permission_data={"type": "USER", "role": "READER"},
                transaction="txn1",
            )


# ===================================================================
# _permission_needs_update
# ===================================================================

class TestPermissionNeedsUpdate:
    def test_role_changed(self, connected_provider):
        assert connected_provider._permission_needs_update(
            {"role": "READER"}, {"role": "WRITER"}
        ) is True

    def test_no_change(self, connected_provider):
        assert connected_provider._permission_needs_update(
            {"role": "READER", "active": True},
            {"role": "READER", "active": True},
        ) is False

    def test_active_changed(self, connected_provider):
        assert connected_provider._permission_needs_update(
            {"active": True}, {"active": False}
        ) is True

    def test_permission_details_changed(self, connected_provider):
        assert connected_provider._permission_needs_update(
            {"permissionDetails": {"level": 1}},
            {"permissionDetails": {"level": 2}},
        ) is True

    def test_permission_details_same(self, connected_provider):
        assert connected_provider._permission_needs_update(
            {"permissionDetails": {"a": 1, "b": 2}},
            {"permissionDetails": {"b": 2, "a": 1}},
        ) is False


# ===================================================================
# delete_records_and_relations
# ===================================================================

class TestDeleteRecordsAndRelations:
    async def test_success(self, connected_provider):
        record_doc = _arango_record(key="r1")
        connected_provider.http_client.get_document.return_value = record_doc
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.delete_records_and_relations("r1")
        assert result is True

    async def test_hard_delete(self, connected_provider):
        record_doc = _arango_record(key="r1")
        connected_provider.http_client.get_document.return_value = record_doc
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.delete_records_and_relations(
            "r1", hard_delete=True
        )
        assert result is True

    async def test_exception_without_txn(self, connected_provider):
        connected_provider.http_client.get_document.side_effect = Exception("err")
        result = await connected_provider.delete_records_and_relations("r1")
        assert result is False

    async def test_exception_with_txn_raises(self, connected_provider):
        connected_provider.http_client.get_document.side_effect = Exception("err")
        with pytest.raises(Exception, match="err"):
            await connected_provider.delete_records_and_relations(
                "r1", transaction="txn1"
            )


# ===================================================================
# delete_record
# ===================================================================

class TestDeleteRecord:
    async def test_record_not_found(self, connected_provider):
        connected_provider.http_client.get_document.return_value = None
        result = await connected_provider.delete_record("r1", "u1")
        assert result["success"] is False
        assert result["code"] == 404

    async def test_exception(self, connected_provider):
        connected_provider.http_client.get_document.side_effect = Exception("err")
        result = await connected_provider.delete_record("r1", "u1")
        assert result["success"] is False
        assert result["code"] == 500


# ===================================================================
# delete_record_by_external_id
# ===================================================================

class TestDeleteRecordByExternalId:
    async def test_record_not_found(self, connected_provider):
        connected_provider.get_record_by_external_id = AsyncMock(return_value=None)
        await connected_provider.delete_record_by_external_id(
            connector_id="conn1", external_id="ext-nope", user_id="u1",
        )


# ===================================================================
# get_key_by_external_file_id
# ===================================================================

class TestGetKeyByExternalFileId:
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["key1"]
        result = await connected_provider.get_key_by_external_file_id("ext-f1")
        assert result == "key1"

    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_key_by_external_file_id("missing")
        assert result is None

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_key_by_external_file_id("ext-f1")
        assert result is None


# ===================================================================
# get_key_by_external_message_id
# ===================================================================

class TestGetKeyByExternalMessageId:
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["key1"]
        result = await connected_provider.get_key_by_external_message_id("ext-m1")
        assert result == "key1"

    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_key_by_external_message_id("missing")
        assert result is None

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_key_by_external_message_id("ext-m1")
        assert result is None


# ===================================================================
# get_related_records_by_relation_type
# ===================================================================

class TestGetRelatedRecordsByRelationType:
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"_key": "r2", "messageId": "msg1", "relationshipType": "REPLY"}
        ]
        result = await connected_provider.get_related_records_by_relation_type(
            record_id="r1", relation_type="REPLY",
            edge_collection="recordRelations",
        )
        assert len(result) == 1

    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_related_records_by_relation_type(
            record_id="r1", relation_type="REPLY",
            edge_collection="recordRelations",
        )
        assert result == []

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_related_records_by_relation_type(
            record_id="r1", relation_type="REPLY",
            edge_collection="recordRelations",
        )
        assert result == []


# ===================================================================
# get_message_id_header_by_key
# ===================================================================

class TestGetMessageIdHeaderByKey:
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["<msg@mail.com>"]
        result = await connected_provider.get_message_id_header_by_key(
            record_key="r1", collection="mails",
        )
        assert result == "<msg@mail.com>"

    async def test_null_value(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [None]
        result = await connected_provider.get_message_id_header_by_key(
            record_key="r1", collection="mails",
        )
        assert result is None

    async def test_empty_results(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_message_id_header_by_key(
            record_key="r1", collection="mails",
        )
        assert result is None

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_message_id_header_by_key(
            record_key="r1", collection="mails",
        )
        assert result is None


# ===================================================================
# get_related_mails_by_message_id_header
# ===================================================================

class TestGetRelatedMailsByMessageIdHeader:
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["k2", "k3"]
        result = await connected_provider.get_related_mails_by_message_id_header(
            message_id_header="<msg@test.com>",
            exclude_key="k1", collection="mails",
        )
        assert result == ["k2", "k3"]

    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_related_mails_by_message_id_header(
            message_id_header="<msg@test.com>",
            exclude_key="k1", collection="mails",
        )
        assert result == []

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_related_mails_by_message_id_header(
            message_id_header="<msg@test.com>",
            exclude_key="k1", collection="mails",
        )
        assert result == []


# ===================================================================
# count_connector_instances_by_scope
# ===================================================================

class TestCountConnectorInstancesByScope:
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [5]
        result = await connected_provider.count_connector_instances_by_scope(
            collection="apps", scope="PERSONAL", user_id="uid1",
        )
        assert result == 5

    async def test_empty_result(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.count_connector_instances_by_scope(
            collection="apps", scope="PERSONAL",
        )
        assert result == 0

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.count_connector_instances_by_scope(
            collection="apps", scope="PERSONAL",
        )
        assert result == 0


# ===================================================================
# check_connector_name_uniqueness
# ===================================================================

class TestCheckConnectorNameUniqueness:
    async def test_unique_personal(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.check_connector_name_uniqueness(
            instance_name="My Drive", scope="personal",
            org_id="org1", user_id="uid1", collection="apps",
        )
        assert result is True

    async def test_not_unique_personal(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [{"name": "my drive"}]
        result = await connected_provider.check_connector_name_uniqueness(
            instance_name="My Drive", scope="personal",
            org_id="org1", user_id="uid1", collection="apps",
        )
        assert result is False

    async def test_team_scope(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.check_connector_name_uniqueness(
            instance_name="Team Drive", scope="team",
            org_id="org1", user_id="uid1", collection="apps",
            edge_collection="orgAppRelation",
        )
        assert result is True

    async def test_exception_returns_true(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.check_connector_name_uniqueness(
            instance_name="Drive", scope="personal",
            org_id="org1", user_id="uid1", collection="apps",
        )
        assert result is True


# ===================================================================
# get_connector_instances_with_filters
# ===================================================================

class TestGetConnectorInstancesWithFilters:
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [3],
            [{"_key": "a1"}, {"_key": "a2"}, {"_key": "a3"}],
        ]
        docs, total = await connected_provider.get_connector_instances_with_filters(
            collection="apps",
        )
        assert total == 3
        assert len(docs) == 3

    async def test_with_scope_and_search(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [1],
            [{"_key": "a1", "name": "Drive"}],
        ]
        docs, total = await connected_provider.get_connector_instances_with_filters(
            collection="apps", scope="PERSONAL",
            user_id="uid1", search="Drive",
        )
        assert total == 1
        assert len(docs) == 1

    async def test_admin_scope(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [2],
            [{"_key": "a1"}, {"_key": "a2"}],
        ]
        docs, total = await connected_provider.get_connector_instances_with_filters(
            collection="apps", is_admin=True,
        )
        assert total == 2

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        docs, total = await connected_provider.get_connector_instances_with_filters(
            collection="apps",
        )
        assert docs == []
        assert total == 0


# ===================================================================
# get_connector_instances_by_scope_and_user
# ===================================================================

class TestGetConnectorInstancesByScopeAndUser:
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"_key": "a1", "scope": "TEAM"}
        ]
        result = await connected_provider.get_connector_instances_by_scope_and_user(
            collection="apps", user_id="uid1",
            team_scope="TEAM", personal_scope="PERSONAL",
        )
        assert len(result) == 1

    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = None
        result = await connected_provider.get_connector_instances_by_scope_and_user(
            collection="apps", user_id="uid1",
            team_scope="TEAM", personal_scope="PERSONAL",
        )
        assert result == []

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.get_connector_instances_by_scope_and_user(
            collection="apps", user_id="uid1",
            team_scope="TEAM", personal_scope="PERSONAL",
        )
        assert result == []


# ===================================================================
# get_user_sync_state
# ===================================================================

class TestGetUserSyncState:
    async def test_found(self, connected_provider):
        connected_provider.get_entity_id_by_email = AsyncMock(return_value="u1")
        connected_provider.http_client.execute_aql.return_value = [
            {"syncState": "IN_PROGRESS", "_key": "edge1"}
        ]
        result = await connected_provider.get_user_sync_state(
            user_email="user@example.com", service_type="GOOGLE_DRIVE",
        )
        assert result is not None
        assert result["syncState"] == "IN_PROGRESS"

    async def test_user_not_found(self, connected_provider):
        connected_provider.get_entity_id_by_email = AsyncMock(return_value=None)
        result = await connected_provider.get_user_sync_state(
            user_email="none@example.com", service_type="GOOGLE_DRIVE",
        )
        assert result is None

    async def test_no_sync_edge(self, connected_provider):
        connected_provider.get_entity_id_by_email = AsyncMock(return_value="u1")
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_user_sync_state(
            user_email="user@example.com", service_type="GOOGLE_DRIVE",
        )
        assert result is None

    async def test_exception(self, connected_provider):
        connected_provider.get_entity_id_by_email = AsyncMock(
            side_effect=Exception("err")
        )
        result = await connected_provider.get_user_sync_state(
            user_email="user@example.com", service_type="GOOGLE_DRIVE",
        )
        assert result is None


# ===================================================================
# update_user_sync_state
# ===================================================================

class TestUpdateUserSyncState:
    async def test_success(self, connected_provider):
        connected_provider.get_entity_id_by_email = AsyncMock(return_value="u1")
        connected_provider.http_client.execute_aql.return_value = [
            {"syncState": "COMPLETED"}
        ]
        result = await connected_provider.update_user_sync_state(
            user_email="user@example.com", state="COMPLETED",
        )
        assert result is not None

    async def test_user_not_found(self, connected_provider):
        connected_provider.get_entity_id_by_email = AsyncMock(return_value=None)
        result = await connected_provider.update_user_sync_state(
            user_email="none@example.com", state="COMPLETED",
        )
        assert result is None

    async def test_exception(self, connected_provider):
        connected_provider.get_entity_id_by_email = AsyncMock(
            side_effect=Exception("err")
        )
        result = await connected_provider.update_user_sync_state(
            user_email="user@example.com", state="COMPLETED",
        )
        assert result is None


# ===================================================================
# get_drive_sync_state
# ===================================================================

class TestGetDriveSyncState:
    async def test_found(self, connected_provider):
        connected_provider.get_nodes_by_filters = AsyncMock(
            return_value=[{"id": "d1", "sync_state": "COMPLETED"}]
        )
        result = await connected_provider.get_drive_sync_state("d1")
        assert result == "COMPLETED"

    async def test_not_found(self, connected_provider):
        connected_provider.get_nodes_by_filters = AsyncMock(return_value=[])
        result = await connected_provider.get_drive_sync_state("d1")
        assert result == "NOT_STARTED"


# ===================================================================
# migrate_agent_hub_knowledge
# ===================================================================

class TestMigrateAgentHubKnowledge:
    async def test_no_target_apps(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.migrate_agent_hub_knowledge("org1")
        assert result["agents_migrated"] == 0
        assert result["knowledge_nodes_created"] == 0

    async def test_no_matching_rows(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=[
            ["kb1"],   # target apps
            [],        # no matching rows
        ])
        result = await connected_provider.migrate_agent_hub_knowledge("org1")
        assert result["agents_migrated"] == 0

    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider.migrate_agent_hub_knowledge("org1")
        assert "error" in result


# ===================================================================
# process_file_permissions
# ===================================================================

class TestProcessFilePermissions:
    async def test_empty_permissions(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        connected_provider.get_file_permissions = AsyncMock(return_value=[])
        result = await connected_provider.process_file_permissions(
            org_id="org1", file_key="r1", permissions_data=[],
        )
        assert result is True

    async def test_exception_without_txn(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        result = await connected_provider.process_file_permissions(
            org_id="org1", file_key="r1", permissions_data=[{"id": "p1"}],
        )
        assert result is False

    async def test_exception_with_txn_raises(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("err")
        with pytest.raises(Exception):
            await connected_provider.process_file_permissions(
                org_id="org1", file_key="r1",
                permissions_data=[{"id": "p1"}],
                transaction="txn1",
            )


# ===================================================================
# delete_google_drive_record
# ===================================================================

class TestDeleteGoogleDriveRecord:
    async def test_user_not_found(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await connected_provider.delete_google_drive_record(
            record_id="r1", user_id="u1",
            record=_arango_record(), transaction=None,
        )
        assert result["code"] == 404

    async def test_exception(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(
            side_effect=Exception("err")
        )
        result = await connected_provider.delete_google_drive_record(
            record_id="r1", user_id="u1",
            record=_arango_record(), transaction=None,
        )
        assert result["success"] is False
        assert result["code"] == 500


# ===================================================================
# delete_gmail_record
# ===================================================================

class TestDeleteGmailRecord:
    async def test_user_not_found(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await connected_provider.delete_gmail_record(
            record_id="r1", user_id="u1",
            record=_arango_record(), transaction=None,
        )
        assert result["code"] == 404

    async def test_exception(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(
            side_effect=Exception("err")
        )
        result = await connected_provider.delete_gmail_record(
            record_id="r1", user_id="u1",
            record=_arango_record(), transaction=None,
        )
        assert result["success"] is False
        assert result["code"] == 500


# ===================================================================
# delete_outlook_record
# ===================================================================

class TestDeleteOutlookRecord:
    async def test_user_not_found(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await connected_provider.delete_outlook_record(
            record_id="r1", user_id="u1",
            record=_arango_record(), transaction=None,
        )
        assert result["code"] == 404

    async def test_exception(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(
            side_effect=Exception("err")
        )
        result = await connected_provider.delete_outlook_record(
            record_id="r1", user_id="u1",
            record=_arango_record(), transaction=None,
        )
        assert result["success"] is False
        assert result["code"] == 500


# ===================================================================
# delete_local_fs_record
# ===================================================================

class TestDeleteLocalFsRecord:
    async def test_user_not_found(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(return_value=None)
        connected_provider.http_client.get_document = AsyncMock(return_value=None)
        result = await connected_provider.delete_local_fs_record(
            record_id="r1", user_id="u1",
            record=_arango_record(), transaction=None,
        )
        assert result["code"] == 404

    async def test_exception(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(
            side_effect=Exception("err")
        )
        result = await connected_provider.delete_local_fs_record(
            record_id="r1", user_id="u1",
            record=_arango_record(), transaction=None,
        )
        assert result["success"] is False
        assert result["code"] == 500


# ===================================================================
# _collect_connector_entities  (lines 7675-7763)
# ===================================================================

class TestCollectConnectorEntities:
    async def test_collects_all_entity_types(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [{"_key": "r1", "virtualRecordId": "vr1"}, {"_key": "r2"}],
            ["rg1", "rg2"],
            ["role1"],
            ["grp1"],
        ]
        result = await connected_provider._collect_connector_entities("c1")
        assert result["record_keys"] == ["r1", "r2"]
        assert result["record_ids"] == ["records/r1", "records/r2"]
        assert result["virtual_record_ids"] == ["vr1"]
        assert result["record_group_keys"] == ["rg1", "rg2"]
        assert result["role_keys"] == ["role1"]
        assert result["group_keys"] == ["grp1"]
        assert "records/r1" in result["all_node_ids"]
        assert "records/r2" in result["all_node_ids"]
        assert "recordGroups/rg1" in result["all_node_ids"]
        assert "recordGroups/rg2" in result["all_node_ids"]
        assert "roles/role1" in result["all_node_ids"]
        assert "groups/grp1" in result["all_node_ids"]
        assert "apps/c1" in result["all_node_ids"]

    async def test_empty_results(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            None, None, None, None,
        ]
        result = await connected_provider._collect_connector_entities("c1")
        assert result["record_keys"] == []
        assert result["record_ids"] == []
        assert result["virtual_record_ids"] == []
        assert result["record_group_keys"] == []
        assert result["role_keys"] == []
        assert result["group_keys"] == []
        assert result["all_node_ids"] == ["apps/c1"]

    async def test_records_without_virtual_record_id(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [{"_key": "r1"}, {"_key": "r2", "virtualRecordId": None}],
            [], [], [],
        ]
        result = await connected_provider._collect_connector_entities("c1")
        assert result["record_keys"] == ["r1", "r2"]
        assert result["virtual_record_ids"] == []

    async def test_transaction_forwarded(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [], [], [], [],
        ]
        await connected_provider._collect_connector_entities("c1", transaction="txn1")
        for call in connected_provider.http_client.execute_aql.call_args_list:
            assert call.kwargs.get("txn_id") == "txn1"


# ===================================================================
# _get_all_edge_collections  (lines 7765-7786)
# ===================================================================

class TestGetAllEdgeCollections:
    async def test_success_nested_format(self, connected_provider):
        connected_provider.http_client.get_graph.return_value = {
            "graph": {
                "edgeDefinitions": [
                    {"collection": "belongsTo"},
                    {"collection": "permission"},
                    {"collection": "isOfType"},
                ]
            }
        }
        result = await connected_provider._get_all_edge_collections()
        assert result == ["belongsTo", "permission", "isOfType"]

    async def test_success_direct_format(self, connected_provider):
        connected_provider.http_client.get_graph.return_value = {
            "edgeDefinitions": [{"collection": "belongsTo"}]
        }
        result = await connected_provider._get_all_edge_collections()
        assert result == ["belongsTo"]

    async def test_no_graph_raises(self, connected_provider):
        connected_provider.http_client.get_graph.return_value = None
        with pytest.raises(Exception, match="not found"):
            await connected_provider._get_all_edge_collections()

    async def test_no_edge_definitions_raises(self, connected_provider):
        connected_provider.http_client.get_graph.return_value = {
            "graph": {"edgeDefinitions": []}
        }
        with pytest.raises(Exception, match="no edge collections"):
            await connected_provider._get_all_edge_collections()

    async def test_filters_none_collections(self, connected_provider):
        connected_provider.http_client.get_graph.return_value = {
            "graph": {
                "edgeDefinitions": [
                    {"collection": "belongsTo"},
                    {},
                    {"collection": None},
                ]
            }
        }
        result = await connected_provider._get_all_edge_collections()
        assert result == ["belongsTo"]


# ===================================================================
# _delete_edges_by_connector_id  (lines 7788-7919)
# ===================================================================

class TestDeleteEdgesByConnectorId:
    async def test_success_counts_all_queries(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [1]
        total, failed = await connected_provider._delete_edges_by_connector_id(
            None, "c1", ["belongsTo"]
        )
        # 4 node collections * 2 (from/to) + 2 app queries = 10 calls per edge coll
        assert connected_provider.http_client.execute_aql.call_count == 10
        assert total == 10
        assert failed == []

    async def test_failure_recorded_per_collection(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        total, failed = await connected_provider._delete_edges_by_connector_id(
            None, "c1", ["failEdge", "okEdge"]
        )
        assert "failEdge" in failed
        assert "okEdge" in failed

    async def test_empty_edge_collections(self, connected_provider):
        total, failed = await connected_provider._delete_edges_by_connector_id(
            None, "c1", []
        )
        assert total == 0
        assert failed == []

    async def test_none_results_handled(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = None
        total, failed = await connected_provider._delete_edges_by_connector_id(
            None, "c1", ["belongsTo"]
        )
        assert total == 0
        assert failed == []

    async def test_transaction_forwarded(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        await connected_provider._delete_edges_by_connector_id(
            "txn1", "c1", ["belongsTo"]
        )
        for call in connected_provider.http_client.execute_aql.call_args_list:
            assert call.kwargs.get("txn_id") == "txn1"


# ===================================================================
# _delete_edges_by_node_ids  (lines 7921-7952)
# ===================================================================

class TestDeleteEdgesByNodeIds:
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [1, 1]
        total, failed = await connected_provider._delete_edges_by_node_ids(
            None, ["records/r1", "records/r2"], ["belongsTo"]
        )
        assert total == 2
        assert failed == []

    async def test_exception_collects_failure(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        total, failed = await connected_provider._delete_edges_by_node_ids(
            None, ["records/r1"], ["belongsTo"]
        )
        assert total == 0
        assert "belongsTo" in failed

    async def test_batching(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [1]
        node_ids = [f"records/r{i}" for i in range(12)]
        total, failed = await connected_provider._delete_edges_by_node_ids(
            None, node_ids, ["belongsTo"], batch_size=5
        )
        assert connected_provider.http_client.execute_aql.call_count == 3
        assert failed == []

    async def test_empty_node_ids(self, connected_provider):
        total, failed = await connected_provider._delete_edges_by_node_ids(
            None, [], ["belongsTo"]
        )
        assert total == 0
        assert failed == []

    async def test_multiple_edge_collections(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [1]
        total, failed = await connected_provider._delete_edges_by_node_ids(
            None, ["records/r1"], ["belongsTo", "permission"]
        )
        assert connected_provider.http_client.execute_aql.call_count == 2
        assert total == 2
        assert failed == []


# ===================================================================
# _collect_isoftype_targets  (lines 7954-7989)
# ===================================================================

class TestCollectIsoftypeTargets:
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"collection": "files", "key": "f1", "full_id": "files/f1"},
            {"collection": "mails", "key": "m1", "full_id": "mails/m1"},
        ]
        targets, success = await connected_provider._collect_isoftype_targets(None, "c1")
        assert success is True
        assert len(targets) == 2

    async def test_empty_connector_id(self, connected_provider):
        targets, success = await connected_provider._collect_isoftype_targets(None, "")
        assert targets == []
        assert success is True

    async def test_none_connector_id(self, connected_provider):
        targets, success = await connected_provider._collect_isoftype_targets(None, None)
        assert targets == []
        assert success is True

    async def test_no_results(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = None
        targets, success = await connected_provider._collect_isoftype_targets(None, "c1")
        assert targets == []
        assert success is True

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        targets, success = await connected_provider._collect_isoftype_targets(None, "c1")
        assert targets == []
        assert success is False


# ===================================================================
# _delete_isoftype_targets_from_collected  (lines 7991-8052)
# ===================================================================

class TestDeleteIsoftypeTargetsFromCollected:
    async def test_empty_targets(self, connected_provider):
        deleted, failed = await connected_provider._delete_isoftype_targets_from_collected(
            "txn1", [], []
        )
        assert deleted == 0
        assert failed == []

    async def test_success_single_collection(self, connected_provider):
        targets = [
            {"collection": "files", "key": "f1", "full_id": "files/f1"},
            {"collection": "files", "key": "f2", "full_id": "files/f2"},
        ]
        connected_provider._delete_nodes_by_keys = AsyncMock(return_value=(2, 0))
        deleted, failed = await connected_provider._delete_isoftype_targets_from_collected(
            "txn1", targets, []
        )
        assert deleted == 2
        assert failed == []

    async def test_success_multiple_collections(self, connected_provider):
        targets = [
            {"collection": "files", "key": "f1", "full_id": "files/f1"},
            {"collection": "mails", "key": "m1", "full_id": "mails/m1"},
        ]
        connected_provider._delete_nodes_by_keys = AsyncMock(return_value=(1, 0))
        deleted, failed = await connected_provider._delete_isoftype_targets_from_collected(
            "txn1", targets, []
        )
        assert deleted == 2

    async def test_failed_batches_raises(self, connected_provider):
        targets = [{"collection": "files", "key": "f1", "full_id": "files/f1"}]
        connected_provider._delete_nodes_by_keys = AsyncMock(return_value=(0, 1))
        with pytest.raises(Exception, match="CRITICAL"):
            await connected_provider._delete_isoftype_targets_from_collected(
                "txn1", targets, []
            )

    async def test_partial_deletion_raises(self, connected_provider):
        targets = [
            {"collection": "files", "key": "f1", "full_id": "files/f1"},
            {"collection": "files", "key": "f2", "full_id": "files/f2"},
        ]
        connected_provider._delete_nodes_by_keys = AsyncMock(return_value=(1, 0))
        with pytest.raises(Exception, match="CRITICAL"):
            await connected_provider._delete_isoftype_targets_from_collected(
                "txn1", targets, []
            )


# ===================================================================
# _delete_nodes_by_keys  (lines 8054-8103)
# ===================================================================

class TestDeleteNodesByKeys:
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [1, 1]
        deleted, failed = await connected_provider._delete_nodes_by_keys(
            "txn1", ["k1", "k2"], "records"
        )
        assert deleted == 2
        assert failed == 0

    async def test_empty_keys(self, connected_provider):
        deleted, failed = await connected_provider._delete_nodes_by_keys(
            "txn1", [], "records"
        )
        assert deleted == 0
        assert failed == 0

    async def test_batch_failure_continues(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        deleted, failed = await connected_provider._delete_nodes_by_keys(
            "txn1", ["k1"], "records"
        )
        assert deleted == 0
        assert failed == 1

    async def test_batching_works(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [1]
        keys = [f"k{i}" for i in range(12)]
        deleted, failed = await connected_provider._delete_nodes_by_keys(
            "txn1", keys, "records", batch_size=5
        )
        assert connected_provider.http_client.execute_aql.call_count == 3
        assert deleted == 3

    async def test_none_results_count_as_zero(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = None
        deleted, failed = await connected_provider._delete_nodes_by_keys(
            "txn1", ["k1"], "records"
        )
        assert deleted == 0
        assert failed == 0

    async def test_multiple_batches_with_partial_failure(self, connected_provider):
        call_count = 0
        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise Exception("batch 2 fail")
            return [1]
        connected_provider.http_client.execute_aql.side_effect = side_effect
        keys = [f"k{i}" for i in range(15)]
        deleted, failed = await connected_provider._delete_nodes_by_keys(
            "txn1", keys, "records", batch_size=5
        )
        assert deleted == 2
        assert failed == 1


# ===================================================================
# _delete_nodes_by_connector_id  (lines 8105-8134)
# ===================================================================

class TestDeleteNodesByConnectorId:
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [1, 1, 1]
        deleted, success = await connected_provider._delete_nodes_by_connector_id(
            "txn1", "c1", "syncPoints"
        )
        assert deleted == 3
        assert success is True

    async def test_empty_result(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = None
        deleted, success = await connected_provider._delete_nodes_by_connector_id(
            "txn1", "c1", "records"
        )
        assert deleted == 0
        assert success is True

    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        deleted, success = await connected_provider._delete_nodes_by_connector_id(
            "txn1", "c1", "syncPoints"
        )
        assert deleted == 0
        assert success is False

    async def test_zero_deleted_returns_true(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        deleted, success = await connected_provider._delete_nodes_by_connector_id(
            "txn1", "c1", "records"
        )
        assert deleted == 0
        assert success is True


# ===================================================================
# delete_sync_points_by_connector_id  (lines 8136-8155)
# ===================================================================

class TestDeleteSyncPointsByConnectorId:
    async def test_delegates_to_delete_nodes_by_connector_id(self, connected_provider):
        connected_provider._delete_nodes_by_connector_id = AsyncMock(return_value=(5, True))
        deleted, success = await connected_provider.delete_sync_points_by_connector_id("c1")
        assert deleted == 5
        assert success is True
        connected_provider._delete_nodes_by_connector_id.assert_called_once_with(
            transaction=None, connector_id="c1", collection="syncPoints"
        )

    async def test_forwards_transaction(self, connected_provider):
        connected_provider._delete_nodes_by_connector_id = AsyncMock(return_value=(0, True))
        await connected_provider.delete_sync_points_by_connector_id("c1", transaction="txn1")
        connected_provider._delete_nodes_by_connector_id.assert_called_once_with(
            transaction="txn1", connector_id="c1", collection="syncPoints"
        )


# ===================================================================
# delete_connector_sync_edges  (lines 8157-8192)
# ===================================================================

class TestDeleteConnectorSyncEdges:
    async def test_success(self, connected_provider):
        connected_provider._collect_connector_entities = AsyncMock(
            return_value={"all_node_ids": ["records/r1", "records/r2"]}
        )
        connected_provider._delete_all_edges_for_nodes = AsyncMock(return_value=(10, []))
        deleted, success = await connected_provider.delete_connector_sync_edges("c1")
        assert deleted == 10
        assert success is True

    async def test_no_entities(self, connected_provider):
        connected_provider._collect_connector_entities = AsyncMock(
            return_value={"all_node_ids": []}
        )
        deleted, success = await connected_provider.delete_connector_sync_edges("c1")
        assert deleted == 0
        assert success is True

    async def test_partial_edge_failure(self, connected_provider):
        connected_provider._collect_connector_entities = AsyncMock(
            return_value={"all_node_ids": ["records/r1"]}
        )
        connected_provider._delete_all_edges_for_nodes = AsyncMock(
            return_value=(3, ["failedColl"])
        )
        deleted, success = await connected_provider.delete_connector_sync_edges("c1")
        assert deleted == 3
        assert success is False

    async def test_exception(self, connected_provider):
        connected_provider._collect_connector_entities = AsyncMock(
            side_effect=Exception("fail")
        )
        deleted, success = await connected_provider.delete_connector_sync_edges("c1")
        assert deleted == 0
        assert success is False

    async def test_uses_correct_sync_edge_collections(self, connected_provider):
        connected_provider._collect_connector_entities = AsyncMock(
            return_value={"all_node_ids": ["records/r1"]}
        )
        connected_provider._delete_all_edges_for_nodes = AsyncMock(return_value=(0, []))
        await connected_provider.delete_connector_sync_edges("c1")
        call_args = connected_provider._delete_all_edges_for_nodes.call_args
        edge_colls = call_args[0][2]
        assert "belongsTo" in edge_colls
        assert "permission" in edge_colls


# ===================================================================
# remove_user_access_to_record  (lines 7616-7673)
# ===================================================================

class TestRemoveUserAccessToRecord:
    async def test_success_with_permissions(self, connected_provider):
        mock_record = _make_mock_record("r1")
        connected_provider.get_record_by_external_id = AsyncMock(return_value=mock_record)
        connected_provider.http_client.execute_aql.return_value = [{"_key": "perm1"}]
        await connected_provider.remove_user_access_to_record(
            connector_id="c1", external_id="ext1", user_id="u1"
        )
        connected_provider.http_client.execute_aql.assert_called_once()
        call_kwargs = connected_provider.http_client.execute_aql.call_args
        assert call_kwargs.kwargs["bind_vars"]["record_from"] == "records/r1"
        assert call_kwargs.kwargs["bind_vars"]["user_to"] == "users/u1"

    async def test_record_not_found(self, connected_provider):
        connected_provider.get_record_by_external_id = AsyncMock(return_value=None)
        await connected_provider.remove_user_access_to_record(
            connector_id="c1", external_id="ext1", user_id="u1"
        )
        connected_provider.http_client.execute_aql.assert_not_called()

    async def test_no_permissions_found(self, connected_provider):
        mock_record = _make_mock_record("r1")
        connected_provider.get_record_by_external_id = AsyncMock(return_value=mock_record)
        connected_provider.http_client.execute_aql.return_value = []
        await connected_provider.remove_user_access_to_record(
            connector_id="c1", external_id="ext1", user_id="u1"
        )

    async def test_exception_propagates(self, connected_provider):
        connected_provider.get_record_by_external_id = AsyncMock(
            side_effect=Exception("fail")
        )
        with pytest.raises(Exception, match="fail"):
            await connected_provider.remove_user_access_to_record(
                connector_id="c1", external_id="ext1", user_id="u1"
            )

    async def test_with_transaction(self, connected_provider):
        mock_record = _make_mock_record("r1")
        connected_provider.get_record_by_external_id = AsyncMock(return_value=mock_record)
        connected_provider.http_client.execute_aql.return_value = []
        await connected_provider.remove_user_access_to_record(
            connector_id="c1", external_id="ext1", user_id="u1", transaction="txn1"
        )
        call_kwargs = connected_provider.http_client.execute_aql.call_args
        assert call_kwargs.kwargs.get("txn_id") == "txn1"


# ===================================================================
# _execute_local_fs_record_deletion  (lines 8718-8769)
# ===================================================================

class TestExecuteLocalFsRecordDeletion:
    async def test_success_with_file_record(self, connected_provider):
        connected_provider.http_client.get_document = AsyncMock(
            return_value={"_key": "r1", "fileName": "test.txt"}
        )
        connected_provider._delete_local_fs_edges = AsyncMock()
        connected_provider._delete_file_record = AsyncMock()
        connected_provider._delete_main_record = AsyncMock()
        connected_provider._create_deleted_record_event_payload = AsyncMock(
            return_value={"recordId": "r1"}
        )
        result = await connected_provider._execute_local_fs_record_deletion(
            "r1", {"connectorId": "c1"}
        )
        assert result["success"] is True
        assert result["record_id"] == "r1"
        connected_provider._delete_file_record.assert_called_once_with("r1", None)
        connected_provider._delete_main_record.assert_called_once_with("r1", None)

    async def test_success_without_file_record(self, connected_provider):
        connected_provider.http_client.get_document = AsyncMock(return_value=None)
        connected_provider._delete_local_fs_edges = AsyncMock()
        connected_provider._delete_main_record = AsyncMock()
        connected_provider._create_deleted_record_event_payload = AsyncMock(
            return_value=None
        )
        result = await connected_provider._execute_local_fs_record_deletion(
            "r1", {"connectorId": "c1"}
        )
        assert result["success"] is True
        assert result["eventData"] is None

    async def test_exception_returns_failure(self, connected_provider):
        connected_provider.http_client.get_document = AsyncMock(
            side_effect=Exception("fail")
        )
        result = await connected_provider._execute_local_fs_record_deletion(
            "r1", {"connectorId": "c1"}
        )
        assert result["success"] is False

    async def test_event_payload_exception_still_succeeds(self, connected_provider):
        connected_provider.http_client.get_document = AsyncMock(
            return_value={"_key": "r1"}
        )
        connected_provider._delete_local_fs_edges = AsyncMock()
        connected_provider._delete_file_record = AsyncMock()
        connected_provider._delete_main_record = AsyncMock()
        connected_provider._create_deleted_record_event_payload = AsyncMock(
            side_effect=Exception("payload fail")
        )
        result = await connected_provider._execute_local_fs_record_deletion(
            "r1", {"connectorId": "c1"}
        )
        assert result["success"] is True
        assert result["eventData"] is None


# ===================================================================
# _delete_local_fs_edges  (lines 8771-8807)
# ===================================================================

class TestDeleteLocalFsEdges:
    async def test_success_deletes_three_collections(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        await connected_provider._delete_local_fs_edges("r1")
        assert connected_provider.http_client.execute_aql.call_count == 3

    async def test_exception_propagates(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=Exception("fail")
        )
        with pytest.raises(Exception, match="fail"):
            await connected_provider._delete_local_fs_edges("r1")

    async def test_with_transaction(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        await connected_provider._delete_local_fs_edges("r1", transaction="txn1")
        for call in connected_provider.http_client.execute_aql.call_args_list:
            assert call.kwargs.get("txn_id") == "txn1"

    async def test_correct_bind_vars(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        await connected_provider._delete_local_fs_edges("r1")
        calls = connected_provider.http_client.execute_aql.call_args_list
        all_bind_vars = [c[0][1] for c in calls]
        collections_used = {bv["@edge_collection"] for bv in all_bind_vars}
        assert "isOfType" in collections_used
        assert "permission" in collections_used
        assert "belongsTo" in collections_used


# ===================================================================
# delete_connector_instance  (lines 8194-8478)
# ===================================================================

class TestDeleteConnectorInstance:
    async def test_connector_not_found(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value=None)
        result = await connected_provider.delete_connector_instance("c1", "org1")
        assert result["success"] is False
        assert "not found" in result["error"]

    async def test_isoftype_collect_failure(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "c1"})
        connected_provider._collect_connector_entities = AsyncMock(return_value={
            "record_keys": [], "record_ids": [],
            "virtual_record_ids": [], "record_group_keys": [],
            "role_keys": [], "group_keys": [], "all_node_ids": [],
        })
        connected_provider._get_all_edge_collections = AsyncMock(
            return_value=["permission"]
        )
        connected_provider._collect_isoftype_targets = AsyncMock(
            return_value=([], False)
        )
        result = await connected_provider.delete_connector_instance("c1", "org1")
        assert result["success"] is False
        assert "isOfType" in result["error"]

    async def test_success_empty_connector(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "c1"})
        connected_provider._collect_connector_entities = AsyncMock(return_value={
            "record_keys": [], "record_ids": [],
            "virtual_record_ids": [], "record_group_keys": [],
            "role_keys": [], "group_keys": [], "all_node_ids": [],
        })
        connected_provider._get_all_edge_collections = AsyncMock(
            return_value=["permission"]
        )
        connected_provider._collect_isoftype_targets = AsyncMock(
            return_value=([], True)
        )
        connected_provider._delete_edges_by_connector_id = AsyncMock(
            return_value=(0, [])
        )
        connected_provider._delete_isoftype_targets_from_collected = AsyncMock(
            return_value=(0, [])
        )
        connected_provider._delete_nodes_by_keys = AsyncMock(return_value=(1, 0))
        connected_provider._delete_nodes_by_connector_id = AsyncMock(
            return_value=(0, True)
        )
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider.commit_transaction = AsyncMock()
        result = await connected_provider.delete_connector_instance("c1", "org1")
        assert result["success"] is True
        assert result["connector_id"] == "c1"

    async def test_exception_returns_failure(self, connected_provider):
        connected_provider.get_document = AsyncMock(side_effect=Exception("db err"))
        result = await connected_provider.delete_connector_instance("c1", "org1")
        assert result["success"] is False

    async def test_critical_record_deletion_failure_rolls_back(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "c1"})
        connected_provider._collect_connector_entities = AsyncMock(return_value={
            "record_keys": ["r1"], "record_ids": ["records/r1"],
            "virtual_record_ids": [], "record_group_keys": [],
            "role_keys": [], "group_keys": [], "all_node_ids": ["records/r1"],
        })
        connected_provider._get_all_edge_collections = AsyncMock(
            return_value=["permission"]
        )
        connected_provider._collect_isoftype_targets = AsyncMock(
            return_value=([], True)
        )
        connected_provider._delete_edges_by_connector_id = AsyncMock(
            return_value=(0, [])
        )
        connected_provider._delete_isoftype_targets_from_collected = AsyncMock(
            return_value=(0, [])
        )
        connected_provider._delete_nodes_by_keys = AsyncMock(return_value=(0, 0))
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider.rollback_transaction = AsyncMock()
        result = await connected_provider.delete_connector_instance("c1", "org1")
        assert result["success"] is False
        connected_provider.rollback_transaction.assert_called_once_with("txn1")

    async def test_uses_provided_transaction(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "c1"})
        connected_provider._collect_connector_entities = AsyncMock(return_value={
            "record_keys": [], "record_ids": [],
            "virtual_record_ids": [], "record_group_keys": [],
            "role_keys": [], "group_keys": [], "all_node_ids": [],
        })
        connected_provider._get_all_edge_collections = AsyncMock(
            return_value=["permission"]
        )
        connected_provider._collect_isoftype_targets = AsyncMock(
            return_value=([], True)
        )
        connected_provider._delete_edges_by_connector_id = AsyncMock(
            return_value=(0, [])
        )
        connected_provider._delete_isoftype_targets_from_collected = AsyncMock(
            return_value=(0, [])
        )
        connected_provider._delete_nodes_by_keys = AsyncMock(return_value=(1, 0))
        connected_provider._delete_nodes_by_connector_id = AsyncMock(
            return_value=(0, True)
        )
        connected_provider.begin_transaction = AsyncMock()
        connected_provider.commit_transaction = AsyncMock()
        result = await connected_provider.delete_connector_instance(
            "c1", "org1", transaction="ext_txn"
        )
        assert result["success"] is True
        connected_provider.begin_transaction.assert_not_called()
        connected_provider.commit_transaction.assert_not_called()


# ===================================================================
# _check_record_permission  (lines 10031-10061)
# ===================================================================

class TestCheckRecordPermission:
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["OWNER"]
        result = await connected_provider._check_record_permission("r1", "u1")
        assert result == "OWNER"

    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider._check_record_permission("r1", "u1")
        assert result is None

    async def test_exception_returns_none(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider._check_record_permission("r1", "u1")
        assert result is None

    async def test_with_transaction(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["WRITER"]
        result = await connected_provider._check_record_permission(
            "r1", "u1", transaction="txn1"
        )
        assert result == "WRITER"
        call_kwargs = connected_provider.http_client.execute_aql.call_args
        assert call_kwargs.kwargs.get("txn_id") == "txn1"

    async def test_correct_bind_vars(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        await connected_provider._check_record_permission("rec1", "user1")
        call_kwargs = connected_provider.http_client.execute_aql.call_args
        assert call_kwargs.kwargs["bind_vars"]["record_to"] == "records/rec1"
        assert call_kwargs.kwargs["bind_vars"]["user_from"] == "users/user1"


# ===================================================================
# _check_drive_permissions  (lines 10063-10135)
# ===================================================================

class TestCheckDrivePermissions:
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["WRITER"]
        result = await connected_provider._check_drive_permissions("r1", "u1")
        assert result == "WRITER"

    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider._check_drive_permissions("r1", "u1")
        assert result is None

    async def test_null_result(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [None]
        result = await connected_provider._check_drive_permissions("r1", "u1")
        assert result is None

    async def test_exception_returns_none(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider._check_drive_permissions("r1", "u1")
        assert result is None

    async def test_correct_bind_vars(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        await connected_provider._check_drive_permissions("rec1", "user1")
        call_kwargs = connected_provider.http_client.execute_aql.call_args
        bv = call_kwargs.kwargs["bind_vars"]
        assert bv["record_id"] == "rec1"
        assert bv["user_key"] == "user1"
        assert "@permission" in bv
        assert "@belongs_to" in bv
        assert "@anyone" in bv


# ===================================================================
# _check_gmail_permissions  (lines 10137-10206)
# ===================================================================

class TestCheckGmailPermissions:
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["OWNER"]
        result = await connected_provider._check_gmail_permissions("r1", "u1")
        assert result == "OWNER"

    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider._check_gmail_permissions("r1", "u1")
        assert result is None

    async def test_null_result(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [None]
        result = await connected_provider._check_gmail_permissions("r1", "u1")
        assert result is None

    async def test_exception_returns_none(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider._check_gmail_permissions("r1", "u1")
        assert result is None

    async def test_correct_bind_vars(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        await connected_provider._check_gmail_permissions("rec1", "user1")
        call_kwargs = connected_provider.http_client.execute_aql.call_args
        bv = call_kwargs.kwargs["bind_vars"]
        assert bv["record_id"] == "rec1"
        assert bv["user_key"] == "user1"
        assert "@records" in bv
        assert "@is_of_type" in bv
        assert "@permission" in bv


# ===================================================================
# _get_kb_context_for_record  (lines 10208-10248)
# ===================================================================

class TestGetKbContextForRecord:
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"kb_id": "kb1", "kb_name": "My KB", "org_id": "org1"}
        ]
        result = await connected_provider._get_kb_context_for_record("r1")
        assert result["kb_id"] == "kb1"
        assert result["kb_name"] == "My KB"

    async def test_not_found_null_result(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [None]
        result = await connected_provider._get_kb_context_for_record("r1")
        assert result is None

    async def test_not_found_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider._get_kb_context_for_record("r1")
        assert result is None

    async def test_exception_returns_none(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider._get_kb_context_for_record("r1")
        assert result is None

    async def test_with_transaction(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [None]
        await connected_provider._get_kb_context_for_record("r1", transaction="txn1")
        call_kwargs = connected_provider.http_client.execute_aql.call_args
        assert call_kwargs.kwargs.get("txn_id") == "txn1"


# ===================================================================
# get_user_kb_permission  (lines 10250-10338)
# ===================================================================

class TestGetUserKbPermission:
    async def test_owner(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["OWNER"]
        result = await connected_provider.get_user_kb_permission("kb1", "u1")
        assert result == "OWNER"

    async def test_no_permission_null(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [None]
        result = await connected_provider.get_user_kb_permission("kb1", "u1")
        assert result is None

    async def test_no_permission_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_user_kb_permission("kb1", "u1")
        assert result is None

    async def test_exception_returns_none(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_user_kb_permission("kb1", "u1")
        assert result is None

    async def test_correct_bind_vars(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        await connected_provider.get_user_kb_permission("kb1", "u1")
        call_kwargs = connected_provider.http_client.execute_aql.call_args
        bv = call_kwargs.kwargs["bind_vars"]
        assert bv["kb_id"] == "kb1"
        assert bv["user_id"] == "u1"
        assert "@permissions_collection" in bv
        assert "role_priority" in bv

    async def test_writer_role(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["WRITER"]
        result = await connected_provider.get_user_kb_permission("kb1", "u1")
        assert result == "WRITER"


# ===================================================================
# list_user_knowledge_bases  (lines 10340-10683)
# ===================================================================

class TestListUserKnowledgeBases:
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=[
            [{"id": "kb1", "name": "KB One", "userRole": "OWNER"}],
            [1],
            [{"permission": "OWNER", "kb_name": "KB One"}],
        ])
        kbs, total, filters = await connected_provider.list_user_knowledge_bases(
            user_id="u1", org_id="org1", skip=0, limit=10
        )
        assert len(kbs) == 1
        assert total == 1
        assert "permissions" in filters

    async def test_empty_results(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=[
            [], [0], [],
        ])
        kbs, total, filters = await connected_provider.list_user_knowledge_bases(
            user_id="u1", org_id="org1", skip=0, limit=10
        )
        assert kbs == []
        assert total == 0

    async def test_with_search(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=[
            [], [0], [],
        ])
        kbs, total, filters = await connected_provider.list_user_knowledge_bases(
            user_id="u1", org_id="org1", skip=0, limit=10, search="test"
        )
        assert kbs == []

    async def test_with_permissions_filter(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=[
            [], [0], [],
        ])
        kbs, total, filters = await connected_provider.list_user_knowledge_bases(
            user_id="u1", org_id="org1", skip=0, limit=10,
            permissions=["OWNER", "WRITER"]
        )
        assert kbs == []

    async def test_exception_returns_empty(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        kbs, total, filters = await connected_provider.list_user_knowledge_bases(
            user_id="u1", org_id="org1", skip=0, limit=10
        )
        assert kbs == []
        assert total == 0
        assert filters["permissions"] == []
        assert "sortFields" in filters

    async def test_sort_by_options(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=[
            [], [0], [],
        ])
        kbs, total, filters = await connected_provider.list_user_knowledge_bases(
            user_id="u1", org_id="org1", skip=0, limit=10,
            sort_by="createdAtTimestamp", sort_order="desc"
        )
        assert kbs == []

    async def test_none_count_result(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=[
            [{"id": "kb1"}], None, [],
        ])
        kbs, total, filters = await connected_provider.list_user_knowledge_bases(
            user_id="u1", org_id="org1", skip=0, limit=10
        )
        assert total == 0


# ===================================================================
# check_connector_name_exists  (lines 1161-1213)
# ===================================================================

class TestCheckConnectorNameExists:
    async def test_personal_exists(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=["k1"])
        result = await connected_provider.check_connector_name_exists(
            collection="apps", instance_name="My Drive",
            scope="personal", user_id="uid1"
        )
        assert result is True

    async def test_personal_not_exists(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.check_connector_name_exists(
            collection="apps", instance_name="My Drive",
            scope="personal", user_id="uid1"
        )
        assert result is False

    async def test_team_scope_exists(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=["k1"])
        result = await connected_provider.check_connector_name_exists(
            collection="apps", instance_name="Team Drive",
            scope="team", org_id="org1"
        )
        assert result is True

    async def test_team_scope_not_exists(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.check_connector_name_exists(
            collection="apps", instance_name="Team Drive",
            scope="team", org_id="org1"
        )
        assert result is False

    async def test_name_normalized(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        await connected_provider.check_connector_name_exists(
            collection="apps", instance_name="  My Drive  ",
            scope="personal", user_id="uid1"
        )
        call_kwargs = connected_provider.execute_query.call_args
        bv = call_kwargs.kwargs.get("bind_vars") or call_kwargs[1].get("bind_vars", {})
        assert bv.get("normalized_name") == "my drive"

    async def test_exception_returns_false(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.check_connector_name_exists(
            collection="apps", instance_name="Name",
            scope="personal", user_id="uid1"
        )
        assert result is False


# ===================================================================
# delete_records_recursive  (lines 11953-12092)
# ===================================================================

class TestDeleteRecordsRecursive:
    async def test_empty_record_ids(self, connected_provider):
        result = await connected_provider.delete_records_recursive([], "c1")
        assert result["success"] is True
        assert result["total_requested"] == 0
        assert result["successfully_deleted"] == 0

    async def test_success(self, connected_provider):
        connected_provider._get_all_edge_collections = AsyncMock(
            return_value=["belongsTo", "permission"]
        )
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider.execute_query = AsyncMock(return_value=[{
            "valid_root_keys": ["r1"],
            "records_with_type": [{
                "record": {
                    "_key": "r1", "recordName": "file.txt",
                    "connectorId": "c1", "virtualRecordId": "vr1",
                    "connectorName": "KB", "origin": "UPLOAD",
                },
                "type_target": {
                    "collection": "files", "key": "r1",
                    "full_id": "files/r1", "doc": {},
                },
            }],
        }])
        connected_provider._delete_edges_by_node_ids = AsyncMock(return_value=(5, []))
        connected_provider._delete_isoftype_targets_from_collected = AsyncMock(
            return_value=(1, [])
        )
        connected_provider._delete_nodes_by_keys = AsyncMock(return_value=(1, 0))
        connected_provider.commit_transaction = AsyncMock()
        connected_provider._create_deleted_record_event_payload = AsyncMock(
            return_value={"virtualRecordId": "vr1"}
        )
        result = await connected_provider.delete_records_recursive(["r1"], "c1")
        assert result["success"] is True
        assert result["successfully_deleted"] == 1
        assert len(result["deleted_records"]) == 1
        connected_provider.commit_transaction.assert_called_once_with("txn1")

    async def test_invalid_record_ids_marked_failed(self, connected_provider):
        connected_provider._get_all_edge_collections = AsyncMock(
            return_value=["belongsTo"]
        )
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider.execute_query = AsyncMock(return_value=[{
            "valid_root_keys": [],
            "records_with_type": [],
        }])
        connected_provider.commit_transaction = AsyncMock()
        result = await connected_provider.delete_records_recursive(
            ["invalid1", "invalid2"], "c1"
        )
        assert result["success"] is True
        assert result["failed_count"] == 2

    async def test_exception_rolls_back(self, connected_provider):
        connected_provider._get_all_edge_collections = AsyncMock(
            return_value=["belongsTo"]
        )
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider.execute_query = AsyncMock(
            side_effect=Exception("db error")
        )
        connected_provider.rollback_transaction = AsyncMock()
        result = await connected_provider.delete_records_recursive(["r1"], "c1")
        assert result["success"] is False
        connected_provider.rollback_transaction.assert_called_once_with("txn1")

    async def test_uses_provided_transaction(self, connected_provider):
        connected_provider._get_all_edge_collections = AsyncMock(
            return_value=["belongsTo"]
        )
        connected_provider.execute_query = AsyncMock(return_value=[{
            "valid_root_keys": [],
            "records_with_type": [],
        }])
        connected_provider.commit_transaction = AsyncMock()
        result = await connected_provider.delete_records_recursive(
            ["r1"], "c1", transaction="ext_txn"
        )
        assert result["success"] is True
        connected_provider.commit_transaction.assert_not_called()

    async def test_records_without_virtual_record_id_skipped(self, connected_provider):
        connected_provider._get_all_edge_collections = AsyncMock(
            return_value=["belongsTo"]
        )
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider.execute_query = AsyncMock(return_value=[{
            "valid_root_keys": ["r1"],
            "records_with_type": [{
                "record": {
                    "_key": "r1", "recordName": "folder",
                    "connectorId": "c1",
                },
                "type_target": None,
            }],
        }])
        connected_provider._delete_edges_by_node_ids = AsyncMock(return_value=(0, []))
        connected_provider._delete_nodes_by_keys = AsyncMock(return_value=(1, 0))
        connected_provider.commit_transaction = AsyncMock()
        result = await connected_provider.delete_records_recursive(["r1"], "c1")
        assert result["success"] is True
        assert result["eventData"] is None


# ===================================================================
# delete_single_record  (lines 12095-12195)
# ===================================================================

class TestDeleteSingleRecord:
    async def test_empty_record_id(self, connected_provider):
        result = await connected_provider.delete_single_record("")
        assert result["success"] is True
        assert result["total_requested"] == 0

    async def test_success(self, connected_provider):
        connected_provider._get_all_edge_collections = AsyncMock(
            return_value=["belongsTo"]
        )
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider.execute_query = AsyncMock(return_value=[{
            "valid_root_keys": ["r1"],
            "records_with_type": [{
                "record": {
                    "_key": "r1", "recordName": "file.txt",
                    "connectorId": "c1",
                },
                "type_target": {
                    "collection": "files", "key": "r1",
                    "full_id": "files/r1", "doc": {},
                },
            }],
        }])
        connected_provider._delete_edges_by_node_ids = AsyncMock(return_value=(2, []))
        connected_provider._delete_isoftype_targets_from_collected = AsyncMock(
            return_value=(1, [])
        )
        connected_provider._delete_nodes_by_keys = AsyncMock(return_value=(1, 0))
        connected_provider.commit_transaction = AsyncMock()
        result = await connected_provider.delete_single_record("r1")
        assert result["success"] is True
        assert result["total_requested"] == 1

    async def test_exception_rolls_back(self, connected_provider):
        connected_provider._get_all_edge_collections = AsyncMock(
            return_value=["belongsTo"]
        )
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider.execute_query = AsyncMock(
            side_effect=Exception("db error")
        )
        connected_provider.rollback_transaction = AsyncMock()
        result = await connected_provider.delete_single_record("r1")
        assert result["success"] is False
        connected_provider.rollback_transaction.assert_called_once_with("txn1")

    async def test_uses_provided_transaction(self, connected_provider):
        connected_provider._get_all_edge_collections = AsyncMock(
            return_value=["belongsTo"]
        )
        connected_provider.execute_query = AsyncMock(return_value=[{
            "valid_root_keys": ["r1"],
            "records_with_type": [{
                "record": {"_key": "r1", "recordName": "f.txt", "connectorId": "c1"},
                "type_target": None,
            }],
        }])
        connected_provider._delete_edges_by_node_ids = AsyncMock(return_value=(0, []))
        connected_provider._delete_nodes_by_keys = AsyncMock(return_value=(1, 0))
        connected_provider.commit_transaction = AsyncMock()
        result = await connected_provider.delete_single_record(
            "r1", transaction="ext_txn"
        )
        assert result["success"] is True
        connected_provider.commit_transaction.assert_not_called()
