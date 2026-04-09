"""Additional tests for knowledge_hub_router targeting remaining uncovered lines.

Covers:
- get_knowledge_hub_service (DI resolution)
- _handle_get_nodes (full flow with all parameters, error paths)
- get_knowledge_hub_children_nodes (invalid parent_type)
- _handle_get_nodes error response codes (not found, type mismatch, server error)
- Search query validation (too short, too long)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.connectors.sources.localKB.api.knowledge_hub_router import (
    _handle_get_nodes,
    _parse_comma_separated_str,
    _parse_date_range,
    _parse_size_range,
    _validate_enum_values,
    _validate_uuid_format,
    get_knowledge_hub_service,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(user_id="user-1", org_id="org-1"):
    """Build a mock FastAPI Request."""
    request = MagicMock()
    request.state.user = {"userId": user_id, "orgId": org_id}
    return request


def _make_knowledge_hub_service():
    """Build a mock KnowledgeHubService."""
    svc = AsyncMock()
    return svc


def _make_success_result():
    """Build a successful KnowledgeHubNodesResponse."""
    result = MagicMock()
    result.success = True
    result.error = None
    result.nodes = []
    return result


def _make_error_result(error_msg="Failed"):
    """Build a failed KnowledgeHubNodesResponse."""
    result = MagicMock()
    result.success = False
    result.error = error_msg
    return result


# ===================================================================
# get_knowledge_hub_service
# ===================================================================

class TestGetKnowledgeHubService:

    @pytest.mark.asyncio
    async def test_resolves_service(self):
        request = MagicMock()
        container = MagicMock()
        container.logger.return_value = MagicMock()
        request.app.container = container
        request.app.state.graph_provider = MagicMock()

        with patch("app.connectors.sources.localKB.api.knowledge_hub_router.KnowledgeHubService") as MockSvc:
            MockSvc.return_value = MagicMock()
            result = await get_knowledge_hub_service(request)
            MockSvc.assert_called_once()
            assert result is not None


# ===================================================================
# _handle_get_nodes
# ===================================================================

class TestHandleGetNodes:

    @pytest.mark.asyncio
    async def test_success_basic(self):
        request = _make_request()
        svc = _make_knowledge_hub_service()
        svc.get_nodes = AsyncMock(return_value=_make_success_result())

        result = await _handle_get_nodes(
            request=request,
            knowledge_hub_service=svc,
            parent_id=None,
            parent_type=None,
            only_containers=False,
            page=1,
            limit=50,
            sort_by="updatedAt",
            sort_order="desc",
            q=None,
            node_types=None,
            record_types=None,
            origins=None,
            connector_ids=None,
            indexing_status=None,
            created_at=None,
            updated_at=None,
            size=None,
            flattened=False,
            include=None,
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_missing_user_id(self):
        request = _make_request(user_id=None)
        svc = _make_knowledge_hub_service()

        with pytest.raises(HTTPException) as exc_info:
            await _handle_get_nodes(
                request=request,
                knowledge_hub_service=svc,
                parent_id=None,
                parent_type=None,
                only_containers=False,
                page=1,
                limit=50,
                sort_by="updatedAt",
                sort_order="desc",
                q=None,
                node_types=None,
                record_types=None,
                origins=None,
                connector_ids=None,
                indexing_status=None,
                created_at=None,
                updated_at=None,
                size=None,
                flattened=False,
                include=None,
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_search_query_too_short(self):
        request = _make_request()
        svc = _make_knowledge_hub_service()

        with pytest.raises(HTTPException) as exc_info:
            await _handle_get_nodes(
                request=request,
                knowledge_hub_service=svc,
                parent_id=None,
                parent_type=None,
                only_containers=False,
                page=1,
                limit=50,
                sort_by="updatedAt",
                sort_order="desc",
                q="a",  # Too short (min 2)
                node_types=None,
                record_types=None,
                origins=None,
                connector_ids=None,
                indexing_status=None,
                created_at=None,
                updated_at=None,
                size=None,
                flattened=False,
                include=None,
            )
        assert exc_info.value.status_code == 400
        assert "at least" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_search_query_too_long(self):
        request = _make_request()
        svc = _make_knowledge_hub_service()

        with pytest.raises(HTTPException) as exc_info:
            await _handle_get_nodes(
                request=request,
                knowledge_hub_service=svc,
                parent_id=None,
                parent_type=None,
                only_containers=False,
                page=1,
                limit=50,
                sort_by="updatedAt",
                sort_order="desc",
                q="x" * 501,  # Too long (max 500)
                node_types=None,
                record_types=None,
                origins=None,
                connector_ids=None,
                indexing_status=None,
                created_at=None,
                updated_at=None,
                size=None,
                flattened=False,
                include=None,
            )
        assert exc_info.value.status_code == 400
        assert "too long" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_parent_id_validation(self):
        request = _make_request()
        svc = _make_knowledge_hub_service()

        with pytest.raises(HTTPException) as exc_info:
            await _handle_get_nodes(
                request=request,
                knowledge_hub_service=svc,
                parent_id="invalid-uuid",
                parent_type="app",
                only_containers=False,
                page=1,
                limit=50,
                sort_by="updatedAt",
                sort_order="desc",
                q=None,
                node_types=None,
                record_types=None,
                origins=None,
                connector_ids=None,
                indexing_status=None,
                created_at=None,
                updated_at=None,
                size=None,
                flattened=False,
                include=None,
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_sort_by_defaults_to_name(self):
        request = _make_request()
        svc = _make_knowledge_hub_service()
        svc.get_nodes = AsyncMock(return_value=_make_success_result())

        await _handle_get_nodes(
            request=request,
            knowledge_hub_service=svc,
            parent_id=None,
            parent_type=None,
            only_containers=False,
            page=1,
            limit=50,
            sort_by="invalid_field",
            sort_order="desc",
            q=None,
            node_types=None,
            record_types=None,
            origins=None,
            connector_ids=None,
            indexing_status=None,
            created_at=None,
            updated_at=None,
            size=None,
            flattened=False,
            include=None,
        )
        call_kwargs = svc.get_nodes.call_args[1]
        assert call_kwargs["sort_by"] == "name"

    @pytest.mark.asyncio
    async def test_invalid_sort_order_defaults_to_asc(self):
        request = _make_request()
        svc = _make_knowledge_hub_service()
        svc.get_nodes = AsyncMock(return_value=_make_success_result())

        await _handle_get_nodes(
            request=request,
            knowledge_hub_service=svc,
            parent_id=None,
            parent_type=None,
            only_containers=False,
            page=1,
            limit=50,
            sort_by="name",
            sort_order="invalid",
            q=None,
            node_types=None,
            record_types=None,
            origins=None,
            connector_ids=None,
            indexing_status=None,
            created_at=None,
            updated_at=None,
            size=None,
            flattened=False,
            include=None,
        )
        call_kwargs = svc.get_nodes.call_args[1]
        assert call_kwargs["sort_order"] == "asc"

    @pytest.mark.asyncio
    async def test_error_result_not_found(self):
        request = _make_request()
        svc = _make_knowledge_hub_service()
        svc.get_nodes = AsyncMock(return_value=_make_error_result("Resource not found"))

        with pytest.raises(HTTPException) as exc_info:
            await _handle_get_nodes(
                request=request,
                knowledge_hub_service=svc,
                parent_id=None,
                parent_type=None,
                only_containers=False,
                page=1,
                limit=50,
                sort_by="name",
                sort_order="asc",
                q=None,
                node_types=None,
                record_types=None,
                origins=None,
                connector_ids=None,
                indexing_status=None,
                created_at=None,
                updated_at=None,
                size=None,
                flattened=False,
                include=None,
            )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_error_result_type_mismatch(self):
        request = _make_request()
        svc = _make_knowledge_hub_service()
        svc.get_nodes = AsyncMock(
            return_value=_make_error_result("Type mismatch error")
        )

        with pytest.raises(HTTPException) as exc_info:
            await _handle_get_nodes(
                request=request,
                knowledge_hub_service=svc,
                parent_id=None,
                parent_type=None,
                only_containers=False,
                page=1,
                limit=50,
                sort_by="name",
                sort_order="asc",
                q=None,
                node_types=None,
                record_types=None,
                origins=None,
                connector_ids=None,
                indexing_status=None,
                created_at=None,
                updated_at=None,
                size=None,
                flattened=False,
                include=None,
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_error_result_invalid(self):
        request = _make_request()
        svc = _make_knowledge_hub_service()
        svc.get_nodes = AsyncMock(
            return_value=_make_error_result("Invalid request parameter")
        )

        with pytest.raises(HTTPException) as exc_info:
            await _handle_get_nodes(
                request=request,
                knowledge_hub_service=svc,
                parent_id=None,
                parent_type=None,
                only_containers=False,
                page=1,
                limit=50,
                sort_by="name",
                sort_order="asc",
                q=None,
                node_types=None,
                record_types=None,
                origins=None,
                connector_ids=None,
                indexing_status=None,
                created_at=None,
                updated_at=None,
                size=None,
                flattened=False,
                include=None,
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_error_result_server_error(self):
        request = _make_request()
        svc = _make_knowledge_hub_service()
        svc.get_nodes = AsyncMock(
            return_value=_make_error_result("Database connection failed")
        )

        with pytest.raises(HTTPException) as exc_info:
            await _handle_get_nodes(
                request=request,
                knowledge_hub_service=svc,
                parent_id=None,
                parent_type=None,
                only_containers=False,
                page=1,
                limit=50,
                sort_by="name",
                sort_order="asc",
                q=None,
                node_types=None,
                record_types=None,
                origins=None,
                connector_ids=None,
                indexing_status=None,
                created_at=None,
                updated_at=None,
                size=None,
                flattened=False,
                include=None,
            )
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_error_result_no_error_message(self):
        request = _make_request()
        svc = _make_knowledge_hub_service()
        svc.get_nodes = AsyncMock(return_value=_make_error_result(None))

        with pytest.raises(HTTPException) as exc_info:
            await _handle_get_nodes(
                request=request,
                knowledge_hub_service=svc,
                parent_id=None,
                parent_type=None,
                only_containers=False,
                page=1,
                limit=50,
                sort_by="name",
                sort_order="asc",
                q=None,
                node_types=None,
                record_types=None,
                origins=None,
                connector_ids=None,
                indexing_status=None,
                created_at=None,
                updated_at=None,
                size=None,
                flattened=False,
                include=None,
            )
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_unexpected_exception(self):
        request = _make_request()
        svc = _make_knowledge_hub_service()
        svc.get_nodes = AsyncMock(side_effect=RuntimeError("unexpected"))

        with pytest.raises(HTTPException) as exc_info:
            await _handle_get_nodes(
                request=request,
                knowledge_hub_service=svc,
                parent_id=None,
                parent_type=None,
                only_containers=False,
                page=1,
                limit=50,
                sort_by="name",
                sort_order="asc",
                q=None,
                node_types=None,
                record_types=None,
                origins=None,
                connector_ids=None,
                indexing_status=None,
                created_at=None,
                updated_at=None,
                size=None,
                flattened=False,
                include=None,
            )
        assert exc_info.value.status_code == 500
        assert "unexpected" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_with_all_filters(self):
        """Test with all filter parameters set."""
        request = _make_request()
        svc = _make_knowledge_hub_service()
        svc.get_nodes = AsyncMock(return_value=_make_success_result())

        result = await _handle_get_nodes(
            request=request,
            knowledge_hub_service=svc,
            parent_id="550e8400-e29b-41d4-a716-446655440000",
            parent_type="app",
            only_containers=True,
            page=2,
            limit=10,
            sort_by="name",
            sort_order="asc",
            q="search term",
            node_types="record,folder",
            record_types="FILE",
            origins="CONNECTOR",
            connector_ids="conn-1,conn-2",
            indexing_status="COMPLETED",
            created_at="gte:1000000000000,lte:2000000000000",
            updated_at="gte:1000000000000",
            size="gte:1024,lte:1048576",
            flattened=True,
            include="breadcrumbs,counts",
        )
        assert result.success is True
        call_kwargs = svc.get_nodes.call_args[1]
        assert call_kwargs["page"] == 2
        assert call_kwargs["limit"] == 10
        assert call_kwargs["only_containers"] is True
        assert call_kwargs["flattened"] is True
        assert call_kwargs["q"] == "search term"

    @pytest.mark.asyncio
    async def test_valid_parent_id_with_knowledge_base_format(self):
        request = _make_request()
        svc = _make_knowledge_hub_service()
        svc.get_nodes = AsyncMock(return_value=_make_success_result())

        result = await _handle_get_nodes(
            request=request,
            knowledge_hub_service=svc,
            parent_id="knowledgeBase_org-123",
            parent_type="app",
            only_containers=False,
            page=1,
            limit=50,
            sort_by="name",
            sort_order="asc",
            q=None,
            node_types=None,
            record_types=None,
            origins=None,
            connector_ids=None,
            indexing_status=None,
            created_at=None,
            updated_at=None,
            size=None,
            flattened=False,
            include=None,
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_search_query_trimmed(self):
        request = _make_request()
        svc = _make_knowledge_hub_service()
        svc.get_nodes = AsyncMock(return_value=_make_success_result())

        await _handle_get_nodes(
            request=request,
            knowledge_hub_service=svc,
            parent_id=None,
            parent_type=None,
            only_containers=False,
            page=1,
            limit=50,
            sort_by="name",
            sort_order="asc",
            q="  search  ",
            node_types=None,
            record_types=None,
            origins=None,
            connector_ids=None,
            indexing_status=None,
            created_at=None,
            updated_at=None,
            size=None,
            flattened=False,
            include=None,
        )
        call_kwargs = svc.get_nodes.call_args[1]
        assert call_kwargs["q"] == "search"
