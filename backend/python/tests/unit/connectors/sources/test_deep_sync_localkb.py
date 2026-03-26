"""Deep sync loop tests for LocalKB service (kb_service.py)."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_logger():
    return logging.getLogger("test.localkb_deep")


@pytest.fixture
def mock_graph_provider():
    gp = AsyncMock()
    gp.get_user_by_user_id = AsyncMock(return_value={
        "id": "user-key-1",
        "_key": "user-key-1",
        "fullName": "Test User",
    })
    gp.begin_transaction = AsyncMock(return_value="txn-1")
    gp.commit_transaction = AsyncMock()
    gp.rollback_transaction = AsyncMock()
    gp.batch_upsert_nodes = AsyncMock()
    gp.batch_create_edges = AsyncMock()
    gp.get_user_kb_permission = AsyncMock(return_value="OWNER")
    gp.get_knowledge_base = AsyncMock(return_value={"id": "kb-1", "groupName": "Test KB"})
    gp.list_user_knowledge_bases = AsyncMock(return_value=([], 0, []))
    return gp


@pytest.fixture
def mock_kafka():
    return AsyncMock()


@pytest.fixture
def kb_service(mock_logger, mock_graph_provider, mock_kafka):
    return KnowledgeBaseService(
        logger=mock_logger,
        graph_provider=mock_graph_provider,
        kafka_service=mock_kafka,
    )


# ---------------------------------------------------------------------------
# create_knowledge_base
# ---------------------------------------------------------------------------

class TestCreateKnowledgeBase:
    async def test_successful_creation(self, kb_service):
        result = await kb_service.create_knowledge_base(
            user_id="user-1", org_id="org-1", name="My KB"
        )
        assert result["success"] is True
        assert result["name"] == "My KB"
        assert "id" in result

    async def test_user_not_found(self, kb_service):
        kb_service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await kb_service.create_knowledge_base(
            user_id="missing-user", org_id="org-1", name="KB"
        )
        assert result["success"] is False
        assert result["code"] == 404

    async def test_transaction_creation_fails(self, kb_service):
        kb_service.graph_provider.begin_transaction = AsyncMock(
            side_effect=RuntimeError("txn fail")
        )
        result = await kb_service.create_knowledge_base(
            user_id="user-1", org_id="org-1", name="KB"
        )
        assert result["success"] is False
        assert result["code"] == 500

    async def test_db_operation_fails_with_rollback(self, kb_service):
        kb_service.graph_provider.batch_upsert_nodes = AsyncMock(
            side_effect=RuntimeError("db error")
        )
        result = await kb_service.create_knowledge_base(
            user_id="user-1", org_id="org-1", name="KB"
        )
        assert result["success"] is False
        kb_service.graph_provider.rollback_transaction.assert_called_once()

    async def test_rollback_failure_handled(self, kb_service):
        kb_service.graph_provider.batch_upsert_nodes = AsyncMock(
            side_effect=RuntimeError("db error")
        )
        kb_service.graph_provider.rollback_transaction = AsyncMock(
            side_effect=RuntimeError("rollback fail")
        )
        result = await kb_service.create_knowledge_base(
            user_id="user-1", org_id="org-1", name="KB"
        )
        assert result["success"] is False

    async def test_user_key_from_key_field(self, kb_service):
        kb_service.graph_provider.get_user_by_user_id = AsyncMock(return_value={
            "_key": "key-from-_key",
            "fullName": "User",
        })
        result = await kb_service.create_knowledge_base(
            user_id="user-1", org_id="org-1", name="KB"
        )
        assert result["success"] is True

    async def test_user_name_fallback_to_firstname_lastname(self, kb_service):
        kb_service.graph_provider.get_user_by_user_id = AsyncMock(return_value={
            "id": "u1",
            "firstName": "John",
            "lastName": "Doe",
        })
        result = await kb_service.create_knowledge_base(
            user_id="user-1", org_id="org-1", name="KB"
        )
        assert result["success"] is True


# ---------------------------------------------------------------------------
# get_knowledge_base
# ---------------------------------------------------------------------------

class TestGetKnowledgeBase:
    async def test_successful_get(self, kb_service):
        result = await kb_service.get_knowledge_base(kb_id="kb-1", user_id="user-1")
        assert result["id"] == "kb-1"

    async def test_user_not_found(self, kb_service):
        kb_service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await kb_service.get_knowledge_base(kb_id="kb-1", user_id="missing")
        assert result["success"] is False
        assert result["code"] == 404

    async def test_no_permission(self, kb_service):
        kb_service.graph_provider.get_user_kb_permission = AsyncMock(return_value=None)
        result = await kb_service.get_knowledge_base(kb_id="kb-1", user_id="user-1")
        assert result["success"] is False
        assert result["code"] == "403"

    async def test_kb_not_found(self, kb_service):
        kb_service.graph_provider.get_knowledge_base = AsyncMock(return_value=None)
        result = await kb_service.get_knowledge_base(kb_id="missing", user_id="user-1")
        assert result["success"] is False
        assert result["code"] == "404"

    async def test_exception_handled(self, kb_service):
        kb_service.graph_provider.get_user_by_user_id = AsyncMock(
            side_effect=RuntimeError("db crash")
        )
        result = await kb_service.get_knowledge_base(kb_id="kb-1", user_id="user-1")
        assert result["success"] is False
        assert result["code"] == "500"


# ---------------------------------------------------------------------------
# list_user_knowledge_bases
# ---------------------------------------------------------------------------

class TestListKnowledgeBases:
    async def test_successful_list(self, kb_service):
        kb_service.graph_provider.list_user_knowledge_bases = AsyncMock(
            return_value=([{"id": "kb-1"}], 1, [])
        )
        result = await kb_service.list_user_knowledge_bases(
            user_id="user-1", org_id="org-1"
        )
        assert isinstance(result, dict)

    async def test_user_not_found(self, kb_service):
        kb_service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await kb_service.list_user_knowledge_bases(
            user_id="missing", org_id="org-1"
        )
        assert result["success"] is False
        assert result["code"] == 404

    async def test_invalid_sort_field_defaults(self, kb_service):
        kb_service.graph_provider.list_user_knowledge_bases = AsyncMock(
            return_value=([], 0, [])
        )
        result = await kb_service.list_user_knowledge_bases(
            user_id="user-1", org_id="org-1", sort_by="invalid_field"
        )
        # Should not crash
        assert isinstance(result, (dict, list))

    async def test_invalid_sort_order_defaults(self, kb_service):
        kb_service.graph_provider.list_user_knowledge_bases = AsyncMock(
            return_value=([], 0, [])
        )
        result = await kb_service.list_user_knowledge_bases(
            user_id="user-1", org_id="org-1", sort_order="invalid"
        )
        assert isinstance(result, (dict, list))
